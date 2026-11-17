#!/usr/bin/env python
"""FaradaIQ canonical HDF5/JSON bundle builder v216.

Builds one root-level H5 and one root-level JSON manifest for FaradaIQ
browser, AI chat, Edison, and SciLink handoff. For HEKA/POTMASTER exports, it
catalogs only the scientific data the user requested: parsed CV cycles, EIS
impedance tables that can generate Nyquist/Bode plots, IV/LSV/Tafel tables,
analyzed DRT tables, photos, and the initial GIF. It intentionally excludes
pySECCM figure CSV/PNG mirrors, DRT image plots, old AI mirrors, and browser
cache files.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None

SCHEMA = "faradaiq-canonical-h5-bundle-v5"
VERSION = "v331"
TABLE_EXTS = {".csv", ".txt", ".tsv", ".asc", ".mpt"}
MEDIA_EXTS = {".gif", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".mpeg", ".mpg", ".m4v", ".webm"}
JSON_EXTS = {".json", ".jsonl"}
DRT_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".pdf"}
EXCLUDED_DIR_TOKENS = {
    "__pycache__", ".git", ".faradaiq_venv", ".deareis_venv", ".faradaiq_litellm_venv",
    "files_snapshot", "raw_export_siblings", "data_generated_from_heka", "drt_input_csv_all",
    "faradaiq_ai_data_context_pack", "scilink_handoff", "scilink_results", "edisonscientific_handoff",
    "edison_results", "report_bundles", "exports", "browser_sessions", "faradaiq_ai_chat_transcripts",
    "deareis_eis_handoff", "pysimpleechem_cv_results", "tafel_analysis", "originlab_export",
    "excel", "hdf5", "peak_tables", "overlay_data", "comparison_tables", "logs", "metadata",
    ".browser_cache_from_h5", "scilink_ready",
}


import threading
_BUNDLE_CONTEXT=threading.local()
def _check_bundle_cancel():
    event=getattr(_BUNDLE_CONTEXT,'cancel_event',None)
    if event is not None and event.is_set():raise InterruptedError('Bundle creation canceled.')
def _preserve_bundle_units():return getattr(_BUNDLE_CONTEXT,'preserve_input_units',False)
def _numeric_bundle_column(col):
    _check_bundle_cancel()
    out=pd.to_numeric(col,errors='coerce')
    _check_bundle_cancel()
    return out


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_name(value: Any) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "FaradaIQ")).strip("_")
    return s[:140] or "FaradaIQ"


def relpath(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return path.name


def safe_json(x: Any, depth: int = 0) -> Any:
    if depth > 8:
        return str(type(x).__name__)
    if x is None or isinstance(x, (str, int, bool)):
        return x
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        v = float(x)
        return v if math.isfinite(v) else None
    if isinstance(x, Path):
        return str(x)
    if isinstance(x, np.ndarray):
        return {"ndarray_shape": list(x.shape), "dtype": str(x.dtype)}
    if isinstance(x, dict):
        return {str(k): safe_json(v, depth + 1) for k, v in list(x.items())[:20000]}
    if isinstance(x, (list, tuple, set)):
        return [safe_json(v, depth + 1) for v in list(x)[:20000]]
    return str(x)


def _parts_lower(p: Path) -> List[str]:
    _check_bundle_cancel()
    try:
        return [x.lower() for x in p.parts]
    except Exception:
        return str(p).lower().replace("\\", "/").split("/")


def _json_load(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception:
        return None


def _read_text_preview(path: Path, limit: int = 2_000_000) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return path.read_text(encoding=enc, errors="replace")[:limit]
        except Exception:
            continue
    return ""


def resolve_bundle_locations(analysis_root: str | Path, base_name: Optional[str] = None) -> Tuple[Path, Path, str, str]:
    root = Path(analysis_root).resolve()
    if root.is_file():
        root = root.parent
    data_root = root
    container_root = root
    if root.name.endswith("_analysis_ready_csv"):
        data_root = root
        container_root = root.parent
    elif root.is_dir():
        kids = sorted([p for p in root.glob("*_analysis_ready_csv") if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
        if kids:
            data_root = kids[0]
            container_root = root
    if base_name is None:
        nm = data_root.name
        base_name = nm[:-len("_analysis_ready_csv")] if nm.endswith("_analysis_ready_csv") else data_root.name
    safe = safe_name(base_name)
    return data_root, container_root, str(base_name), safe


def is_heka_export_root(root: Path) -> bool:
    root = Path(root)
    low = root.as_posix().lower()
    return bool(
        list(root.glob("*_for_plotting_analysis"))
        or list(root.rglob("*heka_sequence_manifest.json"))
        or list(root.rglob("data_by_point_sequence/point_*/point_manifest.json"))
        or "heka" in low or "potmaster" in low or "patchmaster" in low
    )


def _plotting_root(root: Path) -> Optional[Path]:
    hits = sorted(root.glob("*_for_plotting_analysis"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return hits[0] if hits else None


def _sequence_manifest(root: Path) -> Optional[Path]:
    hits = sorted(root.rglob("*heka_sequence_manifest.json"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return hits[0] if hits else None


def _sequence_info(root: Path) -> Dict[str, Any]:
    p = _sequence_manifest(root)
    obj = _json_load(p) if p else None
    if not isinstance(obj, dict):
        return {"sequence_manifest_path": str(p) if p else "", "point_count": None, "scan_rates_mV_s": [], "cycles_per_cv": None, "methods_by_rate": {}}
    rates = []
    methods_by_rate: Dict[str, Dict[str, Any]] = {}
    cycles = []
    try:
        first_point = (obj.get("points") or [])[0]
        for m in first_point.get("methods", []):
            if str(m.get("method_type", "")).upper() == "CV":
                rate = m.get("scan_rate_mV_s")
                if rate is not None:
                    try: rate = float(rate)
                    except Exception: pass
                    rates.append(rate)
                    methods_by_rate[str(int(rate) if float(rate).is_integer() else rate)] = safe_json(m)
                if m.get("n_cycles"):
                    cycles.append(int(m.get("n_cycles")))
    except Exception:
        pass
    return {
        "sequence_manifest_path": str(p) if p else "",
        "point_count": obj.get("point_count"),
        "scan_geometry": obj.get("scan_geometry"),
        "method_counts_total": obj.get("method_counts_total"),
        "sequence_template_counts": obj.get("sequence_template_counts"),
        "scan_rates_mV_s": rates,
        "cycles_per_cv": max(cycles) if cycles else None,
        "methods_by_rate": methods_by_rate,
        "raw_manifest": safe_json(obj),
    }


def _read_table_general(path: Path):
    _check_bundle_cancel()
    if pd is None:
        return None, [], "pandas unavailable"
    try:
        # DRT simple_DRT.csv begins with metadata lines before tau,gamma.
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        lower_lines = [x.strip().lower().replace(" ", "") for x in lines[:80]]
        skip = None
        for i, line in enumerate(lower_lines):
            if line.startswith("tau,gamma") or line.startswith("tau\tgamma") or line == "tau gamma":
                skip = i
                break
        if skip is not None:
            df = pd.read_csv(path, skiprows=skip, sep=None, engine="python")
        else:
            df = pd.read_csv(path, sep=None, engine="python")
            if df.shape[1] == 1:
                try:
                    df2 = pd.read_csv(path, sep=r"\s+", comment="#", engine="python")
                    if df2.shape[1] > df.shape[1]:
                        df = df2
                except Exception:
                    pass
        df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
        if df.empty or df.shape[1] < 2:
            return None, [str(c) for c in df.columns], "table has fewer than two populated columns"
        return df, [str(c) for c in df.columns], ""
    except Exception as exc:
        return None, [], str(exc)


def _num(s: Any) -> Optional[float]:
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def _clean_rate_label(rate: Any) -> str:
    v = _num(rate)
    if v is None:
        return "unknown"
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v))}mVs"
    return f"{v:g}mVs"


def _extract_rate_cycles_method(path: Path) -> Tuple[Optional[float], Optional[int], Optional[int]]:
    s = path.as_posix()
    m = re.search(r"CV(\d+(?:\.\d+)?)mVperS[_-](\d+)cycles[_-](\d+)", s, flags=re.I)
    if m:
        return float(m.group(1)), int(m.group(2)), int(m.group(3))
    m = re.search(r"(\d+(?:\.\d+)?)\s*mV\s*per\s*S", s, flags=re.I)
    rate = float(m.group(1)) if m else None
    m2 = re.search(r"(\d+)cycles", s, flags=re.I)
    cycles = int(m2.group(1)) if m2 else None
    return rate, cycles, None


def _extract_group_index(path: Path) -> Optional[int]:
    m = re.search(r"_(\d{2,4})\.(?:asc|txt|csv|tsv)$", path.name, flags=re.I)
    if m:
        try: return int(m.group(1))
        except Exception: return None
    return None


def _standardize_cv(df, path: Path):
    if _preserve_bundle_units():return df.apply(_numeric_bundle_column).dropna(axis=0,how='all')
    cols = {str(c).lower(): c for c in df.columns}
    potential_col = None; current_col = None; time_col = None
    for c in df.columns:
        l = str(c).lower()
        if potential_col is None and ("emon" in l or "potential" in l or "ewe" in l or l in {"e_v", "voltage_v"}):
            potential_col = c
        if current_col is None and ("imon" in l or "current" in l or l in {"i_a", "current_a"}):
            current_col = c
        if time_col is None and "time" in l:
            time_col = c
    out = pd.DataFrame()
    if time_col is not None:
        out["time_s"] = pd.to_numeric(df[time_col], errors="coerce")
    if potential_col is not None:
        out["potential_V"] = pd.to_numeric(df[potential_col], errors="coerce")
    if current_col is not None:
        out["current_A"] = pd.to_numeric(df[current_col], errors="coerce")
    if out.shape[1] < 2:
        out = df.apply(lambda col: pd.to_numeric(col, errors="coerce"))
    return out.dropna(axis=0, how="all")


def _standardize_eis(df, path: Path):
    if _preserve_bundle_units():return df.apply(_numeric_bundle_column).dropna(axis=0,how='all')
    def find(*tokens):
        for c in df.columns:
            l = str(c).lower()
            if all(t in l for t in tokens):
                return c
        return None
    f = find("frequency") or find("freq") or find("f2") or find("hz")
    zr = find("zreal") or find("realz") or find("real", "ohm") or find("zreal_ohm")
    zi = find("zimag") or find("imagz") or find("imag", "ohm") or find("zimag_ohm")
    zmod = find("impz") or find("zmod") or find("|z|")
    phase = find("phase")
    out = pd.DataFrame()
    if f is not None: out["frequency_Hz"] = pd.to_numeric(df[f], errors="coerce")
    if zr is not None: out["Zreal_ohm"] = pd.to_numeric(df[zr], errors="coerce")
    if zi is not None: out["Zimag_ohm"] = pd.to_numeric(df[zi], errors="coerce")
    if zmod is not None: out["Zmod_ohm"] = pd.to_numeric(df[zmod], errors="coerce")
    if phase is not None: out["phase_deg"] = pd.to_numeric(df[phase], errors="coerce")
    if "Zreal_ohm" in out.columns and "Zimag_ohm" in out.columns:
        out["neg_Zimag_ohm"] = -out["Zimag_ohm"]
        if "Zmod_ohm" not in out.columns:
            out["Zmod_ohm"] = np.sqrt(out["Zreal_ohm"]**2 + out["Zimag_ohm"]**2)
        if "phase_deg" not in out.columns:
            out["phase_deg"] = np.degrees(np.arctan2(out["Zimag_ohm"], out["Zreal_ohm"]))
    if out.shape[1] < 3:
        out = df.apply(lambda col: pd.to_numeric(col, errors="coerce"))
    return out.dropna(axis=0, how="all")


def _standardize_drt(df, path: Path):
    if _preserve_bundle_units():return df.apply(_numeric_bundle_column).dropna(axis=0,how='all')
    # Preserve DRT-specific columns when possible.
    lowcols = [str(c).lower() for c in df.columns]
    out = pd.DataFrame()
    for target, candidates in {
        "tau_s": ["tau", "tau_s"],
        "gamma_ohm": ["gamma", "gamma_ohm"],
        "frequency_Hz": ["frequency", "frequency_hz", "freq", "freq_hz"],
        "Zreal_ohm": ["zreal", "zreal_ohm", "realz"],
        "Zimag_ohm": ["zimag", "zimag_ohm", "imagz"],
    }.items():
        for c in df.columns:
            l = str(c).lower()
            if any(x == l or x in l for x in candidates):
                out[target] = pd.to_numeric(df[c], errors="coerce")
                break
    if out.empty:
        out = df.apply(lambda col: pd.to_numeric(col, errors="coerce"))
    return out.dropna(axis=0, how="all")


def _make_table_record(path: Path, root: Path, technique: str, role: str, df, **meta) -> Dict[str, Any]:
    _check_bundle_cancel()
    rec = {
        "source_path": str(path),
        "relative_path": relpath(path, root),
        "name": path.name,
        "suffix": path.suffix.lower(),
        "size_bytes": int(path.stat().st_size),
        "modified_time": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        "record_type": "table",
        "technique": technique,
        "role": role,
        "columns": [str(c) for c in df.columns],
        "n_rows": int(df.shape[0]),
        "n_columns": int(df.shape[1]),
        "included_in_h5": True,
        "_dataframe": df,
    }
    rec.update({k: v for k, v in meta.items() if v is not None})
    return rec


def _collect_heka_records(root: Path, log=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    plot_root = _plotting_root(root)
    data_dir = plot_root / "data" if plot_root else None
    drt_dir = plot_root / "DRT_input_csv" if plot_root else None
    seq = _sequence_info(root)
    point_count = int(seq.get("point_count") or 0) or 0
    cycles_default = int(seq.get("cycles_per_cv") or 3)
    records: List[Dict[str, Any]] = []
    diagnostics = {"cv_candidates": 0, "eis_candidates": 0, "drt_candidates": 0, "excluded_figures": 0}

    # Metadata: keep sequence and point manifests.
    for q in [] if plot_root is None else list(plot_root.glob("*heka_sequence_manifest.json")) + list(plot_root.glob("plotting_analysis_manifest.json")) + list(plot_root.rglob("data_by_point_sequence/point_*/point_manifest.json")):
        records.append({
            "source_path": str(q), "relative_path": relpath(q, root), "name": q.name, "suffix": q.suffix.lower(),
            "size_bytes": int(q.stat().st_size), "modified_time": datetime.fromtimestamp(q.stat().st_mtime).isoformat(timespec="seconds"),
            "record_type": "metadata", "technique": "Metadata", "role": "experiment_sequence_metadata", "included_in_h5": True,
        })

    # CV: only split group files, one file per point/rate/cycle. Exclude full combined ASC and pySECCM figure mirrors.
    if data_dir and data_dir.exists():
        for p in sorted(data_dir.glob("*CV*mVperS*cycles*_Groups/*.*"), key=lambda x: x.as_posix().lower()):
            if p.suffix.lower() not in {".asc", ".csv", ".txt", ".tsv"}: continue
            diagnostics["cv_candidates"] += 1
            rate, cycles, method_entry = _extract_rate_cycles_method(p)
            cycles = cycles or cycles_default or 1
            idx = _extract_group_index(p)
            if idx is None:
                continue
            point = idx // cycles + 1
            cycle = idx % cycles + 1
            df, cols, err = _read_table_general(p)
            if df is None:
                continue
            sdf = _standardize_cv(df, p)
            records.append(_make_table_record(
                p, root, "CV / LSV", "parsed CV cycle", sdf,
                point=int(point), cycle=int(cycle), scan_rate_mV_s=float(rate) if rate is not None else None,
                scan_rate_label=_clean_rate_label(rate), group_index=int(idx), method_entry=method_entry,
                canonical_key=f"CV:point{int(point):03d}:rate{_clean_rate_label(rate)}:cycle{int(cycle):02d}",
            ))

    # EIS: prefer clean DRT_input_csv position tables. These generate Nyquist and Bode views.
    if drt_dir and drt_dir.exists():
        for p in sorted(drt_dir.glob("position_*_freq_zreal_zimag.csv"), key=lambda x: x.as_posix().lower()):
            diagnostics["eis_candidates"] += 1
            m = re.search(r"position[_-]*(\d+)", p.name, flags=re.I)
            point = int(m.group(1)) if m else None
            df, cols, err = _read_table_general(p)
            if df is None: continue
            sdf = _standardize_eis(df, p)
            records.append(_make_table_record(
                p, root, "EIS", "EIS impedance table for Nyquist/Bode", sdf,
                point=point, eis_view_support=["Nyquist", "Bode magnitude", "Bode phase"],
                canonical_key=f"EIS:point{int(point or 0):03d}",
            ))
    # Fallback if DRT_input_csv tables are absent.
    if not any(r.get("technique") == "EIS" for r in records) and data_dir and data_dir.exists():
        for p in sorted(data_dir.glob("*fullEIS*_Groups/*.*"), key=lambda x: x.as_posix().lower()):
            if p.suffix.lower() not in {".txt", ".csv", ".asc", ".tsv"}: continue
            idx = _extract_group_index(p)
            point = idx + 1 if idx is not None else None
            df, cols, err = _read_table_general(p)
            if df is None: continue
            sdf = _standardize_eis(df, p)
            records.append(_make_table_record(p, root, "EIS", "EIS impedance table for Nyquist/Bode", sdf, point=point, canonical_key=f"EIS:point{int(point or 0):03d}"))

    # IV/LSV/Tafel: include if pySECCM generated them; do not include scripts/readmes.
    if data_dir and data_dir.exists():
        for p in sorted(data_dir.rglob("*"), key=lambda x: x.as_posix().lower()):
            if not p.is_file() or p.suffix.lower() not in TABLE_EXTS: continue
            low = p.as_posix().lower()
            if "/figures/" in low or "_groups/" in low: continue
            if any(tok in low for tok in ["iv", "tafel", "polarization", "polarisation", "lsv"]):
                if "cv" in low or "fulleis" in low or "takedata" in low: continue
                df, cols, err = _read_table_general(p)
                if df is None: continue
                records.append(_make_table_record(p, root, "LSV/IV/Tafel", "IV/LSV/Tafel table", df, canonical_key=f"IV:{p.stem}"))

    # DRT analyzed results: include CSV/TXT/JSON under DRT result folders; exclude image plots.
    if drt_dir and drt_dir.exists():
        for p in sorted(drt_dir.rglob("*"), key=lambda x: x.as_posix().lower()):
            if not p.is_file(): continue
            low = p.as_posix().lower()
            if "/drt/" not in low.replace("\\", "/"): continue
            if p.suffix.lower() in DRT_IMAGE_EXTS: continue
            if p.suffix.lower() in JSON_EXTS:
                records.append({
                    "source_path": str(p), "relative_path": relpath(p, root), "name": p.name, "suffix": p.suffix.lower(),
                    "size_bytes": int(p.stat().st_size), "modified_time": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
                    "record_type": "metadata", "technique": "Metadata", "role": "DRT metadata", "included_in_h5": True,
                })
                continue
            if p.suffix.lower() not in TABLE_EXTS: continue
            # These are useful provenance files but should not appear as plottable DRT data.
            if p.name.lower() in {"drt_input_files_manifest.csv", "drt_run_summary.csv"}:
                records.append({
                    "source_path": str(p), "relative_path": relpath(p, root), "name": p.name, "suffix": p.suffix.lower(),
                    "size_bytes": int(p.stat().st_size), "modified_time": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
                    "record_type": "metadata", "technique": "Metadata", "role": "DRT provenance/summary metadata", "included_in_h5": True,
                })
                continue
            # v138 policy: only the actual DRT distribution table is plotted/cataloged.
            # For headless pyDRTtools this is typically *_simple_drt_data.csv.  Do not
            # load companion *_simple_eis_data.csv, residual/score tables, summaries, or
            # image plots as separate DRT curves.
            name_l = p.name.lower()
            if "drt_data" not in name_l:
                continue
            diagnostics["drt_candidates"] += 1
            df, cols, err = _read_table_general(p)
            if df is None: continue
            sdf = _standardize_drt(df, p)
            m = re.search(r"position[_-]*0*(\d+)", p.name, flags=re.I)
            point = int(m.group(1)) if m else None
            run = None
            for part in p.parts:
                if part.lower().startswith("drt_"):
                    run = part
            drt_kind = "drt_data"
            records.append(_make_table_record(p, root, "DRT", "analyzed DRT data table", sdf, point=point, drt_run_id=run, drt_kind=drt_kind, canonical_key=f"DRT:{run}:point{int(point or 0):03d}:{drt_kind}"))

    # Photos and initial GIF only. Do not include DRT or pySECCM image plots.
    if plot_root:
        for p in sorted((plot_root / "photos").glob("*")) if (plot_root / "photos").exists() else []:
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                records.append({
                    "source_path": str(p), "relative_path": relpath(p, root), "name": p.name, "suffix": p.suffix.lower(),
                    "size_bytes": int(p.stat().st_size), "modified_time": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
                    "record_type": "media", "technique": "Media / photos", "role": "photo used for initial GIF", "included_in_h5": True,
                })
    for p in sorted(root.glob("*_heka_photo_sequence.gif")):
        records.append({
            "source_path": str(p), "relative_path": relpath(p, root), "name": p.name, "suffix": p.suffix.lower(),
            "size_bytes": int(p.stat().st_size), "modified_time": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            "record_type": "media", "technique": "Media / photos", "role": "initial GIF", "included_in_h5": True,
        })

    # De-duplicate by canonical key while keeping first. This should not normally remove CV now.
    final = []
    seen = set(); dup = []
    for r in records:
        key = r.get("canonical_key") or (r.get("record_type"), r.get("relative_path"))
        if key in seen and r.get("record_type") == "table":
            dup.append(r.get("relative_path")); continue
        seen.add(key); final.append(r)

    info = _build_info(final, records, seq, is_heka=True, duplicates=dup, diagnostics=diagnostics)
    return final, info


def infer_technique_nonheka(path: Path, columns: Sequence[str]) -> Tuple[str, str]:
    text = (path.name + " " + path.as_posix() + " " + " ".join(map(str, columns))).lower()
    if "drt" in text and "drt_input" not in text: return "DRT", "analyzed DRT table"
    if any(t in text for t in ["zreal", "zimag", "realz", "imagz", "impedance", "nyquist", "bode", "frequency_hz"]): return "EIS", "EIS impedance table for Nyquist/Bode"
    if any(t in text for t in ["tafel", "polarization", "polarisation", "current-voltage", "iv_"]): return "LSV/IV/Tafel", "IV/LSV/Tafel table"
    if "lsv" in text: return "LSV/IV/Tafel", "LSV table"
    if any(t in text for t in ["charge", "discharge", "capacity", "galvanostatic"]): return "Charge/Discharge", "Charge/discharge table"
    if any(t in text for t in ["ocp", "open circuit", "open_circuit"]): return "OCP", "OCP table"
    if any(t in text for t in ["potential", "voltage", "current", "cycle", "scan_rate", "scanrate", "ewe"]): return "CV / LSV", "CV/LSV current-potential table"
    return "Numeric", "Generic numeric table"


def _include_nonheka(path: Path) -> bool:
    if not path.is_file(): return False
    parts = set(_parts_lower(path))
    if any(x in EXCLUDED_DIR_TOKENS for x in parts): return False
    low = path.as_posix().lower()
    if "faradaiq_bundle" in low or "files_snapshot" in low or ".browser_cache_from_h5" in low: return False
    if path.suffix.lower() in TABLE_EXTS | JSON_EXTS: return True
    if path.suffix.lower() in MEDIA_EXTS:
        return "photo" in low or "gif" in low
    return False


def _collect_nonheka_records(root: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for p in sorted(root.rglob("*"), key=lambda x: x.as_posix().lower()):
        if not _include_nonheka(p): continue
        if p.suffix.lower() in TABLE_EXTS:
            df, cols, err = _read_table_general(p)
            if df is None: continue
            tech, role = infer_technique_nonheka(p, cols)
            sdf = _standardize_eis(df, p) if tech == "EIS" else (_standardize_cv(df, p) if tech in {"CV / LSV", "LSV/IV/Tafel"} else df)
            records.append(_make_table_record(p, root, tech, role, sdf))
        elif p.suffix.lower() in JSON_EXTS:
            records.append({"source_path": str(p), "relative_path": relpath(p, root), "name": p.name, "suffix": p.suffix.lower(), "size_bytes": int(p.stat().st_size), "modified_time": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"), "record_type": "metadata", "technique": "Metadata", "role": "metadata_json", "included_in_h5": True})
        elif p.suffix.lower() in MEDIA_EXTS:
            records.append({"source_path": str(p), "relative_path": relpath(p, root), "name": p.name, "suffix": p.suffix.lower(), "size_bytes": int(p.stat().st_size), "modified_time": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"), "record_type": "media", "technique": "Media / photos", "role": "media", "included_in_h5": True})
    return records, _build_info(records, records, {}, is_heka=False, duplicates=[], diagnostics={})


def collect_records(analysis_root: Path, base_name: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    root = Path(analysis_root).resolve()
    if is_heka_export_root(root):
        return _collect_heka_records(root)
    return _collect_nonheka_records(root)


def _build_info(final: List[Dict[str, Any]], all_records: List[Dict[str, Any]], seq: Dict[str, Any], is_heka: bool, duplicates: List[str], diagnostics: Dict[str, Any]) -> Dict[str, Any]:
    tech_counts: Dict[str, int] = {}
    for r in final:
        tech = str(r.get("technique") or "Unknown")
        if tech == "Metadata": continue
        tech_counts[tech] = tech_counts.get(tech, 0) + 1
    cv = [r for r in final if r.get("technique") == "CV / LSV" and r.get("record_type") == "table"]
    eis = [r for r in final if r.get("technique") == "EIS" and r.get("record_type") == "table"]
    drt = [r for r in final if r.get("technique") == "DRT" and r.get("record_type") == "table"]
    by_rate: Dict[str, Dict[str, Any]] = {}
    by_point: Dict[str, Dict[str, Any]] = {}
    keys = set()
    for r in cv:
        rate = _clean_rate_label(r.get("scan_rate_mV_s"))
        point = int(r.get("point", -1)) if str(r.get("point", "")).isdigit() or isinstance(r.get("point"), int) else r.get("point", "unassigned")
        cyc = int(r.get("cycle", -1)) if str(r.get("cycle", "")).isdigit() or isinstance(r.get("cycle"), int) else r.get("cycle", "unknown")
        by_rate.setdefault(rate, {"files": 0, "cycles": set(), "points": set()})
        by_rate[rate]["files"] += 1; by_rate[rate]["cycles"].add(cyc); by_rate[rate]["points"].add(point)
        by_point.setdefault(str(point), {"files": 0, "cycles": set(), "scan_rates": set()})
        by_point[str(point)]["files"] += 1; by_point[str(point)]["cycles"].add(cyc); by_point[str(point)]["scan_rates"].add(rate)
        keys.add((point, rate, cyc))
    def ser(d):
        out = {}
        for k, v in d.items():
            out[k] = {kk: (sorted(list(vv), key=lambda x: str(x)) if isinstance(vv, set) else vv) for kk, vv in v.items()}
        return out
    warnings = []
    expected_cv = None
    try:
        pc = int(seq.get("point_count") or 0); nr = len(seq.get("scan_rates_mV_s") or []); nc = int(seq.get("cycles_per_cv") or 0)
        expected_cv = pc * nr * nc if pc and nr and nc else None
    except Exception:
        expected_cv = None
    if expected_cv is not None and len(cv) != expected_cv:
        warnings.append(f"Expected {expected_cv} CV cycle files from HEKA sequence metadata but cataloged {len(cv)}.")
    if duplicates:
        warnings.append(f"Skipped {len(duplicates)} duplicate table(s).")
    validation = {
        "is_heka_export": bool(is_heka),
        "expected_points_from_dat": seq.get("point_count"),
        "expected_scan_rates_mV_s_from_dat": seq.get("scan_rates_mV_s"),
        "expected_cycles_per_cv_from_dat": seq.get("cycles_per_cv"),
        "expected_cv_cycle_files_from_dat": expected_cv,
        "cv_files_included": len(cv),
        "cv_unique_point_scanrate_cycle_keys": len(keys),
        "cv_one_file_per_cycle_key": len(cv) == len(keys),
        "eis_tables_included": len(eis),
        "drt_tables_included": len(drt),
        "cv_by_scan_rate": ser(by_rate),
        "cv_by_point": ser(by_point),
        "all_table_files_seen_before_dedupe": len([r for r in all_records if r.get("record_type") == "table"]),
        "final_table_files_in_h5": len([r for r in final if r.get("record_type") == "table"]),
        "warnings": warnings,
        "diagnostics": diagnostics,
    }
    return {"is_heka": is_heka, "technique_counts": tech_counts, "duplicates": duplicates, "validation": validation, "sequence_info": seq}


def _read_supporting_root_jsons(container_root: Path, data_root: Path, safe: str, final_json_path: Optional[Path] = None) -> Tuple[List[Dict[str, Any]], List[Path]]:
    final_resolved = None
    try: final_resolved = final_json_path.resolve() if final_json_path else None
    except Exception: pass
    candidates: List[Path] = []
    patterns = [f"{safe}*_summary.json", f"{safe}*_manifest.json", f"{safe}*_analysis_ready_manifest.json", "*_summary.json", "*_analysis_ready_manifest.json", "*_manifest.json"]
    for base in [container_root, data_root]:
        try:
            for pat in patterns:
                for q in base.glob(pat):
                    if not q.is_file(): continue
                    try:
                        if final_resolved is not None and q.resolve() == final_resolved: continue
                    except Exception: pass
                    if q.name.endswith("_faradaiq_bundle.json"): continue
                    if q not in candidates: candidates.append(q)
        except Exception: pass
    items=[]; delete=[]
    for q in sorted(candidates, key=lambda x: x.as_posix().lower()):
        obj = _json_load(q)
        if obj is None: continue
        items.append({"name": q.name, "path": str(q), "relative_to_container": relpath(q, container_root), "content": safe_json(obj)})
        try:
            if q.parent.resolve() == container_root.resolve(): delete.append(q)
        except Exception: pass
    return items, delete


def _cleanup_legacy_bundle_outputs(container_root: Path, data_root: Path, safe: str, keep_paths: Sequence[Path] = ()) -> None:
    keep = set()
    for kp in keep_paths:
        try: keep.add(kp.resolve())
        except Exception: pass
    for base in {container_root, data_root}:
        for d in list(base.glob("*_FaradaIQ_bundle")) + list(base.glob("*_FaradaIQ_compact_bundle")):
            try:
                if d.is_dir(): shutil.rmtree(d, ignore_errors=True)
            except Exception: pass
    for pat in [f"{safe}_faradaiq_bundle*.h5", f"{safe}_faradaiq_bundle*.json"]:
        for q in container_root.glob(pat):
            try:
                if q.resolve() not in keep and q.is_file(): q.unlink()
            except Exception: pass


def _h5_name(index: int, rec: Dict[str, Any]) -> str:
    tech = safe_name(rec.get("technique", "table"))
    key = safe_name(rec.get("canonical_key") or rec.get("relative_path") or rec.get("name"))
    return f"item_{index:05d}_{tech}_{key}"[:180]


def _build_canonical_bundle_impl(analysis_root: str | Path, base_name: Optional[str] = None, gui_metadata: Optional[Dict[str, Any]] = None, progress_callback=None, label: str = "canonical", *, output_dir=None, preserve_sources=False, bundle_file_stem=None) -> Dict[str, Any]:
    root_in = Path(analysis_root).resolve()
    root, container_root, base_name, safe = resolve_bundle_locations(root_in, base_name=base_name)
    source_container_root=container_root
    if output_dir is not None:container_root=Path(output_dir).resolve()
    container_root.mkdir(parents=True, exist_ok=True)
    file_stem=safe_name(bundle_file_stem) if bundle_file_stem else safe
    h5_path = container_root / f"{file_stem}_faradaiq_bundle.h5"
    json_path = container_root / f"{file_stem}_faradaiq_bundle.json"
    if not preserve_sources:_cleanup_legacy_bundle_outputs(container_root, root, safe, keep_paths=[h5_path, json_path])
    def log(msg):
        if progress_callback:
            try: progress_callback(str(msg))
            except Exception: pass
        try: print(str(msg), flush=True)
        except Exception: pass
    log(f"[FaradaIQ bundle {VERSION}] Cataloging curated experiment data under: {root}")
    _check_bundle_cancel()
    records, info = collect_records(root, base_name=base_name)
    _check_bundle_cancel()
    supporting_jsons, parent_jsons_to_delete = _read_supporting_root_jsons(source_container_root, root, safe, final_json_path=json_path)
    manifest: Dict[str, Any] = {
        "schema": SCHEMA, "app": "FaradaIQ eStudio", "version": VERSION, "created": _now(), "label": label,
        "base_name": base_name, "sample_name": base_name, "analysis_root": str(root), "container_root": str(container_root),
        "bundle_dir": str(container_root), "h5_path": str(h5_path), "json_path": str(json_path),
        "supporting_json_metadata": supporting_jsons, "gui_metadata": safe_json(gui_metadata or {}),
        "source_policy": {
            "one_h5_one_json": True,
            "visible_bundle_location": str(container_root),
            "source_container": str(source_container_root),
            "preserve_source_files": bool(preserve_sources),
            "browser_reads_h5_first": True,
            "heka_policy": "Curated HEKA data only: parsed CV cycles, EIS impedance tables for Nyquist/Bode, IV/LSV/Tafel if present, analyzed DRT tables, photos, and initial GIF. pySECCM figure mirrors and DRT image plots are excluded.",
            "drt_rebuild_policy": "After separate/manual DRT, click Rebuild H5 bundle to catalog new DRT data tables.",
        },
        "techniques_detected": sorted(info.get("technique_counts", {}).keys()),
        "technique_counts": info.get("technique_counts", {}),
        "experiment_structure_from_dat": safe_json(info.get("sequence_info", {})),
        "data_structure": {
            "is_heka_export": info.get("is_heka"),
            "validation": info.get("validation", {}),
            "records": [safe_json({k: v for k, v in r.items() if k != "_dataframe"}) for r in records],
            "duplicate_policy_actions": info.get("duplicates", []),
        },
    }
    try:
        import h5py
    except Exception as exc:
        manifest["h5_error"] = f"h5py unavailable: {exc}"
        json_path.write_text(json.dumps(safe_json(manifest), indent=2, ensure_ascii=False), encoding="utf-8")
        raise
    strdt = h5py.string_dtype(encoding="utf-8")
    n_tables = n_media = n_meta = 0
    with h5py.File(h5_path, "w") as h5:
        for k, v in {"schema": SCHEMA, "app": "FaradaIQ eStudio", "version": VERSION, "created": manifest["created"], "base_name": base_name, "analysis_root": str(root), "container_root": str(container_root), "json_manifest_name": json_path.name}.items():
            h5.attrs[k] = v
        h5.create_dataset("manifest_json", data=json.dumps(safe_json(manifest), ensure_ascii=False), dtype=strdt)
        tables_grp = h5.create_group("tables"); media_grp = h5.create_group("media"); meta_grp = h5.create_group("metadata")
        total_records = max(1, len(records))
        progress_stride = max(1, total_records // 100)
        for idx, rec in enumerate(records, start=1):
            if idx == 1 or idx == total_records or idx % progress_stride == 0:
                log(f"[FaradaIQ bundle progress] {idx}/{total_records} {rec.get('technique', 'record')}: {rec.get('name', '')}")
            _check_bundle_cancel()
            p = Path(rec["source_path"])
            rel = rec.get("relative_path", p.name)
            name = _h5_name(idx, rec)
            try:
                if rec.get("record_type") == "table" and rec.get("_dataframe") is not None:
                    df = rec["_dataframe"]
                    numeric = df.apply(_numeric_bundle_column)
                    arr = numeric.to_numpy(dtype=float, copy=True)
                    ds = tables_grp.create_dataset(name, shape=arr.shape, dtype=arr.dtype, compression="gzip", compression_opts=4, shuffle=True)
                    for row in range(0,len(arr),16384):
                        _check_bundle_cancel()
                        ds[row:row+16384]=arr[row:row+16384]
                    n_tables += 1
                    attrs = {k: v for k, v in rec.items() if k not in {"_dataframe", "source_path"}}
                    ds.attrs["source_path"] = str(p); ds.attrs["relative_path"] = rel
                    ds.attrs["columns_json"] = json.dumps([str(c) for c in df.columns], ensure_ascii=False)
                    ds.attrs["record_json"] = json.dumps(safe_json(attrs), ensure_ascii=False)
                    ds.attrs["technique"] = str(rec.get("technique", "")); ds.attrs["role"] = str(rec.get("role", ""))
                elif rec.get("record_type") == "media":
                    ds = media_grp.create_dataset(name, shape=(p.stat().st_size,), dtype='uint8', compression='gzip',compression_opts=4)
                    with p.open('rb') as stream:
                        offset=0
                        while True:
                            _check_bundle_cancel()
                            raw=stream.read(8*1024*1024)
                            if not raw:break
                            ds[offset:offset+len(raw)]=np.frombuffer(raw,dtype='uint8');offset+=len(raw)
                    n_media += 1
                    ds.attrs["source_path"] = str(p); ds.attrs["relative_path"] = rel
                    ds.attrs["record_json"] = json.dumps(safe_json({k: v for k, v in rec.items() if k != "source_path"}), ensure_ascii=False)
                    ds.attrs["technique"] = str(rec.get("technique", "")); ds.attrs["role"] = str(rec.get("role", ""))
                elif rec.get("record_type") == "metadata":
                    txt = _read_text_preview(p)
                    ds = meta_grp.create_dataset(name, data=txt, dtype=strdt)
                    n_meta += 1
                    ds.attrs["source_path"] = str(p); ds.attrs["relative_path"] = rel
                    ds.attrs["record_json"] = json.dumps(safe_json({k: v for k, v in rec.items() if k != "source_path"}), ensure_ascii=False)
            except InterruptedError:raise
            except Exception as exc:
                if _preserve_bundle_units():raise
                rec["h5_write_error"] = str(exc)
        h5.attrs["n_tables"] = n_tables; h5.attrs["n_media"] = n_media; h5.attrs["n_metadata"] = n_meta
        manifest["h5_counts"] = {"tables": n_tables, "media": n_media, "metadata": n_meta}
        # Convenience counts for AI handoff, QA, and external consumers.
        manifest["table_count"] = n_tables
        manifest["media_count"] = n_media
        manifest["metadata_count"] = n_meta
        del h5["manifest_json"]
        h5.create_dataset("manifest_json", data=json.dumps(safe_json(manifest), ensure_ascii=False), dtype=strdt)
    manifest["browser_cache"] = {"status": "not_extracted_during_bundle_build", "policy": "Data Browser prepares/reuses the hidden cache asynchronously from the canonical H5."}
    json_path.write_text(json.dumps(safe_json(manifest), indent=2, ensure_ascii=False), encoding="utf-8")
    for q in ([] if preserve_sources else parent_jsons_to_delete):
        try:
            if q.resolve() != json_path.resolve() and q.exists(): q.unlink()
        except Exception: pass
    log(f"[FaradaIQ bundle {VERSION}] Complete: {h5_path.name} + {json_path.name} | tables={n_tables} media={n_media} metadata={n_meta}")
    return {
        "bundle_dir": str(container_root), "container_root": str(container_root), "analysis_root": str(root),
        "h5_path": str(h5_path), "json_path": str(json_path), "manifest_path": str(json_path), "summary_path": str(json_path), "index_path": str(json_path), "npy_path": "",
        "n_files_indexed": len(records), "n_files_copied": 0, "techniques_detected": manifest["techniques_detected"], "technique_counts": manifest["technique_counts"], "validation": manifest["data_structure"]["validation"],
    }


def find_latest_bundle(analysis_root: str | Path, base_name: Optional[str] = None) -> Optional[Path]:
    root_in = Path(analysis_root).resolve()
    if root_in.is_file() and root_in.suffix.lower() in {".h5", ".hdf5"}:
        return root_in
    try:
        data_root, container_root, base_name, safe = resolve_bundle_locations(root_in, base_name=base_name)
    except Exception:
        data_root = root_in; container_root = root_in; safe = safe_name(base_name or root_in.name.replace("_analysis_ready_csv", ""))
    candidates=[]
    for base in [container_root, data_root, root_in]:
        try:
            candidates.extend(base.glob(f"{safe}_faradaiq_bundle.h5")); candidates.extend(base.glob("*_faradaiq_bundle.h5"))
        except Exception: pass
    candidates=[p for p in candidates if p.is_file()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def _cache_subdir_for_record(cache: Path, rec: Dict[str, Any]) -> Path:
    tech = safe_name(rec.get("technique") or "Numeric")
    if tech == "CV_LSV" or rec.get("technique") == "CV / LSV":
        rate = _clean_rate_label(rec.get("scan_rate_mV_s"))
        point = int(rec.get("point", 0) or 0)
        cyc = int(rec.get("cycle", 0) or 0)
        return cache / "CV_LSV" / f"scanrate_{rate}" / f"point_{point:03d}" / f"cycle_{cyc:02d}"
    if rec.get("technique") == "EIS":
        point = int(rec.get("point", 0) or 0)
        return cache / "EIS" / f"point_{point:03d}"
    if rec.get("technique") == "DRT":
        point = int(rec.get("point", 0) or 0)
        run = safe_name(rec.get("drt_run_id") or "DRT")
        return cache / "DRT" / run / f"point_{point:03d}"
    point = rec.get("point", "unassigned")
    return cache / tech / f"point_{point}"


def extract_h5_tables_for_browser(bundle_dir_or_h5: str | Path, prefer_reuse: bool = True) -> Dict[str, Any]:
    path = Path(bundle_dir_or_h5).resolve()
    if path.is_dir():
        h5s = sorted(path.glob("*_faradaiq_bundle.h5"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not h5s: h5s = sorted(path.glob("*.h5"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not h5s: raise FileNotFoundError(f"No H5 bundle found in {path}")
        h5_path = h5s[0]
    else:
        h5_path = path
    bundle_dir = h5_path.parent
    json_path = h5_path.with_suffix(".json")
    if not json_path.exists():
        jsons = sorted(bundle_dir.glob("*_faradaiq_bundle.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        json_path = jsons[0] if jsons else None
    manifest = _json_load(json_path) if json_path and json_path.exists() else None
    cache = bundle_dir / ".browser_cache_from_h5"
    source_json = cache / "_bundle_source.json"
    h5_mtime = h5_path.stat().st_mtime
    if prefer_reuse and source_json.exists():
        try:
            cached = json.loads(source_json.read_text(encoding="utf-8", errors="replace"))
            if str(cached.get("h5_path")) == str(h5_path) and float(cached.get("h5_mtime", -1)) == float(h5_mtime):
                return {"cache_root": str(cache), "h5_path": str(h5_path), "json_path": str(json_path or ""), "n_tables": int(cached.get("n_tables", 0)), "manifest": cached.get("manifest"), "cache_reused": True}
        except Exception: pass
    if cache.exists(): shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True, exist_ok=True)
    import h5py
    extracted=[]
    with h5py.File(h5_path, "r") as h5:
        grp = h5.get("tables")
        if grp is not None:
            for key in grp.keys():
                ds = grp[key]
                arr = np.asarray(ds[()])
                try: rec = json.loads(ds.attrs.get("record_json", "{}"))
                except Exception: rec = {}
                try: cols = json.loads(ds.attrs.get("columns_json", "[]"))
                except Exception: cols = []
                width = arr.shape[1] if arr.ndim == 2 else 1
                if not cols or len(cols) != width: cols = [f"col_{i+1}" for i in range(width)]
                sub = _cache_subdir_for_record(cache, rec); sub.mkdir(parents=True, exist_ok=True)
                if rec.get("technique") == "CV / LSV":
                    fname = f"CV_point_{int(rec.get('point',0) or 0):03d}_scanrate_{_clean_rate_label(rec.get('scan_rate_mV_s'))}_cycle_{int(rec.get('cycle',0) or 0):02d}.csv"
                elif rec.get("technique") == "EIS":
                    fname = f"EIS_point_{int(rec.get('point',0) or 0):03d}_Nyquist_Bode.csv"
                elif rec.get("technique") == "DRT":
                    fname = safe_name(rec.get("name") or key) + ".csv"
                else:
                    base_name = safe_name(rec.get("name") or key)
                    while base_name.lower().endswith(".csv"):
                        base_name = base_name[:-4]
                    fname = base_name + ".csv"
                out = sub / fname
                if pd is not None:
                    pd.DataFrame(arr, columns=cols).to_csv(out, index=False)
                else:
                    with out.open("w", newline="", encoding="utf-8") as f:
                        w = csv.writer(f); w.writerow(cols); w.writerows(arr.tolist())
                extracted.append(str(out))
    (cache / "_bundle_source.json").write_text(json.dumps(safe_json({"h5_path": str(h5_path), "h5_mtime": h5_mtime, "json_path": str(json_path or ""), "manifest": manifest, "extracted_files": extracted, "n_tables": len(extracted)}), indent=2, ensure_ascii=False), encoding="utf-8")
    return {"cache_root": str(cache), "h5_path": str(h5_path), "json_path": str(json_path or ""), "n_tables": len(extracted), "manifest": manifest, "cache_reused": False}




# ---- FaradaIQ canonical bundle v138: CV-only HEKA fallback splitter ----
# Some HEKA CV-only exports contain only combined pySECCM-style CV ASC files in
# *_for_plotting_analysis/data. If the pySECCM parser cannot run because the
# generated Windows path is too long, there are no *_Groups folders yet.  The
# canonical H5 builder must still split those combined files into individual
# cycles so the browser, AI, Edison, and SciLink handoffs remain usable.

from io import StringIO as _V138StringIO


def _v138_split_cv_asc_groups(path: Path):
    groups = []
    group_lines = []
    try:
        with Path(path).open('r', encoding='utf-8-sig', errors='replace') as f:
            for line in f:
                if line.startswith('Series') or line.startswith('Sweep'):
                    continue
                if not line.strip():
                    if group_lines:
                        try:
                            group_string = _V138StringIO(''.join(group_lines).replace(' ', '').replace('"', ''))
                            df = pd.read_csv(group_string) if pd is not None else None
                            if df is not None and not df.empty:
                                groups.append(df.dropna(axis=0, how='all').dropna(axis=1, how='all'))
                        except Exception:
                            pass
                        group_lines = []
                else:
                    group_lines.append(line.replace('"', '').replace(' ', ''))
        if group_lines:
            try:
                group_string = _V138StringIO(''.join(group_lines).replace(' ', '').replace('"', ''))
                df = pd.read_csv(group_string) if pd is not None else None
                if df is not None and not df.empty:
                    groups.append(df.dropna(axis=0, how='all').dropna(axis=1, how='all'))
            except Exception:
                pass
    except Exception:
        return []
    return groups


_V136_PREV_COLLECT_HEKA_RECORDS = _collect_heka_records


def _collect_heka_records(root: Path, log=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    records, info = _V136_PREV_COLLECT_HEKA_RECORDS(root, log=log)
    try:
        have_cv = any(r.get('technique') == 'CV / LSV' and r.get('record_type') == 'table' for r in records)
    except Exception:
        have_cv = False
    if have_cv:
        return records, info
    plot_root = _plotting_root(Path(root))
    data_dir = plot_root / 'data' if plot_root else None
    if not data_dir or not data_dir.exists():
        return records, info
    added = []
    for p in sorted(data_dir.glob('*CV*.asc'), key=lambda x: x.as_posix().lower()):
        low = p.as_posix().lower()
        if '_groups/' in low or '_groups\\' in low:
            continue
        rate, cycles_from_name, method_entry = _extract_rate_cycles_method(p)
        groups = _v138_split_cv_asc_groups(p)
        if not groups:
            continue
        for gi, df in enumerate(groups, start=1):
            try:
                sdf = _standardize_cv(df, p)
                if sdf is None or sdf.empty or sdf.shape[1] < 2:
                    continue
                cycle = gi
                point = 1
                rec = _make_table_record(
                    p, Path(root), 'CV / LSV', 'native split CV cycle from combined ASC', sdf,
                    point=point, cycle=int(cycle), scan_rate_mV_s=float(rate) if rate is not None else None,
                    scan_rate_label=_clean_rate_label(rate), method_entry=method_entry,
                    source_splitter='v138_native_combined_cv_asc_splitter',
                    source_group_count=len(groups), source_group_index=gi-1,
                    canonical_key=f"CV_NATIVE:point{point:03d}:rate{_clean_rate_label(rate)}:cycle{int(cycle):02d}:{safe_name(p.stem)}",
                )
                # Use a distinct display name without needing to materialize extra duplicate CSV files.
                rec['name'] = f"{p.stem}_cycle_{cycle:02d}{p.suffix}"
                added.append(rec)
            except Exception:
                continue
    if not added:
        return records, info
    records = records + added
    # For this fallback, the HEKA sequence point_count can be synthetic/overestimated because
    # no real stage coordinates were decoded. Disable expected-CV-count checks and report the
    # detected cycle structure instead of a misleading point count.
    seq = _sequence_info(Path(root))
    try:
        seq['v138_cv_fallback_mode'] = 'combined ASC split natively because pySECCM *_Groups files were absent'
        seq['v138_original_point_count_from_sequence_manifest'] = seq.get('point_count')
        seq['point_count'] = None
        seq['scan_rates_mV_s'] = sorted({float(r.get('scan_rate_mV_s')) for r in added if r.get('scan_rate_mV_s') is not None})
        seq['cycles_per_cv'] = None
    except Exception:
        pass
    diagnostics = dict(((info or {}).get('validation') or {}).get('diagnostics') or {})
    diagnostics['v138_native_cv_fallback_files'] = len({r.get('relative_path') for r in added})
    diagnostics['v138_native_cv_fallback_cycles'] = len(added)
    new_info = _build_info(records, records, seq, is_heka=True, duplicates=(info or {}).get('duplicates', []), diagnostics=diagnostics)
    try:
        new_info['validation_note'] = 'v138 used native combined-CV ASC splitting because pySECCM group folders were missing.'
    except Exception:
        pass
    return records, new_info

# ---- End v138 CV-only HEKA fallback splitter ----

# ---- FaradaIQ canonical bundle v138: raw line-scan CV/CP + multi-method EIS repair ----
# v138 fixes three classes of HEKA exports that were not handled reliably by the
# v136 bundle catalog:
#   1) raw line-scan CV data where HEKA decoded all current/potential pairs into
#      point_001/seqNN_*.csv and left point_002...point_N as TAKEDATA-only placeholders;
#   2) raw CP/current-potential line scans with the same point_001/seqNN pattern;
#   3) EIS exports with two methods per point, where v136 de-duplicated method2.

SCHEMA = "faradaiq-canonical-h5-bundle-v6"
VERSION = "v331"


def _v138_parse_seq_order(path: Path) -> int:
    m = re.search(r"seq[_-]?(\d+)", path.name, flags=re.I)
    return int(m.group(1)) if m else 10**9


def _v138_infer_scan_rate_mVs(df) -> Optional[float]:
    """Infer CV/LSV scan rate from median |dE/dt| in mV/s when metadata is missing."""
    if pd is None or df is None or df.empty:
        return None
    cols = {str(c).lower(): c for c in df.columns}
    tcol = None; ecol = None
    for c in df.columns:
        l = str(c).lower()
        if tcol is None and (l == 'time_s' or 'time' in l):
            tcol = c
        if ecol is None and (l == 'potential_v' or 'potential' in l or 'voltage' in l or 'ewe' in l or 'emon' in l):
            ecol = c
    if tcol is None or ecol is None:
        return None
    try:
        t = pd.to_numeric(df[tcol], errors='coerce').to_numpy(dtype=float)
        e = pd.to_numeric(df[ecol], errors='coerce').to_numpy(dtype=float)
        ok = np.isfinite(t) & np.isfinite(e)
        t = t[ok]; e = e[ok]
        if len(t) < 5:
            return None
        dt = np.diff(t); de = np.diff(e)
        good = np.isfinite(dt) & np.isfinite(de) & (np.abs(dt) > 1e-12)
        vals = np.abs(de[good] / dt[good]) * 1000.0
        vals = vals[np.isfinite(vals)]
        vals = vals[(vals > 0.01) & (vals < 1e6)]
        if len(vals) == 0:
            return None
        med = float(np.nanmedian(vals))
        if med < 0.05:
            return None
        # Return rounded common values cleanly, but preserve unusual rates.
        for common in (1, 2, 5, 10, 20, 25, 50, 75, 100, 200, 500, 1000):
            if abs(med - common) / max(common, 1) < 0.08:
                return float(common)
        return float(round(med, 3))
    except Exception:
        return None


def _v138_sequence_point_dirs(plot_root: Path) -> List[Path]:
    base = plot_root / 'data_by_point_sequence'
    if not base.exists():
        return []
    return sorted([p for p in base.glob('point_*') if p.is_dir()], key=lambda p: p.name.lower())


def _v138_collect_line_scan_pair_records(root: Path, existing_records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    root = Path(root)
    plot_root = _plotting_root(root)
    if not plot_root:
        return []
    seq = _sequence_info(root)
    point_count = int(seq.get('point_count') or 0)
    if point_count < 2:
        return []
    point_dirs = _v138_sequence_point_dirs(plot_root)
    if not point_dirs:
        return []
    # This repair is for the specific HEKA fallback pattern where point_001 contains
    # nearly all experimental traces and later point folders contain only manifests.
    p1 = plot_root / 'data_by_point_sequence' / 'point_001'
    if not p1.exists():
        return []
    candidate_names = ('_CV', 'UNCATEGORIZED_CURRENT_POTENTIAL', 'CURRENT_POTENTIAL_PAIR')
    p1_files = sorted([p for p in p1.glob('seq*.csv') if any(tok.lower() in p.name.lower() for tok in candidate_names)], key=_v138_parse_seq_order)
    if len(p1_files) < max(3, int(point_count * 0.5)):
        return []
    # If the normal grouped CV path already produced a complete point/rate/cycle set,
    # do not add this repair. It is only for collapsed single-block exports.
    existing_cv = [r for r in existing_records if r.get('record_type') == 'table' and r.get('technique') == 'CV / LSV']
    existing_cp = [r for r in existing_records if r.get('record_type') == 'table' and str(r.get('technique','')).lower().startswith('chronopot')]
    # If the combined ASC splitter already created a compact CV cycle catalog
    # (typical multi-scan-rate HEKA CV export), keep that compact structure.
    if any('native_combined_cv_asc_splitter' in str(r.get('source_splitter', '')) for r in existing_cv):
        return []
    if len(existing_cv) >= point_count and 'cp' not in root.name.lower():
        return []
    lowroot = root.as_posix().lower()
    force_cp = bool(re.search(r'(^|[_\-\s])cp($|[_\-\s])', root.name, flags=re.I) or 'chronopot' in lowroot)
    force_cv = ('cv' in lowroot) and not force_cp
    added: List[Dict[str, Any]] = []
    # Use at most one trace per expected line-scan point. Extra files remain available
    # in raw CSV output but are not duplicated into canonical H5.
    for i, p in enumerate(p1_files[:point_count], start=1):
        df, cols, err = _read_table_general(p)
        if df is None:
            continue
        sdf = _standardize_cv(df, p)
        if sdf is None or sdf.empty or sdf.shape[1] < 2:
            continue
        # Decide technique. CP datasets are time/potential/current line scans.
        if force_cp:
            tech = 'Chronopotentiometry / CP'
            role = 'line-scan CP/current-potential point table'
            keyprefix = 'CP'
            rate = None
            cycle = 1
        else:
            tech = 'CV / LSV'
            role = 'line-scan CV/LSV point table from HEKA sequence'
            keyprefix = 'CV_LINE'
            rate = None
            # Prefer any explicit scan_rate column, then infer from dE/dt.
            for c in sdf.columns:
                if str(c).lower() == 'scan_rate_v_s':
                    vals = pd.to_numeric(sdf[c], errors='coerce').dropna() if pd is not None else []
                    if len(vals):
                        rate = float(vals.iloc[0]) * 1000.0
                    break
            if rate is None:
                rate = _v138_infer_scan_rate_mVs(sdf)
            cycle = 1
        rec = _make_table_record(
            p, root, tech, role, sdf,
            point=int(i), cycle=int(cycle), scan_rate_mV_s=float(rate) if rate is not None else None,
            scan_rate_label=_clean_rate_label(rate), sequence_order=_v138_parse_seq_order(p),
            source_repair='v138_collapsed_point001_line_scan_repair',
            original_point_folder='point_001', remapped_from_sequence_file=p.name,
            canonical_key=f"{keyprefix}:point{int(i):03d}:cycle{int(cycle):02d}:rate{_clean_rate_label(rate)}",
        )
        rec['name'] = f"{keyprefix}_point_{int(i):03d}_cycle_{int(cycle):02d}_{p.name}"
        added.append(rec)
    return added


def _v138_collect_all_eis_method_records(root: Path) -> List[Dict[str, Any]]:
    root = Path(root)
    plot_root = _plotting_root(root)
    drt_dir = plot_root / 'DRT_input_csv' if plot_root else None
    if not drt_dir or not drt_dir.exists():
        return []
    recs: List[Dict[str, Any]] = []
    for p in sorted(drt_dir.glob('position_*_freq_zreal_zimag.csv'), key=lambda x: x.as_posix().lower()):
        m = re.search(r'position[_-]*0*(\d+)', p.name, flags=re.I)
        point = int(m.group(1)) if m else None
        mm = re.search(r'method[_-]*0*(\d+)', p.name, flags=re.I)
        method = int(mm.group(1)) if mm else 1
        df, cols, err = _read_table_general(p)
        if df is None:
            continue
        sdf = _standardize_eis(df, p)
        if sdf is None or sdf.empty or sdf.shape[1] < 3:
            continue
        recs.append(_make_table_record(
            p, root, 'EIS', 'EIS impedance table for Nyquist/Bode', sdf,
            point=point, eis_method=method, method_entry=method,
            eis_view_support=['Nyquist', 'Bode magnitude', 'Bode phase'],
            canonical_key=f"EIS:point{int(point or 0):03d}:method{int(method):02d}",
        ))
    return recs


_V138_PREV_COLLECT_HEKA_RECORDS = _collect_heka_records


def _collect_heka_records(root: Path, log=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    root = Path(root)
    records, info = _V138_PREV_COLLECT_HEKA_RECORDS(root, log=log)
    seq = dict((info or {}).get('sequence_info') or _sequence_info(root))
    diagnostics = dict(((info or {}).get('validation') or {}).get('diagnostics') or {})

    # Replace EIS records with all position/method records when present.
    all_eis = _v138_collect_all_eis_method_records(root)
    if all_eis:
        old_eis_count = len([r for r in records if r.get('record_type') == 'table' and r.get('technique') == 'EIS'])
        records = [r for r in records if not (r.get('record_type') == 'table' and r.get('technique') == 'EIS')]
        records.extend(all_eis)
        diagnostics['v138_eis_method_records'] = len(all_eis)
        diagnostics['v138_eis_records_replaced_from_v136'] = old_eis_count

    # Repair collapsed line-scan CV/CP datasets.
    line_scan = _v138_collect_line_scan_pair_records(root, records)
    if line_scan:
        # If v136 native combined-ASC fallback inserted one/two point-001 CV files,
        # remove them because this repair supplies the per-point line-scan structure.
        records = [r for r in records if not (r.get('record_type') == 'table' and r.get('technique') == 'CV / LSV' and str(r.get('source_splitter','')).startswith('v138_native_combined_cv_asc_splitter'))]
        records = [r for r in records if not (r.get('record_type') == 'table' and r.get('technique') == 'CV / LSV' and str(r.get('source_splitter','')).startswith('v136_native_combined_cv_asc_splitter'))]
        # Also remove old point_001-only CV records if this is the CV collapsed repair.
        if any(r.get('technique') == 'CV / LSV' for r in line_scan):
            records = [r for r in records if not (r.get('record_type') == 'table' and r.get('technique') == 'CV / LSV')]
        if any(str(r.get('technique','')).startswith('Chronopotentiometry') for r in line_scan):
            records = [r for r in records if not (r.get('record_type') == 'table' and str(r.get('technique','')).startswith('Chronopotentiometry'))]
        records.extend(line_scan)
        diagnostics['v138_collapsed_line_scan_records'] = len(line_scan)
        try:
            seq['v138_line_scan_repair'] = 'point_001 contained the experimental current/potential sequence; records were remapped to line-scan points.'
        except Exception:
            pass

    # De-duplicate by canonical_key but keep EIS method variants.
    final=[]; seen=set(); dup=[]
    for r in records:
        key = r.get('canonical_key') or (r.get('record_type'), r.get('relative_path'))
        if key in seen and r.get('record_type') == 'table':
            dup.append(r.get('relative_path'))
            continue
        seen.add(key); final.append(r)
    new_info = _build_info(final, records, seq, is_heka=True, duplicates=dup, diagnostics=diagnostics)
    return final, new_info


def _cache_subdir_for_record(cache: Path, rec: Dict[str, Any]) -> Path:
    tech = safe_name(rec.get('technique') or 'Numeric')
    if rec.get('technique') == 'CV / LSV':
        rate = _clean_rate_label(rec.get('scan_rate_mV_s'))
        point = int(rec.get('point', 0) or 0)
        cyc = int(rec.get('cycle', 0) or 0)
        return cache / 'CV_LSV' / f'scanrate_{rate}' / f'point_{point:03d}' / f'cycle_{cyc:02d}'
    if rec.get('technique') == 'EIS':
        point = int(rec.get('point', 0) or 0)
        method = int(rec.get('eis_method', rec.get('method_entry', 1)) or 1)
        return cache / 'EIS' / f'point_{point:03d}' / f'method_{method:02d}'
    if rec.get('technique') == 'DRT':
        point = int(rec.get('point', 0) or 0)
        run = safe_name(rec.get('drt_run_id') or 'DRT')
        return cache / 'DRT' / run / f'point_{point:03d}'
    if str(rec.get('technique','')).startswith('Chronopotentiometry'):
        point = int(rec.get('point', 0) or 0)
        return cache / 'CP' / f'point_{point:03d}'
    point = rec.get('point', 'unassigned')
    return cache / tech / f'point_{point}'


_V138_PREV_EXTRACT_H5_TABLES_FOR_BROWSER = extract_h5_tables_for_browser


def extract_h5_tables_for_browser(bundle_dir_or_h5: str | Path, prefer_reuse: bool = True) -> Dict[str, Any]:
    path = Path(bundle_dir_or_h5).resolve()
    if path.is_dir():
        h5s = sorted(path.glob('*_faradaiq_bundle.h5'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not h5s:
            h5s = sorted(path.glob('*.h5'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not h5s:
            raise FileNotFoundError(f'No H5 bundle found in {path}')
        h5_path = h5s[0]
    else:
        h5_path = path
    bundle_dir = h5_path.parent
    json_path = h5_path.with_suffix('.json')
    if not json_path.exists():
        jsons = sorted(bundle_dir.glob('*_faradaiq_bundle.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        json_path = jsons[0] if jsons else None
    manifest = _json_load(json_path) if json_path and json_path.exists() else None
    cache = bundle_dir / '.browser_cache_from_h5'
    source_json = cache / '_bundle_source.json'
    h5_mtime = h5_path.stat().st_mtime
    if prefer_reuse and source_json.exists():
        try:
            cached = json.loads(source_json.read_text(encoding='utf-8', errors='replace'))
            if str(cached.get('h5_path')) == str(h5_path) and float(cached.get('h5_mtime', -1)) == float(h5_mtime):
                return {'cache_root': str(cache), 'cache_dir': str(cache), 'h5_path': str(h5_path), 'json_path': str(json_path or ''), 'n_tables': int(cached.get('n_tables', 0)), 'manifest': cached.get('manifest'), 'cache_reused': True}
        except Exception:
            pass
    if cache.exists():
        shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True, exist_ok=True)
    import h5py
    extracted=[]
    with h5py.File(h5_path, 'r') as h5:
        grp = h5.get('tables')
        if grp is not None:
            for key in grp.keys():
                ds = grp[key]
                arr = np.asarray(ds[()])
                try: rec = json.loads(ds.attrs.get('record_json', '{}'))
                except Exception: rec = {}
                try: cols = json.loads(ds.attrs.get('columns_json', '[]'))
                except Exception: cols = []
                width = arr.shape[1] if arr.ndim == 2 else 1
                if not cols or len(cols) != width:
                    cols = [f'col_{i+1}' for i in range(width)]
                sub = _cache_subdir_for_record(cache, rec); sub.mkdir(parents=True, exist_ok=True)
                tech = rec.get('technique')
                if tech == 'CV / LSV':
                    fname = f"CV_point_{int(rec.get('point',0) or 0):03d}_scanrate_{_clean_rate_label(rec.get('scan_rate_mV_s'))}_cycle_{int(rec.get('cycle',0) or 0):02d}.csv"
                elif tech == 'EIS':
                    method = int(rec.get('eis_method', rec.get('method_entry', 1)) or 1)
                    fname = f"EIS_point_{int(rec.get('point',0) or 0):03d}_method_{method:02d}_Nyquist_Bode.csv"
                elif tech == 'DRT':
                    fname = safe_name(rec.get('name') or key) + '.csv'
                elif str(tech).startswith('Chronopotentiometry'):
                    fname = f"CP_point_{int(rec.get('point',0) or 0):03d}.csv"
                else:
                    fname = safe_name(rec.get('name') or key) + '.csv'
                out = sub / fname
                if pd is not None:
                    pd.DataFrame(arr, columns=cols).to_csv(out, index=False)
                else:
                    with out.open('w', newline='', encoding='utf-8') as f:
                        w = csv.writer(f); w.writerow(cols); w.writerows(arr.tolist())
                extracted.append(str(out))
    (cache / '_bundle_source.json').write_text(json.dumps(safe_json({'h5_path': str(h5_path), 'h5_mtime': h5_mtime, 'json_path': str(json_path or ''), 'manifest': manifest, 'extracted_files': extracted, 'n_tables': len(extracted)}), indent=2, ensure_ascii=False), encoding='utf-8')
    return {'cache_root': str(cache), 'cache_dir': str(cache), 'h5_path': str(h5_path), 'json_path': str(json_path or ''), 'n_tables': len(extracted), 'manifest': manifest, 'cache_reused': False}

# ---- End v138 raw line-scan repair ----


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Build FaradaIQ canonical one-H5/one-JSON bundle")
    ap.add_argument("analysis_root")
    ap.add_argument("--base-name", default=None)
    ap.add_argument("--extract-browser-cache", action="store_true")
    ns = ap.parse_args()
    info = build_canonical_bundle(ns.analysis_root, base_name=ns.base_name)
    print(json.dumps(safe_json(info), indent=2))
    if ns.extract_browser_cache:
        print(json.dumps(safe_json(extract_h5_tables_for_browser(info["h5_path"])), indent=2))

# ---- FaradaIQ canonical bundle v145: root primary CV/CP line-scan repair ----
# Some raw HEKA line-scan exports produce one root-level primary curve CSV per
# point as *__mNNN__c001__current_vs_potential.csv, while pySECCM-style grouped
# ASC files may be absent or incomplete.  Treat those root primary curves as
# the authoritative browser tables when they outnumber the compact fallback CV
# records.

VERSION = "v331"


def _v145_first_method_scan_rate_mVs(root: Path) -> Optional[float]:
    try:
        for p in sorted(Path(root).glob('*_heka_method_entries.csv')):
            txt = p.read_text(encoding='utf-8', errors='replace')
            m = re.search(r'Rate=\s*([0-9.+-Ee]+)\s*mV', txt, flags=re.I)
            if m:
                return float(m.group(1))
            if pd is not None:
                try:
                    df = pd.read_csv(p)
                    if 'rate' in df.columns:
                        for val in df['rate'].dropna().astype(str):
                            mm = re.search(r'([0-9.+-Ee]+)', val)
                            if mm:
                                return float(mm.group(1))
                except Exception:
                    pass
    except Exception:
        pass
    return None


def _v145_read_primary_current_csv(path: Path):
    if pd is None:
        return None
    try:
        df = pd.read_csv(path, header=None, comment='#')
    except Exception:
        try:
            df = pd.read_csv(path, header=None, sep=None, engine='python')
        except Exception:
            return None
    if df is None or df.empty:
        return None
    # Keep numeric columns only. Two-column files are potential,current.
    num = df.apply(lambda col: pd.to_numeric(col, errors='coerce'))
    num = num.dropna(axis=0, how='all').dropna(axis=1, how='all')
    if num.shape[1] >= 2:
        out = pd.DataFrame()
        out['potential_V'] = num.iloc[:, 0]
        out['current_A'] = num.iloc[:, 1]
        if num.shape[1] >= 3:
            out['extra_col_3'] = num.iloc[:, 2]
        return out.dropna(axis=0, how='all')
    return None


def _v145_collect_root_primary_current_records(root: Path, existing_records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    root = Path(root)
    files = sorted(root.glob('*__m*__c*__current_vs_potential.csv'), key=lambda p: p.name.lower())
    if not files:
        return []
    lowroot = root.name.lower()
    force_cp = bool(re.search(r'(^|[_\-\s])cp($|[_\-\s])', root.name, flags=re.I) or 'chronopot' in lowroot)
    default_rate = _v145_first_method_scan_rate_mVs(root)
    added=[]
    for p in files:
        df = _v145_read_primary_current_csv(p)
        if df is None or df.empty or df.shape[1] < 2:
            continue
        mm = re.search(r'__m0*(\d+)__c0*(\d+)__', p.name, flags=re.I)
        point = int(mm.group(1)) if mm else (len(added)+1)
        cycle = int(mm.group(2)) if mm else 1
        if force_cp:
            tech = 'Chronopotentiometry / CP'
            role = 'root-level HEKA primary current-potential CP line-scan point'
            key = f'CP_ROOT:point{point:03d}:cycle{cycle:02d}'
            rate = None
        else:
            tech = 'CV / LSV'
            role = 'root-level HEKA primary current-potential CV/LSV line-scan point'
            key = f'CV_ROOT:point{point:03d}:cycle{cycle:02d}:rate{_clean_rate_label(default_rate)}'
            rate = default_rate
        rec = _make_table_record(
            p, root, tech, role, df,
            point=int(point), cycle=int(cycle), scan_rate_mV_s=rate,
            scan_rate_label=_clean_rate_label(rate), source_repair='v145_root_primary_current_vs_potential',
            canonical_key=key,
        )
        rec['name'] = f"{('CP' if force_cp else 'CV')}_point_{point:03d}_cycle_{cycle:02d}_{p.name}"
        added.append(rec)
    return added


_V145_PREV_COLLECT_HEKA_RECORDS = _collect_heka_records


def _collect_heka_records(root: Path, log=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    root = Path(root)
    records, info = _V145_PREV_COLLECT_HEKA_RECORDS(root, log=log)
    seq = dict((info or {}).get('sequence_info') or _sequence_info(root))
    diagnostics = dict(((info or {}).get('validation') or {}).get('diagnostics') or {})
    primary = _v145_collect_root_primary_current_records(root, records)
    if primary:
        primary_techs = set(r.get('technique') for r in primary)
        for tech in primary_techs:
            existing_count = len([r for r in records if r.get('record_type') == 'table' and r.get('technique') == tech])
            primary_count = len([r for r in primary if r.get('technique') == tech])
            # Use root primary curves when they are clearly more complete than the compact fallback.
            if primary_count > existing_count:
                records = [r for r in records if not (r.get('record_type') == 'table' and r.get('technique') == tech)]
                records.extend([r for r in primary if r.get('technique') == tech])
                diagnostics[f'v145_root_primary_{safe_name(tech)}_records'] = primary_count
                diagnostics[f'v145_root_primary_{safe_name(tech)}_replaced_previous'] = existing_count
    final=[]; seen=set(); dup=[]
    for r in records:
        key = r.get('canonical_key') or (r.get('record_type'), r.get('relative_path'))
        if key in seen and r.get('record_type') == 'table':
            dup.append(r.get('relative_path'))
            continue
        seen.add(key); final.append(r)
    new_info = _build_info(final, records, seq, is_heka=True, duplicates=dup, diagnostics=diagnostics)
    return final, new_info

# ---- End FaradaIQ canonical bundle v145 root primary line-scan repair ----

# ---- FaradaIQ canonical bundle v145.1: sequence-aware root CV line-scan mapping ----
# v145/v145.0 treated every root-level *__mNNN__c001__current_vs_potential.csv
# as a separate point at the first scan rate.  For HEKA/SECCM line scans the
# root files are usually ordered as, for each spatial point:
#   CV method 1 cycles, CV method 2 cycles, ...
# This patch reconstructs point / scan-rate / cycle from the HEKA sequence
# manifest or method table so mixed scan-rate datasets no longer collapse to
# one scan rate in the browser.

VERSION = "v331"


def _v145_numeric_m_index(path: Path) -> int:
    m = re.search(r"__m0*(\d+)__c0*(\d+)__", path.name, flags=re.I)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    return 10**12


def _v145_cv_method_sequence(root: Path) -> List[Dict[str, Any]]:
    """Return ordered CV methods for root primary curve mapping.

    Only known scan rates are used by default.  Unknown-rate single CV methods
    in HEKA exports often correspond to an instrument template or an extra raw
    block, and including them makes point/rate/cycle mapping wrong for the
    ordinary line-scan current_vs_potential files.  If there are no known-rate
    methods, unknown-rate methods are returned as a fallback.
    """
    root = Path(root)
    seq_file = _sequence_manifest(root)
    obj = _json_load(seq_file) if seq_file else None
    methods: List[Dict[str, Any]] = []
    if isinstance(obj, dict):
        pts = obj.get("points") or []
        first = pts[0] if pts else {}
        seen = set()
        for m in first.get("methods", []) or []:
            if str(m.get("method_type", "")).upper() != "CV":
                continue
            me = m.get("method_entry")
            rate = m.get("scan_rate_mV_s")
            ncy = m.get("n_cycles") or m.get("cycles") or 1
            try:
                ncy = int(ncy)
            except Exception:
                ncy = 1
            key = (me, str(rate), ncy)
            if key in seen:
                continue
            seen.add(key)
            methods.append({
                "method_entry": me,
                "scan_rate_mV_s": _num(rate),
                "scan_rate_label": _clean_rate_label(rate),
                "n_cycles": max(1, ncy),
                "source": "heka_sequence_manifest",
                "raw": safe_json(m),
            })
    if not methods and pd is not None:
        # Fallback to method entries CSV when the sequence manifest is absent.
        for p in sorted(root.glob("*_heka_method_entries.csv")):
            try:
                df = pd.read_csv(p)
            except Exception:
                continue
            for _, row in df.iterrows():
                if str(row.get("technique_text", "")).strip().upper() not in {"CV", "LSV"}:
                    continue
                rate = None
                raw_rate = row.get("rate")
                mm = re.search(r"([0-9.+-Ee]+)", str(raw_rate))
                if mm:
                    try: rate = float(mm.group(1))
                    except Exception: rate = None
                ncy = row.get("cycles", 1)
                try: ncy = int(float(ncy))
                except Exception: ncy = 1
                methods.append({
                    "method_entry": row.get("method_entry"),
                    "scan_rate_mV_s": _num(rate),
                    "scan_rate_label": _clean_rate_label(rate),
                    "n_cycles": max(1, ncy),
                    "source": f"method_entries:{p.name}",
                    "raw": safe_json(row.to_dict()),
                })
            if methods:
                break
    known = [m for m in methods if m.get("scan_rate_mV_s") is not None]
    return known or methods


def _v145_collect_root_primary_current_records_sequence_aware(root: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    root = Path(root)
    files = sorted(root.glob("*__m*__c*__current_vs_potential.csv"), key=_v145_numeric_m_index)
    if not files:
        return [], {}
    lowroot = root.name.lower()
    force_cp = bool(re.search(r"(^|[_\-\s])cp($|[_\-\s])", root.name, flags=re.I) or "chronopot" in lowroot)
    added: List[Dict[str, Any]] = []
    derived: Dict[str, Any] = {
        "v145_root_primary_files": len(files),
        "v145_mapping_mode": "cp_point_order" if force_cp else "cv_unknown",
    }
    if force_cp:
        for p in files:
            df = _v145_read_primary_current_csv(p)
            if df is None or df.empty or df.shape[1] < 2:
                continue
            mm = re.search(r"__m0*(\d+)__c0*(\d+)__", p.name, flags=re.I)
            point = int(mm.group(1)) if mm else (len(added)+1)
            cycle = int(mm.group(2)) if mm else 1
            rec = _make_table_record(
                p, root, "Chronopotentiometry / CP", "root-level HEKA primary CP line-scan point", df,
                point=int(point), cycle=int(cycle), source_repair="v145_root_primary_cp_current_vs_potential",
                canonical_key=f"CP_ROOT:point{point:03d}:cycle{cycle:02d}",
            )
            rec["name"] = f"CP_point_{point:03d}_cycle_{cycle:02d}_{p.name}"
            added.append(rec)
        derived.update({"point_count": len(added), "technique": "Chronopotentiometry / CP"})
        return added, derived

    methods = _v145_cv_method_sequence(root)
    total_cycles = sum(int(m.get("n_cycles") or 1) for m in methods)
    # If known-rate methods cannot explain the root files, try including unknown-rate
    # methods from the manifest. This is a fallback only.
    if methods and total_cycles and len(files) % total_cycles != 0:
        seq_file = _sequence_manifest(root)
        obj = _json_load(seq_file) if seq_file else None
        all_methods = []
        if isinstance(obj, dict):
            first = (obj.get("points") or [{}])[0]
            seen = set()
            for m in first.get("methods", []) or []:
                if str(m.get("method_type", "")).upper() != "CV":
                    continue
                ncy = m.get("n_cycles") or 1
                try: ncy = int(ncy)
                except Exception: ncy = 1
                key = (m.get("method_entry"), str(m.get("scan_rate_mV_s")), ncy)
                if key in seen: continue
                seen.add(key)
                all_methods.append({
                    "method_entry": m.get("method_entry"),
                    "scan_rate_mV_s": _num(m.get("scan_rate_mV_s")),
                    "scan_rate_label": _clean_rate_label(m.get("scan_rate_mV_s")),
                    "n_cycles": max(1, ncy),
                    "source": "heka_sequence_manifest_all_cv_fallback",
                    "raw": safe_json(m),
                })
        all_cycles = sum(int(m.get("n_cycles") or 1) for m in all_methods)
        if all_methods and all_cycles and len(files) % all_cycles == 0:
            methods = all_methods
            total_cycles = all_cycles
    if not methods or not total_cycles:
        # Last-resort behavior: one rate guessed from the first method.
        default_rate = _v145_first_method_scan_rate_mVs(root)
        methods = [{"method_entry": None, "scan_rate_mV_s": default_rate, "scan_rate_label": _clean_rate_label(default_rate), "n_cycles": 1, "source": "fallback_first_method"}]
        total_cycles = 1
    point_count = len(files) // total_cycles if total_cycles and len(files) % total_cycles == 0 else len(files)
    expanded = []
    for m in methods:
        for cyc in range(1, int(m.get("n_cycles") or 1) + 1):
            expanded.append((m, cyc))
    if not expanded:
        expanded = [(methods[0], 1)]

    for i, p in enumerate(files):
        df = _v145_read_primary_current_csv(p)
        if df is None or df.empty or df.shape[1] < 2:
            continue
        if len(files) % len(expanded) == 0:
            point = i // len(expanded) + 1
            method, cycle = expanded[i % len(expanded)]
        else:
            # In ambiguous cases, keep original file index as point but still avoid claiming all rates are the first.
            point = i + 1
            method, cycle = expanded[i % len(expanded)]
        rate = method.get("scan_rate_mV_s")
        rate_label = _clean_rate_label(rate)
        role = "root-level HEKA primary CV/LSV line-scan point mapped by method sequence"
        key = f"CV_ROOT:point{int(point):03d}:rate{rate_label}:cycle{int(cycle):02d}"
        rec = _make_table_record(
            p, root, "CV / LSV", role, df,
            point=int(point), cycle=int(cycle), scan_rate_mV_s=rate,
            scan_rate_label=rate_label, method_entry=method.get("method_entry"),
            source_repair="v145_sequence_aware_root_primary_current_vs_potential",
            original_measurement_index=_v145_numeric_m_index(p),
            canonical_key=key,
        )
        rec["name"] = f"CV_point_{int(point):03d}_scanrate_{rate_label}_cycle_{int(cycle):02d}.csv"
        added.append(rec)
    derived.update({
        "v145_mapping_mode": "cv_method_sequence_root_primary",
        "point_count": point_count,
        "scan_rates_mV_s": [m.get("scan_rate_mV_s") for m in methods if m.get("scan_rate_mV_s") is not None],
        "cycles_per_cv": max([int(m.get("n_cycles") or 1) for m in methods] or [1]),
        "methods": safe_json(methods),
        "total_cycles_per_point": total_cycles,
        "one_file_per_point_scanrate_cycle_expected": bool(len(files) % max(1, total_cycles) == 0),
    })
    return added, derived


# Use the pre-root-primary collector when available, then apply the corrected root primary map.
_V145_SEQUENCE_BASE_COLLECT_HEKA_RECORDS = globals().get("_V145_PREV_COLLECT_HEKA_RECORDS", globals().get("_V139_PREV_COLLECT_HEKA_RECORDS", _collect_heka_records))


def _collect_heka_records(root: Path, log=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    root = Path(root)
    try:
        records, info = _V145_SEQUENCE_BASE_COLLECT_HEKA_RECORDS(root, log=log)
    except TypeError:
        records, info = _V145_SEQUENCE_BASE_COLLECT_HEKA_RECORDS(root)
    info = info or {}
    seq = dict(info.get("sequence_info") or _sequence_info(root))
    diagnostics = dict((info.get("validation") or {}).get("diagnostics") or {})
    primary, derived = _v145_collect_root_primary_current_records_sequence_aware(root)
    if primary:
        primary_techs = set(r.get("technique") for r in primary)
        for tech in primary_techs:
            existing_count = len([r for r in records if r.get("record_type") == "table" and r.get("technique") == tech])
            primary_count = len([r for r in primary if r.get("technique") == tech])
            if primary_count > existing_count:
                records = [r for r in records if not (r.get("record_type") == "table" and r.get("technique") == tech)]
                records.extend([r for r in primary if r.get("technique") == tech])
                diagnostics[f"v145_sequence_aware_root_primary_{safe_name(tech)}_records"] = primary_count
                diagnostics[f"v145_sequence_aware_root_primary_{safe_name(tech)}_replaced_previous"] = existing_count
        if derived.get("point_count"):
            seq["point_count"] = int(derived.get("point_count"))
        if derived.get("scan_rates_mV_s"):
            seq["scan_rates_mV_s"] = derived.get("scan_rates_mV_s")
        if derived.get("cycles_per_cv"):
            seq["cycles_per_cv"] = int(derived.get("cycles_per_cv"))
        diagnostics.update({k: v for k, v in derived.items() if k not in {"methods"}})
        diagnostics["v145_sequence_aware_methods"] = derived.get("methods")
    final=[]; seen=set(); dup=[]
    for r in records:
        key = r.get("canonical_key") or (r.get("record_type"), r.get("relative_path"))
        if key in seen and r.get("record_type") == "table":
            dup.append(r.get("relative_path")); continue
        seen.add(key); final.append(r)
    return final, _build_info(final, records, seq, is_heka=True, duplicates=dup, diagnostics=diagnostics)

# ---- End v145.1 sequence-aware root CV line-scan mapping ----

# ---- FaradaIQ canonical bundle v145: vendor technique catalog fixes ----
# v145 fixes non-HEKA vendor exports where generic *_for_plotting_analysis folders
# were sometimes mistaken for HEKA exports, and adds first-class technique cataloging
# for PalmSens DPV, Solartron ConstV/Cond, BioLogic GCPL/charge-discharge, CA/CP/OCP.
VERSION = "v331"
SCHEMA = "faradaiq-canonical-h5-bundle-v7"

_V145_PREV_IS_HEKA_EXPORT_ROOT = is_heka_export_root

def is_heka_export_root(root: Path) -> bool:
    root = Path(root)
    low = root.as_posix().lower()
    # True HEKA/POTMASTER/PATCHMASTER evidence only.  Do not use the generic
    # *_for_plotting_analysis folder name because PalmSens/Solartron/BioLogic
    # exports also use it.
    return bool(
        list(root.rglob("*heka_sequence_manifest.json"))
        or list(root.rglob("data_by_point_sequence/point_*/point_manifest.json"))
        or list(root.rglob("*_heka_trace_index.csv"))
        or list(root.rglob("*_heka_method_entries.csv"))
        or "potmaster" in low or "patchmaster" in low
    )


def _v145_text_for_inference(path: Path, columns: Sequence[str]) -> str:
    return (path.name + " " + path.as_posix() + " " + " ".join(map(str, columns))).lower()


def infer_technique_nonheka(path: Path, columns: Sequence[str]) -> Tuple[str, str]:
    text = _v145_text_for_inference(path, columns)
    # Exclude helper/readme files from becoming numeric/false technique tables.
    if path.name.lower().startswith("readme") or "readme_" in path.name.lower():
        return "Metadata", "README/helper text"
    if "drt" in text and "drt_input" not in text:
        return "DRT", "analyzed DRT table"
    if any(t in text for t in ["zreal", "zimag", "real(z)", "imag(z)", "realz", "imagz", "impedance", "nyquist", "bode", "frequency_hz", "freq/hz", "phase(z)"]):
        return "EIS", "EIS impedance table for Nyquist/Bode"
    if re.search(r"(^|[_\-/\s])dpv($|[_\-/\s])", text) or "differential pulse" in text:
        return "DPV", "Differential pulse voltammetry table"
    if re.search(r"(^|[_\-/\s])swv($|[_\-/\s])", text) or "square wave" in text:
        return "SWV", "Square wave voltammetry table"
    if re.search(r"(^|[_\-/\s])npv($|[_\-/\s])", text) or "normal pulse" in text:
        return "NPV", "Normal pulse voltammetry table"
    if any(t in text for t in ["tafel", "polarization", "polarisation", "current-voltage", "current_vs_potential", "iv_", "_iv", " lsv", "_lsv", "-lsv"]):
        return "LSV/IV/Tafel", "IV/LSV/Tafel current-potential table"
    if any(t in text for t in ["cyclic voltam", "_cv_", " cv_", "-cv-", "_cv", "cv_i_vs_e", "cycle_"]):
        return "CV / LSV", "CV/LSV current-potential table"
    if any(t in text for t in ["gcpl", "gplc", "charge/discharge", "charge_discharge", "discharge", "capacity", "galvanostatic cycling", "potential limitation", "cccv", "c/", "cp10", "cp20"]):
        return "Charge/Discharge", "Battery galvanostatic charge/discharge table"
    if any(t in text for t in ["chronopot", "constant current", "cstc", "_cp_", " cp_"]):
        return "Chronopotentiometry / CP", "Chronopotentiometry table"
    if any(t in text for t in ["chronoamper", "constant voltage", "constv", "cstv", "_ca_", " ca_"]):
        if "cond" in text or "conduct" in text:
            return "Conductivity / constant voltage", "Constant-voltage conductivity table"
        return "Chronoamperometry / CA", "Chronoamperometry table"
    if any(t in text for t in ["ocp", "open circuit", "open_circuit", "open-circuit"]):
        return "OCP", "Open-circuit potential table"
    if any(t in text for t in ["potential", "voltage", "current", "ewe", "<i>", "i/ma", "i_a"]):
        return "CV / LSV", "Generic current-potential table"
    return "Numeric", "Generic numeric table"


def _v145_extract_meta_from_nonheka_path(path: Path, technique: str, df=None) -> Dict[str, Any]:
    s = path.as_posix()
    low = s.lower()
    meta: Dict[str, Any] = {}
    # scan rate patterns used by PalmSens/Solartron/BioLogic exports.
    for pat in [r"(\d+(?:\.\d+)?)\s*mV\s*/?\s*s", r"(\d+(?:\.\d+)?)\s*mVs", r"(\d+(?:\.\d+)?)\s*mV_s"]:
        m = re.search(pat, s, flags=re.I)
        if m:
            try:
                meta["scan_rate_mV_s"] = float(m.group(1)); meta["scan_rate_label"] = _clean_rate_label(meta["scan_rate_mV_s"])
                break
            except Exception:
                pass
    m = re.search(r"(?:cycle_|scan_)(\d+)", s, flags=re.I)
    if m:
        try: meta["cycle"] = int(m.group(1))
        except Exception: pass
    if "cycle" not in meta:
        m = re.search(r"__c(\d{3})__", s, flags=re.I)
        if m:
            try: meta["cycle"] = int(m.group(1))
            except Exception: pass
    m = re.search(r"(?:point|pt|p)[_\- ]*(\d{1,4})", s, flags=re.I)
    if m:
        try: meta["point"] = int(m.group(1))
        except Exception: pass
    # Extract applied EIS potential from filenames like EIS_-0p5V or EIS_0V.
    if technique == "EIS":
        m = re.search(r"EIS[_\- ]+([\-+]?\d+(?:p\d+|\.\d+)?)V", s, flags=re.I)
        if m:
            txt = m.group(1).replace('p','.')
            try: meta["dc_potential_V"] = float(txt)
            except Exception: pass
    if technique in {"DPV", "SWV", "NPV"}:
        meta.setdefault("pulse_voltammetry", True)
    if technique == "Charge/Discharge":
        meta.setdefault("battery_cycling", True)
    return meta


def _include_nonheka(path: Path) -> bool:
    if not path.is_file(): return False
    low_name = path.name.lower()
    low = path.as_posix().lower()
    parts = set(_parts_lower(path))
    if any(x in EXCLUDED_DIR_TOKENS for x in parts): return False
    if "faradaiq_bundle" in low or "files_snapshot" in low or ".browser_cache_from_h5" in low: return False
    if low_name.startswith("readme") or "readme_drt_input" in low_name: return False
    if "drt_input_csv" in low and "eis" not in low and "zreal" not in low and "zimag" not in low: return False
    if path.suffix.lower() in TABLE_EXTS | JSON_EXTS: return True
    if path.suffix.lower() in MEDIA_EXTS:
        return "photo" in low or "gif" in low or "image" in low
    return False


def _v145_standardize_nonheka(df, path: Path, tech: str):
    if _preserve_bundle_units():return df.apply(_numeric_bundle_column).dropna(axis=0,how='all')
    if tech == "EIS":
        return _standardize_eis(df, path)
    if tech in {"CV / LSV", "LSV/IV/Tafel", "DPV", "SWV", "NPV"}:
        out = _standardize_cv(df, path)
        # Headerless PalmSens/Solartron two-column files often fall through with
        # numeric column names.  Rename first two numeric columns for plotting.
        if list(map(str, out.columns))[:2] not in [["potential_V", "current_A"], ["time_s", "current_A"]] and out.shape[1] >= 2:
            cols = list(out.columns)
            tmp = pd.DataFrame()
            tmp["potential_V"] = pd.to_numeric(out[cols[0]], errors="coerce")
            tmp["current_A"] = pd.to_numeric(out[cols[1]], errors="coerce")
            if out.shape[1] > 2:
                tmp["aux_2"] = pd.to_numeric(out[cols[2]], errors="coerce")
            return tmp.dropna(axis=0, how="all")
        return out
    if tech in {"Chronoamperometry / CA", "Chronopotentiometry / CP", "OCP", "Charge/Discharge", "Conductivity / constant voltage"}:
        # Preserve all numeric columns but normalize common BioLogic names.
        cols = {str(c).lower(): c for c in df.columns}
        out = pd.DataFrame()
        for target, toks in {
            "time_s": ["time/s", "time", "t/s"],
            "potential_V": ["ewe/v", "<ewe>/v", "potential", "voltage", "e/v"],
            "current_A": ["<i>/ma", "i/ma", "current", "i_a", "i/a"],
            "capacity_mAh": ["capacity", "q discharge", "q charge", "mah"],
            "cycle": ["cycle number", "cycle"],
        }.items():
            for c in df.columns:
                l = str(c).lower()
                if any(t in l for t in toks):
                    vals = pd.to_numeric(df[c], errors="coerce")
                    if target == "current_A" and ("ma" in l and "/ma" in l): vals = vals * 1e-3
                    out[target] = vals; break
        if out.shape[1] >= 2:
            return out.dropna(axis=0, how="all")
    return df.apply(lambda col: pd.to_numeric(col, errors="coerce")).dropna(axis=0, how="all")


def _collect_nonheka_records(root: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    diagnostics: Dict[str, Any] = {"v145_nonheka_catalog": True}
    for p in sorted(root.rglob("*"), key=lambda x: x.as_posix().lower()):
        if not _include_nonheka(p): continue
        if p.suffix.lower() in TABLE_EXTS:
            df, cols, err = _read_table_general(p)
            if df is None: continue
            tech, role = infer_technique_nonheka(p, cols)
            if tech == "Metadata":
                continue
            sdf = _v145_standardize_nonheka(df, p, tech)
            if sdf is None or getattr(sdf, 'empty', True):
                continue
            meta = _v145_extract_meta_from_nonheka_path(p, tech, sdf)
            meta.setdefault("canonical_key", f"{safe_name(tech)}:{relpath(p, root)}")
            records.append(_make_table_record(p, root, tech, role, sdf, **meta))
        elif p.suffix.lower() in JSON_EXTS:
            records.append({"source_path": str(p), "relative_path": relpath(p, root), "name": p.name, "suffix": p.suffix.lower(), "size_bytes": int(p.stat().st_size), "modified_time": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"), "record_type": "metadata", "technique": "Metadata", "role": "metadata_json", "included_in_h5": True})
        elif p.suffix.lower() in MEDIA_EXTS:
            records.append({"source_path": str(p), "relative_path": relpath(p, root), "name": p.name, "suffix": p.suffix.lower(), "size_bytes": int(p.stat().st_size), "modified_time": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"), "record_type": "media", "technique": "Media / photos", "role": "media", "included_in_h5": True})
    final=[]; seen=set(); dups=[]
    for r in records:
        key = r.get("canonical_key") or (r.get("record_type"), r.get("relative_path"))
        if key in seen:
            dups.append(r.get("relative_path")); continue
        seen.add(key); final.append(r)
    info = _build_info(final, final, {}, is_heka=False, duplicates=dups, diagnostics=diagnostics)
    return final, info


def collect_records(analysis_root: Path, base_name: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    root = Path(analysis_root).resolve()
    if is_heka_export_root(root):
        return _collect_heka_records(root)
    return _collect_nonheka_records(root)


def _cache_subdir_for_record(cache: Path, rec: Dict[str, Any]) -> Path:
    tech = safe_name(rec.get("technique") or "Numeric")
    if rec.get("technique") == "CV / LSV":
        rate = _clean_rate_label(rec.get("scan_rate_mV_s"))
        point = rec.get("point", "unassigned")
        cyc = int(rec.get("cycle", 0) or 0)
        ptxt = f"point_{int(point):03d}" if isinstance(point, (int, float)) else "point_unassigned"
        return cache / "CV_LSV" / f"scanrate_{rate}" / ptxt / f"cycle_{cyc:02d}"
    if rec.get("technique") in {"DPV", "SWV", "NPV"}:
        rate = _clean_rate_label(rec.get("scan_rate_mV_s"))
        return cache / safe_name(rec.get("technique")) / f"scanrate_{rate}"
    if rec.get("technique") == "EIS":
        point = rec.get("point", "unassigned")
        pot = rec.get("dc_potential_V")
        if pot is not None:
            return cache / "EIS" / f"potential_{str(pot).replace('.', 'p').replace('-', 'neg')}V"
        ptxt = f"point_{int(point):03d}" if isinstance(point, (int, float)) else "point_unassigned"
        return cache / "EIS" / ptxt
    if rec.get("technique") == "DRT":
        point = rec.get("point", "unassigned")
        run = safe_name(rec.get("drt_run_id") or "DRT")
        ptxt = f"point_{int(point):03d}" if isinstance(point, (int, float)) else "point_unassigned"
        return cache / "DRT" / run / ptxt
    return cache / tech
# ---- End v145 vendor technique catalog fixes ----

# ---- FaradaIQ v290: stricter filename-first non-HEKA technique inference ----
def _include_nonheka(path: Path) -> bool:
    if not path.is_file(): return False
    low_name = path.name.lower()
    low = path.as_posix().lower()
    parts = set(_parts_lower(path))
    if any(x in EXCLUDED_DIR_TOKENS for x in parts): return False
    if "faradaiq_bundle" in low or "files_snapshot" in low or ".browser_cache_from_h5" in low: return False
    if low_name.startswith("readme") or "readme_drt_input" in low_name: return False
    # DRT input files are derived duplicates for later DRT execution, not distinct
    # experimental data for the canonical browser bundle.
    if "drt_input_csv" in low: return False
    if path.suffix.lower() in TABLE_EXTS | JSON_EXTS: return True
    if path.suffix.lower() in MEDIA_EXTS:
        return "photo" in low or "gif" in low or "image" in low
    return False


def infer_technique_nonheka(path: Path, columns: Sequence[str]) -> Tuple[str, str]:
    name = path.name.lower()
    stem = path.stem.lower()
    full = _v145_text_for_inference(path, columns)
    cols_text = " ".join(map(str, columns)).lower()
    if name.startswith("readme") or "readme_" in name:
        return "Metadata", "README/helper text"
    if "drt" in full and "drt_input" not in full:
        return "DRT", "analyzed DRT table"
    if any(t in (stem + " " + cols_text) for t in ["zreal", "zimag", "real(z)", "imag(z)", "realz", "imagz", "impedance", "nyquist", "bode", "frequency_hz", "freq/hz", "phase(z)"]):
        return "EIS", "EIS impedance table for Nyquist/Bode"
    # Filename/stem wins over parent folder names such as CV_DPV.
    if re.search(r"(^|[_\-\s])dpv($|[_\-\s])", stem) or "differential pulse" in stem:
        return "DPV", "Differential pulse voltammetry table"
    if re.search(r"(^|[_\-\s])swv($|[_\-\s])", stem) or "square wave" in stem:
        return "SWV", "Square wave voltammetry table"
    if re.search(r"(^|[_\-\s])npv($|[_\-\s])", stem) or "normal pulse" in stem:
        return "NPV", "Normal pulse voltammetry table"
    if re.search(r"(^|[_\-\s])cv($|[_\-\s])", stem) or "cv_i_vs_e" in stem or "cyclic voltam" in stem or "cycle_" in stem:
        return "CV / LSV", "CV/LSV current-potential table"
    if re.search(r"(^|[_\-\s])lsv($|[_\-\s])", stem):
        return "LSV/IV/Tafel", "LSV current-potential table"
    if any(t in stem for t in ["tafel", "polarization", "polarisation", "current-voltage", "current_vs_potential", "_iv", "iv_"]):
        return "LSV/IV/Tafel", "IV/LSV/Tafel current-potential table"
    if any(t in stem for t in ["constv", "constant_voltage", "cstv", "_ca", "ca_"]):
        if "cond" in stem or "conduct" in stem:
            return "Conductivity / constant voltage", "Constant-voltage conductivity table"
        return "Chronoamperometry / CA", "Chronoamperometry table"
    if any(t in stem for t in ["chronopot", "constant_current", "cstc", "_cp", "cp_"]):
        # BioLogic Cp10/Cp20 battery files are charge/discharge, not CP line scans.
        if re.search(r"cp\d+", stem) or "gcpl" in full or "gplc" in full:
            return "Charge/Discharge", "Battery galvanostatic charge/discharge table"
        return "Chronopotentiometry / CP", "Chronopotentiometry table"
    if any(t in full for t in ["gcpl", "gplc", "charge/discharge", "charge_discharge", "galvanostatic cycling", "potential limitation", "cccv"]):
        return "Charge/Discharge", "Battery galvanostatic charge/discharge table"
    if any(t in stem for t in ["ocp", "open_circuit", "open-circuit"]):
        return "OCP", "Open-circuit potential table"
    if any(t in cols_text for t in ["freq/hz", "re(z)", "im(z)", "phase(z)"]):
        return "EIS", "EIS impedance table"
    if any(t in cols_text for t in ["capacity", "q charge", "q discharge"]):
        return "Charge/Discharge", "Battery charge/discharge table"
    if any(t in cols_text for t in ["ewe/v", "<i>/ma", "i/ma", "potential", "current"]):
        return "CV / LSV", "Generic current-potential table"
    return "Numeric", "Generic numeric table"
# ---- End v145.1 ----

# ---- FaradaIQ v290: OCV/symmetric-cell BioLogic name fixes ----
def infer_technique_nonheka(path: Path, columns: Sequence[str]) -> Tuple[str, str]:
    name = path.name.lower(); stem = path.stem.lower(); full = _v145_text_for_inference(path, columns); cols_text = " ".join(map(str, columns)).lower()
    if name.startswith("readme") or "readme_" in name: return "Metadata", "README/helper text"
    if "drt" in full and "drt_input" not in full: return "DRT", "analyzed DRT table"
    if any(t in (stem + " " + cols_text) for t in ["zreal", "zimag", "real(z)", "imag(z)", "realz", "imagz", "impedance", "nyquist", "bode", "frequency_hz", "freq/hz", "phase(z)"]): return "EIS", "EIS impedance table for Nyquist/Bode"
    if any(t in stem for t in ["ocp", "ocv", "open_circuit", "open-circuit"]): return "OCP", "Open-circuit potential table"
    if re.search(r"(^|[_\-\s])dpv($|[_\-\s])", stem) or "differential pulse" in stem: return "DPV", "Differential pulse voltammetry table"
    if re.search(r"(^|[_\-\s])swv($|[_\-\s])", stem) or "square wave" in stem: return "SWV", "Square wave voltammetry table"
    if re.search(r"(^|[_\-\s])npv($|[_\-\s])", stem) or "normal pulse" in stem: return "NPV", "Normal pulse voltammetry table"
    if any(t in full for t in ["gcpl", "gplc", "charge/discharge", "charge_discharge", "galvanostatic cycling", "potential limitation", "cccv"]): return "Charge/Discharge", "Battery galvanostatic charge/discharge table"
    if re.search(r"(^|[_\-\s])sym($|[_\-\s])", stem) and re.search(r"\d+\s*u?a", stem): return "Charge/Discharge", "Symmetric-cell constant-current cycling table"
    if re.search(r"(^|[_\-\s])cv($|[_\-\s])", stem) or "cv_i_vs_e" in stem or "cyclic voltam" in stem or "cycle_" in stem: return "CV / LSV", "CV/LSV current-potential table"
    if re.search(r"(^|[_\-\s])lsv($|[_\-\s])", stem): return "LSV/IV/Tafel", "LSV current-potential table"
    if any(t in stem for t in ["tafel", "polarization", "polarisation", "current-voltage", "current_vs_potential", "_iv", "iv_"]): return "LSV/IV/Tafel", "IV/LSV/Tafel current-potential table"
    if any(t in stem for t in ["constv", "constant_voltage", "cstv", "_ca", "ca_"]):
        if "cond" in stem or "conduct" in stem: return "Conductivity / constant voltage", "Constant-voltage conductivity table"
        return "Chronoamperometry / CA", "Chronoamperometry table"
    if any(t in stem for t in ["chronopot", "constant_current", "cstc", "_cp", "cp_"]):
        if re.search(r"cp\d+", stem): return "Charge/Discharge", "Battery galvanostatic charge/discharge table"
        return "Chronopotentiometry / CP", "Chronopotentiometry table"
    if any(t in cols_text for t in ["freq/hz", "re(z)", "im(z)", "phase(z)"]): return "EIS", "EIS impedance table"
    if any(t in cols_text for t in ["capacity", "q charge", "q discharge"]): return "Charge/Discharge", "Battery charge/discharge table"
    if any(t in cols_text for t in ["ewe/v", "<i>/ma", "i/ma", "potential", "current"]): return "CV / LSV", "Generic current-potential table"
    return "Numeric", "Generic numeric table"
# ---- End v145.2 ----

# ---- FaradaIQ v290: use export manifest technique metadata for non-HEKA vendor bundles ----
def _v145_find_export_manifest_for_root(root: Path) -> Dict[str, Dict[str, Any]]:
    """Return relative_csv_path -> manifest entry, if the parent export manifest exists.

    Non-HEKA vendor exports can have generic-looking CSV filenames, especially BioLogic
    and PalmSens.  The export manifest records the vendor-reported technique and curve
    titles, so use it as the authoritative catalog when available.
    """
    root = Path(root)
    candidates = []
    try:
        safe = safe_name(root.name.replace("_analysis_ready_csv", ""))
    except Exception:
        safe = ""
    for base in [root.parent, root]:
        try:
            patterns = ["*_analysis_ready_manifest.json", "*_manifest.json"]
            if safe:
                patterns.insert(0, f"{safe}*_analysis_ready_manifest.json")
            for pat in patterns:
                candidates.extend([p for p in base.glob(pat) if p.is_file() and not p.name.endswith("_faradaiq_bundle.json")])
        except Exception:
            pass
    out: Dict[str, Dict[str, Any]] = {}
    for p in sorted(set(candidates), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        obj = _json_load(p)
        if not isinstance(obj, dict):
            continue
        entries = obj.get("csv_entries") or obj.get("records") or []
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            rel = str(e.get("relative_csv_path") or e.get("relative_path") or "").replace("\\", "/")
            if rel and rel not in out:
                out[rel] = e
        if out:
            break
    return out


def _v145_canonical_from_manifest_entry(entry: Dict[str, Any], path: Path, cols: Sequence[str]) -> Tuple[str, str]:
    tech_raw = str(entry.get("technique") or entry.get("method") or entry.get("method_title") or entry.get("curve_title") or entry.get("measurement_title") or "")
    title_raw = " ".join(str(entry.get(k) or "") for k in ["curve_title", "measurement_title", "method_id", "relative_source_path", "relative_csv_path"])
    low = (tech_raw + " " + title_raw + " " + path.name).lower()
    # Highly specific techniques first.
    if any(x in low for x in ["dpv", "differential pulse"]):
        return "DPV", "Differential pulse voltammetry table"
    if any(x in low for x in ["swv", "square wave"]):
        return "SWV", "Square wave voltammetry table"
    if any(x in low for x in ["npv", "normal pulse"]):
        return "NPV", "Normal pulse voltammetry table"
    if any(x in low for x in ["peis", "geis", "eis", "impedance", "nyquist", "bode"]):
        return "EIS", "EIS impedance table for Nyquist/Bode"
    if any(x in low for x in ["gcpl", "gplc", "galvanostatic cycling", "potential limitation", "charge/discharge", "charge discharge", "cccv"]):
        return "Charge/Discharge", "Battery galvanostatic charge/discharge table"
    # BioLogic CA/OCP/CP and generic vendor names.
    if any(x in low for x in ["ocp", "ocv", "open circuit", "open-circuit"]):
        return "OCP", "Open-circuit potential table"
    if any(x in low for x in ["chronoamper", " ca ", "_ca", "constv", "constant voltage"]):
        if "cond" in low or "conduct" in low:
            return "Conductivity / constant voltage", "Constant-voltage conductivity table"
        return "Chronoamperometry / CA", "Chronoamperometry table"
    if any(x in low for x in ["chronopot", " cp ", "_cp", "constant current"]):
        return "Chronopotentiometry / CP", "Chronopotentiometry table"
    if any(x in low for x in ["lsv", "linear sweep"]):
        return "LSV/IV/Tafel", "LSV current-potential table"
    if any(x in low for x in ["cv", "cyclic voltam", "current-potential"]):
        return "CV / LSV", "CV/LSV current-potential table"
    return infer_technique_nonheka(path, cols)


def _collect_nonheka_records(root: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    diagnostics: Dict[str, Any] = {"v145_nonheka_catalog": True, "manifest_assisted_technique_classification": True}
    manifest_map = _v145_find_export_manifest_for_root(root)
    diagnostics["manifest_entries_used"] = len(manifest_map)
    for p in sorted(root.rglob("*"), key=lambda x: x.as_posix().lower()):
        if not _include_nonheka(p):
            continue
        if p.suffix.lower() in TABLE_EXTS:
            df, cols, err = _read_table_general(p)
            if df is None:
                continue
            rel = relpath(p, root).replace("\\", "/")
            mentry = manifest_map.get(rel)
            if mentry:
                tech, role = _v145_canonical_from_manifest_entry(mentry, p, cols)
            else:
                tech, role = infer_technique_nonheka(p, cols)
            if tech == "Metadata":
                continue
            sdf = _v145_standardize_nonheka(df, p, tech)
            if sdf is None or getattr(sdf, "empty", True):
                continue
            meta = _v145_extract_meta_from_nonheka_path(p, tech, sdf)
            if mentry:
                for src_key, dst_key in {
                    "entry_id": "entry_id", "relative_source_path": "relative_source_path", "measurement_index": "measurement_index",
                    "curve_index": "curve_index", "measurement_title": "measurement_title", "curve_title": "curve_title",
                    "method_id": "method_id", "scan_rate": "scan_rate_raw", "x_label": "x_label", "x_unit": "x_unit",
                    "y_label": "y_label", "y_unit": "y_unit",
                }.items():
                    if mentry.get(src_key) not in (None, ""):
                        meta[dst_key] = mentry.get(src_key)
                # Preserve scan rate from manifest if path inference did not find it.
                if meta.get("scan_rate_mV_s") is None:
                    sr = mentry.get("scan_rate")
                    try:
                        if isinstance(sr, str):
                            mm = re.search(r"([0-9]+(?:\.[0-9]+)?)", sr.replace("p", "."))
                            if mm: meta["scan_rate_mV_s"] = float(mm.group(1))
                        elif sr not in (None, ""):
                            meta["scan_rate_mV_s"] = float(sr)
                    except Exception:
                        pass
            # File-specific key avoids accidental duplication while keeping one table per exported curve.
            meta.setdefault("canonical_key", f"{safe_name(tech)}:{rel}")
            records.append(_make_table_record(p, root, tech, role, sdf, **meta))
        elif p.suffix.lower() in JSON_EXTS:
            records.append({"source_path": str(p), "relative_path": relpath(p, root), "name": p.name, "suffix": p.suffix.lower(), "size_bytes": int(p.stat().st_size), "modified_time": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"), "record_type": "metadata", "technique": "Metadata", "role": "metadata_json", "included_in_h5": True})
        elif p.suffix.lower() in MEDIA_EXTS:
            records.append({"source_path": str(p), "relative_path": relpath(p, root), "name": p.name, "suffix": p.suffix.lower(), "size_bytes": int(p.stat().st_size), "modified_time": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"), "record_type": "media", "technique": "Media / photos", "role": "media", "included_in_h5": True})
    final=[]; seen=set(); dups=[]
    for r in records:
        key = r.get("canonical_key") or (r.get("record_type"), r.get("relative_path"))
        if key in seen:
            dups.append(r.get("relative_path")); continue
        seen.add(key); final.append(r)
    info = _build_info(final, final, {}, is_heka=False, duplicates=dups, diagnostics=diagnostics)
    return final, info
# ---- End v145.3 ----

# ---- FaradaIQ v290: avoid parent-folder technique leakage from manifest paths ----
def _v145_canonical_from_manifest_entry(entry: Dict[str, Any], path: Path, cols: Sequence[str]) -> Tuple[str, str]:
    # Use the exported filename plus explicit manifest fields.  Do NOT let parent
    # folders like "CV_DPV" classify every child file as DPV.
    explicit = " ".join(str(entry.get(k) or "") for k in ["technique", "curve_title", "measurement_title", "method_id", "x_label", "y_label"])
    low = (explicit + " " + path.name).lower()
    if any(x in low for x in ["differential pulse", " dpv ", "_dpv", "dpv_", "-dpv", "dpv-"]): return "DPV", "Differential pulse voltammetry table"
    if any(x in low for x in ["square wave", " swv ", "_swv", "swv_", "-swv", "swv-"]): return "SWV", "Square wave voltammetry table"
    if any(x in low for x in ["normal pulse", " npv ", "_npv", "npv_", "-npv", "npv-"]): return "NPV", "Normal pulse voltammetry table"
    if any(x in low for x in ["peis", "geis", " eis ", "_eis", "eis_", "impedance", "nyquist", "bode"]): return "EIS", "EIS impedance table for Nyquist/Bode"
    if any(x in low for x in ["gcpl", "gplc", "galvanostatic cycling", "potential limitation", "charge/discharge", "charge discharge", "cccv"]): return "Charge/Discharge", "Battery galvanostatic charge/discharge table"
    if any(x in low for x in ["ocp", "ocv", "open circuit", "open-circuit"]): return "OCP", "Open-circuit potential table"
    if any(x in low for x in ["chronoamper", " ca ", "_ca", "ca_", "constv", "constant voltage"]):
        if "cond" in low or "conduct" in low: return "Conductivity / constant voltage", "Constant-voltage conductivity table"
        return "Chronoamperometry / CA", "Chronoamperometry table"
    if any(x in low for x in ["chronopot", " cp ", "_cp", "cp_", "constant current"]): return "Chronopotentiometry / CP", "Chronopotentiometry table"
    if any(x in low for x in ["linear sweep", " lsv ", "_lsv", "lsv_"]): return "LSV/IV/Tafel", "LSV current-potential table"
    if any(x in low for x in ["cyclic voltam", " cv ", "_cv", "cv_", "current-potential", "current_vs_potential"]): return "CV / LSV", "CV/LSV current-potential table"
    return infer_technique_nonheka(path, cols)
# ---- End v145.4 ----

# ---- FaradaIQ v290: manifest fields beat filename prefixes such as "CV_EIS" ----
def _v145_canonical_from_manifest_entry(entry: Dict[str, Any], path: Path, cols: Sequence[str]) -> Tuple[str, str]:
    explicit = " ".join(str(entry.get(k) or "") for k in ["technique", "curve_title", "measurement_title", "method_id", "x_label", "y_label"]).strip()
    # If the manifest has explicit method/curve fields, trust those only; filenames
    # often contain broad labels like *_CV_EIS_* that should not classify every curve.
    low = explicit.lower() if explicit else path.name.lower()
    if any(x in low for x in ["differential pulse", " dpv ", "_dpv", "dpv_", "-dpv", "dpv-"]): return "DPV", "Differential pulse voltammetry table"
    if any(x in low for x in ["square wave", " swv ", "_swv", "swv_", "-swv", "swv-"]): return "SWV", "Square wave voltammetry table"
    if any(x in low for x in ["normal pulse", " npv ", "_npv", "npv_", "-npv", "npv-"]): return "NPV", "Normal pulse voltammetry table"
    if any(x in low for x in ["cyclic voltam", " cv ", "_cv", "cv_", "current-potential", "current_vs_potential", "versastudio_cv"]): return "CV / LSV", "CV/LSV current-potential table"
    if any(x in low for x in ["linear sweep", " lsv ", "_lsv", "lsv_"]): return "LSV/IV/Tafel", "LSV current-potential table"
    if any(x in low for x in ["peis", "geis", " eis ", "_eis", "eis_", "versastudio_eis", "potentiostatic eis", "impedance", "nyquist", "bode"]): return "EIS", "EIS impedance table for Nyquist/Bode"
    if any(x in low for x in ["gcpl", "gplc", "galvanostatic cycling", "potential limitation", "charge/discharge", "charge discharge", "cccv"]): return "Charge/Discharge", "Battery galvanostatic charge/discharge table"
    if any(x in low for x in ["ocp", "ocv", "open circuit", "open-circuit"]): return "OCP", "Open-circuit potential table"
    if any(x in low for x in ["chronoamper", " ca ", "_ca", "ca_", "constv", "constant voltage"]):
        if "cond" in low or "conduct" in low: return "Conductivity / constant voltage", "Constant-voltage conductivity table"
        return "Chronoamperometry / CA", "Chronoamperometry table"
    if any(x in low for x in ["chronopot", " cp ", "_cp", "cp_", "constant current"]): return "Chronopotentiometry / CP", "Chronopotentiometry table"
    return infer_technique_nonheka(path, cols)
# ---- End v145.5 ----

# ---- FaradaIQ v290: preserve conductivity/ConstV labels when manifest calls it CA ----
def _v145_canonical_from_manifest_entry(entry: Dict[str, Any], path: Path, cols: Sequence[str]) -> Tuple[str, str]:
    explicit = " ".join(str(entry.get(k) or "") for k in ["technique", "curve_title", "measurement_title", "method_id", "x_label", "y_label"]).strip()
    context = " ".join(str(entry.get(k) or "") for k in ["relative_source_path", "relative_csv_path"]) + " " + path.name
    low = explicit.lower() if explicit else path.name.lower()
    ctx = context.lower()
    if any(x in low for x in ["differential pulse", " dpv ", "_dpv", "dpv_", "-dpv", "dpv-"]): return "DPV", "Differential pulse voltammetry table"
    if any(x in low for x in ["square wave", " swv ", "_swv", "swv_", "-swv", "swv-"]): return "SWV", "Square wave voltammetry table"
    if any(x in low for x in ["normal pulse", " npv ", "_npv", "npv_", "-npv", "npv-"]): return "NPV", "Normal pulse voltammetry table"
    if any(x in low for x in ["cyclic voltam", " cv ", "_cv", "cv_", "current-potential", "current_vs_potential", "versastudio_cv"]): return "CV / LSV", "CV/LSV current-potential table"
    if any(x in low for x in ["linear sweep", " lsv ", "_lsv", "lsv_"]): return "LSV/IV/Tafel", "LSV current-potential table"
    if any(x in low for x in ["peis", "geis", " eis ", "_eis", "eis_", "versastudio_eis", "potentiostatic eis", "impedance", "nyquist", "bode"]): return "EIS", "EIS impedance table for Nyquist/Bode"
    if any(x in low for x in ["gcpl", "gplc", "galvanostatic cycling", "potential limitation", "charge/discharge", "charge discharge", "cccv"]): return "Charge/Discharge", "Battery galvanostatic charge/discharge table"
    if any(x in low for x in ["ocp", "ocv", "open circuit", "open-circuit"]): return "OCP", "Open-circuit potential table"
    if any(x in low for x in ["chronoamper", " ca ", "_ca", "ca_", "constv", "constant voltage"]):
        if "cond" in ctx or "conduct" in ctx: return "Conductivity / constant voltage", "Constant-voltage conductivity table"
        return "Chronoamperometry / CA", "Chronoamperometry table"
    if any(x in low for x in ["chronopot", " cp ", "_cp", "cp_", "constant current"]): return "Chronopotentiometry / CP", "Chronopotentiometry table"
    return infer_technique_nonheka(path, cols)
# ---- End v145.6 ----

# ---- FaradaIQ v290: BioLogic CP symmetric-cell cycling classification ----
def _v145_canonical_from_manifest_entry(entry: Dict[str, Any], path: Path, cols: Sequence[str]) -> Tuple[str, str]:
    explicit = " ".join(str(entry.get(k) or "") for k in ["technique", "curve_title", "measurement_title", "method_id", "x_label", "y_label"]).strip()
    context = " ".join(str(entry.get(k) or "") for k in ["relative_source_path", "relative_csv_path"]) + " " + path.name
    low = explicit.lower() if explicit else path.name.lower()
    ctx = context.lower()
    if any(x in low for x in ["differential pulse", " dpv ", "_dpv", "dpv_", "-dpv", "dpv-"]): return "DPV", "Differential pulse voltammetry table"
    if any(x in low for x in ["square wave", " swv ", "_swv", "swv_", "-swv", "swv-"]): return "SWV", "Square wave voltammetry table"
    if any(x in low for x in ["normal pulse", " npv ", "_npv", "npv_", "-npv", "npv-"]): return "NPV", "Normal pulse voltammetry table"
    if any(x in low for x in ["cyclic voltam", " cv ", "_cv", "cv_", "current-potential", "current_vs_potential", "versastudio_cv"]): return "CV / LSV", "CV/LSV current-potential table"
    if any(x in low for x in ["linear sweep", " lsv ", "_lsv", "lsv_"]): return "LSV/IV/Tafel", "LSV current-potential table"
    if any(x in low for x in ["peis", "geis", " eis ", "_eis", "eis_", "versastudio_eis", "potentiostatic eis", "impedance", "nyquist", "bode"]): return "EIS", "EIS impedance table for Nyquist/Bode"
    if any(x in low for x in ["gcpl", "gplc", "galvanostatic cycling", "potential limitation", "charge/discharge", "charge discharge", "cccv"]): return "Charge/Discharge", "Battery galvanostatic charge/discharge table"
    if any(x in low for x in ["ocp", "ocv", "open circuit", "open-circuit"]): return "OCP", "Open-circuit potential table"
    if any(x in low for x in ["chronoamper", " ca ", "_ca", "ca_", "constv", "constant voltage"]):
        if "cond" in ctx or "conduct" in ctx: return "Conductivity / constant voltage", "Constant-voltage conductivity table"
        return "Chronoamperometry / CA", "Chronoamperometry table"
    if any(x in low for x in ["chronopot", " cp ", "_cp", "cp_", "constant current"]):
        if any(x in ctx for x in ["sym_", "symmetric", "for_xps", "cycling", "gcpl", "gplc", "ua_", "ma_", "_10min", "charge", "discharge"]):
            return "Charge/Discharge", "Battery/symmetric-cell constant-current cycling table"
        return "Chronopotentiometry / CP", "Chronopotentiometry table"
    return infer_technique_nonheka(path, cols)
# ---- End v145.7 ----

# ---- FaradaIQ v290: battery cathode CP naming -> Charge/Discharge ----
def _v145_canonical_from_manifest_entry(entry: Dict[str, Any], path: Path, cols: Sequence[str]) -> Tuple[str, str]:
    explicit = " ".join(str(entry.get(k) or "") for k in ["technique", "curve_title", "measurement_title", "method_id", "x_label", "y_label"]).strip()
    context = " ".join(str(entry.get(k) or "") for k in ["relative_source_path", "relative_csv_path"]) + " " + path.name
    low = explicit.lower() if explicit else path.name.lower(); ctx = context.lower()
    if any(x in low for x in ["differential pulse", " dpv ", "_dpv", "dpv_", "-dpv", "dpv-"]): return "DPV", "Differential pulse voltammetry table"
    if any(x in low for x in ["square wave", " swv ", "_swv", "swv_", "-swv", "swv-"]): return "SWV", "Square wave voltammetry table"
    if any(x in low for x in ["normal pulse", " npv ", "_npv", "npv_", "-npv", "npv-"]): return "NPV", "Normal pulse voltammetry table"
    if any(x in low for x in ["cyclic voltam", " cv ", "_cv", "cv_", "current-potential", "current_vs_potential", "versastudio_cv"]): return "CV / LSV", "CV/LSV current-potential table"
    if any(x in low for x in ["linear sweep", " lsv ", "_lsv", "lsv_"]): return "LSV/IV/Tafel", "LSV current-potential table"
    if any(x in low for x in ["peis", "geis", " eis ", "_eis", "eis_", "versastudio_eis", "potentiostatic eis", "impedance", "nyquist", "bode"]): return "EIS", "EIS impedance table for Nyquist/Bode"
    if any(x in low for x in ["gcpl", "gplc", "galvanostatic cycling", "potential limitation", "charge/discharge", "charge discharge", "cccv"]): return "Charge/Discharge", "Battery galvanostatic charge/discharge table"
    if any(x in low for x in ["ocp", "ocv", "open circuit", "open-circuit"]): return "OCP", "Open-circuit potential table"
    if any(x in low for x in ["chronoamper", " ca ", "_ca", "ca_", "constv", "constant voltage"]):
        if "cond" in ctx or "conduct" in ctx: return "Conductivity / constant voltage", "Constant-voltage conductivity table"
        return "Chronoamperometry / CA", "Chronoamperometry table"
    if any(x in low for x in ["chronopot", " cp ", "_cp", "cp_", "constant current"]):
        if any(x in ctx for x in ["sym_", "symmetric", "for_xps", "cycling", "gcpl", "gplc", "ua_", "ma_", "_10min", "charge", "discharge", "cathode", "vs-mg", "vs_acc", "cp10", "cp20", "x20", "x50", "1st_cycle", "restart"]):
            return "Charge/Discharge", "Battery/symmetric-cell constant-current cycling table"
        return "Chronopotentiometry / CP", "Chronopotentiometry table"
    return infer_technique_nonheka(path, cols)
# ---- End v145.8 ----

# ---- FaradaIQ canonical bundle v210: chrono line-scan repair + cache policy ----
# Corrects HEKA/SECCM CA/CP line scans that were historically cataloged as CV/LSV
# merely because they contain current and potential columns. It also invalidates
# stale pre-v208 browser caches and emits method/point-aware CA/CP cache paths.
VERSION = "v331"


def _v206_exact_token(text: str, token: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(str(token)) + r"(?![A-Za-z0-9])", str(text or ""), re.I) is not None


def _v206_declared_chrono_kind(root: Path, manifest: Optional[Dict[str, Any]] = None) -> Optional[str]:
    root = Path(root)
    candidates = [root.name, str(root)]
    if isinstance(manifest, dict):
        candidates.extend(str(manifest.get(k) or "") for k in ("base_name", "label", "sample_name"))
    text = " ".join(candidates)
    low = text.lower()
    # Filename/dataset labels are authoritative only at token boundaries.
    if _v206_exact_token(root.name, "CA") or "chronoamper" in low:
        return "CA"
    if _v206_exact_token(root.name, "CP") or "chronopotentiometry" in low or "chronogalv" in low:
        return "CP"
    # HEKA method tables are stronger than generic current/potential column names.
    method_text = []
    try:
        for p in sorted(root.rglob("*_heka_method_entries.csv"))[:4]:
            method_text.append(p.read_text(encoding="utf-8", errors="replace")[:20000])
    except Exception:
        pass
    joined = " ".join(method_text).lower()
    if any(x in joined for x in ("chronopotstat", "chrono pot stat", "chronoamper", "potentiostatic", "constant voltage")):
        return "CA"
    if any(x in joined for x in ("chronogalv", "chrono galv", "chronopotentiometry", "galvanostatic", "constant current")):
        return "CP"
    return None


def _v206_is_generic_line_scan_record(rec: Dict[str, Any]) -> bool:
    repair = str(rec.get("source_repair") or "").lower()
    role = str(rec.get("role") or "").lower()
    name = str(rec.get("name") or rec.get("relative_path") or "").lower()
    return bool(
        repair.startswith("v138_collapsed")
        or repair.startswith("v145_root_primary")
        or "line-scan" in role
        or "current_potential_pair" in name
        or "__current_vs_potential" in name
    )


def _v206_repair_chrono_records(records: Sequence[Dict[str, Any]], kind: Optional[str]) -> Tuple[List[Dict[str, Any]], int]:
    if kind not in {"CA", "CP"}:
        return [dict(r) for r in records], 0
    target = "Chronoamperometry / CA" if kind == "CA" else "Chronopotentiometry / CP"
    prefix = "CA" if kind == "CA" else "CP"
    out: List[Dict[str, Any]] = []
    changed = 0
    for src in records:
        r = dict(src)
        tech = str(r.get("technique") or "")
        # Only relabel generic/fallback line-scan curves. Explicit CV method records remain CV.
        if r.get("record_type") == "table" and tech == "CV / LSV" and _v206_is_generic_line_scan_record(r):
            point = int(r.get("point") or 0)
            cycle = int(r.get("cycle") or 1)
            method = int(r.get("method_entry") or r.get("method") or 1)
            r["technique"] = target
            r["role"] = f"HEKA/SECCM {prefix} line-scan point table"
            r["method_entry"] = method
            r["method"] = method
            r["cycle"] = cycle
            r.pop("scan_rate_mV_s", None)
            r.pop("scan_rate_label", None)
            r["canonical_key"] = f"{prefix}:point{point:03d}:method{method:02d}:cycle{cycle:02d}"
            r["name"] = f"{prefix}_point_{point:03d}_method_{method:02d}_cycle_{cycle:02d}.csv"
            r["source_repair_v206"] = f"reclassified_generic_line_scan_as_{prefix}"
            changed += 1
        out.append(r)
    return out, changed


_V206_PREV_COLLECT_HEKA_RECORDS = _collect_heka_records


def _collect_heka_records(root: Path, log=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    records, info = _V206_PREV_COLLECT_HEKA_RECORDS(root, log=log)
    kind = _v206_declared_chrono_kind(Path(root), None)
    repaired, n_changed = _v206_repair_chrono_records(records, kind)
    if not n_changed:
        return records, info
    seq = dict((info or {}).get("sequence_info") or _sequence_info(Path(root)))
    validation = dict((info or {}).get("validation") or {})
    diagnostics = dict(validation.get("diagnostics") or {})
    diagnostics["v206_chrono_line_scan_reclassified"] = n_changed
    diagnostics["v206_chrono_kind"] = kind
    duplicates = list(validation.get("duplicate_table_paths_removed") or [])
    new_info = _build_info(repaired, repaired, seq, is_heka=True, duplicates=duplicates, diagnostics=diagnostics)
    return repaired, new_info


def _cache_subdir_for_record(cache: Path, rec: Dict[str, Any]) -> Path:
    tech = str(rec.get("technique") or "Numeric")
    if tech == "CV / LSV":
        rate = _clean_rate_label(rec.get("scan_rate_mV_s"))
        point = int(rec.get("point", 0) or 0)
        cyc = int(rec.get("cycle", 0) or 0)
        return cache / "CV_LSV" / f"scanrate_{rate}" / f"point_{point:03d}" / f"cycle_{cyc:02d}"
    if tech == "EIS":
        point = int(rec.get("point", 0) or 0)
        method = int(rec.get("eis_method", rec.get("method_entry", 1)) or 1)
        return cache / "EIS" / f"point_{point:03d}" / f"method_{method:02d}"
    if tech == "DRT":
        point = int(rec.get("point", 0) or 0)
        run = safe_name(rec.get("drt_run_id") or "DRT")
        method = int(rec.get("method_entry", rec.get("method", 1)) or 1)
        return cache / "DRT" / run / f"method_{method:02d}" / f"point_{point:03d}"
    if tech.startswith("Chronoamperometry"):
        point = int(rec.get("point", 0) or 0)
        method = int(rec.get("method_entry", rec.get("method", 1)) or 1)
        return cache / "CA" / f"method_{method:02d}" / f"point_{point:03d}"
    if tech.startswith("Chronopotentiometry"):
        point = int(rec.get("point", 0) or 0)
        method = int(rec.get("method_entry", rec.get("method", 1)) or 1)
        return cache / "CP" / f"method_{method:02d}" / f"point_{point:03d}"
    point = rec.get("point", "unassigned")
    return cache / safe_name(tech) / f"point_{point}"


def _v206_repair_h5_record_for_dataset(rec: Dict[str, Any], manifest: Optional[Dict[str, Any]], h5_path: Path) -> Dict[str, Any]:
    r = dict(rec or {})
    kind = _v206_declared_chrono_kind(h5_path.parent, manifest)
    repaired, n = _v206_repair_chrono_records([r], kind)
    return repaired[0] if n else r


def extract_h5_tables_for_browser(bundle_dir_or_h5: str | Path, prefer_reuse: bool = True) -> Dict[str, Any]:
    path = Path(bundle_dir_or_h5).resolve()
    if path.is_dir():
        h5s = sorted(path.glob("*_faradaiq_bundle.h5"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not h5s:
            h5s = sorted(path.glob("*.h5"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not h5s:
            raise FileNotFoundError(f"No H5 bundle found in {path}")
        h5_path = h5s[0]
    else:
        h5_path = path
    bundle_dir = h5_path.parent
    json_path = h5_path.with_suffix(".json")
    if not json_path.exists():
        jsons = sorted(bundle_dir.glob("*_faradaiq_bundle.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        json_path = jsons[0] if jsons else None
    manifest = _json_load(json_path) if json_path and json_path.exists() else None
    cache = bundle_dir / ".browser_cache_from_h5"
    source_json = cache / "_bundle_source.json"
    h5_mtime = h5_path.stat().st_mtime
    if prefer_reuse and source_json.exists():
        try:
            cached = json.loads(source_json.read_text(encoding="utf-8", errors="replace"))
            if (
                cached.get("extract_policy_version") in {"v208", "v216", "v216"}
                and str(cached.get("h5_path")) == str(h5_path)
                and float(cached.get("h5_mtime", -1)) == float(h5_mtime)
            ):
                return {
                    "cache_root": str(cache), "cache_dir": str(cache), "h5_path": str(h5_path),
                    "json_path": str(json_path or ""), "n_tables": int(cached.get("n_tables", 0)),
                    "manifest": cached.get("manifest"), "cache_reused": True,
                }
        except Exception:
            pass
    if cache.exists():
        shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True, exist_ok=True)
    import h5py
    extracted = []
    repaired_count = 0
    with h5py.File(h5_path, "r") as h5:
        grp = h5.get("tables")
        if grp is not None:
            for key in grp.keys():
                ds = grp[key]
                arr = np.asarray(ds[()])
                try:
                    rec = json.loads(ds.attrs.get("record_json", "{}"))
                except Exception:
                    rec = {}
                before = str(rec.get("technique") or "")
                rec = _v206_repair_h5_record_for_dataset(rec, manifest, h5_path)
                if str(rec.get("technique") or "") != before:
                    repaired_count += 1
                try:
                    cols = json.loads(ds.attrs.get("columns_json", "[]"))
                except Exception:
                    cols = []
                width = arr.shape[1] if arr.ndim == 2 else 1
                if not cols or len(cols) != width:
                    cols = [f"col_{i+1}" for i in range(width)]
                sub = _cache_subdir_for_record(cache, rec)
                sub.mkdir(parents=True, exist_ok=True)
                tech = str(rec.get("technique") or "")
                point = int(rec.get("point", 0) or 0)
                method = int(rec.get("method_entry", rec.get("method", rec.get("eis_method", 1))) or 1)
                cycle = int(rec.get("cycle", 0) or 0)
                if tech == "CV / LSV":
                    fname = f"CV_point_{point:03d}_scanrate_{_clean_rate_label(rec.get('scan_rate_mV_s'))}_cycle_{cycle:02d}.csv"
                elif tech == "EIS":
                    fname = f"EIS_point_{point:03d}_method_{method:02d}_Nyquist_Bode.csv"
                elif tech == "DRT":
                    base_name = safe_name(rec.get("name") or key)
                    while base_name.lower().endswith(".csv"):
                        base_name = base_name[:-4]
                    fname = base_name + ".csv"
                elif tech.startswith("Chronoamperometry"):
                    fname = f"CA_point_{point:03d}_method_{method:02d}_cycle_{max(1, cycle):02d}.csv"
                elif tech.startswith("Chronopotentiometry"):
                    fname = f"CP_point_{point:03d}_method_{method:02d}_cycle_{max(1, cycle):02d}.csv"
                else:
                    base_name = safe_name(rec.get("name") or key)
                    while base_name.lower().endswith(".csv"):
                        base_name = base_name[:-4]
                    fname = base_name + ".csv"
                out = sub / fname
                if pd is not None:
                    pd.DataFrame(arr, columns=cols).to_csv(out, index=False)
                else:
                    with out.open("w", newline="", encoding="utf-8") as f:
                        w = csv.writer(f)
                        w.writerow(cols)
                        w.writerows(arr.tolist())
                extracted.append(str(out))
    payload = safe_json({
        "extract_policy_version": "v216",
        "h5_path": str(h5_path), "h5_mtime": h5_mtime, "json_path": str(json_path or ""),
        "manifest": manifest, "extracted_files": extracted, "n_tables": len(extracted),
        "chrono_records_repaired": repaired_count,
    })
    source_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "cache_root": str(cache), "cache_dir": str(cache), "h5_path": str(h5_path),
        "json_path": str(json_path or ""), "n_tables": len(extracted), "manifest": manifest,
        "cache_reused": False, "chrono_records_repaired": repaired_count,
    }
# ---- End FaradaIQ canonical bundle v216 chrono repair ----

# ---- FaradaIQ canonical bundle v210 structured HEKA manifest support ----
VERSION = "v331"
SCHEMA = "faradaiq-canonical-h5-bundle-v6"

_V208_PREV_COLLECT_HEKA_RECORDS = _collect_heka_records
_V208_PREV_CACHE_SUBDIR_FOR_RECORD = _cache_subdir_for_record


def _v208_heka_manifest(root: Path) -> Optional[Path]:
    hits = sorted(
        list(Path(root).rglob("*_heka_v216_manifest.json")) + list(Path(root).rglob("*_heka_v211_manifest.json")) + list(Path(root).rglob("*_heka_v210_manifest.json")) + list(Path(root).rglob("*_heka_v209_manifest.json")) + list(Path(root).rglob("*_heka_v208_manifest.json")),
        key=lambda p: ((4 if "_v216_" in p.name else (3 if "_v211_" in p.name else (2 if "_v210_" in p.name else (1 if "_v209_" in p.name else 0)))), p.stat().st_mtime if p.exists() else 0),
        reverse=True,
    )
    return hits[0] if hits else None


def _v208_manifest_table_record(path: Path, root: Path, spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    df, _cols, _err = _read_table_general(path)
    if df is None:
        return None
    tech = str(spec.get("technique") or "Unknown HEKA")
    if tech == "EIS":
        df = _standardize_eis(df, path)
    elif tech in {"CV / LSV", "LSV", "FSCV"}:
        df = _standardize_cv(df, path)
    rec = _make_table_record(
        path,
        root,
        tech,
        str(spec.get("role") or "HEKA structured processed table"),
        df,
        point=int(spec.get("point") or 0),
        cycle=int(spec.get("cycle") or 0),
        method_entry=int(spec.get("method") or spec.get("series_index_in_group") or 1),
        method=int(spec.get("method") or spec.get("series_index_in_group") or 1),
        group_index=int(spec.get("group_index") or 0),
        group_title=spec.get("group_title") or "",
        series_index=int(spec.get("series_index") or 0),
        series_index_in_group=int(spec.get("series_index_in_group") or 0),
        series_title=spec.get("series_title") or "",
        sweep_index=int(spec.get("sweep_index") or 0),
        sweep_title=spec.get("sweep_title") or "",
        heka_technique=spec.get("heka_technique") or tech,
        modality=spec.get("modality") or "",
        confidence=spec.get("confidence"),
        evidence=safe_json(spec.get("evidence") or []),
        default_plot=spec.get("default_plot") or "",
        available_views=safe_json(spec.get("available_views") or []),
        available_axes=safe_json(spec.get("available_axes") or []),
        x_label=spec.get("x_label") or "",
        x_unit=spec.get("x_unit") or "",
        y_label=spec.get("y_label") or "",
        y_unit=spec.get("y_unit") or "",
        x_position=spec.get("x_position"), y_position=spec.get("y_position"), z_position=spec.get("z_position"),
        coordinate_status=spec.get("coordinate_status") or "", coordinate_source=spec.get("coordinate_source") or "",
        scan_rate_mV_s=spec.get("scan_rate_mV_s"), scan_rate=spec.get("scan_rate") or "",
        canonical_key=spec.get("canonical_key") or f"HEKA:{path.stem}",
        heka_v208=True, heka_v209=True, heka_v210=True, heka_v211=True, heka_v216=True,
    )
    # Cache extraction appends .csv for generic techniques, so keep the logical
    # record name extension-free to avoid .csv.csv filenames.
    rec["name"] = path.stem
    return rec


def _collect_heka_records(root: Path, log=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    root = Path(root)
    manifest_path = _v208_heka_manifest(root)
    if manifest_path is None:
        return _V208_PREV_COLLECT_HEKA_RECORDS(root, log=log)
    manifest = _json_load(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("schema") not in {"faradaiq-heka-structured-v216", "faradaiq-heka-structured-v210", "faradaiq-heka-structured-v209", "faradaiq-heka-structured-v208"}:
        return _V208_PREV_COLLECT_HEKA_RECORDS(root, log=log)
    records: List[Dict[str, Any]] = []
    missing = []
    failed = []
    for spec in manifest.get("records", []) or []:
        if not isinstance(spec, dict) or not spec.get("relative_csv_path"):
            continue
        path = manifest_path.parent / str(spec["relative_csv_path"])
        if not path.exists():
            missing.append(str(path))
            continue
        rec = _v208_manifest_table_record(path, root, spec)
        if rec is None:
            failed.append(str(path))
            continue
        records.append(rec)
    # Preserve the hierarchy, metadata, evidence, warnings, and source checksum in
    # the canonical JSON/H5 metadata catalog, but do not expose them as plot leaves.
    for p in [manifest_path] + [
        manifest_path.parent / "heka_hierarchy.json",
        manifest_path.parent / "heka_metadata.json",
        manifest_path.parent / "heka_technique_evidence.json",
        manifest_path.parent / "heka_parser_warnings.json",
        manifest_path.parent / "source_and_checksum.json",
    ]:
        if p.exists():
            records.append({
                "source_path": str(p), "relative_path": relpath(p, root), "name": p.name,
                "suffix": p.suffix.lower(), "size_bytes": int(p.stat().st_size),
                "modified_time": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
                "record_type": "metadata", "technique": "Metadata",
                "role": "HEKA v216 structured hierarchy/metadata", "included_in_h5": True,
                "heka_v208": True, "heka_v209": True, "heka_v210": True,
            })
    diagnostics = {
        "structured_heka_manifest": str(manifest_path),
        "structured_heka_records": sum(1 for r in records if r.get("record_type") == "table"),
        "structured_heka_missing_tables": missing,
        "structured_heka_failed_tables": failed,
        "hierarchy_counts": manifest.get("hierarchy_counts", {}),
        "technique_counts": manifest.get("technique_counts", {}),
        "modalities": manifest.get("modalities", {}),
        "warnings": manifest.get("warnings", []),
        "unsupported_sections": manifest.get("unsupported_sections", []),
    }
    seq = {
        "sequence_manifest_path": str(manifest_path),
        "point_count": (manifest.get("summary") or {}).get("scan_points"),
        "scan_geometry": manifest.get("modalities", {}),
        "method_counts_total": manifest.get("technique_counts", {}),
        "sequence_template_counts": manifest.get("hierarchy_counts", {}),
        "scan_rates_mV_s": [], "cycles_per_cv": None, "methods_by_rate": {},
        "raw_manifest": safe_json(manifest),
    }
    info = _build_info(records, records, seq, is_heka=True, duplicates=[], diagnostics=diagnostics)
    info["heka_v210_manifest"] = safe_json(manifest)
    info["heka_v209_manifest"] = safe_json(manifest)
    info["heka_v208_manifest"] = safe_json(manifest)  # backward compatibility
    return records, info


def _cache_subdir_for_record(cache: Path, rec: Dict[str, Any]) -> Path:
    if (rec.get("heka_v210") or rec.get("heka_v209") or rec.get("heka_v208")) and rec.get("record_type") == "table":
        gi = int(rec.get("group_index") or 0)
        gtitle = safe_name(rec.get("group_title") or "untitled_group")
        tech = safe_name(rec.get("heka_technique") or rec.get("technique") or "Unknown_HEKA")
        pt = int(rec.get("point") or 0)
        raw_si = rec.get("series_index_in_group")
        si = int(raw_si if raw_si is not None else (rec.get("method_entry") or 0))
        stitle = safe_name(rec.get("series_title") or "aggregated_series")
        sw = int(rec.get("sweep_index") or 0)
        return (
            cache / "HEKA" / f"group_{gi:03d}_{gtitle}" / tech /
            f"point_{pt:03d}" / f"series_{si:03d}_{stitle}" / f"sweep_{sw:03d}"
        )
    return _V208_PREV_CACHE_SUBDIR_FOR_RECORD(cache, rec)

# Use a new cache policy so pre-v210 flat caches are never reused for v208 hierarchy data.
_V208_PREV_EXTRACT_H5_TABLES = extract_h5_tables_for_browser


def extract_h5_tables_for_browser(bundle_dir_or_h5: str | Path, prefer_reuse: bool = True) -> Dict[str, Any]:
    # The historical implementation checks its own policy token. Removing the
    # stale cache here guarantees one rebuild after the v210 schema upgrade.
    try:
        p = Path(bundle_dir_or_h5).resolve()
        bundle = p if p.is_dir() else p.parent
        cache = bundle / ".browser_cache_from_h5"
        source = cache / "_bundle_source.json"
        if source.exists():
            obj = json.loads(source.read_text(encoding="utf-8", errors="replace"))
            if obj.get("extract_policy_version") not in {"v216", "v216"}:
                shutil.rmtree(cache, ignore_errors=True)
    except Exception:
        pass
    result = _V208_PREV_EXTRACT_H5_TABLES(bundle_dir_or_h5, prefer_reuse=prefer_reuse)
    try:
        source = Path(result.get("cache_root", "")) / "_bundle_source.json"
        if source.exists():
            obj = json.loads(source.read_text(encoding="utf-8", errors="replace"))
            if obj.get("extract_policy_version") != "v216":
                obj["extract_policy_version"] = "v216"
            source.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return result
# ---- End FaradaIQ canonical bundle v216 structured HEKA support ----

# v208 HEKA manifests are authoritative even when the containing folder name
# does not include the literal token HEKA.
_V208_PREV_IS_HEKA_EXPORT_ROOT = is_heka_export_root

def is_heka_export_root(root: Path) -> bool:
    try:
        if _v208_heka_manifest(Path(root)) is not None:
            return True
    except Exception:
        pass
    return _V208_PREV_IS_HEKA_EXPORT_ROOT(root)


# ---- FaradaIQ canonical bundle v216 media and hierarchy support ----
VERSION = "v331"
_V211_PREV_COLLECT_HEKA_RECORDS = _collect_heka_records
_V211_PREV_EXTRACT_H5_TABLES = extract_h5_tables_for_browser
_MEDIA_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".mpeg", ".mpg", ".m4v", ".webm"}
_MEDIA_IMAGE_EXTS = {".gif", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


def _v211_media_role(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    low = path.as_posix().lower().replace("\\", "/")
    if suffix == ".gif": return "Animated GIF", "Animated GIF"
    if suffix in _MEDIA_VIDEO_EXTS: return "Video", "Video"
    if ("/figures/" in low or "/drt/" in low or "_drt_data" in low or
            "_eis_data" in low or "figure" in path.name.lower()):
        return "Generated figure", "Generated figures"
    return "Photo/image", "Media / photos"


def _collect_heka_records(root: Path, log=None):
    records, info = _V211_PREV_COLLECT_HEKA_RECORDS(root, log=log)
    root = Path(root)
    seen = {str(Path(r.get("source_path", "")).resolve()).lower() for r in records if r.get("source_path")}

    # Structured HEKA manifests are authoritative for acquisition tables, but
    # downstream DRT results are created after that export. Merge the actual
    # DRT distribution tables back into the canonical bundle on every rebuild.
    plot_root = _plotting_root(root)
    drt_root = plot_root / "DRT_input_csv" / "DRT" if plot_root else None
    if drt_root and drt_root.exists():
        for path in sorted(drt_root.rglob("*_drt_data.csv"), key=lambda q: q.as_posix().lower()):
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            df, _cols, _err = _read_table_general(path)
            if df is None:
                continue
            sdf = _standardize_drt(df, path)
            if sdf is None or getattr(sdf, "empty", True):
                continue
            pm = re.search(r"position[_-]*0*(\d+)", path.name, flags=re.I)
            point = int(pm.group(1)) if pm else None
            run = next((part for part in reversed(path.parts) if part.lower().startswith("drt_")), "DRT")
            records.append(_make_table_record(
                path, root, "DRT", "analyzed DRT data table", sdf,
                point=point, drt_run_id=run, drt_kind="drt_data",
                canonical_key=f"DRT:{run}:point{int(point or 0):03d}:drt_data",
            ))
            seen.add(key)

    candidates=[]
    for folder_name in ("photos", "media", "Figures", "figures"):
        candidates.extend(root.rglob(folder_name + "/*"))
    # Include same-folder sidecars copied by the HEKA exporter.
    candidates.extend(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in (_MEDIA_IMAGE_EXTS | _MEDIA_VIDEO_EXTS))
    for path in sorted({p.resolve() for p in candidates if p.is_file() and p.suffix.lower() in (_MEDIA_IMAGE_EXTS | _MEDIA_VIDEO_EXTS)}):
        key=str(path).lower()
        if key in seen: continue
        role, tech = _v211_media_role(path)
        records.append({
            "source_path": str(path), "relative_path": relpath(path, root), "name": path.name,
            "suffix": path.suffix.lower(), "size_bytes": int(path.stat().st_size),
            "modified_time": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            "record_type": "media", "technique": tech, "role": role, "included_in_h5": True,
            "heka_v211": True, "heka_v210": True, "heka_v209": True,
        })
        seen.add(key)
    try:
        info["media_count"] = sum(1 for r in records if r.get("record_type") == "media")
        table_counts = {}
        for rec in records:
            if rec.get("record_type") != "table":
                continue
            tech = str(rec.get("technique") or "Other")
            table_counts[tech] = table_counts.get(tech, 0) + 1
        info["technique_counts"] = table_counts
        validation = info.setdefault("validation", {})
        validation["drt_tables_included"] = int(table_counts.get("DRT", 0))
        validation["final_table_files_in_h5"] = sum(table_counts.values())
    except Exception:
        pass
    return records, info


def extract_h5_tables_for_browser(bundle_dir_or_h5: str | Path, prefer_reuse: bool = True) -> Dict[str, Any]:
    p = Path(bundle_dir_or_h5).resolve()
    bundle = p if p.is_dir() else p.parent
    cache = bundle / ".browser_cache_from_h5"
    source = cache / "_bundle_source.json"
    try:
        if source.exists():
            obj=json.loads(source.read_text(encoding="utf-8", errors="replace"))
            if obj.get("extract_policy_version") != "v216": shutil.rmtree(cache, ignore_errors=True)
    except Exception: pass
    result = _V211_PREV_EXTRACT_H5_TABLES(bundle_dir_or_h5, prefer_reuse=prefer_reuse)
    # A valid table cache is also a valid media cache when all recorded media
    # files still exist.  Do not rewrite hundreds of media/figure files on
    # every browser reopen or duplicate import.
    if bool(result.get("cache_reused")):
        try:
            cached_source = Path(result.get("cache_root", cache)) / "_bundle_source.json"
            cached_obj = json.loads(cached_source.read_text(encoding="utf-8", errors="replace")) if cached_source.exists() else {}
            cached_media = [str(x) for x in (cached_obj.get("extracted_media") or [])]
            if cached_obj.get("extract_policy_version") == "v216" and all(Path(x).exists() for x in cached_media):
                result["n_media"] = int(cached_obj.get("n_media", len(cached_media)) or len(cached_media))
                result["extracted_media"] = cached_media
                result["media_cache_reused"] = True
                return result
        except Exception:
            pass
    h5_path=Path(result.get("h5_path") or p)
    if h5_path.is_dir():
        hits=sorted(h5_path.glob("*_faradaiq_bundle.h5"), key=lambda x:x.stat().st_mtime, reverse=True)
        if not hits: return result
        h5_path=hits[0]
    extracted_media=[]
    media_records=[]
    try:
        import h5py
        with h5py.File(h5_path,"r") as h5:
            grp=h5.get("media")
            if grp is not None:
                for key in grp.keys():
                    ds=grp[key]
                    try: rec=json.loads(ds.attrs.get("record_json","{}"))
                    except Exception: rec={}
                    name=Path(str(rec.get("name") or key)).name
                    suffix=Path(name).suffix.lower()
                    if suffix not in (_MEDIA_IMAGE_EXTS | _MEDIA_VIDEO_EXTS):
                        suffix=str(rec.get("suffix") or ".bin")
                        name=safe_name(Path(name).stem)+suffix
                    role, _tech = _v211_media_role(Path(name))
                    category="GIF" if suffix==".gif" else ("Videos" if suffix in _MEDIA_VIDEO_EXTS else ("Figures" if "figure" in str(rec.get("role") or "").lower() else "Photos"))
                    out=cache/"Media"/category/name
                    out.parent.mkdir(parents=True, exist_ok=True)
                    expected_size=int(rec.get("size_bytes") or getattr(ds,"size",0) or 0)
                    original=Path(str(rec.get("source_path") or ""))
                    if out.exists() and (expected_size <= 0 or out.stat().st_size == expected_size):
                        pass
                    elif original.exists() and original.is_file() and (expected_size <= 0 or original.stat().st_size == expected_size):
                        try:
                            if out.exists(): out.unlink()
                            os.link(original,out)
                        except Exception:
                            shutil.copy2(original,out)
                    else:
                        out.write_bytes(np.asarray(ds[()],dtype=np.uint8).tobytes())
                    extracted_media.append(str(out))
                    media_records.append(rec)
    except Exception as exc:
        result["media_extract_warning"]=str(exc)
    try:
        source=Path(result.get("cache_root",cache))/"_bundle_source.json"
        obj=json.loads(source.read_text(encoding="utf-8",errors="replace")) if source.exists() else {}
        obj["extract_policy_version"]="v216"
        obj["extracted_media"]=extracted_media
        obj["n_media"]=len(extracted_media)
        source.write_text(json.dumps(safe_json(obj),indent=2,ensure_ascii=False),encoding="utf-8")
    except Exception: pass
    result["n_media"]=len(extracted_media)
    result["extracted_media"]=extracted_media
    return result
# ---- End FaradaIQ canonical bundle v216 media support ----


# ---- FaradaIQ Studio v290 public release identity ----
# Parser/schema implementation identifiers remain in their dedicated fields,
# while the public application version written to new canonical bundles is v290.
VERSION = "v331"
FARADAIQ_PUBLIC_VERSION = "v331"

# ---- FaradaIQ Studio v292 public release identity ----
# Historical parser/schema identifiers remain intact; newly generated bundles report v292.
VERSION = "v331"
FARADAIQ_PUBLIC_VERSION = "v331"
# ---- End FaradaIQ Studio v292 public release identity ----


# ---- FaradaIQ Studio v293 public release identity ----
# Historical parser/schema identifiers remain intact; newly generated bundles report v293.
VERSION = "v331"
FARADAIQ_PUBLIC_VERSION = "v331"
# ---- End FaradaIQ Studio v293 public release identity ----

# ---- FaradaIQ Studio v294 public release identity ----
# Historical parser/schema identifiers remain intact; newly generated bundles report v294.
VERSION = "v331"
FARADAIQ_PUBLIC_VERSION = "v331"
# ---- End FaradaIQ Studio v294 public release identity ----


# ---- FaradaIQ Studio v296 public release identity ----
# Historical parser/schema identifiers remain intact; newly generated bundles report v295.
VERSION = "v331"
FARADAIQ_PUBLIC_VERSION = "v331"
# ---- End FaradaIQ Studio v296 public release identity ----

# ---- FaradaIQ Studio v297 public release identity ----
VERSION = "v331"
FARADAIQ_PUBLIC_VERSION = "v331"
# ---- End FaradaIQ Studio v297 public release identity ----

# ---- FaradaIQ Studio v311 public release identity ----
# Historical parser/schema identifiers remain intact; newly generated bundles report v311.
VERSION = "v311"
FARADAIQ_PUBLIC_VERSION = "v311"
# ---- End FaradaIQ Studio v311 public release identity ----

# ---- FaradaIQ Studio v313 public release identity ----
# Historical parser/schema identifiers remain intact; newly generated bundles report v313.
VERSION = "v313"
FARADAIQ_PUBLIC_VERSION = "v313"
# ---- End FaradaIQ Studio v313 public release identity ----

# ---- FaradaIQ Studio v317 manifest-authority classification ----
_V317_PREV_CANONICAL_FROM_MANIFEST_ENTRY = _v145_canonical_from_manifest_entry

def _v145_canonical_from_manifest_entry(entry: Dict[str, Any], path: Path, cols: Sequence[str]) -> Tuple[str, str]:
    declared = str(entry.get("technique") or "").strip().upper().replace("-", "_").replace(" ", "_")
    if declared in {"MOTT_SCHOTTKY", "MOTT_SCHOTTKY_DIAGNOSTIC"}:
        return "Mott-Schottky", "Mott-Schottky capacitance/validity table"
    if declared in {"EIS_CIRCUIT_MODEL", "EIS_FIT_DIAGNOSTIC", "EIS_VALIDATION", "EIS_CAPACITANCE", "POTENTIAL_EIS_MODELING"}:
        role_map = {
            "EIS_CIRCUIT_MODEL": "Potential-dependent EIS circuit-model table",
            "EIS_FIT_DIAGNOSTIC": "Measured-versus-fitted EIS diagnostic table",
            "EIS_VALIDATION": "EIS validity/model-selection table",
            "EIS_CAPACITANCE": "Potential-dependent EIS capacitance table",
            "POTENTIAL_EIS_MODELING": "Potential-dependent EIS analysis table",
        }
        return "EIS analysis", role_map.get(declared, "Potential-dependent EIS analysis table")
    if declared in {"DRT_PEAK_TRACKS", "DRT_ANALYSIS"}:
        return "DRT analysis", "Potential-resolved DRT analysis table"
    return _V317_PREV_CANONICAL_FROM_MANIFEST_ENTRY(entry, path, cols)

VERSION = "v317"
FARADAIQ_PUBLIC_VERSION = "v317"
# ---- End FaradaIQ Studio v317 manifest-authority classification ----

# ---- FaradaIQ Studio v317 completed non-HEKA DRT inclusion ----
_V317_PREV_INCLUDE_NONHEKA = _include_nonheka
_V317_PREV_INFER_NONHEKA = infer_technique_nonheka


def _v317_is_completed_drt_table(path: Path) -> bool:
    low = path.as_posix().lower()
    name = path.name.lower()
    return (
        path.is_file()
        and path.suffix.lower() == ".csv"
        and "/drt_input_csv/drt/" in low.replace("\\", "/")
        and (
            name.endswith("_simple_drt.csv")
            or name.endswith("_bayesian_drt.csv")
            or name.endswith("_bht_drt.csv")
        )
    )


def _include_nonheka(path: Path) -> bool:
    # Keep raw DRT-input staging duplicates out of the canonical bundle, but
    # include the completed distribution tables produced underneath
    # DRT_input_csv/DRT/<run_id>/.
    if _v317_is_completed_drt_table(path):
        return True
    return _V317_PREV_INCLUDE_NONHEKA(path)


def infer_technique_nonheka(path: Path, columns: Sequence[str]) -> Tuple[str, str]:
    if _v317_is_completed_drt_table(path):
        return "DRT", "analyzed DRT distribution table"
    return _V317_PREV_INFER_NONHEKA(path, columns)

VERSION = "v317"
FARADAIQ_PUBLIC_VERSION = "v317"
# ---- End FaradaIQ Studio v317 completed non-HEKA DRT inclusion ----

# ---- FaradaIQ Studio v320 standalone public release identity ----
VERSION = "v331"
FARADAIQ_PUBLIC_VERSION = "v331"
# ---- End FaradaIQ Studio v320 standalone public release identity ----


def build_canonical_bundle(analysis_root,base_name=None,gui_metadata=None,progress_callback=None,label='canonical',*,output_dir=None,preserve_sources=False,preserve_input_units=False,cancel_event=None,bundle_file_stem=None):
    previous=(getattr(_BUNDLE_CONTEXT,'preserve_input_units',False),getattr(_BUNDLE_CONTEXT,'cancel_event',None))
    _BUNDLE_CONTEXT.preserve_input_units=preserve_input_units
    _BUNDLE_CONTEXT.cancel_event=cancel_event
    try:
        _check_bundle_cancel()
        result=_build_canonical_bundle_impl(analysis_root,base_name,gui_metadata,progress_callback,label,output_dir=output_dir,preserve_sources=preserve_sources,bundle_file_stem=bundle_file_stem)
        _check_bundle_cancel()
        return result
    finally:_BUNDLE_CONTEXT.preserve_input_units,_BUNDLE_CONTEXT.cancel_event=previous
