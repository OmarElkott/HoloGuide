"""Bounded scenario playback through the existing production Workflow.

Fault injection is explicit and audited. Only rendered PNG paths reach inspection.
No runner code changes the workflow step, overrides decisions, or supplies labels.
"""
import argparse
import importlib
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

from src.config import load_config
from src.control.audit_log import AuditLog
from src.control.workflow import Workflow
from src.simulation.assembly_env import AssemblyEnv
from src.simulation.scenarios import NAMES, get_scenario


@dataclass
class RunResult:
    scenario: str
    completed: bool = False
    fault_status: str | None = None
    recovered: bool = False
    guard_blocked: bool = False
    recovery_s: float | None = None
    decisions: list[str] = field(default_factory=list)
    error: str | None = None


class ScenarioRunner:
    """One step per tick, suitable for CLI, dashboard and recovery evaluation.

    The caller owns and closes the environment. A fresh workflow is required.
    The action-order guard is tested separately from deliberate physical injection.
    """
    def __init__(self, name, environment, workflow, directory):
        get_scenario(name)
        if workflow.current_step_id != 1 or workflow.last_decision is not None:
            raise ValueError('Scenario requires a fresh workflow')
        self.name, self.env, self.workflow = name, environment, workflow
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.result = RunResult(name)
        self.done = False
        self.fault_done = name == 'correct'
        self.pending_fix = False
        self.frame = None
        self.ticks = 0
        self.started_recovery = None
        self.present = set()  # scripted actions, never passed to inspector
        self.workflow.audit.record('scenario_started', scenario=name, mode='rendered')

    def _place(self, part, *, fault=False):
        if part in self.present:
            return
        if not fault:
            check = self.workflow.check_action(part)
            if not check.allowed:
                raise RuntimeError(check.reason)
        else:
            self.workflow.audit.record('simulation_fault_injected', part=part, scenario=self.name)
        self.env.place_part(part)
        self.present.add(part)

    def _remove(self, part):
        if part in self.present:
            self.env.remove_part(part)
            self.present.remove(part)
            self.workflow.audit.record('scenario_correction', action='remove', part=part)

    def tick(self):
        if self.done:
            return self.result
        try:
            self._tick()
        except Exception as exc:
            self.result.error = f'{type(exc).__name__}: {exc}'
            self.done = True
        return self.result

    def _tick(self):
        self.ticks += 1
        if self.ticks > len(self.workflow.config.steps) + 3:
            raise RuntimeError('Scenario exceeded bounded action budget')
        step = self.workflow.current_step
        if self.pending_fix:
            if self.name in ('missing_gasket', 'wrong_order'):
                self._remove('cover')
            elif self.name == 'wrong_cover':
                self._remove('wrong_cover')
            self.pending_fix = False
            self.workflow.audit.record('scenario_correction_started', scenario=self.name)
        trigger = {'missing_gasket': 2, 'wrong_cover': 3, 'wrong_order': 1}.get(self.name)
        injecting = not self.fault_done and step.id == trigger
        if injecting:
            self.fault_done = True
            if self.name == 'wrong_order':
                check = self.workflow.check_action('cover')
                self.result.guard_blocked = not check.allowed
                if check.allowed:
                    raise RuntimeError('Order guard allowed premature cover')
            if self.name == 'wrong_cover':
                self._place('wrong_cover', fault=True)
            else:
                self._place('cover', fault=True)
                if self.name == 'wrong_order':
                    for part in step.adds_parts:
                        self._place(part)
        else:
            for part in step.adds_parts:
                self._place(part)
        self.frame = Path(self.env.render_to_file(str(self.directory / f'{self.ticks:03d}.png')))
        decision = self.workflow.request_inspection(self.frame)
        status = decision.status.value
        self.result.decisions.append(status)
        if injecting:
            self.result.fault_status = status
            if status != 'block':
                raise RuntimeError(f'Injected fault expected block, received {status}; stopping')
            self.started_recovery = perf_counter()
            self.pending_fix = True
        elif status != 'pass':
            raise RuntimeError(f'Inspection returned {status}; human review required')
        elif self.started_recovery is not None and not self.result.recovered:
            self.result.recovered = True
            self.result.recovery_s = perf_counter() - self.started_recovery
        self.result.completed = self.workflow.state.value == 'complete'
        self.done = self.result.completed
        if self.done:
            self.workflow.audit.record('scenario_completed', scenario=self.name)

    def run(self):
        while not self.done:
            self.tick()
        return self.result


def load_inspector(spec, step_id_interface=False):
    module, name = spec.split(':', 1)
    inspector = getattr(importlib.import_module(module), name)()
    if step_id_interface:
        from src.dashboard.backends import StepIdInspectorAdapter
        inspector = StepIdInspectorAdapter(inspector)
    return inspector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=NAMES, default='missing_gasket')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--inspector-factory', required=True)
    parser.add_argument('--step-id-interface', action='store_true')
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    config = load_config()
    inspector = load_inspector(args.inspector_factory, args.step_id_interface)
    with AssemblyEnv(config, seed=args.seed) as env:
        workflow = Workflow(config, inspector, AuditLog(args.output / 'audit.jsonl'))
        result = ScenarioRunner(args.scenario, env, workflow, args.output / 'frames').run()
    print(result)
    if not result.completed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
