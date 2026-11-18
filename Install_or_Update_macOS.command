#!/bin/bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
for PYTHON in "${FARADAIQ_PYTHON:-python3.12}" /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 /opt/homebrew/bin/python3.12 python3.11 python3; do
 if command -v "$PYTHON" >/dev/null 2>&1 && "$PYTHON" -c 'import sys;sys.exit(0 if (3,11)<=sys.version_info[:2]<(3,13) else 1)' 2>/dev/null; then
  exec "$PYTHON" "$ROOT/scripts/macos_update.py" "$@"
 fi
done
printf '%s\n' 'Open the release ZIP and run its versioned installer to install Python 3.12 and eStudio.' >&2
exit 1
