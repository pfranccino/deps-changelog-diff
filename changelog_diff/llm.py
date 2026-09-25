"""Optional LLM layer: summaries and structured intelligence via Anthropic."""
from __future__ import annotations

from typing import Any, Literal

import anthropic
from pydantic import BaseModel

from . import log


# ---- Structured output schemas -----------------------------------------------

class SummaryResult(BaseModel):
    breaking_changes: list[str]
    deprecations: list[str]
    new_features: list[str]
    security_fixes: list[str]
    migration_effort: Literal["low", "medium", "high"]
    migration_notes: str
    tldr: str


class Change(BaseModel):
    kind: Literal[
        "breaking", "removal", "deprecation", "requirement",
        "security", "feature", "behavior",
    ]
    summary: str
    apis: list[str]
    replacement: str | None
    version: str | None


class Seeds(BaseModel):
    packages: list[str]
    types: list[str]
    functions: list[str]


class IntelResult(BaseModel):
    changes: list[Change]
    seeds: Seeds
    effort: Literal["low", "medium", "high"]


# ---- Prompts (business rules only; formatting handled by structured output) --

SUMMARY_PROMPT = """You are a senior Android engineer reviewing dependency changes.
You are given the official release notes between the version a project currently uses
and the latest stable version. Summarize in English without inventing anything not in the notes.
If a category does not apply, leave its list empty.

Dependency: {coordinate}
From version {from_v} to {to_v}

Official release notes:
---
{notes}
---"""

INTEL_PROMPT = """You are a senior Android engineer. You are given the official release notes
of a dependency between the version in use and the latest stable version. Extract change
intelligence so another tool can search for impact in the code. Do NOT invent anything
not in the notes.

Rules: prioritize breaking/removal/deprecation/requirement. In 'apis' put ONLY the
AFFECTED symbols (the old ones to search/change), NOT the replacement. In 'replacement'
put the new symbol only if the notes indicate it (e.g. 'deprecated X, use Y' -> apis:[X],
replacement:Y). In 'seeds' put only real code symbols (packages, classes, functions),
no constants or noise.

Dependency: {coordinate}   ({from_v} -> {to_v})

Notes:
---
{notes}
---"""


# ---- API calls ---------------------------------------------------------------

def summarize_with_llm(
    coordinate: str, from_v: str, to_v: str, notes: str,
    model: str, api_key: str, timeout: int = 60,
) -> dict[str, Any] | None:
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=1024,
            output_format=SummaryResult,
            messages=[{
                "role": "user",
                "content": SUMMARY_PROMPT.format(
                    coordinate=coordinate, from_v=from_v, to_v=to_v,
                    notes=notes[:60000],
                ),
            }],
        )
    except anthropic.APIError as exc:
        log(f"   ⚠️  LLM call failed: {exc}")
        return None
    if response.parsed_output is None:
        log("   ⚠️  LLM returned no structured output")
        return None
    return response.parsed_output.model_dump()


def enrich_intel_with_llm(
    coordinate: str, from_v: str, to_v: str, notes: str,
    model: str, api_key: str, timeout: int = 60,
) -> dict | None:
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=2048,
            output_format=IntelResult,
            messages=[{
                "role": "user",
                "content": INTEL_PROMPT.format(
                    coordinate=coordinate, from_v=from_v, to_v=to_v,
                    notes=notes[:60000],
                ),
            }],
        )
    except anthropic.APIError as exc:
        log(f"   ⚠️  LLM call failed: {exc}")
        return None
    if response.parsed_output is None:
        log("   ⚠️  LLM returned no structured output")
        return None
    return response.parsed_output.model_dump()
