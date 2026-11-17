#!/usr/bin/env python
"""Run the FaradaIQ v320 LLM/SciLink/Edison GUI smoke checks safely."""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = [
    "gui_v240_main_ai_workflow.py",
    "gui_v240_scilink_schema.py",
    "gui_v245_browser_review.py",
    "gui_v245_main_scilink_edison.py",
    "gui_v245_main_scilink_only.py",
    "gui_v245_scilink_review.py",
    "gui_v250_all_services.py",
    "gui_v250_startup_selector.py",
    "gui_v273_review_actions.py",
    "gui_v273_analyze_controls.py",
    "gui_v273_matplotlib_toolbar_visibility.py",
    "gui_v273_scilink_front.py",
    "gui_v273_saved_views_responsive.py",
    "gui_v273_short_names.py",
    "gui_v273_portable_browser.py",
    "gui_v293_ocp_handoff.py",
    "gui_v293_tafel_trace.py",
    "gui_v293_tafel_apply.py",
    "gui_v296_spatial_axis.py",
    "gui_v302_corrosion_metrics.py",
]


def _terminate_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _command(script: Path) -> list[str]:
    if os.name != "nt" and shutil.which("xvfb-run"):
        return ["xvfb-run", "-a", sys.executable, str(script)]
    return [sys.executable, str(script)]


def run_one(name: str) -> bool:
    script = ROOT / "tests" / name
    if not script.is_file():
        print(f"MISSING: {script}", file=sys.stderr)
        return False
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("MPLBACKEND", "Agg")
    env.setdefault("FARADAIQ_SKIP_STARTUP_AI_SELECTOR", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")
    proc = subprocess.Popen(
        _command(script),
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=(os.name != "nt"),
        bufsize=1,
    )
    output_parts: list[str] = []
    passed_event = threading.Event()

    def reader() -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            output_parts.append(line)
            if "smoke: PASS" in line:
                passed_event.set()

    thread = threading.Thread(target=reader, name=f"FaradaIQ-GUI-smoke-{name}", daemon=True)
    thread.start()
    deadline = time.monotonic() + 180.0
    terminated_after_pass = False
    while proc.poll() is None and time.monotonic() < deadline:
        if passed_event.wait(0.1):
            # GUI smoke scripts may leave non-daemon Tk/Matplotlib workers alive
            # after all assertions pass. Give buffered output a moment, then end
            # the process tree without waiting for the full safety timeout.
            time.sleep(0.5)
            _terminate_tree(proc)
            terminated_after_pass = True
            break
    if proc.poll() is None:
        _terminate_tree(proc)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _terminate_tree(proc)
    thread.join(timeout=3)
    output = "".join(output_parts)
    print(output, end="" if output.endswith("\n") or not output else "\n")
    passed_marker = "smoke: PASS" in output
    clean_output = "Traceback (most recent call last)" not in output and "AssertionError" not in output
    passed = passed_marker and clean_output
    state = "PASS" if passed else "FAIL"
    suffix = " (terminated after PASS marker)" if passed and terminated_after_pass else ""
    print(f"[{state}] {name}{suffix}")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scripts", nargs="*", help="Optional GUI smoke script names")
    args = parser.parse_args()
    selected = args.scripts or SCRIPTS
    failures = [name for name in selected if not run_one(name)]
    if failures:
        print("LLM GUI smoke failures: " + ", ".join(failures), file=sys.stderr)
        return 1
    print(f"All {len(selected)} LLM/SciLink/Edison GUI smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
