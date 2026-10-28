import random

import pytest

from config import load_config
from control.audit_log import AuditLog
from control.workflow import InvalidTransition, Workflow, WorkflowState
from vision.schemas import DecidedBy, DecisionStatus, Observation


class MutableInspector:
    def __init__(self, result=None):
        self.result = result
        self.calls = 0

    def inspect(self, image_path, step):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class MemoryAudit:
    def __init__(self, fail_on_event=None):
        self.events = []
        self.fail_on_event = fail_on_event

    def record(self, event_type, **fields):
        if event_type == self.fail_on_event:
            raise OSError("audit unavailable")
        self.events.append({"event_type": event_type, **fields})


def passing(config, step_id):
    return Observation(
        parts_detected=sorted(config.expected_parts(step_id)),
        confidence=0.95,
    )


@pytest.fixture
def config():
    return load_config()


def reviewed_workflow(config, audit=None, *, retry_limit=3):
    inspector = MutableInspector(
        Observation(parts_detected=["base_plate"], confidence=0.50)
    )
    workflow = Workflow(
        config,
        inspector,
        audit or MemoryAudit(),
        retry_limit=retry_limit,
    )
    decision = workflow.request_inspection("uncertain.png")
    assert decision.status is DecisionStatus.NEEDS_REVIEW
    return workflow, inspector


def test_approved_override_advances_one_step_and_is_audited(config, tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    workflow, _ = reviewed_workflow(config, audit)

    decision = workflow.operator_override(
        True,
        reason="visual check confirms the base plate",
        operator="operator-17",
    )

    assert decision.status is DecisionStatus.PASS
    assert decision.decided_by is DecidedBy.OPERATOR
    assert workflow.current_step_id == 2
    assert workflow.state is WorkflowState.STEP_COMPLETE
    event = audit.read_all()[-1]
    assert event["event_type"] == "operator_override"
    assert event["previous_state"] == "needs_review"
    assert event["resulting_state"] == "step_complete"
    assert event["operator"] == "operator-17"
    assert event["reason"] == "visual check confirms the base plate"
    assert event["action"] == "approve"
    assert event["decision"]["decided_by"] == "operator"


def test_rejected_override_is_audited_and_does_not_advance(config, tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    workflow, _ = reviewed_workflow(config, audit)

    decision = workflow.operator_override(
        False,
        reason="part alignment is unacceptable",
        operator="operator-4",
    )

    assert decision.status is DecisionStatus.BLOCK
    assert decision.decided_by is DecidedBy.OPERATOR
    assert workflow.current_step_id == 1
    assert workflow.state is WorkflowState.BLOCKED
    event = audit.read_all()[-1]
    assert event["event_type"] == "operator_override"
    assert event["action"] == "reject"
    assert event["resulting_state"] == "blocked"


@pytest.mark.parametrize(
    ("reason", "operator", "message"),
    [
        ("", "operator-1", "reason"),
        ("   ", "operator-1", "reason"),
        ("confirmed", "", "operator identifier"),
        ("confirmed", "   ", "operator identifier"),
    ],
)
def test_override_requires_reason_and_operator(config, reason, operator, message):
    workflow, _ = reviewed_workflow(config)
    with pytest.raises(ValueError, match=message):
        workflow.operator_override(True, reason, operator)
    assert workflow.current_step_id == 1
    assert workflow.state is WorkflowState.NEEDS_REVIEW


@pytest.mark.parametrize(
    "state",
    [
        WorkflowState.WAITING,
        WorkflowState.INSPECTING,
        WorkflowState.HOLD,
        WorkflowState.STEP_COMPLETE,
        WorkflowState.COMPLETE,
    ],
)
def test_override_rejects_non_overrideable_states(config, state):
    workflow = Workflow(config, MutableInspector(), MemoryAudit())
    workflow.state = state
    with pytest.raises(InvalidTransition, match="not permitted"):
        workflow.operator_override(True, "manual approval", "operator-1")
    assert workflow.current_step_id == 1


def test_override_audit_failure_prevents_advancement(config):
    audit = MemoryAudit(fail_on_event="operator_override")
    workflow, _ = reviewed_workflow(config, audit)
    before_state = workflow.state

    with pytest.raises(OSError, match="audit unavailable"):
        workflow.operator_override(True, "confirmed", "operator-1")

    assert workflow.current_step_id == 1
    assert workflow.state is before_state
    assert workflow.last_decision.status is DecisionStatus.NEEDS_REVIEW


def test_approved_override_can_complete_only_the_final_step(config):
    audit = MemoryAudit()
    inspector = MutableInspector()
    workflow = Workflow(config, inspector, audit)
    for step_id in range(1, len(config.steps)):
        inspector.result = passing(config, step_id)
        workflow.request_inspection(f"step-{step_id}.png")

    inspector.result = Observation(
        parts_detected=sorted(config.expected_parts(len(config.steps))),
        confidence=0.50,
    )
    workflow.request_inspection("final-review.png")
    assert workflow.current_step_id == len(config.steps)
    assert workflow.state is WorkflowState.NEEDS_REVIEW

    decision = workflow.operator_override(True, "final assembly verified", "operator-9")
    assert decision.status is DecisionStatus.PASS
    assert workflow.state is WorkflowState.COMPLETE
    assert workflow.current_step_id == len(config.steps)
    assert audit.events[-1]["event_type"] == "operator_override"
    assert audit.events[-1]["step_id"] == len(config.steps)
    assert audit.events[-1]["resulting_state"] is WorkflowState.COMPLETE


def test_timeout_enters_hold_and_clear_requires_reinspection(config, tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    inspector = MutableInspector(TimeoutError("model timed out"))
    workflow = Workflow(config, inspector, audit)

    decision = workflow.request_inspection("frame.png")
    assert decision.status is DecisionStatus.HOLD
    assert workflow.state is WorkflowState.HOLD
    assert workflow.current_step_id == 1

    workflow.clear_hold("operator-2", "inspector service restarted")
    assert workflow.state is WorkflowState.WAITING
    assert workflow.current_step_id == 1

    inspector.result = passing(config, 1)
    assert workflow.request_inspection("retry.png").status is DecisionStatus.PASS
    assert workflow.current_step_id == 2
    assert [event["event_type"] for event in audit.read_all()] == [
        "inspection_requested",
        "inspection_hold",
        "hold_cleared",
        "inspection_requested",
        "inspection_passed",
        "step_advanced",
    ]


def test_hold_exit_audit_failure_preserves_hold(config):
    audit = MemoryAudit(fail_on_event="hold_cleared")
    workflow = Workflow(
        config,
        MutableInspector(TimeoutError("offline")),
        audit,
    )
    workflow.request_inspection("frame.png")
    with pytest.raises(OSError, match="audit unavailable"):
        workflow.clear_hold("operator-1", "service restarted")
    assert workflow.state is WorkflowState.HOLD
    assert workflow.current_step_id == 1


def test_low_confidence_is_review_not_hold(config):
    workflow, _ = reviewed_workflow(config)
    assert workflow.state is WorkflowState.NEEDS_REVIEW
    assert workflow.last_decision.status is DecisionStatus.NEEDS_REVIEW


def test_retry_limit_escalates_and_prevents_unlimited_reinspection(config):
    workflow, inspector = reviewed_workflow(config, retry_limit=3)
    assert workflow.status()["inspection_attempts"] == 1

    workflow.request_inspection("retry-2.png")
    final = workflow.request_inspection("retry-3.png")
    assert final.status is DecisionStatus.NEEDS_REVIEW
    assert final.decided_by is DecidedBy.SYSTEM
    assert workflow.status()["retry_limit_reached"] is True
    assert workflow.retry_limit_reached is True
    assert workflow.inspection_attempts == 3
    assert inspector.calls == 3

    with pytest.raises(InvalidTransition, match="retry limit"):
        workflow.request_inspection("retry-4.png")
    assert inspector.calls == 3


def test_repeated_timeouts_escalate_only_after_hold_is_cleared(config):
    inspector = MutableInspector(TimeoutError("offline"))
    workflow = Workflow(config, inspector, MemoryAudit(), retry_limit=2)

    assert workflow.request_inspection("first.png").status is DecisionStatus.HOLD
    workflow.clear_hold("operator-1", "service restarted")
    assert workflow.state is WorkflowState.WAITING

    assert workflow.request_inspection("second.png").status is DecisionStatus.HOLD
    assert workflow.state is WorkflowState.HOLD
    workflow.clear_hold("operator-1", "second recovery attempt")
    assert workflow.state is WorkflowState.NEEDS_REVIEW
    assert workflow.current_step_id == 1

    with pytest.raises(InvalidTransition, match="retry limit"):
        workflow.request_inspection("third.png")


def test_successful_transition_resets_retry_counter(config):
    inspector = MutableInspector(
        Observation(parts_detected=["base_plate"], confidence=0.50)
    )
    workflow = Workflow(config, inspector, MemoryAudit(), retry_limit=3)
    workflow.request_inspection("uncertain.png")
    assert workflow.status()["inspection_attempts"] == 1

    inspector.result = passing(config, 1)
    workflow.request_inspection("pass.png")
    assert workflow.current_step_id == 2
    assert workflow.status()["inspection_attempts"] == 0
    assert workflow.status()["retry_limit_reached"] is False


def test_no_progress_for_block_review_or_hold(config):
    cases = [
        Observation(missing_parts=["base_plate"], confidence=0.95),
        Observation(parts_detected=["base_plate"], confidence=0.50),
        RuntimeError("inspector unavailable"),
    ]
    expected = [
        DecisionStatus.BLOCK,
        DecisionStatus.NEEDS_REVIEW,
        DecisionStatus.HOLD,
    ]
    for result, expected_status in zip(cases, expected):
        workflow = Workflow(config, MutableInspector(result), MemoryAudit())
        decision = workflow.request_inspection("frame.png")
        assert decision.status is expected_status
        assert workflow.current_step_id == 1


def test_repeated_invalid_actions_cannot_bypass_guard(config):
    inspector = MutableInspector(passing(config, 1))
    workflow = Workflow(config, inspector, MemoryAudit())
    for _ in range(20):
        result = workflow.check_action("cover")
        assert result.blocked
        assert workflow.current_step_id == 1
    with pytest.raises(InvalidTransition, match="order guard"):
        workflow.request_inspection("bypass.png")
    assert inspector.calls == 0


def test_complete_cannot_be_overridden_or_progress(config):
    audit = MemoryAudit()
    inspector = MutableInspector()
    workflow = Workflow(config, inspector, audit)
    for step in config.steps:
        inspector.result = passing(config, step.id)
        workflow.request_inspection(f"step-{step.id}.png")
    assert workflow.state is WorkflowState.COMPLETE

    with pytest.raises(InvalidTransition, match="complete"):
        workflow.operator_override(True, "extra approval", "operator-1")
    with pytest.raises(InvalidTransition, match="complete"):
        workflow.request_inspection("extra.png")
    assert workflow.current_step_id == len(config.steps)


def test_seeded_randomized_workflow_never_advances_without_authorization(config):
    rng = random.Random(20261028)
    audit = MemoryAudit()
    inspector = MutableInspector()
    workflow = Workflow(config, inspector, audit, retry_limit=3)
    parts = [part.name for part in config.parts] + ["unknown_part"]

    for sequence in range(500):
        before_step = workflow.current_step_id
        before_state = workflow.state
        event_offset = len(audit.events)
        result = None

        operation = rng.choice(["action", "inspect", "override", "clear"])
        try:
            if operation == "action":
                workflow.check_action(rng.choice(parts))
            elif operation == "clear":
                workflow.clear_hold("random-operator", f"recovery {sequence}")
            elif operation == "override":
                result = workflow.operator_override(
                    rng.choice([True, False]),
                    f"manual decision {sequence}",
                    "random-operator",
                )
            else:
                outcome = rng.randrange(4)
                if outcome == 0:
                    inspector.result = passing(config, workflow.current_step_id)
                elif outcome == 1:
                    inspector.result = Observation(
                        missing_parts=[workflow.current_step.adds_parts[0]],
                        confidence=0.95,
                    )
                elif outcome == 2:
                    inspector.result = Observation(
                        parts_detected=sorted(
                            config.expected_parts(workflow.current_step_id)
                        ),
                        confidence=0.50,
                    )
                else:
                    inspector.result = TimeoutError("seeded timeout")
                result = workflow.request_inspection(f"random-{sequence}.png")
        except (InvalidTransition, ValueError, RuntimeError):
            pass

        after_step = workflow.current_step_id
        new_events = audit.events[event_offset:]
        assert after_step <= before_step + 1
        if after_step > before_step:
            assert result is not None
            assert result.status is DecisionStatus.PASS
            if result.decided_by is DecidedBy.OPERATOR:
                assert any(
                    event["event_type"] == "operator_override"
                    and event["decision"].status is DecisionStatus.PASS
                    for event in new_events
                )
            else:
                assert any(
                    event["event_type"] == "inspection_passed"
                    for event in new_events
                )
                assert any(
                    event["event_type"] == "step_advanced"
                    for event in new_events
                )

        if before_state is not WorkflowState.COMPLETE and workflow.state is WorkflowState.COMPLETE:
            assert result is not None
            assert result.status is DecisionStatus.PASS
            assert any(
                event["event_type"] in {"assembly_completed", "operator_override"}
                for event in new_events
            )
