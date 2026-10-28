import json
from datetime import datetime, timezone

import pytest

from control.audit_log import AuditLog, AuditLogError
from control.workflow import WorkflowState
from vision.schemas import Decision, DecisionStatus, Observation


def test_missing_audit_file_returns_empty_list(tmp_path):
    assert AuditLog(tmp_path / "missing" / "audit.jsonl").read_all() == []


def test_record_creates_directory_and_valid_jsonl(tmp_path):
    path = tmp_path / "nested" / "logs" / "audit.jsonl"
    audit = AuditLog(path)
    audit.record("workflow_started", current_step=1)

    assert path.is_file()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event_type"] == "workflow_started"


def test_multiple_events_append_in_order(tmp_path):
    path = tmp_path / "audit.jsonl"
    audit = AuditLog(path)
    audit.record("first", sequence=1)
    audit.record("second", sequence=2)

    events = audit.read_all()
    assert [event["event_type"] for event in events] == ["first", "second"]
    assert [event["sequence"] for event in events] == [1, 2]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_timestamp_is_timezone_aware_utc(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.record("test_event")
    timestamp = datetime.fromisoformat(audit.read_all()[0]["timestamp"])
    assert timestamp.tzinfo is not None
    assert timestamp.utcoffset() == timezone.utc.utcoffset(timestamp)


def test_serializes_pydantic_models_enums_and_datetimes(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    observation = Observation(parts_detected=["base_plate"], confidence=0.9)
    decision = Decision(
        step_id=1,
        status=DecisionStatus.PASS,
        message="verified",
        observation=observation,
    )
    occurred_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)

    audit.record(
        "inspection_passed",
        workflow_state=WorkflowState.STEP_COMPLETE,
        decision=decision,
        observation=observation,
        occurred_at=occurred_at,
        metadata={"source": "unit-test"},
    )

    event = audit.read_all()[0]
    assert event["workflow_state"] == "step_complete"
    assert event["decision"]["status"] == "pass"
    assert event["decision"]["observation"]["parts_detected"] == ["base_plate"]
    assert event["observation"]["confidence"] == 0.9
    assert datetime.fromisoformat(event["occurred_at"]) == occurred_at
    assert event["metadata"] == {"source": "unit-test"}


def test_write_failure_is_explicit(tmp_path):
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("occupied", encoding="utf-8")
    audit = AuditLog(parent_file / "audit.jsonl")

    with pytest.raises(AuditLogError, match="failed to append"):
        audit.record("cannot_write")


def test_invalid_existing_json_is_reported(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(AuditLogError, match="failed to read"):
        AuditLog(path).read_all()

