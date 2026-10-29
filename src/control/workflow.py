"""Stateful workflow orchestration for deterministic assembly control.

A simulator or dashboard must call ``check_action(part)`` before placing a part
and proceed only when the returned ActionCheck is allowed. The order guard is
independent of simulation state and never invokes inspection.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from os import PathLike
from typing import Any, Protocol

if __package__ and __package__.startswith("src."):
    from ..config import AssemblyConfig, StepSpec
    from ..vision.schemas import (
        DecidedBy,
        Decision,
        DecisionStatus,
        Observation,
        uncertain_observation,
    )
    from .rules import RuleContext, evaluate
else:
    from config import AssemblyConfig, StepSpec
    from control.rules import RuleContext, evaluate
    from vision.schemas import (
        DecidedBy,
        Decision,
        DecisionStatus,
        Observation,
        uncertain_observation,
    )


class WorkflowState(str, Enum):
    WAITING = "waiting"
    INSPECTING = "inspecting"
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"
    HOLD = "hold"
    STEP_COMPLETE = "step_complete"
    COMPLETE = "complete"


class InvalidTransition(RuntimeError):
    """Raised when an operation is not legal in the current workflow state."""


@dataclass(frozen=True)
class ActionCheck:
    """Result of checking a proposed simulation action against configured order."""

    allowed: bool
    reason: str
    part: str
    current_step: StepSpec
    requires_reinspection: bool = False
    invalidates_from_step_id: int | None = None

    def __bool__(self) -> bool:
        """Preserve convenient ``if workflow.check_action(part)`` usage."""
        return self.allowed

    @property
    def step_id(self) -> int:
        return self.current_step.id

    @property
    def blocked(self) -> bool:
        return not self.allowed


class Inspector(Protocol):
    def inspect(self, image_path: str | PathLike[str], step: StepSpec) -> Observation:
        """Inspect an image for the supplied assembly step."""


class AuditSink(Protocol):
    def record(self, event_type: str, **fields: Any) -> None:
        """Append one structured workflow event."""


class Workflow:
    """Own assembly progression and map pure rule decisions to workflow states.

    ``retry_limit`` is the maximum consecutive unsuccessful inspections allowed
    for one step (default: 3). At the limit, non-HOLD outcomes escalate directly
    to NEEDS_REVIEW. Real failures still enter HOLD first; clearing that final
    HOLD moves to NEEDS_REVIEW instead of enabling another automatic retry.
    Successful progression, an operator decision, or explicit invalidation resets
    the counter.
    """

    _INSPECTABLE = {
        WorkflowState.WAITING,
        WorkflowState.BLOCKED,
        WorkflowState.NEEDS_REVIEW,
        WorkflowState.STEP_COMPLETE,
    }
    _OVERRIDEABLE = {
        WorkflowState.BLOCKED,
        WorkflowState.NEEDS_REVIEW,
    }

    def __init__(
        self,
        config: AssemblyConfig,
        inspector: Inspector,
        audit: AuditSink | None = None,
        *,
        retry_limit: int = 3,
    ) -> None:
        if not config.steps:
            raise ValueError("workflow requires at least one configured assembly step")
        if isinstance(retry_limit, bool) or not isinstance(retry_limit, int) or retry_limit < 1:
            raise ValueError("retry_limit must be a positive integer")
        self.config = config
        self.inspector = inspector
        self.audit = audit
        self.retry_limit = retry_limit
        self.state = WorkflowState.WAITING
        self._current_step_index = 0
        self._accepted_actions: set[str] = set()
        self._action_guard_blocked = False
        self._inspection_attempts = 0
        self._retry_limit_reached = False
        self.last_decision: Decision | None = None

    @property
    def current_step(self) -> StepSpec:
        return self.config.steps[self._current_step_index]

    @property
    def current_step_id(self) -> int:
        return self.current_step.id

    @property
    def inspection_attempts(self) -> int:
        return self._inspection_attempts

    @property
    def retry_limit_reached(self) -> bool:
        return self._retry_limit_reached

    def check_action(self, part: str) -> ActionCheck:
        """Authorize a proposed placement before any simulation or AI call.

        Previously verified parts are protected. A removal or replacement must be
        preceded by ``invalidate_part(part, reason, actor)`` and then checked again;
        this rolls the workflow back to the part's originating step for reinspection.
        """
        if not isinstance(part, str) or not part.strip():
            raise ValueError("part must be a non-empty configured part name")
        part = part.strip()

        if self.state is WorkflowState.COMPLETE:
            origin = self._part_step(part)
            return self._reject_action(
                part,
                "Assembly is complete; invalidate the relevant approval before changing it.",
                requires_reinspection=origin is not None,
                invalidates_from_step_id=origin.id if origin else None,
                preserve_state=True,
            )
        if self.state in {WorkflowState.INSPECTING, WorkflowState.HOLD}:
            return self._reject_action(
                part,
                f"Actions are not allowed while workflow is {self.state.value}.",
                preserve_state=True,
            )

        part_spec = next((item for item in self.config.parts if item.name == part), None)
        if part_spec is None:
            return self._reject_action(part, f"Unknown part: {part.replace('_', ' ')}.")
        if part_spec.is_decoy:
            return self._reject_action(
                part,
                f"Incorrect/decoy part: {part_spec.display_name}. Use the configured current-step part.",
            )

        origin = self._part_step(part)
        if origin is None:
            return self._reject_action(
                part,
                f"Part {part_spec.display_name} is not assigned to an assembly step.",
            )
        if origin.id > self.current_step.id:
            return self._reject_action(
                part,
                f"Incorrect assembly order: {part_spec.display_name} belongs to step "
                f"{origin.id}. Complete {self.current_step.name} first.",
            )
        if origin.id < self.current_step.id:
            return self._reject_action(
                part,
                f"{part_spec.display_name} was already verified at step {origin.id}. "
                "Invalidate that approval before removal or replacement.",
                requires_reinspection=True,
                invalidates_from_step_id=origin.id,
            )
        if part in self._accepted_actions:
            return self._reject_action(
                part,
                f"Duplicate action: {part_spec.display_name} was already accepted for "
                f"step {self.current_step.id}. Inspect before repeating it.",
                preserve_state=True,
            )

        result = ActionCheck(
            allowed=True,
            reason=f"{part_spec.display_name} is allowed for {self.current_step.name}.",
            part=part,
            current_step=self.current_step,
        )
        next_state = WorkflowState.WAITING
        self._audit_event(
            "action_accepted",
            current_step=self.current_step,
            workflow_state=self.state,
            next_state=next_state,
            part=part,
            allowed=True,
            reason=result.reason,
        )
        self._accepted_actions.add(part)
        self._action_guard_blocked = False
        self.state = next_state
        return result

    def invalidate_part(self, part: str, reason: str, actor: str) -> None:
        """Invalidate prior approvals before modifying an already verified part.

        The caller must invoke this successfully *before* changing simulator state.
        The affected part and every later step then require fresh inspection.
        """
        if not reason or not reason.strip():
            raise ValueError("reason is required to invalidate an approval")
        if not actor or not actor.strip():
            raise ValueError("actor is required to invalidate an approval")
        origin = self._part_step(part)
        if origin is None:
            raise ValueError(f"cannot invalidate unknown or unassigned part '{part}'")

        origin_index = origin.id - 1
        is_verified = self.state is WorkflowState.COMPLETE or origin_index < self._current_step_index
        if not is_verified:
            raise InvalidTransition(f"part '{part}' has no verified approval to invalidate")

        self._audit_event(
            "approval_invalidated",
            current_step=self.current_step,
            workflow_state=self.state,
            part=part,
            invalidates_from_step_id=origin.id,
            reason=reason.strip(),
            actor=actor.strip(),
            next_state=WorkflowState.WAITING,
        )
        self._current_step_index = origin_index
        self._accepted_actions.clear()
        self._action_guard_blocked = False
        self._reset_attempts()
        self.last_decision = None
        self.state = WorkflowState.WAITING

    def request_inspection(self, image_path: str | PathLike[str]) -> Decision:
        """Inspect and evaluate the current step, then apply exactly one transition."""
        if self.state not in self._INSPECTABLE:
            raise InvalidTransition(f"cannot request inspection while workflow is {self.state.value}")
        if self._action_guard_blocked:
            raise InvalidTransition(
                "inspection is blocked until the order guard accepts the current-step part"
            )
        if self._retry_limit_reached and self.state is WorkflowState.NEEDS_REVIEW:
            raise InvalidTransition(
                "retry limit reached; an operator override is required before another inspection"
            )

        inspected_step = self.current_step
        self._audit_event(
            "inspection_requested",
            current_step=inspected_step,
            workflow_state=self.state,
            next_state=WorkflowState.INSPECTING,
            metadata={"image_path": str(image_path)},
        )
        self.state = WorkflowState.INSPECTING
        self._accepted_actions.clear()
        try:
            raw_observation = self.inspector.inspect(image_path, inspected_step)
            observation = Observation.model_validate(raw_observation)
            decision = evaluate(
                observation,
                inspected_step,
                self.config.confidence_threshold,
                context=RuleContext.from_config(self.config, inspected_step),
            )
        except Exception as exc:
            observation = uncertain_observation(f"{type(exc).__name__}: {exc}")
            decision = Decision(
                step_id=inspected_step.id,
                status=DecisionStatus.HOLD,
                message="Inspection failed. Resolve the inspection error and retry before continuing.",
                decided_by=DecidedBy.SYSTEM,
                observation=observation,
            )

        decision = self._apply_retry_policy(decision)

        try:
            self._audit_decision(decision, inspected_step)
        except Exception as exc:
            if decision.status is DecisionStatus.PASS:
                self._inspection_attempts += 1
            self._retry_limit_reached = self._inspection_attempts >= self.retry_limit
            decision = Decision(
                step_id=inspected_step.id,
                status=DecisionStatus.HOLD,
                message="Audit recording failed. Restore audit logging before continuing.",
                decided_by=DecidedBy.SYSTEM,
                observation=uncertain_observation(f"{type(exc).__name__}: {exc}"),
            )
            self.last_decision = decision
            self.state = WorkflowState.HOLD
            return decision

        self.last_decision = decision
        self._apply_decision(decision)
        return decision

    def operator_override(self, approve: bool, reason: str, operator: str) -> Decision:
        """Manually approve or reject the current blocked/review decision.

        Overrides are explicit API calls only; observation rationale and other AI
        text are never interpreted as override instructions.
        """
        if not isinstance(approve, bool):
            raise TypeError("approve must be a bool")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required for an operator override")
        if not isinstance(operator, str) or not operator.strip():
            raise ValueError("operator identifier is required for an operator override")
        if self.state not in self._OVERRIDEABLE:
            raise InvalidTransition(
                f"operator override is not permitted while workflow is {self.state.value}"
            )
        if self.audit is None:
            raise RuntimeError("operator overrides require a configured audit sink")

        previous_state = self.state
        overridden_decision = self.last_decision
        if approve:
            resulting_state = (
                WorkflowState.COMPLETE
                if self._current_step_index == len(self.config.steps) - 1
                else WorkflowState.STEP_COMPLETE
            )
            status = DecisionStatus.PASS
            message = (
                f"Operator {operator.strip()} approved step {self.current_step.id}: "
                f"{reason.strip()}"
            )
            action = "approve"
        else:
            resulting_state = WorkflowState.BLOCKED
            status = DecisionStatus.BLOCK
            message = (
                f"Operator {operator.strip()} rejected step {self.current_step.id}: "
                f"{reason.strip()}"
            )
            action = "reject"

        decision = Decision(
            step_id=self.current_step.id,
            status=status,
            message=message,
            decided_by=DecidedBy.OPERATOR,
            observation=(
                overridden_decision.observation if overridden_decision is not None else None
            ),
        )
        # This single event is the authorization record for the entire manual
        # transition. It is written before any state or step mutation.
        self._audit_event(
            "operator_override",
            current_step=self.current_step,
            step_id=self.current_step.id,
            previous_state=previous_state,
            resulting_state=resulting_state,
            workflow_state=previous_state,
            operator=operator.strip(),
            actor=operator.strip(),
            reason=reason.strip(),
            action=action,
            decision=decision,
            overridden_decision=overridden_decision,
        )

        self.last_decision = decision
        self._action_guard_blocked = False
        self._reset_attempts()
        if approve:
            if self._current_step_index == len(self.config.steps) - 1:
                self.state = WorkflowState.COMPLETE
            else:
                self._current_step_index += 1
                self.state = WorkflowState.STEP_COMPLETE
        else:
            self.state = WorkflowState.BLOCKED
        return decision

    def clear_hold(self, operator: str, reason: str) -> None:
        """Acknowledge a resolved hold without advancing the assembly."""
        if self.state is not WorkflowState.HOLD:
            raise InvalidTransition("clear_hold is only valid while workflow is on hold")
        if not isinstance(operator, str) or not operator.strip():
            raise ValueError("operator is required to clear a hold")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required to clear a hold")
        resulting_state = (
            WorkflowState.NEEDS_REVIEW
            if self._retry_limit_reached
            else WorkflowState.WAITING
        )
        self._audit_event(
            "hold_cleared",
            current_step=self.current_step,
            workflow_state=self.state,
            previous_state=self.state,
            next_state=resulting_state,
            resulting_state=resulting_state,
            actor=operator.strip(),
            reason=reason.strip(),
            inspection_attempts=self._inspection_attempts,
            retry_limit=self.retry_limit,
        )
        self.state = resulting_state

    def status(self) -> dict[str, Any]:
        """Return a serialization-friendly dashboard snapshot."""
        return {
            "state": self.state.value,
            "current_step_id": self.current_step.id,
            "current_step": self.current_step.model_dump(),
            "total_steps": len(self.config.steps),
            "inspection_attempts": self._inspection_attempts,
            "retry_limit": self.retry_limit,
            "retry_limit_reached": self._retry_limit_reached,
            "last_decision": (
                self.last_decision.model_dump(mode="json") if self.last_decision else None
            ),
        }

    def _reject_action(
        self,
        part: str,
        reason: str,
        *,
        requires_reinspection: bool = False,
        invalidates_from_step_id: int | None = None,
        preserve_state: bool = False,
    ) -> ActionCheck:
        result = ActionCheck(
            allowed=False,
            reason=reason,
            part=part,
            current_step=self.current_step,
            requires_reinspection=requires_reinspection,
            invalidates_from_step_id=invalidates_from_step_id,
        )
        next_state = self.state if preserve_state else WorkflowState.BLOCKED
        decision = Decision(
            step_id=self.current_step.id,
            status=DecisionStatus.BLOCK,
            message=reason,
            decided_by=DecidedBy.RULE,
        )
        self._audit_event(
            "action_rejected",
            current_step=self.current_step,
            workflow_state=self.state,
            next_state=next_state,
            part=part,
            allowed=False,
            reason=reason,
            requires_reinspection=requires_reinspection,
            invalidates_from_step_id=invalidates_from_step_id,
            decision=decision,
        )
        self.last_decision = decision
        if not preserve_state:
            self._action_guard_blocked = True
        self.state = next_state
        return result

    def _audit_decision(self, decision: Decision, inspected_step: StepSpec) -> None:
        event_types = {
            DecisionStatus.PASS: "inspection_passed",
            DecisionStatus.BLOCK: "inspection_blocked",
            DecisionStatus.NEEDS_REVIEW: "inspection_needs_review",
            DecisionStatus.HOLD: "inspection_hold",
        }
        next_state = self._state_for_decision(decision)
        self._audit_event(
            event_types[decision.status],
            current_step=inspected_step,
            workflow_state=self.state,
            next_state=next_state,
            decision=decision,
            observation=decision.observation,
            inspection_attempts=self._inspection_attempts,
            retry_limit=self.retry_limit,
        )
        if self._retry_limit_reached:
            self._audit_event(
                "retry_limit_reached",
                current_step=inspected_step,
                workflow_state=self.state,
                resulting_state=next_state,
                inspection_attempts=self._inspection_attempts,
                retry_limit=self.retry_limit,
                decision=decision,
            )
        if decision.status is DecisionStatus.PASS:
            if self._current_step_index == len(self.config.steps) - 1:
                self._audit_event(
                    "assembly_completed",
                    current_step=inspected_step,
                    workflow_state=self.state,
                    next_state=WorkflowState.COMPLETE,
                    decision=decision,
                )
            else:
                self._audit_event(
                    "step_advanced",
                    current_step=inspected_step,
                    workflow_state=self.state,
                    next_state=WorkflowState.STEP_COMPLETE,
                    from_step_id=inspected_step.id,
                    to_step_id=self.config.steps[self._current_step_index + 1].id,
                    decision=decision,
                )

    def _state_for_decision(self, decision: Decision) -> WorkflowState:
        if decision.status is DecisionStatus.PASS:
            if self._current_step_index == len(self.config.steps) - 1:
                return WorkflowState.COMPLETE
            return WorkflowState.STEP_COMPLETE
        return {
            DecisionStatus.BLOCK: WorkflowState.BLOCKED,
            DecisionStatus.NEEDS_REVIEW: WorkflowState.NEEDS_REVIEW,
            DecisionStatus.HOLD: WorkflowState.HOLD,
        }[decision.status]

    def _apply_decision(self, decision: Decision) -> None:
        if decision.step_id != self.current_step.id:
            self.state = WorkflowState.HOLD
            raise RuntimeError("decision step does not match the workflow's current step")

        if decision.status is DecisionStatus.PASS:
            if self._current_step_index == len(self.config.steps) - 1:
                self.state = WorkflowState.COMPLETE
            else:
                self._current_step_index += 1
                self.state = WorkflowState.STEP_COMPLETE
            self._reset_attempts()
        else:
            self.state = self._state_for_decision(decision)

    def _apply_retry_policy(self, decision: Decision) -> Decision:
        if decision.status is DecisionStatus.PASS:
            return decision

        self._inspection_attempts += 1
        self._retry_limit_reached = self._inspection_attempts >= self.retry_limit
        if not self._retry_limit_reached or decision.status is DecisionStatus.HOLD:
            return decision

        return Decision(
            step_id=decision.step_id,
            status=DecisionStatus.NEEDS_REVIEW,
            message=(
                f"Retry limit reached after {self._inspection_attempts} unsuccessful "
                "inspections. Operator review is required before continuing."
            ),
            decided_by=DecidedBy.SYSTEM,
            observation=decision.observation,
        )

    def _reset_attempts(self) -> None:
        self._inspection_attempts = 0
        self._retry_limit_reached = False

    def _audit_event(self, event_type: str, **fields: Any) -> None:
        if self.audit is not None:
            self.audit.record(event_type, **fields)

    def _part_step(self, part: str) -> StepSpec | None:
        return next((step for step in self.config.steps if part in step.adds_parts), None)
