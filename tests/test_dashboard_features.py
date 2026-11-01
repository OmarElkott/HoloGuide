import json
from pathlib import Path

import pytest
from PIL import Image

from src.dashboard.backends import StepIdInspectorAdapter
from src.dashboard.demo import DemoController
from src.dashboard.presentation import STATE_BANNERS, camera_image, filter_audit
from src.dashboard.session import DashboardSession
from src.vision.schemas import Observation


def test_camera_missing_mock_corrupt_and_rgb(tmp_path):
    assert camera_image(None)[0] is None
    assert 'Mock JSON' in camera_image(tmp_path / 'mock.json')[1]
    path = tmp_path / 'scene.png'
    assert camera_image(path)[0] is None
    path.write_bytes(b'not an image')
    assert camera_image(path)[0] is None
    Image.new('RGB', (16, 16), 'blue').save(path)
    assert camera_image(path) == (path.read_bytes(), None)


def test_every_control_state_has_banner_and_audit_validation():
    from src.control.workflow import WorkflowState
    assert set(STATE_BANNERS) == {state.value for state in WorkflowState}
    events = [{'event_type': 'inspection_passed'}, {'event_type': 'operator_override'}]
    assert filter_audit(events, ['operator_override']) == events[1:]
    assert filter_audit([]) == []
    with pytest.raises(ValueError, match='Malformed'):
        filter_audit([{}])


def test_real_render_contract_same_image_for_inspection_and_cleanup(tmp_path):
    class Env:
        closed = 0
        def render_to_file(self, path):
            Image.new('RGB', (8, 8)).save(path)
            return path
        def place_part(self, part):
            pass
        def close(self):
            self.closed += 1
    class Inspector:
        path = None
        def inspect(self, image_path, step):
            self.path = image_path
            return Observation(parts_detected=['base_plate'], confidence=1)
    env, inspector = Env(), Inspector()
    session = DashboardSession(tmp_path, environment=env, inspector=inspector, mode='real')
    session.place('base_plate')
    revision = session.revision
    session.inspect(revision)
    assert inspector.path == session.frame == session.inspected_frame
    assert session.inspected_step_id == 1
    assert session.workflow.current_step_id == 2
    assert session.latency_s >= 0
    with pytest.raises(ValueError, match='stale'):
        session.inspect(revision)
    session.close()
    session.close()
    assert env.closed == 1
    with pytest.raises(ValueError, match='requires'):
        DashboardSession(tmp_path, mode='real')


def test_step_id_adapter_is_explicit(assembly_config):
    class Inspector:
        def inspect(self, path, step_id):
            assert isinstance(path, str)
            assert step_id == 2
            return Observation()
    assert isinstance(StepIdInspectorAdapter(Inspector()).inspect(Path('frame.png'), assembly_config.step(2)), Observation)


def test_guard_rejection_does_not_claim_image_was_inspected(tmp_path):
    session = DashboardSession(tmp_path)
    session.place('cover')
    with pytest.raises(RuntimeError, match='order guard'):
        session.inspect()
    assert session.inspected_frame is None
    assert session.inspected_step_id is None
    assert session.workflow.inspector.inspection_count == 0


def test_scripted_demo_runs_scenarios_review_hold_and_logged_override(tmp_path):
    demo = DemoController(tmp_path)
    while demo.running:
        demo.tick()
    assert demo.error is None
    assert [entry['stage'] for entry in demo.history] == list(demo.stages)
    assert '[CORRECTION]' in demo.history[1]['detail']
    events = demo.session.audit.read_all()
    assert 'inspection_hold' in [event['event_type'] for event in events]
    assert 'inspection_needs_review' in [event['event_type'] for event in events]
    override = events[-1]
    assert override['event_type'] == 'operator_override'
    assert override['reason'].startswith('DEMONSTRATION ONLY')
    demo.tick()
    assert len(demo.history) == len(demo.stages)
    stopped = DemoController(tmp_path)
    stopped.stop()
    stopped.tick()
    assert stopped.history == []


def test_app_reset_persists_and_real_mode_never_silently_falls_back(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.delenv('HOLOGUIDE_BACKEND_FACTORY', raising=False)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'src/dashboard/app.py')).run()
    session = app.session_state['assembly']
    app.run()
    assert app.session_state['assembly'] is session
    app.selectbox[0].set_value('Real').run()
    next(b for b in app.button if b.label == 'Start selected mode / reset session').click().run()
    assert app.session_state['assembly'] is session
    assert any('Real backend unavailable' in error.value for error in app.error)
    app.selectbox[0].set_value('Mock').run()
    next(b for b in app.button if b.label == 'Start selected mode / reset session').click().run()
    assert session.closed
    assert app.session_state['assembly'] is not session
    assert not app.exception


def test_app_malformed_audit_is_visible():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'src/dashboard/app.py')).run()
    session = app.session_state['assembly']
    session.audit.path.parent.mkdir(parents=True, exist_ok=True)
    session.audit.path.write_text('{broken json', encoding='utf-8')
    app.run()
    assert not app.exception
    assert any('Audit unavailable' in error.value for error in app.error)
