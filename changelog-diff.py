#!/usr/bin/env python3
"""Thin entry point — the logic lives in the changelog_diff package."""
import sys

from changelog_diff.cli import main

# Re-export everything so `importlib.util.spec_from_file_location` in tests
# and any external code that imported from the monolith keeps working.
from changelog_diff import log, _QUIET  # noqa: F401
from changelog_diff.version import *  # noqa: F401, F403
from changelog_diff.http import Fetcher, html_to_markdownish  # noqa: F401
from changelog_diff.discovery import (  # noqa: F401
    github_owner_repo, pom_urls, discover_github_from_pom,
)
from changelog_diff.models import (  # noqa: F401
    OUTDATED_CODES, Dependency, load_dependencies,
)
from changelog_diff.cache import Cache  # noqa: F401
from changelog_diff.adapters import (  # noqa: F401
    VersionNote, AndroidXAdapter, GitHubAdapter, GenericCrawlAdapter,
    KnownSourceAdapter, SourceRouter,
    androidx_slug, slice_markdown_by_version,
)
from changelog_diff.llm import (  # noqa: F401
    SUMMARY_PROMPT, INTEL_PROMPT,
    summarize_with_llm, enrich_intel_with_llm,
)
from changelog_diff.intel import (  # noqa: F401
    classify_kind, extract_seeds, heuristic_changes,
    version_jump, guess_effort, guess_confidence,
    extract_gates, impact_seeds_from_changes,
    build_intel_entry, build_intel_document, compare_intel,
    _normalize_change, _apply_enrichment, _select_targets,
    _VALID_KINDS,
)
from changelog_diff.render import render_markdown, render_intel_markdown  # noqa: F401
from changelog_diff.cli import process_dependency  # noqa: F401

if __name__ == "__main__":
    sys.exit(main())
