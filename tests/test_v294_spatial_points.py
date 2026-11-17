from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from faradaiq.spatial_points_v294 import enrich_catalog_records, auto_axis_for_records, available_axes
from faradaiq import multidim_v216 as multidim
from faradaiq import ocp_handoff_v291 as ocp


def _physical_records():
    records = []
    for point, x_um in enumerate((1.0, 4.0, 10.0), start=1):
        records.append({
            "technique_key": "POSITION", "point": point,
            "x_position": x_um * 1e-6, "y_position": 0.0, "z_position": 0.0,
            "coordinate_source": "decoded_from_HEKA_pul_takedata_records",
            "coordinate_status": "decoded physical X/Y/Z from HEKA Takedata",
            "path": f"point_{point:03d}_takedata.csv",
        })
        records.append({"technique_key": "OCP", "point": point, "path": f"point_{point:03d}_ocp.csv"})
    return records


def test_catalog_spatial_enrichment_uses_takedata_and_distance():
    records = _physical_records()
    summary = enrich_catalog_records(records)
    ocp_rows = [r for r in records if r["technique_key"] == "OCP"]
    assert summary["point_count"] == 3
    assert [round(r["x_position_m"] * 1e6, 6) for r in ocp_rows] == [1.0, 4.0, 10.0]
    assert [round(r["scan_distance_m"] * 1e6, 6) for r in ocp_rows] == [0.0, 3.0, 9.0]
    assert auto_axis_for_records(ocp_rows) == "X"
    assert available_axes(ocp_rows) == ["Point", "X", "Y", "Distance"]


def test_synthetic_compatibility_xyz_is_not_promoted_to_physical():
    records = [
        {"technique_key": "POSITION", "point": 1, "x_position": 0.0, "y_position": 0.0,
         "coordinate_source": "SYNTHETIC_XYZ_ordered_point_line", "path": "a_SYNTHETIC_XYZ.txt"},
        {"technique_key": "OCP", "point": 1, "path": "ocp.csv"},
    ]
    summary = enrich_catalog_records(records)
    assert summary["point_count"] == 0
    assert not records[1].get("spatial_coordinate_available")


def test_diagonal_line_auto_uses_cumulative_distance():
    records = []
    for p, (x, y) in enumerate(((0, 0), (1e-6, 1e-6), (2e-6, 2e-6)), start=1):
        records.append({"point": p, "x_position_m": x, "y_position_m": y})
    assert auto_axis_for_records(records) == "Distance"


def test_metric_axis_can_be_point_x_y_or_distance():
    df = pd.DataFrame({
        "point": [1, 2, 3], "method": [1, 1, 1],
        "x_coord": [1e-6, 4e-6, 10e-6], "y_coord": [0.0, 0.0, 0.0],
        "steady_state": [1.0, 2.0, 3.0],
    })
    out, axis, label = multidim._v294_metric_horizontal_axis(df, {"point_axis": "Auto", "spatial_unit": "µm"})
    assert axis == "X"
    assert "X position" in label
    assert list(out["spatial_axis_display"].round(6)) == [1.0, 4.0, 10.0]
    out2, axis2, _ = multidim._v294_metric_horizontal_axis(df, {"point_axis": "Point", "spatial_unit": "µm"})
    assert axis2 == "Point"
    assert list(out2["spatial_axis_display"]) == [1, 2, 3]
    out3, axis3, _ = multidim._v294_metric_horizontal_axis(df, {"point_axis": "Distance", "spatial_unit": "µm"})
    assert axis3 == "Distance"
    assert list(out3["spatial_axis_display"].round(6)) == [0.0, 3.0, 9.0]


def test_ocp_handoff_writes_point_and_spatial_coordinates(tmp_path: Path, monkeypatch):
    f1 = tmp_path / "OCP_point_001.csv"
    f2 = tmp_path / "OCP_point_002.csv"
    f1.write_text("time_s,potential_V\n0,-0.60\n5,-0.55\n10,-0.50\n", encoding="utf-8")
    f2.write_text("time_s,potential_V\n0,-0.50\n5,-0.45\n10,-0.40\n", encoding="utf-8")

    class Browser:
        analyzed_root = tmp_path
        _v294_ocp_axis = "Auto"
        _catalog_records = [
            {"technique_key": "POSITION", "point": 1, "x_position": 1e-6, "y_position": 0.0, "z_position": 0.0,
             "coordinate_source": "decoded_from_HEKA_pul_takedata_records", "coordinate_status": "decoded physical X/Y/Z from HEKA Takedata", "path": str(tmp_path / "p1.csv")},
            {"technique_key": "POSITION", "point": 2, "x_position": 4e-6, "y_position": 0.0, "z_position": 0.0,
             "coordinate_source": "decoded_from_HEKA_pul_takedata_records", "coordinate_status": "decoded physical X/Y/Z from HEKA Takedata", "path": str(tmp_path / "p2.csv")},
        ]

    browser = Browser()
    monkeypatch.setattr(ocp, "selected_ocp_sources", lambda source: [
        (f1, {"technique_key": "OCP", "point": 1}),
        (f2, {"technique_key": "OCP", "point": 2}),
    ])
    result = ocp.prepare_ocp_tail_handoff(browser, 30.0, horizontal_axis="Auto", write_plot=False)
    assert result.horizontal_axis == "X"
    assert result.point_csv.is_file()
    assert result.spatial_csv.is_file()
    rows = list(csv.DictReader(result.spatial_csv.open(encoding="utf-8")))
    assert [float(r["x_position_um"]) for r in rows] == [1.0, 4.0]
    assert [float(r["scan_distance_um"]) for r in rows] == [0.0, 3.0]
