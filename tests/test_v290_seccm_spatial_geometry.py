from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


ocp = _load("faradaiq_parse_ocp_v290", "pyseccm_tools/parse_OCP.py")
iv = _load("faradaiq_parse_iv_v290", "pyseccm_tools/parse_IV.py")


def _frame(xs, ys, values, name="value"):
    return pd.DataFrame({"X-pos[m]": xs, "Y-pos[m]": ys, name: values})


def test_ocp_two_point_dataset_is_line_not_2d_map():
    frame = _frame([2e-6, 3e-6], [0.0, 0.0], [-0.5652, -0.5251], "OCP-value[V]")
    geometry, details = ocp._spatial_geometry(frame)
    assert geometry == "line"
    assert details["n_points"] == 2
    np.testing.assert_allclose(ocp._distance_along_scan(frame), [0.0, 1e-6])


def test_ocp_ten_point_collinear_linescan_is_line():
    frame = _frame(np.arange(1, 11) * 1e-6, np.zeros(10), np.linspace(-0.60, -0.50, 10), "OCP-value[V]")
    geometry, details = ocp._spatial_geometry(frame)
    assert geometry == "line"
    assert details["n_points"] == 10
    assert details["rank"] == 1


def test_nearly_collinear_diagonal_scan_is_line_without_fake_jitter():
    x = np.linspace(0.0, 9e-6, 10)
    y = 0.5 * x + np.linspace(0.0, 2e-10, 10)
    frame = _frame(x, y, np.arange(10.0), "OCP-value[V]")
    geometry, details = ocp._spatial_geometry(frame)
    assert geometry == "line"
    assert details["linearity_ratio"] < 0.02


def test_true_2d_map_retains_interpolation_path():
    frame = _frame(
        [0.0, 1.0, 0.0, 1.0, 0.5],
        [0.0, 0.0, 1.0, 1.0, 0.5],
        [0.0, 1.0, 1.0, 2.0, 1.0],
        "OCP-value[V]",
    )
    geometry, details = ocp._spatial_geometry(frame)
    assert geometry == "map2d"
    gx, gy = np.meshgrid(np.linspace(0, 1, 8), np.linspace(0, 1, 8))
    grid, method, error = ocp._safe_griddata(frame[["X-pos[m]", "Y-pos[m]"]], frame["OCP-value[V]"], (gx, gy))
    assert error is None
    assert method in {"cubic", "linear", "nearest"}
    assert grid is not None and np.isfinite(grid).any()


def test_iv_empty_rp_is_valid_and_does_not_request_qhull():
    xy = pd.DataFrame({"X-pos[m]": [2e-6, 3e-6], "Y-pos[m]": [0.0, 0.0]})
    frame = iv._finite_spatial_frame(xy, [np.nan, np.nan], "Rp")
    geometry, details = iv._spatial_geometry(frame)
    assert geometry == "empty"
    assert details["n_points"] == 0


def test_iv_parser_contains_current_to_log_current_fallback():
    source = (ROOT / "pyseccm_tools/parse_IV.py").read_text(encoding="utf-8")
    assert 'if column not in data.columns' in source
    assert '"Imon-2[A]" in data.columns' in source
    assert 'np.log10(np.abs(current))' in source


def test_ocp_parser_no_longer_unconditionally_calls_cubic_griddata():
    source = (ROOT / "pyseccm_tools/parse_OCP.py").read_text(encoding="utf-8")
    assert 'geometry == "map2d"' in source
    assert '_distance_along_scan' in source
    assert '1-D spatial profile used; 2-D triangulation intentionally skipped.' in source
