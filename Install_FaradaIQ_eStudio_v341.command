#!/bin/bash
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
/bin/bash "$ROOT/Install_FaradaIQ_eStudio_v341.sh" "$@"
RESULT=$?
if [ "$RESULT" -ne 0 ]; then read -r -p 'Installation failed. Press Return to close. ' _; fi
exit "$RESULT"
