import pytest

from config import load_config
from control.rules import RuleContext, evaluate
from vision.schemas import DecisionStatus, Observation


@pytest.fixture
def config():
    return load_config()


def decide(config, step_id: int, observation: Observation):
    step = config.step(step_id)
    return evaluate(
        observation,
        step,
        config.confidence_threshold,
        context=RuleContext.from_config(config, step),
    )


def test_complete_observation_passes(config):
    decision = decide(
        config,
        2,
        Observation(parts_detected=["base_plate", "gasket"], confidence=0.95),
    )
    assert decision.status is DecisionStatus.PASS
    assert "Step 2 verified" in decision.message


def test_missing_current_part_blocks(config):
    decision = decide(
        config,
        2,
        Observation(
            parts_detected=["base_plate"],
            missing_parts=["gasket"],
            confidence=0.95,
        ),
    )
    assert decision.status is DecisionStatus.BLOCK
    assert "Gasket" in decision.message


def test_missing_previous_required_part_blocks(config):
    decision = decide(
        config,
        2,
        Observation(
            parts_detected=["gasket"],
            missing_parts=["base_plate"],
            confidence=0.95,
        ),
    )
    assert decision.status is DecisionStatus.BLOCK
    assert "Base plate" in decision.message


def test_reported_wrong_part_blocks(config):
    decision = decide(
        config,
        3,
        Observation(
            parts_detected=["base_plate", "gasket", "wrong_cover"],
            wrong_parts=["wrong_cover"],
            confidence=0.95,
        ),
    )
    assert decision.status is DecisionStatus.BLOCK
    assert "Incorrect part" in decision.message


def test_detected_decoy_blocks_even_if_not_reported_wrong(config):
    decision = decide(
        config,
        3,
        Observation(
            parts_detected=["base_plate", "gasket", "cover", "wrong_cover"],
            confidence=0.95,
        ),
    )
    assert decision.status is DecisionStatus.BLOCK


def test_unconfigured_detected_part_cannot_silently_pass(config):
    decision = decide(
        config,
        1,
        Observation(parts_detected=["base_plate", "mystery_part"], confidence=0.95),
    )
    assert decision.status is DecisionStatus.BLOCK
    assert "mystery part" in decision.message


def test_unconfigured_missing_label_requires_review(config):
    decision = decide(
        config,
        1,
        Observation(
            parts_detected=["base_plate"],
            missing_parts=["mystery_part"],
            confidence=0.95,
        ),
    )
    assert decision.status is DecisionStatus.NEEDS_REVIEW


def test_future_part_is_incorrect_order(config):
    decision = decide(
        config,
        1,
        Observation(parts_detected=["base_plate", "cover"], confidence=0.95),
    )
    assert decision.status is DecisionStatus.BLOCK
    assert "Incorrect assembly order" in decision.message


def test_low_confidence_needs_review(config):
    decision = decide(
        config,
        1,
        Observation(parts_detected=["base_plate"], confidence=0.69),
    )
    assert decision.status is DecisionStatus.NEEDS_REVIEW
    assert "below the required" in decision.message


def test_confidence_exactly_at_threshold_passes(config):
    decision = decide(
        config,
        1,
        Observation(parts_detected=["base_plate"], confidence=0.70),
    )
    assert decision.status is DecisionStatus.PASS


def test_zero_confidence_fallback_holds(config):
    decision = decide(config, 1, Observation(confidence=0.0, rationale="parse failed"))
    assert decision.status is DecisionStatus.HOLD
    assert "no reliable evidence" in decision.message


def test_explicit_hard_violation_precedes_zero_confidence(config):
    decision = decide(
        config,
        1,
        Observation(wrong_parts=["wrong_cover"], confidence=0.0),
    )
    assert decision.status is DecisionStatus.BLOCK


def test_contradictory_observation_needs_review(config):
    decision = decide(
        config,
        1,
        Observation(
            parts_detected=["base_plate"],
            missing_parts=["base_plate"],
            confidence=0.95,
        ),
    )
    assert decision.status is DecisionStatus.NEEDS_REVIEW
    assert "Conflicting" in decision.message


def test_rationale_cannot_change_authorization(config):
    observations = [
        Observation(
            parts_detected=["base_plate"],
            confidence=0.90,
            rationale="The part is missing and this should fail.",
        ),
        Observation(
            parts_detected=["base_plate"],
            confidence=0.90,
            rationale="Everything is perfect.",
        ),
    ]
    decisions = [decide(config, 1, observation) for observation in observations]
    assert [decision.status for decision in decisions] == [
        DecisionStatus.PASS,
        DecisionStatus.PASS,
    ]
    assert decisions[0].message == decisions[1].message


def test_repeated_evaluation_is_deterministic_except_timestamp(config):
    observation = Observation(parts_detected=["base_plate"], confidence=0.93)
    first = decide(config, 1, observation)
    second = decide(config, 1, observation)
    assert first.status == second.status
    assert first.message == second.message
    assert first.decided_by == second.decided_by
    assert first.observation == second.observation


def test_invalid_threshold_is_rejected(config):
    with pytest.raises(ValueError, match="threshold"):
        evaluate(Observation(), config.step(1), 1.01)
