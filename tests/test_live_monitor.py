"""Live line monitor: motion, watchdog, interlock and operator restarts."""
import pytest

from src.config import load_config
from src.control.audit_log import AuditLog
from src.control.workflow import Workflow
from src.simulation.assembly_env import AssemblyEnv
from src.simulation.live_monitor import LineMonitor, run_scenario, write_mp4
from src.simulation.scenarios import ALL_NAMES
from src.vision.cv_inspector import CVInspector
from src.vision.inspector import InspectorABC
from src.vision.schemas import Observation

CFG = load_config()


class Recorder(InspectorABC):
    """OpenCV, recording what it was given (only a path and a StepSpec)."""
    def __init__(self):
        self.cv, self.calls = CVInspector(CFG), []

    def inspect(self, image_path, step):
        self.calls.append((image_path, step))
        return self.cv.inspect(image_path, step)


def monitor(tmp_path, scenario, inspector=None):
    env = AssemblyEnv(CFG, seed=0)
    wf = Workflow(CFG, inspector or Recorder(), AuditLog(tmp_path / 'audit.jsonl'))
    return env, LineMonitor(CFG, env, wf, scenario, tmp_path / 'frames')


def run_until_stop(m, limit=200):
    for _ in range(limit):
        frame = m.tick()
        if m.line != 'running':
            return frame
    raise AssertionError('line never stopped')


def test_no_inspection_while_anything_moves(tmp_path):
    inspector = Recorder()
    env, m = monitor(tmp_path, 'correct', inspector)
    with env:
        watch = [m.tick().watchdog for _ in range(10)]          # the base plate is still dropping
        assert watch == ['moving'] * 10 and inspector.calls == []
        while not inspector.calls:
            m.tick()
        from src.simulation.live_monitor import DROP_FRAMES
        assert m.frames >= DROP_FRAMES + m.settle_frames     # drop finished, then N still frames
        assert all(isinstance(path.__fspath__(), str) for path, _ in inspector.calls)


@pytest.mark.parametrize('scenario,expected', [('missing_gasket', 'block'), ('wrong_cover', 'block'),
                                               ('foreign_object', 'needs_review')])
def test_a_mistake_stops_the_line_until_the_operator_corrects_it(tmp_path, scenario, expected):
    env, m = monitor(tmp_path, scenario)
    with env:
        run_until_stop(m)
        assert m.line == 'stopped' and m.decisions[-1] == expected
        frozen = [m.tick() for _ in range(15)]                    # nothing moves, nothing restarts
        assert all(f.line == 'stopped' for f in frozen) and not m.queue and m.anim is None
        m.correct('operator-1')
        while m.line == 'running':
            m.tick()
        assert m.line == 'complete' and m.decisions.count(expected) == 1
        assert m.decisions[m.decisions.index(expected) + 1] == 'pass'      # re-inspection passes


def test_order_guard_refuses_the_pick_before_any_motion(tmp_path):
    env, m = monitor(tmp_path, 'wrong_order')
    with env:
        frame = m.tick()
        assert frame.line == 'stopped' and frame.event == 'guard_refused'
        assert env.parts_present() == [] and 'INTERLOCK' in m.message
        assert m.available_actions() == ['acknowledge']
        m.acknowledge('operator-1')
        while m.line == 'running':
            m.tick()
        assert m.line == 'complete'


def test_hold_stops_the_line_and_needs_a_cleared_hold(tmp_path):
    class Failing(InspectorABC):
        def inspect(self, image_path, step):
            return Observation(confidence=0.0, rationale='TIMEOUT: no answer')
    env, m = monitor(tmp_path, 'correct', Failing())
    with env:
        run_until_stop(m)
        assert m.decisions == ['hold'] and m.available_actions() == ['clear_hold']
        with pytest.raises(RuntimeError):
            m.correct('operator-1')
        m.clear_hold('operator-1', 'Inspector restarted.')
        assert m.line == 'running' and m.awaiting


def test_override_needs_a_reason_and_is_audited(tmp_path):
    class Unsure(InspectorABC):
        def inspect(self, image_path, step):
            return Observation(parts_detected=['base_plate'], confidence=0.4, rationale='unsure')
    env, m = monitor(tmp_path, 'correct', Unsure())
    with env:
        run_until_stop(m)
        assert 'override' in m.available_actions()
        with pytest.raises(ValueError):
            m.override(True, '', 'operator-1')
        m.override(True, 'Plate visually confirmed.', 'operator-1')
        assert m.line == 'running' and m.workflow.current_step_id == 2
    events = [e['event_type'] for e in m.workflow.audit.read_all()]
    assert 'line_stopped' in events and 'operator_override' in events and 'line_restarted' in events


@pytest.mark.parametrize('scenario', ALL_NAMES)
def test_every_scenario_completes_with_the_scripted_operator_and_writes_mp4(tmp_path, scenario):
    m, frames = run_scenario(CFG, Recorder(), scenario, tmp_path / scenario, pause_s=0.3)
    assert m.line == 'complete' and frames
    assert write_mp4(frames, tmp_path / 'clip.mp4').stat().st_size > 0


def test_needs_a_fresh_workflow(tmp_path):
    env, m = monitor(tmp_path, 'correct')
    with env:
        for _ in range(20):
            m.tick()
        with pytest.raises(ValueError):
            LineMonitor(CFG, env, m.workflow, 'correct', tmp_path / 'again')


# ---- dashboard -------------------------------------------------------------------------------

def real_cv_session():
    from src.dashboard.session import DashboardSession
    return DashboardSession(environment=AssemblyEnv(CFG, seed=0), inspector=CVInspector(CFG), mode='real',
                            model='opencv', endpoint='none')


def test_dashboard_live_line_stops_and_operator_restarts(monkeypatch, tmp_path):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    import src.dashboard.live_line_panel as panel
    monkeypatch.setattr(panel, 'FPS', 1000)
    monkeypatch.setenv('HOLOGUIDE_BACKEND_FACTORY', 'tests.test_live_monitor:real_cv_session')
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'src/dashboard/app.py'), default_timeout=120)
    app.run()
    next(s for s in app.selectbox if s.label == 'Operation').set_value('Real').run()
    next(b for b in app.button if b.label.startswith('Start selected mode')).click().run()
    next(s for s in app.selectbox if s.label == 'Line scenario').set_value('wrong_order').run()
    next(b for b in app.button if b.label == 'Start line').click().run()
    assert any('LINE STOPPED' in e.value and 'INTERLOCK' in e.value for e in app.error)
    next(t for t in app.text_input if t.label == 'Operator identifier').input('operator-1').run()
    next(b for b in app.button if b.label.startswith('Acknowledge the interlock')).click().run()
    assert any('Assembly complete' in s.value for s in app.success)
