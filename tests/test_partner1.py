"""Scripted observations test integration, never vision accuracy."""
from pathlib import Path
import csv
import pytest
from PIL import Image
from src.config import load_config
from src.control.audit_log import AuditLog
from src.control.workflow import Workflow
from src.simulation.assembly_env import AssemblyEnv
from src.simulation.dataset import generate
from src.simulation.runner import ScenarioRunner
from src.simulation.scenarios import NAMES, get_scenario
from src.vision.schemas import Observation


class ScriptedInspector:
    def __init__(self, scenario='correct'):
        self.scenario = scenario
        self.injected = False
        self.paths = []

    def inspect(self, path, step):
        self.paths.append(path)
        with Image.open(path) as image:
            assert image.format == 'PNG'
            assert image.mode == 'RGB'
        config = load_config()
        detected = sorted(config.expected_parts(step.id))
        trigger = {'missing_gasket': 2, 'wrong_cover': 3, 'wrong_order': 1}.get(self.scenario)
        if not self.injected and step.id == trigger:
            self.injected = True
            if self.scenario == 'missing_gasket':
                detected.remove('gasket')
                detected.append('cover')
            elif self.scenario == 'wrong_cover':
                detected.remove('cover')
                detected.append('wrong_cover')
            else:
                detected.append('cover')
        return Observation(parts_detected=detected, confidence=1,
                           rationale='SCRIPTED TEST DOUBLE: not image analysis')


@pytest.mark.parametrize('name', NAMES)
def test_full_rendered_scenario(tmp_path, name):
    config = load_config()
    inspector = ScriptedInspector(name)
    audit = AuditLog(tmp_path / 'audit.jsonl')
    with AssemblyEnv(config) as env:
        runner = ScenarioRunner(name, env, Workflow(config, inspector, audit), tmp_path / 'frames')
        result = runner.run()
        assert result.completed, result.error
        assert set(env.parts_present()) == config.expected_parts(4)
        assert result.recovered == (name != 'correct')
        assert result.guard_blocked == (name == 'wrong_order')
        assert len(inspector.paths) == (4 if name == 'correct' else 5)
        assert all(p.suffix == '.png' for p in inspector.paths)


def test_hold_stops_without_advancement(tmp_path):
    class Failed:
        def inspect(self, path, step):
            raise TimeoutError('test fault')
    config = load_config()
    with AssemblyEnv(config) as env:
        workflow = Workflow(config, Failed(), AuditLog(tmp_path / 'audit.jsonl'))
        result = ScenarioRunner('correct', env, workflow, tmp_path / 'frames').run()
        assert not result.completed
        assert workflow.current_step_id == 1
        assert workflow.state.value == 'hold'


def test_dataset_contract_and_repeatability(tmp_path):
    first = generate(tmp_path / 'one', seed=42, randomize=True, correct=4, per_fault=4)
    second = generate(tmp_path / 'two', seed=42, randomize=True, correct=4, per_fault=4)
    assert first == second
    assert len(first) == 16
    assert {r['step_id'] for r in first if r['mistake_type'] == 'correct'} == {1,2,3,4}
    assert all(r['step_id'] < 4 for r in first if r['mistake_type'] == 'wrong_order')
    assert not list((tmp_path / 'one' / 'images').glob('*.json'))
    with pytest.raises(FileExistsError):
        generate(tmp_path / 'one')
    with (tmp_path / 'one' / 'labels.csv').open(encoding='utf-8') as stream:
        assert len(list(csv.DictReader(stream))) == 16


def test_seed_reset_and_fault_translation(tmp_path):
    config = load_config()
    hashes = []
    for seed in (7,7,8):
        with AssemblyEnv(config, seed=seed, randomize=True) as env:
            env.place_part('gasket')
            path = env.render_to_file(str(tmp_path / f'{seed}.png'))
            hashes.append(Path(path).read_bytes())
            env.reset()
            env.place_part('gasket')
            assert Path(env.render_to_file(str(tmp_path / 'reset.png'))).read_bytes() == hashes[-1]
            env.offset_part('gasket', .05, 0)
            assert Path(env.render_to_file(str(tmp_path / 'offset.png'))).read_bytes() != hashes[-1]
    assert hashes[0] == hashes[1]
    assert hashes[1] != hashes[2]


def test_scenario_validation():
    with pytest.raises(ValueError):
        get_scenario('unknown')
    a = get_scenario('correct')
    a.actions.clear()
    assert get_scenario('correct').actions

@pytest.mark.parametrize('fault', ['missing_gasket', 'wrong_cover', 'wrong_order'])
def test_false_acceptance_is_not_reported_as_recovery(tmp_path, fault):
    config = load_config()
    with AssemblyEnv(config) as env:
        result = ScenarioRunner(fault, env, Workflow(config, ScriptedInspector(),
            AuditLog(tmp_path / 'audit.jsonl')), tmp_path / 'frames').run()
    assert not result.completed
    assert not result.recovered
    assert result.fault_status == 'pass'
    assert 'expected block' in result.error


def test_dataset_feeds_existing_evaluation_without_labels(tmp_path):
    from src.evaluation.runner import run
    generate(tmp_path / 'data', correct=1, per_fault=1)
    inspector = ScriptedInspector()
    rows, _ = run(tmp_path / 'data/labels.csv', inspector, load_config(), tmp_path / 'results.csv')
    assert len(rows) == 4
    assert all(not row['error'] for row in rows)
    assert len(inspector.paths) == 4


def test_invalid_offsets_do_not_corrupt_scene(tmp_path):
    config = load_config()
    with AssemblyEnv(config) as env:
        env.place_part('cover')
        with pytest.raises(ValueError):
            env.offset_part('cover', float('nan'), 0)
        with pytest.raises(ValueError):
            env.offset_part('gasket', 0, 0)
        assert env.parts_present() == ['cover']


def test_real_dashboard_launcher_ticks(tmp_path):
    from streamlit.testing.v1 import AppTest
    script = '''
import streamlit as st
from src.config import load_config
from src.simulation.assembly_env import AssemblyEnv
from src.dashboard.session import DashboardSession
from src.dashboard.scenario_launcher import show_launcher
from tests.test_partner1 import ScriptedInspector
if 'session' not in st.session_state:
    st.session_state.session = DashboardSession(directory=DIRECTORY,
        environment=AssemblyEnv(load_config()), inspector=ScriptedInspector('wrong_cover'), mode='real')
show_launcher(st, st.session_state.session)
'''.replace('DIRECTORY', repr(str(tmp_path)))
    app = AppTest.from_string(script).run()
    assert not app.exception
    app.selectbox[0].select('wrong_cover').run()
    app.button[0].click().run()
    for _ in range(5):
        next(button for button in app.button if button.label == 'Play next scenario step').click().run()
        assert not app.exception
    session = app.session_state['session']
    assert session.scenario_runner.result.completed
    assert session.scenario_runner.result.recovered
    session.close()


def test_recovery_records_factory_failure_and_continues(tmp_path):
    from src.evaluation.recovery import run_trials
    def broken_factory():
        raise RuntimeError('model unavailable')
    rows = run_trials(tmp_path / 'trial', broken_factory, runs=1)
    assert len(rows) == 4
    assert all(not r['completed'] and 'model unavailable' in r['error'] for r in rows)
    assert (tmp_path / 'trial/provenance.json').exists()
    assert (tmp_path / 'trial/summary.json').exists()
    with (tmp_path / 'trial/recovery.csv').open() as stream:
        assert len(list(csv.DictReader(stream))) == 4


def test_rehearsal_all_scenarios(tmp_path):
    from src.simulation.rehearsal import rehearse
    results = rehearse(tmp_path / 'rehearsal')
    assert len(results) == 4
    assert all(r['completed'] for r in results)
    with Image.open(tmp_path / 'rehearsal/rehearsal.gif') as animation:
        assert animation.n_frames == 19


def test_dashboard_restores_hold_recovery_after_scenario_stop(tmp_path):
    from streamlit.testing.v1 import AppTest
    from src.dashboard.session import DashboardSession
    class TimeoutInspector:
        def inspect(self, path, step):
            raise TimeoutError('injected test timeout')
    session = DashboardSession(directory=tmp_path, mode='real',
        environment=AssemblyEnv(load_config()), inspector=TimeoutInspector())
    try:
        app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'src/dashboard/app.py')
        app.session_state['assembly'] = session
        app.run()
        next(b for b in app.button if b.label == 'Load scenario').click().run()
        next(b for b in app.button if b.label == 'Play next scenario step').click().run()
        assert not app.exception
        assert session.workflow.state.value == 'hold'
        assert session.inspected_step_id == 1
        assert any(b.label == 'Acknowledge resolved hold' for b in app.button)
    finally:
        session.close()

