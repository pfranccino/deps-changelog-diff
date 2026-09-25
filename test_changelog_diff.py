#!/usr/bin/env python3
"""Offline tests for changelog-diff (pure functions and parsing)."""
import importlib.util
import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# El archivo tiene guion, no es importable por nombre: lo cargamos por ruta.
_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("changelog_diff", os.path.join(_HERE, "changelog-diff.py"))
cd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cd)


class TestVersionHelpers(unittest.TestCase):
    def test_version_tuple(self):
        self.assertEqual(cd.version_tuple("1.2.10"), (1, 2, 10))
        self.assertEqual(cd.version_tuple("2026.08.00"), (2026, 8, 0))
        self.assertEqual(cd.version_tuple("5.0.0-alpha.14"), (5, 0, 0))

    def test_version_sort_key_orders_prerelease_below_final(self):
        self.assertLess(cd.version_sort_key("1.3.0-alpha01"), cd.version_sort_key("1.3.0"))
        self.assertLess(cd.version_sort_key("1.3.0-beta01"), cd.version_sort_key("1.3.0-rc01"))
        self.assertLess(cd.version_sort_key("1.2.9"), cd.version_sort_key("1.3.0-alpha01"))

    def test_is_prerelease(self):
        self.assertTrue(cd.is_prerelease("1.0.0-alpha01"))
        self.assertTrue(cd.is_prerelease("1.0.0-rc1"))
        self.assertTrue(cd.is_prerelease("2.0.0-SNAPSHOT"))
        self.assertFalse(cd.is_prerelease("1.2.3"))
        self.assertFalse(cd.is_prerelease("2026.08.00"))

    def test_extract_version_from_tag(self):
        self.assertEqual(cd.extract_version_from_tag("v1.2.3"), "1.2.3")
        self.assertEqual(cd.extract_version_from_tag("okhttp-5.0.0"), "5.0.0")
        self.assertEqual(cd.extract_version_from_tag("parent-4.12.0"), "4.12.0")
        self.assertEqual(cd.extract_version_from_tag("release/2.9.1"), "2.9.1")
        self.assertIsNone(cd.extract_version_from_tag("latest"))
        self.assertIsNone(cd.extract_version_from_tag(""))

    def test_versions_in_range_excludes_prereleases(self):
        cands = ["1.1.0", "1.2.0", "1.2.1", "1.3.0-alpha01", "1.3.0", "1.4.0"]
        got = cd.versions_in_range(cands, "1.1.0", "1.3.0")
        self.assertEqual(got, ["1.3.0", "1.2.1", "1.2.0"])  # nuevas->viejas, sin alpha, sin 1.4

    def test_versions_in_range_include_prereleases(self):
        cands = ["1.2.0", "1.3.0-alpha01", "1.3.0"]
        got = cd.versions_in_range(cands, "1.2.0", "1.3.0", include_prereleases=True)
        self.assertIn("1.3.0-alpha01", got)

    def test_versions_in_range_calver(self):
        cands = ["2026.01.00", "2026.04.01", "2026.08.00"]
        got = cd.versions_in_range(cands, "2026.01.00", "2026.08.00")
        self.assertEqual(got, ["2026.08.00", "2026.04.01"])


class TestAndroidX(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(cd.androidx_slug("androidx.compose.foundation"), "compose-foundation")
        self.assertEqual(cd.androidx_slug("androidx.core"), "core")
        self.assertEqual(cd.androidx_slug("androidx.compose.ui"), "compose-ui")
        self.assertIsNone(cd.androidx_slug("com.squareup.okhttp3"))

    def test_slice_markdown_by_version(self):
        md = (
            "## Version 1.13\n"
            "### Version 1.13.0-alpha01\n"
            "September 09, 2026\n"
            "Cosas alpha.\n"
            "## Version 1.12\n"
            "### Version 1.12.1\n"
            "August 01, 2026\n"
            "Arreglo importante.\n"
            "### Version 1.12.0\n"
            "July 01, 2026\n"
            "Primera de la 1.12.\n"
        )
        sections = cd.slice_markdown_by_version(md)
        self.assertIn("1.12.1", sections)
        self.assertIn("1.12.0", sections)
        self.assertIn("1.13.0-alpha01", sections)
        date, body = sections["1.12.1"]
        self.assertEqual(date, "August 01, 2026")
        self.assertIn("Arreglo importante", body)
        self.assertNotIn("Primera de la 1.12", body)  # no se cuela la siguiente sección

    def test_slice_markdown_spanish(self):
        md = (
            "## Versión 1.12\n"
            "### Versión 1.12.0\n"
            "12 de agosto de 2026\n"
            "Cambios importantes desde la versión 1.11.0.\n"
            "### Versión 1.11.0\n"
            "22 de abril de 2026\n"
            "Nuevas funciones de la 1.11.\n"
        )
        sections = cd.slice_markdown_by_version(md)
        self.assertIn("1.12.0", sections)
        self.assertIn("1.11.0", sections)
        date, body = sections["1.12.0"]
        self.assertEqual(date, "12 de agosto de 2026")
        self.assertIn("Cambios importantes", body)
        self.assertNotIn("Nuevas funciones de la 1.11", body)

    def test_html_to_markdownish_androidx_like(self):
        # HTML estático como el que sirve developer.android.com (h2 grupo, h3 versión, li).
        html = """
        <html><body>
        <h2 data-text="Versión 1.12">Versión 1.12</h2>
        <h3>Versión 1.12.0</h3>
        <p>12 de agosto de 2026</p>
        <p><b>Cambios en la API</b></p>
        <ul>
          <li>Se agreg&oacute; <code>Modifier.foo()</code> para animar.</li>
          <li>Se elimin&oacute; el par&aacute;metro <code>bar</code>.</li>
        </ul>
        <h3>Versión 1.11.0</h3>
        <p>22 de abril de 2026</p>
        <ul><li>Cosas viejas de la 1.11.</li></ul>
        </body></html>
        """
        md = cd.html_to_markdownish(html)
        self.assertIn("### Versión 1.12.0", md)
        self.assertIn("- Se agregó", md)  # entidades HTML decodificadas
        sections = cd.slice_markdown_by_version(md)
        self.assertIn("1.12.0", sections)
        _, body = sections["1.12.0"]
        self.assertIn("Modifier.foo()", body)
        self.assertIn("Se eliminó el parámetro", body)
        self.assertNotIn("Cosas viejas de la 1.11", body)  # no se cuela la siguiente

    def test_slug_overrides(self):
        self.assertEqual(cd.androidx_slug("androidx.test.ext"), "test")
        self.assertEqual(cd.androidx_slug("androidx.test"), "test")
        self.assertEqual(cd.androidx_slug("androidx.arch.core"), "arch-core")

    def test_slice_multicomponent_headings(self):
        # Página estilo /test: encabezados con nombre de componente, sin la palabra "Version".
        md = (
            "### Core Core-ktx 1.7.0\n"
            "Notas de core.\n"
            "### Runner and Rules 1.7.0\n"
            "Notas de runner.\n"
            "### Espresso 3.7.0\n"
            "Notas de espresso.\n"
        )
        sections = cd.slice_markdown_by_version(md)
        self.assertIn("3.7.0", sections)
        self.assertIn("1.7.0", sections)  # misma versión en dos componentes → concatenada
        _, body = sections["1.7.0"]
        self.assertIn("Notas de core", body)
        self.assertIn("Notas de runner", body)
        self.assertIn("Runner and Rules 1.7.0", body)  # se marca el componente

    def test_slice_version_mid_heading_places(self):
        # Places: la versión va a mitad del título, con la fecha al final.
        md = (
            "## Version 5.3.0 (July 07, 2026)\n"
            "GA launch of Advanced Places UI Kit.\n"
            "## Version 5.2.0 (April 07, 2026)\n"
            "New fields added.\n"
        )
        sections = cd.slice_markdown_by_version(md)
        self.assertIn("5.3.0", sections)
        self.assertIn("5.2.0", sections)
        date, body = sections["5.3.0"]
        self.assertEqual(date, "July 07, 2026")
        self.assertIn("Advanced Places", body)

    def test_androidx_page_url_lang(self):
        self.assertEqual(
            cd.AndroidXAdapter(lang="es-419").page_url("compose-animation"),
            "https://developer.android.com/jetpack/androidx/releases/compose-animation?hl=es-419",
        )
        self.assertEqual(
            cd.AndroidXAdapter(lang="en").page_url("compose-animation"),
            "https://developer.android.com/jetpack/androidx/releases/compose-animation",
        )


class TestGitHubDiscovery(unittest.TestCase):
    def test_owner_repo_from_urls(self):
        self.assertEqual(cd.github_owner_repo("https://github.com/square/okhttp"), ("square", "okhttp"))
        self.assertEqual(cd.github_owner_repo("scm:git:git://github.com/square/okhttp.git"),
                         ("square", "okhttp"))
        self.assertEqual(cd.github_owner_repo("https://github.com/JakeWharton/timber/issues"),
                         ("JakeWharton", "timber"))
        self.assertIsNone(cd.github_owner_repo("https://example.com/foo"))
        self.assertIsNone(cd.github_owner_repo(None))

    def test_discover_from_pom(self):
        pom = """<?xml version="1.0"?>
        <project xmlns="http://maven.apache.org/POM/4.0.0">
          <url>https://square.github.io/okhttp/</url>
          <scm>
            <connection>scm:git:https://github.com/square/okhttp.git</connection>
            <url>https://github.com/square/okhttp</url>
          </scm>
        </project>"""

        class FakeFetcher:
            def get_text(self, url, headers=None):
                return pom

        found = cd.discover_github_from_pom(FakeFetcher(), "com.squareup.okhttp3", "okhttp",
                                            "5.0.0", "maven")
        self.assertEqual(found, ("square", "okhttp"))

    def test_discover_from_parent_pom(self):
        # El POM del módulo NO trae github; el <parent> sí. Debe seguir la cadena.
        module_pom = """<?xml version="1.0"?>
        <project xmlns="http://maven.apache.org/POM/4.0.0">
          <parent>
            <groupId>com.google.code.gson</groupId>
            <artifactId>gson-parent</artifactId>
            <version>2.14.0</version>
          </parent>
          <artifactId>gson</artifactId>
        </project>"""
        parent_pom = """<?xml version="1.0"?>
        <project xmlns="http://maven.apache.org/POM/4.0.0">
          <scm><url>https://github.com/google/gson</url></scm>
        </project>"""

        class FakeFetcher:
            def get_text(self, url, headers=None):
                if "gson-parent" in url:
                    return parent_pom
                if "/gson/2.14.0/gson-2.14.0.pom" in url:
                    return module_pom
                return None

        found = cd.discover_github_from_pom(FakeFetcher(), "com.google.code.gson", "gson",
                                            "2.14.0", "maven")
        self.assertEqual(found, ("google", "gson"))


class TestInput(unittest.TestCase):
    def _write(self, data):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        self.addCleanup(os.remove, path)
        return path

    def test_load_filters_outdated_and_usable(self):
        data = {
            "com.squareup.okhttp3:okhttp": {
                "version_used": "4.12.0", "latest_stable": "5.0.0",
                "type": "maven", "status_code": "major",
                "url": "https://github.com/square/okhttp",
            },
            "androidx.core:core": {  # al día -> se descarta salvo --all
                "version_used": "1.12.0", "latest_stable": "1.12.0",
                "type": "google", "status_code": "ok", "url": "",
            },
            "foo:bar": {  # sin latest -> no usable
                "version_used": "1.0.0", "latest_stable": "N/A",
                "type": "maven", "status_code": "unknown", "url": "",
            },
        }
        path = self._write(data)
        only_outdated = cd.load_dependencies(path, include_all=False)
        self.assertEqual([d.coordinate for d in only_outdated], ["com.squareup.okhttp3:okhttp"])

        all_deps = cd.load_dependencies(path, include_all=True)
        coords = sorted(d.coordinate for d in all_deps)
        # 'ok' entra con --all pero solo si es "usable"; aquí no lo es (misma versión).
        self.assertEqual(coords, ["com.squareup.okhttp3:okhttp"])

    def test_dependency_fields(self):
        dep = cd.Dependency("com.squareup.okhttp3:okhttp", {
            "version_used": "4.12.0", "latest_stable": "5.0.0", "type": "maven",
            "url": "https://github.com/square/okhttp", "status_code": "major",
        })
        self.assertEqual(dep.group_id, "com.squareup.okhttp3")
        self.assertEqual(dep.artifact_id, "okhttp")
        self.assertTrue(dep.usable)


class TestKnownSources(unittest.TestCase):
    def _fetcher(self, markdown):
        class FakeFetcher:
            def get_rendered_markdown(self, url):
                return markdown
        return FakeFetcher()

    def test_firebase_bom(self):
        md = (
            "### Firebase Android BoM (Bill of Materials) version 34.19.0\n"
            "Added turnComplete support in the LiveSessionFutures Java API.\n"
            "### Firebase Android BoM (Bill of Materials) version 34.10.0\n"
            "Cosas viejas.\n"
            "### Firebase Android BoM (Bill of Materials) version 34.5.0\n"
            "Aún más viejas.\n"
        )
        dep = cd.Dependency("com.google.firebase:firebase-bom", {
            "version_used": "34.8.0", "latest_stable": "34.19.0", "type": "google", "url": "",
        })
        url, notes = cd.KnownSourceAdapter().notes_for(self._fetcher(md), dep, False)
        vers = [n.version for n in notes]
        self.assertEqual(vers, ["34.19.0", "34.10.0"])  # 34.5.0 queda fuera del rango
        self.assertIn("turnComplete", notes[0].notes)

    def test_places_slice(self):
        md = ("## Version 5.3.0 (July 07, 2026)\nGA launch.\n"
              "## Version 5.0.0 (Jan 01, 2025)\nViejo.\n")
        dep = cd.Dependency("com.google.android.libraries.places:places", {
            "version_used": "5.1.1", "latest_stable": "5.3.0", "type": "google", "url": "",
        })
        url, notes = cd.KnownSourceAdapter().notes_for(self._fetcher(md), dep, False)
        self.assertEqual([n.version for n in notes], ["5.3.0"])

    def test_playservices_by_date(self):
        md = (
            "## September 17, 2026\n"
            "The latest updates to the play-services-base library (v18.11.0) include:\n"
            "Changed the minSdkVersion to 24.\n"
            "## March 01, 2024\n"
            "The play-services-base library (v17.5.0) fixed stuff.\n"
        )
        dep = cd.Dependency("com.google.android.gms:play-services-base", {
            "version_used": "17.1.0", "latest_stable": "18.11.0", "type": "google", "url": "",
        })
        url, notes = cd.KnownSourceAdapter().notes_for(self._fetcher(md), dep, False)
        vers = [n.version for n in notes]
        self.assertIn("18.11.0", vers)
        self.assertIn("17.5.0", vers)
        self.assertIn("minSdkVersion", notes[0].notes)


class TestGenericNoShell(unittest.TestCase):
    def test_generic_returns_empty_on_shell(self):
        # Cascarón tipo maven.google.com (app JS sin versiones) -> vacío, no basura.
        shell = "Google's Maven Repository\n{{selectedGroupNode.text}}\nArtifacts (157)\n"

        class FakeFetcher:
            def get_rendered_markdown(self, url):
                return shell

        dep = cd.Dependency("com.google.android.gms:play-services-maps", {
            "version_used": "18.0.1", "latest_stable": "20.0.0", "type": "google",
            "url": "https://maven.google.com/web/index.html#com.google.android.gms",
        })
        url, notes = cd.GenericCrawlAdapter().notes_for(FakeFetcher(), dep, False)
        self.assertEqual(notes, [])  # sin cascarón


class TestRouterOrder(unittest.TestCase):
    def test_order_by_type(self):
        router = cd.SourceRouter()
        androidx_dep = cd.Dependency("androidx.core:core", {
            "version_used": "1.1.0", "latest_stable": "1.2.0", "type": "google", "url": "",
        })
        maven_dep = cd.Dependency("com.squareup.okhttp3:okhttp", {
            "version_used": "4.0.0", "latest_stable": "5.0.0", "type": "maven", "url": "",
        })
        self.assertIs(router.order_for(androidx_dep)[0], router.androidx)
        self.assertIs(router.order_for(maven_dep)[0], router.github)


class TestIntelHeuristic(unittest.TestCase):
    def test_extract_seeds_classifies(self):
        text = ("deprecated [`requestOfflineAccess`](https://x/reference/com/google/android/gms/"
                "auth/api/identity/AuthorizationRequest.Builder) and added [`setPrompt`] and "
                "`AuthorizationRequest.Prompt`; constant `GHP_STRUCTURE_ID`; `minSdk`.")
        seeds = cd.extract_seeds(text)
        self.assertIn("requestOfflineAccess", seeds["functions"])
        self.assertIn("setPrompt", seeds["functions"])
        self.assertIn("AuthorizationRequest.Prompt", seeds["types"])
        self.assertIn("com.google.android.gms.auth.api.identity", seeds["packages"])
        self.assertNotIn("GHP_STRUCTURE_ID", seeds["types"])   # constante = ruido
        self.assertNotIn("minSdk", seeds["functions"])          # ruido

    def test_classify_kind(self):
        self.assertEqual(cd.classify_kind("This is a breaking change"), "breaking")
        self.assertEqual(cd.classify_kind("Removed the old API"), "removal")
        self.assertEqual(cd.classify_kind("deprecated foo()"), "deprecation")
        self.assertEqual(cd.classify_kind("requires Java 17"), "requirement")
        self.assertEqual(cd.classify_kind("Added new bar()"), "feature")
        self.assertEqual(cd.classify_kind("Improved internal logging"), "behavior")

    def test_version_jump(self):
        self.assertEqual(cd.version_jump("4.2.2", "5.0.0"), "major")
        self.assertEqual(cd.version_jump("2.9.2", "2.11.0"), "minor")
        self.assertEqual(cd.version_jump("0.7.3", "0.7.14"), "patch")

    def test_effort(self):
        self.assertEqual(cd.guess_effort("major", [{"kind": "breaking"}]), "high")
        self.assertEqual(cd.guess_effort("major", [{"kind": "feature"}]), "medium")
        self.assertEqual(cd.guess_effort("minor", [{"kind": "removal"}]), "medium")
        self.assertEqual(cd.guess_effort("patch", [{"kind": "feature"}]), "low")

    def test_gates_only_explicit(self):
        intel = {
            "a:b": {"to": "6.0", "changes": [{"summary": "Now requires Java 17 to build"}]},
            "c:d": {"to": "1.0", "changes": [{"summary": "Tested against Java 25 in CI"}]},
            "e:f": {"to": "1.7", "changes": [{"summary": "minSdkVersion is now 23"}]},
        }
        gates = {g["requirement"] for g in cd.extract_gates(intel)}
        self.assertIn("Java 17+", gates)
        self.assertIn("minSdk >= 23", gates)
        self.assertNotIn("Java 25+", gates)   # mención suelta, no requisito

    def test_build_intel_entry(self):
        dep = cd.Dependency("com.squareup.okhttp3:okhttp", {
            "version_used": "4.2.2", "latest_stable": "5.0.0", "type": "maven", "alias": "core_okhttp"})
        result = {"coordinate": "com.squareup.okhttp3:okhttp", "source": "github",
                  "source_url": "https://github.com/square/okhttp/releases",
                  "versions": [{"version": "5.0.0", "date": "2025-07-02", "url": "u",
                                "notes": "Breaking change: removed `Foo`. Added `bar()`."}]}
        e = cd.build_intel_entry(dep, result)
        self.assertEqual(e["alias"], "core_okhttp")
        self.assertEqual(e["jump"], "major")
        self.assertEqual(e["effort"], "high")
        self.assertTrue(any(c["kind"] in ("breaking", "removal") for c in e["changes"]))
        self.assertEqual(e["raw"], result["versions"])

    def test_impact_seeds_only_breaking(self):
        changes = [
            {"kind": "deprecation", "apis": ["oldMethod", "newMethod"], "replacement": "newMethod"},
            {"kind": "removal", "apis": ["Foo"], "replacement": None},
            {"kind": "feature", "apis": ["Bar"], "replacement": None},   # NO debe entrar
        ]
        imp = cd.impact_seeds_from_changes(changes)
        self.assertIn("oldMethod", imp["functions"])       # el viejo, sí
        self.assertNotIn("newMethod", imp["functions"])    # el reemplazo, no
        self.assertIn("Foo", imp["types"])                 # removido, sí
        self.assertNotIn("Bar", imp["types"])              # feature, no

    def test_select_targets_scope(self):
        intel = {
            "x:major": {"jump": "major", "raw": [{}]},
            "y:minor": {"jump": "minor", "raw": [{}]},
            "z:nodata": {"jump": "major", "raw": []},
        }
        self.assertEqual(set(cd._select_targets(intel, "major", [])), {"x:major"})
        self.assertEqual(set(cd._select_targets(intel, "all", [])), {"x:major", "y:minor"})
        self.assertEqual(set(cd._select_targets(intel, "major", ["y:minor"])), {"y:minor"})

    def test_compare_intel_delta(self):
        heur = {"dependencies": {"a:b": {"seeds": {"packages": [], "types": ["X", "NOISE"], "functions": []},
                                         "changes": [{"kind": "behavior"}]}}}
        llm = {"dependencies": {"a:b": {"enriched": True,
                                        "seeds": {"packages": [], "types": ["X"], "functions": []},
                                        "changes": [{"kind": "deprecation", "replacement": "Y"}]}}}
        delta = cd.compare_intel(heur, llm)
        self.assertEqual(delta["deps_enriched"], 1)
        self.assertEqual(delta["replacements_added"], 1)
        self.assertEqual(delta["seeds_cleaned"], 1)
        self.assertEqual(delta["kinds_refined"], 1)


class TestFixPlanRegressions(unittest.TestCase):
    """Tests que cubren los 11 hallazgos del FIX-PLAN.md."""

    # 1.1 — _PRERELEASE_RE anclado
    def test_is_prerelease_release_tag(self):
        self.assertFalse(cd.is_prerelease("release/2.9.1"))

    def test_is_prerelease_release_dash_tag(self):
        self.assertFalse(cd.is_prerelease("release-2.9.1"))

    def test_is_prerelease_opensearch(self):
        self.assertFalse(cd.is_prerelease("opensearch-2.1.0"))

    def test_is_prerelease_spring_RELEASE(self):
        self.assertFalse(cd.is_prerelease("4.3.30.RELEASE"))

    def test_is_prerelease_still_detects_real(self):
        self.assertTrue(cd.is_prerelease("1.0.0-alpha01"))
        self.assertTrue(cd.is_prerelease("1.0.0-rc1"))
        self.assertTrue(cd.is_prerelease("2.0.0-SNAPSHOT"))
        self.assertTrue(cd.is_prerelease("3.0.0-beta.2"))
        self.assertTrue(cd.is_prerelease("1.0.0-M1"))
        self.assertTrue(cd.is_prerelease("1.0.0-ea"))

    # 1.2 — M<n> en version_sort_key
    def test_milestone_sorts_below_stable(self):
        self.assertLess(cd.version_sort_key("1.0.0-M1"), cd.version_sort_key("1.0.0"))

    def test_versions_in_range_keeps_stable_with_milestone(self):
        cands = ["1.0.0", "1.0.0-M1", "0.9.0"]
        got = cd.versions_in_range(cands, "0.9.0", "1.0.0", include_prereleases=True)
        self.assertIn("1.0.0", got)
        self.assertIn("1.0.0-M1", got)
        self.assertEqual(len(got), 2)

    # 1.3 — Dependency.usable with prerelease → stable
    def test_usable_prerelease_to_stable(self):
        dep = cd.Dependency("a:b", {
            "version_used": "1.0.0-alpha01", "latest_stable": "1.0.0",
            "type": "maven", "status_code": "prerelease",
        })
        self.assertTrue(dep.usable)

    # 2.1 — _normalize_change
    def test_normalize_change_valid(self):
        c = {"kind": "breaking", "summary": "Removed foo()", "apis": ["foo"], "replacement": "bar"}
        result = cd._normalize_change(c, "http://example.com")
        self.assertEqual(result["kind"], "breaking")
        self.assertEqual(result["summary"], "Removed foo()")
        self.assertEqual(result["apis"], ["foo"])
        self.assertEqual(result["replacement"], "bar")

    def test_normalize_change_missing_summary(self):
        self.assertIsNone(cd._normalize_change({"kind": "breaking"}, None))

    def test_normalize_change_invalid_apis(self):
        c = {"summary": "test", "apis": [123, None, "valid"]}
        result = cd._normalize_change(c, None)
        self.assertEqual(result["apis"], ["valid"])

    def test_normalize_change_invalid_replacement(self):
        c = {"summary": "test", "replacement": 42}
        result = cd._normalize_change(c, None)
        self.assertIsNone(result["replacement"])

    def test_normalize_change_unknown_kind(self):
        c = {"summary": "test", "kind": "magic"}
        result = cd._normalize_change(c, None)
        self.assertEqual(result["kind"], "behavior")

    # 2.1 — _apply_enrichment with deformed LLM output
    def test_apply_enrichment_deformed_changes(self):
        entry = {"source_url": "http://x", "changes": [], "impact_seeds": {},
                 "seeds": {"packages": [], "types": [], "functions": []}}
        enr = {"changes": ["not a dict", {"summary": "ok", "kind": "feature"}]}
        cd._apply_enrichment(entry, enr)
        self.assertEqual(len(entry["changes"]), 1)
        self.assertEqual(entry["changes"][0]["summary"], "ok")

    def test_apply_enrichment_deformed_seeds(self):
        entry = {"source_url": "http://x", "changes": [],
                 "seeds": {"packages": [], "types": [], "functions": []}}
        enr = {"seeds": {"types": "Foo", "functions": ["bar"], "packages": [123, "ok"]}}
        cd._apply_enrichment(entry, enr)
        self.assertEqual(entry["seeds"]["types"], [])
        self.assertEqual(entry["seeds"]["functions"], ["bar"])
        self.assertEqual(entry["seeds"]["packages"], ["ok"])

    # 2.2 — extract_gates tolerant
    def test_extract_gates_missing_summary(self):
        intel = {"a:b": {"to": "2.0", "changes": [{"kind": "requirement"}]}}
        cd.extract_gates(intel)  # should not raise

    # 3.1 — Cache opts_key
    def test_cache_different_opts_keys(self):
        dep = cd.Dependency("a:b", {
            "version_used": "1.0.0", "latest_stable": "2.0.0", "type": "maven"})
        c1 = cd.Cache(None, opts_key="pre=True")
        c2 = cd.Cache(None, opts_key="pre=False")
        # Both return None (no directory), but we can test _path with a temp dir
        with tempfile.TemporaryDirectory() as td:
            c1 = cd.Cache(td, opts_key="pre=True")
            c2 = cd.Cache(td, opts_key="pre=False")
            self.assertNotEqual(c1._path(dep), c2._path(dep))

    # 4.1 — KnownSourceAdapter passes include_prereleases
    def test_firebase_passes_include_prereleases(self):
        md = (
            "### Firebase Android BoM (Bill of Materials) version 34.19.0\n"
            "Stuff.\n"
            "### Firebase Android BoM (Bill of Materials) version 34.10.0-beta01\n"
            "Beta stuff.\n"
        )
        dep = cd.Dependency("com.google.firebase:firebase-bom", {
            "version_used": "34.8.0", "latest_stable": "34.19.0", "type": "google", "url": "",
        })

        class FakeFetcher:
            def get_rendered_markdown(self, url):
                return md

        adapter = cd.KnownSourceAdapter()
        # Without prereleases, beta should be excluded by in_range
        _, notes_no_pre = adapter.notes_for(FakeFetcher(), dep, False)
        vers_no_pre = [n.version for n in notes_no_pre]
        self.assertIn("34.19.0", vers_no_pre)
        self.assertNotIn("34.10.0-beta01", vers_no_pre)

    # 4.2 — Empty output with --format intel
    def test_build_intel_document_empty(self):
        doc = cd.build_intel_document({}, [], summarize=False, scope="major",
                                      only=[], model="x", api_key=None)
        self.assertEqual(doc["schema"], "deps-changelog-diff/change-intel-1")
        self.assertEqual(doc["meta"]["totals"]["analyzed"], 0)
        md = cd.render_intel_markdown(doc)
        self.assertIn("0 analyzed", md)

    # GitHub adapter with release/ tags (integration-level)
    def test_github_adapter_release_slash_tag(self):
        releases = [
            {"tag_name": "release/2.9.1", "prerelease": False,
             "published_at": "2025-01-01T00:00:00Z",
             "html_url": "http://x", "body": "Notes for 2.9.1"},
            {"tag_name": "release/2.8.0", "prerelease": False,
             "published_at": "2024-06-01T00:00:00Z",
             "html_url": "http://y", "body": "Notes for 2.8.0"},
        ]

        class FakeFetcher:
            def get_text(self, url, headers=None):
                pom = ('<?xml version="1.0"?><project xmlns="http://maven.apache.org/POM/4.0.0">'
                       '<scm><url>https://github.com/owner/repo</url></scm></project>')
                return pom

            def get_json(self, url):
                if "releases" in url:
                    return releases
                return None

        dep = cd.Dependency("com.example:lib", {
            "version_used": "2.7.0", "latest_stable": "2.9.1",
            "type": "maven", "url": "",
        })
        adapter = cd.GitHubAdapter(max_releases=100)
        result = adapter.notes_for(FakeFetcher(), dep, include_prereleases=False)
        self.assertIsNotNone(result)
        url, notes = result
        vers = [n.version for n in notes]
        self.assertIn("2.9.1", vers)
        self.assertIn("2.8.0", vers)


class TestLLMIntegration(unittest.TestCase):
    """Tests for the LLM module (mocked — no API key needed)."""

    def _make_mock_response(self, parsed_output):
        resp = MagicMock()
        resp.parsed_output = parsed_output
        return resp

    @patch("changelog_diff.llm.anthropic.Anthropic")
    def test_summarize_returns_valid_dict(self, MockAnthropic):
        from changelog_diff.llm import summarize_with_llm, SummaryResult

        fake_result = SummaryResult(
            breaking_changes=["Removed setFoo() method"],
            deprecations=["Bar class deprecated"],
            new_features=["Added Baz support"],
            security_fixes=[],
            migration_effort="medium",
            migration_notes="Replace setFoo() with configure(). Update Bar usages.",
            tldr="Breaking removal of setFoo(), new Baz API added.",
        )
        client = MockAnthropic.return_value
        client.messages.parse.return_value = self._make_mock_response(fake_result)

        result = summarize_with_llm(
            "com.example:lib", "1.0.0", "2.0.0",
            "## 2.0.0\n- Removed setFoo()\n- Deprecated Bar\n- Added Baz",
            "claude-sonnet-5", "fake-key",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["breaking_changes"], ["Removed setFoo() method"])
        self.assertEqual(result["migration_effort"], "medium")
        self.assertIsInstance(result["new_features"], list)
        self.assertIsInstance(result["security_fixes"], list)
        self.assertEqual(len(result["security_fixes"]), 0)

        call_kwargs = client.messages.parse.call_args.kwargs
        self.assertEqual(call_kwargs["output_format"], SummaryResult)
        self.assertIn("com.example:lib", call_kwargs["messages"][0]["content"])

    @patch("changelog_diff.llm.anthropic.Anthropic")
    def test_enrich_intel_returns_valid_dict(self, MockAnthropic):
        from changelog_diff.llm import enrich_intel_with_llm, IntelResult, Change, Seeds

        fake_result = IntelResult(
            changes=[
                Change(
                    kind="removal",
                    summary="Removed setFoo() method",
                    apis=["setFoo"],
                    replacement="configure",
                    version="2.0.0",
                ),
                Change(
                    kind="deprecation",
                    summary="Bar class deprecated in favor of Baz",
                    apis=["Bar"],
                    replacement="Baz",
                    version="1.9.0",
                ),
            ],
            seeds=Seeds(packages=["com.example.lib"], types=["Bar"], functions=["setFoo"]),
            effort="medium",
        )
        client = MockAnthropic.return_value
        client.messages.parse.return_value = self._make_mock_response(fake_result)

        result = enrich_intel_with_llm(
            "com.example:lib", "1.0.0", "2.0.0",
            "## 2.0.0\n- Removed setFoo()\n- Deprecated Bar, use Baz",
            "claude-sonnet-5", "fake-key",
        )

        self.assertIsNotNone(result)
        self.assertEqual(len(result["changes"]), 2)
        self.assertEqual(result["changes"][0]["kind"], "removal")
        self.assertEqual(result["changes"][0]["apis"], ["setFoo"])
        self.assertEqual(result["changes"][0]["replacement"], "configure")
        self.assertEqual(result["seeds"]["types"], ["Bar"])
        self.assertEqual(result["effort"], "medium")

    @patch("changelog_diff.llm.anthropic.Anthropic")
    def test_api_error_returns_none(self, MockAnthropic):
        from changelog_diff.llm import summarize_with_llm
        import anthropic

        client = MockAnthropic.return_value
        client.messages.parse.side_effect = anthropic.APIError(
            message="Unauthorized", request=MagicMock(), body=None,
        )

        result = summarize_with_llm(
            "com.example:lib", "1.0.0", "2.0.0", "notes",
            "claude-sonnet-5", "bad-key",
        )
        self.assertIsNone(result)

    @patch("changelog_diff.llm.anthropic.Anthropic")
    def test_none_parsed_output_returns_none(self, MockAnthropic):
        from changelog_diff.llm import enrich_intel_with_llm

        client = MockAnthropic.return_value
        client.messages.parse.return_value = self._make_mock_response(None)

        result = enrich_intel_with_llm(
            "com.example:lib", "1.0.0", "2.0.0", "notes",
            "claude-sonnet-5", "fake-key",
        )
        self.assertIsNone(result)

    @patch("changelog_diff.llm.anthropic.Anthropic")
    def test_enrich_result_compatible_with_apply_enrichment(self, MockAnthropic):
        """Verify the dict shape from enrich_intel_with_llm works with _apply_enrichment."""
        from changelog_diff.llm import enrich_intel_with_llm, IntelResult, Change, Seeds

        fake_result = IntelResult(
            changes=[
                Change(
                    kind="breaking",
                    summary="Removed deprecated OldApi class",
                    apis=["OldApi"],
                    replacement="NewApi",
                    version="3.0.0",
                ),
            ],
            seeds=Seeds(packages=["com.example"], types=["OldApi"], functions=[]),
            effort="high",
        )
        client = MockAnthropic.return_value
        client.messages.parse.return_value = self._make_mock_response(fake_result)

        enr = enrich_intel_with_llm(
            "com.example:lib", "2.0.0", "3.0.0", "notes",
            "claude-sonnet-5", "fake-key",
        )

        entry = {
            "changes": [],
            "seeds": {"packages": [], "types": [], "functions": []},
            "effort": "low",
            "source_url": "https://example.com",
        }
        cd._apply_enrichment(entry, enr)

        self.assertTrue(entry["enriched"])
        self.assertEqual(entry["effort"], "high")
        self.assertEqual(entry["changes"][0]["kind"], "breaking")
        self.assertEqual(entry["changes"][0]["replacement"], "NewApi")
        self.assertEqual(entry["seeds"]["types"], ["OldApi"])
        self.assertIn("OldApi", entry["impact_seeds"]["types"])

    def test_pydantic_models_reject_invalid_kind(self):
        from changelog_diff.llm import Change
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            Change(
                kind="invalid_kind",
                summary="test",
                apis=[],
                replacement=None,
                version=None,
            )

    def test_pydantic_models_reject_invalid_effort(self):
        from changelog_diff.llm import SummaryResult
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            SummaryResult(
                breaking_changes=[],
                deprecations=[],
                new_features=[],
                security_fixes=[],
                migration_effort="extreme",
                migration_notes="",
                tldr="",
            )

    @patch("changelog_diff.llm.anthropic.Anthropic")
    def test_prompt_includes_dependency_info(self, MockAnthropic):
        from changelog_diff.llm import summarize_with_llm, SummaryResult

        fake_result = SummaryResult(
            breaking_changes=[], deprecations=[], new_features=[],
            security_fixes=[], migration_effort="low",
            migration_notes="Nothing to do.", tldr="Patch release.",
        )
        client = MockAnthropic.return_value
        client.messages.parse.return_value = self._make_mock_response(fake_result)

        summarize_with_llm(
            "androidx.core:core", "1.9.0", "1.12.0",
            "Bug fixes and improvements",
            "claude-sonnet-5", "fake-key",
        )

        content = client.messages.parse.call_args.kwargs["messages"][0]["content"]
        self.assertIn("androidx.core:core", content)
        self.assertIn("1.9.0", content)
        self.assertIn("1.12.0", content)
        self.assertIn("Bug fixes and improvements", content)

    @patch("changelog_diff.llm.anthropic.Anthropic")
    def test_notes_truncated_at_60k_chars(self, MockAnthropic):
        from changelog_diff.llm import summarize_with_llm, SummaryResult

        fake_result = SummaryResult(
            breaking_changes=[], deprecations=[], new_features=[],
            security_fixes=[], migration_effort="low",
            migration_notes="", tldr="",
        )
        client = MockAnthropic.return_value
        client.messages.parse.return_value = self._make_mock_response(fake_result)

        huge_notes = "Z" * 100000
        summarize_with_llm(
            "com.test:lib", "1.0.0", "2.0.0", huge_notes,
            "claude-sonnet-5", "fake-key",
        )

        content = client.messages.parse.call_args.kwargs["messages"][0]["content"]
        z_count = content.count("Z")
        self.assertEqual(z_count, 60000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
