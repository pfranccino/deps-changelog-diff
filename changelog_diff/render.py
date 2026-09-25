"""Markdown rendering of results."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def render_markdown(results: list[dict[str, Any]]) -> str:
    lines = ["# Dependency version changes", ""]
    lines.append(
        f"_Generated: {datetime.now().isoformat(timespec='seconds')}_  "
    )
    lines.append(f"_{len(results)} dependencies analyzed_")
    lines.append("")
    for r in results:
        lines.append(f"## {r['coordinate']}  ·  {r['from']} → {r['to']}")
        src = (
            f"[{r['source']}]({r['source_url']})"
            if r.get("source_url") else "(no source)"
        )
        lines.append(f"Source: {src}")
        lines.append("")
        summary = r.get("summary")
        if summary:
            lines.append(f"**TL;DR:** {summary.get('tldr', '')}")
            lines.append(
                f"**Migration effort:** "
                f"{summary.get('migration_effort', '?')}"
            )
            for label, key in (
                ("💥 Breaking", "breaking_changes"),
                ("⚠️ Deprecations", "deprecations"),
                ("✨ New", "new_features"),
                ("🔒 Security", "security_fixes"),
            ):
                items = summary.get(key) or []
                if items:
                    lines.append(f"\n**{label}:**")
                    lines.extend(f"- {it}" for it in items)
            if summary.get("migration_notes"):
                lines.append(
                    f"\n**Migration:** {summary['migration_notes']}"
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
                "_No release notes found for this range._"
            )
            lines.append("")
    return "\n".join(lines)


def render_intel_markdown(doc: dict) -> str:
    lines = ["# Dependency change intelligence", ""]
    m = doc["meta"]
    lines.append(
        f"_Generated: {m['generated_at']} · "
        f"enrichment: {m['enrichment']}_  "
    )
    t = m["totals"]
    lines.append(
        f"_{t['analyzed']} analyzed · {t['with_changes']} with changes · "
        f"{t['no_source']} without source_"
    )
    lines.append("")
    if doc["project_gates"]:
        lines.append("## 🚧 Project requirements (build)")
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
    lines.append("## Dependencies")
    for coord, e in deps:
        lines.append(
            f"### {coord} · {e['from']} → {e['to']} · {e['jump']} · "
            f"effort {e['effort']} ({e['confidence']})"
        )
        if not e.get("changes"):
            lines.append("_No changes extracted._\n")
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
                f"{c.get('summary', '(no summary)')}{rep}{apis}"
            )
        lines.append("")
    return "\n".join(lines)
