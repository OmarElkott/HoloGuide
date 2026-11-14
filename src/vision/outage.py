"""Simulated AI outage: make Gemma unreachable inside a running inspector (demo + tests).

Swaps the Gemma parts of an inspector for stand-ins that fail exactly like an
unreachable Ollama would (a failure Observation / an error Identification), so the
real failure paths run: Hybrid keeps deciding known parts with OpenCV and sends
anything that needs Gemma to review; Gemma-first and Gemma-only give HOLD.
Nothing is faked downstream: the rules and workflow see ordinary failures.
"""
from __future__ import annotations

if __package__ and __package__.startswith("src."):
    from .foreign_objects import Identification
    from .inspector import InspectorABC, failure_observation
else:
    from vision.foreign_objects import Identification
    from vision.inspector import InspectorABC, failure_observation

DETAIL = "AI OUTAGE (simulated): Gemma endpoint unreachable"


class UnreachableGemma(InspectorABC):
    def inspect(self, image_path, step):
        return failure_observation("error", f"ConnectionError: {DETAIL}")


class UnreachableIdentifier:
    calls = 0

    def identify(self, image_path, box):
        return Identification("error", detail=DETAIL)


def set_ai_outage(inspector, on: bool) -> bool:
    """Turn the simulated outage on or off. Returns False if the inspector has no Gemma part."""
    slots = [(name, stub) for name, stub in (("gemma", UnreachableGemma()), ("identifier", UnreachableIdentifier()),
                                            ("inner", UnreachableGemma()))
             if hasattr(inspector, name) and (name != "inner" or type(inspector.inner).__name__ == "GemmaInspector")]
    if not slots:
        return False
    backup = getattr(inspector, "_ai_outage_backup", None)
    if on and backup is None:
        inspector._ai_outage_backup = {name: getattr(inspector, name) for name, _ in slots}
        for name, stub in slots:
            setattr(inspector, name, stub)
    elif not on and backup is not None:
        for name, original in backup.items():
            setattr(inspector, name, original)
        inspector._ai_outage_backup = None
    return True


def ai_outage_active(inspector) -> bool:
    return getattr(inspector, "_ai_outage_backup", None) is not None
