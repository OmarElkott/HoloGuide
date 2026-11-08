"""P4 integration regression tests; doubles are not real-camera evidence."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src.config import load_config
from src.dashboard.backends import inspector_configuration
from src.dashboard.demo import DemonstrationFaultInspector, RealDemoController
from src.dashboard.session import DashboardSession
from src.evaluation.diagnostics import counters, outcome
from src.evaluation.runner import run
from src.evaluation.report import read_results, summarize
from src.vision.inspector import timeout_observation, failure_observation
from src.vision.schemas import Observation


def make_manifest(tmp_path):
    Image.new('RGB', (16, 16)).save(tmp_path / 'input.png')
    path = tmp_path / 'labels.csv'
    path.write_text('image_path,step_id,mistake_type,expected_status\ninput.png,1,correct,pass\n')
    return path


@pytest.mark.parametrize('observation,timeout', [(timeout_observation(1), True),
                                               (failure_observation('test', 'bad input'), False)])
def test_returned_p2_failures_are_counted(tmp_path, observation, timeout):
    class Inspector:
        def inspect(self, image_path, step):
            assert image_path.suffix == '.png'
            assert not hasattr(step, 'expected_status')
            return observation
    output = tmp_path / 'results.csv'
    rows, summary = run(make_manifest(tmp_path), Inspector(), load_config(), output)
    assert rows[0]['status'] == 'hold'
    assert rows[0]['error'] == observation.rationale
    assert summary['error_count'] == 1
    assert summary['timeout_count'] == int(timeout)
    assert summarize(read_results(output))['timeout_count'] == int(timeout)


def test_hybrid_does_not_reuse_stale_skipped_gemma_timeout():
    gemma = SimpleNamespace(inspection_count=1, last_status='timeout')
    hybrid = SimpleNamespace(inspection_count=1, last_status='cv_only', gemma=gemma)
    before = counters(hybrid)
    hybrid.inspection_count += 1
    obs = Observation(parts_detected=['base_plate'], confidence=1)
    assert outcome(obs, before)[:2] == (False, '')
    before = counters(hybrid)
    hybrid.inspection_count += 1
    gemma.inspection_count += 1
    hybrid.last_status = 'gemma_unavailable'
    assert outcome(obs, before)[0]


def test_dataset_metadata_and_hashes_must_match(tmp_path):
    manifest = make_manifest(tmp_path)
    class Never:
        def inspect(self, *args):
            pytest.fail('Invalid dataset must fail before inspection')
    manifest.write_text(manifest.read_text().replace('expected_status\n', 'expected_status,sha256\n').replace('correct,pass\n', 'correct,pass,invalid\n'))
    with pytest.raises(ValueError, match='checksum'):
        run(manifest, Never(), load_config(), tmp_path / 'out.csv')
    manifest = make_manifest(tmp_path)
    (tmp_path / 'dataset.json').write_text(json.dumps({'config': {'wrong': 'config'}}))
    with pytest.raises(ValueError, match='configuration'):
        run(manifest, Never(), load_config(), tmp_path / 'out.csv')


def test_configuration_uses_actual_defaults_and_strips_credentials(monkeypatch):
    monkeypatch.delenv('OLLAMA_HOST', raising=False)
    model = SimpleNamespace(model='gemma3:4b', _host=None)
    settings = inspector_configuration(SimpleNamespace(inner=model))
    assert settings['model'] == 'gemma3:4b'
    assert settings['endpoint'] == 'http://localhost:11434'
    assert 'not verified' in settings['offline_evidence']
    model._host = 'https://secret:password@example.com:443/?token=hidden'
    settings = inspector_configuration(model)
    assert settings['endpoint'] == 'https://example.com:443'
    assert 'Non-loopback' in settings['connection']
    assert 'secret' not in json.dumps(settings)
    model._host = 'http://localhost:invalid'
    assert inspector_configuration(model)['connection'] == 'Invalid model endpoint'


def test_demo_injection_does_not_hide_real_failure():
    class Failed:
        def inspect(self, path, step):
            return failure_observation('real backend', 'unavailable')
    edge = DemonstrationFaultInspector(Failed())
    edge.fault = 'timeout'
    result = edge.inspect('real.png', load_config().step(1))
    assert 'real backend' in result.rationale
    assert not edge.applied


def edge_factory(tmp_path):
    class ImageEnvironment:
        def place_part(self, part):
            pass
        def render_to_file(self, path):
            Image.new('RGB', (16, 16)).save(path)
            return path
        def close(self):
            pass
    class Good:
        def inspect(self, path, step):
            return Observation(parts_detected=['base_plate'], confidence=1,
                               rationale='TEST DOUBLE; not measured vision')
    return DashboardSession(tmp_path, mode='real', inspector=Good(), environment=ImageEnvironment())


def test_real_controller_edge_actions_use_workflow_and_audit(tmp_path):
    demo = RealDemoController(lambda: edge_factory(tmp_path))
    demo.index = 4  # Isolate edge stages; this is a controller test using doubles.
    for state in ('needs_review', 'hold', 'needs_review', 'step_complete'):
        demo.tick()
        assert demo.error is None
        assert demo.session.workflow.state.value == state
    events = demo.session.audit.read_all()
    assert events[-1]['event_type'] == 'operator_override'
    assert events[-1]['operator'] == 'demo-operator'
    assert 'DEMONSTRATION ONLY' in events[-1]['reason']
    assert sum(e['event_type'] == 'demonstration_fault_armed' for e in events) == 3
    assert not demo.running


def test_real_controller_stop_and_failure_never_complete_or_override(tmp_path):
    class FailedRunner:
        def __init__(self, *args):
            self.frame = None
            self.done = True
        def tick(self):
            return SimpleNamespace(error='real inspector unavailable', completed=False)
    demo = RealDemoController(lambda: edge_factory(tmp_path), runner_factory=FailedRunner)
    demo.tick()
    assert 'unavailable' in demo.error
    assert demo.index == 0
    assert not any(e['event_type'] == 'operator_override' for e in demo.session.audit.read_all())
    stopped = RealDemoController(lambda: pytest.fail('Stopped demo must not create resources'))
    stopped.stop()
    stopped.tick()
    assert stopped.session is None


def test_live_real_demo_with_cv_when_simulator_is_installed(tmp_path):
    pytest.importorskip('pybullet', reason='Real simulator acceptance requires PyBullet')
    from src.simulation.assembly_env import AssemblyEnv
    from src.vision.factories import cv
    def factory():
        return DashboardSession(tmp_path, mode='real', environment=AssemblyEnv(load_config()), inspector=cv())
    demo = RealDemoController(factory)
    try:
        for _ in range(40):
            if not demo.running:
                break
            demo.tick()
        assert demo.error is None
        assert demo.index == len(demo.stages)
        assert demo.session.frame.suffix == '.png'
        assert demo.session.audit.read_all()[-1]['event_type'] == 'operator_override'
    finally:
        demo.close()
