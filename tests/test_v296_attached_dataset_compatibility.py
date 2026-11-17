from pathlib import Path

import numpy as np

from faradaiq.spatial_points_v296 import (
    _read_takedata_coordinate_file,
    auto_axis_for_records,
    enrich_catalog_records,
)
import faradaiq_studio_core as core


def _write_takedata(path: Path, coords, repeats: int = 1):
    lines = []
    for _ in range(repeats):
        for x, y in coords:
            lines.append("Sweep #, Imon-2[A], Emon-2[V], X-pos[m], Y-pos[m], Z-pos[m], Time[s]")
            lines.append(f"1, 5e-10, 0.48, {x:.9E}, {y:.9E}, -1e-4, 0")
    path.write_text("\n".join(lines), encoding="utf-8")


def test_repeated_eis_coordinate_cycles_are_unique_spatial_points(tmp_path):
    coords = [(0.0, 0.0), (1e-8, 1e-3), (1.25e-8, 2e-3), (2.5e-8, 3e-3), (-1.775e-7, 4e-3)]
    path = tmp_path / "sample_pointscan_EIS_run4_takedata_1.txt"
    _write_takedata(path, coords, repeats=2)
    rows = _read_takedata_coordinate_file(path)
    assert len(rows) == 5
    assert rows[0]["coordinate_repeat_count"] == 2
    assert rows[-1]["y_m"] == 4e-3


def test_real_line_scan_lateral_drift_auto_selects_y():
    # Matches the geometry class present in the supplied point/line scans:
    # millimetres of Y travel with sub-micrometre X drift.
    records = []
    for idx, (x, y) in enumerate([
        (-2e-8, 0.0), (-1.2e-7, 5e-4), (-3.5e-7, 1.5e-3), (-6.4e-7, 4.25e-3)
    ], start=1):
        records.append({"point": idx, "x_position_m": x, "y_position_m": y})
    assert auto_axis_for_records(records) == "Y"


def test_diagonal_scan_still_uses_distance():
    records = [
        {"point": 1, "x_position_m": 0.0, "y_position_m": 0.0},
        {"point": 2, "x_position_m": 1e-3, "y_position_m": 1e-3},
        {"point": 3, "x_position_m": 2e-3, "y_position_m": 2e-3},
    ]
    assert auto_axis_for_records(records) == "Distance"


def test_neighboring_run_takedata_does_not_contaminate_selected_run(tmp_path):
    a = tmp_path / "sample_A_linescan_run2_takedata_1.txt"
    b = tmp_path / "sample_B_pointscan_run3_takedata_1.txt"
    _write_takedata(a, [(0, 0), (0, 1e-3), (0, 2e-3)])
    _write_takedata(b, [(1e-2, 0), (2e-2, 0), (3e-2, 0), (4e-2, 0)])
    records = [
        {
            "point": i,
            "technique_key": "OCP",
            "path": str(tmp_path / "sample_A_linescan_run2" / "processed" / f"point_{i:03d}" / "sweep.csv"),
            "metadata": {"dataset": "sample_A_linescan_run2"},
        }
        for i in range(1, 4)
    ]
    summary = enrich_catalog_records(records, [tmp_path])
    assert summary["point_count"] == 3
    assert summary["auto_axis"] == "Y"
    assert all("sample_A_linescan_run2_takedata_1.txt" in r["spatial_coordinate_source"] for r in records)
    assert [r["y_position_m"] for r in records] == [0.0, 1e-3, 2e-3]


def test_no_takedata_is_safe_point_fallback(tmp_path):
    records = [{"point": 1, "technique_key": "OCP", "path": str(tmp_path / "p1.csv")},
               {"point": 2, "technique_key": "OCP", "path": str(tmp_path / "p2.csv")}]
    summary = enrich_catalog_records(records, [tmp_path])
    assert summary["point_count"] == 0
    assert summary["auto_axis"] == "Point"
    assert all(not r.get("spatial_coordinate_available") for r in records)


def test_bytes_bundle_accepts_absolute_dat_section_offsets():
    parser = core.HEKAPotmasterParser()
    parser.mode = "bytes_bundle"
    parser.sections = {".dat": core.HekaSection(".dat", 256, 20004)}
    parser.dat_payload_offset = 256
    parser.dat_payload_length = 20004
    assert parser._classify_trace_offset(256, 20004) == "bundle_absolute"
