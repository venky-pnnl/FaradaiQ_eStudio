from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from faradaiq import ocp_handoff_v291 as ocp
import faradaiq.v262_technique_scilink as scilink


def test_tail_window_is_elapsed_time_not_row_fraction():
    t = np.array([0.0, 1.0, 2.0, 3.0, 10.0])
    e = np.array([-0.8, -0.7, -0.6, -0.5, -0.4])
    result = ocp._tail_metrics(t, e, 30.0)
    assert result["window_basis"] == "elapsed_time"
    assert result["tail_start_s"] == 7.0
    assert result["n_tail"] == 1
    assert result["mean_ocp_V"] == -0.4


def test_tail_window_mean_and_stability_metrics():
    t = np.arange(0.0, 11.0)
    e = np.linspace(-0.6, -0.4, len(t))
    result = ocp._tail_metrics(t, e, 30.0)
    # final 30% of 0..10 begins at 7 s => samples 7,8,9,10
    assert result["n_tail"] == 4
    assert abs(result["mean_ocp_V"] - np.mean(e[7:])) < 1e-12
    assert result["tail_drift_V_per_s"] > 0
    assert result["std_ocp_V"] > 0


def test_nonmonotonic_time_fallback_is_explicit():
    result = ocp._tail_metrics(np.array([0.0, 2.0, 1.0, 3.0]), np.array([1.0, 2.0, 3.0, 4.0]), 50.0)
    assert result["window_basis"] == "sample_count_fallback_nonmonotonic_time"
    assert result["n_tail"] == 2
    assert result["mean_ocp_V"] == 3.5


def test_point_identity_recognizes_browser_cache_names():
    assert ocp._point_from_path(Path("OCP_point_007_cycle_01.csv")) == 7
    assert ocp._point_from_path(Path("method_01_P012_OCP.csv")) == 12
    assert ocp._point_from_path(Path("sample_point_11points/OCP.csv")) is None


def test_custom_scilink_bundle_scope_uses_explicit_ocp_paths(tmp_path: Path):
    a = tmp_path / "OCP_point_001.csv"; a.write_text("time_s,potential_V\n0,-0.5\n1,-0.4\n", encoding="utf-8")
    b = tmp_path / "ocp_average_vs_point.csv"; b.write_text("point_index,mean_ocp_V\n1,-0.4\n", encoding="utf-8")
    item = scilink._bundle_candidate(object(), [a, b], record_names_override=[], display_label="OCP dedicated handoff")
    assert item is not None
    assert item["bundle_source_paths"] == [str(a.resolve()), str(b.resolve())]
    assert item["bundle_record_names"] == []
    assert item["display_path"] == "OCP dedicated handoff"


def test_prepare_handoff_writes_average_vs_point(tmp_path: Path, monkeypatch):
    f1 = tmp_path / "OCP_point_001.csv"
    f2 = tmp_path / "OCP_point_002.csv"
    f1.write_text("time_s,potential_V\n0,-0.60\n5,-0.55\n10,-0.50\n", encoding="utf-8")
    f2.write_text("time_s,potential_V\n0,-0.50\n5,-0.45\n10,-0.40\n", encoding="utf-8")

    class Browser:
        analyzed_root = tmp_path

    browser = Browser()
    monkeypatch.setattr(ocp, "selected_ocp_sources", lambda source: [(f1, {"technique_key": "OCP", "point": 1}), (f2, {"technique_key": "OCP", "point": 2})])
    result = ocp.prepare_ocp_tail_handoff(browser, 30.0, write_plot=False)
    assert result.point_csv.is_file()
    rows = list(csv.DictReader(result.point_csv.open(encoding="utf-8")))
    assert [int(r["point_index"]) for r in rows] == [1, 2]
    # For 0..10 s, final 30% starts at 7 s, so only t=10 is in these sparse test traces.
    assert abs(float(rows[0]["mean_ocp_V"]) + 0.50) < 1e-12
    assert abs(float(rows[1]["mean_ocp_V"]) + 0.40) < 1e-12
