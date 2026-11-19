from io import BytesIO
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from faradaiq import workbench_spatial as spatial
from faradaiq.workbench_audit import read_history


def _map(points, values, *, coordinates="um", values_unit="nA", status=None):
    frame = pd.DataFrame({"x": [x for x, _ in points], "y": [y for _, y in points], "signal": values})
    if status is not None:
        frame["status"] = status
    return spatial.scalar_data(frame, axes=["x", "y"], value="signal", axis_unit=coordinates,
                               value_unit=values_unit, status="status" if status is not None else None)


def _trace(times, values, *, time_unit, values_unit, status=None):
    frame = pd.DataFrame({"time": times, "signal": values})
    if status is not None:
        frame["status"] = status
    return spatial.scalar_data(frame, axes=["time"], value="signal", axis_unit=time_unit,
                               value_unit=values_unit, status="status" if status is not None else None)


def test_affine_inverse_and_degenerate_landmarks():
    xy = np.array([[0, 0], [2, 0], [0, 2], [3, 4], [-1, 7]], dtype=float) * 1e-6
    matrix = np.array([[1.2e7, 2.5e5, 150], [-2.8e6, -2.3e7, 500]])
    pixels = xy @ matrix[:, :2].T + matrix[:, 2]
    fitted, inv, residuals, rms = spatial.affine_registration(xy, pixels)
    np.testing.assert_allclose(fitted, matrix)
    np.testing.assert_allclose(spatial.apply_affine(pixels, inv), xy, atol=1e-20)
    assert residuals.residual_px.max() < 1e-12
    assert rms < 1e-12
    pixels[-1, 0] += 2
    assert spatial.affine_registration(xy, pixels)[-1] > 0
    with pytest.raises(ValueError, match="Stage landmarks must be noncollinear"):
        spatial.affine_registration([[0, 0], [1, 1], [2, 2]], [[0, 0], [2, 0], [0, 2]])
    with pytest.raises(ValueError, match="Image landmarks must be noncollinear"):
        spatial.affine_registration([[0, 0], [2, 0], [0, 2]], [[0, 0], [1, 1], [2, 2]])


def test_roi_inclusive_rectangle_concave_polygon_and_line():
    points = [(0, 0), (2, 0), (0, 2), (2, 2), (1, 1), (0.25, 1.75), (1.8, 1.8)]
    data = _map(points, [0, 2, 2, 4, 2, 2, 3.6], status=[0, 0, 1, 0, 0, 0, 0])
    _, rows, stats = spatial.roi_statistics(data, [[0, 0], [2, 2]], shape="Rectangle")
    assert stats["roi_rows"] == 7
    assert stats["included"] == 6
    assert stats["excluded"] == 1
    assert stats["mean"] == pytest.approx(13.6 / 6)
    assert rows.loc[2, "in_roi"] and not rows.loc[2, "included"]
    vertices = [[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2], [0, 0]]
    _, polygon, _ = spatial.roi_statistics(data, vertices, shape="Polygon")
    assert polygon.in_roi.to_list() == [True, True, True, False, True, True, False]
    with pytest.raises(ValueError, match="edges"):
        spatial.polygon_mask(points, [[0, 0], [2, 2], [2, 0], [0, 2]])
    line = spatial.line_profile(data, [[0, 0], [2, 2]], count=3, tolerance=0)
    np.testing.assert_allclose(line.distance, [0, np.sqrt(2), 2 * np.sqrt(2)])
    np.testing.assert_allclose(line.value, [0, 2, 4])
    assert line.source_row.to_list() == [1, 5, 4]

    collinear = _map([(0, 0), (1, 0), (2, 0)], [0, 2, 4], coordinates="mm")
    linear = spatial.line_profile(collinear, [[-0.5, 0], [2.5, 0]], count=7, tolerance=0.5, method="Linear")
    np.testing.assert_allclose(linear.value, [np.nan, 0, 1, 2, 3, 4, np.nan], equal_nan=True)
    assert linear.distance.iloc[-1] == 3.0


def test_difference_pairs_maximize_coverage_and_convert_units():
    left = _map([(0, 0), (1, 0), (5, 0), (np.nan, 0)], [1, 2, 3, 4])
    right = _map([(0.0004, 0), (-0.0005, 0), (0.006, 0)], [0.003, 0.002, 0.003],
                 coordinates="mm", values_unit="uA")
    difference, counts = spatial.difference_map(left, right, tolerance=0.6)
    assert counts == {"before_rows": 4, "after_rows": 3, "valid_before": 3, "valid_after": 3,
                      "supported_before": 2, "unmatched_valid_before": 1, "matched_after": 2,
                      "unmatched_valid_after": 1}
    assert difference.after_row.to_list()[:2] == [2, 1]
    np.testing.assert_allclose(difference.difference, [1, 1, np.nan, np.nan], equal_nan=True)
    assert difference.status.to_list() == ["matched", "matched", "unmatched", "invalid before row"]
    with pytest.raises(ValueError, match="incompatible|compatible"):
        spatial.difference_map(left, _map([(0, 0)], [3], values_unit="mV"), tolerance=0)
    with pytest.raises(ValueError, match="registration"):
        spatial.difference_map(left, _map([(0, 0)], [3], coordinates="px"), tolerance=0)
    i, j, distance = spatial.one_to_one_matches([[0], [1], [3]], [[0], [2], [3]], 0)
    np.testing.assert_array_equal(i, [0, 2])
    np.testing.assert_array_equal(j, [0, 2])
    np.testing.assert_array_equal(distance, [0, 0])


def test_difference_interpolation_has_hull_and_distance_limits():
    before = _map([(0, 0), (0.25, 0.25), (0.5, 0.5), (0.8, 0.8), (1.1, 0)], [1, 1, 1, 1, 1])
    after = _map([(0, 0), (1, 0), (0, 1)], [1, 4, 2])
    result, counts = spatial.difference_map(before, after, tolerance=0.6, method="Linear")
    np.testing.assert_allclose(result.difference, [0, 1, np.nan, np.nan, np.nan], equal_nan=True)
    assert counts["matched_after"] is None
    result, _ = spatial.difference_map(before, after, tolerance=2, method="Linear")
    np.testing.assert_allclose(result.difference, [0, 1, 2, np.nan, np.nan], equal_nan=True)


def test_map_quality_masks_and_explicit_axis_units():
    immutable = pd.DataFrame({"X_mm": [0, 0.001, 0.002, 0.003, 0.004, np.nan, 0.006],
                              "Y_um": [1] * 7, "I_nA": [1, 1, 1, 50, np.nan, 1, 10],
                              "status": ["true", "false", "passed", "passed", "passed", "", "failed"]})
    before = immutable.copy(deep=True)
    data = spatial.scalar_data(immutable, axes=["X_mm", "Y_um"], value="I_nA", axis_unit="µm",
                               value_unit="nA", status="status")
    rows = spatial.quality_map(data)
    np.testing.assert_allclose(rows.x.head(5), [0, 1, 2, 3, 4])
    assert rows.quality.to_list() == ["failed", "usable", "usable", "outlier", "missing", "missing", "failed"]
    assert rows.failed.sum() == rows.missing.sum() == 2
    pd.testing.assert_frame_equal(immutable, before)
    with pytest.raises(ValueError, match="coordinate.*unit"):
        spatial.scalar_data(immutable, axes=["X_mm", "Y_um"], value="I_nA", axis_unit=None, value_unit="nA")
    with pytest.raises(ValueError, match="Unrecognized"):
        spatial.scalar_data(immutable.assign(status="mystery"), axes=["X_mm", "Y_um"],
                            value="I_nA", axis_unit="um", value_unit="nA", status="status")


@pytest.mark.parametrize("column", ["Valid", "AcquisitionSuccess", "success_flag"])
def test_success_flag_keeps_successful_rows(column):
    frame = pd.DataFrame({"X_um": range(7), "Y_um": [0] * 7, "I_nA": [1] * 7,
                          column: [True, False, 1, 0, "passed", "error", None]})
    spec = dict(axes=["X_um", "Y_um"], value="I_nA", axis_unit="um", value_unit="nA", status=column)
    passed = spatial.scalar_data(frame, **spec)
    assert passed.valid.tolist() == [True, False, True, False, True, False, False]
    assert passed.rows.status_missing.tolist() == [False] * 6 + [True]
    failure_flag = spatial.scalar_data(frame, **spec, true_means="Failure")
    assert failure_flag.valid.tolist() == [False, True, False, True, True, False, False]


def test_multimodal_time_and_coordinate_correlation_with_counts():
    electrochem = _trace([0, 1, 2, 3, 4], [1, 2, 3, 4, np.nan], time_unit="s", values_unit="nA")
    raman = _trace([1000, 2000, 3010, 5000], [6, 4, 2, 9], time_unit="ms", values_unit="a.u.")
    thermal = _trace([0, 1, 2], [18, 18, 18], time_unit="s", values_unit="°C")
    summary, matched = spatial.correlate_scalars({"Electrochemistry": electrochem, "Raman": raman, "Thermal": thermal},
                                                  mode="time", tolerance=0.01, unit="s", offsets={"Raman": -1})
    row = summary.iloc[0]
    assert (row.matched, row.left_unmatched, row.right_unmatched, row.left_invalid) == (3, 2, 1, 1)
    assert row.pearson_r == pytest.approx(-1)
    assert row.spearman_rho == pytest.approx(-1)
    assert summary.iloc[1].status == "constant matched values"
    pair = matched["Electrochemistry", "Raman"]
    np.testing.assert_allclose(pair.match_distance, [0, 0, .01], atol=1e-14)
    first = _map([(0, 0), (2, 2), (1, 1)], [3, 2, 1])
    second = _map([(0, 0), (.002, .002), (.001, .001)], [6, 4, 2], coordinates="mm")
    summary, _ = spatial.correlate_scalars({"HSI": first, "mMZI": second},
                                            mode="coordinates", tolerance=0, unit="um")
    assert summary.iloc[0].matched == 3
    assert summary.iloc[0].pearson_r == pytest.approx(1)


def test_event_alignment_baselines_offsets_zero_and_missing():
    stimulus = pd.DataFrame({"Name": ["Magnet on", "Magnet off"], "time_ms": [3000, 20000]})
    events = spatial.event_table(stimulus, name="Name", time="time_ms", unit="ms", offset_s=-1)
    assert events.event_time_s.to_list() == [2, 19]
    first = _trace([0, 500, 1000, 1500, 2000, 2500, np.nan], [1, 2, 4, 6, 10, 12, 99],
                   time_unit="ms", values_unit="mV", status=[0, 0, 1, 0, 0, 0, 0])
    second = _trace([0, 1, 2, 3], [0, 0, 4, 8], time_unit="s", values_unit="K")
    timeline, windows, baseline = spatial.align_events(
        {"electrochem": first, "thermal": second}, events,
        before_s=1, after_s=1, baseline_s=1.5, offsets_s={"electrochem": 0.5},
    )
    assert len(timeline) == 10
    current = windows.loc[(windows.signal == "electrochem") & (windows.event == "Magnet on")]
    assert current.relative_time_s.to_list() == [-1, -.5, 0, .5, 1]
    assert current.baseline.iloc[0] == 1.5
    assert current.delta.to_list()[2] == 4.5
    assert current.relative_change_pct.to_list()[2] == 300
    assert np.isnan(current.delta.to_list()[1])
    thermal = windows.loc[windows.signal == "thermal"]
    assert thermal.relative_change_pct.isna().all()
    assert list(baseline.baseline_samples) == [2, 0, 1, 0]
    assert baseline.iloc[0].missing_time_rows == 1
    with pytest.raises(ValueError, match="Every event"):
        spatial.event_table(stimulus.assign(Name=["Magnet on", ""]), name="Name", time="time_ms", unit="s")


def _app(tmp_path, paths, *, instruments=None):
    return AppTest.from_string(
        f"""from pathlib import Path
from faradaiq.browser_data import BrowserData
from faradaiq.workbench_spatial_ui import render
root = Path({str(tmp_path)!r})
paths = tuple(Path(item) for item in {list(map(str, paths))!r})
instruments = {instruments or {}!r}
records = {{path: {{"instrument": instruments.get(path.name, "fixture"), "handle_id": f"source-{{index}}",
                   "source_path": str(root / f"raw-{{index}}"), "original_table_path": f"original/{{path.name}}",
                   "display_name": path.stem, "tags": ["series", path.stem]}}
           for index, path in enumerate(paths, 1)}}
data = BrowserData(root, root, paths, records, {{}})
render(data, workspace=root)
""",
        default_timeout=30,
    ).run()


def _element(elements, label):
    return next(element for element in elements if element.label == label)


def test_browser_workbench_roi_and_saved_measurement(tmp_path):
    source = tmp_path / "scan.csv"
    source.write_text("X_um,Y_um,I_nA,Valid\n0,0,1,true\n1,0,2,true\n1,1,100,false\n0,1,4,true\n")
    original = source.read_bytes()
    app = _app(tmp_path, [source])
    _element(app.selectbox, "Spatial and event tool").select("ROI and line profiles").run()
    _element(app.selectbox, "Spatial map").select(str(source)).run()
    assert not app.exception
    _element(app.selectbox, "Acquisition-status column").select("Valid").run()
    assert _element(app.selectbox, "True or 1 means").value == "Success"
    _element(app.text_area, "Opposite corners (X,Y)").set_value("0,0\n1,1")
    _element(app.button, "Calculate ROI statistics").click().run()
    assert not app.exception and not app.error
    assert len(app.get("plotly_chart")) == 1
    assert app.dataframe[0].value.iloc[0]["mean"] == pytest.approx(7 / 3)
    [entry] = read_history(tmp_path)
    assert entry["inputs"] == [str(source)]
    assert entry["action"] == "Calculate ROI statistics"
    assert len(entry["outputs"]) == 3
    assert all(Path(name).is_file() for name in entry["outputs"])
    assert source.read_bytes() == original
    _element(app.selectbox, "Spatial and event tool").select("Optical registration").run()
    _element(app.selectbox, "Spatial and event tool").select("ROI and line profiles").run()
    assert _element(app.text_area, "Opposite corners (X,Y)").value == "0,0\n1,1"
    assert app.dataframe[0].value.iloc[0]["included"] == 3
    assert read_history(tmp_path) == [entry]
    _element(app.selectbox, "Geometry").select("Line profile").run()
    _element(app.text_area, "Line endpoints (X,Y)").set_value("0,0\n1,0")
    _element(app.number_input, "Profile samples").set_value(2)
    _element(app.button, "Sample line profile").click().run()
    assert not app.exception and not app.error
    assert len(app.get("plotly_chart")) == 2
    assert app.dataframe[0].value.value.to_list() == [1, 2]


def test_browser_correlation_and_event_timeline(tmp_path):
    first = tmp_path / "current.csv"
    second = tmp_path / "raman.csv"
    events = tmp_path / "stimulus.csv"
    first.write_text("time_s,I_nA\n0,1\n1,2\n2,3\n3,4\n")
    second.write_text("time_ms,peak_A\n0,9\n1000,6\n2000,3\n3000,0\n")
    events.write_text("event,time_s\nMagnet on,2\n")
    app = _app(tmp_path, [first, second, events], instruments={first.name: "HEKA", second.name: "Raman"})
    _element(app.selectbox, "Spatial and event tool").select("Multimodal scalar correlation").run()
    _element(app.multiselect, "Scalar exports").set_value([str(first), str(second)]).run()
    _element(app.button, "Compare scalar exports").click().run()
    assert not app.exception and not app.error
    assert app.dataframe[0].value.iloc[0].pearson_r == pytest.approx(-1)
    _element(app.selectbox, "Spatial and event tool").select("Stimulus-event alignment").run()
    _element(app.file_uploader, "Named stimulus event CSV").set_value(
        (events.name, events.read_bytes(), "text/csv")
    ).run()
    _element(app.multiselect, "Signal exports").set_value([str(first), str(second)]).run()
    _element(app.button, "Align stimulus events").click().run()
    assert not app.exception and not app.error
    assert len(app.get("plotly_chart")) == 2
    entries = read_history(tmp_path)
    assert [entry["action"] for entry in entries] == [
        "Multimodal scalar correlation", "Stimulus-event alignment",
    ]
    saved_events = next(Path(item) for item in entries[1]["outputs"] if item.endswith("event-source.csv"))
    assert saved_events.read_bytes() == events.read_bytes()
    expected_sources = [
        {"path": str(path), "instrument": instrument, "handle_id": f"source-{index}",
         "source_path": str(tmp_path / f"raw-{index}"), "original_table_path": f"original/{path.name}",
         "display_name": path.stem, "tags": ["series", path.stem]}
        for index, (path, instrument) in enumerate([(first, "HEKA"), (second, "Raman")], 1)
    ]
    for entry in entries:
        receipt = next(Path(item) for item in entry["outputs"] if item.endswith("analysis.json"))
        assert json.loads(receipt.read_text())["sources"] == expected_sources
        assert entry["parameters"]["sources"] == expected_sources


def test_browser_registration_uses_local_pixels_and_position_only_overlay(tmp_path):
    positions = tmp_path / "positions.csv"
    positions.write_text("X_mm,Y_mm\n0,0\n0.001,0\n0,0.001\n0.001,0.001\n")
    stream = BytesIO()
    Image.new("RGB", (24, 18), "gray").save(stream, format="PNG")
    payload = stream.getvalue()
    app = _app(tmp_path, [positions])
    _element(app.file_uploader, "Optical image").set_value(("image.png", payload, "image/png"))
    _element(app.selectbox, "Stage map for overlay (optional)").select(str(positions)).run()
    _element(app.selectbox, "Landmark coordinate unit").select("um")
    _element(app.text_area, "Stage landmarks (X,Y)").set_value("0,0\n1,0\n0,1")
    _element(app.text_area, "Image landmarks (column,row)").set_value("2,3\n7,3\n2,8")
    _element(app.button, "Fit optical registration").click().run()
    assert not app.exception and not app.error
    assert len(app.get("plotly_chart")) == 1
    [entry] = read_history(tmp_path)
    output = {Path(item).name: Path(item) for item in entry["outputs"]}
    np.testing.assert_allclose(pd.read_csv(output["registered_points.csv"])[["u_px", "v_px"]],
                               [[2, 3], [7, 3], [2, 8], [7, 8]])
    assert output["optical-image.png"].read_bytes() == payload


def test_browser_difference_and_quality_outputs(tmp_path):
    first = tmp_path / "before.csv"
    second = tmp_path / "after.csv"
    first.write_text("X_um,Y_um,I_nA,status\n0,0,1,ok\n1,0,2,ok\n")
    second.write_text("X_um,Y_um,I_nA,status\n0,0,3,ok\n1,0,4,ok\n")
    app = _app(tmp_path, [first, second])
    _element(app.selectbox, "Spatial and event tool").select("Before and after differences").run()
    _element(app.selectbox, "Before map").select(str(first))
    _element(app.selectbox, "After map").select(str(second)).run()
    _element(app.button, "Calculate difference map").click().run()
    assert not app.exception and not app.error
    assert app.dataframe[1].value.difference.to_list() == [2, 2]
    _element(app.selectbox, "Spatial and event tool").select("Map quality flags").run()
    _element(app.selectbox, "Spatial map").select(str(first)).run()
    _element(app.button, "Flag map quality").click().run()
    assert not app.exception and not app.error
    assert app.dataframe[1].value.quality.to_list() == ["usable", "usable"]
    assert len(read_history(tmp_path)) == 2
