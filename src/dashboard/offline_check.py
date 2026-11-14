"""Offline self-test for the dashboard: is this really a single-device, offline deployment?

Three checks, none of which downloads anything:
1. The model endpoint is on this machine (localhost / 127.0.0.1 / ::1).
2. The model is already on disk (Ollama's /api/tags lists it; nothing is pulled).
3. The internet is NOT reachable: one TCP connect attempt to a public DNS server
   (1.1.1.1:53, 1 s timeout). No data is sent; it only tests for a route.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlparse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_ENDPOINT = "http://localhost:11434"
DEFAULT_MODEL = "gemma3:4b"


@dataclass
class OfflineReport:
    endpoint_local: bool
    model_on_disk: bool
    internet_reachable: bool
    details: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if not (self.endpoint_local and self.model_on_disk):
            return "AI NOT READY"
        return "OFFLINE VERIFIED" if not self.internet_reachable else "LOCAL AI - NETWORK STILL UP"


def _internet_reachable(probe=("1.1.1.1", 53), timeout=1.0, connect=socket.create_connection) -> bool:
    try:
        connect(probe, timeout=timeout).close()
        return True
    except OSError:
        return False


def _model_listed(endpoint: str, model: str, opener=urllib.request.urlopen) -> bool:
    try:
        with opener(f"{endpoint.rstrip('/')}/api/tags", timeout=2) as response:
            names = [m.get("name", "") for m in json.load(response).get("models", [])]
    except (OSError, ValueError):
        return False
    return model in names or f"{model}:latest" in names


def run_check(endpoint: str | None = None, model: str | None = None, *, connect=socket.create_connection,
              opener=urllib.request.urlopen) -> OfflineReport:
    endpoint = endpoint or os.environ.get("HOLOGUIDE_MODEL_ENDPOINT") or DEFAULT_ENDPOINT
    model = model or os.environ.get("HOLOGUIDE_MODEL_NAME") or DEFAULT_MODEL
    host = urlparse(endpoint).hostname or ""
    local = host in LOCAL_HOSTS
    listed = local and _model_listed(endpoint, model, opener)   # never query a remote endpoint
    online = _internet_reachable(connect=connect)
    details = [
        f"Model endpoint {endpoint}: {'on this machine' if local else 'NOT local'}",
        f"Model {model}: {'already on disk' if listed else 'not found (nothing will be downloaded)'}",
        f"Internet: {'reachable (inference still stays on localhost)' if online else 'unreachable'}",
    ]
    return OfflineReport(local, listed, online, details)
