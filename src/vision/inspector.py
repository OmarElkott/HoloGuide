"""Inspector contract, test doubles and the timeout wrapper (VIS-04).

Contract (docs/HOLOGUIDE_ROADMAP.md section 8): an inspector NEVER raises into
the workflow. Any failure or timeout comes back as an Observation with
confidence 0.0, which the rules engine maps to HOLD.

``step`` may be a StepSpec (what workflow.py passes today) or a plain step id
(what the roadmap text describes); both are accepted.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from os import PathLike
from typing import Any

if __package__ and __package__.startswith("src."):
    from ..config import AssemblyConfig, StepSpec
    from .schemas import Observation, uncertain_observation
else:
    from config import AssemblyConfig, StepSpec
    from vision.schemas import Observation, uncertain_observation

TIMEOUT_PREFIX = "TIMEOUT:"
FAILURE_PREFIX = "INSPECTION FAILED"


def resolve_step(config: AssemblyConfig, step: Any) -> StepSpec:
    """Accept a StepSpec or a 1-based step id and return the StepSpec."""
    if hasattr(step, "adds_parts") and hasattr(step, "id"):
        return step
    if isinstance(step, bool) or not isinstance(step, int):
        raise TypeError("step must be a StepSpec or an integer step id")
    if not 1 <= step <= len(config.steps):
        raise ValueError(f"step id {step} is outside 1..{len(config.steps)}")
    return config.step(step)


def failure_observation(kind: str, detail: str) -> Observation:
    return uncertain_observation(f"{FAILURE_PREFIX} ({kind}): {detail}"[:300])


def timeout_observation(timeout_s: float) -> Observation:
    return uncertain_observation(f"{TIMEOUT_PREFIX} inspection exceeded {timeout_s:g}s")


def is_timeout(observation: Observation) -> bool:
    return observation.rationale.startswith(TIMEOUT_PREFIX)


def is_failure(observation: Observation) -> bool:
    return observation.rationale.startswith((FAILURE_PREFIX, TIMEOUT_PREFIX))


class InspectorABC(ABC):
    @abstractmethod
    def inspect(self, image_path: str | PathLike[str], step: Any) -> Observation:
        """Return an Observation for one image. Must not raise."""


class MockInspector(InspectorABC):
    """Canned observations for teammates; ignores the image. Not AI."""

    MODES = ("correct", "missing", "wrong", "uncertain")

    def __init__(self, config: AssemblyConfig, mode: str = "correct", target: str | None = None):
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}")
        self.config, self.mode, self.target = config, mode, target
        self.inspection_count = 0

    def inspect(self, image_path, step) -> Observation:
        step = resolve_step(self.config, step)
        self.inspection_count += 1
        expected = set(self.config.expected_parts(step.id))
        target = self.target or step.adds_parts[0]
        decoys = [p.name for p in self.config.parts if p.is_decoy]
        if self.mode == "missing":
            return Observation(parts_detected=sorted(expected - {target}), missing_parts=[target],
                               confidence=0.9, rationale="Mock: a required part is absent.")
        if self.mode == "wrong":
            decoy = f"wrong_{target}" if f"wrong_{target}" in decoys else (decoys[0] if decoys else target)
            return Observation(parts_detected=sorted((expected - {target}) | {decoy}),
                               missing_parts=[target], wrong_parts=[decoy], confidence=0.9,
                               rationale="Mock: a wrong part is visible.")
        confidence = 0.4 if self.mode == "uncertain" else 0.95
        return Observation(parts_detected=sorted(expected), confidence=confidence,
                           rationale=f"Mock: {self.mode} fixture.")


class SlowInspector(InspectorABC):
    """Sleeps before delegating; used to exercise the timeout and HOLD path."""

    def __init__(self, inner: InspectorABC, delay_s: float):
        self.inner, self.delay_s = inner, delay_s
        self.inspection_count = 0

    def inspect(self, image_path, step) -> Observation:
        self.inspection_count += 1
        time.sleep(self.delay_s)
        return self.inner.inspect(image_path, step)


class TimeoutInspector(InspectorABC):
    """Run ``inner.inspect`` with a deadline; on expiry return a timeout Observation.

    The call runs in a daemon thread. Python cannot kill a thread, so a timed-out
    call is abandoned and its late result is discarded (it can never reach the
    workflow). GemmaInspector also sets an HTTP timeout so the request itself
    ends instead of lingering.
    """

    def __init__(self, inner: InspectorABC, timeout_s: float):
        if not timeout_s > 0:
            raise ValueError("timeout_s must be positive")
        self.inner, self.timeout_s = inner, float(timeout_s)
        self.inspection_count = 0
        self.last_status = "idle"          # ok | timeout | error
        self.last_latency_s = 0.0

    @property
    def inner_status(self) -> str | None:
        return getattr(self.inner, "last_status", None)

    def inspect(self, image_path, step) -> Observation:
        self.inspection_count += 1
        box: dict[str, Any] = {}

        def work() -> None:
            try:
                box["obs"] = self.inner.inspect(image_path, step)
            except Exception as exc:  # noqa: BLE001 - contract: never raise
                box["err"] = exc

        t0 = time.perf_counter()
        worker = threading.Thread(target=work, daemon=True, name="hologuide-inspect")
        worker.start()
        worker.join(self.timeout_s)
        self.last_latency_s = time.perf_counter() - t0
        if worker.is_alive():
            self.last_status = "timeout"
            return timeout_observation(self.timeout_s)
        if "err" in box:
            self.last_status = "error"
            return failure_observation("error", f"{type(box['err']).__name__}: {box['err']}")
        try:
            observation = Observation.model_validate(box["obs"])
        except Exception as exc:  # noqa: BLE001
            self.last_status = "error"
            return failure_observation("invalid observation", str(exc))
        self.last_status = "ok"
        return observation
