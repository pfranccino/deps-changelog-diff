"""Analyze what changed between dependency versions using the owner's docs."""
from __future__ import annotations

import contextlib
import sys

__version__ = "0.2.0"

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_QUIET = False


def log(message: str) -> None:
    if not _QUIET:
        print(message, file=sys.stderr, flush=True)
