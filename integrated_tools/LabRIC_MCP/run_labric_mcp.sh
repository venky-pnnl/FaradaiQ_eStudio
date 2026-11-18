#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PY="$ROOT/.faradaiq_venv/bin/python"
if [[ -x "$ROOT/.faradaiq_venv_macos/bin/python" ]]; then
  PY="$ROOT/.faradaiq_venv_macos/bin/python"
fi
: "${LABRIC_MCP_URL:=https://mcp.labric.co/mcp}"
export LABRIC_MCP_URL
if [[ -z "${LABRIC_API_KEY:-}" ]]; then
  echo "[FaradaIQ LabRIC MCP] LABRIC_API_KEY is missing." >&2
  exit 2
fi
if [[ ! -x "$PY" ]]; then
  echo "[FaradaIQ LabRIC MCP] FaradaIQ private Python not found: $PY" >&2
  exit 3
fi
export PYTHONNOUSERSITE=1
exec "$PY" "$HERE/labric_mcp_proxy.py"
