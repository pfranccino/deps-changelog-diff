"""Punto de entrada CLI: argparse, orquestación y main()."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
from datetime import datetime
from typing import Any

import changelog_diff
from . import log
from .adapters import SourceRouter
from .cache import Cache
from .http import Fetcher
from .intel import build_intel_document, compare_intel
from .llm import summarize_with_llm
from .models import Dependency, load_dependencies
from .render import render_markdown, render_intel_markdown


def process_dependency(
    dep: Dependency, fetcher: Fetcher, router: SourceRouter,
    cache: Cache, include_prereleases: bool,
    summarize: bool, model: str, api_key: str | None,
) -> dict[str, Any]:
    cached = cache.get(dep)
    if cached is not None:
        log(f"   💾 {dep.coordinate} (caché)")
        result = cached
    else:
        log(f"   🔎 {dep.coordinate}  {dep.version_used} → {dep.latest_stable}")
        resolved = router.resolve(fetcher, dep, include_prereleases)
        result = {
            "coordinate": dep.coordinate,
            "type": dep.dep_type,
            "status_code": dep.status_code,
            "from": dep.version_used,
            "to": dep.latest_stable,
            "source": resolved["source"],
            "source_url": resolved["source_url"],
            "versions": resolved["notes"],
        }
        cache.put(dep, result)

    if (summarize and api_key and result.get("versions")
            and "summary" not in result):
        combined = "\n\n".join(
            f"# {n['version']} ({n.get('date') or 's/f'})\n{n['notes']}"
            for n in result["versions"]
        )
        summary = summarize_with_llm(
            dep.coordinate, dep.version_used, dep.latest_stable,
            combined, model, api_key,
        )
        if summary:
            result["summary"] = summary
            cache.put(dep, result)
    return result


def main() -> int:
    changelog_diff._QUIET = False
    parser = argparse.ArgumentParser(
        description="Analiza qué cambia entre versiones de dependencias "
                    "usando la doc del owner.",
    )
    parser.add_argument(
        "status_json", nargs="?", default="dependency_status.json",
        help="Ruta al JSON que genera check-dependencies.sh.",
    )
    parser.add_argument(
        "-o", "--output", default="changelog_diff.json",
        help="Ruta del JSON de salida.",
    )
    parser.add_argument(
        "--md", metavar="RUTA",
        help="Además, escribe un Markdown legible en esta ruta.",
    )
    parser.add_argument(
        "--format", choices=("raw", "intel"), default="raw",
        help="raw = notas crudas por versión (por defecto). "
             "intel = esquema change-intel-1.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Analiza todas las dependencias, no solo las desactualizadas.",
    )
    parser.add_argument(
        "--include-prereleases", action="store_true",
        help="Incluye alphas/betas/rc intermedias en el rango.",
    )
    parser.add_argument(
        "--summarize", action="store_true",
        help="Enriquece con LLM (necesita ANTHROPIC_API_KEY).",
    )
    parser.add_argument(
        "--summarize-scope", choices=("major", "all"), default="major",
        help="A qué deps aplica el LLM en --format intel.",
    )
    parser.add_argument(
        "--summarize-only", default="",
        help="Coordenadas separadas por comas a enriquecer con LLM.",
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="Corre heurístico vs LLM (intel), reporta el delta.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        help="Modelo para el resumen (o ANTHROPIC_MODEL).",
    )
    parser.add_argument(
        "--cache-dir", default=".changelog-cache",
        help="Directorio de caché por versión.",
    )
    parser.add_argument(
        "--github-token", default=os.environ.get("GITHUB_TOKEN"),
        help="Token de GitHub para subir el rate limit.",
    )
    parser.add_argument(
        "--androidx-lang",
        default=os.environ.get("ANDROIDX_LANG", "es-419"),
        help="Idioma de las páginas AndroidX vía ?hl=.",
    )
    parser.add_argument(
        "--no-crawl4ai", action="store_true",
        help="No uses crawl4AI en el fallback (evita Playwright en CI).",
    )
    parser.add_argument(
        "--max-releases", type=int, default=300,
        help="Máximo de releases de GitHub a recorrer por dependencia.",
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=6,
        help="Dependencias en paralelo.",
    )
    parser.add_argument(
        "--timeout", type=int, default=20,
        help="Timeout por petición HTTP (s).",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="Silencia el progreso.",
    )
    args = parser.parse_args()

    changelog_diff._QUIET = args.quiet

    if not os.path.exists(args.status_json):
        print(
            f"❌ No existe {args.status_json}. "
            f"Corre primero check-dependencies.sh.",
            file=sys.stderr,
        )
        return 1

    deps = load_dependencies(args.status_json, include_all=args.all)
    if not deps:
        log("✅ No hay dependencias que analizar. Usa --all para forzar.")
        if args.format == "intel":
            empty_doc = {
                "schema": "deps-changelog-diff/change-intel-1",
                "meta": {
                    "generated_at": datetime.now().isoformat(
                        timespec="seconds"
                    ),
                    "enrichment": "none",
                    "totals": {
                        "analyzed": 0, "with_changes": 0, "no_source": 0,
                    },
                },
                "project_gates": [],
                "dependencies": {},
            }
            with open(args.output, "w", encoding="utf-8") as handle:
                json.dump(empty_doc, handle, ensure_ascii=False, indent=2)
            if args.md:
                with open(args.md, "w", encoding="utf-8") as handle:
                    handle.write(render_intel_markdown(empty_doc))
        else:
            with open(args.output, "w", encoding="utf-8") as handle:
                json.dump({}, handle)
            if args.md:
                with open(args.md, "w", encoding="utf-8") as handle:
                    handle.write(render_markdown([]))
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if args.summarize and not api_key:
        log("   ⚠️  --summarize pedido pero no hay ANTHROPIC_API_KEY.")

    cache_dir = (
        None if args.cache_dir.lower() in ("", "none") else args.cache_dir
    )
    opts_key = (
        f"pre={args.include_prereleases};"
        f"lang={args.androidx_lang};"
        f"max={args.max_releases}"
    )
    cache = Cache(cache_dir, opts_key=opts_key)
    router = SourceRouter(
        max_releases=args.max_releases, androidx_lang=args.androidx_lang,
    )
    fetcher = Fetcher(
        timeout=args.timeout, github_token=args.github_token,
        use_crawl4ai=not args.no_crawl4ai,
    )

    deps_by_coord = {d.coordinate: d for d in deps}
    only = [c.strip() for c in args.summarize_only.split(",") if c.strip()]
    summary_in_fetch = (
        args.format == "raw" and args.summarize and not args.compare
    )

    log(f"🚀 Analizando {len(deps)} dependencias...")
    results: list[dict[str, Any]] = []
    failed = 0
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, args.jobs)
    ) as pool:
        futures = {
            pool.submit(
                process_dependency, dep, fetcher, router, cache,
                args.include_prereleases, summary_in_fetch,
                args.model, api_key,
            ): dep
            for dep in deps
        }
        for future in concurrent.futures.as_completed(futures):
            dep = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                failed += 1
                log(f"   ❌ {dep.coordinate}: {type(exc).__name__}: {exc}")
    results.sort(key=lambda r: r["coordinate"])
    if failed:
        log(f"   ⚠️  {failed} dependencia(s) fallaron durante el análisis.")

    def _write(path: str, obj: Any) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, ensure_ascii=False, indent=2)
        log(f"💾 Escrito {path}")

    if args.compare:
        base = (
            args.output[:-5]
            if args.output.endswith(".json") else args.output
        )
        heur = build_intel_document(
            deps_by_coord, results, summarize=False,
            scope=args.summarize_scope, only=only,
            model=args.model, api_key=api_key,
        )
        _write(f"{base}.heuristic.json", heur)
        if not api_key:
            log("⚠️  --compare sin ANTHROPIC_API_KEY: solo heurístico.")
            return 0
        llm = build_intel_document(
            deps_by_coord, results, summarize=True,
            scope=args.summarize_scope, only=only,
            model=args.model, api_key=api_key,
        )
        _write(f"{base}.llm.json", llm)
        delta = compare_intel(heur, llm)
        print("\n===== COMPARACIÓN heurístico vs LLM =====")
        print(f"  deps enriquecidas por LLM : {delta['deps_enriched']}")
        print(f"  replacements (old→new)    : +{delta['replacements_added']}")
        print(f"  kinds refinados           : +{delta['kinds_refined']}")
        print(f"  seeds limpiados (ruido)   : -{delta['seeds_cleaned']}")
        for h in delta["highlights"]:
            print(f"    • {h}")
        _write(f"{base}.compare.json", delta)
        return 0

    if args.format == "intel":
        doc = build_intel_document(
            deps_by_coord, results, summarize=args.summarize,
            scope=args.summarize_scope, only=only,
            model=args.model, api_key=api_key,
        )
        _write(args.output, doc)
        if args.md:
            with open(args.md, "w", encoding="utf-8") as handle:
                handle.write(render_intel_markdown(doc))
            log(f"📝 Escrito {args.md}")
        tt = doc["meta"]["totals"]
        log(
            f"✨ Listo (intel/{doc['meta']['enrichment']}): "
            f"{tt['with_changes']}/{tt['analyzed']} con cambios, "
            f"{tt['no_source']} sin fuente."
        )
        return 0

    output = {r["coordinate"]: r for r in results}
    _write(args.output, output)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(results))
        log(f"📝 Escrito {args.md}")
    found = sum(1 for r in results if r.get("versions"))
    log(f"✨ Listo: {found}/{len(results)} con notas encontradas.")
    return 0
