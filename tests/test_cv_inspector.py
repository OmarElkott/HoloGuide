"""VIS-07a/07b/11/08: OpenCV inspector on real PyBullet renders (no model, no GPU)."""
import cv2
import numpy as np
import pytest

from src.config import load_config
from src.control.rules import RuleContext, evaluate
from src.simulation.assembly_env import AssemblyEnv
from src.vision.cv_inspector import (CVInspector, foreground_bbox, part_masks,
                                     presence_confidence)
from src.vision.inspector import is_failure
from src.vision.schemas import DecisionStatus

CFG = load_config()


def render(tmp_path, parts, name="frame.png", seed=0, randomize=False):
    path = tmp_path / name
    with AssemblyEnv(CFG, seed=seed, randomize=randomize) as env:
        for part in parts:
            env.place_part(part)
        env.render_to_file(str(path))
    return path


def edit(path, draw):
    image = cv2.imread(str(path))
    draw(image)
    cv2.imwrite(str(path), image)
    return path


def decide(obs, step_id):
    step = CFG.step(step_id)
    return evaluate(obs, step, CFG.confidence_threshold, context=RuleContext.from_config(CFG, step)).status


@pytest.mark.parametrize("step_id,parts,expected", [
    (1, ["base_plate"], DecisionStatus.PASS),
    (2, ["base_plate", "gasket"], DecisionStatus.PASS),
    (3, ["base_plate", "gasket", "cover"], DecisionStatus.PASS),
    (4, ["base_plate", "gasket", "cover", "bolts"], DecisionStatus.PASS),
    (1, [], DecisionStatus.BLOCK),                                   # missing base plate
    (3, ["base_plate", "cover"], DecisionStatus.BLOCK),              # missing gasket under cover
    (4, ["base_plate", "gasket", "cover"], DecisionStatus.BLOCK),    # missing bolts
    (3, ["base_plate", "gasket", "wrong_cover"], DecisionStatus.BLOCK),   # decoy
    (1, ["base_plate", "wrong_cover"], DecisionStatus.BLOCK),
    (1, ["base_plate", "gasket"], DecisionStatus.BLOCK),             # gasket too early
    (2, ["base_plate", "gasket", "cover"], DecisionStatus.BLOCK),    # cover too early
    (3, ["base_plate", "gasket", "cover", "bolts"], DecisionStatus.BLOCK),
])
def test_rendered_scenes_reach_the_right_decision(tmp_path, step_id, parts, expected):
    obs = CVInspector(CFG).inspect(render(tmp_path, parts), CFG.step(step_id))
    assert set(obs.parts_detected) == set(parts)
    assert decide(obs, step_id) == expected


def test_reports_evidence_only_and_flags_decoys(tmp_path):
    obs = CVInspector(CFG).inspect(render(tmp_path, ["base_plate", "gasket", "wrong_cover"]), 3)
    assert obs.wrong_parts == ["wrong_cover"]
    assert obs.missing_parts == ["cover"]
    assert obs.confidence >= CFG.confidence_threshold
    assert obs.rationale.startswith("OpenCV:")


def test_randomised_render_still_passes(tmp_path):
    parts = ["base_plate", "gasket", "cover", "bolts"]
    obs = CVInspector(CFG).inspect(render(tmp_path, parts, seed=7, randomize=True), 4)
    assert decide(obs, 4) == DecisionStatus.PASS
    assert "4 found" in obs.rationale


def test_part_masks_cover_every_configured_part(tmp_path):
    image = cv2.imread(str(render(tmp_path, ["base_plate", "gasket", "cover", "bolts"])))
    masks = part_masks(cv2.cvtColor(image, cv2.COLOR_BGR2HSV), CFG)
    assert set(masks) == {p.name for p in CFG.parts}
    assert cv2.countNonZero(masks["wrong_cover"]) == 0
    assert all(cv2.countNonZero(masks[p]) > 0 for p in ("base_plate", "gasket", "cover", "bolts"))


def test_confidence_grows_with_margin_and_is_low_in_the_ambiguous_band():
    state, low = presence_confidence(0.0031, 0.0008, 0.003)
    _, high = presence_confidence(0.05, 0.0008, 0.003)
    assert state == "present" and 0.75 <= low < high <= 0.95
    assert presence_confidence(0.0, 0.0008, 0.003) == ("absent", 0.95)
    state, conf = presence_confidence(0.002, 0.0008, 0.003)
    assert state == "ambiguous" and conf < CFG.confidence_threshold


def test_small_patch_of_part_colour_goes_to_review(tmp_path):
    # ~500 px of orange: more than a speck, less than a real gasket rim.
    path = edit(render(tmp_path, ["base_plate"]),
                lambda im: cv2.rectangle(im, (40, 400), (64, 420), (0, 140, 255), -1))
    obs = CVInspector(CFG).inspect(path, 1)
    assert "gasket" not in obs.parts_detected and obs.missing_parts == []
    assert decide(obs, 1) == DecisionStatus.NEEDS_REVIEW


def test_wrong_number_of_bolts_goes_to_review(tmp_path):
    def two_bolts(im):
        cv2.circle(im, (60, 420), 7, (25, 25, 215), -1)
        cv2.circle(im, (580, 420), 7, (25, 25, 215), -1)
    path = edit(render(tmp_path, ["base_plate", "gasket", "cover"]), two_bolts)
    obs = CVInspector(CFG).inspect(path, 4)
    assert "bolts" not in obs.parts_detected and "2 found" in obs.rationale
    assert decide(obs, 4) == DecisionStatus.NEEDS_REVIEW


def test_green_object_that_is_not_box_shaped_goes_to_review(tmp_path):
    path = edit(render(tmp_path, ["base_plate"]),
                lambda im: cv2.circle(im, (520, 400), 50, (50, 180, 25), 6))
    obs = CVInspector(CFG).inspect(path, 1)
    assert "wrong_cover" not in obs.parts_detected
    assert "not box-shaped" in obs.rationale
    assert decide(obs, 1) == DecisionStatus.NEEDS_REVIEW


def test_unrecognised_object_goes_to_review(tmp_path):
    path = edit(render(tmp_path, ["base_plate"]),
                lambda im: cv2.rectangle(im, (20, 380), (140, 470), (160, 30, 140), -1))  # purple
    obs = CVInspector(CFG).inspect(path, 1)
    assert "unrecognised" in obs.rationale
    assert decide(obs, 1) == DecisionStatus.NEEDS_REVIEW


def test_unreadable_image_never_raises_and_holds(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    for path in (bad, tmp_path / "missing.png"):
        obs = CVInspector(CFG).inspect(path, 2)
        assert is_failure(obs) and obs.confidence == 0.0
        assert decide(obs, 2) == DecisionStatus.HOLD


def test_bad_step_is_a_failure_not_an_exception(tmp_path):
    obs = CVInspector(CFG).inspect(render(tmp_path, ["base_plate"]), 99)
    assert is_failure(obs)


def test_foreground_bbox(tmp_path):
    empty = cv2.imread(str(render(tmp_path, [], "empty.png")))
    assert foreground_bbox(empty) is None
    full = cv2.imread(str(render(tmp_path, ["base_plate", "gasket", "cover", "bolts"])))
    x0, y0, x1, y1 = foreground_bbox(full)
    h, w = full.shape[:2]
    assert 0 <= x0 < w // 2 < x1 <= w and 0 <= y0 < h // 2 < y1 <= h
    assert (x1 - x0) * (y1 - y0) < w * h            # the crop is smaller than the frame
