"""HybridInspector (VIS-09): OpenCV decides presence, Gemma explains and handles unclear cases.

Policy
- OpenCV runs first on every image (fast, deterministic).
- OpenCV sure (confidence >= threshold, no ambiguous part): its Observation is
  returned. With ``explain="always"`` Gemma's sentence is appended to the
  rationale as text only; it never changes which parts are reported.
- OpenCV unsure about one or more parts: Gemma is asked. Its answer is used
  only for those ambiguous parts and only when Gemma itself is "clear".
  If Gemma contradicts a part OpenCV was sure about, the result stays below
  the threshold (operator review).
- Gemma failure or timeout: the OpenCV Observation is returned capped below
  the threshold, so the step goes to Needs Review instead of passing.
- Foreign objects (with an ``identifier``): when OpenCV finds pixels no part
  explains, Gemma is shown a crop of just that region. If it names a tool,
  cloth or debris, the Observation lists it in ``foreign_objects`` (the rules
  BLOCK and name it). Any other answer, or a failure, goes to operator review.
Neither model ever sees labels; the rules engine still makes every decision.
"""

from __future__ import annotations

from os import PathLike
from typing import Any, Literal

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


class HybridInspector(InspectorABC):
    def __init__(self, config: AssemblyConfig, cv: CVInspector, gemma: InspectorABC,
                 *, explain: Literal["unclear", "always"] = "unclear",
                 threshold: float | None = None, identifier: Any = None) -> None:
        if explain not in ("unclear", "always"):
            raise ValueError('explain must be "unclear" or "always"')
        self.config, self.cv, self.gemma, self.explain = config, cv, gemma, explain
        self.identifier = identifier
        self.threshold = config.confidence_threshold if threshold is None else threshold
        self.inspection_count = 0
        self.last_status = "idle"   # cv_only | cv_explained | gemma_resolved | gemma_conflict | gemma_unavailable | foreign_identified | foreign_unconfirmed | error
        self.gemma_calls = 0

    def inspect(self, image_path: str | PathLike[str], step: Any) -> Observation:
        self.inspection_count += 1
        try:
            return self._inspect(image_path, resolve_step(self.config, step))
        except Exception as exc:  # noqa: BLE001 - contract: never raise
            self.last_status = "error"
            return failure_observation("error", f"Hybrid: {type(exc).__name__}: {exc}")

    def _inspect(self, image_path, step) -> Observation:
        cv_obs, evidence = self.cv.inspect_detailed(image_path, step)
        if evidence is None:                       # image unreadable: HOLD path
            self.last_status = "error"
            return cv_obs
        if self.identifier is not None and \
                self.cv.last_details.get("unexplained_share", 0.0) > self.cv.settings.max_unexplained:
            return self._foreign(image_path, cv_obs)
        unsure = {n for n, e in evidence.items() if e.state == "ambiguous"}
        if cv_obs.confidence >= self.threshold and not unsure:
            if self.explain == "always":
                gemma_obs = self._ask_gemma(image_path, step)
                if not is_failure(gemma_obs):
                    self.last_status = "cv_explained"
                    return cv_obs.model_copy(update={"rationale": self._join(
                        cv_obs.rationale, f"Gemma: {gemma_obs.rationale}")})
            self.last_status = "cv_only"
            return cv_obs

        gemma_obs = self._ask_gemma(image_path, step)
        if is_failure(gemma_obs):
            self.last_status = "gemma_unavailable"
            return self._capped(cv_obs, f"Gemma unavailable ({gemma_obs.rationale})")
        if not unsure or gemma_obs.confidence < self.threshold:
            # Gemma cannot clear an unrecognised object, and an unsure Gemma settles nothing.
            self.last_status = "gemma_conflict"
            return self._capped(cv_obs, f"Gemma: {gemma_obs.rationale}")

        cv_detected = set(cv_obs.parts_detected)
        gemma_detected = set(gemma_obs.parts_detected)
        sure = set(evidence) - unsure
        conflicts = {n for n in sure if (n in cv_detected) != (n in gemma_detected)}
        if conflicts:
            self.last_status = "gemma_conflict"
            return self._capped(cv_obs, f"Gemma disagrees about {', '.join(sorted(conflicts))}: "
                                        f"{gemma_obs.rationale}")

        detected = cv_detected | (unsure & gemma_detected)
        decoys = {p.name for p in self.config.parts if p.is_decoy}
        confidence = min([gemma_obs.confidence] + [evidence[n].confidence for n in sure])
        self.last_status = "gemma_resolved"
        return Observation(
            parts_detected=sorted(detected),
            missing_parts=sorted(set(self.config.expected_parts(step.id)) - detected),
            wrong_parts=sorted(detected & decoys),
            confidence=confidence,
            rationale=self._join(cv_obs.rationale,
                                 f"Gemma settled {', '.join(sorted(unsure))}: {gemma_obs.rationale}"),
        )

    def _foreign(self, image_path, cv_obs: Observation) -> Observation:
        self.gemma_calls += 1
        found = self.identifier.identify(image_path, self.cv.last_details.get("unexplained_bbox"))
        if found.is_foreign:
            self.last_status = "foreign_identified"
            return cv_obs.model_copy(update={
                "foreign_objects": [found.label()],
                "rationale": self._join(cv_obs.rationale, f"Gemma identified it as a {found.label()}."),
            })
        self.last_status = "foreign_unconfirmed"
        said = f"Gemma: {found.label()}" if found.kind != "error" else f"Gemma unavailable ({found.detail})"
        return self._capped(cv_obs, f"{said}; operator must check the unrecognised object.")

    def _ask_gemma(self, image_path, step) -> Observation:
        self.gemma_calls += 1
        obs = self.gemma.inspect(image_path, step)
        # Tolerate an Observation imported under the other path style (vision. vs src.vision.).
        return obs if isinstance(obs, Observation) else Observation.model_validate(
            obs.model_dump() if hasattr(obs, "model_dump") else obs)

    def _capped(self, cv_obs: Observation, note: str) -> Observation:
        return cv_obs.model_copy(update={
            "confidence": min(cv_obs.confidence, AMBIGUOUS_CONFIDENCE),
            "missing_parts": [],
            "rationale": self._join(cv_obs.rationale, note),
        })

    @staticmethod
    def _join(first: str, second: str) -> str:
        return f"{first} {second}".strip()[:300]
