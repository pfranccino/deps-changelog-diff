"""Parsing, comparación y rangos de versiones semánticas."""
from __future__ import annotations

import re
from typing import Iterable

_NUM_RE = re.compile(r"\d+")
_VERSION_TOKEN_RE = re.compile(r"\d+(?:\.\d+)+(?:[.\-_][0-9A-Za-z]+)*")
_PRERELEASE_RE = re.compile(
    r"(?i)(?:^|[-._+])(alpha|beta|rc|cr|milestone|m\d+|snapshot|preview|eap|dev|pre|ea)"
    r"(?![a-z])"
)

ALPHA, BETA, RC, STABLE = 0, 1, 2, 3
_QUALIFIERS = (
    ("alpha", ALPHA), ("beta", BETA), ("milestone", BETA), ("preview", BETA),
    ("snapshot", ALPHA), ("eap", BETA), ("dev", ALPHA), ("rc", RC), ("cr", RC),
    ("pre", ALPHA), ("ea", ALPHA), ("m", BETA),
)
_REL_RE = re.compile(r"\s*v?(\d+(?:\.\d+)*)(.*)$")


def version_tuple(version: str) -> tuple[int, ...]:
    match = _REL_RE.match(version.strip())
    if not match:
        return (0,)
    return tuple(int(n) for n in match.group(1).split("."))


def version_sort_key(version: str) -> tuple[tuple[int, ...], int, int]:
    match = _REL_RE.match(version.strip())
    if not match:
        return ((0,), STABLE, 0)
    rel = tuple(int(n) for n in match.group(1).split("."))
    rest = match.group(2).lower()
    rank, num = STABLE, 0
    best_idx = len(rest) + 1
    for qualifier, qrank in _QUALIFIERS:
        pat = re.compile(r"(?:^|[-._+])" + re.escape(qualifier) + r"(?![a-z])")
        m = pat.search(rest)
        if m:
            idx = m.start()
            if idx < best_idx:
                best_idx = idx
                rank = qrank
                after = rest[m.end():]
                num_match = re.search(r"\d+", after)
                num = int(num_match.group()) if num_match else 0
    return (rel, rank, num)


def is_prerelease(version: str) -> bool:
    return bool(_PRERELEASE_RE.search(version))


def in_range(version: str, current: str, target: str,
             include_prereleases: bool = False) -> bool:
    if not include_prereleases and is_prerelease(version):
        return False
    key = version_sort_key(version)
    return version_sort_key(current) < key <= version_sort_key(target)


def extract_version_from_tag(tag: str) -> str | None:
    if not tag:
        return None
    match = _VERSION_TOKEN_RE.search(tag)
    return match.group(0) if match else None


def versions_in_range(
    candidates: Iterable[str],
    current: str,
    target: str,
    include_prereleases: bool = False,
) -> list[str]:
    seen: dict[tuple, str] = {}
    for raw in candidates:
        v = raw.strip()
        if not v or not in_range(v, current, target, include_prereleases):
            continue
        key = version_sort_key(v)
        if key not in seen or len(v) > len(seen[key]):
            seen[key] = v
    return [seen[k] for k in sorted(seen, reverse=True)]
