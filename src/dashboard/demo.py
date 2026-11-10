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


class DemonstrationFaultInspector:
    """Delegate real inspection first; labelled faults never mask real failures."""
    def __init__(self, inspector, threshold=.7):
        self.inspector = inspector
        self.threshold = threshold
        self.fault = None
        self.applied = False

    def inspect(self, image_path, step):
        from src.evaluation.diagnostics import counters, outcome
        self.applied = False
        before = counters(self.inspector)
        observation = self.inspector.inspect(image_path, step)
        _, error, _ = outcome(observation, before)
        if error or observation.confidence < self.threshold:
            return observation
        if self.fault == 'timeout':
            self.applied = True
            from src.vision.inspector import timeout_observation
            return timeout_observation(0).model_copy(update={
                'rationale': 'TIMEOUT: DEMONSTRATION INJECTION after real inspection; not a measured model timeout'})
        if self.fault == 'review':
            self.applied = True
            return observation.model_copy(update={
                'confidence': min(observation.confidence, self.threshold / 2),
                'rationale': 'DEMONSTRATION: confidence deliberately capped after real inspection. ' + observation.rationale})
        return observation


class RealDemoController:
    """One real scenario tick per UI update; stop on unexpected real decisions.

    Physical fault construction/corrections are P1 ScenarioRunner's responsibility.
    Edge injections are explicit adapter actions, never model-performance evidence.
    """
    stages = DemoController.stages

    def __init__(self, session_factory, runner_factory=None):
        self.factory = session_factory
        self.runner_factory = runner_factory
        self.index = 0
        self.running = True
        self.history = []
        self.session = None
        self.runner = None
        self.error = None
        self.mode = 'real'

    def stop(self):
        self.running = False

    def close(self):
        self.stop()
        if self.session:
            self.session.close()

    def _session(self):
        if self.session:
            self.session.close()
        session = self.factory()
        if session.mode != 'real':
            session.close()
            raise ValueError('Real demo requires an explicit real session; no fallback')
        self.session = session

    def tick(self):
        if not self.running:
            return
        stage = self.stages[self.index]
        try:
            if stage in SCENARIOS:
                if self.runner is None:
                    if self.runner_factory is None:
                        from src.simulation.runner import ScenarioRunner
                        self.runner_factory = ScenarioRunner
                    self._session()
                    self.runner = self.runner_factory(stage, self.session.environment,
                                                 self.session.workflow, self.session.directory / 'scenario-frames')
                from time import perf_counter
                step_id = self.session.workflow.current_step_id
                start = perf_counter()
                result = self.runner.tick()
                self.session.frame = self.runner.frame
                self.session.inspected_frame = self.runner.frame
                self.session.inspected_step_id = step_id
                self.session.revision += 1
                self.session.latency_s = None  # runner timing includes rendering; not workflow latency
                elapsed = perf_counter() - start
                if result.error:
                    raise RuntimeError(result.error)
                detail = self.session.workflow.last_decision.message
                self.history.append({'stage': stage,
                    'detail': f'Step {step_id}: {detail} (scenario tick {elapsed:.3f}s, including render)',
                    'audit_path': str(self.session.audit.path)})
                if not self.runner.done:
                    return
                if not result.completed:
                    raise RuntimeError('Scenario stopped before completion')
                self.runner = None
            else:
                if stage == 'review':
                    self._session()
                    self.edge = DemonstrationFaultInspector(self.session.workflow.inspector,
                                                           self.session.config.confidence_threshold)
                    self.session.workflow.inspector = self.edge
                    for part in self.session.workflow.current_step.adds_parts:
                        if not self.session.place(part).allowed:
                            raise RuntimeError('Demo base placement rejected')
                if stage in {'review', 'timeout', 'clear_hold'}:
                    if stage == 'clear_hold':
                        self.session.workflow.clear_hold('demo-operator', 'DEMONSTRATION: injected timeout cleared')
                    self.edge.fault = 'timeout' if stage == 'timeout' else 'review'
                    self.session.audit.record('demonstration_fault_armed', kind=self.edge.fault,
                                              reason='Explicit presentation injection; not observed model behavior')
                    decision = self.session.inspect()
                    if not self.edge.applied:
                        raise RuntimeError('Real inspection was uncertain or failed; presentation injection not applied')
                    expected = 'hold' if stage == 'timeout' else 'needs_review'
                    if decision.status.value != expected:
                        raise RuntimeError(f'Expected demonstration {expected}, received {decision.status.value}: {decision.message}')
                else:
                    decision = self.session.override(True,
                        'DEMONSTRATION ONLY: scripted operator approval of the rendered base plate', 'demo-operator')
                self.history.append({'stage': stage, 'detail': decision.message,
                                     'audit_path': str(self.session.audit.path)})
            self.index += 1
            self.running = self.index < len(self.stages)
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'
            self.running = False
