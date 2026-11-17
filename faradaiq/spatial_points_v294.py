"""FaradaIQ Studio v294 spatial point-coordinate support.

This module keeps point identity and physical HEKA Takedata coordinates tied together.
It never treats explicitly synthetic compatibility XYZ as measured coordinates.

Public helpers are intentionally small and dependency-light so browser catalog,
multidimensional views, OCP handoff, and future point-derived analyses can share
one coordinate contract.
"""
from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

import numpy as np

VERSION = "v294"
AXES = ("Auto", "Point", "X", "Y", "Distance")


def _finite(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _point(record: Mapping[str, Any]) -> int:
    for key in ("point", "point_index", "position_index"):
        try:
            value = int(float(record.get(key)))
            if value > 0:
                return value
        except Exception:
            pass
    return 0


def _text(record: Mapping[str, Any]) -> str:
    meta = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    return " ".join(
        str(x or "") for x in (
            record.get("coordinate_status"), record.get("coordinate_source"),
            meta.get("coordinate_status"), meta.get("coordinate_source"),
            record.get("path"),
        )
    ).lower()


def coordinate_is_physical(record: Mapping[str, Any]) -> bool:
    """True when record coordinates are usable as measured Takedata coordinates."""
    text = _text(record)
    if "synthetic" in text or "ordered_point_line" in text or "not_synthetic_stage" in text:
        return False
    x = coordinate_m(record, "x", allow_synthetic=True)
    y = coordinate_m(record, "y", allow_synthetic=True)
    return x is not None and y is not None


def coordinate_m(record: Mapping[str, Any], axis: str, *, allow_synthetic: bool = False) -> float | None:
    axis = str(axis).lower()[:1]
    if axis not in {"x", "y", "z"}:
        return None
    if not allow_synthetic and ("synthetic" in _text(record) or "ordered_point_line" in _text(record)):
        return None
    candidates = (
        f"{axis}_position_m", f"{axis}_position", f"{axis}_m", f"{axis}_coord",
        f"{axis}_coordinate", f"{axis}_coordinate_m",
    )
    meta = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    for source in (record, meta):
        for key in candidates:
            if key in source:
                value = _finite(source.get(key))
                if value is not None:
                    # Canonical x_position/x_m are SI metres. Unit-suffixed fallbacks
                    # are handled only for explicit keys below.
                    if key.endswith("_um"):
                        return value * 1e-6
                    if key.endswith("_mm"):
                        return value * 1e-3
                    return value
        for suffix, factor in (("um", 1e-6), ("µm", 1e-6), ("mm", 1e-3)):
            key = f"{axis}_{suffix}"
            if key in source:
                value = _finite(source.get(key))
                if value is not None:
                    return value * factor
    return None


def _best_point_coordinates(records: Sequence[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    """Build one physical coordinate row per point from canonical/Takedata metadata."""
    candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        pt = _point(rec)
        if pt <= 0:
            continue
        x = coordinate_m(rec, "x")
        y = coordinate_m(rec, "y")
        z = coordinate_m(rec, "z")
        if x is None or y is None:
            continue
        tech = str(rec.get("technique_key") or "").upper()
        source = str(rec.get("coordinate_source") or (rec.get("metadata") or {}).get("coordinate_source") or "")
        status = str(rec.get("coordinate_status") or (rec.get("metadata") or {}).get("coordinate_status") or "")
        score = 0
        if tech == "POSITION": score += 20
        if "takedata" in (source + " " + status).lower(): score += 10
        if z is not None: score += 1
        candidates[pt].append({"point": pt, "x_m": x, "y_m": y, "z_m": z, "source": source, "status": status, "score": score})
    out: dict[int, dict[str, Any]] = {}
    for pt, rows in candidates.items():
        rows.sort(key=lambda r: (-int(r["score"]), str(r.get("source") or "")))
        out[pt] = dict(rows[0])
    return out


def _scan_distance(point_rows: Mapping[int, Mapping[str, Any]]) -> dict[int, float]:
    """Cumulative XY distance in point/acquisition order, in metres."""
    out: dict[int, float] = {}
    prev: tuple[float, float] | None = None
    cumulative = 0.0
    for pt in sorted(point_rows):
        row = point_rows[pt]
        x, y = _finite(row.get("x_m")), _finite(row.get("y_m"))
        if x is None or y is None:
            continue
        if prev is not None:
            cumulative += math.hypot(x - prev[0], y - prev[1])
        out[pt] = cumulative
        prev = (x, y)
    return out


def enrich_catalog_records(records: Sequence[MutableMapping[str, Any]]) -> dict[str, Any]:
    """Attach physical X/Y/Z and cumulative scan distance to every matching point.

    Existing point views remain untouched. Coordinates are propagated from the most
    authoritative record for the same point, preferentially POSITION/Takedata.
    """
    point_rows = _best_point_coordinates(records)
    distances = _scan_distance(point_rows)
    for rec in records:
        pt = _point(rec)
        spatial = point_rows.get(pt)
        if not spatial:
            rec.setdefault("spatial_coordinate_available", False)
            continue
        rec["x_position_m"] = spatial.get("x_m")
        rec["y_position_m"] = spatial.get("y_m")
        rec["z_position_m"] = spatial.get("z_m")
        # Keep legacy fields populated for existing visualization code.
        if rec.get("x_position") is None: rec["x_position"] = spatial.get("x_m")
        if rec.get("y_position") is None: rec["y_position"] = spatial.get("y_m")
        if rec.get("z_position") is None: rec["z_position"] = spatial.get("z_m")
        rec["scan_distance_m"] = distances.get(pt)
        rec["spatial_coordinate_available"] = True
        rec["spatial_coordinate_source"] = spatial.get("source") or "HEKA Takedata / canonical point metadata"
        rec["spatial_coordinate_status"] = spatial.get("status") or "physical X/Y from canonical point metadata"
    axis = auto_axis_for_records(records)
    for rec in records:
        if rec.get("spatial_coordinate_available"):
            rec["spatial_axis_auto"] = axis
    return {
        "point_count": len(point_rows),
        "distance_point_count": len(distances),
        "auto_axis": axis,
        "source": "HEKA Takedata / canonical point metadata",
    }


def _axis_values(records: Iterable[Mapping[str, Any]], axis: str) -> list[float]:
    out: list[float] = []
    key = {"X": "x_position_m", "Y": "y_position_m", "Distance": "scan_distance_m"}.get(axis)
    if not key:
        return out
    seen: set[int] = set()
    for rec in records:
        pt = _point(rec)
        if pt <= 0 or pt in seen:
            continue
        value = _finite(rec.get(key))
        if value is not None:
            seen.add(pt); out.append(value)
    return out


def auto_axis_for_records(records: Sequence[Mapping[str, Any]]) -> str:
    """Choose a useful 1-D spatial axis while retaining Point as safe fallback."""
    unique: dict[int, tuple[float, float]] = {}
    for rec in records:
        pt = _point(rec)
        x = _finite(rec.get("x_position_m") if rec.get("x_position_m") is not None else rec.get("x_position"))
        y = _finite(rec.get("y_position_m") if rec.get("y_position_m") is not None else rec.get("y_position"))
        if pt > 0 and x is not None and y is not None:
            unique.setdefault(pt, (x, y))
    if len(unique) < 2:
        return "Point"
    pts = np.asarray([unique[p] for p in sorted(unique)], dtype=float)
    xr = float(np.ptp(pts[:, 0])); yr = float(np.ptp(pts[:, 1]))
    scale = max(xr, yr, 1e-30)
    tol = scale * 1e-9
    if xr <= tol and yr <= tol:
        return "Point"
    if yr <= tol:
        return "X"
    if xr <= tol:
        return "Y"
    # For diagonal/curved one-dimensional scans, cumulative distance is the only
    # coordinate guaranteed to remain monotonic in acquisition order.
    centered = pts - np.mean(pts, axis=0, keepdims=True)
    try:
        rank = int(np.linalg.matrix_rank(centered, tol=max(scale * 1e-9, 1e-15)))
    except Exception:
        rank = 2
    if rank <= 1:
        return "Distance"
    return "Point"  # 2-D maps use X-Y map rendering instead of flattening by default.


def available_axes(records: Sequence[Mapping[str, Any]]) -> list[str]:
    axes = ["Point"]
    for axis in ("X", "Y", "Distance"):
        vals = _axis_values(records, axis)
        if vals:
            axes.append(axis)
    return axes


def spatial_axis_value(record: Mapping[str, Any], axis: str) -> float | None:
    axis = str(axis or "Point").title()
    if axis == "Point":
        pt = _point(record)
        return float(pt) if pt > 0 else None
    return _finite(record.get({"X": "x_position_m", "Y": "y_position_m", "Distance": "scan_distance_m"}.get(axis, "")))


def scale_m(values: Sequence[float], unit: str = "µm") -> tuple[np.ndarray, str]:
    arr = np.asarray(values, dtype=float)
    u = str(unit or "µm")
    factors = {"nm": 1e9, "µm": 1e6, "um": 1e6, "mm": 1e3, "m": 1.0}
    if u == "Scientific":
        return arr, "m"
    factor = factors.get(u, 1e6)
    label = "µm" if u == "um" else u
    return arr * factor, label


def format_coordinate(value_m: Any, unit: str = "µm") -> str:
    value = _finite(value_m)
    if value is None:
        return ""
    arr, label = scale_m([value], unit)
    return f"{float(arr[0]):.6g} {label}"


def dataframe_with_scan_distance(df: Any) -> Any:
    """Attach scan_distance_m to a point-metric DataFrame with x_coord/y_coord in m."""
    try:
        import pandas as pd
        out = df.copy()
        if out.empty or not {"point", "x_coord", "y_coord"}.issubset(out.columns):
            return out
        first = out[["point", "x_coord", "y_coord"]].dropna().drop_duplicates(subset=["point"]).sort_values("point")
        cumulative = 0.0; prev = None; mapping: dict[int, float] = {}
        for _, row in first.iterrows():
            x, y = float(row["x_coord"]), float(row["y_coord"]); pt = int(row["point"])
            if prev is not None:
                cumulative += math.hypot(x - prev[0], y - prev[1])
            mapping[pt] = cumulative; prev = (x, y)
        out["scan_distance_m"] = [mapping.get(int(p)) if _finite(p) is not None else np.nan for p in out["point"]]
        out["x_position_m"] = out["x_coord"]
        out["y_position_m"] = out["y_coord"]
        return out
    except Exception:
        return df
