"""Descubrimiento del repositorio en GitHub desde la URL o el POM de Maven."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .http import Fetcher

GOOGLE_MAVEN = "https://dl.google.com/android/maven2"
MAVEN_CENTRAL = "https://repo1.maven.org/maven2"
PLUGIN_PORTAL = "https://plugins.gradle.org/m2"

_GITHUB_RE = re.compile(
    r"github\.com[:/]+([\w.\-]+)/([\w.\-]+?)(?:\.git)?(?:[/#?].*)?$", re.IGNORECASE
)


def github_owner_repo(text: str | None) -> tuple[str, str] | None:
    if not text:
        return None
    match = _GITHUB_RE.search(text.strip())
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if owner.lower() in {"sponsors", "features", "about"}:
        return None
    return owner, repo


def _group_path(group_id: str) -> str:
    return group_id.replace(".", "/")


def pom_urls(group_id: str, artifact_id: str, version: str,
             dep_type: str) -> list[str]:
    path = (f"{_group_path(group_id)}/{artifact_id}/{version}/"
            f"{artifact_id}-{version}.pom")
    if dep_type == "google":
        bases = [GOOGLE_MAVEN, MAVEN_CENTRAL]
    elif dep_type == "plugin":
        bases = [PLUGIN_PORTAL, MAVEN_CENTRAL]
    else:
        bases = [MAVEN_CENTRAL, GOOGLE_MAVEN]
    return [f"{base}/{path}" for base in bases]


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _github_from_pom_xml(root: ET.Element) -> tuple[str, str] | None:
    for elem in root.iter():
        name = _localname(elem.tag).lower()
        if name in {"url", "connection", "developerconnection"} and elem.text:
            found = github_owner_repo(elem.text)
            if found:
                return found
    return None


def _parent_coords(root: ET.Element) -> tuple[str, str, str] | None:
    for elem in list(root):
        if _localname(elem.tag).lower() != "parent":
            continue
        g = a = v = None
        for child in elem:
            ln = _localname(child.tag).lower()
            if ln == "groupid":
                g = (child.text or "").strip()
            elif ln == "artifactid":
                a = (child.text or "").strip()
            elif ln == "version":
                v = (child.text or "").strip()
        if g and a and v:
            return g, a, v
    return None


def discover_github_from_pom(
    fetcher: Fetcher, group_id: str, artifact_id: str,
    version: str, dep_type: str, _depth: int = 0,
) -> tuple[str, str] | None:
    for url in pom_urls(group_id, artifact_id, version, dep_type):
        xml = fetcher.get_text(url)
        if not xml:
            continue
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            continue
        found = _github_from_pom_xml(root)
        if found:
            return found
        if _depth < 4:
            parent = _parent_coords(root)
            if parent:
                pg, pa, pv = parent
                if (pg, pa, pv) != (group_id, artifact_id, version):
                    up = discover_github_from_pom(
                        fetcher, pg, pa, pv, dep_type, _depth + 1
                    )
                    if up:
                        return up
        return None
    return None
