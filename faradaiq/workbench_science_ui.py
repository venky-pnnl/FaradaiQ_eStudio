"""Streamlit controls for the electrochemical science workbench."""

from collections.abc import Mapping
from contextlib import nullcontext
import csv
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import re
import shutil
from tempfile import mkdtemp

import plotly.graph_objects as go
import streamlit as st

from . import browser_analysis as browser
from . import workbench_science as science
from .workbench_audit import record_operation

TOOLS = {
    37: "Scan-rate series and peak regression",
    38: "CV peak tracking",
    39: "Signed CV charge by sweep and window",
    40: "Double-layer capacitance",
    41: "Electrochemical surface area (ECSA)",
    42: "Randles–Ševčík and Laviron analysis",
    43: "Chronoamperometry transient comparison",
    44: "Scharifker–Hills nucleation diagnostics",
    45: "Chronopotentiometry transition",
    46: "OCP stability window",
    47: "Deposition charge, mass and composition",
    48: "Faradaic efficiency",
    49: "Tafel window sensitivity",
    50: "Corrosion series",
    62: "Mott–Schottky by frequency",
    63: "Arrhenius series",
    64: "Levich and Koutecky–Levich",
}

_SERIES = {37, 40, 42, 50, 63, 64}
_POTENTIAL_SERIES = {37, 40, 42, 50, 64}
_ROLES = {41: (), 43: ("time", "current"), 44: ("time", "current"), 45: ("time", "potential"),
          46: ("time", "potential"), 47: ("time", "current"), 48: ("time", "current"),
          62: ("potential", "frequency", "zreal", "zimag"), 63: ()}
_ROLE_LABELS = {"potential": "Potential column", "current": "Current column", "time": "Time column",
                "frequency": "Frequency column", "zreal": "Real impedance column", "zimag": "Imaginary impedance column"}


class Fields:
    def __init__(self, prefix):
        self.prefix = prefix

    def _options(self, key):
        return {"key": self.prefix + "_" + key, "persist_state": "session"}

    def text(self, key, label, default="", *, optional=False, help=None):
        value = st.text_input(label, value=str(default), help=help, **self._options(key)).strip()
        return None if optional and not value else value

    def area(self, key, label, default="", help=None):
        return st.text_area(label, value=default, help=help, **self._options(key))

    def choice(self, key, label, choices, **options):
        return st.selectbox(label, choices, **options, **self._options(key))

    def many(self, key, label, choices, **options):
        return st.multiselect(label, choices, **options, **self._options(key))

    def check(self, key, label, default=False):
        return st.checkbox(label, value=default, **self._options(key))

    def count(self, key, label):
        return st.number_input(label, min_value=1, value=1, step=1, **self._options(key))

    def window(self, key, title, unit, *, defaults=("", "")):
        left, right = st.columns(2)
        with left:
            lo = self.text(key + "_start", f"{title} lower ({unit})", defaults[0])
        with right:
            hi = self.text(key + "_end", f"{title} upper ({unit})", defaults[1])
        return lo, hi


def _digest(value):
    return sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _row_values(text, expected, label):
    try:
        rows = [[value.strip() for value in row] for row in csv.reader(StringIO(text), strict=True)
                if any(value.strip() for value in row)]
    except csv.Error as error:
        raise ValueError(f"Check CSV formatting in {label}: {error}") from error
    if not rows or any(len(row) != expected for row in rows):
        raise ValueError(f"Enter one {label} per row with {expected} comma-separated values.")
    return rows


def _label(data, path):
    record = data.records.get(path, {})
    relative = str(path.relative_to(data.analysis_root)) if path.is_relative_to(data.analysis_root) else str(path)
    instrument = record.get("instrument")
    name = record.get("display_name") or relative
    if name != relative:
        name = f"{name} [{relative}]"
    return f"{instrument} · {name}" if instrument else str(name)


def _measurement_identity(data, path):
    identity = data.records.get(path, {})
    source = getattr(data, "metadata", {})
    contexts = {"source_metadata.": source}
    if handle_id := identity.get("handle_id"):
        handles = source.get("project_handles") or ()
        if isinstance(handles, Mapping):
            handle = handles.get(handle_id)
        else:
            handle = next((item for item in handles if isinstance(item, Mapping) and item.get("id") == handle_id), None)
        if isinstance(handle, Mapping):
            contexts.update({"project_handle.": handle, "project_handle.metadata.": handle.get("metadata")})
    elif manifest := getattr(data, "bundle_manifest", None):
        contexts.update({"bundle_manifest.": manifest, "bundle_manifest.experiment_metadata.": manifest.get("experiment_metadata")})
    return dict(identity, _potential_reference_context=contexts)


def _sweep(fields):
    direction = fields.choice("direction", "Sweep direction", ("any", "increasing", "decreasing"))
    branch = fields.count("sweep", "Sweep number within selected direction")
    return {"branch": branch, "direction": {"any": None, "increasing": 1, "decreasing": -1}[direction]}


def _polarity(fields, *, both=False):
    choices = ("both", "cathodic", "anodic") if both else ("cathodic", "anodic")
    return fields.choice("polarity", "Current polarity", choices)


def _peak_window(fields, key="peak", label="Peak potential window"):
    if fields.check(key + "_enable", "Limit " + label.lower()):
        return fields.window(key, label, "V")
    return None


def _scan_columns(selected):
    return [(row["label"], row["table"], row["axis_value"], row["columns"], row["identity"]) for row in selected]


def _defaults_for_interval(selected):
    row = selected[0]
    table, column = row["table"], row["columns"].get("time")
    if column is None:
        return "", ""
    try:
        normalized = science._normalized(table, {"time": column}, ("time",))
    except ValueError:
        return "", ""
    values = normalized.time_s
    return (str(float(values.iloc[0])), str(float(values.iloc[-1]))) if len(values) > 1 else ("", "")


def _measurement_settings(fields, data, selected_paths, tool, *, synthetic_time, common_reference):
    roles = _ROLES.get(tool, ("potential", "current"))
    if tool == 39 and not synthetic_time:
        roles += ("time",)
    selected = []
    for path in selected_paths:
        table = data.read_table(path)
        label = _label(data, path)
        identity = _measurement_identity(data, path)
        entry = Fields(fields.prefix + "_" + _digest(path))
        columns = {}
        axis_value = None
        observable = None
        reference_sources = {}
        offset = None
        roles_found = browser.measurement_roles(table)
        expanded = len(selected_paths) == 1 or tool in (37, 40, 42, 63, 64)
        with st.expander(label, expanded=expanded) if roles or tool == 63 else nullcontext():
            for role in roles:
                options = [None, *table.columns]
                candidate = science._measurement_column(table, None, role, roles_found)
                index = options.index(candidate) if candidate is not None else 0
                columns[role] = entry.choice(role, _ROLE_LABELS[role], options, index=index,
                                             format_func=lambda value: "Detect from header" if value is None else str(value))
            if "potential" in roles:
                reference_sources = science.potential_reference_sources(table, columns, identity)
            if tool in _POTENTIAL_SERIES:
                references = ", ".join(dict.fromkeys(reference_sources.values())) or "Unknown"
                st.caption(f"Recorded potential reference — {references}")
                if common_reference:
                    offset_key = "potential_offset_" + _digest((common_reference, columns["potential"]))
                    offset = entry.text(offset_key, "Potential offset to common reference (V)", optional=True,
                                        help="The calculation adds this offset to the selected potential column. Enter 0 when you have verified that a column with different recorded metadata is already on the common scale.")
            if tool in (37, 40, 42):
                axis_value = entry.text("scan_rate", "Scan rate (V/s)", identity.get("scan_rate_V_s") or "")
            elif tool == 63:
                unit = st.session_state.get(fields.prefix + "_temp_unit")
                default = identity.get("temperature_K") or "" if unit == "K" else ""
                axis_value = entry.text("temperature_" + str(unit), f"Temperature ({unit})", default)
                observable = entry.text("rate", "Measured positive kinetic observable", help="Enter a value measured for this run in the common unit below.")
            elif tool == 64:
                axis_value = entry.text("rpm", "Rotation speed (rpm)", identity.get("rotation_rpm") or "")
            if roles:
                st.caption("Select columns with explicit units in their headers.")
        selected.append({"path": path, "table": table, "label": label, "identity": identity,
                         "columns": columns, "axis_value": axis_value, "observable": observable,
                         "potential_reference_sources": reference_sources, "potential_offset_V": offset})
    return selected


def _configure(tool, fields, data, selected_paths):
    params = {}
    synthetic_time = tool == 39 and fields.check("synthetic_time", "Use a supplied constant scan rate")
    if tool == 63:
        params["unit"] = fields.choice("temp_unit", "Temperature unit", ("K", "°C"))
    common_reference = None
    if tool in _POTENTIAL_SERIES:
        reference_fields = Fields(fields.prefix + "_" + _digest(tuple(map(str, selected_paths))))
        common_reference = reference_fields.text("reference", "Common potential reference", optional=True,
                                                 help="Enter the scale for all potential inputs and outputs when a recorded reference is missing or differs. Leave blank when the recorded references agree.")
    selected = _measurement_settings(fields, data, selected_paths, tool, synthetic_time=synthetic_time,
                                     common_reference=common_reference)
    if tool in _POTENTIAL_SERIES:
        params.update(common_potential_reference=common_reference, potential_offsets_V=[row["potential_offset_V"] for row in selected])
    table = selected[0]["table"]
    columns = selected[0]["columns"]
    if tool in (37, 38, 39):
        if tool != 38:
            params.update(_sweep(fields))
        if tool in (37, 38):
            params.update(polarity=_polarity(fields, both=tool == 38), window=_peak_window(fields))
        if tool == 37:
            params["transform"] = fields.choice("transform", "Scan-rate transform", ("sqrt", "linear", "log10"),
                                               format_func={"sqrt": "Square root", "linear": "Linear", "log10": "log₁₀"}.__getitem__)
            run = lambda: science.scan_rate_series(_scan_columns(selected), **params)
        elif tool == 38:
            run = lambda: science.cv_peak_tracking(table, columns=columns, **params)
        else:
            params.update(window=fields.window("window", "Integration potential", "V"),
                          scan_rate_V_s=fields.text("scan_rate", "Scan rate (V/s)") if synthetic_time else None)
            run = lambda: science.integrate_cv_charge(table, columns=columns, **params)
    elif tool == 40:
        params.update(potential_V=fields.text("potential", "Nonfaradaic potential (V)"),
                      increasing_sweep=fields.count("increasing", "Increasing sweep number"),
                      decreasing_sweep=fields.count("decreasing", "Decreasing sweep number"))
        run = lambda: science.double_layer_capacitance(_scan_columns(selected), **params)
    elif tool == 41:
        method = fields.choice("method", "Reference approach", ("capacitance", "charge"))
        unit = "F" if method == "capacitance" else "C"
        params.update(method=method, signal=fields.text("signal", f"Measured {'Cdl' if method == 'capacitance' else 'reference-process charge magnitude'} ({unit})"),
                      reference_per_cm2=fields.text("reference", f"Material-specific reference ({unit}/cm²)"),
                      geometric_area_cm2=fields.text("geometric_area", "Geometric area (cm², optional)", optional=True))
        run = lambda: science.electrochemical_surface_area(**params)
    elif tool == 42:
        method = fields.choice("kinetics", "Named method", ("Reversible Randles–Ševčík", "Diffusionless Laviron"))
        params.update(electrons=fields.text("electrons", "Electrons transferred"),
                      temperature_K=fields.text("temperature", "Temperature (K)"))
        if method == "Reversible Randles–Ševčík":
            confirmed = fields.check("rsa", "Reversible planar diffusion and corrected background apply")
            params.update(_sweep(fields))
            params.update(polarity=_polarity(fields), window=_peak_window(fields),
                          area_cm2=fields.text("area", "Electrode area (cm²)"),
                          concentration_mol_cm3=fields.text("concentration", "Bulk concentration (mol/cm³)"))
        else:
            confirmed = fields.check("laviron", "The selected high-rate voltammograms measure a diffusionless couple")
            params.update(formal_potential_V=fields.text("formal", "Formal potential (V)"),
                          increasing_sweep=fields.count("increasing", "Increasing sweep number"),
                          decreasing_sweep=fields.count("decreasing", "Decreasing sweep number"),
                          anodic_window=_peak_window(fields, "anodic", "Anodic peak potential window"),
                          cathodic_window=_peak_window(fields, "cathodic", "Cathodic peak potential window"))

        def run():
            if not confirmed:
                raise ValueError("Confirm that the selected experiment meets the named method’s assumptions.")
            function = science.randles_sevcik if method == "Reversible Randles–Ševčík" else science.laviron
            return function(_scan_columns(selected), **params)
        return run, params | {"named_method": method, "assumptions_confirmed": confirmed}, selected
    elif tool in (43, 44, 45, 46, 47, 48):
        defaults = _defaults_for_interval(selected)
        params["interval_s"] = fields.window("interval", "Analysis time window", "s", defaults=defaults)
        if tool in (43, 44):
            params["step_time_s"] = fields.text("step", "Potential step time (s)", defaults[0])
        if tool == 43:
            models = ("Cottrell + offset", "Exponential + offset")
            params["models"] = fields.many("models", "Transient models", models, default=list(models))
            run = lambda: science.chronoamperometry_fit(table, columns=columns, **params)
        elif tool == 44:
            params.update(polarity=_polarity(fields), background_A=fields.text("background", "Constant background (A)", "0"))
            run = lambda: science.scharifker_hills(table, columns=columns, **params)
        elif tool == 45:
            params["threshold_V_s"] = fields.text("threshold", "Minimum transition |dE/dt| (V/s)")
            run = lambda: science.chronopotentiometry_transition(table, columns=columns, **params)
        elif tool == 46:
            params.update(maximum_drift_mV_min=fields.text("drift_limit", "Maximum absolute drift (mV/min, optional)", optional=True),
                          maximum_noise_mV=fields.text("noise_limit", "Maximum detrended noise (mV, optional)", optional=True))
            run = lambda: science.ocp_stability(table, columns=columns, **params)
        else:
            params.update(polarity=_polarity(fields), charge_basis=fields.choice("charge_basis", "Charge basis",
                                                                                ("net", "selected polarity")))
            if tool == 47:
                params["efficiency_percent"] = fields.text("assumed_efficiency", "Assumed deposition efficiency (%)")
                rows = fields.area("components", "Deposit components",
                                   help="One row per component with name, molar mass (g/mol), electron count and mole fraction separated by commas. Fractions sum to 1.")
                run = lambda: science.deposition(table, columns=columns, components=_row_values(rows, 4, "deposit component"), **params)
                return run, params | {"components_csv": rows}, selected
            rows = fields.area("products", "Measured products",
                               help="One row per product with name, amount, unit (mol, µmol or mg), electron count and molar mass (g/mol, only for mg). Include a final comma when molar mass is blank.")
            run = lambda: science.faradaic_efficiency(table, columns=columns, products=_row_values(rows, 5, "product"), **params)
            return run, params | {"products_csv": rows}, selected
    elif tool == 49:
        params.update(branch=_polarity(fields), area_cm2=fields.text("area", "Geometric area (cm²)"),
                      equilibrium_V=fields.text("eeq", "Equilibrium potential (V)"),
                      resistance_ohm=fields.text("resistance", "Uncompensated resistance (Ω)", "0"))
        rows = fields.area("windows", "Overpotential windows (V)",
                           help="Enter at least two rows, each with lower and upper limits separated by a comma. Use the sign of the selected branch.")
        run = lambda: science.tafel_window_sensitivity(table, columns=columns, windows_V=_row_values(rows, 2, "window"), **params)
        return run, params | {"windows_csv": rows}, selected
    elif tool == 50:
        params.update(area_cm2=fields.text("area", "Geometric area (cm², optional)", optional=True),
                      equivalent_weight_g_per_equiv=fields.text("equivalent_weight", "Equivalent weight (g/equiv, optional)", optional=True),
                      density_g_cm3=fields.text("density", "Density (g/cm³, optional)", optional=True))
        records = [(row["label"], row["table"], row["columns"],
                    dict(row["identity"], source_path=str(row["identity"].get("source_path") or row["path"]))) for row in selected]
        run = lambda: science.corrosion_series(records, **params)
    elif tool == 62:
        params["negate_imaginary"] = fields.check("negate", "Selected column stores −Im(Z)")
        frequency_axis = science._normalized(table, {"frequency": columns["frequency"]}, ("frequency",)).frequency_Hz
        chosen = fields.many("frequency_selection", "Frequencies to show (Hz)", sorted(frequency_axis.unique().tolist()),
                             help="Leave empty to include every measured frequency.")
        params["frequencies_Hz"] = chosen or None
        run = lambda: science.mott_schottky_frequencies(table, columns=columns, **params)
    elif tool == 63:
        params["rate_unit"] = fields.text("observable_unit", "Common kinetic observable unit", help="Use the same rate or kinetic-current convention for every measurement.")
        run = lambda: science.arrhenius([row["axis_value"] for row in selected], [row["observable"] for row in selected],
                                       labels=[row["label"] for row in selected], **params)
    else:
        params.update(_sweep(fields))
        params.update(method=fields.choice("method", "Rotating disk method", ("Levich", "Koutecky–Levich")),
                      potential_V=fields.text("bias", "Common potential (V)"),
                      polarity=_polarity(fields),
                      area_cm2=fields.text("area", "Disk area (cm²)"),
                      concentration_mol_cm3=fields.text("concentration", "Bulk concentration (mol/cm³)"),
                      diffusion_cm2_s=fields.text("diffusion", "Diffusion coefficient (cm²/s)"),
                      kinematic_viscosity_cm2_s=fields.text("viscosity", "Kinematic viscosity (cm²/s)"),
                      reference_electrons=fields.text("reference_electrons", "Reference electron count (optional)", optional=True))
        run = lambda: science.rotating_disk_series(_scan_columns(selected), **params)
    return run, params, selected


def _chart(result, specification):
    table, x, variables, group = specification
    data = result.tables[table]
    fig = go.Figure()
    groups = data.groupby(group, sort=False) if group else [(None, data)]
    for name, subset in groups:
        for variable in variables:
            if not subset[variable].notna().any():
                continue
            title = variable if name is None else f"{name} · {variable}"
            fit_like = any(term in variable for term in ("fitted", "_fit_", "instantaneous", "progressive"))
            fig.add_scatter(x=subset[x].tolist(), y=subset[variable].tolist(), name=title,
                            mode="lines" if fit_like else "lines+markers",
                            line={"dash": "dash"} if fit_like else None)
    fig.update_layout(xaxis_title=x, yaxis_title=" / ".join(variables), legend_title_text="", margin={"t": 20})
    return fig


def _persist_result(workspace, tool, selected_paths, provenance, result):
    if tool in _POTENTIAL_SERIES:
        provenance = provenance | {"resolved_potential_reference": result.metrics["potential_reference"],
                                   "measurements": [row | {"potential_offset_V": float(row["potential_offset_V"] or 0)}
                                                    for row in provenance["measurements"]]}
    root = Path(workspace).expanduser().resolve() / "Workbench" / "Science"
    root.mkdir(parents=True, exist_ok=True)
    destination = Path(mkdtemp(prefix=f"feature_{tool}_", dir=root))
    outputs = {}
    try:
        for name, frame in result.tables.items():
            slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            path = destination / f"{slug}.csv"
            frame.to_csv(path, index=False)
            outputs[name] = path
        path = destination / "methods.json"
        payload = {"feature": tool, "method": TOOLS[tool], "parameters": provenance,
                   "inputs": list(map(str, selected_paths)), "notes": result.notes, "metrics": result.metrics}
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str, allow_nan=False) + "\n", encoding="utf-8")
        outputs["Methods and inputs"] = path
        record_operation(workspace, f"Science · {TOOLS[tool]}", inputs=selected_paths,
                         parameters={"feature": tool, **provenance}, outputs=outputs.values())
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return outputs


def render(data, *, workspace: Path):
    """Render the science route for canonical single- and multi-source data."""
    st.header("Electrochemical science")
    if data is None or not data.tables:
        st.info("Open an experiment or project with measurement tables to run an analysis.")
        return
    prefix = "w340_science_" + _digest((workspace, data.source))
    tool = Fields(prefix).choice("tool", "Science tool", list(TOOLS), format_func=TOOLS.__getitem__)
    fields = Fields(prefix + f"_{tool}")
    available = list(data.tables)
    labels = {path: _label(data, path) for path in available}
    if tool in _SERIES:
        paths = fields.many("measurements", "Measurement tables", available, format_func=labels.__getitem__)
    else:
        paths = [fields.choice("measurement", "Measurement table", available, format_func=labels.__getitem__)]
    if not paths:
        st.info("Select measurement tables from the current experiment or project.")
        return
    try:
        calculate, parameters, selected = _configure(tool, fields, data, paths)
    except (OSError, TypeError, ValueError) as error:
        st.error(str(error))
        return
    provenance = parameters | {"measurements": [
        {"path": str(row["path"]), "display": row["label"], "columns": row["columns"],
         "instrument": row["identity"].get("instrument"), "handle_id": row["identity"].get("handle_id"),
         "source_path": row["identity"].get("source_path"), "axis_value": row["axis_value"],
         "observable": row["observable"], "potential_reference_sources": row["potential_reference_sources"],
         "potential_offset_V": row["potential_offset_V"]} for row in selected]}
    signature = _digest(provenance)
    result_key = fields.prefix + "_results_" + _digest(tuple(map(str, paths)))
    if st.button("Calculate", key=fields.prefix + "_calculate", type="primary"):
        try:
            result = calculate()
            outputs = _persist_result(workspace, tool, paths, provenance, result)
        except (OSError, TypeError, ValueError, ArithmeticError) as error:
            st.error(str(error))
        else:
            st.session_state[result_key] = (signature, result, outputs)
    saved = st.session_state.get(result_key)
    if saved is None:
        return
    previous, result, outputs = saved
    if previous != signature:
        st.caption("Settings differ from the last calculation for this tool.")
        return
    st.subheader("Results")
    for note in result.notes:
        st.caption(note)
    for i, specification in enumerate(result.charts):
        fig = _chart(result, specification)
        if fig.data:
            st.plotly_chart(fig, key=fields.prefix + f"_chart_{i}", width="stretch")
    for name, frame in result.tables.items():
        with st.expander(name, expanded=name == next(iter(result.tables))):
            st.dataframe(frame, hide_index=True, width="stretch")
    for i, (name, output) in enumerate(outputs.items()):
        if output.is_file():
            mime = "text/csv" if output.suffix == ".csv" else "application/json"
            st.download_button(f"Download {name.lower()}", output.read_bytes(), output.name, mime,
                               key=fields.prefix + f"_download_{i}", on_click="ignore")
