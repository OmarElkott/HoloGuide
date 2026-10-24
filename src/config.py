from __future__ import annotations
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "assembly.yaml"


class CameraSpec(BaseModel):
    width: int = 640
    height: int = 480
    target: tuple[float, float, float] = (0.0, 0.0, 0.03)
    distance: float = 0.75
    yaw: float = 45
    pitch: float = -55


class PartSpec(BaseModel):
    name: str
    display_name: str
    shape: Literal["box", "cylinder"]
    color_rgb: tuple[float, float, float]
    is_decoy: bool = False


class StepSpec(BaseModel):
    id: int
    name: str
    instruction: str
    adds_parts: list[str]


class AssemblyConfig(BaseModel):
    name: str
    confidence_threshold: float = Field(0.70, ge=0, le=1)
    ai_timeout_s: float = Field(20, gt=0)
    camera: CameraSpec = CameraSpec()
    parts: list[PartSpec]
    steps: list[StepSpec]

    @model_validator(mode="after")
    def _check_consistency(self) -> "AssemblyConfig":
        names = [p.name for p in self.parts]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate part names in config")
        known = set(names)
        for step in self.steps:
            for part in step.adds_parts:
                if part not in known:
                    raise ValueError(f"Step {step.id} references unknown part '{part}'")
        ids = [s.id for s in self.steps]
        if ids != list(range(1, len(ids) + 1)):
            raise ValueError("Step ids must be consecutive starting at 1")
        return self

    def part(self, name: str) -> PartSpec:
        return next(p for p in self.parts if p.name == name)

    def step(self, step_id: int) -> StepSpec:
        return self.steps[step_id - 1]

    def expected_parts(self, step_id: int) -> set[str]:
        """All parts that must be visible once `step_id` is complete."""
        expected: set[str] = set()
        for s in self.steps[:step_id]:
            expected.update(s.adds_parts)
        return expected


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> AssemblyConfig:
    with open(path, "r", encoding="utf-8") as f:
        return AssemblyConfig.model_validate(yaml.safe_load(f))