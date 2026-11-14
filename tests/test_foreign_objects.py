"""Foreign objects (FOD): OpenCV finds where, Gemma says what, the rules decide."""
import csv
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from src.config import load_config
from src.control.rules import RuleContext, evaluate
from src.simulation.assembly_env import AssemblyEnv
from src.simulation.dataset import generate
from src.vision.cv_inspector import CVInspector
from src.vision.foreign_objects import ForeignObjectIdentifier, Identification, crop_region
from src.vision.gemma_first_inspector import GemmaFirstInspector
from src.vision.hybrid_inspector import HybridInspector
from src.vision.inspector import InspectorABC
from src.vision.schemas import DecisionStatus, Observation

CFG = load_config()
ASSEMBLED = ["base_plate", "gasket", "cover"]


def decide(obs, step_id):
    step = CFG.step(step_id)
    return evaluate(obs, step, CFG.confidence_threshold, context=RuleContext.from_config(CFG, step))


def render(tmp_path, parts, foreign=None, on_assembly=True, seed=0):
    path = tmp_path / "frame.png"
    with AssemblyEnv(CFG, seed=seed) as env:
        for part in parts:
            env.place_part(part)
        if foreign:
            env.place_foreign_object(foreign, on_assembly=on_assembly)
        env.render_to_file(str(path))
    return path


class FakeIdentifier:
    def __init__(self, result):
        self.result, self.calls, self.boxes = result, 0, []

    def identify(self, image_path, box):
        self.calls += 1
        self.boxes.append(box)
        return self.result


class FakeGemma(InspectorABC):
    def __init__(self, parts):
        self.calls = 0
        self.observation = Observation(parts_detected=parts, confidence=0.9, rationale="Gemma sentence.")

    def inspect(self, image_path, step):
        self.calls += 1
        return self.observation


class FakeClient:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.reply)))


TOOL = Identification("tool", "purple screwdriver", "partly_clear")


# ---- rules -----------------------------------------------------------------

def test_rules_block_and_name_a_foreign_object():
    obs = Observation(parts_detected=ASSEMBLED, foreign_objects=["purple screwdriver (tool)"], confidence=0.95)
    decision = decide(obs, 3)
    assert decision.status == DecisionStatus.BLOCK
    assert "purple screwdriver" in decision.message and "Remove it" in decision.message


def test_foreign_object_blocks_even_with_low_confidence():
    obs = Observation(parts_detected=ASSEMBLED, foreign_objects=["rag (cloth)"], confidence=0.3)
    assert decide(obs, 3).status == DecisionStatus.BLOCK


def test_foreign_object_is_reported_before_other_findings():
    # Measured with real Gemma: a purple screwdriver misread as bolts must say "remove the tool",
    # not "incorrect order"; after clearing it, re-inspection checks the order again.
    obs = Observation(parts_detected=["base_plate", "cover"], foreign_objects=["rag"], confidence=0.9)
    assert decide(obs, 1).message.startswith("Foreign object in the work area: rag.")


def test_observation_default_has_no_foreign_objects():
    assert Observation().foreign_objects == []


# ---- simulation and dataset (ground truth stays out of the inspector) -----------

def test_foreign_object_is_tracked_separately_from_parts():
    with AssemblyEnv(CFG, seed=1) as env:
        env.place_part("base_plate")
        env.place_foreign_object("wrench")
        assert env.parts_present() == ["base_plate"]
        assert env.foreign_present() == ["wrench"]
        with pytest.raises(ValueError):
            env.place_foreign_object("wrench")
        with pytest.raises(ValueError):
            env.place_foreign_object("hammer")
        env.reset()
        assert env.foreign_present() == []


def test_dataset_adds_foreign_object_cases_only_when_asked(tmp_path):
    generate(tmp_path / "plain", config=CFG, correct=1, per_fault=1)
    with (tmp_path / "plain" / "labels.csv").open() as stream:
        assert "foreign_objects" not in csv.DictReader(stream).fieldnames
    rows = generate(tmp_path / "fod", config=CFG, correct=1, per_fault=1, foreign=3)
    fod = [r for r in rows if r["mistake_type"] == "foreign_object"]
    assert len(fod) == 3 and all(r["expected_status"] == "block" for r in fod)
    assert [json.loads(r["foreign_objects"]) for r in fod] == [["screwdriver"], ["wrench"], ["rag"]]


# ---- OpenCV localises ------------------------------------------------------------

@pytest.mark.parametrize("kind,on_assembly", [("screwdriver", True), ("wrench", False), ("rag", True)])
def test_opencv_flags_and_boxes_every_foreign_object(tmp_path, kind, on_assembly):
    cv = CVInspector(CFG)
    obs = cv.inspect(render(tmp_path, ASSEMBLED, kind, on_assembly), 3)
    assert cv.last_details["unexplained_share"] > cv.settings.max_unexplained
    assert cv.last_details["unexplained_bbox"] is not None
    assert decide(obs, 3).status == DecisionStatus.NEEDS_REVIEW   # OpenCV alone: never PASS


def test_clean_scene_has_no_unexplained_region(tmp_path):
    cv = CVInspector(CFG)
    cv.inspect(render(tmp_path, ASSEMBLED), 3)
    assert cv.last_details["unexplained_share"] == 0.0
    assert cv.last_details["unexplained_bbox"] is None


def test_crop_region_is_a_square_png(tmp_path):
    path = render(tmp_path, ASSEMBLED, "rag")
    cv = CVInspector(CFG)
    cv.inspect(path, 3)
    data = crop_region(path, cv.last_details["unexplained_bbox"])
    (tmp_path / "crop.png").write_bytes(data)
    assert Image.open(tmp_path / "crop.png").size == (336, 336)


# ---- Gemma identifies --------------------------------------------------------------

def test_identifier_parses_a_foreign_answer(tmp_path):
    path = render(tmp_path, ASSEMBLED, "screwdriver")
    client = FakeClient({"kind": "tool", "name": "purple screwdriver", "certainty": "clear"})
    found = ForeignObjectIdentifier(client=client).identify(path, (300, 200, 340, 240))
    assert found.is_foreign and found.label() == "purple screwdriver (tool)"
    assert isinstance(client.calls[0]["messages"][0]["images"][0], bytes)   # a crop, not the frame


@pytest.mark.parametrize("client", [FakeClient(error=TimeoutError("timed out")),
                                    FakeClient({"kind": "spaceship", "name": "x", "certainty": "clear"})])
def test_identifier_never_raises(tmp_path, client):
    found = ForeignObjectIdentifier(client=client).identify(render(tmp_path, ASSEMBLED), (0, 0, 10, 10))
    assert found.kind == "error" and not found.is_foreign


def test_identifier_without_a_region_is_an_error():
    assert ForeignObjectIdentifier(client=FakeClient()).identify("x.png", None).kind == "error"


def test_assembly_part_answer_is_not_foreign():
    assert not Identification("assembly_part", "orange edge").is_foreign


# ---- the handoff in the inspectors ---------------------------------------------------

def hybrid(identifier):
    return HybridInspector(CFG, CVInspector(CFG), FakeGemma(ASSEMBLED), identifier=identifier)


def test_hybrid_blocks_with_the_object_gemma_names(tmp_path):
    identifier = FakeIdentifier(TOOL)
    inspector = hybrid(identifier)
    obs = inspector.inspect(render(tmp_path, ASSEMBLED, "screwdriver"), 3)
    assert identifier.calls == 1 and identifier.boxes[0] is not None
    assert obs.foreign_objects == ["purple screwdriver (tool)"]
    assert inspector.last_status == "foreign_identified"
    assert decide(obs, 3).status == DecisionStatus.BLOCK


@pytest.mark.parametrize("answer", [Identification("assembly_part", "orange edge"),
                                    Identification("unclear"),
                                    Identification("error", detail="TimeoutError")])
def test_hybrid_sends_an_unconfirmed_object_to_review(tmp_path, answer):
    inspector = hybrid(FakeIdentifier(answer))
    obs = inspector.inspect(render(tmp_path, ASSEMBLED, "rag"), 3)
    assert obs.foreign_objects == [] and inspector.last_status == "foreign_unconfirmed"
    assert decide(obs, 3).status == DecisionStatus.NEEDS_REVIEW


def test_hybrid_does_not_ask_gemma_about_a_clean_scene(tmp_path):
    identifier = FakeIdentifier(TOOL)
    obs = hybrid(identifier).inspect(render(tmp_path, ASSEMBLED), 3)
    assert identifier.calls == 0 and decide(obs, 3).status == DecisionStatus.PASS


def test_gemma_first_blocks_with_the_named_object(tmp_path):
    inspector = GemmaFirstInspector(CFG, FakeGemma(ASSEMBLED), CVInspector(CFG), identifier=FakeIdentifier(TOOL))
    obs = inspector.inspect(render(tmp_path, ASSEMBLED, "wrench", on_assembly=False), 3)
    assert obs.foreign_objects == ["purple screwdriver (tool)"]
    assert decide(obs, 3).status == DecisionStatus.BLOCK


def test_gemma_first_unconfirmed_object_goes_to_review(tmp_path):
    inspector = GemmaFirstInspector(CFG, FakeGemma(ASSEMBLED), CVInspector(CFG),
                                    identifier=FakeIdentifier(Identification("unclear")))
    obs = inspector.inspect(render(tmp_path, ASSEMBLED, "wrench"), 3)
    assert obs.foreign_objects == [] and decide(obs, 3).status == DecisionStatus.NEEDS_REVIEW


# ---- full scenario through the production Workflow -----------------------------------

def test_foreign_object_scenario_blocks_then_recovers(tmp_path):
    from src.control.audit_log import AuditLog
    from src.control.workflow import Workflow
    from src.simulation.runner import ScenarioRunner

    identifier = FakeIdentifier(TOOL)
    inspector = HybridInspector(CFG, CVInspector(CFG), FakeGemma(ASSEMBLED), identifier=identifier)
    audit = AuditLog(tmp_path / "audit.jsonl")
    with AssemblyEnv(CFG, seed=0) as env:
        workflow = Workflow(CFG, inspector, audit)
        result = ScenarioRunner("foreign_object", env, workflow, tmp_path / "frames").run()
        assert env.foreign_present() == []
    assert result.completed and result.recovered, result.error
    assert result.decisions == ["pass", "pass", "block", "pass", "pass"]
    assert identifier.calls == 1                       # Gemma asked only about the screwdriver frame
    events = [e["event_type"] for e in audit.read_all()]
    assert "simulation_fault_injected" in events and "scenario_correction" in events
