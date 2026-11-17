from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd


def _dummy(tmp_path: Path) -> Path:
    import faradaiq_tafel_adapter as tafel
    return tafel.generate_dummy_dataset(tmp_path, noise_std_dec=0.012)


def test_fast_stable_tafel_analysis_is_bounded_and_writes_diagnostics(tmp_path: Path):
    import faradaiq_tafel_adapter as tafel
    path = _dummy(tmp_path)
    settings = tafel.TafelSettings(
        eeq_V=0.0,
        current_adjust=False,
        optimizer_mode="Fast",
        eta_scan_windows_mV=(60.0, 120.0),
        enable_bootstrap=True,
        bootstrap_samples=20,
    )
    started = time.monotonic()
    result = tafel.analyze_file_variants(path, tmp_path, settings)[0]
    elapsed = time.monotonic() - started
    # The prior exhaustive overlapping-window implementation took minutes.
    assert elapsed < 30.0
    stable = [row for row in result.comparison_rows if row.get("method") == "Stable-window Tafel fit"]
    assert len(stable) == 2
    assert all(row.get("quality_grade") in {"Excellent", "Good", "Questionable", "Unsuitable"} for row in stable)
    assert all(row.get("rmse") is not None for row in stable)
    assert Path(result.stability_csv).exists()
    diagnostic = pd.read_csv(result.stability_csv)
    assert {"branch", "eta_V", "local_tafel_slope_mV_dec", "local_r2"}.issubset(diagnostic.columns)
    assert len(diagnostic) > 20


def test_ir_correction_uses_current_and_resistance(tmp_path: Path):
    import faradaiq_tafel_adapter as tafel
    path = tmp_path / "ir_test.csv"
    potential = np.linspace(-0.1, 0.1, 9)
    current = potential * 1e-2
    frame = pd.DataFrame({"Potential_V": potential, "Current_A": current})
    frame.to_csv(path, index=False)
    corrected, warnings, _, _ = tafel.normalize_iv_lsv(
        path,
        tafel.TafelSettings(eeq_V=0.0, current_adjust=False, uncompensated_resistance_ohm=10.0),
    )
    by_current = corrected.sort_values("current_A")
    assert np.allclose(by_current["E_V"].to_numpy(), potential - current * 10.0)
    assert any("iR compensation" in warning for warning in warnings)


def test_worker_emits_progress_and_result_json(tmp_path: Path):
    import faradaiq_tafel_adapter as tafel
    path = _dummy(tmp_path)
    result_json = tmp_path / "worker_result.json"
    job_json = tmp_path / "worker_job.json"
    settings = tafel.TafelSettings(
        eeq_V=0.0,
        current_adjust=False,
        optimizer_mode="Fast",
        eta_scan_windows_mV=(60.0, 120.0),
        enable_bootstrap=False,
    )
    job_json.write_text(json.dumps({
        "paths": [str(path)],
        "output_root": str(tmp_path),
        "settings": asdict(settings),
        "result_json": str(result_json),
    }), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "faradaiq.tafel_worker_v237", "--job", str(job_json)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=45,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip().startswith("{")]
    assert events[0]["event"] == "started"
    assert any(event["event"] == "file_started" for event in events)
    assert events[-1]["event"] == "completed"
    payload = json.loads(result_json.read_text(encoding="utf-8"))
    assert payload["schema"] == "faradaiq-tafel-worker-v237"
    assert len(payload["results"]) == 1


def test_cv_forward_and_reverse_remain_separate(tmp_path: Path):
    import faradaiq_tafel_adapter as tafel
    potential = np.r_[np.linspace(0.32, -0.32, 91), np.linspace(-0.32, 0.32, 91)[1:]]
    current = 1.4e-6 * np.sinh(8.5 * potential)
    path = tmp_path / "cv.csv"
    pd.DataFrame({"Potential_V": potential, "Current_A": current}).to_csv(path, index=False)
    settings = tafel.TafelSettings(
        scan_branch_mode="Forward + reverse separately", optimizer_mode="Fast",
        enable_bootstrap=False, eta_scan_windows_mV=(60.0, 120.0),
    )
    results = tafel.analyze_file_variants(path, tmp_path, settings)
    assert len(results) == 2
    assert {row["scan_branch"] for result in results for row in result.comparison_rows} == {"forward", "reverse"}
