"""Dashboard adapter using existing mock and control contracts only."""
from pathlib import Path
from uuid import uuid4

from src.config import load_config
from src.control.audit_log import AuditLog
from src.control.cli import MockAssemblyEnvironment, MockInspector
from src.control.workflow import Workflow


class DashboardSession:
    def __init__(self, directory='outputs/dashboard', *, inspector=None):
        self.directory = Path(directory) / uuid4().hex
        self.config = load_config()
        self.environment = MockAssemblyEnvironment()
        self.audit = AuditLog(self.directory / 'audit.jsonl')
        self.workflow = Workflow(self.config, inspector or MockInspector(self.config), self.audit)
        self.frame = None

    def place(self, part):
        check = self.workflow.check_action(part)
        if check.allowed:
            self.environment.place_part(part)
        return check

    def remove(self, part, reason, operator):
        if not reason.strip() or not operator.strip():
            raise ValueError('operator and reason are required for corrections')
        origin = next((s for s in self.config.steps if part in s.adds_parts), None)
        if origin and (origin.id < self.workflow.current_step_id
                       or self.workflow.state.value == 'complete'):
            self.workflow.invalidate_part(part, reason, operator)
        self.audit.record('mock_part_removed', part=part, reason=reason, actor=operator)
        self.environment.remove_part(part)

    def inspect(self):
        self.frame = self.environment.render(self.directory, 'dashboard', self.workflow.current_step_id)
        return self.workflow.request_inspection(self.frame)

    def override(self, approve, reason, operator):
        return self.workflow.operator_override(approve, reason, operator)
