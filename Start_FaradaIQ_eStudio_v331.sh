#!/bin/bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.faradaiq_venv_macos/bin/python"
if [ ! -x "$PYTHON" ]; then
  /bin/bash "$ROOT/Install_FaradaIQ_eStudio_v331.sh" --no-launch
fi
export PYTHONNOUSERSITE=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export FARADAIQ_AI_LAUNCH_MODE=scilink_edison_llm FARADAIQ_SCIENCE_MODE=scilink_edison_llm
export FARADAIQ_EDISON_ENABLED=1 FARADAIQ_DIRECT_EDISON_ENABLED=1 FARADAIQ_LLM_TOOLS_ENABLED=1
export FARADAIQ_APP_VERSION=v331
exec "$PYTHON" "$ROOT/faradaiq_estudio_v331.py" "$@"
