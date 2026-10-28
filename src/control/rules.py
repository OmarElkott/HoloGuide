"""Deterministic authorization rules for assembly inspections.

Decision precedence is deliberately fail-safe:

1. Explicit wrong, decoy, or out-of-order parts are hard violations (BLOCK).
2. Explicit missing parts are hard violations (BLOCK).
3. Failed/zero-confidence evidence cannot authorize work (HOLD).
4. Contradictory or low-confidence evidence requires human review.
5. PASS is returned only when all cumulative requirements are consistently present.

The inspector's free-text rationale is never used for authorization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

if __package__ and __package__.startswith("src."):
    from ..config import AssemblyConfig, StepSpec
    from ..vision.schemas import DecidedBy, Decision, DecisionStatus, Observation
else:
    from config import AssemblyConfig, StepSpec
    from vision.schemas import DecidedBy, Decision, DecisionStatus, Observation


@dataclass(frozen=True)
class RuleContext:
    """Configuration-derived facts needed beyond the public evaluate signature."""

    expected_parts: frozenset[str]
    future_parts: frozenset[str] = field(default_factory=frozenset)
    decoy_parts: frozenset[str] = field(default_factory=frozenset)
    known_parts: frozenset[str] = field(default_factory=frozenset)
    display_names: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: AssemblyConfig, step: StepSpec) -> "RuleContext":
        future_parts = {
            part
            for later_step in config.steps
            if later_step.id > step.id
            for part in later_step.adds_parts
        }
        return cls(
            expected_parts=frozenset(config.expected_parts(step.id)),
            future_parts=frozenset(future_parts),
            decoy_parts=frozenset(part.name for part in config.parts if part.is_decoy),
            known_parts=frozenset(part.name for part in config.parts),
            display_names={part.name: part.display_name for part in config.parts},
        )


def evaluate(
    observation: Observation,
    step: StepSpec,
    threshold: float,
    *,
    context: RuleContext | None = None,
) -> Decision:
    """Evaluate one observation without mutating workflow state.

    The first three parameters preserve the roadmap's public contract. Callers that
    own an :class:`AssemblyConfig` should supply ``RuleContext.from_config(...)`` so
    cumulative requirements, future parts, and decoys can also be checked. Without
    a context, the function safely evaluates the parts added by this step only.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")

    try:
        checked = Observation.model_validate(observation)
    except (TypeError, ValueError):
        return _decision(
            step,
            DecisionStatus.HOLD,
            "Inspection failed or returned malformed evidence. Retry before continuing.",
            None,
        )

    rule_context = context or RuleContext(expected_parts=frozenset(step.adds_parts))
    expected = set(rule_context.expected_parts)
    detected = _names(checked.parts_detected)
    reported_missing = _names(checked.missing_parts)
    reported_wrong = _names(checked.wrong_parts)

    future_detected = detected & set(rule_context.future_parts)
    if future_detected:
        names = _display(future_detected, rule_context.display_names)
        return _decision(
            step,
            DecisionStatus.BLOCK,
            f"Incorrect assembly order: {names} belongs to a later step. "
            f"Remove it and complete {step.name} first.",
            checked,
        )

    unknown_detected = (
        detected - set(rule_context.known_parts) if rule_context.known_parts else set()
    )
    incorrect = (
        reported_wrong
        | (detected & set(rule_context.decoy_parts))
        | unknown_detected
    )
    if incorrect:
        names = _display(incorrect, rule_context.display_names)
        return _decision(
            step,
            DecisionStatus.BLOCK,
            f"Incorrect part detected: {names}. Remove it and inspect {step.name} again.",
            checked,
        )

    explicit_missing = reported_missing & expected
    contradictory = detected & reported_missing
    independently_missing = explicit_missing - contradictory
    if independently_missing:
        names = _display(independently_missing, rule_context.display_names)
        return _decision(
            step,
            DecisionStatus.BLOCK,
            f"Missing required part: {names}. Add it and inspect {step.name} again.",
            checked,
        )

    if checked.confidence == 0.0:
        return _decision(
            step,
            DecisionStatus.HOLD,
            "Inspection failed or returned no reliable evidence. Retry before continuing.",
            checked,
        )

    inferred_missing = expected - detected
    if inferred_missing and checked.confidence >= threshold:
        names = _display(inferred_missing, rule_context.display_names)
        return _decision(
            step,
            DecisionStatus.BLOCK,
            f"Missing required part: {names}. Add it and inspect {step.name} again.",
            checked,
        )

    if contradictory:
        names = _display(contradictory, rule_context.display_names)
        return _decision(
            step,
            DecisionStatus.NEEDS_REVIEW,
            f"Conflicting inspection evidence for {names}. Reinspect or request operator review.",
            checked,
        )

    unknown_missing = (
        reported_missing - set(rule_context.known_parts)
        if rule_context.known_parts
        else set()
    )
    if unknown_missing:
        names = _display(unknown_missing, rule_context.display_names)
        return _decision(
            step,
            DecisionStatus.NEEDS_REVIEW,
            f"Inspection reported unknown part names: {names}. Reinspect or request operator review.",
            checked,
        )

    if checked.confidence < threshold:
        return _decision(
            step,
            DecisionStatus.NEEDS_REVIEW,
            f"Inspection confidence {checked.confidence:.0%} is below the required "
            f"{threshold:.0%}. Reinspect or request operator review.",
            checked,
        )

    # This guard makes PASS structurally impossible while a requirement is absent,
    # even if the ordering above is changed later.
    if inferred_missing:
        names = _display(inferred_missing, rule_context.display_names)
        return _decision(
            step,
            DecisionStatus.BLOCK,
            f"Missing required part: {names}. Add it and inspect {step.name} again.",
            checked,
        )

    return _decision(
        step,
        DecisionStatus.PASS,
        f"Step {step.id} verified: {step.name}. Continue to the next step.",
        checked,
    )


def _names(values: Iterable[str]) -> set[str]:
    return {value.strip() for value in values if value and value.strip()}


def _display(names: Iterable[str], display_names: Mapping[str, str]) -> str:
    labels = [display_names.get(name, name.replace("_", " ")) for name in names]
    return ", ".join(sorted(labels, key=str.casefold))


def _decision(
    step: StepSpec,
    status: DecisionStatus,
    message: str,
    observation: Observation | None,
) -> Decision:
    return Decision(
        step_id=step.id,
        status=status,
        message=message,
        decided_by=DecidedBy.RULE,
        observation=observation,
    )
