"""Local Streamlit workspace for electrochemical data and SciLink handoff."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass, replace
import json
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
import threading
from types import SimpleNamespace

import numpy as np
import pandas as pd
import streamlit as st

from faradaiq.browser_plotting import negative_impedance_label
from faradaiq.release_identity import APP_NAME, APP_VERSION

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = (
    Path(r"C:\Data\ANALYSIS\Faradaiq") if sys.platform == "win32"
    else Path.home() / "Data" / "ANALYSIS" / "FaradaIQ"
) / "eStudio" / "Browser"
PREFERENCES = Path.home() / ".faradaiq" / "estudio-browser.json"
PAGES = ("Data", "Analysis", "Batch jobs", "Reports", "SciLink", "Settings")
MAX_DOWNLOAD = 50 * 1024 * 1024
TECHNIQUES = ("CV", "LSV", "CA", "CP", "OCP", "FSCV", "DPV", "SWV", "NPV", "Stripping", "Deposition")
SCILINK_UPDATE = "Update SciLink"
SCILINK_OPEN = "Open SciLink"


class LocalJob:
    def __init__(self, label, operation, cancelable, on_stop):
        self.label = label
        self.cancelable = cancelable
        self.cancel = threading.Event()
        self.on_stop = on_stop
        self.messages = []
        self._lock = threading.Lock()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="estudio-browser")
        self.future = executor.submit(operation, self.cancel, self.update)
        executor.shutdown(wait=False)

    def update(self, message):
        with self._lock:
            self.messages.append(str(message))
            del self.messages[:-25]

    def progress(self):
        with self._lock:
            return self.messages[-1] if self.messages else "Running on this computer…"

    def stop(self):
        self.cancel.set()
        self.update("Canceling…")
        if self.on_stop is not None:
            threading.Thread(target=self.on_stop, name="estudio-stop", daemon=True).start()


def _job_busy():
    job = st.session_state.get("job")
    return job is not None and not job.future.done()


def _take_job_result():
    job = st.session_state.get("job")
    if job is None or not job.future.done():
        return
    del st.session_state["job"]
    try:
        result = job.future.result()
    except InterruptedError:
        st.session_state["notice"] = ("warning", f"{job.label} canceled.")
    except Exception as error:
        st.session_state["notice"] = ("error", f"{job.label}: {error}")
    else:
        st.session_state[job.label] = result
        if job.label == "Batch export" and result["waiting"]:
            st.session_state["notice"] = ("info", f"Batch paused. {result['waiting']} job(s) can resume.")
        elif job.label in {SCILINK_UPDATE, SCILINK_OPEN}:
            from faradaiq.scilink_runtime_ui import runtime_status

            st.session_state["notice"] = ("success", runtime_status(result))
        else:
            st.session_state["notice"] = ("success", f"{job.label} completed.")


@st.fragment(run_every=1)
def _job_panel():
    job = st.session_state.get("job")
    if job is None:
        return
    if job.future.done():
        st.rerun(scope="app")
    st.info(f"{job.label} · {job.progress()}")
    if job.cancelable and st.button("STOP", key="stop_job", disabled=job.cancel.is_set()):
        job.stop()
    if job.label == "Batch export":
        model = st.session_state.get("batch_queue")
        if model and st.button("Pause after active jobs", disabled=model.pause_after_current or job.cancel.is_set()):
            model.pause_after_current = True
            st.info("Active jobs will finish before the queue pauses.")


def _submit(label, operation, *, cancelable=False, on_stop=None):
    if _job_busy():
        st.warning("Wait for the active job or stop it before starting another one.")
        return
    st.session_state["job"] = LocalJob(label, operation, cancelable, on_stop)
    st.rerun()


def _workspace():
    value = st.session_state["preferences"].get("workspace", str(DEFAULT_WORKSPACE))
    if not value.strip():
        raise ValueError("Choose a local output folder in Settings.")
    path = Path(value).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_preferences():
    try:
        payload = json.loads(PREFERENCES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: payload[key] for key in ("workspace", "scilink_url") if isinstance(payload.get(key), str)}


def _save_preferences(payload):
    PREFERENCES.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=PREFERENCES.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            json.dump(payload, stream, indent=2)
        except BaseException:
            stream.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(PREFERENCES)
    finally:
        temp.unlink(missing_ok=True)


def _open_data(source, *, metadata=None, restored=None):
    from faradaiq.browser_data import open_data

    description = (metadata or {}).get("description") or (metadata or {}).get("experimental_description", "")
    source_path = Path(source).expanduser().resolve() if source else None
    saved_root = (restored or {}).get("analysis_root")
    if saved_root and Path(saved_root).expanduser().is_dir():
        input_path = Path(saved_root).expanduser()
    elif source_path is not None:
        input_path = source_path
    else:
        raise ValueError("The session does not identify any local data.")
    with st.spinner("Opening local data…"):
        data = open_data(input_path, _workspace() / "Imports", description=description)
    if source_path is not None:
        data = replace(data, source=source_path)
    st.session_state["dataset"] = data
    st.session_state["metadata"] = dict(data.metadata) | (metadata or {})
    st.session_state["restored_session"] = restored
    for key in ("table", "analysis_result", "DRT", "PowerPoint", "Portable HTML", "SciLink bundle",
                "Interactive view", "Canonical HDF5", "plot_recipe", "prepared_plot", "analysis-tabs"):
        st.session_state.pop(key, None)
    selections = (restored or {}).get("state") or {}
    st.session_state["selected_table"] = selections.get("active_table")
    if isinstance(selections.get("plot_recipe"), dict):
        st.session_state["plot_recipe"] = selections["plot_recipe"]
    return data


def _show_file(label, path):
    path = Path(path)
    if path.is_file() and path.stat().st_size <= MAX_DOWNLOAD:
        st.download_button(label, path.read_bytes(), file_name=path.name, key=f"{label}:{path}")
    elif path.is_file():
        st.write(label)
        st.code(str(path), language=None)


def _table_labels(data):
    labels = {}
    for path in data.tables:
        try:
            label = path.relative_to(data.analysis_root).as_posix()
        except ValueError:
            label = path.name
        labels[path] = label
    return labels


def _data_page():
    from faradaiq.browser_catalog import render_catalog
    from faradaiq.browser_data import load_session, save_session, stage_browser_uploads

    st.header("Data")
    st.caption("Open data on this computer. Use a local path for large files and network shares mounted here.")
    path = st.text_input("Local file or folder")
    upload_folder = st.checkbox("Select an entire folder", help="Keeps relative paths and instrument companion files together.")
    uploads = st.file_uploader(
        "Choose a folder" if upload_folder else "Choose files",
        accept_multiple_files="directory" if upload_folder else True,
        help="Selected files are sent to the local eStudio process on this computer.",
    )
    if st.button("Open data", type="primary", disabled=_job_busy()):
        try:
            if path.strip():
                source = Path(path.strip()).expanduser()
            else:
                source = stage_browser_uploads(uploads, _workspace() / "Uploads")
            _open_data(source)
        except Exception as error:
            st.error(str(error))

    data = st.session_state.get("dataset")
    if data:
        st.write(f"{len(data.tables)} analysis tables loaded")
        render_catalog(data)
        st.subheader("Experiment context")
        metadata = dict(st.session_state.get("metadata", {}))
        with st.form(f"experiment:{data.source}"):
            sample = st.text_input("Sample name", value=str(metadata.get("sample_name") or ""))
            session_id = st.text_input("Session ID", value=str(metadata.get("session_id") or ""))
            description = st.text_area("Experiment notes", value=str(
                metadata.get("experimental_description") or metadata.get("description") or ""))
            if st.form_submit_button("Save context"):
                metadata.update(sample_name=sample, session_id=session_id,
                                description=description, experimental_description=description)
                st.session_state["metadata"] = metadata

    with st.expander("Open or save a session"):
        session_path = st.text_input("Local session JSON path")
        load_column, save_column = st.columns(2)
        if load_column.button("Open session", disabled=not session_path.strip() or _job_busy()):
            try:
                session = load_session(Path(session_path).expanduser())
                fields = session["browser_workspace"]
                _open_data(fields["source"], metadata=fields.get("metadata"), restored=fields)
                st.session_state["restored_session"] = session
                st.success("Session data reopened.")
            except Exception as error:
                st.error(str(error))
        if save_column.button("Save session", disabled=data is None or not session_path.strip() or _job_busy()):
            try:
                saved = save_session(Path(session_path).expanduser(), source=data.source,
                                     analysis_root=data.analysis_root, metadata=st.session_state.get("metadata"),
                                     browser_state={"active_table": st.session_state.get("selected_table"),
                                                    "plot_recipe": st.session_state.get("plot_recipe")},
                                     previous=st.session_state.get("restored_session"))
                st.success(f"Saved {saved}")
            except Exception as error:
                st.error(str(error))


def _render_value(value):
    if isinstance(value, pd.DataFrame):
        st.dataframe(value, hide_index=True)
    elif is_dataclass(value):
        _render_value(asdict(value))
    elif isinstance(value, dict):
        plain = {}
        for key, item in value.items():
            if isinstance(item, pd.DataFrame):
                st.write(key)
                st.dataframe(item, hide_index=True)
            else:
                plain[key] = item
        if plain:
            st.json(json.loads(json.dumps(plain, default=_json_fallback)))
    elif isinstance(value, list) and value and is_dataclass(value[0]):
        for item in value:
            _render_value(item)
    else:
        st.write(value)


def _json_fallback(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    if is_dataclass(value):
        return asdict(value)
    return str(value)


def _analysis_page():
    from faradaiq import browser_analysis as science
    from faradaiq.browser_plot_ui import measurement_technique, render_measurement_plot, render_native_views

    st.header("Analysis")
    data = st.session_state.get("dataset")
    if data is None or not data.tables:
        st.info("Open data to choose a measurement and analysis.")
        return
    labels = _table_labels(data)
    active = next((index for index, candidate in enumerate(data.tables)
                   if labels[candidate] == st.session_state.get("selected_table")), 0)
    path = st.selectbox("Active measurement", data.tables, index=active, format_func=labels.get, key="table")
    st.session_state["selected_table"] = labels[path]
    try:
        table = data.read_table(path)
    except Exception as error:
        st.error(f"Could not read the selected measurement: {error}")
        return
    if table.empty or len(table.columns) < 2:
        st.warning("Choose a table with at least two columns and one row.")
        return
    roles = science.measurement_roles(table)
    technique_hint = measurement_technique(data, path, roles)
    plot, tools, views = st.tabs(("Plots and table", "Analysis tools", "Interactive views"),
                                 key="analysis-tabs", on_change="rerun")
    with plot:
        render_measurement_plot(data, labels, path, table, roles, technique_hint)
        with st.expander("View measurement table"):
            st.dataframe(table, hide_index=True)
    with views:
        render_native_views(data, labels, path, workspace=_workspace, submit=_submit,
                            busy=_job_busy, show_file=_show_file)
    with tools:
        modes = ("EIS and DRT", "Voltammetry and transients", "CV peaks and baselines",
                 "Tafel and corrosion", "Mott–Schottky", "Spatial points")
        default_mode = ("Mott–Schottky" if roles.get("potential") and "mott" in path.stem.casefold()
                        else "EIS and DRT" if roles.get("frequency") else "Voltammetry and transients")
        mode = st.selectbox("Analysis tool", modes, index=modes.index(default_mode), key=f"analysis-tool:{path}")
        if mode in {"EIS and DRT", "Mott–Schottky"}:
            _impedance_tools(science, table, roles, path, mode)
        elif mode == "Tafel and corrosion":
            _tafel_tools(science, table, roles, path)
        elif mode == "CV peaks and baselines":
            _cv_tools(science, table, roles, path)
        elif mode == "Spatial points":
            _spatial_tools(science, table, roles, path)
        else:
            with st.form(f"trace:{path}"):
                default_technique = technique_hint
                technique = st.selectbox("Technique", TECHNIQUES, index=TECHNIQUES.index(default_technique))
                potential = _axis("Potential column", table, roles.get("potential"), True)
                current = _axis("Current column", table, roles.get("current"), True)
                time = _axis("Time column", table, roles.get("time"), True)
                tail = st.number_input("OCP tail (% of recorded time)", min_value=1.0, max_value=100.0, value=20.0)
                applied = st.text_input("Applied current for CP (A, optional)",
                                        help="Used to calculate charge when a current column is absent.")
                run = st.form_submit_button("Calculate trace metrics")
            if run:
                _calculate(path, mode, lambda: science.analyze_trace(
                    table, technique, potential=potential, current=current, time=time, final_time_percent=tail,
                    applied_current_A=float(applied) if technique == "CP" and applied.strip() else None))
        result = st.session_state.get("analysis_result")
        if result and result[:2] == (path, mode):
            _render_value(result[2])
            _download_analysis_artifacts(result[2])
        drt = st.session_state.get("DRT")
        if mode == "EIS and DRT" and drt and drt.get("source") == path:
            if drt.get("completed_modes"):
                st.success("Completed DRT methods: " + ", ".join(drt["completed_modes"]))
            if drt.get("failed_modes"):
                st.warning("DRT methods with errors: " + ", ".join(drt["failed_modes"]))
            artifacts = (*drt["drt_csvs"], *drt.get("csvs", ()), drt.get("settings_path"), drt.get("summary_path"), drt.get("log_path"))
            for artifact in dict.fromkeys(artifacts):
                if artifact:
                    _show_file(f"Download {Path(artifact).name}", artifact)
            for figure in drt["figures"]:
                st.image(str(figure))
            st.code(str(drt["output_dir"]), language=None)


def _axis(label, table, detected, optional=False):
    columns = ([None] if optional else []) + list(table.columns)
    return st.selectbox(label, columns, index=columns.index(detected) if detected in columns else 0,
                        format_func=lambda value: "Auto-detect" if value is None else str(value))


def _calculate(path, mode, call):
    previous = st.session_state.get("analysis_result")
    if previous and previous[:2] == (path, mode):
        st.session_state.pop("analysis_result")
    try:
        st.session_state["analysis_result"] = (path, mode, call())
    except Exception as error:
        st.error(str(error))


def _download_analysis_artifacts(value):
    from faradaiq.browser_plotting import result_artifacts

    for artifact in result_artifacts(value):
        _show_file(f"Download {artifact.name}", artifact)


def _impedance_tools(science, table, roles, path, mode):
    from faradaiq.browser_science_controls import drt_settings

    with st.form(f"{mode}:{path}"):
        inputs = st.columns(3)
        with inputs[0]:
            frequency = _axis("Frequency column", table, roles.get("frequency"), True)
        with inputs[1]:
            real = _axis("Real impedance column", table, roles.get("zreal"), True)
        with inputs[2]:
            imaginary = _axis("Imaginary impedance column", table, roles.get("zimag"), True)
        convention = st.selectbox("Imaginary column convention",
                                  ("Detect from header", "Signed Im(Z)", "Negative imaginary −Im(Z)"))
        if mode == "Mott–Schottky":
            potential = _axis("DC potential column", table, roles.get("potential"), True)
            execute = st.form_submit_button("Calculate Mott–Schottky")
        else:
            circuit = st.selectbox("Circuit", science.CIRCUITS, index=science.CIRCUITS.index("R-RC"))
            action = st.radio("Method", ("Circuit fit", "Linear Kramers–Kronig", "DRT", "DearEIS handoff"), horizontal=True)
            drt_options = drt_settings(key=f"drt:{path}")
            execute = st.form_submit_button("Run analysis", disabled=_job_busy())
    if not execute:
        return
    negate = convention == "Negative imaginary −Im(Z)" or (
        convention == "Detect from header" and negative_impedance_label(imaginary or roles.get("zimag")))
    if mode == "Mott–Schottky":
        _calculate(path, mode, lambda: science.analyze_mott_schottky(
            table, potential=potential, frequency=frequency, real=real,
            imaginary=imaginary, negate_imaginary=negate, output_dir=_workspace() / "Results"))
        return
    try:
        eis = science.eis_table(table, frequency, real, imaginary, negate_imaginary=negate)
    except Exception as error:
        st.error(str(error))
        return
    if action == "DRT":
        output_dir = _workspace() / "Results"
        def operation(cancel, report):
            return science.run_drt(
                eis, output_dir, cancel, progress_callback=report, **drt_options,
            ) | {"source": path}
        _submit("DRT", operation, cancelable=True)
    elif action == "DearEIS handoff":
        _calculate(path, mode, lambda: science.make_deareis_handoff(eis, _workspace() / "Results"))
    else:
        def impedance_result():
            if action != "Circuit fit":
                return science.validate_impedance(eis)
            result = science.fit_circuit(eis, circuit, str(path))
            return {"Fit": asdict(result), **({"Fitted spectrum": science.circuit_trace(eis, result)}
                                             if result.success else {})}
        _calculate(path, mode, impedance_result)


def _cv_tools(science, table, roles, path):
    from faradaiq.browser_science_controls import cv_scan_rate, pysimple_settings

    mode = st.radio("Method", ("eStudio branch metrics", "PySimpleEChem"), horizontal=True)
    potential = _axis("Potential column", table, roles.get("potential"), True)
    current = _axis("Current column", table, roles.get("current"), True)
    with st.form(f"cv:{path}:{mode}"):
        if mode == "eStudio branch metrics":
            function = science.analyze_cv_branches
            options = {"scan_rate_V_s": cv_scan_rate(key=f"cv:{path}")}
        else:
            function = science.analyze_pysimple_cv
            options = {"parameters": pysimple_settings(table, potential=potential, current=current, key=f"cv:{path}")}
        submit = st.form_submit_button("Find peaks and baselines")
    if submit:
        _calculate(path, "CV peaks and baselines", lambda: function(
            table, _workspace() / "Results", potential=potential, current=current, **options))


def _tafel_tools(science, table, roles, path):
    from faradaiq.browser_science_controls import corrosion_settings, tafel_settings

    mode = st.radio("Calculation", ("Tafel fit", "Corrosion metrics"), horizontal=True)
    with st.form(f"tafel:{path}:{mode}"):
        potential = _axis("Potential column", table, roles.get("potential"), True)
        current = _axis("Current column", table, roles.get("current"), True)
        settings = (tafel_settings if mode == "Tafel fit" else corrosion_settings)(key=f"{mode}:{path}")
        execute = st.form_submit_button("Analyze polarization")
    if execute:
        parameters = dict(settings=settings, potential=potential, current=current, output_dir=_workspace() / "Results")
        if mode == "Tafel fit":
            _calculate(path, "Tafel and corrosion", lambda: science.analyze_tafel(table, **parameters))
        else:
            _calculate(path, "Tafel and corrosion", lambda: science.analyze_corrosion(
                table, identity={"source_path": str(path)}, **parameters))


def _spatial_tools(science, table, roles, path):
    import plotly.express as px

    x = _axis("X position column", table, roles.get("x"), True)
    y = _axis("Y position column", table, roles.get("y"), True)
    signal = _axis("Signal column", table, roles.get("signal"), True)
    if st.button("Show spatial points"):
        _calculate(path, "Spatial points", lambda: science.spatial_points(table, x=x, y=y, signal=signal))
    result = st.session_state.get("analysis_result")
    if result and result[:2] == (path, "Spatial points") and isinstance(result[2], pd.DataFrame):
        points = result[2]
        metric = next((key for key in ("mean", "max", "peak_abs") if key in points), None)
        if {"x_m", "y_m"}.issubset(points.columns) and metric:
            st.plotly_chart(px.scatter(points, x="x_m", y="y_m", color=metric,
                                       labels={"x_m": "X (m)", "y_m": "Y (m)"}))


def _analysis_root_widget(label, key):
    data = st.session_state.get("dataset")
    return st.text_input(label, value=str(data.analysis_root) if data else "",
                         key=f"{key}:{data.analysis_root if data else 'empty'}")


def _batch_page():
    from faradaiq._legacy import legacy_module
    from faradaiq.v233_batch_jobs import BatchQueueModel
    from faradaiq.browser_batch_controls import render_job_settings, run_queue, save_queue_file

    st.header("Batch jobs")
    st.caption("Use the native eStudio export queue and its saved checkpoints.")
    model = st.session_state.get("batch_queue")
    if model is None:
        try:
            workspace = _workspace()
        except (OSError, ValueError) as error:
            st.error(str(error))
            return
        model = BatchQueueModel(
            SimpleNamespace(output_dir_var=SimpleNamespace(get=lambda: str(workspace / "Batch"))),
            vars(legacy_module()),
        )
        st.session_state["batch_queue"] = model

    busy = _job_busy()
    dataset = st.session_state.get("dataset")
    sources = st.text_area("Instrument files or folders, one local path per line",
                           value=str(dataset.source) if dataset else "")
    if st.button("Add batch jobs", disabled=not sources.strip() or busy):
        try:
            paths = [Path(line.strip()).expanduser() for line in sources.splitlines() if line.strip()]
            if missing := [str(path) for path in paths if not path.exists()]:
                raise ValueError("Could not find: " + ", ".join(missing))
            model.add_paths(paths, open_window=False)
        except Exception as error:
            st.error(str(error))
    queue_path = st.text_input("Saved jobs JSON", value=str(model.queue_path or ""))
    queue_buttons = st.columns(2)
    if queue_buttons[0].button("Open jobs JSON", disabled=not queue_path.strip() or busy):
        try:
            model.load_queue(Path(queue_path).expanduser())
        except (OSError, ValueError) as error:
            st.error(str(error))
    if queue_buttons[1].button("Save jobs JSON", disabled=not queue_path.strip() or busy or not model.jobs):
        try:
            st.success(f"Saved {save_queue_file(model, queue_path)}")
        except (OSError, ValueError) as error:
            st.error(str(error))
    if not model.jobs:
        return
    st.dataframe(pd.DataFrame([{
        "Experiment": job.experiment_name, "Status": job.status, "Source": job.source_path,
        "Output": job.output_dir, "Progress": f"{job.progress:.1f}%", "Detail": job.message,
    } for job in model.jobs]), hide_index=True)
    if model.queue_path:
        st.code(str(model.queue_path), language=None)
    st.caption("Batch export requires an original format supported by the desktop export queue.")
    labels = {job.job_id: f"{job.experiment_name} · {job.job_id}" for job in model.jobs}
    chosen = st.multiselect("Jobs to run", tuple(labels), format_func=labels.get, disabled=busy)
    render_job_settings(model, labels, busy=busy, show_file=_show_file)
    workers = st.number_input("Parallel workers", min_value=1, max_value=8,
                              value=max(1, min(8, model.parallel_workers)), disabled=busy)
    actions = st.columns(3)
    run_selected = actions[0].button("Run selected jobs", disabled=not chosen or busy, type="primary")
    pending = [job.job_id for job in model.jobs if job.status != "Completed"]
    run_all = actions[1].button("Run queued and unfinished jobs", disabled=not pending or busy)
    if actions[2].button("Remove selected jobs", disabled=not chosen or busy):
        model.remove_jobs(chosen)
        st.rerun()
    if run_selected or run_all:
        selection = chosen if run_selected else pending
        try:
            by_id = {job.job_id: job for job in model.jobs}
            destinations = [Path(by_id[job_id].output_dir).expanduser().resolve() for job_id in selection]
            if workers > 1 and any(
                p.is_relative_to(q) or q.is_relative_to(p)
                for index, p in enumerate(destinations) for q in destinations[index + 1:]
            ):
                raise ValueError("Choose separate output folders for parallel jobs.")
            queued = model.enqueue(selection)
            if not queued:
                raise ValueError("No jobs were queued.")
        except (OSError, ValueError) as error:
            st.error(str(error))
            return

        model.pause_after_current = False
        _submit("Batch export", lambda cancel, report: run_queue(model, queued, workers, cancel, report),
                cancelable=True, on_stop=model.stop_all_exports)


def _reports_page():
    from faradaiq.browser_data import export_browser_bundle
    from faradaiq.scilink_browser_handoff import open_local_folder
    from faradaiq.v290_portable_browser import write_portable_browser
    from faradaiq.v290_powerpoint_summary import write_powerpoint_summary

    st.header("Reports")
    root = _analysis_root_widget("Analysis-ready folder", "reports_root")
    name = st.text_input("Experiment name")
    batch_zips = st.checkbox("Create portable batch ZIPs")
    data = st.session_state.get("dataset")
    buttons = st.columns(3)
    if buttons[2].button("Export canonical HDF5", disabled=data is None or _job_busy()):
        metadata = dict(st.session_state.get("metadata", {}))
        export_dir = _workspace() / "Bundle exports"
        _submit("Canonical HDF5", lambda cancel, report: export_browser_bundle(
            data, export_dir, sample_name=name or None, metadata=metadata,
            progress_callback=report, cancel_event=cancel), cancelable=True)
    if buttons[0].button("Make portable HTML", disabled=not root or _job_busy()):
        _submit("Portable HTML", lambda _cancel, _report: write_portable_browser(
            Path(root).expanduser(), experiment_name=name or None, create_batch_zips=batch_zips))
    if buttons[1].button("Make PowerPoint", disabled=not root or _job_busy()):
        _submit("PowerPoint", lambda _cancel, _report: write_powerpoint_summary(
            Path(root).expanduser(), experiment_name=name or None, use_direct_llm=False))
    canonical = st.session_state.get("Canonical HDF5")
    if canonical:
        _show_file("Download canonical HDF5", canonical["h5_path"])
        _show_file("Download canonical manifest", canonical["json_path"])
        st.code(str(Path(canonical["h5_path"]).parent), language=None)
    for key in ("Portable HTML", "PowerPoint"):
        receipt = st.session_state.get(key)
        if not receipt:
            continue
        if receipt.get("html_path"):
            path = Path(receipt["html_path"])
            st.code(str(path), language=None)
            if st.button("Open portable report folder"):
                try:
                    open_local_folder(path.parent)
                except OSError as error:
                    st.error(str(error))
            st.caption("Share the complete report folder to keep its relative data and media links.")
        if receipt.get("pptx_path"):
            _show_file("Download PowerPoint", receipt["pptx_path"])


def _scilink_runtime_panel():
    from faradaiq import managed_scilink
    from faradaiq.scilink_runtime_ui import activate_scilink, runtime_identity

    st.subheader("SciLink Streamlit")
    metadata = managed_scilink.current()
    running = managed_scilink.running_url() if metadata else None
    if metadata:
        state = "Running on this computer" if running else "Installed on this computer"
        st.caption(f"{runtime_identity(metadata)} · {state}")
    else:
        st.caption("Update SciLink to install the local runtime and open its browser workspace.")
    update, launch = st.columns(2)
    if update.button(SCILINK_UPDATE, type="primary", disabled=_job_busy()):
        _submit(SCILINK_UPDATE, lambda _cancel, progress: activate_scilink(update=True, progress=progress))
    if running:
        launch.link_button(SCILINK_OPEN, running)
    elif launch.button(SCILINK_OPEN, disabled=not metadata or _job_busy()):
        _submit(SCILINK_OPEN, lambda _cancel, progress: activate_scilink(progress=progress))


def _scilink_page():
    from faradaiq.browser_service_ui import show_direct_services
    from faradaiq.scilink_bundle import make_scilink_ready_bundle
    from faradaiq.scilink_browser_handoff import (
        DEFAULT_SCILINK_URL, SCILINK_STREAMLIT_COMMAND, normalize_scilink_url, open_local_folder,
        streamlit_analysis_prompt, streamlit_handoff_files, streamlit_handoff_text,
    )

    st.header("SciLink")
    st.caption("Prepare a local data bundle, review its files, then attach them in your SciLink browser session.")
    _scilink_runtime_panel()
    root = _analysis_root_widget("Analysis-ready folder for SciLink", "scilink_root")
    data = st.session_state.get("dataset")
    root_path = Path(root).expanduser().resolve() if root.strip() else None
    same_experiment = data is not None and root_path == data.analysis_root.resolve()
    metadata = dict(st.session_state.get("metadata", {})) if same_experiment else {}
    name = st.text_input("Sample name for the bundle", value=str(metadata.get("sample_name") or ""),
                         key=f"bundle_name:{root}")
    if st.button("Make SciLink-ready bundle", type="primary", disabled=not root or _job_busy()):
        _submit("SciLink bundle", lambda cancel, report: make_scilink_ready_bundle(
            root_path, sample_name=name or None, metadata=metadata,
            progress_callback=report, cancel_event=cancel), cancelable=True)

    receipt = st.session_state.get("SciLink bundle")
    existing = st.text_input("Or reopen a SciLink-ready HDF5 bundle")
    if existing.strip():
        receipt = Path(existing.strip()).expanduser()
    if receipt:
        st.subheader("Review and attach")
        try:
            files = streamlit_handoff_files(receipt)
        except (OSError, ValueError) as error:
            st.error(str(error))
        else:
            folder = files["Data"].parent
            if st.button("Open bundle folder"):
                try:
                    open_local_folder(folder)
                except OSError as error:
                    st.error(str(error))
            st.code(str(folder), language=None)
            for label, path in files.items():
                _show_file(f"Download {label}", path)
            with st.expander("File selection and experimental context"):
                st.code(streamlit_handoff_text(receipt), language=None)
                try:
                    st.json(json.loads(files["Metadata"].read_text(encoding="utf-8")))
                except (OSError, ValueError) as error:
                    st.error(f"Could not read the metadata: {error}")
            with st.expander("Files used to prepare the HDF5"):
                manifest_path = files["Data"].with_suffix(".json")
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    source_files = manifest.get("scilink_ready", {}).get("source_files", [])
                    st.dataframe(pd.DataFrame(source_files).reindex(columns=["path", "size", "sha256"]),
                                 hide_index=True)
                    st.caption("The HDF5 includes original table and metadata documents under /source_documents/.")
                except (AttributeError, OSError, TypeError, ValueError) as error:
                    st.error(f"Could not read the full bundle manifest: {error}")
            st.text_area("Suggested SciLink question", streamlit_analysis_prompt(receipt),
                         key=f"prompt:{folder}", height=120)

    with st.expander("Connect to another local SciLink instance"):
        saved = st.session_state["preferences"].get("scilink_url", DEFAULT_SCILINK_URL)
        address = st.text_input("Local SciLink address", value=saved)
        try:
            url = normalize_scilink_url(address)
        except ValueError as error:
            st.warning(str(error))
        else:
            st.link_button("Open local SciLink", url)
            if url != saved and st.button("Remember SciLink address"):
                preferences = st.session_state["preferences"] | {"scilink_url": url}
                try:
                    _save_preferences(preferences)
                except OSError as error:
                    st.error(str(error))
                else:
                    st.session_state["preferences"] = preferences
                    st.success("Saved the local SciLink address.")
        st.code(SCILINK_STREAMLIT_COMMAND, language="bash")
    st.caption("After attaching the files in SciLink, review the question and submit there. "
               "SciLink can use an external provider if configured to do so.")
    show_direct_services(workspace=_workspace, submit=_submit, busy=_job_busy, show_file=_show_file, bundle=receipt)


def _settings_page():
    st.header("Settings")
    saved = st.session_state["preferences"].get("workspace", str(DEFAULT_WORKSPACE))
    output = st.text_input("Local output folder", value=saved)
    if st.button("Save output folder", disabled=not output.strip()):
        try:
            path = Path(output).expanduser().resolve()
            path.mkdir(parents=True, exist_ok=True)
            preferences = st.session_state["preferences"] | {"workspace": str(path)}
            _save_preferences(preferences)
        except OSError as error:
            st.error(str(error))
        else:
            st.session_state["preferences"] = preferences
            st.success("Saved the local output folder.")
    st.write("Browser startup, data reading, plots, and deterministic analysis run on this computer.")
    st.caption("SciLink and other model services follow the settings of the application where you submit a request.")
    st.write(str(ROOT))


def main():
    st.set_page_config(
        page_title=f"{APP_NAME} Browser {APP_VERSION}",
        page_icon=str(ROOT / "assets/estudio.png"), layout="wide", initial_sidebar_state="expanded",
    )
    if "preferences" not in st.session_state:
        st.session_state["preferences"] = _load_preferences()
    _take_job_result()
    if notice := st.session_state.pop("notice", None):
        getattr(st, notice[0])(notice[1])
    with st.sidebar:
        st.title(APP_NAME)
        st.caption(f"{APP_VERSION}  ·  Local browser")
        page = st.radio("Workspace", PAGES, label_visibility="collapsed")
        st.caption("Local session")
        if data := st.session_state.get("dataset"):
            st.write(data.source.name)
    _job_panel()
    pages = {"Data": _data_page, "Analysis": _analysis_page, "Batch jobs": _batch_page, "Reports": _reports_page,
             "SciLink": _scilink_page, "Settings": _settings_page}
    pages[page]()


if __name__ == "__main__":
    main()
