from io import StringIO
from pathlib import Path
import subprocess
import sys

import pytest

from src.config import load_config
from src.control.audit_log import AuditLog
from src.control.cli import (
    MockAssemblyEnvironment,
    MockInspector,
    run_mock_scenario,
)
from src.control.workflow import InvalidTransition, Workflow, WorkflowState
from src.vision.schemas import DecidedBy, DecisionStatus, Observation


class FailingMockInspector:
    def __init__(self):
        self.inspection_count = 0

    def inspect(self, image_path, step):
        self.inspection_count += 1
        raise TimeoutError("mock inspector timeout")


class ReviewMockInspector(MockInspector):
    def inspect(self, image_path, step):
        observed = super().inspect(image_path, step)
        return observed.model_copy(update={"confidence": 0.5})


def run_scenario(tmp_path, scenario, *, inspector=None):
    config = load_config()
    audit = AuditLog(tmp_path / f"{scenario}.jsonl")
    output = StringIO()
    result = run_mock_scenario(
        scenario,
        config=config,
        inspector=inspector,
        audit=audit,
        frames_dir=tmp_path / "frames",
        output=output,
    )
    return result, audit.read_all(), output.getvalue()


def test_full_successful_mock_assembly_reaches_complete(tmp_path):
    result, events, output = run_scenario(tmp_path, "correct")
    assert result.completed is True
    assert result.workflow.state is WorkflowState.COMPLETE
    assert result.workflow.current_step_id == 4
    assert [decision.status for decision in result.decisions] == [
        DecisionStatus.PASS,
        DecisionStatus.PASS,
        DecisionStatus.PASS,
        DecisionStatus.PASS,
    ]
    assert "Mode: MOCK" in output
    assert "Outcome: COMPLETE" in output
    assert events[0]["event_type"] == "scenario_started"
    assert events[-1]["event_type"] == "scenario_completed"


@pytest.mark.parametrize(
    ("scenario", "expected_message"),
    [
        ("missing_gasket", "Missing required part"),
        ("wrong_cover", "Incorrect part detected"),
    ],
)
def test_visual_fault_blocks_then_correction_completes(
    tmp_path, scenario, expected_message
):
    result, events, output = run_scenario(tmp_path, scenario)
    assert result.completed is True
    assert result.corrections == 1
    assert any(
        decision.status is DecisionStatus.BLOCK
        and expected_message in decision.message
        for decision in result.decisions
    )
    assert result.decisions[-1].status is DecisionStatus.PASS
    event_types = [event["event_type"] for event in events]
    assert "inspection_blocked" in event_types
    assert event_types[-1] == "scenario_completed"
    assert "[CORRECTION]" in output


def test_wrong_order_guard_runs_before_inspector_and_then_recovers(tmp_path):
    result, events, _ = run_scenario(tmp_path, "wrong_order")
    assert result.completed is True
    assert result.corrections == 1
    assert result.inspector.inspection_count == 4
    assert result.environment.applied_actions[0][0] == "base_plate"

    event_types = [event["event_type"] for event in events]
    assert event_types[:3] == [
        "scenario_started",
        "action_rejected",
        "action_accepted",
    ]
    assert event_types.index("action_rejected") < event_types.index(
        "inspection_requested"
    )


def test_correct_scenario_has_expected_audit_sequence(tmp_path):
    _, events, _ = run_scenario(tmp_path, "correct")
    expected = ["scenario_started"]
    for _ in range(3):
        expected.extend(
            [
                "action_accepted",
                "inspection_requested",
                "inspection_passed",
                "step_advanced",
            ]
        )
    expected.extend(
        [
            "action_accepted",
            "inspection_requested",
            "inspection_passed",
            "assembly_completed",
            "scenario_completed",
        ]
    )
    assert [event["event_type"] for event in events] == expected


def test_inspection_failure_stops_on_hold_without_advancement(tmp_path):
    inspector = FailingMockInspector()
    result, events, output = run_scenario(
        tmp_path,
        "correct",
        inspector=inspector,
    )
    assert result.completed is False
    assert result.workflow.state is WorkflowState.HOLD
    assert result.workflow.current_step_id == 1
    assert result.decisions[0].status is DecisionStatus.HOLD
    assert inspector.inspection_count == 1
    assert [event["event_type"] for event in events] == [
        "scenario_started",
        "action_accepted",
        "inspection_requested",
        "inspection_hold",
        "scenario_stopped",
    ]
    assert "SAFE STOP" in output


def test_manual_override_integrates_mock_components_and_audit(tmp_path):
    config = load_config()
    audit = AuditLog(tmp_path / "override.jsonl")
    environment = MockAssemblyEnvironment()
    inspector = ReviewMockInspector(config)
    workflow = Workflow(config, inspector, audit)

    guard = workflow.check_action("base_plate")
    assert guard.allowed
    environment.place_part("base_plate")
    frame = environment.render(tmp_path / "frames", "override", 1)
    reviewed = workflow.request_inspection(frame)
    assert reviewed.status is DecisionStatus.NEEDS_REVIEW
    assert workflow.current_step_id == 1

    approved = workflow.operator_override(
        True,
        "manual fixture verification",
        "integration-operator",
    )
    assert approved.status is DecisionStatus.PASS
    assert approved.decided_by is DecidedBy.OPERATOR
    assert workflow.current_step_id == 2
    override_event = audit.read_all()[-1]
    assert override_event["event_type"] == "operator_override"
    assert override_event["action"] == "approve"
    assert override_event["operator"] == "integration-operator"


def test_repeated_invalid_actions_are_safe_and_idempotent(tmp_path):
    config = load_config()
    audit = AuditLog(tmp_path / "repeated.jsonl")
    inspector = MockInspector(config)
    workflow = Workflow(config, inspector, audit)

    for _ in range(5):
        rejected = workflow.check_action("cover")
        assert rejected.blocked
        assert workflow.current_step_id == 1
    with pytest.raises(InvalidTransition, match="order guard"):
        workflow.request_inspection(tmp_path / "never-rendered.json")
    assert inspector.inspection_count == 0
    assert all(
        event["event_type"] == "action_rejected"
        for event in audit.read_all()
    )


def test_module_cli_entrypoint_runs_without_live_dependencies(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.control.cli",
            "--scenario",
            "correct",
            "--audit-path",
            str(tmp_path / "subprocess-audit.jsonl"),
            "--frames-dir",
            str(tmp_path / "subprocess-frames"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "[MODE] MOCK" in completed.stdout
    assert "Outcome: COMPLETE" in completed.stdout


def test_real_mode_fails_safe_when_dependencies_are_unavailable(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.control.cli",
            "--scenario",
            "correct",
            "--mode",
            "real",
            "--audit-path",
            str(tmp_path / "unused.jsonl"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "Real mode is unavailable" in completed.stderr
