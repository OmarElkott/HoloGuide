from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class Observation(BaseModel):
    """What the AI inspector reports about ONE image. It never decides workflow."""
    parts_detected: list[str] = Field(
        default_factory=list, description="Part names visible in the image")
    missing_parts: list[str] = Field(
        default_factory=list, description="Expected parts that are not visible")
    wrong_parts: list[str] = Field(
        default_factory=list, description="Visible parts that should not be there")
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    rationale: str = Field("", description="One short sentence explaining what was seen")


class DecisionStatus(str, Enum):
    PASS = "pass"                    # step verified, workflow may advance
    BLOCK = "block"                  # mistake found, workflow stays on this step
    NEEDS_REVIEW = "needs_review"    # AI unsure, operator must look
    HOLD = "hold"                    # AI timeout/failure, safe stop


class DecidedBy(str, Enum):
    RULE = "rule"
    OPERATOR = "operator"
    SYSTEM = "system"


class Decision(BaseModel):
    """What the deterministic workflow decides. Only the control layer creates this."""
    step_id: int
    status: DecisionStatus
    message: str
    decided_by: DecidedBy = DecidedBy.RULE
    observation: Observation | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def uncertain_observation(reason: str) -> Observation:
    """Safe fallback when the AI output cannot be parsed or the call fails."""
    return Observation(confidence=0.0, rationale=reason)