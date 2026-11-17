"""Optional DearEIS environment checker for FaradaIQ.

DearEIS is intentionally checked in a separate optional environment:
    Windows: .deareis_venv\\Scripts\\python.exe
    macOS/Linux: .deareis_venv/bin/python

Exit codes:
  0 = DearEIS import works in .deareis_venv
  2 = .deareis_venv or DearEIS is missing
  3 = .deareis_venv uses Python older than 3.11
  4 = DearEIS import attempted but failed for another reason
"""
from __future__ import annotations

import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if sys.platform.startswith("win"):
    DEAREIS_PY = ROOT / ".deareis_venv" / "Scripts" / "python.exe"
else:
    DEAREIS_PY = ROOT / ".deareis_venv" / "bin" / "python"


def _run_env_python() -> int:
    if not DEAREIS_PY.exists():
        print("DearEIS status: MISSING")
        print("Reason: .deareis_venv has not been created yet.")
        print("Install command: install_deareis_optional.bat on Windows, install_deareis_optional_macos.command on macOS")
        return 2
    cmd = [str(DEAREIS_PY), "-c", (
        "import importlib, shutil, sys; "
        "print('Python executable:', sys.executable); "
        "print('Python version:', sys.version.replace(chr(10), ' ')); "
        "\nif sys.version_info < (3, 11):\n"
        "    print('DearEIS status: SKIPPED'); "
        "    print('Reason: DearEIS requires Python >= 3.11.'); "
        "    raise SystemExit(3)\n"
        "\ntry:\n"
        "    deareis = importlib.import_module('deareis')\n"
        "except ModuleNotFoundError:\n"
        "    print('DearEIS status: MISSING'); "
        "    print('Reason: deareis is not installed inside .deareis_venv.'); "
        "    raise SystemExit(2)\n"
        "except Exception as exc:\n"
        "    print('DearEIS status: BROKEN'); "
        "    print(repr(exc)); "
        "    raise SystemExit(4)\n"
        "print('DearEIS status: INSTALLED'); "
        "print('DearEIS version:', getattr(deareis, '__version__', 'version unknown')); "
        "print('DearEIS module:', getattr(deareis, '__file__', 'unknown')); "
        "exe = shutil.which('deareis'); "
        "print('DearEIS command:', exe or 'not on PATH; FaradaIQ uses python -m deareis'); "
        "print('Launch command:', repr(sys.executable) + ' -m deareis')"
    )]
    try:
        return subprocess.call(cmd)
    except Exception:
        print("DearEIS status: BROKEN")
        traceback.print_exc()
        return 4


if __name__ == "__main__":
    sys.exit(_run_env_python())
