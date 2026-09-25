"""Capa LLM opcional: resúmenes e inteligencia estructurada vía Anthropic."""
from __future__ import annotations

import json
import re
from typing import Any

import requests

from . import log

SUMMARY_PROMPT = """Eres un ingeniero Android senior revisando cambios de dependencias.
Te doy las notas de versión oficiales entre la versión que un proyecto usa y la última
estable. Resume EN ESPAÑOL, sin inventar nada que no esté en las notas.

Dependencia: {coordinate}
De la versión {from_v} a la {to_v}

Notas oficiales:
---
{notes}
---

Responde SOLO con un objeto JSON con esta forma exacta:
{{
  "breaking_changes": ["..."],
  "deprecations": ["..."],
  "new_features": ["..."],
  "security_fixes": ["..."],
  "migration_effort": "low|medium|high",
  "migration_notes": "1-3 frases con lo que habría que tocar",
  "tldr": "una frase"
}}
Si una categoría no aplica, deja la lista vacía. No agregues texto fuera del JSON."""


INTEL_PROMPT = """Eres un ingeniero Android senior. Te doy las notas de versión oficiales de
una dependencia entre la versión en uso y la última estable. Extrae inteligencia de cambios
para que otra herramienta busque el impacto en el código. NO inventes nada que no esté en las notas.

Dependencia: {coordinate}   ({from_v} -> {to_v})

Notas:
---
{notes}
---

Responde SOLO con un objeto JSON con esta forma exacta (en español los textos):
{{
  "changes": [
    {{
      "kind": "breaking|removal|deprecation|requirement|security|feature|behavior",
      "summary": "una frase clara del cambio",
      "apis": ["identificadores de API/clases/funciones mencionados"],
      "replacement": "API nueva que reemplaza a la vieja, o null",
      "version": "X.Y.Z donde ocurrió"
    }}
  ],
  "seeds": {{ "packages": [], "types": [], "functions": [] }},
  "effort": "low|medium|high"
}}
Reglas: prioriza breaking/removal/deprecation/requirement. En 'apis' pon SOLO los símbolos
AFECTADOS (los viejos que hay que buscar/cambiar), NO el reemplazo. En 'replacement' pon el
símbolo nuevo solo si las notas lo indican (ej. 'deprecated X, use Y' -> apis:[X], replacement:Y).
En 'seeds' pon solo símbolos reales de código (paquetes, clases, funciones), sin constantes ni ruido.
No agregues texto fuera del JSON."""


def _call_anthropic(payload: dict, api_key: str,
                    timeout: int = 60) -> str | None:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            json=payload, headers=headers, timeout=timeout,
        )
    except requests.RequestException as exc:
        log(f"   ⚠️  fallo llamando al LLM: {exc}")
        return None
    if resp.status_code != 200:
        log(f"   ⚠️  LLM devolvió {resp.status_code}: {resp.text[:200]}")
        return None
    return "".join(
        b.get("text", "") for b in resp.json().get("content", [])
    )


def _parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(match.group(0)) if match else None
    except Exception as exc:
        log(f"   ⚠️  no pude parsear la respuesta del LLM: {exc}")
        return None


def summarize_with_llm(
    coordinate: str, from_v: str, to_v: str, notes: str,
    model: str, api_key: str, timeout: int = 60,
) -> dict[str, Any] | None:
    payload = {
        "model": model,
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": SUMMARY_PROMPT.format(
                coordinate=coordinate, from_v=from_v, to_v=to_v,
                notes=notes[:60000],
            ),
        }],
    }
    return _parse_json(_call_anthropic(payload, api_key, timeout))


def enrich_intel_with_llm(
    coordinate: str, from_v: str, to_v: str, notes: str,
    model: str, api_key: str, timeout: int = 60,
) -> dict | None:
    payload = {
        "model": model,
        "max_tokens": 2048,
        "messages": [{
            "role": "user",
            "content": INTEL_PROMPT.format(
                coordinate=coordinate, from_v=from_v, to_v=to_v,
                notes=notes[:60000],
            ),
        }],
    }
    return _parse_json(_call_anthropic(payload, api_key, timeout))
