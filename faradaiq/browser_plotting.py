"""Interactive browser plots and exports of their exact displayed trace values."""
from __future__ import annotations

from io import BytesIO
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pandas as pd

from .release_identity import APP_VERSION


def negative_impedance_label(label):
    text = str(label or "").strip().casefold().replace("−", "-")
    return bool(re.match(r"^(?:-|neg(?:ative)?(?:[\s_]|zimag)|minus(?:[\s_]|zimag))", text))


def _style(figure, *, title="", height=480, theme="Light", legend=True):
    figure.update_layout(
        template="plotly_dark" if theme == "Dark" else "plotly_white",
        title=title,
        height=height,
        margin={"l": 45, "r": 24, "t": 48 if title else 20, "b": 36},
        showlegend=legend,
        font={"family": "Arial, sans-serif", "size": 13},
        colorway=["#236a9c", "#c64d38", "#17826d", "#8055a0", "#ba811d", "#33858c"],
        legend={"x": 1.01, "y": 1, "font": {"size": 11}, "itemsizing": "constant"},
        hoverlabel={"namelength": -1},
    )


def _fit_axis(figure, values, axis, *, logarithmic=False):
    """Fit the real numeric span, including nano/pico-scale and constant signals."""
    parts = [np.asarray(part, dtype=float) for part in values]
    finite = np.concatenate(parts) if parts else np.array([])
    finite = finite[np.isfinite(finite) & ((finite > 0) if logarithmic else True)]
    if not len(finite):
        return
    if logarithmic:
        finite = np.log10(finite)
    lo, hi = float(finite.min()), float(finite.max())
    padding = (hi - lo) * .065
    if padding == 0:
        padding = .05 if logarithmic else abs(lo) * .05 or 1.
    getattr(figure, 'update_' + axis + 'axes')(range=[lo - padding, hi + padding], autorange=False,
                                             exponentformat='SI', showexponent='all', automargin=True)


def build_xy_figure(series, x, y, *, invert=False, log_x=False, log_y=False,
                    title="", x_title="", y_title="", height=480, theme="Light", legend=True,
                    trace_style="Lines and points", names=None):
    import plotly.graph_objects as go

    figure = go.Figure()
    mode = {"Lines and points": "lines+markers", "Lines": "lines", "Points": "markers"}[trace_style]
    for path, frame in series:
        if x not in frame or y not in frame:
            continue
        x_values = pd.to_numeric(frame[x], errors="coerce").to_numpy(dtype=float)
        y_values = pd.to_numeric(frame[y], errors="coerce").to_numpy(dtype=float)
        figure.add_scatter(x=x_values, y=-y_values if invert else y_values,
                           name=(names or {}).get(path, Path(path).stem), mode=mode, marker={"size": 4},
                           line={"width": 2}, hovertemplate="%{x:.7g}<br>%{y:.7g}<extra>%{fullData.name}</extra>")
    _style(figure, title=title, height=height, theme=theme, legend=legend)
    figure.update_xaxes(title=x_title or str(x), type="log" if log_x else "linear")
    figure.update_yaxes(title=y_title or (f"−{y}" if invert else str(y)), type="log" if log_y else "linear")
    # Plotly's default tiny/constant-signal padding can otherwise obscure real
    # electrochemical signals with a +/-1 axis. Values and exports stay exact.
    paired = []
    for trace in figure.data:
        xv, yv = np.asarray(trace.x,dtype=float), np.asarray(trace.y,dtype=float)
        mask = np.isfinite(xv) & np.isfinite(yv)
        if log_x: mask &= xv > 0
        if log_y: mask &= yv > 0
        paired.append((xv[mask],yv[mask]))
    _fit_axis(figure, [xv for xv,_ in paired], 'x', logarithmic=log_x)
    _fit_axis(figure, [yv for _,yv in paired], 'y', logarithmic=log_y)
    return figure


def build_impedance_figure(series, *, representation, title="", height=600, theme="Light", legend=True, names=None):
    """Normalize supported spectra with the shared EIS reader before plotting."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    from .browser_analysis import eis_table
    from .browser_analysis import measurement_roles

    if representation not in {"Bode", "Nyquist"}:
        raise ValueError("Choose a Bode or Nyquist impedance plot.")
    bode = representation == "Bode"
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1) if bode else go.Figure()
    colors = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9")
    for index, (path, frame) in enumerate(series):
        roles = measurement_roles(frame)
        if any(roles.get(role) is None for role in ("frequency", "zreal", "zimag")):
            continue
        spectrum = eis_table(frame, negate_imaginary=negative_impedance_label(roles["zimag"]))
        if bode:
            spectrum = spectrum.sort_values("frequency_Hz", kind="stable")
        frequency = spectrum["frequency_Hz"].to_numpy()
        real = spectrum["Zreal_ohm"].to_numpy()
        imaginary = spectrum["Zimag_ohm"].to_numpy()
        name = (names or {}).get(path, Path(path).stem)
        trace = dict(name=name, legendgroup=str(index), mode="lines+markers", marker={"size": 4},
                     line={"color": colors[index % len(colors)]})
        if bode:
            figure.add_scatter(x=frequency, y=np.hypot(real, imaginary), **trace, row=1, col=1)
            phase = np.rad2deg(np.arctan2(imaginary, real))
            figure.add_scatter(x=frequency, y=phase, **trace, showlegend=False, row=2, col=1)
        else:
            figure.add_scatter(x=real, y=-imaginary, **trace, customdata=frequency,
                               hovertemplate="Z′=%{x} Ω<br>−Z″=%{y} Ω<br>f=%{customdata} Hz")
    _style(figure, title=title, height=height, theme=theme, legend=legend)
    if bode:
        figure.update_xaxes(title="Frequency (Hz)", type="log", row=2, col=1)
        figure.update_xaxes(type="log", row=1, col=1)
        figure.update_yaxes(title="|Z| (Ω)", type="log", row=1, col=1)
        figure.update_yaxes(title="Phase (°)", row=2, col=1)
    else:
        figure.update_xaxes(title="Z′ (Ω)")
        figure.update_yaxes(title="−Z″ (Ω)", scaleanchor="x", scaleratio=1)
    return figure


def _trace_values(figure):
    for index, trace in enumerate(figure.data, start=1):
        x_ref = getattr(trace, "xaxis", None) or "x"
        y_ref = getattr(trace, "yaxis", None) or "y"
        x_label = getattr(figure.layout[f"xaxis{x_ref[1:]}"].title, "text", None) or "Frequency (Hz)"
        y_label = getattr(figure.layout[f"yaxis{y_ref[1:]}"].title, "text", None) or ""
        panel = int(y_ref[1:] or 1)
        values = np.column_stack((np.asarray(trace.x, dtype=float), np.asarray(trace.y, dtype=float)))
        metadata = trace.meta if isinstance(trace.meta, dict) else {}
        identity = metadata.get('measurement') or trace.name or f"Trace {index}"
        yield index, str(identity), panel, x_label, y_label, values


def displayed_plot_csv(figure):
    tables = [pd.DataFrame(dict(trace=index, measurement=name, panel=panel, x_axis=x_label,
                                y_axis=y_label, x=values[:, 0], y=values[:, 1]))
              for index, name, panel, x_label, y_label, values in _trace_values(figure)]
    if not tables:
        raise ValueError("There are no displayed traces to export.")
    return pd.concat(tables, ignore_index=True).to_csv(index=False).encode("utf-8")


def displayed_plot_hdf5(figure):
    import h5py

    output = BytesIO()
    with h5py.File(output, "w") as bundle:
        bundle.attrs["schema"] = "faradaiq-displayed-plot-exchange-1"
        bundle.attrs["application_version"] = APP_VERSION
        bundle.attrs["title"] = figure.layout.title.text or ""
        curves = bundle.create_group("curves")
        for index, name, panel, x_label, y_label, values in _trace_values(figure):
            entry = curves.create_dataset(f"trace_{index:03d}", data=values, compression="gzip")
            entry.attrs["measurement"] = name
            entry.attrs["panel"] = panel
            entry.attrs["columns"] = json.dumps([x_label, y_label], ensure_ascii=False)
    return output.getvalue()


def native_view_records(data, selected):
    """Map browser catalog entries to the record geometry expected by native views."""
    from .v290_portable_browser import _technique_key

    def record(path):
        values = dict(data.records.get(path, {}))
        technique = _technique_key(values.get("technique_key") or values.get("technique") or path.stem)
        values.update(path=str(path), technique_key=technique or "OTHER")
        if "point" not in values and values.get("point_index") is not None:
            values["point"] = values["point_index"]
        return values

    chosen = [record(path) for path in selected]
    positions = []
    for path in data.tables:
        item = record(path)
        if item["technique_key"] == "POSITION":
            positions.append(item)
    return chosen, positions


def write_native_view_archive(entry_path):
    """Archive one generated view folder and its relative assets."""
    entry = Path(entry_path).resolve(strict=True)
    folder = entry.parent
    dest = folder.parent / f"{entry.stem}_{APP_VERSION}.zip"
    with NamedTemporaryFile(dir=dest.parent, suffix=".zip", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob("*")):
                if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(folder):
                    archive.write(path, path.relative_to(folder).as_posix())
        temporary.replace(dest)
    finally:
        temporary.unlink(missing_ok=True)
    return dest


def result_artifacts(value):
    """Collect files explicitly returned by desktop analysis adapters."""
    files = []
    def collect(item, key=''):
        if isinstance(item, dict):
            for field, child in item.items():
                collect(child, field)
        elif is_dataclass(item):
            collect(asdict(item))
        elif isinstance(item, (list, tuple)):
            for child in item:
                collect(child, key)
        elif isinstance(item, (str, Path)) and key not in {'source', 'source_path', 'source_filename'}:
            path = Path(item)
            if path.is_absolute() and path.suffix.lower() in {'.csv', '.json', '.png', '.html', '.deareis', '.zip', '.txt'}:
                try:
                    if path.is_file() and path not in files:
                        files.append(path)
                except OSError:
                    pass
    collect(value)
    return files
