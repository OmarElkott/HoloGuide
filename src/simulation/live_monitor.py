"""Live line monitor: a simulated gantry builds the assembly while HoloGuide watches.

Roles (the project invariant holds: the AI reports, the rules decide):
- Motion: a scripted gantry lowers each part into place; frames are rendered continuously.
- Watchdog (deterministic, no AI): consecutive frames are compared; an inspection is only
  triggered once the scene has been still for ``settle_frames`` frames.
- Inspector + rules: ``Workflow.request_inspection`` on the settled frame.
- Interlock: the gantry may only pick the next part after the workflow passes the step,
  and every pick goes through ``Workflow.check_action`` (the order guard). BLOCK,
  NEEDS_REVIEW, HOLD or a refused pick stops the line. Only an operator action
  (correction, acknowledgement, logged override, cleared hold) restarts it.
Faults are scripted gantry mis-picks, audited as ``simulation_fault_injected``. Labels and
simulator ground truth never reach the inspector: it only receives the saved PNG path.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from src.config import load_config
from src.control.audit_log import AuditLog
from src.control.workflow import Workflow, WorkflowState
from src.simulation.assembly_env import AssemblyEnv
from src.simulation.scenarios import ALL_NAMES
from src.vision.schemas import DecisionStatus

DROP_HEIGHT = 0.30      # metres above the final position where the gantry starts
DROP_FRAMES = 10        # frames per pick or removal


@dataclass(frozen=True)
class Move:
    action: str          # place | remove | place_foreign | remove_foreign
    item: str
    fault: bool = False


# scenario -> (step id, gantry moves at that step, operator correction moves)
FAULTS = {
    'missing_gasket': (2, [Move('place', 'cover', True)], [Move('remove', 'cover')]),
    'wrong_cover': (3, [Move('place', 'wrong_cover', True)], [Move('remove', 'wrong_cover')]),
    'wrong_order': (1, [Move('place', 'cover')], []),           # the order guard refuses the pick
    'foreign_object': (3, [Move('place', 'cover'), Move('place_foreign', 'screwdriver', True)],
                       [Move('remove_foreign', 'screwdriver')]),
}


@dataclass
class LineFrame:
    image: np.ndarray    # annotated RGB frame
    line: str            # running | stopped | complete
    watchdog: str        # moving | settling | inspecting | idle
    message: str
    event: str | None = None


class LineMonitor:
    def __init__(self, config, environment, workflow, scenario='correct', directory='outputs/live',
                 *, settle_frames=3, motion_threshold=0.5):
        if scenario not in ALL_NAMES:
            raise ValueError(f'Unknown scenario: {scenario}')
        if workflow.current_step_id != 1 or workflow.last_decision is not None:
            raise ValueError('The line monitor needs a fresh workflow')
        self.config, self.env, self.workflow, self.scenario = config, environment, workflow, scenario
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.settle_frames, self.motion_threshold = settle_frames, motion_threshold
        self.line, self.message, self.stop_kind = 'running', 'Line started.', None
        self.present, self.foreign = set(), set()
        self.fault = FAULTS.get(scenario)
        self.fault_injected = False
        self.correction: list[Move] = []
        self.queue: list[Move] = []
        self.anim = None
        self.prev = None
        self.still = 0
        self.awaiting = False
        self.frames = self.inspections = 0
        self.decisions: list[str] = []
        self.workflow.audit.record('line_started', scenario=scenario, mode='live-line-monitor')
        self._plan_step()

    # ---- planning ---------------------------------------------------------------
    def _plan_step(self):
        step = self.workflow.current_step
        if self.fault and not self.fault_injected and step.id == self.fault[0]:
            self.fault_injected = True
            self.queue = list(self.fault[1])
            self.correction = list(self.fault[2])
        else:
            self.queue = [Move('place', p) for p in step.adds_parts if p not in self.present]
        self.awaiting, self.still = True, 0

    # ---- one frame -------------------------------------------------------------------
    def tick(self) -> LineFrame:
        event = None
        if self.line == 'running':
            if self.anim:
                self._advance()
            elif self.queue:
                event = self._start(self.queue.pop(0))
        raw = self.env.render_rgb()
        moving = self.anim is not None or (
            self.prev is not None and float(np.abs(raw.astype(np.int16) - self.prev).mean()) > self.motion_threshold)
        self.prev = raw.astype(np.int16)
        watchdog = 'moving' if moving else 'idle'
        if self.line == 'running' and self.awaiting and not self.anim and not self.queue:
            self.still = 0 if moving else self.still + 1
            watchdog = 'moving' if moving else 'settling'
            if self.still >= self.settle_frames:
                event = self._inspect(raw)
                watchdog = 'inspecting'
        self.frames += 1
        return LineFrame(annotate(raw, self, watchdog), self.line, watchdog, self.message, event)

    def _start(self, move: Move):
        if move.action == 'place':
            if not move.fault:
                check = self.workflow.check_action(move.item)
                if not check.allowed:      # interlock: the pick never happens
                    self._stop(f'INTERLOCK: gantry pick refused. {check.reason}', 'guard')
                    return 'guard_refused'
            else:
                self.workflow.audit.record('simulation_fault_injected', part=move.item, scenario=self.scenario,
                                           source='live-line gantry mis-pick')
            self.env.place_part(move.item)
            self.env.offset_part(move.item, 0, 0, DROP_HEIGHT)
            self.present.add(move.item)
            self.anim = (move, DROP_FRAMES, -DROP_HEIGHT / DROP_FRAMES)
        elif move.action == 'place_foreign':
            self.workflow.audit.record('simulation_fault_injected', object=move.item, scenario=self.scenario,
                                       source='live-line: tool left behind')
            self.env.place_foreign_object(move.item, on_assembly=True)
            self.env.offset_foreign_object(move.item, 0, 0, DROP_HEIGHT)
            self.foreign.add(move.item)
            self.anim = (move, DROP_FRAMES, -DROP_HEIGHT / DROP_FRAMES)
        else:                                     # removals lift the item away
            self.anim = (move, DROP_FRAMES, DROP_HEIGHT / DROP_FRAMES)
        return f'{move.action}:{move.item}'

    def _advance(self):
        move, left, dz = self.anim
        if move.action in ('place', 'remove'):
            self.env.offset_part(move.item, 0, 0, dz)
        else:
            self.env.offset_foreign_object(move.item, 0, 0, dz)
        left -= 1
        if left:
            self.anim = (move, left, dz)
            return
        self.anim = None
        if move.action == 'remove':
            self.env.remove_part(move.item)
            self.present.discard(move.item)
        elif move.action == 'remove_foreign':
            self.env.remove_foreign_object(move.item)
            self.foreign.discard(move.item)

    def _inspect(self, raw):
        self.inspections += 1
        path = self.directory / f'inspection-{self.inspections:03d}.png'
        Image.fromarray(raw).save(path)
        decision = self.workflow.request_inspection(path)      # only the PNG path reaches the AI
        self.awaiting = False
        return self._apply(decision)

    def _apply(self, decision):
        self.decisions.append(decision.status.value)
        if decision.status is DecisionStatus.PASS:
            self.message = decision.message
            if self.workflow.state is WorkflowState.COMPLETE:
                self.line = 'complete'
                self.workflow.audit.record('line_completed', scenario=self.scenario)
            else:
                self._plan_step()
            return 'pass'
        self._stop(decision.message, decision.status.value)
        return decision.status.value

    def _stop(self, message, kind):
        self.line, self.message, self.stop_kind = 'stopped', message, kind
        self.workflow.audit.record('line_stopped', reason=message, kind=kind, scenario=self.scenario)

    def _restart(self, note):
        self.line, self.stop_kind, self.message = 'running', None, note
        self.workflow.audit.record('line_restarted', note=note, scenario=self.scenario)

    # ---- operator actions (the only way to restart a stopped line) ----------------------
    def available_actions(self) -> list[str]:
        if self.line != 'stopped':
            return []
        if self.stop_kind == 'guard':
            return ['acknowledge']
        if self.stop_kind == 'hold':
            return ['clear_hold']
        actions = ['correct'] if self.correction else []
        if self.workflow.state in (WorkflowState.BLOCKED, WorkflowState.NEEDS_REVIEW):
            actions.append('override')
        return actions

    def correct(self, operator: str):
        """Operator removes the faulty item; the step is then re-inspected."""
        if 'correct' not in self.available_actions():
            raise RuntimeError('No correction available')
        self.workflow.audit.record('scenario_correction', actor=operator,
                                   moves=[f'{m.action}:{m.item}' for m in self.correction])
        self.queue, self.correction = list(self.correction), []
        step = self.workflow.current_step
        self.queue += [Move('place', p) for p in step.adds_parts if p not in self.present - {m.item for m in self.queue}]
        self.awaiting, self.still = True, 0
        self._restart(f'Operator {operator}: correcting, then re-inspecting {step.name}.')

    def acknowledge(self, operator: str):
        """Operator acknowledges a refused pick; the gantry continues with the allowed parts."""
        if 'acknowledge' not in self.available_actions():
            raise RuntimeError('Nothing to acknowledge')
        step = self.workflow.current_step
        self.queue = [Move('place', p) for p in step.adds_parts if p not in self.present]
        self.awaiting, self.still = True, 0
        self._restart(f'Operator {operator} acknowledged the interlock; continuing {step.name}.')

    def override(self, approve: bool, reason: str, operator: str):
        if 'override' not in self.available_actions():
            raise RuntimeError('No override available')
        decision = self.workflow.operator_override(approve, reason, operator)
        if decision.status is DecisionStatus.PASS:
            self._restart(f'Operator {operator} approved: {reason}')
            self.correction = []
            self._apply(decision)
        return decision

    def clear_hold(self, operator: str, reason: str):
        if 'clear_hold' not in self.available_actions():
            raise RuntimeError('Line is not on hold')
        self.workflow.clear_hold(operator, reason)
        self.awaiting, self.still = True, 0
        self._restart(f'Operator {operator} cleared the hold; re-inspecting.')


# ---- overlay ----------------------------------------------------------------------------
_COLOURS = {'running': (40, 160, 60), 'stopped': (210, 40, 40), 'complete': (40, 90, 200)}


def annotate(raw: np.ndarray, monitor: LineMonitor, watchdog: str) -> np.ndarray:
    img = raw.copy()
    h, w = img.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    step = monitor.workflow.current_step
    cv2.rectangle(img, (0, 0), (w, 30), (25, 30, 40), -1)
    cv2.putText(img, f'Step {step.id}/{len(monitor.config.steps)}: {step.name}', (8, 21), font, 0.55,
                (255, 255, 255), 1, cv2.LINE_AA)
    label = {'running': 'LINE RUNNING', 'stopped': 'LINE STOPPED', 'complete': 'ASSEMBLY COMPLETE'}[monitor.line]
    (tw, _), _ = cv2.getTextSize(label, font, 0.55, 2)
    cv2.rectangle(img, (w - tw - 18, 3), (w - 3, 27), _COLOURS[monitor.line], -1)
    cv2.putText(img, label, (w - tw - 10, 21), font, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.rectangle(img, (0, h - 66), (w, h), (25, 30, 40), -1)
    wd = {'moving': 'WATCHDOG: motion detected - not inspecting',
          'settling': f'WATCHDOG: still {monitor.still}/{monitor.settle_frames} frames',
          'inspecting': 'WATCHDOG: settled -> inspected', 'idle': 'WATCHDOG: idle'}[watchdog]
    if monitor.line == 'stopped' and watchdog == 'idle':
        wd = 'LINE STOPPED - gantry locked out until an operator acts'
    cv2.putText(img, wd, (8, h - 47), font, 0.45, (255, 210, 90), 1, cv2.LINE_AA)
    words, lines = monitor.message.split(), ['']
    for word in words:
        if len(lines[-1]) + len(word) + 1 > 84 and len(lines) < 2:
            lines.append('')
        lines[-1] = (lines[-1] + ' ' + word).strip()
    for i, line in enumerate(lines):
        cv2.putText(img, line[:90], (8, h - 27 + 18 * i), font, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
    if monitor.line == 'stopped':
        cv2.rectangle(img, (2, 32), (w - 3, h - 68), (210, 40, 40), 3)
    return img


# ---- CLI: run with a scripted operator and write an MP4 ------------------------------------------
def run_scenario(config, inspector, scenario, output, *, fps=10, pause_s=2.0, max_frames=900, operator='operator-1'):
    output = Path(output)
    audit = AuditLog(output / 'audit.jsonl')
    frames = []
    with AssemblyEnv(config, seed=0) as env:
        monitor = LineMonitor(config, env, Workflow(config, inspector, audit), scenario, output / 'frames')
        stopped_for = 0
        while monitor.line != 'complete' and len(frames) < max_frames:
            frame = monitor.tick()
            frames.append(frame.image)
            if monitor.line != 'stopped':
                stopped_for = 0
                continue
            stopped_for += 1
            if stopped_for < pause_s * fps:
                continue
            actions = monitor.available_actions()     # scripted operator, labelled in the audit
            if 'correct' in actions:
                monitor.correct(operator)
            elif 'acknowledge' in actions:
                monitor.acknowledge(operator)
            elif 'clear_hold' in actions:
                monitor.clear_hold(operator, 'Scripted operator: inspector restarted.')
            elif 'override' in actions:
                monitor.override(True, 'Scripted operator: visually verified the step.', operator)
            else:
                break
        for _ in range(int(pause_s * fps)):
            frames.append(monitor.tick().image)
    return monitor, frames


def write_mp4(frames, path, fps=10):
    path = Path(path)
    h, w = frames[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError('Local MP4 encoder unavailable')
    try:
        for frame in frames:
            writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    finally:
        writer.release()
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--scenario', choices=(*ALL_NAMES, 'all'), default='all')
    parser.add_argument('--inspector-factory', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fps', type=int, default=10)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f'{args.output} exists; choose a new output directory')
    from src.simulation.runner import load_inspector
    config = load_config()
    inspector = load_inspector(args.inspector_factory)
    names = ALL_NAMES if args.scenario == 'all' else (args.scenario,)
    summary, clip = [], []
    for name in names:
        monitor, frames = run_scenario(config, inspector, name, args.output / name, fps=args.fps)
        clip += frames
        summary.append(dict(scenario=name, line=monitor.line, decisions=monitor.decisions,
                            frames=len(frames), inspections=monitor.inspections))
        print(summary[-1])
    write_mp4(clip, args.output / 'live-line.mp4', args.fps)
    (args.output / 'summary.json').write_text(json.dumps(dict(
        inspector=type(inspector).__name__, operator='scripted', fps=args.fps, runs=summary), indent=2))
    print('Wrote', args.output / 'live-line.mp4')


if __name__ == '__main__':
    main()
