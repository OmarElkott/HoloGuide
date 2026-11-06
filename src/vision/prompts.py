"""Prompt templates and the response schema for the Gemma inspector (VIS-01).

The prompt is built from ``config/assembly.yaml`` so part names, colours and
step names can never drift from the assembly definition. It deliberately does
NOT tell the model which parts are expected: a small model that is told what
should be there tends to "see" it. The model only reports what is visible;
missing parts are derived afterwards in code (see gemma_inspector.py).
"""

from __future__ import annotations

if __package__ and __package__.startswith("src."):
    from ..config import AssemblyConfig, StepSpec
else:
    from config import AssemblyConfig, StepSpec

PROMPT_VERSION = "v1"
CERTAINTY_LEVELS = ("clear", "partly_clear", "unclear")

# Nearest-colour naming keeps the prompt in sync with color_rgb in the YAML.
_PALETTE: dict[str, tuple[float, float, float]] = {
    "grey": (0.55, 0.55, 0.60),
    "white": (0.95, 0.95, 0.95),
    "black": (0.05, 0.05, 0.05),
    "red": (0.85, 0.10, 0.10),
    "orange": (1.00, 0.55, 0.00),
    "yellow": (0.95, 0.90, 0.10),
    "green": (0.10, 0.70, 0.20),
    "blue": (0.10, 0.30, 0.90),
    "purple": (0.55, 0.15, 0.70),
    "brown": (0.45, 0.28, 0.12),
}
_SHAPE_WORDS = {"box": "flat rectangular piece", "cylinder": "small round peg"}

# Optional "where to look" hints, keyed by step id. Missing ids fall back to
# the step instruction from the YAML, so editing the config never breaks this.
STEP_HINTS: dict[int, str] = {
    1: "Look at the work surface: the base plate should be the lowest, largest piece.",
    2: "Look on top of the base plate for a thin flat layer (the gasket).",
    3: "Look for a cover lying on top of the stack. A thin rim of the layer "
       "below may stick out around the cover's edges.",
    4: "Look for small round pegs near the corners of the top piece.",
}


def color_name(rgb: tuple[float, float, float]) -> str:
    return min(_PALETTE, key=lambda n: sum((a - b) ** 2 for a, b in zip(rgb, _PALETTE[n])))


def part_catalog(config: AssemblyConfig) -> list[str]:
    lines = []
    for part in config.parts:
        shape = _SHAPE_WORDS.get(part.shape, part.shape)
        lines.append(f"- {part.name}: {color_name(part.color_rgb)} {shape} ({part.display_name})")
    return lines


def build_schema(config: AssemblyConfig) -> dict:
    """JSON schema sent to Ollama. Part names are restricted to the catalog."""
    return {
        "type": "object",
        "properties": {
            "parts_detected": {
                "type": "array",
                "items": {"type": "string", "enum": [p.name for p in config.parts]},
            },
            "certainty": {"type": "string", "enum": list(CERTAINTY_LEVELS)},
            "rationale": {"type": "string"},
        },
        "required": ["parts_detected", "certainty", "rationale"],
        "additionalProperties": False,
    }


def build_prompt(config: AssemblyConfig, step: StepSpec) -> str:
    hint = STEP_HINTS.get(step.id, step.instruction)
    catalog = "\n".join(part_catalog(config))
    return f"""You are a quality inspector looking at ONE overhead camera image of a small assembly.
Step being checked: {step.id} of {len(config.steps)} - {step.name}.
{hint}

Parts you may report (use these exact names only):
{catalog}

Rules:
- Put a part in "parts_detected" ONLY if you can clearly see it in the image.
- Do not guess, and do not list a part just because it would normally be there.
- Tell parts apart by colour and shape.
- "certainty" is "clear" if every part is easy to see, "partly_clear" if something is hard to see, and "unclear" if you cannot judge the image.
- "rationale" is one short sentence about what you see.

Reply with JSON only, in exactly this shape:
{{"parts_detected": ["<part_name>", "..."], "certainty": "<clear|partly_clear|unclear>", "rationale": "<one short sentence>"}}"""
