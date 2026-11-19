"""Browser workbench for registered maps, scalar correlations and stimulus events."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
from PIL import Image
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from . import workbench_spatial as spatial
from .browser_analysis import measurement_roles
from .readable_names import new_folder
from .workbench_audit import record_operation

_PREFIX = "w340_spatial_"
_SETTINGS = _PREFIX + "settings"
_TOOLS = (
    "Optical registration", "ROI and line profiles", "Before and after differences",
    "Map quality flags", "Multimodal scalar correlation", "Stimulus-event alignment",
)
_VALUE_UNITS = ("V", "mV", "uV", "A", "mA", "uA", "nA", "pA", "fA",
                "ohm", "mohm", "kohm", "Mohm", "Hz", "mHz", "kHz", "MHz",
                "K", "°C", "dB", "%", "a.u.", "1")


def _digest(value):
    return sha256(str(value).encode()).hexdigest()[:16]


def _units(unit):
    return "µm" if unit == "um" else "µs" if unit == "us" else str(unit)


class _Page:
    def __init__(self, data, workspace):
        self.data = data
        self.workspace = workspace
        self.source = str(data.source)
        self.prefix = f"{_PREFIX}{_digest(self.source)}_"
        self.settings = st.session_state.setdefault(_SETTINGS, {}).setdefault(self.source, {})
        self.paths = {str(path): path for path in data.tables}

    def key(self, name):
        return self.prefix + name

    def select(self, label, options, name, *, default=None, display=str):
        options = list(options)
        previous = self.settings.get(name, default)
        index = options.index(previous) if previous in options else 0
        value = st.selectbox(label, options, index=index, format_func=display, key=self.key(name))
        self.settings[name] = value
        return value

    def number(self, label, name, *, value=0.0, **kwargs):
        result = st.number_input(label, value=self.settings.get(name, value), key=self.key(name), **kwargs)
        self.settings[name] = result
        return result

    def text(self, label, name, *, value="", area=False, **kwargs):
        widget = st.text_area if area else st.text_input
        result = widget(label, value=self.settings.get(name, value), key=self.key(name), **kwargs)
        self.settings[name] = result
        return result

    def path_label(self, path):
        record = self.data.records.get(self.paths[path], {})
        instrument = record.get("instrument") or ""
        display = record.get("display_name") or self.paths[path].name
        identity = record.get("handle_id") or record.get("source_path") or str(self.paths[path])
        return f"{display} · {instrument + ' · ' if instrument else ''}{identity}"

    def input_references(self, inputs):
        sources = []
        for path in dict.fromkeys(map(str, inputs)):
            if path not in self.paths:
                continue
            record = self.data.records.get(self.paths[path], {})
            fields = ("instrument", "handle_id", "source_path", "original_table_path", "display_name")
            reference = {key: str(record[key]) for key in fields if record.get(key) is not None}
            tags = record.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            reference["tags"] = list(map(str, tags))
            sources.append({"path": path, **reference})
        return sources

    def choose_path(self, label, key, *, exclude=()):
        options = [item for item in self.paths if item not in exclude]
        return self.select(label, [None, *options], key,
                           display=lambda item: "Choose a table" if item is None else self.path_label(item))

    def choose_paths(self, label, key):
        selected = [item for item in self.settings.get(key, []) if item in self.paths]
        result = st.multiselect(label, list(self.paths), default=selected,
                                format_func=self.path_label, key=self.key(key))
        self.settings[key] = result
        return result

    def read(self, path):
        frame = self.data.read_table(self.paths[path])
        if len(frame.columns) < 2 or not frame.columns.is_unique:
            raise ValueError("Select a table with at least two uniquely named columns.")
        if any(not isinstance(column, str) for column in frame.columns):
            frame = frame.rename(columns=str)
        return frame

    def column(self, label, columns, key, default=None, *, optional=False):
        options = [None, *columns] if optional else columns
        return self.select(label, options, key, default=default,
                           display=lambda item: ("None" if optional else "Choose a column") if item is None else str(item))

    def scalar_form(self, path, key, mode, *, require_value=True):
        frame = self.read(path)
        columns = list(frame.columns)
        roles = measurement_roles(frame)
        prefix = f"{key}_{_digest(path)}_"
        if mode == "coordinates":
            fields = ("x", "y")
            labels = ("X column", "Y column")
        else:
            fields = ("time",)
            labels = ("Time column",)
        axes = []
        for field, label in zip(fields, labels):
            options = [column for column in columns if column not in axes]
            detected = roles.get(field)
            default = detected if detected in options else options[0]
            axes.append(self.column(label, options, prefix + field, default))
        choices = [column for column in columns if column not in axes]
        if require_value and not choices:
            raise ValueError("The selected table needs a separate scalar value column.")
        likely = next((roles.get(role) for role in ("current", "potential", "signal")
                       if roles.get(role) in choices), choices[0] if require_value else None)
        value = self.column("Scalar value column", choices, prefix + "value", likely, optional=not require_value)
        units = spatial.DISTANCE_UNITS if mode == "coordinates" else spatial.TIME_UNITS
        declared = next((unit for column in axes if (unit := spatial.header_unit(column, units))), None)
        title = "Coordinate unit" if mode == "coordinates" else "Time unit"
        unit = self.select(title, [None, *units], prefix + "axis_unit_" + _digest(axes), default=declared,
                           display=lambda item: "Choose a unit" if item is None else _units(item))
        status = true_means = value_unit = None
        if value is not None:
            options = [column for column in choices if column != value]
            status = self.column("Acquisition-status column", options, prefix + "status", optional=True)
            if status is not None:
                true_means = self.select("True or 1 means", ("Success", "Failure"),
                                         prefix + "status_polarity_" + _digest(status),
                                         default=spatial.status_flag_means(status))
            value_unit = self.text("Scalar value unit", prefix + "value_unit_" + _digest(value),
                                   value=spatial.header_unit(value, _VALUE_UNITS) or "",
                                   help="Use a.u. or 1 where applicable. Explicit header units convert where compatible.")
        return frame, {"axes": axes, "value": value, "axis_unit": unit, "value_unit": value_unit,
                       "status": status, "true_means": true_means}

    def result(self, key):
        result = st.session_state.get(self.key(key + "_result"))
        if result and result["inputs"]:
            st.caption("Saved result · " + ", ".join(Path(path).name for path in result["inputs"]))
        return result

    def save(self, key, action, inputs, parameters, tables, *, assets=None):
        """Save only on explicit analysis, then retain the result for reruns."""
        base = Path(self.workspace) / "Workbench" / "Spatial"
        folder = new_folder(base, action, reserve=36)
        assets = assets or {}
        names = [f"{name}.csv" for name in tables] + list(assets)
        sources = self.input_references(inputs)
        metadata = {"action": action, "source": self.source, "inputs": list(map(str, inputs)), "sources": sources,
                    "parameters": parameters, "tables": list(tables), "assets": list(assets)}
        try:
            for name, frame in tables.items():
                frame.to_csv(folder / f"{name}.csv", index=False)
            for name, payload in assets.items():
                (folder / name).write_bytes(payload)
            (folder / "analysis.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            record_operation(self.workspace, action, inputs=inputs, parameters={**parameters, "sources": sources},
                             outputs=[folder / filename for filename in [*names, "analysis.json"]])
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        result = {**metadata, "folder": folder, "tables": tables}
        st.session_state[self.key(key + "_result")] = result
        return result

    def exports(self, key, result):
        folder = result["folder"]
        with st.expander("Saved data and parameters"):
            st.caption(str(folder))
            for filename in [*(f"{name}.csv" for name in result["tables"]), "analysis.json", *result["assets"]]:
                path = folder / filename
                if path.is_file():
                    mime = {".csv": "text/csv", ".json": "application/json"}.get(path.suffix, "application/octet-stream")
                    st.download_button(f"Download {filename}", path.read_bytes(), filename,
                                       mime=mime,
                                       key=self.key(f"{key}_download_{filename}"))


def _make_scalar(source):
    frame, spec = source
    return spatial.scalar_data(frame, **spec)


def _parse_pairs(text, label):
    rows = [line.split(",") for line in text.splitlines() if line.strip()]
    try:
        result = np.array(rows, dtype=float)
    except ValueError as exc:
        raise ValueError(f"{label} must contain two comma-separated numbers per line.") from exc
    if not len(result) or result.ndim != 2 or result.shape[1] != 2 or not np.isfinite(result).all():
        raise ValueError(f"{label} must contain two finite comma-separated numbers per line.")
    return result


def _plot(figure, page, key):
    figure.update_layout(margin={"l": 16, "r": 16, "t": 24, "b": 16})
    st.plotly_chart(figure, key=page.key("chart_" + key), config={"displaylogo": False}, width="stretch")


def _map_plot(rows, unit, value_unit, *, field="value", midpoint=None):
    figure = go.Figure()
    visible = rows[["x", "y", field]].notna().all(axis=1)
    if field == "value":
        visible &= ~rows.failed & ~rows.status_missing
    points = rows.loc[visible]
    figure.add_trace(go.Scattergl(
        x=points.x, y=points.y, mode="markers", name=field.replace("_", " ").title(),
        marker={"color": points[field], "colorscale": "RdBu_r" if midpoint is not None else "Viridis",
                "size": 8, "cmid": midpoint, "colorbar": {"title": _units(value_unit)}},
        customdata=points[field], hovertemplate="%{x}, %{y}<br>%{customdata:.6g}<extra></extra>",
    ))
    other = rows.loc[~visible & rows[["x", "y"]].notna().all(axis=1)]
    if len(other):
        figure.add_trace(go.Scattergl(x=other.x, y=other.y, mode="markers", name="Unavailable",
                                     marker={"symbol": "x", "color": "#929ca8", "size": 8}))
    figure.update_xaxes(title_text=f"X ({_units(unit)})")
    figure.update_yaxes(title_text=f"Y ({_units(unit)})", scaleanchor="x", scaleratio=1)
    return figure


def _table(frame):
    st.dataframe(frame, hide_index=True, width="stretch")


def _optical_image(payload):
    try:
        with Image.open(BytesIO(payload)) as image:
            image.load()
            return image.convert("RGB")
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError(f"Cannot preview the selected optical image. {error}") from error


def _registration(page):
    st.caption("Pair stage XY coordinates with image column and row positions in pixels. Pixel (0, 0) is at the upper left.")
    upload = st.file_uploader("Optical image", type=["png", "jpg", "jpeg", "tif", "tiff"],
                              key=page.key("registration_image"))
    image_name = upload.name if upload else None
    payload = upload.getvalue() if upload else None
    if payload:
        st.image(_optical_image(payload), caption=f"Preview · {image_name}", width=460)
    path = page.choose_path("Stage map for overlay (optional)", "registration_map")
    with st.expander("Stage map columns", expanded=bool(path)):
        source = page.scalar_form(path, "registration", "coordinates", require_value=False) if path else None
    columns = st.columns(2)
    with columns[0]:
        xy_text = page.text("Stage landmarks (X,Y)", "registration_xy", area=True, placeholder="0,0\n10,0\n0,10")
    with columns[1]:
        pixel_text = page.text("Image landmarks (column,row)", "registration_pixels", area=True,
                               placeholder="25,50\n125,50\n25,150")
    default_unit = source[1]["axis_unit"] if source else None
    stage_unit = page.select("Landmark coordinate unit", [None, *(unit for unit in spatial.DISTANCE_UNITS if unit != "px")],
                             "registration_unit", default=default_unit,
                             display=lambda item: "Choose a unit" if item is None else _units(item))
    if st.button("Fit optical registration", key=page.key("registration_run"), type="primary"):
        if stage_unit is None:
            raise ValueError("Choose the physical stage landmark unit.")
        xy = _parse_pairs(xy_text, "Stage landmarks")
        pixels = _parse_pairs(pixel_text, "Image landmarks")
        fwd, inverse, landmarks, rms = spatial.affine_registration(xy, pixels)
        tables = {"landmarks": landmarks}
        params = {"stage_unit": stage_unit, "pixel_unit": "px", "stage_to_pixel": fwd.tolist(),
                  "pixel_to_stage": inverse.tolist(), "rms_residual_px": rms, "image": image_name}
        if source:
            frame, spec = source
            if spec["value"] is None:
                rows = spatial.coordinate_rows(frame, axes=spec["axes"], axis_unit=spec["axis_unit"])
                coords = rows[["x", "y"]].to_numpy() * spatial._axis_scale(spec["axis_unit"], stage_unit, "coordinates")
            else:
                data = _make_scalar(source)
                rows, coords = data.rows, spatial._aligned_axes(data, stage_unit)
            mapped = spatial.apply_affine(coords, fwd)
            tables["registered_points"] = rows.assign(u_px=mapped[:, 0], v_px=mapped[:, 1])
            params["map"] = spec
        saved_image = {}
        if payload:
            params["image_file"] = "optical-image" + Path(image_name).suffix.lower()
            saved_image = {params["image_file"]: payload}
            params["image_sha256"] = sha256(payload).hexdigest()
        inputs = [item for item in (path, image_name) if item]
        page.save("registration", "Optical registration", inputs, params, tables, assets=saved_image)
    result = page.result("registration")
    if not result:
        return
    tables, params = result["tables"], result["parameters"]
    landmarks = tables["landmarks"]
    figure = go.Figure()
    if params.get("image_file") and (image_path := result["folder"] / params["image_file"]).is_file():
        figure.add_trace(go.Image(z=np.asarray(_optical_image(image_path.read_bytes()))))
    if "registered_points" in tables:
        points = tables["registered_points"].dropna(subset=["u_px", "v_px"])
        marker = {"size": 8, "color": "#ffe179"}
        if "value" in points:
            ok = points.value.notna() & ~points.failed & ~points.status_missing
            marker.update(color=points.value[ok], colorscale="Viridis", colorbar={"title": params["map"]["value_unit"]})
            if (~ok).any():
                figure.add_trace(go.Scattergl(x=points.u_px[~ok], y=points.v_px[~ok], mode="markers",
                                             name="Unavailable stage points",
                                             marker={"symbol": "x", "color": "#929ca8"}))
            points = points.loc[ok]
        figure.add_trace(go.Scattergl(x=points.u_px, y=points.v_px, mode="markers",
                                     name="Registered stage points", marker=marker))
    for x, y, name, symbol in (("u_px", "v_px", "Picked landmarks", "circle-open"),
                               ("fit_u_px", "fit_v_px", "Fitted landmarks", "cross")):
        figure.add_trace(go.Scatter(x=landmarks[x], y=landmarks[y], mode="markers",
                                    marker={"symbol": symbol, "size": 12}, name=name))
    figure.update_xaxes(title_text="Image column (px)")
    figure.update_yaxes(title_text="Image row (px)", autorange="reversed", scaleanchor="x", scaleratio=1)
    st.metric("RMS landmark residual (px)", f"{params['rms_residual_px']:.6g}")
    _plot(figure, page, "registration_plot")
    _table(landmarks)
    with st.expander("Transform coefficients"):
        st.write(f"Stage ({_units(params['stage_unit'])}) → pixel (px)")
        _table(pd.DataFrame(params["stage_to_pixel"], columns=["X coefficient", "Y coefficient", "Intercept"],
                            index=["Image column", "Image row"]).reset_index(names="Output"))
        st.write(f"Pixel (px) → stage ({_units(params['stage_unit'])})")
        _table(pd.DataFrame(params["pixel_to_stage"], columns=["Column coefficient", "Row coefficient", "Intercept"],
                            index=["Stage X", "Stage Y"]).reset_index(names="Output"))
    page.exports("registration", result)


def _roi_and_line(page):
    mode = page.select("Geometry", ("Rectangle", "Polygon", "Line profile"), "roi_geometry")
    path = page.choose_path("Spatial map", "roi_map")
    if path is None:
        return
    source = page.scalar_form(path, "roi", "coordinates")
    prompt = {"Rectangle": "Opposite corners (X,Y)", "Polygon": "Ordered polygon vertices (X,Y)",
              "Line profile": "Line endpoints (X,Y)"}
    example = "0,0\n10,0\n10,5\n0,5" if mode == "Polygon" else "0,0\n10,5"
    selected_unit = source[1]["axis_unit"]
    shape_scope = _digest((path, selected_unit))
    geometry = page.text(prompt[mode], f"roi_vertices_{mode}_{shape_scope}", area=True, placeholder=example)
    unit = _units(selected_unit or "map coordinate unit")
    if selected_unit:
        st.caption(f"Enter coordinates in {unit}.")
    key = "line" if mode == "Line profile" else "roi"
    if mode == "Line profile":
        method = page.select("Sampling", ("Nearest", "Linear"), "line_method")
        count = page.number("Profile samples", "line_count", value=101, min_value=2, max_value=10000)
        tolerance = page.number(f"Maximum distance to a measured sample ({unit})",
                                "line_tolerance_" + shape_scope, min_value=0.0, format="%.8g")
    button = "Sample line profile" if mode == "Line profile" else "Calculate ROI statistics"
    if st.button(button, key=page.key(key + "_run"), type="primary"):
        data = _make_scalar(source)
        vertices = _parse_pairs(geometry, prompt[mode])
        parameters = {"map": source[1], "geometry": mode, "vertices": vertices.tolist()}
        if mode == "Line profile":
            profile = spatial.line_profile(data, vertices, count=count, tolerance=tolerance, method=method)
            parameters.update(method=method, samples=count, tolerance=tolerance, tolerance_unit=data.axis_unit)
            tables = {"line_profile": profile, "map_points": data.rows}
        else:
            outline, rows, stats = spatial.roi_statistics(data, vertices, shape=mode)
            parameters["outline"] = outline.tolist()
            tables = {"roi_statistics": pd.DataFrame([stats]), "roi_membership": rows}
        page.save(key, button, [path], parameters, tables)
    result = page.result(key)
    if not result:
        return
    tables, params = result["tables"], result["parameters"]
    unit, value_unit = params["map"]["axis_unit"], params["map"]["value_unit"]
    if mode == "Line profile":
        profile = tables["line_profile"]
        figure = go.Figure(go.Scatter(x=profile.distance, y=profile.value, mode="lines+markers", connectgaps=False))
        figure.update_xaxes(title_text=f"Distance from start ({_units(unit)})")
        figure.update_yaxes(title_text=f"Scalar value ({value_unit})")
        _plot(figure, page, "line_profile_curve")
        _table(profile)
        outline = np.array(params["vertices"])
        rows = tables["map_points"]
    elif params["geometry"] == mode:
        _table(tables["roi_statistics"])
        rows = tables["roi_membership"]
        outline = np.array([*params["outline"], params["outline"][0]])
        _table(rows.loc[rows.in_roi])
    else:
        return
    figure = _map_plot(rows, unit, value_unit)
    figure.add_trace(go.Scatter(x=outline[:, 0], y=outline[:, 1], mode="lines+markers",
                                name=params["geometry"], line={"color": "#e74b43", "width": 2}))
    _plot(figure, page, key + "_map")
    page.exports(key, result)


def _difference(page):
    first = page.choose_path("Before map", "difference_before")
    second = page.choose_path("After map", "difference_after", exclude={first})
    if first is None or second is None:
        return
    with st.expander("Before columns and units", expanded=True):
        before = page.scalar_form(first, "difference_before", "coordinates")
    with st.expander("After columns and units", expanded=True):
        after = page.scalar_form(second, "difference_after", "coordinates")
    method = page.select("Difference method", ("Nearest", "Linear"), "difference_method",
                         display={"Nearest": "One-to-one measured pairs", "Linear": "Linear interpolation"}.get)
    unit = _units(before[1]["axis_unit"] or "before coordinate unit")
    tolerance = page.number(f"Maximum distance ({unit})", "difference_tolerance_" + str(before[1]["axis_unit"]),
                            min_value=0.0, format="%.8g")
    st.caption("Measured pairs use each sample once. Linear interpolation leaves gaps outside the after map and beyond the distance limit.")
    if st.button("Calculate difference map", key=page.key("difference_run"), type="primary"):
        rows, counts = spatial.difference_map(_make_scalar(before), _make_scalar(after), tolerance=tolerance, method=method)
        page.save("difference", "Before/after difference map", [first, second],
                  {"before": before[1], "after": after[1], "tolerance": tolerance,
                   "tolerance_unit": before[1]["axis_unit"], "method": method},
                  {"difference_map": rows, "match_counts": pd.DataFrame([counts])})
    result = page.result("difference")
    if result:
        params, tables = result["parameters"], result["tables"]
        rows = tables["difference_map"]
        _table(tables["match_counts"])
        _plot(_map_plot(rows, params["before"]["axis_unit"], params["before"]["value_unit"],
                        field="difference", midpoint=0), page, "difference_plot")
        _table(rows)
        page.exports("difference", result)


def _quality(page):
    path = page.choose_path("Spatial map", "quality_map")
    if path is None:
        return
    source = page.scalar_form(path, "quality", "coordinates")
    threshold = page.number("Robust outlier threshold", "quality_threshold", value=3.5, min_value=0.000001)
    st.caption("Outliers use median absolute deviation among usable values. If the deviation is zero, values that differ from the median are flagged.")
    if st.button("Flag map quality", key=page.key("quality_run"), type="primary"):
        rows = spatial.quality_map(_make_scalar(source), threshold=threshold)
        counts = {"rows": len(rows), **{key: int(rows[key].sum()) for key in ("missing", "failed", "outlier")},
                  "scored": int(rows.robust_z.notna().sum()),
                  "unplottable_coordinates": int(rows[["x", "y"]].isna().any(axis=1).sum())}
        page.save("quality", "Spatial map quality", [path], {"map": source[1], "threshold": threshold},
                  {"quality_flags": rows, "usable_points": rows.loc[rows.quality.eq("usable")],
                   "quality_counts": pd.DataFrame([counts])})
    result = page.result("quality")
    if result:
        rows = result["tables"]["quality_flags"]
        unit = _units(result["parameters"]["map"]["axis_unit"])
        _table(result["tables"]["quality_counts"])
        figure = go.Figure()
        styles = {"usable": ("circle", "#365d79"), "missing": ("diamond-open", "#d18a22"),
                  "failed": ("x", "#c53e44"), "outlier": ("triangle-up", "#763f94")}
        for flag, (symbol, color) in styles.items():
            points = rows.loc[rows.quality.eq(flag)].dropna(subset=["x", "y"])
            figure.add_trace(go.Scattergl(x=points.x, y=points.y, mode="markers", name=flag.title(),
                                         marker={"size": 9, "symbol": symbol, "color": color},
                                         customdata=points.row,
                                         hovertemplate="Row %{customdata}<br>%{x}, %{y}<extra></extra>"))
        figure.update_xaxes(title_text=f"X ({unit})")
        figure.update_yaxes(title_text=f"Y ({unit})", scaleanchor="x", scaleratio=1)
        _plot(figure, page, "quality_plot")
        _table(rows)
        page.exports("quality", result)


def _signal_label(path, modality, index):
    return f"{index + 1}. {modality} · {Path(path).name}"


def _signal_forms(page, paths, key, mode, *, offset_unit=None):
    forms, specs, offsets = {}, {}, {}
    for index, path in enumerate(paths):
        name = f"{key}_{_digest(path)}"
        with st.expander(page.path_label(path), expanded=True):
            record = page.data.records.get(page.paths[path], {})
            input_type = str(record.get("instrument") or "").casefold()
            default = next((modality for modality, terms in (
                ("Raman", ("raman",)), ("Hyperspectral imaging (HSI)", ("hyperspect", "hsi")),
                ("Thermal", ("thermal", "thermograph")), ("mMZI", ("mmzi",)),
                ("Electrochemistry", ("heka", "biologic", "gamry", "versastat", "electrochem", "palm",
                                       "metrohm", "vmp", "autolab", "corrtest", "ivium")),
            ) if any(term in input_type for term in terms)), None)
            modality = page.select("Modality", [None, *spatial.MODALITIES], name + "_modality",
                                   default=default, display=lambda item: item or "Choose a modality")
            label = _signal_label(path, modality or "Unspecified modality", index)
            scalar = page.scalar_form(path, key, mode)
            forms[label] = scalar
            specs[label] = {"path": path, "modality": modality, "columns": scalar[1]}
            if offset_unit is not None:
                offsets[label] = page.number(f"Time shift added to this signal ({_units(offset_unit)})",
                                             name + "_offset_" + offset_unit, format="%.8g")
    return forms, specs, offsets


def _selected_signals(forms, specs):
    if any(not spec["modality"] for spec in specs.values()):
        raise ValueError("Choose a modality for every selected signal.")
    return {label: _make_scalar(form) for label, form in forms.items()}


def _correlation(page):
    st.caption("Select scalar columns from numeric exports. Use a shared coordinate frame or set explicit time shifts.")
    paths = page.choose_paths("Scalar exports", "correlation_paths")
    if len(paths) < 2:
        st.info("Choose at least two tables to compare.")
        return
    mode = page.select("Match by", ("time", "coordinates"), "correlation_mode",
                       display={"time": "Time", "coordinates": "XY coordinates"}.get)
    units = spatial.TIME_UNITS if mode == "time" else spatial.DISTANCE_UNITS
    common = page.select("Matching unit", list(units), "correlation_unit_" + mode, display=_units)
    tolerance = page.number(f"Maximum match distance ({_units(common)})", "correlation_tolerance_" + common,
                            min_value=0.0, format="%.8g")
    forms, specs, offsets = _signal_forms(page, paths, "correlation", mode,
                                         offset_unit=common if mode == "time" else None)
    st.caption("Each pair uses one-to-one matches within tolerance. Counts include excluded rows and identify invalid rows separately.")
    if st.button("Compare scalar exports", key=page.key("correlation_run"), type="primary"):
        signals = _selected_signals(forms, specs)
        summary, pairs = spatial.correlate_scalars(signals, mode=mode, tolerance=tolerance, unit=common,
                                                    offsets=offsets)
        tables = {"correlations": summary, **{f"pair_{i:02d}": frame
                                              for i, frame in enumerate(pairs.values(), start=1)}}
        page.save("correlation", "Multimodal scalar correlation", paths,
                  {"signals": specs, "mode": mode, "matching_unit": common,
                   "tolerance": tolerance, "time_shifts": offsets}, tables)
    result = page.result("correlation")
    if result:
        summary = result["tables"]["correlations"]
        _table(summary)
        pair_names = {f"pair_{i:02d}": f"{row.left} ↔ {row.right}"
                      for i, row in enumerate(summary.itertuples(index=False), start=1)}
        pair = page.select("Inspect matched pair", pair_names, "correlation_pair", display=pair_names.get)
        points = result["tables"][pair]
        row = summary.iloc[int(pair.split("_")[-1]) - 1]
        figure = go.Figure(go.Scattergl(x=points.left_value, y=points.right_value, mode="markers",
                                        marker={"size": 8}, customdata=points.match_distance,
                                        hovertemplate="x=%{x}<br>y=%{y}<br>match distance=%{customdata:.6g}<extra></extra>"))
        figure.update_xaxes(title_text=f"{row.left} ({row.left_unit})")
        figure.update_yaxes(title_text=f"{row.right} ({row.right_unit})")
        _plot(figure, page, "correlation_plot")
        _table(points)
        page.exports("correlation", result)


def _event_source(page):
    origin = page.select("Event input", ("Upload event CSV", "Project table"), "event_origin")
    if origin == "Upload event CSV":
        upload = st.file_uploader("Named stimulus event CSV", type=["csv"], key=page.key("event_file"))
        if upload is None:
            st.caption("Use a name column and a numeric time column. A stimulus label can be Magnet on, Light off, or another recorded action.")
            return
        payload = upload.getvalue()
        frame, path = pd.read_csv(BytesIO(payload)), upload.name
        provenance = {"filename": path, "sha256": sha256(payload).hexdigest()}
        assets = {"event-source.csv": payload}
    else:
        path = page.choose_path("Event table", "event_table")
        if path is None:
            return
        frame = page.read(path)
        provenance = {"path": path}
        assets = {}
    columns = list(frame.columns)
    if len(columns) < 2:
        raise ValueError("An event table needs a name column and a time column.")
    key = "event_source_" + _digest(path)
    default_name = next((col for col in columns if col.strip().casefold() in {"event", "stimulus", "name"}), columns[0])
    name = page.column("Event name column", columns, key + "_name", default_name)
    time_columns = [column for column in columns if column != name]
    default_time = next((col for col in time_columns if col.strip().lower().startswith(("time", "timestamp"))),
                        time_columns[0])
    time = page.column("Event time column", time_columns, key + "_time", default_time)
    declared = spatial.header_unit(time, spatial.TIME_UNITS)
    unit = page.select("Event time unit", [None, *spatial.TIME_UNITS], key + "_unit_" + _digest(time),
                       default=declared, display=lambda item: _units(item) if item else "Choose a unit")
    offset = page.number("Time shift added to event clock (s)", "event_clock_shift", format="%.8g")
    _table(frame[[name, time]].head(10))
    return frame, path, {"name": name, "time": time, "unit": unit, "offset_s": offset}, provenance, assets


def _timeline_figure(timeline, events):
    names = timeline.signal.unique()
    figure = make_subplots(rows=len(names) + 1, cols=1, shared_xaxes=True, vertical_spacing=min(0.08, 0.6 / len(names)))
    figure.add_trace(go.Scattergl(x=events.event_time_s, y=np.zeros(len(events)), customdata=events.event,
                                  name="Stimulus events", mode="markers", marker={"symbol": "diamond", "size": 9},
                                  hovertemplate="%{customdata}<br>%{x:.6g} s<extra></extra>"), row=1, col=1)
    figure.update_yaxes(title_text="Stimulus", showticklabels=False, row=1, col=1)
    for index, label in enumerate(names, start=2):
        trace = timeline.loc[timeline.signal.eq(label)]
        figure.add_trace(go.Scattergl(x=trace.time_s, y=trace.value.where(trace.valid), mode="lines+markers",
                                      marker={"size": 3}, connectgaps=False, name=label), row=index, col=1)
        figure.update_yaxes(title_text=trace.unit.iloc[0], row=index, col=1)
    for time in events.event_time_s.unique()[:50]:
        figure.add_vline(x=time, line_dash="dot", line_color="#9ca5ac", line_width=1)
    figure.update_xaxes(title_text="Shared clock (s)", row=len(names) + 1, col=1)
    figure.update_layout(height=min(1250, 130 + 175 * len(names)))
    return figure


def _events(page):
    events = _event_source(page)
    if events is None:
        return
    frame, event_path, event_parameters, provenance, assets = events
    paths = page.choose_paths("Signal exports", "event_signals")
    if not paths:
        st.info("Choose one or more time series.")
        return
    forms, specs, offsets = _signal_forms(page, paths, "event", "time", offset_unit="s")
    before = page.number("Seconds before each event", "event_before", value=5.0, min_value=0.0)
    after = page.number("Seconds after each event", "event_after", value=10.0, min_value=0.0)
    baseline = page.number("Baseline duration before each event (s)", "event_baseline", value=5.0, min_value=0.000001)
    st.caption("The baseline is the mean of valid pre-event samples. Relative change is 100 × (value − baseline) / baseline.")
    if st.button("Align stimulus events", key=page.key("event_run"), type="primary"):
        stimuli = spatial.event_table(frame, **event_parameters)
        signals = _selected_signals(forms, specs)
        timeline, aligned, baselines = spatial.align_events(signals, stimuli, before_s=before, after_s=after,
                                                             baseline_s=baseline, offsets_s=offsets)
        params = {"event_input": provenance, "event_columns": event_parameters, "signals": specs,
                  "time_shifts_s": offsets, "before_s": before, "after_s": after, "baseline_s": baseline}
        page.save("event", "Stimulus-event alignment", [event_path, *paths], params,
                  {"events": stimuli, "shared_timeline": timeline, "event_aligned": aligned, "baselines": baselines},
                  assets=assets)
    result = page.result("event")
    if result:
        tables, params = result["tables"], result["parameters"]
        stimuli, timeline, aligned = tables["events"], tables["shared_timeline"], tables["event_aligned"]
        st.subheader("Shared timeline")
        if len(timeline):
            _plot(_timeline_figure(timeline, stimuli), page, "event_timeline_plot")
        else:
            st.info("The selected signals have no finite times.")
        _table(stimuli)
        st.subheader("Event-aligned traces")
        signal = page.select("Signal to inspect", list(params["signals"]), "event_plot_signal")
        metric_names = {"value": "Scalar value", "delta": "Change from baseline",
                        "relative_change_pct": "Relative change (%)"}
        metric = page.select("Aligned signal view", metric_names, "event_plot_metric", display=metric_names.get)
        window = aligned.loc[aligned.signal.eq(signal)]
        figure = go.Figure()
        for event, samples in window.groupby("event_id", sort=False):
            values = samples[metric].where(samples.valid)
            figure.add_trace(go.Scattergl(x=samples.relative_time_s, y=values, mode="lines+markers",
                                          connectgaps=False, name=f"{samples.event.iloc[0]} · #{event}"))
        figure.add_vline(x=0, line_dash="dot")
        figure.update_xaxes(title_text="Time from event (s)")
        signal_unit = params["signals"][signal]["columns"]["value_unit"]
        figure.update_yaxes(title_text=metric_names[metric] if metric.endswith("pct")
                            else f"{metric_names[metric]} ({signal_unit})")
        _plot(figure, page, "event_aligned_plot")
        _table(tables["baselines"])
        _table(window)
        page.exports("event", result)


def render(data, *, workspace: Path):
    """Render the spatial workbench using only the current project's tables."""
    page = _Page(data, workspace)
    tool = page.select("Spatial and event tool", _TOOLS, "tool")
    index = _TOOLS.index(tool)
    if index and not page.paths:
        st.info("Open numeric table exports to use this tool.")
        return
    try:
        (_registration, _roi_and_line, _difference, _quality, _correlation, _events)[index](page)
    except (ValueError, OSError) as error:
        st.error(str(error))
