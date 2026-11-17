"""Dependency checker wrapper."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .paths import bundled_path

def dependency_checker_script() -> Path:
    return bundled_path("_check_bridge_dependencies.py")

def run_dependency_check(*, python_executable: str | Path | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = [str(python_executable or sys.executable), str(dependency_checker_script())]
    return subprocess.run(cmd, cwd=str(bundled_path()), text=True, capture_output=True, check=check)
