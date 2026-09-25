# deps-changelog-diff 📜

Analiza **qué cambia** entre la versión que usas y la última estable de cada dependencia,
leyendo la documentación oficial de cada proyecto (GitHub Releases, AndroidX, Firebase,
Play services, Places…).

Es el **segundo paso** de [`toml-deps-checker`](https://github.com/pfranccino/toml-deps-checker):
mientras ese te dice *qué tan atrasada* está cada dependencia, este te dice *qué cambió*.

No vuelve a consultar Maven: consume el `dependency_status.json` que genera el primer
script y, por cada dependencia desactualizada (🟡/🔴), recolecta las notas de versión.

## 🧭 De dónde saca las notas (router de fuentes)

| Tipo | Fuente | Cómo |
|---|---|---|
| `maven`, `plugin` | **GitHub Releases API** | Descubre el repo desde el `<scm>`/`<url>` del POM (sigue el POM **padre** en proyectos multi-módulo) y baja las notas por tag, en Markdown. |
| `google` (AndroidX) | **crawl4AI** sobre developer.android.com | Página por grupo con encabezados `### Version X.Y.Z`. crawl4AI renderiza; si no está instalado, cae a un parseo de HTML estático (`requests`). |
| Firebase / Play services / Places | **páginas oficiales** | `firebase-bom` (release-notes de Firebase), `play-services-*` (guía de releases de Play services) y `places` (release-notes del Places SDK). |
| cualquiera (fallback) | **crawl4AI** | Solo para webs de terceros con mucho JS. Si no trocea versiones reales, devuelve **vacío (`source: null`)** — nunca guarda el cascarón de una página, para no dar falsa expectativa. |

Solo se comparan **versiones estables** (sin alphas/betas/rc), y el rango es
`(tu_versión, última_estable]`: todas las estables intermedias, para no perderte un
breaking change que se coló en medio.

## 🚀 Uso

```bash
# 1) Clona y ejecuta toml-deps-checker para generar dependency_status.json
#    → https://github.com/pfranccino/toml-deps-checker
git clone https://github.com/pfranccino/toml-deps-checker.git
cd toml-deps-checker
./check-dependencies.sh /ruta/a/tu/proyecto/gradle
# Esto genera dependency_status.json con el estado de cada dependencia.

# 2) Luego, en este repo, el análisis de cambios
./check-changelog.sh                                   # usa ./dependency_status.json
./check-changelog.sh --md changelog.md                 # además, un Markdown legible
./check-changelog.sh ./ruta/dependency_status.json -o cambios.json
```

### 🪟 En Windows (PowerShell), sin el wrapper de bash

El `.sh` puede fallar por saltos de línea CRLF. Corre el Python directo:

```powershell
pip install -r requirements.txt
python -m playwright install chromium        # navegador que usa crawl4AI (una vez)
python changelog-diff.py dependency_status.json --md changelog.md --github-token TU_TOKEN
```

> **Usa un token de GitHub.** Sin token, la API te da ~60 peticiones/hora y con decenas de
> dependencias lo agotas (verás notas vacías por rate limit). Un
> [token clásico](https://github.com/settings/tokens) sin ningún scope basta para leer
> repos públicos. Pásalo con `--github-token` o la variable `GITHUB_TOKEN`.

## 📤 Salida

`changelog_diff.json`, una entrada por coordenada:

```json
{
  "com.squareup.okhttp3:okhttp": {
    "type": "maven",
    "from": "4.12.0",
    "to": "5.0.0",
    "source": "github",
    "source_url": "https://github.com/square/okhttp/releases",
    "versions": [
      { "version": "5.0.0", "date": "2025-07-02", "url": "...", "notes": "..." }
    ]
  }
}
```

Por defecto `notes` es el **texto crudo** de las notas oficiales, una entrada por versión
estable del rango. No es un resumen (salvo que uses `--summarize`).

## 🔌 Formato `intel` (change-intel-1) — para un plugin de impacto

Con `--format intel` el output deja de ser prosa y pasa a **inteligencia de cambios**
pensada para que un plugin busque el impacto en tu repo (p. ej. con el Android CLI):

```bash
# Gratis: heurística (semillas + gates + kind aproximado)
python changelog-diff.py dependency_status.json --format intel -o intel.json

# Enriquecido: agrega replacement (old→new), kind fino y effort con LLM
python changelog-diff.py dependency_status.json --format intel --summarize -o intel.json
```

Cada dependencia trae:

```json
{
  "alias": "google_services_auth",
  "from": "16.0.1", "to": "22.0.0", "jump": "major",
  "effort": "high", "confidence": "high",
  "seeds": {
    "packages": ["com.google.android.gms.auth.api.identity"],
    "types": ["AuthorizationRequest", "GoogleSignInClient"],
    "functions": ["requestOfflineAccess", "setPrompt", "getPrompt"]
  },
  "impact_seeds": {
    "packages": [], "types": [], "functions": ["requestOfflineAccess"]
  },
  "changes": [
    { "kind": "deprecation", "summary": "requestOfflineAccess() deprecado; usar setPrompt()",
      "apis": ["AuthorizationRequest.Builder.requestOfflineAccess"],
      "replacement": "setPrompt", "version": "21.5.0", "ref": "..." }
  ],
  "raw": [ /* las notas por versión, para auditar */ ]
}
```

- **`seeds`** = red amplia de todo lo mencionado (paquetes/tipos/funciones), gratis desde
  los backticks y enlaces del changelog.
- **`impact_seeds`** = subconjunto que **rompe** (solo cambios `breaking`/`removal`/
  `deprecation`), excluyendo el reemplazo. Es lo que el plugin debe buscar sí o sí. Sale
  aproximado con heurística y **limpio con `--summarize`**.
- **`changes[].replacement`** = la relación vieja→nueva (lo aporta el `--summarize`).
- **`project_gates`** (arriba del doc) = requisitos de build (Java 17, minSdk N).
- **`effort` / `confidence`** = para priorizar y saber cuándo desconfiar del dato.

El plugin pone el **dónde** (busca en el repo); este output pone el **qué, por qué y qué
tan grave**.

### Dos capas y comparación de costo

- **Heurística (gratis):** `seeds`, `gates`, `kind` aproximado, `summary` crudo. Se ejecuta
  siempre.
- **LLM (`--summarize`):** agrega `replacement`, afina `kind`/`effort` y limpia ruido. Se
  **deduplica por repo** (kotlin ×5, ktor ×7 → 1 llamada) y se acota con
  `--summarize-scope major|all` o `--summarize-only "a,b"`. Clasificar todos los majors
  cuesta centavos.
- **`--compare`:** corre las dos y reporta, sin costo extra de comparación, cuántos
  `replacement` agregó el LLM, cuántos `seeds` limpió y cuántos `kind` refinó — para medir
  qué te dio el LLM por lo que pagaste.

## 🤖 `--summarize` en formato raw

En `--format raw`, `--summarize` agrega un `summary` legible (breaking/deprecaciones/…)
por dependencia. Necesita `ANTHROPIC_API_KEY`; sin ella sale solo el crudo.

## 💾 Caché

Los changelogs son inmutables por versión, así que se cachean por
`(coordenada, versión_actual..última)` en `.changelog-cache/` (configurable con
`--cache-dir`; `none` lo desactiva). **Al actualizar el script, borra la caché** o usa
`--cache-dir none`: la caché no se entera de que cambió el código.

## 🎛️ Flags

| Flag | Para qué |
|---|---|
| `--format raw\|intel` | `raw` = notas crudas (por defecto). `intel` = esquema change-intel-1. |
| `--summarize` | Enriquece con LLM (necesita `ANTHROPIC_API_KEY`). |
| `--summarize-scope major\|all` | A qué deps aplica el LLM en `intel` (por defecto solo majors). |
| `--summarize-only "a,b"` | Lista de coordenadas a enriquecer (ignora el scope). |
| `--compare` | Corre heurístico vs LLM (intel) y reporta el delta. |
| `--all` | Analiza todas las deps, no solo las desactualizadas. |
| `--include-prereleases` | Incluye alphas/betas/rc intermedias (por defecto solo estables). |
| `--model` | Modelo del LLM (o `ANTHROPIC_MODEL`). |
| `--github-token` | Token de GitHub para el rate limit (o `GITHUB_TOKEN`). |
| `--androidx-lang` | Idioma de AndroidX vía `?hl=` (por defecto `es-419`; `en` = inglés). |
| `--no-crawl4ai` | No uses crawl4AI (evita Playwright; AndroidX usa parseo de HTML). |
| `--cache-dir` | Directorio de caché; `none` lo desactiva. |
| `-o/--output`, `--md`, `-j/--jobs`, `--timeout`, `-q` | Salida, paralelismo, timeout, silencio. |

## 🤖 En CI

Hay un ejemplo en [`.github/workflows/changelog.yml`](.github/workflows/changelog.yml).
En CI conviene `--no-crawl4ai` (evita Playwright) y pasar `GITHUB_TOKEN`.

## 🧪 Tests

Funciones puras (versiones, tags, slugs, troceo, descubrimiento de repo, fuentes
conocidas) cubiertas sin tocar la red:

```bash
python -m unittest test_changelog_diff -v
```

## ⚠️ Límites conocidos

- **`null` honesto:** las binarias sin changelog público por versión —`AGP`,
  `google-services`, `googleid`, `integrity`, `appsflyer`, `nimbus-jose-jwt`— salen con
  `source: null` y sin notas. Preferimos `null` antes que un cascarón que engañe.
- **Firebase** trae la *tabla* de qué artefacto subió por versión de BOM, no el detalle de
  cada librería (la página remite a las notas de cada una).
- **`places`** puede volver `null` según cómo renderice su página (extractor pendiente de
  afinar — ver roadmap).
- **`okhttp`** puede colapsar en una sola nota (el CHANGELOG entero) si su repo publica
  pocas *releases*; el contenido está, pero mal versionado (ver roadmap).
- Los extractores de Firebase/Play services/Places dependen de la estructura de esas
  páginas oficiales; si Google las reestructura, pueden dejar de casar.

## 🗺️ Roadmap

- [x] **Esquema `change-intel-1`** (`--format intel`): `seeds`, `changes`, `project_gates`,
      `effort`, `confidence`, `raw`.
- [x] **Heurística gratis** de `seeds` (backticks + links) y `kind` por regex.
- [x] **Dedup por repo+rango** antes de sumarizar (kotlin ×5, ktor ×7 → 1 llamada).
- [x] **`--summarize` acotado** (`--summarize-scope`, `--summarize-only`) y **`--compare`**.
- [ ] Afinar extractor de **Places** y descubrimiento de repo de **okhttp** (evitar forks).
- [ ] Mejorar `project_gates` (más patrones de requisito) y clasificación heurística de `kind`.
- [ ] Emitir un `triage` de alto nivel (top por esfuerzo) en el `meta`.

## 📄 Licencia

MIT © Paul Ayala. Ver [LICENSE](LICENSE).
