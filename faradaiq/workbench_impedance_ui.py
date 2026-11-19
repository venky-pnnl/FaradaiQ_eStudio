"""Interactive circuit fitting, lin-KK, and DRT views for the browser workbench."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from . import browser_analysis as analysis
from . import workbench_impedance as engine
from .readable_names import new_folder
from .workbench_audit import record_operation


_METHODS = {
    "circuits": "Fit complex circuit models",
    "lin_kk": "Run lin-KK batch",
    "drt": "Explore DRT regularization",
    "maps": "Build impedance condition maps",
}


def _key(prefix, label):
    return {"key": prefix + label, "persist_state": "session"}


def _fingerprint(value):
    return sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _source_name(data, path):
    record = data.records.get(path, {})
    title = record.get("display_name") or path.name
    instrument = record.get("instrument")
    relative = path.relative_to(data.analysis_root) if path.is_relative_to(data.analysis_root) else path
    return " · ".join([*(str(x) for x in (instrument,) if x), str(title), str(relative)])


def _preview_columns(data, path, prefix):
    frame = data.read_table(path, max_rows=5)
    roles = analysis.measurement_roles(frame)
    options = list(frame.columns)
    if not options:
        raise ValueError("The table does not have any measurement columns.")
    options = [None, *options]
    columns = []
    for role, label in (("frequency", "Frequency column"), ("zreal", "Real impedance column"),
                        ("zimag", "Imaginary impedance column")):
        found = roles.get(role)
        index = options.index(found) if found in options else 0
        columns.append(st.selectbox(label, options, index=index, format_func=lambda name: name or "Detect",
                                    **_key(prefix, role)))
    return columns


def _input_options(data, paths, prefix):
    with st.expander("Frequency selection and spectrum columns"):
        left, right = st.columns(2)
        low = left.number_input("Minimum frequency (Hz)", min_value=0.0, value=None, format="%.8g",
                                **_key(prefix, "frequency_min"))
        high = right.number_input("Maximum frequency (Hz)", min_value=0.0, value=None, format="%.8g",
                                  **_key(prefix, "frequency_max"))
        excluded = st.text_input("Exclude frequency intervals (Hz)", placeholder="40–50, 100–110",
                                 help="Intervals include both endpoints. Use a colon or an en dash between bounds.",
                                 **_key(prefix, "frequency_excluded"))
        values = {}
        for path in paths:
            st.markdown(f"**{_source_name(data, path)}**")
            box = prefix + "input_" + _fingerprint(path) + "_"
            try:
                frequency, real, imaginary = _preview_columns(data, path, box)
            except (OSError, ValueError, TypeError) as error:
                st.warning(str(error))
                frequency = real = imaginary = None
            negate = st.checkbox("Imaginary column contains −Im(Z)", value=engine.negative_imaginary_label(imaginary),
                                 **_key(box, "negate"))
            rows = st.text_input("Exclude source data rows", placeholder="7, 21",
                                 help="Enter 1-based data row numbers, counting the first row below the header as 1.",
                                 **_key(box, "rows"))
            values[str(path)] = {"frequency": frequency, "real": real, "imaginary": imaginary,
                                 "negate_imaginary": negate, "excluded_rows": rows}
    return {"minimum_hz": low, "maximum_hz": high, "excluded_ranges": excluded, "columns": values}


def _intervals(value):
    intervals = []
    for token in value.split(","):
        if token.strip():
            parts = re.split(r"[:–—]", token)
            if len(parts) != 2:
                raise ValueError("Enter excluded frequency intervals as lower:upper, separated by commas.")
            intervals.append(tuple(map(float, parts)))
    return tuple(intervals)


def _excluded_rows(value):
    try:
        rows = tuple(int(token) for token in re.split(r"[,\s]+", value.strip()) if token)
    except ValueError as error:
        raise ValueError("Enter source data rows as comma-separated positive integers.") from error
    if any(row < 1 for row in rows):
        raise ValueError("Source data row numbers start at 1.")
    return rows


def _condition_controls(data, paths, prefix):
    axis = st.selectbox("Condition axis", list(engine.CONDITION_AXES), **_key(prefix, "axis"))
    step = st.checkbox("Split tables into consecutive condition steps",
                       help="Use a recorded condition column with one value per spectrum step.",
                       **_key(prefix, "split"))
    overrides = {}
    bases = {}
    if not step:
        for path in paths:
            overrides[str(path)] = st.number_input(f"{_source_name(data, path)} · {axis}",
                                                   value=None, format="%.8g",
                                                   placeholder="Use recorded value",
                                                   **_key(prefix, f"condition_{axis}_{_fingerprint(path)}"))
            if overrides[str(path)] is not None and axis in {"Potential (V)", "Time (s)"}:
                bases[str(path)] = st.text_input(f"{_source_name(data, path)} · Reference basis",
                                                help="Enter the reference used for the supplied value, such as SHE "
                                                     "or the elapsed time origin.",
                                                **_key(prefix, f"basis_{axis}_{_fingerprint(path)}"))
    return {"axis": axis, "split": step, "supplied_conditions": overrides, "supplied_bases": bases}


def _prepare(data, paths, settings, conditions=None):
    intervals = _intervals(settings["excluded_ranges"])
    spectra = []
    errors = {}
    recorded = {}
    condition_errors = {}
    sources = []
    for path in paths:
        name = str(path.relative_to(data.analysis_root)) if path.is_relative_to(data.analysis_root) else str(path)
        record = data.records.get(path, {})
        source = {"data_path": str(path), "original_path": str(record.get("source_path") or path),
                  "original_table_path": str(record.get("original_table_path") or path),
                  "display_name": str(record.get("display_name") or path.name), "tags": record.get("tags", {}),
                  "instrument": record.get("instrument", ""), "handle_id": record.get("handle_id", "")}
        column_settings = dict(settings["columns"][str(path)])
        try:
            rows = _excluded_rows(column_settings.pop("excluded_rows"))
            mask = engine.FrequencyMask(settings["minimum_hz"], settings["maximum_hz"], intervals, rows)
            table = data.read_table(path)
            groups = [(None, None)]
            if conditions and conditions["split"]:
                groups = engine.condition_step_rows(table, conditions["axis"], record=record)
        except (OSError, ValueError, TypeError) as error:
            errors[name] = str(error)
            sources.append({"spectrum": name, "selection_step": len(sources) + 1, **source})
            continue
        for step, (condition, positions) in enumerate(groups, 1):
            step_name = f"{name} · step {step}" if positions is not None else name
            sources.append({"spectrum": step_name, "selection_step": len(sources) + 1, **source})
            subset = table.iloc[positions] if positions is not None else table
            if conditions:
                try:
                    recorded[step_name] = condition or engine.recorded_condition(
                        subset, record, conditions["axis"], supplied=conditions["supplied_conditions"].get(str(path)),
                        supplied_basis=conditions.get("supplied_bases", {}).get(str(path)))
                except ValueError as error:
                    condition_errors[step_name] = str(error)
            try:
                spectra.append(engine.spectrum_from_table(step_name, subset, mask,
                                                          source_rows=positions + 1 if positions is not None else None,
                                                          **column_settings))
            except (OSError, ValueError, TypeError) as error:
                errors[step_name] = str(error)
    return spectra, errors, recorded, condition_errors, sources


def _add_branch(prefix, kind):
    branch_ids = st.session_state[prefix + "branch_ids"]
    next_id = st.session_state[prefix + "next_branch"]
    st.session_state[prefix + "branch_ids"] = [*branch_ids, next_id]
    st.session_state[prefix + "next_branch"] = next_id + 1
    st.session_state[f"{prefix}branch_{next_id}"] = kind


def _remove_branch(prefix, id_to_remove):
    st.session_state[prefix + "branch_ids"] = [i for i in st.session_state[prefix + "branch_ids"] if i != id_to_remove]


def _circuit_builder(prefix):
    st.session_state.setdefault(prefix + "branch_ids", [1])
    st.session_state.setdefault(prefix + "next_branch", 2)
    branches = []
    with st.expander("Circuit constructor", expanded=True):
        for index, branch_id in enumerate(st.session_state[prefix + "branch_ids"], 1):
            column, button = st.columns([3, 1], vertical_alignment="bottom")
            branches.append(column.selectbox(f"Parallel branch {index}", ["RC", "CPE"],
                                             **_key(prefix, f"branch_{branch_id}")))
            button.button("Remove", key=f"{prefix}remove_branch_{branch_id}", on_click=_remove_branch,
                          args=(prefix, branch_id))
        left, right = st.columns(2)
        left.button("Add RC branch", key=prefix + "add_rc", on_click=_add_branch, args=(prefix, "RC"))
        right.button("Add CPE branch", key=prefix + "add_cpe", on_click=_add_branch, args=(prefix, "CPE"))
        warburg = st.checkbox("Series semi-infinite Warburg", **_key(prefix, "warburg"))
        circuit = engine.Circuit(tuple(branches), warburg)
        st.markdown(f"**{circuit.label}**")
        st.caption("Q is expressed in S·sᵅ. The CPE exponent α ranges from 0 to 1.")
    return circuit


def _parameter_controls(circuit, prefix):
    params = {}
    with st.expander("Initial values, bounds, and parameter scope"):
        st.caption("Blank initial values are estimated from the spectra. Blank bounds use each parameter’s "
                   "physical range. Shared parameters have one fitted value across all spectra in a global fit.")
        for name in circuit.parameters:
            st.markdown(f"**{name}**")
            columns = st.columns([2, 2, 2, 1, 1], vertical_alignment="bottom")
            key = prefix + _fingerprint(circuit.label + name) + "_"
            numeric = [columns[i].number_input(label, value=None, format="%.8g", **_key(key, label.lower()))
                       for i, label in enumerate(("Initial", "Lower bound", "Upper bound"))]
            fixed = columns[3].checkbox("Fixed", **_key(key, "fixed"))
            shared = columns[4].checkbox("Shared", **_key(key, "shared"))
            params[name] = engine.Parameter(*numeric, fixed=fixed, shared=shared)
    return params


def _circuit_from_settings(value):
    return engine.Circuit(tuple(value["branches"]), value["warburg"])


def _circuit_setting(circuit, parameters):
    return {"circuit": asdict(circuit), "parameters": {name: asdict(p) for name, p in parameters.items()}}


def _fit_controls(prefix):
    circuit = _circuit_builder(prefix)
    parameters = _parameter_controls(circuit, prefix)
    current = _circuit_setting(circuit, parameters)
    saved = st.session_state.setdefault(prefix + "candidates", {})
    if st.button("Save circuit as comparison candidate", key=prefix + "save_candidate"):
        saved = {**saved, circuit.label: current}
        st.session_state[prefix + "candidates"] = saved
        st.success(f"Saved {circuit.label} with its parameter settings.")
    available = {c.label: _circuit_setting(c, {}) for c in engine.PRESET_CIRCUITS}
    available.update(saved)
    chosen = st.multiselect("Additional models to compare", list(available), **_key(prefix, "comparison"))
    global_fit = st.radio("Fit scope", ["Each spectrum", "Global fit"], horizontal=True,
                          **_key(prefix, "scope")) == "Global fit"
    confidence = st.number_input("Confidence level (%)", min_value=1.0, max_value=99.9, value=95.0,
                                 **_key(prefix, "confidence")) / 100
    return {"models": [current, *(available[label] for label in chosen if label != circuit.label)],
            "global_fit": global_fit, "confidence": confidence}


def _drt_controls(prefix):
    st.caption(engine.DRT_METHOD)
    left, right = st.columns(2)
    values = st.text_input("DRT lambdas", "0.00001, 0.0001, 0.001, 0.01",
                           help="Enter positive values separated by commas.", **_key(prefix, "lambdas"))
    points = left.number_input("Tau grid points", min_value=5, max_value=250, value=61,
                               **_key(prefix, "grid_points"))
    padding = right.number_input("Tau padding (decades)", min_value=0.0, max_value=4.0, value=0.5,
                                 **_key(prefix, "padding"))
    prominent = left.number_input("Peak prominence (% of maximum gamma)", min_value=0.1, max_value=100.0,
                                   value=5.0, **_key(prefix, "prominence"))
    shift = right.number_input("Maximum peak shift (decades)", min_value=0.01, value=0.5,
                               **_key(prefix, "shift"))
    order = st.radio("Peak tracking order", ["Spectrum selection", "Condition"], horizontal=True,
                     **_key(prefix, "order"))
    return {"lambdas": values, "points": int(points), "padding_decades": padding,
            "prominence_fraction": prominent / 100, "max_shift_decades": shift,
            "condition_order": order == "Condition", "method": engine.DRT_METHOD}


def _tables_for_fit(spectra, settings):
    circuits = [_circuit_from_settings(model["circuit"]) for model in settings["models"]]
    parameters = {circuit: {name: engine.Parameter(**values) for name, values in model["parameters"].items()}
                  for circuit, model in zip(circuits, settings["models"])}
    scores, fits = engine.compare_models(spectra, circuits, global_fit=settings["global_fit"],
                                        settings=parameters, confidence=settings["confidence"])
    tables = {"model_scores": scores}
    for attribute in ("parameters", "residuals", "covariance", "correlations"):
        parts = []
        for number, (group, label, fit) in enumerate(fits, 1):
            frame = getattr(fit, attribute)
            if attribute in {"covariance", "correlations"}:
                frame = frame.rename_axis(index="row_parameter", columns="column_parameter").stack(
                    future_stack=True).rename(attribute).reset_index()
            parts.append(frame.assign(fit_id=number, group=group, circuit=label))
        if parts:
            tables[attribute] = pd.concat(parts, ignore_index=True)
    return tables


def _lambdas(value):
    try:
        return tuple(dict.fromkeys(float(token) for token in re.split(r"[,\s]+", value.strip()) if token))
    except ValueError as error:
        raise ValueError("Enter numeric DRT lambda values separated by commas.") from error


def _drt_solution(spectra, settings):
    results, errors = engine.explore_drt(spectra, settings["lambdas"], points=settings["points"],
                                        padding_decades=settings["padding_decades"])
    tables = {}
    if results:
        tables["drt_diagnostics"] = pd.DataFrame([result.diagnostics for result in results])
        tables["drt_distribution"] = pd.concat([result.distribution for result in results], ignore_index=True)
        tables["drt_residuals"] = pd.concat([result.residuals.assign(**{"lambda": result.diagnostics["lambda"]})
                                             for result in results], ignore_index=True)
    if not errors.empty:
        tables["drt_errors"] = errors
    return results, tables


def _tables_for_drt(spectra, settings, recorded, series_order):
    results, tables = _drt_solution(spectra, settings)
    conditions = {name: c.value for name, c in recorded.items()} if settings["condition_order"] else None
    tracks = []
    track_errors = []
    for value in settings["lambdas"]:
        frames = {result.diagnostics["spectrum"]: result.distribution for result in results
                  if result.diagnostics["lambda"] == value}
        try:
            if conditions is not None:
                engine.common_condition_basis(recorded.values())
            peaks = engine.track_drt_peaks(frames, series_order=series_order, conditions=conditions,
                                           prominence_fraction=settings["prominence_fraction"],
                                           max_shift_decades=settings["max_shift_decades"])
            if conditions is not None:
                peaks["condition_source"] = peaks["spectrum"].map({name: item.source for name, item in recorded.items()})
                peaks["condition_basis"] = peaks["spectrum"].map({name: item.basis for name, item in recorded.items()})
            tracks.append(peaks.assign(**{"lambda": value}))
        except ValueError as error:
            track_errors.append({"lambda": value, "error": str(error)})
    if tracks:
        tables["drt_peak_tracks"] = pd.concat(tracks, ignore_index=True)
    if track_errors:
        tables["peak_tracking_errors"] = pd.DataFrame(track_errors)
    return tables


def _record_run(workspace, operation, paths, receipt, tables):
    target = new_folder(Path(workspace).expanduser() / "Workbench" / "Impedance series",
                        _METHODS[operation], reserve=40)
    try:
        outputs = []
        for name, frame in tables.items():
            if len(frame.columns):
                file = target / f"{name}.csv"
                frame.to_csv(file, index=False)
                outputs.append(file)
        file = target / "settings.json"
        file.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False, default=str) + "\n",
                        encoding="utf-8")
        outputs.append(file)
        record_operation(workspace, _METHODS[operation], inputs=paths, parameters=receipt, outputs=outputs)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def _run(data, workspace, paths, operation, inputs, settings, condition_settings):
    receipt = {"operation": operation, "source": str(data.source), "selected_paths": [str(path) for path in paths],
               "input_settings": inputs, "analysis_settings": settings, "condition_settings": condition_settings}
    spectra, errors, recorded, condition_errors, sources = _prepare(data, paths, inputs, condition_settings)
    receipt["spectra"] = sources
    receipt["resolved_conditions"] = {name: asdict(condition) for name, condition in recorded.items()}
    tables = {"spectra": pd.DataFrame(sources)}
    if spectra:
        tables["frequency_mask"] = pd.concat([s.provenance for s in spectra], ignore_index=True)
    if errors:
        tables["input_errors"] = pd.DataFrame(({"spectrum": s, "error": e} for s, e in errors.items()))
    if condition_errors:
        tables["condition_errors"] = pd.DataFrame(({"spectrum": s, "error": e}
                                                  for s, e in condition_errors.items()))
    if operation == "lin_kk":
        tables["lin_kk"] = engine.batch_lin_kk(spectra, errors=errors)
    elif operation == "circuits" and spectra:
        if settings["global_fit"] and errors:
            tables["fit_errors"] = pd.DataFrame([{"error": "Every selected spectrum is required for a global fit."}])
        else:
            tables.update(_tables_for_fit(spectra, settings))
    elif operation == "drt":
        tables.update(_tables_for_drt(spectra, settings, recorded, [row["spectrum"] for row in sources]))
    elif operation == "maps":
        eligible = [s for s in spectra if s.name in recorded and len(s.data) >= 2]
        insufficient = [s for s in spectra if len(s.data) < 2]
        if insufficient:
            tables["map_errors"] = pd.DataFrame([{"spectrum": s.name, "error": "Fewer than two retained rows."}
                                                 for s in insufficient])
        if eligible:
            results = []
            if settings["include_drt"]:
                results, drt_tables = _drt_solution(eligible, settings)
                tables.update(drt_tables)
            try:
                eis_map, drt_map = engine.condition_maps(eligible, recorded, condition_settings["axis"],
                                                         distributions=results)
            except ValueError as error:
                tables["map_errors"] = pd.concat([tables.get("map_errors", pd.DataFrame()),
                                                  pd.DataFrame([{"error": str(error)}])], ignore_index=True)
            else:
                tables["eis_condition_map"] = eis_map
                tables["drt_condition_map"] = drt_map
    return _record_run(workspace, operation, paths, receipt, tables)


def _frame(folder, name):
    file = folder / f"{name}.csv"
    if file.is_file():
        return pd.read_csv(file, float_precision="round_trip")
    return pd.DataFrame()


def _chart(chart, key):
    st.plotly_chart(chart, width="stretch", key=key)


def _fit_view(folder, prefix):
    scores = _frame(folder, "model_scores")
    if scores.empty:
        return
    st.markdown("**Model comparison**")
    st.caption("AICc and BIC compare fits on the same retained data. The scores count estimated error variance "
               "as one parameter. Confidence intervals use local linearization with a common residual variance. "
               "Models with deficient Jacobian rank have diagnostic scores and no ranking.")
    st.dataframe(scores.drop(columns="fit_id", errors="ignore"), hide_index=True)
    parameters = _frame(folder, "parameters")
    if parameters.empty:
        return
    choices = scores[["fit_id", "group", "circuit"]].dropna(subset="fit_id").set_index("fit_id")
    fit_id = st.selectbox("Fit to inspect", choices.index.tolist(),
                          format_func=lambda value: " · ".join(choices.loc[value]),
                          key=prefix + "inspect")
    selected_score = scores.loc[scores.fit_id == fit_id].iloc[0]
    if selected_score.status == "non-identifiable":
        st.warning("Free coefficients are non-identifiable. Diagnostic values reproduce this residual trace.")
    st.dataframe(parameters.loc[parameters.fit_id == fit_id].drop(columns=["fit_id", "group", "circuit"]),
                 hide_index=True)
    residuals = _frame(folder, "residuals").query("fit_id == @fit_id")
    selected = st.selectbox("Spectrum to inspect", residuals.spectrum.unique(), key=prefix + "spectrum")
    residuals = residuals.loc[residuals.spectrum == selected].sort_values("frequency_Hz")
    measured = residuals.assign(Zreal=residuals.Zreal_measured_ohm, neg_Zimag=-residuals.Zimag_measured_ohm,
                                curve="Measured")
    fitted = residuals.assign(Zreal=residuals.Zreal_fit_ohm, neg_Zimag=-residuals.Zimag_fit_ohm, curve="Fit")
    traces = pd.concat([measured, fitted], ignore_index=True)
    _chart(px.line(traces, x="Zreal", y="neg_Zimag", color="curve", markers=True, title="Nyquist plot",
                   labels={"Zreal": "Re(Z) (Ω)", "neg_Zimag": "−Im(Z) (Ω)"}, hover_data=["frequency_Hz"]),
           prefix + "nyquist")
    components = residuals.rename(columns={"residual_real_ohm": "Real", "residual_imag_ohm": "Imaginary"}).melt(
        id_vars=["frequency_Hz"], value_vars=["Real", "Imaginary"], var_name="Component", value_name="Residual (Ω)")
    chart = px.line(components, x="frequency_Hz", y="Residual (Ω)", color="Component", log_x=True, markers=True,
                    title="Complex residuals versus frequency", labels={"frequency_Hz": "Frequency (Hz)"})
    chart.add_hline(y=0, line_dash="dot")
    _chart(chart, prefix + "residuals")
    st.dataframe(residuals.drop(columns=["fit_id", "group", "circuit"]), hide_index=True)
    for name, title in (("covariance", "Parameter covariance"), ("correlations", "Parameter correlation")):
        values = _frame(folder, name)
        if not values.empty:
            values = values.loc[values.fit_id == fit_id].pivot(index="row_parameter", columns="column_parameter",
                                                               values=name)
            if not values.empty:
                with st.expander(title):
                    st.dataframe(values)


def _lin_kk_view(folder):
    table = _frame(folder, "lin_kk")
    if not table.empty:
        st.dataframe(table, hide_index=True)


def _drt_view(folder, prefix):
    diagnostics = _frame(folder, "drt_diagnostics")
    if not diagnostics.empty:
        st.dataframe(diagnostics, hide_index=True)
        name = st.selectbox("DRT spectrum to inspect", diagnostics.spectrum.unique(), key=prefix + "inspect")
        selected = diagnostics.loc[diagnostics.spectrum == name].sort_values("lambda")
        _chart(px.line(selected, x="lambda", y=["residual_norm_ohm", "curvature_norm_ohm"], log_x=True, markers=True,
                       title="DRT regularization trade-off", labels={"value": "Norm (Ω)", "variable": "Diagnostic"}),
               prefix + "lambda")
        distribution = _frame(folder, "drt_distribution")
        distribution = distribution.loc[distribution.spectrum == name].copy()
        distribution["Lambda"] = distribution["lambda"].map(lambda value: f"{value:.6g}")
        _chart(px.line(distribution, x="tau_seconds", y="gamma_ohm", color="Lambda", log_x=True,
                       title="Relaxation-time distributions",
                       labels={"tau_seconds": "τ (s)", "gamma_ohm": "γ (Ω)"}), prefix + "distribution")
    tracks = _frame(folder, "drt_peak_tracks")
    if tracks.empty:
        st.info("The selected prominence and tau grid yielded no tracked interior peaks.")
        return
    value = st.selectbox("Lambda for peak tracks", sorted(tracks["lambda"].unique()),
                         format_func=lambda number: f"{number:.6g}", key=prefix + "peak_lambda")
    tracks = tracks.loc[tracks["lambda"] == value].sort_values("series_step").copy()
    tracked = tracks.loc[tracks.track.notna()].copy()
    if not tracked.empty:
        tracked["Track"] = tracked["track"].astype(int).astype(str)
        axis = "condition" if "condition" in tracks else "series_step"
        settings = json.loads((folder / "settings.json").read_text(encoding="utf-8"))
        condition_axis = (settings["condition_settings"] or {}).get("axis", "Condition")
        _chart(px.line(tracked, x=axis, y="tau_seconds", color="Track", log_y=True, markers=True,
                       title="Tracked DRT peaks", hover_data=["spectrum", "area_dln_tau_ohm"],
                       labels={"tau_seconds": "Peak τ (s)", "condition": condition_axis, "series_step": "Series step"}),
               prefix + "peaks")
    st.dataframe(tracks, hide_index=True)


def _maps_view(folder, prefix):
    receipt = json.loads((folder / "settings.json").read_text(encoding="utf-8"))
    axis = receipt["condition_settings"]["axis"]
    coordinate = engine.CONDITION_AXES[axis][0]
    eis = _frame(folder, "eis_condition_map")
    if eis.empty:
        return
    quantities = {"Impedance magnitude (Ω)": "Z_magnitude_ohm", "Phase (°)": "phase_deg",
                  "Real impedance (Ω)": "Zreal_ohm", "Imaginary impedance (Ω)": "Zimag_ohm"}
    measure = st.selectbox("Impedance map value", list(quantities), key=prefix + "map_value")
    st.caption("Maps show the measured coordinates and retain the source identity in each marker.")
    _chart(px.scatter(eis, x=coordinate, y="frequency_Hz", color=quantities[measure], log_y=True,
                      title="Impedance condition map", hover_data=["spectrum", "source_row", "condition_source"],
                      labels={coordinate: axis, "frequency_Hz": "Frequency (Hz)", quantities[measure]: measure}),
           prefix + "eis")
    drt = _frame(folder, "drt_condition_map")
    if not drt.empty:
        _chart(px.scatter(drt, x=coordinate, y="tau_seconds", color="gamma_ohm", log_y=True,
                          title="DRT condition map", hover_data=["spectrum", "condition_source"],
                          labels={coordinate: axis, "tau_seconds": "τ (s)", "gamma_ohm": "γ (Ω)"}), prefix + "drt")


def _show_result(folder, operation, prefix):
    if not folder.is_dir():
        st.warning("The saved run folder is no longer available.")
        return
    st.subheader("Latest run")
    for name in ("input_errors", "condition_errors", "fit_errors", "drt_errors", "peak_tracking_errors", "map_errors"):
        errors = _frame(folder, name)
        if not errors.empty:
            st.markdown(f"**{name.replace('_', ' ').capitalize()}**")
            st.dataframe(errors, hide_index=True)
    if operation == "lin_kk":
        _lin_kk_view(folder)
    elif operation == "circuits":
        _fit_view(folder, prefix + "fit_")
    elif operation == "drt":
        _drt_view(folder, prefix + "drt_")
    else:
        _maps_view(folder, prefix + "maps_")
    mask = _frame(folder, "frequency_mask")
    if not mask.empty:
        with st.expander("Row-level frequency selection"):
            st.dataframe(mask, hide_index=True)
    with st.expander("Download tables and settings"):
        for file in sorted(folder.glob("*.csv")) + [folder / "settings.json"]:
            st.download_button(f"Download {file.name}", data=file.read_bytes(), file_name=file.name,
                               mime="application/json" if file.suffix == ".json" else "text/csv",
                               key=prefix + file.name, on_click="ignore")


def _run_panel(data, workspace, paths, inputs, operation, prefix, settings, conditions=None, *, label):
    result_key = prefix + "latest_" + operation
    if st.button(label, key=prefix + "run_" + operation, type="primary"):
        try:
            if operation == "drt":
                settings = {**settings, "lambdas": _lambdas(settings["lambdas"])}
            with st.spinner(_METHODS[operation]):
                st.session_state[result_key] = _run(data, workspace, paths, operation, inputs, settings, conditions)
        except (OSError, ValueError, RuntimeError, TypeError) as error:
            st.error(str(error))
    if result := st.session_state.get(result_key):
        _show_result(Path(result), operation, prefix + operation + "_result_")


def render(data, *, workspace: Path):
    """Render the impedance-series tools using read-only browser data."""
    prefix = "w340_eis_" + _fingerprint(data.source) + "_"
    st.subheader("Impedance and DRT series")
    if not data.tables:
        st.info("Open one or more impedance tables to begin.")
        return
    options = {str(path): path for path in data.tables}
    preferred = [str(path) for path in data.tables
                 if re.search(r"\b(?:eis|peis|geis|impedance)\b", re.sub(r"[_-]", " ",
                              f"{path.stem} {data.records.get(path, {}).get('technique', '')}"), re.I)]
    selected = st.multiselect("Impedance spectra", list(options), default=(preferred or list(options))[:1],
                              format_func=lambda value: _source_name(data, options[value]),
                              **_key(prefix, "spectra"))
    if not selected:
        st.info("Select one or more spectra for the run.")
        return
    paths = [options[value] for value in selected]
    inputs = _input_options(data, paths, prefix)
    circuits, validation, drt, maps = st.tabs(["Circuits", "lin-KK", "DRT explorer", "Condition maps"])
    with circuits:
        settings = _fit_controls(prefix)
        _run_panel(data, workspace, paths, inputs, "circuits", prefix + "fit_", settings, label="Run circuit fits")
    with validation:
        st.caption("lin-KK requires at least three distinct frequencies after masking. "
                   "The batch limits RC elements to keep positive residual degrees of freedom.")
        _run_panel(data, workspace, paths, inputs, "lin_kk", prefix + "kk_", {}, label="Run lin-KK batch")
    with drt:
        settings = _drt_controls(prefix + "drt_")
        conditions = _condition_controls(data, paths, prefix + "drt_") if settings["condition_order"] else None
        _run_panel(data, workspace, paths, inputs, "drt", prefix + "drt_", settings, conditions,
                   label="Run DRT explorer")
    with maps:
        conditions = _condition_controls(data, paths, prefix + "maps_")
        include_drt = st.checkbox("Include a DRT condition map", **_key(prefix, "map_include_drt"))
        settings = {"include_drt": include_drt}
        if include_drt:
            st.caption(engine.DRT_METHOD)
            left, middle, right = st.columns(3)
            value = left.number_input("Map DRT lambda", min_value=1e-15, value=0.0001, format="%.8g",
                                      **_key(prefix, "map_lambda"))
            points = middle.number_input("Map tau grid points", min_value=5, max_value=250, value=61,
                                         **_key(prefix, "map_points"))
            padding = right.number_input("Map tau padding (decades)", min_value=0.0, max_value=4.0, value=0.5,
                                         **_key(prefix, "map_padding"))
            settings.update(lambdas=(value,), points=int(points), padding_decades=padding, method=engine.DRT_METHOD)
        _run_panel(data, workspace, paths, inputs, "maps", prefix + "maps_", settings, conditions,
                   label="Run condition maps")
