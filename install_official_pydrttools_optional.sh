#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"
AUTO_MODE=0
if [ "${1:-}" = "--auto" ]; then AUTO_MODE=1; fi
VENV_PY=".faradaiq_venv/bin/python"
REQ="requirements_official_pydrttools_optional.txt"

printf '%s\n' "============================================================"
printf '%s\n' "Official ciuccislab pyDRTtools installer for FaradaIQ Studio v273"
printf '%s\n' "============================================================"
printf '%s\n' "Installs only into the local .faradaiq_venv environment."
printf '%s\n' "The pyDRTtools package is installed with --no-deps so its older"
printf '%s\n' "upstream pins cannot downgrade FaradaIQ's scientific stack."
printf '\n'

if [ ! -x "$VENV_PY" ]; then
  echo "ERROR: FaradaIQ private environment was not found."
  echo "Run Start_FaradaIQ_Studio_v237.command or .sh once first."
  [ "$AUTO_MODE" -eq 0 ] && read -r -p "Press Enter to close..." _
  exit 1
fi
if [ ! -f "$REQ" ]; then
  echo "ERROR: $REQ was not found."
  [ "$AUTO_MODE" -eq 0 ] && read -r -p "Press Enter to close..." _
  exit 1
fi

echo "Installing/repairing pyDRTtools GUI/runtime prerequisites..."
"$VENV_PY" -m pip install --upgrade -r "$REQ" || {
  echo "ERROR: pyDRTtools prerequisites failed to install."
  [ "$AUTO_MODE" -eq 0 ] && read -r -p "Press Enter to close..." _
  exit 1
}

echo
echo "Installing the official Ciucci Lab package without dependency downgrades..."
if ! "$VENV_PY" -m pip install --upgrade --no-deps "https://github.com/ciuccislab/pyDRTtools/archive/refs/heads/master.zip"; then
  echo "GitHub install was unavailable. Trying the PyPI package..."
  "$VENV_PY" -m pip install --upgrade --no-deps pyDRTtools || {
    echo "ERROR: Official pyDRTtools package installation failed."
    [ "$AUTO_MODE" -eq 0 ] && read -r -p "Press Enter to close..." _
    exit 1
  }
fi

echo
echo "Verifying GUI and selected-EIS loader..."
"$VENV_PY" _check_pydrttools_optional.py || {
  echo "ERROR: pyDRTtools installed but did not import cleanly."
  [ "$AUTO_MODE" -eq 0 ] && read -r -p "Press Enter to close..." _
  exit 1
}

echo
echo "Official pyDRTtools is ready for FaradaIQ selected-EIS handoff."
[ "$AUTO_MODE" -eq 0 ] && read -r -p "Press Enter to close..." _
exit 0
