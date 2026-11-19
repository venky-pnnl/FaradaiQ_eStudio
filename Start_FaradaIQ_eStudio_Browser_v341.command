#!/bin/bash
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)" || exit 1
cd -- "$ROOT" || exit 1
PYTHON="$ROOT/.faradaiq_venv_macos/bin/python"
if [ ! -x "$PYTHON" ]; then
  printf '%s\n' 'Run Install_FaradaIQ_eStudio_v341.command from the extracted package before opening Browser mode.' >&2
  if [ -t 0 ]; then read -r -p 'Press Return to close. ' _; fi
  exit 1
fi

export PYTHONNOUSERSITE=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export PYTHONFAULTHANDLER=1 FARADAIQ_APP_VERSION=v341
LOG_DIR="$HOME/Library/Logs/FaradaIQ eStudio"
LOG="$LOG_DIR/browser_v341.log"
if /bin/mkdir -p "$LOG_DIR" && [ -w "$LOG_DIR" ]; then
  printf 'FaradaIQ eStudio Browser v341 log: %s\n' "$LOG"
  {
    printf '\n=== FaradaIQ eStudio Browser v341 started %s ===\n' "$(/bin/date)"
    "$PYTHON" -u "$ROOT/faradaiq_browser_v341.py" "$@"
  } 2>&1 | /usr/bin/tee -a "$LOG"
  RESULT=${PIPESTATUS[0]}
else
  printf '%s\n' 'Could not create the Browser startup log folder. Output will appear in this window.' >&2
  "$PYTHON" -u "$ROOT/faradaiq_browser_v341.py" "$@"
  RESULT=$?
fi

if [ "$RESULT" -ne 0 ]; then
  printf '\nFaradaIQ eStudio Browser exited with code %s. See %s\n' "$RESULT" "$LOG" >&2
  if [ -t 0 ]; then read -r -p 'Press Return to close. ' _; fi
fi
exit "$RESULT"
