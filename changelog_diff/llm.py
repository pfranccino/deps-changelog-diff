"""Optional LLM layer: summaries and structured intelligence via Anthropic.

`anthropic` and `pydantic` are optional (extra `llm`): they are imported on first
use so the raw/heuristic paths work without them.
"""
from __future__ import annotations

import functools
import json
import os
import types
from typing import Any, Literal

from . import log

INSTALL_HINT = (
    "LLM support needs the 'llm' extra: "
    "pipx install \"deps-changelog-diff[llm] @ "
    "git+https://github.com/pfranccino/deps-changelog-diff\" "
    "(or pip install anthropic pydantic)."
)

# Bedrock inference-profile IDs (US geo), checked against the Bedrock model
# cards on 2026-10-04. Anything else is passed through as-is, so a full ID
# (e.g. global.anthropic.claude-sonnet-5) always works.
BEDROCK_MODELS = {
    "claude-haiku-4-5-20251001": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-sonnet-5": "us.anthropic.claude-sonnet-5",
    "claude-opus-5-5": "us.anthropic.claude-opus-5-5",
}
# Bedrock lists structured outputs as unsupported for these; messages.parse needs it.
_BEDROCK_NO_STRUCTURED = ("claude-sonnet-5", "claude-opus-5-5")


def bedrock_model_id(model: str) -> str:
    """Map a short Anthropic model name to its Bedrock ID (pass-through otherwise)."""
    resolved = BEDROCK_MODELS.get(model, model)
    if any(resolved.endswith(f"anthropic.{m}") for m in _BEDROCK_NO_STRUCTURED):
        log(f"   ⚠️  Bedrock does not list structured outputs for {resolved}; "
            "LLM calls may fail. claude-haiku-4-5-20251001 supports them.")
    return resolved


def _anthropic():
    import anthropic
    return anthropic


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


def _parse_custom_headers(raw: str) -> dict[str, str]:
    """Parse ANTHROPIC_CUSTOM_HEADERS ('Key: Val\\nKey2: Val2') into a dict."""
    headers: dict[str, str] = {}
    for line in raw.split("\n"):
        line = line.strip()
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    return headers


def make_client(
    provider: str = "anthropic",
    api_key: str | None = None,
    aws_region: str | None = None,
    aws_profile: str | None = None,
    timeout: int = 60,
) -> Any:
    """Return an Anthropic or AnthropicBedrock client.

    Raises ImportError (with INSTALL_HINT) when the SDK is not installed.
    """
    try:
        anthropic = _anthropic()
    except ImportError as exc:
        raise ImportError(INSTALL_HINT) from exc
    if provider == "bedrock":
        from anthropic import AnthropicBedrock
        claude_env = _load_claude_env()

        def _get(key: str) -> str | None:
            return os.environ.get(key) or claude_env.get(key) or None

        bearer = _get("AWS_BEARER_TOKEN_BEDROCK")
        aws_region = aws_region or _get("AWS_REGION") or _get("AWS_DEFAULT_REGION")
        aws_profile = aws_profile or _get("AWS_PROFILE")
        access_key = _get("AWS_ACCESS_KEY_ID")
        secret_key = _get("AWS_SECRET_ACCESS_KEY")
        session_token = _get("AWS_SESSION_TOKEN")
        base_url = _get("ANTHROPIC_BEDROCK_BASE_URL")
        custom_h = _get("ANTHROPIC_CUSTOM_HEADERS")

        if not aws_region:
            try:
                import boto3
                session = boto3.Session(profile_name=aws_profile)
                aws_region = session.region_name
            except Exception:
                pass

        kwargs: dict[str, Any] = {
            "aws_region": aws_region or "us-east-1",
            "timeout": timeout,
        }

        if custom_h:
            kwargs["default_headers"] = _parse_custom_headers(custom_h)

        if base_url:
            kwargs["base_url"] = base_url

        if bearer:
            kwargs["api_key"] = bearer
        elif base_url and not access_key:
            kwargs["api_key"] = "gateway"
        else:
            if aws_profile:
                kwargs["aws_profile"] = aws_profile
            if access_key:
                kwargs["aws_access_key"] = access_key
            if secret_key:
                kwargs["aws_secret_key"] = secret_key
            if session_token:
                kwargs["aws_session_token"] = session_token

        return AnthropicBedrock(**kwargs)
    return anthropic.Anthropic(api_key=api_key, timeout=timeout)


# ---- Structured output schemas (built lazily: pydantic is optional) ----------

@functools.lru_cache(maxsize=None)
def _schemas() -> types.SimpleNamespace:
    from pydantic import BaseModel

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

    return types.SimpleNamespace(
        SummaryResult=SummaryResult, Change=Change,
        Seeds=Seeds, IntelResult=IntelResult,
    )


def __getattr__(name: str) -> Any:
    if name in ("SummaryResult", "Change", "Seeds", "IntelResult"):
        return getattr(_schemas(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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

def _parse(
    client: Any, model: str, max_tokens: int, schema: Any, prompt: str,
) -> dict[str, Any] | None:
    anthropic = _anthropic()
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            output_format=schema,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        log(f"   ⚠️  LLM call failed: {exc}")
        return None
    except ValueError as exc:  # pydantic.ValidationError: invalid/partial JSON
        log(f"   ⚠️  LLM output did not match the schema: {exc}")
        return None
    if getattr(response, "stop_reason", None) == "max_tokens":
        log(f"   ⚠️  LLM output truncated at max_tokens={max_tokens}; "
            "result discarded.")
        return None
    if response.parsed_output is None:
        log("   ⚠️  LLM returned no structured output")
        return None
    return response.parsed_output.model_dump()


def summarize_with_llm(
    coordinate: str, from_v: str, to_v: str, notes: str,
    model: str, client: Any,
) -> dict[str, Any] | None:
    return _parse(
        client, model, 1024, _schemas().SummaryResult,
        SUMMARY_PROMPT.format(
            coordinate=coordinate, from_v=from_v, to_v=to_v,
            notes=notes[:60000],
        ),
    )


def enrich_intel_with_llm(
    coordinate: str, from_v: str, to_v: str, notes: str,
    model: str, client: Any,
) -> dict | None:
    return _parse(
        client, model, 8192, _schemas().IntelResult,
        INTEL_PROMPT.format(
            coordinate=coordinate, from_v=from_v, to_v=to_v,
            notes=notes[:60000],
        ),
    )
