"""Foreign-object identification (FOD): OpenCV finds WHERE, Gemma says WHAT.

OpenCV can tell that some pixels belong to no known part, but not what they are.
Gemma 3 4B on the full 640x480 frame misses small objects (measured: it answered
"no other object" for 6 of 6 tool/rag scenes). So the two are combined:

  OpenCV unexplained-pixel box -> square crop, upscaled -> Gemma: kind + name
  kind in {tool, cloth, debris}      -> foreign object (the rules BLOCK, naming it)
  kind assembly_part / unclear / any failure -> not identified (the step goes to review)

Gemma only ever sees the crop of the image. It never sees labels or simulator
ground truth, and it never decides: the rules engine does.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from os import PathLike
from typing import Any

FOREIGN_KINDS = ("tool", "cloth", "debris")
KINDS = (*FOREIGN_KINDS, "assembly_part", "unclear")

PROMPT = (
    "This is a close-up crop from an overhead camera over an assembly bench. The assembly itself "
    "is made of a grey plate, an orange gasket layer, a blue (or green) square cover and red pegs. "
    "Look at the object in the CENTRE of the crop. Is it a foreign object that should not be there "
    "(a tool, a cloth or rag, or debris), or just a piece of the assembly? "
    'Give "kind", a short "name" with its colour, and "certainty".'
)
SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS)},
        "name": {"type": "string"},
        "certainty": {"type": "string", "enum": ["clear", "partly_clear", "unclear"]},
    },
    "required": ["kind", "name", "certainty"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Identification:
    kind: str                 # one of KINDS, or "error"
    name: str = ""
    certainty: str = "unclear"
    detail: str = ""

    @property
    def is_foreign(self) -> bool:
        return self.kind in FOREIGN_KINDS

    def label(self) -> str:
        """Short name for the operator, e.g. 'purple screwdriver (tool)'."""
        name = " ".join(self.name.split())[:60] or "unidentified object"
        return f"{name} ({self.kind})"


def crop_region(image_path: str | PathLike[str], box: tuple[int, int, int, int],
                pad: int = 20, side: int = 336) -> bytes:
    """Square PNG crop centred on ``box``, padded and resized to ``side`` pixels."""
    from PIL import Image
    x0, y0, x1, y1 = box
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        size = max(x1 - x0, y1 - y0) + 2 * pad
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        left, top = max(0, cx - size // 2), max(0, cy - size // 2)
        crop = img.crop((left, top, min(img.width, left + size), min(img.height, top + size)))
        crop = crop.resize((side, side), Image.LANCZOS)
        buffer = io.BytesIO()
        crop.save(buffer, format="PNG")
    return buffer.getvalue()


class ForeignObjectIdentifier:
    """Asks Gemma what an unexplained region is. Never raises."""

    def __init__(self, *, model: str = "gemma3:4b", host: str | None = None,
                 timeout_s: float = 20.0, client: Any = None, keep_alive: str | int = "30m") -> None:
        self.model, self.host, self.timeout_s = model, host, timeout_s
        self.keep_alive = keep_alive
        self._client = client
        self.calls = 0
        # Same options as GemmaInspector, so Ollama does not reload the model between calls.
        self._options = {"temperature": 0, "seed": 0, "num_predict": 120, "num_ctx": 2048}

    @property
    def client(self) -> Any:
        if self._client is None:
            from ollama import Client  # lazily, so tests need no Ollama
            self._client = Client(host=self.host, timeout=self.timeout_s)
        return self._client

    def identify(self, image_path: str | PathLike[str],
                 box: tuple[int, int, int, int] | None) -> Identification:
        self.calls += 1
        if box is None:
            return Identification("error", detail="no region to identify")
        try:
            message = {"role": "user", "content": PROMPT, "images": [crop_region(image_path, box)]}
            response = self.client.chat(model=self.model, messages=[message], format=SCHEMA,
                                        options=self._options, keep_alive=self.keep_alive)
            content = getattr(getattr(response, "message", None), "content", None)
            if content is None and isinstance(response, dict):
                content = (response.get("message") or {}).get("content")
            data = json.loads(content)
            kind = data.get("kind")
            if kind not in KINDS:
                return Identification("error", detail=f"unexpected kind {kind!r}")
            return Identification(kind, str(data.get("name", "")), str(data.get("certainty", "unclear")))
        except Exception as exc:  # noqa: BLE001 - contract: never raise; caller sends to review
            name = "TimeoutError" if "timed out" in str(exc).lower() else type(exc).__name__
            return Identification("error", detail=f"{name}: {exc}"[:200])
