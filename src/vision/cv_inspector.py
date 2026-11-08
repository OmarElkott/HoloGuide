"""Classic OpenCV inspector (VIS-07a, VIS-07b, VIS-11, VIS-08).

Flow for one image:
  BGR image -> HSV colour mask per part (thresholds derived from color_rgb in
  assembly.yaml) -> pixel share per part -> present / absent / ambiguous with a
  confidence -> contour checks (four compact bolts, box-shaped covers)
  -> Observation

Like GemmaInspector it only reports evidence: the rules engine decides. It
never raises, and it only ever sees the rendered image and the StepSpec.

Confidence (VIS-11): each part's pixel share is compared with two cut-offs.
Clearly above ``present_min`` or clearly below ``absent_max`` scores 0.75-0.95
(more margin = higher); between them the part is ambiguous and scores 0.50,
below the 0.70 threshold, so the rules send the step to operator review.
The Observation's confidence is the weakest part's confidence.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Any

import cv2
import numpy as np

if __package__ and __package__.startswith("src."):
    from ..config import AssemblyConfig
    from .inspector import InspectorABC, failure_observation, resolve_step
    from .schemas import Observation
else:
    from config import AssemblyConfig
    from vision.inspector import InspectorABC, failure_observation, resolve_step
    from vision.schemas import Observation

AMBIGUOUS_CONFIDENCE = 0.50
MIN_DECIDED_CONFIDENCE, MAX_CONFIDENCE = 0.75, 0.95


@dataclass(frozen=True)
class CVSettings:
    """Tunable thresholds. Fractions are shares of the whole image."""
    hue_tolerance: int = 10          # OpenCV hue units (0-179)
    min_saturation: int = 90         # chromatic parts
    min_value: int = 50
    grey_max_saturation: int = 70    # achromatic parts (base plate)
    grey_value_band: tuple[int, int] = (40, 185)   # floor renders brighter (V >= ~200)
    floor_min_value: int = 190       # foreground = anything not floor-like
    absent_max: float = 0.0008       # <= this share: clearly absent
    present_min: float = 0.0030      # >= this share: clearly present
    # Small parts (bolts) are counted as blobs instead of by pixel share.
    blob_parts: dict[str, int] = field(default_factory=lambda: {"bolts": 4})
    blob_area: tuple[int, int] = (40, 2500)        # pixels per blob at 640x480
    blob_min_fill: float = 0.45      # blob area / bounding-box area
    # Box-shaped parts must look like a box from above.
    box_parts: tuple[str, ...] = ("cover", "wrong_cover")
    box_min_solidity: float = 0.85   # contour area / convex hull area
    box_vertices: tuple[int, int] = (4, 6)   # measured: covers give 4
    max_unexplained: float = 0.015   # non-floor pixels no part explains
    kernel: int = 3


@dataclass
class PartEvidence:
    name: str
    share: float
    state: str                       # present | absent | ambiguous
    confidence: float
    note: str = ""


# ---- VIS-07a: colour masks -----------------------------------------------

def _hsv_of(rgb: tuple[float, float, float]) -> tuple[int, int, int]:
    h, s, v = colorsys.rgb_to_hsv(*rgb)
    return round(h * 179), round(s * 255), round(v * 255)


def part_masks(hsv: np.ndarray, config: AssemblyConfig,
               settings: CVSettings = CVSettings()) -> dict[str, np.ndarray]:
    """One binary mask (uint8 0/255) per configured part, from an HSV image."""
    hue, sat, val = hsv[..., 0].astype(int), hsv[..., 1], hsv[..., 2]
    kernel = np.ones((settings.kernel, settings.kernel), np.uint8)
    masks = {}
    for part in config.parts:
        h, s, _ = _hsv_of(part.color_rgb)
        if s > 100:   # chromatic: match hue (with wrap-around for red)
            dist = np.abs(hue - h)
            dist = np.minimum(dist, 180 - dist)
            mask = (dist <= settings.hue_tolerance) & (sat >= settings.min_saturation) \
                & (val >= settings.min_value)
        else:         # achromatic: low saturation, darker than the floor
            lo, hi = settings.grey_value_band
            mask = (sat <= settings.grey_max_saturation) & (val >= lo) & (val <= hi)
        mask = mask.astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        masks[part.name] = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return masks


def foreground_mask(hsv: np.ndarray, settings: CVSettings = CVSettings()) -> np.ndarray:
    """Everything that is not the bright, unsaturated floor."""
    fg = (hsv[..., 1] > settings.grey_max_saturation) | (hsv[..., 2] < settings.floor_min_value)
    kernel = np.ones((settings.kernel, settings.kernel), np.uint8)
    return cv2.morphologyEx(fg.astype(np.uint8) * 255, cv2.MORPH_OPEN, kernel)


def foreground_bbox(image_bgr: np.ndarray, pad: float = 0.15,
                    settings: CVSettings = CVSettings()) -> tuple[int, int, int, int] | None:
    """Padded (x0, y0, x1, y1) box around the assembly, or None for an empty scene."""
    fg = foreground_mask(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV), settings)
    points = cv2.findNonZero(fg)
    if points is None:
        return None
    x, y, w, h = cv2.boundingRect(points)
    px, py = int(w * pad) + 8, int(h * pad) + 8
    height, width = fg.shape
    return max(0, x - px), max(0, y - py), min(width, x + w + px), min(height, y + h + py)


# ---- VIS-11: pixel share -> confidence -------------------------------------

def presence_confidence(share: float, absent_max: float, present_min: float) -> tuple[str, float]:
    """Map a pixel share to (state, confidence) with more margin = more confidence."""
    span = MAX_CONFIDENCE - MIN_DECIDED_CONFIDENCE
    if share >= present_min:
        margin = min(1.0, (share - present_min) / present_min)
        return "present", round(MIN_DECIDED_CONFIDENCE + span * margin, 3)
    if share <= absent_max:
        margin = 1.0 - share / absent_max if absent_max > 0 else 1.0
        return "absent", round(MIN_DECIDED_CONFIDENCE + span * margin, 3)
    return "ambiguous", AMBIGUOUS_CONFIDENCE


# ---- VIS-08: contour and shape checks --------------------------------------

def count_blobs(mask: np.ndarray, settings: CVSettings = CVSettings()) -> tuple[int, int]:
    """Return (compact blobs of bolt size, other non-trivial blobs)."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    lo, hi = settings.blob_area
    good = odd = 0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < lo / 2:
            continue                                   # speck / anti-aliasing
        _, _, w, h = cv2.boundingRect(contour)
        fill = area / float(w * h) if w * h else 0.0
        if lo <= area <= hi and fill >= settings.blob_min_fill and max(w, h) <= 3 * min(w, h):
            good += 1
        else:
            odd += 1
    return good, odd


def box_shape_ok(mask: np.ndarray, settings: CVSettings = CVSettings()) -> tuple[bool, str]:
    """Does the largest region look like a box seen from above (convex, few corners)?"""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return False, "no region"
    contour = max(contours, key=cv2.contourArea)
    region = np.zeros_like(mask)
    cv2.drawContours(region, [contour], -1, 255, -1)
    # Count real mask pixels, so a ring (hollow outline) is not mistaken for a solid box.
    filled = cv2.countNonZero(cv2.bitwise_and(region, mask))
    hull = cv2.contourArea(cv2.convexHull(contour))
    solidity = filled / hull if hull else 0.0
    approx = cv2.approxPolyDP(contour, 0.03 * cv2.arcLength(contour, True), True)
    lo, hi = settings.box_vertices
    ok = solidity >= settings.box_min_solidity and lo <= len(approx) <= hi
    return ok, f"solidity {solidity:.2f}, {len(approx)} corners"


# ---- VIS-07b: per-part presence + decoy check ------------------------------

def analyse(image_bgr: np.ndarray, config: AssemblyConfig,
            settings: CVSettings = CVSettings()) -> tuple[dict[str, PartEvidence], float]:
    """Per-part evidence plus the share of foreground that no part explains."""
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    total = float(hsv.shape[0] * hsv.shape[1])
    masks = part_masks(hsv, config, settings)
    evidence: dict[str, PartEvidence] = {}
    for name, mask in masks.items():
        share = cv2.countNonZero(mask) / total
        if name in settings.blob_parts:
            evidence[name] = _blob_evidence(name, mask, share, settings)
            continue
        state, conf = presence_confidence(share, settings.absent_max, settings.present_min)
        note = ""
        if state == "present" and name in settings.box_parts:
            ok, note = box_shape_ok(mask, settings)
            if not ok:
                state, conf, note = "ambiguous", AMBIGUOUS_CONFIDENCE, f"not box-shaped ({note})"
        evidence[name] = PartEvidence(name, share, state, conf, note)
    explained = np.zeros_like(next(iter(masks.values())))
    for mask in masks.values():
        explained = cv2.bitwise_or(explained, mask)
    grown = cv2.dilate(explained, np.ones((5, 5), np.uint8))   # ignore edge pixels
    unexplained = cv2.bitwise_and(foreground_mask(hsv, settings), cv2.bitwise_not(grown))
    return evidence, cv2.countNonZero(unexplained) / total


def _blob_evidence(name: str, mask: np.ndarray, share: float,
                   settings: CVSettings) -> PartEvidence:
    expected = settings.blob_parts[name]
    good, odd = count_blobs(mask, settings)
    note = f"{good} found"
    if good == expected and odd == 0:
        return PartEvidence(name, share, "present", MAX_CONFIDENCE, note)
    if good == 0 and odd == 0 and share <= settings.absent_max:
        return PartEvidence(name, share, "absent", MAX_CONFIDENCE, note)
    if odd:
        note += f", {odd} odd-shaped region(s)"
    return PartEvidence(name, share, "ambiguous", AMBIGUOUS_CONFIDENCE, f"{note}, expected {expected}")


class CVInspector(InspectorABC):
    """OpenCV colour + shape inspector. Deterministic, ~10 ms per image, no GPU."""

    def __init__(self, config: AssemblyConfig, settings: CVSettings | None = None):
        self.config = config
        self.settings = settings or CVSettings()
        self.inspection_count = 0
        self.last_status = "idle"            # ok | error
        self.last_details: dict[str, Any] = {}

    def inspect(self, image_path: str | PathLike[str], step: Any) -> Observation:
        return self.inspect_detailed(image_path, step)[0]

    def inspect_detailed(self, image_path: str | PathLike[str], step: Any
                         ) -> tuple[Observation, dict[str, PartEvidence] | None]:
        """Observation plus per-part evidence (None when the image could not be read)."""
        self.inspection_count += 1
        try:
            step = resolve_step(self.config, step)
            image = cv2.imread(str(Path(image_path)), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"unreadable or missing image: {image_path}")
            evidence, unexplained = analyse(image, self.config, self.settings)
            observation = self._observe(evidence, unexplained, step)
        except Exception as exc:  # noqa: BLE001 - contract: never raise
            self.last_status = "error"
            return failure_observation("error", f"OpenCV: {type(exc).__name__}: {exc}"), None
        self.last_status = "ok"
        return observation, evidence

    def _observe(self, evidence: dict[str, PartEvidence], unexplained: float, step) -> Observation:
        detected = sorted(n for n, e in evidence.items() if e.state == "present")
        unsure = sorted(n for n, e in evidence.items() if e.state == "ambiguous")
        confidence = min(e.confidence for e in evidence.values())
        if unexplained > self.settings.max_unexplained:
            confidence = min(confidence, AMBIGUOUS_CONFIDENCE)
        decoys = {p.name for p in self.config.parts if p.is_decoy}
        decided = not unsure and unexplained <= self.settings.max_unexplained
        missing = sorted(set(self.config.expected_parts(step.id)) - set(detected)) if decided else []
        self.last_details = {
            "parts": {n: vars(e) for n, e in evidence.items()},
            "unexplained_share": unexplained,
        }
        return Observation(
            parts_detected=detected,
            missing_parts=missing,
            wrong_parts=sorted(set(detected) & decoys),
            confidence=confidence,
            rationale=self._rationale(evidence, detected, unsure, unexplained),
        )

    def _rationale(self, evidence, detected, unsure, unexplained) -> str:
        names = {p.name: p.display_name for p in self.config.parts}
        seen = ", ".join(names[n] for n in detected) or "no parts"
        text = f"OpenCV: saw {seen}."
        if "bolts" in evidence and evidence["bolts"].note:
            text += f" Bolts: {evidence['bolts'].note}."
        if unsure:
            text += " Unsure about " + "; ".join(
                f"{names[n]} ({evidence[n].note or f'{evidence[n].share:.2%} of image'})"
                for n in unsure) + "."
        if unexplained > self.settings.max_unexplained:
            text += f" {unexplained:.1%} of the image is an unrecognised object."
        return text[:300]
