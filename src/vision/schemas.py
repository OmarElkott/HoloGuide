"""Shared data contracts for HoloGuide.

Observation = what the AI says it sees (untrusted input).
Decision    = what the deterministic rules decide to do (trusted output).
"""

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Observation(BaseModel):
    """Structured result returned by the vision model for one image."""

    parts_detected: list[str] = Field(
        default_factory=list,
        description="Names of assembly parts visible in the image.",
    )
    missing_parts: list[str] = Field(
        default_factory=list,
        description="Expected parts for this step that are NOT visible.",
    )
    wrong_parts: list[str] = Field(
        default_factory=list,
        description="Visible parts that do not belong in this assembly.",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence from 0.0 to 1.0."
    )
    rationale: str = Field(
        default="", description="One short sentence explaining what was seen."
    )


class Action(str, Enum):
    PASS = "pass"        # step verified, workflow may advance
    BLOCK = "block"      # mistake found, workflow must not advance
    REVIEW = "review"    # AI uncertain, operator must decide
    HOLD = "hold"        # AI failed or timed out, safe stop


class Decision(BaseModel):
    """The rules engine's final ruling for one inspection."""

    step_id: int
    action: Action
    message: str
    decided_by: Literal["rule", "operator", "timeout"] = "rule"
    observation: Optional[Observation] = None