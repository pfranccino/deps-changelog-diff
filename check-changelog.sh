#!/bin/bash

# Segundo proceso: analiza QUÉ CAMBIA entre versiones de dependencias a partir del
# dependency_status.json que genera check-dependencies.sh.
#
# Uso:
#   ./check-changelog.sh [ruta/al/dependency_status.json] [opciones de changelog-diff.py]
#
# Ejemplos:
#   ./check-changelog.sh                                  # usa ./dependency_status.json
#   ./check-changelog.sh ./dependency_status.json --md changelog.md
#   ./check-changelog.sh --summarize                      # con resumen LLM (ANTHROPIC_API_KEY)
#   WITH_CRAWL4AI=1 ./check-changelog.sh                  # instala también crawl4AI (fallback)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/venv-changelog"
PYTHON_SCRIPT="${SCRIPT_DIR}/changelog-diff.py"

echo "🚀 Iniciando analizador de cambios entre versiones"

# Crear entorno virtual si no existe
if [ ! -d "${VENV_DIR}" ]; then
  echo "🔨 Creando entorno virtual en ${VENV_DIR}..."
  python3 -m venv "${VENV_DIR}" || { echo "❌ Error al crear el entorno virtual"; exit 1; }
fi

# Localizar los binarios del venv (Windows/Git Bash usa Scripts/ en vez de bin/)
if [ -x "${VENV_DIR}/Scripts/python.exe" ]; then
  VENV_PYTHON="${VENV_DIR}/Scripts/python.exe"
elif [ -x "${VENV_DIR}/bin/python" ]; then
  VENV_PYTHON="${VENV_DIR}/bin/python"
else
  echo "❌ Error: no se encontró el intérprete de Python en ${VENV_DIR}"
  exit 1
fi

# Instalar dependencias base (requests).
echo "📦 Instalando dependencias..."
REQUIREMENTS="${SCRIPT_DIR}/requirements.txt"
if [ -f "${REQUIREMENTS}" ]; then
  PIP_ARGS=(-r "${REQUIREMENTS}")
else
  PIP_ARGS=(requests)
fi
if ! "${VENV_PYTHON}" -m pip install --quiet "${PIP_ARGS[@]}"; then
  echo "❌ Error al instalar dependencias"
  exit 1
fi

# crawl4AI es opcional: se instala solo si pides WITH_CRAWL4AI=1.
# Arrastra Playwright y un navegador headless, así que el opt-in evita ese peso en CI.
if [ "${WITH_CRAWL4AI:-0}" = "1" ]; then
  echo "📦 Instalando crawl4AI (solicitado vía WITH_CRAWL4AI=1)..."
  CRAWL_REQ="${SCRIPT_DIR}/requirements-crawl4ai.txt"
  if [ -f "${CRAWL_REQ}" ]; then
    "${VENV_PYTHON}" -m pip install --quiet -r "${CRAWL_REQ}" || \
      echo "⚠️  No se pudo instalar crawl4AI; el script usará el parseo de HTML de respaldo."
  else
    "${VENV_PYTHON}" -m pip install --quiet "crawl4ai>=0.4.0" || \
      echo "⚠️  No se pudo instalar crawl4AI; el script usará el parseo de HTML de respaldo."
  fi

  # crawl4AI necesita bajar el navegador de Playwright una sola vez.
  if [ "${SKIP_CRAWL4AI:-0}" != "1" ]; then
    echo "🌐 Preparando navegador de crawl4AI (Playwright)..."
    "${VENV_PYTHON}" -m playwright install chromium >/dev/null 2>&1 || \
      echo "⚠️  No se pudo bajar el navegador de Playwright; AndroidX usará el parseo de HTML de respaldo."
  fi
fi

echo "🔎 Analizando cambios entre versiones..."
"${VENV_PYTHON}" "${PYTHON_SCRIPT}" "$@"
RESULT=$?

if [ ${RESULT} -ne 0 ]; then
  echo "❌ Error al ejecutar el análisis"
  exit ${RESULT}
fi

echo "✨ Proceso completado"
