"""Selected-evidence reports, deterministic replay, and local diagnostics."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from html import escape
from importlib import metadata, util
from io import BytesIO
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit, urlunsplit
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pandas as pd

from .readable_names import new_folder, safe_component
from .release_identity import APP_NAME, APP_VERSION
from .scilink_bundle import column_unit
from .workbench_audit import record_operation

RECIPE_SCHEMA = "faradaiq-workbench-recipe-1"
EVIDENCE_SCHEMA = "faradaiq-workbench-evidence-1"
TRACE_TECHNIQUES = ("CV", "LSV", "FSCV", "CA", "DEPOSITION", "CP", "OCP",
                    "DPV", "SWV", "NPV", "STRIPPING")
_PLOT_TEXT = {"measurement", "representation", "title", "x_title", "y_title", "x", "y",
              "theme", "trace_style"}
_PLOT_FLAGS = {"invert", "log_x", "log_y", "legend"}
_TRACE_COLUMNS = {"potential", "current", "time"}
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_TABLE_TYPES = {".csv": "text/csv", ".json": "application/json"}
_APP_ROOT = Path(__file__).resolve().parents[1]


def _digest(payload):
    return hashlib.sha256(payload).hexdigest()


def _file_digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _json(value):
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _write_json(path, value):
    path.write_text(_json(value), encoding="utf-8")
    return path


def _versions(distributions):
    result = {"Python": platform.python_version(), APP_NAME: APP_VERSION}
    for name in distributions:
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result[name] = None
    return result


def _relative(path, root):
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name


def measurement_catalog(data):
    """Use the same relative labels the browser stores in plot recipes."""
    labels = {}
    for value in data.tables:
        path = Path(value)
        label = _relative(path, Path(data.analysis_root))
        if label in labels:
            raise ValueError(f"More than one table has the label {label!r}. Select a project with unique table paths.")
        labels[label] = path
    return labels


def _column_units(frame, declared=None):
    from .browser_analysis import measurement_roles
    from .browser_productivity import _explicit_unit

    declared = declared if isinstance(declared, Mapping) else {}
    detected = {column: _explicit_unit(column, role) for role, column in measurement_roles(frame).items()
                if column is not None}
    units = {}
    for column in frame:
        unit = declared.get(column) or column_unit(column) or detected.get(column)
        units[column] = str(unit) if unit else None
    return units


@dataclass(frozen=True)
class MeasurementSnapshot:
    reference: dict
    frame: pd.DataFrame
    csv: bytes


def capture_measurements(data, selected):
    """Read only selected table entries and hash the CSV representation used in replay."""
    catalog = measurement_catalog(data)
    snapshots = {}
    for choice in dict.fromkeys(selected):
        if choice not in catalog:
            raise ValueError(f"Choose a measurement in the current project: {choice!r}.")
        path = catalog[choice]
        before = path.stat()
        frame = data.read_table(path).rename(columns=str)
        if frame.empty or frame.shape[1] < 2 or not frame.columns.is_unique:
            raise ValueError(f"{choice} needs rows and at least two uniquely named columns.")
        entry = data.records.get(path, {})
        csv = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
        sha = _digest(csv)
        source_sha = _file_digest(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"{choice} changed during capture. Retry after the source is stable.")
        ref = {"id": f"measurement-{_digest((choice + sha + source_sha).encode())[:20]}",
               "kind": "measurement", "label": choice, "relative_path": choice,
               "path": str(path), "file_sha256": source_sha, "csv_sha256": sha,
               "rows": len(frame), "units": _column_units(frame, entry.get("units")),
               "text_columns": [column for column in frame if
                                pd.api.types.is_string_dtype(frame[column].dtype)
                                or pd.api.types.is_object_dtype(frame[column].dtype)],
               "float_dtypes": {column: str(dtype) for column, dtype in frame.dtypes.items()
                                if str(dtype) in {"float32", "float64", "Float32", "Float64"}}}
        for key in ("source_path", "original_table_path", "display_name", "instrument",
                    "handle_id", "technique", "technique_key", "hdf5_dataset"):
            if entry.get(key) is not None:
                ref[key] = str(entry[key])
        snapshots[choice] = MeasurementSnapshot(ref, frame, csv)
    return snapshots


def _validate_plot(settings, available):
    if not isinstance(settings, dict) or set(settings) - _PLOT_TEXT - _PLOT_FLAGS - {"height", "overlays"}:
        raise ValueError("The plot has unsupported settings.")
    if any(not isinstance(settings[key], str) or len(settings[key]) > 4096 for key in settings.keys() & _PLOT_TEXT):
        raise ValueError("Plot labels and settings must be text.")
    if any(type(settings[key]) is not bool for key in settings.keys() & _PLOT_FLAGS):
        raise ValueError("Plot flags must be true or false.")
    if "height" in settings and (type(settings["height"]) is not int or not 200 <= settings["height"] <= 2000):
        raise ValueError("Plot height must be between 200 and 2,000 pixels.")
    overlays = settings.get("overlays", [])
    if not isinstance(overlays, list) or any(not isinstance(item, str) for item in overlays):
        raise ValueError("Plot overlays must be a list of measurement labels.")
    labels = [settings.get("measurement"), *overlays]
    if any(label not in available for label in labels):
        raise ValueError("The plot references a measurement outside the selected project or recipe scope.")
    if len(set(labels)) != len(labels):
        raise ValueError("Select each plot measurement once.")
    representation = settings.get("representation", "XY overlay")
    if representation not in {"XY overlay", "Bode", "Nyquist"}:
        raise ValueError("Choose XY overlay, Bode, or Nyquist.")
    if representation == "XY overlay" and (not settings.get("x") or not settings.get("y")
                                            or settings["x"] == settings["y"]):
        raise ValueError("Choose two different XY columns.")
    if settings.get("theme", "Light") not in {"Light", "Dark"}:
        raise ValueError("Choose a Light or Dark plot theme.")
    if settings.get("trace_style", "Lines and points") not in {"Lines and points", "Lines", "Points"}:
        raise ValueError("Choose a supported trace style.")
    return labels


def _require_units(snapshot, required, settings=None):
    from .browser_analysis import measurement_roles
    from .browser_productivity import _explicit_unit

    frame = snapshot.frame
    roles = measurement_roles(frame)
    for role in required:
        column = (settings or {}).get(role) or roles.get(role)
        if column not in frame:
            raise ValueError(f"Choose a table with a {role} column.")
        explicit = _explicit_unit(column, role)
        if explicit is None:
            raise ValueError(f"Record an explicit supported {role} unit in the column header {column!r}.")
        if explicit != _unit_identity(snapshot.reference["units"].get(column)):
            raise ValueError(f"Recorded units and the column header disagree for {column!r}.")


@dataclass(frozen=True)
class ReproducedPlot:
    table: pd.DataFrame
    title: str
    settings: dict
    sources: tuple[dict, ...]


def reproduce_plot(snapshots, settings):
    """Apply existing browser XY conversion and impedance plotting to the selected curves."""
    from .browser_plotting import _trace_values, build_impedance_figure, build_xy_figure
    from .browser_productivity import prepare_xy_series

    labels = _validate_plot(settings, snapshots)
    series = [(label, snapshots[label].frame) for label in labels]
    names = {label: label for label in labels}
    fields = ("title", "height", "theme", "legend")
    options = {key: settings[key] for key in fields if key in settings}
    representation = settings.get("representation", "XY overlay")
    if representation == "XY overlay":
        x, y = settings["x"], settings["y"]
        if x not in series[0][1] or y not in series[0][1]:
            raise ValueError("The active measurement is missing a selected XY column.")
        converted, skipped = prepare_xy_series(series, x, y)
        if skipped:
            raise ValueError("Incompatible overlay. " + ". ".join(skipped))
        for label, _ in series[1:]:
            for axis in (x, y):
                source = snapshots[label]
                active = snapshots[labels[0]]
                if axis in source.frame:
                    source_unit = _unit_identity(source.reference["units"][axis])
                    active_unit = _unit_identity(active.reference["units"][axis])
                    if not source_unit or source_unit != active_unit:
                        raise ValueError(f"{label} has unverified or incompatible units for {axis}.")
        options.update({key: settings[key] for key in _PLOT_FLAGS | {"x_title", "y_title", "trace_style"}
                        if key in settings})
        figure = build_xy_figure(converted, x, y, names=names, **options)
        units = {"x_unit": snapshots[labels[0]].reference["units"][x],
                 "y_unit": snapshots[labels[0]].reference["units"][y]}
    else:
        for label, _ in series:
            try:
                _require_units(snapshots[label], ("frequency", "zreal", "zimag"))
            except ValueError as error:
                raise ValueError(f"{label}: {error}") from error
        figure = build_impedance_figure(series, representation=representation, names=names, **options)
        units = {"x_unit": "Hz" if representation == "Bode" else "ohm", "y_unit": "ohm"}

    tables = []
    for index, label, panel, x_label, y_label, values in _trace_values(figure):
        trace = figure.data[index - 1]
        axes = [figure.layout[f"{kind}axis{(getattr(trace, kind + 'axis', None) or kind)[1:]}"]
                for kind in ("x", "y")]
        if not np.isfinite(values).all(axis=1).any():
            raise ValueError(f"{label} has no finite paired values in the selected plot.")
        ref = snapshots[label].reference
        tables.append(pd.DataFrame({"trace": index, "measurement": label, "panel": panel,
                                    "source_id": ref["id"], "source_path": ref["path"],
                                    "source_sha256": ref["file_sha256"], "table_sha256": ref["csv_sha256"],
                                    "x_axis": x_label, "y_axis": y_label, "x_unit": units["x_unit"],
                                    "y_unit": "deg" if representation == "Bode" and panel == 2 else units["y_unit"],
                                    "x_scale": axes[0].type or "linear", "y_scale": axes[1].type or "linear",
                                    "style": trace.mode, "x": values[:, 0], "y": values[:, 1]}))
    table = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
    if table.empty or set(table["measurement"]) != set(labels):
        raise ValueError("At least one selected measurement cannot be plotted.")
    return ReproducedPlot(table, settings.get("title") or representation,
                          dict(settings), tuple(snapshots[label].reference for label in labels))


def plot_figure(plot):
    """Render the scientific traces as a print-friendly, scalable Matplotlib figure."""
    from matplotlib.figure import Figure
    from matplotlib.ticker import ScalarFormatter

    panels = plot.table["panel"].nunique()
    figure = Figure(figsize=(6.8, 4.2 if panels == 1 else 6.3), layout="constrained")
    axes = figure.subplots(panels, 1, squeeze=False).ravel()
    palette = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#806000", "#26778C", "#4B4B4B")
    for panel, rows in plot.table.groupby("panel", sort=True):
        axis = axes[panel - 1]
        for index, (_, group) in enumerate(rows.groupby("trace", sort=False)):
            trace = group.iloc[0]
            style = trace["style"]
            axis.plot(group["x"], group["y"], label=trace["measurement"],
                      linestyle="-" if "lines" in style else "None",
                      marker="o" if "markers" in style else "None",
                      markersize=3, linewidth=1.45, color=palette[index % len(palette)])
        axis.set_xlabel(_axis_title(trace["x_axis"], trace["x_unit"]), fontsize=10)
        axis.set_ylabel(_axis_title(trace["y_axis"], trace["y_unit"]), fontsize=10)
        axis.set_xscale(trace["x_scale"], **({"nonpositive": "mask"} if trace["x_scale"] == "log" else {}))
        axis.set_yscale(trace["y_scale"], **({"nonpositive": "mask"} if trace["y_scale"] == "log" else {}))
        if trace["x_scale"] == "linear":
            axis.xaxis.set_major_formatter(ScalarFormatter(useMathText=True, useOffset=False))
        if trace["y_scale"] == "linear":
            axis.yaxis.set_major_formatter(ScalarFormatter(useMathText=True, useOffset=False))
        axis.grid(alpha=0.22)
        if plot.settings.get("legend", True) and panel == 1:
            axis.legend(loc="best", fontsize=8, frameon=False)
        if plot.settings.get("representation") == "Nyquist":
            axis.set_aspect("equal", adjustable="datalim")
    figure.suptitle(plot.title, fontsize=13, fontweight="bold")
    return figure


def _axis_title(label, unit):
    if not unit or column_unit(label):
        return label
    symbol = {"ohm": "Ω", "deg": "°", "uA": "µA", "uV": "µV", "us": "µs"}.get(unit, unit)
    return f"{label} ({symbol})"


def _unit_identity(unit):
    from .browser_analysis import _NORMALIZE_UNITS

    return str(unit).translate(_NORMALIZE_UNITS).replace("Ohm", "ohm").strip() if unit else None


def _plot_bytes(plot, fmt):
    from matplotlib import rc_context

    stream = BytesIO()
    with rc_context({"svg.fonttype": "none", "pdf.fonttype": 42, "text.usetex": False,
                     "text.parse_math": False}):
        figure = plot_figure(plot)
        metadata_fields = {"Title": plot.title}
        figure.savefig(stream, format=fmt, dpi=170, metadata=metadata_fields)
    return stream.getvalue()


@contextmanager
def _export(workspace, name):
    folder = new_folder(Path(workspace) / "Workbench" / "Reports", name, reserve=42)
    try:
        yield folder
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _archive(path, folder, files):
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for item in files:
            archive.write(item, item.relative_to(folder).as_posix())
    return path


def write_publication_export(data, workspace, settings):
    labels = _validate_plot(settings, measurement_catalog(data))
    plot = reproduce_plot(capture_measurements(data, labels), settings)
    with _export(workspace, "Publication") as folder:
        files = [folder / f"figure.{fmt}" for fmt in ("svg", "pdf")]
        for item in files:
            item.write_bytes(_plot_bytes(plot, item.suffix[1:]))
        table = folder / "figure_data.csv"
        plot.table.to_csv(table, index=False)
        preview_file = folder / "figure.png"
        preview_file.write_bytes(_plot_bytes(plot, "png"))
        files += [table, _write_json(folder / "figure_provenance.json",
                                    {"version": APP_VERSION, "settings": settings, "sources": plot.sources})]
        archive = _archive(folder / "publication.zip", folder, files)
    record_operation(workspace, "Publication figure export", [ref["path"] for ref in plot.sources], settings,
                     [*files, preview_file, archive])
    return {"files": [*files, archive], "preview": plot.table}


def _validate_trace(settings):
    keys = _TRACE_COLUMNS | {"technique", "final_time_percent", "applied_current_A"}
    if not isinstance(settings, dict) or set(settings) - keys:
        raise ValueError("Trace metrics has unsupported settings.")
    technique = settings.get("technique")
    if not isinstance(technique, str) or technique not in TRACE_TECHNIQUES:
        raise ValueError("Choose a supported trace analysis technique.")
    if any(not isinstance(settings[key], str) or not settings[key].strip()
           for key in settings.keys() & _TRACE_COLUMNS):
        raise ValueError("Trace column selections must be column names.")
    for field, allowed_technique in (("final_time_percent", "OCP"), ("applied_current_A", "CP")):
        if field in settings:
            value = settings[field]
            if (technique != allowed_technique or type(value) not in {int, float}
                    or not -1e100 < value < 1e100 or (field == "final_time_percent" and not 0 < value <= 100)):
                raise ValueError(f"Invalid {field} for {technique}.")


def validate_recipe(recipe, available):
    """Only named deterministic operations and documented per-step fields are accepted."""
    if (not isinstance(recipe, dict) or set(recipe) != {"schema", "steps"}
            or recipe["schema"] != RECIPE_SCHEMA or not isinstance(recipe["steps"], list)
            or not 1 <= len(recipe["steps"]) <= 50):
        raise ValueError("Choose a version 1 workbench recipe containing 1 to 50 steps.")
    ordered = []
    for step in recipe["steps"]:
        if not isinstance(step, dict):
            raise ValueError("Each recipe step must be a JSON object.")
        tool = step.get("tool")
        if tool == "plot" and set(step) == {"tool", "settings"}:
            ordered.extend(_validate_plot(step["settings"], available))
        elif tool in ("table_quality", "trace_metrics"):
            keys = {"tool", "measurement"} if tool == "table_quality" else {"tool", "measurement", "settings"}
            label = step.get("measurement")
            if set(step) != keys or not isinstance(label, str) or label not in available:
                raise ValueError(f"{tool} must name one measurement from the reviewed scope.")
            if tool == "trace_metrics":
                _validate_trace(step["settings"])
            ordered.append(label)
        else:
            raise ValueError(f"Unsupported recipe step: {tool!r}.")
    return tuple(dict.fromkeys(ordered))


def _trace_metrics(snapshot, settings):
    from .browser_analysis import analyze_trace

    technique = settings["technique"]
    if technique in {"CA", "DEPOSITION"}:
        required = ("time", "current")
    elif technique in {"OCP", "CP"}:
        required = ("time", "potential")
    else:
        required = ("potential", "current")
    _require_units(snapshot, required, settings)
    from .browser_analysis import measurement_roles

    detected = measurement_roles(snapshot.frame)
    optional = [role for role in _TRACE_COLUMNS - set(required)
                if (role != "current" or technique != "OCP") and (settings.get(role) or detected.get(role))]
    _require_units(snapshot, optional, settings)
    result = analyze_trace(snapshot.frame, identity={"relative_path": snapshot.reference["label"]}, **settings)
    if result.get("status", "complete") != "complete":
        reason = result.get("reason") or result.get("warning") or result.get("status")
        raise ValueError(f"{snapshot.reference['label']} could not be analyzed. {reason}")
    return result


def execute_recipe(snapshots, recipe):
    """Run the same deterministic steps for an interactive recipe and a replay notebook."""
    from .browser_productivity import table_quality

    validate_recipe(recipe, snapshots)
    results = []
    for step in recipe["steps"]:
        tool = step["tool"]
        if tool == "plot":
            value = reproduce_plot(snapshots, step["settings"])
        else:
            snapshot = snapshots[step["measurement"]]
            if tool == "table_quality":
                frame, issues = table_quality(snapshot.frame)
                value = {"table": frame.to_dict("records"), "issues": issues}
            else:
                value = _trace_metrics(snapshot, step["settings"])
        results.append((step, value))
    return results


def _plain(value):
    """Write numeric results as strict JSON, with missing/nonfinite values as null."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_plain(item) for item in value]
    if isinstance(value, np.generic):
        return _plain(value.item())
    if (isinstance(value, float) and not math.isfinite(value)) or value is pd.NA:
        return None
    if isinstance(value, Path):
        return str(value)
    return value


def run_reviewed_recipe(data, workspace, recipe, selected):
    catalog = measurement_catalog(data)
    selected = set(selected)
    if not selected <= catalog.keys():
        raise ValueError("The reviewed scope includes a measurement outside the current project.")
    labels = validate_recipe(recipe, selected)
    snapshots = capture_measurements(data, labels)
    results = execute_recipe(snapshots, recipe)
    with _export(workspace, "Recipe") as folder:
        files = [_write_json(folder / "recipe.json", recipe)]
        receipts = []
        for index, (step, value) in enumerate(results, 1):
            if isinstance(value, ReproducedPlot):
                output = folder / f"step_{index:02d}_curves.csv"
                value.table.to_csv(output, index=False)
                svg = folder / f"step_{index:02d}_figure.svg"
                svg.write_bytes(_plot_bytes(value, "svg"))
                files += [output, svg]
                receipts.append({"step": step, "files": [output.name, svg.name]})
            else:
                output = _write_json(folder / f"step_{index:02d}_result.json", _plain(value))
                files.append(output)
                receipts.append({"step": step, "files": [output.name]})
        refs = [snapshot.reference for snapshot in snapshots.values()]
        files.append(_write_json(folder / "provenance.json", {"version": APP_VERSION, "sources": refs, "steps": receipts}))
    record_operation(workspace, "Run reviewed workbench recipe", [ref["path"] for ref in refs], recipe, files)
    return {"files": files, "steps": receipts}


def restore_snapshots(entries):
    """Check the selected input snapshots before any notebook calculation."""
    snapshots = {}
    for item in entries:
        reference = item["reference"]
        content = base64.b64decode(item["csv_base64"], validate=True)
        if _digest(content) != reference["csv_sha256"]:
            raise ValueError(f"Input snapshot hash mismatch for {reference['label']}.")
        dtypes = {column: str for column in reference["text_columns"]}
        dtypes.update(reference.get("float_dtypes", {}))
        frame = pd.read_csv(BytesIO(content), float_precision="round_trip", keep_default_na=False,
                            na_values=[""], dtype=dtypes)
        snapshots[reference["label"]] = MeasurementSnapshot(reference, frame, content)
    return snapshots


def write_reproducibility_notebook(data, workspace, recipe, selected):
    catalog = measurement_catalog(data)
    selected = set(selected)
    if not selected <= catalog.keys():
        raise ValueError("The notebook scope includes a measurement outside the current project.")
    labels = validate_recipe(recipe, selected)
    snapshots = capture_measurements(data, labels)
    entries = [{"reference": item.reference, "csv_base64": base64.b64encode(item.csv).decode("ascii")}
               for item in snapshots.values()]
    versions = _versions(("numpy", "pandas", "scipy", "matplotlib", "plotly"))
    payload = {"recipe": recipe, "application_version": APP_VERSION, "versions": versions, "inputs": entries}
    # Verify the round-tripped input works before saving the notebook.
    execute_recipe(restore_snapshots(entries), recipe)
    encoded = base64.b64encode(_json(payload).encode("utf-8")).decode("ascii")
    code = [
        f"""import base64
import json
from faradaiq.release_identity import APP_VERSION
from faradaiq.workbench_reports import (
    ReproducedPlot, execute_recipe, plot_figure, restore_snapshots,
)

provenance = json.loads(base64.b64decode({encoded!r}))
if APP_VERSION != provenance["application_version"]:
    raise RuntimeError("Open this notebook with eStudio " + provenance["application_version"])
snapshots = restore_snapshots(provenance["inputs"])
print(json.dumps(provenance["versions"], indent=2))
""",
        """results = execute_recipe(snapshots, provenance["recipe"])
curves = [result.table for _, result in results if isinstance(result, ReproducedPlot)]
metrics = [result for _, result in results if not isinstance(result, ReproducedPlot)]
for step, result in results:
    print(step["tool"], step.get("measurement", ""))
    if isinstance(result, ReproducedPlot):
        display(plot_figure(result))
        display(result.table)
    else:
        display(result)
""",
    ]
    notes = [
        "# Electrochemical analysis reproduction\n",
        f"Run all cells with the {APP_NAME} {APP_VERSION} Python environment and a Jupyter kernel.\n",
        "The notebook verifies the SHA-256 of each embedded CSV snapshot before replaying the recorded plot and deterministic metrics.\n",
        "\n", "Selected inputs\n",
        *[f"- {escape(ref.reference['label']).replace('[', '&#91;').replace('`', '&#96;')}"
          f" · `{ref.reference['csv_sha256']}`\n" for ref in snapshots.values()],
    ]
    notebook = {"nbformat": 4, "nbformat_minor": 5,
                "metadata": {"kernelspec": {"display_name": "Python 3 (eStudio)", "language": "python", "name": "python3"},
                             "language_info": {"name": "python", "version": versions["Python"]},
                             "faradaiq": {"application_version": APP_VERSION, "versions": versions,
                                         "inputs": [item["reference"] for item in entries], "recipe": recipe}},
                "cells": [{"id": "provenance", "cell_type": "markdown", "source": notes, "metadata": {}},
                          *[{"id": f"replay-{index}", "cell_type": "code", "source": source.splitlines(keepends=True),
                             "metadata": {}, "execution_count": None, "outputs": []}
                            for index, source in enumerate(code, 1)]]}
    with _export(workspace, "Notebook") as folder:
        path = _write_json(folder / "reproduce.ipynb", notebook)
    record_operation(workspace, "Export reproducibility notebook",
                     [entry["reference"]["path"] for entry in entries], recipe, [path])
    return {"files": [path]}


@dataclass(frozen=True)
class DisplayedTrace:
    reference: dict
    values: np.ndarray
    csv: bytes


def review_scilink_plot(data, displayed_plot):
    """Collect only the visible numeric axes of the last successfully rendered plot."""
    from .browser_plotting import _trace_values

    if not displayed_plot or displayed_plot.get("source") != str(data.source):
        raise ValueError("Display a plot under Analysis for this source before preparing its SciLink files.")
    catalog = measurement_catalog(data)
    figure, labels = displayed_plot["figure"], displayed_plot["trace_labels"]
    settings = dict(displayed_plot["settings"])
    if (len(labels) != len(figure.data) or any(label not in catalog for label in labels)
            or settings.get("measurement") not in catalog):
        raise ValueError("This plot has changed. Display it again under Analysis to review the current traces.")
    representation = settings["representation"]
    if representation not in {"XY overlay", "Bode", "Nyquist"}:
        raise ValueError("The displayed plot format has no SciLink trace export.")
    traces = []
    if representation == "XY overlay":
        columns = [settings["x"], settings["y"]]
        base = data.records.get(catalog[settings["measurement"]], {})
        base_units = _column_units(pd.DataFrame(columns=columns), base.get("units"))
        x_unit, y_unit = map(base_units.get, columns)
    for index, _, panel, x_axis, y_axis, values in _trace_values(figure):
        trace, label = figure.data[index - 1], labels[index - 1]
        if trace.visible in (False, "legendonly"):
            continue
        visible = np.isfinite(values).all(axis=1)
        for axis, kind in enumerate(("x", "y")):
            axis_ref = getattr(trace, kind + "axis", None) or kind
            if figure.layout[f"{kind}axis{axis_ref[1:]}"].type == "log":
                visible &= values[:, axis] > 0
        points = values[visible]
        if not len(points):
            continue
        if representation == "Bode":
            x_unit, y_unit = "Hz", "deg" if panel == 2 else "ohm"
        elif representation == "Nyquist":
            x_unit = y_unit = "ohm"
        csv = pd.DataFrame(points, columns=["x", "y"]).to_csv(index=False).encode("utf-8")
        record = data.records.get(catalog[label], {})
        reference = {"label": label, "trace": index, "panel": panel, "x_axis": x_axis, "y_axis": y_axis,
                     "rows": len(points), "units": {"x": x_unit, "y": y_unit}, "csv_sha256": _digest(csv),
                     "technique": str(record.get("technique_key") or record.get("technique") or "")}
        traces.append(DisplayedTrace(reference, points, csv))
    if not traces:
        raise ValueError("The displayed plot has no visible finite coordinate pairs to include.")
    used = list(dict.fromkeys(trace.reference["label"] for trace in traces))
    if settings.get("measurement") not in used:
        settings["measurement"] = used[0]
    settings["overlays"] = [label for label in used if label != settings["measurement"]]
    return settings, traces


def _restore_scilink_plot_precision(receipt, by_filename):
    """Keep the HDF5 tables aligned with the reviewed IEEE-754 values and axes."""
    import h5py

    h5_path = Path(receipt["h5_path"])
    manifest = json.loads(Path(receipt["json_path"]).read_text(encoding="utf-8"))
    ready = manifest["scilink_ready"]
    review = {ref["staged_table"]: ref for ref in ready["experimental_metadata"]["displayed_traces"]}
    restored = set()
    with h5py.File(h5_path, "r+") as bundle:
        for record in ready["datasets"]:
            if not record["path"].startswith("/tables/"):
                continue
            filename = Path(record["source_path"]).name
            trace = by_filename.get(filename)
            table = bundle[record["path"]]
            if (trace is None or filename in restored or table.shape != trace.values.shape
                    or record["columns"] != ["x", "y"]):
                raise ValueError("SciLink could not stage the reviewed plot exactly. No handoff was saved.")
            table[...] = trace.values
            record["units"] = trace.reference["units"]
            table.attrs["units_json"] = _json(record["units"])
            for key in ("x_axis", "y_axis", "panel"):
                table.attrs[key] = trace.reference[key]
            review[filename]["dataset"] = record["path"]
            restored.add(filename)
        if restored != by_filename.keys():
            raise ValueError("SciLink could not stage every reviewed trace. No handoff was saved.")
        bundle["manifest_json"][()] = _json(manifest)
        bundle["metadata/scilink_ready_json"][()] = _json(ready)
    _write_json(Path(receipt["json_path"]), manifest)
    return ready


def write_scilink_handoff(data, workspace, *, selected=None, displayed_plot=None, context=""):
    """Create the normal reviewable SciLink package from a staged, restricted input subset."""
    from .scilink_browser_handoff import streamlit_handoff_files, streamlit_handoff_text, write_streamlit_metadata
    from .scilink_bundle import make_scilink_ready_bundle

    if (selected is None) == (displayed_plot is None):
        raise ValueError("Choose either the active plot or explicitly selected measurements.")
    if displayed_plot is not None:
        settings, measurements = review_scilink_plot(data, displayed_plot)
    else:
        settings, measurements = None, list(capture_measurements(data, selected).values())
    if not measurements:
        raise ValueError("Select at least one measurement for the SciLink handoff.")
    catalog = measurement_catalog(data)
    with TemporaryDirectory(prefix="estudio_scilink_") as temp:
        staged = Path(temp) / "review"
        staged.mkdir()
        references = []
        by_filename = {}
        for number, snapshot in enumerate(measurements, 1):
            reference = {key: value for key, value in snapshot.reference.items()
                         if key not in {"path", "source_path", "original_table_path", "hdf5_dataset"}}
            name = safe_component(Path(reference["label"]).stem, max_chars=55)
            filename = f"{number:03d}_{name}.csv"
            (staged / filename).write_bytes(snapshot.csv)
            references.append(dict(reference, staged_table=filename))
            if settings is not None:
                by_filename[filename] = snapshot
        if settings is not None:
            selected_measurements = [{"label": label} for label in dict.fromkeys(ref["label"] for ref in references)]
        else:
            selected_measurements = references
        provenance = {"scope": "active_plot" if settings is not None else "selected_tables",
                      "selected_measurements": selected_measurements, "plot_recipe": settings,
                      "experimental_description": str(context)}
        if settings is not None:
            provenance["displayed_traces"] = references
        manifest = {"csv_entries": [
            {"relative_csv_path": ref["staged_table"], "columns": list(ref["units"]),
             "technique": ref.get("technique_key") or ref.get("technique", ""), "relative_path": ref["label"]}
            for ref in references
        ]}
        _write_json(staged / "analysis_ready_manifest.json", manifest)
        receipt = make_scilink_ready_bundle(staged, sample_name="Selected measurements", metadata=provenance)
        origin = Path(receipt["h5_path"]).parent
        if by_filename:
            ready = _restore_scilink_plot_precision(receipt, by_filename)
            provenance = ready["experimental_metadata"]
            write_streamlit_metadata(origin, ready, Path(receipt["h5_path"]).name)
        with _export(workspace, "SciLink") as folder:
            files = []
            for source in sorted(origin.iterdir()):
                if source.is_file() and source.name != "name_mapping.json":
                    output = folder / source.name
                    shutil.copyfile(source, output)
                    files.append(output)
            handoff = streamlit_handoff_files(folder / Path(receipt["h5_path"]).name)
            upload_metadata = json.loads(handoff["Metadata"].read_text(encoding="utf-8"))
            upload_metadata["faradaiq_bundle"].update(provenance)
            _write_json(handoff["Metadata"], upload_metadata)
            instructions = streamlit_handoff_text(handoff["Data"])
            if by_filename:
                instructions += "\n\nThe displayed_traces metadata gives the axis labels, units, and panel for each dataset."
            archive = _archive(folder / "scilink_selected.zip", folder, files)
    record_operation(workspace, "Prepare selected SciLink handoff",
                     [catalog[label] for label in dict.fromkeys(entry["label"] for entry in references)],
                     {"scope": provenance["scope"], "plot": settings},
                     [*files, archive])
    return {"files": [*files, archive], "instructions": instructions}


@dataclass(frozen=True)
class EvidenceAsset:
    reference: dict
    content: bytes
    mime: str


def discover_result_files(data, workspace):
    """Only offer audit-listed workbench files linked to this source's measurements."""
    choices = {path.resolve() for path in measurement_catalog(data).values()} | {Path(data.source).resolve()}
    root = Path(workspace).resolve() / "Workbench"
    from .workbench_audit import read_history

    files = {}
    for row in read_history(workspace):
        if not choices.intersection(Path(value).resolve() for value in (row.get("inputs") or [])):
            continue
        for entry in row.get("outputs") or []:
            path = Path(entry).resolve()
            kind = "figure" if path.suffix.lower() in _IMAGE_TYPES else "result"
            if (path.is_relative_to(root) and path.is_file() and path.suffix.lower() in _IMAGE_TYPES | _TABLE_TYPES):
                files[path] = (kind, f"{_relative(path, root)} · {row['action']}")
    return files


def _asset(path, kind, workspace):
    if kind not in ("figure", "result"):
        raise ValueError("Select a result table or figure.")
    formats = _IMAGE_TYPES if kind == "figure" else _TABLE_TYPES
    path = Path(path).expanduser().resolve(strict=True)
    if path.suffix.lower() not in formats or not path.is_file():
        raise ValueError(f"Unsupported {kind} file: {path.name}.")
    payload = path.read_bytes()
    if kind == "figure":
        from PIL import Image
        with Image.open(BytesIO(payload)) as original:
            original.verify()
    elif path.suffix.lower() == ".json":
        json.loads(payload)
    digest = _digest(payload)
    relative = _relative(path, Path(workspace).resolve() / "Workbench")
    ref = {"id": f"{kind}-{_digest((relative + digest).encode())[:20]}",
           "kind": kind, "label": path.name, "relative_path": relative,
           "path": str(path), "file_sha256": digest}
    return EvidenceAsset(ref, payload, formats[path.suffix.lower()])


def _plot_asset(plot, path, workspace):
    path.write_bytes(_plot_bytes(plot, "png"))
    asset = _asset(path, "figure", workspace)
    return EvidenceAsset(dict(asset.reference, label=plot.title, settings=plot.settings,
                              derived_from=[ref["id"] for ref in plot.sources]), asset.content, asset.mime)


def _make_registry(references, conclusions, *, aliases=None):
    evidence = {ref["id"]: ref for ref in references}
    claims = []
    for conclusion in conclusions:
        if not isinstance(conclusion, Mapping) or not isinstance(conclusion.get("text"), str):
            raise ValueError("Use a conclusion with text and linked evidence.")
        text = conclusion["text"].strip()
        related = conclusion.get("evidence")
        if not text or not isinstance(related, (list, tuple)) or not related:
            raise ValueError("Write a conclusion and link at least one selected evidence item.")
        if any(not isinstance(ref, str) for ref in related):
            raise ValueError("Conclusion evidence identifiers must be text.")
        related = [(aliases or {}).get(ref, ref) for ref in related]
        if any(ref not in evidence for ref in related):
            raise ValueError("A conclusion links to evidence outside the selected items.")
        claims.append({"id": f"conclusion-{len(claims) + 1:03d}", "text": text,
                       "evidence": list(dict.fromkeys(related))})
    return {"schema": EVIDENCE_SCHEMA, "version": APP_VERSION, "evidence": evidence, "conclusions": claims}


def list_evidence(data, workspace, selected, *, figures=(), tables=()):
    measurements = capture_measurements(data, selected)
    paths = [(path, "figure") for path in figures] + [(path, "result") for path in tables]
    assets = [_asset(path, kind, workspace) for path, kind in dict.fromkeys(paths)]
    return [snapshot.reference for snapshot in measurements.values()] + [asset.reference for asset in assets]


def write_evidence_registry(data, workspace, selected, conclusions, *, figures=(), tables=()):
    refs = list_evidence(data, workspace, selected, figures=figures, tables=tables)
    if not refs or not conclusions:
        raise ValueError("Select evidence and add a linked conclusion.")
    registry = _make_registry(refs, conclusions)
    with _export(workspace, "Evidence") as folder:
        path = _write_json(folder / "evidence_registry.json", registry)
    record_operation(workspace, "Save evidence registry", [ref["path"] for ref in refs],
                     {"conclusions": len(conclusions)}, [path])
    return {"files": [path], "registry": registry}


def resolve_evidence(registry, *, roots=(), candidates=()):
    """Locate existing or relocated files and verify their recorded contents."""
    if (not isinstance(registry, dict) or registry.get("schema") != EVIDENCE_SCHEMA
            or not isinstance(registry.get("evidence"), dict)):
        raise ValueError("Choose an eStudio evidence registry.")
    roots = [Path(root).expanduser().resolve() for root in roots]
    candidates = [Path(path).expanduser().resolve() for path in candidates]
    checked = {}
    found = []
    for identifier, ref in registry["evidence"].items():
        if (not isinstance(ref, dict) or any(not isinstance(ref.get(key), str)
                for key in ("label", "path", "relative_path", "file_sha256"))
                or not re.fullmatch(r"[0-9a-f]{64}", ref["file_sha256"])
                or not re.fullmatch(r"(?:measurement|result|figure)-[0-9a-f]{20}", identifier)):
            raise ValueError("The registry contains an invalid evidence reference.")
        previous = Path(ref["path"])
        relative = Path(ref["relative_path"])
        options = [previous]
        for root in roots:
            if relative.is_absolute() or ".." in relative.parts:
                continue
            options.extend([root / relative, root / previous.name])
            extension = ".csv" if ref.get("kind") == "measurement" else Path(previous).suffix
            options.append(root / "evidence" / f"{identifier}{extension}")
            if previous.name:
                options.extend(root.rglob(previous.name.replace("[", "[[]").replace("?", "[?]").replace("*", "[*]")))
        status = "Missing or changed"
        matched = ""
        for option in dict.fromkeys([*options, *candidates]):
            try:
                if option not in checked:
                    checked[option] = _file_digest(option) if option.is_file() else None
                if checked[option] == ref["file_sha256"]:
                    matched = str(option.resolve())
                    status = "Verified" if option == previous else "Relocated and verified"
                    break
                if ref.get("csv_sha256") and checked[option] == ref["csv_sha256"]:
                    matched, status = str(option.resolve()), "Verified table snapshot"
                    break
            except OSError:
                continue
        found.append({"id": identifier, "label": ref["label"], "status": status, "path": matched})
    return found


def _preview(asset, rows=12, cols=8):
    if asset.mime == "text/csv":
        return pd.read_csv(BytesIO(asset.content), nrows=rows, keep_default_na=False).iloc[:, :cols].astype(str)
    value = json.loads(asset.content)
    if isinstance(value, list) and value and all(isinstance(row, dict) for row in value[:rows]):
        return pd.DataFrame(value[:rows]).iloc[:, :cols].fillna("").astype(str)
    fields = value.items() if isinstance(value, dict) else [("Result", value)]
    return pd.DataFrame([(str(key), json.dumps(item, ensure_ascii=False)) for key, item in list(fields)[:rows]],
                        columns=["Field", "Value"])


def _data_uri(mime, payload):
    return f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")


def _report_html(title, methods, context, registry, assets):
    def content(text):
        return escape(text).replace("\n", "<br>\n")
    evidence = registry["evidence"]
    evidence_sections = []
    for asset in assets:
        ref = asset.reference
        uri = _data_uri(asset.mime, asset.content)
        if ref["kind"] == "figure":
            preview = f'<img alt="{escape(ref["label"])}" src="{uri}">'
        else:
            preview = _preview(asset).to_html(index=False, border=0, escape=True)
        referenced = " ".join(f'<a href="#{source}">{escape(evidence[source]["label"])}</a>'
                              for source in ref.get("derived_from", []))
        filename = Path(ref["path"]).with_suffix(".csv").name if ref["kind"] == "measurement" else Path(ref["path"]).name
        evidence_sections.append(
            f'<section id="{ref["id"]}"><h3>{content(ref["label"])}</h3>{preview}'
            f'<p>{referenced}</p>'
            f'<p><a download="{escape(filename)}" href="{uri}">Download full evidence</a></p>'
            f'<p class="provenance">{content(ref["path"])}<br>SHA-256 {ref.get("csv_sha256", ref["file_sha256"])}</p>'
            '</section>')
    conclusions = []
    for claim in registry["conclusions"]:
        citations = " · ".join(f'<a href="#{ref}">{escape(evidence[ref]["label"])}</a>' for ref in claim["evidence"])
        conclusions.append(f'<li id="{claim["id"]}">{content(claim["text"])}<br>{citations}</li>')
    registry_uri = _data_uri("application/json", _json(registry).encode("utf-8"))
    conclusion_section = f'<h2>Conclusions</h2><ol>{"".join(conclusions)}</ol>' if conclusions else ""
    text_sections = "".join(f"<h2>{name}</h2><p>{content(text)}</p>"
                            for name, text in (("Experimental context", context), ("Methods", methods)) if text.strip())
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
body{{color:#1a2535;background:#f8fafc;font:16px/1.55 system-ui,sans-serif;margin:0}}
main{{max-width:76ch;margin:auto;padding:3rem 1.25rem}}
h1,h2,h3{{color:#13304a;line-height:1.25}} h1{{font-size:2.45rem}}
h2{{margin-top:2rem}} h3{{margin:.1rem 0 .8rem}} a{{color:#005788}}
section{{border:1px solid #d4dee7;border-radius:.5rem;padding:1rem;margin-bottom:1rem;overflow:auto;background:#fff}}
section:target{{outline:3px solid #0072B2}}
table{{border-collapse:collapse;text-align:left;font-size:.9rem}} th,td{{padding:.35rem .7rem;border-bottom:1px solid #e1e7ed}}
img{{max-width:100%;height:auto}} li{{margin-bottom:.7rem}} .provenance{{overflow-wrap:anywhere;font-size:.8rem}}
</style><main><h1>{content(title)}</h1><p>{APP_NAME} {APP_VERSION}</p>{text_sections}{conclusion_section}
<h2>Evidence</h2>{''.join(evidence_sections)}
<p><a download="evidence_registry.json" href="{registry_uri}">Download evidence registry</a></p></main></html>"""


def _office_text(text):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", str(text))


def _office_docx(path, title, context, methods, registry, assets):
    from docx import Document
    from docx.shared import Inches

    report = Document()
    report.add_heading(_office_text(title), 0)
    report.add_paragraph(f"{APP_NAME} {APP_VERSION}")
    for heading, text in (("Experimental context", context), ("Methods", methods)):
        if text.strip():
            report.add_heading(heading, level=1)
            report.add_paragraph(_office_text(text))
    if registry["conclusions"]:
        report.add_heading("Conclusions", level=1)
        for claim in registry["conclusions"]:
            report.add_paragraph(_office_text(claim["text"]), style="List Number")
            labels = [registry["evidence"][key]["label"] for key in claim["evidence"]]
            report.add_paragraph("Evidence · " + _office_text(" · ".join(labels)))
    report.add_heading("Evidence", level=1)
    for asset in assets:
        ref = asset.reference
        report.add_heading(_office_text(ref["label"]), level=2)
        if ref["kind"] == "figure":
            from PIL import Image
            with Image.open(BytesIO(asset.content)) as image:
                width, height = image.size
            report.add_picture(BytesIO(asset.content), width=Inches(min(6.3, 6.5 * width / height)))
        else:
            preview = _preview(asset, rows=10, cols=6)
            table = report.add_table(rows=1, cols=len(preview.columns), style="Light Shading Accent 1")
            for cell, column in zip(table.rows[0].cells, preview.columns):
                cell.text = _office_text(column)
            for values in preview.itertuples(index=False, name=None):
                for cell, value in zip(table.add_row().cells, values):
                    cell.text = _office_text(value[:240])
        report.add_paragraph(f"{ref['id']} · SHA-256 {ref.get('csv_sha256', ref['file_sha256'])}", style="Caption")
    report.save(path)


def _office_pptx(path, title, context, methods, registry, assets):
    from PIL import Image
    from pptx import Presentation
    from pptx.util import Inches, Pt

    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)

    def slide(heading):
        current = presentation.slides.add_slide(presentation.slide_layouts[5])
        current.shapes.title.text = _office_text(heading)
        return current

    def textbox(current, text, y=1.6, h=5.5, font=21):
        box = current.shapes.add_textbox(Inches(.8), Inches(y), Inches(11.7), Inches(h))
        box.text_frame.word_wrap = True
        box.text = _office_text(text)
        for paragraph in box.text_frame.paragraphs:
            paragraph.font.size = Pt(font)

    def text_slides(heading, text):
        # Break long user-authored sections at line boundaries and wrap unbroken paragraphs.
        import textwrap
        lines = [part for line in text.splitlines() for part in (textwrap.wrap(line, width=86) or [""])]
        for start in range(0, len(lines), 13):
            textbox(slide(heading), "\n".join(lines[start:start + 13]))

    textbox(slide(title), f"{APP_NAME} {APP_VERSION}\n{len(assets)} evidence items", y=2.7)
    for heading, text in (("Experimental context", context), ("Methods", methods)):
        if text.strip():
            text_slides(heading, text)
    for claim in registry["conclusions"]:
        labels = [registry["evidence"][key]["label"] for key in claim["evidence"]]
        text_slides("Conclusion", claim["text"] + "\n\nEvidence · " + " · ".join(labels))
    for asset in assets:
        ref = asset.reference
        current = slide(ref["label"])
        if ref["kind"] == "figure":
            payload = BytesIO(asset.content)
            with Image.open(payload) as image:
                width, height = image.size
            scale = min(11.5 / width, 5.25 / height)
            picture_width, picture_height = width * scale, height * scale
            payload.seek(0)
            current.shapes.add_picture(payload, Inches((13.333 - picture_width) / 2), Inches(1.35),
                                       Inches(picture_width), Inches(picture_height))
        else:
            preview = _preview(asset, rows=7, cols=6)
            table = current.shapes.add_table(len(preview) + 1, len(preview.columns),
                                             Inches(.8), Inches(1.45), Inches(11.7), Inches(4.8)).table
            for row, values in enumerate([list(preview.columns), *preview.itertuples(index=False, name=None)]):
                for column, value in enumerate(values):
                    cell = table.cell(row, column)
                    cell.text = _office_text(value[:120])
                    for paragraph in cell.text_frame.paragraphs:
                        paragraph.font.size = Pt(13)
        textbox(current, f"{ref['id']} · SHA-256 {ref.get('csv_sha256', ref['file_sha256'])}",
                y=6.75, h=.5, font=10)
    presentation.save(path)


def write_report(data, workspace, selected, *, title, context="", methods="", conclusions=(), figures=(),
                 tables=(), settings=None, formats=("html", "docx", "pptx")):
    """Package selected evidence with author-supplied methods, context, and conclusions."""
    formats = tuple(dict.fromkeys(formats))
    if not formats or set(formats) - {"html", "docx", "pptx"}:
        raise ValueError("Choose HTML, DOCX, or PowerPoint report output.")
    title = str(title).strip()
    if not title:
        raise ValueError("Enter a report title.")
    plot_labels = _validate_plot(settings, measurement_catalog(data)) if settings is not None else []
    snapshots = capture_measurements(data, [*selected, *plot_labels])
    assets = [EvidenceAsset(snapshot.reference, snapshot.csv, "text/csv") for snapshot in snapshots.values()]
    for path in dict.fromkeys(figures):
        assets.append(_asset(path, "figure", workspace))
    for path in dict.fromkeys(tables):
        assets.append(_asset(path, "result", workspace))
    if not assets:
        raise ValueError("Select measurements, a plot, or saved analysis evidence.")
    plot = reproduce_plot(snapshots, settings) if settings is not None else None
    with _export(workspace, "Report") as folder:
        supporting_files = []
        if plot:
            figure_path = folder / "report_figure.png"
            assets.append(_plot_asset(plot, figure_path, workspace))
            supporting_files.append(figure_path)
        aliases = {"included-plot": assets[-1].reference["id"]} if plot else None
        registry = _make_registry([asset.reference for asset in assets], conclusions, aliases=aliases)
        files = []
        if "html" in formats:
            path = folder / "report.html"
            path.write_text(_report_html(title, methods, context, registry, assets), encoding="utf-8")
            files.append(path)
        if "docx" in formats:
            path = folder / "report.docx"
            _office_docx(path, title, context, methods, registry, assets)
            files.append(path)
        if "pptx" in formats:
            path = folder / "report.pptx"
            _office_pptx(path, title, context, methods, registry, assets)
            files.append(path)
        files.append(_write_json(folder / "evidence_registry.json", registry))
        evidence_folder = folder / "evidence"
        evidence_folder.mkdir()
        for asset in assets:
            extension = ".csv" if asset.mime == "text/csv" else Path(asset.reference["path"]).suffix
            path = evidence_folder / f"{asset.reference['id']}{extension}"
            path.write_bytes(asset.content)
            supporting_files.append(path)
        archive = _archive(folder / "report_with_evidence.zip", folder, files + supporting_files)
    record_operation(workspace, "Build selected-evidence report",
                     [asset.reference["path"] for asset in assets], {"title": title, "formats": formats},
                     [*files, *supporting_files, archive])
    return {"files": [*files, archive], "registry": registry}


def dependency_health():
    """Inspect installed packages and receipts locally without calling a service."""
    from . import managed_scilink

    packages = {"docx": "python-docx", "pptx": "python-pptx", "h5py": "h5py",
                "matplotlib": "matplotlib", "ipykernel": "ipykernel", "impedance": "impedance",
                "deareis": "deareis", "pyDRTtools": "pyDRTtools"}
    versions = _versions(packages.values())
    tools = []
    for module, package in packages.items():
        try:
            available = util.find_spec(module) is not None
        except (ImportError, ValueError):
            available = False
        tools.append({"dependency": package, "status": "Detected" if available else "Unavailable in eStudio Python",
                      "version": versions[package]})
    optional_env = _APP_ROOT / ".deareis_venv"
    optional_python = optional_env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    tools.append({"dependency": "DearEIS private runtime",
                  "status": "Found executable" if optional_python.is_file() else "Unavailable", "version": None})
    scilink = managed_scilink.current()
    tools.append({"dependency": "SciLink managed runtime", "status": "Installed" if scilink else "Unavailable",
                  "version": scilink.get("version") if scilink else None,
                  "revision": scilink.get("commit") if scilink else None})
    return {"application": APP_NAME, "version": APP_VERSION, "python": versions["Python"],
            "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "dependencies": tools}


def repair_command(target):
    if target == "SciLink":
        return [sys.executable, "-m", "faradaiq.managed_scilink", "update"]
    tools = {"DearEIS": "deareis", "Official pyDRTtools": "pydrttools"}
    if target not in tools:
        raise ValueError("Choose a supported installer.")
    return [sys.executable, str(_APP_ROOT / "scripts" / "setup_analysis_tools.py"), "--root",
            str(_APP_ROOT), "--tool", tools[target], "--auto"]


def repair_dependency(workspace, target, *, reviewed=False):
    if reviewed is not True:
        raise ValueError("Review the installer command before running it.")
    command = repair_command(target)
    try:
        result = subprocess.run(command, cwd=_APP_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", check=False,
                                timeout=600)
        output = result.stdout[-80_000:]
        code = result.returncode
    except (OSError, subprocess.TimeoutExpired) as error:
        output, code = str(error), 1
    outcome = {"command": command, "output": redact_diagnostics(output), "exit_code": code}
    record_operation(workspace, "Run reviewed dependency installer", parameters={"target": target, "exit_code": code})
    return outcome


_SECRET_NAME = re.compile(
    r"password|passphrase|credential|authorization|bearer|secret|(?:api|access|private|client|signing)[ _.-]*key"
    r"|(?:oauth|access|refresh|session|id|auth|api)[ _.-]*token|^token$|(?:^|[_.-])token$|cookie",
    re.IGNORECASE,
)
_SECRET_FIELD = re.compile(
    r"""(?ix)(?P<prefix>(?<![\w.-])["']?[\w.-]*(?:password|passphrase|credential|authorization|bearer|secret|"""
    r"""api[_.-]?key|access[_.-]?key|private[_.-]?key|signing[_.-]?key|client[_.-]?key|token|cookie)"""
    r"""[\w.-]*["']?\s*[:=][ \t]*)"""
    r"""(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)"""
)
_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>\"']+")
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)[ \t]+[\w./~+%=-]+")
_AUTH_HEADER = re.compile(r"(?im)(\b(?:proxy-authorization|authorization|set-cookie|cookie):[ \t]*)[^\r\n]+")
_PEM = re.compile(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----", re.S)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_CLOUD_KEY = re.compile(r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{20,}|"
                        r"github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b")
_REDACTED = "[redacted]"


def _clean_url(match):
    try:
        url = urlsplit(match.group())
        host = url.netloc.rsplit("@", 1)[-1]
        return urlunsplit((url.scheme, host, url.path, _REDACTED if url.query else "",
                           _REDACTED if url.fragment else ""))
    except ValueError:
        return _REDACTED


def redact_diagnostics(value, *, environ=None):
    """Redact structured secret fields and standard credential formats in diagnostic text."""
    environ = os.environ if environ is None else environ
    secrets = sorted({item for key, item in environ.items() if _SECRET_NAME.search(str(key))
                      and isinstance(item, str) and len(item) >= 4}, key=len, reverse=True)

    def sanitize(item):
        if isinstance(item, Mapping):
            return {sanitize(str(key)): _REDACTED if _SECRET_NAME.search(str(key)) else sanitize(val)
                    for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [sanitize(val) for val in item]
        if isinstance(item, str):
            for secret in secrets:
                item = item.replace(secret, _REDACTED)
            item = _PEM.sub(_REDACTED, item)
            item = _AUTH_HEADER.sub(lambda match: match.group(1) + _REDACTED, item)
            item = _BEARER.sub(lambda match: f"{match.group(1)} {_REDACTED}", item)
            item = _URL.sub(_clean_url, item)
            item = _JWT.sub(_REDACTED, item)
            item = _CLOUD_KEY.sub(_REDACTED, item)
            return _SECRET_FIELD.sub(lambda match: match.group("prefix") + _REDACTED, item)
        return item

    return sanitize(value)


def _redact_log(text):
    if text.lstrip().startswith(("{", "[")):
        try:
            return _json(redact_diagnostics(json.loads(text)))
        except ValueError:
            pass
    clean = []
    pending = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(("{", "[")):
            try:
                entry = json.loads(line)
            except ValueError:
                pass
            else:
                clean.append(redact_diagnostics("".join(pending)))
                pending.clear()
                clean.append(json.dumps(redact_diagnostics(entry), ensure_ascii=False) + "\n")
                continue
        pending.append(line)
    clean.append(redact_diagnostics("".join(pending)))
    return "".join(clean)


def discover_diagnostic_logs(workspace):
    """List known app log folders and the workbench audit log for explicit selection."""
    from .managed_scilink import install_root

    workspace = Path(workspace).resolve()
    roots = (_APP_ROOT / "logs", install_root() / "logs", workspace / "logs")
    candidates = [path for root in roots for path in root.glob("*.log") if path.is_file() and not path.is_symlink()]
    audit = workspace / "Workbench" / "analysis_history.jsonl"
    if audit.is_file() and not audit.is_symlink():
        candidates.append(audit)
    return list(dict.fromkeys(candidates))


def write_support_bundle(workspace, *, diagnostics=None, logs=(), settings=None, data=None, include_raw=()):
    """Save selected diagnostic material. A raw table requires an explicit measurement selection."""
    logs = tuple(dict.fromkeys(Path(path).expanduser().resolve() for path in logs))
    if any(path.suffix.lower() not in {".log", ".jsonl"} or not path.is_file() for path in logs):
        raise ValueError("Choose text logs with a .log or .jsonl extension.")
    if include_raw and data is None:
        raise ValueError("Open a project before including measurement tables.")
    raw = capture_measurements(data, include_raw) if include_raw else {}
    manifest = {"application": APP_NAME, "version": APP_VERSION,
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "diagnostics_included": diagnostics is not None, "settings_included": settings is not None,
                "selected_logs": [path.name for path in logs], "raw_measurements": list(raw)}
    with _export(workspace, "Support") as folder:
        archive = folder / "support_bundle.zip"
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as content:
            content.writestr("manifest.json", _json(redact_diagnostics(manifest)))
            for name, value in (("diagnostics", diagnostics), ("settings", settings)):
                if value is not None:
                    content.writestr(f"{name}.json", _json(_plain(redact_diagnostics(value))))
            for index, path in enumerate(logs, 1):
                with path.open("rb") as stream:
                    # Include the most recent five MiB from large logs. Drop partial leading lines.
                    limit = 5 * 1024**2
                    if path.stat().st_size > limit:
                        stream.seek(-limit, os.SEEK_END)
                        stream.readline()
                    log = stream.read(limit).decode("utf-8", errors="replace")
                name = safe_component(redact_diagnostics(path.stem), max_chars=75, default="diagnostic")
                content.writestr(f"logs/{index:02d}_{name}.txt", _redact_log(log))
            for index, (label, snapshot) in enumerate(raw.items(), 1):
                name = safe_component(redact_diagnostics(Path(label).stem), max_chars=75)
                content.writestr(f"measurements/{index:03d}_{name}.csv", snapshot.csv)
    record_operation(workspace, "Build support bundle",
                     [*[str(path) for path in logs], *[item.reference["path"] for item in raw.values()]],
                     {"diagnostics": diagnostics is not None, "settings": settings is not None,
                      "raw_tables": len(raw)}, [archive])
    return {"files": [archive], "manifest": redact_diagnostics(manifest)}
