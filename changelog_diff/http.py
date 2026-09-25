"""Shared HTTP client and HTML-to-Markdown conversion."""
from __future__ import annotations

import html as html_module
import re
import threading
from typing import Any

import requests

from . import log

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(fragment: str) -> str:
    return _TAG_RE.sub("", fragment).strip()


def html_to_markdownish(html: str) -> str:
    """Convert HTML to a Markdown sufficient for slicing by version, without a browser."""
    s = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", html)
    for level in range(1, 7):
        s = re.sub(
            rf"(?is)<h{level}[^>]*>(.*?)</h{level}>",
            lambda m, lv=level: f"\n\n{'#' * lv} {_strip_tags(m.group(1))}\n",
            s,
        )
    s = re.sub(r"(?is)<li[^>]*>(.*?)</li>", lambda m: f"\n- {_strip_tags(m.group(1))}", s)
    s = re.sub(r"(?is)</(p|div|tr|ul|ol|table|section)\s*>", "\n", s)
    s = re.sub(r"(?is)<br\s*/?>", "\n", s)
    s = _TAG_RE.sub(" ", s)
    s = html_module.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n[ \t]+", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


class Fetcher:
    """Thread-safe HTTP client with optional crawl4AI support for fallback."""

    def __init__(self, timeout: int = 20, github_token: str | None = None,
                 use_crawl4ai: bool = True):
        self.timeout = timeout
        self._local = threading.local()
        self._ua = "toml-deps-changelog/1.0"
        self.github_token = github_token
        self.use_crawl4ai = use_crawl4ai
        self._crawler_lock = threading.Lock()
        self._crawler_checked = False
        self._crawl4ai_ok = False

    @property
    def session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers["User-Agent"] = self._ua
            self._local.session = s
        return s

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str | None:
        try:
            resp = self.session.get(url, timeout=self.timeout, headers=headers)
        except requests.RequestException as exc:
            log(f"   ⚠️  network error on {url}: {exc}")
            return None
        if resp.status_code == 200:
            return resp.text
        log(f"   ⚠️  {resp.status_code} on {url}")
        return None

    def get_json(self, url: str) -> Any | None:
        headers = {"Accept": "application/vnd.github+json"}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        try:
            resp = self.session.get(url, timeout=self.timeout, headers=headers)
        except requests.RequestException as exc:
            log(f"   ⚠️  network error on {url}: {exc}")
            return None
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            log("   ⚠️  GitHub rate limit. Pass --github-token or GITHUB_TOKEN.")
        elif resp.status_code != 404:
            log(f"   ⚠️  {resp.status_code} on {url}")
        return None

    def get_rendered_markdown(self, url: str) -> str | None:
        if self.use_crawl4ai:
            md = self._crawl4ai(url)
            if md is not None:
                return md
        html = self.get_text(url)
        return html_to_markdownish(html) if html else None

    def _crawl4ai(self, url: str) -> str | None:
        with self._crawler_lock:
            if not self._crawler_checked:
                self._crawler_checked = True
                try:
                    import crawl4ai  # noqa: F401
                    self._crawl4ai_ok = True
                except Exception:
                    log("   ℹ️  crawl4AI is not installed; using plain HTTP fetch for fallback.")
                    self._crawl4ai_ok = False
        if not self._crawl4ai_ok:
            return None
        try:
            import asyncio
            from crawl4ai import AsyncWebCrawler

            async def _run() -> str | None:
                async with AsyncWebCrawler(verbose=False) as crawler:
                    result = await crawler.arun(url=url)
                    return getattr(result, "markdown", None) or None

            return asyncio.run(_run())
        except Exception as exc:
            log(f"   ⚠️  crawl4AI failed on {url}: {exc}")
            return None
