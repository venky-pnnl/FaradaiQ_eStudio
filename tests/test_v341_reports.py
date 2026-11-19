import base64
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import h5py
import numpy as np
import pandas as pd
import pytest

from faradaiq import workbench_reports as reports
from faradaiq.browser_analysis import analyze_trace
from faradaiq.browser_data import open_data
from faradaiq.browser_plotting import build_impedance_figure, build_xy_figure
from faradaiq.browser_productivity import prepare_xy_series, table_quality
from faradaiq.release_identity import APP_VERSION
from faradaiq.workbench_audit import read_history


@pytest.fixture
def catalog(tmp_path):
    experiment = tmp_path / "source"
    experiment.mkdir()
    rows = np.linspace(-0.5, 0.5, 16)
    pd.DataFrame({"potential_V": rows, "current_mA": rows**2 + 1,
                  "private_unplotted": ["UNPLOTTED-FIXTURE-790"] * len(rows)}).to_csv(
        experiment / "cv_primary.csv", index=False)
    pd.DataFrame({"potential_mV": 1000 * rows, "current_uA": 1000 * (rows**2 + 2)}).to_csv(
        experiment / "cv_overlay.csv", index=False)
    pd.DataFrame({"time_s": np.arange(16.), "potential_V": 0.1 + rows / 100}).to_csv(
        experiment / "ocp_excluded.csv", index=False)
    workspace = tmp_path / "workspace"
    data = open_data(experiment, workspace)
    settings = {"measurement": "cv_primary.csv", "overlays": ["cv_overlay.csv"],
                "representation": "XY overlay", "title": "CV <comparison>",
                "x": "potential_V", "y": "current_mA", "x_title": "Potential", "invert": True}
    return data, workspace, settings


def _xy_display(data, settings):
    catalog = reports.measurement_catalog(data)
    labels = [settings["measurement"], *settings["overlays"]]
    series, _ = prepare_xy_series([(label, data.read_table(catalog[label])) for label in labels],
                                  settings["x"], settings["y"])
    plot_options = {key: settings[key] for key in ("title", "x_title", "invert", "log_x") if key in settings}
    figure = build_xy_figure(series, settings["x"], settings["y"], **plot_options)
    return {"source": str(data.source), "settings": settings, "figure": figure,
            "trace_labels": tuple(label for label, _ in series)}


def test_report_and_evidence_registry_link_only_selected_artifacts(catalog):
    from docx import Document
    from pptx import Presentation

    data, workspace, settings = catalog
    summary = workspace / "Workbench" / "fit.csv"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text("parameter,value\nconductance,3.6\n", encoding="utf-8")
    measurements = ["cv_primary.csv"]
    chosen = reports.list_evidence(data, workspace, measurements, tables=[summary])
    conclusion = {"text": "<script>unsafe()</script> The fit used the selected trace.",
                  "evidence": [chosen[0]["id"], chosen[1]["id"], "included-plot"]}
    receipt = reports.write_report(data, workspace, measurements, title="Study <one>", context="At 25 °C",
                                   methods="Fit the linear response", conclusions=[conclusion], tables=[summary],
                                   settings=settings)
    outputs = {item.suffix: item for item in receipt["files"]}
    html = outputs[".html"].read_text(encoding="utf-8")
    assert "Study &lt;one&gt;" in html and "<script>" not in html
    assert "data:image/png;base64," in html and "data:text/csv;base64," in html
    assert "https://" not in html and "ocp_excluded.csv" not in html
    registry = receipt["registry"]
    for identifier in registry["conclusions"][0]["evidence"]:
        assert f'href="#{identifier}"' in html
        assert f'id="{identifier}"' in html
    plot_ref = next(item for item in registry["evidence"].values() if item["kind"] == "figure")
    assert len(plot_ref["derived_from"]) == 2
    assert "Fit the linear response" in "\n".join(paragraph.text for paragraph in Document(outputs[".docx"]).paragraphs)
    presentation = Presentation(outputs[".pptx"])
    assert any(slide.shapes.title.text == "Methods" for slide in presentation.slides)
    with ZipFile(outputs[".zip"]) as archive:
        assert "evidence_registry.json" in archive.namelist()
        assert "ocp_excluded.csv" not in archive.namelist()
    assert read_history(workspace)[-1]["action"] == "Build selected-evidence report"


def test_publication_vector_formats_values_units_and_source(catalog):
    data, workspace, settings = catalog
    receipt = reports.write_publication_export(data, workspace, settings)
    outputs = {item.suffix: item for item in receipt["files"]}
    svg = outputs[".svg"].read_text(encoding="utf-8")
    assert "<svg " in svg and "<path " in svg and "<image" not in svg
    assert "Potential (V)" in svg
    assert outputs[".pdf"].read_bytes().startswith(b"%PDF-")
    table = pd.read_csv(outputs[".csv"])
    assert set(table["measurement"]) == {"cv_primary.csv", "cv_overlay.csv"}
    assert set(table["x_unit"]) == {"V"}
    assert set(table["y_unit"]) == {"mA"}
    assert table["source_sha256"].str.len().eq(64).all()
    assert np.allclose(table.loc[table.measurement == "cv_overlay.csv", "y"], -(np.linspace(-.5, .5, 16)**2 + 2))
    with ZipFile(outputs[".zip"]) as archive:
        assert set(archive.namelist()) == {"figure.svg", "figure.pdf", "figure_data.csv", "figure_provenance.json"}


@pytest.mark.parametrize("dtype", ["float64", "float32"])
def test_notebook_executes_with_selected_embedded_inputs_and_settings(catalog, monkeypatch, dtype):
    data, workspace, settings = catalog
    read_table = type(data).read_table

    def typed_table(source, path, **options):
        table = read_table(source, path, **options)
        return table.astype({column: dtype for column in table.select_dtypes(include="floating")})

    monkeypatch.setattr(type(data), "read_table", typed_table)
    steps = [{"tool": "plot", "settings": settings},
             {"tool": "table_quality", "measurement": "cv_overlay.csv"},
             {"tool": "trace_metrics", "measurement": "cv_primary.csv", "settings": {"technique": "CV"}}]
    recipe = {"schema": reports.RECIPE_SCHEMA, "steps": steps}
    snapshot = reports.capture_measurements(data, ["cv_primary.csv", "cv_overlay.csv"])
    expected = reports.execute_recipe(snapshot, recipe)
    displayed = _xy_display(data, settings)
    quality, issues = table_quality(snapshot["cv_overlay.csv"].frame)
    metrics = analyze_trace(snapshot["cv_primary.csv"].frame, "CV", identity={"relative_path": "cv_primary.csv"})
    receipt = reports.write_reproducibility_notebook(data, workspace, recipe, list(snapshot))
    content = json.loads(receipt["files"][0].read_text(encoding="utf-8"))
    assert content["nbformat"] == 4 and content["metadata"]["faradaiq"]["application_version"] == APP_VERSION
    assert [row["label"] for row in content["metadata"]["faradaiq"]["inputs"]] == list(snapshot)
    for path in data.tables:
        path.unlink()
    outputs = []
    namespace = {"display": outputs.append}
    for cell in content["cells"]:
        if cell["cell_type"] == "code":
            exec(compile("".join(cell["source"]), "notebook.py", "exec"), namespace)
    curves = namespace["curves"][0]
    pd.testing.assert_frame_equal(curves, expected[0][1].table, check_exact=True)
    for label, trace in zip(displayed["trace_labels"], displayed["figure"].data):
        series = curves.loc[curves["measurement"].eq(label)]
        np.testing.assert_array_equal(series["x"], trace.x)
        np.testing.assert_array_equal(series["y"], trace.y)
        assert set(series["x_unit"]) == {"V"} and set(series["y_unit"]) == {"mA"}
    assert namespace["metrics"] == [{"table": quality.to_dict("records"), "issues": issues}, metrics]
    assert namespace["metrics"] == [expected[1][1], expected[2][1]]
    assert len(outputs) == 4
    inputs = namespace["provenance"]["inputs"]
    for label in snapshot:
        pd.testing.assert_frame_equal(namespace["snapshots"][label].frame, snapshot[label].frame, check_exact=True)
    inputs[0]["csv_base64"] = base64.b64encode(b"changed").decode()
    with pytest.raises(ValueError, match="hash mismatch"):
        namespace["restore_snapshots"](inputs)


def test_scilink_uses_plot_scope_and_unchanged_skill(catalog, monkeypatch):
    from faradaiq import managed_scilink

    def forbidden(*args, **kwargs):
        pytest.fail("Handoff attempted a live SciLink operation")

    monkeypatch.setattr(managed_scilink, "update", forbidden)
    monkeypatch.setattr(managed_scilink, "launch", forbidden)
    data, workspace, settings = catalog
    displayed = _xy_display(data, {**settings, "log_x": True, "overlays": [*settings["overlays"], "ocp_excluded.csv"]})
    active = reports.write_scilink_handoff(data, workspace, displayed_plot=displayed, context="Selected plot")
    files = {path.name: path for path in active["files"]}
    origins = Path(reports.__file__).resolve().parents[1] / "skills" / "electrochemistry_skill.md"
    assert files[origins.name].read_bytes() == origins.read_bytes()
    request = json.loads(files["scilink_request.json"].read_text())
    assert request["approved"] is False and request["analysis_only"] is True
    hdf5 = files[request["data_paths"][0]]
    assert "/tables/" in active["instructions"]
    with h5py.File(hdf5, "r") as session:
        manifest = json.loads(session["manifest_json"][()])
        scope = manifest["scilink_ready"]["experimental_metadata"]
        assert scope["plot_recipe"] == {**settings, "log_x": True}
        assert {ref["label"] for ref in scope["selected_measurements"]} == {"cv_primary.csv", "cv_overlay.csv"}
        assert len(session["tables"]) == 2
        tables = {Path(entry["source_path"]).name: entry for entry in manifest["scilink_ready"]["datasets"]
                  if entry["path"].startswith("/tables/")}
        for plot_trace, ref in zip(displayed["figure"].data, scope["displayed_traces"]):
            entry = tables[ref["staged_table"]]
            visible = plot_trace.x > 0
            expected = np.column_stack([plot_trace.x[visible], plot_trace.y[visible]])
            table = session[entry["path"]]
            np.testing.assert_array_equal(table[:], expected)
            assert ref["dataset"] == entry["path"]
            assert table.attrs["x_axis"] == ref["x_axis"] == "Potential"
            assert entry["columns"] == ["x", "y"] and ref["rows"] == len(expected)
            assert entry["units"] == json.loads(table.attrs["units_json"]) == {"x": "V", "y": "mA"}
        assert b"ocp_excluded.csv" not in session["manifest_json"][()]
        assert str(data.source).encode() not in session["manifest_json"][()]
        assert all(b"private_unplotted" not in doc[:].tobytes() for doc in session["source_documents"].values())
    uploader = json.loads(files["scilink_upload_metadata.json"].read_text())["faradaiq_bundle"]
    assert uploader["displayed_traces"] == scope["displayed_traces"]
    explicit = reports.write_scilink_handoff(data, workspace, selected=["cv_primary.csv"])
    explicit_h5 = next(path for path in explicit["files"] if path.suffix == ".h5")
    with h5py.File(explicit_h5, "r") as session:
        assert len(session["tables"]) == 1
        manifest = json.loads(session["manifest_json"][()])
        assert manifest["scilink_ready"]["experimental_metadata"]["plot_recipe"] is None
        assert any(b"UNPLOTTED-FIXTURE-790" in doc[:].tobytes() for doc in session["source_documents"].values())


def test_scilink_plot_review_uses_browser_display_and_resets_when_context_changes(catalog):
    from streamlit.testing.v1 import AppTest

    data, workspace, _ = catalog
    script = f"""
from pathlib import Path
import streamlit as st
from faradaiq.browser_analysis import measurement_roles
from faradaiq.browser_data import open_data
from faradaiq.browser_plot_ui import render_measurement_plot
from faradaiq.workbench_reports import measurement_catalog
from faradaiq.workbench_reports_ui import render
root = Path({str(data.source)!r})
workspace = Path({str(workspace)!r})
data = open_data(root, workspace)
labels = {{path: label for label, path in measurement_catalog(data).items()}}
if st.radio('Page', ['Analysis', 'Reports']) == 'Analysis':
    primary = root / 'cv_primary.csv'
    table = data.read_table(primary)
    render_measurement_plot(data, labels, primary, table, measurement_roles(table), 'CV')
else:
    render(data, workspace=workspace)
"""
    app = AppTest.from_string(script).run()
    assert not app.exception
    next(widget for widget in app.multiselect if widget.label == "Overlay measurements").set_value(
        [data.source / "cv_overlay.csv", data.source / "ocp_excluded.csv"]).run()
    assert any("Skipped overlay" in message.value for message in app.warning)
    app.radio[0].set_value("Reports").run()
    app.selectbox[0].set_value("SciLink handoff").run()
    assert not app.exception
    assert set(app.dataframe[0].value["Measurement"]) == {"cv_primary.csv", "cv_overlay.csv"}
    assert next(button for button in app.button if button.label == "Prepare local SciLink files").proto.disabled
    assert read_history(workspace) == []
    app.checkbox[0].check().run()
    app.text_area[0].set_value("Changed context").run()
    assert not app.checkbox[0].value
    app.checkbox[0].check().run()
    next(button for button in app.button if button.label == "Prepare local SciLink files").click().run()
    assert not app.exception and not app.error
    assert any(item.proto.label == "scilink_selected.zip" for item in app.get("download_button"))
    assert read_history(workspace)[-1]["inputs"] == [str(data.source / name) for name in ("cv_primary.csv", "cv_overlay.csv")]
    app.text_area[0].set_value("Another context").run()
    assert not app.get("download_button")


def test_evidence_resolution_checks_content_after_relocation(catalog, tmp_path):
    data, workspace, _ = catalog
    selected = ["cv_primary.csv"]
    reference = reports.list_evidence(data, workspace, selected)[0]
    claim = {"text": "The instrument recorded a voltammogram.", "evidence": [reference["id"]]}
    receipt = reports.write_evidence_registry(data, workspace, selected, [claim])
    registry = receipt["registry"]
    assert reports.resolve_evidence(registry)[0]["status"] == "Verified"
    moved = tmp_path / "moved" / "nested"
    moved.mkdir(parents=True)
    expected = Path(reference["path"]).rename(moved / Path(reference["path"]).name)
    outcome = reports.resolve_evidence(registry, roots=[moved.parent])[0]
    assert outcome["path"] == str(expected) and outcome["status"] == "Relocated and verified"
    expected.write_text("altered", encoding="utf-8")
    assert reports.resolve_evidence(registry, roots=[moved.parent])[0]["status"] == "Missing or changed"
    with pytest.raises(ValueError, match="outside"):
        reports.write_evidence_registry(data, workspace, ["cv_overlay.csv"], [claim])


def test_recipe_whitelist_rejects_code_extra_fields_and_out_of_scope_inputs(catalog):
    data, workspace, settings = catalog
    selected = ["cv_primary.csv"]
    valid = {"schema": reports.RECIPE_SCHEMA,
             "steps": [{"tool": "table_quality", "measurement": selected[0]}]}
    result = reports.run_reviewed_recipe(data, workspace, valid, selected)
    inspected = next(path for path in result["files"] if path.name == "step_01_result.json")
    assert json.loads(inspected.read_text())["table"][0]["Finite values"] == 16
    for step in ({"tool": "shell", "command": "touch x"},
                 {"tool": "table_quality", "measurement": selected[0], "python": "import os"},
                 {"tool": "plot", "settings": settings},
                 {"tool": "trace_metrics", "measurement": selected[0], "settings": {"technique": "import os"}},
                 {"tool": "table_quality", "measurement": "../someone-else.csv"}):
        with pytest.raises(ValueError):
            reports.run_reviewed_recipe(data, workspace, dict(valid, steps=[step]), selected)
    assert len(read_history(workspace)) == 1


def test_health_is_local_and_installer_requires_explicit_review(catalog, monkeypatch):
    from faradaiq import managed_scilink

    data, workspace, _ = catalog
    assert data is not None
    monkeypatch.setattr(managed_scilink, "current", lambda: {"version": "3.4", "commit": "a" * 40})
    monkeypatch.setattr(managed_scilink, "running_url", lambda: pytest.fail("Health must not probe a server."))
    calls = []

    def execute(command, **options):
        calls.append((command, options))
        return reports.subprocess.CompletedProcess(command, 0, stdout="token=plain-secret-result")

    monkeypatch.setattr(reports.subprocess, "run", execute)
    health = reports.dependency_health()
    assert not calls
    assert any(row["dependency"] == "SciLink managed runtime" and row["version"] == "3.4"
               for row in health["dependencies"])
    with pytest.raises(ValueError, match="Review"):
        reports.repair_dependency(workspace, "SciLink")
    outcome = reports.repair_dependency(workspace, "SciLink", reviewed=True)
    assert calls[0][0][-3:] == ["-m", "faradaiq.managed_scilink", "update"]
    assert outcome["exit_code"] == 0 and "plain-secret-result" not in outcome["output"]


def test_support_bundle_redacts_standard_credentials_and_omits_raw_by_default(catalog, monkeypatch):
    data, workspace, _ = catalog
    workspace.mkdir(parents=True, exist_ok=True)
    log = workspace / "local.log"
    monkeypatch.setenv("DISTANT_API_KEY", "present-in-environment")
    log.write_text("Authorization: Bearer hidden-bearer-token\nAPI_KEY='hidden-json-token'\n"
                   "Connecting to https://username:hidden-password@localhost/x?custom=hidden-query\n"
                   "Raw runtime echoed present-in-environment\n", encoding="utf-8")
    result = reports.write_support_bundle(workspace, diagnostics={"versions": {"tool": "7.1"}}, logs=[log],
                                          settings={"apiKey": "hidden-nested", "headers": {
                                              "Authorization": "hidden-header"}, "nested": [{"password": "hidden"}]},
                                          data=data)
    with ZipFile(result["files"][0]) as bundle:
        assert bundle.namelist() == ["manifest.json", "diagnostics.json", "settings.json", "logs/01_local.txt"]
        combined = b"\n".join(bundle.read(name) for name in bundle.namelist()).decode("utf-8")
        assert "hidden" not in combined and "present-in-environment" not in combined
        assert "localhost/x" in combined and "versions" in combined
    selected = reports.write_support_bundle(workspace, data=data, include_raw=["cv_primary.csv"])
    with ZipFile(selected["files"][0]) as bundle:
        assert bundle.namelist() == ["manifest.json", "measurements/001_cv_primary.csv"]
        snapshot = pd.read_csv(BytesIO(bundle.read(bundle.namelist()[-1])))
        assert tuple(snapshot.columns) == ("potential_V", "current_mA", "private_unplotted")


def test_report_ui_routes_and_download_survive_reruns(catalog):
    from streamlit.testing.v1 import AppTest

    data, workspace, settings = catalog
    script = f"""
from pathlib import Path
import streamlit as st
from faradaiq.browser_data import open_data
from faradaiq.workbench_reports_ui import render
workspace = Path({str(workspace)!r})
data = open_data({str(data.source)!r}, workspace)
st.session_state["plot_recipe"] = {settings!r}
render(data, workspace=workspace)
"""
    app = AppTest.from_string(script, default_timeout=12).run()
    assert not app.exception
    app.multiselect[0].set_value(["cv_primary.csv"]).run()
    app.multiselect[-1].set_value(["HTML"]).run()
    next(button for button in app.button if button.label == "Build report").click().run()
    assert not app.exception
    assert not app.error
    assert {item.proto.label for item in app.get("download_button")} >= {"report.html", "report_with_evidence.zip"}
    for route in ("Publication figures", "Reproducibility notebook", "SciLink handoff", "Evidence registry",
                  "Reviewed recipes", "Dependency health", "Support bundle", "Report builder"):
        app.selectbox[0].set_value(route).run()
        assert not app.exception, route
    assert {item.proto.label for item in app.get("download_button")} >= {"report.html", "report_with_evidence.zip"}


def test_support_ui_works_without_open_data(tmp_path):
    from streamlit.testing.v1 import AppTest

    script = f"""
from pathlib import Path
from faradaiq.workbench_reports_ui import render
render(None, workspace=Path({str(tmp_path)!r}))
"""
    app = AppTest.from_string(script).run()
    app.selectbox[0].set_value("Support bundle").run()
    next(button for button in app.button if button.label == "Build redacted support bundle").click().run()
    assert not app.exception and not app.error
    assert any(item.proto.label == "support_bundle.zip" for item in app.get("download_button"))
    assert not any(box.label == "Include selected measurement data" for box in app.checkbox)


def test_bode_publication_panel_units_and_normalization(tmp_path):
    source = tmp_path / "spectra"
    source.mkdir()
    spectrum = pd.DataFrame({"frequency_kHz": [0.1, 0.001, 0.01], "Zreal_kohm": [1., 1., 1.],
                             "-Zimag_kohm": [1., 1., 1.]})
    spectrum.to_csv(source / "eis.csv", index=False)
    data = open_data(source, tmp_path)
    settings = {"measurement": "eis.csv", "representation": "Bode"}
    result = reports.write_publication_export(data, tmp_path, settings)["preview"]
    magnitude = result.loc[result["panel"].eq(1)]
    phase = result.loc[result["panel"].eq(2)]
    assert list(magnitude["x"]) == [1., 10., 100.]
    assert np.allclose(magnitude["y"], np.sqrt(2) * 1000)
    assert np.allclose(phase["y"], -45.)
    assert set(magnitude["y_unit"]) == {"ohm"} and set(phase["y_unit"]) == {"deg"}
    assert set(magnitude["x_unit"]) == {"Hz"}
    assert any(path.suffix == ".png" for path in reports.discover_result_files(data, tmp_path))

    figure = build_impedance_figure([("eis.csv", data.read_table(source / "eis.csv"))], representation="Bode")
    displayed = {"source": str(data.source), "settings": settings, "figure": figure,
                 "trace_labels": ("eis.csv", "eis.csv")}
    handoff = reports.write_scilink_handoff(data, tmp_path, displayed_plot=displayed)
    path = next(path for path in handoff["files"] if path.suffix == ".h5")
    with h5py.File(path, "r") as bundle:
        manifest = json.loads(bundle["manifest_json"][()])["scilink_ready"]
        references = manifest["experimental_metadata"]["displayed_traces"]
        entries = {Path(item["source_path"]).name: item for item in manifest["datasets"]
                   if item["path"].startswith("/tables/")}
        assert len(entries) == 2
        for reference, frame, unit in zip(references, (magnitude, phase), ("ohm", "deg")):
            item = entries[reference["staged_table"]]
            assert item["units"] == {"x": "Hz", "y": unit}
            np.testing.assert_array_equal(bundle[item["path"]][:], frame[["x", "y"]].to_numpy())
