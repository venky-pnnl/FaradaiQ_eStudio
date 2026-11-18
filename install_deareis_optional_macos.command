#!/bin/bash
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)" || exit 1
PYTHON="$ROOT/.faradaiq_venv_macos/bin/python"
if [ ! -x "$PYTHON" ]; then
  printf '%s\n' 'Run Install_FaradaIQ_eStudio_v338.command first to prepare the private runtime.' >&2
  if [ "${1:-}" != '--auto' ] && [ -t 0 ]; then read -r -p 'Press Return to close. ' _; fi
  exit 1
fi
"$PYTHON" "$ROOT/scripts/setup_analysis_tools.py" --tool deareis --auto
status=$?
if [ "$status" -ne 0 ] && [ "${1:-}" != '--auto' ] && [ -t 0 ]; then
  read -r -p 'Press Return to close. ' _
fi
exit "$status"
