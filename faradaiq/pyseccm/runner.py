"""Runner helpers for bundled pySECCM parser scripts."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence

from ..runtime.paths import bundled_path

_PARSER_FILES = {
    "cv": "parse_CV.py",
    "eis": "parse_EIS.py",
    "iv": "parse_IV.py",
    "tafel": "parse_IV.py",
    "ocp": "parse_OCP.py",
}

def available_parsers() -> tuple[str, ...]:
    return tuple(sorted(_PARSER_FILES))

def parser_script(kind: str) -> Path:
    key = kind.strip().lower()
    if key not in _PARSER_FILES:
        raise ValueError(f"Unknown pySECCM parser {kind!r}. Available: {', '.join(available_parsers())}")
    return bundled_path("pyseccm_tools", _PARSER_FILES[key])

def run_pyseccm_parser(
    kind: str,
    *args: str | Path,
    python_executable: str | Path | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    script = parser_script(kind)
    cmd = [str(python_executable or sys.executable), str(script), *(str(a) for a in args)]
    return subprocess.run(cmd, cwd=str(script.parent), text=True, check=check)
