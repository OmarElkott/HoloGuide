"""VIS-06 option A: GemmaFirstInspector - Gemma decides, OpenCV can only send to review."""
import cv2
import pytest

from src.config import load_config
from src.control.rules import RuleContext, evaluate
from src.simulation.assembly_env import AssemblyEnv
from src.vision.cv_inspector import CVInspector
from src.vision.gemma_first_inspector import GemmaFirstInspector
from src.vision.inspector import InspectorABC, timeout_observation
from src.vision.schemas import DecisionStatus, Observation

CFG = load_config()


class FakeGemma(InspectorABC):
    def __init__(self, observation):
        self.observation, self.calls = observation, 0

    def inspect(self, image_path, step):
        self.calls += 1
        return self.observation


def gemma_says(parts, confidence=0.9, why="Gemma sentence.", missing=()):
    return FakeGemma(Observation(parts_detected=parts, missing_parts=list(missing),
                                 confidence=confidence, rationale=why))


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


def decide(obs, step_id):
    step = CFG.step(step_id)
    return evaluate(obs, step, CFG.confidence_threshold, context=RuleContext.from_config(CFG, step)).status


def first(gemma):
    return GemmaFirstInspector(CFG, gemma, CVInspector(CFG))


def test_gemma_answer_is_used_when_opencv_agrees(tmp_path):
    gemma = gemma_says(["base_plate"], why="A grey plate is on the table.")
    inspector = first(gemma)
    obs = inspector.inspect(render(tmp_path, ["base_plate"]), 1)
    assert gemma.calls == 1 and inspector.last_status == "agreed"
    assert obs.parts_detected == ["base_plate"] and obs.confidence == 0.9
    assert obs.rationale.startswith("Gemma: A grey plate is on the table.")
    assert decide(obs, 1) == DecisionStatus.PASS


def test_gemma_catches_a_missing_part(tmp_path):
    obs = first(gemma_says(["base_plate"], missing=["gasket"])).inspect(
        render(tmp_path, ["base_plate"]), 2)
    assert decide(obs, 2) == DecisionStatus.BLOCK


def test_gemma_catches_a_decoy(tmp_path):
    gemma = FakeGemma(Observation(parts_detected=["base_plate", "gasket", "wrong_cover"],
                                  wrong_parts=["wrong_cover"], confidence=0.9, rationale="Green cover."))
    obs = first(gemma).inspect(render(tmp_path, ["base_plate", "gasket", "wrong_cover"]), 3)
    assert decide(obs, 3) == DecisionStatus.BLOCK


@pytest.mark.parametrize("parts,step,gemma_parts", [
    (["base_plate", "gasket"], 2, ["base_plate"]),   # Gemma misses the gasket: no false block
    (["base_plate", "gasket"], 1, ["base_plate"]),   # wrong order Gemma misses: no false accept
    (["base_plate"], 1, []),                         # Gemma misses the plate
])
def test_opencv_disagreement_sends_to_review_never_pass(tmp_path, parts, step, gemma_parts):
    inspector = first(gemma_says(gemma_parts, missing=["gasket"] if step == 2 else []))
    obs = inspector.inspect(render(tmp_path, parts), step)
    assert inspector.last_status == "cv_veto"
    assert obs.confidence < CFG.confidence_threshold and obs.missing_parts == []
    assert "OpenCV disagrees" in obs.rationale
    assert decide(obs, step) == DecisionStatus.NEEDS_REVIEW


def test_unsure_gemma_goes_to_review(tmp_path):
    obs = first(gemma_says(["base_plate"], confidence=0.6)).inspect(render(tmp_path, ["base_plate"]), 1)
    assert decide(obs, 1) == DecisionStatus.NEEDS_REVIEW


def test_unrecognised_object_sends_to_review(tmp_path):
    purple = lambda im: cv2.rectangle(im, (20, 380), (140, 470), (160, 30, 140), -1)
    inspector = first(gemma_says(["base_plate"]))
    obs = inspector.inspect(render(tmp_path, ["base_plate"], purple), 1)
    assert inspector.last_status == "cv_veto"
    assert decide(obs, 1) == DecisionStatus.NEEDS_REVIEW


def test_gemma_timeout_holds_and_opencv_does_not_substitute(tmp_path):
    inspector = first(FakeGemma(timeout_observation(20)))
    obs = inspector.inspect(render(tmp_path, ["base_plate"]), 1)
    assert inspector.last_status == "gemma_failed"
    assert obs.confidence == 0.0 and obs.rationale.startswith("TIMEOUT:")
    assert decide(obs, 1) == DecisionStatus.HOLD


def test_gemma_that_raises_is_contained(tmp_path):
    class Broken(InspectorABC):
        def inspect(self, image_path, step):
            raise RuntimeError("boom")
    obs = first(Broken()).inspect(render(tmp_path, ["base_plate"]), 1)
    assert obs.confidence == 0.0 and "boom" in obs.rationale
    assert decide(obs, 1) == DecisionStatus.HOLD


def test_long_gemma_text_never_hides_the_opencv_note(tmp_path):
    obs = first(gemma_says([], why="x" * 400)).inspect(render(tmp_path, ["base_plate"]), 1)
    assert len(obs.rationale) <= 300 and "OpenCV disagrees about Base plate" in obs.rationale


def test_factory_builds_gemma_first_without_calling_the_model():
    from src.vision import factories
    inspector = factories.gemma_first(CFG)
    assert isinstance(inspector, GemmaFirstInspector) and inspector.gemma_calls == 0
