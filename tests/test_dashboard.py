import pytest

from src.dashboard.session import DashboardSession


def test_guard_override_and_audit(tmp_path):
    session = DashboardSession(tmp_path)
    assert not session.place('cover').allowed
    assert session.workflow.inspector.inspection_count == 0
    assert session.place('base_plate').allowed
    decision = session.inspect()
    assert decision.status.value == 'pass'
    assert session.workflow.current_step_id == 2
    assert session.inspect().status.value == 'block'
    with pytest.raises(ValueError):
        session.override(True, '   ', 'operator')
    assert session.workflow.current_step_id == 2
    session.override(True, 'fixture checked manually', 'operator')
    assert session.workflow.current_step_id == 3
    assert session.audit.read_all()[-1]['event_type'] == 'operator_override'


def test_verified_part_removal_invalidates_via_workflow(tmp_path):
    session = DashboardSession(tmp_path)
    session.place('base_plate')
    session.inspect()
    session.remove('base_plate', 'replace damaged plate', 'operator')
    assert session.workflow.current_step_id == 1
    assert 'base_plate' not in session.environment.visible_parts
    assert 'approval_invalidated' in [e['event_type'] for e in session.audit.read_all()]


def test_streamlit_startup():
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'src/dashboard/app.py'))
    app.run()
    assert not app.exception
    assert app.title[0].value == 'HoloGuide assembly dashboard'
    def click(label):
        next(button for button in app.button if button.label == label).click().run()
        assert not app.exception
    click('Place part')
    click('Inspect')
    assert app.progress[0].value == 25
    click('Inspect')
    submit = next(button for button in app.button if button.label == 'Submit override')
    assert submit.disabled
    app.text_input[0].set_value('test-operator')
    app.text_input[1].set_value('fixture checked manually').run()
    app.checkbox[0].check().run()
    click('Submit override')
    assert app.progress[0].value == 50
