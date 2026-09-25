"""Optional LLM layer: summaries and structured intelligence via Anthropic."""
from __future__ import annotations

import json
import os
from typing import Any, Literal

import anthropic
from pydantic import BaseModel

from . import log


# ---- Client factory ----------------------------------------------------------

_CLAUDE_SETTINGS_PATHS = [
    os.path.expanduser("~/.claude/settings.json"),
    os.path.expanduser("~/.claude/settings.local.json"),
]


def _load_claude_env() -> dict[str, str]:
    """Read the env block from Claude Code settings.json files."""
    env: dict[str, str] = {}
    for path in _CLAUDE_SETTINGS_PATHS:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.loads(f.read())
            for k, v in (data.get("env") or {}).items():
                if isinstance(v, str) and v:
                    env.setdefault(k, v)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return env


def _inject_claude_bedrock_env() -> None:
    """Inject Claude Code Bedrock env vars into os.environ if not already set."""
    claude_env = _load_claude_env()
    for key in (
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
        "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE",
    ):
        if key not in os.environ and key in claude_env:
            os.environ[key] = claude_env[key]


def make_client(
    provider: str = "anthropic",
    api_key: str | None = None,
    aws_region: str | None = None,
    aws_profile: str | None = None,
    timeout: int = 60,
) -> anthropic.Anthropic:
    """Return an Anthropic or AnthropicBedrock client."""
    if provider == "bedrock":
        from anthropic import AnthropicBedrock
        _inject_claude_bedrock_env()
        if not aws_region:
            aws_region = (
                os.environ.get("AWS_REGION")
                or os.environ.get("AWS_DEFAULT_REGION")
            )
        if not aws_region:
            try:
                import boto3
                session = boto3.Session(profile_name=aws_profile)
                aws_region = session.region_name
            except Exception:
                pass
        if not aws_profile:
            aws_profile = os.environ.get("AWS_PROFILE")
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "aws_region": aws_region or "us-east-1",
        }
        if aws_profile:
            kwargs["aws_profile"] = aws_profile
        return AnthropicBedrock(**kwargs)
    return anthropic.Anthropic(api_key=api_key, timeout=timeout)


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
    model: str, client: anthropic.Anthropic,
) -> dict[str, Any] | None:
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
    model: str, client: anthropic.Anthropic,
) -> dict | None:
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
