"""Gemma 3 4B inspector through Ollama (VIS-02, VIS-03, VIS-05).

Flow for one image:
  prompt + image -> Ollama (schema-constrained JSON) -> validate -> Observation
Failure handling: one retry, then a safe Observation with confidence 0.0.
The inspector never raises into the workflow and never decides workflow state.

What the model reports vs. what code derives
  model : parts_detected, certainty (clear / partly_clear / unclear), rationale
  code  : confidence (from certainty), wrong_parts (detected decoys),
          missing_parts (expected minus detected, only when certainty is "clear")
Leaving missing_parts empty on shaky evidence lets the rules engine's
confidence threshold send it to operator review instead of a hard block.
"""

from __future__ import annotations

import io
import json
import time
from os import PathLike
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

if __package__ and __package__.startswith("src."):
    from ..config import AssemblyConfig
    from .inspector import (InspectorABC, TimeoutInspector, failure_observation,
                            resolve_step)
    from .prompts import build_prompt, build_schema
    from .schemas import Observation
else:
    from config import AssemblyConfig
    from vision.inspector import (InspectorABC, TimeoutInspector, failure_observation,
                                  resolve_step)
    from vision.prompts import build_prompt, build_schema
    from vision.schemas import Observation

DEFAULT_MODEL = "gemma3:4b"

# Certainty words are far more reliable than a 4B model's free-form number.
# Values sit around the 0.70 threshold in assembly.yaml: "clear" can pass,
# anything weaker goes to operator review.
CERTAINTY_TO_CONFIDENCE = {"clear": 0.90, "partly_clear": 0.60, "unclear": 0.30}


class ReplyFormatError(ValueError):
    """The model answered, but not in a usable shape."""


class _ModelReply(BaseModel):
    parts_detected: list[str]
    certainty: Literal["clear", "partly_clear", "unclear"]
    rationale: str = ""


def parse_reply(content: Any) -> _ModelReply:
    if not isinstance(content, str) or not content.strip():
        raise ReplyFormatError("empty response")
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return _ModelReply.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ReplyFormatError(f"malformed JSON reply: {str(exc)[:120]}") from exc


def _message_content(response: Any) -> Any:
    message = getattr(response, "message", None)
    if message is None and isinstance(response, dict):
        message = response.get("message")
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


class GemmaInspector(InspectorABC):
    def __init__(
        self,
        config: AssemblyConfig,
        *,
        model: str = DEFAULT_MODEL,
        host: str | None = None,
        client: Any = None,
        keep_alive: str | int = "30m",
        max_retries: int = 1,
        request_timeout_s: float | None = None,
        max_image_side: int | None = None,
        num_ctx: int | None = 2048,
        num_predict: int = 256,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.config, self.model = config, model
        self.keep_alive, self.max_retries = keep_alive, max_retries
        self.max_image_side = max_image_side
        self._client = client
        self._host, self._request_timeout_s = host, request_timeout_s
        # The same options are used for warm-up and for inspection; changing
        # num_ctx between them would make Ollama reload the model.
        self._options: dict[str, Any] = {"temperature": 0, "seed": 0, "num_predict": num_predict}
        if num_ctx:
            self._options["num_ctx"] = num_ctx
        self.inspection_count = 0
        self.last_status = "idle"      # ok | retried_ok | parse_failure | error
        self.last_detail = ""
        self.last_latency_s = 0.0
        self.last_raw_responses: list[str] = []

    @property
    def client(self) -> Any:
        if self._client is None:
            from ollama import Client  # imported lazily so tests need no Ollama
            self._client = Client(host=self._host, timeout=self._request_timeout_s)
        return self._client

    # ---- VIS-05 -------------------------------------------------------
    def warm_up(self) -> dict[str, Any]:
        """Load the model into memory and keep it there. Returns timing info."""
        t0 = time.perf_counter()
        try:
            self.client.generate(model=self.model, keep_alive=self.keep_alive, options=self._options)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "seconds": time.perf_counter() - t0,
                    "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "seconds": time.perf_counter() - t0, "error": None}

    def unload(self) -> None:
        """Free the model (keep_alive=0). Used to measure a true cold start."""
        self.client.generate(model=self.model, keep_alive=0)

    def memory_report(self) -> dict[str, Any]:
        """Ask Ollama how much of the loaded model sits in VRAM (like `ollama ps`)."""
        try:
            for item in getattr(self.client.ps(), "models", []) or []:
                name = getattr(item, "model", None) or getattr(item, "name", "")
                if str(name).startswith(self.model.split(":")[0]):
                    size, vram = getattr(item, "size", 0) or 0, getattr(item, "size_vram", 0) or 0
                    return {"model": name, "size_bytes": size, "vram_bytes": vram,
                            "gpu_fraction": (vram / size) if size else None}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {}

    # ---- VIS-02 / VIS-03 ---------------------------------------------
    def inspect(self, image_path: str | PathLike[str], step: Any) -> Observation:
        self.inspection_count += 1
        self.last_raw_responses = []
        t0 = time.perf_counter()
        try:
            observation = self._inspect(Path(image_path), resolve_step(self.config, step))
        except Exception as exc:  # noqa: BLE001 - last line of defence
            self._finish("error", f"{type(exc).__name__}: {exc}")
            observation = failure_observation("error", self.last_detail)
        self.last_latency_s = time.perf_counter() - t0
        return observation

    def _inspect(self, path: Path, step) -> Observation:
        if not path.is_file():
            self._finish("error", f"image not found: {path}")
            return failure_observation("error", self.last_detail)

        message = {"role": "user", "content": build_prompt(self.config, step),
                   "images": [self._image_payload(path)]}
        schema = build_schema(self.config)
        kind, detail = "error", ""
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.chat(model=self.model, messages=[message], format=schema,
                                            options=self._options, keep_alive=self.keep_alive)
                content = _message_content(response)
                if isinstance(content, str):
                    self.last_raw_responses.append(content)
                observation = self._to_observation(parse_reply(content), step)
            except ReplyFormatError as exc:
                kind, detail = "parse_failure", str(exc)
            except Exception as exc:  # noqa: BLE001 - connection, timeout, OOM, ...
                kind, detail = "error", f"{type(exc).__name__}: {exc}"
            else:
                self._finish("ok" if attempt == 0 else "retried_ok", "")
                return observation
        self._finish(kind, detail)
        return failure_observation(kind, detail)

    def _to_observation(self, reply: _ModelReply, step) -> Observation:
        known = {p.name for p in self.config.parts}
        unknown = set(reply.parts_detected) - known
        if unknown:
            raise ReplyFormatError(f"unknown part names: {sorted(unknown)}")
        detected = set(reply.parts_detected)
        decoys = {p.name for p in self.config.parts if p.is_decoy}
        missing = sorted(set(self.config.expected_parts(step.id)) - detected) \
            if reply.certainty == "clear" else []
        return Observation(
            parts_detected=sorted(detected),
            missing_parts=missing,
            wrong_parts=sorted(detected & decoys),
            confidence=CERTAINTY_TO_CONFIDENCE[reply.certainty],
            rationale=(reply.rationale.strip() or "No rationale given.")[:300],
        )

    def _image_payload(self, path: Path) -> str | bytes:
        if not self.max_image_side:
            return str(path)
        from PIL import Image
        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((self.max_image_side, self.max_image_side))
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
        return buffer.getvalue()

    def _finish(self, status: str, detail: str) -> None:
        self.last_status, self.last_detail = status, detail


def make_default_inspector(config: AssemblyConfig, **kwargs: Any) -> TimeoutInspector:
    """Production inspector: Gemma wrapped in the config's AI timeout (ai_timeout_s)."""
    gemma = GemmaInspector(config, request_timeout_s=config.ai_timeout_s, **kwargs)
    return TimeoutInspector(gemma, config.ai_timeout_s)
