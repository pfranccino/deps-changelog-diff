"""Optional LLM layer: summaries and structured intelligence via Anthropic."""
from __future__ import annotations

import json
import os
from typing import Any, Literal

import anthropic
from pydantic import BaseModel

from . import log


# ---- Client factory ----------------------------------------------------------

def _load_claude_env() -> dict[str, str]:
    """Read the env block from Claude Code settings files.

    settings.local.json overrides settings.json (local wins).
    """
    env: dict[str, str] = {}
    for path in (
        os.path.expanduser("~/.claude/settings.json"),
        os.path.expanduser("~/.claude/settings.local.json"),
    ):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.loads(f.read())
            if not isinstance(data, dict):
                continue
            raw = data.get("env")
            if not isinstance(raw, dict):
                continue
            for k, v in raw.items():
                if isinstance(v, str) and v:
                    env[k] = v
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return env


def _resolve(cli_val: str | None, *env_keys: str,
             env: dict[str, str] | None = None) -> str | None:
    """Return cli_val if set, else first matching env key, else None."""
    if cli_val:
        return cli_val
    for key in env_keys:
        val = os.environ.get(key)
        if val:
            return val
        if env and env.get(key):
            return env[key]
    return None


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
        claude_env = _load_claude_env()

        aws_region = _resolve(aws_region, "AWS_REGION",
                              "AWS_DEFAULT_REGION", env=claude_env)
        aws_profile = _resolve(aws_profile, "AWS_PROFILE", env=claude_env)
        aws_access_key = _resolve(None, "AWS_ACCESS_KEY_ID", env=claude_env)
        aws_secret_key = _resolve(None, "AWS_SECRET_ACCESS_KEY", env=claude_env)
        aws_session_token = _resolve(None, "AWS_SESSION_TOKEN", env=claude_env)

        if not aws_region:
            try:
                import boto3
                session = boto3.Session(profile_name=aws_profile)
                aws_region = session.region_name
            except Exception:
                pass

        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "aws_region": aws_region or "us-east-1",
        }
        if aws_profile:
            kwargs["aws_profile"] = aws_profile
        if aws_access_key:
            kwargs["aws_access_key"] = aws_access_key
        if aws_secret_key:
            kwargs["aws_secret_key"] = aws_secret_key
        if aws_session_token:
            kwargs["aws_session_token"] = aws_session_token
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
