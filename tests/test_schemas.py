import pytest
from pydantic import ValidationError
from vision.schemas import Observation, Decision, DecisionStatus, uncertain_observation


def test_valid_observation_parses():
    raw = '{"parts_detected":["gasket"],"missing_parts":[],"wrong_parts":[],"confidence":0.9,"rationale":"ok"}'
    assert Observation.model_validate_json(raw).confidence == 0.9


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        Observation(confidence=1.7)


def test_uncertain_fallback_has_zero_confidence():
    assert uncertain_observation("parse failed").confidence == 0.0


def test_decision_defaults_to_rule():
    d = Decision(step_id=2, status=DecisionStatus.BLOCK, message="Gasket missing")
    assert d.decided_by.value == "rule"