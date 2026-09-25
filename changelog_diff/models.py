"""Modelo de dependencia y carga del JSON de entrada."""
from __future__ import annotations

import json
from typing import Any

from .version import version_sort_key

OUTDATED_CODES = {"patch", "minor", "major", "prerelease"}


class Dependency:
    __slots__ = ("coordinate", "group_id", "artifact_id", "version_used",
                 "latest_stable", "dep_type", "url", "status_code", "alias")

    def __init__(self, coordinate: str, data: dict[str, Any]):
        self.coordinate = coordinate
        group, _, artifact = coordinate.partition(":")
        self.group_id = group
        self.artifact_id = artifact or group
        self.version_used = str(data.get("version_used") or "").strip()
        self.latest_stable = str(
            data.get("latest_stable") or data.get("latest_version") or ""
        ).strip()
        self.dep_type = data.get("type") or "maven"
        self.url = data.get("url") or ""
        self.status_code = data.get("status_code") or "unknown"
        self.alias = data.get("alias") or None

    @property
    def usable(self) -> bool:
        return bool(
            self.version_used and self.latest_stable
            and self.version_used not in ("N/A", "")
            and self.latest_stable not in ("N/A", "")
            and version_sort_key(self.version_used)
            < version_sort_key(self.latest_stable)
        )


def load_dependencies(path: str, include_all: bool) -> list[Dependency]:
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    deps = []
    for coordinate, data in raw.items():
        if not isinstance(data, dict):
            continue
        dep = Dependency(coordinate, data)
        if not include_all and dep.status_code not in OUTDATED_CODES:
            continue
        if not dep.usable:
            continue
        deps.append(dep)
    return deps
