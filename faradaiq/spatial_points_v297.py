"""FaradaIQ Studio v297 Takedata spatial point-coordinate support.

This module keeps point identity and physical HEKA Takedata coordinates tied together.
It never treats explicitly synthetic compatibility XYZ as measured coordinates.

Public helpers are intentionally small and dependency-light so browser catalog,
multidimensional views, OCP handoff, and future point-derived analyses can share
one coordinate contract.
"""
from __future__ import annotations

import csv
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

import numpy as np

VERSION = "v297"
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
            record.get("spatial_coordinate_status"), record.get("spatial_coordinate_source"), record.get("spatial_coordinate_quality"),
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


def _is_synthetic_text(text: str) -> bool:
    low = str(text or "").lower()
    return "synthetic" in low or "ordered_point_line" in low or "fallback" in low and "takedata" in low


def _header_unit_factor(header: str) -> float:
    low = str(header or "").lower().replace("μ", "µ")
    if "[nm]" in low or low.endswith("_nm") or low.endswith(" nm"):
        return 1e-9
    if "[µm]" in low or "[um]" in low or low.endswith("_um") or low.endswith(" µm") or low.endswith(" um"):
        return 1e-6
    if "[mm]" in low or low.endswith("_mm") or low.endswith(" mm"):
        return 1e-3
    return 1.0


def _norm_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower().replace("μ", "u").replace("µ", "u"))


def _coord_column(headers: Sequence[str], axis: str) -> int | None:
    axis = axis.lower()[0]
    norms = [_norm_header(h) for h in headers]
    exact = {
        "x": {"xposm", "xpositionm", "xposition", "xcoord", "xcoordinate", "xm", "xposum", "xpositionum", "xmm"},
        "y": {"yposm", "ypositionm", "yposition", "ycoord", "ycoordinate", "ym", "yposum", "ypositionum", "ymm"},
        "z": {"zposm", "zpositionm", "zposition", "zcoord", "zcoordinate", "zm", "zposum", "zpositionum", "zmm"},
    }[axis]
    for i, n in enumerate(norms):
        if n in exact:
            return i
    for i, n in enumerate(norms):
        if n.startswith(axis) and any(token in n for token in ("pos", "position", "coord")):
            return i
    return None


def _point_column(headers: Sequence[str]) -> int | None:
    for i, h in enumerate(headers):
        n = _norm_header(h)
        if n in {"point", "pointindex", "positionindex", "acquisitionpoint", "acquisitionposition"}:
            return i
    return None


def _split_row(line: str) -> list[str]:
    text = str(line).strip().strip("\ufeff")
    if not text:
        return []
    if "," in text:
        try:
            return [str(x).strip() for x in next(csv.reader([text], skipinitialspace=True))]
        except Exception:
            return [x.strip().strip('"') for x in text.split(",")]
    if "\t" in text:
        return [x.strip().strip('"') for x in text.split("\t")]
    return [x.strip().strip('"') for x in re.split(r"\s{2,}", text) if x.strip()]



def _coord_pair_close(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Compare Takedata XY rows with a scale-aware sub-nanometre floor."""
    ax, ay = _finite(a.get("x_m")), _finite(a.get("y_m"))
    bx, by = _finite(b.get("x_m")), _finite(b.get("y_m"))
    if None in (ax, ay, bx, by):
        return False
    scale = max(abs(float(ax)), abs(float(ay)), abs(float(bx)), abs(float(by)), 1e-9)
    tol = max(1e-12, scale * 1e-8)
    return abs(float(ax) - float(bx)) <= tol and abs(float(ay) - float(by)) <= tol


def _collapse_adjacent_stationary_coordinate_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Collapse consecutive Takedata rows that represent the same acquisition position.

    Some HEKA/SECCM exports write two or more Takedata records at one spatial point
    (for example an entry record and a second record a few seconds later) before
    moving hundreds of micrometres to the next point.  Those are repeated records
    at one point, not additional spatial points.

    The tolerance is derived from the dataset's actual move distances.  A row is
    collapsed only when its XY move is tiny relative to the median substantive
    move, so genuine fine-step scans are preserved.
    """
    data = [dict(r) for r in rows]
    if len(data) < 3:
        return data
    distances: list[float] = []
    for a, b in zip(data[:-1], data[1:]):
        ax, ay = _finite(a.get("x_m")), _finite(a.get("y_m"))
        bx, by = _finite(b.get("x_m")), _finite(b.get("y_m"))
        if None in (ax, ay, bx, by):
            continue
        d = math.hypot(float(bx)-float(ax), float(by)-float(ay))
        if math.isfinite(d) and d > 0:
            distances.append(d)
    if not distances:
        return data
    arr = np.asarray(distances, dtype=float)
    # Estimate the real scan step from the upper half of moves so repeated
    # same-position rows do not drag the median toward zero.
    q50 = float(np.quantile(arr, 0.50))
    substantive = arr[arr >= q50]
    step = float(np.median(substantive)) if substantive.size else float(np.max(arr))
    if not math.isfinite(step) or step <= 0:
        return data
    tol = max(5e-9, step * 0.005)  # 5 nm floor, at most 0.5% of normal move.
    out: list[dict[str, Any]] = []
    for row in data:
        if not out:
            out.append(dict(row)); continue
        a = out[-1]
        ax, ay = _finite(a.get("x_m")), _finite(a.get("y_m"))
        bx, by = _finite(row.get("x_m")), _finite(row.get("y_m"))
        if None not in (ax, ay, bx, by):
            d = math.hypot(float(bx)-float(ax), float(by)-float(ay))
            if d <= tol:
                a["coordinate_repeat_count"] = int(a.get("coordinate_repeat_count") or 1) + 1
                a["coordinate_repeat_pattern"] = "adjacent_same_position"
                a["coordinate_repeat_tolerance_m"] = tol
                status = str(a.get("status") or "")
                extra = "adjacent Takedata records at the same spatial position collapsed"
                if extra not in status:
                    a["status"] = f"{status}; {extra}".strip("; ")
                continue
        out.append(dict(row))
    return out

def _collapse_repeated_coordinate_cycles(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Collapse a Takedata table that repeats the same full point sequence.

    Several real HEKA EIS acquisitions write the complete point-position list once
    per repeat/cycle.  Those rows describe repeated measurements at the same
    spatial positions, not new spatial points.  Preserve only the first complete
    coordinate cycle and annotate the repeat count.  Arbitrary revisits are not
    collapsed unless the *entire* sequence repeats periodically.
    """
    data = [dict(r) for r in rows]
    n = len(data)
    if n < 2:
        return data
    for period in range(1, n // 2 + 1):
        if n % period:
            continue
        repeats = n // period
        if repeats < 2:
            continue
        if all(_coord_pair_close(data[i], data[i % period]) for i in range(n)):
            base = [dict(r) for r in data[:period]]
            for row in base:
                row["coordinate_repeat_count"] = repeats
                status = str(row.get("status") or "")
                extra = f"Takedata coordinate sequence repeated {repeats} times; collapsed to unique spatial positions"
                row["status"] = f"{status}; {extra}".strip("; ")
            return base
    return data


def _normalized_path_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _takedata_dataset_affinity(path: Path, records: Sequence[Mapping[str, Any]]) -> int:
    """Score how strongly a Takedata table belongs to the selected record set.

    A Browser analysis root can contain multiple run folders.  v295 searched every
    Takedata table under that root with equal weight, which could allow coordinates
    from a neighboring run to win.  v296 prefers matching run/dataset stems and
    technique hints while retaining generic/global Takedata support.
    """
    low_name = path.name.lower()
    stem = re.sub(r"(?i)_?takedata.*$", "", path.stem)
    stem_norm = _normalized_path_text(stem)
    rec_texts: list[str] = []
    rec_techs: set[str] = set()
    for rec in records:
        vals = [rec.get("path"), rec.get("source_file"), rec.get("run"), rec.get("group_title"), rec.get("series_title")]
        meta = rec.get("metadata") if isinstance(rec.get("metadata"), Mapping) else {}
        vals.extend([meta.get("source_file"), meta.get("source_path"), meta.get("run"), meta.get("dataset")])
        rec_texts.extend(_normalized_path_text(v) for v in vals if v)
        tech = str(rec.get("technique_key") or "").upper()
        if tech:
            rec_techs.add(tech)
    score = 0
    if stem_norm and len(stem_norm) >= 8 and any(stem_norm in t or t in stem_norm for t in rec_texts if len(t) >= 8):
        score += 120
    # Common run identifiers are especially useful when names are long/truncated.
    for token in re.findall(r"(?:run|line|point|scan)\d+|run\d+", low_name):
        tnorm = _normalized_path_text(token)
        if any(tnorm and tnorm in t for t in rec_texts):
            score += 20
    specific = None
    if "_ocp_" in low_name or "takedata_ocp" in low_name:
        specific = {"OCP", "OCV"}
    elif "_eis_" in low_name or "takedata_eis" in low_name:
        specific = {"EIS"}
    elif "_iv_" in low_name or "tafel" in low_name or "takedata_iv" in low_name:
        specific = {"IV", "TAFEL", "IV/TAFEL"}
    elif "_cv_" in low_name or "takedata_cv" in low_name:
        specific = {"CV", "LSV"}
    if specific:
        score += 30 if rec_techs & specific else -20
    return score

def _read_takedata_coordinate_file(path: Path) -> list[dict[str, Any]]:
    """Read point coordinates from the full v300 Takedata parser.

    The shared parser understands repeated vendor headers, adjacent duplicate
    station records, repeated coordinate cycles, and explicit coordinate units.
    """
    try:
        from .takedata_v300 import parse_takedata_table
        parsed = parse_takedata_table(path)
    except Exception:
        parsed = []
    synthetic = _is_synthetic_text(path.name)
    rows=[]
    for r in parsed:
        x,y=_finite(r.get("x_m")),_finite(r.get("y_m"))
        if x is None or y is None:
            continue
        rows.append({
            "point": int(r.get("point_index") or len(rows)+1),
            "x_m": x, "y_m": y, "z_m": _finite(r.get("z_m")),
            "scan_distance_m": _finite(r.get("scan_distance_m")),
            "source": f"Takedata file: {path.name}",
            "status": (
                "Takedata compatibility coordinate (synthetic ordered fallback; not a measured HEKA stage position)"
                if synthetic else str(r.get("status") or "coordinate read directly from Takedata table")
            ),
            "synthetic": bool(synthetic), "path": str(path),
            "coordinate_repeat_count": int(r.get("coordinate_repeat_count") or 1),
            "sequence_repeat_count": int(r.get("sequence_repeat_count") or 1),
        })
    return rows

def _candidate_takedata_paths(records: Sequence[Mapping[str, Any]], search_roots: Sequence[Path | str] | None = None) -> list[Path]:
    """Return plausible Takedata tables without recursively walking broad drives."""
    found: dict[str, Path] = {}
    def add(path: Any) -> None:
        try:
            p = Path(str(path)).expanduser()
            if p.exists() and p.is_file() and "takedata" in p.name.lower() and p.suffix.lower() in {".txt", ".csv", ".tsv", ".asc", ".dat"}:
                found[os.path.normcase(str(p.resolve()))] = p.resolve()
        except Exception:
            pass
    for rec in records:
        if str(rec.get("technique_key") or "").upper() == "POSITION" or "takedata" in str(rec.get("path") or "").lower():
            add(rec.get("path"))
    roots: list[Path] = []
    # Recover the original acquisition directory when the Browser is operating
    # from a derived/cached analysis tree. This is essential for using the real
    # companion Takedata instead of a synthetic compatibility coordinate line.
    for rec in records:
        meta = rec.get("metadata") if isinstance(rec.get("metadata"), Mapping) else {}
        for raw_source in (rec.get("source_file"), rec.get("source_path"), meta.get("source_file"), meta.get("source_path")):
            if not raw_source:
                continue
            try:
                q=Path(str(raw_source)).expanduser()
                if q.exists(): roots.append(q if q.is_dir() else q.parent)
            except Exception:
                pass
    for raw in search_roots or []:
        try:
            p = Path(str(raw)).expanduser()
            if p.exists(): roots.append(p if p.is_dir() else p.parent)
        except Exception:
            pass
    # Include a conservative common data root when all selected records live nearby.
    try:
        paths = [Path(str(r.get("path") or "")).resolve() for r in records if str(r.get("path") or "")]
        if paths:
            common = Path(os.path.commonpath([str(p.parent) for p in paths]))
            if common.exists() and len(common.parts) >= 3:
                roots.append(common)
    except Exception:
        pass
    seen_roots: set[str] = set()
    for root in roots:
        try:
            key = os.path.normcase(str(root.resolve()))
            if key in seen_roots:
                continue
            seen_roots.add(key)
            # Analysis folders are modest, but cap discovery to prevent a stale
            # root from causing an unbounded recursive scan.
            count = 0
            for pat in ("*takedata*.txt", "*takedata*.csv", "*takedata*.tsv", "*takedata*.asc"):
                for p in root.rglob(pat):
                    add(p); count += 1
                    if count >= 250:
                        break
                if count >= 250:
                    break
        except Exception:
            pass
    return sorted(found.values(), key=lambda p: ("synthetic_xyz" in p.name.lower(), len(p.parts), p.name.lower()))




def _target_points_for_takedata(path: Path, records: Sequence[Mapping[str, Any]], n_rows: int) -> list[int]:
    """Infer the actual scientific point IDs represented by a method-specific Takedata file."""
    low = path.name.lower()
    techs: set[str] | None = None
    if "takedata_ocp" in low or "_ocp_" in low:
        techs = {"OCP", "OCV"}
    elif "takedata_eis" in low or "_eis_" in low:
        techs = {"EIS"}
    elif "takedata_iv" in low or "_iv_" in low or "tafel" in low:
        techs = {"IV/TAFEL", "TAFEL", "IV"}
    points: list[int] = []
    for rec in records:
        pt = _point(rec)
        if pt <= 0:
            continue
        if techs is not None and str(rec.get("technique_key") or "").upper() not in techs:
            continue
        points.append(pt)
    unique = sorted(set(points))
    if len(unique) == n_rows:
        return unique
    # Generic/global Takedata files usually represent the complete acquisition point list.
    all_points = sorted({_point(rec) for rec in records if _point(rec) > 0})
    if len(all_points) == n_rows:
        return all_points
    return list(range(1, n_rows + 1))
def _best_point_coordinates(
    records: Sequence[Mapping[str, Any]],
    search_roots: Sequence[Path | str] | None = None,
) -> dict[int, dict[str, Any]]:
    """Build one coordinate row per point, preferring physical Takedata.

    v296 behavior: if physical stage coordinates are unavailable, coordinates
    explicitly present in a FaradaIQ/pySECCM Takedata compatibility file are used
    for plotting/grouping and are marked synthetic in provenance.  They are never
    relabeled as measured HEKA stage coordinates.
    """
    candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
    # 1) Coordinate metadata already attached to canonical records.
    for rec in records:
        pt = _point(rec)
        if pt <= 0:
            continue
        synthetic = _is_synthetic_text(_text(rec))
        x = coordinate_m(rec, "x", allow_synthetic=True)
        y = coordinate_m(rec, "y", allow_synthetic=True)
        z = coordinate_m(rec, "z", allow_synthetic=True)
        if x is None or y is None:
            continue
        tech = str(rec.get("technique_key") or "").upper()
        source = str(rec.get("spatial_coordinate_source") or rec.get("coordinate_source") or (rec.get("metadata") or {}).get("coordinate_source") or "")
        status = str(rec.get("spatial_coordinate_status") or rec.get("coordinate_status") or (rec.get("metadata") or {}).get("coordinate_status") or "")
        score = 100 if not synthetic else 20
        if tech == "POSITION": score += 20
        if "takedata" in (source + " " + status).lower(): score += 10
        if z is not None: score += 1
        candidates[pt].append({"point": pt, "x_m": x, "y_m": y, "z_m": z, "source": source, "status": status, "score": score, "synthetic": synthetic})

    # 2) Read the Takedata table itself. This is the critical v297 fallback for
    # existing H5/browser caches where the record metadata did not carry X/Y.
    paths = _candidate_takedata_paths(records, search_roots)
    affinities = {path: _takedata_dataset_affinity(path, records) for path in paths}
    if affinities:
        best_affinity = max(affinities.values())
        # Once a run/dataset match exists, do not allow neighboring runs under
        # the same experiment root to contribute point coordinates.
        if best_affinity > 0:
            paths = [path for path in paths if affinities.get(path, 0) >= best_affinity - 20]
    selected_points = sorted({_point(rec) for rec in records if _point(rec) > 0})
    expected_count = len(selected_points)
    for path in paths:
        parsed = _read_takedata_coordinate_file(path)
        target_points = _target_points_for_takedata(path, records, len(parsed))
        affinity = affinities.get(path, 0)
        count_bonus = 0
        if expected_count and len(parsed) == expected_count:
            count_bonus += 45
        elif expected_count and abs(len(parsed) - expected_count) == 1:
            count_bonus += 15
        low_name = path.name.lower()
        cleaned_bonus = 8 if any(token in low_name for token in ("_edited", "_clean", "_cleaned", "_dedup")) else 0
        for idx, row in enumerate(parsed):
            pt = target_points[idx] if idx < len(target_points) else int(row.get("point") or 0)
            if pt <= 0:
                continue
            synthetic = bool(row.get("synthetic"))
            score = (80 if not synthetic else 30) + affinity + count_bonus + cleaned_bonus
            item = dict(row); item["point"] = pt
            candidates[pt].append({**item, "score": score, "dataset_affinity": affinity, "point_count_bonus": count_bonus, "cleaned_table_bonus": cleaned_bonus})

    out: dict[int, dict[str, Any]] = {}
    for pt, rows in candidates.items():
        rows.sort(key=lambda r: (-int(r.get("score") or 0), bool(r.get("synthetic")), str(r.get("source") or "")))
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


def enrich_catalog_records(records: Sequence[MutableMapping[str, Any]], search_roots: Sequence[Path | str] | None = None) -> dict[str, Any]:
    """Attach Takedata X/Y/Z and cumulative scan distance to matching points.

    Physical HEKA coordinates are preferred. If only an explicitly synthetic
    Takedata compatibility coordinate exists, v297 uses it for plotting/grouping
    while preserving a synthetic provenance flag. Existing Point views remain.
    """
    point_rows = _best_point_coordinates(records, search_roots=search_roots)
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
        rec["spatial_coordinate_is_synthetic"] = bool(spatial.get("synthetic"))
        rec["spatial_coordinate_quality"] = "takedata_compatibility_synthetic" if spatial.get("synthetic") else "physical_takedata"
        rec["spatial_coordinate_source"] = spatial.get("source") or "HEKA Takedata / canonical point metadata"
        rec["spatial_coordinate_status"] = spatial.get("status") or "X/Y from Takedata/canonical point metadata"
    axis = auto_axis_for_records(records)
    for rec in records:
        if rec.get("spatial_coordinate_available"):
            rec["spatial_axis_auto"] = axis
    return {
        "point_count": len(point_rows),
        "distance_point_count": len(distances),
        "auto_axis": axis,
        "source": "Takedata/canonical point metadata",
        "synthetic_point_count": sum(1 for row in point_rows.values() if row.get("synthetic")),
        "physical_point_count": sum(1 for row in point_rows.values() if not row.get("synthetic")),
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
    """Choose the most useful 1-D spatial axis while retaining Point for 2-D maps.

    Real line scans often contain small orthogonal stage drift.  Treat a coordinate
    whose span is <=2% of the dominant span as lateral drift and use the dominant
    physical axis.  Otherwise use PCA/rank to distinguish a diagonal/curved 1-D
    path (Distance) from a genuine 2-D map (Point/X-Y map rendering).
    """
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
    tol = max(scale * 1e-9, 1e-15)
    if xr <= tol and yr <= tol:
        return "Point"
    if yr <= tol:
        return "X"
    if xr <= tol:
        return "Y"
    # Real HEKA lines can wander laterally by tens/hundreds of nm over millimetres.
    # Do not misclassify that instrument/path drift as a 2-D map.
    dominance = min(xr, yr) / max(xr, yr)
    if dominance <= 0.02:
        return "X" if xr > yr else "Y"
    centered = pts - np.mean(pts, axis=0, keepdims=True)
    try:
        singular = np.linalg.svd(centered, compute_uv=False)
        if len(singular) >= 2 and singular[0] > 0 and singular[1] / singular[0] <= 0.05:
            return "Distance"
        rank = int(np.linalg.matrix_rank(centered, tol=tol))
    except Exception:
        rank = 2
    if rank <= 1:
        return "Distance"
    return "Point"


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
