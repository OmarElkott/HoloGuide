"""Dashboard adapter using existing mock and control contracts only."""
from pathlib import Path
from uuid import uuid4
from time import perf_counter
from weakref import finalize

from src.config import load_config
from src.control.audit_log import AuditLog
from src.control.cli import MockAssemblyEnvironment, MockInspector
from src.control.workflow import Workflow


class DashboardSession:
    def __init__(self, directory='outputs/dashboard', *, inspector=None, environment=None,
                 mode='mock', endpoint=None, model=None):
        if mode not in {'mock', 'real'}:
            raise ValueError('mode must be mock or real')
        if mode == 'real' and (environment is None or inspector is None):
            raise ValueError('Real mode requires an environment and inspector; no mock fallback')
        self.directory = Path(directory) / uuid4().hex
        self.config = load_config()
        self.mode, self.endpoint, self.model = mode, endpoint, model
        self.environment = environment if environment is not None else MockAssemblyEnvironment()
        self.audit = AuditLog(self.directory / 'audit.jsonl')
        self.workflow = Workflow(self.config, inspector if inspector is not None else MockInspector(self.config), self.audit)
        self.frame = None
        self.inspected_frame = None
        self.inspected_step_id = None
        self.latency_s = None
        self.revision = 0
        self.closed = False
        close = getattr(self.environment, 'close', None)
        self._cleanup = finalize(self, close) if close else None

    def close(self):
        if not self.closed:
            if self._cleanup:
                self._cleanup()
            self.closed = True

    def render(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.mode == 'mock':
            self.frame = self.environment.render(self.directory, 'dashboard', self.workflow.current_step_id)
        else:
            target = self.directory / f'camera-{uuid4().hex}.png'
            self.frame = Path(self.environment.render_to_file(str(target)))
        return self.frame

    def _consume(self, revision):
        if revision is not None and revision != self.revision:
            raise ValueError('This submission is stale; use the current controls')
        self.revision += 1

    def place(self, part):
        check = self.workflow.check_action(part)
        if check.allowed:
            self.environment.place_part(part)
            self.frame = None
            self.revision += 1
        return check

    def remove(self, part, reason, operator):
        if not reason.strip() or not operator.strip():
            raise ValueError('operator and reason are required for corrections')
        origin = next((s for s in self.config.steps if part in s.adds_parts), None)
        if origin and (origin.id < self.workflow.current_step_id
                       or self.workflow.state.value == 'complete'):
            self.workflow.invalidate_part(part, reason, operator)
        self.audit.record('part_removal_requested', mode=self.mode, part=part, reason=reason, actor=operator)
        self.environment.remove_part(part)
        self.audit.record('part_removed', mode=self.mode, part=part, reason=reason, actor=operator)
        self.frame = None
        self.revision += 1

    def inspect(self, revision=None):
        self._consume(revision)
        self.render()
        self.inspected_frame = self.frame
        self.inspected_step_id = self.workflow.current_step_id
        start = perf_counter()
        try:
            return self.workflow.request_inspection(self.frame)
        finally:
            self.latency_s = perf_counter() - start

    def override(self, approve, reason, operator, revision=None):
        self._consume(revision)
        return self.workflow.operator_override(approve, reason, operator)
