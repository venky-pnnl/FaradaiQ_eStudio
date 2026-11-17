"""Unified FaradaIQ browser catalog for FaradaIQ Studio v290.

The canonical H5/JSON bundle is the source of truth.  This module builds one
normalized record catalog before the Tk tree is rendered.  All importers use
the same catalog contract, while authoritative importer metadata (especially
HEKA Group/Series/Sweep/Trace metadata) takes precedence over filename guesses.
"""
from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

CATALOG_SCHEMA = "faradaiq-browser-catalog-v290"
CATALOG_VERSION = "v290"

PLOT_MODES: Tuple[str, ...] = (
    "Auto",
    "CV / LSV",
    "CV: current vs time",
    "CV: potential vs time",
    "CV: charge vs time",
    "IV / Tafel",
    "CA: current vs time",
    "CP: potential vs time",
    "OCP: potential vs time",
    "Chrono: charge vs time",
    "EIS Nyquist",
    "EIS Nyquist — frequency gradient",
    "EIS Nyquist — stored Zimag",
    "EIS Bode magnitude",
    "EIS Bode phase",
    "EIS Admittance Nyquist",
    "DRT",
    "Approach curve",
    "Distance calibration",
    "Shear-force amplitude",
    "Shear-force phase",
    "Time scan",
    "X scan",
    "Y scan",
    "Z scan",
    "Line scan",
    "2D map",
    "3D scan",
    "Scanning Kelvin probe",
    "Media viewer",
    "Numeric",
)

# key -> user-facing label, order, default plot mode
TECHNIQUE_SPECS: Dict[str, Dict[str, Any]] = {
    "CV": {"label": "Cyclic voltammetry (CV)", "order": 10, "mode": "CV / LSV"},
    "FSCV": {"label": "Fast-scan cyclic voltammetry (FSCV)", "order": 11, "mode": "CV / LSV"},
    "CV/LSV": {"label": "Voltammetry (CV / LSV)", "order": 12, "mode": "CV / LSV"},
    "LSV": {"label": "Linear sweep / polarization (LSV)", "order": 13, "mode": "CV / LSV"},
    "IV/TAFEL": {"label": "I–V / Tafel / corrosion", "order": 14, "mode": "IV / Tafel"},
    "CA": {"label": "Chronoamperometry (CA)", "order": 20, "mode": "CA: current vs time"},
    "CP": {"label": "Chronopotentiometry (CP)", "order": 21, "mode": "CP: potential vs time"},
    "OCP": {"label": "Open-circuit potential (OCP / OCV)", "order": 22, "mode": "OCP: potential vs time"},
    "EIS": {"label": "Electrochemical impedance spectroscopy (EIS)", "order": 30, "mode": "EIS Nyquist"},
    "DRT": {"label": "Distribution of relaxation times (DRT)", "order": 31, "mode": "DRT"},
    "APPROACH": {"label": "Approach / retract curves", "order": 40, "mode": "Approach curve"},
    "DISTANCE_CAL": {"label": "Distance calibration", "order": 41, "mode": "Distance calibration"},
    "SHEAR_FORCE": {"label": "Shear-force distance regulation", "order": 42, "mode": "Shear-force amplitude"},
    "TIME_SCAN": {"label": "Time scan", "order": 43, "mode": "Time scan"},
    "X_SCAN": {"label": "X scan", "order": 44, "mode": "X scan"},
    "Y_SCAN": {"label": "Y scan", "order": 45, "mode": "Y scan"},
    "Z_SCAN": {"label": "Z scan", "order": 46, "mode": "Z scan"},
    "LINE_SCAN": {"label": "Line scan", "order": 47, "mode": "Line scan"},
    "MAP_2D": {"label": "2D matrix / spatial map", "order": 48, "mode": "2D map"},
    "SCAN_3D": {"label": "3D scan", "order": 49, "mode": "3D scan"},
    "SKP": {"label": "Scanning Kelvin probe", "order": 50, "mode": "Scanning Kelvin probe"},
    "SICM": {"label": "Scanning ion conductance microscopy (SICM)", "order": 51, "mode": "Numeric"},
    "DPV": {"label": "Differential pulse voltammetry (DPV)", "order": 60, "mode": "CV / LSV"},
    "SWV": {"label": "Square-wave voltammetry (SWV)", "order": 61, "mode": "CV / LSV"},
    "NPV": {"label": "Normal pulse voltammetry (NPV)", "order": 62, "mode": "CV / LSV"},
    "GCD": {"label": "Galvanostatic charge–discharge (GCD / GCPL)", "order": 63, "mode": "Numeric"},
    "CHRONOCOULOMETRY": {"label": "Chronocoulometry", "order": 64, "mode": "Time scan"},
    "ZRA": {"label": "Zero-resistance ammetry (ZRA)", "order": 65, "mode": "Time scan"},
    "CONDUCTIVITY": {"label": "Conductivity / constant-voltage measurement", "order": 66, "mode": "Time scan"},
    "EQCM": {"label": "Electrochemical quartz crystal microbalance (EQCM)", "order": 67, "mode": "Time scan"},
    "GITT": {"label": "Galvanostatic intermittent titration (GITT)", "order": 68, "mode": "Numeric"},
    "PITT": {"label": "Potentiostatic intermittent titration (PITT)", "order": 69, "mode": "Numeric"},
    "MOTT_SCHOTTKY": {"label": "Mott–Schottky", "order": 70, "mode": "Numeric"},
    "RDE_RRDE": {"label": "RDE / RRDE", "order": 71, "mode": "CV / LSV"},
    "NOISE": {"label": "Electrochemical noise", "order": 72, "mode": "Time scan"},
    "RAMAN": {"label": "Raman / SERS", "order": 80, "mode": "Numeric"},
    "FTIR": {"label": "FTIR / IR", "order": 81, "mode": "Numeric"},
    "UVVIS": {"label": "UV–Vis", "order": 82, "mode": "Numeric"},
    "XRD": {"label": "X-ray diffraction (XRD)", "order": 83, "mode": "Numeric"},
    "XPS": {"label": "X-ray photoelectron spectroscopy (XPS)", "order": 84, "mode": "Numeric"},
    "POSITION": {"label": "Acquisition positions / Takedata", "order": 5, "mode": "Numeric"},
    "PHOTO": {"label": "Photos / images", "order": 850, "mode": "Media viewer"},
    "GIF": {"label": "Animated GIFs", "order": 851, "mode": "Media viewer"},
    "VIDEO": {"label": "Videos", "order": 852, "mode": "Media viewer"},
    "FIGURE": {"label": "Generated figures", "order": 853, "mode": "Media viewer"},
    "UNKNOWN_HEKA": {"label": "Unknown HEKA technique", "order": 900, "mode": "Numeric"},
    "OTHER": {"label": "Other numeric tables", "order": 999, "mode": "Numeric"},
}


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _safe_float(value: Any) -> Optional[float]:
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def _path_meta(path: Path) -> Dict[str, Any]:
    parts = [path.name] + list(reversed(path.parts[:-1]))

    def pick(patterns: Sequence[str], default: int = 0) -> int:
        for text in parts:
            for pattern in patterns:
                matches = list(re.finditer(pattern, str(text), flags=re.I))
                if matches:
                    try:
                        return int(matches[-1].group(1))
                    except Exception:
                        pass
        return default

    point = pick((r"(?:position|pos)[_\- ]*(\d+)", r"(?:point|pt)[_\- ]*(\d+)", r"^P(\d{1,5})[_\- ]*M\d+"))
    method = pick((r"method[_\- ]*(\d+)", r"^P\d{1,5}[_\- ]*M(\d+)", r"(?:^|[_\-])m(\d+)(?:_|\.|$)"), 1)
    cycle = pick((r"cycle[_\- ]*(\d+)", r"(?:^|[_\-])c(\d+)(?:_|\.|$)"))
    line = pick((r"line[_\- ]*(\d+)",))
    sweep = pick((r"sweep[_\- ]*(\d+)",))
    rate = ""
    joined = path.as_posix()
    m = re.search(r"scanrate[_\- ]*([0-9]+(?:\.[0-9]+)?(?:[_\- ]*(?:mV|V)(?:[_\- ]*s)?)?)", joined, flags=re.I)
    if m:
        rate = m.group(1)
    else:
        m = re.search(r"(\d+(?:\.\d+)?)\s*mV(?:perS|s)", joined, flags=re.I)
        if m:
            rate = m.group(1) + " mV/s"
    run = ""
    for part in path.parts:
        if re.match(r"DRT[_\-]\d", part, flags=re.I):
            run = part
    engine = ""
    low = path.name.lower()
    for token, label in (("bayes", "Bayesian"), ("bht", "BHT"), ("simple", "Simple"), ("deareis", "DearEIS"), ("ridge", "Ridge"), ("nnls", "NNLS")):
        if token in low:
            engine = label
            break
    return {"point": point, "method": method, "cycle": cycle, "line": line, "sweep": sweep, "scan_rate": rate, "run": run, "engine": engine}



def _heka_path_fields(path: Path) -> Dict[str, Any]:
    """Recover retained HEKA hierarchy from portable cache paths."""
    out: Dict[str, Any] = {}
    for part in path.parts:
        m = re.match(r"group_(\d+)(?:_(.*))?$", str(part), flags=re.I)
        if m:
            out["group_index"] = int(m.group(1))
            out["group_title"] = (m.group(2) or "").replace("_", " ")
            continue
        m = re.match(r"series_(\d+)(?:_(.*))?$", str(part), flags=re.I)
        if m:
            out["series_index_in_group"] = int(m.group(1))
            out["series_title"] = (m.group(2) or "").replace("_", " ")
            continue
        m = re.match(r"sweep_(\d+)$", str(part), flags=re.I)
        if m:
            out["sweep_index"] = int(m.group(1))
            continue
        m = re.match(r"point_(\d+)$", str(part), flags=re.I)
        if m:
            out["point"] = int(m.group(1))
    return out



def _heka_logical_fields(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    """Recover HEKA hierarchy from canonical metadata even after a previous short-name alias.

    The previous short-name implementation could shorten the physical ``*_HEKA_v216`` folder. The canonical key
    still retains the authoritative group/point/series/sweep hierarchy, so the
    Browser must use that logical path rather than infer identity from aliases.
    """
    out: Dict[str, Any] = {}
    candidates = [
        metadata.get("canonical_key"),
        metadata.get("relative_path"),
        metadata.get("source_path"),
        metadata.get("relative_csv_path"),
    ]
    for raw in candidates:
        text = str(raw or "").replace("\\", "/")
        if not text:
            continue
        if re.search(r"/processed/group_\d+", text, flags=re.I) or re.search(r"/series_\d+", text, flags=re.I):
            out["is_heka"] = True
        patterns = (
            ("group_index", r"(?:^|/)group_(\d+)(?:_([^/]+))?"),
            ("point", r"(?:^|/)point_(\d+)(?:/|$)"),
            ("series_index_in_group", r"(?:^|/)series_(\d+)(?:_([^/]+))?"),
            ("sweep_index", r"(?:^|/)sweep_(\d+)(?:_|/|$)"),
        )
        for key, pattern in patterns:
            match = re.search(pattern, text, flags=re.I)
            if not match:
                continue
            out[key] = int(match.group(1))
            if key == "group_index" and match.lastindex and match.lastindex >= 2 and match.group(2):
                out["group_title"] = match.group(2).replace("_", " ")
            if key == "series_index_in_group" and match.lastindex and match.lastindex >= 2 and match.group(2):
                out["series_title"] = match.group(2).replace("_", " ")
    return out

def _read_header(path: Path) -> List[str]:
    try:
        if path.suffix.lower() in {".xlsx", ".xls"}:
            import pandas as pd  # optional
            return [str(c) for c in pd.read_excel(path, nrows=1).columns]
        text = path.read_bytes()[:32768].decode("utf-8-sig", errors="replace")
    except Exception:
        return []
    known = ("time", "potential", "voltage", "current", "charge", "frequency", "freq", "zreal", "zimag", "tau", "gamma", "phase", "distance", "position", "slope")
    best: List[str] = []
    best_score = -1.0
    for line in text.splitlines()[:24]:
        if not line.strip():
            continue
        delimiter = max((",", "\t", ";"), key=line.count)
        if line.count(delimiter) < 1:
            continue
        cols = [c.strip().strip("\"'") for c in line.split(delimiter)]
        score = sum(1 for c in cols if any(k in _norm(c) for k in known)) + min(len(cols), 12) * 0.02
        if score > best_score:
            best, best_score = cols, score
    return best


def _column_flags(columns: Sequence[str]) -> Dict[str, bool]:
    n = {_norm(c) for c in columns or []}

    def has(*tokens: str) -> bool:
        targets = {_norm(t) for t in tokens}
        return any(c in targets or any(len(t) >= 4 and t in c for t in targets) for c in n)

    return {
        "time": has("time", "time_s", "t_s"),
        "potential": has("potential", "potential_v", "ewe", "voltage", "emon"),
        "current": has("current", "current_a", "i_a", "imon"),
        "frequency": has("frequency", "frequency_hz", "freq", "freq_hz"),
        "zreal": has("zreal", "zreal_ohm", "realz", "re_z"),
        "zimag": has("zimag", "zimag_ohm", "imagz", "neg_zimag", "im_z"),
        "tau": has("tau", "tau_s", "timescale"),
        "gamma": has("gamma", "gamma_ohm", "drt"),
        "phase": has("phase", "phase_deg"),
        "x": has("x_position", "x_coordinate", "x_um", "x_mm"),
        "y": has("y_position", "y_coordinate", "y_um", "y_mm"),
        "z": has("z_position", "distance", "height", "z_coordinate"),
    }


def normalize_technique(metadata: Mapping[str, Any], path: Path, columns: Sequence[str]) -> str:
    heka = str(metadata.get("heka_technique") or "").strip()
    raw = heka or str(metadata.get("technique") or metadata.get("tech") or "").strip()
    n = _norm(raw)
    aliases = {
        "cv": "CV", "cyclicvoltammetry": "CV", "cvlsv": "CV/LSV", "voltammetrycvlsv": "CV/LSV",
        "fscv": "FSCV", "fastscancyclicvoltammetry": "FSCV",
        "lsv": "LSV", "linearsweepvoltammetry": "LSV", "linearsweeppolarizationlsv": "LSV",
        "tafelcorrosion": "IV/TAFEL", "ivtafel": "IV/TAFEL", "ivtafelcorrosion": "IV/TAFEL",
        "ca": "CA", "chronoamperometryca": "CA", "chronoamperometry": "CA",
        "cp": "CP", "chronopotentiometrycp": "CP", "chronopotentiometry": "CP",
        "ocp": "OCP", "ocv": "OCP", "ocpocv": "OCP", "opencircuitpotentialocpocv": "OCP",
        "eis": "EIS", "electrochemicalimpedancespectroscopyeis": "EIS",
        "drt": "DRT", "distributionofrelaxationtimesdrt": "DRT",
        "approachcurve": "APPROACH", "approachretractcurves": "APPROACH",
        "distancecalibration": "DISTANCE_CAL",
        "shearforce": "SHEAR_FORCE", "shearforcedistanceregulation": "SHEAR_FORCE",
        "timescan": "TIME_SCAN", "xscan": "X_SCAN", "yscan": "Y_SCAN", "zscan": "Z_SCAN",
        "linescan": "LINE_SCAN", "2dmatrixscan": "MAP_2D", "2dmatrixspatialmap": "MAP_2D", "3dscan": "SCAN_3D",
        "scanningkelvinprobe": "SKP", "sicm": "SICM",
        "dpv": "DPV", "differentialpulsevoltammetrydpv": "DPV",
        "swv": "SWV", "squarewavevoltammetryswv": "SWV",
        "npv": "NPV", "normalpulsevoltammetrynpv": "NPV",
        "gcdgcpl": "GCD", "galvanostaticchargedischargegcdgcpl": "GCD",
        "chronocoulometry": "CHRONOCOULOMETRY", "zra": "ZRA",
        "conductivity": "CONDUCTIVITY", "conductivityconstantvoltagemeasurements": "CONDUCTIVITY",
        "eqcm": "EQCM", "electrochemicalquartzcrystalmicrobalanceeqcm": "EQCM",
        "gitt": "GITT", "pitt": "PITT", "mottschottky": "MOTT_SCHOTTKY",
        "rderrde": "RDE_RRDE", "electrochemicalnoise": "NOISE",
        "ramansers": "RAMAN", "ftirir": "FTIR", "uvvis": "UVVIS", "xrd": "XRD", "xps": "XPS",
        "positiontakedata": "POSITION", "takedata": "POSITION", "acquisitionposition": "POSITION",
        "mediaphotos": "PHOTO", "photo": "PHOTO", "image": "PHOTO", "animatedgif": "GIF", "gif": "GIF", "video": "VIDEO", "movie": "VIDEO", "figure": "FIGURE",
        "unknownheka": "UNKNOWN_HEKA", "other": "OTHER", "numeric": "OTHER",
    }
    if n in aliases:
        return aliases[n]

    suffix = path.suffix.lower()
    if suffix == ".gif":
        return "GIF"
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        low_path = path.as_posix().lower()
        return "FIGURE" if ("figure" in low_path or "/drt/" in low_path or "_drt_data" in low_path or "_eis_data" in low_path) else "PHOTO"
    if suffix in {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".mpeg", ".mpg", ".m4v", ".webm"}:
        return "VIDEO"

    flags = _column_flags(columns)
    if flags["tau"] and flags["gamma"]:
        return "DRT"
    if flags["zreal"] and flags["zimag"]:
        return "EIS"

    low = path.as_posix().lower()
    tokens = {x for part in path.parts for x in re.split(r"[^a-z0-9]+", str(part).lower()) if x}
    parts_norm = {_norm(part) for part in path.parts}
    if "takedata" in low or "positiontakedata" in parts_norm:
        return "POSITION"
    if "cv" in parts_norm:
        return "CV"
    exact_map = {
        "approach_curve": "APPROACH", "distance_calibration": "DISTANCE_CAL", "shear_force": "SHEAR_FORCE",
        "time_scan": "TIME_SCAN", "x_scan": "X_SCAN", "y_scan": "Y_SCAN", "z_scan": "Z_SCAN",
        "line_scan": "LINE_SCAN", "2d_matrix_scan": "MAP_2D", "3d_scan": "SCAN_3D",
    }
    for token, key in exact_map.items():
        if token in low:
            return key
    if "drt" in tokens and flags["gamma"]:
        return "DRT"
    if "eis" in tokens or "nyquist" in low or "bode" in low:
        return "EIS"
    if "ca" in tokens or "chronoamperometry" in tokens or "chronoamp" in low:
        return "CA"
    if "cp" in tokens or "chronopotentiometry" in tokens or "chronopot" in low:
        return "CP"
    if "ocp" in tokens or "ocv" in tokens:
        return "OCP"
    if "tafel" in tokens or "iv" in tokens:
        return "IV/TAFEL"
    if "lsv" in tokens:
        return "LSV"
    if "fscv" in tokens:
        return "FSCV"
    if "cv" in tokens or "cv_lsv" in low:
        return "CV/LSV"
    if flags["potential"] and flags["current"]:
        return "CV/LSV"
    if flags["time"] and flags["current"]:
        return "CA"
    return "UNKNOWN_HEKA" if metadata.get("heka_v216") or metadata.get("heka_v211") or metadata.get("heka_v210") or metadata.get("heka_v209") or metadata.get("heka_v208") else "OTHER"


def _instrument(metadata: Mapping[str, Any], path: Path) -> str:
    logical = _heka_logical_fields(metadata)
    if (
        metadata.get("heka_v216") or metadata.get("heka_v211") or metadata.get("heka_v210")
        or metadata.get("heka_v209") or metadata.get("heka_v208") or "HEKA" in path.parts
        or logical.get("is_heka")
    ):
        return "HEKA"
    text = " ".join((
        str(metadata.get("role") or ""), str(metadata.get("source_path") or ""),
        str(metadata.get("canonical_key") or ""), path.as_posix(),
    )).lower()
    for token, name in (("palmsens", "PalmSens"), ("versastudio", "VersaStudio"), ("biologic", "BioLogic"), ("solartron", "Solartron"), ("gamry", "Gamry"), ("seccm", "SECCM")):
        if token in text:
            return name
    return str(metadata.get("instrument") or "")


def _source_file(manifest: Mapping[str, Any], metadata: Mapping[str, Any], path: Path) -> str:
    try:
        raw = manifest.get("experiment_structure_from_dat", {}).get("raw_manifest", {})
        src = raw.get("source", {}).get("original_source_filename")
        if src:
            return str(src)
    except Exception:
        pass
    for key in ("original_source_filename", "source_file", "source_filename"):
        if metadata.get(key):
            return str(metadata[key])
    return path.name


def _method_signature(record: MutableMapping[str, Any]) -> Tuple[str, str, Optional[int]]:
    tech = record["technique_key"]
    heka = record.get("instrument") == "HEKA" or record.get("heka_v216") or record.get("heka_v211") or record.get("heka_v210") or record.get("heka_v209") or record.get("heka_v208")
    title = str(record.get("series_title") or record.get("method_title") or record.get("method_id") or "").strip()
    explicit = _safe_int(record.get("method"), 0)
    rate = str(record.get("scan_rate") or "").strip()

    if heka:
        if tech == "POSITION":
            return "heka:position:takedata", "Takedata acquisition position", None
        # HEKA Series index is acquisition order, not a method identity.  Repeated
        # Series titles/protocols across points therefore share one browser method.
        if tech == "EIS" and "aggregated" in title.lower():
            title = "EIS frequency program"
        if tech in {"CV", "CV/LSV", "LSV", "FSCV"}:
            rate_value = str(record.get("scan_rate") or "").strip()
            if not rate_value:
                m = re.search(r"(?i)([0-9]+(?:\.[0-9]+)?)\s*(mV|V)", " ".join((str(record.get("group_title") or ""), title)))
                if m:
                    value = float(m.group(1)) * (1000.0 if m.group(2).lower() == "v" else 1.0)
                    rate_value = f"{value:g} mV/s"
                    record["scan_rate"] = rate_value
            if rate_value:
                return f"heka:{tech}:rate:{_norm(rate_value)}", f"scan rate {rate_value}", None
        if not title:
            title = str(record.get("role") or TECHNIQUE_SPECS.get(tech, {}).get("label") or tech)
        sig = f"heka:{tech}:{_norm(title)}"
        return sig, title, None

    if rate and tech in {"CV", "FSCV", "CV/LSV", "LSV"}:
        return f"rate:{_norm(rate)}", f"scan rate {rate}", explicit or None
    method_id = str(record.get("method_id") or "").strip()
    if method_id and _norm(method_id) not in {"unknown", _norm(tech)}:
        return f"methodid:{_norm(method_id)}", method_id, explicit or None
    if explicit > 0:
        return f"method:{explicit}", f"method {explicit:02d}", explicit

    stem = Path(record["path"]).stem
    stem = re.sub(r"(?:point|position|pos|pt)[_\- ]*\d+", "", stem, flags=re.I)
    stem = re.sub(r"(?:cycle|sweep|line)[_\- ]*\d+", "", stem, flags=re.I)
    stem = re.sub(r"[_\- ]+", " ", stem).strip()
    title = title or stem or TECHNIQUE_SPECS.get(tech, {}).get("label", tech)
    return f"derived:{tech}:{_norm(title)}", title, None


def _normalize_record(path: Path, metadata: Mapping[str, Any], manifest: Mapping[str, Any], order: int) -> Dict[str, Any]:
    pm = _path_meta(path)
    physical_heka = _heka_path_fields(path) if "HEKA" in path.parts else {}
    logical_heka = _heka_logical_fields(metadata)
    heka_path = {**physical_heka, **logical_heka}
    columns = [str(x) for x in (metadata.get("columns") or _read_header(path))]
    tech = normalize_technique(metadata, path, columns)
    spec = TECHNIQUE_SPECS.get(tech, TECHNIQUE_SPECS["OTHER"])
    has_canonical_point = "point" in logical_heka
    if has_canonical_point:
        point = _safe_int(logical_heka.get("point"), 0)
    else:
        point = _safe_int(metadata.get("point"), _safe_int(heka_path.get("point"), pm["point"]))
    cycle = _safe_int(metadata.get("cycle"), pm["cycle"])
    line = _safe_int(metadata.get("line"), pm["line"])
    sweep = _safe_int(metadata.get("sweep_index"), _safe_int(heka_path.get("sweep_index"), pm["sweep"]))
    method = _safe_int(metadata.get("method"), pm["method"])
    group_index = _safe_int(metadata.get("group_index"), _safe_int(heka_path.get("group_index"), 0))
    series_index = _safe_int(metadata.get("series_index"), _safe_int(heka_path.get("series_index_in_group"), 0))
    confidence = _safe_float(metadata.get("confidence"))
    evidence = metadata.get("evidence") or []
    if isinstance(evidence, str):
        evidence = [evidence]
    recovered_heka = bool(logical_heka.get("is_heka"))
    record: Dict[str, Any] = {
        "path": str(path.resolve()),
        "source_order": int(order),
        "technique_key": tech,
        "technique_label": spec["label"],
        "plot_mode": spec["mode"],
        "technique_order": int(spec["order"]),
        "instrument": _instrument(metadata, path),
        "source_file": _source_file(manifest, metadata, path),
        "role": str(metadata.get("role") or "processed table"),
        "columns": columns,
        "point": point,
        "point_from_canonical_key": has_canonical_point,
        "line": line,
        "cycle": cycle,
        "sweep": sweep,
        "method": method,
        "method_id": str(metadata.get("method_id") or ""),
        "scan_rate": str(metadata.get("scan_rate") or metadata.get("scan_rate_mV_s") or pm["scan_rate"] or ""),
        "scan_rate_mV_s": _safe_float(metadata.get("scan_rate_mV_s")),
        "x_label": str(metadata.get("x_label") or ""),
        "x_unit": str(metadata.get("x_unit") or ""),
        "y_label": str(metadata.get("y_label") or ""),
        "y_unit": str(metadata.get("y_unit") or ""),
        "x_position": _safe_float(metadata.get("x_position") if metadata.get("x_position") is not None else metadata.get("x_m") if metadata.get("x_m") is not None else metadata.get("x_coordinate")),
        "y_position": _safe_float(metadata.get("y_position") if metadata.get("y_position") is not None else metadata.get("y_m") if metadata.get("y_m") is not None else metadata.get("y_coordinate")),
        "z_position": _safe_float(metadata.get("z_position") if metadata.get("z_position") is not None else metadata.get("z_m") if metadata.get("z_m") is not None else metadata.get("z_coordinate")),
        "initial_setup": bool(metadata.get("initial_setup")) or point == 0,
        "group_index": group_index,
        "group_title": str(metadata.get("group_title") or heka_path.get("group_title") or ""),
        "series_index": series_index,
        "series_index_in_group": _safe_int(metadata.get("series_index_in_group"), _safe_int(heka_path.get("series_index_in_group"), series_index)),
        "series_title": str(metadata.get("series_title") or heka_path.get("series_title") or ""),
        "sweep_title": str(metadata.get("sweep_title") or ""),
        "modality": str(metadata.get("modality") or ""),
        "confidence": confidence,
        "evidence": [str(x) for x in evidence] + (["Recovered HEKA hierarchy from canonical_key after physical-name aliasing"] if recovered_heka else []),
        "default_plot": str(metadata.get("default_plot") or ""),
        "available_axes": [str(x) for x in (metadata.get("available_axes") or [])],
        "available_views": [str(x) for x in (metadata.get("available_views") or [])],
        "canonical_key": str(metadata.get("canonical_key") or ""),
        "run": str(metadata.get("run") or pm["run"] or ""),
        "engine": str(metadata.get("engine") or pm["engine"] or ""),
        "heka_v208": bool(metadata.get("heka_v208")),
        "heka_v209": bool(metadata.get("heka_v209")),
        "heka_v210": bool(metadata.get("heka_v210")),
        "heka_v211": bool(metadata.get("heka_v211")),
        "heka_v216": bool(metadata.get("heka_v216")) or bool(metadata.get("heka_v211")) or recovered_heka,
        "metadata": dict(metadata),
    }
    if record.get("scan_rate_mV_s") is None and record.get("scan_rate"):
        m_rate = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(mV|V)", str(record.get("scan_rate")), flags=re.I)
        if m_rate:
            record["scan_rate_mV_s"] = float(m_rate.group(1)) * (1000.0 if m_rate.group(2).lower() == "v" else 1.0)
    sig, method_title, preferred_index = _method_signature(record)
    record["method_signature"] = sig
    record["method_title"] = method_title
    record["preferred_method_index"] = preferred_index
    return record


def _load_bundle_records(cache_root: Path) -> Tuple[Dict[str, Any], List[Tuple[Path, Dict[str, Any]]], List[str]]:
    source = cache_root / "_bundle_source.json"
    warnings: List[str] = []
    if not source.exists():
        return {}, [], warnings
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, [], [f"Could not read {source.name}: {exc}"]
    manifest = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else {}
    extracted = [Path(x) for x in payload.get("extracted_files", [])]
    extracted_media = [Path(x) for x in payload.get("extracted_media", [])]
    records = []
    try:
        records = [r for r in manifest.get("data_structure", {}).get("records", []) if isinstance(r, dict) and r.get("record_type") == "table" and r.get("included_in_h5", True)]
    except Exception:
        records = []
    pairs: List[Tuple[Path, Dict[str, Any]]] = []
    if records and len(records) == len(extracted):
        for p, rec in zip(extracted, records):
            if p.exists():
                pairs.append((p, rec))
    else:
        if records or extracted:
            warnings.append(f"Canonical manifest/table mapping count mismatch: metadata={len(records)}, extracted={len(extracted)}. Falling back to indexed matching.")
        # Match using stable order and, where possible, group/series/sweep tokens.
        unused = list(records)
        for p in extracted:
            if not p.exists():
                continue
            pm = _path_meta(p)
            match = None
            for rec in unused:
                if _safe_int(rec.get("point"), 0) == pm["point"] and (_safe_int(rec.get("sweep_index"), 0) in {0, pm["sweep"]}):
                    t = normalize_technique(rec, p, rec.get("columns") or [])
                    path_t = normalize_technique({}, p, rec.get("columns") or [])
                    if t == path_t or path_t in {"OTHER", "UNKNOWN_HEKA"}:
                        match = rec
                        break
            if match is None and unused:
                match = unused[0]
            if match is not None:
                unused.remove(match)
                pairs.append((p, match))
            else:
                pairs.append((p, {}))
    # Portable-cache migration: saved browser sessions may contain absolute
    # Windows paths that do not exist on the current machine. Reassociate the
    # manifest records with tables extracted under this cache using the retained
    # HEKA Group/Series/Sweep hierarchy instead of falling back to filenames.
    if records and not pairs:
        actual=[]
        for ext in ("*.csv", "*.tsv", "*.txt", "*.asc", "*.xlsx", "*.xls"):
            actual.extend(cache_root.rglob(ext))
        unused=sorted({p.resolve() for p in actual if p.is_file()}, key=lambda p:p.as_posix().lower())
        for rec in records:
            gi=_safe_int(rec.get("group_index"),0); si=_safe_int(rec.get("series_index_in_group"),0); sw=_safe_int(rec.get("sweep_index"),0)
            candidates=list(unused)
            if gi>0: candidates=[p for p in candidates if f"group_{gi:03d}_" in p.as_posix().lower()]
            if si>=0 and gi>0: candidates=[p for p in candidates if f"series_{si:03d}_" in p.as_posix().lower()]
            if sw>=0 and gi>0: candidates=[p for p in candidates if f"sweep_{sw:03d}" in p.as_posix().lower()]
            if not candidates:
                # Technique/name fallback for non-HEKA tables.
                t=normalize_technique(rec, Path(str(rec.get("name") or "table.csv")), rec.get("columns") or [])
                candidates=[p for p in unused if normalize_technique({},p,rec.get("columns") or []) in {t,"OTHER","UNKNOWN_HEKA"}]
            if candidates:
                chosen=candidates[0]; unused.remove(chosen); pairs.append((chosen,rec))
        if pairs:
            warnings.append(f"Reassociated {len(pairs)} canonical table(s) from portable cache hierarchy; stale absolute paths were ignored.")

    media_records = [r for r in manifest.get("data_structure", {}).get("records", []) if isinstance(r, dict) and r.get("record_type") == "media" and r.get("included_in_h5", True)] if isinstance(manifest, dict) else []
    for idx, path in enumerate(extracted_media):
        if not path.exists():
            continue
        meta = dict(media_records[idx]) if idx < len(media_records) else {"record_type": "media", "role": "media"}
        pairs.append((path, meta))
    return manifest, pairs, warnings



def _repair_heka_acquisition_blocks(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Repair cached HEKA point blocks before browser grouping.

    NewPoint/Takedata groups are acquisition anchors. Older v208-v210 bundles
    sometimes contained a synthetic aggregated EIS table under a NewPoint
    group. Those rows are useful only for inferring the missing point boundary;
    they are never shown as EIS or repurposed as Takedata data. A raw DAT
    re-export is required to recover missing Takedata traces.
    """
    heka = [r for r in records if r.get("instrument") == "HEKA" or r.get("heka_v216") or r.get("heka_v211") or r.get("heka_v210") or r.get("heka_v209") or r.get("heka_v208")]
    if not heka:
        return records

    group_rows: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for r in heka:
        group_rows[_safe_int(r.get("group_index"), 0)].append(r)

    anchors: List[int] = []
    suppressed_ids: set[int] = set()
    for gi, rows in sorted(group_rows.items()):
        gtitle = " ".join(str(r.get("group_title") or "") for r in rows).lower()
        is_newpoint_group = "newpoint" in gtitle
        for r in rows:
            stitle = str(r.get("series_title") or "").lower()
            is_takedata = "takedata" in stitle or r.get("technique_key") == "POSITION"
            is_old_false_eis = (
                is_newpoint_group
                and r.get("technique_key") == "EIS"
                and _safe_int(r.get("series_index_in_group"), _safe_int(r.get("series_index"), 0)) == 0
                and "aggregated" in stitle
            )
            if is_takedata or is_old_false_eis:
                anchors.append(gi)
            if is_takedata:
                r["technique_key"] = "POSITION"
                r["technique_label"] = TECHNIQUE_SPECS["POSITION"]["label"]
                r["technique_order"] = TECHNIQUE_SPECS["POSITION"]["order"]
                r["plot_mode"] = "Numeric"
                r["method_title"] = "Takedata acquisition position"
                r["method_id"] = "Takedata"
                r["evidence"] = list(r.get("evidence") or []) + ["NewPoint/Takedata is an acquisition-position anchor"]
            elif is_old_false_eis:
                suppressed_ids.add(id(r))
                r["evidence"] = list(r.get("evidence") or []) + [
                    "Suppressed stale NewPoint aggregated-EIS cache artifact; re-export raw DAT to recover Takedata"
                ]

    anchors = sorted(set(g for g in anchors if g > 0))
    if anchors:
        for r in heka:
            if id(r) in suppressed_ids:
                continue
            if r.get("point_from_canonical_key"):
                continue
            metadata = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
            explicit_point = metadata.get("point")
            if (r.get("heka_v216") or r.get("heka_v211")) and explicit_point not in (None, ""):
                r["point"] = _safe_int(explicit_point, 0)
                continue
            gi = _safe_int(r.get("group_index"), 0)
            r["point"] = max(1, sum(1 for g in anchors if g <= gi))

    for r in heka:
        if id(r) in suppressed_ids:
            continue
        if r.get("technique_key") in {"CV", "CV/LSV", "LSV", "FSCV"} and not str(r.get("scan_rate") or "").strip():
            m = re.search(r"(?i)([0-9]+(?:\.[0-9]+)?)\s*(mV|V)", " ".join((str(r.get("group_title") or ""), str(r.get("series_title") or ""))))
            if m:
                value = float(m.group(1)) * (1000.0 if m.group(2).lower() == "v" else 1.0)
                r["scan_rate"] = f"{value:g} mV/s"

    return [r for r in records if id(r) not in suppressed_ids]


def _assign_method_numbers(records: List[Dict[str, Any]]) -> None:
    by_tech: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rec in records:
        by_tech[rec["technique_key"]].append(rec)
    for tech, rows in by_tech.items():
        rows.sort(key=lambda r: (r["source_order"], r["path"].lower()))
        used: Dict[str, int] = {}
        occupied: set[int] = set()
        # Preserve explicit non-HEKA method numbers where they are meaningful.
        for rec in rows:
            pref = rec.get("preferred_method_index")
            sig = rec["method_signature"]
            if pref and sig not in used and int(pref) > 0 and int(pref) not in occupied:
                used[sig] = int(pref); occupied.add(int(pref))
        next_index = 1
        for rec in rows:
            sig = rec["method_signature"]
            if sig not in used:
                while next_index in occupied:
                    next_index += 1
                used[sig] = next_index; occupied.add(next_index); next_index += 1
            rec["method_index"] = used[sig]
            title = str(rec.get("method_title") or "").strip()
            generic = _norm(title) in {"", "method", f"method{used[sig]}"}
            rec["method_label"] = f"Method {used[sig]:02d}" + ("" if generic else f" — {title}")


def build_catalog(
    cache_root: Path | str,
    bundle_root: Optional[Path | str] = None,
    manifest: Optional[Mapping[str, Any]] = None,
    base_name: str = "",
    fallback_collector: Optional[Callable[..., Iterable[Mapping[str, Any]]]] = None,
) -> Dict[str, Any]:
    cache_root = Path(cache_root).resolve()
    bundle_root_path = Path(bundle_root).resolve() if bundle_root else cache_root
    canonical_manifest, mapped, warnings = _load_bundle_records(cache_root)
    effective_manifest: Mapping[str, Any] = canonical_manifest or manifest or {}
    records: List[Dict[str, Any]] = []
    seen: set[str] = set()
    seen_basenames: set[str] = set()

    def _dedupe_basename(path_or_name: Any) -> str:
        """Normalize historical duplicate suffixes such as .csv.csv."""
        name = Path(str(path_or_name)).name.lower()
        for suffix in (".csv", ".tsv", ".txt", ".asc"):
            doubled = suffix + suffix
            while name.endswith(doubled):
                name = name[:-len(suffix)]
        return name

    order = 0
    for path, meta in mapped:
        order += 1
        rec = _normalize_record(path, meta, effective_manifest, order)
        key = rec["path"].lower()
        if key not in seen:
            records.append(rec); seen.add(key); seen_basenames.add(_dedupe_basename(path.name))

    fallback_rows: Iterable[Mapping[str, Any]] = []
    if callable(fallback_collector):
        try:
            fallback_rows = fallback_collector(cache_root, bundle_root_path, effective_manifest, base_name) or []
        except Exception as exc:
            warnings.append(f"Fallback browser collector failed: {exc}")
    if not mapped:
        # A canonical cache may be absent for a legacy folder.  Scan supported tables.
        candidates = []
        for ext in ("*.csv", "*.tsv", "*.txt", "*.asc", "*.xlsx", "*.xls", "*.png", "*.jpg", "*.jpeg", "*.tif", "*.tiff", "*.bmp", "*.gif", "*.webp", "*.mp4", "*.avi", "*.mov", "*.mkv", "*.wmv", "*.mpeg", "*.mpg", "*.m4v", "*.webm"):
            candidates.extend(cache_root.rglob(ext))
        fallback_rows = list(fallback_rows) + [{"path": p} for p in candidates]

    for raw in fallback_rows:
        try:
            p = Path(raw.get("path") or "").resolve()
        except Exception:
            continue
        if not p.exists() or not p.is_file():
            continue
        key = str(p).lower()
        # The fallback collector points to original analysis files while the
        # canonical mapping points to their extracted cache copies. Identical
        # basenames represent the same scientific record and must not appear
        # twice in the browser after an H5 rebuild.
        if key in seen or _dedupe_basename(p.name) in seen_basenames:
            continue
        meta = dict(raw)
        # Normalize the historical collector's names into the unified contract.
        if meta.get("tech") and not meta.get("technique"):
            meta["technique"] = meta.get("tech")
        order += 1
        rec = _normalize_record(p, meta, effective_manifest, order)
        records.append(rec); seen.add(key); seen_basenames.add(_dedupe_basename(p.name))

    _before_heka_repair = len(records)
    records = _repair_heka_acquisition_blocks(records)
    if len(records) < _before_heka_repair:
        warnings.append(
            f"Suppressed {_before_heka_repair - len(records)} stale NewPoint aggregated-EIS cache artifact(s). "
            "Re-export the original HEKA DAT2 file to recover any Takedata traces missing from an older H5 bundle."
        )
    sequence_role_summary = {}
    try:
        from .sequence_roles_v297 import assign_sequence_roles
        sequence_role_summary = assign_sequence_roles(records)
    except Exception as exc:
        warnings.append(f"Could not attach v297 pre/post electrochemistry sequence roles: {exc}")
    _assign_method_numbers(records)
    # v297: propagate Takedata coordinates with duplicate-position cleanup to
    # every record at the same scientific point and calculate cumulative scan
    # distance. Synthetic compatibility XYZ is retained with explicit provenance when physical coordinates are unavailable.
    spatial_summary = {}
    try:
        from .spatial_points_v297 import enrich_catalog_records
        spatial_summary = enrich_catalog_records(records, search_roots=[cache_root, bundle_root_path])
    except Exception as exc:
        warnings.append(f"Could not attach v297 Takedata spatial point metadata: {exc}")
    records.sort(key=lambda r: (
        int(r.get("technique_order", 999)), int(r.get("method_index", 1)), int(r.get("point", 0)),
        int(r.get("line", 0)), int(r.get("cycle", 0)), int(r.get("sweep", 0)), int(r.get("source_order", 0)), r["path"].lower(),
    ))
    counts = Counter(r["technique_key"] for r in records)
    method_counts = {tech: len({r["method_index"] for r in records if r["technique_key"] == tech}) for tech in counts}
    point_counts = {tech: len({r["point"] for r in records if r["technique_key"] == tech and int(r["point"]) > 0}) for tech in counts}
    if point_counts.get("CV", 0) > point_counts.get("POSITION", 0) and counts.get("POSITION", 0):
        warnings.append(
            f"This bundle contains CV data for {point_counts.get('CV', 0)} point(s) but Takedata tables for only {point_counts.get('POSITION', 0)} point(s). "
            "The browser preserves the available data; re-export the raw DAT2 file for a complete Takedata point list."
        )
    result = {
        "schema": CATALOG_SCHEMA,
        "version": CATALOG_VERSION,
        "base_name": base_name or str(effective_manifest.get("base_name") or ""),
        "cache_root": str(cache_root),
        "bundle_root": str(bundle_root_path),
        "record_count": len(records),
        "technique_counts": dict(counts),
        "method_counts": method_counts,
        "point_counts": point_counts,
        "spatial_point_summary": spatial_summary,
        "sequence_role_summary": sequence_role_summary,
        "warnings": warnings,
        "records": records,
    }
    try:
        out = cache_root / "_faradaiq_browser_catalog_v216.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["catalog_path"] = str(out)
    except Exception as exc:
        warnings.append(f"Could not write browser catalog cache: {exc}")
    return result


def technique_spec(key: str) -> Dict[str, Any]:
    return dict(TECHNIQUE_SPECS.get(key, TECHNIQUE_SPECS["OTHER"]))


def record_label(record: Mapping[str, Any], include_method: bool = True) -> str:
    parts: List[str] = []
    if include_method:
        parts.append(f"M{_safe_int(record.get('method_index'), 1):02d}")
    if _safe_int(record.get("point"), 0) > 0:
        parts.append(f"P{_safe_int(record.get('point')):03d}")
    if _safe_int(record.get("line"), 0) > 0:
        parts.append(f"L{_safe_int(record.get('line')):03d}")
    if _safe_int(record.get("cycle"), 0) > 0:
        parts.append(f"C{_safe_int(record.get('cycle')):02d}")
    elif _safe_int(record.get("sweep"), 0) > 0:
        parts.append(f"S{_safe_int(record.get('sweep')):02d}")
    if not parts:
        parts.append(Path(str(record.get("path") or "table")).stem[:50])
    return " ".join(parts)
