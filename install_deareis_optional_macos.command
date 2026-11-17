#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"
export PYTHONNOUSERSITE=1

APP_VERSION="v216"
DEAREIS_VENV=".deareis_venv"
DEAREIS_PY="$DEAREIS_VENV/bin/python"
REQ="requirements_deareis_optional.txt"
SEED_PYTHON_CMD=""

echo "============================================================"
echo "FaradaIQ optional DearEIS installer/checker ${APP_VERSION}"
echo "============================================================"
echo
echo "DearEIS is a third-party open-source GUI/API for impedance-spectrum"
echo "processing, validation, DRT analysis, equivalent-circuit fitting, and plotting."
echo "Credit: DearEIS developers / Ville Yrjana. See:"
echo "  https://vyrjana.github.io/DearEIS/"
echo
echo "DearEIS is installed into a separate optional environment:"
echo "  ${DEAREIS_VENV}"
echo "This avoids changing FaradaIQ's pinned .faradaiq_venv packages."
echo

find_seed_python() {
  SEED_PYTHON_CMD=""
  for cand in python3.14 python3.13 python3.12 python3.11 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      if "$cand" - <<'PY' >/dev/null 2>&1
import sys, venv
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
      then
        SEED_PYTHON_CMD="$cand"
        return 0
      fi
    fi
  done
  return 1
}

auto_install_python_311() {
  echo
  echo "No Python 3.11+ runtime found. Trying automatic install with Homebrew if available..."
  if command -v brew >/dev/null 2>&1; then
    brew install python@3.11 || brew install python || true
  else
    echo "Homebrew is not available. Install Python 3.11+ from python.org or Homebrew, then rerun."
  fi
}

if [ -x "$DEAREIS_PY" ]; then
  "$DEAREIS_PY" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 3)
PY
  status=$?
  if [ "$status" -eq 3 ]; then
    echo "Existing ${DEAREIS_VENV} uses Python older than 3.11. Rebuilding automatically."
    rm -rf "$DEAREIS_VENV"
  fi
fi

if [ ! -x "$DEAREIS_PY" ]; then
  echo "No usable DearEIS environment found yet."
  find_seed_python || true
  if [ -z "$SEED_PYTHON_CMD" ]; then
    auto_install_python_311
    find_seed_python || true
  fi
  if [ -z "$SEED_PYTHON_CMD" ]; then
    echo "ERROR: DearEIS requires Python 3.11 or newer."
    exit 1
  fi
  echo "Creating DearEIS environment with: ${SEED_PYTHON_CMD}"
  "$SEED_PYTHON_CMD" -m venv "$DEAREIS_VENV" || exit 1
fi

if [ ! -x "$DEAREIS_PY" ]; then
  echo "ERROR: DearEIS Python was not created: ${DEAREIS_PY}"
  exit 1
fi

"$DEAREIS_PY" - <<'PY'
import sys
print('DearEIS env Python:', sys.executable)
print('Python version:', sys.version.replace('\n', ' '))
raise SystemExit(0 if sys.version_info >= (3, 11) else 3)
PY
if [ "$?" -eq 3 ]; then
  echo "ERROR: DearEIS environment still uses Python older than 3.11."
  exit 1
fi

echo
echo "Upgrading pip/wheel in the DearEIS-only environment..."
"$DEAREIS_PY" -m ensurepip --upgrade || true
"$DEAREIS_PY" -m pip install --upgrade 'pip<27' setuptools wheel || exit 1

if [ ! -f "$REQ" ]; then
  echo "ERROR: ${REQ} not found."
  exit 1
fi

echo
echo "Installing/upgrading DearEIS in ${DEAREIS_VENV} only..."
"$DEAREIS_PY" -m pip install --upgrade -r "$REQ" || exit 1

echo
echo "Verifying DearEIS..."
"$DEAREIS_PY" - <<'PY'
import deareis, sys
print('DearEIS import OK:', getattr(deareis, '__version__', 'version unknown'))
print('Python:', sys.executable)
PY
if [ "$?" -ne 0 ]; then
  echo "DearEIS installed but did not import cleanly."
  exit 1
fi

echo
echo "DearEIS is ready."
echo "To launch DearEIS manually later:"
echo "  ./.deareis_venv/bin/python -m deareis"
