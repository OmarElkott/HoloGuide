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
    from src.dashboard.offline_check import OfflineReport
    monkeypatch.setattr(panel, 'FPS', 1000)
    monkeypatch.setattr(panel, 'run_check', lambda *a, **k: OfflineReport(True, True, False, ['stub']))
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


# ---- E-stop, handoff, scripted operator, shift, AI outage ------------------------------------

def test_estop_freezes_mid_motion_and_needs_operator_and_reason(tmp_path):
    env, m = monitor(tmp_path, 'correct')
    with env:
        for _ in range(4):
            m.tick()
        assert m.anim is not None                                  # the plate is mid-drop
        m.estop('operator-1', 'Hand in the cell')
        frozen = [m.tick() for _ in range(5)]
        assert all(f.line == 'stopped' for f in frozen) and m.anim is not None
        assert m.available_actions() == ['release_estop']
        with pytest.raises(ValueError):
            m.release_estop('operator-1', ' ')
        m.release_estop('operator-1', 'Cell clear')
        while m.line == 'running':
            m.tick()
        assert m.line == 'complete' and m.estops == 1 and not m.caught and m.false_stops == 0
    events = [e['event_type'] for e in m.workflow.audit.read_all()]
    assert 'emergency_stop' in events and 'estop_released' in events


def test_handoff_line_names_who_contributed(tmp_path):
    from src.vision.foreign_objects import Identification
    from src.vision.hybrid_inspector import HybridInspector

    class Ident:
        def identify(self, image_path, box):
            return Identification('tool', 'purple screwdriver')

    class NoGemma(InspectorABC):
        def inspect(self, image_path, step):
            raise AssertionError('not needed')
    inspector = HybridInspector(CFG, CVInspector(CFG), NoGemma(), identifier=Ident())
    env, m = monitor(tmp_path, 'foreign_object', inspector)
    with env:
        run_until_stop(m)
        assert m.handoff.startswith("OpenCV: unrecognised object -> Gemma: 'purple screwdriver (tool)'")
        assert m.handoff.endswith('-> rules: BLOCK') and m.caught


def test_scripted_operator_is_labelled_on_screen(tmp_path):
    from src.simulation.live_monitor import scripted_action
    env, m = monitor(tmp_path, 'wrong_cover')
    with env:
        run_until_stop(m)
        assert scripted_action(m).startswith('SCRIPTED OPERATOR')
        assert m.banner.startswith('SCRIPTED OPERATOR') and m.frames < m.banner_until


def test_shift_counts_faults_and_never_lets_one_escape(tmp_path):
    from src.simulation.live_monitor import run_shift
    shift, frames = run_shift(CFG, Recorder(), tmp_path / 'shift', units=6, fault_rate=0.7, seed=3)
    k = shift.kpis()
    assert k['units_completed'] == 6 and k['faults_injected'] >= 1
    assert k['faults_caught'] == k['faults_injected'] and k['faults_escaped'] == 0 and k['false_stops'] == 0
    assert k['units_per_hour'] > 0 and frames


def test_ai_outage_hybrid_keeps_known_parts_and_reviews_foreign_objects(tmp_path):
    from src.vision.foreign_objects import Identification
    from src.vision.hybrid_inspector import HybridInspector
    from src.vision.outage import ai_outage_active, set_ai_outage

    class Ident:
        def identify(self, image_path, box):
            return Identification('tool', 'screwdriver')
    inspector = HybridInspector(CFG, CVInspector(CFG), Recorder(), identifier=Ident())
    assert set_ai_outage(inspector, True) and ai_outage_active(inspector)
    env, m = monitor(tmp_path, 'foreign_object', inspector)
    with env:
        run_until_stop(m)
        assert m.decisions == ['pass', 'pass', 'needs_review']     # never PASS without Gemma
        assert 'AI OUTAGE' in m.workflow.last_decision.observation.rationale
    set_ai_outage(inspector, False)
    assert not ai_outage_active(inspector) and isinstance(inspector.identifier, Ident)


def test_ai_outage_gemma_first_holds(tmp_path):
    from src.vision.gemma_first_inspector import GemmaFirstInspector
    from src.vision.outage import set_ai_outage
    inspector = GemmaFirstInspector(CFG, Recorder(), CVInspector(CFG))
    set_ai_outage(inspector, True)
    env, m = monitor(tmp_path, 'correct', inspector)
    with env:
        run_until_stop(m)
        assert m.decisions == ['hold'] and m.holds == 1


def test_outage_switch_refuses_inspectors_without_gemma():
    from src.vision.outage import set_ai_outage
    assert set_ai_outage(CVInspector(CFG), True) is False


def test_dashboard_whole_shift_shows_kpis(monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    import src.dashboard.live_line_panel as panel
    from src.dashboard.offline_check import OfflineReport
    monkeypatch.setattr(panel, 'FPS', 1000)
    monkeypatch.setattr(panel, 'OPERATOR_DELAY_S', 0.005)
    monkeypatch.setattr(panel, 'run_check', lambda *a, **k: OfflineReport(True, True, False, ['stub']))
    monkeypatch.setenv('HOLOGUIDE_BACKEND_FACTORY', 'tests.test_live_monitor:real_cv_session')
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'src/dashboard/app.py'), default_timeout=300)
    app.run()
    next(s for s in app.selectbox if s.label == 'Operation').set_value('Real').run()
    next(b for b in app.button if b.label.startswith('Start selected mode')).click().run()
    assert any('OFFLINE VERIFIED' in s.value for s in app.success)
    assert not any(b.label == 'Run scripted mock demo' for b in app.button)
    next(r for r in app.radio if r.label == 'Run').set_value('Whole shift').run()
    next(n for n in app.number_input if n.label == 'Units').set_value(3).run()
    next(b for b in app.button if b.label == 'Start line').click().run()
    for _ in range(20):                  # a browser follows st.rerun() between chunks; AppTest needs a nudge
        if any('Line complete' in s.value for s in app.success):
            break
        app.run()
    metrics = {m.label: m.value for m in app.metric}
    assert metrics['Units done'] == '3/3' and metrics['Faults escaped'] == '0'
    assert any('Line complete' in s.value for s in app.success)


def test_estop_halts_a_scripted_shift_until_a_human_releases_it(tmp_path):
    from src.simulation.live_monitor import Shift
    with AssemblyEnv(CFG, seed=0) as env:
        shift = Shift(CFG, env, Recorder(), AuditLog(tmp_path / 'a.jsonl'), tmp_path / 's', units=2,
                      fault_rate=0.0, scripted_operator=True)
        for _ in range(5):
            shift.tick()
        shift.estop('operator-1', 'Hand in the cell')
        for _ in range(60):                       # far longer than the scripted operator's delay
            shift.tick()
        assert shift.line == 'stopped' and shift.available_actions() == ['release_estop']
        shift.release_estop('operator-1', 'Cell clear')
        while shift.line != 'complete':
            shift.tick()
        assert shift.kpis()['estops'] == 1 and shift.kpis()['units_completed'] == 2
