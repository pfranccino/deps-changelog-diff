"""Source adapters: AndroidX, GitHub, Known Sources, Generic crawl."""
from __future__ import annotations

import base64
import re
from typing import Any

from . import log
from .discovery import github_owner_repo, discover_github_from_pom
from .http import Fetcher, html_to_markdownish
from .models import Dependency
from .version import (
    version_tuple, version_sort_key, is_prerelease, in_range,
    extract_version_from_tag, versions_in_range,
)

GITHUB_API = "https://api.github.com"
ANDROIDX_RELEASES = "https://developer.android.com/jetpack/androidx/releases"


class VersionNote:
    __slots__ = ("version", "date", "url", "notes")

    def __init__(self, version: str, date: str | None, url: str | None,
                 notes: str):
        self.version = version
        self.date = date
        self.url = url
        self.notes = notes.strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version, "date": self.date,
            "url": self.url, "notes": self.notes,
        }


# ---- AndroidX slug -----------------------------------------------------------

_ANDROIDX_SLUG_OVERRIDES = {
    "androidx.test": "test",
    "androidx.test.ext": "test",
    "androidx.test.espresso": "test",
    "androidx.test.uiautomator": "uiautomator",
    "androidx.arch.core": "arch-core",
}


def androidx_slug(group_id: str) -> str | None:
    if group_id in _ANDROIDX_SLUG_OVERRIDES:
        return _ANDROIDX_SLUG_OVERRIDES[group_id]
    if not group_id.startswith("androidx."):
        return None
    return group_id[len("androidx."):].replace(".", "-")


# ---- Markdown slicing --------------------------------------------------------

_HEADING_LINE_RE = re.compile(r"(?m)^#{2,4}[ \t]+(.+?)[ \t]*$")
_VER_IN_TEXT_RE = re.compile(r"\b(\d+\.\d+(?:\.\d+)*(?:-[0-9A-Za-z.\-]+)?)")
_DATE_RE = re.compile(
    r"(?mi)^\s*(?:([A-Z][a-z]+ \d{1,2},? \d{4})"
    r"|(\d{1,2} de [a-záéíóúñ]+ de \d{4}))\s*$"
)
_DATE_INLINE_RE = re.compile(
    r"(?i)([A-Z][a-z]+ \d{1,2},? \d{4}"
    r"|\d{1,2} de [a-záéíóúñ]+ de \d{4})"
)
_VERSION_LABEL_RE = re.compile(r"(?i)^versi[oó]n\s")


def slice_markdown_by_version(
    markdown: str,
) -> dict[str, tuple[str | None, str]]:
    heads: list[tuple[re.Match, str, str]] = []
    for m in _HEADING_LINE_RE.finditer(markdown):
        text = m.group(1).strip()
        vm = _VER_IN_TEXT_RE.search(text)
        if not vm:
            continue
        version = vm.group(1)
        if len(version_tuple(version)) < 3:
            continue
        heads.append((m, text, version))

    sections: dict[str, tuple[str | None, str]] = {}
    for i, (m, heading, version) in enumerate(heads):
        start = m.end()
        end = heads[i + 1][0].start() if i + 1 < len(heads) else len(markdown)
        body = markdown[start:end].strip()
        date_match = _DATE_RE.search(f"{heading}\n{body[:200]}")
        date = (
            (date_match.group(1) or date_match.group(2)) if date_match else None
        )
        if not date:
            inline = _DATE_INLINE_RE.search(heading)
            date = inline.group(1) if inline else None
        has_component = heading != version and not _VERSION_LABEL_RE.match(
            heading
        )
        if version in sections:
            prev_date, prev_body = sections[version]
            merged = f"{prev_body}\n\n**{heading}**\n{body}".strip()
            sections[version] = (prev_date or date, merged)
        else:
            body_out = f"**{heading}**\n{body}" if has_component else body
            sections[version] = (date, body_out)
    return sections


# ---- Adapters ----------------------------------------------------------------

class AndroidXAdapter:
    name = "androidx"

    def __init__(self, lang: str = "es-419"):
        self.lang = lang

    def page_url(self, slug: str) -> str:
        if self.lang and self.lang.lower() != "en":
            return f"{ANDROIDX_RELEASES}/{slug}?hl={self.lang}"
        return f"{ANDROIDX_RELEASES}/{slug}"

    def notes_for(
        self, fetcher: Fetcher, entry: Dependency,
        include_prereleases: bool,
    ) -> tuple[str, list[VersionNote]] | None:
        slug = androidx_slug(entry.group_id)
        if not slug:
            return None
        url = self.page_url(slug)
        markdown = fetcher.get_rendered_markdown(url)
        if not markdown:
            return None
        sections = slice_markdown_by_version(markdown)
        if not sections:
            return None
        wanted = versions_in_range(
            sections.keys(), entry.version_used, entry.latest_stable,
            include_prereleases,
        )
        notes = [
            VersionNote(v, sections[v][0], url, sections[v][1])
            for v in wanted if v in sections
        ]
        return (url, notes)


class GitHubAdapter:
    name = "github"

    def __init__(self, max_releases: int = 300):
        self.max_releases = max_releases

    def _repo(self, fetcher: Fetcher, entry: Dependency) -> tuple[str, str] | None:
        found = github_owner_repo(entry.url)
        if found:
            return found
        return discover_github_from_pom(
            fetcher, entry.group_id, entry.artifact_id,
            entry.latest_stable, entry.dep_type,
        )

    def notes_for(
        self, fetcher: Fetcher, entry: Dependency,
        include_prereleases: bool,
    ) -> tuple[str, list[VersionNote]] | None:
        repo = self._repo(fetcher, entry)
        if not repo:
            return None
        owner, name = repo
        base = f"{GITHUB_API}/repos/{owner}/{name}"
        collected: dict[tuple, VersionNote] = {}
        page = 1
        fetched = 0
        while fetched < self.max_releases:
            data = fetcher.get_json(f"{base}/releases?per_page=100&page={page}")
            if not data:
                break
            for rel in data:
                fetched += 1
                tag = rel.get("tag_name") or ""
                ver = extract_version_from_tag(tag)
                if not ver:
                    continue
                if not include_prereleases and (
                    rel.get("prerelease") or is_prerelease(tag)
                ):
                    continue
                if in_range(ver, entry.version_used, entry.latest_stable,
                            include_prereleases):
                    collected[version_sort_key(ver)] = VersionNote(
                        ver,
                        (rel.get("published_at") or "")[:10] or None,
                        rel.get("html_url"),
                        rel.get("body") or "(no body in release)",
                    )
            if len(data) < 100:
                break
            page += 1
        if collected:
            ordered = [collected[k] for k in sorted(collected, reverse=True)]
            return f"https://github.com/{owner}/{name}/releases", ordered
        changelog = self._changelog_file(fetcher, owner, name)
        if changelog:
            url = f"https://github.com/{owner}/{name}/blob/HEAD/CHANGELOG.md"
            note = VersionNote(entry.latest_stable, None, url, changelog)
            return url, [note]
        return f"https://github.com/{owner}/{name}", []

    def _changelog_file(self, fetcher: Fetcher, owner: str,
                        name: str) -> str | None:
        for fname in ("CHANGELOG.md", "CHANGELOG", "CHANGES.md", "changelog.md"):
            data = fetcher.get_json(
                f"{GITHUB_API}/repos/{owner}/{name}/contents/{fname}"
            )
            if isinstance(data, dict) and data.get("content"):
                try:
                    raw = base64.b64decode(data["content"]).decode(
                        "utf-8", "replace"
                    )
                except Exception:
                    continue
                return raw[:20000]
        return None


class GenericCrawlAdapter:
    name = "generic"

    def notes_for(
        self, fetcher: Fetcher, entry: Dependency,
        include_prereleases: bool,
    ) -> tuple[str, list[VersionNote]] | None:
        if not entry.url:
            return None
        markdown = fetcher.get_rendered_markdown(entry.url)
        if not markdown:
            return None
        sections = slice_markdown_by_version(markdown)
        wanted = versions_in_range(
            sections.keys(), entry.version_used,
            entry.latest_stable, include_prereleases,
        )
        notes = [
            VersionNote(v, sections[v][0], entry.url, sections[v][1])
            for v in wanted if v in sections
        ]
        return entry.url, notes


class KnownSourceAdapter:
    """Official per-version pages for libraries without GitHub or AndroidX."""
    name = "known"
    FIREBASE_URL = "https://firebase.google.com/support/release-notes/android"
    PLACES_URL = (
        "https://developers.google.com/maps/documentation/places/"
        "android-sdk/release-notes"
    )
    PLAYSERVICES_URL = "https://developers.google.com/android/guides/releases"

    def _route(self, entry: Dependency) -> tuple[str, str] | None:
        g, a = entry.group_id, entry.artifact_id
        if entry.coordinate == "com.google.firebase:firebase-bom":
            return self.FIREBASE_URL, "firebase"
        if g == "com.google.android.libraries.places" and a == "places":
            return self.PLACES_URL, "slice"
        if g == "com.google.android.gms" and a.startswith("play-services"):
            return self.PLAYSERVICES_URL, "playservices"
        return None

    def notes_for(
        self, fetcher: Fetcher, entry: Dependency,
        include_prereleases: bool,
    ) -> tuple[str, list[VersionNote]] | None:
        route = self._route(entry)
        if not route:
            return None
        url, mode = route
        markdown = fetcher.get_rendered_markdown(url)
        if not markdown:
            return None
        if mode == "playservices":
            return url, self._playservices(
                markdown, entry, url, include_prereleases
            )
        if mode == "firebase":
            return url, self._firebase(
                markdown, entry, url, include_prereleases
            )
        sections = slice_markdown_by_version(markdown)
        wanted = versions_in_range(
            sections.keys(), entry.version_used,
            entry.latest_stable, include_prereleases,
        )
        return url, [
            VersionNote(v, sections[v][0], url, sections[v][1]) for v in wanted
        ]

    def _firebase(
        self, md: str, entry: Dependency, url: str,
        include_prereleases: bool = False,
    ) -> list[VersionNote]:
        bounds = list(re.finditer(
            r"(?im)^.*?bo[mM].*?version\s+(\d+\.\d+\.\d+).*$", md
        ))
        if not bounds:
            sections = slice_markdown_by_version(md)
            wanted = versions_in_range(
                sections.keys(), entry.version_used,
                entry.latest_stable, include_prereleases,
            )
            return [
                VersionNote(v, sections[v][0], url, sections[v][1])
                for v in wanted
            ]
        notes: list[VersionNote] = []
        for i, m in enumerate(bounds):
            ver = m.group(1)
            if not in_range(ver, entry.version_used, entry.latest_stable,
                            include_prereleases):
                continue
            start = m.end()
            end = bounds[i + 1].start() if i + 1 < len(bounds) else len(md)
            notes.append(
                VersionNote(ver, None, url, md[start:end].strip()[:3000])
            )
        notes.sort(key=lambda n: version_sort_key(n.version), reverse=True)
        return notes

    def _playservices(
        self, md: str, entry: Dependency, url: str,
        include_prereleases: bool = False,
    ) -> list[VersionNote]:
        art = re.escape(entry.artifact_id)
        pat = re.compile(rf"{art}\b[^\n(]*\(v?(\d+\.\d+(?:\.\d+)*)\)")
        heads = list(re.finditer(r"(?m)^#{1,4}[ \t]+(.+?)[ \t]*$", md))
        collected: dict[tuple, VersionNote] = {}
        for i, h in enumerate(heads):
            start = h.end()
            end = heads[i + 1].start() if i + 1 < len(heads) else len(md)
            body = md[start:end]
            for m in pat.finditer(body):
                ver = m.group(1)
                if in_range(ver, entry.version_used, entry.latest_stable,
                            include_prereleases):
                    key = version_sort_key(ver)
                    collected.setdefault(
                        key,
                        VersionNote(ver, h.group(1).strip(), url,
                                    body.strip()[:2500]),
                    )
        return [collected[k] for k in sorted(collected, reverse=True)]


class SourceRouter:
    """Pick the adapter by type, with crawl4AI/generic as last resort."""

    def __init__(self, max_releases: int = 300, androidx_lang: str = "es-419"):
        self.androidx = AndroidXAdapter(lang=androidx_lang)
        self.github = GitHubAdapter(max_releases=max_releases)
        self.known = KnownSourceAdapter()
        self.generic = GenericCrawlAdapter()

    def order_for(self, entry: Dependency) -> list[Any]:
        if entry.dep_type == "google" or entry.group_id.startswith("androidx."):
            return [self.androidx, self.known, self.github, self.generic]
        return [self.github, self.known, self.androidx, self.generic]

    def resolve(
        self, fetcher: Fetcher, entry: Dependency,
        include_prereleases: bool,
    ) -> dict[str, Any]:
        for adapter in self.order_for(entry):
            result = adapter.notes_for(fetcher, entry, include_prereleases)
            if result is None:
                continue
            source_url, notes = result
            if notes:
                return {
                    "source": adapter.name,
                    "source_url": source_url,
                    "notes": [n.to_dict() for n in notes],
                }
        return {"source": None, "source_url": None, "notes": []}
