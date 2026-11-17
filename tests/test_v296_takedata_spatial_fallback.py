from __future__ import annotations

import csv
from pathlib import Path

from faradaiq.spatial_points_v296 import (
    _read_takedata_coordinate_file,
    enrich_catalog_records,
    auto_axis_for_records,
)
from faradaiq import ocp_handoff_v291 as ocp


def _write_repeated_takedata(path: Path, xs_m: list[float], *, synthetic: bool = True) -> Path:
    rows = []
    header = "Sweep #,Imon-2[A],Emon-2[V],X-pos[m],Y-pos[m],Z-pos[m],Time[s]"
    for i, x in enumerate(xs_m):
        rows.append(header)
        rows.append(f"1,0,0,{x:.9E},0,0,{float(i):.9E}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_repeated_header_takedata_is_read_in_point_order(tmp_path: Path):
    path = _write_repeated_takedata(tmp_path / "run_takedata_OCP_method01_SYNTHETIC_XYZ.txt", [0.0, 1e-6, 2e-6])
    rows = _read_takedata_coordinate_file(path)
    assert [r["point"] for r in rows] == [1, 2, 3]
    assert [round(r["x_m"] * 1e6, 6) for r in rows] == [0.0, 1.0, 2.0]
    assert all(r["synthetic"] for r in rows)
    assert all("not a measured HEKA stage position" in r["status"] for r in rows)


def test_catalog_uses_synthetic_takedata_only_as_explicit_fallback(tmp_path: Path):
    _write_repeated_takedata(tmp_path / "run_takedata_1_SYNTHETIC_XYZ.txt", [0.0, 1e-6, 2e-6])
    records = [{"technique_key": "OCP", "point": i, "path": str(tmp_path / f"point_{i:03d}_ocp.csv")} for i in range(1, 4)]
    summary = enrich_catalog_records(records, search_roots=[tmp_path])
    assert summary["point_count"] == 3
    assert summary["synthetic_point_count"] == 3
    assert summary["physical_point_count"] == 0
    assert auto_axis_for_records(records) == "X"
    assert [round(r["x_position_m"] * 1e6, 6) for r in records] == [0.0, 1.0, 2.0]
    assert [round(r["scan_distance_m"] * 1e6, 6) for r in records] == [0.0, 1.0, 2.0]
    assert all(r["spatial_coordinate_is_synthetic"] for r in records)


def test_physical_coordinates_win_over_synthetic_takedata(tmp_path: Path):
    _write_repeated_takedata(tmp_path / "run_takedata_1_SYNTHETIC_XYZ.txt", [0.0, 1e-6])
    records = [
        {"technique_key": "POSITION", "point": 1, "x_position": 5e-6, "y_position": 0.0,
         "coordinate_source": "decoded_from_HEKA_pul_takedata_records", "path": str(tmp_path / "p1.csv")},
        {"technique_key": "POSITION", "point": 2, "x_position": 9e-6, "y_position": 0.0,
         "coordinate_source": "decoded_from_HEKA_pul_takedata_records", "path": str(tmp_path / "p2.csv")},
        {"technique_key": "OCP", "point": 1, "path": str(tmp_path / "o1.csv")},
        {"technique_key": "OCP", "point": 2, "path": str(tmp_path / "o2.csv")},
    ]
    summary = enrich_catalog_records(records, search_roots=[tmp_path])
    ocp_rows = [r for r in records if r["technique_key"] == "OCP"]
    assert summary["physical_point_count"] == 2
    assert [round(r["x_position_m"] * 1e6, 6) for r in ocp_rows] == [5.0, 9.0]
    assert not any(r["spatial_coordinate_is_synthetic"] for r in ocp_rows)


def test_ocp_handoff_recovers_takedata_coordinates_from_analysis_root(tmp_path: Path, monkeypatch):
    f1 = tmp_path / "point_001" / "sweep_001_cycle_001_OCP.csv"
    f2 = tmp_path / "point_002" / "sweep_001_cycle_001_OCP.csv"
    f1.parent.mkdir(); f2.parent.mkdir()
    f1.write_text("time_s,potential_V\n0,-0.60\n5,-0.55\n10,-0.50\n", encoding="utf-8")
    f2.write_text("time_s,potential_V\n0,-0.50\n5,-0.45\n10,-0.40\n", encoding="utf-8")
    td_dir = tmp_path / "data"; td_dir.mkdir()
    _write_repeated_takedata(td_dir / "sample_takedata_OCP_method01_SYNTHETIC_XYZ.txt", [2e-6, 7e-6])

    class Browser:
        analyzed_root = tmp_path
        cache_root = tmp_path / "browser_cache"
        bundle_root = tmp_path
        _v294_ocp_axis = "Auto"
        _catalog_records = [
            {"technique_key": "OCP", "point": 1, "path": str(f1)},
            {"technique_key": "OCP", "point": 2, "path": str(f2)},
        ]

    browser = Browser()
    monkeypatch.setattr(ocp, "selected_ocp_sources", lambda source: [
        (f1, {"technique_key": "OCP", "point": 1}),
        (f2, {"technique_key": "OCP", "point": 2}),
    ])
    result = ocp.prepare_ocp_tail_handoff(browser, 30.0, horizontal_axis="Auto", write_plot=False)
    assert result.horizontal_axis == "X"
    rows = list(csv.DictReader(result.spatial_csv.open(encoding="utf-8")))
    assert [float(r["x_position_um"]) for r in rows] == [2.0, 7.0]
    assert [float(r["scan_distance_um"]) for r in rows] == [0.0, 5.0]
    assert all(r["spatial_coordinate_is_synthetic"].lower() == "true" for r in rows)

def test_method_specific_takedata_rows_follow_actual_point_subset(tmp_path: Path):
    _write_repeated_takedata(tmp_path / "sample_takedata_OCP_method01_SYNTHETIC_XYZ.txt", [10e-6, 40e-6])
    records = [
        {"technique_key": "OCP", "point": 2, "path": str(tmp_path / "ocp_p2.csv")},
        {"technique_key": "OCP", "point": 5, "path": str(tmp_path / "ocp_p5.csv")},
        {"technique_key": "IV/TAFEL", "point": 1, "path": str(tmp_path / "iv_p1.csv")},
    ]
    enrich_catalog_records(records, search_roots=[tmp_path])
    ocp_rows = [r for r in records if r["technique_key"] == "OCP"]
    assert [r["point"] for r in ocp_rows] == [2, 5]
    assert [round(r["x_position_m"] * 1e6, 6) for r in ocp_rows] == [10.0, 40.0]
