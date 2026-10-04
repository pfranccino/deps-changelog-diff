"""Change intelligence: seeds, heuristics, gates, LLM normalization."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from . import log
from .llm import enrich_intel_with_llm
from .models import Dependency
from .version import version_tuple, version_sort_key

# ---- Symbols and classification -----------------------------------------------

_BACKTICK_RE = re.compile(r"`([A-Za-z][\w.]*(?:\(\))?)`")
_PKG_ROOTS = (
    "com|org|io|androidx|net|dev|kotlin|kotlinx|okhttp3|okio|retrofit2"
    "|coil|coil3|dagger|javax|java|app|me|co"
)
# developer.android.com serves Kotlin docs under /reference/kotlin/<package>/.
_REF_PKG_RE = re.compile(
    rf"/reference/(?:kotlin/(?=(?:{_PKG_ROOTS})/))?"
    rf"((?:{_PKG_ROOTS})(?:/[a-z0-9_]+)*)/[A-Z]"
)
# "deprecated `X`; use `Y` instead" -> Y is the replacement, not an affected API.
_REPLACEMENT_RE = re.compile(
    r"(?i)(?:\buse|\breplaced\s+(?:by|with)|\bin\s+favou?r\s+of|\bmigrate\s+to)"
    r"\s+(?:the\s+(?:new\s+)?)?\[?`([A-Za-z][\w.]*(?:\(\))?)`"
)
_CONST_RE = re.compile(r"^[A-Z0-9_]{3,}$")
_NOISE_SEEDS = {
    "minSdk", "minSdkVersion", "compileSdk", "compileSdkVersion",
    "targetSdk", "targetSdkVersion", "N/A", "true", "false", "null",
}

_KIND_RULES = [
    ("requirement", re.compile(
        r"(?i)requires?\s+(?:java|jdk|android|min\s?sdk)"
        r"|min\s?sdk(?:version)?\b|\b(?:java|jdk)\s?1[0-9]\b"
    )),
    ("removal", re.compile(
        r"(?i)\bremov(?:ed|al|es)\b|\bdeleted\b|\bdropp?ed\b|\bno longer\b"
    )),
    ("deprecation", re.compile(r"(?i)\bdeprecat")),
    ("security", re.compile(r"(?i)\bsecurit|\bCVE-\d|\bvulnerab")),
    ("breaking", re.compile(
        r"(?i)breaking change|incompatible|\bbreaking\b"
    )),
    ("feature", re.compile(
        r"(?i)\badded\b|\bnew\b|\bintroduc|\bsupport (?:for|de)\b"
    )),
]


def classify_kind(text: str) -> str:
    for kind, rx in _KIND_RULES:
        if rx.search(text):
            return kind
    return "behavior"


def _split_member(t: str) -> dict[str, str] | None:
    """`Owner.Type.member` -> {"owner": "Owner.Type", "name": "member"}."""
    segs = t.split(".")
    if len(segs) < 2 or not segs[-1][:1].islower():
        return None
    if not any(s[:1].isupper() for s in segs[:-1]):
        return None
    return {"owner": ".".join(segs[:-1]), "name": segs[-1]}


def _classify_token(t: str) -> str:
    if "." in t:
        segs = t.split(".")
        if all(
            s and s[0].islower() and not any(c.isupper() for c in s)
            for s in segs
        ):
            return "packages"
        if _split_member(t):
            return "members"
        return "types"
    return "types" if t[:1].isupper() else "functions"


def _new_buckets() -> dict[str, set]:
    return {"packages": set(), "types": set(), "functions": set(), "members": set()}


def _add_token(buckets: dict[str, set], t: str) -> None:
    kind = _classify_token(t)
    if kind == "members":
        m = _split_member(t)
        buckets["members"].add((m["owner"], m["name"]))
        buckets["functions"].add(m["name"])  # flat list kept for compatibility
    else:
        buckets[kind].add(t)


def _finish_buckets(buckets: dict[str, set]) -> dict[str, list]:
    out: dict[str, list] = {
        k: sorted(v) for k, v in buckets.items() if k != "members"
    }
    out["members"] = [
        {"owner": o, "name": n} for o, n in sorted(buckets["members"])
    ]
    return out


def extract_seeds(notes_text: str) -> dict[str, list]:
    buckets = _new_buckets()
    for m in _REF_PKG_RE.finditer(notes_text):
        buckets["packages"].add(m.group(1).replace("/", "."))
    for tok in _BACKTICK_RE.findall(notes_text):
        t = tok[:-2] if tok.endswith("()") else tok
        if not t or t in _NOISE_SEEDS or _CONST_RE.match(t):
            continue
        if len(t) < 3 and "." not in t:
            continue
        _add_token(buckets, t)
    return _finish_buckets(buckets)


# ---- Heuristic changes ------------------------------------------------------

def _split_lines(notes: str) -> list[str]:
    parts = re.split(r"\n|(?<=[.\)])\s+(?=[A-Z*\-•])", notes)
    return [p.strip(" *-•\t") for p in parts]


MAX_CHANGES = 40
_KIND_PRIORITY = {
    k: i for i, k in enumerate(
        ("removal", "breaking", "requirement", "deprecation", "security")
    )
}
_REPLACEABLE_KINDS = {"removal", "breaking", "deprecation"}


def _strip_parens(t: str) -> str:
    return t[:-2] if t.endswith("()") else t


def heuristic_changes(
    versions: list[dict], max_items: int | None = MAX_CHANGES,
) -> list[dict]:
    """Changes ordered by priority (removal > breaking > ... > rest), newest
    first within a kind, so the cut never drops an old removal for a new feature."""
    out: list[dict] = []
    for v in versions:
        for line in _split_lines(v.get("notes", "")):
            if len(line) < 12:
                continue
            if not any(rx.search(line) for _, rx in _KIND_RULES):
                continue
            kind = classify_kind(line)
            replacement = None
            if kind in _REPLACEABLE_KINDS:
                rm = _REPLACEMENT_RE.search(line)
                if rm:
                    replacement = _strip_parens(rm.group(1))
            apis = [
                _strip_parens(t) for t in _BACKTICK_RE.findall(line)
                if not _CONST_RE.match(t)
            ]
            apis = [a for a in apis if a != replacement]
            summary = re.sub(
                r"\s+", " ", re.sub(r"\]\([^)]+\)", "]", line)
            ).strip()
            out.append({
                "kind": kind,
                "summary": summary[:240],
                "apis": apis[:6],
                "replacement": replacement,
                "version": v.get("version"),
                "ref": v.get("url"),
            })
    out.sort(key=lambda c: _KIND_PRIORITY.get(c["kind"], len(_KIND_PRIORITY)))
    return out if max_items is None else out[:max_items]


# ---- Version jump / effort / confidence -------------------------------------

def version_jump(frm: str, to: str) -> str:
    a, b = version_tuple(frm), version_tuple(to)
    if not a or not b:
        return "unknown"
    if b[0] != a[0]:
        return "major"
    if len(a) > 1 and len(b) > 1 and b[1] != a[1]:
        return "minor"
    return "patch"


def guess_effort(jump: str, changes: list[dict]) -> str:
    hard = any(
        c["kind"] in ("breaking", "removal", "requirement") for c in changes
    )
    if jump == "major":
        return "high" if hard else "medium"
    if jump == "minor":
        return "medium" if hard else "low"
    return "low"


def guess_confidence(
    source: str | None, versions: list[dict],
) -> tuple[str, str | None]:
    if not versions:
        return "none", "no public per-version changelog"
    if (source == "github" and len(versions) == 1
            and len(versions[0].get("notes", "")) > 4000):
        return "medium", (
            "extractor collapsed into a single note; "
            "review per-version mapping"
        )
    if source in ("github", "androidx", "known"):
        return "high", None
    return "medium", None


# ---- Gates -------------------------------------------------------------------

_JAVA_GATE_RE = re.compile(
    r"(?i)(?:requires?|minimum|needs|now requires?)\s+"
    r"(?:java|jdk)\s*(?:version\s*)?(\d{1,2})"
    r"|(?:java|jdk)\s*(\d{1,2})\s*"
    r"(?:or (?:higher|newer|above)|required|is required|as the minimum)"
)
_MINSDK_GATE_RE = re.compile(
    r"(?i)min\s?sdk(?:version)?\s*(?:is now|now|>=|:|to|=|is)?\s*(\d{2})"
)


def extract_gates(intel: dict[str, dict]) -> list[dict]:
    gates: dict[str, set] = {}
    for coord, dep in intel.items():
        text = " ".join(
            c.get("summary", "") for c in dep.get("changes", [])
        )
        origin = f"{coord} {dep.get('to', '?')}"
        for m in _JAVA_GATE_RE.finditer(text):
            ver = m.group(1) or m.group(2)
            if ver:
                gates.setdefault(f"Java {ver}+", set()).add(origin)
        for m in _MINSDK_GATE_RE.finditer(text):
            gates.setdefault(f"minSdk >= {m.group(1)}", set()).add(origin)
    return [
        {"requirement": k, "from": sorted(v), "blocks_build": True}
        for k, v in sorted(gates.items())
    ]


# ---- Impact seeds ------------------------------------------------------------

_IMPACT_KINDS = {"breaking", "removal", "deprecation"}


def impact_seeds_from_changes(changes: list[dict]) -> dict[str, list]:
    buckets = _new_buckets()
    for c in changes:
        if c.get("kind") not in _IMPACT_KINDS:
            continue
        replacement = c.get("replacement")
        if replacement:
            replacement = _strip_parens(replacement)
        for a in c.get("apis") or []:
            a = _strip_parens(a)
            if not a or _CONST_RE.match(a) or a == replacement:
                continue
            _add_token(buckets, a)
    return _finish_buckets(buckets)


# ---- LLM normalization ------------------------------------------------------

_VALID_KINDS = {
    "breaking", "removal", "deprecation", "requirement",
    "security", "feature", "behavior",
}


def _normalize_change(c: Any, default_ref: str | None) -> dict | None:
    if not isinstance(c, dict):
        return None
    summary = c.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    kind = c.get("kind")
    apis = [a for a in (c.get("apis") or []) if isinstance(a, str) and a]
    replacement = c.get("replacement")
    return {
        "kind": kind if kind in _VALID_KINDS else "behavior",
        "summary": summary.strip()[:240],
        "apis": apis[:6],
        "replacement": replacement if isinstance(replacement, str) else None,
        "version": (
            c.get("version") if isinstance(c.get("version"), str) else None
        ),
        "ref": c.get("ref") or default_ref,
    }


def _apply_enrichment(entry: dict, enr: dict) -> None:
    changes = enr.get("changes")
    if isinstance(changes, list) and changes:
        normalized = [
            _normalize_change(c, entry.get("source_url")) for c in changes
        ]
        normalized = [c for c in normalized if c is not None]
        if normalized:
            entry["changes"] = normalized
            entry["impact_seeds"] = impact_seeds_from_changes(normalized)
            entry.pop("changes_dropped", None)
    seeds = enr.get("seeds")
    if isinstance(seeds, dict):
        safe_seeds: dict[str, list] = {}
        for k in ("packages", "types", "functions"):
            raw = seeds.get(k, [])
            if isinstance(raw, list):
                safe_seeds[k] = sorted(
                    set(s for s in raw if isinstance(s, str) and s)
                )
            else:
                safe_seeds[k] = []
        members = {
            (m["owner"], m["name"])
            for k in ("types", "functions") for s in safe_seeds[k]
            if (m := _split_member(s))
        }
        safe_seeds["members"] = [
            {"owner": o, "name": n} for o, n in sorted(members)
        ]
        entry["seeds"] = safe_seeds
    if enr.get("effort") in ("low", "medium", "high"):
        entry["effort"] = enr["effort"]
    entry["enriched"] = True


# ---- Build document ----------------------------------------------------------

def build_intel_entry(dep: Dependency, result: dict) -> dict:
    versions = result.get("versions") or []
    notes_text = " ".join(n.get("notes", "") for n in versions)
    all_changes = heuristic_changes(versions, max_items=None)
    changes = all_changes[:MAX_CHANGES]
    jump = version_jump(dep.version_used, dep.latest_stable)
    confidence, conf_note = guess_confidence(result.get("source"), versions)
    entry = {
        "alias": dep.alias,
        "type": dep.dep_type,
        "from": dep.version_used,
        "to": dep.latest_stable,
        "jump": jump,
        "effort": guess_effort(jump, changes),
        "confidence": confidence,
        "source": result.get("source"),
        "source_url": result.get("source_url"),
        "seeds": extract_seeds(notes_text),
        "impact_seeds": impact_seeds_from_changes(changes),
        "changes": changes,
        "raw": versions,
    }
    if conf_note:
        entry["confidence_note"] = conf_note
    if len(all_changes) > len(changes):
        entry["changes_dropped"] = len(all_changes) - len(changes)
    return entry


def _combined_notes(entry: dict) -> str:
    return "\n\n".join(
        f"# {n.get('version')} ({n.get('date') or 's/f'})\n{n.get('notes', '')}"
        for n in entry.get("raw", [])
    )


def _select_targets(
    intel: dict[str, dict], scope: str, only: list[str],
) -> list[str]:
    if only:
        return [c for c in intel if c in only and intel[c].get("raw")]
    if scope == "all":
        return [c for c, e in intel.items() if e.get("raw")]
    return [
        c for c, e in intel.items()
        if e.get("raw") and e.get("jump") == "major"
    ]


def build_intel_document(
    deps_by_coord: dict[str, Dependency], results: list[dict], *,
    summarize: bool, scope: str, only: list[str],
    model: str, client: Any = None,
) -> dict:
    intel: dict[str, dict] = {}
    for r in results:
        dep = deps_by_coord.get(r["coordinate"])
        if dep:
            intel[r["coordinate"]] = build_intel_entry(dep, r)
    enrichment = "heuristic"

    if summarize and client is not None:
        targets = _select_targets(intel, scope, only)
        groups: dict[tuple, list[str]] = {}
        for coord in targets:
            e = intel[coord]
            groups.setdefault(
                (e.get("source_url") or coord, e["from"], e["to"]), []
            ).append(coord)
        log(
            f"   🧠 LLM on {len(targets)} deps in {len(groups)} "
            f"groups (deduped by repo)..."
        )
        for (_su, _f, _t), coords in groups.items():
            enr = enrich_intel_with_llm(
                coords[0], intel[coords[0]]["from"],
                intel[coords[0]]["to"],
                _combined_notes(intel[coords[0]]),
                model, client,
            )
            if enr:
                for c in coords:
                    _apply_enrichment(intel[c], enr)
        enrichment = "heuristic+llm"

    return {
        "schema": "deps-changelog-diff/change-intel-1",
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "enrichment": enrichment,
            "totals": {
                "analyzed": len(intel),
                "with_changes": sum(
                    1 for e in intel.values() if e["changes"]
                ),
                "no_source": sum(
                    1 for e in intel.values() if not e["raw"]
                ),
                "changes_dropped": sum(
                    e.get("changes_dropped", 0) for e in intel.values()
                ),
            },
        },
        "project_gates": extract_gates(intel),
        "dependencies": dict(sorted(intel.items())),
    }


def compare_intel(heur: dict, llm: dict) -> dict:
    hd, ld = heur["dependencies"], llm["dependencies"]
    replacements_added = kinds_refined = seeds_cleaned = enriched = 0
    highlights = []
    for coord, le in ld.items():
        he = hd.get(coord, {})
        if not le.get("enriched"):
            continue
        enriched += 1
        reps = sum(1 for c in le.get("changes", []) if c.get("replacement"))
        replacements_added += reps
        h_seeds = sum(
            len(he.get("seeds", {}).get(k, []))
            for k in ("packages", "types", "functions")
        )
        l_seeds = sum(
            len(le.get("seeds", {}).get(k, []))
            for k in ("packages", "types", "functions")
        )
        if h_seeds > l_seeds:
            seeds_cleaned += h_seeds - l_seeds
        h_behavior = sum(
            1 for c in he.get("changes", []) if c.get("kind") == "behavior"
        )
        l_behavior = sum(
            1 for c in le.get("changes", []) if c.get("kind") == "behavior"
        )
        if h_behavior > l_behavior:
            kinds_refined += h_behavior - l_behavior
        if reps:
            highlights.append(f"{coord}: +{reps} replacement(s)")
    return {
        "deps_enriched": enriched,
        "replacements_added": replacements_added,
        "kinds_refined": kinds_refined,
        "seeds_cleaned": seeds_cleaned,
        "highlights": highlights[:15],
    }
