"""FaradaIQ Studio v273 multidimensional visualization backends.

Matplotlib remains the native publication backend. This module adds lazy,
optional interactive Altair 2D, Plotly 3D, and Plotly hyperspectral/stack
views without changing the canonical parser or browser catalog.
"""
from __future__ import annotations

import json
import math
import os
import html
import re
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .numpy_compat import trapezoid
import pandas as pd

VERSION = "v273"
_TABLE_EXTS = {".csv", ".tsv", ".txt", ".asc", ".xlsx", ".xls"}


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default




def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None and str(value) != "":
            return value
    return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def _numeric_header(columns: Sequence[Any]) -> bool:
    if not columns:
        return False
    ok = 0
    for c in columns:
        try:
            float(str(c).strip())
            ok += 1
        except Exception:
            pass
    return ok == len(columns)


def _read_table(path: Path, record: Optional[Mapping[str, Any]] = None, max_rows: int = 250000) -> pd.DataFrame:
    path = Path(path)
    record = record or {}
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path, nrows=max_rows)
    else:
        attempts = [
            dict(sep=None, engine="python", comment="#", nrows=max_rows),
            dict(sep=",", comment="#", nrows=max_rows),
            dict(sep="\t", comment="#", nrows=max_rows),
            dict(sep=r"\s+", engine="python", comment="#", nrows=max_rows),
        ]
        last = None
        for kwargs in attempts:
            try:
                df = pd.read_csv(path, **kwargs)
                if df.shape[1] >= 2:
                    break
            except Exception as exc:
                last = exc
        else:
            raise last or ValueError(f"Could not read table: {path}")
    if _numeric_header(list(df.columns)):
        # Legacy HEKA/pySECCM exports can omit headers. Recover the first row.
        try:
            df = pd.read_csv(path, sep=None, engine="python", comment="#", header=None, nrows=max_rows)
        except Exception:
            pass
    df = df.copy()
    tech = str(record.get("technique_key") or "").upper()
    if all(isinstance(c, (int, np.integer)) for c in df.columns):
        if df.shape[1] == 2:
            if tech in {"CA", "CP", "OCP", "TIME_SCAN", "POSITION"}:
                df.columns = ["time_s", "current_A" if tech == "CA" else "signal"]
            elif tech in {"CV", "LSV", "FSCV", "CV/LSV", "IV/TAFEL"}:
                df.columns = ["potential_V", "current_A"]
            else:
                df.columns = ["x", "signal"]
        else:
            df.columns = [f"col_{i+1}" for i in range(df.shape[1])]
    # Normalize duplicate/blank labels while preserving user-facing text elsewhere.
    seen: Dict[str, int] = {}
    cols = []
    for i, c in enumerate(df.columns):
        base = str(c).strip() or f"col_{i+1}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        cols.append(base if count == 0 else f"{base}_{count+1}")
    df.columns = cols
    return df


def _column_roles(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    cols = list(df.columns)
    nn = {_norm(c): c for c in cols}

    def choose(*tokens: str) -> Optional[str]:
        nt = [_norm(t) for t in tokens]
        for t in nt:
            if t in nn:
                return nn[t]
        for c in cols:
            n = _norm(c)
            for t in nt:
                if len(t) >= 3 and (n.startswith(t) or t in n):
                    return c
        return None

    roles = {
        "time": choose("time_s", "time", "t_s", "seconds"),
        "potential": choose("potential_v", "potential", "voltage", "ewe", "emon", "e_v"),
        "current": choose("current_a", "current", "imon", "i_a", "amps", "ampere"),
        "charge": choose("charge_c", "charge", "q_c"),
        "frequency": choose("frequency_hz", "frequency", "freq_hz", "freq"),
        "wavelength": choose("wavelength_nm", "wavelength", "raman_shift", "wavenumber"),
        "tau": choose("tau_s", "tau", "timescale"),
        "gamma": choose("gamma_ohm", "gamma", "drt"),
        "zreal": choose("zreal_ohm", "zreal", "real_z", "rez"),
        "zimag": choose("zimag_ohm", "zimag", "imag_z", "imz", "neg_zimag"),
        "phase": choose("phase_deg", "phase"),
        "x": choose("x_m", "x_um", "x_mm", "x_position", "x_coordinate", "xcoord"),
        "y": choose("y_m", "y_um", "y_mm", "y_position", "y_coordinate", "ycoord"),
        "z": choose("z_m", "z_um", "z_mm", "z_position", "z_coordinate", "distance", "height"),
        "point": choose("point_index", "point", "position_index", "position"),
        "line": choose("line_index", "line"),
        "band": choose("band", "band_index", "channel_index"),
        "signal": choose("signal", "value", "intensity", "response"),
    }
    return roles


def _numeric(df: pd.DataFrame, col: Optional[str]) -> np.ndarray:
    if not col or col not in df:
        return np.array([], dtype=float)
    return pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)


def _finite_xy(x: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    mask = np.isfinite(x) & np.isfinite(y)
    return x[mask], y[mask]


def _downsample(x: np.ndarray, y: np.ndarray, max_n: int = 1000) -> Tuple[np.ndarray, np.ndarray]:
    x, y = _finite_xy(x, y)
    if len(x) <= max_n:
        return x, y
    idx = np.linspace(0, len(x) - 1, max_n).astype(int)
    return x[idx], y[idx]


def _preferred_axes(record: Mapping[str, Any], roles: Mapping[str, Optional[str]]) -> Tuple[Optional[str], Optional[str]]:
    tech = str(record.get("technique_key") or "").upper()
    if tech in {"CV", "LSV", "FSCV", "CV/LSV", "IV/TAFEL"}:
        return roles.get("potential") or roles.get("time"), roles.get("current") or roles.get("signal")
    if tech == "EIS":
        return roles.get("frequency"), roles.get("zreal") or roles.get("phase")
    if tech == "DRT":
        return roles.get("tau"), roles.get("gamma")
    if tech == "CA":
        return roles.get("time"), roles.get("current") or roles.get("signal")
    if tech in {"CP", "OCP"}:
        return roles.get("time"), roles.get("potential") or roles.get("signal")
    if tech in {"APPROACH", "DISTANCE_CAL", "Z_SCAN"}:
        return roles.get("z") or roles.get("time"), roles.get("current") or roles.get("potential") or roles.get("signal")
    return roles.get("time") or roles.get("potential") or roles.get("frequency") or roles.get("tau") or roles.get("x"), roles.get("current") or roles.get("potential") or roles.get("gamma") or roles.get("zreal") or roles.get("signal")


@dataclass
class Curve:
    path: Path
    record: Dict[str, Any]
    x_name: str
    y_name: str
    x: np.ndarray
    y: np.ndarray
    point: int
    method: int
    cycle: int
    line: int
    x_coord: Optional[float] = None
    y_coord: Optional[float] = None
    z_coord: Optional[float] = None


def _selected_records(browser) -> List[Dict[str, Any]]:
    # Reuse the unified browser's descendant selection contract.
    records: List[Dict[str, Any]] = []
    seen = set()
    try:
        from . import browser_ui_v209 as ui
        for iid in browser.tree.selection():
            for rec in ui._tree_records(browser, iid):
                p = Path(str(rec.get("path") or ""))
                if p.suffix.lower() not in _TABLE_EXTS or not p.exists():
                    continue
                key = str(p.resolve()).lower()
                if key not in seen:
                    seen.add(key); records.append(dict(rec))
    except Exception:
        pass
    return records


def _position_lookup(browser) -> Dict[int, Tuple[Optional[float], Optional[float], Optional[float]]]:
    out: Dict[int, Tuple[Optional[float], Optional[float], Optional[float]]] = {}
    for rec in list(getattr(browser, "_catalog_records", []) or []):
        if str(rec.get("technique_key") or "").upper() != "POSITION":
            continue
        pt = _safe_int(rec.get("point"), 0)
        if pt <= 0:
            continue
        x = _safe_float(_coalesce(rec.get("x_position"), rec.get("x_m"), rec.get("x_coord")))
        y = _safe_float(_coalesce(rec.get("y_position"), rec.get("y_m"), rec.get("y_coord")))
        z = _safe_float(_coalesce(rec.get("z_position"), rec.get("z_m"), rec.get("z_coord")))
        p = Path(str(rec.get("path") or ""))
        if p.exists() and (x is None or y is None):
            try:
                df = _read_table(p, rec, max_rows=20)
                roles = _column_roles(df)
                for key, col in (("x", roles.get("x")), ("y", roles.get("y")), ("z", roles.get("z"))):
                    vals = _numeric(df, col)
                    vals = vals[np.isfinite(vals)]
                    if len(vals):
                        if key == "x" and x is None: x = float(vals[0])
                        if key == "y" and y is None: y = float(vals[0])
                        if key == "z" and z is None: z = float(vals[0])
            except Exception:
                pass
        out[pt] = (x, y, z)
    return out


def _load_curves(browser, max_curves: int = 300) -> List[Curve]:
    recs = _selected_records(browser)[:max_curves]
    pos = _position_lookup(browser)
    curves: List[Curve] = []
    for i, rec in enumerate(recs, 1):
        p = Path(str(rec.get("path")))
        try:
            df = _read_table(p, rec)
            roles = _column_roles(df)
            xn, yn = _preferred_axes(rec, roles)
            if not xn or not yn:
                numeric_cols = [c for c in df.columns if pd.to_numeric(df[c], errors="coerce").notna().sum() >= 2]
                if len(numeric_cols) >= 2:
                    xn, yn = numeric_cols[0], numeric_cols[1]
                else:
                    continue
            x, y = _finite_xy(_numeric(df, xn), _numeric(df, yn))
            if len(x) < 2:
                continue
            pt = _safe_int(rec.get("point"), i)
            xc = _safe_float(_coalesce(rec.get("x_position"), rec.get("x_m"), rec.get("x_coord")))
            yc = _safe_float(_coalesce(rec.get("y_position"), rec.get("y_m"), rec.get("y_coord")))
            zc = _safe_float(_coalesce(rec.get("z_position"), rec.get("z_m"), rec.get("z_coord")))
            if pt in pos:
                px, py, pz = pos[pt]
                xc = xc if xc is not None else px
                yc = yc if yc is not None else py
                zc = zc if zc is not None else pz
            curves.append(Curve(
                path=p, record=rec, x_name=str(xn), y_name=str(yn), x=x, y=y,
                point=pt, method=_safe_int(rec.get("method_index"), 1),
                cycle=_safe_int(rec.get("cycle"), 0), line=_safe_int(rec.get("line"), 0),
                x_coord=xc, y_coord=yc, z_coord=zc,
            ))
        except Exception:
            continue
    return curves


def _metrics(curve: Curve) -> Dict[str, float]:
    x, y = _finite_xy(curve.x, curve.y)
    if not len(y):
        return {}
    tail = y[max(0, int(len(y) * 0.9)):]
    result = {
        "mean": float(np.nanmean(y)),
        "median": float(np.nanmedian(y)),
        "minimum": float(np.nanmin(y)),
        "maximum": float(np.nanmax(y)),
        "absolute_peak": float(y[np.nanargmax(np.abs(y))]),
        "endpoint": float(y[-1]),
        "steady_state": float(np.nanmean(tail)) if len(tail) else float(y[-1]),
        "range": float(np.nanmax(y) - np.nanmin(y)),
    }
    if len(x) == len(y) and len(x) > 1:
        order = np.argsort(x)
        try:
            result["integral"] = float(trapezoid(y[order], x[order]))
        except Exception:
            result["integral"] = float(np.trapz(y[order], x[order]))
    return result


def _point_table(curves: Sequence[Curve]) -> pd.DataFrame:
    rows = []
    for c in curves:
        row = {
            "point": c.point, "method": c.method, "cycle": c.cycle, "line": c.line,
            "x_coord": c.x_coord, "y_coord": c.y_coord, "z_coord": c.z_coord,
            "source": c.path.name, "x_axis": c.x_name, "signal": c.y_name,
        }
        row.update(_metrics(c))
        rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # Aggregate cycles/sweeps per scientific point/method for map views.
    numeric = [c for c in df.columns if c not in {"source", "x_axis", "signal"}]
    agg = {c: "mean" for c in numeric if c not in {"point", "method", "cycle", "line"}}
    for c in ("cycle", "line"):
        if c in df: agg[c] = "min"
    grouped = df.groupby(["point", "method"], as_index=False).agg(agg)
    try:
        from .spatial_points_v296 import dataframe_with_scan_distance
        grouped = dataframe_with_scan_distance(grouped)
    except Exception:
        pass
    return grouped


def _view_dir(browser) -> Path:
    candidates = [
        Path(str(getattr(browser, "h5_path", ""))).parent if getattr(browser, "h5_path", None) else None,
        Path(str(getattr(browser, "cache_root", ""))).parent if getattr(browser, "cache_root", None) else None,
        Path.cwd(),
    ]
    for c in candidates:
        if c and c.exists():
            d = c / "FaradaIQ_interactive_views"
            d.mkdir(parents=True, exist_ok=True)
            return d
    d = Path.cwd() / "FaradaIQ_interactive_views"; d.mkdir(parents=True, exist_ok=True); return d


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text or "view")).strip("_")
    return s[:90] or "view"


def _curve_long(curves: Sequence[Curve], max_per_curve: int = 900, max_total: int = 120000) -> pd.DataFrame:
    rows = []
    remaining = max_total
    for c in curves:
        if remaining <= 0:
            break
        x, y = _downsample(c.x, c.y, min(max_per_curve, remaining))
        label = f"M{c.method:02d} P{c.point:03d}" + (f" C{c.cycle:02d}" if c.cycle else "")
        for xx, yy in zip(x, y):
            rows.append({"axis": float(xx), "value": float(yy), "point": c.point, "method": c.method, "cycle": c.cycle, "label": label, "source": c.path.name})
        remaining -= len(x)
    return pd.DataFrame(rows)


def _choose_metric(point_df: pd.DataFrame, requested: str = "Auto") -> str:
    candidates = ["steady_state", "integral", "absolute_peak", "endpoint", "mean", "minimum", "maximum", "range"]
    if requested and requested != "Auto" and requested in point_df.columns:
        return requested
    for c in candidates:
        if c in point_df.columns and pd.to_numeric(point_df[c], errors="coerce").notna().any():
            return c
    nums = [c for c in point_df.columns if c not in {"point", "method", "cycle", "line", "x_coord", "y_coord", "z_coord"} and pd.api.types.is_numeric_dtype(point_df[c])]
    return nums[0] if nums else "point"


def _regular_grid(df: pd.DataFrame) -> bool:
    if not {"x_coord", "y_coord"}.issubset(df.columns):
        return False
    d = df.dropna(subset=["x_coord", "y_coord"])
    ux, uy = d.x_coord.nunique(), d.y_coord.nunique()
    return ux >= 2 and uy >= 2 and len(d) >= max(4, ux * uy * 0.7)


def make_altair_2d(browser, metric: str = "Auto") -> Dict[str, Any]:
    import altair as alt
    curves = _load_curves(browser)
    if not curves:
        raise ValueError("Select one or more numeric technique items before opening Interactive 2D.")
    points = _point_table(curves)
    metric = _choose_metric(points, metric)
    long = _curve_long(curves)
    out_dir = _view_dir(browser)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_interactive_2D_{stamp}.html"
    csv_path = out.with_suffix(".point_metrics.csv")
    points.to_csv(csv_path, index=False)

    alt.data_transformers.disable_max_rows()
    selection = alt.selection_point(fields=["point"], on="click", empty=True, name="SelectedPoint")
    tooltip = [alt.Tooltip("point:Q"), alt.Tooltip("method:Q"), alt.Tooltip(f"{metric}:Q", format=".5g")]
    spatial = points.dropna(subset=["x_coord", "y_coord"]) if {"x_coord", "y_coord"}.issubset(points.columns) else pd.DataFrame()
    if len(spatial) >= 4 and spatial.x_coord.nunique() >= 2 and spatial.y_coord.nunique() >= 2:
        map_chart = alt.Chart(spatial).mark_square(size=420).encode(
            x=alt.X("x_coord:Q", title="X position"), y=alt.Y("y_coord:Q", title="Y position"),
            color=alt.Color(f"{metric}:Q", title=metric.replace("_", " ")),
            tooltip=tooltip + [alt.Tooltip("x_coord:Q"), alt.Tooltip("y_coord:Q")],
            opacity=alt.condition(selection, alt.value(1.0), alt.value(0.35)),
        ).add_params(selection).properties(title=f"Interactive spatial map: {metric}", width=520, height=420)
    else:
        map_chart = alt.Chart(points).mark_circle(size=120).encode(
            x=alt.X("point:Q", title="Point / acquisition position"),
            y=alt.Y(f"{metric}:Q", title=metric.replace("_", " ")),
            color=alt.Color("method:N", title="Method"), tooltip=tooltip,
            opacity=alt.condition(selection, alt.value(1.0), alt.value(0.35)),
        ).add_params(selection).properties(title=f"Point-dependent {metric}", width=520, height=420)
    line_chart = alt.Chart(long).mark_line().encode(
        x=alt.X("axis:Q", title=curves[0].x_name),
        y=alt.Y("value:Q", title=curves[0].y_name),
        color=alt.Color("label:N", legend=None),
        tooltip=["label:N", alt.Tooltip("axis:Q", format=".6g"), alt.Tooltip("value:Q", format=".6g")],
    ).transform_filter(selection).properties(title="Selected point trace(s)", width=600, height=420).interactive()
    chart = (map_chart | line_chart).resolve_scale(color="independent").properties(
        title=f"FaradaIQ {VERSION}: linked 2D map and trace viewer"
    )
    try:
        chart.save(out, inline=True)
    except TypeError:
        chart.save(out)
    spec = out.with_suffix(".vegalite.json")
    spec.write_text(chart.to_json(indent=2), encoding="utf-8")
    return {"html": str(out), "point_metrics": str(csv_path), "spec": str(spec), "engine": "Altair", "metric": metric, "curves": len(curves), "points": int(points.point.nunique())}


def make_plotly_3d(browser, metric: str = "Auto") -> Dict[str, Any]:
    import plotly.graph_objects as go
    curves = _load_curves(browser)
    if not curves:
        raise ValueError("Select numeric technique items before opening 3D.")
    points = _point_table(curves)
    metric = _choose_metric(points, metric)
    traces = []
    buttons = []
    spatial_count = 0
    spatial = points.dropna(subset=["x_coord", "y_coord", metric]) if {"x_coord", "y_coord", metric}.issubset(points.columns) else pd.DataFrame()
    if len(spatial) >= 4:
        if _regular_grid(spatial):
            pivot = spatial.pivot_table(index="y_coord", columns="x_coord", values=metric, aggfunc="mean").sort_index().sort_index(axis=1)
            traces.append(go.Surface(x=pivot.columns.to_numpy(float), y=pivot.index.to_numpy(float), z=pivot.to_numpy(float), name=f"{metric} surface", visible=True, hovertemplate="X=%{x}<br>Y=%{y}<br>Value=%{z:.6g}<extra></extra>"))
        else:
            traces.append(go.Scatter3d(x=spatial.x_coord, y=spatial.y_coord, z=spatial[metric], mode="markers", marker=dict(size=6, color=spatial[metric], colorscale="Viridis", colorbar=dict(title=metric)), text=[f"Point {int(p)}" for p in spatial.point], name=f"{metric} spatial", visible=True, hovertemplate="%{text}<br>X=%{x}<br>Y=%{y}<br>Value=%{z:.6g}<extra></extra>"))
        spatial_count = 1
    # Point-stacked curves provide position/point-dependent 3D even without physical X/Y.
    max_each = max(120, min(700, 80000 // max(1, len(curves))))
    for c in curves:
        x, y = _downsample(c.x, c.y, max_each)
        traces.append(go.Scatter3d(x=x, y=np.full_like(x, c.point, dtype=float), z=y, mode="lines", name=f"M{c.method:02d} P{c.point:03d}" + (f" C{c.cycle:02d}" if c.cycle else ""), visible=(spatial_count == 0)))
    fig = go.Figure(data=traces)
    n = len(traces)
    if spatial_count:
        buttons = [
            dict(label=f"Spatial {metric}", method="update", args=[{"visible": [True] + [False] * (n - 1)}, {"title": f"Spatial {metric}", "scene": {"xaxis_title": "X position", "yaxis_title": "Y position", "zaxis_title": metric}}]),
            dict(label="Point-stacked traces", method="update", args=[{"visible": [False] + [True] * (n - 1)}, {"title": "Point-dependent stacked traces", "scene": {"xaxis_title": curves[0].x_name, "yaxis_title": "Point", "zaxis_title": curves[0].y_name}}]),
        ]
    fig.update_layout(
        title=f"FaradaIQ {VERSION}: " + (f"spatial {metric}" if spatial_count else "point-dependent 3D traces"),
        scene=dict(xaxis_title="X position" if spatial_count else curves[0].x_name, yaxis_title="Y position" if spatial_count else "Point", zaxis_title=metric if spatial_count else curves[0].y_name),
        updatemenus=[dict(type="buttons", direction="right", buttons=buttons, x=0.0, y=1.12)] if buttons else [],
        margin=dict(l=0, r=0, b=0, t=80), legend=dict(itemsizing="constant"),
    )
    out_dir = _view_dir(browser); stamp = time.strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_3D_{stamp}.html"
    fig.write_html(out, include_plotlyjs="directory", full_html=True, auto_open=False, config={"responsive": True, "scrollZoom": True, "displaylogo": False, "modeBarButtonsToAdd": ["select2d", "lasso2d"], "doubleClick": "reset+autosize"})
    points.to_csv(out.with_suffix(".point_metrics.csv"), index=False)
    return {"html": str(out), "engine": "Plotly", "metric": metric, "curves": len(curves), "points": int(points.point.nunique()), "spatial": bool(spatial_count)}


def _common_grid(curves: Sequence[Curve], max_bands: int = 80) -> Tuple[np.ndarray, np.ndarray, List[Curve]]:
    usable = []
    mins, maxs = [], []
    for c in curves:
        x, y = _finite_xy(c.x, c.y)
        if len(x) < 3:
            continue
        order = np.argsort(x); x, y = x[order], y[order]
        unique, idx = np.unique(x, return_index=True); y = y[idx]; x = unique
        if len(x) < 3:
            continue
        clone = Curve(**{**c.__dict__, "x": x, "y": y})
        usable.append(clone); mins.append(float(np.min(x))); maxs.append(float(np.max(x)))
    if not usable:
        raise ValueError("No compatible curves for hyperspectral/stack mode.")
    lo, hi = max(mins), min(maxs)
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        lo, hi = min(mins), max(maxs)
    # Preserve log spacing for positive frequency/tau axes.
    positive = lo > 0 and any(k in _norm(usable[0].x_name) for k in ("frequency", "freq", "tau", "wavelength"))
    if positive:
        grid = np.geomspace(lo, hi, max_bands)
    else:
        grid = np.linspace(lo, hi, max_bands)
    matrix = np.vstack([np.interp(grid, c.x, c.y) for c in usable])
    return grid, matrix, usable


def make_hyperspectral(browser, metric: str = "Auto") -> Dict[str, Any]:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    curves = _load_curves(browser)
    if len(curves) < 2:
        raise ValueError("Hyperspectral/stack mode needs at least two compatible point or position curves.")
    grid, matrix, curves = _common_grid(curves, max_bands=70)
    points = np.array([c.point for c in curves], dtype=float)
    coords_ok = sum(c.x_coord is not None and c.y_coord is not None for c in curves) >= max(4, int(0.7 * len(curves)))
    title = f"FaradaIQ {VERSION}: hyperspectral / multidimensional slider"
    if coords_ok:
        xcoords = np.array([float(c.x_coord) for c in curves]); ycoords = np.array([float(c.y_coord) for c in curves])
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=xcoords, y=ycoords, mode="markers", marker=dict(size=14, color=matrix[:, 0], colorscale="Viridis", colorbar=dict(title=curves[0].y_name)), text=[f"Point {c.point}" for c in curves], hovertemplate="%{text}<br>X=%{x}<br>Y=%{y}<br>Value=%{marker.color:.6g}<extra></extra>"))
        frames = [go.Frame(data=[go.Scatter(marker=dict(color=matrix[:, j]))], name=str(j)) for j in range(len(grid))]
        fig.frames = frames
        steps = [dict(method="animate", label=f"{v:.4g}", args=[[str(j)], {"mode": "immediate", "frame": {"duration": 0, "redraw": True}, "transition": {"duration": 0}}]) for j, v in enumerate(grid)]
        fig.update_layout(title=title, xaxis_title="X position", yaxis_title="Y position", sliders=[dict(active=0, currentvalue={"prefix": f"{curves[0].x_name} = "}, pad={"t": 40}, steps=steps)])
    else:
        fig = make_subplots(rows=1, cols=2, subplot_titles=("Point × spectral-axis cube", "Selected spectral slice"), column_widths=[0.68, 0.32])
        fig.add_trace(go.Heatmap(z=matrix, x=grid, y=points, colorbar=dict(title=curves[0].y_name), hovertemplate=f"Point=%{{y}}<br>{curves[0].x_name}=%{{x:.6g}}<br>{curves[0].y_name}=%{{z:.6g}}<extra></extra>"), row=1, col=1)
        fig.add_trace(go.Scatter(x=points, y=matrix[:, 0], mode="lines+markers", name="slice"), row=1, col=2)
        frames = [go.Frame(data=[go.Heatmap(z=matrix), go.Scatter(x=points, y=matrix[:, j])], name=str(j)) for j in range(len(grid))]
        fig.frames = frames
        steps = [dict(method="animate", label=f"{v:.4g}", args=[[str(j)], {"mode": "immediate", "frame": {"duration": 0, "redraw": True}, "transition": {"duration": 0}}]) for j, v in enumerate(grid)]
        fig.update_xaxes(title_text=curves[0].x_name, row=1, col=1)
        fig.update_yaxes(title_text="Point", row=1, col=1)
        fig.update_xaxes(title_text="Point", row=1, col=2)
        fig.update_yaxes(title_text=curves[0].y_name, row=1, col=2)
        fig.update_layout(title=title, sliders=[dict(active=0, currentvalue={"prefix": f"Slice {curves[0].x_name} = "}, pad={"t": 40}, steps=steps)])
    out_dir = _view_dir(browser); stamp = time.strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_hyperspectral_{stamp}.html"
    fig.write_html(out, include_plotlyjs="directory", full_html=True, auto_open=False, config={"responsive": True, "scrollZoom": True, "displaylogo": False, "modeBarButtonsToAdd": ["select2d", "lasso2d"], "doubleClick": "reset+autosize"})
    npz = out.with_suffix(".cube.npz")
    np.savez_compressed(npz, spectral_axis=grid, values=matrix, points=points)
    meta = {
        "schema": "faradaiq-hyperspectral-cube-v216", "version": VERSION,
        "spectral_axis_name": curves[0].x_name, "signal_name": curves[0].y_name,
        "n_points": len(curves), "n_bands": len(grid), "coordinates_available": bool(coords_ok),
        "source_files": [c.path.name for c in curves],
    }
    meta_path = out.with_suffix(".cube.json"); meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"html": str(out), "engine": "Plotly hyperspectral", "cube": str(npz), "metadata": str(meta_path), **meta}


# v216 multi-technique dimensional-view overrides.  These definitions replace
# the single-compatible-group functions above while retaining their public API.
def _curve_group_key(curve: Curve) -> Tuple[str, str, str]:
    tech = str(curve.record.get("technique_key") or curve.record.get("heka_technique") or "OTHER").upper()
    return tech, _norm(curve.x_name), _norm(curve.y_name)


def _curve_groups(curves: Sequence[Curve], min_size: int = 1) -> List[Tuple[str, List[Curve]]]:
    grouped: Dict[Tuple[str, str, str], List[Curve]] = {}
    for curve in curves:
        grouped.setdefault(_curve_group_key(curve), []).append(curve)
    out: List[Tuple[str, List[Curve]]] = []
    for (tech, _x, _y), items in grouped.items():
        if len(items) < min_size:
            continue
        label = f"{tech} — {items[0].y_name} vs {items[0].x_name}"
        out.append((label, items))
    out.sort(key=lambda pair: (-len(pair[1]), pair[0].lower()))
    return out


def _write_master_index(path: Path, title: str, entries: Sequence[Mapping[str, Any]]) -> Path:
    """Write a tabbed index that loads only the visible child visualization.

    Older builds assigned ``src`` to every hidden iframe. A three-cycle CV
    page therefore parsed and rendered all three large Plotly documents before
    the first cycle became responsive. Hidden views now keep their URL in
    ``data-src`` and are attached only when the user selects that tab.
    """
    path = Path(path)
    buttons: List[str] = []
    frames: List[str] = []
    for i, entry in enumerate(entries):
        label = html.escape(str(entry.get("label") or f"View {i + 1}"))
        src = html.escape(Path(str(entry.get("html"))).name, quote=True)
        buttons.append(f'<button id="tabbtn{i}" class="tabbtn" onclick="showTab({i})">{label}</button>')
        style = "" if i == 0 else "display:none"
        if i == 0:
            frames.append(
                f'<iframe id="tab{i}" class="tabframe" src="{src}" data-loaded="1" '
                f'style="{style}" loading="eager"></iframe>'
            )
        else:
            frames.append(
                f'<iframe id="tab{i}" class="tabframe" data-src="{src}" data-loaded="0" '
                f'style="{style}" loading="lazy" src="about:blank"></iframe>'
            )
    script = '''
<script>
function showTab(i){
  const frames=[...document.querySelectorAll('.tabframe')];
  const buttons=[...document.querySelectorAll('.tabbtn')];
  frames.forEach((frame,j)=>{
    const active=(j===i);
    frame.style.display=active?'block':'none';
    buttons[j].classList.toggle('active',active);
    if(active && frame.dataset.loaded!=='1'){
      frame.dataset.loaded='1';
      frame.src=frame.dataset.src;
    }
  });
}
window.addEventListener('DOMContentLoaded',()=>showTab(0));
</script>'''
    doc = (
        '<!doctype html><html><head><meta charset="utf-8"><title>' + html.escape(title) + '</title>'
        '<style>body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f5f7fa}'
        '.top{position:sticky;top:0;background:#fff;padding:10px;border-bottom:1px solid #ccd3dc;z-index:2}'
        '.tabbtn{margin:3px;padding:7px 11px;border:1px solid #aab4c0;border-radius:5px;background:#fff;cursor:pointer}'
        '.tabbtn.active{background:#e8f0fb;border-color:#6688b5;font-weight:600}'
        '.hint{font-size:12px;color:#52606d;margin-left:8px}'
        '.tabframe{width:100%;height:calc(100vh - 78px);border:0;background:#fff}</style>'
        + script + '</head><body><div class="top"><b>' + html.escape(title) + '</b>'
        + '<span class="hint">Other tabs load only when selected.</span><br>'
        + ''.join(buttons) + '</div>' + ''.join(frames) + '</body></html>'
    )
    path.write_text(doc, encoding="utf-8")
    return path



def _make_altair_group(browser, curves: Sequence[Curve], metric: str, group_label: str, suffix: str) -> Dict[str, Any]:
    import altair as alt
    points = _point_table(curves)
    metric = _choose_metric(points, metric)
    long = _curve_long(curves)
    out_dir = _view_dir(browser)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_interactive_2D_{_slug(suffix)}_{stamp}.html"
    csv_path = out.with_suffix(".point_metrics.csv")
    points.to_csv(csv_path, index=False)
    alt.data_transformers.disable_max_rows()
    selection = alt.selection_point(fields=["point"], on="click", empty=True, name="SelectedPoint")
    tooltip = [alt.Tooltip("point:Q"), alt.Tooltip("method:Q"), alt.Tooltip(f"{metric}:Q", format=".5g")]
    spatial = points.dropna(subset=["x_coord", "y_coord"]) if {"x_coord", "y_coord"}.issubset(points.columns) else pd.DataFrame()
    if len(spatial) >= 4 and spatial.x_coord.nunique() >= 2 and spatial.y_coord.nunique() >= 2:
        map_chart = alt.Chart(spatial).mark_square(size=420).encode(
            x=alt.X("x_coord:Q", title="X position / m"),
            y=alt.Y("y_coord:Q", title="Y position / m"),
            color=alt.Color(f"{metric}:Q", title=metric.replace("_", " ")),
            tooltip=tooltip + [alt.Tooltip("x_coord:Q"), alt.Tooltip("y_coord:Q")],
            opacity=alt.condition(selection, alt.value(1.0), alt.value(0.35)),
        ).add_params(selection).properties(title=f"Interactive spatial map: {metric}", width=520, height=420)
    else:
        map_chart = alt.Chart(points).mark_circle(size=120).encode(
            x=alt.X("point:Q", title="Point / acquisition position"),
            y=alt.Y(f"{metric}:Q", title=metric.replace("_", " ")),
            color=alt.Color("method:N", title="Method"),
            tooltip=tooltip,
            opacity=alt.condition(selection, alt.value(1.0), alt.value(0.35)),
        ).add_params(selection).properties(title=f"Point-dependent {metric}", width=520, height=420)
    line_chart = alt.Chart(long).mark_line().encode(
        x=alt.X("axis:Q", title=curves[0].x_name),
        y=alt.Y("value:Q", title=curves[0].y_name),
        color=alt.Color("label:N", legend=None),
        tooltip=["label:N", alt.Tooltip("axis:Q", format=".6g"), alt.Tooltip("value:Q", format=".6g")],
    ).transform_filter(selection).properties(title="Selected point trace(s)", width=600, height=420).interactive()
    chart = (map_chart | line_chart).resolve_scale(color="independent").properties(title=f"FaradaIQ {VERSION}: {group_label}")
    try:
        chart.save(out, inline=True)
    except TypeError:
        chart.save(out)
    spec = out.with_suffix(".vegalite.json")
    spec.write_text(chart.to_json(indent=2), encoding="utf-8")
    return {
        "html": str(out), "point_metrics": str(csv_path), "spec": str(spec),
        "engine": "Altair", "metric": metric, "curves": len(curves),
        "points": int(points.point.nunique()), "label": group_label,
    }


def make_altair_2d(browser, metric: str = "Auto") -> Dict[str, Any]:
    curves = _load_curves(browser)
    if not curves:
        raise ValueError("Select one or more numeric technique items before opening Interactive 2D.")
    groups = _curve_groups(curves, min_size=1)
    outputs = [_make_altair_group(browser, items, metric, label, f"g{i + 1:02d}") for i, (label, items) in enumerate(groups)]
    if len(outputs) == 1:
        return outputs[0]
    out_dir = _view_dir(browser)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    master = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_interactive_2D_multitechnique_{stamp}.html"
    _write_master_index(master, f"FaradaIQ {VERSION} multi-technique interactive 2D", outputs)
    return {**outputs[0], "html": str(master), "engine": "Altair multi-technique", "groups": outputs, "group_count": len(outputs), "curves": len(curves)}


def _make_plotly_group(browser, curves: Sequence[Curve], metric: str, group_label: str, suffix: str) -> Dict[str, Any]:
    import plotly.graph_objects as go
    points = _point_table(curves)
    metric = _choose_metric(points, metric)
    traces: List[Any] = []
    spatial_count = 0
    spatial = points.dropna(subset=["x_coord", "y_coord", metric]) if {"x_coord", "y_coord", metric}.issubset(points.columns) else pd.DataFrame()
    if len(spatial) >= 4:
        if _regular_grid(spatial):
            pivot = spatial.pivot_table(index="y_coord", columns="x_coord", values=metric, aggfunc="mean").sort_index().sort_index(axis=1)
            traces.append(go.Surface(
                x=pivot.columns.to_numpy(float), y=pivot.index.to_numpy(float), z=pivot.to_numpy(float),
                name=f"{metric} surface", visible=True,
                hovertemplate="X=%{x}<br>Y=%{y}<br>Value=%{z:.6g}<extra></extra>",
            ))
        else:
            traces.append(go.Scatter3d(
                x=spatial.x_coord, y=spatial.y_coord, z=spatial[metric], mode="markers",
                marker=dict(size=6, color=spatial[metric], colorscale="Viridis", colorbar=dict(title=metric)),
                text=[f"Point {int(p)}" for p in spatial.point], name=f"{metric} spatial", visible=True,
                hovertemplate="%{text}<br>X=%{x}<br>Y=%{y}<br>Value=%{z:.6g}<extra></extra>",
            ))
        spatial_count = 1
    max_each = max(120, min(700, 80000 // max(1, len(curves))))
    for curve in curves:
        x, y = _downsample(curve.x, curve.y, max_each)
        traces.append(go.Scatter3d(
            x=x, y=np.full_like(x, curve.point, dtype=float), z=y, mode="lines",
            name=f"M{curve.method:02d} P{curve.point:03d}" + (f" C{curve.cycle:02d}" if curve.cycle else ""),
            visible=(spatial_count == 0),
        ))
    fig = go.Figure(data=traces)
    n = len(traces)
    buttons = []
    if spatial_count:
        buttons = [
            dict(label=f"Spatial {metric}", method="update", args=[{"visible": [True] + [False] * (n - 1)}, {"title": f"{group_label}: spatial {metric}", "scene": {"xaxis_title": "X position / m", "yaxis_title": "Y position / m", "zaxis_title": metric}}]),
            dict(label="Point-stacked traces", method="update", args=[{"visible": [False] + [True] * (n - 1)}, {"title": f"{group_label}: point-dependent traces", "scene": {"xaxis_title": curves[0].x_name, "yaxis_title": "Point", "zaxis_title": curves[0].y_name}}]),
        ]
    fig.update_layout(
        title=f"FaradaIQ {VERSION}: {group_label}",
        scene=dict(
            xaxis_title="X position / m" if spatial_count else curves[0].x_name,
            yaxis_title="Y position / m" if spatial_count else "Point",
            zaxis_title=metric if spatial_count else curves[0].y_name,
        ),
        updatemenus=[dict(type="buttons", direction="right", buttons=buttons, x=0.0, y=1.12)] if buttons else [],
        margin=dict(l=0, r=0, b=0, t=80), legend=dict(itemsizing="constant"),
    )
    out_dir = _view_dir(browser)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_3D_{_slug(suffix)}_{stamp}.html"
    fig.write_html(out, include_plotlyjs="directory", full_html=True, auto_open=False, config={"responsive": True, "scrollZoom": True, "displaylogo": False, "modeBarButtonsToAdd": ["select2d", "lasso2d"], "doubleClick": "reset+autosize"})
    points.to_csv(out.with_suffix(".point_metrics.csv"), index=False)
    return {
        "html": str(out), "engine": "Plotly", "metric": metric,
        "curves": len(curves), "points": int(points.point.nunique()),
        "spatial": bool(spatial_count), "label": group_label,
    }


def make_plotly_3d(browser, metric: str = "Auto") -> Dict[str, Any]:
    curves = _load_curves(browser)
    if not curves:
        raise ValueError("Select numeric technique items before opening 3D.")
    groups = _curve_groups(curves, min_size=1)
    outputs = [_make_plotly_group(browser, items, metric, label, f"g{i + 1:02d}") for i, (label, items) in enumerate(groups)]
    if len(outputs) == 1:
        return outputs[0]
    out_dir = _view_dir(browser)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    master = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_3D_multitechnique_{stamp}.html"
    _write_master_index(master, f"FaradaIQ {VERSION} multi-technique 3D", outputs)
    return {**outputs[0], "html": str(master), "engine": "Plotly multi-technique", "groups": outputs, "group_count": len(outputs), "curves": len(curves)}


def _make_hyperspectral_group(browser, curves: Sequence[Curve], group_label: str, suffix: str) -> Dict[str, Any]:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    grid, matrix, curves = _common_grid(curves, max_bands=70)
    points = np.array([curve.point for curve in curves], dtype=float)
    valid_coords = [curve.x_coord is not None and curve.y_coord is not None for curve in curves]
    coords_ok = sum(valid_coords) >= max(4, int(0.7 * len(curves)))
    title = f"FaradaIQ {VERSION}: {group_label} hyperspectral slider"
    regular_xy = False
    if coords_ok:
        xcoords = np.array([float(curve.x_coord) for curve in curves])
        ycoords = np.array([float(curve.y_coord) for curve in curves])
        coord_df = pd.DataFrame({"x": xcoords, "y": ycoords, "row": np.arange(len(curves))})
        ux = np.sort(coord_df.x.unique())
        uy = np.sort(coord_df.y.unique())
        regular_xy = len(ux) >= 2 and len(uy) >= 2 and len(coord_df.drop_duplicates(["x", "y"])) >= max(4, int(0.7 * len(ux) * len(uy)))
        if regular_xy:
            def band_grid(j: int) -> np.ndarray:
                z = np.full((len(uy), len(ux)), np.nan, dtype=float)
                xmap = {value: i for i, value in enumerate(ux)}
                ymap = {value: i for i, value in enumerate(uy)}
                for row_i, (xv, yv) in enumerate(zip(xcoords, ycoords)):
                    z[ymap[yv], xmap[xv]] = matrix[row_i, j]
                return z
            fig = go.Figure(data=[go.Heatmap(
                x=ux, y=uy, z=band_grid(0), colorscale="Viridis",
                colorbar=dict(title=curves[0].y_name),
                hovertemplate=f"X=%{{x}}<br>Y=%{{y}}<br>{curves[0].y_name}=%{{z:.6g}}<extra></extra>",
            )])
            fig.frames = [go.Frame(data=[go.Heatmap(z=band_grid(j))], name=str(j)) for j in range(len(grid))]
        else:
            fig = go.Figure(data=[go.Scatter(
                x=xcoords, y=ycoords, mode="markers",
                marker=dict(size=14, color=matrix[:, 0], colorscale="Viridis", colorbar=dict(title=curves[0].y_name)),
                text=[f"Point {curve.point}" for curve in curves],
                hovertemplate="%{text}<br>X=%{x}<br>Y=%{y}<br>Value=%{marker.color:.6g}<extra></extra>",
            )])
            fig.frames = [go.Frame(data=[go.Scatter(marker=dict(color=matrix[:, j]))], name=str(j)) for j in range(len(grid))]
        steps = [dict(method="animate", label=f"{value:.4g}", args=[[str(j)], {"mode": "immediate", "frame": {"duration": 0, "redraw": True}, "transition": {"duration": 0}}]) for j, value in enumerate(grid)]
        fig.update_layout(
            title=title, xaxis_title="X position / m", yaxis_title="Y position / m",
            sliders=[dict(active=0, currentvalue={"prefix": f"{curves[0].x_name} = "}, pad={"t": 40}, steps=steps)],
        )
    else:
        fig = make_subplots(rows=1, cols=2, subplot_titles=("Point × spectral-axis cube", "Selected spectral slice"), column_widths=[0.68, 0.32])
        fig.add_trace(go.Heatmap(
            z=matrix, x=grid, y=points, colorbar=dict(title=curves[0].y_name),
            hovertemplate=f"Point=%{{y}}<br>{curves[0].x_name}=%{{x:.6g}}<br>{curves[0].y_name}=%{{z:.6g}}<extra></extra>",
        ), row=1, col=1)
        fig.add_trace(go.Scatter(x=points, y=matrix[:, 0], mode="lines+markers", name="slice"), row=1, col=2)
        fig.frames = [go.Frame(data=[go.Heatmap(z=matrix), go.Scatter(x=points, y=matrix[:, j])], name=str(j)) for j in range(len(grid))]
        steps = [dict(method="animate", label=f"{value:.4g}", args=[[str(j)], {"mode": "immediate", "frame": {"duration": 0, "redraw": True}, "transition": {"duration": 0}}]) for j, value in enumerate(grid)]
        fig.update_xaxes(title_text=curves[0].x_name, row=1, col=1)
        fig.update_yaxes(title_text="Point", row=1, col=1)
        fig.update_xaxes(title_text="Point", row=1, col=2)
        fig.update_yaxes(title_text=curves[0].y_name, row=1, col=2)
        fig.update_layout(title=title, sliders=[dict(active=0, currentvalue={"prefix": f"Slice {curves[0].x_name} = "}, pad={"t": 40}, steps=steps)])
    out_dir = _view_dir(browser)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_hyperspectral_{_slug(suffix)}_{stamp}.html"
    fig.write_html(out, include_plotlyjs="directory", full_html=True, auto_open=False, config={"responsive": True, "scrollZoom": True, "displaylogo": False, "modeBarButtonsToAdd": ["select2d", "lasso2d"], "doubleClick": "reset+autosize"})
    npz = out.with_suffix(".cube.npz")
    np.savez_compressed(
        npz,
        spectral_axis=grid,
        values=matrix,
        points=points,
        x_coordinates=np.array([np.nan if curve.x_coord is None else curve.x_coord for curve in curves]),
        y_coordinates=np.array([np.nan if curve.y_coord is None else curve.y_coord for curve in curves]),
        z_coordinates=np.array([np.nan if curve.z_coord is None else curve.z_coord for curve in curves]),
        methods=np.array([curve.method for curve in curves]),
        cycles=np.array([curve.cycle for curve in curves]),
    )
    meta = {
        "schema": "faradaiq-hyperspectral-cube-v216",
        "version": VERSION,
        "group_label": group_label,
        "technique": str(curves[0].record.get("technique_key") or "OTHER"),
        "spectral_axis_name": curves[0].x_name,
        "signal_name": curves[0].y_name,
        "n_points": len(curves),
        "n_unique_points": len(set(curve.point for curve in curves)),
        "n_bands": len(grid),
        "coordinates_available": bool(coords_ok),
        "regular_xy_grid": bool(regular_xy),
        "point_indices": [curve.point for curve in curves],
        "method_indices": [curve.method for curve in curves],
        "cycle_indices": [curve.cycle for curve in curves],
        "source_files": [curve.path.name for curve in curves],
    }
    meta_path = out.with_suffix(".cube.json")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"html": str(out), "engine": "Plotly hyperspectral", "cube": str(npz), "metadata": str(meta_path), "label": group_label, **meta}


def make_hyperspectral(browser, metric: str = "Auto") -> Dict[str, Any]:
    curves = _load_curves(browser)
    if len(curves) < 2:
        raise ValueError("Hyperspectral/stack mode needs at least two compatible point or position curves.")
    groups = _curve_groups(curves, min_size=2)
    if not groups:
        raise ValueError("No compatible repeated technique curves were found for hyperspectral mode.")
    outputs = [_make_hyperspectral_group(browser, items, label, f"g{i + 1:02d}") for i, (label, items) in enumerate(groups)]
    if len(outputs) == 1:
        return outputs[0]
    out_dir = _view_dir(browser)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    master = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_hyperspectral_multitechnique_{stamp}.html"
    _write_master_index(master, f"FaradaIQ {VERSION} multi-technique hyperspectral viewer", outputs)
    master_meta = out_dir / f"{master.stem}.cube_index.json"
    master_meta.write_text(json.dumps({
        "schema": "faradaiq-hyperspectral-multitechnique-index-v216",
        "version": VERSION,
        "groups": outputs,
    }, indent=2), encoding="utf-8")
    return {
        **outputs[0], "html": str(master), "engine": "Plotly multi-technique hyperspectral",
        "groups": outputs, "group_count": len(outputs), "cube_index": str(master_meta), "curves": len(curves),
    }


def _open_html_process(path: Path) -> None:
    path = Path(path).resolve()
    try:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--view", str(path)], cwd=str(Path(__file__).resolve().parents[1]))
    except Exception:
        webbrowser.open(path.as_uri())


def _view_html(path: Path) -> int:
    path = Path(path).resolve()
    try:
        import webview
        webview.create_window(f"FaradaIQ {VERSION} interactive visualization", path.as_uri(), width=1400, height=900)
        webview.start(debug=False)
        return 0
    except Exception:
        webbrowser.open(path.as_uri())
        return 0


def generate(browser, mode: str, metric: str = "Auto") -> Dict[str, Any]:
    mode_l = str(mode or "").lower()
    if "altair" in mode_l or "2d" in mode_l:
        result = make_altair_2d(browser, metric)
    elif "hyper" in mode_l or "cube" in mode_l or "stack" in mode_l:
        result = make_hyperspectral(browser, metric)
    elif "3d" in mode_l or "plotly" in mode_l:
        result = make_plotly_3d(browser, metric)
    else:
        raise ValueError(f"Unsupported multidimensional mode: {mode}")
    _open_html_process(Path(result["html"]))
    return result


def _install_legacy_v216(core: Dict[str, Any]) -> None:
    Browser = core.get("FaradaIQH5Browser")
    ttk = core.get("ttk")
    tk = core.get("tk")
    if Browser is None or ttk is None or tk is None:
        raise RuntimeError("FaradaIQ browser classes are unavailable")
    if getattr(Browser, "_v216_multidim_installed", False):
        return
    previous = Browser._build_visible_ui_first

    def build_ui(self):
        previous(self)
        try:
            bar = ttk.LabelFrame(self, text="1D / 2D / 3D / hyperspectral visualization", padding=6)
            # Use a plain pack call. Some older browser panels contain grid-managed
            # children, and Tk rejects pack(before=<grid child>). The dimensionality
            # toolbar must always be constructed rather than silently stopping after
            # an empty frame is created.
            bar.pack(fill="x", padx=8, pady=(0, 6))
            self._v216_viz_mode_var = tk.StringVar(value="Altair interactive 2D")
            self._v216_viz_metric_var = tk.StringVar(value="Auto")
            ttk.Label(bar, text="Engine").pack(side="left", padx=(0, 5))
            ttk.Combobox(bar, textvariable=self._v216_viz_mode_var, state="readonly", width=25,
                         values=("Altair interactive 2D", "Plotly true 3D", "Hyperspectral slider / cube")).pack(side="left", padx=(0, 8))
            ttk.Label(bar, text="Metric").pack(side="left", padx=(0, 5))
            ttk.Combobox(bar, textvariable=self._v216_viz_metric_var, state="readonly", width=17,
                         values=("Auto", "steady_state", "integral", "absolute_peak", "endpoint", "mean", "minimum", "maximum", "range")).pack(side="left", padx=(0, 8))

            def run():
                mode = self._v216_viz_mode_var.get(); metric = self._v216_viz_metric_var.get()
                try:
                    self.log(f"Preparing {mode} using metric {metric}...")
                    self.set_status(f"Preparing {mode}...") if hasattr(self, "set_status") else None
                except Exception:
                    pass
                def worker():
                    try:
                        result = generate(self, mode, metric)
                        msg = f"Opened {result.get('engine')} view: {result.get('html')}"
                    except Exception as exc:
                        msg = f"Multidimensional visualization failed: {exc}"
                    try:
                        self.after(0, lambda m=msg: (self.log(m), self.set_status(m) if hasattr(self, "set_status") else None))
                    except Exception:
                        pass
                threading.Thread(target=worker, daemon=True).start()
            ttk.Button(bar, text="Open selected data", command=run).pack(side="left", padx=2)
            ttk.Label(bar, text="Matplotlib remains the native 1D/publication backend.").pack(side="left", padx=(10, 0))
        except Exception:
            pass

    def apply_visualization_options(browser: Any, raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        opts = _v216_options(raw or {})
        browser._v222_pending_visualization_options = dict(opts)
        browser._v222_visualization_options = dict(opts)
        mapping = {
            "_v216_spatial_unit_var": "spatial_unit", "_v216_signal_unit_var": "signal_unit", "_v216_spectral_unit_var": "spectral_unit",
            "_v216_render_mode_var": "render_mode", "_v216_interpolation_var": "interpolation", "_v216_contour_levels_var": "contour_levels",
            "_v216_adaptive_contours_var": "adaptive_contours", "_v216_palette_var": "palette", "_v216_reverse_palette_var": "reverse_palette",
            "_v216_range_mode_var": "range_mode", "_v216_range_min_var": "range_min", "_v216_range_max_var": "range_max",
            "_v216_contour_line_width_var": "contour_line_width", "_v216_contour_labels_var": "contour_labels", "_v216_smoothing_var": "smoothing",
            "_v216_mask_hull_var": "mask_outside_hull", "_v216_overlay_points_var": "overlay_points", "_v216_opacity_var": "opacity",
            "_v216_point_size_var": "point_size", "_v222_surface_model_var": "surface_model", "_v222_tip_diameter_var": "tip_diameter_um",
            "_v222_overlap_var": "overlap_percent", "_v222_clip_overshoot_var": "clip_overshoot", "_v222_grid_quality_var": "grid_quality",
        }
        for attr, key in mapping.items():
            try:
                var = getattr(browser, attr)
                var.set(opts[key])
            except Exception:
                pass
        _v222_save_persisted_options(opts)
        return opts

    # Include the full visualization state in canonical browser/main sessions.
    previous_collect = core.get("_v75_collect_session")
    if callable(previous_collect):
        def collect_session(browser: Any, _prev=previous_collect):
            payload = _prev(browser)
            try:
                opts = browser._v216_multidim_options() if hasattr(browser, "_v216_multidim_options") else getattr(browser, "_v222_visualization_options", _v222_load_persisted_options())
                payload["visualization_options"] = _v216_options(opts)
            except Exception:
                payload["visualization_options"] = _v222_load_persisted_options()
            return payload
        core["_v75_collect_session"] = collect_session

    previous_apply = core.get("_v75_apply_session_payload")
    if callable(previous_apply):
        def apply_session(browser: Any, payload: Any, silent: bool=False, _prev=previous_apply):
            result = _prev(browser, payload, silent=silent)
            try:
                raw = payload.get("visualization_options") if isinstance(payload, dict) else None
                if raw:
                    apply_visualization_options(browser, raw)
            except Exception:
                pass
            return result
        core["_v75_apply_session_payload"] = apply_session

    previous_v138_payload = core.get("_v138_session_payload")
    if callable(previous_v138_payload):
        def v138_payload(browser: Any, include_bundle: bool=True, _prev=previous_v138_payload):
            payload = _prev(browser, include_bundle=include_bundle)
            try:
                opts = browser._v216_multidim_options() if hasattr(browser, "_v216_multidim_options") else getattr(browser, "_v222_visualization_options", _v222_load_persisted_options())
                payload["visualization_options"] = _v216_options(opts)
            except Exception:
                pass
            return payload
        core["_v138_session_payload"] = v138_payload

    Browser._v222_apply_visualization_options = apply_visualization_options
    Browser._build_visible_ui_first = build_ui
    Browser._v216_multidim_installed = True


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--view":
        raise SystemExit(_view_html(Path(sys.argv[2])))
    raise SystemExit("Use through FaradaIQ Studio or: multidim_v216.py --view <html>")

# ---------------------------------------------------------------------------
# FaradaIQ Studio v273 branch-aware multidimensional visualization patch
# ---------------------------------------------------------------------------
import base64 as _v216_base64
import io as _v216_io

_V216_DEFAULT_OPTIONS = {
    "spatial_unit": "µm",
    "point_axis": "Auto",
    "signal_unit": "Auto",
    "spectral_unit": "Auto",
    "render_mode": "Contour + points",
    "interpolation": "Linear",
    "contour_levels": 16,
    "adaptive_contours": True,
    "palette": "Viridis",
    "reverse_palette": False,
    "range_mode": "Robust (2–98%)",
    "range_scope": "Lock across cube",
    "range_min": "",
    "range_max": "",
    "contour_line_width": 1.0,
    "contour_labels": False,
    "smoothing": "Light",
    "surface_model": "Standard interpolation",
    "tip_diameter_um": "5.0",
    "overlap_percent": "0",
    "clip_overshoot": True,
    "grid_quality": "Auto",
    "mask_outside_hull": True,
    "overlay_points": True,
    "opacity": 0.92,
    "point_size": 13,
    "background_enabled": False,
    "background_path": "",
    "background_fit": "Fit to data extent",
    "background_opacity": 0.35,
    "background_xmin": "",
    "background_xmax": "",
    "background_ymin": "",
    "background_ymax": "",
    "lock_xy_aspect": False,
    "aspect_preset": "Auto fit",
}


def _v216_options(options: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    out = dict(_V216_DEFAULT_OPTIONS)
    if options:
        out.update({str(k): v for k, v in dict(options).items()})
    try:
        out["contour_levels"] = max(3, min(80, int(float(out.get("contour_levels", 16)))))
    except Exception:
        out["contour_levels"] = 16
    try:
        out["opacity"] = max(0.05, min(1.0, float(out.get("opacity", 0.92))))
    except Exception:
        out["opacity"] = 0.92
    try:
        out["point_size"] = max(3, min(40, int(float(out.get("point_size", 13)))))
    except Exception:
        out["point_size"] = 13
    try:
        out["background_opacity"] = max(0.0, min(1.0, float(out.get("background_opacity", 0.35))))
    except Exception:
        out["background_opacity"] = 0.35
    out["overlay_points"] = bool(out.get("overlay_points", True))
    out["background_enabled"] = bool(out.get("background_enabled", False))
    out["lock_xy_aspect"] = bool(out.get("lock_xy_aspect", False))
    _aspect = str(out.get("aspect_preset") or "Auto fit")
    out["aspect_preset"] = _aspect if _aspect in {"Auto fit", "Equal XY units", "Square plot", "Rectangle 4:3", "Wide 16:9"} else "Auto fit"
    if out["lock_xy_aspect"] and out["aspect_preset"] == "Auto fit":
        out["aspect_preset"] = "Equal XY units"
    out["adaptive_contours"] = bool(out.get("adaptive_contours", True))
    out["reverse_palette"] = bool(out.get("reverse_palette", False))
    out["contour_labels"] = bool(out.get("contour_labels", False))
    out["mask_outside_hull"] = bool(out.get("mask_outside_hull", True))
    out["clip_overshoot"] = bool(out.get("clip_overshoot", True))
    try:
        out["tip_diameter_um"] = max(0.001, min(1000000.0, float(out.get("tip_diameter_um", 5.0))))
    except Exception:
        out["tip_diameter_um"] = 5.0
    try:
        out["overlap_percent"] = max(0.0, min(99.5, float(out.get("overlap_percent", 0.0))))
    except Exception:
        out["overlap_percent"] = 0.0
    if str(out.get("surface_model") or "").strip() not in {"Standard interpolation", "Origin-style smooth", "SECCM tip footprint"}:
        out["surface_model"] = "Standard interpolation"
    if str(out.get("grid_quality") or "").strip() not in {"Auto", "Fine", "Publication"}:
        out["grid_quality"] = "Auto"
    if str(out.get("range_scope") or "").strip() not in {"Lock across cube", "Per frame"}:
        out["range_scope"] = "Lock across cube"
    try:
        out["contour_line_width"] = max(0.2, min(5.0, float(out.get("contour_line_width", 1.0))))
    except Exception:
        out["contour_line_width"] = 1.0
    point_axis = str(out.get("point_axis") or "Auto").title()
    out["point_axis"] = point_axis if point_axis in {"Auto", "Point", "X", "Y", "Distance"} else "Auto"
    return out



_V222_LAST_OPTIONS: Dict[str, Any] = dict(_V216_DEFAULT_OPTIONS)


def _v222_settings_path() -> Path:
    """Return a writable, package-local visualization settings path when possible."""
    candidates: List[Path] = []
    env = str(os.environ.get("FARADAIQ_RUNTIME_SETTINGS_DIR") or "").strip()
    if env:
        candidates.append(Path(env).expanduser())
    try:
        candidates.append(Path(__file__).resolve().parents[1] / "FaradaIQ_runtime_settings")
    except Exception:
        pass
    try:
        candidates.append(Path.home() / ".faradaiq")
    except Exception:
        pass
    for folder in candidates:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return folder / "visualization_defaults_v226.json"
        except Exception:
            continue
    return Path("visualization_defaults_v226.json")


def _v222_load_persisted_options() -> Dict[str, Any]:
    global _V222_LAST_OPTIONS
    try:
        path = _v222_settings_path()
        candidates = [path]
        # Read the v222 settings filename once for backward compatibility, then
        # future saves migrate to the normalized v228 filename.
        legacy = path.with_name("visualization_defaults_v225.json")
        older = path.with_name("visualization_defaults_v222.json")
        if legacy != path:
            candidates.append(legacy)
        if older != path and older != legacy:
            candidates.append(older)
        for candidate in candidates:
            if candidate.exists():
                obj = json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
                if isinstance(obj, dict):
                    legacy_default = (
                        str(obj.get("render_mode", "Measured points")) == "Measured points"
                        and str(obj.get("surface_model", "Standard interpolation")) == "Standard interpolation"
                        and str(obj.get("interpolation", "Linear")) == "Linear"
                        and "contour_default_generation" not in obj
                    )
                    if legacy_default:
                        obj["render_mode"] = "Contour + points"
                    obj["contour_default_generation"] = 246
                    _V222_LAST_OPTIONS = _v216_options(obj)
                    try:
                        path.write_text(json.dumps(_V222_LAST_OPTIONS, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception:
                        pass
                    break
    except Exception:
        pass
    return _v216_options(_V222_LAST_OPTIONS)


def _v222_save_persisted_options(options: Mapping[str, Any]) -> None:
    global _V222_LAST_OPTIONS
    _V222_LAST_OPTIONS = _v216_options(options)
    try:
        path = _v222_settings_path()
        path.write_text(json.dumps(_V222_LAST_OPTIONS, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _v222_tip_diameter_in_spatial_unit(options: Mapping[str, Any]) -> float:
    """Convert the user-entered SECCM tip diameter in µm to the active X/Y unit."""
    diameter_um = float(_v216_options(options).get("tip_diameter_um", 5.0))
    unit = str(options.get("spatial_unit") or "µm")
    factors = {
        "nm": 1000.0,
        "µm": 1.0,
        "um": 1.0,
        "mm": 0.001,
        "m": 1e-6,
        "Scientific": 1e-6,
    }
    return diameter_um * factors.get(unit, 1.0)


def _v222_nominal_spacing(options: Mapping[str, Any]) -> float:
    opts = _v216_options(options)
    diameter = _v222_tip_diameter_in_spatial_unit(opts)
    overlap = float(opts.get("overlap_percent", 0.0)) / 100.0
    return max(diameter * (1.0 - overlap), diameter * 0.005)


def _v222_median_neighbor_spacing(points: np.ndarray) -> float:
    points = np.asarray(points, dtype=float)
    if len(points) < 2:
        return 1.0
    try:
        from scipy.spatial import cKDTree
        distances, _ = cKDTree(points).query(points, k=2)
        vals = np.asarray(distances[:, 1], dtype=float)
        vals = vals[np.isfinite(vals) & (vals > 0)]
        if vals.size:
            return float(np.nanmedian(vals))
    except Exception:
        pass
    dx = float(np.nanmax(points[:, 0]) - np.nanmin(points[:, 0]))
    dy = float(np.nanmax(points[:, 1]) - np.nanmin(points[:, 1]))
    return max(dx, dy, 1.0) / max(2.0, math.sqrt(len(points)))


def _v222_grid_axes(df: pd.DataFrame, options: Mapping[str, Any], physical_step: Optional[float] = None) -> Tuple[np.ndarray, np.ndarray]:
    span_x = max(float(df.x.max() - df.x.min()), 1e-12)
    span_y = max(float(df.y.max() - df.y.min()), 1e-12)
    quality = str(options.get("grid_quality") or "Auto")
    max_dim = {"Auto": 180, "Fine": 260, "Publication": 360}.get(quality, 180)
    min_dim = {"Auto": 60, "Fine": 100, "Publication": 140}.get(quality, 60)
    if physical_step and math.isfinite(physical_step) and physical_step > 0:
        pixel = max(physical_step / 4.0, min(span_x, span_y) / max_dim)
        nx = int(math.ceil(span_x / pixel)) + 1
        ny = int(math.ceil(span_y / pixel)) + 1
    else:
        base = int(round(math.sqrt(max(1, len(df))) * (12 if quality == "Auto" else 17 if quality == "Fine" else 22)))
        nx = ny = base
    nx = max(min_dim, min(max_dim, nx))
    ny = max(min_dim, min(max_dim, ny))
    return (
        np.linspace(float(df.x.min()), float(df.x.max()), nx),
        np.linspace(float(df.y.min()), float(df.y.max()), ny),
    )


def _v222_origin_style_surface(df: pd.DataFrame, options: Mapping[str, Any]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Create a smooth C1-like surface using normalized thin-plate RBF interpolation.

    This is an open SciPy reconstruction intended to provide the smooth visual
    character users expect from commercial contour packages. It is not a claim
    to reproduce OriginLab's proprietary implementation exactly.
    """
    gx, gy = _v222_grid_axes(df, options)
    xx, yy = np.meshgrid(gx, gy)
    pts = df[["x", "y"]].to_numpy(float)
    vals = df.v.to_numpy(float)
    coord_scale = max(_v222_median_neighbor_spacing(pts), 1e-12)
    center = np.nanmean(pts, axis=0)
    pts_n = (pts - center) / coord_scale
    query = (np.column_stack([xx.ravel(), yy.ravel()]) - center) / coord_scale
    v_mean = float(np.nanmean(vals))
    v_scale = max(float(np.nanstd(vals)), max(abs(v_mean), 1.0) * 1e-12)
    vals_n = (vals - v_mean) / v_scale
    smoothing = str(options.get("smoothing") or "Light")
    smooth_value = {"None": 0.0, "Light": 0.015, "Medium": 0.06, "Strong": 0.18}.get(smoothing, 0.015)
    try:
        from scipy.interpolate import RBFInterpolator
        kwargs: Dict[str, Any] = {
            "kernel": "thin_plate_spline",
            "smoothing": smooth_value,
            "degree": 1,
        }
        if len(pts_n) > 48:
            kwargs["neighbors"] = min(64, len(pts_n))
        rbf = RBFInterpolator(pts_n, vals_n, **kwargs)
        z = rbf(query).reshape(xx.shape) * v_scale + v_mean
    except Exception:
        try:
            from scipy.interpolate import CloughTocher2DInterpolator
            z = np.asarray(CloughTocher2DInterpolator(pts, vals, fill_value=np.nan)(xx, yy), dtype=float)
        except Exception:
            from scipy.interpolate import griddata
            z = griddata(pts, vals, (xx, yy), method="cubic")
    return gx, gy, np.asarray(z, dtype=float)


def _v222_seccm_footprint_surface(df: pd.DataFrame, options: Mapping[str, Any]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Blend measured values with a Gaussian SECCM footprint.

    Tip diameter is interpreted as the Gaussian full width at half maximum
    (FWHM). Overlap determines nominal center-to-center scan spacing and the
    interpolation grid density. Measured values themselves are not modified.
    """
    diameter = max(_v222_tip_diameter_in_spatial_unit(options), 1e-12)
    spacing = max(_v222_nominal_spacing(options), diameter * 0.005)
    gx, gy = _v222_grid_axes(df, options, physical_step=spacing)
    xx, yy = np.meshgrid(gx, gy)
    pts = df[["x", "y"]].to_numpy(float)
    vals = df.v.to_numpy(float)
    sigma = diameter / 2.354820045
    cutoff = 1.75 * diameter
    flat_x = xx.ravel()
    flat_y = yy.ravel()
    out = np.full(flat_x.shape, np.nan, dtype=float)
    chunk = 12000
    denom_floor = math.exp(-0.5 * (cutoff / sigma) ** 2)
    for start in range(0, len(flat_x), chunk):
        stop = min(len(flat_x), start + chunk)
        dx = flat_x[start:stop, None] - pts[None, :, 0]
        dy = flat_y[start:stop, None] - pts[None, :, 1]
        dist2 = dx * dx + dy * dy
        weights = np.exp(-0.5 * dist2 / (sigma * sigma))
        weights[dist2 > cutoff * cutoff] = 0.0
        den = np.sum(weights, axis=1)
        num = weights @ vals
        good = den > max(denom_floor, 1e-12)
        block = out[start:stop]
        block[good] = num[good] / den[good]
    return gx, gy, out.reshape(xx.shape)


def _v222_surface_grid(x: np.ndarray, y: np.ndarray, values: np.ndarray, options: Mapping[str, Any]) -> Tuple[np.ndarray, np.ndarray, np.ndarray, bool, str]:
    df = pd.DataFrame({"x": x, "y": y, "v": values}).replace([np.inf, -np.inf], np.nan).dropna()
    if df.empty:
        raise ValueError("No finite spatial values are available.")
    df = df.groupby(["x", "y"], as_index=False).v.mean()
    model = str(options.get("surface_model") or "Standard interpolation")
    if model == "Origin-style smooth" and len(df) >= 3:
        gx, gy, z = _v222_origin_style_surface(df, options)
        regular = False
        descriptor = "Origin-style thin-plate smooth surface"
    elif model == "SECCM tip footprint" and len(df) >= 1:
        gx, gy, z = _v222_seccm_footprint_surface(df, options)
        regular = False
        diameter = float(options.get("tip_diameter_um", 5.0))
        overlap = float(options.get("overlap_percent", 0.0))
        spacing_um = diameter * (1.0 - overlap / 100.0)
        descriptor = f"SECCM Gaussian footprint: {diameter:g} µm tip, {overlap:g}% overlap, {spacing_um:g} µm nominal spacing"
    else:
        gx, gy, z, regular = _v216_grid_values(x, y, values, str(options.get("interpolation") or "Linear"))
        descriptor = "regular grid from measured points" if regular else f"{str(options.get('interpolation') or 'Linear').lower()} interpolation"
    if bool(options.get("clip_overshoot", True)) and np.asarray(z).size:
        finite = df.v.to_numpy(float)
        lo, hi = float(np.nanmin(finite)), float(np.nanmax(finite))
        z = np.clip(np.asarray(z, dtype=float), lo, hi)
    return gx, gy, np.asarray(z, dtype=float), regular, descriptor


def _v222_color_range(values: Any, options: Mapping[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Return stable robust, full-data, or manual color limits."""
    vals = np.asarray(values, dtype=float).reshape(-1)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return None, None
    opts = _v216_options(options)
    mode = str(opts.get("range_mode") or "Robust (2–98%)")
    if mode.startswith("Robust") and vals.size >= 3:
        lo, hi = np.nanpercentile(vals, [2.0, 98.0])
    else:
        lo, hi = float(np.nanmin(vals)), float(np.nanmax(vals))
    if mode.startswith("Manual"):
        try: lo = float(opts.get("range_min"))
        except Exception: pass
        try: hi = float(opts.get("range_max"))
        except Exception: pass
    lo, hi = float(lo), float(hi)
    if not math.isfinite(lo) or not math.isfinite(hi):
        return None, None
    if hi <= lo:
        pad = max(abs(lo), 1.0) * 1e-6
        lo, hi = lo - pad, hi + pad
    return lo, hi


def _v222_nice_contour_spec(cmin: Optional[float], cmax: Optional[float], target: int) -> Dict[str, float]:
    if cmin is None or cmax is None or not math.isfinite(cmin) or not math.isfinite(cmax):
        return {}
    if cmax <= cmin:
        pad = max(abs(cmin), 1.0) * 1e-6
        cmin, cmax = cmin - pad, cmax + pad
    target = max(3, min(60, int(target)))
    try:
        from matplotlib.ticker import MaxNLocator
        ticks = np.asarray(MaxNLocator(nbins=target, steps=[1, 2, 2.5, 5, 10], min_n_ticks=3).tick_values(cmin, cmax), dtype=float)
        ticks = ticks[np.isfinite(ticks)]
        ticks = ticks[(ticks >= cmin - 1e-12 * max(1.0, abs(cmin))) & (ticks <= cmax + 1e-12 * max(1.0, abs(cmax)))]
        if len(ticks) >= 2:
            diffs = np.diff(ticks)
            size = float(np.nanmedian(diffs[diffs > 0]))
            if size > 0:
                return {"start": float(ticks[0]), "end": float(ticks[-1]), "size": size}
    except Exception:
        pass
    return {"start": float(cmin), "end": float(cmax), "size": float((cmax - cmin) / target)}

def _v216_record_coord_m(record: Mapping[str, Any], axis: str) -> Optional[float]:
    for key, factor in (
        (f"{axis}_m", 1.0),
        (f"{axis}_um", 1e-6),
        (f"{axis}_µm", 1e-6),
        (f"{axis}_mm", 1e-3),
        (f"{axis}_nm", 1e-9),
        (f"{axis}_position", 1.0),
        (f"{axis}_coord", 1.0),
    ):
        if key in record:
            value = _safe_float(record.get(key))
            if value is not None:
                return value * factor
    return None


def _v216_column_length_to_m(value: float, column: Optional[str]) -> float:
    n = _norm(column)
    if "nanometer" in n or n.endswith("nm"):
        return float(value) * 1e-9
    if "micrometer" in n or "micron" in n or n.endswith("um"):
        return float(value) * 1e-6
    if "millimeter" in n or n.endswith("mm"):
        return float(value) * 1e-3
    return float(value)


def _position_lookup(browser) -> Dict[int, Tuple[Optional[float], Optional[float], Optional[float]]]:
    """Read authoritative coordinates and normalize them to metres."""
    out: Dict[int, Tuple[Optional[float], Optional[float], Optional[float]]] = {}
    for rec in list(getattr(browser, "_catalog_records", []) or []):
        if str(rec.get("technique_key") or "").upper() != "POSITION":
            continue
        pt = _safe_int(rec.get("point"), 0)
        if pt <= 0:
            continue
        x = _v216_record_coord_m(rec, "x")
        y = _v216_record_coord_m(rec, "y")
        z = _v216_record_coord_m(rec, "z")
        p = Path(str(rec.get("path") or ""))
        if p.exists() and (x is None or y is None or z is None):
            try:
                df = _read_table(p, rec, max_rows=20)
                roles = _column_roles(df)
                for key, col in (("x", roles.get("x")), ("y", roles.get("y")), ("z", roles.get("z"))):
                    vals = _numeric(df, col)
                    vals = vals[np.isfinite(vals)]
                    if len(vals):
                        value_m = _v216_column_length_to_m(float(vals[0]), col)
                        if key == "x" and x is None:
                            x = value_m
                        elif key == "y" and y is None:
                            y = value_m
                        elif key == "z" and z is None:
                            z = value_m
            except Exception:
                pass
        out[pt] = (x, y, z)
    return out


def _load_curves(browser, max_curves: int = 2500) -> List[Curve]:
    """Load selected curves while preserving cycle, method, point, and SI coordinates."""
    recs = _selected_records(browser)[:max_curves]
    pos = _position_lookup(browser)
    curves: List[Curve] = []
    for i, rec in enumerate(recs, 1):
        p = Path(str(rec.get("path") or ""))
        try:
            df = _read_table(p, rec)
            roles = _column_roles(df)
            xn, yn = _preferred_axes(rec, roles)
            if not xn or not yn:
                numeric_cols = [c for c in df.columns if pd.to_numeric(df[c], errors="coerce").notna().sum() >= 2]
                if len(numeric_cols) < 2:
                    continue
                xn, yn = numeric_cols[:2]
            x, y = _finite_xy(_numeric(df, xn), _numeric(df, yn))
            if len(x) < 2:
                continue
            pt = _safe_int(rec.get("point"), i)
            xc = _v216_record_coord_m(rec, "x")
            yc = _v216_record_coord_m(rec, "y")
            zc = _v216_record_coord_m(rec, "z")
            if pt in pos:
                px, py, pz = pos[pt]
                xc = xc if xc is not None else px
                yc = yc if yc is not None else py
                zc = zc if zc is not None else pz
            curves.append(Curve(
                path=p,
                record=dict(rec),
                x_name=str(xn),
                y_name=str(yn),
                x=x,
                y=y,
                point=pt,
                method=_safe_int(_coalesce(rec.get("method_index"), rec.get("method")), 1),
                cycle=_safe_int(_coalesce(rec.get("cycle"), rec.get("sweep")), 0),
                line=_safe_int(rec.get("line"), 0),
                x_coord=xc,
                y_coord=yc,
                z_coord=zc,
            ))
        except Exception:
            continue
    return curves


def _v216_method_token(curve: Curve) -> str:
    rec = curve.record
    text = _coalesce(
        rec.get("method_label"), rec.get("method_name"), rec.get("scan_rate_label"),
        rec.get("scan_rate"), rec.get("method_id"), rec.get("series_title"),
    )
    if text is None:
        return f"Method {curve.method:02d}"
    value = re.sub(r"\s+", " ", str(text)).strip()
    return value[:80] or f"Method {curve.method:02d}"


def _curve_groups(curves: Sequence[Curve], min_size: int = 1) -> List[Tuple[str, List[Curve]]]:
    """Separate techniques and scientific methods before multidimensional rendering."""
    grouped: Dict[Tuple[str, str, str, str], List[Curve]] = {}
    for curve in curves:
        tech = str(curve.record.get("technique_key") or "OTHER").upper()
        grouped.setdefault((tech, _v216_method_token(curve), _norm(curve.x_name), _norm(curve.y_name)), []).append(curve)
    out: List[Tuple[str, List[Curve]]] = []
    for (tech, method, _x, _y), items in grouped.items():
        if len(items) < min_size:
            continue
        label = f"{tech} — {method} — {items[0].y_name} vs {items[0].x_name}"
        out.append((label, sorted(items, key=lambda c: (c.cycle, c.point, c.line, c.path.name.lower()))))
    out.sort(key=lambda pair: (-len(pair[1]), pair[0].lower()))
    return out


def _v216_quantity_family(name: str) -> str:
    n = _norm(name)
    if any(t in n for t in ("current", "imon", "ampere")) or n in {"ia", "i"}:
        return "current"
    if any(t in n for t in ("potential", "voltage", "ewe", "emon")):
        return "potential"
    if any(t in n for t in ("zreal", "zimag", "impedance", "gammaohm", "resistance")):
        return "resistance"
    if "charge" in n or n in {"qc", "q"}:
        return "charge"
    if any(t in n for t in ("frequency", "freq")):
        return "frequency"
    if "tau" in n or "timescale" in n:
        return "time"
    if "time" in n or n in {"ts", "seconds"}:
        return "time"
    if any(t in n for t in ("wavelength", "distance", "position", "height")):
        return "length"
    return "generic"


def _v216_base_unit(family: str) -> str:
    return {
        "current": "A", "potential": "V", "resistance": "Ω", "charge": "C",
        "frequency": "Hz", "time": "s", "length": "m", "generic": "",
    }.get(family, "")


def _v216_auto_signal_unit(family: str, values: np.ndarray) -> str:
    vals = np.asarray(values, dtype=float)
    vals = np.abs(vals[np.isfinite(vals)])
    scale = float(np.nanmedian(vals[vals > 0])) if np.any(vals > 0) else 0.0
    if family == "current":
        if scale < 1e-12: return "fA"
        if scale < 1e-9: return "pA"
        if scale < 1e-6: return "nA"
        if scale < 1e-3: return "µA"
        if scale < 1.0: return "mA"
        return "A"
    if family == "resistance":
        if scale >= 1e6: return "MΩ"
        if scale >= 1e3: return "kΩ"
        return "Ω"
    if family == "frequency":
        if scale >= 1e6: return "MHz"
        if scale >= 1e3: return "kHz"
        return "Hz"
    if family == "time":
        if scale and scale < 1e-3: return "µs"
        if scale and scale < 1.0: return "ms"
        return "s"
    return _v216_base_unit(family)


_V216_UNIT_FACTORS: Dict[Tuple[str, str], float] = {
    ("current", "fA"): 1e15, ("current", "pA"): 1e12, ("current", "nA"): 1e9,
    ("current", "µA"): 1e6, ("current", "uA"): 1e6, ("current", "mA"): 1e3, ("current", "A"): 1.0,
    ("potential", "mV"): 1e3, ("potential", "V"): 1.0,
    ("resistance", "Ω"): 1.0, ("resistance", "kΩ"): 1e-3, ("resistance", "MΩ"): 1e-6,
    ("charge", "fC"): 1e15, ("charge", "pC"): 1e12, ("charge", "nC"): 1e9,
    ("charge", "µC"): 1e6, ("charge", "mC"): 1e3, ("charge", "C"): 1.0,
    ("frequency", "Hz"): 1.0, ("frequency", "kHz"): 1e-3, ("frequency", "MHz"): 1e-6,
    ("time", "µs"): 1e6, ("time", "us"): 1e6, ("time", "ms"): 1e3, ("time", "s"): 1.0,
    ("length", "nm"): 1e9, ("length", "µm"): 1e6, ("length", "um"): 1e6,
    ("length", "mm"): 1e3, ("length", "m"): 1.0,
}


def _v216_scaled(values: np.ndarray, name: str, requested: str) -> Tuple[np.ndarray, str, str]:
    family = _v216_quantity_family(name)
    req = str(requested or "Auto")
    scientific = req.lower() == "scientific"
    if req in {"", "Auto"}:
        unit = _v216_auto_signal_unit(family, np.asarray(values, dtype=float))
    elif (family, req) in _V216_UNIT_FACTORS:
        unit = req
    else:
        unit = _v216_base_unit(family)
    factor = _V216_UNIT_FACTORS.get((family, unit), 1.0)
    arr = np.asarray(values, dtype=float) * factor
    return arr, unit, ".3e" if scientific else ".6g"


def _v216_spatial_scaled(values_m: np.ndarray, requested: str) -> Tuple[np.ndarray, str, str]:
    req = str(requested or "µm")
    scientific = req.lower() == "scientific"
    unit = "m" if scientific else (req if req in {"nm", "µm", "um", "mm", "m"} else "µm")
    factor = _V216_UNIT_FACTORS.get(("length", unit), 1e6)
    label = "µm" if unit == "um" else unit
    return np.asarray(values_m, dtype=float) * factor, label, ".3e" if scientific else ".6g"


def _v216_pretty_quantity(name: str) -> str:
    family = _v216_quantity_family(name)
    return {
        "current": "Current", "potential": "Potential", "resistance": "Impedance",
        "charge": "Charge", "frequency": "Frequency", "time": "Time", "length": "Position",
    }.get(family, re.sub(r"[_]+", " ", str(name)).strip().title())


def _v216_split_cv_curve(curve: Curve) -> Dict[str, Tuple[np.ndarray, np.ndarray, str]]:
    """Split one acquisition-ordered CV cycle into forward and reverse branches."""
    x, y = _finite_xy(curve.x, curve.y)
    if len(x) < 8:
        return {"forward": (x, y, "Forward scan")}
    dx = np.diff(x)
    span = max(float(np.nanmax(x) - np.nanmin(x)), 1e-15)
    nz = dx[np.abs(dx) > span * 1e-9]
    if not len(nz):
        return {"forward": (x, y, "Forward scan")}
    initial = 1 if float(np.nanmedian(nz[: max(3, min(30, len(nz) // 5 or 3))])) >= 0 else -1
    turn = int(np.nanargmax(x) if initial > 0 else np.nanargmin(x))
    if turn < max(3, int(0.08 * len(x))) or turn > min(len(x) - 4, int(0.92 * len(x))):
        signs = np.sign(dx)
        candidates: List[int] = []
        for i in range(3, len(signs) - 4):
            before = signs[max(0, i - 4):i]
            after = signs[i:min(len(signs), i + 5)]
            if np.nanmedian(before) * initial > 0 and np.nanmedian(after) * initial < 0:
                candidates.append(i + 1)
        if candidates:
            turn = candidates[0]
    if turn <= 2 or turn >= len(x) - 3:
        return {"forward": (x, y, "Forward scan")}
    f_dir = "increasing potential" if x[turn] >= x[0] else "decreasing potential"
    r_dir = "decreasing potential" if x[-1] <= x[turn] else "increasing potential"
    return {
        "forward": (x[:turn + 1], y[:turn + 1], f"Forward scan ({f_dir})"),
        "reverse": (x[turn:], y[turn:], f"Reverse / return scan ({r_dir})"),
    }


def _v216_branch_cube(curves: Sequence[Curve], branch: str, max_bands: int = 64) -> Tuple[np.ndarray, np.ndarray, List[Curve], str]:
    segments: List[Tuple[Curve, np.ndarray, np.ndarray, str]] = []
    mins: List[float] = []
    maxs: List[float] = []
    for curve in curves:
        data = _v216_split_cv_curve(curve).get(branch)
        if not data:
            continue
        x, y, label = data
        x, y = _finite_xy(x, y)
        if len(x) < 3:
            continue
        order = np.argsort(x)
        xs, idx = np.unique(x[order], return_index=True)
        ys = y[order][idx]
        if len(xs) < 3:
            continue
        segments.append((curve, xs, ys, label))
        mins.append(float(xs.min())); maxs.append(float(xs.max()))
    if not segments:
        raise ValueError(f"No compatible {branch} CV branches were found.")
    lo, hi = max(mins), min(maxs)
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        lo, hi = min(mins), max(maxs)
    direction_increasing = "increasing" in segments[0][3].lower()
    grid_asc = np.linspace(lo, hi, max_bands)
    matrix = np.vstack([np.interp(grid_asc, xs, ys) for _, xs, ys, _ in segments])
    grid = grid_asc if direction_increasing else grid_asc[::-1]
    if not direction_increasing:
        matrix = matrix[:, ::-1]
    return grid, matrix, [item[0] for item in segments], segments[0][3]


def _v216_grid_values(x: np.ndarray, y: np.ndarray, values: np.ndarray, interpolation: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray, bool]:
    """Build a contour-ready surface for regular or scattered SECCM coordinates.

    Coordinates are normalized before triangulation to avoid Qhull failures on
    elongated scan fields.  Linear/cubic interpolation falls back to nearest
    fill and then inverse-distance weighting rather than returning an empty
    grid that silently degrades the requested contour to measured points.
    """
    df = pd.DataFrame({"x": x, "y": y, "v": values}).replace([np.inf, -np.inf], np.nan).dropna()
    if df.empty:
        raise ValueError("No finite spatial values are available.")
    df = df.groupby(["x", "y"], as_index=False).v.mean()
    ux = np.sort(df.x.unique()); uy = np.sort(df.y.unique())
    regular = len(ux) >= 2 and len(uy) >= 2 and len(df) >= max(4, int(0.72 * len(ux) * len(uy)))
    method = str(interpolation or "Linear").lower()
    if regular:
        pivot = df.pivot_table(index="y", columns="x", values="v", aggfunc="mean").reindex(index=uy, columns=ux)
        z = pivot.to_numpy(float)
        if np.isnan(z).any() and method != "none":
            try:
                from scipy.interpolate import griddata
                xx, yy = np.meshgrid(ux, uy)
                fill = griddata(df[["x", "y"]].to_numpy(float), df.v.to_numpy(float), (xx, yy), method="nearest" if method == "nearest" else "linear", rescale=True)
                z = np.where(np.isfinite(z), z, fill)
            except Exception:
                pass
        return ux, uy, z, True
    if method == "none":
        return ux, uy, np.empty((0, 0)), False

    nx = max(60, min(180, int(round(math.sqrt(len(df)) * 14))))
    ny = max(60, min(180, int(round(math.sqrt(len(df)) * 14))))
    gx = np.linspace(float(df.x.min()), float(df.x.max()), nx)
    gy = np.linspace(float(df.y.min()), float(df.y.max()), ny)
    xx, yy = np.meshgrid(gx, gy)
    pts = df[["x", "y"]].to_numpy(float)
    vals = df.v.to_numpy(float)

    # Normalize each spatial axis separately. This is essential for elongated
    # SECCM rasters where X and Y can differ by orders of magnitude.
    lo = np.nanmin(pts, axis=0); span = np.nanmax(pts, axis=0) - lo
    span = np.where(np.isfinite(span) & (span > 0), span, 1.0)
    pts_n = (pts - lo) / span
    query_n = np.column_stack([((xx.ravel() - lo[0]) / span[0]), ((yy.ravel() - lo[1]) / span[1])])
    qx = query_n[:, 0].reshape(xx.shape); qy = query_n[:, 1].reshape(yy.shape)

    z = None
    requested = method if method in {"nearest", "linear", "cubic"} else "linear"
    candidates = []
    for candidate in (requested, "linear", "nearest"):
        if candidate not in candidates:
            candidates.append(candidate)
    try:
        from scipy.interpolate import griddata
        for candidate in candidates:
            if candidate == "cubic" and len(df) < 6:
                continue
            try:
                trial = griddata(pts_n, vals, (qx, qy), method=candidate)
            except Exception:
                continue
            if trial is not None and np.isfinite(trial).any():
                z = np.asarray(trial, dtype=float)
                if candidate != "nearest" and np.isnan(z).any():
                    try:
                        nearest = griddata(pts_n, vals, (qx, qy), method="nearest")
                        z = np.where(np.isfinite(z), z, nearest)
                    except Exception:
                        pass
                break
    except Exception:
        z = None

    # Deterministic inverse-distance fallback. This provides a contour-ready
    # field for sparse/irregular point clouds without inventing values outside
    # the measured hull (the caller applies the hull mask afterward).
    if z is None or not np.isfinite(z).any():
        q = query_n
        out = np.empty(len(q), dtype=float)
        chunk = 12000
        eps = 1e-14
        for i in range(0, len(q), chunk):
            block = q[i:i + chunk]
            d2 = np.sum((block[:, None, :] - pts_n[None, :, :]) ** 2, axis=2)
            nearest_idx = np.argmin(d2, axis=1)
            exact = np.min(d2, axis=1) <= eps
            weights = 1.0 / np.maximum(d2, eps)
            estimate = (weights @ vals) / np.maximum(np.sum(weights, axis=1), eps)
            estimate[exact] = vals[nearest_idx[exact]]
            out[i:i + len(block)] = estimate
        z = out.reshape(xx.shape)
    return gx, gy, np.asarray(z, dtype=float), False


def _v216_spatial_traces(
    x: np.ndarray,
    y: np.ndarray,
    values: np.ndarray,
    options: Mapping[str, Any],
    color_title: str,
    cmin: Optional[float] = None,
    cmax: Optional[float] = None,
) -> Tuple[List[Any], str]:
    import plotly.graph_objects as go
    options = _v216_options(options)
    x = np.asarray(x, dtype=float).reshape(-1)
    y = np.asarray(y, dtype=float).reshape(-1)
    values = np.asarray(values, dtype=float).reshape(-1)
    n_common = min(len(x), len(y), len(values))
    x, y, values = x[:n_common], y[:n_common], values[:n_common]
    measured_mask = np.isfinite(x) & np.isfinite(y) & np.isfinite(values)
    x, y, values = x[measured_mask], y[measured_mask], values[measured_mask]
    if not len(values):
        raise ValueError("No finite measured X/Y/value triples are available for the spatial map.")
    style = str(options.get("render_mode") or "Contour + points")
    interpolation = str(options.get("interpolation") or "Linear")
    opacity = float(options.get("opacity", 0.92))
    point_size = int(options.get("point_size", 13))
    palette = str(options.get("palette") or "Viridis")
    if bool(options.get("reverse_palette", False)):
        palette = palette + "_r"
    finite = values
    if cmin is None or cmax is None:
        auto_min, auto_max = _v222_color_range(finite, options)
        if cmin is None:
            cmin = auto_min
        if cmax is None:
            cmax = auto_max
    levels = int(options.get("contour_levels", 16))
    if bool(options.get("adaptive_contours", True)):
        # enough lines to show structure, but not so many that sparse maps look noisy
        levels = max(6, min(28, int(round(4 + 2.2 * math.sqrt(max(1, len(finite)))))))
    overlay = bool(options.get("overlay_points", True))
    marker = dict(size=point_size, color=values, colorscale=palette, cmin=cmin, cmax=cmax, colorbar=dict(title=color_title), opacity=opacity)
    point_trace = go.Scatter(
        x=x, y=y, mode="markers", marker=marker,
        customdata=np.arange(1, len(x) + 1),
        hovertemplate=f"Point %{{customdata}}<br>X=%{{x:.6g}}<br>Y=%{{y:.6g}}<br>{color_title}=%{{marker.color:.6g}}<extra></extra>",
        name=f"Measured points (n={len(x)})",
    )
    if style.lower().startswith("measured"):
        return [point_trace], "Measured points"
    gx, gy, z, regular, surface_descriptor = _v222_surface_grid(x, y, values, options)
    if z.size == 0:
        return [point_trace], "Measured points (contour unavailable without interpolation)"
    # Smooth only the interpolated field; measured values and point count remain untouched.
    smoothing = str(options.get("smoothing") or "None")
    if smoothing != "None" and str(options.get("surface_model") or "Standard interpolation") == "Standard interpolation":
        try:
            from scipy.ndimage import gaussian_filter
            sigma = {"Light": 0.7, "Medium": 1.2, "Strong": 2.0}.get(smoothing, 0.0)
            if sigma:
                valid = np.isfinite(z)
                num = gaussian_filter(np.where(valid, z, 0.0), sigma=sigma)
                den = gaussian_filter(valid.astype(float), sigma=sigma)
                z = np.where(den > 1e-8, num / den, np.nan)
        except Exception:
            pass
    if bool(options.get("mask_outside_hull", True)) and not regular and len(x) >= 3:
        try:
            from scipy.spatial import Delaunay
            xx, yy = np.meshgrid(gx, gy)
            inside = Delaunay(np.column_stack([x, y])).find_simplex(np.column_stack([xx.ravel(), yy.ravel()])) >= 0
            z = np.where(inside.reshape(xx.shape), z, np.nan)
        except Exception:
            pass
    if cmin is not None and cmax is not None and cmax <= cmin:
        pad = max(abs(float(cmin)), 1.0) * 1e-6
        cmin, cmax = float(cmin) - pad, float(cmax) + pad
    common = dict(x=gx, y=gy, z=z, colorscale=palette, zmin=cmin, zmax=cmax, opacity=opacity, colorbar=dict(title=color_title), connectgaps=False)
    width = float(options.get("contour_line_width", 1.0))
    labels = bool(options.get("contour_labels", False))
    nice = _v222_nice_contour_spec(cmin, cmax, levels)
    if style.lower().startswith("heat"):
        base = go.Heatmap(**common, hovertemplate=f"X=%{{x:.6g}}<br>Y=%{{y:.6g}}<br>{color_title}=%{{z:.6g}}<extra></extra>", name="Heatmap")
        effective = "Heatmap"
    elif style.lower().startswith("contour lines"):
        contour_cfg = dict(coloring="lines", showlabels=labels, **nice)
        base = go.Contour(**common, ncontours=levels, contours=contour_cfg, line=dict(width=width), hovertemplate=f"X=%{{x:.6g}}<br>Y=%{{y:.6g}}<br>{color_title}=%{{z:.6g}}<extra></extra>", name="Contour lines")
        effective = "Contour lines"
    else:
        contour_cfg = dict(coloring="heatmap", showlabels=labels, **nice)
        base = go.Contour(**common, ncontours=levels, contours=contour_cfg, line=dict(width=width), hovertemplate=f"X=%{{x:.6g}}<br>Y=%{{y:.6g}}<br>{color_title}=%{{z:.6g}}<extra></extra>", name="Filled contour")
        effective = "Filled contour"
    traces: List[Any] = [base]
    if overlay or "points" in style.lower():
        traces.append(go.Scatter(
            x=x, y=y, mode="markers", name=f"Measured locations (n={len(x)})",
            marker=dict(size=max(4, point_size - 3), symbol="circle-open", line=dict(width=1.4), opacity=min(1.0, opacity + 0.05)),
            customdata=np.arange(1, len(x) + 1),
            hovertemplate="Point %{customdata}<br>X=%{x:.6g}<br>Y=%{y:.6g}<extra></extra>",
        ))
        effective += f" + measured points (n={len(x)})"
    suffix = surface_descriptor
    if bool(options.get("mask_outside_hull", True)) and not regular and len(x) >= 3:
        suffix += "; hull masked"
    return traces, effective + f" ({suffix})"


def _v216_background_layout(options: Mapping[str, Any], x: np.ndarray, y: np.ndarray) -> List[Dict[str, Any]]:
    if not bool(options.get("background_enabled")):
        return []
    path = Path(str(options.get("background_path") or ""))
    if not path.exists() or not path.is_file():
        return []
    try:
        from PIL import Image
        with Image.open(path) as image:
            image = image.convert("RGBA")
            image.thumbnail((2400, 2400))
            buffer = _v216_io.BytesIO()
            image.save(buffer, format="PNG", optimize=True)
        source = "data:image/png;base64," + _v216_base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return []
    finite_x = np.asarray(x, dtype=float); finite_x = finite_x[np.isfinite(finite_x)]
    finite_y = np.asarray(y, dtype=float); finite_y = finite_y[np.isfinite(finite_y)]
    if not len(finite_x) or not len(finite_y):
        return []
    defaults = [float(finite_x.min()), float(finite_x.max()), float(finite_y.min()), float(finite_y.max())]
    manual: List[float] = []
    for key, default in zip(("background_xmin", "background_xmax", "background_ymin", "background_ymax"), defaults):
        try:
            value = float(str(options.get(key, "")).strip())
            manual.append(value if math.isfinite(value) else default)
        except Exception:
            manual.append(default)
    xmin, xmax, ymin, ymax = manual
    if xmax <= xmin or ymax <= ymin:
        xmin, xmax, ymin, ymax = defaults
    fit = str(options.get("background_fit") or "Fit to data extent").lower()
    sizing = "contain" if "preserve" in fit else "stretch"
    return [dict(
        source=source, xref="x", yref="y", x=xmin, y=ymax,
        sizex=xmax - xmin, sizey=ymax - ymin, xanchor="left", yanchor="top",
        sizing=sizing, opacity=float(options.get("background_opacity", 0.35)), layer="below",
    )]


def _v216_write_figure(fig: Any, out: Path, metadata: Mapping[str, Any]) -> None:
    fig.write_html(out, include_plotlyjs="directory", full_html=True, auto_open=False, config={"responsive": True, "scrollZoom": True, "displaylogo": False, "modeBarButtonsToAdd": ["select2d", "lasso2d"], "doubleClick": "reset+autosize"})
    out.with_suffix(".visualization.json").write_text(json.dumps(dict(metadata), indent=2, default=str), encoding="utf-8")


def _v216_cv_cycle_view(browser: Any, curves: Sequence[Curve], group_label: str, suffix: str, options: Mapping[str, Any]) -> Dict[str, Any]:
    import plotly.graph_objects as go
    cycle = int(curves[0].cycle or 0)
    branch_outputs: Dict[str, Tuple[np.ndarray, np.ndarray, List[Curve], str]] = {}
    for branch in ("forward", "reverse"):
        try:
            branch_outputs[branch] = _v216_branch_cube(curves, branch, max_bands=64)
        except Exception:
            pass
    if not branch_outputs:
        raise ValueError("No forward or reverse CV scan branches could be reconstructed.")
    all_signals = np.concatenate([item[1].ravel() for item in branch_outputs.values()])
    scaled_all, signal_unit, _ = _v216_scaled(all_signals, curves[0].y_name, str(options.get("signal_unit", "Auto")))
    signal_factor = 1.0
    finite_base = all_signals[np.isfinite(all_signals)]
    finite_scaled = scaled_all[np.isfinite(scaled_all)]
    if len(finite_base) and len(finite_scaled):
        idx = np.flatnonzero(np.abs(finite_base) > 0)
        if len(idx): signal_factor = float(finite_scaled[idx[0]] / finite_base[idx[0]])
    if str(options.get("range_scope") or "Lock across cube") == "Per frame":
        cmin = cmax = None
    else:
        cmin, cmax = _v222_color_range(scaled_all, options)
    spatial_unit = str(options.get("spatial_unit", "µm"))
    first_curves = next(iter(branch_outputs.values()))[2]
    x_m = np.array([np.nan if c.x_coord is None else c.x_coord for c in first_curves], dtype=float)
    y_m = np.array([np.nan if c.y_coord is None else c.y_coord for c in first_curves], dtype=float)
    coords_ok = np.sum(np.isfinite(x_m) & np.isfinite(y_m)) >= max(4, int(0.7 * len(first_curves)))
    x_disp, spatial_label, spatial_fmt = _v216_spatial_scaled(x_m, spatial_unit)
    y_disp, _, _ = _v216_spatial_scaled(y_m, spatial_unit)
    spectral_values = np.concatenate([item[0] for item in branch_outputs.values()])
    _, spectral_label, _ = _v216_scaled(spectral_values, curves[0].x_name, str(options.get("spectral_unit", "Auto")))
    frames: List[Any] = []
    branch_steps: Dict[str, List[Dict[str, Any]]] = {"forward": [], "reverse": []}
    first_data: Optional[List[Any]] = None
    effective_modes: List[str] = []
    for branch in ("forward", "reverse"):
        if branch not in branch_outputs:
            continue
        grid, matrix, branch_curves, branch_label = branch_outputs[branch]
        grid_disp, branch_axis_unit, _ = _v216_scaled(grid, curves[0].x_name, str(options.get("spectral_unit", "Auto")))
        if branch_axis_unit:
            spectral_label = branch_axis_unit
        # Branch rows can be a strict subset. Rebuild coordinate arrays in the same order.
        bx_m = np.array([np.nan if c.x_coord is None else c.x_coord for c in branch_curves], dtype=float)
        by_m = np.array([np.nan if c.y_coord is None else c.y_coord for c in branch_curves], dtype=float)
        bx, spatial_label, spatial_fmt = _v216_spatial_scaled(bx_m, spatial_unit)
        by, _, _ = _v216_spatial_scaled(by_m, spatial_unit)
        for j, potential in enumerate(grid_disp):
            values = matrix[:, j] * signal_factor
            frame_name = f"c{cycle:02d}_{branch}_{j:03d}"
            title = f"FaradaIQ {VERSION}: {group_label} | Cycle {cycle or 1:02d} | {branch_label} | {_v216_pretty_quantity(curves[0].x_name)} = {potential:.6g} {spectral_label}"
            if coords_ok:
                traces, effective = _v216_spatial_traces(bx, by, values, options, f"{_v216_pretty_quantity(curves[0].y_name)} / {signal_unit or 'SI'}", cmin, cmax)
                effective_modes.append(effective)
            else:
                traces = [go.Scatter(
                    x=[c.point for c in branch_curves], y=values, mode="lines+markers",
                    marker=dict(
                        size=int(options.get("point_size", 13)), color=values,
                        colorscale=(str(options.get("palette") or "Viridis") + ("_r" if bool(options.get("reverse_palette", False)) else "")),
                        cmin=cmin, cmax=cmax,
                        colorbar=dict(title=f"{_v216_pretty_quantity(curves[0].y_name)} / {signal_unit or 'SI'}")),
                    text=[f"Point {c.point}" for c in branch_curves],
                    hovertemplate="%{text}<br>Value=%{y:.6g}<extra></extra>", name="Point-dependent slice",
                )]
                effective_modes.append("Point-dependent line")
            if first_data is None:
                first_data = traces
            frames.append(go.Frame(data=traces, name=frame_name, layout=go.Layout(title=title)))
            # Keep the slider readable.  Forward and reverse scans use separate
            # sliders, and only evenly spaced potential labels are printed.
            # The exact selected potential remains visible in the plot title.
            n_grid = max(1, len(grid_disp))
            label_slots = {int(round(k * (n_grid - 1) / 8.0)) for k in range(9)}
            tick_label = f"{potential:.4g}" if j in label_slots else ""
            branch_steps[branch].append(dict(
                method="animate", label=tick_label,
                args=[[frame_name], {"mode": "immediate", "frame": {"duration": 0, "redraw": True}, "transition": {"duration": 0}}],
            ))
    if first_data is None:
        raise ValueError("No CV hyperspectral frames were generated.")
    fig = go.Figure(data=first_data, frames=frames)
    first_branch = "forward" if "forward" in branch_outputs else next(iter(branch_outputs))
    first_name = f"c{cycle:02d}_{first_branch}_000"
    reverse_name = f"c{cycle:02d}_reverse_000" if "reverse" in branch_outputs else first_name
    layout_images = _v216_background_layout(options, x_disp, y_disp) if coords_ok else []
    fig.update_layout(
        title=frames[0].layout.title.text if frames else f"FaradaIQ {VERSION}: {group_label}",
        xaxis_title=f"X position / {spatial_label}" if coords_ok else "Point / acquisition position",
        yaxis_title=f"Y position / {spatial_label}" if coords_ok else f"{_v216_pretty_quantity(curves[0].y_name)} / {signal_unit or 'SI'}",
        images=layout_images,
        sliders=[
            dict(
                active=0, x=0.05, len=0.90, y=-0.08,
                name="Forward scan potential",
                currentvalue={"visible": False},
                pad={"t": 24},
                steps=branch_steps.get("forward", []),
                visible=bool(branch_steps.get("forward")),
            ),
            dict(
                active=0, x=0.05, len=0.90, y=-0.20,
                name="Reverse scan potential",
                currentvalue={"visible": False},
                pad={"t": 24},
                steps=branch_steps.get("reverse", []),
                visible=bool(branch_steps.get("reverse")),
            ),
        ],
        updatemenus=[dict(
            type="buttons", direction="right", x=0.0, y=1.16,
            buttons=[
                dict(label="Forward scan", method="animate", args=[[first_name], {"mode": "immediate", "frame": {"duration": 0, "redraw": True}, "transition": {"duration": 0}}]),
                dict(label="Reverse / return scan", method="animate", args=[[reverse_name], {"mode": "immediate", "frame": {"duration": 0, "redraw": True}, "transition": {"duration": 0}}]),
            ],
        )],
        meta={"branch_codes": ["FWD", "REV"], "slider_layout": "separate_forward_reverse_sparse_labels"},
        annotations=[
            dict(
                text="Forward scan potential", xref="paper", yref="paper",
                x=0.0, y=-0.075, xanchor="left", showarrow=False,
            ),
            dict(
                text="Reverse / return scan potential", xref="paper", yref="paper",
                x=0.0, y=-0.195, xanchor="left", showarrow=False,
            ),
            dict(
                text="Exact selected potential and branch are shown in the title. Use the separate scan sliders below.",
                xref="paper", yref="paper", x=0.5, y=1.08, showarrow=False,
            ),
        ],
        margin=dict(l=70, r=50, b=175, t=125),
    )
    if coords_ok:
        if bool(options.get("lock_xy_aspect", False)):
            fig.update_xaxes(tickformat=spatial_fmt, scaleanchor="y", scaleratio=1)
        else:
            fig.update_xaxes(tickformat=spatial_fmt)
        fig.update_yaxes(tickformat=spatial_fmt)
    out_dir = _view_dir(browser); stamp = time.strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_hyperspectral_{_slug(suffix)}_cycle{cycle or 1:02d}_{stamp}.html"
    meta = {
        "schema": "faradaiq-hyperspectral-cv-branch-cube-v216",
        "version": VERSION,
        "group_label": group_label,
        "cycle": cycle or 1,
        "branches": {k: {"label": v[3], "n_bands": len(v[0]), "potential_min": float(np.nanmin(v[0])), "potential_max": float(np.nanmax(v[0]))} for k, v in branch_outputs.items()},
        "total_slider_frames": len(frames),
        "slider_layout": "separate_forward_reverse_sparse_labels",
        "coordinates_available": bool(coords_ok),
        "spatial_unit": spatial_label,
        "signal_unit": signal_unit,
        "spectral_unit": spectral_label,
        "effective_render_modes": sorted(set(effective_modes)),
        "background_loaded": bool(layout_images),
        "options": dict(options),
        "source_files": [c.path.name for c in curves],
    }
    _v216_write_figure(fig, out, meta)
    npz = out.with_suffix(".cube.npz")
    payload: Dict[str, Any] = {
        "points": np.array([c.point for c in first_curves]),
        "x_coordinates_m": x_m, "y_coordinates_m": y_m,
        "cycle": np.array([cycle or 1]),
    }
    for branch, (grid, matrix, _curves, _label) in branch_outputs.items():
        payload[f"{branch}_potential_V"] = grid
        payload[f"{branch}_values_SI"] = matrix
    np.savez_compressed(npz, **payload)
    return {"html": str(out), "cube": str(npz), "metadata": str(out.with_suffix('.visualization.json')), "engine": "Plotly CV branch-aware hyperspectral", "label": f"Cycle {cycle or 1:02d}", **meta}


def _v216_generic_hyperspectral_view(browser: Any, curves: Sequence[Curve], group_label: str, suffix: str, options: Mapping[str, Any]) -> Dict[str, Any]:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    grid, matrix, curves = _common_grid(curves, max_bands=70)
    x_m = np.array([np.nan if c.x_coord is None else c.x_coord for c in curves], dtype=float)
    y_m = np.array([np.nan if c.y_coord is None else c.y_coord for c in curves], dtype=float)
    coords_ok = np.sum(np.isfinite(x_m) & np.isfinite(y_m)) >= max(4, int(0.7 * len(curves)))
    x_disp, spatial_label, spatial_fmt = _v216_spatial_scaled(x_m, str(options.get("spatial_unit", "µm")))
    y_disp, _, _ = _v216_spatial_scaled(y_m, str(options.get("spatial_unit", "µm")))
    grid_disp, spectral_unit, _ = _v216_scaled(grid, curves[0].x_name, str(options.get("spectral_unit", "Auto")))
    matrix_disp, signal_unit, _ = _v216_scaled(matrix, curves[0].y_name, str(options.get("signal_unit", "Auto")))
    if str(options.get("range_scope") or "Lock across cube") == "Per frame":
        cmin = cmax = None
    else:
        cmin, cmax = _v222_color_range(matrix_disp, options)
    frames: List[Any] = []
    steps: List[Dict[str, Any]] = []
    effective_modes: List[str] = []
    if coords_ok:
        first_data: Optional[List[Any]] = None
        for j, axis_value in enumerate(grid_disp):
            traces, effective = _v216_spatial_traces(x_disp, y_disp, matrix_disp[:, j], options, f"{_v216_pretty_quantity(curves[0].y_name)} / {signal_unit or 'SI'}", cmin, cmax)
            effective_modes.append(effective)
            name = f"band_{j:03d}"
            title = f"FaradaIQ {VERSION}: {group_label} | {_v216_pretty_quantity(curves[0].x_name)} = {axis_value:.6g} {spectral_unit}"
            if first_data is None: first_data = traces
            frames.append(go.Frame(data=traces, name=name, layout=go.Layout(title=title)))
            steps.append(dict(method="animate", label=f"{axis_value:.4g} {spectral_unit}", args=[[name], {"mode": "immediate", "frame": {"duration": 0, "redraw": True}, "transition": {"duration": 0}}]))
        fig = go.Figure(data=first_data or [], frames=frames)
        images = _v216_background_layout(options, x_disp, y_disp)
        fig.update_layout(
            title=frames[0].layout.title.text if frames else group_label,
            xaxis_title=f"X position / {spatial_label}", yaxis_title=f"Y position / {spatial_label}",
            images=images,
            sliders=[dict(active=0, currentvalue={"prefix": f"{_v216_pretty_quantity(curves[0].x_name)}: "}, pad={"t": 45}, steps=steps)],
            margin=dict(l=70, r=50, b=90, t=95),
        )
        if bool(options.get("lock_xy_aspect", False)):
            fig.update_xaxes(tickformat=spatial_fmt, scaleanchor="y", scaleratio=1)
        else:
            fig.update_xaxes(tickformat=spatial_fmt)
        fig.update_yaxes(tickformat=spatial_fmt)
    else:
        points = np.array([c.point for c in curves], dtype=float)
        fig = make_subplots(rows=1, cols=2, subplot_titles=("Point × spectral-axis cube", "Selected spectral slice"), column_widths=[0.68, 0.32])
        fig.add_trace(go.Heatmap(z=matrix_disp, x=grid_disp, y=points, colorbar=dict(title=f"{_v216_pretty_quantity(curves[0].y_name)} / {signal_unit or 'SI'}")), row=1, col=1)
        fig.add_trace(go.Scatter(x=points, y=matrix_disp[:, 0], mode="lines+markers", name="slice"), row=1, col=2)
        fig.frames = [go.Frame(data=[go.Heatmap(z=matrix_disp), go.Scatter(x=points, y=matrix_disp[:, j])], name=f"band_{j:03d}", layout=go.Layout(title=f"FaradaIQ {VERSION}: {group_label} | {_v216_pretty_quantity(curves[0].x_name)} = {grid_disp[j]:.6g} {spectral_unit}")) for j in range(len(grid_disp))]
        steps = [dict(method="animate", label=f"{v:.4g} {spectral_unit}", args=[[f"band_{j:03d}"], {"mode": "immediate", "frame": {"duration": 0, "redraw": True}, "transition": {"duration": 0}}]) for j, v in enumerate(grid_disp)]
        fig.update_xaxes(title_text=f"{_v216_pretty_quantity(curves[0].x_name)} / {spectral_unit}", row=1, col=1)
        fig.update_yaxes(title_text="Point", row=1, col=1)
        fig.update_xaxes(title_text="Point", row=1, col=2)
        fig.update_yaxes(title_text=f"{_v216_pretty_quantity(curves[0].y_name)} / {signal_unit or 'SI'}", row=1, col=2)
        fig.update_layout(title=f"FaradaIQ {VERSION}: {group_label}", sliders=[dict(active=0, currentvalue={"prefix": f"Slice {_v216_pretty_quantity(curves[0].x_name)}: "}, pad={"t": 45}, steps=steps)])
        images = []
    out_dir = _view_dir(browser); stamp = time.strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_hyperspectral_{_slug(suffix)}_{stamp}.html"
    meta = {
        "schema": "faradaiq-hyperspectral-cube-v216", "version": VERSION, "group_label": group_label,
        "technique": str(curves[0].record.get("technique_key") or "OTHER"),
        "spectral_axis_name": curves[0].x_name, "signal_name": curves[0].y_name,
        "n_points": len(curves), "n_unique_points": len(set(c.point for c in curves)), "n_bands": len(grid),
        "coordinates_available": bool(coords_ok), "regular_xy_grid": bool(coords_ok and _regular_grid(pd.DataFrame({"x_coord": x_m, "y_coord": y_m}))), "spatial_unit": spatial_label, "signal_unit": signal_unit,
        "spectral_unit": spectral_unit, "effective_render_modes": sorted(set(effective_modes)),
        "background_loaded": bool(images), "options": dict(options), "source_files": [c.path.name for c in curves],
    }
    _v216_write_figure(fig, out, meta)
    npz = out.with_suffix(".cube.npz")
    np.savez_compressed(npz, spectral_axis=grid, values=matrix, spectral_axis_SI=grid, values_SI=matrix, points=np.array([c.point for c in curves]), x_coordinates=np.array(x_m), y_coordinates=np.array(y_m), x_coordinates_m=x_m, y_coordinates_m=y_m, methods=np.array([c.method for c in curves]), cycles=np.array([c.cycle for c in curves]))
    return {"html": str(out), "engine": "Plotly hyperspectral", "cube": str(npz), "metadata": str(out.with_suffix('.visualization.json')), "label": group_label, **meta}


def _make_hyperspectral_group(browser, curves: Sequence[Curve], group_label: str, suffix: str, options: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    opts = _v216_options(options)
    tech = str(curves[0].record.get("technique_key") or "").upper() if curves else ""
    is_cv = tech in {"CV", "LSV", "FSCV", "CV/LSV"} and _v216_quantity_family(curves[0].x_name) == "potential"
    if is_cv:
        cycle_groups: Dict[int, List[Curve]] = {}
        for curve in curves:
            cycle_groups.setdefault(int(curve.cycle or 1), []).append(curve)
        outputs = [_v216_cv_cycle_view(browser, items, group_label, f"{suffix}_c{cycle:02d}", opts) for cycle, items in sorted(cycle_groups.items())]
        if len(outputs) == 1:
            return outputs[0]
        out_dir = _view_dir(browser); stamp = time.strftime("%Y%m%d_%H%M%S")
        master = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_hyperspectral_{_slug(suffix)}_cycles_{stamp}.html"
        _write_master_index(master, f"FaradaIQ {VERSION}: {group_label} branch-aware cycles", outputs)
        meta_path = master.with_suffix(".cube_index.json")
        meta_path.write_text(json.dumps({"schema": "faradaiq-hyperspectral-cv-cycle-index-v216", "version": VERSION, "group_label": group_label, "cycle_views": outputs, "options": opts}, indent=2, default=str), encoding="utf-8")
        return {**outputs[0], "html": str(master), "engine": "Plotly CV branch-aware multi-cycle hyperspectral", "label": group_label, "cycle_views": outputs, "cycle_count": len(outputs), "cube_index": str(meta_path)}
    return _v216_generic_hyperspectral_view(browser, curves, group_label, suffix, opts)


def make_hyperspectral(browser, metric: str = "Auto", options: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    curves = _load_curves(browser)
    if len(curves) < 2:
        raise ValueError("Hyperspectral/stack mode needs at least two compatible point or position curves.")
    groups = _curve_groups(curves, min_size=2)
    if not groups:
        raise ValueError("No compatible repeated technique curves were found for hyperspectral mode.")
    opts = _v216_options(options)
    outputs = [_make_hyperspectral_group(browser, items, label, f"g{i + 1:02d}", opts) for i, (label, items) in enumerate(groups)]
    if len(outputs) == 1:
        return outputs[0]
    out_dir = _view_dir(browser); stamp = time.strftime("%Y%m%d_%H%M%S")
    master = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_hyperspectral_multitechnique_{stamp}.html"
    _write_master_index(master, f"FaradaIQ {VERSION} multi-technique hyperspectral viewer", outputs)
    index = master.with_suffix(".cube_index.json")
    index.write_text(json.dumps({"schema": "faradaiq-hyperspectral-multitechnique-index-v216", "version": VERSION, "groups": outputs, "options": opts}, indent=2, default=str), encoding="utf-8")
    return {**outputs[0], "html": str(master), "engine": "Plotly multi-technique hyperspectral", "groups": outputs, "group_count": len(outputs), "cube_index": str(index), "curves": len(curves)}


def _v216_metric_scale(points: pd.DataFrame, metric: str, signal_name: str, requested: str) -> Tuple[pd.DataFrame, str]:
    out = points.copy()
    if metric not in out:
        return out, ""
    # Integrated values carry compound units and remain in native SI unless explicitly scientific.
    if metric == "integral":
        return out, "SI integral"
    scaled, unit, _ = _v216_scaled(pd.to_numeric(out[metric], errors="coerce").to_numpy(float), signal_name, requested)
    out[metric] = scaled
    return out, unit


def _v296_metric_horizontal_axis(points: pd.DataFrame, opts: Mapping[str, Any]) -> Tuple[pd.DataFrame, str, str]:
    """Return point metrics with a selected Point/X/Y/Distance display axis.

    X/Y/Distance use physical HEKA Takedata coordinates in metres internally and
    are converted only for display. Auto uses a useful line-scan axis; a true 2-D
    map is handled separately by the caller.
    """
    out = points.copy()
    try:
        from .spatial_points_v296 import dataframe_with_scan_distance, auto_axis_for_records, scale_m
        out = dataframe_with_scan_distance(out)
        requested = str(opts.get("point_axis") or "Auto").title()
        axis = requested
        if axis == "Auto":
            axis = auto_axis_for_records(out.to_dict("records"))
        if axis not in {"Point", "X", "Y", "Distance"}:
            axis = "Point"
        if axis == "Point":
            out["spatial_axis_display"] = pd.to_numeric(out["point"], errors="coerce")
            return out, axis, "Point / acquisition position"
        key = {"X": "x_position_m", "Y": "y_position_m", "Distance": "scan_distance_m"}[axis]
        if key not in out or pd.to_numeric(out[key], errors="coerce").notna().sum() == 0:
            out["spatial_axis_display"] = pd.to_numeric(out["point"], errors="coerce")
            return out, "Point", "Point / acquisition position"
        vals = pd.to_numeric(out[key], errors="coerce").to_numpy(float)
        scaled, unit = scale_m(vals, str(opts.get("spatial_unit") or "µm"))
        out["spatial_axis_display"] = scaled
        label = f"{axis} position / {unit}" if axis in {"X", "Y"} else f"Cumulative scan distance / {unit}"
        return out, axis, label
    except Exception:
        out["spatial_axis_display"] = pd.to_numeric(out.get("point"), errors="coerce")
        return out, "Point", "Point / acquisition position"


_v294_metric_horizontal_axis = _v296_metric_horizontal_axis  # backward-compatible alias


def _make_altair_group(browser, curves: Sequence[Curve], metric: str, group_label: str, suffix: str, options: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    import altair as alt
    opts = _v216_options(options)
    points = _point_table(curves)
    metric = _choose_metric(points, metric)
    points, signal_unit = _v216_metric_scale(points, metric, curves[0].y_name, str(opts.get("signal_unit", "Auto")))
    points, selected_point_axis, selected_point_axis_label = _v296_metric_horizontal_axis(points, opts)
    long = _curve_long(curves)
    long_values, trace_unit, _ = _v216_scaled(long.value.to_numpy(float), curves[0].y_name, str(opts.get("signal_unit", "Auto")))
    long = long.copy(); long["value"] = long_values
    out_dir = _view_dir(browser); stamp = time.strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_interactive_2D_{_slug(suffix)}_{stamp}.html"
    csv_path = out.with_suffix(".point_metrics.csv"); points.to_csv(csv_path, index=False)
    alt.data_transformers.disable_max_rows()
    selection = alt.selection_point(fields=["point"], on="click", empty=True, name="SelectedPoint")
    tooltip = [alt.Tooltip("point:Q"), alt.Tooltip("method:Q"), alt.Tooltip(f"{metric}:Q", format=".5g")]
    spatial = points.dropna(subset=["x_coord", "y_coord"]) if {"x_coord", "y_coord"}.issubset(points.columns) else pd.DataFrame()
    is_true_2d = len(spatial) >= 4 and spatial.x_coord.nunique() >= 2 and spatial.y_coord.nunique() >= 2 and _regular_grid(spatial)
    # Auto preserves a true 2-D X-Y map. Any explicit axis request produces the
    # requested metric-vs-coordinate view, even for raster datasets. For 1-D
    # line scans Auto selects X, Y, or cumulative distance from Takedata.
    if is_true_2d and str(opts.get("point_axis") or "Auto").title() == "Auto":
        xs, spatial_unit, _ = _v216_spatial_scaled(spatial.x_coord.to_numpy(float), str(opts.get("spatial_unit", "µm")))
        ys, _, _ = _v216_spatial_scaled(spatial.y_coord.to_numpy(float), str(opts.get("spatial_unit", "µm")))
        spatial = spatial.copy(); spatial["x_display"] = xs; spatial["y_display"] = ys
        map_chart = alt.Chart(spatial).mark_square(size=max(80, int(opts.get("point_size", 13)) ** 2 * 2), opacity=float(opts.get("opacity", 0.92))).encode(
            x=alt.X("x_display:Q", title=f"X position / {spatial_unit}"), y=alt.Y("y_display:Q", title=f"Y position / {spatial_unit}"),
            color=alt.Color(f"{metric}:Q", title=f"{metric.replace('_', ' ')} / {signal_unit}" if signal_unit else metric.replace("_", " ")),
            tooltip=tooltip + [alt.Tooltip("x_display:Q"), alt.Tooltip("y_display:Q")],
            opacity=alt.condition(selection, alt.value(float(opts.get("opacity", 0.92))), alt.value(0.25)),
        ).add_params(selection).properties(title=f"Interactive spatial map: {metric}", width=520, height=420)
    else:
        axis_tooltip = [alt.Tooltip("spatial_axis_display:Q", title=selected_point_axis_label, format=".6g")]
        map_chart = alt.Chart(points).mark_circle(size=max(70, int(opts.get("point_size", 13)) ** 2), opacity=float(opts.get("opacity", 0.92))).encode(
            x=alt.X("spatial_axis_display:Q", title=selected_point_axis_label),
            y=alt.Y(f"{metric}:Q", title=f"{metric.replace('_', ' ')} / {signal_unit}" if signal_unit else metric.replace("_", " ")),
            color=alt.Color("method:N", title="Method"), tooltip=tooltip + axis_tooltip,
            opacity=alt.condition(selection, alt.value(float(opts.get("opacity", 0.92))), alt.value(0.25)),
        ).add_params(selection).properties(title=f"{metric.replace('_', ' ')} vs {selected_point_axis_label}", width=520, height=420)
    line_chart = alt.Chart(long).mark_line().encode(
        x=alt.X("axis:Q", title=curves[0].x_name),
        y=alt.Y("value:Q", title=f"{_v216_pretty_quantity(curves[0].y_name)} / {trace_unit}" if trace_unit else curves[0].y_name),
        color=alt.Color("label:N", legend=None),
        tooltip=["label:N", alt.Tooltip("axis:Q", format=".6g"), alt.Tooltip("value:Q", format=".6g")],
    ).transform_filter(selection).properties(title="Selected point trace(s)", width=600, height=420).interactive()
    chart = (map_chart | line_chart).resolve_scale(color="independent").properties(title=f"FaradaIQ {VERSION}: {group_label}")
    try:
        chart.save(out, inline=True)
    except Exception:
        chart.save(out)
    spec = out.with_suffix(".vegalite.json"); spec.write_text(chart.to_json(indent=2), encoding="utf-8")
    meta = {"schema": "faradaiq-altair-linked-2d-v297", "version": "v297", "group_label": group_label, "metric": metric, "spatial_unit": opts.get("spatial_unit"), "signal_unit": signal_unit, "point_axis": selected_point_axis, "point_axis_label": selected_point_axis_label, "coordinate_source": "Takedata/canonical point metadata; synthetic compatibility coordinates are explicitly flagged when physical stage coordinates are unavailable", "options": opts}
    out.with_suffix(".visualization.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return {"html": str(out), "point_metrics": str(csv_path), "spec": str(spec), "metadata": str(out.with_suffix('.visualization.json')), "engine": "Altair", "metric": metric, "curves": len(curves), "points": int(points.point.nunique()), "label": group_label}


def make_altair_2d(browser, metric: str = "Auto", options: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    curves = _load_curves(browser)
    if not curves:
        raise ValueError("Select one or more numeric technique items before opening Interactive 2D.")
    opts = _v216_options(options)
    groups = _curve_groups(curves, min_size=1)
    outputs = [_make_altair_group(browser, items, metric, label, f"g{i + 1:02d}", opts) for i, (label, items) in enumerate(groups)]
    if len(outputs) == 1: return outputs[0]
    out_dir = _view_dir(browser); stamp = time.strftime("%Y%m%d_%H%M%S")
    master = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_interactive_2D_multitechnique_{stamp}.html"
    _write_master_index(master, f"FaradaIQ {VERSION} multi-technique interactive 2D", outputs)
    return {**outputs[0], "html": str(master), "engine": "Altair multi-technique", "groups": outputs, "group_count": len(outputs), "curves": len(curves)}


def _make_plotly_group(browser, curves: Sequence[Curve], metric: str, group_label: str, suffix: str, options: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    import plotly.graph_objects as go
    opts = _v216_options(options)
    points = _point_table(curves); metric = _choose_metric(points, metric)
    points, signal_unit = _v216_metric_scale(points, metric, curves[0].y_name, str(opts.get("signal_unit", "Auto")))
    traces: List[Any] = []; spatial_count = 0
    spatial = points.dropna(subset=["x_coord", "y_coord", metric]) if {"x_coord", "y_coord", metric}.issubset(points.columns) else pd.DataFrame()
    spatial_unit = str(opts.get("spatial_unit", "µm"))
    if len(spatial) >= 4:
        xs, spatial_label, _ = _v216_spatial_scaled(spatial.x_coord.to_numpy(float), spatial_unit)
        ys, _, _ = _v216_spatial_scaled(spatial.y_coord.to_numpy(float), spatial_unit)
        values = spatial[metric].to_numpy(float)
        palette = str(opts.get("palette") or "Viridis")
        if bool(opts.get("reverse_palette", False)):
            palette = palette + "_r"
        cmin, cmax = _v222_color_range(values, opts)
        colorbar_title = f"{metric} / {signal_unit}" if signal_unit else metric
        if _regular_grid(spatial):
            temp = spatial.copy(); temp["xd"] = xs; temp["yd"] = ys
            pivot = temp.pivot_table(index="yd", columns="xd", values=metric, aggfunc="mean").sort_index().sort_index(axis=1)
            traces.append(go.Surface(
                x=pivot.columns.to_numpy(float), y=pivot.index.to_numpy(float), z=pivot.to_numpy(float),
                name=f"{metric} surface", visible=True, opacity=float(opts.get("opacity", 0.92)),
                colorscale=palette, cmin=cmin, cmax=cmax, colorbar=dict(title=colorbar_title),
                hovertemplate="X=%{x}<br>Y=%{y}<br>Value=%{z:.6g}<extra></extra>"))
        else:
            traces.append(go.Scatter3d(
                x=xs, y=ys, z=values, mode="markers",
                marker=dict(size=max(3, int(opts.get("point_size", 13)) // 2), color=values,
                            colorscale=palette, cmin=cmin, cmax=cmax, colorbar=dict(title=colorbar_title),
                            opacity=float(opts.get("opacity", 0.92))),
                text=[f"Point {int(p)}" for p in spatial.point], name=f"{metric} spatial", visible=True,
                hovertemplate="%{text}<br>X=%{x}<br>Y=%{y}<br>Value=%{z:.6g}<extra></extra>"))
        spatial_count = 1
    max_each = max(120, min(700, 80000 // max(1, len(curves))))
    for curve in curves:
        x, y = _downsample(curve.x, curve.y, max_each)
        y_scaled, trace_unit, _ = _v216_scaled(y, curve.y_name, str(opts.get("signal_unit", "Auto")))
        traces.append(go.Scatter3d(x=x, y=np.full_like(x, curve.point, dtype=float), z=y_scaled, mode="lines", name=f"M{curve.method:02d} P{curve.point:03d}" + (f" C{curve.cycle:02d}" if curve.cycle else ""), visible=(spatial_count == 0)))
    fig = go.Figure(data=traces); n = len(traces); buttons = []
    if spatial_count:
        buttons = [
            dict(label=f"Spatial {metric}", method="update", args=[{"visible": [True] + [False] * (n - 1)}, {"title": f"{group_label}: spatial {metric}", "scene": {"xaxis_title": f"X position / {spatial_label}", "yaxis_title": f"Y position / {spatial_label}", "zaxis_title": f"{metric} / {signal_unit}" if signal_unit else metric}}]),
            dict(label="Point-stacked traces", method="update", args=[{"visible": [False] + [True] * (n - 1)}, {"title": f"{group_label}: point-dependent traces", "scene": {"xaxis_title": curves[0].x_name, "yaxis_title": "Point", "zaxis_title": f"{_v216_pretty_quantity(curves[0].y_name)} / {trace_unit}" if trace_unit else curves[0].y_name}}]),
        ]
    fig.update_layout(title=f"FaradaIQ {VERSION}: {group_label}", scene=dict(xaxis_title=f"X position / {spatial_label}" if spatial_count else curves[0].x_name, yaxis_title=f"Y position / {spatial_label}" if spatial_count else "Point", zaxis_title=f"{metric} / {signal_unit}" if spatial_count and signal_unit else (metric if spatial_count else curves[0].y_name)), updatemenus=[dict(type="buttons", direction="right", buttons=buttons, x=0.0, y=1.12)] if buttons else [], margin=dict(l=0, r=0, b=0, t=80), legend=dict(itemsizing="constant"))
    out_dir = _view_dir(browser); stamp = time.strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_3D_{_slug(suffix)}_{stamp}.html"
    meta = {"schema": "faradaiq-plotly-3d-v216", "version": VERSION, "group_label": group_label, "metric": metric, "spatial_unit": spatial_unit, "signal_unit": signal_unit, "options": opts}
    _v216_write_figure(fig, out, meta); points.to_csv(out.with_suffix(".point_metrics.csv"), index=False)
    return {"html": str(out), "engine": "Plotly", "metric": metric, "curves": len(curves), "points": int(points.point.nunique()), "spatial": bool(spatial_count), "label": group_label, "metadata": str(out.with_suffix('.visualization.json'))}


def make_plotly_3d(browser, metric: str = "Auto", options: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    curves = _load_curves(browser)
    if not curves: raise ValueError("Select numeric technique items before opening 3D.")
    opts = _v216_options(options); groups = _curve_groups(curves, min_size=1)
    outputs = [_make_plotly_group(browser, items, metric, label, f"g{i + 1:02d}", opts) for i, (label, items) in enumerate(groups)]
    if len(outputs) == 1: return outputs[0]
    out_dir = _view_dir(browser); stamp = time.strftime("%Y%m%d_%H%M%S")
    master = out_dir / f"{_slug(getattr(browser, 'base_name', 'data'))}_3D_multitechnique_{stamp}.html"
    _write_master_index(master, f"FaradaIQ {VERSION} multi-technique 3D", outputs)
    return {**outputs[0], "html": str(master), "engine": "Plotly multi-technique", "groups": outputs, "group_count": len(outputs), "curves": len(curves)}


def generate(browser, mode: str, metric: str = "Auto", options: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    mode_l = str(mode or "").lower(); opts = _v216_options(options)
    if "altair" in mode_l or ("2d" in mode_l and "3d" not in mode_l):
        result = make_altair_2d(browser, metric, opts)
    elif "hyper" in mode_l or "cube" in mode_l or "stack" in mode_l:
        result = make_hyperspectral(browser, metric, opts)
    elif "3d" in mode_l or "plotly" in mode_l:
        result = make_plotly_3d(browser, metric, opts)
    else:
        raise ValueError(f"Unsupported multidimensional mode: {mode}")
    _open_html_process(Path(result["html"]))
    return result


def _v222_apply_visualization_options(browser: Any, raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Apply saved visualization settings now, or defer until controls exist."""
    opts = _v216_options(raw or {})
    browser._v222_pending_visualization_options = dict(opts)
    browser._v222_visualization_options = dict(opts)
    mapping = {
        "_v216_spatial_unit_var": "spatial_unit", "_v216_signal_unit_var": "signal_unit", "_v216_spectral_unit_var": "spectral_unit",
        "_v216_render_mode_var": "render_mode", "_v216_interpolation_var": "interpolation", "_v216_contour_levels_var": "contour_levels",
        "_v216_adaptive_contours_var": "adaptive_contours", "_v216_palette_var": "palette", "_v216_reverse_palette_var": "reverse_palette",
        "_v216_range_mode_var": "range_mode", "_v222_range_scope_var": "range_scope", "_v216_range_min_var": "range_min", "_v216_range_max_var": "range_max",
        "_v216_contour_line_width_var": "contour_line_width", "_v216_contour_labels_var": "contour_labels", "_v216_smoothing_var": "smoothing",
        "_v216_mask_hull_var": "mask_outside_hull", "_v216_overlay_points_var": "overlay_points", "_v216_opacity_var": "opacity",
        "_v216_point_size_var": "point_size", "_v222_surface_model_var": "surface_model", "_v222_tip_diameter_var": "tip_diameter_um",
        "_v222_overlap_var": "overlap_percent", "_v222_clip_overshoot_var": "clip_overshoot", "_v222_grid_quality_var": "grid_quality",
        "_v217_lock_xy_aspect_var": "lock_xy_aspect",
    }
    for attr, key in mapping.items():
        try: getattr(browser, attr).set(opts[key])
        except Exception: pass
    _v222_save_persisted_options(opts)
    return opts


def _v222_install_session_hooks(core: Dict[str, Any], Browser: Any) -> None:
    if getattr(Browser, "_v222_session_option_hooks", False):
        return
    previous_collect = core.get("_v75_collect_session")
    if callable(previous_collect):
        def collect_session(browser: Any, _prev=previous_collect):
            payload = _prev(browser)
            try:
                options = browser._v216_multidim_options() if hasattr(browser, "_v216_multidim_options") else getattr(browser, "_v222_visualization_options", _v222_load_persisted_options())
                payload["visualization_options"] = _v216_options(options)
            except Exception:
                payload["visualization_options"] = _v222_load_persisted_options()
            return payload
        core["_v75_collect_session"] = collect_session
    previous_apply = core.get("_v75_apply_session_payload")
    if callable(previous_apply):
        def apply_session(browser: Any, payload: Any, silent: bool=False, _prev=previous_apply):
            result = _prev(browser, payload, silent=silent)
            try:
                raw = payload.get("visualization_options") if isinstance(payload, dict) else None
                if raw: _v222_apply_visualization_options(browser, raw)
            except Exception: pass
            return result
        core["_v75_apply_session_payload"] = apply_session
    previous_v138_payload = core.get("_v138_session_payload")
    if callable(previous_v138_payload):
        def v138_payload(browser: Any, include_bundle: bool=True, _prev=previous_v138_payload):
            payload = _prev(browser, include_bundle=include_bundle)
            try:
                options = browser._v216_multidim_options() if hasattr(browser, "_v216_multidim_options") else getattr(browser, "_v222_visualization_options", _v222_load_persisted_options())
                payload["visualization_options"] = _v216_options(options)
            except Exception: pass
            return payload
        core["_v138_session_payload"] = v138_payload
    Browser._v222_apply_visualization_options = _v222_apply_visualization_options
    Browser._v222_session_option_hooks = True


def _v222_install_help(core: Dict[str, Any]) -> None:
    tabs = core.get("_V207_HELP_TABS")
    title = "v228 Origin-style and SECCM-aware contour reconstruction"
    if not isinstance(tabs, list) or any(str(item[0]) == title for item in tabs if isinstance(item, (list, tuple)) and item):
        return
    tabs.append((title, r"""
ORIGIN-STYLE SMOOTH SURFACE

Choose Origin-style smooth to reconstruct a continuous surface with SciPy thin-plate radial-basis interpolation and optional regularization. Light, Medium, and Strong control the reconstruction regularization. This provides the smooth visual character expected from commercial contour software without claiming to reproduce OriginLab's proprietary algorithm. Measured values and measured point locations are never changed.

SECCM TIP FOOTPRINT

Choose SECCM tip footprint and enter the physical tip diameter in micrometres plus the scan overlap percentage. FaradaIQ treats the diameter as the Gaussian footprint full width at half maximum and calculates nominal center spacing as:

spacing = tip diameter × (1 − overlap / 100)

The physical footprint blends measured locations onto the contour grid, while overlap controls the nominal sampling distance and grid density. Mask outside measured hull and Prevent overshoot are enabled by default to avoid invented regions and values outside the measured range.

SMART CONTOURS AND COLOR RANGE

Smart contour optimization selects a readable line count from the number of measured locations and uses human-friendly contour intervals. Palette, reversed palette, robust 2–98% range, full range, manual limits, contour width, labels, point overlay, smoothing, and grid quality remain user controlled. Lock across cube uses one palette range for direct comparison of every slider frame. Per frame recalculates the range for maximum contrast in each frame.

CARRYOVER

Visualization options are shared between the main browser and Popout all sections, applied to every frame in a generated cube, written into Plot/view metadata, saved in browser sessions, and restored for future launches. Raw points remain traceable and hover labels report the true measured-point count.
"""))


def install(core: Dict[str, Any]) -> None:
    Browser = core.get("FaradaIQH5Browser"); ttk = core.get("ttk"); tk = core.get("tk"); filedialog = core.get("filedialog")
    if Browser is None or ttk is None or tk is None:
        raise RuntimeError("FaradaIQ browser classes are unavailable")
    if getattr(Browser, "_v216_multidim_installed", False):
        return
    _v222_install_session_hooks(core, Browser)
    _v222_install_help(core)
    previous = Browser._build_visible_ui_first

    def build_ui(self):
        previous(self)
        try:
            persisted = _v216_options(getattr(self, "_v222_pending_visualization_options", None) or _v222_load_persisted_options())
            bar = ttk.LabelFrame(self, text="1D / 2D / 3D / hyperspectral visualization", padding=7)
            bar.pack(fill="x", padx=8, pady=(0, 6))
            self._v216_viz_mode_var = tk.StringVar(value="Altair interactive 2D")
            self._v216_viz_metric_var = tk.StringVar(value="Auto")
            self._v294_point_axis_var = tk.StringVar(value="Auto")
            self._v216_spatial_unit_var = tk.StringVar(value="µm")
            self._v216_signal_unit_var = tk.StringVar(value="Auto")
            self._v216_spectral_unit_var = tk.StringVar(value="Auto")
            self._v216_render_mode_var = tk.StringVar(value="Contour + points")
            self._v216_interpolation_var = tk.StringVar(value="Linear")
            self._v216_contour_levels_var = tk.StringVar(value="16")
            self._v216_adaptive_contours_var = tk.BooleanVar(value=True)
            self._v216_palette_var = tk.StringVar(value="Viridis")
            self._v216_reverse_palette_var = tk.BooleanVar(value=False)
            self._v216_range_mode_var = tk.StringVar(value="Robust (2–98%)")
            self._v222_range_scope_var = tk.StringVar(value="Lock across cube")
            self._v216_range_min_var = tk.StringVar(value="")
            self._v216_range_max_var = tk.StringVar(value="")
            self._v216_contour_line_width_var = tk.StringVar(value="1.0")
            self._v216_contour_labels_var = tk.BooleanVar(value=False)
            self._v216_smoothing_var = tk.StringVar(value="Light")
            self._v216_mask_hull_var = tk.BooleanVar(value=True)
            self._v216_overlay_points_var = tk.BooleanVar(value=True)
            self._v216_opacity_var = tk.StringVar(value="0.92")
            self._v216_point_size_var = tk.StringVar(value="13")
            self._v216_background_enabled_var = tk.BooleanVar(value=False)
            self._v216_background_path_var = tk.StringVar(value="")
            self._v216_background_fit_var = tk.StringVar(value="Fit to data extent")
            self._v216_background_opacity_var = tk.StringVar(value="0.35")
            self._v216_background_xmin_var = tk.StringVar(value="")
            self._v216_background_xmax_var = tk.StringVar(value="")
            self._v216_background_ymin_var = tk.StringVar(value="")
            self._v216_background_ymax_var = tk.StringVar(value="")
            self._v222_surface_model_var = tk.StringVar(value=str(persisted.get("surface_model", "Standard interpolation")))
            self._v222_tip_diameter_var = tk.StringVar(value=str(persisted.get("tip_diameter_um", 5.0)))
            self._v222_overlap_var = tk.StringVar(value=str(persisted.get("overlap_percent", 0.0)))
            self._v222_clip_overshoot_var = tk.BooleanVar(value=bool(persisted.get("clip_overshoot", True)))
            self._v222_grid_quality_var = tk.StringVar(value=str(persisted.get("grid_quality", "Auto")))
            self._v222_spacing_label_var = tk.StringVar(value="")

            # Restore the full last-used visualization state. These controls are
            # shared by every slider frame and carried into saved Plot/view metadata.
            restore_map = [
                (self._v216_spatial_unit_var, "spatial_unit"), (self._v294_point_axis_var, "point_axis"), (self._v216_signal_unit_var, "signal_unit"), (self._v216_spectral_unit_var, "spectral_unit"),
                (self._v216_render_mode_var, "render_mode"), (self._v216_interpolation_var, "interpolation"), (self._v216_contour_levels_var, "contour_levels"),
                (self._v216_adaptive_contours_var, "adaptive_contours"), (self._v216_palette_var, "palette"), (self._v216_reverse_palette_var, "reverse_palette"),
                (self._v216_range_mode_var, "range_mode"), (self._v222_range_scope_var, "range_scope"), (self._v216_range_min_var, "range_min"), (self._v216_range_max_var, "range_max"),
                (self._v216_contour_line_width_var, "contour_line_width"), (self._v216_contour_labels_var, "contour_labels"), (self._v216_smoothing_var, "smoothing"),
                (self._v216_mask_hull_var, "mask_outside_hull"), (self._v216_overlay_points_var, "overlay_points"), (self._v216_opacity_var, "opacity"),
                (self._v216_point_size_var, "point_size"), (self._v216_background_enabled_var, "background_enabled"), (self._v216_background_path_var, "background_path"),
                (self._v216_background_fit_var, "background_fit"), (self._v216_background_opacity_var, "background_opacity"), (self._v216_background_xmin_var, "background_xmin"),
                (self._v216_background_xmax_var, "background_xmax"), (self._v216_background_ymin_var, "background_ymin"), (self._v216_background_ymax_var, "background_ymax"),
            ]
            for var, key in restore_map:
                try:
                    if key in persisted: var.set(persisted[key])
                except Exception:
                    pass

            row1 = ttk.Frame(bar); row1.pack(fill="x", pady=(0, 4))
            ttk.Label(row1, text="Engine").pack(side="left", padx=(0, 5))
            ttk.Combobox(row1, textvariable=self._v216_viz_mode_var, state="readonly", width=25, values=("Altair interactive 2D", "Plotly true 3D", "Hyperspectral slider / cube")).pack(side="left", padx=(0, 8))
            ttk.Label(row1, text="Metric").pack(side="left", padx=(0, 5))
            ttk.Combobox(row1, textvariable=self._v216_viz_metric_var, state="readonly", width=15, values=("Auto", "steady_state", "integral", "absolute_peak", "endpoint", "mean", "minimum", "maximum", "range")).pack(side="left", padx=(0, 8))
            open_button = ttk.Button(row1, text="Open selected data")
            open_button.pack(side="left", padx=2)
            ttk.Label(row1, text="CV hyperspectral mode preserves forward and reverse branches.").pack(side="left", padx=(10, 0))

            row2 = ttk.Frame(bar); row2.pack(fill="x", pady=(0, 4))
            ttk.Label(row2, text="Spatial unit").pack(side="left", padx=(0, 4))
            ttk.Combobox(row2, textvariable=self._v216_spatial_unit_var, state="readonly", width=8, values=("Scientific", "nm", "µm", "mm", "m")).pack(side="left", padx=(0, 8))
            ttk.Label(row2, text="Point/line X axis").pack(side="left", padx=(0, 4))
            ttk.Combobox(row2, textvariable=self._v294_point_axis_var, state="readonly", width=10, values=("Auto", "Point", "X", "Y", "Distance")).pack(side="left", padx=(0, 8))
            ttk.Label(row2, text="Signal/color unit").pack(side="left", padx=(0, 4))
            ttk.Combobox(row2, textvariable=self._v216_signal_unit_var, state="readonly", width=10, values=("Auto", "Scientific", "fA", "pA", "nA", "µA", "mA", "A", "mV", "V", "Ω", "kΩ", "MΩ", "fC", "pC", "nC", "µC", "mC", "C")).pack(side="left", padx=(0, 8))
            ttk.Label(row2, text="Slider axis unit").pack(side="left", padx=(0, 4))
            ttk.Combobox(row2, textvariable=self._v216_spectral_unit_var, state="readonly", width=10, values=("Auto", "Scientific", "mV", "V", "µs", "ms", "s", "Hz", "kHz", "MHz", "nm", "µm", "mm", "m")).pack(side="left", padx=(0, 8))
            ttk.Label(row2, text="Map style").pack(side="left", padx=(0, 4))
            ttk.Combobox(row2, textvariable=self._v216_render_mode_var, state="readonly", width=18, values=("Measured points", "Heatmap", "Filled contour", "Contour lines", "Contour + points")).pack(side="left", padx=(0, 8))

            row3 = ttk.Frame(bar); row3.pack(fill="x", pady=(0, 4))
            ttk.Label(row3, text="Interpolation").pack(side="left", padx=(0, 4))
            ttk.Combobox(row3, textvariable=self._v216_interpolation_var, state="readonly", width=9, values=("None", "Nearest", "Linear", "Cubic")).pack(side="left", padx=(0, 8))
            ttk.Label(row3, text="Contour levels").pack(side="left", padx=(0, 4))
            ttk.Entry(row3, textvariable=self._v216_contour_levels_var, width=5).pack(side="left", padx=(0, 8))
            ttk.Checkbutton(row3, text="Overlay measured points", variable=self._v216_overlay_points_var).pack(side="left", padx=(0, 8))
            ttk.Label(row3, text="Opacity").pack(side="left", padx=(0, 4)); ttk.Entry(row3, textvariable=self._v216_opacity_var, width=6).pack(side="left", padx=(0, 8))
            ttk.Label(row3, text="Point size").pack(side="left", padx=(0, 4)); ttk.Entry(row3, textvariable=self._v216_point_size_var, width=5).pack(side="left", padx=(0, 8))

            row3b = ttk.Frame(bar); row3b.pack(fill="x", pady=(0, 4))
            ttk.Label(row3b, text="Palette").pack(side="left", padx=(0,4))
            ttk.Combobox(row3b, textvariable=self._v216_palette_var, state="readonly", width=13, values=("Viridis","Cividis","Turbo","Plasma","Inferno","Magma","Greys","RdBu","Picnic","Portland","Jet")).pack(side="left", padx=(0,6))
            ttk.Checkbutton(row3b, text="Reverse", variable=self._v216_reverse_palette_var).pack(side="left", padx=(0,8))
            ttk.Label(row3b, text="Palette range").pack(side="left", padx=(0,4))
            ttk.Combobox(row3b, textvariable=self._v216_range_mode_var, state="readonly", width=17, values=("Robust (2–98%)","Full data range","Manual")).pack(side="left", padx=(0,5))
            ttk.Entry(row3b, textvariable=self._v216_range_min_var, width=8).pack(side="left"); ttk.Label(row3b,text="to").pack(side="left",padx=2); ttk.Entry(row3b, textvariable=self._v216_range_max_var, width=8).pack(side="left", padx=(0,8))
            ttk.Label(row3b, text="Smoothing").pack(side="left", padx=(0,4))
            ttk.Combobox(row3b, textvariable=self._v216_smoothing_var, state="readonly", width=8, values=("None","Light","Medium","Strong")).pack(side="left", padx=(0,8))
            ttk.Checkbutton(row3b, text="Adaptive levels", variable=self._v216_adaptive_contours_var).pack(side="left", padx=(0,6))
            ttk.Checkbutton(row3b, text="Mask outside measured hull", variable=self._v216_mask_hull_var).pack(side="left")

            row3c = ttk.Frame(bar); row3c.pack(fill="x", pady=(0,4))
            ttk.Label(row3c, text="Contour line width").pack(side="left", padx=(0,4)); ttk.Entry(row3c, textvariable=self._v216_contour_line_width_var, width=6).pack(side="left", padx=(0,8))
            ttk.Checkbutton(row3c, text="Contour labels", variable=self._v216_contour_labels_var).pack(side="left", padx=(0,8))
            ttk.Label(row3c, text="Smart defaults use robust color limits, adaptive nice-number contour spacing, and no extrapolation outside measured points.").pack(side="left", padx=(8,0))

            row3d = ttk.Frame(bar); row3d.pack(fill="x", pady=(0,4))
            ttk.Label(row3d, text="Surface model").pack(side="left", padx=(0,4))
            ttk.Combobox(row3d, textvariable=self._v222_surface_model_var, state="readonly", width=23, values=("Standard interpolation", "Origin-style smooth", "SECCM tip footprint")).pack(side="left", padx=(0,8))
            ttk.Label(row3d, text="Tip diameter / µm").pack(side="left", padx=(0,4))
            ttk.Entry(row3d, textvariable=self._v222_tip_diameter_var, width=7).pack(side="left", padx=(0,8))
            ttk.Label(row3d, text="Overlap / %").pack(side="left", padx=(0,4))
            ttk.Entry(row3d, textvariable=self._v222_overlap_var, width=6).pack(side="left", padx=(0,8))
            ttk.Label(row3d, text="Grid").pack(side="left", padx=(0,4))
            ttk.Combobox(row3d, textvariable=self._v222_grid_quality_var, state="readonly", width=11, values=("Auto", "Fine", "Publication")).pack(side="left", padx=(0,8))
            ttk.Checkbutton(row3d, text="Prevent interpolation overshoot", variable=self._v222_clip_overshoot_var).pack(side="left", padx=(0,8))
            ttk.Label(row3d, textvariable=self._v222_spacing_label_var).pack(side="left", padx=(8,0))

            row3e = ttk.Frame(bar); row3e.pack(fill="x", pady=(0,4))
            ttk.Label(row3e, text="Presets").pack(side="left", padx=(0,4))

            def apply_preset(name: str):
                if name == "Raw measured":
                    self._v216_render_mode_var.set("Measured points"); self._v222_surface_model_var.set("Standard interpolation"); self._v216_smoothing_var.set("None")
                elif name == "Origin-style smooth":
                    self._v216_render_mode_var.set("Contour + points"); self._v222_surface_model_var.set("Origin-style smooth"); self._v216_smoothing_var.set("Light"); self._v216_adaptive_contours_var.set(True); self._v216_mask_hull_var.set(True)
                elif name == "SECCM physical footprint":
                    self._v216_render_mode_var.set("Contour + points"); self._v222_surface_model_var.set("SECCM tip footprint"); self._v216_adaptive_contours_var.set(True); self._v216_mask_hull_var.set(True); self._v222_clip_overshoot_var.set(True)
                elif name == "Publication contour":
                    self._v216_render_mode_var.set("Contour + points"); self._v216_adaptive_contours_var.set(True); self._v216_contour_line_width_var.set("1.1"); self._v216_contour_labels_var.set(False); self._v222_grid_quality_var.set("Publication")
                update_spacing_label()

            for preset in ("Raw measured", "Origin-style smooth", "SECCM physical footprint", "Publication contour"):
                ttk.Button(row3e, text=preset, command=lambda n=preset: apply_preset(n)).pack(side="left", padx=2)
            ttk.Label(row3e, text="Settings carry across cube frames, browser/popout views, saved Plot/views, and future launches.").pack(side="left", padx=(10,0))

            def update_spacing_label(*_args):
                try:
                    d = max(0.001, float(self._v222_tip_diameter_var.get()))
                    o = max(0.0, min(99.5, float(self._v222_overlap_var.get())))
                    spacing = d * (1.0 - o / 100.0)
                    self._v222_spacing_label_var.set(f"Nominal spacing: {spacing:g} µm; Gaussian FWHM: {d:g} µm")
                except Exception:
                    self._v222_spacing_label_var.set("Enter numeric tip diameter and overlap")
            self._v222_tip_diameter_var.trace_add("write", update_spacing_label)
            self._v222_overlap_var.trace_add("write", update_spacing_label)
            update_spacing_label()

            row4 = ttk.Frame(bar); row4.pack(fill="x")
            ttk.Checkbutton(row4, text="Use background image", variable=self._v216_background_enabled_var).pack(side="left", padx=(0, 6))
            bg_label = ttk.Label(row4, text="No background loaded")
            bg_label.pack(side="left", padx=(0, 8))

            def choose_background():
                if filedialog is None: return
                path = filedialog.askopenfilename(parent=self, title="Choose spatial background image", filetypes=[("Images", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp"), ("All files", "*.*")])
                if path:
                    self._v216_background_path_var.set(path); self._v216_background_enabled_var.set(True); bg_label.configure(text=Path(path).name)

            def clear_background():
                self._v216_background_path_var.set(""); self._v216_background_enabled_var.set(False); bg_label.configure(text="No background loaded")

            def background_settings():
                win = tk.Toplevel(self); win.title(f"FaradaIQ Studio {VERSION} — background image settings"); win.transient(self)
                frame = ttk.Frame(win, padding=12); frame.pack(fill="both", expand=True)
                ttk.Checkbutton(frame, text="Use background image", variable=self._v216_background_enabled_var).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
                ttk.Button(frame, text="Choose image…", command=choose_background).grid(row=1, column=0, sticky="ew", padx=(0, 5)); ttk.Button(frame, text="Clear", command=clear_background).grid(row=1, column=1, sticky="ew")
                ttk.Label(frame, textvariable=self._v216_background_path_var, wraplength=560).grid(row=2, column=0, columnspan=2, sticky="ew", pady=6)
                ttk.Label(frame, text="Fit / resize mode").grid(row=3, column=0, sticky="w"); ttk.Combobox(frame, textvariable=self._v216_background_fit_var, state="readonly", values=("Fit to data extent", "Preserve aspect", "Manual extent"), width=24).grid(row=3, column=1, sticky="ew")
                ttk.Label(frame, text="Image opacity").grid(row=4, column=0, sticky="w"); ttk.Entry(frame, textvariable=self._v216_background_opacity_var).grid(row=4, column=1, sticky="ew")
                ttk.Label(frame, text="Manual extent uses the selected spatial unit. Leave blank to use data bounds.", wraplength=560).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 4))
                for r, (label, var) in enumerate((("X min", self._v216_background_xmin_var), ("X max", self._v216_background_xmax_var), ("Y min", self._v216_background_ymin_var), ("Y max", self._v216_background_ymax_var)), start=6):
                    ttk.Label(frame, text=label).grid(row=r, column=0, sticky="w"); ttk.Entry(frame, textvariable=var).grid(row=r, column=1, sticky="ew")
                ttk.Button(frame, text="Apply and close", command=win.destroy).grid(row=10, column=0, columnspan=2, sticky="ew", pady=(10, 0))
                frame.columnconfigure(1, weight=1)

            ttk.Button(row4, text="Choose…", command=choose_background).pack(side="left", padx=2)
            ttk.Button(row4, text="Background resize/settings…", command=background_settings).pack(side="left", padx=2)
            ttk.Button(row4, text="Clear", command=clear_background).pack(side="left", padx=2)
            ttk.Label(row4, text="Background is off by default and applies only to spatial Plotly maps.").pack(side="left", padx=(10, 0))

            def collect_options() -> Dict[str, Any]:
                opts = _v216_options({
                    "spatial_unit": self._v216_spatial_unit_var.get(), "point_axis": self._v294_point_axis_var.get(), "signal_unit": self._v216_signal_unit_var.get(), "spectral_unit": self._v216_spectral_unit_var.get(),
                    "render_mode": self._v216_render_mode_var.get(), "interpolation": self._v216_interpolation_var.get(), "contour_levels": self._v216_contour_levels_var.get(),
                    "adaptive_contours": self._v216_adaptive_contours_var.get(), "palette": self._v216_palette_var.get(), "reverse_palette": self._v216_reverse_palette_var.get(),
                    "range_mode": self._v216_range_mode_var.get(), "range_scope": self._v222_range_scope_var.get(), "range_min": self._v216_range_min_var.get(), "range_max": self._v216_range_max_var.get(),
                    "contour_line_width": self._v216_contour_line_width_var.get(), "contour_labels": self._v216_contour_labels_var.get(),
                    "smoothing": self._v216_smoothing_var.get(), "surface_model": self._v222_surface_model_var.get(),
                    "tip_diameter_um": self._v222_tip_diameter_var.get(), "overlap_percent": self._v222_overlap_var.get(),
                    "clip_overshoot": self._v222_clip_overshoot_var.get(), "grid_quality": self._v222_grid_quality_var.get(),
                    "mask_outside_hull": self._v216_mask_hull_var.get(),
                    "overlay_points": self._v216_overlay_points_var.get(), "opacity": self._v216_opacity_var.get(), "point_size": self._v216_point_size_var.get(),
                    "background_enabled": self._v216_background_enabled_var.get(), "background_path": self._v216_background_path_var.get(), "background_fit": self._v216_background_fit_var.get(),
                    "background_opacity": self._v216_background_opacity_var.get(), "background_xmin": self._v216_background_xmin_var.get(), "background_xmax": self._v216_background_xmax_var.get(),
                    "background_ymin": self._v216_background_ymin_var.get(), "background_ymax": self._v216_background_ymax_var.get(),
                })
                self._v222_visualization_options = dict(opts)
                _v222_save_persisted_options(opts)
                return opts

            def run():
                mode = self._v216_viz_mode_var.get(); metric = self._v216_viz_metric_var.get(); opts = collect_options()
                try:
                    self.log(f"Preparing {mode} using metric {metric}; point/line axis {opts['point_axis']}; spatial unit {opts['spatial_unit']}; map style {opts['render_mode']}...")
                    if hasattr(self, "set_status"): self.set_status(f"Preparing {mode}...")
                except Exception: pass
                open_button.configure(state="disabled")
                def worker():
                    try:
                        result = generate(self, mode, metric, opts)
                        msg = f"Opened {result.get('engine')} view: {result.get('html')}"
                    except Exception as exc:
                        msg = f"Multidimensional visualization failed: {exc}"
                    def finish():
                        try: open_button.configure(state="normal")
                        except Exception: pass
                        try:
                            self.log(msg)
                            if hasattr(self, "set_status"): self.set_status(msg)
                        except Exception: pass
                    try: self.after(0, finish)
                    except Exception: pass
                threading.Thread(target=worker, daemon=True).start()
            open_button.configure(command=run)
            self._v216_multidim_options = collect_options
            self._v216_multidim_bar = bar
        except Exception as exc:
            try: print(f"[FaradaIQ {VERSION}] multidimensional UI warning: {exc}")
            except Exception: pass

    Browser._build_visible_ui_first = build_ui
    Browser._v216_multidim_installed = True
