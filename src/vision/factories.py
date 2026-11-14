"""Zero-argument inspector factories for the CLIs and the dashboard.

Use as ``module:function`` anywhere a factory is accepted, e.g.::

    python -m src.evaluation.runner outputs/dataset-v1/labels.csv \\
        --output outputs/evaluation/hybrid.csv --inspector-factory src.vision.factories:hybrid
    HOLOGUIDE_INSPECTOR_FACTORY=src.vision.factories:hybrid   # dashboard real mode

All inspectors accept a StepSpec, so no ``--step-id-interface`` adapter is needed.
Gemma settings come from HOLOGUIDE_MODEL_NAME / HOLOGUIDE_MODEL_ENDPOINT (the
same variables the dashboard backend reads) and HOLOGUIDE_PROMPT_VERSION;
nothing is ever downloaded.
"""

from __future__ import annotations

import os
from typing import Any

if __package__ and __package__.startswith("src."):
    from ..config import AssemblyConfig, load_config
    from .cv_inspector import CVInspector
    from .foreign_objects import ForeignObjectIdentifier
    from .gemma_first_inspector import GemmaFirstInspector
    from .gemma_inspector import DEFAULT_MODEL, make_default_inspector
    from .hybrid_inspector import HybridInspector
    from .inspector import TimeoutInspector
else:
    from config import AssemblyConfig, load_config
    from vision.cv_inspector import CVInspector
    from vision.foreign_objects import ForeignObjectIdentifier
    from vision.gemma_first_inspector import GemmaFirstInspector
    from vision.gemma_inspector import DEFAULT_MODEL, make_default_inspector
    from vision.hybrid_inspector import HybridInspector
    from vision.inspector import TimeoutInspector


# Prompt used by the factories; v2 is chosen from measured run-2 results (docs/decisions.md).
# HOLOGUIDE_PROMPT_VERSION=v1 reproduces the run-1 baseline.
GEMMA_PROMPT_VERSION = "v2"


def gemma_settings() -> dict[str, Any]:
    """GemmaInspector keyword arguments from the environment."""
    return {"model": os.environ.get("HOLOGUIDE_MODEL_NAME") or DEFAULT_MODEL,
            "host": os.environ.get("HOLOGUIDE_MODEL_ENDPOINT") or None,
            "prompt_version": os.environ.get("HOLOGUIDE_PROMPT_VERSION") or GEMMA_PROMPT_VERSION}


def foreign_identifier(config: AssemblyConfig | None = None) -> ForeignObjectIdentifier:
    """Gemma on a crop of OpenCV's unexplained region; request timeout = ai_timeout_s."""
    config = config or load_config()
    settings = gemma_settings()
    return ForeignObjectIdentifier(model=settings["model"], host=settings["host"],
                                   timeout_s=config.ai_timeout_s)


def gemma(config: AssemblyConfig | None = None) -> TimeoutInspector:
    """Gemma 3 4B via local Ollama, wrapped in the config's ai_timeout_s."""
    return make_default_inspector(config or load_config(), **gemma_settings())


def cv(config: AssemblyConfig | None = None) -> TimeoutInspector:
    """OpenCV colour + shape inspector (no model, no GPU)."""
    config = config or load_config()
    return TimeoutInspector(CVInspector(config), config.ai_timeout_s)


def hybrid(config: AssemblyConfig | None = None, explain: str = "unclear") -> HybridInspector:
    """OpenCV decides presence; Gemma (timeout-wrapped) handles unclear images.

    Not wrapped in an outer timeout: OpenCV takes milliseconds and the Gemma
    call already has its own ai_timeout_s, whose expiry gives Needs Review.
    """
    config = config or load_config()
    return HybridInspector(config, CVInspector(config), gemma(config), explain=explain,
                           identifier=foreign_identifier(config))


def hybrid_explained(config: AssemblyConfig | None = None) -> HybridInspector:
    """Hybrid that also asks Gemma for a sentence on every image (slower; text only)."""
    return hybrid(config, explain="always")


def gemma_first(config: AssemblyConfig | None = None) -> GemmaFirstInspector:
    """Gemma decides every image; OpenCV can only send a disagreement to operator review.

    Not wrapped in an outer timeout: the Gemma call has its own ai_timeout_s
    (expiry gives HOLD) and the OpenCV check takes milliseconds.
    """
    config = config or load_config()
    return GemmaFirstInspector(config, gemma(config), CVInspector(config),
                               identifier=foreign_identifier(config))
