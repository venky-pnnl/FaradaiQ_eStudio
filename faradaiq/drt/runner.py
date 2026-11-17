"""Headless DRT runner helpers.

These helpers centralize command construction for the bundled v118 headless
pyDRTtools bridge. The GUI still calls the legacy functions, but new code can
use this module directly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

from ..runtime.paths import bundled_path

def headless_drt_script() -> Path:
    return bundled_path("pyseccm_tools", "headless_pyDRTtools", "pyDRTtools", "headless_pyDRTtools.py")

def build_headless_drt_command(
    input_dir: str | Path,
    *,
    engine: str = "auto",
    run_types: Iterable[str] | None = None,
    plot_types: Iterable[str] | None = None,
    export_types: Iterable[str] | None = None,
    python_executable: str | Path | None = None,
    extra_args: Sequence[str] | None = None,
) -> list[str]:
    py = str(python_executable or sys.executable)
    cmd = [py, "-u", str(headless_drt_script()), str(input_dir), "--engine", engine]
    for flag, values in (("--run_types", run_types), ("--plot_types", plot_types), ("--export_types", export_types)):
        if values:
            cmd.append(flag)
            cmd.extend(str(v) for v in values)
    if extra_args:
        cmd.extend(str(a) for a in extra_args)
    return cmd

def run_headless_drt(
    input_dir: str | Path,
    *,
    engine: str = "auto",
    run_types: Iterable[str] | None = None,
    plot_types: Iterable[str] | None = None,
    export_types: Iterable[str] | None = None,
    python_executable: str | Path | None = None,
    extra_args: Sequence[str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = build_headless_drt_command(
        input_dir, engine=engine, run_types=run_types, plot_types=plot_types,
        export_types=export_types, python_executable=python_executable, extra_args=extra_args,
    )
    return subprocess.run(cmd, cwd=str(headless_drt_script().parent), text=True, check=check)
