#!/usr/bin/env python
"""Run FaradaIQ pytest files in isolated groups to avoid GUI-thread shutdown hangs."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = sorted((ROOT / "tests").glob("test_*.py"))
ISOLATED = {
    "test_v250_reliability.py",
    "test_v251_context_edison.py",
    "test_v253_llm_transactions.py",
    "test_v253_visual_browser.py",
    "test_v254_contour_prerender.py",
    "test_v255_browser_color.py",
    "test_v260_slider_scilink_bundle.py",
}


def _groups() -> list[list[Path]]:
    # Run one test module per child process. A few legacy GUI/service modules are
    # individually clean but can interfere with later modules when imported in
    # the same pytest interpreter. Per-file isolation makes release QA
    # deterministic and keeps failures attributable to one module.
    return [[path] for path in TESTS]


def main() -> int:
    if not TESTS:
        print("No pytest files found.", file=sys.stderr)
        return 2
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("MPLBACKEND", "Agg")
    env.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    groups = _groups()
    for index, group in enumerate(groups, 1):
        print(f"\n=== FaradaIQ QA group {index}/{len(groups)}: {len(group)} file(s) ===", flush=True)
        # The QA-only plugin exits *after* pytest emits its terminal summary,
        # preserving the real exit status while avoiding third-party thread hangs.
        command = [sys.executable, "-m", "pytest", "-ra", "-p", "no:cacheprovider", "-p", "_qa_force_exit_plugin_v320", *[str(path) for path in group]]
        try:
            result = subprocess.run(command, cwd=ROOT, env=env, timeout=360)
        except subprocess.TimeoutExpired:
            print(f"QA group {index} exceeded the 360-second safety timeout.", file=sys.stderr)
            return 124
        if result.returncode:
            print(f"QA group {index} failed with exit code {result.returncode}.", file=sys.stderr)
            return result.returncode
    print(f"\nAll {len(TESTS)} FaradaIQ pytest files passed in isolated groups.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
