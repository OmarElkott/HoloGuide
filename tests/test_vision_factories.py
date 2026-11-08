"""Zero-argument factories used by the evaluation runner, scenario runner and dashboard."""
from src.config import load_config
from src.simulation.assembly_env import AssemblyEnv
from src.simulation.runner import load_inspector
from src.vision import factories
from src.vision.cv_inspector import CVInspector
from src.vision.gemma_inspector import GemmaInspector
from src.vision.hybrid_inspector import HybridInspector
from src.vision.inspector import TimeoutInspector


def test_factories_build_without_contacting_ollama(monkeypatch):
    monkeypatch.setenv("HOLOGUIDE_MODEL_NAME", "gemma3:4b-test")
    monkeypatch.setenv("HOLOGUIDE_MODEL_ENDPOINT", "http://127.0.0.1:9")
    gemma = factories.gemma()
    assert isinstance(gemma, TimeoutInspector) and isinstance(gemma.inner, GemmaInspector)
    assert gemma.inner.model == "gemma3:4b-test" and gemma.inner._client is None
    cv = factories.cv()
    assert isinstance(cv.inner, CVInspector)
    hybrid = factories.hybrid()
    assert isinstance(hybrid, HybridInspector) and hybrid.explain == "unclear"
    assert factories.hybrid_explained().explain == "always"


def test_cv_factory_loads_by_spec_and_inspects_a_render(tmp_path):
    config = load_config()
    inspector = load_inspector("src.vision.factories:cv")
    with AssemblyEnv(config, seed=0) as env:
        env.place_part("base_plate")
        image = env.render_to_file(str(tmp_path / "f.png"))
    obs = inspector.inspect(image, config.step(1))
    assert obs.parts_detected == ["base_plate"] and inspector.last_status == "ok"
