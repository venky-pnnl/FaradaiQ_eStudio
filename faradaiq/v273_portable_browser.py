"""Portable comparison-first HTML browser for FaradaIQ Studio v273 outputs.

The generated page is static, self-contained, and offline.  It embeds a
technique-aware, full-file-sampled plot catalog while keeping all file and media
links relative to the experiment root.  The complete experiment folder can be
copied, renamed, or moved without breaking the browser.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import re
import threading
import zipfile
from collections import defaultdict
from datetime import datetime
from itertools import chain
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence
from urllib.parse import quote

VERSION = "v273"
VERSION_NUMBER = 273
SEMVER = "0.273.0"
APP_TITLE = f"FaradaIQ Studio {VERSION}"

HTML_NAME = "FaradaIQ_Data_Browser.html"
MANIFEST_NAME = "faradaiq_portable_browser_manifest.json"
DOWNLOAD_DIR_NAME = "browser_downloads"
README_NAME = "README_PORTABLE_BROWSER.txt"
SCHEMA = "faradaiq-portable-browser-v273"
DEFAULT_ENABLED = True
MAX_PREVIEW_ROWS = 640
MAX_PREVIEW_COLUMNS = 12
MAX_FILE_RECORDS = 16000
MAX_BATCHES = 2500

_TABLE_SUFFIXES = {".csv", ".tsv", ".txt", ".dat"}
_FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".tif", ".tiff", ".bmp"}
_BROWSER_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}
_REPORT_SUFFIXES = {".html", ".htm", ".pdf", ".docx", ".pptx", ".xlsx", ".xls"}
_METADATA_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".ini", ".md"}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _slug(value: Any, default: str = "item", max_chars: int = 48) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or default)).strip("._-") or default
    if len(text) <= max_chars:
        return text
    token = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:8]
    room = max(6, max_chars - len(token) - 2)
    return f"{text[:room].rstrip('._-')}__{token}"


def _posix_rel(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


def _href(relative_path: str) -> str:
    return "/".join(quote(part, safe="._-~()[]") for part in str(relative_path).replace("\\", "/").split("/"))


def _derive_relative_from_absolute(value: Any, root: Path) -> str:
    raw = str(value or "").replace("\\", "/")
    if not raw:
        return ""
    markers = ["/.browser_cache_from_h5/", "/browser_cache_from_h5/", "/processed/"]
    for marker in markers:
        idx = raw.lower().find(marker.lower())
        if idx >= 0:
            if "browser_cache" in marker:
                return ".browser_cache_from_h5/" + raw[idx + len(marker):].lstrip("/")
            return "processed/" + raw[idx + len(marker):].lstrip("/")
    candidate = Path(raw)
    try:
        if candidate.exists():
            return _posix_rel(candidate, root)
    except Exception:
        pass
    return ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _find_catalog(root: Path) -> Path | None:
    candidates: list[Path] = []
    cache = root / ".browser_cache_from_h5"
    if cache.is_dir():
        candidates.extend(cache.glob("_faradaiq_browser_catalog*.json"))
    candidates.extend(root.rglob("_faradaiq_browser_catalog*.json"))
    candidates = [p for p in candidates if p.is_file()]
    return max(candidates, key=lambda p: (p.stat().st_mtime, p.name)) if candidates else None


def _find_analysis_root(root: Path, explicit: str | Path | None = None) -> Path | None:
    if explicit:
        p = Path(explicit)
        if p.is_dir():
            return p.resolve()
    candidates = [p for p in root.iterdir() if p.is_dir() and (p.name.endswith("_analysis_ready_csv") or p.name == "processed")]
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    processed = [p for p in root.rglob("processed") if p.is_dir()]
    return min(processed, key=lambda p: len(p.parts)) if processed else None


def _technique_key(value: Any) -> str:
    text = str(value or "OTHER").strip()
    aliases = {
        "CV / LSV": "CV_LSV", "CV/LSV": "CV_LSV", "Voltammetry (CV / LSV)": "CV_LSV",
        "Chronoamperometry (CA)": "CA", "Chronoamperometry": "CA",
        "Chronopotentiometry (CP)": "CP", "Chronopotentiometry": "CP",
        "Open-circuit potential (OCP)": "OCP", "Open circuit potential": "OCP",
        "Electrochemical impedance spectroscopy (EIS)": "EIS", "EIS / GEIS": "EIS",
        "Photos / images": "PHOTO", "Photos": "PHOTO", "Images": "PHOTO",
    }
    return _slug(aliases.get(text, text.replace("/", "_")), default="OTHER", max_chars=24).upper()


def _parse_number(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except Exception:
        match = re.search(r"(\d+)", str(value))
        return int(match.group(1)) if match else None


def _logical_hierarchy(record: Mapping[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    candidates = (
        record.get("canonical_key"), metadata.get("canonical_key"), metadata.get("relative_path"),
        metadata.get("source_path"), metadata.get("relative_csv_path"),
    )
    out: dict[str, Any] = {}
    for raw in candidates:
        text = str(raw or "").replace("\\", "/")
        if not text:
            continue
        if re.search(r"(?:^|/)processed/group_\d+", text, flags=re.I) or re.search(r"(?:^|/)series_\d+", text, flags=re.I):
            out["is_heka"] = True
        patterns = (
            ("group_index", r"(?:^|/)group_(\d+)(?:_([^/]+))?"),
            ("point", r"(?:^|/)point_(\d+)(?:/|$)"),
            ("series_index_in_group", r"(?:^|/)series_(\d+)(?:_([^/]+))?"),
            ("sweep", r"(?:^|/)sweep_(\d+)(?:_|/|$)"),
            ("cycle", r"(?:^|[_/])cycle_(\d+)(?:_|/|\.|$)"),
        )
        for key, pattern in patterns:
            match = re.search(pattern, text, flags=re.I)
            if not match:
                continue
            out[key] = int(match.group(1))
            if key == "group_index" and match.lastindex and match.lastindex >= 2 and match.group(2):
                out["group_title"] = match.group(2).replace("_", " ")
            elif key == "series_index_in_group" and match.lastindex and match.lastindex >= 2 and match.group(2):
                out["series_title"] = match.group(2).replace("_", " ")
    return out


def _infer_media_point(path_or_name: Any) -> int | None:
    stem = Path(str(path_or_name or "")).stem
    # Prefer a terminal point/frame number, e.g. Mag_dep_170.jpg.
    matches = re.findall(r"(?:point|pt|frame|img|image|photo|dep)?[_ -]?(\d+)", stem, flags=re.I)
    return int(matches[-1]) if matches else None


def _scientific_display(record: Mapping[str, Any], fallback: str) -> str:
    technique = str(record.get("technique_label") or record.get("technique_key") or record.get("technique") or "Dataset")
    if _technique_key(record.get("technique_key") or technique) == "PHOTO":
        if "sequence" in str(fallback).lower() or Path(str(fallback)).suffix.lower() == ".gif":
            return f"Animated photo sequence — {fallback}"
        point = _parse_number(record.get("point"))
        return f"Photo — Point {point:03d} — {fallback}" if point is not None else f"Photo / image — {fallback}"
    parts = [technique]
    method = str(record.get("method_label") or record.get("method_title") or record.get("method_id") or "").strip()
    point, cycle, sweep, line = record.get("point"), record.get("cycle"), record.get("sweep"), record.get("line")
    if method and method.lower() not in {"method", "method 01", "method 01 — method 01"}:
        parts.append(method)
    if point not in (None, ""):
        try: parts.append(f"Point {int(point):03d}")
        except Exception: parts.append(f"Point {point}")
    if line not in (None, "", 0, "0"):
        try: parts.append(f"Line {int(line):03d}")
        except Exception: parts.append(f"Line {line}")
    if cycle not in (None, "", 0, "0"):
        try: parts.append(f"Cycle {int(cycle):02d}")
        except Exception: parts.append(f"Cycle {cycle}")
    if sweep not in (None, "", 0, "0"):
        try: parts.append(f"Sweep {int(sweep):02d}")
        except Exception: parts.append(f"Sweep {sweep}")
    return " — ".join(parts) if parts else fallback


def _detect_delimiter(sample: str, suffix: str) -> str:
    # Do not include a space as a delimiter. Scientific-notation and descriptive
    # headers made the old Sniffer occasionally split valid CSV rows incorrectly.
    counts = {",": sample.count(","), "\t": sample.count("\t"), ";": sample.count(";")}
    delimiter = max(counts, key=counts.get)
    if counts[delimiter] == 0:
        return "\t" if suffix in {".tsv", ".txt"} and "\t" in sample else ","
    return delimiter


def _as_number(text: Any) -> float | None:
    try:
        value = float(str(text).strip())
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _normalize_column(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _numeric_column_info(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    info: list[dict[str, Any]] = []
    for idx, name in enumerate(columns):
        vals = [float(r[idx]) for r in rows if idx < len(r) and isinstance(r[idx], (int, float)) and math.isfinite(float(r[idx]))]
        if not vals:
            continue
        unique = len({round(v, 14) for v in vals[:500]})
        coverage = len(vals) / max(1, len(rows))
        info.append({
            "index": idx, "name": name, "coverage": coverage, "varying": unique > 1,
            "min": min(vals), "max": max(vals), "mean": sum(vals) / len(vals),
        })
    return info


def _preferred_axes(technique: str, columns: Sequence[str], numeric: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_norm = {_normalize_column(c): i for i, c in enumerate(columns)}
    varying = [int(v["index"]) for v in numeric if bool(v.get("varying")) and float(v.get("coverage", 0)) >= 0.45]
    numeric_all = [int(v["index"]) for v in numeric if float(v.get("coverage", 0)) >= 0.45]

    def find(aliases: Sequence[str]) -> int | None:
        norms = [_normalize_column(a) for a in aliases]
        for alias in norms:
            if alias in by_norm:
                return by_norm[alias]
        for idx, col in enumerate(columns):
            norm = _normalize_column(col)
            if any(alias and (alias in norm or norm in alias) for alias in norms):
                return idx
        return None

    axis_aliases = {
        "CA": (["time_s", "time", "elapsed_s", "duration_s"], ["current_A", "current", "i_A", "I"]),
        "CV_LSV": (["potential_V", "potential", "voltage_V", "E_V"], ["current_A", "current", "i_A", "I"]),
        "CP": (["time_s", "time", "elapsed_s"], ["potential_V", "potential", "voltage_V", "E_V"]),
        "OCP": (["time_s", "time", "elapsed_s"], ["potential_V", "potential", "voltage_V", "E_V"]),
        "EIS": (["z_real_ohm", "zreal", "real", "re_z"], ["z_imag_ohm", "zimag", "imag", "im_z"]),
        "DRT": (["tau_s", "tau", "relaxation_time"], ["gamma_ohm", "gamma", "distribution"]),
    }
    x_alias, y_alias = axis_aliases.get(technique, (["time_s", "potential_V", "frequency_Hz", "distance_m", "x"], ["current_A", "potential_V", "z_imag_ohm", "gamma_ohm", "y"]))
    x_idx, y_idx = find(x_alias), find(y_alias)
    # EIS and DRT must not silently plot unrelated metadata columns. Require
    # their scientific axis pairs; otherwise keep the table visible in the
    # Dataset browser but exclude it from comparison plots.
    if technique == "EIS" and (x_idx is None or y_idx is None):
        freq = find(["frequency_Hz", "frequency", "freq_Hz"])
        magnitude = find(["z_magnitude_ohm", "magnitude", "abs_z", "impedance_magnitude"])
        if freq is not None and magnitude is not None:
            x_idx, y_idx = freq, magnitude
        else:
            return {"x_index": None, "y_index": None, "x_column": "", "y_column": "", "invert_y_default": False, "log_x_default": False}
    if technique == "DRT" and (x_idx is None or y_idx is None):
        return {"x_index": None, "y_index": None, "x_column": "", "y_column": "", "invert_y_default": False, "log_x_default": False}
    ignored = {i for i, c in enumerate(columns) if _normalize_column(c) in {"point", "pointindex", "cycle", "sweep", "line", "groupindex", "seriesindex"}}
    usable = [i for i in varying if i not in ignored]
    if x_idx is None or x_idx not in numeric_all or x_idx == y_idx:
        x_idx = usable[0] if usable else (numeric_all[0] if numeric_all else None)
    if y_idx is None or y_idx not in numeric_all or y_idx == x_idx:
        y_idx = next((i for i in usable if i != x_idx), None)
        if y_idx is None:
            y_idx = next((i for i in numeric_all if i != x_idx), None)
    return {
        "x_index": x_idx, "y_index": y_idx,
        "x_column": columns[x_idx] if x_idx is not None and x_idx < len(columns) else "",
        "y_column": columns[y_idx] if y_idx is not None and y_idx < len(columns) else "",
        "invert_y_default": bool(technique == "EIS" and y_idx is not None and "imag" in _normalize_column(columns[y_idx])),
        "log_x_default": bool(technique in {"EIS", "DRT"} and x_idx is not None and any(k in _normalize_column(columns[x_idx]) for k in ("frequency", "tau"))),
    }


def _preview_table(path: Path, columns_hint: Sequence[Any] = (), technique: str = "OTHER", limit: int = MAX_PREVIEW_ROWS) -> dict[str, Any]:
    """Sample the complete table, preserving ordered coverage and the final row.

    The previous browser read only the beginning of a table and sometimes used a
    small cache derivative.  v273 reads the actual processed table and performs
    adaptive ordered decimation across the full file, so every curve has a useful
    preview even when the raw table contains thousands or millions of rows.
    """
    result: dict[str, Any] = {
        "kind": "table", "columns": [str(c) for c in columns_hint], "rows": [],
        "sampled_rows": 0, "total_rows": 0, "decimation_factor": 1,
        "numeric_columns": [], "preferred_axes": {}, "error": "", "status": "unavailable",
    }
    if not path.is_file() or path.suffix.lower() not in _TABLE_SUFFIXES:
        result["error"] = "Source is not a supported table file."
        return result
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            sample = handle.read(65536)
            handle.seek(0)
            delimiter = _detect_delimiter(sample, path.suffix.lower())
            reader = csv.reader(handle, delimiter=delimiter)
            first = next(reader, None)
            if first is None:
                result["error"] = "The table is empty."
                return result
            first_numbers = [_as_number(cell) for cell in first]
            numeric_count = sum(v is not None for v in first_numbers)
            has_header = numeric_count < max(1, min(2, len(first)))
            if has_header:
                columns = [str(x).strip() or f"column_{i+1}" for i, x in enumerate(first[:MAX_PREVIEW_COLUMNS])]
                source_rows: Iterable[Sequence[str]] = reader
            else:
                columns = [str(x) for x in columns_hint[:MAX_PREVIEW_COLUMNS]] or [f"column_{i+1}" for i in range(min(len(first), MAX_PREVIEW_COLUMNS))]
                source_rows = chain([first], reader)
            result["columns"] = columns
            rows: list[list[Any]] = []
            factor = 1
            total = 0
            last_row: list[Any] | None = None
            for raw in source_rows:
                total += 1
                vals: list[Any] = []
                for cell in list(raw)[:len(columns)]:
                    text = str(cell).strip()
                    num = _as_number(text)
                    vals.append(num if num is not None else text[:160])
                last_row = vals
                if (total - 1) % factor == 0:
                    rows.append(vals)
                if len(rows) > limit * 2:
                    rows = rows[::2]
                    factor *= 2
            if last_row is not None and (not rows or rows[-1] != last_row):
                rows.append(last_row)
            if len(rows) > limit:
                if limit <= 2:
                    rows = rows[:limit]
                else:
                    idxs = sorted({round(i * (len(rows) - 1) / (limit - 1)) for i in range(limit)})
                    rows = [rows[i] for i in idxs]
            result["rows"] = rows
            result["sampled_rows"] = len(rows)
            result["total_rows"] = total
            result["decimation_factor"] = max(1, factor)
            numeric = _numeric_column_info(columns, rows)
            result["numeric_columns"] = numeric
            axes = _preferred_axes(technique, columns, numeric)
            result["preferred_axes"] = axes
            if rows:
                result["status"] = "plot_ready" if axes.get("x_index") is not None and axes.get("y_index") is not None else "table_ready"
            else:
                result["error"] = "No readable rows were found."
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _resolve_catalog_file(root: Path, record: Mapping[str, Any]) -> tuple[str, Path | None]:
    rel = _derive_relative_from_absolute(record.get("path"), root)
    if rel:
        p = root / rel
        if p.is_file():
            return rel, p
    source_file = str(record.get("source_file") or "")
    if source_file and (root / ".browser_cache_from_h5").is_dir():
        matches = list((root / ".browser_cache_from_h5").rglob(source_file))
        if matches:
            return _posix_rel(matches[0], root), matches[0]
    return rel, (root / rel if rel else None)


def _resolve_processed_file(root: Path, analysis_root: Path | None, record: Mapping[str, Any]) -> tuple[str, Path | None]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    rel_meta = str(metadata.get("relative_path") or "").replace("\\", "/")
    candidates: list[Path] = []
    if rel_meta and analysis_root is not None:
        candidates.append(analysis_root / rel_meta)
    source_abs_rel = _derive_relative_from_absolute(metadata.get("source_path"), root)
    if source_abs_rel:
        candidates.append(root / source_abs_rel)
    for p in candidates:
        if p.is_file():
            return _posix_rel(p, root), p
    return "", None


def _media_preview(rel: str, path: Path) -> dict[str, Any]:
    browser_renderable = path.suffix.lower() in _BROWSER_IMAGE_SUFFIXES
    return {
        "kind": "image", "status": "image_ready" if browser_renderable else "download_only",
        "image_path": rel, "image_href": _href(rel), "browser_renderable": browser_renderable,
        "columns": [], "rows": [], "sampled_rows": 0, "total_rows": 0,
        "numeric_columns": [], "preferred_axes": {}, "error": "" if browser_renderable else "This image format may not render directly in every browser; use Open or Download.",
    }


def _catalog_datasets(root: Path, analysis_root: Path | None, catalog_path: Path) -> list[dict[str, Any]]:
    catalog = _read_json(catalog_path)
    records = catalog.get("records") if isinstance(catalog.get("records"), list) else []
    datasets: list[dict[str, Any]] = []
    for idx, raw in enumerate(records, 1):
        if not isinstance(raw, Mapping):
            continue
        record = dict(raw)
        cache_rel, cache_file = _resolve_catalog_file(root, record)
        processed_rel, processed_file = _resolve_processed_file(root, analysis_root, record)
        # The processed file is authoritative for plotting. Cache derivatives can
        # be intentionally compact and were the reason some old previews had two rows.
        chosen_rel = processed_rel or cache_rel
        chosen_file = processed_file or cache_file
        if not chosen_rel or chosen_file is None or not chosen_file.is_file():
            continue
        technique_label = str(record.get("technique_label") or record.get("technique_key") or record.get("technique") or "Other")
        technique = _technique_key(record.get("technique_key") or technique_label)
        logical = _logical_hierarchy(record)
        point = _parse_number(logical.get("point")) if "point" in logical else _parse_number(record.get("point"))
        if technique == "PHOTO" and (point is None or point == 0):
            inferred = _infer_media_point(chosen_file.name)
            if inferred is not None:
                point = inferred
        cycle = _parse_number(logical.get("cycle")) if "cycle" in logical else _parse_number(record.get("cycle"))
        sweep = _parse_number(logical.get("sweep")) if "sweep" in logical else _parse_number(record.get("sweep"))
        method_index = _parse_number(record.get("method_index") or record.get("preferred_method_index") or record.get("method"))
        method_label = str(record.get("method_label") or record.get("method_title") or record.get("method_id") or (f"Method {method_index:02d}" if method_index is not None else "Method"))
        metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
        if chosen_file.suffix.lower() in _FIGURE_SUFFIXES or technique == "PHOTO":
            preview = _media_preview(chosen_rel, chosen_file)
        else:
            preview = _preview_table(chosen_file, record.get("columns") or metadata.get("columns") or [], technique=technique)
        actual_columns = [] if preview.get("kind") == "image" else [str(c) for c in (preview.get("columns") or record.get("columns") or metadata.get("columns") or [])]
        display_record = dict(record)
        display_record.update({"point": point, "cycle": cycle, "sweep": sweep, "method_label": method_label, "technique_key": technique, "technique_label": technique_label})
        display_name = _scientific_display(display_record, chosen_file.name)
        canonical_key = str(record.get("canonical_key") or metadata.get("canonical_key") or "")
        dataset_id = f"d{idx:05d}_{hashlib.sha1((canonical_key or chosen_rel).encode('utf-8', errors='replace')).hexdigest()[:8]}"
        axes = preview.get("preferred_axes") if isinstance(preview.get("preferred_axes"), Mapping) else {}
        datasets.append({
            "id": dataset_id, "display_name": display_name,
            "technique_key": technique, "technique_label": technique_label,
            "method_index": method_index, "method_label": method_label,
            "point": point, "line": _parse_number(record.get("line")), "cycle": cycle, "sweep": sweep,
            "physical_path": chosen_rel, "physical_href": _href(chosen_rel),
            "browser_cache_path": cache_rel, "processed_source_path": processed_rel,
            "original_scientific_path": str(metadata.get("relative_path") or canonical_key or chosen_rel).replace("\\", "/"),
            "canonical_key": canonical_key,
            "role": str(record.get("role") or metadata.get("role") or ("media" if technique == "PHOTO" else "processed table")),
            "columns": actual_columns,
            "declared_columns": [] if preview.get("kind") == "image" else [str(c) for c in (record.get("columns") or metadata.get("columns") or [])],
            "n_rows": preview.get("total_rows") or metadata.get("n_rows"),
            "size_bytes": int(chosen_file.stat().st_size),
            "preview": preview,
            "plot_spec": {
                "x_column": axes.get("x_column", ""), "y_column": axes.get("y_column", ""),
                "x_index": axes.get("x_index"), "y_index": axes.get("y_index"),
                "invert_y_default": bool(axes.get("invert_y_default")), "log_x_default": bool(axes.get("log_x_default")),
            },
            "is_media": technique == "PHOTO" or chosen_file.suffix.lower() in _FIGURE_SUFFIXES,
            "metadata": {
                "instrument": "HEKA" if logical.get("is_heka") else record.get("instrument"),
                "hierarchy_recovered_from_canonical_key": bool(logical.get("is_heka") and "point" in logical),
                "group_index": logical.get("group_index", record.get("group_index")),
                "group_title": logical.get("group_title", record.get("group_title")),
                "series_index_in_group": logical.get("series_index_in_group", record.get("series_index_in_group")),
                "series_title": logical.get("series_title", record.get("series_title")),
                "scan_rate": record.get("scan_rate") or record.get("scan_rate_mV_s"),
                "x_label": record.get("x_label"), "x_unit": record.get("x_unit"),
                "y_label": record.get("y_label"), "y_unit": record.get("y_unit"),
                "x_position": record.get("x_position"), "y_position": record.get("y_position"), "z_position": record.get("z_position"),
                "confidence": record.get("confidence"), "evidence": record.get("evidence") or [],
            },
        })
    return datasets


def _infer_from_path(path: Path, root: Path, index: int) -> dict[str, Any]:
    rel = _posix_rel(path, root)
    text, upper = rel.replace("\\", "/"), rel.upper()
    technique = "PHOTO" if path.suffix.lower() in _FIGURE_SUFFIXES else "OTHER"
    if technique != "PHOTO":
        for key in ("CV_LSV", "CA", "CP", "OCP", "EIS", "GEIS", "DRT"):
            if f"/{key}/" in f"/{upper}/" or upper.startswith(key + "/") or f"_{key}_" in upper:
                technique = "EIS" if key == "GEIS" else key
                break
    point_match = re.search(r"point[_ -]?(\d+)", text, re.I)
    cycle_match = re.search(r"cycle[_ -]?(\d+)", text, re.I)
    sweep_match = re.search(r"sweep[_ -]?(\d+)", text, re.I)
    method_match = re.search(r"method[_ -]?(\d+)", text, re.I)
    point = int(point_match.group(1)) if point_match else (_infer_media_point(path.name) if technique == "PHOTO" else None)
    cycle = int(cycle_match.group(1)) if cycle_match else None
    sweep = int(sweep_match.group(1)) if sweep_match else None
    method_index = int(method_match.group(1)) if method_match else None
    method_label = f"Method {method_index:02d}" if method_index is not None else "Method"
    preview = _media_preview(rel, path) if technique == "PHOTO" else _preview_table(path, technique=technique)
    axes = preview.get("preferred_axes", {})
    record = {"technique_key": technique, "technique_label": technique.replace("_", " / "), "method_label": method_label, "point": point, "cycle": cycle, "sweep": sweep}
    return {
        "id": f"d{index:05d}_{hashlib.sha1(rel.encode()).hexdigest()[:8]}",
        "display_name": _scientific_display(record, path.name),
        "technique_key": technique, "technique_label": technique.replace("_", " / "),
        "method_index": method_index, "method_label": method_label,
        "point": point, "line": None, "cycle": cycle, "sweep": sweep,
        "physical_path": rel, "physical_href": _href(rel), "browser_cache_path": rel if ".browser_cache_from_h5/" in rel else "",
        "processed_source_path": rel, "original_scientific_path": rel, "canonical_key": "",
        "role": "media" if technique == "PHOTO" else "processed table",
        "columns": preview.get("columns", []), "declared_columns": [], "n_rows": preview.get("total_rows"),
        "size_bytes": path.stat().st_size, "preview": preview,
        "plot_spec": {"x_column": axes.get("x_column", ""), "y_column": axes.get("y_column", ""), "x_index": axes.get("x_index"), "y_index": axes.get("y_index"), "invert_y_default": bool(axes.get("invert_y_default")), "log_x_default": bool(axes.get("log_x_default"))},
        "is_media": technique == "PHOTO", "metadata": {},
    }


def _scan_datasets(root: Path, analysis_root: Path | None) -> list[dict[str, Any]]:
    scan_root = analysis_root if analysis_root and analysis_root.is_dir() else root
    paths: list[Path] = []
    for p in scan_root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in (_TABLE_SUFFIXES | _FIGURE_SUFFIXES):
            continue
        rel = _posix_rel(p, root)
        if rel.startswith(DOWNLOAD_DIR_NAME + "/") or p.name.startswith("faradaiq_portable_browser_manifest"):
            continue
        paths.append(p)
    return [_infer_from_path(p, root, idx) for idx, p in enumerate(sorted(paths), 1)]


def _file_category(path: Path, rel: str) -> str:
    suffix, low = path.suffix.lower(), rel.lower()
    if rel.startswith(DOWNLOAD_DIR_NAME + "/"): return "Download bundles"
    if suffix in _FIGURE_SUFFIXES:
        return "Photos" if any(x in low for x in ("/photo", "/media", "image")) else "Figures"
    if suffix in _REPORT_SUFFIXES: return "Reports"
    if suffix in _TABLE_SUFFIXES: return "Data tables"
    if suffix in {".h5", ".hdf5", ".npy", ".npz"}: return "Canonical data"
    if suffix in _METADATA_SUFFIXES: return "Metadata"
    if "scilink" in low or "edison" in low or "fq_ai_jobs" in low: return "SciLink and Edison"
    return "Other files"


def _scan_files(root: Path) -> list[dict[str, Any]]:
    excluded = {HTML_NAME, MANIFEST_NAME}
    rows: list[dict[str, Any]] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name in excluded:
            continue
        rel = _posix_rel(p, root)
        if rel.startswith(".faradaiq_venv/") or "/__pycache__/" in f"/{rel}/":
            continue
        try: stat = p.stat()
        except OSError: continue
        rows.append({
            "name": p.name, "path": rel, "href": _href(rel), "category": _file_category(p, rel),
            "size_bytes": stat.st_size, "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "extension": p.suffix.lower(), "point": _infer_media_point(p.name) if p.suffix.lower() in _FIGURE_SUFFIXES else None,
            "browser_renderable": p.suffix.lower() in _BROWSER_IMAGE_SUFFIXES,
        })
        if len(rows) >= MAX_FILE_RECORDS: break
    return rows


def _build_media(files: Sequence[Mapping[str, Any]], datasets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for d in datasets:
        if not d.get("is_media"):
            continue
        rel = str(d.get("physical_path") or "")
        by_path[rel] = {
            "id": "m_" + str(d.get("id")), "name": Path(rel).name, "path": rel, "href": d.get("physical_href") or _href(rel),
            "point": d.get("point"), "technique_key": d.get("technique_key"), "display_name": d.get("display_name"),
            "category": "Photos / images", "browser_renderable": bool((d.get("preview") or {}).get("browser_renderable")),
            "size_bytes": d.get("size_bytes", 0), "dataset_id": d.get("id"),
        }
    for f in files:
        if str(f.get("extension") or "").lower() not in _FIGURE_SUFFIXES:
            continue
        rel = str(f.get("path") or "")
        if rel in by_path: continue
        by_path[rel] = {
            "id": "m_" + hashlib.sha1(rel.encode()).hexdigest()[:12], "name": f.get("name"), "path": rel, "href": f.get("href") or _href(rel),
            "point": f.get("point"), "technique_key": "FIGURE", "display_name": f.get("name"),
            "category": f.get("category"), "browser_renderable": bool(f.get("browser_renderable")),
            "size_bytes": f.get("size_bytes", 0), "dataset_id": "",
        }
    # The same photo is often present once in the processed tree and once in
    # .browser_cache_from_h5. Keep one scientific gallery entry, preferring the
    # dataset/processed path, while all physical copies remain available in Files.
    unique: dict[tuple[str, int], dict[str, Any]] = {}
    for row in by_path.values():
        identity = (str(row.get("name") or "").lower(), int(row.get("size_bytes") or 0))
        prior = unique.get(identity)
        if prior is None or (not prior.get("dataset_id") and row.get("dataset_id")) or (".browser_cache_from_h5/" in str(prior.get("path")) and ".browser_cache_from_h5/" not in str(row.get("path"))):
            unique[identity] = row
    return sorted(unique.values(), key=lambda m: (m.get("point") is None, m.get("point") if m.get("point") is not None else 10**9, str(m.get("name"))))


def _short_name_summary(root: Path, enabled: bool | None = None) -> dict[str, Any]:
    path = root / "short_name_map.json"
    payload = _read_json(path) if path.is_file() else {}
    policy = payload.get("short_name_policy") if isinstance(payload.get("short_name_policy"), Mapping) else payload
    inferred = bool(policy.get("enabled")) if isinstance(policy, Mapping) and "enabled" in policy else bool(enabled if enabled is not None else True)
    aliases = []
    raw_aliases = policy.get("name_aliases") if isinstance(policy, Mapping) else []
    if isinstance(raw_aliases, list):
        for row in raw_aliases[:5000]:
            if isinstance(row, Mapping):
                aliases.append({"original_name": str(row.get("original_name") or ""), "short_name": str(row.get("short_name") or ""), "kind": str(row.get("kind") or ""), "context": str(row.get("context") or "")})
    return {
        "enabled": inferred, "mapping_file": "short_name_map.json" if path.is_file() else "",
        "display_uses_original_scientific_names": True, "physical_paths_may_be_shortened": inferred,
        "canonical_identity_changed": False,
        "note": "Shortened names are physical filesystem aliases only. The HTML, JSON, and ZIP manifests preserve original scientific names and canonical keys.",
        "name_aliases": aliases,
    }


def _batch_key(dataset: Mapping[str, Any]) -> tuple[str, str, int | None]:
    return str(dataset.get("technique_key") or "OTHER"), str(dataset.get("method_label") or "Method"), _parse_number(dataset.get("point"))


def _write_batch_archives(root: Path, datasets: list[dict[str, Any]], short_names: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = root / DOWNLOAD_DIR_NAME
    out.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple[str, str, int | None], list[dict[str, Any]]] = defaultdict(list)
    for row in datasets:
        groups[_batch_key(row)].append(row)
    batches: list[dict[str, Any]] = []
    expected_archives: set[str] = set()
    ordered_groups = sorted(groups.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] if item[0][2] is not None else -1))
    for idx, (key, rows) in enumerate(ordered_groups, 1):
        if idx > MAX_BATCHES: break
        technique, method_label, point = key
        method_index = next((_parse_number(r.get("method_index")) for r in rows if _parse_number(r.get("method_index")) is not None), None)
        token_seed = "|".join(str(r.get("canonical_key") or r.get("physical_path")) for r in rows)
        token = hashlib.sha1(token_seed.encode("utf-8", errors="replace")).hexdigest()[:8]
        point_token = f"p{point:03d}" if point is not None else "all"
        filename = f"batch_{_slug(technique, max_chars=14)}_{_slug(method_label, max_chars=20)}_{point_token}_{token}.zip"
        expected_archives.add(filename)
        zip_path, tmp = out / filename, out / (filename + ".tmp")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            entries, used = [], set()
            for ordinal, row in enumerate(rows, 1):
                rel = str(row.get("physical_path") or "")
                src = root / rel
                if not src.is_file(): continue
                ext = src.suffix.lower() or ".dat"
                archive_name = f"data/d{ordinal:04d}{ext}"
                while archive_name in used:
                    ordinal += 1; archive_name = f"data/d{ordinal:04d}{ext}"
                used.add(archive_name); zf.write(src, archive_name)
                entries.append({
                    "dataset_id": row.get("id"), "display_name": row.get("display_name"), "archive_path": archive_name,
                    "physical_relative_path": rel, "original_scientific_path": row.get("original_scientific_path"),
                    "canonical_key": row.get("canonical_key"), "columns": row.get("columns") or [],
                    "short_names_enabled": bool(short_names.get("enabled")), "name_mapping_note": short_names.get("note"),
                })
            batch_manifest = {
                "schema": "faradaiq-portable-batch-v273", "version": VERSION, "created": datetime.now().isoformat(timespec="seconds"),
                "technique_key": technique, "method_label": method_label, "method_index": method_index, "point": point,
                "dataset_count": len(entries), "short_names": _json_safe(short_names), "datasets": entries,
            }
            zf.writestr("FaradaIQ_batch_manifest.json", json.dumps(batch_manifest, ensure_ascii=False, indent=2))
            zf.writestr("README.txt", "FaradaIQ portable batch\n\nCompact ZIP filenames avoid Windows path-length failures. Use FaradaIQ_batch_manifest.json for original scientific names, canonical keys, columns, and physical path mapping.\n")
        tmp.replace(zip_path)
        batch_id = f"b{idx:05d}_{token}"
        label = f"{technique.replace('_', ' / ')} — {method_label}" + (f" — Point {point:03d}" if point is not None else "")
        batch = {"id": batch_id, "label": label, "technique_key": technique, "method_label": method_label, "method_index": method_index, "point": point, "dataset_ids": [r.get("id") for r in rows], "dataset_count": len(rows), "zip_path": _posix_rel(zip_path, root), "zip_href": _href(_posix_rel(zip_path, root)), "size_bytes": zip_path.stat().st_size}
        batches.append(batch)
        for row in rows:
            row["batch_id"], row["batch_zip_path"], row["batch_zip_href"] = batch_id, batch["zip_path"], batch["zip_href"]
    for stale in out.glob("batch_*.zip"):
        if stale.name not in expected_archives:
            try: stale.unlink()
            except OSError: pass
    return batches


def _find_primary_downloads(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    h5 = sorted(root.glob("*_faradaiq_bundle.h5")) or sorted(root.glob("*.h5"))
    js = sorted(root.glob("*_faradaiq_bundle.json")) or sorted(root.glob("*canonical*.json"))
    if h5: result["canonical_h5"] = _posix_rel(h5[0], root)
    if js: result["canonical_json"] = _posix_rel(js[0], root)
    return result


def _comparison_summary(datasets: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for d in datasets:
        p = d.get("preview") if isinstance(d.get("preview"), Mapping) else {}
        if p.get("kind") != "table" or p.get("status") != "plot_ready":
            continue
        key = str(d.get("technique_key") or "OTHER")
        row = groups.setdefault(key, {"technique_key": key, "technique_label": d.get("technique_label") or key, "curve_count": 0, "points": [], "methods": set(), "columns": defaultdict(int)})
        row["curve_count"] += 1
        if d.get("point") is not None: row["points"].append(d.get("point"))
        row["methods"].add(str(d.get("method_label") or "Method"))
        for c in d.get("columns") or []: row["columns"][str(c)] += 1
    out = []
    for row in groups.values():
        pts = row.pop("points")
        methods = sorted(row.pop("methods"))
        cols = sorted(row.pop("columns").items(), key=lambda x: (-x[1], x[0]))
        row.update({"point_min": min(pts) if pts else None, "point_max": max(pts) if pts else None, "unique_points": len(set(pts)), "methods": methods, "common_columns": [c for c, _ in cols]})
        out.append(row)
    return {"groups": sorted(out, key=lambda r: (-r["curve_count"], r["technique_key"])), "comparison_curve_count": sum(r["curve_count"] for r in out)}


def build_manifest(experiment_root: str | Path, *, analysis_root: str | Path | None = None, experiment_name: str | None = None, short_names_enabled: bool | None = None, create_batch_zips: bool = True) -> dict[str, Any]:
    root = Path(experiment_root).expanduser().resolve(); root.mkdir(parents=True, exist_ok=True)
    analysis, catalog_path = _find_analysis_root(root, analysis_root), _find_catalog(root)
    datasets = _catalog_datasets(root, analysis, catalog_path) if catalog_path else []
    if not datasets: datasets = _scan_datasets(root, analysis)
    short_names = _short_name_summary(root, enabled=short_names_enabled)
    batches = _write_batch_archives(root, datasets, short_names) if create_batch_zips else []
    files = _scan_files(root); media = _build_media(files, datasets)
    techniques: dict[str, int] = defaultdict(int)
    preview_status: dict[str, int] = defaultdict(int)
    for row in datasets:
        techniques[str(row.get("technique_key") or "OTHER")] += 1
        preview_status[str((row.get("preview") or {}).get("status") or "unavailable")] += 1
    comparison = _comparison_summary(datasets)
    return {
        "schema": SCHEMA, "version": VERSION, "created": datetime.now().isoformat(timespec="seconds"),
        "experiment_name": experiment_name or root.name,
        "portable": True, "relative_paths_only": True, "requires_faradaiq": False, "requires_python": False, "requires_web_server": False, "requires_internet": False,
        "html_file": HTML_NAME, "manifest_file": MANIFEST_NAME,
        "analysis_root": _posix_rel(analysis, root) if analysis and analysis.exists() else "",
        "catalog_source": _posix_rel(catalog_path, root) if catalog_path and catalog_path.exists() else "filesystem_scan",
        "dataset_count": len(datasets), "batch_count": len(batches), "file_count": len(files), "media_count": len(media),
        "technique_counts": dict(sorted(techniques.items())), "preview_status_counts": dict(sorted(preview_status.items())),
        "comparison": comparison, "short_names": short_names, "primary_downloads": _find_primary_downloads(root),
        "datasets": datasets, "batches": batches, "media": media, "files": files,
        "portability_note": "Move or copy the entire experiment folder, not the HTML file alone. All links are relative to this folder.",
        "browser_security_note": "Local web browsers cannot copy arbitrary folders. Individual files and pre-built batch ZIPs use normal browser download/save behavior.",
        "preview_policy": {"source": "authoritative processed table when available", "full_file_sampling": True, "maximum_embedded_rows_per_table": MAX_PREVIEW_ROWS, "images_parsed_as_tables": False},
    }


_CSS = r"""
:root{color-scheme:dark;--bg:#070a0e;--panel:#0e141b;--panel2:#151d27;--line:#2b3948;--text:#edf3f8;--muted:#9fb0c2;--accent:#53a9ff;--accent2:#7bd7ff;--good:#63d68b;--warn:#ffd166;--danger:#ff7b7b;--shadow:0 12px 30px rgba(0,0,0,.28)}*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--bg);color:var(--text);font:14px Segoe UI,Arial,sans-serif}.top{position:sticky;top:0;z-index:20;background:linear-gradient(180deg,#0d141c,#0a0f15);border-bottom:1px solid var(--line);padding:12px 18px}.brandline{display:flex;align-items:center;justify-content:space-between;gap:16px}.brand h1{font-size:21px;margin:0}.muted{color:var(--muted)}.stats{display:flex;gap:8px;flex-wrap:wrap;margin-top:7px}.pill{border:1px solid var(--line);border-radius:999px;padding:4px 9px;background:var(--panel)}.nav{display:flex;gap:4px;margin-top:11px;overflow:auto}.nav button{border:1px solid transparent;background:transparent;color:var(--muted);padding:8px 13px;border-radius:6px;cursor:pointer;font-weight:650;white-space:nowrap}.nav button:hover,.nav button.active{background:#16283a;color:#fff;border-color:#31516e}.page{display:none;padding:14px 16px 24px}.page.active{display:block}.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;border:1px solid #3c536a;background:#172537;color:var(--text);border-radius:6px;padding:7px 10px;text-decoration:none;cursor:pointer}.btn:hover{border-color:var(--accent);background:#1d334b}.btn.primary{background:#0d4f85;border-color:#2e84c3}.btn.small{padding:4px 7px;font-size:12px}.toolbar,.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.compare-layout{display:grid;grid-template-columns:minmax(250px,315px) minmax(0,1fr);gap:12px}.control-panel,.card{background:var(--panel);border:1px solid var(--line);border-radius:9px;box-shadow:var(--shadow)}.control-panel{padding:12px;max-height:calc(100vh - 180px);overflow:auto;position:sticky;top:150px}.control-panel h3,.card h3{margin:3px 0 9px;font-size:15px}.field{margin:9px 0}.field label{display:block;color:#bfd0df;font-size:12px;margin-bottom:4px}.field select,.field input[type=number],.field input[type=search]{width:100%;background:#080d13;color:var(--text);border:1px solid var(--line);border-radius:5px;padding:7px}.field.inline{display:grid;grid-template-columns:1fr 1fr;gap:7px}.checks{display:grid;grid-template-columns:1fr 1fr;gap:5px 8px;margin:8px 0}.range-line{display:grid;grid-template-columns:1fr 48px;gap:8px;align-items:center}.range-line input{width:100%}.plot-column{min-width:0}.plot-card{padding:10px}.plot-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:7px}.plot-title{font-size:17px;font-weight:700}.plot-sub{font-size:12px;color:var(--muted);margin-top:2px}.canvas-wrap{position:relative;background:#05080c;border:1px solid var(--line);border-radius:8px;overflow:hidden;min-height:560px}.canvas-wrap canvas{display:block;width:100%;height:560px;cursor:crosshair}.plot-tip{position:absolute;display:none;pointer-events:none;background:rgba(8,13,19,.95);border:1px solid #49637e;border-radius:6px;padding:7px 9px;font-size:12px;max-width:280px;box-shadow:var(--shadow)}.plot-status{display:flex;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:12px;margin-top:7px}.curve-panel{margin-top:10px;padding:10px}.curve-actions{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}.curve-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));max-height:220px;overflow:auto;border-top:1px solid var(--line)}.curve-item{display:flex;align-items:center;gap:7px;padding:6px;border-bottom:1px solid #22303e;font-size:12px}.swatch{width:20px;height:3px;border-radius:3px;flex:0 0 auto}.dashboard{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin-bottom:12px}.metric{padding:13px}.metric b{font-size:24px;display:block}.metric span{color:var(--muted)}.gallery-toolbar,.dataset-toolbar,.files-toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}.gallery-toolbar input,.gallery-toolbar select,.dataset-toolbar input,.dataset-toolbar select,.files-toolbar input,.files-toolbar select{background:#080d13;color:var(--text);border:1px solid var(--line);border-radius:5px;padding:8px;min-width:180px}.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}.media-card{background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}.media-frame{height:180px;background:#05080c;display:flex;align-items:center;justify-content:center;cursor:pointer}.media-frame img{max-width:100%;max-height:100%;object-fit:contain}.media-body{padding:9px}.media-title{font-weight:650;overflow-wrap:anywhere}.media-meta{color:var(--muted);font-size:12px;margin:4px 0}.lightbox{display:none;position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.92);padding:30px}.lightbox.open{display:flex;align-items:center;justify-content:center}.lightbox img{max-width:92vw;max-height:88vh;object-fit:contain}.lightbox .close{position:absolute;top:16px;right:18px;font-size:26px}.dataset-layout{display:grid;grid-template-columns:minmax(310px,38%) 1fr;gap:12px}.dataset-list{max-height:calc(100vh - 225px);overflow:auto}.section{background:var(--panel);border:1px solid var(--line);border-radius:7px;margin-bottom:8px;overflow:hidden}.section>summary{cursor:pointer;padding:10px 12px;background:var(--panel2);font-weight:650}.dataset-row{padding:8px 10px;border-top:1px solid #22303e;cursor:pointer}.dataset-row:hover,.dataset-row.selected{background:#18304a}.dataset-row .sub{font-size:12px;color:var(--muted);margin-top:2px}.detail{padding:13px}.meta{display:grid;grid-template-columns:185px minmax(0,1fr);border:1px solid var(--line);border-radius:6px;overflow:hidden}.meta div{padding:7px 9px;border-bottom:1px solid #26313e}.meta div:nth-child(odd){color:var(--muted);background:#111821}.path{font-family:Consolas,monospace;overflow-wrap:anywhere}.detail-canvas{height:390px;border:1px solid var(--line);border-radius:7px;background:#05080c}.detail-canvas canvas{width:100%;height:390px}.image-detail{display:flex;justify-content:center;min-height:360px;background:#05080c;border:1px solid var(--line);border-radius:7px}.image-detail img{max-width:100%;max-height:70vh;object-fit:contain}.tablewrap{overflow:auto;max-height:360px;border:1px solid var(--line);border-radius:6px;margin-top:10px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid #26313e;padding:6px 8px;text-align:right;white-space:nowrap}th{position:sticky;top:0;background:#17202b;color:#cfe4f7}.file-groups{display:grid;gap:9px}.file{display:grid;grid-template-columns:minmax(0,1fr) 100px 165px;gap:8px;padding:7px 9px;border-top:1px solid #25303c;align-items:center}.file a{color:#83c1ff;text-decoration:none;overflow-wrap:anywhere}.note{border-left:4px solid var(--warn);padding:9px 11px;background:#211d11;margin:10px 0}.good{border-left-color:var(--good);background:#102117}.empty{padding:18px;color:var(--muted)}@media(max-width:980px){.compare-layout,.dataset-layout{grid-template-columns:1fr}.control-panel{position:static;max-height:none}.canvas-wrap,.canvas-wrap canvas{min-height:430px;height:430px}}@media(max-width:620px){.page{padding:10px}.file{grid-template-columns:1fr}.checks{grid-template-columns:1fr}.brandline{display:block}}
"""

_JS = r"""
const M=window.FARADAIQ_MANIFEST;const q=s=>document.querySelector(s),qa=s=>[...document.querySelectorAll(s)];const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const href=p=>String(p||'').split('/').map(encodeURIComponent).join('/');const fmt=n=>{n=Number(n||0);if(n<1024)return n+' B';if(n<1048576)return(n/1024).toFixed(1)+' KB';if(n<1073741824)return(n/1048576).toFixed(1)+' MB';return(n/1073741824).toFixed(2)+' GB'};const num=v=>typeof v==='number'&&Number.isFinite(v);const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
let compare={tech:'',method:'',x:'',y:'',mode:'overlay',norm:'none',selected:new Set(),pointMin:null,pointMax:null,opacity:.52,width:1.2,offset:.12,logX:false,logY:false,invertY:false,legend:true,view:null,drag:null,lastRender:[]};let current=null;
function colorFor(i,n,a=1){const h=(208+i*317.508/n*6)%360;return `hsla(${h},78%,62%,${a})`}
function showPage(name){qa('.page').forEach(x=>x.classList.toggle('active',x.id==='page-'+name));qa('.nav button').forEach(x=>x.classList.toggle('active',x.dataset.page===name));if(name==='compare')requestAnimationFrame(renderComparison);if(name==='gallery')renderGallery();if(name==='datasets')renderDatasetList();if(name==='files')renderFiles()}
function plottable(){return M.datasets.filter(d=>d.preview&&d.preview.kind==='table'&&d.preview.status==='plot_ready')}
function methodsFor(tech){return [...new Set(plottable().filter(d=>!tech||d.technique_key===tech).map(d=>d.method_label||'Method'))].sort()}
function commonColumns(rows){const c={};rows.forEach(d=>(d.columns||[]).forEach(x=>c[x]=(c[x]||0)+1));return Object.entries(c).sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).map(x=>x[0])}
function activeCurves(){return plottable().filter(d=>(!compare.tech||d.technique_key===compare.tech)&&(!compare.method||d.method_label===compare.method)&&(d.point==null||compare.pointMin==null||d.point>=compare.pointMin)&&(d.point==null||compare.pointMax==null||d.point<=compare.pointMax)&&compare.selected.has(d.id))}
function chooseDefaults(){const groups=(M.comparison&&M.comparison.groups)||[];compare.tech=groups.length?groups[0].technique_key:(plottable()[0]?.technique_key||'');const all=plottable().filter(d=>d.technique_key===compare.tech);compare.method=methodsFor(compare.tech)[0]||'';const rows=all.filter(d=>!compare.method||d.method_label===compare.method);const points=rows.map(d=>d.point).filter(v=>v!=null);compare.pointMin=points.length?Math.min(...points):null;compare.pointMax=points.length?Math.max(...points):null;compare.selected=new Set(rows.map(d=>d.id));const first=rows[0];compare.x=first?.plot_spec?.x_column||commonColumns(rows)[0]||'';compare.y=first?.plot_spec?.y_column||commonColumns(rows).find(c=>c!==compare.x)||'';compare.logX=!!first?.plot_spec?.log_x_default;compare.invertY=!!first?.plot_spec?.invert_y_default}
function populateCompareControls(resetSelection=false){const techs=[...new Set(plottable().map(d=>d.technique_key))].sort();q('#cmpTech').innerHTML=techs.map(t=>`<option ${t===compare.tech?'selected':''}>${esc(t)}</option>`).join('');const methods=methodsFor(compare.tech);if(!methods.includes(compare.method))compare.method=methods[0]||'';q('#cmpMethod').innerHTML=methods.map(m=>`<option ${m===compare.method?'selected':''}>${esc(m)}</option>`).join('');const candidates=plottable().filter(d=>d.technique_key===compare.tech&&(!compare.method||d.method_label===compare.method));if(resetSelection)compare.selected=new Set(candidates.map(d=>d.id));const cols=commonColumns(candidates);if(!cols.includes(compare.x))compare.x=candidates[0]?.plot_spec?.x_column||cols[0]||'';if(!cols.includes(compare.y)||compare.y===compare.x)compare.y=candidates[0]?.plot_spec?.y_column||cols.find(c=>c!==compare.x)||'';q('#cmpX').innerHTML=cols.map(c=>`<option ${c===compare.x?'selected':''}>${esc(c)}</option>`).join('');q('#cmpY').innerHTML=cols.map(c=>`<option ${c===compare.y?'selected':''}>${esc(c)}</option>`).join('');const pts=candidates.map(d=>d.point).filter(v=>v!=null);const lo=pts.length?Math.min(...pts):0,hi=pts.length?Math.max(...pts):0;if(compare.pointMin==null||compare.pointMin<lo||compare.pointMin>hi)compare.pointMin=lo;if(compare.pointMax==null||compare.pointMax<lo||compare.pointMax>hi)compare.pointMax=hi;q('#pointMin').value=compare.pointMin??'';q('#pointMax').value=compare.pointMax??'';q('#logX').checked=compare.logX;q('#logY').checked=compare.logY;q('#invertY').checked=compare.invertY;q('#showLegend').checked=compare.legend;renderCurveList()}
function renderCurveList(){const rows=plottable().filter(d=>d.technique_key===compare.tech&&(!compare.method||d.method_label===compare.method));q('#curveCount').textContent=`${activeCurves().length} shown / ${rows.length} available`;q('#curveList').innerHTML=rows.map((d,i)=>`<label class="curve-item"><input type="checkbox" data-curve="${d.id}" ${compare.selected.has(d.id)?'checked':''}><span class="swatch" style="background:${colorFor(i,rows.length)}"></span><span>${esc(d.point!=null?'Point '+String(d.point).padStart(3,'0'):d.display_name)}</span></label>`).join('');qa('[data-curve]').forEach(el=>el.onchange=()=>{el.checked?compare.selected.add(el.dataset.curve):compare.selected.delete(el.dataset.curve);renderCurveList();renderComparison()})}
function curveData(d){const p=d.preview||{},xi=(d.columns||[]).indexOf(compare.x),yi=(d.columns||[]).indexOf(compare.y);if(xi<0||yi<0)return[];let pts=(p.rows||[]).filter(r=>num(r[xi])&&num(r[yi])).map(r=>[r[xi],r[yi]]);if(compare.logX)pts=pts.filter(v=>v[0]>0).map(v=>[Math.log10(v[0]),v[1]]);if(compare.logY)pts=pts.filter(v=>v[1]>0).map(v=>[v[0],Math.log10(v[1])]);if(!pts.length)return pts;const ys=pts.map(v=>v[1]);if(compare.norm==='maxabs'){const s=Math.max(...ys.map(Math.abs))||1;pts=pts.map(v=>[v[0],v[1]/s])}else if(compare.norm==='minmax'){const lo=Math.min(...ys),hi=Math.max(...ys),s=hi-lo||1;pts=pts.map(v=>[v[0],(v[1]-lo)/s])}else if(compare.norm==='baseline'){const b=ys[0];pts=pts.map(v=>[v[0],v[1]-b])}else if(compare.norm==='area'){let a=0;for(let i=1;i<pts.length;i++)a+=Math.abs((pts[i][0]-pts[i-1][0])*(pts[i][1]+pts[i-1][1])/2);a=a||1;pts=pts.map(v=>[v[0],v[1]/a])}return pts}
function fitBounds(series){const xs=[],ys=[];series.forEach(s=>s.pts.forEach(v=>{xs.push(v[0]);ys.push(v[1]+s.offset)}));if(!xs.length)return{x0:0,x1:1,y0:0,y1:1};let x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);if(x0===x1){x0-=.5;x1+=.5}if(y0===y1){y0-=.5;y1+=.5}const px=(x1-x0)*.04,py=(y1-y0)*.07;return{x0:x0-px,x1:x1+px,y0:y0-py,y1:y1+py}}
function nice(v){if(!Number.isFinite(v))return'';const a=Math.abs(v);return a!==0&&(a>=1e4||a<1e-3)?v.toExponential(3):v.toPrecision(5).replace(/\.?0+$/,'')}
function prepCanvas(c,height=560){const r=c.getBoundingClientRect(),d=window.devicePixelRatio||1;c.width=Math.max(600,Math.round(r.width*d));c.height=Math.round(height*d);const x=c.getContext('2d');x.setTransform(d,0,0,d,0,0);return{x,w:r.width,h:height}}
function axes(ctx,w,h,b,L=72,R=25,T=28,B=52){ctx.strokeStyle='#53687d';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(L,T);ctx.lineTo(L,h-B);ctx.lineTo(w-R,h-B);ctx.stroke();ctx.fillStyle='#aab9c8';ctx.font='12px Segoe UI';for(let i=0;i<=5;i++){const f=i/5,px=L+f*(w-L-R),py=h-B-f*(h-T-B);ctx.strokeStyle='rgba(83,104,125,.25)';ctx.beginPath();ctx.moveTo(px,T);ctx.lineTo(px,h-B);ctx.moveTo(L,py);ctx.lineTo(w-R,py);ctx.stroke();ctx.fillStyle='#aab9c8';ctx.fillText(nice(b.x0+f*(b.x1-b.x0)),px-18,h-B+20);ctx.fillText(nice(b.y0+f*(b.y1-b.y0)),5,py+4)}ctx.fillStyle='#d6e4ef';ctx.fillText((compare.logX?'log10 ':'')+compare.x,w/2-40,h-12);ctx.save();ctx.translate(17,h/2+30);ctx.rotate(-Math.PI/2);ctx.fillText((compare.logY?'log10 ':'')+(compare.invertY?'- ':'')+compare.y,0,0);ctx.restore();return{L,R,T,B}}
function renderComparison(){const canvas=q('#compareCanvas');if(!canvas)return;const {x:ctx,w,h}=prepCanvas(canvas,560);ctx.fillStyle='#05080c';ctx.fillRect(0,0,w,h);let curves=activeCurves().map((d,i)=>({d,pts:curveData(d),i,offset:0})).filter(s=>s.pts.length>1);if(compare.invertY)curves.forEach(s=>s.pts=s.pts.map(v=>[v[0],-v[1]]));if(compare.mode==='waterfall'){const allY=curves.flatMap(s=>s.pts.map(v=>v[1]));const span=(Math.max(...allY)-Math.min(...allY)||1)*compare.offset;curves.forEach((s,i)=>s.offset=i*span)}if(compare.mode==='small'){drawSmallMultiples(ctx,w,h,curves);compare.lastRender=curves;updatePlotText(curves);return}if(compare.mode==='metric'){drawMetric(ctx,w,h,curves);compare.lastRender=curves;updatePlotText(curves);return}let b=compare.view||fitBounds(curves),pad=axes(ctx,w,h,b),sx=v=>pad.L+(v-b.x0)/(b.x1-b.x0)*(w-pad.L-pad.R),sy=v=>pad.T+(1-(v-b.y0)/(b.y1-b.y0))*(h-pad.T-pad.B);ctx.save();ctx.beginPath();ctx.rect(pad.L,pad.T,w-pad.L-pad.R,h-pad.T-pad.B);ctx.clip();curves.forEach((s,i)=>{ctx.strokeStyle=colorFor(i,curves.length,compare.opacity);ctx.lineWidth=compare.width;ctx.beginPath();s.pts.forEach((v,j)=>{const px=sx(v[0]),py=sy(v[1]+s.offset);j?ctx.lineTo(px,py):ctx.moveTo(px,py)});ctx.stroke()});ctx.restore();if(compare.legend&&curves.length<=36){ctx.font='11px Segoe UI';curves.slice(0,36).forEach((s,i)=>{const x=w-155,y=18+i*14;ctx.fillStyle=colorFor(i,curves.length,1);ctx.fillRect(x,y-7,18,2);ctx.fillStyle='#d9e4ec';ctx.fillText(s.d.point!=null?'Point '+String(s.d.point).padStart(3,'0'):s.d.display_name,x+24,y-3)})}compare.lastRender=curves.map(s=>({...s,b,pad,sx,sy}));updatePlotText(curves)}
function drawSmallMultiples(ctx,w,h,curves){if(!curves.length){emptyPlot(ctx,'No selected curves contain the chosen x/y columns.');return}const cols=Math.ceil(Math.sqrt(curves.length*w/h)),rows=Math.ceil(curves.length/cols),cw=w/cols,ch=h/rows;curves.forEach((s,i)=>{const c=i%cols,r=Math.floor(i/cols),x0=c*cw,y0=r*ch,b=fitBounds([s]),L=x0+7,R=x0+cw-5,T=y0+15,B=y0+ch-7,sx=v=>L+(v-b.x0)/(b.x1-b.x0)*(R-L),sy=v=>T+(1-(v-b.y0)/(b.y1-b.y0))*(B-T);ctx.strokeStyle='rgba(95,116,136,.5)';ctx.strokeRect(x0+.5,y0+.5,cw-1,ch-1);ctx.strokeStyle=colorFor(i,curves.length,.9);ctx.lineWidth=1;ctx.beginPath();s.pts.forEach((v,j)=>j?ctx.lineTo(sx(v[0]),sy(v[1])):ctx.moveTo(sx(v[0]),sy(v[1])));ctx.stroke();ctx.fillStyle='#c9d7e2';ctx.font='10px Segoe UI';ctx.fillText(s.d.point!=null?'P'+String(s.d.point).padStart(3,'0'):String(i+1),x0+5,y0+11)})}
function drawMetric(ctx,w,h,curves){if(!curves.length){emptyPlot(ctx,'No selected curves contain the chosen x/y columns.');return}const vals=curves.map(s=>{const y=s.pts.map(v=>v[1]);let v=q('#metricMode').value==='min'?Math.min(...y):q('#metricMode').value==='final'?y[y.length-1]:q('#metricMode').value==='mean'?y.reduce((a,b)=>a+b,0)/y.length:Math.max(...y);return{x:s.d.point??s.i,y:v,d:s.d}}).sort((a,b)=>a.x-b.x);const fake=[{pts:vals.map(v=>[v.x,v.y]),offset:0}],b=fitBounds(fake),p=axes(ctx,w,h,b),sx=v=>p.L+(v-b.x0)/(b.x1-b.x0)*(w-p.L-p.R),sy=v=>p.T+(1-(v-b.y0)/(b.y1-b.y0))*(h-p.T-p.B);ctx.strokeStyle='#62b5ff';ctx.lineWidth=1.6;ctx.beginPath();vals.forEach((v,i)=>{i?ctx.lineTo(sx(v.x),sy(v.y)):ctx.moveTo(sx(v.x),sy(v.y))});ctx.stroke();ctx.fillStyle='#8bd0ff';vals.forEach(v=>{ctx.beginPath();ctx.arc(sx(v.x),sy(v.y),2.6,0,Math.PI*2);ctx.fill()})}
function emptyPlot(ctx,msg){ctx.fillStyle='#9fb0c2';ctx.font='16px Segoe UI';ctx.fillText(msg,28,48)}
function updatePlotText(curves){q('#plotTitle').textContent=`${compare.tech||'Comparison'} · ${compare.method||'All methods'}`;q('#plotSub').textContent=`${curves.length} curves · ${compare.x||'x'} vs ${compare.y||'y'} · ${compare.mode}`;q('#plotStatus').innerHTML=`<span>${curves.length} selected curves</span><span>full-file sampled previews</span><span>wheel: zoom</span><span>drag: pan</span><span>double-click: fit</span>`;q('#curveCount').textContent=`${curves.length} shown`}
function resetView(){compare.view=null;renderComparison()}
function downloadPlot(){const a=document.createElement('a');a.download=`FaradaIQ_${compare.tech}_${compare.mode}.png`;a.href=q('#compareCanvas').toDataURL('image/png');a.click()}
function bindCanvas(){const c=q('#compareCanvas');c.addEventListener('wheel',e=>{e.preventDefault();if(!compare.lastRender.length||compare.mode==='small'||compare.mode==='metric')return;const r=c.getBoundingClientRect(),x=(e.clientX-r.left)/r.width,y=(e.clientY-r.top)/r.height,b=compare.view||compare.lastRender[0].b||fitBounds(compare.lastRender),z=e.deltaY<0?.82:1.22,cx=b.x0+x*(b.x1-b.x0),cy=b.y1-y*(b.y1-b.y0);compare.view={x0:cx+(b.x0-cx)*z,x1:cx+(b.x1-cx)*z,y0:cy+(b.y0-cy)*z,y1:cy+(b.y1-cy)*z};renderComparison()},{passive:false});c.addEventListener('pointerdown',e=>{if(compare.mode==='small'||compare.mode==='metric')return;c.setPointerCapture(e.pointerId);compare.drag={x:e.clientX,y:e.clientY,b:{...(compare.view||compare.lastRender[0]?.b||fitBounds(compare.lastRender))}}});c.addEventListener('pointermove',e=>{hoverPlot(e);if(!compare.drag)return;const r=c.getBoundingClientRect(),dx=(e.clientX-compare.drag.x)/r.width,dy=(e.clientY-compare.drag.y)/r.height,b=compare.drag.b;compare.view={x0:b.x0-dx*(b.x1-b.x0),x1:b.x1-dx*(b.x1-b.x0),y0:b.y0+dy*(b.y1-b.y0),y1:b.y1+dy*(b.y1-b.y0)};renderComparison()});c.addEventListener('pointerup',()=>compare.drag=null);c.addEventListener('pointerleave',()=>{compare.drag=null;q('#plotTip').style.display='none'});c.ondblclick=resetView}
function hoverPlot(e){if(compare.drag||!compare.lastRender.length||compare.mode==='small'||compare.mode==='metric')return;const c=q('#compareCanvas'),r=c.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;let best=null;compare.lastRender.forEach((s,si)=>s.pts.forEach(v=>{const px=s.sx(v[0]),py=s.sy(v[1]+s.offset),d=(px-mx)**2+(py-my)**2;if(d<180&&(best==null||d<best.d))best={d,px,py,v,s,si}}));const tip=q('#plotTip');if(!best){tip.style.display='none';return}tip.style.display='block';tip.style.left=clamp(best.px+12,8,r.width-260)+'px';tip.style.top=clamp(best.py+12,8,r.height-85)+'px';tip.innerHTML=`<b>${esc(best.s.d.display_name)}</b><br>${esc(compare.x)}: ${nice(best.v[0])}<br>${esc(compare.y)}: ${nice(compare.invertY?-best.v[1]:best.v[1])}`}
function renderGallery(){const term=q('#gallerySearch').value.trim().toLowerCase(),kind=q('#galleryKind').value,lo=Number(q('#galleryMin').value||-Infinity),hi=Number(q('#galleryMax').value||Infinity);const rows=(M.media||[]).filter(m=>(!term||(m.display_name+' '+m.path).toLowerCase().includes(term))&&(!kind||m.category===kind)&&(m.point==null||m.point>=lo)&&(m.point==null||m.point<=hi));q('#galleryCount').textContent=`${rows.length} media files`;q('#gallery').innerHTML=rows.map(m=>`<div class="media-card"><div class="media-frame" onclick="openLightbox('${m.id}')">${m.browser_renderable?`<img loading="lazy" src="${m.href||href(m.path)}" alt="${esc(m.display_name)}">`:`<div class="empty">Preview not supported by this browser</div>`}</div><div class="media-body"><div class="media-title">${esc(m.display_name||m.name)}</div><div class="media-meta">${m.point!=null?'Point '+String(m.point).padStart(3,'0')+' · ':''}${esc(m.category)} · ${fmt(m.size_bytes)}</div><div class="actions"><a class="btn small" href="${m.href||href(m.path)}" target="_blank">Open</a><a class="btn small" href="${m.href||href(m.path)}" download>Download</a>${m.dataset_id?`<button class="btn small" onclick="openDataset('${m.dataset_id}')">Dataset</button>`:''}</div></div></div>`).join('')||'<div class="empty">No media match the current filters.</div>'}
function openLightbox(id){const m=(M.media||[]).find(x=>x.id===id);if(!m||!m.browser_renderable)return;q('#lightboxImage').src=m.href||href(m.path);q('#lightboxTitle').textContent=m.display_name||m.name;q('#lightbox').classList.add('open')}
function closeLightbox(){q('#lightbox').classList.remove('open');q('#lightboxImage').src=''}
function renderDatasetList(){const term=q('#dataSearch').value.trim().toLowerCase(),tech=q('#dataTech').value;const groups={};M.datasets.forEach(d=>{const hay=(d.display_name+' '+d.original_scientific_path+' '+d.physical_path+' '+d.role).toLowerCase();if(term&&!hay.includes(term))return;if(tech&&d.technique_key!==tech)return;(groups[d.technique_label||d.technique_key]??=[]).push(d)});q('#datasetList').innerHTML=Object.entries(groups).sort().map(([name,rows],gi)=>`<details class="section" ${gi<2?'open':''}><summary>${esc(name)} <span class="muted">(${rows.length})</span></summary>${rows.map(d=>`<div class="dataset-row ${current&&current.id===d.id?'selected':''}" data-id="${d.id}" onclick="selectDataset('${d.id}')"><b>${esc(d.display_name)}</b><div class="sub">${esc(d.role)} · ${fmt(d.size_bytes)} · ${esc((d.preview||{}).status||'unavailable')}</div></div>`).join('')}</details>`).join('')||'<div class="empty">No matching datasets.</div>'}
function metaRow(k,v){if(v===null||v===undefined||v==='')return'';return`<div>${esc(k)}</div><div class="path">${esc(Array.isArray(v)?v.join(', '):v)}</div>`}
function selectDataset(id){current=M.datasets.find(x=>x.id===id);renderDatasetList();if(!current)return;const d=current,p=d.preview||{},acts=[`<a class="btn primary" href="${d.physical_href||href(d.physical_path)}" download>Download data file</a>`,`<a class="btn" href="${d.physical_href||href(d.physical_path)}" target="_blank">Open file</a>`];if(d.batch_zip_href)acts.push(`<a class="btn" href="${d.batch_zip_href}" download>Download point batch ZIP</a>`);let preview='';if(p.kind==='image'){preview=p.browser_renderable?`<div class="image-detail"><img src="${p.image_href||href(p.image_path)}" alt="${esc(d.display_name)}"></div>`:`<div class="note">This image format may not render in the browser. Use Open file or Download data file.</div>`}else if(p.rows&&p.rows.length){preview=`<div class="detail-canvas"><canvas id="detailCanvas"></canvas></div><div id="previewTable" class="tablewrap"></div>`}else{preview=`<div class="note">A numeric plot is not available for this file. ${esc(p.error||'The file has no embedded numeric rows.')} The original file remains available above.</div>`}q('#datasetDetail').innerHTML=`<h2>${esc(d.display_name)}</h2><div class="actions">${acts.join('')}</div><div class="meta">${metaRow('Technique',d.technique_label)}${metaRow('Method',d.method_label)}${metaRow('Point',d.point)}${metaRow('Cycle',d.cycle)}${metaRow('Sweep',d.sweep)}${metaRow('Role',d.role)}${metaRow('Actual columns',d.columns)}${metaRow('Declared columns',d.declared_columns)}${metaRow('Rows in source',d.n_rows)}${metaRow('Rows embedded',p.sampled_rows)}${metaRow('Original scientific path',d.original_scientific_path)}${metaRow('Physical relative path',d.physical_path)}${metaRow('Canonical key',d.canonical_key)}</div><h3>Preview</h3>${preview}`;if(p.kind==='table'&&p.rows&&p.rows.length){drawDetail(d);renderPreviewTable(p)}}
function openDataset(id){showPage('datasets');selectDataset(id)}
function drawDetail(d){const c=q('#detailCanvas');if(!c)return;const p=d.preview||{},xcol=d.plot_spec?.x_column||d.columns?.[0],ycol=d.plot_spec?.y_column||d.columns?.[1],xi=(d.columns||[]).indexOf(xcol),yi=(d.columns||[]).indexOf(ycol),pts=(p.rows||[]).filter(r=>num(r[xi])&&num(r[yi])).map(r=>[r[xi],d.plot_spec?.invert_y_default?-r[yi]:r[yi]]);const {x:ctx,w,h}=prepCanvas(c,390);ctx.fillStyle='#05080c';ctx.fillRect(0,0,w,h);if(pts.length<2){emptyPlot(ctx,'Table preview is available below; no varying numeric pair was found.');return}const old={...compare};compare.x=xcol;compare.y=ycol;const b=fitBounds([{pts,offset:0}]),pax=axes(ctx,w,h,b),sx=v=>pax.L+(v-b.x0)/(b.x1-b.x0)*(w-pax.L-pax.R),sy=v=>pax.T+(1-(v-b.y0)/(b.y1-b.y0))*(h-pax.T-pax.B);ctx.strokeStyle='#58adff';ctx.lineWidth=1.7;ctx.beginPath();pts.forEach((v,i)=>i?ctx.lineTo(sx(v[0]),sy(v[1])):ctx.moveTo(sx(v[0]),sy(v[1])));ctx.stroke();Object.assign(compare,old)}
function renderPreviewTable(p){const box=q('#previewTable');if(!box)return;const cols=p.columns||[];box.innerHTML='<table><thead><tr>'+cols.map(c=>`<th>${esc(c)}</th>`).join('')+'</tr></thead><tbody>'+(p.rows||[]).slice(0,120).map(r=>'<tr>'+cols.map((_,i)=>`<td>${esc(num(r[i])?nice(r[i]):r[i]??'')}</td>`).join('')+'</tr>').join('')+'</tbody></table>'}
function renderFiles(){const term=q('#fileSearch').value.trim().toLowerCase(),cat=q('#fileCategory').value;const groups={};(M.files||[]).forEach(f=>{if(term&&!(f.path+' '+f.name).toLowerCase().includes(term))return;if(cat&&f.category!==cat)return;(groups[f.category]??=[]).push(f)});q('#fileGroups').innerHTML=Object.entries(groups).sort().map(([g,rows])=>`<details class="section" open><summary>${esc(g)} <span class="muted">(${rows.length})</span></summary>${rows.map(f=>`<div class="file"><a href="${f.href||href(f.path)}" target="_blank">${esc(f.path)}</a><span>${fmt(f.size_bytes)}</span><span class="actions"><a class="btn small" href="${f.href||href(f.path)}" target="_blank">Open</a><a class="btn small" href="${f.href||href(f.path)}" download>Download</a></span></div>`).join('')}</details>`).join('')||'<div class="empty">No files match the filters.</div>'}
function init(){q('#title').textContent=M.experiment_name;q('#stats').innerHTML=`<span class="pill">${M.dataset_count} datasets</span><span class="pill">${M.comparison?.comparison_curve_count||0} comparison curves</span><span class="pill">${M.media_count||0} photos / figures</span><span class="pill">${M.batch_count} batch ZIPs</span><span class="pill">offline and portable</span>`;qa('.nav button').forEach(b=>b.onclick=()=>showPage(b.dataset.page));chooseDefaults();populateCompareControls(true);const techOptions=[...new Set(M.datasets.map(d=>d.technique_key))].sort();q('#dataTech').innerHTML='<option value="">All techniques</option>'+techOptions.map(t=>`<option>${esc(t)}</option>`).join('');const cats=[...new Set((M.files||[]).map(f=>f.category))].sort();q('#fileCategory').innerHTML='<option value="">All file categories</option>'+cats.map(t=>`<option>${esc(t)}</option>`).join('');const mediaCats=[...new Set((M.media||[]).map(f=>f.category))].sort();q('#galleryKind').innerHTML='<option value="">All media</option>'+mediaCats.map(t=>`<option>${esc(t)}</option>`).join('');const mediaPts=(M.media||[]).map(m=>m.point).filter(v=>v!=null);if(mediaPts.length){q('#galleryMin').value=Math.min(...mediaPts);q('#galleryMax').value=Math.max(...mediaPts)}const p=M.primary_downloads||{},top=[];if(p.canonical_h5)top.push(`<a class="btn primary" href="${href(p.canonical_h5)}" download>Canonical H5</a>`);if(p.canonical_json)top.push(`<a class="btn" href="${href(p.canonical_json)}" download>Canonical JSON</a>`);top.push(`<a class="btn" href="${href(M.manifest_file)}" download>Browser manifest</a>`);q('#primary').innerHTML=top.join('');q('#shortNote').innerHTML=M.short_names.enabled?`<div class="note good"><b>Short physical names enabled.</b> Original scientific identity is preserved in this browser and manifests.</div>`:`<div class="note good"><b>Scientific names preserved.</b> All links are relative and portable.</div>`;bindControls();bindCanvas();renderComparison();renderGallery();renderDatasetList();renderFiles();if(M.datasets.length)selectDataset(M.datasets[0].id);window.addEventListener('resize',()=>{if(q('#page-compare').classList.contains('active'))renderComparison()})}
function bindControls(){q('#cmpTech').onchange=e=>{compare.tech=e.target.value;compare.method='';compare.view=null;populateCompareControls(true);renderComparison()};q('#cmpMethod').onchange=e=>{compare.method=e.target.value;compare.view=null;populateCompareControls(true);renderComparison()};q('#cmpX').onchange=e=>{compare.x=e.target.value;compare.view=null;renderComparison()};q('#cmpY').onchange=e=>{compare.y=e.target.value;compare.view=null;renderComparison()};q('#cmpMode').onchange=e=>{compare.mode=e.target.value;q('#metricField').style.display=compare.mode==='metric'?'block':'none';compare.view=null;renderComparison()};q('#norm').onchange=e=>{compare.norm=e.target.value;compare.view=null;renderComparison()};q('#metricMode').onchange=renderComparison;['pointMin','pointMax'].forEach(id=>q('#'+id).onchange=e=>{compare[id==='pointMin'?'pointMin':'pointMax']=Number(e.target.value);renderCurveList();renderComparison()});q('#opacity').oninput=e=>{compare.opacity=Number(e.target.value);q('#opacityVal').textContent=compare.opacity.toFixed(2);renderComparison()};q('#lineWidth').oninput=e=>{compare.width=Number(e.target.value);q('#widthVal').textContent=compare.width.toFixed(1);renderComparison()};q('#offset').oninput=e=>{compare.offset=Number(e.target.value);q('#offsetVal').textContent=compare.offset.toFixed(2);renderComparison()};q('#logX').onchange=e=>{compare.logX=e.target.checked;compare.view=null;renderComparison()};q('#logY').onchange=e=>{compare.logY=e.target.checked;compare.view=null;renderComparison()};q('#invertY').onchange=e=>{compare.invertY=e.target.checked;compare.view=null;renderComparison()};q('#showLegend').onchange=e=>{compare.legend=e.target.checked;renderComparison()};q('#selectAll').onclick=()=>{plottable().filter(d=>d.technique_key===compare.tech&&(!compare.method||d.method_label===compare.method)).forEach(d=>compare.selected.add(d.id));renderCurveList();renderComparison()};q('#selectNone').onclick=()=>{plottable().filter(d=>d.technique_key===compare.tech&&(!compare.method||d.method_label===compare.method)).forEach(d=>compare.selected.delete(d.id));renderCurveList();renderComparison()};q('#invertSelect').onclick=()=>{plottable().filter(d=>d.technique_key===compare.tech&&(!compare.method||d.method_label===compare.method)).forEach(d=>compare.selected.has(d.id)?compare.selected.delete(d.id):compare.selected.add(d.id));renderCurveList();renderComparison()};q('#resetView').onclick=resetView;q('#downloadPlot').onclick=downloadPlot;['gallerySearch','galleryKind','galleryMin','galleryMax'].forEach(id=>q('#'+id).addEventListener('input',renderGallery));['dataSearch','dataTech'].forEach(id=>q('#'+id).addEventListener('input',renderDatasetList));['fileSearch','fileCategory'].forEach(id=>q('#'+id).addEventListener('input',renderFiles));q('#lightbox').onclick=e=>{if(e.target.id==='lightbox'||e.target.classList.contains('close'))closeLightbox()};document.addEventListener('keydown',e=>{if(e.key==='Escape')closeLightbox()})}
document.addEventListener('DOMContentLoaded',init);
"""


def _render_html(manifest: Mapping[str, Any]) -> str:
    payload = json.dumps(_json_safe(manifest), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    title = html.escape(str(manifest.get("experiment_name") or "FaradaIQ Data Browser"))
    return f"""<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{title} — FaradaIQ Data Browser</title><style>{_CSS}</style></head><body>
<header class=\"top\"><div class=\"brandline\"><div class=\"brand\"><h1>FaradaIQ Data Browser</h1><span id=\"title\" class=\"muted\"></span></div><div id=\"primary\" class=\"actions\"></div></div><div id=\"stats\" class=\"stats\"></div><nav class=\"nav\"><button class=\"active\" data-page=\"compare\">Compare all points</button><button data-page=\"gallery\">Photos and figures</button><button data-page=\"datasets\">Dataset browser</button><button data-page=\"files\">All files</button><button data-page=\"about\">About / portability</button></nav></header>
<section id=\"page-compare\" class=\"page active\"><div class=\"dashboard\"><div class=\"card metric\"><b>{manifest.get('comparison',{}).get('comparison_curve_count',0)}</b><span>plottable curves</span></div><div class=\"card metric\"><b>{manifest.get('media_count',0)}</b><span>photos and figures</span></div><div class=\"card metric\"><b>{manifest.get('dataset_count',0)}</b><span>indexed datasets</span></div><div class=\"card metric\"><b>{manifest.get('batch_count',0)}</b><span>downloadable batches</span></div></div><div class=\"compare-layout\"><aside class=\"control-panel\"><h3>Comparison controls</h3><div class=\"field\"><label>Technique</label><select id=\"cmpTech\"></select></div><div class=\"field\"><label>Method</label><select id=\"cmpMethod\"></select></div><div class=\"field inline\"><div><label>X column</label><select id=\"cmpX\"></select></div><div><label>Y column</label><select id=\"cmpY\"></select></div></div><div class=\"field inline\"><div><label>Point from</label><input id=\"pointMin\" type=\"number\"></div><div><label>Point to</label><input id=\"pointMax\" type=\"number\"></div></div><div class=\"field\"><label>Plot mode</label><select id=\"cmpMode\"><option value=\"overlay\">Overlay all curves</option><option value=\"waterfall\">Waterfall / stacked</option><option value=\"small\">Small multiples</option><option value=\"metric\">Metric versus point</option></select></div><div id=\"metricField\" class=\"field\" style=\"display:none\"><label>Metric</label><select id=\"metricMode\"><option value=\"max\">Maximum</option><option value=\"min\">Minimum</option><option value=\"final\">Final value</option><option value=\"mean\">Mean</option></select></div><div class=\"field\"><label>Normalization</label><select id=\"norm\"><option value=\"none\">None</option><option value=\"maxabs\">Maximum absolute</option><option value=\"minmax\">0–1 min/max</option><option value=\"baseline\">Subtract first value</option><option value=\"area\">Normalize by absolute area</option></select></div><div class=\"checks\"><label><input id=\"logX\" type=\"checkbox\"> Log X</label><label><input id=\"logY\" type=\"checkbox\"> Log Y</label><label><input id=\"invertY\" type=\"checkbox\"> Invert Y</label><label><input id=\"showLegend\" type=\"checkbox\" checked> Legend</label></div><div class=\"field\"><label>Opacity</label><div class=\"range-line\"><input id=\"opacity\" type=\"range\" min=\"0.05\" max=\"1\" step=\"0.01\" value=\"0.52\"><span id=\"opacityVal\">0.52</span></div></div><div class=\"field\"><label>Line width</label><div class=\"range-line\"><input id=\"lineWidth\" type=\"range\" min=\"0.4\" max=\"4\" step=\"0.1\" value=\"1.2\"><span id=\"widthVal\">1.2</span></div></div><div class=\"field\"><label>Waterfall spacing</label><div class=\"range-line\"><input id=\"offset\" type=\"range\" min=\"0\" max=\"1\" step=\"0.01\" value=\"0.12\"><span id=\"offsetVal\">0.12</span></div></div><div class=\"actions\"><button id=\"resetView\" class=\"btn\">Fit all</button><button id=\"downloadPlot\" class=\"btn primary\">Save plot PNG</button></div></aside><div class=\"plot-column\"><div class=\"card plot-card\"><div class=\"plot-head\"><div><div id=\"plotTitle\" class=\"plot-title\"></div><div id=\"plotSub\" class=\"plot-sub\"></div></div></div><div class=\"canvas-wrap\"><canvas id=\"compareCanvas\"></canvas><div id=\"plotTip\" class=\"plot-tip\"></div></div><div id=\"plotStatus\" class=\"plot-status\"></div></div><div class=\"card curve-panel\"><div class=\"curve-actions\"><b id=\"curveCount\"></b><button id=\"selectAll\" class=\"btn small\">Select all</button><button id=\"selectNone\" class=\"btn small\">Select none</button><button id=\"invertSelect\" class=\"btn small\">Invert</button></div><div id=\"curveList\" class=\"curve-list\"></div></div></div></div></section>
<section id=\"page-gallery\" class=\"page\"><div class=\"gallery-toolbar\"><input id=\"gallerySearch\" type=\"search\" placeholder=\"Search photos or figures…\"><select id=\"galleryKind\"></select><input id=\"galleryMin\" type=\"number\" title=\"Minimum point\"><input id=\"galleryMax\" type=\"number\" title=\"Maximum point\"><b id=\"galleryCount\"></b></div><div id=\"gallery\" class=\"gallery\"></div></section>
<section id=\"page-datasets\" class=\"page\"><div class=\"dataset-toolbar\"><input id=\"dataSearch\" type=\"search\" placeholder=\"Search technique, point, method, path…\"><select id=\"dataTech\"></select></div><div class=\"dataset-layout\"><aside id=\"datasetList\" class=\"dataset-list\"></aside><main id=\"datasetDetail\" class=\"card detail\"><div class=\"empty\">Select a dataset.</div></main></div></section>
<section id=\"page-files\" class=\"page\"><div class=\"files-toolbar\"><input id=\"fileSearch\" type=\"search\" placeholder=\"Search all files…\"><select id=\"fileCategory\"></select></div><div id=\"fileGroups\" class=\"file-groups\"></div></section>
<section id=\"page-about\" class=\"page\"><div class=\"card detail\"><h2>Portable FaradaIQ experiment browser</h2><div id=\"shortNote\"></div><p>The comparison page is the default view and plots every selected point using previews sampled across each complete processed table. Photos, GIFs, and generated figures are handled as media and are never parsed as CSV tables.</p><div class=\"meta\"><div>Version</div><div>{VERSION}</div><div>Catalog source</div><div class=\"path\">{html.escape(str(manifest.get('catalog_source','')))}</div><div>Analysis root</div><div class=\"path\">{html.escape(str(manifest.get('analysis_root','')))}</div><div>Preview policy</div><div>Authoritative processed tables; full-file adaptive sampling; up to {MAX_PREVIEW_ROWS} embedded rows per table</div><div>Internet required</div><div>No</div><div>FaradaIQ required</div><div>No</div></div><div class=\"note\"><b>Portable folder rule:</b> Copy or move the complete experiment folder. Do not move this HTML file by itself. All file, image, report, and ZIP links are relative.</div></div></section>
<div id=\"lightbox\" class=\"lightbox\"><button class=\"btn close\">×</button><img id=\"lightboxImage\"><div id=\"lightboxTitle\" style=\"position:absolute;bottom:15px;left:30px;right:30px;text-align:center\"></div></div><script>window.FARADAIQ_MANIFEST={payload};</script><script>{_JS}</script></body></html>"""


def write_portable_browser(experiment_root: str | Path, *, analysis_root: str | Path | None = None, experiment_name: str | None = None, short_names_enabled: bool | None = None, create_batch_zips: bool = True) -> dict[str, Any]:
    root = Path(experiment_root).expanduser().resolve()
    manifest = build_manifest(root, analysis_root=analysis_root, experiment_name=experiment_name, short_names_enabled=short_names_enabled, create_batch_zips=create_batch_zips)
    manifest_path, html_path, readme_path = root / MANIFEST_NAME, root / HTML_NAME, root / README_NAME
    manifest_path.write_text(json.dumps(_json_safe(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_render_html(manifest), encoding="utf-8")
    readme_path.write_text(
        "FaradaIQ portable data browser\n================================\n\n"
        f"Open {HTML_NAME} in a modern web browser. The first page compares all available point curves.\n\n"
        "Copy or share the complete experiment folder. The HTML uses only relative paths and does not require FaradaIQ, Python, a web server, or internet access.\n"
        "Plot previews are sampled across the complete authoritative processed tables. Photos, GIFs, and figures appear in the media gallery.\n"
        f"Individual batch downloads are stored under {DOWNLOAD_DIR_NAME}. Scientific names are preserved in {MANIFEST_NAME} and inside every batch ZIP manifest even when physical short names are enabled.\n",
        encoding="utf-8",
    )
    return {
        "html_path": str(html_path), "manifest_path": str(manifest_path), "readme_path": str(readme_path),
        "download_dir": str(root / DOWNLOAD_DIR_NAME), "dataset_count": manifest.get("dataset_count", 0),
        "comparison_curve_count": manifest.get("comparison", {}).get("comparison_curve_count", 0),
        "media_count": manifest.get("media_count", 0), "batch_count": manifest.get("batch_count", 0),
        "file_count": manifest.get("file_count", 0), "portable": True, "relative_paths_only": True,
    }


def _find_widget_by_text(root: Any, needle: str) -> Any | None:
    target = needle.strip().lower(); stack = [root]
    while stack:
        widget = stack.pop()
        try:
            if target in str(widget.cget("text")).strip().lower(): return widget
        except Exception: pass
        try: stack.extend(widget.winfo_children())
        except Exception: pass
    return None


def _patch_batch_jobs(core: MutableMapping[str, Any]) -> None:
    import importlib
    modules = []
    for name in ("faradaiq.v233_batch_jobs", "faradaiq.v232_batch_jobs"):
        try:
            mod = importlib.import_module(name)
            if mod not in modules: modules.append(mod)
        except Exception: pass
    for jobs in modules:
        old_defaults = jobs.default_export_settings
        if not getattr(old_defaults, "_faradaiq_v273_portable", False):
            def defaults(_old=old_defaults) -> dict[str, Any]:
                values = dict(_old()); values.setdefault("portable_html_browser", True); return values
            defaults._faradaiq_v273_portable = True; jobs.default_export_settings = defaults
        BatchJob, old_init = jobs.BatchJob, jobs.BatchJob.__init__
        if not getattr(old_init, "_faradaiq_v273_portable", False):
            def init(self: Any, *args: Any, _old=old_init, _jobs=jobs, **kwargs: Any) -> None:
                _old(self, *args, **kwargs); merged = _jobs.default_export_settings(); merged.update(getattr(self, "export_settings", {}) or {}); self.export_settings = merged
            init._faradaiq_v273_portable = True; BatchJob.__init__ = init
        Window, old_build = jobs.BatchQueueWindow, jobs.BatchQueueWindow._build
        if not getattr(old_build, "_faradaiq_v273_portable", False):
            def build(self: Any, _old=old_build) -> Any:
                result = _old(self); tk, ttk = self.tk, self.ttk; var = tk.BooleanVar(master=self.win, value=True); self.portable_html_browser_var = var; self.export_vars["portable_html_browser"] = var
                box = _find_widget_by_text(self.win, "Export and parser settings")
                if box is not None:
                    try: row = max(7, int(box.grid_size()[1]))
                    except Exception: row = 7
                    ttk.Checkbutton(box, text="Create portable HTML data browser", variable=var).grid(row=row, column=0, columnspan=2, sticky="w", pady=(5, 1))
                    ttk.Label(box, text="Default ON. Comparison-first offline browser with all-point plots, photo/figure gallery, and batch ZIPs.", style="Muted.TLabel").grid(row=row+1, column=0, columnspan=2, sticky="w", padx=(22, 8), pady=(0, 3))
                return result
            build._faradaiq_v273_portable = True; Window._build = build
        old_run = jobs.run_batch_job
        if not getattr(old_run, "_faradaiq_v273_portable", False):
            def run(job: Any, *args: Any, _old=old_run, **kwargs: Any) -> Any:
                result = _old(job, *args, **kwargs); enabled = bool((job.export_settings or {}).get("portable_html_browser", True))
                if enabled:
                    progress_cb = args[1] if len(args) > 1 and callable(args[1]) else kwargs.get("progress")
                    job.status, job.stage, job.progress, job.message, job.finished_at = "Running", "portable_browser", 100.0, "Building comparison plots, media gallery, and portable batch downloads.", ""
                    try:
                        if callable(progress_cb): progress_cb("Running", "portable_browser", 100.0, job.message)
                    except Exception: pass
                    out = Path(str(job.outputs.get("output_dir") or job.output_dir)).resolve(); analysis = job.outputs.get("analysis_ready_root")
                    try:
                        receipt = write_portable_browser(out, analysis_root=analysis, experiment_name=job.experiment_name, short_names_enabled=bool((job.export_settings or {}).get("short_names", True)), create_batch_zips=True)
                        job.outputs["portable_html_browser"] = receipt; job.message = f"Job completed. Portable browser: {Path(receipt['html_path']).name}"
                    except Exception as exc:
                        job.outputs["portable_html_browser_error"] = str(exc); job.message = f"Job completed, but portable HTML browser generation failed: {exc}"
                    job.status, job.stage, job.progress, job.finished_at = "Completed", "completed", 100.0, datetime.now().isoformat(timespec="seconds")
                    try:
                        if callable(progress_cb): progress_cb("Completed", "completed", 100.0, job.message)
                    except Exception: pass
                    try: job.touch()
                    except Exception: pass
                return result
            run._faradaiq_v273_portable = True; jobs.run_batch_job = run
    try:
        from faradaiq import v233_batch_jobs as active
        for key in ("_v233_default_export_settings", "_v232_default_export_settings", "_v230_default_export_settings", "_v229_default_export_settings"): core[key] = active.default_export_settings
        for key in ("_v233_run_batch_job", "_v232_run_batch_job", "_v230_run_batch_job", "_v229_run_batch_job"): core[key] = active.run_batch_job
    except Exception: pass


def _patch_main_gui(core: MutableMapping[str, Any]) -> None:
    Session, tk, ttk = core.get("SessionReaderGUI"), core.get("tk"), core.get("ttk")
    if Session is None or tk is None or ttk is None: return
    old_build = Session._build_ui
    if not getattr(old_build, "_faradaiq_v273_portable", False):
        def build(self: Any, *args: Any, **kwargs: Any) -> Any:
            if not hasattr(self, "portable_html_browser_var"): self.portable_html_browser_var = tk.BooleanVar(master=self, value=True)
            result = old_build(self, *args, **kwargs); label = _find_widget_by_text(self, "Use shortened names for generated folders and files"); short_frame = getattr(label, "master", None) if label is not None else None; top = getattr(short_frame, "master", None) if short_frame is not None else None
            if top is not None:
                try: row = max(4, int(top.grid_size()[1]))
                except Exception: row = 4
                portable = ttk.Frame(top); portable.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(1, 2)); ttk.Checkbutton(portable, text="Create portable HTML data browser", variable=self.portable_html_browser_var).pack(side="left"); ttk.Label(portable, text="Default ON. All-point comparisons, photo/figure gallery, dataset previews, and compact ZIPs.", style="Muted.TLabel").pack(side="left", padx=(10, 0))
            return result
        build._faradaiq_v273_portable = True; Session._build_ui = build
    old_export = Session.export_outputs
    if not getattr(old_export, "_faradaiq_v273_portable", False):
        def export(self: Any, *args: Any, **kwargs: Any) -> Any:
            result = old_export(self, *args, **kwargs)
            try:
                if not bool(self.portable_html_browser_var.get()): return result
                source = Path(str(getattr(self.collection, "source_path", "") or "experiment")); base = source.stem if source.is_file() else source.name or "experiment"; out = Path(str(self.output_dir_var.get() or ".")).resolve(); analysis = out / f"{base}_analysis_ready_csv"
                if not analysis.is_dir(): return result
                short_enabled = bool(getattr(getattr(self, "short_names_var", None), "get", lambda: True)())
                def worker() -> None:
                    try:
                        receipt = write_portable_browser(out, analysis_root=analysis, experiment_name=base, short_names_enabled=short_enabled, create_batch_zips=True)
                        try: self.after(0, lambda: self.set_status(f"Portable data browser created: {Path(receipt['html_path']).name}"))
                        except Exception: pass
                    except Exception as exc:
                        try: self.after(0, lambda text=f"Portable browser generation failed: {exc}": self.set_status(text))
                        except Exception: pass
                threading.Thread(target=worker, name="FaradaIQPortableBrowser", daemon=True).start()
            except Exception: pass
            return result
        export._faradaiq_v273_portable = True; Session.export_outputs = export


def install(core: MutableMapping[str, Any]) -> None:
    core["FARADAIQ_PUBLIC_VERSION"] = VERSION; core["FARADAIQ_APP_VERSION"] = VERSION; core["APP_TITLE"] = APP_TITLE
    core["_v273_write_portable_browser"] = write_portable_browser; core["_v273_build_portable_browser_manifest"] = build_manifest
    collector = core.get("_v177_collect_full_session")
    if callable(collector) and not getattr(collector, "_faradaiq_v273_portable", False):
        def collect_full_session(gui: Any) -> Any:
            payload = collector(gui)
            if isinstance(payload, dict): payload["portable_html_browser"] = {"enabled": bool(getattr(getattr(gui, "portable_html_browser_var", None), "get", lambda: True)()), "default_enabled": True, "html_name": HTML_NAME, "relative_paths_only": True, "comparison_first": True}
            return payload
        collect_full_session._faradaiq_v273_portable = True; core["_v177_collect_full_session"] = collect_full_session
    applier = core.get("_v177_apply_full_session")
    if callable(applier) and not getattr(applier, "_faradaiq_v273_portable", False):
        def apply_full_session(gui: Any, payload: Any, *args: Any, **kwargs: Any) -> Any:
            result = applier(gui, payload, *args, **kwargs)
            try:
                settings = payload.get("portable_html_browser", {}) if isinstance(payload, dict) else {}
                if hasattr(gui, "portable_html_browser_var") and "enabled" in settings: gui.portable_html_browser_var.set(bool(settings.get("enabled")))
            except Exception: pass
            return result
        apply_full_session._faradaiq_v273_portable = True; core["_v177_apply_full_session"] = apply_full_session
    _patch_batch_jobs(core); _patch_main_gui(core)
