#!/bin/bash
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)" || exit 1
LOG_DIR="$HOME/Library/Logs/FaradaIQ eStudio"
LOG="$LOG_DIR/startup_v338.log"
export PYTHONFAULTHANDLER=1 PYTHONUNBUFFERED=1

if /bin/mkdir -p "$LOG_DIR"; then
  printf 'FaradaIQ eStudio v338 startup log: %s\n' "$LOG"
  {
    printf '\n=== FaradaIQ eStudio v338 started %s ===\n' "$(/bin/date)"
    /bin/bash "$ROOT/Start_FaradaIQ_eStudio_v338.sh" "$@"
  } 2>&1 | /usr/bin/tee -a "$LOG"
  RESULT=${PIPESTATUS[0]}
else
  printf '%s\n' 'Could not create the startup log folder. Startup messages will appear in this window.' >&2
  /bin/bash "$ROOT/Start_FaradaIQ_eStudio_v338.sh" "$@"
  RESULT=$?
fi

if [ "$RESULT" -ne 0 ]; then
  printf '\nFaradaIQ eStudio exited with code %s. See %s\n' "$RESULT" "$LOG" >&2
  if [ -t 0 ]; then
    read -r -p 'Press Return to close. ' _
  fi
fi
exit "$RESULT"
