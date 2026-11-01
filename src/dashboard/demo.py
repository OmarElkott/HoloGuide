"""Dashboard orchestration of existing control scenarios, explicitly mock only."""
from io import StringIO

from src.control.cli import SCENARIOS, MockInspector, run_mock_scenario
from src.dashboard.session import DashboardSession


class EdgeCaseInspector:
    """Demo-only fault injection; does not pretend to time a real model call."""
    def __init__(self, config):
        self.delegate = MockInspector(config)
        self.fault = 'review'

    def inspect(self, image_path, step):
        if self.fault == 'timeout':
            raise TimeoutError('Scripted mock timeout injection; no slow model call')
        result = self.delegate.inspect(image_path, step)
        return result.model_copy(update={'confidence': .5})


class DemoController:
    """One existing scenario per tick, then review/HOLD/override via Workflow.

    Episodes have separate workflows/audits. UI shows each result and retains
    history rather than presenting separate episodes as a single assembly.
    """
    stages = ('correct', 'missing_gasket', 'wrong_cover', 'wrong_order',
              'review', 'timeout', 'clear_hold', 'override')

    def __init__(self, directory='outputs/dashboard-demo'):
        self.directory = directory
        self.index = 0
        self.running = True
        self.history = []
        self.session = None
        self.error = None

    def stop(self):
        self.running = False

    def close(self):
        self.stop()
        if self.session:
            self.session.close()

    def tick(self):
        if not self.running or self.index >= len(self.stages):
            return
        stage = self.stages[self.index]
        try:
            if stage in SCENARIOS:
                if self.session:
                    self.session.close()
                session = DashboardSession(self.directory)
                output = StringIO()
                result = run_mock_scenario(
                    stage, config=session.config, environment=session.environment,
                    inspector=session.workflow.inspector, audit=session.audit,
                    frames_dir=session.directory, output=output)
                session.workflow = result.workflow
                session.render()
                self.session = session
                detail = output.getvalue()
                if not result.completed:
                    raise RuntimeError(result.safe_stop_reason or 'Scenario did not complete')
            elif stage == 'review':
                self.session.close()
                self.session = DashboardSession(self.directory)
                self.session.workflow.inspector = EdgeCaseInspector(self.session.config)
                self.session.place(self.session.workflow.current_step.adds_parts[0])
                detail = self.session.inspect().message
            elif stage == 'timeout':
                self.session.workflow.inspector.fault = 'timeout'
                detail = self.session.inspect().message
            elif stage == 'clear_hold':
                self.session.workflow.clear_hold('demo-operator', 'Demonstration: injected fault cleared')
                self.session.workflow.inspector.fault = 'review'
                detail = self.session.inspect().message
            else:
                detail = self.session.override(True,
                    'DEMONSTRATION ONLY: mock fixture checked manually', 'demo-operator').message
            self.history.append({'stage': stage, 'detail': detail,
                                 'audit_path': str(self.session.audit.path)})
            self.index += 1
            self.running = self.index < len(self.stages)
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'
            self.running = False
