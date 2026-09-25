"""Renderizado a Markdown de los resultados."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def render_markdown(results: list[dict[str, Any]]) -> str:
    lines = ["# Cambios entre versiones de dependencias", ""]
    lines.append(
        f"_Generado: {datetime.now().isoformat(timespec='seconds')}_  "
    )
    lines.append(f"_{len(results)} dependencias analizadas_")
    lines.append("")
    for r in results:
        lines.append(f"## {r['coordinate']}  ·  {r['from']} → {r['to']}")
        src = (
            f"[{r['source']}]({r['source_url']})"
            if r.get("source_url") else "(sin fuente)"
        )
        lines.append(f"Fuente: {src}")
        lines.append("")
        summary = r.get("summary")
        if summary:
            lines.append(f"**TL;DR:** {summary.get('tldr', '')}")
            lines.append(
                f"**Esfuerzo de migración:** "
                f"{summary.get('migration_effort', '?')}"
            )
            for label, key in (
                ("💥 Breaking", "breaking_changes"),
                ("⚠️ Deprecaciones", "deprecations"),
                ("✨ Nuevo", "new_features"),
                ("🔒 Seguridad", "security_fixes"),
            ):
                items = summary.get(key) or []
                if items:
                    lines.append(f"\n**{label}:**")
                    lines.extend(f"- {it}" for it in items)
            if summary.get("migration_notes"):
                lines.append(
                    f"\n**Migración:** {summary['migration_notes']}"
                )
            lines.append("")
        elif r.get("versions"):
            for n in r["versions"]:
                lines.append(
                    f"### {n['version']} ({n.get('date') or 's/f'})"
                )
                lines.append(n["notes"][:4000])
                lines.append("")
        else:
            lines.append(
                "_No se encontraron notas de versión para este rango._"
            )
            lines.append("")
    return "\n".join(lines)


def render_intel_markdown(doc: dict) -> str:
    lines = ["# Inteligencia de cambios de dependencias", ""]
    m = doc["meta"]
    lines.append(
        f"_Generado: {m['generated_at']} · "
        f"enriquecido: {m['enrichment']}_  "
    )
    t = m["totals"]
    lines.append(
        f"_{t['analyzed']} analizadas · {t['with_changes']} con cambios · "
        f"{t['no_source']} sin fuente_"
    )
    lines.append("")
    if doc["project_gates"]:
        lines.append("## 🚧 Requisitos de proyecto (build)")
        for g in doc["project_gates"]:
            lines.append(
                f"- **{g['requirement']}** — por: {', '.join(g['from'])}"
            )
        lines.append("")
    order = {"high": 0, "medium": 1, "low": 2, "unknown": 3}
    deps = sorted(
        doc["dependencies"].items(),
        key=lambda kv: (order.get(kv[1].get("effort"), 4), kv[0]),
    )
    lines.append("## Dependencias")
    for coord, e in deps:
        lines.append(
            f"### {coord} · {e['from']} → {e['to']} · {e['jump']} · "
            f"esfuerzo {e['effort']} ({e['confidence']})"
        )
        if not e.get("changes"):
            lines.append("_Sin cambios extraídos._\n")
            continue
        for c in e["changes"][:12]:
            rep = (
                f"  → **{c.get('replacement')}**"
                if c.get("replacement") else ""
            )
            apis_list = c.get("apis") or []
            apis = (
                f"  `{'`, `'.join(str(a) for a in apis_list)}`"
                if apis_list else ""
            )
            lines.append(
                f"- [{c.get('kind', 'behavior')}] "
                f"{c.get('summary', '(sin resumen)')}{rep}{apis}"
            )
        lines.append("")
    return "\n".join(lines)
