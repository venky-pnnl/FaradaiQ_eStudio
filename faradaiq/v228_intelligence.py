"""FaradaIQ Studio v273 scientific-intelligence workspace.

This module layers a persistent dataset index, progressive prepared views,
technique-aware quality checks, native CV/EIS/DRT analysis, reconstruction
cross-validation with uncertainty, unified selection/history, command palette,
structured AI context, and reproducible reports over the stable compatibility core.

The implementation is intentionally non-destructive: source files are never
modified. Every generated artifact is written below
``<dataset>_FaradaIQ_analyzed_data/Scientific_Intelligence_v237`` and carries a
provenance record.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
import re
import shutil
import sys
import statistics
import threading
import time
import traceback
import webbrowser
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import numpy as np

from .numpy_compat import trapezoid
import pandas as pd

VERSION = "v273"
APP_VERSION = "0.273.0"
INDEX_SCHEMA = "faradaiq-scientific-index-v228"
PROVENANCE_SCHEMA = "faradaiq-provenance-v228"
REPORT_SCHEMA = "faradaiq-scientific-report-v228"
_INDEX_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug(value: Any, default: str = "item") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or default)).strip("._")
    return (text or default)[:120]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, np.ndarray):
        return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, pd.DataFrame):
        return [_json_safe(r) for r in value.to_dict(orient="records")]
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    try:
        return _json_safe(asdict(value))
    except Exception:
        return str(value)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _output_root(browser: Any) -> Path:
    root = Path(getattr(browser, "analyzed_root", "") or getattr(browser, "cache_root", "") or Path.cwd())
    out = root / "Scientific_Intelligence_v237"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _record_signature(path: Path) -> Dict[str, Any]:
    try:
        st = path.stat()
        return {"path": str(path.resolve()), "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}
    except Exception:
        return {"path": str(path), "size": -1, "mtime_ns": -1}


def _signature_key(sig: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(sig), sort_keys=True).encode("utf-8")).hexdigest()


def _provenance_path(browser: Any) -> Path:
    return _output_root(browser) / "provenance.jsonl"


def record_event(browser: Any, event: str, details: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    payload = {
        "schema": PROVENANCE_SCHEMA,
        "timestamp": _now(),
        "version": VERSION,
        "event": str(event),
        "dataset": str(getattr(browser, "base_name", "")),
        "h5_path": str(getattr(browser, "h5_path", "") or ""),
        "details": _json_safe(details or {}),
    }
    try:
        path = _provenance_path(browser)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    try:
        history = getattr(browser, "_v228_action_history", None)
        if history is None:
            history = []
            browser._v228_action_history = history
        history.append(payload)
        if len(history) > 500:
            del history[:-500]
        refresh = getattr(browser, "_v228_refresh_history", None)
        if callable(refresh) and threading.current_thread() is threading.main_thread():
            browser.after(0, refresh)
    except Exception:
        pass
    return payload


def _read_table(path: Path, record: Optional[Mapping[str, Any]] = None, max_rows: int = 100000) -> pd.DataFrame:
    from . import multidim_v216 as md
    return md._read_table(Path(path), record or {}, max_rows=max_rows)


def _roles(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    from . import multidim_v216 as md
    return md._column_roles(df)


def _numeric(df: pd.DataFrame, col: Optional[str]) -> np.ndarray:
    if not col or col not in df:
        return np.array([], dtype=float)
    return pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)


def _finite(*arrays: np.ndarray) -> Tuple[np.ndarray, ...]:
    if not arrays:
        return tuple()
    n = min(len(a) for a in arrays)
    if n <= 0:
        return tuple(np.array([], dtype=float) for _ in arrays)
    mask = np.ones(n, dtype=bool)
    for arr in arrays:
        mask &= np.isfinite(arr[:n])
    return tuple(arr[:n][mask] for arr in arrays)


def _monotonic_status(values: np.ndarray) -> str:
    values = values[np.isfinite(values)]
    if len(values) < 2:
        return "insufficient"
    d = np.diff(values)
    if np.all(d > 0):
        return "strictly increasing"
    if np.all(d < 0):
        return "strictly decreasing"
    if np.all(d >= 0):
        return "nondecreasing"
    if np.all(d <= 0):
        return "nonincreasing"
    return "nonmonotonic"


def _profile_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    path = Path(str(record.get("path") or ""))
    tech = str(record.get("technique_key") or "OTHER").upper()
    profile: Dict[str, Any] = {
        "signature": _record_signature(path),
        "path": str(path),
        "technique": tech,
        "method": int(record.get("method_index") or 1),
        "point": int(record.get("point") or 0),
        "cycle": int(record.get("cycle") or record.get("sweep") or 0),
        "confidence": record.get("confidence"),
        "status": "ok",
        "warnings": [],
        "severity": "info",
    }
    if not path.exists():
        profile.update(status="missing", severity="error", warnings=["Source file is missing."])
        return profile
    try:
        df = _read_table(path, record, max_rows=50000)
    except Exception as exc:
        profile.update(status="unreadable", severity="error", warnings=[f"Could not read table: {exc}"])
        return profile
    profile["rows_profiled"] = int(len(df))
    profile["columns"] = [str(c) for c in df.columns]
    if df.empty:
        profile.update(status="empty", severity="error")
        profile["warnings"].append("Table has no rows.")
        return profile
    numeric_cols: Dict[str, Dict[str, Any]] = {}
    for col in df.columns:
        vals = pd.to_numeric(df[col], errors="coerce")
        finite = vals[np.isfinite(vals)]
        if len(finite) < 2:
            continue
        numeric_cols[str(col)] = {
            "finite": int(len(finite)),
            "missing_or_nonfinite": int(len(vals) - len(finite)),
            "min": float(finite.min()),
            "max": float(finite.max()),
            "median": float(finite.median()),
            "q02": float(finite.quantile(0.02)),
            "q98": float(finite.quantile(0.98)),
            "monotonic": _monotonic_status(finite.to_numpy(float)),
        }
    profile["numeric_columns"] = numeric_cols
    profile["duplicate_rows"] = int(df.duplicated().sum())
    if profile["duplicate_rows"]:
        profile["warnings"].append(f"Contains {profile['duplicate_rows']} duplicate row(s).")
    roles = _roles(df)
    profile["roles"] = roles
    # Generic nonfinite and repeated-axis checks.
    for role in ("time", "potential", "frequency", "tau", "x", "y"):
        arr = _numeric(df, roles.get(role))
        arr = arr[np.isfinite(arr)]
        if len(arr) > 1:
            duplicates = len(arr) - len(np.unique(arr))
            if duplicates > max(1, int(0.05 * len(arr))) and role in {"time", "frequency", "tau"}:
                profile["warnings"].append(f"{role} axis contains {duplicates} repeated values.")
    if tech == "EIS":
        f = _numeric(df, roles.get("frequency"))
        zr = _numeric(df, roles.get("zreal"))
        zi = _numeric(df, roles.get("zimag"))
        f, zr, zi = _finite(f, zr, zi)
        if len(f) < 6:
            profile["warnings"].append("EIS spectrum has fewer than 6 valid frequency points.")
        else:
            pos = f[f > 0]
            profile["frequency_decades"] = float(np.log10(np.max(pos) / np.min(pos))) if len(pos) else 0.0
            profile["frequency_order"] = _monotonic_status(f)
            profile["inductive_fraction"] = float(np.mean(zi > 0))
            if profile["frequency_order"] == "nonmonotonic":
                profile["warnings"].append("Frequency is nonmonotonic; sort or inspect acquisition ordering before fitting.")
            if profile["frequency_decades"] < 2:
                profile["warnings"].append("EIS frequency span is below two decades.")
    elif tech in {"CV", "LSV", "FSCV", "CV/LSV", "IV/TAFEL"}:
        e = _numeric(df, roles.get("potential"))
        i = _numeric(df, roles.get("current"))
        e, i = _finite(e, i)
        if len(e) < 8:
            profile["warnings"].append("Voltammetric trace has fewer than 8 valid points.")
        elif len(e):
            d = np.diff(e)
            turns = int(np.sum(np.sign(d[1:]) != np.sign(d[:-1]))) if len(d) > 1 else 0
            profile["potential_turning_points"] = turns
            profile["current_dynamic_range"] = float(np.nanmax(i) - np.nanmin(i)) if len(i) else None
            if tech in {"CV", "FSCV", "CV/LSV"} and turns == 0:
                profile["warnings"].append("CV label is present but no potential turning point was detected.")
    elif tech in {"CA", "CP", "OCP", "TIME_SCAN"}:
        t = _numeric(df, roles.get("time"))
        if len(t) and _monotonic_status(t) == "nonmonotonic":
            profile["warnings"].append("Time is nonmonotonic.")
    confidence = profile.get("confidence")
    try:
        if confidence is not None and float(confidence) < 0.5:
            profile["warnings"].append(f"Parser confidence is low ({float(confidence):.2f}).")
    except Exception:
        pass
    if any("missing" in w.lower() or "could not" in w.lower() for w in profile["warnings"]):
        profile["severity"] = "error"
    elif profile["warnings"]:
        profile["severity"] = "warning"
    return profile


def _dataset_quality(records: Sequence[Mapping[str, Any]], profiles: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    for p in profiles:
        for text in p.get("warnings") or []:
            warnings.append({"severity": p.get("severity", "warning"), "path": p.get("path"), "message": text})
    # Missing points and duplicate coordinates.
    by_tech_method: Dict[Tuple[str, int], List[int]] = defaultdict(list)
    coords: Dict[Tuple[float, float], List[int]] = defaultdict(list)
    for r in records:
        tech = str(r.get("technique_key") or "OTHER")
        method = int(r.get("method_index") or 1)
        point = int(r.get("point") or 0)
        if point > 0:
            by_tech_method[(tech, method)].append(point)
        try:
            x = float(r.get("x_position") if r.get("x_position") is not None else r.get("x_coord"))
            y = float(r.get("y_position") if r.get("y_position") is not None else r.get("y_coord"))
            if math.isfinite(x) and math.isfinite(y):
                coords[(round(x, 12), round(y, 12))].append(point)
        except Exception:
            pass
    for (tech, method), pts in sorted(by_tech_method.items()):
        unique = sorted(set(pts))
        if len(unique) >= 3:
            missing = sorted(set(range(unique[0], unique[-1] + 1)) - set(unique))
            if missing:
                warnings.append({"severity": "warning", "path": "", "message": f"{tech} method {method:02d} is missing point(s): {missing[:30]}"})
    for coord, pts in coords.items():
        unique = sorted(set(p for p in pts if p > 0))
        if len(unique) > 1:
            warnings.append({"severity": "warning", "path": "", "message": f"Coordinate {coord} is assigned to multiple points: {unique[:20]}"})
    counts = Counter(str(p.get("severity") or "info") for p in profiles)
    counts.update(w.get("severity", "warning") for w in warnings if not w.get("path"))
    score = max(0, 100 - 12 * counts.get("error", 0) - 3 * counts.get("warning", 0))
    grade = "Excellent" if score >= 95 else "Good" if score >= 80 else "Review" if score >= 60 else "Needs attention"
    return {"score": int(score), "grade": grade, "counts": dict(counts), "warnings": warnings}


def build_scientific_index(browser: Any, force: bool = False, on_progress=None) -> Dict[str, Any]:
    """Build or incrementally repair the persistent scientific index."""
    records = list(getattr(browser, "_catalog_records", []) or [])
    path = _output_root(browser) / "scientific_index_v228.json"
    old: Dict[str, Any] = {}
    if path.exists() and not force:
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            old = {}
    old_by_key = {str(p.get("signature_key")): p for p in old.get("profiles", []) if p.get("signature_key")}
    profiles: List[Dict[str, Any]] = []
    reused = 0
    for idx, record in enumerate(records, 1):
        p = Path(str(record.get("path") or ""))
        sig = _record_signature(p)
        key = _signature_key(sig)
        cached = old_by_key.get(key)
        if cached and str(cached.get("path")) == str(p):
            profile = dict(cached)
            reused += 1
        else:
            profile = _profile_record(record)
            profile["signature_key"] = key
        profiles.append(profile)
        if on_progress is not None and idx % 10 == 0:
            on_progress(f"Indexing {idx}/{len(records)} records…")
    quality = _dataset_quality(records, profiles)
    by_tech = Counter(str(r.get("technique_key") or "OTHER") for r in records)
    payload = {
        "schema": INDEX_SCHEMA,
        "version": VERSION,
        "created": _now(),
        "dataset": str(getattr(browser, "base_name", "")),
        "h5_path": str(getattr(browser, "h5_path", "") or ""),
        "record_count": len(records),
        "reused_profiles": reused,
        "technique_counts": dict(by_tech),
        "quality": quality,
        "profiles": profiles,
        "cache_health": {
            "state": "ready",
            "profile_reuse_fraction": float(reused / len(records)) if records else 1.0,
            "index_path": str(path),
        },
    }
    with _INDEX_LOCK:
        _atomic_json(path, payload)
    browser._v228_scientific_index = payload
    record_event(browser, "scientific_index_built", {"records": len(records), "reused": reused, "quality_score": quality["score"]})
    return payload


def schedule_index(browser: Any, force: bool = False) -> None:
    if getattr(browser, "_v228_index_running", False):
        return
    browser._v228_index_running = True
    try:
        browser._v228_index_status_var.set("Preparing scientific index…")
    except Exception:
        pass
    messages = SimpleQueue()

    def refresh_ui() -> None:
        try:
            while True:
                state, text = messages.get_nowait()
                browser._v228_index_status_var.set(text)
                if state != "progress":
                    browser._v228_index_running = False
                    if state == "ready":
                        getattr(browser, "_v228_refresh_overview", lambda: None)()
                        getattr(browser, "_v228_refresh_history", lambda: None)()
                    return
        except Empty:
            browser.after(100, refresh_ui)

    def worker() -> None:
        try:
            result = build_scientific_index(
                browser, force=force, on_progress=lambda text: messages.put(("progress", text))
            )
            text = f"Ready • {result['record_count']} records • quality {result['quality']['score']}/100 • reused {result['reused_profiles']}"
            messages.put(("ready", text))
        except Exception as exc:
            messages.put(("error", f"Index failed: {exc}"))
    threading.Thread(target=worker, daemon=True, name="FaradaIQ-v228-index").start()
    browser.after(100, refresh_ui)


class UnifiedSelection:
    """One selection contract for tree, Plot/views, and external tools."""

    def __init__(self, browser: Any):
        self.browser = browser
        self.paths: List[Path] = []
        self.records: List[Dict[str, Any]] = []
        self.source = "none"
        self.updated = ""

    def refresh(self) -> None:
        browser = self.browser
        paths: List[Path] = []
        records: List[Dict[str, Any]] = []
        try:
            from . import browser_ui_v209 as ui
            for iid in list(browser.tree.selection()):
                records.extend(dict(r) for r in ui._tree_records(browser, iid))
            paths = [Path(str(r.get("path"))) for r in records if r.get("path")]
            if paths:
                self.source = "experiment tree"
        except Exception:
            pass
        if not paths:
            try:
                idxs = list(browser.plot_view_list.curselection())
                for idx in idxs:
                    view = browser.plot_views[idx]
                    paths.extend(Path(p) for p in view.get("paths", []) or [])
                if paths:
                    self.source = "Plot/views"
            except Exception:
                pass
        seen = set()
        unique: List[Path] = []
        by_path = getattr(browser, "_catalog_by_path", {}) or {}
        for p in paths:
            key = str(p.resolve()).lower() if p.exists() else str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(p)
            rec = by_path.get(key)
            if rec and not any(str(x.get("path")) == str(rec.get("path")) for x in records):
                records.append(dict(rec))
        self.paths = unique
        self.records = records
        self.updated = _now()

    def selected_paths(self, plottable_only: bool = True) -> List[Path]:
        self.refresh()
        if not plottable_only:
            return list(self.paths)
        return [p for p in self.paths if p.suffix.lower() in {".csv", ".tsv", ".txt", ".asc", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".mp4", ".avi", ".mov"}]


def _selection(browser: Any) -> UnifiedSelection:
    model = getattr(browser, "_v228_selection", None)
    if model is None:
        model = UnifiedSelection(browser)
        browser._v228_selection = model
    model.refresh()
    return model


def _selected_records(browser: Any, techniques: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    model = _selection(browser)
    records = list(model.records)
    wanted = {str(x).upper() for x in techniques or []}
    if wanted:
        records = [r for r in records if str(r.get("technique_key") or "").upper() in wanted]
    return records


def _curves_from_records(browser: Any, records: Sequence[Mapping[str, Any]], max_curves: int = 2500) -> List[Any]:
    from . import multidim_v216 as md
    pos = md._position_lookup(browser)
    curves = []
    for i, record in enumerate(list(records)[:max_curves], 1):
        rec = dict(record)
        p = Path(str(rec.get("path") or ""))
        if not p.exists() or p.suffix.lower() not in {".csv", ".tsv", ".txt", ".asc", ".xlsx", ".xls"}:
            continue
        try:
            df = md._read_table(p, rec)
            roles = md._column_roles(df)
            xn, yn = md._preferred_axes(rec, roles)
            if not xn or not yn:
                nums = [c for c in df.columns if pd.to_numeric(df[c], errors="coerce").notna().sum() >= 2]
                if len(nums) < 2:
                    continue
                xn, yn = nums[:2]
            x, y = md._finite_xy(md._numeric(df, xn), md._numeric(df, yn))
            if len(x) < 2:
                continue
            pt = md._safe_int(rec.get("point"), i)
            xc = md._v216_record_coord_m(rec, "x")
            yc = md._v216_record_coord_m(rec, "y")
            zc = md._v216_record_coord_m(rec, "z")
            if pt in pos:
                px, py, pz = pos[pt]
                xc = xc if xc is not None else px
                yc = yc if yc is not None else py
                zc = zc if zc is not None else pz
            curves.append(md.Curve(
                path=p, record=rec, x_name=str(xn), y_name=str(yn), x=x, y=y,
                point=pt, method=md._safe_int(rec.get("method_index"), 1),
                cycle=md._safe_int(rec.get("cycle") or rec.get("sweep"), 0),
                line=md._safe_int(rec.get("line"), 0), x_coord=xc, y_coord=yc, z_coord=zc,
            ))
        except Exception:
            continue
    return curves


def visualization_advice(browser: Any) -> Dict[str, Any]:
    from . import multidim_v216 as md
    records = _selected_records(browser)
    curves = _curves_from_records(browser, records)
    if not curves:
        raise ValueError("Select numeric data before requesting visualization advice.")
    points = md._point_table(curves)
    spatial = points.dropna(subset=["x_coord", "y_coord"]) if {"x_coord", "y_coord"}.issubset(points.columns) else pd.DataFrame()
    metric = md._choose_metric(points, "Auto")
    values = pd.to_numeric(points.get(metric), errors="coerce").to_numpy(float) if metric in points else np.array([])
    finite = values[np.isfinite(values)]
    advice: Dict[str, Any] = {
        "metric": metric,
        "n_curves": len(curves),
        "n_points": int(points.point.nunique()) if "point" in points else len(points),
        "coordinates_available": int(len(spatial)),
        "recommended_view": "linked traces",
        "interpolation": "None",
        "surface_model": "Raw measured",
        "smoothing": "None",
        "palette_range": "Robust (2–98%)",
        "mask_outside_hull": True,
        "explanation": "The selection does not contain enough spatial coordinates for a defensible interpolated map.",
    }
    if len(spatial) >= 4:
        regular = md._regular_grid(spatial)
        xy = spatial[["x_coord", "y_coord"]].to_numpy(float)
        unique_xy = len(np.unique(np.round(xy, 12), axis=0))
        duplicate = len(xy) - unique_xy
        dynamic = float(np.nanmax(finite) - np.nanmin(finite)) if len(finite) else 0.0
        q = np.nanpercentile(finite, [25, 75]) if len(finite) else np.array([0.0, 0.0])
        iqr = float(q[1] - q[0])
        outlier_fraction = float(np.mean(np.abs(finite - np.nanmedian(finite)) > 4 * max(iqr, 1e-30))) if len(finite) else 0.0
        advice.update({
            "regular_grid": bool(regular),
            "duplicate_coordinates": int(duplicate),
            "dynamic_range": dynamic,
            "outlier_fraction": outlier_fraction,
            "recommended_view": "heatmap + measured points" if regular else "filled contour + measured points",
            "interpolation": "Linear" if regular else "Cubic" if len(spatial) >= 12 and outlier_fraction < 0.08 else "Linear",
            "surface_model": "SECCM tip footprint" if hasattr(browser, "_v222_tip_diameter_var") else "Origin-style smooth",
            "smoothing": "Light" if outlier_fraction < 0.08 else "None",
            "palette_range": "Robust (2–98%)" if outlier_fraction > 0.01 else "Full data range",
            "explanation": (
                f"{len(spatial)} measured locations form a {'regular' if regular else 'scattered'} spatial grid. "
                f"The recommended view preserves measured points, masks extrapolation, and uses "
                f"{'light smoothing' if outlier_fraction < 0.08 else 'no smoothing because outliers are present'}."
            ),
        })
    record_event(browser, "visualization_advice", advice)
    return advice


def apply_visualization_advice(browser: Any, advice: Mapping[str, Any]) -> None:
    mapping = {
        "_v216_interpolation_var": advice.get("interpolation"),
        "_v216_smoothing_var": advice.get("smoothing"),
        "_v216_range_mode_var": advice.get("palette_range"),
        "_v216_mask_hull_var": advice.get("mask_outside_hull", True),
        "_v222_surface_model_var": advice.get("surface_model"),
    }
    view = str(advice.get("recommended_view") or "").lower()
    if "heatmap" in view:
        mapping["_v216_render_mode_var"] = "Heatmap"
    elif "contour" in view:
        mapping["_v216_render_mode_var"] = "Contour + points"
    else:
        mapping["_v216_render_mode_var"] = "Measured points"
    for attr, value in mapping.items():
        if value is None:
            continue
        try:
            getattr(browser, attr).set(value)
        except Exception:
            pass
    record_event(browser, "visualization_advice_applied", dict(advice))


def _idw_predict(train_xy: np.ndarray, train_z: np.ndarray, query_xy: np.ndarray, power: float = 2.0) -> np.ndarray:
    delta = query_xy[:, None, :] - train_xy[None, :, :]
    dist = np.sqrt(np.sum(delta * delta, axis=2))
    exact = dist < 1e-15
    weights = 1.0 / np.maximum(dist, 1e-15) ** power
    pred = np.sum(weights * train_z[None, :], axis=1) / np.sum(weights, axis=1)
    if np.any(exact):
        rows, cols = np.where(exact)
        for row in np.unique(rows):
            pred[row] = train_z[cols[rows == row][0]]
    return pred


def _predict_model(name: str, train_xy: np.ndarray, train_z: np.ndarray, query_xy: np.ndarray) -> np.ndarray:
    from scipy.interpolate import RBFInterpolator, griddata
    if name == "Nearest":
        return np.asarray(griddata(train_xy, train_z, query_xy, method="nearest"), dtype=float)
    if name == "Linear":
        out = np.asarray(griddata(train_xy, train_z, query_xy, method="linear"), dtype=float)
        missing = ~np.isfinite(out)
        if np.any(missing):
            out[missing] = griddata(train_xy, train_z, query_xy[missing], method="nearest")
        return out
    if name == "Cubic":
        out = np.asarray(griddata(train_xy, train_z, query_xy, method="cubic"), dtype=float)
        missing = ~np.isfinite(out)
        if np.any(missing):
            out[missing] = griddata(train_xy, train_z, query_xy[missing], method="linear")
        missing = ~np.isfinite(out)
        if np.any(missing):
            out[missing] = griddata(train_xy, train_z, query_xy[missing], method="nearest")
        return out
    if name == "Thin-plate RBF":
        model = RBFInterpolator(train_xy, train_z, kernel="thin_plate_spline", smoothing=0.0)
        return np.asarray(model(query_xy), dtype=float)
    if name == "Gaussian RBF":
        # Scale epsilon from median nearest-neighbor distance.
        d = np.sqrt(np.sum((train_xy[:, None, :] - train_xy[None, :, :]) ** 2, axis=2))
        d[d == 0] = np.nan
        eps = float(np.nanmedian(np.nanmin(d, axis=1)))
        model = RBFInterpolator(train_xy, train_z, kernel="gaussian", epsilon=max(eps, 1e-12), smoothing=1e-9)
        return np.asarray(model(query_xy), dtype=float)
    if name == "IDW":
        return _idw_predict(train_xy, train_z, query_xy)
    raise ValueError(f"Unsupported reconstruction model: {name}")


def validate_reconstruction(browser: Any) -> Dict[str, Any]:
    from . import multidim_v216 as md
    records = _selected_records(browser)
    curves = _curves_from_records(browser, records)
    if len(curves) < 5:
        raise ValueError("Select at least five spatially located curves for reconstruction validation.")
    points = md._point_table(curves)
    metric = md._choose_metric(points, "Auto")
    spatial = points.dropna(subset=["x_coord", "y_coord", metric]).copy()
    if len(spatial) < 5:
        raise ValueError("At least five measured locations with finite X, Y, and metric values are required.")
    xy = spatial[["x_coord", "y_coord"]].to_numpy(float)
    z = pd.to_numeric(spatial[metric], errors="coerce").to_numpy(float)
    mask = np.isfinite(xy).all(axis=1) & np.isfinite(z)
    xy, z = xy[mask], z[mask]
    models = ["Nearest", "Linear", "Cubic", "IDW", "Thin-plate RBF", "Gaussian RBF"]
    rows: List[Dict[str, Any]] = []
    predictions: Dict[str, np.ndarray] = {}
    for name in models:
        pred = np.full(len(z), np.nan)
        errors: List[str] = []
        for i in range(len(z)):
            try:
                pred[i] = _predict_model(name, np.delete(xy, i, axis=0), np.delete(z, i), xy[i:i+1])[0]
            except Exception as exc:
                errors.append(str(exc))
        ok = np.isfinite(pred) & np.isfinite(z)
        if int(ok.sum()) < max(3, int(0.7 * len(z))):
            rows.append({"model": name, "n_valid": int(ok.sum()), "rmse": None, "mae": None, "r2": None, "status": "insufficient", "warning": "; ".join(errors[:2])})
            continue
        residual = pred[ok] - z[ok]
        rmse = float(np.sqrt(np.mean(residual ** 2)))
        mae = float(np.mean(np.abs(residual)))
        denom = float(np.sum((z[ok] - np.mean(z[ok])) ** 2))
        r2 = float(1 - np.sum(residual ** 2) / denom) if denom > 0 else None
        rows.append({"model": name, "n_valid": int(ok.sum()), "rmse": rmse, "mae": mae, "r2": r2, "status": "ok", "warning": "; ".join(errors[:2])})
        predictions[name] = pred
    valid = [r for r in rows if r.get("rmse") is not None]
    if not valid:
        raise RuntimeError("None of the reconstruction models could be cross-validated for this geometry.")
    valid.sort(key=lambda r: (float(r["rmse"]), float(r["mae"])))
    best = valid[0]["model"]
    # Build signal and uncertainty grids. Uncertainty combines model disagreement
    # and normalized distance to the nearest measured point.
    gx = np.linspace(float(np.min(xy[:, 0])), float(np.max(xy[:, 0])), 120)
    gy = np.linspace(float(np.min(xy[:, 1])), float(np.max(xy[:, 1])), 120)
    xx, yy = np.meshgrid(gx, gy)
    q = np.column_stack([xx.ravel(), yy.ravel()])
    grid_predictions = []
    for row in valid[:4]:
        try:
            grid_predictions.append(_predict_model(row["model"], xy, z, q))
        except Exception:
            pass
    best_grid = _predict_model(best, xy, z, q)
    disagreement = np.nanstd(np.vstack(grid_predictions), axis=0) if len(grid_predictions) >= 2 else np.zeros(len(q))
    distance = np.sqrt(np.min(np.sum((q[:, None, :] - xy[None, :, :]) ** 2, axis=2), axis=1))
    scale = float(np.nanmedian(distance[distance > 0])) if np.any(distance > 0) else 1.0
    zscale = float(np.nanstd(z)) or max(float(np.nanmax(z) - np.nanmin(z)), 1.0)
    uncertainty = np.sqrt(disagreement ** 2 + (distance / max(scale, 1e-30) * 0.08 * zscale) ** 2)
    out = _output_root(browser) / "reconstruction" / f"{_slug(getattr(browser, 'base_name', 'dataset'))}_{int(time.time())}"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "model_cross_validation.csv", index=False)
    spatial.to_csv(out / "measured_point_metrics.csv", index=False)
    np.savez_compressed(out / "reconstruction_and_uncertainty.npz", x=gx, y=gy, signal=best_grid.reshape(xx.shape), uncertainty=uncertainty.reshape(xx.shape), measured_xy=xy, measured_z=z)
    result = {
        "schema": "faradaiq-reconstruction-validation-v228",
        "created": _now(), "metric": metric, "best_model": best, "models": rows,
        "n_measured": len(z), "output_dir": str(out),
        "interpretation": "Cross-validation error estimates interpolation performance only at measured locations. The uncertainty map also increases away from measurements and is not a probabilistic confidence interval.",
    }
    _atomic_json(out / "reconstruction_validation.json", result)
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        fig = make_subplots(rows=1, cols=2, subplot_titles=(f"Best reconstruction: {best}", "Interpolation uncertainty"))
        fig.add_trace(go.Contour(x=gx, y=gy, z=best_grid.reshape(xx.shape), contours_coloring="heatmap", colorbar=dict(title=metric, x=0.46)), row=1, col=1)
        fig.add_trace(go.Scatter(x=xy[:, 0], y=xy[:, 1], mode="markers", marker=dict(size=7, color="white", line=dict(color="black", width=1)), name=f"Measured (n={len(z)})"), row=1, col=1)
        fig.add_trace(go.Contour(x=gx, y=gy, z=uncertainty.reshape(xx.shape), contours_coloring="heatmap", colorbar=dict(title="Uncertainty", x=1.02)), row=1, col=2)
        fig.update_layout(title=f"FaradaIQ {VERSION} reconstruction validation • {metric}", width=1250, height=650)
        fig.write_html(out / "reconstruction_validation.html", include_plotlyjs="cdn")
        result["html"] = str(out / "reconstruction_validation.html")
    except Exception as exc:
        result["plot_warning"] = str(exc)
    record_event(browser, "reconstruction_validated", result)
    return result


def _scan_rate_v_s(record: Mapping[str, Any]) -> Optional[float]:
    text = " ".join(str(record.get(k) or "") for k in ("scan_rate", "method_title", "series_title", "group_title"))
    match = re.search(r"(?i)([0-9]+(?:\.[0-9]+)?)\s*(mV|V)\s*/?\s*s", text)
    if not match:
        return None
    value = float(match.group(1))
    return value * 1e-3 if match.group(2).lower() == "mv" else value


def _linear_baseline(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    if len(x) < 2 or x[-1] == x[0]:
        return np.full_like(y, float(np.nanmedian(y)))
    slope = (y[-1] - y[0]) / (x[-1] - x[0])
    return y[0] + slope * (x - x[0])


def analyze_cv(browser: Any) -> Dict[str, Any]:
    from . import multidim_v216 as md
    records = _selected_records(browser, {"CV", "LSV", "FSCV", "CV/LSV"})
    curves = _curves_from_records(browser, records)
    curves = [c for c in curves if str(c.record.get("technique_key") or "").upper() in {"CV", "LSV", "FSCV", "CV/LSV"}]
    if not curves:
        raise ValueError("Select one or more CV/LSV curves before running native CV analysis.")
    rows: List[Dict[str, Any]] = []
    traces: List[Dict[str, Any]] = []
    for curve in curves:
        try:
            branches = md._v216_split_cv_curve(curve)
        except Exception:
            branches = {"whole": (curve.x, curve.y, "Whole trace")}
        scan_rate = _scan_rate_v_s(curve.record)
        for key, item in branches.items():
            x, y, label = item
            x, y = _finite(np.asarray(x, float), np.asarray(y, float))
            if len(x) < 4:
                continue
            baseline = _linear_baseline(x, y)
            corrected = y - baseline
            ia = int(np.nanargmax(corrected)); ic = int(np.nanargmin(corrected))
            integral_i_dE = float(trapezoid(corrected, x))
            charge = float(integral_i_dE / scan_rate) if scan_rate and scan_rate > 0 else None
            row = {
                "source": str(curve.path), "method": curve.method, "point": curve.point,
                "cycle": curve.cycle, "branch": str(label), "branch_key": key,
                "scan_rate_V_s": scan_rate, "n_points": len(x),
                "anodic_peak_current_A": float(corrected[ia]), "anodic_peak_potential_V": float(x[ia]),
                "cathodic_peak_current_A": float(corrected[ic]), "cathodic_peak_potential_V": float(x[ic]),
                "delta_Ep_V": float(x[ia] - x[ic]),
                "ipa_over_abs_ipc": float(corrected[ia] / abs(corrected[ic])) if corrected[ic] != 0 else None,
                "integral_I_dE_A_V": integral_i_dE, "charge_C": charge,
                "x_coord_m": curve.x_coord, "y_coord_m": curve.y_coord,
                "baseline": "linear endpoints",
            }
            rows.append(row)
            traces.append({"x": x, "y": y, "baseline": baseline, "corrected": corrected, "row": row})
    if not rows:
        raise RuntimeError("No valid CV branches were available for analysis.")
    out = _output_root(browser) / "CV_analysis" / f"{_slug(getattr(browser, 'base_name', 'dataset'))}_{int(time.time())}"
    out.mkdir(parents=True, exist_ok=True)
    table = pd.DataFrame(rows)
    table.to_csv(out / "cv_branch_metrics.csv", index=False)
    result: Dict[str, Any] = {
        "schema": "faradaiq-cv-analysis-v228", "created": _now(), "output_dir": str(out),
        "curve_count": len(curves), "branch_count": len(rows), "metrics_csv": str(out / "cv_branch_metrics.csv"),
        "notes": [
            "Peak currents use a linear endpoint baseline within each acquisition-order branch.",
            "Charge is calculated as integral(I dE)/scan_rate only when a scan rate is available.",
            "Forward and reverse labels follow acquisition order, not assumed anodic/cathodic direction.",
        ],
    }
    # Spatial metric maps.
    spatial = table.dropna(subset=["x_coord_m", "y_coord_m"])
    if len(spatial) >= 4:
        spatial.to_csv(out / "cv_spatial_metrics.csv", index=False)
        try:
            import plotly.express as px
            metrics = ["anodic_peak_current_A", "cathodic_peak_current_A", "anodic_peak_potential_V", "cathodic_peak_potential_V", "charge_C"]
            pages = []
            for metric in metrics:
                data = spatial.dropna(subset=[metric])
                if len(data) < 4:
                    continue
                fig = px.scatter(data, x="x_coord_m", y="y_coord_m", color=metric, facet_col="branch", hover_data=["point", "method", "cycle"], title=f"FaradaIQ {VERSION}: {metric}")
                p = out / f"map_{metric}.html"
                fig.write_html(p, include_plotlyjs="cdn")
                pages.append(str(p))
            result["spatial_maps"] = pages
        except Exception as exc:
            result["spatial_map_warning"] = str(exc)
    try:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 7))
        for tr in traces[:24]:
            r = tr["row"]
            ax.plot(tr["x"], tr["y"], linewidth=1, label=f"M{r['method']:02d} P{r['point']:03d} C{r['cycle']:02d} {r['branch']}")
        ax.set_xlabel("Potential / V"); ax.set_ylabel("Current / A"); ax.set_title(f"FaradaIQ {VERSION} CV branch analysis")
        if len(traces) <= 16:
            ax.legend(fontsize=6)
        ax.grid(True, alpha=0.25); fig.tight_layout()
        fig.savefig(out / "cv_branch_overlay.png", dpi=220)
        plt.close(fig)
        result["figure_png"] = str(out / "cv_branch_overlay.png")
    except Exception as exc:
        result["figure_warning"] = str(exc)
    _atomic_json(out / "cv_analysis.json", result)
    record_event(browser, "cv_analysis_completed", result)
    return result


@dataclass
class CircuitFit:
    source: str
    model: str
    success: bool
    n_points: int
    parameters: Dict[str, float]
    rmse: Optional[float]
    aic: Optional[float]
    bic: Optional[float]
    jacobian_condition: Optional[float]
    identifiable: bool
    warning: str = ""


def _complex_model(name: str, freq: np.ndarray, p: np.ndarray) -> np.ndarray:
    w = 2 * np.pi * freq
    jw = 1j * w
    if name == "R":
        return np.full_like(freq, np.exp(p[0]), dtype=complex)
    if name == "R-RC":
        rs, rct, c = np.exp(p[:3])
        return rs + 1 / (1 / rct + jw * c)
    if name == "R-CPE":
        rs, rct, q = np.exp(p[:3]); alpha = 1 / (1 + np.exp(-p[3]))
        return rs + 1 / (1 / rct + q * jw ** alpha)
    if name == "R-RC-W":
        rs, rct, c, sigma = np.exp(p[:4])
        return rs + 1 / (1 / rct + jw * c) + sigma / np.sqrt(jw)
    raise ValueError(name)


def _fit_circuit(name: str, freq: np.ndarray, z: np.ndarray, source: str) -> CircuitFit:
    from scipy.optimize import least_squares
    freq, zr, zi = _finite(freq, z.real, z.imag)
    z = zr + 1j * zi
    n = len(freq)
    if n < 6:
        return CircuitFit(source, name, False, n, {}, None, None, None, None, False, "Too few points")
    rs0 = max(float(np.nanmin(z.real)), 1e-9)
    rct0 = max(float(np.nanmax(z.real) - np.nanmin(z.real)), rs0 * 0.1, 1e-9)
    fmid = float(np.exp(np.mean(np.log(freq[freq > 0])))) if np.any(freq > 0) else 1.0
    c0 = max(1 / (2 * np.pi * fmid * rct0), 1e-12)
    if name == "R": p0 = np.log([rs0])
    elif name == "R-RC": p0 = np.log([rs0, rct0, c0])
    elif name == "R-CPE": p0 = np.r_[np.log([rs0, rct0, max(c0, 1e-12)]), 0.0]
    else: p0 = np.log([rs0, rct0, c0, max(rct0 / math.sqrt(2 * np.pi * max(np.min(freq), 1e-12)), 1e-12)])
    scale = max(float(np.nanmedian(np.abs(z))), 1e-12)
    def residual(p: np.ndarray) -> np.ndarray:
        pred = _complex_model(name, freq, p)
        return np.r_[(pred.real - z.real) / scale, (pred.imag - z.imag) / scale]
    try:
        fit = least_squares(residual, p0, max_nfev=5000, loss="soft_l1")
        pred = _complex_model(name, freq, fit.x)
        res = np.r_[pred.real - z.real, pred.imag - z.imag]
        rss = float(np.sum(res ** 2)); m = len(res); k = len(fit.x)
        rmse = float(np.sqrt(rss / max(m, 1)))
        aic = float(m * np.log(max(rss / max(m, 1), 1e-300)) + 2 * k)
        bic = float(m * np.log(max(rss / max(m, 1), 1e-300)) + k * np.log(max(m, 1)))
        try:
            cond = float(np.linalg.cond(fit.jac.T @ fit.jac))
        except Exception:
            cond = None
        if name == "R": params = {"Rs_ohm": float(np.exp(fit.x[0]))}
        elif name == "R-RC": params = {"Rs_ohm": float(np.exp(fit.x[0])), "Rct_ohm": float(np.exp(fit.x[1])), "C_F": float(np.exp(fit.x[2]))}
        elif name == "R-CPE": params = {"Rs_ohm": float(np.exp(fit.x[0])), "Rct_ohm": float(np.exp(fit.x[1])), "Q": float(np.exp(fit.x[2])), "alpha": float(1 / (1 + np.exp(-fit.x[3])))}
        else: params = {"Rs_ohm": float(np.exp(fit.x[0])), "Rct_ohm": float(np.exp(fit.x[1])), "C_F": float(np.exp(fit.x[2])), "Warburg_sigma": float(np.exp(fit.x[3]))}
        identifiable = bool(cond is not None and math.isfinite(cond) and cond < 1e12)
        warning = "" if identifiable else "Jacobian is ill-conditioned; parameters may not be identifiable."
        return CircuitFit(source, name, bool(fit.success), n, params, rmse, aic, bic, cond, identifiable, warning)
    except Exception as exc:
        return CircuitFit(source, name, False, n, {}, None, None, None, None, False, str(exc))


def _lin_kk(freq: np.ndarray, z: np.ndarray) -> Dict[str, Any]:
    """Run optional impedance.py lin-KK when available; otherwise readiness only."""
    readiness = {
        "available": False,
        "frequency_monotonic": _monotonic_status(freq),
        "frequency_decades": float(np.log10(np.max(freq[freq > 0]) / np.min(freq[freq > 0]))) if np.any(freq > 0) else 0.0,
        "duplicate_frequencies": int(len(freq) - len(np.unique(freq))),
    }
    try:
        from impedance.validation import linKK
        order = np.argsort(freq)[::-1]
        f = freq[order]; zz = z[order]
        result = linKK(f, zz, c=0.85, max_M=100, fit_type="complex", add_cap=True)
        if len(result) >= 5:
            M, mu, zfit, res_r, res_i = result[:5]
            readiness.update({
                "available": True, "M": int(M), "mu": float(mu),
                "rmse": float(np.sqrt(np.mean(np.r_[np.asarray(res_r, float), np.asarray(res_i, float)] ** 2))),
            })
    except Exception as exc:
        readiness["warning"] = f"Full lin-KK unavailable: {exc}"
    return readiness


def analyze_eis(browser: Any) -> Dict[str, Any]:
    records = _selected_records(browser, {"EIS"})
    if not records:
        raise ValueError("Select one or more EIS spectra before running EIS intelligence.")
    spectra: List[Dict[str, Any]] = []
    fits: List[CircuitFit] = []
    for rec in records:
        path = Path(str(rec.get("path") or ""))
        try:
            df = _read_table(path, rec)
            roles = _roles(df)
            f = _numeric(df, roles.get("frequency")); zr = _numeric(df, roles.get("zreal")); zi = _numeric(df, roles.get("zimag"))
            f, zr, zi = _finite(f, zr, zi)
            ok = f > 0
            f, zr, zi = f[ok], zr[ok], zi[ok]
            if len(f) < 6:
                continue
            z = zr + 1j * zi
            quality = {
                "n_points": len(f), "frequency_order": _monotonic_status(f),
                "frequency_decades": float(np.log10(np.max(f) / np.min(f))),
                "duplicate_frequencies": int(len(f) - len(np.unique(f))),
                "inductive_fraction": float(np.mean(zi > 0)),
                "lin_kk": _lin_kk(f, z),
            }
            spectrum_fits = [_fit_circuit(name, f, z, str(path)) for name in ("R", "R-RC", "R-CPE", "R-RC-W")]
            fits.extend(spectrum_fits)
            ranked = sorted([x for x in spectrum_fits if x.aic is not None], key=lambda x: float(x.aic))
            spectra.append({"source": str(path), "quality": quality, "best_model": ranked[0].model if ranked else None, "frequency": f, "z": z})
        except Exception as exc:
            spectra.append({"source": str(path), "error": str(exc)})
    if not any("z" in s for s in spectra):
        raise RuntimeError("No selected EIS spectrum contained usable frequency, Z-real, and Z-imag columns.")
    out = _output_root(browser) / "EIS_analysis" / f"{_slug(getattr(browser, 'base_name', 'dataset'))}_{int(time.time())}"
    out.mkdir(parents=True, exist_ok=True)
    fit_rows = []
    for fit in fits:
        row = asdict(fit); params = row.pop("parameters")
        row.update(params); fit_rows.append(row)
    pd.DataFrame(fit_rows).to_csv(out / "equivalent_circuit_ranking.csv", index=False)
    quality_rows = []
    for spec in spectra:
        q = dict(spec.get("quality") or {})
        lk = q.pop("lin_kk", {})
        quality_rows.append({"source": spec.get("source"), "best_model": spec.get("best_model"), **q, **{f"linKK_{k}": v for k, v in lk.items()}})
    pd.DataFrame(quality_rows).to_csv(out / "eis_quality.csv", index=False)
    result: Dict[str, Any] = {
        "schema": "faradaiq-eis-intelligence-v228", "created": _now(), "output_dir": str(out),
        "spectrum_count": len([s for s in spectra if "z" in s]),
        "quality_csv": str(out / "eis_quality.csv"), "circuit_ranking_csv": str(out / "equivalent_circuit_ranking.csv"),
        "model_note": "Circuit ranking uses bounded nonlinear least-squares and AIC/BIC. A low residual does not establish physical uniqueness; inspect identifiability and residuals.",
    }
    try:
        import matplotlib.pyplot as plt
        n = len([s for s in spectra if "z" in s])
        fig, axes = plt.subplots(1, 2, figsize=(12, 5))
        for spec in spectra:
            if "z" not in spec: continue
            z = spec["z"]; f = spec["frequency"]
            label = Path(spec["source"]).stem[:35]
            axes[0].plot(z.real, -z.imag, "o-", markersize=2, linewidth=1, label=label)
            axes[1].semilogx(f, np.abs(z), "o-", markersize=2, linewidth=1, label=label)
        axes[0].set_xlabel("Z′ / Ω"); axes[0].set_ylabel("−Z″ / Ω"); axes[0].set_title("Nyquist")
        axes[1].set_xlabel("Frequency / Hz"); axes[1].set_ylabel("|Z| / Ω"); axes[1].set_title("Bode magnitude")
        for ax in axes: ax.grid(True, alpha=0.25)
        if n <= 10:
            axes[0].legend(fontsize=6); axes[1].legend(fontsize=6)
        fig.tight_layout(); fig.savefig(out / "eis_diagnostics.png", dpi=220); plt.close(fig)
        result["figure_png"] = str(out / "eis_diagnostics.png")
    except Exception as exc:
        result["figure_warning"] = str(exc)
    _atomic_json(out / "eis_analysis.json", result)
    record_event(browser, "eis_analysis_completed", result)
    return result


def analyze_drt(browser: Any) -> Dict[str, Any]:
    from scipy.signal import find_peaks, peak_widths
    records = _selected_records(browser, {"DRT"})
    if not records:
        raise ValueError("Select one or more DRT tables before opening the DRT comparison workspace.")
    peak_rows: List[Dict[str, Any]] = []
    traces: List[Tuple[Path, np.ndarray, np.ndarray]] = []
    for rec in records:
        path = Path(str(rec.get("path") or ""))
        try:
            df = _read_table(path, rec)
            roles = _roles(df)
            tau = _numeric(df, roles.get("tau")); gamma = _numeric(df, roles.get("gamma"))
            tau, gamma = _finite(tau, gamma)
            ok = tau > 0; tau, gamma = tau[ok], gamma[ok]
            if len(tau) < 5: continue
            order = np.argsort(tau); tau, gamma = tau[order], gamma[order]
            peaks, props = find_peaks(gamma, prominence=max(float(np.nanstd(gamma)) * 0.15, 1e-30))
            widths = peak_widths(gamma, peaks, rel_height=0.5)[0] if len(peaks) else []
            for j, idx in enumerate(peaks):
                left = max(0, int(idx - widths[j] / 2)); right = min(len(tau) - 1, int(idx + widths[j] / 2))
                area = float(trapezoid(gamma[left:right + 1], np.log10(tau[left:right + 1]))) if right > left else 0.0
                peak_rows.append({
                    "source": str(path), "method": int(rec.get("method_index") or 1), "point": int(rec.get("point") or 0),
                    "cycle": int(rec.get("cycle") or rec.get("sweep") or 0), "peak_index": j + 1,
                    "tau_s": float(tau[idx]), "frequency_Hz": float(1 / (2 * np.pi * tau[idx])),
                    "amplitude": float(gamma[idx]), "area_log_tau": area,
                })
            traces.append((path, tau, gamma))
        except Exception:
            continue
    if not traces:
        raise RuntimeError("No usable tau/gamma DRT tables were found in the selection.")
    out = _output_root(browser) / "DRT_analysis" / f"{_slug(getattr(browser, 'base_name', 'dataset'))}_{int(time.time())}"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(peak_rows).to_csv(out / "drt_peaks.csv", index=False)
    result = {"schema": "faradaiq-drt-comparison-v228", "created": _now(), "output_dir": str(out), "spectrum_count": len(traces), "peak_count": len(peak_rows), "peaks_csv": str(out / "drt_peaks.csv")}
    try:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 6))
        for path, tau, gamma in traces:
            ax.semilogx(tau, gamma, linewidth=1.2, label=path.stem[:40])
        ax.set_xlabel("Relaxation time τ / s"); ax.set_ylabel("γ(ln τ)"); ax.set_title(f"FaradaIQ {VERSION} DRT comparison"); ax.grid(True, alpha=0.25)
        if len(traces) <= 14: ax.legend(fontsize=6)
        fig.tight_layout(); fig.savefig(out / "drt_overlay.png", dpi=220); plt.close(fig)
        result["figure_png"] = str(out / "drt_overlay.png")
    except Exception as exc:
        result["figure_warning"] = str(exc)
    _atomic_json(out / "drt_analysis.json", result)
    record_event(browser, "drt_analysis_completed", result)
    return result


def analyze_tafel_reliability(browser: Any) -> Dict[str, Any]:
    import faradaiq_tafel_adapter as tafel
    records = _selected_records(browser, {"CV", "LSV", "FSCV", "CV/LSV", "IV/TAFEL"})
    paths = [Path(str(r.get("path"))) for r in records if r.get("path")]
    if not paths:
        raise ValueError("Select CV, LSV, IV, or polarization data before running Tafel reliability analysis.")
    out = _output_root(browser) / "Tafel_reliability" / f"{_slug(getattr(browser, 'base_name', 'dataset'))}_{int(time.time())}"
    out.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    result_paths: List[str] = []
    for path in paths:
        settings = tafel.TafelSettings(scan_branch_mode="Forward + reverse separately")
        try:
            results = tafel.analyze_file_variants(path, out, settings)
        except Exception:
            try:
                results = [tafel.analyze_file(path, out, settings)]
            except Exception as exc:
                rows.append({"source": str(path), "status": "failed", "warning": str(exc), "score": 0, "grade": "Unsuitable"})
                continue
        for item in results:
            result_paths.append(str(item.summary_json))
            comparison = list(item.comparison_rows or [])
            candidates = []
            for fit in comparison:
                try:
                    r2 = float(fit.get("r2")) if fit.get("r2") is not None else np.nan
                    n = int(fit.get("n_points") or 0)
                    span = abs(float(fit.get("eta_max_V") or 0) - float(fit.get("eta_min_V") or 0))
                    slope = abs(float(fit.get("tafel_slope_mV_dec") or 0))
                    score = 0
                    score += 45 if np.isfinite(r2) and r2 >= 0.995 else 35 if np.isfinite(r2) and r2 >= 0.98 else 15 if np.isfinite(r2) and r2 >= 0.95 else 0
                    score += 20 if n >= 15 else 10 if n >= 8 else 0
                    score += 20 if span >= 0.08 else 10 if span >= 0.04 else 0
                    score += 15 if 20 <= slope <= 500 else 5 if slope > 0 else 0
                    candidates.append((score, fit, r2, n, span, slope))
                except Exception:
                    continue
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                score, fit, r2, n, span, slope = candidates[0]
                grade = "Suitable" if score >= 80 else "Questionable" if score >= 55 else "Unsuitable"
                warning = "" if grade == "Suitable" else "Inspect ohmic drop, transport limitation, fit range, and branch hysteresis before interpreting the slope."
                rows.append({
                    "source": item.source_file, "branch": fit.get("branch"), "method": fit.get("method"),
                    "score": score, "grade": grade, "r2": r2, "n_points": n,
                    "eta_span_V": span, "tafel_slope_mV_dec": slope, "warning": warning,
                    "summary_json": item.summary_json,
                })
            else:
                rows.append({"source": item.source_file, "score": 0, "grade": "Unsuitable", "warning": "No valid linear Tafel fit was produced.", "summary_json": item.summary_json})
    table = pd.DataFrame(rows)
    table.to_csv(out / "tafel_reliability.csv", index=False)
    result = {
        "schema": "faradaiq-tafel-reliability-v228", "created": _now(), "output_dir": str(out),
        "rows": rows, "csv": str(out / "tafel_reliability.csv"), "analysis_summaries": result_paths,
        "note": "Suitability is a diagnostic score, not validation. It combines fit linearity, point count, overpotential span, and slope plausibility.",
    }
    _atomic_json(out / "tafel_reliability.json", result)
    record_event(browser, "tafel_reliability_completed", result)
    return result


def build_ai_context(browser: Any) -> Dict[str, Any]:
    model = _selection(browser)
    index = getattr(browser, "_v228_scientific_index", {}) or {}
    selected_profiles = []
    profile_by_path = {str(p.get("path")): p for p in index.get("profiles", [])}
    for path in model.paths:
        if str(path) in profile_by_path:
            selected_profiles.append(profile_by_path[str(path)])
    current_recipe = {}
    try:
        current_recipe = dict(getattr(getattr(browser, "publication_panel", None), "last_recipe", {}) or {})
    except Exception:
        pass
    try:
        if not current_recipe and getattr(browser, "plot_panel", None):
            current_recipe = dict(getattr(browser.plot_panel, "last_recipe", {}) or {})
    except Exception:
        pass
    context = {
        "schema": "faradaiq-ai-context-v228", "created": _now(), "version": VERSION,
        "dataset": str(getattr(browser, "base_name", "")),
        "selection": {
            "source": model.source, "paths": [str(p) for p in model.paths],
            "records": [{k: _json_safe(r.get(k)) for k in ("technique_key", "method_index", "point", "cycle", "sweep", "scan_rate", "path", "x_position", "y_position", "confidence")} for r in model.records],
        },
        "data_quality": index.get("quality", {}),
        "selected_profiles": selected_profiles,
        "current_plot_recipe": current_recipe,
        "available_actions": [
            "analyze_cv", "analyze_eis", "analyze_drt", "analyze_tafel_reliability",
            "validate_reconstruction", "generate_report", "open_deareis", "open_pydrttools",
        ],
        "response_contract": {
            "findings": "Evidence-linked observations only",
            "uncertainty": "State limitations and confidence",
            "recommended_analysis": "Reproducible next analysis",
            "next_experiment": "Bounded experimental recommendation",
            "actions": "Machine-readable proposed actions requiring user approval",
        },
    }
    out = _output_root(browser) / "AI_context"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "current_ai_context.json"
    _atomic_json(path, context)
    context["path"] = str(path)
    record_event(browser, "ai_context_built", {"path": str(path), "selected": len(model.paths)})
    return context


ACTION_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "analyze_cv": {"label": "Analyze selected CV branches", "function": analyze_cv, "techniques": ["CV", "LSV", "FSCV", "CV/LSV"]},
    "analyze_eis": {"label": "Run EIS quality and circuit ranking", "function": analyze_eis, "techniques": ["EIS"]},
    "analyze_drt": {"label": "Compare selected DRT spectra", "function": analyze_drt, "techniques": ["DRT"]},
    "analyze_tafel_reliability": {"label": "Assess Tafel fit reliability", "function": analyze_tafel_reliability, "techniques": ["CV", "LSV", "IV/TAFEL"]},
    "validate_reconstruction": {"label": "Cross-validate spatial reconstruction", "function": validate_reconstruction, "techniques": []},
}


def plan_action(browser: Any, request: str) -> Dict[str, Any]:
    text = str(request or "").strip().lower()
    candidates: List[str] = []
    if any(t in text for t in ("cv", "peak", "charge", "cyclic")): candidates.append("analyze_cv")
    if any(t in text for t in ("eis", "impedance", "circuit", "nyquist", "kramers", "kk")): candidates.append("analyze_eis")
    if any(t in text for t in ("drt", "relaxation", "tau")): candidates.append("analyze_drt")
    if any(t in text for t in ("tafel", "butler", "exchange current")): candidates.append("analyze_tafel_reliability")
    if any(t in text for t in ("map", "contour", "interpolation", "uncertainty", "spatial")): candidates.append("validate_reconstruction")
    if not candidates:
        techs = {str(r.get("technique_key") or "").upper() for r in _selection(browser).records}
        if techs & {"CV", "LSV", "FSCV", "CV/LSV"}: candidates.append("analyze_cv")
        if "EIS" in techs: candidates.append("analyze_eis")
        if "DRT" in techs: candidates.append("analyze_drt")
    plan = {
        "schema": "faradaiq-action-plan-v228", "created": _now(), "request": request,
        "selected_paths": [str(p) for p in _selection(browser).paths],
        "actions": [{"id": key, "label": ACTION_DEFINITIONS[key]["label"], "destructive": False, "requires_approval": True} for key in dict.fromkeys(candidates)],
        "status": "ready_for_approval" if candidates else "no_matching_action",
    }
    record_event(browser, "action_plan_created", plan)
    return plan


def execute_plan(browser: Any, plan: Mapping[str, Any]) -> List[Any]:
    results = []
    for action in plan.get("actions", []) or []:
        key = str(action.get("id") or "")
        spec = ACTION_DEFINITIONS.get(key)
        if not spec:
            continue
        results.append(spec["function"](browser))
    record_event(browser, "action_plan_executed", {"actions": [a.get("id") for a in plan.get("actions", [])], "result_count": len(results)})
    return results


def _write_progress_shell(path: Path, title: str, message: str, target: Optional[Path] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if target is None:
        body = f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='2'>
<title>{html.escape(title)}</title><style>body{{font-family:Arial,sans-serif;margin:40px;background:#050505;color:#fff}}.card{{border:1px solid #fff;padding:24px;max-width:900px}}.bar{{height:10px;background:#222;overflow:hidden}}.bar:after{{content:'';display:block;width:35%;height:100%;background:#fff200;animation:m 1.1s infinite alternate}}@keyframes m{{from{{margin-left:0}}to{{margin-left:65%}}}}</style></head>
<body><div class='card'><h2>{html.escape(title)}</h2><p>{html.escape(message)}</p><div class='bar'></div><p>The measured-point preview is available immediately. FaradaIQ is preparing the cached high-resolution view in the background.</p></div></body></html>"""
    else:
        uri = target.resolve().as_uri()
        body = f"<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='0;url={html.escape(uri, quote=True)}'><title>{html.escape(title)}</title></head><body><p>Opening prepared view… <a href='{html.escape(uri, quote=True)}'>Open now</a></p></body></html>"
    path.write_text(body, encoding="utf-8")


def install_progressive_visualization(core: Mapping[str, Any]) -> None:
    from . import multidim_v216 as md
    if getattr(md, "_v228_progressive_installed", False):
        return
    original_generate = md.generate

    def progressive_generate(browser: Any, mode: str, metric: str = "Auto", options: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        # Alt/Shift can request legacy synchronous behavior for debugging.
        if os.environ.get("FARADAIQ_SYNCHRONOUS_VIEWS", "0").lower() in {"1", "true", "yes"}:
            return original_generate(browser, mode, metric, options)
        records = _selected_records(browser)
        if not records:
            raise ValueError("Select numeric technique data before opening a visualization.")
        outdir = md._view_dir(browser)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        shell = outdir / f"{md._slug(getattr(browser, 'base_name', 'data'))}_progressive_{md._slug(mode)}_{stamp}.html"
        _write_progress_shell(shell, f"FaradaIQ {VERSION} progressive visualization", f"Preparing {mode} for {len(records)} selected record(s)…")
        md._open_html_process(shell)
        opts = md._v216_options(options)
        captured = [dict(r) for r in records]

        def worker() -> None:
            try:
                curves = _curves_from_records(browser, captured)
                if not curves:
                    raise ValueError("No compatible numeric curves were loaded from the captured selection.")
                groups = md._curve_groups(curves, min_size=2 if any(x in str(mode).lower() for x in ("hyper", "cube", "stack")) else 1)
                if not groups:
                    raise ValueError("No compatible visualization groups were found.")
                outputs = []
                for i, (label, items) in enumerate(groups, 1):
                    if "altair" in str(mode).lower() or ("2d" in str(mode).lower() and "3d" not in str(mode).lower()):
                        outputs.append(md._make_altair_group(browser, items, metric, label, f"progress_g{i:02d}", opts))
                    elif any(x in str(mode).lower() for x in ("hyper", "cube", "stack")):
                        outputs.append(md._make_hyperspectral_group(browser, items, label, f"progress_g{i:02d}", opts))
                    else:
                        outputs.append(md._make_plotly_group(browser, items, metric, label, f"progress_g{i:02d}", opts))
                if len(outputs) == 1:
                    result = outputs[0]
                else:
                    master = outdir / f"{md._slug(getattr(browser, 'base_name', 'data'))}_progressive_master_{stamp}.html"
                    md._write_master_index(master, f"FaradaIQ {VERSION}: {mode}", outputs)
                    result = {**outputs[0], "html": str(master), "groups": outputs, "group_count": len(outputs)}
                target = Path(str(result["html"]))
                _write_progress_shell(shell, f"FaradaIQ {VERSION} prepared visualization", "Prepared view is ready.", target=target)
                result["progressive_shell"] = str(shell)
                result["progressive"] = True
                record_event(browser, "progressive_visualization_ready", {"mode": mode, "target": str(target), "cache_hit": result.get("cache_hit")})
                try:
                    browser.after(0, lambda: browser.log(f"Prepared {mode} view is ready: {target.name}"))
                except Exception:
                    pass
            except Exception as exc:
                shell.write_text(f"<!doctype html><html><body><h2>Visualization failed</h2><pre>{html.escape(str(exc))}</pre></body></html>", encoding="utf-8")
                record_event(browser, "progressive_visualization_failed", {"mode": mode, "error": str(exc)})
        threading.Thread(target=worker, daemon=True, name="FaradaIQ-v228-progressive-view").start()
        return {"html": str(shell), "engine": f"Progressive {mode}", "metric": metric, "progressive": True, "records": len(records), "label": str(mode)}

    md.generate = progressive_generate
    md._v228_progressive_installed = True


def _latest_analysis_artifacts(browser: Any) -> List[Path]:
    root = _output_root(browser)
    patterns = ["**/*analysis.json", "**/*reliability.json", "**/*validation.json", "**/*peaks.csv", "**/*metrics.csv", "**/*quality.csv", "**/*.png", "**/*.html"]
    files: List[Path] = []
    seen = set()
    for pattern in patterns:
        for p in root.glob(pattern):
            if p.is_file() and p.name not in {"scientific_report.html"}:
                key = str(p.resolve())
                if key not in seen:
                    seen.add(key); files.append(p)
    return sorted(files, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)[:80]


def generate_report(browser: Any) -> Dict[str, Any]:
    index = getattr(browser, "_v228_scientific_index", None) or build_scientific_index(browser)
    ai_context = build_ai_context(browser)
    artifacts = _latest_analysis_artifacts(browser)
    out = _output_root(browser) / "Reports" / f"{_slug(getattr(browser, 'base_name', 'dataset'))}_{int(time.time())}"
    out.mkdir(parents=True, exist_ok=True)
    quality = index.get("quality", {})
    provenance = []
    try:
        lines = _provenance_path(browser).read_text(encoding="utf-8").splitlines()[-100:]
        provenance = [json.loads(line) for line in lines if line.strip()]
    except Exception:
        pass
    report = {
        "schema": REPORT_SCHEMA, "created": _now(), "version": VERSION, "output_dir": str(out),
        "dataset": str(getattr(browser, "base_name", "")), "h5_path": str(getattr(browser, "h5_path", "") or ""),
        "index": {k: index.get(k) for k in ("record_count", "technique_counts", "cache_health")},
        "quality": quality, "ai_context": ai_context, "artifacts": [str(p) for p in artifacts],
        "provenance": provenance,
    }
    _atomic_json(out / "scientific_report.json", report)
    warning_rows = quality.get("warnings", [])[:100]
    artifact_links = "\n".join(f"<li><a href='{html.escape(p.resolve().as_uri(), quote=True)}'>{html.escape(p.name)}</a> <small>{html.escape(str(p.parent.name))}</small></li>" for p in artifacts)
    warning_html = "\n".join(f"<tr><td>{html.escape(str(w.get('severity','')))}</td><td>{html.escape(str(w.get('message','')))}</td><td>{html.escape(Path(str(w.get('path') or '')).name)}</td></tr>" for w in warning_rows)
    tech_html = "".join(f"<li>{html.escape(str(k))}: {int(v)}</li>" for k, v in (index.get("technique_counts") or {}).items())
    body = f"""<!doctype html><html><head><meta charset='utf-8'><title>FaradaIQ scientific report</title>
<style>body{{font-family:Arial,sans-serif;margin:32px;line-height:1.45}}h1,h2{{color:#123}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #aaa;padding:6px;text-align:left}}code{{background:#eee;padding:2px 4px}}.score{{font-size:28px;font-weight:bold}}</style></head><body>
<h1>FaradaIQ {VERSION} scientific report</h1><p><b>Dataset:</b> {html.escape(str(report['dataset']))}<br><b>Created:</b> {html.escape(report['created'])}<br><b>Canonical H5:</b> <code>{html.escape(report['h5_path'])}</code></p>
<h2>Dataset summary</h2><p>Indexed records: {int(index.get('record_count') or 0)}</p><ul>{tech_html}</ul>
<h2>Data quality</h2><p class='score'>{int(quality.get('score') or 0)}/100 — {html.escape(str(quality.get('grade') or ''))}</p>
<table><thead><tr><th>Severity</th><th>Finding</th><th>Source</th></tr></thead><tbody>{warning_html or '<tr><td colspan=3>No quality warnings were recorded.</td></tr>'}</tbody></table>
<h2>Analysis artifacts</h2><ul>{artifact_links or '<li>No native analysis artifacts generated yet.</li>'}</ul>
<h2>Reproducibility and provenance</h2><p>Machine-readable report: <a href='{(out / 'scientific_report.json').resolve().as_uri()}'>scientific_report.json</a><br>Structured AI context: <a href='{Path(ai_context['path']).resolve().as_uri()}'>current_ai_context.json</a><br>Provenance events included: {len(provenance)}</p>
<h2>Interpretation guardrail</h2><p>Automated fits, smoothing, interpolation, quality scores, and AI suggestions are decision support. They do not validate a mechanism or replace inspection of raw data, residuals, experimental controls, and uncertainty.</p>
</body></html>"""
    html_path = out / "scientific_report.html"
    html_path.write_text(body, encoding="utf-8")
    report["html"] = str(html_path)
    # DOCX is optional but supported when python-docx is available.
    try:
        from docx import Document
        doc = Document(); doc.add_heading(f"FaradaIQ {VERSION} scientific report", 0)
        doc.add_paragraph(f"Dataset: {report['dataset']}"); doc.add_paragraph(f"Created: {report['created']}")
        doc.add_heading("Dataset summary", level=1); doc.add_paragraph(f"Indexed records: {index.get('record_count', 0)}")
        for k, v in (index.get("technique_counts") or {}).items(): doc.add_paragraph(f"{k}: {v}", style="List Bullet")
        doc.add_heading("Data quality", level=1); doc.add_paragraph(f"{quality.get('score', 0)}/100 — {quality.get('grade', '')}")
        for w in warning_rows[:40]: doc.add_paragraph(f"[{w.get('severity','')}] {w.get('message','')}", style="List Bullet")
        doc.add_heading("Analysis artifacts", level=1)
        for p in artifacts[:40]: doc.add_paragraph(str(p), style="List Bullet")
        doc.add_heading("Interpretation guardrail", level=1); doc.add_paragraph("Automated analyses are decision support and do not constitute mechanism validation.")
        docx_path = out / "scientific_report.docx"; doc.save(docx_path); report["docx"] = str(docx_path)
    except Exception as exc:
        report["docx_warning"] = str(exc)
    # PDF via Matplotlib's PDF backend, avoiding browser/office dependencies.
    try:
        from matplotlib.backends.backend_pdf import PdfPages
        import matplotlib.pyplot as plt
        pdf_path = out / "scientific_report.pdf"
        with PdfPages(pdf_path) as pdf:
            fig = plt.figure(figsize=(8.27, 11.69)); fig.patch.set_facecolor("white")
            text = f"FaradaIQ {VERSION} scientific report\n\nDataset: {report['dataset']}\nCreated: {report['created']}\n\nIndexed records: {index.get('record_count',0)}\nQuality: {quality.get('score',0)}/100 — {quality.get('grade','')}\n\nTechniques:\n" + "\n".join(f"  {k}: {v}" for k,v in (index.get('technique_counts') or {}).items())
            text += "\n\nKey quality findings:\n" + "\n".join(f"  • {w.get('message','')}" for w in warning_rows[:18])
            fig.text(0.07, 0.95, text, va="top", fontsize=10, wrap=True); plt.axis("off"); pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)
        report["pdf"] = str(pdf_path)
    except Exception as exc:
        report["pdf_warning"] = str(exc)
    _atomic_json(out / "scientific_report.json", report)
    record_event(browser, "scientific_report_generated", report)
    return report


def _open_path(path: Path) -> None:
    path = Path(path)
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif path.is_file() and path.suffix.lower() in {".html", ".htm"}:
            webbrowser.open(path.resolve().as_uri())
        else:
            import subprocess
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
    except Exception:
        try:
            webbrowser.open(path.resolve().as_uri())
        except Exception:
            pass


def _result_output(result: Any) -> Optional[Path]:
    if isinstance(result, Mapping):
        for key in ("html", "output_dir", "figure_png", "pdf", "docx", "path"):
            value = result.get(key)
            if value:
                return Path(str(value))
    return None


def _friendly_error(browser: Any, operation: str, exc: Exception) -> None:
    diagnostics = {
        "schema": "faradaiq-error-diagnostic-v228", "created": _now(), "operation": operation,
        "error_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc(),
        "selection": [str(p) for p in _selection(browser).paths],
        "index_path": str((_output_root(browser) / "scientific_index_v228.json")),
    }
    out = _output_root(browser) / "Diagnostics"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"error_{_slug(operation)}_{int(time.time())}.json"
    _atomic_json(path, diagnostics)
    record_event(browser, "operation_failed", {"operation": operation, "error": str(exc), "diagnostic": str(path)})
    try:
        from tkinter import messagebox
        messagebox.showerror(
            f"{operation} failed",
            f"What failed\n{operation}\n\nWhy\n{exc}\n\nWhat FaradaIQ did\nThe source data were not changed. A diagnostic bundle was written to:\n{path}\n\nOpen Activity for the full traceback.",
            parent=browser,
        )
    except Exception:
        pass
    try:
        browser.log(f"{operation} failed: {exc}\nDiagnostic: {path}\n{diagnostics['traceback']}")
    except Exception:
        pass


def run_background(browser: Any, operation: str, function: Callable[[], Any]) -> None:
    if getattr(browser, "_v228_operation_running", False):
        try:
            from tkinter import messagebox
            messagebox.showinfo("FaradaIQ is busy", "Another scientific-intelligence operation is still running.", parent=browser)
        except Exception:
            pass
        return
    browser._v228_operation_running = True
    try:
        browser._v228_operation_var.set(f"Running: {operation}…")
    except Exception:
        pass
    record_event(browser, "operation_started", {"operation": operation})

    def worker() -> None:
        try:
            result = function()
            out = _result_output(result)
            summary = f"Completed: {operation}"
            if out:
                summary += f"\n\nOutput:\n{out}"
            try:
                browser.after(0, lambda: browser._v228_operation_var.set(summary.replace("\n", " • ")))
                browser.after(0, lambda: getattr(browser, "_v228_refresh_overview", lambda: None)())
            except Exception:
                pass
            try:
                from tkinter import messagebox
                browser.after(0, lambda: messagebox.showinfo(operation, summary, parent=browser))
            except Exception:
                pass
        except Exception as exc:
            try:
                browser.after(0, lambda e=exc: _friendly_error(browser, operation, e))
                browser.after(0, lambda e=exc: browser._v228_operation_var.set(f"Failed: {operation} • {e}"))
            except Exception:
                pass
        finally:
            browser._v228_operation_running = False
    threading.Thread(target=worker, daemon=True, name=f"FaradaIQ-v228-{_slug(operation)}").start()


def _snapshot(browser: Any) -> Dict[str, Any]:
    attrs = [
        "plot_mode_var", "plot_xscale_var", "plot_yscale_var", "plot_grid_var", "plot_xmin_var", "plot_xmax_var", "plot_ymin_var", "plot_ymax_var",
        "_v216_render_mode_var", "_v216_interpolation_var", "_v216_contour_levels_var", "_v216_palette_var", "_v216_reverse_palette_var",
        "_v216_range_mode_var", "_v216_smoothing_var", "_v216_mask_hull_var", "_v216_overlay_points_var", "_v222_surface_model_var",
        "_v222_tip_diameter_var", "_v222_overlap_var", "_v227_aspect_var",
    ]
    values = {}
    for attr in attrs:
        try:
            values[attr] = getattr(browser, attr).get()
        except Exception:
            pass
    try:
        selection = list(browser.tree.selection())
    except Exception:
        selection = []
    return {"selection": selection, "values": values, "geometry": browser.geometry(), "time": _now()}


def _apply_snapshot(browser: Any, state: Mapping[str, Any]) -> None:
    browser._v228_applying_state = True
    try:
        for attr, value in (state.get("values") or {}).items():
            try:
                getattr(browser, attr).set(value)
            except Exception:
                pass
        try:
            browser.tree.selection_set([iid for iid in state.get("selection", []) if browser.tree.exists(iid)])
            if state.get("selection"):
                browser.tree.see(state["selection"][0])
        except Exception:
            pass
        try:
            browser.geometry(str(state.get("geometry")))
        except Exception:
            pass
    finally:
        browser._v228_applying_state = False


def push_state(browser: Any, reason: str = "change") -> None:
    if getattr(browser, "_v228_applying_state", False):
        return
    state = _snapshot(browser)
    stack = getattr(browser, "_v228_undo_stack", None)
    if stack is None:
        stack = []; browser._v228_undo_stack = stack
    if stack and stack[-1].get("selection") == state.get("selection") and stack[-1].get("values") == state.get("values"):
        return
    stack.append(state)
    if len(stack) > 100: del stack[:-100]
    browser._v228_redo_stack = []
    record_event(browser, "state_checkpoint", {"reason": reason, "checkpoint": len(stack)})


def undo_state(browser: Any) -> None:
    stack = getattr(browser, "_v228_undo_stack", [])
    if len(stack) < 2:
        return
    current = stack.pop()
    browser._v228_redo_stack.append(current)
    _apply_snapshot(browser, stack[-1])
    record_event(browser, "undo", {"remaining": len(stack)})


def redo_state(browser: Any) -> None:
    redo = getattr(browser, "_v228_redo_stack", [])
    if not redo:
        return
    state = redo.pop()
    browser._v228_undo_stack.append(state)
    _apply_snapshot(browser, state)
    record_event(browser, "redo", {"remaining": len(redo)})


def _pane_state(browser: Any) -> List[Dict[str, Any]]:
    out = []
    def walk(widget: Any, prefix: str = "root") -> None:
        try:
            cls = widget.winfo_class()
            children = widget.winfo_children()
            if cls == "TPanedwindow":
                positions = []
                for i in range(max(0, len(widget.panes()) - 1)):
                    try: positions.append(int(widget.sashpos(i)))
                    except Exception: pass
                out.append({"path": prefix, "positions": positions})
            for i, child in enumerate(children):
                walk(child, f"{prefix}/{i}")
        except Exception:
            pass
    walk(browser)
    return out


def _layout_profiles_path(browser: Any) -> Path:
    return _output_root(browser) / "layout_profiles_v228.json"


def save_layout_profile(browser: Any, name: str) -> None:
    path = _layout_profiles_path(browser)
    try: data = json.loads(path.read_text(encoding="utf-8"))
    except Exception: data = {"schema": "faradaiq-layout-profiles-v228", "profiles": {}}
    data.setdefault("profiles", {})[name] = {"geometry": browser.geometry(), "panes": _pane_state(browser), "state": _snapshot(browser), "saved": _now()}
    _atomic_json(path, data)
    record_event(browser, "layout_profile_saved", {"name": name, "path": str(path)})


def apply_layout_profile(browser: Any, name: str) -> None:
    presets = {
        "Browse": "1500x880", "Spatial mapping": "1700x980", "EIS and DRT": "1650x950",
        "CV analysis": "1600x920", "Publication": "1500x950", "Laptop": "1180x760", "Dual monitor": "1900x1050",
    }
    if name in presets:
        browser.geometry(presets[name])
        record_event(browser, "layout_preset_applied", {"name": name, "geometry": presets[name]})
        return
    try:
        data = json.loads(_layout_profiles_path(browser).read_text(encoding="utf-8"))
        profile = data.get("profiles", {}).get(name)
        if not profile: raise KeyError(name)
        _apply_snapshot(browser, profile.get("state") or {})
        browser.geometry(profile.get("geometry") or browser.geometry())
        record_event(browser, "layout_profile_applied", {"name": name})
    except Exception as exc:
        raise ValueError(f"Layout profile '{name}' could not be loaded: {exc}")


def open_command_palette(browser: Any, core: Mapping[str, Any]) -> None:
    tk = core["tk"]; ttk = core["ttk"]
    win = tk.Toplevel(browser); win.title(f"FaradaIQ {VERSION} command palette"); win.geometry("720x520"); win.transient(browser)
    try: core.get("_v36_apply_theme", lambda *_a: None)(win, getattr(browser.gui, "_faradaiq_theme_mode", "high contrast"))
    except Exception: pass
    outer = ttk.Frame(win, style="Card.TFrame", padding=(10, 10)); outer.pack(fill="both", expand=True)
    query = tk.StringVar(value="")
    entry = ttk.Entry(outer, textvariable=query, font=("Segoe UI", 12)); entry.pack(fill="x", pady=(0, 8)); entry.focus_set()
    box = tk.Listbox(outer, exportselection=False, font=("Segoe UI", 10)); box.pack(fill="both", expand=True)
    try: browser._style_text(box)
    except Exception: pass
    commands: List[Tuple[str, Callable[[], Any]]] = [
        ("Plot selected in Quick Plot", lambda: browser.plot_selected(clear=True)),
        ("Overlay selected on current plot", lambda: browser.plot_selected(clear=False)),
        ("Advise visualization for selected data", lambda: run_background(browser, "Visualization advice", lambda: visualization_advice(browser))),
        ("Validate spatial reconstruction and uncertainty", lambda: run_background(browser, "Reconstruction validation", lambda: validate_reconstruction(browser))),
        ("Analyze selected CV branches", lambda: run_background(browser, "CV analysis", lambda: analyze_cv(browser))),
        ("Analyze selected EIS and rank circuits", lambda: run_background(browser, "EIS intelligence", lambda: analyze_eis(browser))),
        ("Compare selected DRT spectra", lambda: run_background(browser, "DRT comparison", lambda: analyze_drt(browser))),
        ("Assess Tafel reliability", lambda: run_background(browser, "Tafel reliability", lambda: analyze_tafel_reliability(browser))),
        ("Generate reproducible scientific report", lambda: run_background(browser, "Scientific report", lambda: generate_report(browser))),
        ("Build structured AI context", lambda: run_background(browser, "AI context", lambda: build_ai_context(browser))),
        ("Rebuild scientific index and data quality", lambda: schedule_index(browser, force=True)),
        ("Undo last browser/plot setting change", lambda: undo_state(browser)),
        ("Redo browser/plot setting change", lambda: redo_state(browser)),
        ("Open DearEIS with selected EIS", lambda: core.get("_v138_open_deareis_with_selected", lambda *_a, **_k: None)(browser, launch=True)),
        ("Open pyDRTtools with selected EIS", lambda: core.get("_v225_open_pydrttools_with_selected", lambda *_a, **_k: None)(browser)),
        ("Open analysis output folder", lambda: _open_path(_output_root(browser))),
    ]
    filtered = commands[:]
    def refresh(*_a: Any) -> None:
        nonlocal filtered
        tokens = query.get().lower().split()
        filtered = [c for c in commands if all(t in c[0].lower() for t in tokens)]
        box.delete(0, "end")
        for label, _ in filtered: box.insert("end", label)
        if filtered: box.selection_set(0)
    def run(_event: Any = None) -> None:
        sel = box.curselection()
        if not sel: return
        callback = filtered[sel[0]][1]; win.destroy(); callback()
    query.trace_add("write", refresh); box.bind("<Double-1>", run); entry.bind("<Return>", run); win.bind("<Escape>", lambda _e: win.destroy())
    refresh()


def _set_text(widget: Any, text: str) -> None:
    try:
        widget.configure(state="normal"); widget.delete("1.0", "end"); widget.insert("1.0", text); widget.configure(state="disabled")
    except Exception:
        pass


def _quality_text(browser: Any) -> str:
    index = getattr(browser, "_v228_scientific_index", {}) or {}
    q = index.get("quality", {})
    lines = [
        f"Scientific index: {index.get('record_count', 0)} record(s)",
        f"Cache reuse: {int(100 * float((index.get('cache_health') or {}).get('profile_reuse_fraction', 0)))}%",
        f"Data quality: {q.get('score', 0)}/100 — {q.get('grade', 'not ready')}",
        "",
    ]
    for item in (q.get("warnings") or [])[:80]:
        lines.append(f"[{str(item.get('severity','warning')).upper()}] {item.get('message','')}" + (f" — {Path(str(item.get('path'))).name}" if item.get("path") else ""))
    if len(lines) == 4:
        lines.append("No quality findings are available yet. Rebuild the scientific index.")
    return "\n".join(lines)


def _overview_text(browser: Any) -> str:
    model = _selection(browser)
    index = getattr(browser, "_v228_scientific_index", {}) or {}
    techs = Counter(str(r.get("technique_key") or "OTHER") for r in model.records)
    recent = getattr(browser, "_v228_action_history", [])[-8:]
    lines = [
        f"Dataset: {getattr(browser, 'base_name', '')}",
        f"Indexed records: {index.get('record_count', 0)}",
        f"Quality: {(index.get('quality') or {}).get('score', 0)}/100 — {(index.get('quality') or {}).get('grade', 'not ready')}",
        f"Current selection: {len(model.paths)} item(s) from {model.source}",
        "Selected techniques: " + (", ".join(f"{k} ({v})" for k, v in techs.items()) if techs else "none"),
        "",
        "Recent actions:",
    ]
    lines.extend(f"• {e.get('timestamp','')} — {e.get('event','')}" for e in recent)
    return "\n".join(lines)


def _build_intelligence_tab(browser: Any, parent: Any, core: Mapping[str, Any]) -> None:
    tk = core["tk"]; ttk = core["ttk"]
    browser._v228_index_status_var = tk.StringVar(value="Scientific index waiting for catalog…")
    browser._v228_operation_var = tk.StringVar(value="Ready")
    header = ttk.Frame(parent, style="Card.TFrame"); header.pack(fill="x", pady=(0, 6))
    ttk.Label(header, text="Scientific intelligence", style="Section.TLabel").pack(side="left")
    ttk.Button(header, text="Command palette  Ctrl+K", command=lambda: open_command_palette(browser, core)).pack(side="right")
    ttk.Label(parent, textvariable=browser._v228_index_status_var, style="Muted.TLabel", wraplength=400).pack(fill="x", anchor="w")
    ttk.Label(parent, textvariable=browser._v228_operation_var, style="Muted.TLabel", wraplength=400).pack(fill="x", anchor="w", pady=(0, 6))
    tabs = ttk.Notebook(parent); tabs.pack(fill="both", expand=True)
    overview = ttk.Frame(tabs, style="Card.TFrame", padding=(6, 6)); tabs.add(overview, text="Overview")
    analyze = ttk.Frame(tabs, style="Card.TFrame", padding=(6, 6)); tabs.add(analyze, text="Analyze")
    quality = ttk.Frame(tabs, style="Card.TFrame", padding=(6, 6)); tabs.add(quality, text="Quality")
    history = ttk.Frame(tabs, style="Card.TFrame", padding=(6, 6)); tabs.add(history, text="History")

    browser._v228_overview_text = tk.Text(overview, wrap="word", height=14)
    browser._v228_overview_text.pack(fill="both", expand=True)
    try: browser._style_text(browser._v228_overview_text)
    except Exception: pass
    o_buttons = ttk.Frame(overview, style="Card.TFrame"); o_buttons.pack(fill="x", pady=(6, 0))
    ttk.Button(o_buttons, text="Rebuild index", command=lambda: schedule_index(browser, force=True)).pack(side="left", fill="x", expand=True, padx=(0, 2))
    ttk.Button(o_buttons, text="Generate report", command=lambda: run_background(browser, "Scientific report", lambda: generate_report(browser))).pack(side="left", fill="x", expand=True, padx=2)
    ttk.Button(o_buttons, text="Open outputs", command=lambda: _open_path(_output_root(browser))).pack(side="left", fill="x", expand=True, padx=(2, 0))

    # Technique-aware analysis controls. Buttons are retained but disabled when
    # the unified selection does not contain the corresponding technique.
    selection_var = tk.StringVar(value="Select data in the experiment tree or Plot/views.")
    ttk.Label(analyze, textvariable=selection_var, style="Muted.TLabel", wraplength=390).pack(fill="x", anchor="w", pady=(0, 5))
    advisor = ttk.LabelFrame(analyze, text="Visualization intelligence", padding=(5, 5)); advisor.pack(fill="x", pady=(0, 5))
    advice_text = tk.Text(advisor, wrap="word", height=5); advice_text.pack(fill="x", pady=(0, 4))
    try: browser._style_text(advice_text)
    except Exception: pass
    browser._v228_last_advice = None
    def advise() -> None:
        try:
            result = visualization_advice(browser); browser._v228_last_advice = result
            _set_text(advice_text, result.get("explanation", "") + "\n\n" + json.dumps(result, indent=2, default=str))
        except Exception as exc: _friendly_error(browser, "Visualization advice", exc)
    arow = ttk.Frame(advisor, style="Card.TFrame"); arow.pack(fill="x")
    ttk.Button(arow, text="Advise", command=advise).pack(side="left", fill="x", expand=True, padx=(0, 2))
    ttk.Button(arow, text="Apply advice", command=lambda: apply_visualization_advice(browser, browser._v228_last_advice or visualization_advice(browser))).pack(side="left", fill="x", expand=True, padx=2)
    ttk.Button(arow, text="Validate + uncertainty", command=lambda: run_background(browser, "Reconstruction validation", lambda: validate_reconstruction(browser))).pack(side="left", fill="x", expand=True, padx=(2, 0))

    native = ttk.LabelFrame(analyze, text="Native technique analysis", padding=(5, 5)); native.pack(fill="x", pady=(0, 5))
    browser._v228_cv_btn = ttk.Button(native, text="CV peaks, charge, branches, maps", command=lambda: run_background(browser, "CV analysis", lambda: analyze_cv(browser))); browser._v228_cv_btn.pack(fill="x", pady=2)
    browser._v228_eis_btn = ttk.Button(native, text="EIS quality, lin-KK, circuits, identifiability", command=lambda: run_background(browser, "EIS intelligence", lambda: analyze_eis(browser))); browser._v228_eis_btn.pack(fill="x", pady=2)
    browser._v228_drt_btn = ttk.Button(native, text="DRT overlay, peak tracking, area, τ", command=lambda: run_background(browser, "DRT comparison", lambda: analyze_drt(browser))); browser._v228_drt_btn.pack(fill="x", pady=2)
    browser._v228_tafel_btn = ttk.Button(native, text="Tafel reliability and branch diagnostics", command=lambda: run_background(browser, "Tafel reliability", lambda: analyze_tafel_reliability(browser))); browser._v228_tafel_btn.pack(fill="x", pady=2)

    ai = ttk.LabelFrame(analyze, text="AI action preview and approval", padding=(5, 5)); ai.pack(fill="both", expand=True)
    request_var = tk.StringVar(value="")
    ttk.Entry(ai, textvariable=request_var).pack(fill="x", pady=(0, 4))
    plan_text = tk.Text(ai, wrap="word", height=7); plan_text.pack(fill="both", expand=True)
    try: browser._style_text(plan_text)
    except Exception: pass
    browser._v228_pending_plan = None
    def preview_plan() -> None:
        plan = plan_action(browser, request_var.get()); browser._v228_pending_plan = plan; _set_text(plan_text, json.dumps(plan, indent=2))
    def execute_pending() -> None:
        plan = browser._v228_pending_plan or plan_action(browser, request_var.get())
        if not plan.get("actions"):
            _set_text(plan_text, json.dumps(plan, indent=2)); return
        try:
            from tkinter import messagebox
            labels = "\n".join(f"• {a.get('label')}" for a in plan.get("actions", []))
            if not messagebox.askyesno("Approve analysis actions", f"Execute these non-destructive actions?\n\n{labels}", parent=browser): return
        except Exception: pass
        run_background(browser, "Approved analysis plan", lambda: execute_plan(browser, plan))
    prow = ttk.Frame(ai, style="Card.TFrame"); prow.pack(fill="x", pady=(4, 0))
    ttk.Button(prow, text="Preview plan", command=preview_plan).pack(side="left", fill="x", expand=True, padx=(0, 2))
    ttk.Button(prow, text="Approve + execute", command=execute_pending).pack(side="left", fill="x", expand=True, padx=2)
    ttk.Button(prow, text="Build AI context", command=lambda: run_background(browser, "AI context", lambda: build_ai_context(browser))).pack(side="left", fill="x", expand=True, padx=(2, 0))

    qhead = ttk.Frame(quality, style="Card.TFrame"); qhead.pack(fill="x")
    ttk.Label(qhead, text="Automatic data-quality report", style="Section.TLabel").pack(side="left")
    ttk.Button(qhead, text="Refresh", command=lambda: _set_text(browser._v228_quality_text, _quality_text(browser))).pack(side="right")
    browser._v228_quality_text = tk.Text(quality, wrap="word", height=22); browser._v228_quality_text.pack(fill="both", expand=True, pady=(4, 0))
    try: browser._style_text(browser._v228_quality_text)
    except Exception: pass

    hrow = ttk.Frame(history, style="Card.TFrame"); hrow.pack(fill="x", pady=(0, 5))
    ttk.Button(hrow, text="Checkpoint", command=lambda: push_state(browser, "manual")).pack(side="left", fill="x", expand=True, padx=(0, 2))
    ttk.Button(hrow, text="Undo", command=lambda: undo_state(browser)).pack(side="left", fill="x", expand=True, padx=2)
    ttk.Button(hrow, text="Redo", command=lambda: redo_state(browser)).pack(side="left", fill="x", expand=True, padx=(2, 0))
    layout = ttk.LabelFrame(history, text="Layout profiles", padding=(5, 5)); layout.pack(fill="x", pady=(0, 5))
    layout_var = tk.StringVar(value="Browse")
    ttk.Combobox(layout, textvariable=layout_var, state="readonly", values=("Browse", "Spatial mapping", "EIS and DRT", "CV analysis", "Publication", "Laptop", "Dual monitor"), width=22).pack(side="left", fill="x", expand=True, padx=(0, 3))
    ttk.Button(layout, text="Apply", command=lambda: apply_layout_profile(browser, layout_var.get())).pack(side="left", padx=3)
    def save_named() -> None:
        try:
            from tkinter import simpledialog
            name = simpledialog.askstring("Save layout profile", "Profile name:", parent=browser)
            if name: save_layout_profile(browser, name)
        except Exception: pass
    ttk.Button(layout, text="Save current", command=save_named).pack(side="left", padx=(3, 0))
    browser._v228_history_list = tk.Listbox(history, height=14, exportselection=False); browser._v228_history_list.pack(fill="both", expand=True)
    try: browser._style_text(browser._v228_history_list)
    except Exception: pass

    def refresh_history() -> None:
        try:
            browser._v228_history_list.delete(0, "end")
            for event in reversed(getattr(browser, "_v228_action_history", [])[-200:]):
                browser._v228_history_list.insert("end", f"{event.get('timestamp','')}  {event.get('event','')}")
        except Exception: pass
    browser._v228_refresh_history = refresh_history

    def refresh_overview() -> None:
        _set_text(browser._v228_overview_text, _overview_text(browser))
        _set_text(browser._v228_quality_text, _quality_text(browser))
        refresh_history()
    browser._v228_refresh_overview = refresh_overview

    def refresh_context(_event: Any = None) -> None:
        model = _selection(browser)
        techs = {str(r.get("technique_key") or "").upper() for r in model.records}
        selection_var.set(f"{len(model.paths)} selected item(s) from {model.source} • " + (", ".join(sorted(techs)) if techs else "no technique"))
        for button, enabled in (
            (browser._v228_cv_btn, bool(techs & {"CV", "LSV", "FSCV", "CV/LSV"})),
            (browser._v228_eis_btn, "EIS" in techs),
            (browser._v228_drt_btn, "DRT" in techs),
            (browser._v228_tafel_btn, bool(techs & {"CV", "LSV", "FSCV", "CV/LSV", "IV/TAFEL"})),
        ):
            try: button.configure(state="normal" if enabled else "disabled")
            except Exception: pass
        refresh_overview()
        if not getattr(browser, "_v228_applying_state", False):
            try:
                if getattr(browser, "_v228_checkpoint_after", None): browser.after_cancel(browser._v228_checkpoint_after)
            except Exception: pass
            try: browser._v228_checkpoint_after = browser.after(250, lambda: push_state(browser, "selection/settings"))
            except Exception: pass
    browser._v228_refresh_context = refresh_context
    try: browser.tree.bind("<<TreeviewSelect>>", refresh_context, add="+")
    except Exception: pass
    try: browser.plot_view_list.bind("<<ListboxSelect>>", refresh_context, add="+")
    except Exception: pass
    # Track key plot/visualization variables for undo/redo without flooding history.
    for attr in ("plot_mode_var", "plot_xscale_var", "plot_yscale_var", "plot_grid_var", "_v216_render_mode_var", "_v216_interpolation_var", "_v216_palette_var", "_v216_smoothing_var", "_v222_surface_model_var", "_v227_aspect_var"):
        try: getattr(browser, attr).trace_add("write", lambda *_a: refresh_context())
        except Exception: pass
    push_state(browser, "browser opened")
    refresh_context()


def _find_header_frame(browser: Any) -> Optional[Any]:
    try:
        # The browser's outer frame contains the header as its first child.
        for child in browser.winfo_children():
            grandchildren = child.winfo_children()
            if grandchildren:
                return grandchildren[0]
    except Exception:
        pass
    return None


def install_ui(core: MutableMapping[str, Any]) -> None:
    Browser = core.get("FaradaIQH5Browser"); ttk = core.get("ttk"); tk = core.get("tk")
    if Browser is None or ttk is None or tk is None or getattr(Browser, "_v228_intelligence_installed", False):
        return
    previous_build = Browser._build_visible_ui_first
    previous_selected_paths = Browser.selected_paths
    def build(self: Any) -> None:
        previous_build(self)
        self._v228_selection = UnifiedSelection(self)
        # Add command palette to the visible header.
        try:
            header = _find_header_frame(self)
            if header is not None:
                ttk.Button(header, text="Command palette", command=lambda: open_command_palette(self, core)).pack(side="right", padx=3)
        except Exception: pass
        # Add a fifth task-oriented workspace tab without disturbing v226 tabs.
        try:
            notebook = getattr(self, "_v226_side_notebook", None)
            if notebook is not None:
                intelligence = ttk.Frame(notebook, style="Card.TFrame", padding=(7, 7))
                notebook.add(intelligence, text="Intelligence")
                _build_intelligence_tab(self, intelligence, core)
                self._v228_intelligence_tab = intelligence
        except Exception as exc:
            try: self.log(f"Scientific intelligence UI warning: {exc}")
            except Exception: pass
        try:
            self.bind("<Control-k>", lambda _e: open_command_palette(self, core), add="+")
            self.bind("<Control-K>", lambda _e: open_command_palette(self, core), add="+")
        except Exception: pass
        try: self.after(250, lambda: schedule_index(self, force=False))
        except Exception: schedule_index(self, force=False)
    def selected_paths(self: Any, plottable_only: bool = True) -> List[Path]:
        try:
            paths = previous_selected_paths(self, plottable_only=plottable_only)
            if paths: return paths
        except Exception:
            pass
        return _selection(self).selected_paths(plottable_only=plottable_only)
    Browser._build_visible_ui_first = build
    Browser.selected_paths = selected_paths
    # Re-index after catalog refresh. The compatibility contour prebuild remains active.
    previous_refresh = Browser.refresh_tree
    def refresh(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = previous_refresh(self, *args, **kwargs)
        try: self.after(300, lambda: schedule_index(self, force=False))
        except Exception: pass
        return result
    Browser.refresh_tree = refresh
    Browser._v228_intelligence_installed = True


def install(core: MutableMapping[str, Any]) -> None:
    """Install all coordinated v228 improvements."""
    install_progressive_visualization(core)
    install_ui(core)
    core["_v228_build_scientific_index"] = build_scientific_index
    core["_v228_schedule_index"] = schedule_index
    core["_v228_visualization_advice"] = visualization_advice
    core["_v228_validate_reconstruction"] = validate_reconstruction
    core["_v228_analyze_cv"] = analyze_cv
    core["_v228_analyze_eis"] = analyze_eis
    core["_v228_analyze_drt"] = analyze_drt
    core["_v228_analyze_tafel_reliability"] = analyze_tafel_reliability
    core["_v228_build_ai_context"] = build_ai_context
    core["_v228_generate_report"] = generate_report
    help_tabs = core.get("_V207_HELP_TABS")
    title = "v232 scientific intelligence, progressive views, quality, analysis, provenance, and reports"
    if isinstance(help_tabs, list) and not any(str(x[0]) == title for x in help_tabs if isinstance(x, (list, tuple)) and x):
        help_tabs.append((title, r"""
SCIENTIFIC INTELLIGENCE WORKSPACE

The browser includes an Intelligence workspace with Overview, Analyze, Quality, and History tabs. Ctrl+K opens a command palette. Controls are technique-aware and use one synchronized selection from the experiment tree or Plot/views.

INSTANT INDEX AND PREPARED CACHE

FaradaIQ writes an incremental scientific index containing source signatures, technique/method/point/cycle metadata, column roles, robust ranges, quality checks, and cache health. Unchanged source profiles are reused. The existing contour/cube prebuilder remains active, and v228 visualization windows open a progressive shell immediately while the final cached view is prepared in the background.

VISUALIZATION INTELLIGENCE

Visualization advice considers coordinate availability, regular/scattered geometry, duplicate locations, dynamic range, and outliers. Reconstruction validation compares nearest, linear, cubic, inverse-distance, thin-plate RBF, and Gaussian RBF models with held-out measured points. It writes model errors, the chosen reconstruction, and an uncertainty map combining model disagreement with distance from measurements. Interpolation uncertainty is decision support and is not a probabilistic confidence interval.

NATIVE ANALYSIS

CV analysis separates acquisition-order branches, subtracts a linear endpoint baseline, extracts anodic/cathodic peak current and potential, calculates peak separation and current ratio, integrates I dE, and reports charge when scan rate is available. Spatial metric files and maps are generated when coordinates exist.

EIS intelligence checks frequency order/span, duplicate frequencies, inductive response, optional impedance.py lin-KK consistency, bounded equivalent-circuit fits, AIC/BIC ranking, and Jacobian identifiability. A low residual is not physical validation.

DRT comparison overlays selected DRT spectra and extracts peak amplitude, characteristic relaxation time/frequency, and integrated peak area. Tafel reliability scores fit linearity, point count, overpotential span, and slope plausibility for whole/forward/reverse branches.

QUALITY, PROVENANCE, UNDO, AND REPORTS

Every generated output is non-destructive and stored under Scientific_Intelligence_v237. A provenance JSONL ledger records indexing, selections, analyses, AI context, action approval, reports, undo/redo, and failures. The History tab provides checkpoints, undo/redo of selection and plotting/visualization settings, and layout profiles. Reports export HTML, JSON, PDF, and DOCX when python-docx is available.

AI ACTION PREVIEW

Build AI context creates a structured JSON description of the selected data, units, techniques, methods, points, cycles, quality findings, current plot recipe, and allowed actions. The action planner previews deterministic, non-destructive analysis actions and requires user approval before execution.
"""))
