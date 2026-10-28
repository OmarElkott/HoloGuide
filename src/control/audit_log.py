"""Lightweight append-only JSON Lines audit logging."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Mapping

from pydantic import BaseModel


class AuditLogError(RuntimeError):
    """Raised when an audit event cannot be written or read reliably."""


class AuditLog:
    """Append and read ordered JSONL audit events without rewriting history."""

    def __init__(self, path: str | Path = "outputs/audit.jsonl") -> None:
        self.path = Path(path)
        self._lock = Lock()

    def record(self, event_type: str, **fields: Any) -> None:
        """Append one event, raising :class:`AuditLogError` on I/O failure."""
        if not event_type or not event_type.strip():
            raise ValueError("event_type must be a non-empty string")
        reserved = {"timestamp", "event_type"} & fields.keys()
        if reserved:
            names = ", ".join(sorted(reserved))
            raise ValueError(f"reserved audit fields cannot be overridden: {names}")

        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type.strip(),
            **fields,
        }
        try:
            line = json.dumps(
                _json_value(event),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="") as stream:
                    written = stream.write(line + "\n")
                    stream.flush()
                if written != len(line) + 1:
                    raise OSError("incomplete audit event write")
        except (OSError, TypeError, ValueError) as exc:
            raise AuditLogError(
                f"failed to append audit event '{event_type}' to {self.path}: {exc}"
            ) from exc

    def read_all(self) -> list[dict[str, Any]]:
        """Read events in append order; nonexistent and empty logs return ``[]``."""
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            with self._lock:
                with self.path.open("r", encoding="utf-8") as stream:
                    for line_number, line in enumerate(stream, start=1):
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError(f"line {line_number} is not a JSON object")
                        events.append(event)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise AuditLogError(f"failed to read audit log {self.path}: {exc}") from exc
        return events


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("audit timestamps must be timezone-aware")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    return value
