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
from time import perf_counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from src.config import load_config
from src.control.audit_log import AuditLog
from src.control.workflow import Workflow, WorkflowState
from src.simulation.assembly_env import AssemblyEnv
from src.simulation.scenarios import ALL_NAMES
from src.vision.outage import ai_outage_active
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
        self.handoff = 'Waiting for the first settled frame.'
        self.banner, self.banner_until = '', 0
        # KPIs (scoring uses the script's own fault plan; the inspector never sees it)
        self.caught = self.escaped = False
        self.false_stops = self.holds = self.estops = 0
        self.stopped_frames = 0
        self.inspect_seconds = 0.0
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
        if self.line == 'stopped':
            self.stopped_frames += 1
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
        started = perf_counter()
        decision = self.workflow.request_inspection(path)      # only the PNG path reaches the AI
        seconds = perf_counter() - started
        self.inspect_seconds += seconds
        self.handoff = describe_handoff(self.workflow.inspector, decision, seconds)
        self.awaiting = False
        return self._apply(decision)

    def _apply(self, decision):
        self.decisions.append(decision.status.value)
        if decision.status is DecisionStatus.PASS:
            if self.correction:            # passed while the injected fault was still in the scene
                self.escaped, self.correction = True, []
                self.workflow.audit.record('line_fault_escaped', scenario=self.scenario)
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
        if kind == 'estop':
            self.estops += 1
        elif kind == 'hold':
            self.holds += 1
        elif self.correction or kind == 'guard':
            self.caught = True
        else:
            self.false_stops += 1
        self.line, self.message, self.stop_kind = 'stopped', message, kind
        self.workflow.audit.record('line_stopped', reason=message, kind=kind, scenario=self.scenario)

    def _restart(self, note):
        self.line, self.stop_kind, self.message = 'running', None, note
        self.workflow.audit.record('line_restarted', note=note, scenario=self.scenario)

    # ---- operator actions (the only way to restart a stopped line) ----------------------
    def available_actions(self) -> list[str]:
        if self.line != 'stopped':
            return []
        if self.stop_kind == 'estop':
            return ['release_estop']
        if self.stop_kind == 'guard':
            return ['acknowledge']
        if self.stop_kind == 'hold':
            return ['clear_hold']
        actions = ['correct'] if self.correction else []
        if self.workflow.state in (WorkflowState.BLOCKED, WorkflowState.NEEDS_REVIEW):
            actions.append('override')
        return actions

    def estop(self, operator: str, reason: str = 'Emergency stop pressed'):
        """Manual emergency stop: freezes the gantry mid-motion. Always available while running."""
        if self.line != 'running':
            raise RuntimeError('Line is not running')
        if not operator.strip():
            raise ValueError('operator identifier is required')
        self.workflow.audit.record('emergency_stop', actor=operator, reason=reason, scenario=self.scenario)
        self._stop(f'E-STOP by {operator}: {reason}. Gantry locked out.', 'estop')

    def release_estop(self, operator: str, reason: str):
        if 'release_estop' not in self.available_actions():
            raise RuntimeError('No emergency stop to release')
        if not operator.strip() or not reason.strip():
            raise ValueError('operator identifier and a typed reason are required to release an E-stop')
        self.workflow.audit.record('estop_released', actor=operator, reason=reason, scenario=self.scenario)
        self._restart(f'E-stop released by {operator}: {reason}')

    def note(self, text: str, frames: int = 25):
        """Show a banner on the next frames (used to label scripted operator actions)."""
        self.banner, self.banner_until = text, self.frames + frames

    def kpis(self, fps: int) -> dict:
        return dict(scenario=self.scenario, fault=self.fault is not None, caught=self.caught,
                    escaped=self.escaped, false_stops=self.false_stops, holds=self.holds, estops=self.estops,
                    inspections=self.inspections, line_s=round((self.frames - self.stopped_frames) / fps
                                                               + self.inspect_seconds, 2),
                    downtime_s=round(self.stopped_frames / fps, 2), completed=self.line == 'complete')

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
        self.handoff = f'Operator override by {operator} (logged with reason) -> {decision.status.value.upper()}'
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

_HANDOFF = {
    'cv_only': 'OpenCV: all parts certain',
    'cv_explained': 'OpenCV certain; Gemma added an explanation',
    'gemma_resolved': 'OpenCV unsure -> Gemma settled it',
    'gemma_conflict': 'OpenCV unsure -> Gemma disagreed',
    'gemma_unavailable': 'OpenCV unsure -> Gemma UNAVAILABLE',
    'foreign_unconfirmed': 'OpenCV: unrecognised object -> Gemma could not confirm',
    'agreed': 'Gemma read the frame; OpenCV agrees',
    'cv_unsure': 'Gemma read the frame; OpenCV partly unsure',
    'cv_veto': 'Gemma read the frame; OpenCV disagrees',
    'gemma_failed': 'Gemma FAILED or timed out',
}


def describe_handoff(inspector, decision, seconds: float) -> str:
    """Who contributed to this decision, from the inspector's own status (no labels involved)."""
    status = getattr(inspector, 'last_status', None)
    name = type(getattr(inspector, 'inner', inspector)).__name__
    if status == 'foreign_identified':
        named = (decision.observation.foreign_objects or ['?'])[0] if decision.observation else '?'
        head = f"OpenCV: unrecognised object -> Gemma: '{named}'"
    elif status in _HANDOFF:
        head = _HANDOFF[status]
    elif name == 'CVInspector':
        head = 'OpenCV only'
    elif name == 'GemmaInspector':
        head = 'Gemma only'
    else:
        head = name
    took = f'{seconds * 1000:.0f} ms' if seconds < 1 else f'{seconds:.1f} s'
    return f'{head} ({took}) -> rules: {decision.status.value.upper()}'


def annotate(raw: np.ndarray, monitor: LineMonitor, watchdog: str) -> np.ndarray:
    img = raw.copy()
    h, w = img.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    step = monitor.workflow.current_step
    cv2.rectangle(img, (0, 0), (w, 30), (25, 30, 40), -1)
    cv2.putText(img, f'Step {step.id}/{len(monitor.config.steps)}: {step.name}', (8, 21), font, 0.55,
                (255, 255, 255), 1, cv2.LINE_AA)
    label = {'running': 'LINE RUNNING', 'stopped': 'LINE STOPPED', 'complete': 'ASSEMBLY COMPLETE'}[monitor.line]
    if monitor.line == 'stopped' and monitor.stop_kind == 'estop':
        label = 'E-STOP'
    (tw, _), _ = cv2.getTextSize(label, font, 0.55, 2)
    cv2.rectangle(img, (w - tw - 18, 3), (w - 3, 27), _COLOURS[monitor.line], -1)
    cv2.putText(img, label, (w - tw - 10, 21), font, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    if ai_outage_active(monitor.workflow.inspector):
        cv2.rectangle(img, (w - tw - 150, 3), (w - tw - 24, 27), (230, 120, 20), -1)
        cv2.putText(img, 'AI OUTAGE', (w - tw - 142, 21), font, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    if monitor.banner and monitor.frames < monitor.banner_until:
        cv2.rectangle(img, (0, 32), (w, 58), (250, 205, 60), -1)
        cv2.putText(img, monitor.banner[:80], (8, 50), font, 0.5, (20, 20, 20), 1, cv2.LINE_AA)
    cv2.rectangle(img, (0, h - 84), (w, h), (25, 30, 40), -1)
    wd = {'moving': 'WATCHDOG: motion detected - not inspecting',
          'settling': f'WATCHDOG: still {monitor.still}/{monitor.settle_frames} frames',
          'inspecting': 'WATCHDOG: settled -> inspected', 'idle': 'WATCHDOG: idle'}[watchdog]
    if monitor.line == 'stopped' and watchdog == 'idle':
        wd = 'LINE STOPPED - gantry locked out until an operator acts'
    cv2.putText(img, wd, (8, h - 65), font, 0.45, (255, 210, 90), 1, cv2.LINE_AA)
    cv2.putText(img, 'AI: ' + monitor.handoff[:92], (8, h - 46), font, 0.40, (120, 220, 255), 1,
                cv2.LINE_AA)
    words, lines = monitor.message.split(), ['']
    for word in words:
        if len(lines[-1]) + len(word) + 1 > 84 and len(lines) < 2:
            lines.append('')
        lines[-1] = (lines[-1] + ' ' + word).strip()
    for i, line in enumerate(lines):
        cv2.putText(img, line[:90], (8, h - 27 + 18 * i), font, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
    if monitor.line == 'stopped':
        cv2.rectangle(img, (2, 32), (w - 3, h - 86), (210, 40, 40), 3)
    return img


# ---- scripted operator (CLI clips and unattended shifts; always labelled on screen) -------------
def scripted_action(monitor: LineMonitor, operator: str = 'scripted-operator') -> str | None:
    actions = monitor.available_actions()
    if 'correct' in actions:
        monitor.correct(operator)
        text = 'SCRIPTED OPERATOR: removed the faulty item; re-inspecting'
    elif 'acknowledge' in actions:
        monitor.acknowledge(operator)
        text = 'SCRIPTED OPERATOR: acknowledged the interlock'
    elif 'clear_hold' in actions:
        monitor.clear_hold(operator, 'Scripted operator: inspector restarted.')
        text = 'SCRIPTED OPERATOR: cleared the hold; re-inspecting'
    elif 'override' in actions:
        monitor.override(True, 'Scripted operator: visually verified the step.', operator)
        text = 'SCRIPTED OPERATOR: logged override (approve)'
    else:
        return None
    monitor.note(text)
    return text


# ---- a whole shift ------------------------------------------------------------------------------
FAULT_SCENARIOS = tuple(n for n in ALL_NAMES if n != 'correct')


class Shift:
    """Build ``units`` assemblies in a row; each unit gets a seeded random fault (or none).

    The fault plan only drives the simulated gantry and the KPI scoring; the inspector
    still only receives settled PNG paths. Operator actions are scripted when
    ``scripted_operator`` is on (labelled on every frame), or left to a human.
    """

    def __init__(self, config, environment, inspector, audit, directory, *, units=20, fault_rate=0.4,
                 seed=0, fps=10, scripted_operator=True, operator_delay_s=2.0):
        self.config, self.env, self.inspector, self.audit = config, environment, inspector, audit
        self.directory = Path(directory)
        self.units, self.fault_rate, self.fps = units, fault_rate, fps
        self.scripted_operator, self.delay_frames = scripted_operator, max(1, int(operator_delay_s * fps))
        self.rng = np.random.default_rng(seed)
        self.results: list[dict] = []
        self.monitor: LineMonitor | None = None
        self.unit = 0
        self.waited = 0
        self.audit.record('shift_started', units=units, fault_rate=fault_rate, seed=seed,
                          scripted_operator=scripted_operator)
        self._next_unit()

    def _next_unit(self):
        self.unit += 1
        scenario = 'correct' if self.rng.random() >= self.fault_rate else str(self.rng.choice(FAULT_SCENARIOS))
        self.env.reset()
        workflow = Workflow(self.config, self.inspector, self.audit)
        self.monitor = LineMonitor(self.config, self.env, workflow, scenario,
                                   self.directory / f'unit-{self.unit:03d}')
        self.monitor.note(f'Unit {self.unit}/{self.units} started', 15)

    @property
    def line(self):
        if self.monitor.line == 'complete' and len(self.results) >= self.units:
            return 'complete'
        if self.monitor.line == 'stopped':
            # A scripted operator will act on ordinary stops, so the shift keeps ticking (the frame
            # still shows LINE STOPPED). An E-stop always halts the shift for a human.
            if self.scripted_operator and self.monitor.stop_kind != 'estop':
                return 'running'
            return 'stopped'
        return 'running'

    def __getattr__(self, name):            # operator actions and display state of the current unit
        if name in ('message', 'available_actions', 'correct', 'acknowledge', 'override', 'clear_hold',
                    'estop', 'release_estop', 'decisions', 'scenario', 'workflow', 'handoff'):
            return getattr(self.monitor, name)
        raise AttributeError(name)

    def tick(self) -> LineFrame:
        if self.monitor.line == 'complete' and len(self.results) < self.units:
            self.results.append(dict(unit=self.unit, **self.monitor.kpis(self.fps)))
            self.audit.record('shift_unit_completed', **self.results[-1])
            if len(self.results) < self.units:
                self._next_unit()
            else:
                self.audit.record('shift_completed', **self.kpis())
        frame = self.monitor.tick()
        if self.scripted_operator and self.monitor.line == 'stopped' and self.monitor.stop_kind != 'estop':
            self.waited += 1
            if self.waited >= self.delay_frames:
                scripted_action(self.monitor)
                self.waited = 0
        else:
            self.waited = 0
        return frame

    def kpis(self) -> dict:
        done = self.results
        faults = [r for r in done if r['fault']]
        line_s = sum(r['line_s'] for r in done)
        downtime = sum(r['downtime_s'] for r in done)
        stops = sum(r['caught'] + r['false_stops'] + r['holds'] for r in done)
        return dict(units_completed=len(done), units_planned=self.units, faults_injected=len(faults),
                    faults_caught=sum(r['caught'] for r in faults), faults_escaped=sum(r['escaped'] for r in done),
                    false_stops=sum(r['false_stops'] for r in done), holds=sum(r['holds'] for r in done),
                    estops=sum(r['estops'] for r in done),
                    mean_cycle_s=round((line_s + downtime) / len(done), 2) if done else None,
                    mean_downtime_per_stop_s=round(downtime / stops, 2) if stops else None,
                    units_per_hour=round(3600 * len(done) / (line_s + downtime), 1) if done else None)


# ---- CLI: run with a scripted operator and write an MP4 ------------------------------------------
def run_scenario(config, inspector, scenario, output, *, fps=10, pause_s=2.0, max_frames=900,
                 operator='scripted-operator'):
    output = Path(output)
    audit = AuditLog(output / 'audit.jsonl')
    frames = []
    with AssemblyEnv(config, seed=0) as env:
        monitor = LineMonitor(config, env, Workflow(config, inspector, audit), scenario, output / 'frames')
        stopped_for = 0
        while monitor.line != 'complete' and len(frames) < max_frames:
            frames.append(monitor.tick().image)
            if monitor.line != 'stopped':
                stopped_for = 0
                continue
            stopped_for += 1
            if stopped_for >= pause_s * fps and not scripted_action(monitor, operator):
                break
        for _ in range(int(pause_s * fps)):
            frames.append(monitor.tick().image)
    return monitor, frames


def run_shift(config, inspector, output, *, units=20, fault_rate=0.4, seed=0, fps=10, max_frames=20000):
    output = Path(output)
    frames = []
    with AssemblyEnv(config, seed=0) as env:
        shift = Shift(config, env, inspector, AuditLog(output / 'audit.jsonl'), output / 'units',
                      units=units, fault_rate=fault_rate, seed=seed, fps=fps)
        while shift.line != 'complete' and len(frames) < max_frames:
            frames.append(shift.tick().image)
    return shift, frames


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
    parser.add_argument('--shift', type=int, metavar='UNITS', help='run a shift of UNITS assemblies instead')
    parser.add_argument('--fault-rate', type=float, default=0.4)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--inspector-factory', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fps', type=int, default=10)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f'{args.output} exists; choose a new output directory')
    from src.simulation.runner import load_inspector
    config = load_config()
    inspector = load_inspector(args.inspector_factory)
    if args.shift:
        shift, clip = run_shift(config, inspector, args.output, units=args.shift, fault_rate=args.fault_rate,
                                seed=args.seed, fps=args.fps)
        summary = dict(inspector=type(inspector).__name__, operator='scripted', fps=args.fps, seed=args.seed,
                       fault_rate=args.fault_rate, kpis=shift.kpis(), units=shift.results)
        print(json.dumps(summary['kpis'], indent=2))
    else:
        names = ALL_NAMES if args.scenario == 'all' else (args.scenario,)
        runs, clip = [], []
        for name in names:
            monitor, frames = run_scenario(config, inspector, name, args.output / name, fps=args.fps)
            clip += frames
            runs.append(dict(decisions=monitor.decisions, frames=len(frames), **monitor.kpis(args.fps)))
            print(runs[-1])
        summary = dict(inspector=type(inspector).__name__, operator='scripted', fps=args.fps, runs=runs)
    write_mp4(clip, args.output / 'live-line.mp4', args.fps)
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print('Wrote', args.output / 'live-line.mp4')


if __name__ == '__main__':
    main()
