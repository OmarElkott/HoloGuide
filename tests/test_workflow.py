from collections import deque

import pytest

from config import load_config
from control.audit_log import AuditLog
from control.workflow import InvalidTransition, Workflow, WorkflowState
from vision.schemas import DecisionStatus, Observation


class QueueInspector:
    def __init__(self, *results):
        self.results = deque(results)
        self.calls = []

    def inspect(self, image_path, step):
        self.calls.append((image_path, step.id))
        result = self.results.popleft()
        if isinstance(result, Exception):
            raise result
        return result


class FailingAudit:
    def __init__(self, fail_on_call=1):
        self.fail_on_call = fail_on_call
        self.calls = 0

    def record(self, event_type, **fields):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise OSError("audit unavailable")


def passing_observation(config, step_id):
    return Observation(
        parts_detected=sorted(config.expected_parts(step_id)),
        confidence=config.confidence_threshold,
    )


@pytest.fixture
def config():
    return load_config()


def test_initial_state(config):
    workflow = Workflow(config, QueueInspector())
    assert workflow.state is WorkflowState.WAITING
    assert workflow.current_step_id == 1
    assert workflow.status()["current_step_id"] == 1


def test_legal_action_check_and_invalid_order_transition(config):
    workflow = Workflow(config, QueueInspector())
    rejected = workflow.check_action("cover")
    assert rejected.allowed is False
    assert workflow.state is WorkflowState.BLOCKED
    assert workflow.current_step_id == 1
    assert "Incorrect assembly order" in workflow.last_decision.message

    accepted = workflow.check_action("base_plate")
    assert accepted.allowed is True
    assert workflow.state is WorkflowState.WAITING
    assert workflow.current_step_id == 1


def test_invalid_transition_from_hold(config):
    workflow = Workflow(config, QueueInspector(RuntimeError("offline")))
    workflow.request_inspection("frame.png")
    with pytest.raises(InvalidTransition, match="hold"):
        workflow.request_inspection("frame.png")


def test_one_successful_step_advances_exactly_once(config):
    inspector = QueueInspector(passing_observation(config, 1))
    workflow = Workflow(config, inspector)
    decision = workflow.request_inspection("step1.png")
    assert decision.status is DecisionStatus.PASS
    assert workflow.state is WorkflowState.STEP_COMPLETE
    assert workflow.current_step_id == 2
    assert inspector.calls == [("step1.png", 1)]


def test_all_steps_complete_successfully(config):
    inspector = QueueInspector(
        *(passing_observation(config, step.id) for step in config.steps)
    )
    workflow = Workflow(config, inspector)

    for expected_step_id in range(1, len(config.steps) + 1):
        decision = workflow.request_inspection(f"step{expected_step_id}.png")
        assert decision.status is DecisionStatus.PASS

    assert workflow.state is WorkflowState.COMPLETE
    assert workflow.current_step_id == len(config.steps)


def test_block_does_not_advance(config):
    inspector = QueueInspector(
        Observation(missing_parts=["base_plate"], confidence=0.95)
    )
    workflow = Workflow(config, inspector)
    decision = workflow.request_inspection("missing.png")
    assert decision.status is DecisionStatus.BLOCK
    assert workflow.state is WorkflowState.BLOCKED
    assert workflow.current_step_id == 1


def test_needs_review_does_not_advance(config):
    inspector = QueueInspector(
        Observation(parts_detected=["base_plate"], confidence=0.50)
    )
    workflow = Workflow(config, inspector)
    decision = workflow.request_inspection("uncertain.png")
    assert decision.status is DecisionStatus.NEEDS_REVIEW
    assert workflow.state is WorkflowState.NEEDS_REVIEW
    assert workflow.current_step_id == 1


def test_hold_does_not_advance_and_never_stays_inspecting(config):
    workflow = Workflow(config, QueueInspector(TimeoutError("timed out")))
    decision = workflow.request_inspection("timeout.png")
    assert decision.status is DecisionStatus.HOLD
    assert workflow.state is WorkflowState.HOLD
    assert workflow.current_step_id == 1
    assert "TimeoutError" in decision.observation.rationale


def test_clear_hold_requires_identity_and_reason_and_does_not_advance(config):
    workflow = Workflow(config, QueueInspector(RuntimeError("offline")))
    workflow.request_inspection("frame.png")
    with pytest.raises(ValueError, match="operator"):
        workflow.clear_hold("", "fixed")
    with pytest.raises(ValueError, match="reason"):
        workflow.clear_hold("operator-1", "")

    workflow.clear_hold("operator-1", "inspector restarted")
    assert workflow.state is WorkflowState.WAITING
    assert workflow.current_step_id == 1


def test_retry_after_block_can_pass(config):
    inspector = QueueInspector(
        Observation(missing_parts=["base_plate"], confidence=0.95),
        passing_observation(config, 1),
    )
    workflow = Workflow(config, inspector)
    assert workflow.request_inspection("bad.png").status is DecisionStatus.BLOCK
    assert workflow.request_inspection("fixed.png").status is DecisionStatus.PASS
    assert workflow.current_step_id == 2


def test_complete_cannot_advance_or_restart(config):
    workflow = Workflow(
        config,
        QueueInspector(*(passing_observation(config, step.id) for step in config.steps)),
    )
    for step in config.steps:
        workflow.request_inspection(f"step{step.id}.png")

    with pytest.raises(InvalidTransition, match="complete"):
        workflow.request_inspection("extra.png")
    result = workflow.check_action("base_plate")
    assert result.allowed is False
    assert result.requires_reinspection is True
    assert workflow.state is WorkflowState.COMPLETE
    assert workflow.current_step_id == len(config.steps)


def test_operator_override_requires_audit_sink(config):
    workflow = Workflow(
        config,
        QueueInspector(Observation(parts_detected=["base_plate"], confidence=0.50)),
    )
    workflow.request_inspection("uncertain.png")
    with pytest.raises(RuntimeError, match="audit sink"):
        workflow.operator_override(True, "looks correct", "operator-1")
    assert workflow.state is WorkflowState.NEEDS_REVIEW
    assert workflow.current_step_id == 1


def test_status_is_serialization_friendly(config):
    workflow = Workflow(config, QueueInspector(passing_observation(config, 1)))
    workflow.request_inspection("step1.png")
    snapshot = workflow.status()
    assert snapshot["state"] == "step_complete"
    assert snapshot["last_decision"]["status"] == "pass"


def test_correct_part_action_check_returns_typed_result(config):
    workflow = Workflow(config, QueueInspector())
    result = workflow.check_action("base_plate")
    assert result.allowed is True
    assert result.blocked is False
    assert bool(result) is True
    assert result.part == "base_plate"
    assert result.current_step.id == 1
    assert result.requires_reinspection is False


@pytest.mark.parametrize("part", ["cover", "does_not_exist", "wrong_cover"])
def test_wrong_order_unknown_and_decoy_actions_are_rejected(config, part):
    workflow = Workflow(config, QueueInspector())
    result = workflow.check_action(part)
    assert result.allowed is False
    assert workflow.current_step_id == 1
    assert workflow.state is WorkflowState.BLOCKED


def test_rejected_guard_prevents_inspector_invocation(config):
    inspector = QueueInspector(passing_observation(config, 1))
    workflow = Workflow(config, inspector)
    assert not workflow.check_action("cover")

    with pytest.raises(InvalidTransition, match="order guard"):
        workflow.request_inspection("must-not-run.png")
    assert inspector.calls == []
    assert workflow.current_step_id == 1


def test_duplicate_action_is_rejected_without_losing_original_acceptance(config):
    workflow = Workflow(config, QueueInspector(passing_observation(config, 1)))
    assert workflow.check_action("base_plate").allowed
    duplicate = workflow.check_action("base_plate")
    assert duplicate.allowed is False
    assert "Duplicate" in duplicate.reason
    assert workflow.state is WorkflowState.WAITING

    # The first accepted placement can still be inspected.
    assert workflow.request_inspection("step1.png").status is DecisionStatus.PASS


def test_verified_part_change_requires_explicit_invalidation_and_reinspection(config):
    inspector = QueueInspector(
        passing_observation(config, 1),
        passing_observation(config, 1),
    )
    workflow = Workflow(config, inspector)
    workflow.request_inspection("step1.png")
    assert workflow.current_step_id == 2

    protected = workflow.check_action("base_plate")
    assert protected.allowed is False
    assert protected.requires_reinspection is True
    assert protected.invalidates_from_step_id == 1
    assert workflow.current_step_id == 2

    workflow.invalidate_part("base_plate", "replace damaged plate", "operator-1")
    assert workflow.current_step_id == 1
    assert workflow.state is WorkflowState.WAITING
    assert workflow.last_decision is None

    assert workflow.check_action("base_plate").allowed
    workflow.request_inspection("replacement.png")
    assert workflow.current_step_id == 2


def test_action_audit_failure_prevents_acceptance(config):
    workflow = Workflow(config, QueueInspector(), FailingAudit())
    with pytest.raises(OSError, match="audit unavailable"):
        workflow.check_action("base_plate")
    assert workflow.state is WorkflowState.WAITING
    assert workflow.current_step_id == 1


def test_inspection_request_audit_failure_prevents_inspector_call(config):
    inspector = QueueInspector(passing_observation(config, 1))
    workflow = Workflow(config, inspector, FailingAudit())
    with pytest.raises(OSError, match="audit unavailable"):
        workflow.request_inspection("step1.png")
    assert inspector.calls == []
    assert workflow.state is WorkflowState.WAITING
    assert workflow.current_step_id == 1


def test_decision_audit_failure_holds_without_advancing(config):
    inspector = QueueInspector(passing_observation(config, 1))
    workflow = Workflow(config, inspector, FailingAudit(fail_on_call=2))
    decision = workflow.request_inspection("step1.png")
    assert decision.status is DecisionStatus.HOLD
    assert workflow.state is WorkflowState.HOLD
    assert workflow.current_step_id == 1


def test_audit_events_for_action_pass_and_step_advancement(config, tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    workflow = Workflow(
        config,
        QueueInspector(passing_observation(config, 1)),
        audit,
    )
    workflow.check_action("base_plate")
    workflow.request_inspection("step1.png")
    events = audit.read_all()
    assert [event["event_type"] for event in events] == [
        "action_accepted",
        "inspection_requested",
        "inspection_passed",
        "step_advanced",
    ]
    assert events[2]["decision"]["status"] == "pass"
    assert events[3]["from_step_id"] == 1
    assert events[3]["to_step_id"] == 2


@pytest.mark.parametrize(
    ("observation", "event_type"),
    [
        (Observation(missing_parts=["base_plate"], confidence=0.95), "inspection_blocked"),
        (
            Observation(parts_detected=["base_plate"], confidence=0.50),
            "inspection_needs_review",
        ),
        (RuntimeError("offline"), "inspection_hold"),
    ],
)
def test_audit_event_for_each_non_pass_decision(config, tmp_path, observation, event_type):
    audit = AuditLog(tmp_path / f"{event_type}.jsonl")
    workflow = Workflow(config, QueueInspector(observation), audit)
    workflow.request_inspection("frame.png")
    assert [event["event_type"] for event in audit.read_all()] == [
        "inspection_requested",
        event_type,
    ]


def test_action_rejection_invalidation_and_completion_are_audited(config, tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    inspector = QueueInspector(
        *(passing_observation(config, step.id) for step in config.steps)
    )
    workflow = Workflow(config, inspector, audit)
    workflow.check_action("wrong_cover")
    workflow.check_action("base_plate")
    for step in config.steps:
        workflow.request_inspection(f"step{step.id}.png")
    assert workflow.state is WorkflowState.COMPLETE

    workflow.invalidate_part("cover", "replace scratched cover", "operator-2")
    event_types = [event["event_type"] for event in audit.read_all()]
    assert "action_rejected" in event_types
    assert "assembly_completed" in event_types
    assert event_types[-1] == "approval_invalidated"
    assert workflow.current_step_id == 3
