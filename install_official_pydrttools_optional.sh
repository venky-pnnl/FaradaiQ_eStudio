#!/bin/bash
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)" || exit 1
PYTHON="$ROOT/.faradaiq_venv/bin/python"
if [ -x "$ROOT/.faradaiq_venv_macos/bin/python" ]; then
  PYTHON="$ROOT/.faradaiq_venv_macos/bin/python"
fi
if [ ! -x "$PYTHON" ]; then
  printf '%s\n' 'Run Install_FaradaIQ_eStudio_v336.command first to prepare the private runtime.' >&2
  if [ "${1:-}" != '--auto' ] && [ -t 0 ]; then read -r -p 'Press Return to close. ' _; fi
  exit 1
fi
"$PYTHON" "$ROOT/scripts/setup_analysis_tools.py" --tool pydrttools --auto
status=$?
if [ "$status" -ne 0 ] && [ "${1:-}" != '--auto' ] && [ -t 0 ]; then
  read -r -p 'Press Return to close. ' _
fi
exit "$status"
