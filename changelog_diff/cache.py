"""Caché por (coordenada, rango de versión)."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from typing import Any

from .models import Dependency


class Cache:
    """Los changelogs son inmutables por versión: se cachea el bloque de notas ya montado."""

    def __init__(self, directory: str | None, opts_key: str = ""):
        self.directory = directory
        self.opts_key = opts_key
        if directory:
            os.makedirs(directory, exist_ok=True)

    def _path(self, dep: Dependency) -> str | None:
        if not self.directory:
            return None
        safe = re.sub(
            r"[^\w.\-]", "_",
            f"{dep.coordinate}@{dep.version_used}..{dep.latest_stable}",
        )
        if self.opts_key:
            fingerprint = hashlib.sha1(self.opts_key.encode()).hexdigest()[:8]
            return os.path.join(self.directory, f"{safe}.{fingerprint}.json")
        return os.path.join(self.directory, f"{safe}.json")

    def get(self, dep: Dependency) -> dict[str, Any] | None:
        path = self._path(dep)
        if path and os.path.exists(path):
            with contextlib.suppress(Exception):
                with open(path, "r", encoding="utf-8") as handle:
                    return json.load(handle)
        return None

    def put(self, dep: Dependency, payload: dict[str, Any]) -> None:
        path = self._path(dep)
        if not path:
            return
        with contextlib.suppress(Exception):
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
