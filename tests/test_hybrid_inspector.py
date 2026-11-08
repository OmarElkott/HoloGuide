"""VIS-09: HybridInspector - OpenCV decides presence, Gemma only settles unclear cases."""
import cv2
import pytest

from src.config import load_config
from src.control.rules import RuleContext, evaluate
from src.simulation.assembly_env import AssemblyEnv
from src.vision.cv_inspector import CVInspector
from src.vision.hybrid_inspector import HybridInspector
from src.vision.inspector import InspectorABC, timeout_observation
from src.vision.schemas import DecisionStatus, Observation

CFG = load_config()


class FakeGemma(InspectorABC):
    def __init__(self, observation):
        self.observation, self.calls = observation, 0

    def inspect(self, image_path, step):
        self.calls += 1
        return self.observation


def gemma_says(parts, confidence=0.9, why="Gemma sentence."):
    return FakeGemma(Observation(parts_detected=parts, confidence=confidence, rationale=why))


def render(tmp_path, parts, draw=None):
    path = tmp_path / "frame.png"
    with AssemblyEnv(CFG, seed=0) as env:
        for part in parts:
            env.place_part(part)
        env.render_to_file(str(path))
    if draw:
        image = cv2.imread(str(path))
        draw(image)
        cv2.imwrite(str(path), image)
    return path


def orange_patch(im):          # ambiguous gasket evidence for OpenCV
    cv2.rectangle(im, (40, 400), (64, 420), (0, 140, 255), -1)


def decide(obs, step_id):
    step = CFG.step(step_id)
    return evaluate(obs, step, CFG.confidence_threshold, context=RuleContext.from_config(CFG, step)).status


def hybrid(gemma, **kw):
    return HybridInspector(CFG, CVInspector(CFG), gemma, **kw)


def test_confident_opencv_skips_gemma(tmp_path):
    gemma = gemma_says(["cover"])
    obs = hybrid(gemma).inspect(render(tmp_path, ["base_plate"]), 1)
    assert gemma.calls == 0 and obs.parts_detected == ["base_plate"]
    assert decide(obs, 1) == DecisionStatus.PASS


def test_explain_always_adds_text_but_never_changes_parts(tmp_path):
    gemma = gemma_says(["base_plate", "wrong_cover"], why="I see a green cover.")
    inspector = hybrid(gemma, explain="always")
    obs = inspector.inspect(render(tmp_path, ["base_plate"]), 1)
    assert gemma.calls == 1 and obs.parts_detected == ["base_plate"]
    assert "Gemma: I see a green cover." in obs.rationale
    assert inspector.last_status == "cv_explained"
    assert decide(obs, 1) == DecisionStatus.PASS


def test_gemma_settles_an_ambiguous_part_as_absent(tmp_path):
    inspector = hybrid(gemma_says(["base_plate"]))
    obs = inspector.inspect(render(tmp_path, ["base_plate"], orange_patch), 1)
    assert inspector.last_status == "gemma_resolved"
    assert obs.parts_detected == ["base_plate"]
    assert decide(obs, 1) == DecisionStatus.PASS


def test_gemma_settles_an_ambiguous_part_as_present(tmp_path):
    obs = hybrid(gemma_says(["base_plate", "gasket"])).inspect(
        render(tmp_path, ["base_plate"], orange_patch), 1)
    assert "gasket" in obs.parts_detected
    assert decide(obs, 1) == DecisionStatus.BLOCK            # gasket belongs to step 2


@pytest.mark.parametrize("gemma,status", [
    (gemma_says(["base_plate"], confidence=0.6), "gemma_conflict"),          # Gemma unsure too
    (gemma_says([]), "gemma_conflict"),                                      # contradicts sure base plate
    (FakeGemma(timeout_observation(20)), "gemma_unavailable"),
])
def test_unresolved_cases_go_to_review_never_pass(tmp_path, gemma, status):
    inspector = hybrid(gemma)
    obs = inspector.inspect(render(tmp_path, ["base_plate"], orange_patch), 1)
    assert inspector.last_status == status
    assert obs.confidence < CFG.confidence_threshold
    assert decide(obs, 1) == DecisionStatus.NEEDS_REVIEW


def test_gemma_cannot_clear_an_unrecognised_object(tmp_path):
    purple = lambda im: cv2.rectangle(im, (20, 380), (140, 470), (160, 30, 140), -1)
    obs = hybrid(gemma_says(["base_plate"])).inspect(render(tmp_path, ["base_plate"], purple), 1)
    assert decide(obs, 1) == DecisionStatus.NEEDS_REVIEW


def test_unreadable_image_holds_without_calling_gemma(tmp_path):
    gemma = gemma_says(["base_plate"])
    obs = hybrid(gemma).inspect(tmp_path / "missing.png", 1)
    assert gemma.calls == 0 and obs.confidence == 0.0
    assert decide(obs, 1) == DecisionStatus.HOLD


def test_gemma_that_raises_is_contained(tmp_path):
    class Broken(InspectorABC):
        def inspect(self, image_path, step):
            raise RuntimeError("boom")
    obs = hybrid(Broken()).inspect(render(tmp_path, ["base_plate"], orange_patch), 1)
    assert obs.confidence == 0.0 and "boom" in obs.rationale
    assert decide(obs, 1) == DecisionStatus.HOLD


def test_explain_mode_is_validated():
    with pytest.raises(ValueError):
        HybridInspector(CFG, CVInspector(CFG), gemma_says([]), explain="sometimes")
