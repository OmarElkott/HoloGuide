"""GemmaFirstInspector (VIS-06 option A): Gemma decides presence, OpenCV only cross-checks.

Policy
- Gemma 3 4B inspects every image. Its Observation is what reaches the rules.
- Gemma failure or timeout: its failure Observation is returned unchanged
  (confidence 0.0, so the rules give HOLD). OpenCV never substitutes for it.
- OpenCV then checks Gemma's answer. It can only lower confidence, never add
  a pass: if OpenCV is sure about a part and disagrees with Gemma, or sees an
  unrecognised object, confidence is capped below the threshold and
  missing_parts is cleared, so the step goes to operator review.
- OpenCV unsure about a part, or unable to read the image: no veto on that
  part; Gemma's answer stands.
Neither model ever sees labels; the rules engine still makes every decision.
"""

from __future__ import annotations

from os import PathLike
from typing import Any

if __package__ and __package__.startswith("src."):
    from ..config import AssemblyConfig
    from .cv_inspector import AMBIGUOUS_CONFIDENCE, CVInspector
    from .inspector import InspectorABC, failure_observation, is_failure, resolve_step
    from .schemas import Observation
else:
    from config import AssemblyConfig
    from vision.cv_inspector import AMBIGUOUS_CONFIDENCE, CVInspector
    from vision.inspector import InspectorABC, failure_observation, is_failure, resolve_step
    from vision.schemas import Observation


class GemmaFirstInspector(InspectorABC):
    def __init__(self, config: AssemblyConfig, gemma: InspectorABC, cv: CVInspector) -> None:
        self.config, self.gemma, self.cv = config, gemma, cv
        self.inspection_count = 0
        self.gemma_calls = 0
        self.last_status = "idle"   # agreed | cv_unsure | cv_veto | gemma_failed | error

    def inspect(self, image_path: str | PathLike[str], step: Any) -> Observation:
        self.inspection_count += 1
        try:
            return self._inspect(image_path, resolve_step(self.config, step))
        except Exception as exc:  # noqa: BLE001 - contract: never raise
            self.last_status = "error"
            return failure_observation("error", f"GemmaFirst: {type(exc).__name__}: {exc}")

    def _inspect(self, image_path, step) -> Observation:
        self.gemma_calls += 1
        obs = self.gemma.inspect(image_path, step)
        # Tolerate an Observation imported under the other path style (vision. vs src.vision.).
        gemma_obs = obs if isinstance(obs, Observation) else Observation.model_validate(
            obs.model_dump() if hasattr(obs, "model_dump") else obs)
        if is_failure(gemma_obs):
            self.last_status = "gemma_failed"
            return gemma_obs

        _, evidence = self.cv.inspect_detailed(image_path, step)
        rationale = f"Gemma: {gemma_obs.rationale}"
        if evidence is None:
            self.last_status = "cv_unsure"
            return gemma_obs.model_copy(update={"rationale": self._join(
                rationale, "OpenCV check unavailable.")})

        names = {p.name: p.display_name for p in self.config.parts}
        detected = set(gemma_obs.parts_detected)
        sure = {n for n, e in evidence.items() if e.state != "ambiguous"}
        conflicts = sorted(n for n in sure if (evidence[n].state == "present") != (n in detected))
        unrecognised = self.cv.last_details.get("unexplained_share", 0.0) > self.cv.settings.max_unexplained
        if conflicts or unrecognised:
            self.last_status = "cv_veto"
            reason = (f"OpenCV disagrees about {', '.join(names[n] for n in conflicts)}"
                      if conflicts else "OpenCV sees an unrecognised object")
            return gemma_obs.model_copy(update={
                "confidence": min(gemma_obs.confidence, AMBIGUOUS_CONFIDENCE),
                "missing_parts": [],
                "rationale": self._join(rationale, f"{reason}; operator review needed."),
            })
        self.last_status = "agreed" if sure == set(evidence) else "cv_unsure"
        note = "OpenCV agrees." if self.last_status == "agreed" else "OpenCV partly unsure; no conflict."
        return gemma_obs.model_copy(update={"rationale": self._join(rationale, note)})

    @staticmethod
    def _join(first: str, second: str) -> str:
        """Fit into 300 characters, shortening Gemma's text so the OpenCV note always survives."""
        room = 300 - len(second) - 1
        if len(first) > room:
            first = first[:max(room - 1, 0)].rstrip() + "…"
        return f"{first} {second}".strip()[:300]
