# Plan de corrección — deps-changelog-diff

Revisión de `changelog-diff.py`, `check-changelog.sh`, `requirements.txt` y
`.github/workflows/changelog.yml`. 11 hallazgos reproducidos ejecutando el código.
Los 34 tests actuales pasan: ninguno cubre estos casos.

> Nota: el directorio **no es un repo git**, así que no hubo diff que revisar.
> El alcance fue el código completo.

---

## Resumen por prioridad

| # | Prioridad | Archivo:línea | Problema | Esfuerzo |
|---|-----------|---------------|----------|----------|
| 1 | 🔴 Alta | `changelog-diff.py:75` | `_PRERELEASE_RE` sin límites de palabra: "release" contiene "ea" | S |
| 3 | 🔴 Alta | `changelog-diff.py:1052` | `extract_gates` revienta con `KeyError` por JSON del LLM | S |
| 4 | 🟠 Media | `changelog-diff.py:1080` | `AttributeError` por `apis` no-string del LLM | S |
| 2 | 🟠 Media | `changelog-diff.py:110` | `M1` colisiona con el estable y lo desplaza | S |
| 6 | 🟠 Media | `changelog-diff.py:731` | `usable` descarta justo los `prerelease` | S |
| 5 | 🟠 Media | `changelog-diff.py:767` | Clave de caché ignora los flags de rango | S |
| 7 | 🟠 Media | `changelog-diff.py:1385` | `suppress(Exception)` oculta deps caídas | S |
| 8 | 🟡 Baja | `changelog-diff.py:645` | `--include-prereleases` ignorado en Firebase/Play | XS |
| 9 | 🟡 Baja | `changelog-diff.py:1357` | Salida `{}` rompe el esquema `intel` | XS |
| 10 | 🟡 Baja | `check-changelog.sh:12` | `WITH_CRAWL4AI` documentado pero inexistente | XS |
| 11 | 🟡 Baja | `changelog-diff.py:174` | `requests.Session` compartida entre hilos | M |

---

## Fase 1 — Correcciones de versionado (bloquean resultados correctos)

### 1.1 Anclar `_PRERELEASE_RE` (`changelog-diff.py:75`) 🔴

**Síntoma:** `is_prerelease("release/2.9.1") → True` (el `ea` de r-el-**ea**-se),
`is_prerelease("4.3.30.RELEASE") → True`, `is_prerelease("opensearch-2.1.0") → True`
(el `rc` de sea-**rc**-h).

**Impacto real:** `GitHubAdapter.notes_for:544` llama `is_prerelease(tag)` sobre el tag
crudo. Todo repo que etiquete `release/X.Y.Z` o `release-X.Y.Z` pierde **el 100 %** de
sus releases y reporta "sin notas". Es el mismo formato que usa el fixture propio del
proyecto en `test_changelog_diff.py:38`. También elimina coordenadas estilo Spring
(`4.3.30.RELEASE`).

**Fix:** exigir un separador o límite de palabra antes del cualificador.

```python
_PRERELEASE_RE = re.compile(
    r"(?i)(?:^|[-._+])(alpha|beta|rc|cr|milestone|m\d+|snapshot|preview|eap|dev|pre|ea)"
    r"(?![a-z])"
)
```

**Tests a añadir:**
- `is_prerelease("release/2.9.1")` → `False`
- `is_prerelease("opensearch-2.1.0")` → `False`
- `is_prerelease("4.3.30.RELEASE")` → `False`
- Mantener verdes los casos existentes (`-alpha01`, `-rc1`, `-SNAPSHOT`)
- Test de integración de `GitHubAdapter` con fetcher falso y tags `release/X.Y.Z`

---

### 1.2 Añadir la forma `M<n>` a `_QUALIFIERS` (`changelog-diff.py:110`) 🟠

**Síntoma:** `version_sort_key("1.0.0-M1") == version_sort_key("1.0.0")`.
`_PRERELEASE_RE` reconoce `-m\d` pero `_QUALIFIERS` no tiene entrada equivalente.

**Impacto real:** con `--include-prereleases`, la deduplicación de
`versions_in_range:161` ("nos quedamos con la forma más larga") **tira el estable y
conserva el milestone**:

```python
versions_in_range(['1.0.0', '1.0.0-M1', '0.9.0'], '0.9.0', '1.0.0',
                  include_prereleases=True)
# → ['1.0.0-M1']   ← se perdieron las notas de 1.0.0
```

**Fix:** añadir `("m", BETA)` al final de `_QUALIFIERS` (después de `milestone`, y
ojo con el orden: `milestone` ya va antes). Verificar que el `rest.find()` no
capture la `m` de otra palabra — conviene migrar el bucle a una regex anclada en
separador, igual que en 1.1, en vez de `find()` sobre subcadena.

**Riesgo:** `version_sort_key` usa `rest.find(qualifier)` y rompe en el **primer
cualificador del orden de la tupla**, no en el primero del string. Al tocar esto,
aprovechar para hacerlo determinista (buscar todos y quedarse con el de menor índice).

**Tests:**
- `version_sort_key("1.0.0-M1") < version_sort_key("1.0.0")`
- `versions_in_range([...], include_prereleases=True)` conserva `1.0.0` **y** `1.0.0-M1`

---

### 1.3 `Dependency.usable` con `version_sort_key` (`changelog-diff.py:731`) 🟠

**Síntoma:** compara solo el núcleo numérico vía `version_tuple`, que descarta el
sufijo. Para `1.0.0-alpha01 → 1.0.0` ambos lados son `(1,0,0)`, la comparación es
falsa y `load_dependencies` descarta la entrada.

**Impacto real:** `OUTDATED_CODES` (línea 60) incluye `"prerelease"`, pero ese es
exactamente el caso que `usable` elimina. El usuario no recibe ninguna señal de por qué
falta la dependencia.

**Fix:**

```python
return bool(... and version_sort_key(self.version_used) < version_sort_key(self.latest_stable))
```

**Test:** `Dependency` con `version_used="1.0.0-alpha01"`, `latest_stable="1.0.0"`,
`status_code="prerelease"` → `usable is True` y sobrevive a `load_dependencies`.

---

## Fase 2 — Robustez frente a la salida del LLM

Ambos hallazgos comparten causa raíz: **`_apply_enrichment:1179` asigna el `changes`
crudo del modelo sin validar nada**, y el fallo ocurre *después* de pagar todas las
llamadas.

### 2.1 Validar/normalizar el JSON del LLM (`changelog-diff.py:1174`) 🔴

Introducir un normalizador antes de aceptar la respuesta:

```python
_VALID_KINDS = {"breaking", "removal", "deprecation", "requirement",
                "security", "feature", "behavior"}

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
        "version": c.get("version") if isinstance(c.get("version"), str) else None,
        "ref": c.get("ref") or default_ref,
    }
```

Y en `_apply_enrichment`, filtrar `None` y **no sobrescribir** la heurística si no
queda ningún cambio válido. Hacer lo mismo con `seeds` (línea 1184): comprobar que
cada valor sea una lista de strings antes del `set(...)` — hoy un string da un set de
caracteres y una lista anidada lanza `TypeError: unhashable type`.

**Cierra los hallazgos:**
- **#3** `extract_gates:1052` → `KeyError: 'summary'` (reproducido)
- **#4** `impact_seeds_from_changes:1080` → `AttributeError: 'int' object has no
  attribute 'endswith'` (reproducido)
- **#3b** `render_intel_markdown:1302` → mismo riesgo vía `c['kind']` / `c['summary']`

### 2.2 Defensa en profundidad

Aunque 2.1 cubra el origen, usar acceso tolerante en los consumidores para que un
cambio futuro no vuelva a tirar la corrida entera:
- `extract_gates:1052` → `c.get("summary", "")`
- `render_intel_markdown:1300-1302` → `.get()` con defaults

**Tests:** alimentar `_apply_enrichment` con respuestas deformes
(`summary` ausente, `apis=[123]`, `seeds={"types": "Foo"}`, `changes="texto"`) y
verificar que `build_intel_document` completa y devuelve un documento válido.

---

## Fase 3 — Caché, concurrencia y observabilidad

### 3.1 Incluir los flags de rango en la clave de caché (`changelog-diff.py:767`) 🟠

**Síntoma:** `_path` solo usa `coordenada@usada..última`. Una corrida normal seguida de
otra con `--include-prereleases` hace *cache hit* en todas las dependencias, nunca
llama a `router.resolve`, y devuelve resultados sin pre-releases mientras imprime
`💾 ... (caché)`. `--androidx-lang` y `--max-releases` tienen el mismo problema.

**Fix:** añadir un sufijo corto derivado de las opciones que afectan al rango/fuente.

```python
def _path(self, dep):
    if not self.directory:
        return None
    fingerprint = hashlib.sha1(self.opts_key.encode()).hexdigest()[:8]
    safe = re.sub(r"[^\w.\-]", "_", f"{dep.coordinate}@{dep.version_used}..{dep.latest_stable}")
    return os.path.join(self.directory, f"{safe}.{fingerprint}.json")
```

donde `opts_key` se construye en `main` con
`include_prereleases`, `androidx_lang`, `max_releases`.

### 3.2 Dejar de tragarse las excepciones por dependencia (`changelog-diff.py:1385`) 🟠

Hoy `with contextlib.suppress(Exception): results.append(future.result())` descarta en
silencio cualquier dependencia cuyo worker haya lanzado. El resumen final
(`✨ Listo: N/N`) se calcula sobre el `N` ya truncado, así que una salida incompleta es
indistinguible de una completa.

```python
for future in concurrent.futures.as_completed(futures):
    dep = futures[future]
    try:
        results.append(future.result())
    except Exception as exc:
        failed += 1
        log(f"   ❌ {dep.coordinate}: {type(exc).__name__}: {exc}")
```

Y reportar `failed` en el log final (y considerar un código de salida ≠ 0 si todas
fallan).

### 3.3 Sesión HTTP por hilo (`changelog-diff.py:174`) 🟡

`Fetcher` crea una sola `requests.Session` y `main:1380` la comparte entre hasta
`--jobs` hilos (6 por defecto). `requests.Session` no está documentada como
thread-safe: el pool de conexiones y el cookie jar pueden corromperse, produciendo
fallos intermitentes que — hasta arreglar 3.2 — quedaban completamente ocultos.

**Fix:** `threading.local()` con una sesión por hilo, o un `Fetcher` por worker.
Aprovechar para proteger también `_crawler_checked` / `_crawl4ai_ok` con un lock.

---

## Fase 4 — Consistencia de flags, salidas y empaquetado

### 4.1 Propagar `include_prereleases` en `KnownSourceAdapter` (`:645`, `:650`, `:671`) 🟡

`notes_for` recibe el flag y solo lo reenvía en la rama `"slice"` (línea 636).
`_firebase` (líneas 645 y 650) y `_playservices` (línea 671) llaman a
`versions_in_range` / `in_range` sin él, así que cae al `False` por defecto.
Un `--include-prereleases` sobre Firebase BoM o `play-services-*` devuelve solo
estables, sin aviso. Pasar el parámetro por ambas funciones auxiliares.

### 4.2 Salida vacía que respete `--format` (`changelog-diff.py:1357`) 🟡

El atajo de "sin dependencias" escribe `{}` y sale 0, sin importar `--format intel`
ni `--md`. Un consumidor del esquema `change-intel-1` falla al leer `doc["meta"]`, y
el paso `upload-artifact` del workflow (que lista `changelog.md` en
`.github/workflows/changelog.yml:43`) apunta a un fichero inexistente.

**Fix:** emitir el documento vacío **en la forma pedida** (reutilizar
`build_intel_document` con listas vacías cuando `--format intel`) y escribir siempre
el `--md` si se pidió.

### 4.3 Arreglar el opt-in de crawl4AI (`check-changelog.sh:12`, `:37-45`) 🟡

La cabecera anuncia `WITH_CRAWL4AI=1 ./check-changelog.sh` y el comentario de las
líneas 37-38 afirma que crawl4AI "se instala solo si pides WITH_CRAWL4AI=1", pero
**la variable no se lee en ninguna parte**. Las líneas 41-46 instalan
`requirements.txt` incondicionalmente, y `requirements.txt:11` fija
`crawl4ai>=0.4.0`. Todos los usuarios se llevan Playwright quieran o no. La única
puerta existente es `SKIP_CRAWL4AI` (línea 54), que controla la descarga del
navegador, no el paquete.

**Elegir una de las dos:**
- **(a)** Implementar de verdad el opt-in: sacar `crawl4ai` de `requirements.txt` a un
  `requirements-crawl4ai.txt`, e instalarlo solo si `WITH_CRAWL4AI=1`.
- **(b)** Aceptar que es obligatorio y corregir la documentación (cabecera + comentarios
  + README), dejando solo `SKIP_CRAWL4AI`.

Recomendada: **(a)**, porque el workflow de CI ya instala solo `requests`
(`.github/workflows/changelog.yml:27`) y usa `--no-crawl4ai`.

**Además:** `requirements.txt:7` indica `pip install -r requirements-changelog.txt`,
un fichero que no existe en el repo.

---

## Menor (opcional)

- `version_jump:1009` — `if not a or not b` es código muerto: `version_tuple` devuelve
  `(0,)` y nunca `()`, así que el salto `"unknown"` es inalcanzable y esas versiones se
  reportan como `"patch"`. O se arregla `version_tuple` para devolver `()` en fallo, o
  se elimina la rama.
- `slice_markdown_by_version:467` — `has_component` se calcula pero no se usa en la rama
  de fusión (línea 470), que siempre añade `**{heading}**` aunque el título sea un
  simple "Version X.Y.Z". Cosmético.
- `_apply_enrichment` asigna **el mismo objeto lista** a varias entradas del grupo
  (línea 1179). Hoy es inocuo porque la clave de grupo incluye `source_url`, pero es
  un aliasing frágil si esa clave cambia.

---

## Orden de ejecución sugerido

1. **Fase 1** (1.1 → 1.2 → 1.3) — son la causa de resultados *silenciosamente
   incorrectos*, lo peor de todo: la herramienta dice "sin notas" en vez de fallar.
2. **Fase 2** — evita perder corridas completas (y el coste del LLM) por un JSON raro.
3. **3.2 antes que 3.3** — primero hacer visibles los fallos, luego arreglar la
   concurrencia que los provoca.
4. **3.1**, luego **Fase 4**.

## Regresión

Añadir, junto a cada fix, su test en `test_changelog_diff.py`. Prioritarios porque hoy
**ningún test los cubre**:

- Tags `release/X.Y.Z` y `release-X.Y.Z` a través de `GitHubAdapter` (no solo de
  `is_prerelease` aislado).
- Versiones estilo `X.Y.Z.RELEASE` de punta a punta.
- `--include-prereleases` con milestones `M1`.
- `_apply_enrichment` / `build_intel_document` con respuestas de LLM deformes.
- Caché: misma dependencia con y sin `--include-prereleases` → dos entradas distintas.
- `main` con `--format intel` y cero dependencias → documento válido.
