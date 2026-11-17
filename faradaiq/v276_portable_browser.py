"""Portable comparison-first HTML browser for FaradaIQ Studio v277 outputs.

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
import shutil
import threading
import zipfile
from collections import defaultdict
from datetime import datetime
from itertools import chain
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence
from urllib.parse import quote

VERSION = "v277"
VERSION_NUMBER = 277
SEMVER = "0.277.0"
APP_TITLE = f"FaradaIQ Studio {VERSION}"

HTML_NAME = "FaradaIQ_Data_Browser.html"
MANIFEST_NAME = "faradaiq_portable_browser_manifest.json"
DOWNLOAD_DIR_NAME = "browser_downloads"
ASSET_DIR_NAME = "browser_assets"
MEDIA_ASSET_DIR_NAME = "media"
README_NAME = "README_PORTABLE_BROWSER.txt"
SCHEMA = "faradaiq-portable-browser-v277"
DEFAULT_ENABLED = True
MAX_PREVIEW_ROWS = 640
MAX_PREVIEW_COLUMNS = 12
MAX_FILE_RECORDS = 16000
MAX_BATCHES = 2500
MAX_MEDIA_PREVIEW_PIXELS = 2_560_000

_TABLE_SUFFIXES = {".csv", ".tsv", ".txt", ".dat"}
_FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".tif", ".tiff", ".bmp"}
_BROWSER_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}
_REPORT_SUFFIXES = {".html", ".htm", ".pdf", ".docx", ".pptx", ".xlsx", ".xls"}
_METADATA_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".ini", ".md"}

_STRUCTURED_TABLE_SUFFIXES = _TABLE_SUFFIXES | {".json", ".xlsx"}
_ANALYSIS_HINTS = (
    "analysis", "result", "fit", "fitting", "summary", "metric", "parameter",
    "deareis", "pydrt", "drt", "tafel", "polarization", "corrosion", "cottrell", "scharifker",
    "nucleation", "kinetic", "koutecky", "levich", "mott", "schottky", "ecsa",
    "peak", "map", "profile", "histogram", "statistics", "replicate", "comparison",
)

# Semantic registry used by both the manifest generator and the portable browser.
# The browser is deliberately column-driven: labels and paths help classify a file,
# but scientific column contracts determine which comparisons are actually offered.
TECHNIQUE_REGISTRY: dict[str, dict[str, Any]] = {
    "CV": {"label": "Cyclic voltammetry (CV)", "family": "Voltammetry", "kind": "raw"},
    "FSCV": {"label": "Fast-scan cyclic voltammetry (FSCV)", "family": "Voltammetry", "kind": "raw"},
    "IV": {"label": "Current-voltage / polarization (I–V)", "family": "Voltammetry", "kind": "raw"},
    "RDE": {"label": "Rotating-disk electrode voltammetry (RDE)", "family": "Hydrodynamic voltammetry", "kind": "raw"},
    "RRDE": {"label": "Rotating ring-disk electrode voltammetry (RRDE)", "family": "Hydrodynamic voltammetry", "kind": "raw"},
    "LPR": {"label": "Linear polarization resistance (LPR)", "family": "Polarization", "kind": "raw"},
    "LSV": {"label": "Linear sweep voltammetry (LSV)", "family": "Voltammetry", "kind": "raw"},
    "CV_LSV": {"label": "CV / LSV voltammetry", "family": "Voltammetry", "kind": "raw"},
    "DPV": {"label": "Differential pulse voltammetry (DPV)", "family": "Pulse and stripping", "kind": "raw"},
    "SWV": {"label": "Square-wave voltammetry (SWV)", "family": "Pulse and stripping", "kind": "raw"},
    "NPV": {"label": "Normal pulse voltammetry (NPV)", "family": "Pulse and stripping", "kind": "raw"},
    "STRIPPING": {"label": "Stripping voltammetry", "family": "Pulse and stripping", "kind": "raw"},
    "CA": {"label": "Chronoamperometry (CA)", "family": "Transient", "kind": "raw"},
    "DEPOSITION": {"label": "Electrodeposition transient", "family": "Transient", "kind": "raw"},
    "CP": {"label": "Chronopotentiometry (CP)", "family": "Transient", "kind": "raw"},
    "GCD": {"label": "Galvanostatic charge / discharge", "family": "Transient", "kind": "raw"},
    "OCP": {"label": "Open-circuit potential (OCP)", "family": "Transient", "kind": "raw"},
    "ZRA": {"label": "Zero-resistance ammetry (ZRA)", "family": "Transient", "kind": "raw"},
    "GITT": {"label": "Galvanostatic intermittent titration (GITT)", "family": "Intermittent titration", "kind": "raw"},
    "PITT": {"label": "Potentiostatic intermittent titration (PITT)", "family": "Intermittent titration", "kind": "raw"},
    "EIS": {"label": "Electrochemical impedance spectroscopy (EIS)", "family": "Impedance", "kind": "raw"},
    "GEIS": {"label": "Galvanostatic EIS (GEIS)", "family": "Impedance", "kind": "raw"},
    "DRT": {"label": "Distribution of relaxation times (DRT)", "family": "DRT", "kind": "analysis"},
    "TAFEL": {"label": "Tafel / polarization", "family": "Polarization", "kind": "analysis"},
    "EQCM": {"label": "Electrochemical quartz crystal microbalance (EQCM)", "family": "Mass-coupled electrochemistry", "kind": "raw"},
    "CONDUCTIVITY": {"label": "Conductivity / conductance", "family": "Transport", "kind": "raw"},
    "EIS_ANALYSIS": {"label": "EIS fit and parameter analysis", "family": "Impedance analysis", "kind": "analysis"},
    "DRT_ANALYSIS": {"label": "DRT peak and parameter analysis", "family": "DRT", "kind": "analysis"},
    "VOLTAMMETRY_ANALYSIS": {"label": "Voltammetry metrics and peak analysis", "family": "Voltammetry analysis", "kind": "analysis"},
    "TRANSIENT_ANALYSIS": {"label": "CA / CP / deposition analysis", "family": "Transient analysis", "kind": "analysis"},
    "TAFEL_ANALYSIS": {"label": "Tafel fit comparison and diagnostics", "family": "Polarization", "kind": "analysis"},
    "BATTERY_ANALYSIS": {"label": "Battery / cycling metrics", "family": "Battery analysis", "kind": "analysis"},
    "KINETICS_ANALYSIS": {"label": "Diffusion and electron-transfer kinetics", "family": "Kinetics analysis", "kind": "analysis"},
    "EQCM_ANALYSIS": {"label": "EQCM mass/charge analysis", "family": "Mass-coupled electrochemistry", "kind": "analysis"},
    "NUCLEATION_ANALYSIS": {"label": "Nucleation and growth model analysis", "family": "Transient analysis", "kind": "analysis"},
    "CORROSION_ANALYSIS": {"label": "Corrosion, LPR, and polarization analysis", "family": "Polarization analysis", "kind": "analysis"},
    "RDE_ANALYSIS": {"label": "RDE/RRDE Levich and Koutecky–Levich analysis", "family": "Hydrodynamic analysis", "kind": "analysis"},
    "SEMICONDUCTOR_ANALYSIS": {"label": "Mott–Schottky and semiconductor analysis", "family": "Semiconductor analysis", "kind": "analysis"},
    "ECSA_ANALYSIS": {"label": "Electrochemically active surface-area analysis", "family": "Surface-area analysis", "kind": "analysis"},
    "TRANSPORT_ANALYSIS": {"label": "Diffusion, conductivity, and transport analysis", "family": "Transport analysis", "kind": "analysis"},
    "STATISTICS": {"label": "Statistical / replicate comparison", "family": "Analysis summaries", "kind": "analysis"},
    "SPATIAL_MAP": {"label": "Spatial map / point comparison", "family": "Spatial", "kind": "analysis"},
    "PROFILE": {"label": "Line profile / ordered position", "family": "Spatial", "kind": "analysis"},
    "APPROACH": {"label": "Approach curve", "family": "Scanning probe / SECCM", "kind": "raw"},
    "POSITION": {"label": "Position / motion trace", "family": "Scanning probe / SECCM", "kind": "raw"},
    "SUMMARY": {"label": "Derived metrics / summary table", "family": "Analysis summaries", "kind": "analysis"},
    "PHOTO": {"label": "Photos / images", "family": "Media", "kind": "media"},
    "FIGURE": {"label": "Generated figures", "family": "Media", "kind": "media"},
    "OTHER": {"label": "Other numeric data", "family": "Other", "kind": "data"},
}

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "time": ("time_s", "time", "elapsed_s", "elapsed_time_s", "duration_s", "t_s"),
    "potential": ("potential_v", "potential", "voltage_v", "e_v", "working_electrode_potential_v", "eta_v", "overpotential_v"),
    "overpotential": ("eta_v", "overpotential_v", "overpotential", "eta"),
    "current": ("current_a", "current", "i_a", "i", "measured_current_a"),
    "current_density": ("current_density_a_cm2", "j_a_cm2", "j", "currentdensity", "current_density"),
    "charge": ("charge_c", "charge", "q_c", "cumulative_charge_c", "capacity_c"),
    "capacity": ("capacity_ah", "capacity_mah", "specific_capacity_mah_g", "capacity_c", "charge_c"),
    "frequency": ("frequency_hz", "frequency", "freq_hz", "freq", "f_hz"),
    "zreal": ("z_real_ohm", "zreal_ohm", "zreal", "real_ohm", "re_z", "z_re"),
    "zimag": ("z_imag_ohm", "zimag_ohm", "zimag", "imag_ohm", "im_z", "z_im"),
    "zmag": ("z_magnitude_ohm", "zmag_ohm", "impedance_magnitude_ohm", "abs_z", "mod_z", "magnitude_ohm"),
    "phase": ("phase_deg", "phase", "phase_angle_deg", "z_phase_deg"),
    "tau": ("tau_s", "tau", "relaxation_time_s", "relaxation_time"),
    "gamma": ("gamma_ohm", "gamma", "drt_gamma", "distribution", "drt_amplitude"),
    "log_current": ("log10_abs_j_a_cm2", "log10_abs_current", "log10_i", "log_current", "log10_current_density"),
    "mass": ("mass_change_ng", "mass_change_ug", "mass_change_g", "mass_ng", "mass_ug", "delta_mass", "dmass", "mass"),
    "frequency_shift": ("frequency_shift_hz", "delta_frequency_hz", "df_hz", "frequency_change_hz"),
    "conductivity": ("conductivity_s_cm", "conductivity_ms_cm", "conductivity", "conductance_s", "conductance"),
    "resistance": ("resistance_ohm", "resistance", "r_ohm"),
    "energy": ("energy_wh", "specific_energy_wh_kg", "energy_j", "energy"),
    "power": ("power_w", "specific_power_w_kg", "power"),
    "efficiency": ("coulombic_efficiency_pct", "energy_efficiency_pct", "efficiency_pct", "efficiency"),
    "temperature": ("temperature_c", "temperature_k", "temp_c", "temperature"),
    "rotation_rate": ("rotation_rate_rpm", "rotation_rpm", "rpm", "angular_velocity_rad_s", "omega_rad_s"),
    "capacitance": ("capacitance_f", "capacitance", "cdl_f", "c_dl_f", "space_charge_capacitance_f"),
    "inverse_capacitance_squared": ("inverse_capacitance_squared", "inv_c2", "1_c2", "c_minus_2", "one_over_c_squared"),
    "ring_current": ("ring_current_a", "i_ring_a", "ring_current", "rrde_ring_current_a"),
    "disk_current": ("disk_current_a", "i_disk_a", "disk_current", "rrde_disk_current_a"),
    "point": ("point", "point_index", "point_id", "site", "pixel", "record_index"),
    "cycle": ("cycle", "cycle_index", "cycle_number"),
    "sweep": ("sweep", "sweep_index", "segment", "segment_index"),
    "scan_rate": ("scan_rate_v_s", "scan_rate_mv_s", "scan_rate", "scanrate"),
    "x_position": ("x_position", "x_position_m", "x_m", "x_position_um", "x_um", "x_mm", "x", "position_x"),
    "y_position": ("y_position", "y_position_um", "y_m", "y_um", "y_mm", "y", "position_y"),
    "z_position": ("z_position", "z_position_m", "z_position_um", "z_m", "z_um", "z_mm", "position_z"),
    "distance": ("distance", "distance_um", "distance_mm", "position", "position_um", "arc_length"),
}


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



def _column_lookup(columns: Sequence[Any]) -> dict[str, int]:
    return {_normalize_column(c): i for i, c in enumerate(columns)}


def _find_column(columns: Sequence[Any], semantic_or_aliases: str | Sequence[str]) -> int | None:
    aliases = COLUMN_ALIASES.get(str(semantic_or_aliases), ()) if isinstance(semantic_or_aliases, str) else tuple(semantic_or_aliases)
    if not aliases and isinstance(semantic_or_aliases, str):
        aliases = (semantic_or_aliases,)
    norms = [_normalize_column(a) for a in aliases]
    by_norm = _column_lookup(columns)
    for alias in norms:
        if alias in by_norm:
            return by_norm[alias]
    for idx, col in enumerate(columns):
        norm = _normalize_column(col)
        for alias in norms:
            if alias and len(alias) >= 3 and (alias in norm or norm in alias):
                return idx
    return None


def _contains_token(text: str, token: str) -> bool:
    return bool(re.search(rf"(?<![A-Z0-9]){re.escape(token.upper())}(?![A-Z0-9])", text.upper()))


def _analysis_hint(text: str) -> bool:
    low = text.lower()
    return any(h in low for h in _ANALYSIS_HINTS)


def _technique_key(value: Any, *, path: Any = "", columns: Sequence[Any] = (), role: Any = "") -> str:
    """Classify raw techniques and derived analysis outputs.

    Path/label aliases are useful, but column contracts take priority so the
    portable browser remains robust to LLM-generated filenames and renamed folders.
    """
    label = str(value or "").strip()
    text = " ".join((label, str(path or ""), str(role or ""))).replace("\\", "/")
    upper = text.upper()
    word_upper = re.sub(r"[^A-Z0-9]+", " ", upper).strip()
    norms = {_normalize_column(c) for c in columns}

    def has(semantic: str) -> bool:
        return any(_normalize_column(a) in norms for a in COLUMN_ALIASES.get(semantic, ()))

    # Media is never treated as a numeric table.
    suffix = Path(str(path or label)).suffix.lower()
    if suffix in _FIGURE_SUFFIXES:
        return "PHOTO" if any(k in upper for k in ("PHOTO", "IMAGE", "FRAME", "CAMERA")) else "FIGURE"

    analysis_context = _analysis_hint(text) or "/analysis/" in f"/{str(path or '').replace(chr(92), '/').lower().strip('/')}"
    sequential_raw = has("time") and (has("potential") or has("current") or has("current_density"))

    # Recognize derived analyses before broad raw-data contracts. This keeps an
    # EQCM summary from being labeled as a raw EQCM transient and connects common
    # electrochemical model outputs even when an LLM chooses an unfamiliar filename.
    if analysis_context:
        if any(k in word_upper for k in ("BATTERY", "CYCLING", "CAPACITY RETENTION", "COULOMBIC EFFICIENCY")) and not sequential_raw:
            return "BATTERY_ANALYSIS"
        if any(k in word_upper for k in ("KOUTECKY", "LEVICH", "RDE ANALYSIS", "RRDE ANALYSIS", "ROTATING DISK")):
            return "RDE_ANALYSIS"
        if any(k in word_upper for k in ("MOTT SCHOTTKY", "MOTT", "FLAT BAND", "DONOR DENSITY", "SEMICONDUCTOR")):
            return "SEMICONDUCTOR_ANALYSIS"
        if any(k in word_upper for k in ("ECSA", "ACTIVE SURFACE AREA", "DOUBLE LAYER CAPACITANCE")) and not sequential_raw:
            return "ECSA_ANALYSIS"
        if any(k in word_upper for k in ("NUCLEATION", "SCHARIFKER", "HILLS", "GROWTH MODEL")):
            return "NUCLEATION_ANALYSIS"
        if any(k in word_upper for k in ("CORROSION", "LPR ANALYSIS", "POLARIZATION RESISTANCE")) and not (has("potential") and (has("current") or has("current_density"))):
            return "CORROSION_ANALYSIS"
        if any(k in word_upper for k in ("KINETICS", "KHET", "K HET", "NICHOLSON", "LAVAGNINI", "RANDLES SEVCIK", "DIFFUSION COEFFICIENT")) and not sequential_raw:
            return "KINETICS_ANALYSIS"
        if any(k in word_upper for k in ("TRANSPORT", "CONDUCTIVITY SUMMARY", "MOBILITY", "TRANSFERENCE")) and not sequential_raw:
            return "TRANSPORT_ANALYSIS"
        if any(k in word_upper for k in ("STATISTICS", "REPLICATE", "ANOVA", "CONFIDENCE INTERVAL", "STANDARD DEVIATION")):
            return "STATISTICS"
        if any(k in word_upper for k in ("EQCM ANALYSIS", "EQCM SUMMARY", "SAUERBREY", "MASS PER CHARGE")) and not sequential_raw:
            return "EQCM_ANALYSIS"

    # Strong scientific column contracts.
    if has("tau") and has("gamma"):
        return "DRT"
    if has("zreal") and has("zimag"):
        return "GEIS" if _contains_token(upper, "GEIS") or "GALVANOSTATIC EIS" in upper else "EIS"
    if has("frequency") and (has("zmag") or has("phase")):
        return "GEIS" if _contains_token(upper, "GEIS") else "EIS"
    if has("distance") and (has("current") or has("potential")):
        return "APPROACH"
    if (has("x_position") or has("y_position") or has("z_position")) and any(k in word_upper for k in ("POSITION", "TAKEDATA", "MOTION")):
        return "POSITION"
    if has("mass") or has("frequency_shift"):
        return "EQCM"
    if has("conductivity"):
        return "CONDUCTIVITY"

    metric_tokens = {
        "rct", "rs", "cpe", "cdl", "chi2", "chisquared", "aic", "bic", "residual",
        "peaktaus", "peaktau", "peakgamma", "integratedarea", "fwhm", "nucleationtime",
        "chargec", "transitiontimes", "driftpers", "diffusioncoefficient", "khet",
        "tafel_slope", "tafelslope", "exchange_current", "j0", "corrosion_current",
        "peakcurrent", "peakpotential", "deltapeak", "ep", "ip", "diffusion", "nicholson",
        "lavagnini", "randles", "warburg", "capacitance", "inductance", "conductivity",
        "coulombicefficiency", "energyefficiency", "capacityretention", "specificcapacity",
        "masschange", "masspercharge", "sauerbrey", "roughness", "coverage", "uncertainty",
    }
    has_metric_token = any(any(tok in n for tok in metric_tokens) for n in norms)
    if any(k in word_upper for k in ("GITT ANALYSIS", "PITT ANALYSIS", "BATTERY ANALYSIS", "BATTERY CYCLE", "CYCLING SUMMARY", "CAPACITY RETENTION")) and has_metric_token and not (has("time") and (has("potential") or has("current"))):
        return "BATTERY_ANALYSIS"
    if any(k in word_upper for k in ("RANDLES", "NICHOLSON", "LAVAGNINI", "COTTRELL DIFFUSION", "KINETICS ANALYSIS", "KINETICS SUMMARY", "K HET", "KHET")) and has_metric_token:
        return "KINETICS_ANALYSIS"
    if any(k in word_upper for k in ("EQCM ANALYSIS", "SAUERBREY", "MASS PER CHARGE")) and has_metric_token:
        return "EQCM_ANALYSIS"
    if any(k in word_upper for k in ("STATISTICS", "REPLICATE", "ANOVA", "CONFIDENCE INTERVAL")) and has_metric_token:
        return "STATISTICS"
    if ("DRT" in word_upper or any("tau" in n or "gamma" in n for n in norms)) and has_metric_token:
        return "DRT_ANALYSIS"
    if any(k in word_upper for k in ("DEAREIS", "EIS FIT", "IMPEDANCE FIT", "CIRCUIT")) and has_metric_token:
        return "EIS_ANALYSIS"
    if "TAFEL" in word_upper and (has_metric_token or any(k in n for n in norms for k in ("slope", "alpha", "log10j0", "window"))):
        return "TAFEL_ANALYSIS"
    if any(k in word_upper for k in ("CA CP ANALYSIS", "TRANSIENT ANALYSIS", "COTTRELL", "SCHARIFKER", "NUCLEATION")) and has_metric_token:
        return "TRANSIENT_ANALYSIS"
    if any(k in word_upper for k in ("PEAK ANALYSIS", "VOLTAMMETRY ANALYSIS", "RANDLES", "NICHOLSON", "LAVAGNINI")) and has_metric_token:
        return "VOLTAMMETRY_ANALYSIS"

    # Preserve a catalog's explicit technique only when the actual columns satisfy
    # that technique's minimum data contract. This prevents a HEKA CA table from
    # becoming CP merely because its method path contains "ChronoPot", while also
    # preventing an approach curve mislabeled CV/LSV from receiving a fake potential axis.
    label_words = re.sub(r"[^A-Z0-9]+", " ", label.upper()).strip()
    explicit_candidates = (
        ("FSCV", ("FSCV",)), ("CV", ("CV", "CYCLIC VOLTAMMETRY")),
        ("RRDE", ("RRDE", "ROTATING RING DISK")), ("RDE", ("RDE", "ROTATING DISK")),
        ("LPR", ("LPR", "LINEAR POLARIZATION RESISTANCE")),
        ("LSV", ("LSV", "LINEAR SWEEP")), ("CV_LSV", ("CV LSV",)),
        ("DPV", ("DPV", "DIFFERENTIAL PULSE")), ("SWV", ("SWV", "SQUARE WAVE")),
        ("NPV", ("NPV", "NORMAL PULSE")), ("STRIPPING", ("STRIPPING",)),
        ("CA", ("CA", "CHRONOAMPEROMETRY")), ("DEPOSITION", ("DEPOSITION", "ELECTRODEPOSITION")),
        ("CP", ("CP", "CHRONOPOTENTIOMETRY")), ("GCD", ("GCD",)), ("OCP", ("OCP",)),
        ("GITT", ("GITT",)), ("PITT", ("PITT",)), ("ZRA", ("ZRA",)),
        ("EIS", ("EIS",)), ("GEIS", ("GEIS",)), ("DRT", ("DRT",)),
        ("TAFEL", ("TAFEL",)), ("EQCM", ("EQCM",)), ("CONDUCTIVITY", ("CONDUCTIVITY",)),
        ("APPROACH", ("APPROACH",)), ("POSITION", ("POSITION", "TAKEDATA")),
    )
    def compatible(key: str) -> bool:
        if key in {"CV", "FSCV", "LSV", "CV_LSV", "DPV", "SWV", "NPV", "STRIPPING", "IV", "RDE", "RRDE", "LPR"}:
            return has("potential") and (has("current") or has("current_density"))
        if key in {"CA", "DEPOSITION", "PITT"}:
            return has("time") and (has("current") or has("current_density"))
        if key in {"CP", "GCD", "OCP", "GITT", "ZRA"}:
            return has("time") and has("potential")
        if key in {"EIS", "GEIS"}:
            return has("frequency") and ((has("zreal") and has("zimag")) or has("zmag") or has("phase"))
        if key == "DRT": return has("tau") and has("gamma")
        if key == "TAFEL": return (has("overpotential") or has("potential")) and (has("current") or has("current_density") or has("log_current"))
        if key == "EQCM": return has("mass") or has("frequency_shift")
        if key == "CONDUCTIVITY": return has("conductivity")
        if key == "APPROACH": return has("distance") and (has("current") or has("potential"))
        if key == "POSITION": return has("x_position") or has("y_position") or has("z_position")
        return True
    for key, aliases in explicit_candidates:
        if any(label_words == alias or f" {alias} " in f" {label_words} " for alias in aliases) and compatible(key):
            return key

    # Explicit technique aliases, ordered from most specific to broadest.
    alias_order = (
        ("APPROACH", ("APPROACH CURVE", "APPROACH")),
        ("POSITION", ("POSITION TAKEDATA", "TAKEDATA", "POSITION TRACE")),
        ("FSCV", ("FSCV", "FAST SCAN CYCLIC", "FAST-SCAN CYCLIC")),
        ("RRDE", ("RRDE", "ROTATING RING DISK", "ROTATING RING-DISK")),
        ("RDE", ("RDE", "ROTATING DISK", "ROTATING-DISK")),
        ("LPR", ("LPR", "LINEAR POLARIZATION RESISTANCE")),
        ("GITT", ("GITT", "GALVANOSTATIC INTERMITTENT")),
        ("PITT", ("PITT", "POTENTIOSTATIC INTERMITTENT")),
        ("EQCM", ("EQCM", "QUARTZ CRYSTAL MICROBALANCE", "QCM")),
        ("ZRA", ("ZRA", "ZERO RESISTANCE AMMETRY", "ZERO-RESISTANCE AMMETRY")),
        ("CONDUCTIVITY", ("CONDUCTIVITY", "CONDUCTANCE")),
        ("STRIPPING", ("STRIPPING", "ANODIC STRIP", "CATHODIC STRIP", "ANODIC STRIPPING VOLTAMMETRY", "CATHODIC STRIPPING VOLTAMMETRY")),
        ("DPV", ("DPV", "DIFFERENTIAL PULSE")),
        ("SWV", ("SWV", "SQUARE WAVE")),
        ("NPV", ("NPV", "NORMAL PULSE")),
        ("DEPOSITION", ("DEPOSITION", "ELECTRODEPOSITION", "PLATING")),
        ("TAFEL", ("TAFEL", "POLARIZATION")),
        ("GEIS", ("GEIS", "GALVANOSTATIC EIS")),
        ("EIS", ("EIS", "IMPEDANCE", "NYQUIST", "BODE")),
        ("GCD", ("GCD", "CHARGE DISCHARGE", "GALVANOSTATIC CHARGE")),
        ("OCP", ("OCP", "OPEN CIRCUIT", "OPEN-CIRCUIT")),
        ("CP", ("CHRONOPOTENTIOMETRY", "CHRONOPOT", " CP ")),
        ("CA", ("CHRONOAMPEROMETRY", "CHRONOAMP", " CA ")),
        ("LSV", ("LSV", "LINEAR SWEEP")),
        ("IV", ("I-V", " I V ", "CURRENT VOLTAGE", "CURRENT-VOLTAGE")),
        ("CV", ("CYCLIC VOLTAMMETRY", " CV ")),
    )
    padded = f" {word_upper} "
    for key, aliases in alias_order:
        matched = False
        for alias in aliases:
            alias_words = re.sub(r"[^A-Z0-9]+", " ", alias.upper()).strip()
            if alias_words and f" {alias_words} " in padded:
                matched = True
                break
        if matched:
            return key

    # Column-driven fallback for files with arbitrary generated names.
    if has("distance") and (has("current") or has("potential")):
        return "APPROACH"
    if (has("x_position") or has("y_position") or has("z_position")) and any(k in word_upper for k in ("POSITION", "TAKEDATA", "MOTION")):
        return "POSITION"
    if has("mass") or has("frequency_shift"):
        return "EQCM"
    if has("conductivity"):
        return "CONDUCTIVITY"
    if has("overpotential") and (has("log_current") or has("current") or has("current_density")):
        return "TAFEL"
    if has("potential") and has("current"):
        return "CV_LSV"
    if has("time") and has("current"):
        return "CA"
    if has("time") and has("potential"):
        return "CP"

    x_pos, y_pos = has("x_position"), has("y_position")
    if x_pos and y_pos:
        return "SPATIAL_MAP"
    if has("distance"):
        return "PROFILE"
    if has_metric_token or _analysis_hint(text):
        return "SUMMARY"
    return "OTHER"

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



def _plot_views(technique: str, columns: Sequence[str], numeric: Sequence[Mapping[str, Any]], *, path_hint: str = "") -> list[dict[str, Any]]:
    """Return every scientifically meaningful view supported by the table.

    Multiple views can be attached to one file, for example Nyquist, Bode
    magnitude, Bode phase, and frequency-resolved real/imaginary impedance for
    the same EIS spectrum.  Generic views are added only after technique-aware
    views so arbitrary analysis outputs remain visible without overriding the
    correct electrochemical representation.
    """
    numeric_idx = [int(v["index"]) for v in numeric if float(v.get("coverage", 0)) >= 0.35]
    varying_idx = [int(v["index"]) for v in numeric if bool(v.get("varying")) and float(v.get("coverage", 0)) >= 0.35]
    views: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    def idx(name: str) -> int | None:
        return _find_column(columns, name)

    def add(view_id: str, label: str, x: int | None, y: int | None, *,
            x_transform: str = "identity", y_transform: str = "identity",
            x_scale: str = "linear", y_scale: str = "linear",
            plot_type: str = "curve", description: str = "", priority: int = 50,
            allow_synthetic_x: bool = False) -> None:
        if y is None or y not in numeric_idx:
            return
        if x is None and not allow_synthetic_x:
            return
        synthetic_x = x is None
        if x is not None and x not in numeric_idx:
            return
        key = (x, y, x_transform, y_transform, plot_type)
        if key in seen:
            return
        seen.add(key)
        views.append({
            "id": view_id, "label": label,
            "x_index": x, "y_index": y,
            "x_column": columns[x] if x is not None and x < len(columns) else "row_index",
            "y_column": columns[y] if y < len(columns) else "",
            "x_transform": x_transform, "y_transform": y_transform,
            "x_scale": x_scale, "y_scale": y_scale,
            "plot_type": plot_type, "synthetic_x": synthetic_x,
            "description": description, "priority": priority,
        })

    time_i, potential_i, current_i = idx("time"), idx("potential"), idx("current")
    current_density_i, charge_i, capacity_i = idx("current_density"), idx("charge"), idx("capacity")
    freq_i, zr_i, zi_i = idx("frequency"), idx("zreal"), idx("zimag")
    zmag_i, phase_i, tau_i, gamma_i = idx("zmag"), idx("phase"), idx("tau"), idx("gamma")
    eta_i, log_i = idx("overpotential"), idx("log_current")
    point_i, scan_i = idx("point"), idx("scan_rate")
    mass_i, fshift_i, conductivity_i = idx("mass"), idx("frequency_shift"), idx("conductivity")
    resistance_i, energy_i, power_i = idx("resistance"), idx("energy"), idx("power")
    efficiency_i, temperature_i = idx("efficiency"), idx("temperature")
    xpos_i, ypos_i, zpos_i, distance_i = idx("x_position"), idx("y_position"), idx("z_position"), idx("distance")

    if technique in {"CV", "FSCV", "LSV", "CV_LSV", "IV", "RDE", "RRDE", "LPR", "DPV", "SWV", "NPV", "STRIPPING"}:
        add("potential_current", "Potential vs current", potential_i, current_i, priority=1,
            description="Primary voltammetry comparison")
        add("potential_current_density", "Potential vs current density", potential_i, current_density_i, priority=2)
        add("time_current", "Time vs current", time_i, current_i, priority=15)
        add("scanrate_peak", "Scan rate vs current / peak metric", scan_i, current_i, priority=20)
    elif technique in {"CA", "DEPOSITION", "PITT"}:
        add("time_current", "Time vs current", time_i, current_i, priority=1)
        add("time_current_density", "Time vs current density", time_i, current_density_i, priority=2)
        add("time_charge", "Time vs charge", time_i, charge_i, priority=3)
        add("anson", "Anson: t¹ᐟ² vs charge", time_i, charge_i, x_transform="sqrt_positive", priority=7,
            description="Diagnostic Anson representation; interpretation requires applicable diffusion and background assumptions")
        add("cottrell", "Cottrell: t⁻¹ᐟ² vs current", time_i, current_i, x_transform="inverse_sqrt_positive", priority=8,
            description="Diagnostic Cottrell representation; interpretation requires applicable diffusion assumptions")
    elif technique in {"CP", "GCD", "OCP", "GITT", "ZRA"}:
        add("time_potential", "Time vs potential", time_i, potential_i, priority=1)
        cap_or_charge = capacity_i if capacity_i is not None else charge_i
        add("capacity_potential", "Capacity / charge vs potential", cap_or_charge, potential_i, priority=2)
        add("time_current", "Time vs current", time_i, current_i, priority=10)
        add("time_charge", "Time vs charge / capacity", time_i, cap_or_charge, priority=11)
        add("sqrt_time_potential", "t¹ᐟ² vs potential", time_i, potential_i, x_transform="sqrt_positive", priority=12,
            description="Diagnostic square-root-time representation for CP/GITT/PITT transients")
    elif technique == "APPROACH":
        add("distance_current", "Distance vs current", distance_i, current_i, priority=1)
        add("distance_potential", "Distance vs potential", distance_i, potential_i, priority=2)
        add("time_current", "Time vs current", time_i, current_i, priority=5)
    elif technique == "POSITION":
        add("time_x_position", "Time vs X position", time_i, xpos_i, priority=1)
        add("time_y_position", "Time vs Y position", time_i, ypos_i, priority=2)
        add("time_z_position", "Time vs Z position", time_i, zpos_i, priority=3)
        add("time_current", "Time vs current", time_i, current_i, priority=5)
        add("time_potential", "Time vs potential", time_i, potential_i, priority=6)
    elif technique in {"EIS", "GEIS"}:
        add("nyquist", "Nyquist: Zreal vs −Zimag", zr_i, zi_i, y_transform="negate", priority=1,
            description="Standard Nyquist representation")
        add("bode_magnitude", "Bode magnitude", freq_i, zmag_i, x_scale="log", y_scale="log", priority=2)
        add("bode_phase", "Bode phase", freq_i, phase_i, x_scale="log", priority=3)
        add("frequency_zreal", "Frequency vs Zreal", freq_i, zr_i, x_scale="log", priority=8)
        add("frequency_neg_zimag", "Frequency vs −Zimag", freq_i, zi_i, y_transform="negate", x_scale="log", priority=9)
        # If magnitude/phase columns are absent, the browser derives them from Zreal/Zimag.
        if freq_i is not None and zr_i is not None and zi_i is not None:
            if zmag_i is None:
                views.append({"id":"bode_magnitude_derived","label":"Bode magnitude (derived from Z)","x_index":freq_i,"y_index":None,
                              "x_column":columns[freq_i],"y_column":"|Z| derived","x_transform":"identity","y_transform":"hypot_columns",
                              "derived_columns":[zr_i,zi_i],"x_scale":"log","y_scale":"log","plot_type":"curve","synthetic_x":False,
                              "description":"Magnitude calculated as sqrt(Zreal² + Zimag²)","priority":4})
            if phase_i is None:
                views.append({"id":"bode_phase_derived","label":"Bode phase (derived from Z)","x_index":freq_i,"y_index":None,
                              "x_column":columns[freq_i],"y_column":"phase_deg derived","x_transform":"identity","y_transform":"atan2_deg_columns",
                              "derived_columns":[zi_i,zr_i],"x_scale":"log","y_scale":"linear","plot_type":"curve","synthetic_x":False,
                              "description":"Phase calculated as atan2(Zimag, Zreal)","priority":5})
            views.append({"id":"admittance_nyquist","label":"Admittance: conductance vs susceptance","x_index":None,"y_index":None,
                          "x_column":"G = Re(1/Z)","y_column":"B = Im(1/Z)","x_transform":"admittance_real_columns",
                          "y_transform":"admittance_imag_columns","x_derived_columns":[zr_i,zi_i],"derived_columns":[zr_i,zi_i],
                          "x_scale":"linear","y_scale":"linear","plot_type":"curve","synthetic_x":False,
                          "description":"Admittance derived point-by-point as Y = 1/Z","priority":6})
            views.append({"id":"bode_admittance_magnitude","label":"Bode admittance magnitude (derived)","x_index":freq_i,"y_index":None,
                          "x_column":columns[freq_i],"y_column":"|Y| derived","x_transform":"identity","y_transform":"inverse_hypot_columns",
                          "derived_columns":[zr_i,zi_i],"x_scale":"log","y_scale":"log","plot_type":"curve","synthetic_x":False,
                          "description":"Admittance magnitude calculated as 1/sqrt(Zreal² + Zimag²)","priority":7})
            views.append({"id":"effective_series_capacitance","label":"Effective series capacitance (derived)","x_index":freq_i,"y_index":None,
                          "x_column":columns[freq_i],"y_column":"C_series derived","x_transform":"identity","y_transform":"series_capacitance_columns",
                          "derived_columns":[freq_i,zi_i],"x_scale":"log","y_scale":"log","plot_type":"curve","synthetic_x":False,
                          "description":"Diagnostic C = -1/(2πf·Zimag); interpret only where a series-capacitance representation is physically applicable","priority":10})
    elif technique == "DRT":
        add("drt_tau", "DRT: tau vs gamma", tau_i, gamma_i, x_scale="log", priority=1)
        add("drt_frequency", "DRT: characteristic frequency vs gamma", tau_i, gamma_i,
            x_transform="inverse_2pi_positive", x_scale="log", priority=2)
    elif technique in {"TAFEL", "TAFEL_ANALYSIS"}:
        eta_or_potential = eta_i if eta_i is not None else potential_i
        add("tafel_precomputed", "Tafel: overpotential vs log10|j|", eta_or_potential, log_i, priority=1)
        add("tafel_current_density", "Tafel: overpotential vs log10|j|", eta_or_potential, current_density_i,
            y_transform="log10_abs", priority=2)
        add("tafel_current", "Tafel: potential vs log10|I|", eta_or_potential, current_i,
            y_transform="log10_abs", priority=3)
    elif technique == "EQCM":
        add("potential_mass", "Potential vs mass change", potential_i, mass_i, priority=1)
        add("time_mass", "Time vs mass change", time_i, mass_i, priority=2)
        add("potential_frequency_shift", "Potential vs frequency shift", potential_i, fshift_i, priority=3)
        add("time_frequency_shift", "Time vs frequency shift", time_i, fshift_i, priority=4)
        add("charge_mass", "Charge vs mass change", charge_i, mass_i, priority=5)
        add("potential_current", "Potential vs current", potential_i, current_i, priority=10)
    elif technique == "CONDUCTIVITY":
        add("time_conductivity", "Time vs conductivity / conductance", time_i, conductivity_i, priority=1)
        add("potential_conductivity", "Potential vs conductivity / conductance", potential_i, conductivity_i, priority=2)
        add("temperature_conductivity", "Temperature vs conductivity / conductance", temperature_i, conductivity_i, priority=3)

    # Additional domain views for derived electrochemical summaries.
    if technique in {"BATTERY_ANALYSIS", "GCD", "GITT", "PITT"}:
        add("cycle_capacity", "Cycle / point vs capacity", point_i, capacity_i if capacity_i is not None else charge_i, plot_type="metric", priority=5)
        add("cycle_efficiency", "Cycle / point vs efficiency", point_i, efficiency_i, plot_type="metric", priority=6)
        add("cycle_energy", "Cycle / point vs energy", point_i, energy_i, plot_type="metric", priority=7)
        add("cycle_power", "Cycle / point vs power", point_i, power_i, plot_type="metric", priority=8)
    if technique in {"KINETICS_ANALYSIS", "EIS_ANALYSIS", "VOLTAMMETRY_ANALYSIS", "TRANSIENT_ANALYSIS"}:
        add("point_resistance", "Point / record vs resistance", point_i, resistance_i, plot_type="metric", priority=8)
    if technique == "EQCM_ANALYSIS":
        add("point_mass", "Point / record vs mass change", point_i, mass_i, plot_type="metric", priority=5)

    # Spatial data can offer a colored map for every numeric metric column.
    if xpos_i is not None and ypos_i is not None:
        excluded = {xpos_i, ypos_i, point_i, _find_column(columns, "cycle"), _find_column(columns, "sweep")}
        for yi in varying_idx:
            if yi in excluded:
                continue
            before = len(views)
            add(f"map_{_slug(columns[yi], max_chars=28)}", f"Spatial map: {columns[yi]}", xpos_i, yi,
                plot_type="spatial", priority=12,
                description=f"Color-coded by {columns[yi]}; Y position uses {columns[ypos_i]}")
            if len(views) > before:
                views[-1]["spatial_y_index"] = ypos_i
                views[-1]["spatial_y_column"] = columns[ypos_i]

    # Ordered profiles and derived summary metrics.  Point/position is preferred,
    # then scan rate, cycle, sweep, or row index.
    identifier = next((x for x in (point_i, distance_i, scan_i, _find_column(columns, "cycle"), _find_column(columns, "sweep")) if x is not None), None)
    if technique in {"EIS_ANALYSIS", "DRT_ANALYSIS", "VOLTAMMETRY_ANALYSIS", "TRANSIENT_ANALYSIS", "TAFEL_ANALYSIS", "BATTERY_ANALYSIS", "KINETICS_ANALYSIS", "EQCM_ANALYSIS", "NUCLEATION_ANALYSIS", "CORROSION_ANALYSIS", "RDE_ANALYSIS", "SEMICONDUCTOR_ANALYSIS", "ECSA_ANALYSIS", "TRANSPORT_ANALYSIS", "STATISTICS", "SPATIAL_MAP", "PROFILE", "SUMMARY"} or _analysis_hint(path_hint):
        excluded = {identifier, xpos_i, ypos_i, zpos_i}
        for yi in varying_idx:
            if yi in excluded:
                continue
            add(f"metric_{_slug(columns[yi], max_chars=30)}", f"{columns[yi]} vs {columns[identifier] if identifier is not None else 'row'}",
                identifier, yi, plot_type="metric", priority=20, allow_synthetic_x=identifier is None)

    # Generic fallback: every numeric table with at least one varying numeric
    # column receives a real preview.  This prevents silent omission of novel
    # analysis formats while preserving semantic views above.
    if not views and varying_idx:
        x = varying_idx[0] if len(varying_idx) > 1 else None
        for yi in varying_idx[1:7] if x is not None else varying_idx[:6]:
            add(f"generic_{_slug(columns[yi], max_chars=24)}", f"{columns[yi]} vs {columns[x] if x is not None else 'row index'}", x, yi, priority=80, allow_synthetic_x=x is None)
    elif views:
        # Add a limited custom pair set when additional numeric columns are present.
        primary_x = views[0].get("x_index")
        for yi in varying_idx:
            if yi == primary_x or any(v.get("x_index") == primary_x and v.get("y_index") == yi for v in views):
                continue
            add(f"additional_{_slug(columns[yi], max_chars=24)}", f"Additional: {columns[yi]}", primary_x, yi, priority=90)
            if len(views) >= 18:
                break

    return sorted(views, key=lambda v: (int(v.get("priority", 50)), str(v.get("label", ""))))


def _preferred_axes(technique: str, columns: Sequence[str], numeric: Sequence[Mapping[str, Any]], *, path_hint: str = "") -> dict[str, Any]:
    views = _plot_views(technique, columns, numeric, path_hint=path_hint)
    first = views[0] if views else {}
    return {
        "x_index": first.get("x_index"), "y_index": first.get("y_index"),
        "x_column": first.get("x_column", ""), "y_column": first.get("y_column", ""),
        "invert_y_default": first.get("y_transform") == "negate",
        "log_x_default": first.get("x_scale") == "log",
        "log_y_default": first.get("y_scale") == "log",
        "x_transform": first.get("x_transform", "identity"),
        "y_transform": first.get("y_transform", "identity"),
        "plot_type": first.get("plot_type", "curve"),
        "views": views,
    }



def _finalize_preview(result: dict[str, Any], technique: str, path_hint: str = "") -> dict[str, Any]:
    columns = [str(c) for c in result.get("columns") or []]
    rows = result.get("rows") or []
    numeric = _numeric_column_info(columns, rows)
    result["numeric_columns"] = numeric
    axes = _preferred_axes(technique, columns, numeric, path_hint=path_hint)
    result["preferred_axes"] = {k: v for k, v in axes.items() if k != "views"}
    result["plot_views"] = axes.get("views") or []
    result["status"] = "plot_ready" if result["plot_views"] else ("table_ready" if rows else ("empty_table" if columns else "unavailable"))
    if result["status"] == "empty_table" and not result.get("error"):
        result["error"] = "Valid table structure detected, but this file contains no data rows."

    # Compact metric summary used for cross-file and point comparisons.  It is
    # intentionally derived from the embedded full-file sample, while source row
    # counts remain visible so users know when adaptive sampling occurred.
    metrics: dict[str, float] = {}
    for info in numeric[:20]:
        idx = int(info["index"]); name = columns[idx]
        vals = [float(r[idx]) for r in rows if idx < len(r) and isinstance(r[idx], (int, float)) and math.isfinite(float(r[idx]))]
        if not vals:
            continue
        if len(rows) <= 3:
            metrics[name] = vals[-1]
        else:
            metrics[f"{name} · mean"] = sum(vals) / len(vals)
            metrics[f"{name} · min"] = min(vals)
            metrics[f"{name} · max"] = max(vals)
            metrics[f"{name} · final"] = vals[-1]
    result["summary_metrics"] = metrics
    return result


def _preview_table(path: Path, columns_hint: Sequence[Any] = (), technique: str = "OTHER", limit: int = MAX_PREVIEW_ROWS) -> dict[str, Any]:
    """Sample a complete delimited table with ordered adaptive decimation."""
    result: dict[str, Any] = {
        "kind": "table", "columns": [str(c) for c in columns_hint], "rows": [],
        "sampled_rows": 0, "total_rows": 0, "decimation_factor": 1,
        "numeric_columns": [], "preferred_axes": {}, "plot_views": [], "summary_metrics": {},
        "error": "", "status": "unavailable", "source_format": path.suffix.lower().lstrip("."),
    }
    if not path.is_file() or path.suffix.lower() not in _TABLE_SUFFIXES:
        result["error"] = "Source is not a supported delimited table file."
        return result
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            sample = handle.read(65536); handle.seek(0)
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
            rows: list[list[Any]] = []; factor = 1; total = 0; last_row: list[Any] | None = None
            for raw in source_rows:
                total += 1
                vals: list[Any] = []
                for cell in list(raw)[:len(columns)]:
                    text = str(cell).strip(); num = _as_number(text)
                    vals.append(num if num is not None else text[:240])
                last_row = vals
                if (total - 1) % factor == 0:
                    rows.append(vals)
                if len(rows) > limit * 2:
                    rows = rows[::2]; factor *= 2
            if last_row is not None and (not rows or rows[-1] != last_row):
                rows.append(last_row)
            if len(rows) > limit:
                idxs = sorted({round(i * (len(rows) - 1) / max(1, limit - 1)) for i in range(limit)})
                rows = [rows[k] for k in idxs]
            result.update({"rows": rows, "sampled_rows": len(rows), "total_rows": total, "decimation_factor": max(1, factor)})
            if not rows:
                # Plain-text diagnostics and structure previews are represented as
                # text rows rather than a misleading failed CSV preview.
                if path.suffix.lower() in {".txt", ".dat"} and len(first) == 1 and delimiter not in str(first[0]):
                    text_lines = [line[:500] for line in sample.splitlines() if line.strip()][:limit]
                    if text_lines:
                        result.update({"columns": ["text"], "rows": [[line] for line in text_lines],
                                       "sampled_rows": len(text_lines), "total_rows": len(text_lines), "decimation_factor": 1})
                        return _finalize_preview(result, technique, str(path))
                result["error"] = "Valid table structure detected, but this file contains no data rows."
                return _finalize_preview(result, technique, str(path))
    except Exception as exc:
        result["error"] = f"Delimited-table read failed: {exc}"
        return result
    return _finalize_preview(result, technique, str(path))


def _flatten_scalar_dict(payload: Mapping[str, Any], prefix: str = "", depth: int = 0) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping) and depth < 2:
            out.update(_flatten_scalar_dict(value, name, depth + 1))
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[name] = value
    return out


def _json_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [_flatten_scalar_dict(x) if isinstance(x, Mapping) else {"value": x} for x in payload[:200000]]
    if not isinstance(payload, Mapping):
        return []
    for key in ("results", "records", "data", "rows", "items", "fits", "peaks", "parameters", "comparison_rows", "eta_scan_rows"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return [_flatten_scalar_dict(x) if isinstance(x, Mapping) else {"value": x} for x in value[:200000]]
    flat = _flatten_scalar_dict(payload)
    return [flat] if flat else []


def _json_metadata_records(payload: Any, prefix: str = "", depth: int = 0) -> list[dict[str, Any]]:
    """Create a readable key/type/summary preview for metadata-only JSON."""
    rows: list[dict[str, Any]] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, Mapping):
                rows.append({"key": name, "type": "object", "summary": f"{len(value)} keys"})
                if depth < 2:
                    rows.extend(_json_metadata_records(value, name, depth + 1))
            elif isinstance(value, list):
                sample = value[:3]
                rows.append({"key": name, "type": "array", "summary": f"{len(value)} items; sample={str(sample)[:180]}"})
            else:
                rows.append({"key": name, "type": type(value).__name__, "summary": str(value)[:240]})
    elif isinstance(payload, list):
        for idx, value in enumerate(payload[:200]):
            rows.append({"key": f"[{idx}]", "type": type(value).__name__, "summary": str(value)[:240]})
    else:
        rows.append({"key": "value", "type": type(payload).__name__, "summary": str(payload)[:240]})
    return rows[:2000]


def _preview_json(path: Path, technique: str = "SUMMARY", limit: int = MAX_PREVIEW_ROWS) -> dict[str, Any]:
    result: dict[str, Any] = {"kind":"table","columns":[],"rows":[],"sampled_rows":0,"total_rows":0,
        "decimation_factor":1,"numeric_columns":[],"preferred_axes":{},"plot_views":[],"summary_metrics":{},
        "error":"","status":"unavailable","source_format":"json"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        records = _json_records(payload)
        if not records:
            records = _json_metadata_records(payload)
        if not records:
            records = [{"key": "(empty)", "type": "empty", "summary": "This JSON file contains no entries."}]
        columns: list[str] = []
        for record in records[:1000]:
            for key in record:
                if key not in columns:
                    columns.append(key)
                if len(columns) >= MAX_PREVIEW_COLUMNS:
                    break
            if len(columns) >= MAX_PREVIEW_COLUMNS:
                break
        total = len(records)
        if total > limit:
            ids = sorted({round(i * (total - 1) / max(1, limit - 1)) for i in range(limit)})
            records = [records[k] for k in ids]
        rows = []
        for rec in records:
            row=[]
            for c in columns:
                value=rec.get(c, "")
                num=_as_number(value)
                row.append(num if num is not None else str(value)[:240])
            rows.append(row)
        result.update({"columns":columns,"rows":rows,"sampled_rows":len(rows),"total_rows":total,
                       "decimation_factor":max(1, math.ceil(total/max(1,len(rows))))})
        return _finalize_preview(result, technique, str(path))
    except Exception as exc:
        result["error"] = f"JSON preview failed: {exc}"
        return result


def _preview_xlsx(path: Path, technique: str = "SUMMARY", limit: int = MAX_PREVIEW_ROWS) -> dict[str, Any]:
    result: dict[str, Any] = {"kind":"table","columns":[],"rows":[],"sampled_rows":0,"total_rows":0,
        "decimation_factor":1,"numeric_columns":[],"preferred_axes":{},"plot_views":[],"summary_metrics":{},
        "error":"","status":"unavailable","source_format":"xlsx"}
    try:
        from openpyxl import load_workbook  # optional, already used by spreadsheet-capable installs
        wb=load_workbook(path, read_only=True, data_only=True); ws=wb.active
        iterator=ws.iter_rows(values_only=True); first=next(iterator, None)
        if first is None:
            result["error"]="Workbook sheet is empty."; return result
        columns=[str(v).strip() if v not in (None,"") else f"column_{i+1}" for i,v in enumerate(first[:MAX_PREVIEW_COLUMNS])]
        rows=[]; total=0; factor=1; last=None
        for raw in iterator:
            total+=1; vals=[]
            for value in raw[:len(columns)]:
                num=_as_number(value); vals.append(num if num is not None else str(value or "")[:240])
            last=vals
            if (total-1)%factor==0: rows.append(vals)
            if len(rows)>limit*2: rows=rows[::2]; factor*=2
        if last is not None and (not rows or rows[-1]!=last): rows.append(last)
        if len(rows)>limit:
            ids=sorted({round(i*(len(rows)-1)/max(1,limit-1)) for i in range(limit)}); rows=[rows[k] for k in ids]
        result.update({"columns":columns,"rows":rows,"sampled_rows":len(rows),"total_rows":total,"decimation_factor":factor})
        return _finalize_preview(result, technique, str(path))
    except ImportError:
        result["error"]="Excel preview requires the optional openpyxl package; the workbook remains downloadable."
    except Exception as exc:
        result["error"]=f"Excel preview failed: {exc}"
    return result


def _preview_structured(path: Path, columns_hint: Sequence[Any] = (), technique: str = "OTHER") -> dict[str, Any]:
    suffix=path.suffix.lower()
    if suffix in _TABLE_SUFFIXES:
        return _preview_table(path, columns_hint, technique)
    if suffix == ".json":
        return _preview_json(path, technique)
    if suffix == ".xlsx":
        return _preview_xlsx(path, technique)
    return {"kind":"file","columns":[],"rows":[],"sampled_rows":0,"total_rows":0,"numeric_columns":[],
            "preferred_axes":{},"plot_views":[],"summary_metrics":{},"error":"No embedded preview parser is available for this file format.","status":"download_only"}


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



def _registry_meta(technique: str) -> dict[str, Any]:
    return dict(TECHNIQUE_REGISTRY.get(technique, TECHNIQUE_REGISTRY["OTHER"]))


def _dataset_record(*, idx: int, root: Path, chosen_rel: str, chosen_file: Path,
                    technique: str, technique_label: str, method_index: int | None,
                    method_label: str, point: int | None, cycle: int | None, sweep: int | None,
                    line: int | None, role: str, original_scientific_path: str,
                    canonical_key: str, preview: Mapping[str, Any], metadata: Mapping[str, Any] | None = None,
                    cache_rel: str = "", processed_rel: str = "") -> dict[str, Any]:
    reg = _registry_meta(technique)
    views = list(preview.get("plot_views") or []) if isinstance(preview, Mapping) else []
    first = views[0] if views else {}
    display_record = {"point":point,"cycle":cycle,"sweep":sweep,"line":line,"method_label":method_label,
                      "technique_key":technique,"technique_label":technique_label}
    display_name = _scientific_display(display_record, chosen_file.name)
    dataset_id = f"d{idx:05d}_{hashlib.sha1((canonical_key or chosen_rel).encode('utf-8', errors='replace')).hexdigest()[:8]}"
    actual_columns = [] if preview.get("kind") == "image" else [str(c) for c in preview.get("columns") or []]
    return {
        "id": dataset_id, "display_name": display_name,
        "technique_key": technique, "technique_label": technique_label,
        "family": reg.get("family", "Other"), "data_kind": reg.get("kind", "data"),
        "analysis_kind": "analysis" if reg.get("kind") == "analysis" else ("media" if reg.get("kind") == "media" else "raw/data"),
        "method_index": method_index, "method_label": method_label,
        "point": point, "line": line, "cycle": cycle, "sweep": sweep,
        "physical_path": chosen_rel, "physical_href": _href(chosen_rel),
        "browser_cache_path": cache_rel, "processed_source_path": processed_rel,
        "original_scientific_path": original_scientific_path,
        "canonical_key": canonical_key, "role": role,
        "columns": actual_columns, "declared_columns": [],
        "n_rows": preview.get("total_rows"), "size_bytes": int(chosen_file.stat().st_size),
        "preview": dict(preview), "plot_views": views, "summary_metrics": dict(preview.get("summary_metrics") or {}),
        "plot_spec": {
            "view_id": first.get("id", ""), "view_label": first.get("label", ""),
            "x_column": first.get("x_column", ""), "y_column": first.get("y_column", ""),
            "x_index": first.get("x_index"), "y_index": first.get("y_index"),
            "x_transform": first.get("x_transform", "identity"), "y_transform": first.get("y_transform", "identity"),
            "x_scale": first.get("x_scale", "linear"), "y_scale": first.get("y_scale", "linear"),
            "plot_type": first.get("plot_type", "curve"),
            "invert_y_default": first.get("y_transform") == "negate",
            "log_x_default": first.get("x_scale") == "log", "log_y_default": first.get("y_scale") == "log",
        },
        "is_media": technique in {"PHOTO", "FIGURE"} or chosen_file.suffix.lower() in _FIGURE_SUFFIXES,
        "is_comparable": bool(views or preview.get("summary_metrics")),
        "metadata": dict(metadata or {}),
    }


def _catalog_datasets(root: Path, analysis_root: Path | None, catalog_path: Path) -> list[dict[str, Any]]:
    catalog = _read_json(catalog_path)
    records = catalog.get("records") if isinstance(catalog.get("records"), list) else []
    datasets: list[dict[str, Any]] = []
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        record = dict(raw); metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
        cache_rel, cache_file = _resolve_catalog_file(root, record)
        processed_rel, processed_file = _resolve_processed_file(root, analysis_root, record)
        chosen_rel, chosen_file = (processed_rel, processed_file) if processed_file and processed_file.is_file() else (cache_rel, cache_file)
        if not chosen_rel or chosen_file is None or not chosen_file.is_file():
            continue
        columns_hint = [str(c) for c in (record.get("columns") or metadata.get("columns") or [])]
        raw_label = record.get("technique_key") or record.get("technique_label") or record.get("technique") or ""
        technique = _technique_key(raw_label, path=chosen_rel, columns=columns_hint, role=record.get("role"))
        logical = _logical_hierarchy(record)
        point = _parse_number(logical.get("point")) if "point" in logical else _parse_number(record.get("point"))
        if technique in {"PHOTO", "FIGURE"} and point in (None, 0):
            point = _infer_media_point(chosen_file.name)
        cycle = _parse_number(logical.get("cycle")) if "cycle" in logical else _parse_number(record.get("cycle"))
        sweep = _parse_number(logical.get("sweep")) if "sweep" in logical else _parse_number(record.get("sweep"))
        method_index = _parse_number(record.get("method_index") or record.get("preferred_method_index") or record.get("method"))
        method_label = str(record.get("method_label") or record.get("method_title") or record.get("method_id") or (f"Method {method_index:02d}" if method_index is not None else "Method"))
        if chosen_file.suffix.lower() in _FIGURE_SUFFIXES:
            preview = _media_preview(chosen_rel, chosen_file)
        else:
            preview = _preview_structured(chosen_file, columns_hint, technique)
            # Reclassify after reading actual columns. This is essential for
            # arbitrarily named analysis outputs and LLM-generated filenames.
            refined = _technique_key(raw_label, path=chosen_rel, columns=preview.get("columns") or columns_hint, role=record.get("role"))
            if refined != technique:
                technique = refined
                preview = _finalize_preview(dict(preview), technique, chosen_rel) if preview.get("kind") == "table" and preview.get("rows") else preview
        reg = _registry_meta(technique)
        technique_label = str(reg.get("label") or record.get("technique_label") or technique)
        canonical_key = str(record.get("canonical_key") or metadata.get("canonical_key") or "")
        md = {
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
            "catalog_technique": raw_label,
        }
        row = _dataset_record(idx=len(datasets)+1, root=root, chosen_rel=chosen_rel, chosen_file=chosen_file,
            technique=technique, technique_label=technique_label, method_index=method_index, method_label=method_label,
            point=point, cycle=cycle, sweep=sweep, line=_parse_number(record.get("line")),
            role=str(record.get("role") or metadata.get("role") or ("media" if technique in {"PHOTO","FIGURE"} else "processed table")),
            original_scientific_path=str(metadata.get("relative_path") or canonical_key or chosen_rel).replace("\\", "/"),
            canonical_key=canonical_key, preview=preview, metadata=md, cache_rel=cache_rel, processed_rel=processed_rel)
        row["declared_columns"] = columns_hint
        datasets.append(row)
    return datasets


def _infer_method_label(path: Path) -> tuple[int | None, str]:
    text = str(path).replace("\\", "/")
    match = re.search(r"(?:method|series)[_ -]?(\d+)(?:[_ -]([^/]+))?", text, re.I)
    if match:
        idx = int(match.group(1)); title = (match.group(2) or "").replace("_", " ").strip()
        return idx, f"Method {idx:02d}" + (f" — {title}" if title else "")
    parent = path.parent.name.replace("_", " ")
    return None, parent if parent and parent.lower() not in {"processed", "results", "analysis"} else "Method"


def _infer_from_path(path: Path, root: Path, index: int) -> dict[str, Any]:
    rel = _posix_rel(path, root); suffix = path.suffix.lower()
    point_match = re.search(r"(?:point|pt|site|pixel)[_ -]?(\d+)", rel, re.I)
    cycle_match = re.search(r"cycle[_ -]?(\d+)", rel, re.I)
    sweep_match = re.search(r"(?:sweep|segment)[_ -]?(\d+)", rel, re.I)
    point = int(point_match.group(1)) if point_match else (_infer_media_point(path.name) if suffix in _FIGURE_SUFFIXES else None)
    cycle = int(cycle_match.group(1)) if cycle_match else None; sweep = int(sweep_match.group(1)) if sweep_match else None
    method_index, method_label = _infer_method_label(path)
    preliminary = _technique_key(rel, path=rel, columns=(), role="filesystem scan")
    if suffix in _FIGURE_SUFFIXES:
        preview = _media_preview(rel, path)
        technique = preliminary if preliminary in {"PHOTO","FIGURE"} else "FIGURE"
    else:
        preview = _preview_structured(path, technique=preliminary)
        technique = _technique_key(preliminary, path=rel, columns=preview.get("columns") or [], role="analysis output" if _analysis_hint(rel) else "data table")
        if preview.get("kind") == "table" and preview.get("rows"):
            preview = _finalize_preview(dict(preview), technique, rel)
    reg = _registry_meta(technique)
    role = "media" if suffix in _FIGURE_SUFFIXES else ("derived analysis table" if reg.get("kind") == "analysis" or _analysis_hint(rel) else "processed table")
    return _dataset_record(idx=index, root=root, chosen_rel=rel, chosen_file=path,
        technique=technique, technique_label=str(reg.get("label") or technique),
        method_index=method_index, method_label=method_label, point=point, cycle=cycle, sweep=sweep, line=None,
        role=role, original_scientific_path=rel, canonical_key="", preview=preview,
        metadata={"classification_source":"columns + path", "filesystem_scanned":True}, processed_rel=rel)


def _scan_datasets(root: Path, analysis_root: Path | None, *, skip_cache: bool = False) -> list[dict[str, Any]]:
    paths: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = _posix_rel(p, root); low = rel.lower(); suffix = p.suffix.lower()
        if rel.startswith(DOWNLOAD_DIR_NAME + "/") or rel.startswith(ASSET_DIR_NAME + "/") or p.name in {HTML_NAME, MANIFEST_NAME, README_NAME, "short_name_map.json"}:
            continue
        if "/__pycache__/" in f"/{rel}/" or rel.startswith(".faradaiq_venv/"):
            continue
        if any(part.lower() in {"faradaiq_job_sessions", "pyseccm_tools_scripts", "scripts", "templates"} for part in p.parts):
            continue
        stem_low = p.stem.lower()
        if any(token in stem_low for token in ("samplecode", "readme", "license", "requirements")):
            continue
        if skip_cache and rel.startswith(".browser_cache_from_h5/"):
            continue
        if suffix in _FIGURE_SUFFIXES or suffix in _TABLE_SUFFIXES:
            paths.append(p); continue
        if suffix == ".xlsx":
            paths.append(p); continue
        if suffix == ".json" and _analysis_hint(low) and not any(x in low for x in ("manifest", "browser_catalog", "scilink_request", "service_settings", "settings.json", "config.json", "session.json", "schema.json")):
            paths.append(p)
    return [_infer_from_path(path, root, idx) for idx, path in enumerate(sorted(paths), 1)]


def _merge_datasets(primary: Sequence[dict[str, Any]], scanned: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []; paths: set[str] = set(); identities: set[tuple[Any, ...]] = set()
    for row in list(primary) + list(scanned):
        rel = str(row.get("physical_path") or "")
        if rel in paths:
            continue
        identity = (row.get("technique_key"), row.get("point"), row.get("cycle"), row.get("sweep"),
                    Path(rel).name.lower(), int(row.get("size_bytes") or 0))
        # Prefer authoritative catalog/processed datasets over cache or scan copies.
        if identity in identities and (rel.startswith(".browser_cache_from_h5/") or row.get("metadata", {}).get("filesystem_scanned")):
            continue
        paths.add(rel); identities.add(identity); out.append(row)
    for idx, row in enumerate(out, 1):
        seed = str(row.get("canonical_key") or row.get("physical_path") or idx)
        row["id"] = f"d{idx:05d}_{hashlib.sha1(seed.encode('utf-8', errors='replace')).hexdigest()[:8]}"
    return out


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
        if rel.startswith(ASSET_DIR_NAME + "/") or rel.startswith(".faradaiq_venv/") or "/__pycache__/" in f"/{rel}/":
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


def _media_semantics(path_text: str, declared_point: Any) -> tuple[int | None, int | None, str]:
    """Resolve point/frame meaning without treating every numeric suffix as a point.

    Generated comparison figures commonly end in ``_000``, ``_001``, and so on;
    those suffixes are animation or render-frame indexes, not acquisition points.
    Actual specimen photos often carry a direct point number in the filename.
    """
    rel = str(path_text or "").replace("\\", "/")
    low = rel.lower()
    name = Path(rel).stem

    explicit = re.search(r"(?:^|[_\-\s])point[_\-\s]*0*(\d+)(?:$|[_\-\s])", name, re.I)
    if explicit is None:
        explicit = re.search(r"(?:^|[_\-\s])p0*(\d{1,5})(?:$|[_\-\s])", name, re.I)
    if explicit:
        return int(explicit.group(1)), None, "acquisition point"

    trailing = re.search(r"(?:^|[_\-])0*(\d+)$", name)
    trailing_index = int(trailing.group(1)) if trailing else None

    if "/media/photos/" in f"/{low}/":
        point = trailing_index if trailing_index is not None else (_parse_number(declared_point))
        return point, None, "acquisition photo"
    if "/media/figures/" in f"/{low}/" or "generated" in low:
        return None, trailing_index, "generated comparison frame"
    if "/media/gif/" in f"/{low}/" or Path(rel).suffix.lower() == ".gif":
        return None, None, "animated sequence"
    spectrum = re.search(r"(?:^|/)spectrum[_\-]?0*(\d+)(?:/|$)", low)
    if spectrum:
        return None, int(spectrum.group(1)), "analysis result frame"
    return _parse_number(declared_point), None, "media"


def _build_media(files: Sequence[Mapping[str, Any]], datasets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for d in datasets:
        if not d.get("is_media"):
            continue
        rel = str(d.get("physical_path") or "")
        point, frame_index, media_role = _media_semantics(rel, d.get("point"))
        by_path[rel] = {
            "id": "m_" + str(d.get("id")), "name": Path(rel).name, "path": rel, "href": d.get("physical_href") or _href(rel),
            "point": point, "frame_index": frame_index, "media_role": media_role,
            "technique_key": d.get("technique_key"), "display_name": d.get("display_name"),
            "category": "Photos / images", "browser_renderable": bool((d.get("preview") or {}).get("browser_renderable")),
            "size_bytes": d.get("size_bytes", 0), "dataset_id": d.get("id"),
        }
    for f in files:
        if str(f.get("extension") or "").lower() not in _FIGURE_SUFFIXES:
            continue
        rel = str(f.get("path") or "")
        if rel in by_path: continue
        point, frame_index, media_role = _media_semantics(rel, f.get("point"))
        by_path[rel] = {
            "id": "m_" + hashlib.sha1(rel.encode()).hexdigest()[:12], "name": f.get("name"), "path": rel, "href": f.get("href") or _href(rel),
            "point": point, "frame_index": frame_index, "media_role": media_role,
            "technique_key": "FIGURE", "display_name": f.get("name"),
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
    return sorted(unique.values(), key=lambda m: (
        m.get("point") is None,
        m.get("point") if m.get("point") is not None else 10**9,
        m.get("frame_index") is None,
        m.get("frame_index") if m.get("frame_index") is not None else 10**9,
        str(m.get("name")),
    ))



def _stage_media_assets(
    root: Path,
    media: Sequence[Mapping[str, Any]],
    files: Sequence[Mapping[str, Any]],
    datasets: Sequence[MutableMapping[str, Any]],
) -> dict[str, Any]:
    """Copy browser previews into a compact, portable asset directory.

    Generated figures are frequently stored under very deep analysis paths and
    may later be removed from a cache or renamed by another workflow.  The HTML
    must not depend on those volatile paths.  Every renderable image therefore
    receives a compact preview copy under ``browser_assets/media``.  Original
    scientific paths remain in the manifest and continue to be used for source
    downloads when available.
    """
    asset_root = root / ASSET_DIR_NAME
    media_root = asset_root / MEDIA_ASSET_DIR_NAME
    if asset_root.exists():
        try:
            shutil.rmtree(asset_root)
        except OSError:
            pass
    media_root.mkdir(parents=True, exist_ok=True)

    by_identity: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
    by_name: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in files:
        name = str(row.get("name") or "").lower()
        size = int(row.get("size_bytes") or 0)
        by_identity[(name, size)].append(row)
        by_name[name].append(row)

    dataset_by_id = {str(d.get("id")): d for d in datasets}
    copied = 0
    converted = 0
    missing = 0
    bytes_written = 0
    staged_paths: set[str] = set()

    for idx, raw in enumerate(media, 1):
        row = raw if isinstance(raw, MutableMapping) else dict(raw)
        source_rel = str(row.get("path") or "").replace("\\", "/")
        source = root / source_rel if source_rel else None
        if source is None or not source.is_file():
            key = (str(row.get("name") or "").lower(), int(row.get("size_bytes") or 0))
            candidates = list(by_identity.get(key) or by_name.get(key[0]) or [])
            # Prefer cache media, then any existing physical copy.
            candidates.sort(key=lambda f: (
                0 if "/media/" in f"/{str(f.get('path') or '').lower()}/" else 1,
                0 if str(f.get("path") or "").startswith(".browser_cache_from_h5/") else 1,
                len(str(f.get("path") or "")),
            ))
            source = None
            for candidate in candidates:
                rel = str(candidate.get("path") or "")
                p = root / rel
                if p.is_file():
                    source, source_rel = p, rel
                    break

        token = hashlib.sha1(
            f"{row.get('id')}|{row.get('path')}|{row.get('size_bytes')}".encode("utf-8", errors="replace")
        ).hexdigest()[:10]
        source_suffix = source.suffix.lower() if source is not None else Path(str(row.get("name") or "")).suffix.lower()
        preview_suffix = source_suffix if source_suffix in _BROWSER_IMAGE_SUFFIXES else ".png"
        asset_name = f"m{idx:05d}_{token}{preview_suffix}"
        asset_path = media_root / asset_name
        preview_rel = f"{ASSET_DIR_NAME}/{MEDIA_ASSET_DIR_NAME}/{asset_name}"

        ok = False
        error = ""
        if source is not None and source.is_file():
            try:
                if source_suffix in _BROWSER_IMAGE_SUFFIXES:
                    shutil.copy2(source, asset_path)
                    copied += 1
                    ok = asset_path.is_file() and asset_path.stat().st_size > 0
                elif source_suffix in {".tif", ".tiff"}:
                    try:
                        from PIL import Image
                        with Image.open(source) as image:
                            image.seek(0)
                            frame = image.convert("RGBA") if "A" in image.getbands() else image.convert("RGB")
                            pixels = max(1, frame.width * frame.height)
                            if pixels > MAX_MEDIA_PREVIEW_PIXELS:
                                scale = math.sqrt(MAX_MEDIA_PREVIEW_PIXELS / pixels)
                                frame = frame.resize(
                                    (max(1, int(frame.width * scale)), max(1, int(frame.height * scale))),
                                    Image.Resampling.LANCZOS,
                                )
                            frame.save(asset_path, format="PNG", optimize=True)
                        converted += 1
                        ok = asset_path.is_file() and asset_path.stat().st_size > 0
                    except Exception as exc:
                        error = f"TIFF preview conversion failed: {exc}"
                else:
                    error = "This image format does not have an offline browser preview converter."
            except Exception as exc:
                error = f"Portable media staging failed: {exc}"
        else:
            error = "No existing physical source image was found while building the portable browser."

        row["source_path"] = str(row.get("path") or "")
        row["source_href"] = str(row.get("href") or _href(str(row.get("path") or "")))
        row["resolved_source_path"] = source_rel if source is not None and source.is_file() else ""
        row["download_href"] = _href(source_rel) if source is not None and source.is_file() else row["source_href"]
        row["preview_asset_path"] = preview_rel if ok else ""
        row["preview_href"] = _href(preview_rel) if ok else ""
        row["preview_available"] = bool(ok)
        row["preview_error"] = error
        row["browser_renderable"] = bool(ok)
        if ok:
            staged_paths.add(preview_rel)
            try:
                bytes_written += asset_path.stat().st_size
            except OSError:
                pass
        else:
            missing += 1

        dataset_id = str(row.get("dataset_id") or "")
        dataset = dataset_by_id.get(dataset_id)
        if dataset is not None:
            dataset["portable_media_asset"] = preview_rel if ok else ""
            dataset["resolved_media_source_path"] = source_rel if source is not None and source.is_file() else ""
            preview = dataset.get("preview")
            if isinstance(preview, MutableMapping):
                preview["source_image_path"] = str(preview.get("image_path") or dataset.get("physical_path") or "")
                preview["image_path"] = preview_rel if ok else preview.get("image_path", "")
                preview["image_href"] = _href(preview_rel) if ok else preview.get("image_href", "")
                preview["browser_renderable"] = bool(ok)
                preview["status"] = "image_ready" if ok else "download_only"
                if error:
                    preview["error"] = error

    return {
        "asset_root": ASSET_DIR_NAME,
        "media_asset_root": f"{ASSET_DIR_NAME}/{MEDIA_ASSET_DIR_NAME}",
        "media_count": len(media),
        "preview_count": len(staged_paths),
        "copied_count": copied,
        "converted_count": converted,
        "missing_count": missing,
        "bytes_written": bytes_written,
        "relative_paths_only": True,
        "note": "All gallery previews use compact copies under browser_assets/media. Original scientific paths remain recorded for downloads and provenance.",
    }


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
                "schema": "faradaiq-portable-batch-v277", "version": VERSION, "created": datetime.now().isoformat(timespec="seconds"),
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




# User-facing browser relevance policy. Technical parser/provenance tables remain
# downloadable and searchable, but they never compete with scientific measurements
# and analysis results on the Discover, Trends, or recommended-comparison pages.
_TECHNICAL_PATH_HINTS = (
    "semantic_sections", "semantic_section", "heka_hierarchy", "heka_metadata",
    "parser_warning", "parser_warnings", "raw_section", "raw_trace_index",
    "technique_evidence", "unsupported", "source_and_checks", "source_and_check",
    "provenance", "record_index", "tree_header", "bundle_source",
)
_TECHNICAL_COLUMN_HINTS = {
    "record_offset", "record_size", "record_sha256", "tree_level", "tree_magic",
    "header_size", "parsed_bytes", "total_bytes", "child_count", "graph_index",
    "method_index", "entry_index", "function_index", "trace_index", "trace_count",
    "stimulation_index", "segment_index", "marker_kind_code", "class_code",
    "compression_factor", "offset", "length", "bytes", "line", "schema",
    "format_version", "parser_version", "reserved1", "reserved2", "x_wave", "y_wave",
}
_PRIMARY_VIEW_SCORES = {
    "potential_current": 100, "time_current": 100, "time_potential": 100,
    "nyquist": 100, "bode_magnitude": 96, "bode_phase": 96,
    "tau_gamma": 100, "frequency_gamma": 96, "distance_current": 100,
    "time_charge": 92, "capacity_voltage": 92, "cycle_capacity": 92,
    "tafel": 94, "polarization": 92, "spatial": 96, "profile": 92,
    "anson": 82, "cottrell": 82,
}
_SCIENTIFIC_Y_TOKENS = (
    "current", "potential", "voltage", "charge", "capacity", "impedance", "zreal",
    "zimag", "zmod", "phase", "gamma", "tau", "resistance", "capacitance",
    "conductivity", "mass", "efficiency", "energy", "power", "temperature",
    "diffusion", "rate", "slope", "peak", "area", "composition", "concentration",
)


def _column_is_scientific(name: Any) -> bool:
    low = _normalize_column(name)
    return any(token in low for token in _SCIENTIFIC_Y_TOKENS)


def _semantic_column_key(name: Any) -> str:
    norm = _normalize_column(name)
    for semantic, aliases in COLUMN_ALIASES.items():
        if norm in {_normalize_column(a) for a in aliases}:
            return semantic
    return norm


def _assign_ui_roles(datasets: Sequence[MutableMapping[str, Any]]) -> None:
    for d in datasets:
        key = str(d.get("technique_key") or "OTHER")
        path = " ".join(str(d.get(k) or "") for k in ("physical_path", "original_scientific_path", "role")).lower()
        columns = [_normalize_column(c) for c in d.get("columns") or []]
        technical_hits = sum(1 for c in columns if c in _TECHNICAL_COLUMN_HINTS or any(h in c for h in ("record_offset", "tree_", "reserved_", "parser_")))
        technical_path = any(h in path for h in _TECHNICAL_PATH_HINTS)
        if d.get("is_media") or d.get("data_kind") == "media":
            role, reason = "media", "Photo or generated figure"
        elif d.get("data_kind") == "raw" or key in {"CV","FSCV","IV","RDE","RRDE","LPR","LSV","CV_LSV","DPV","SWV","NPV","STRIPPING","CA","DEPOSITION","CP","GCD","OCP","ZRA","GITT","PITT","EIS","GEIS","EQCM","CONDUCTIVITY","APPROACH","POSITION"}:
            role, reason = "measurement", "Primary measurement"
        elif key in {"DRT","TAFEL","EIS_ANALYSIS","DRT_ANALYSIS","VOLTAMMETRY_ANALYSIS","TRANSIENT_ANALYSIS","TAFEL_ANALYSIS","BATTERY_ANALYSIS","KINETICS_ANALYSIS","EQCM_ANALYSIS","NUCLEATION_ANALYSIS","CORROSION_ANALYSIS","RDE_ANALYSIS","SEMICONDUCTOR_ANALYSIS","ECSA_ANALYSIS","TRANSPORT_ANALYSIS","STATISTICS","SPATIAL_MAP","PROFILE"}:
            role, reason = "analysis", "Scientific analysis result"
        elif key == "SUMMARY" and (technical_path or (columns and technical_hits >= max(2, len(columns)//3))):
            role, reason = "technical", "Parser, hierarchy, or provenance table"
        elif key == "SUMMARY" and any(_column_is_scientific(c) for c in columns):
            role, reason = "analysis", "Scientific summary table"
        else:
            role, reason = "supporting", "Supporting metadata or table"
        d["ui_role"] = role
        d["ui_role_reason"] = reason
        d["ui_hidden_default"] = role in {"technical", "supporting"}
        d["ui_search_text"] = " ".join(str(d.get(k) or "") for k in ("display_name","technique_label","method_label","point","cycle","sweep","physical_path","role")).lower()
    canonical_measurements: dict[tuple[str, Any], list[int | None]] = defaultdict(list)
    canonical_semantics: dict[tuple[str, Any], set[str]] = defaultdict(set)
    for d in datasets:
        path = str(d.get("physical_path") or "").replace("\\", "/").lower()
        if d.get("ui_role") == "measurement" and "/processed/" in path:
            ident = (str(d.get("technique_key")), d.get("point"))
            canonical_measurements[ident].append(_parse_number(d.get("n_rows")))
            canonical_semantics[ident].update(_semantic_column_key(c) for c in d.get("columns") or [])
    for d in datasets:
        path = str(d.get("physical_path") or "").replace("\\", "/").lower()
        ident = (str(d.get("technique_key")), d.get("point"))
        row_count = _parse_number(d.get("n_rows"))
        canonical_rows = canonical_measurements.get(ident, [])
        row_match = not canonical_rows or row_count in canonical_rows or (len(canonical_rows) == 1 and row_count is not None)
        alternate = d.get("ui_role") == "measurement" and "_for_plotting_analysis/data" in path and ident in canonical_measurements and row_match
        suppressed: list[str] = []
        if alternate:
            existing = canonical_semantics.get(ident, set())
            for view in d.get("plot_views") or []:
                if _semantic_column_key(view.get("y_column")) in existing:
                    suppressed.append(str(view.get("id") or ""))
        d["ui_alternate_copy"] = bool(alternate)
        d["ui_suppressed_view_ids"] = sorted({v for v in suppressed if v})
        all_view_ids = {str(v.get("id") or "") for v in d.get("plot_views") or [] if v.get("id")}
        d["ui_hidden_from_compare"] = bool(alternate and all_view_ids and all_view_ids.issubset(set(suppressed)))
        if alternate:
            d["ui_role_reason"] = "Analysis-ready alternate copy; duplicate views are hidden while added scientific quantities remain available"


def _view_base_score(dataset: Mapping[str, Any], view: Mapping[str, Any]) -> int:
    if dataset.get("ui_hidden_default"):
        return 0
    vid = str(view.get("id") or "").lower()
    y = str(view.get("y_column") or "").lower()
    score = _PRIMARY_VIEW_SCORES.get(vid, 0)
    if score:
        return score
    if vid.startswith("additional_"):
        return 45 if _column_is_scientific(y) else 5
    if vid.startswith("metric_") or vid.startswith("generic_"):
        if _column_is_scientific(y):
            return 58 if dataset.get("point") is not None else 42
        return 4
    if _column_is_scientific(y):
        return 64
    return 20 if dataset.get("ui_role") in {"measurement","analysis"} else 0


def _transform_metric_value(value: float, transform: str) -> float | None:
    try:
        v = float(value)
    except Exception:
        return None
    if not math.isfinite(v):
        return None
    transform = str(transform or "identity")
    if transform == "negate": return -v
    if transform == "abs": return abs(v)
    if transform == "log10_abs": return math.log10(abs(v)) if v else None
    return v


def _dataset_view_values(dataset: Mapping[str, Any], view: Mapping[str, Any]) -> tuple[list[float], list[float]]:
    preview = dataset.get("preview") if isinstance(dataset.get("preview"), Mapping) else {}
    rows = preview.get("rows") if isinstance(preview.get("rows"), list) else []
    xi, yi = view.get("x_index"), view.get("y_index")
    if yi is None:
        return [], []
    xs: list[float] = []; ys: list[float] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or int(yi) >= len(row):
            continue
        y = _transform_metric_value(row[int(yi)], str(view.get("y_transform") or "identity"))
        if y is None: continue
        if xi is None:
            x = float(idx)
        elif int(xi) < len(row):
            x = _transform_metric_value(row[int(xi)], str(view.get("x_transform") or "identity"))
            if x is None: continue
        else:
            continue
        xs.append(x); ys.append(y)
    return xs, ys


def _metric_for_dataset(dataset: Mapping[str, Any], view: Mapping[str, Any], metric: str) -> float | None:
    xs, ys = _dataset_view_values(dataset, view)
    if not ys: return None
    if metric == "max": return max(ys)
    if metric == "min": return min(ys)
    if metric == "mean": return sum(ys)/len(ys)
    if metric == "area" and len(xs) > 1:
        return sum((xs[i]-xs[i-1])*(ys[i]+ys[i-1])*0.5 for i in range(1,len(xs)))
    if metric == "xmax": return xs[max(range(len(ys)), key=lambda i: abs(ys[i]))]
    if metric == "maxabs": return max(ys, key=lambda v: abs(v))
    return ys[-1]


def _default_metric_for(technique: str, view_id: str) -> tuple[str, str]:
    key, vid = str(technique), str(view_id)
    if key in {"CV","FSCV","IV","RDE","RRDE","LPR","LSV","CV_LSV","DPV","SWV","NPV","STRIPPING"}:
        return "maxabs", "Peak magnitude"
    if key in {"EIS","GEIS"}:
        return ("max", "Maximum impedance") if "phase" not in vid else ("min", "Minimum phase")
    if key in {"DRT","DRT_ANALYSIS"}:
        return "max", "Maximum DRT amplitude"
    if key in {"APPROACH"}:
        return "maxabs", "Maximum current magnitude"
    if key in {"CA","DEPOSITION","CP","GCD","OCP","ZRA","GITT","PITT"}:
        return "final", "Final value"
    return "final", "Final value"


def _safe_median(values: Sequence[float]) -> float:
    vals = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not vals: return float("nan")
    n=len(vals); return vals[n//2] if n%2 else (vals[n//2-1]+vals[n//2])*0.5


def _trend_cards(datasets: Sequence[Mapping[str, Any]], recommended: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_id={str(d.get("id")):d for d in datasets}
    cards=[]; unusual=[]; seen_trends=set()
    for rec in recommended:
        if len(cards) >= 12: break
        if int(rec.get("score") or 0) < 80:
            continue
        trend_key=(str(rec.get("technique_key")),_normalize_column(rec.get("y_column") or rec.get("view_id") or ""))
        if trend_key in seen_trends:
            continue
        seen_trends.add(trend_key)
        metric, metric_label = _default_metric_for(str(rec.get("technique_key")), str(rec.get("view_id")))
        values_by_point: dict[float,list[float]] = defaultdict(list)
        source_ids=[]
        for did in rec.get("dataset_ids") or []:
            d=by_id.get(str(did));
            if not d or d.get("point") is None: continue
            view=next((v for v in d.get("plot_views") or [] if str(v.get("id"))==str(rec.get("view_id"))),None)
            if not view: continue
            val=_metric_for_dataset(d,view,metric)
            if val is None or not math.isfinite(val): continue
            values_by_point[float(d.get("point"))].append(float(val)); source_ids.append(str(did))
        values=[{"point":int(p) if float(p).is_integer() else p,"value":_safe_median(vs)} for p,vs in sorted(values_by_point.items())]
        if len(values)<3: continue
        x=[float(v["point"]) for v in values]; y=[float(v["value"]) for v in values]
        xm=sum(x)/len(x); ym=sum(y)/len(y)
        den=(sum((a-xm)**2 for a in x)*sum((b-ym)**2 for b in y))**0.5
        corr=sum((a-xm)*(b-ym) for a,b in zip(x,y))/den if den else 0.0
        ymin=min(values,key=lambda v:v["value"]); ymax=max(values,key=lambda v:v["value"])
        card={
            "id":f"trend_{len(cards)+1:02d}","technique_key":rec.get("technique_key"),"technique_label":rec.get("technique_label"),
            "family":rec.get("family"),"view_id":rec.get("view_id"),"view_label":rec.get("view_label"),
            "metric":metric,"metric_label":metric_label,"values":values,"dataset_ids":source_ids,
            "point_count":len(values),"correlation":corr,"min_point":ymin["point"],"min_value":ymin["value"],
            "max_point":ymax["point"],"max_value":ymax["value"],"start_value":values[0]["value"],"end_value":values[-1]["value"],
            "summary":("Generally increases with point" if corr>=0.65 else "Generally decreases with point" if corr<=-0.65 else "Varies non-monotonically across points"),
            "unit":_unit_for_column(str(rec.get("y_column") or "")),
        }
        cards.append(card)
        med=_safe_median(y); deviations=[abs(v-med) for v in y]; mad=_safe_median(deviations)
        if mad>0:
            for row in values:
                z=0.6745*(row["value"]-med)/mad
                if abs(z)>=3.5:
                    unusual.append({"technique_key":rec.get("technique_key"),"technique_label":rec.get("technique_label"),"view_id":rec.get("view_id"),"view_label":rec.get("view_label"),"metric":metric,"metric_label":metric_label,"point":row["point"],"value":row["value"],"robust_z":z,"unit":card["unit"]})
    unusual.sort(key=lambda r:abs(float(r.get("robust_z") or 0)), reverse=True)
    return cards, unusual[:12]


def _unit_for_column(name: str) -> str:
    low=_normalize_column(name)
    if "current_density" in low: return "A cm⁻²"
    if "current" in low: return "A"
    if "potential" in low or "voltage" in low or low.startswith("eta"): return "V"
    if "charge" in low: return "C"
    if "capacity" in low: return "capacity"
    if "frequency" in low: return "Hz"
    if "tau" in low or "time" in low: return "s"
    if any(t in low for t in ("zreal","zimag","zmod","impedance","resistance","gamma")): return "Ω"
    if "phase" in low: return "°"
    if "distance" in low or "position" in low: return "m"
    if "mass" in low: return "mass"
    return ""


def _discovery_summary(datasets: Sequence[Mapping[str, Any]], comparison: Mapping[str, Any]) -> dict[str, Any]:
    visible=[d for d in datasets if not d.get("ui_hidden_default")]
    points=sorted({d.get("point") for d in visible if d.get("point") is not None})
    methods=sorted({str(d.get("method_label")) for d in visible if d.get("method_label")})
    cycles=sorted({d.get("cycle") for d in visible if d.get("cycle") is not None})
    sweeps=sorted({d.get("sweep") for d in visible if d.get("sweep") is not None})
    tech_rows=[]
    for key, rows in sorted(((k,list(v)) for k,v in _group_by(visible,lambda d:str(d.get("technique_key") or "OTHER")).items()), key=lambda kv:(str(kv[1][0].get("family")),str(kv[1][0].get("technique_label")))):
        if key in {"PHOTO","FIGURE"}: continue
        tech_rows.append({"technique_key":key,"technique_label":rows[0].get("technique_label") or key,"family":rows[0].get("family") or "Other","data_kind":rows[0].get("data_kind") or "data","dataset_count":len(rows),"point_count":len({r.get("point") for r in rows if r.get("point") is not None}),"method_count":len({r.get("method_label") for r in rows if r.get("method_label")}),"analysis_count":sum(1 for r in rows if r.get("ui_role")=="analysis")})
    recommended=list(comparison.get("recommended") or [])
    trends, unusual=_trend_cards(datasets,recommended)
    analyses=[d for d in visible if d.get("ui_role")=="analysis"]
    analyses.sort(key=lambda d:(str(d.get("family")),str(d.get("display_name"))))
    return {
        "point_count":len(points),"point_min":min(points) if points else None,"point_max":max(points) if points else None,
        "method_count":len(methods),"cycle_count":len(cycles),"sweep_count":len(sweeps),
        "measurement_count":sum(1 for d in visible if d.get("ui_role")=="measurement"),
        "analysis_count":len(analyses),"media_count":sum(1 for d in datasets if d.get("ui_role")=="media"),
        "technical_dataset_count":sum(1 for d in datasets if d.get("ui_role")=="technical"),
        "supporting_dataset_count":sum(1 for d in datasets if d.get("ui_role")=="supporting"),
        "alternate_copy_count":sum(1 for d in datasets if d.get("ui_alternate_copy")),
        "techniques":tech_rows,"recommended_views":recommended,"trend_cards":trends,"unusual_points":unusual,
        "analysis_cards":[{"id":d.get("id"),"display_name":d.get("display_name"),"technique_label":d.get("technique_label"),"family":d.get("family"),"point":d.get("point"),"method_label":d.get("method_label"),"view_count":len(d.get("plot_views") or []),"status":(d.get("preview") or {}).get("status")} for d in analyses[:24]],
    }


def _group_by(rows: Sequence[Mapping[str, Any]], key_fn: Any) -> dict[Any,list[Mapping[str,Any]]]:
    out: dict[Any,list[Mapping[str,Any]]] = defaultdict(list)
    for row in rows: out[key_fn(row)].append(row)
    return out

def _comparison_summary(datasets: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    matrix: dict[tuple[str, str], dict[str, Any]] = {}
    advanced_matrix: dict[tuple[str, str], dict[str, Any]] = {}
    comparable_count = 0
    for d in datasets:
        views = d.get("plot_views") if isinstance(d.get("plot_views"), list) else []
        if not views and not d.get("summary_metrics"):
            continue
        if not d.get("ui_hidden_default") and not d.get("ui_hidden_from_compare"):
            comparable_count += 1
        key = str(d.get("technique_key") or "OTHER")
        if not d.get("ui_hidden_default") and not d.get("ui_hidden_from_compare"):
            row = groups.setdefault(key, {
                "technique_key": key, "technique_label": d.get("technique_label") or key,
                "family": d.get("family") or "Other", "data_kind": d.get("data_kind") or "data",
                "dataset_count": 0, "view_count": 0, "points": [], "methods": set(), "columns": defaultdict(int), "view_ids": set(),
            })
            row["dataset_count"] += 1
            if d.get("point") is not None: row["points"].append(d.get("point"))
            row["methods"].add(str(d.get("method_label") or "Method"))
            for c in d.get("columns") or []: row["columns"][str(c)] += 1
        for view in views:
            view_id = str(view.get("id") or "custom")
            if view_id in set(d.get("ui_suppressed_view_ids") or []):
                continue
            score = _view_base_score(d, view)
            target = advanced_matrix
            akey=(key,view_id)
            a=target.setdefault(akey,{"technique_key":key,"technique_label":d.get("technique_label") or key,"family":d.get("family") or "Other","view_id":view_id,"view_label":view.get("label") or view_id,"plot_type":view.get("plot_type") or "curve","dataset_ids":[],"points":[],"methods":set(),"x_column":view.get("x_column") or "","y_column":view.get("y_column") or "","description":view.get("description") or "","score":0})
            a["dataset_ids"].append(d.get("id")); a["score"]=max(a["score"],score)
            if d.get("point") is not None: a["points"].append(d.get("point"))
            a["methods"].add(str(d.get("method_label") or "Method"))
            if score < 35 or d.get("ui_hidden_default") or d.get("ui_hidden_from_compare"):
                continue
            row=groups[key]; row["view_ids"].add(view_id)
            mkey=(key,view_id)
            m=matrix.setdefault(mkey,{"technique_key":key,"technique_label":row["technique_label"],"family":row["family"],"view_id":view_id,"view_label":view.get("label") or view_id,"plot_type":view.get("plot_type") or "curve","dataset_ids":[],"points":[],"methods":set(),"x_column":view.get("x_column") or "","y_column":view.get("y_column") or "","description":view.get("description") or "","score":score})
            m["dataset_ids"].append(d.get("id")); m["score"]=max(m["score"],score)
            if d.get("point") is not None: m["points"].append(d.get("point"))
            m["methods"].add(str(d.get("method_label") or "Method"))
    out=[]
    for row in groups.values():
        pts=row.pop("points"); methods=sorted(row.pop("methods")); cols=sorted(row.pop("columns").items(), key=lambda x:(-x[1],x[0])); view_ids=sorted(row.pop("view_ids"))
        row.update({"point_min":min(pts) if pts else None,"point_max":max(pts) if pts else None,"unique_points":len(set(pts)),"methods":methods,"common_columns":[c for c,_ in cols],"view_ids":view_ids,"view_count":len(view_ids)})
        out.append(row)
    def finalize(rows):
        result=[]
        for m in rows.values():
            pts=m.pop("points"); methods=sorted(m.pop("methods")); ids=list(dict.fromkeys(m.pop("dataset_ids")))
            m.update({"dataset_ids":ids,"dataset_count":len(ids),"unique_points":len(set(pts)),"point_min":min(pts) if pts else None,"point_max":max(pts) if pts else None,"methods":methods})
            m["recommendation_score"]=int(m.get("score",0))+min(24,m["dataset_count"]*2)+min(24,m["unique_points"]*3)+min(12,len(methods)*3)
            result.append(m)
        return result
    matrix_rows=finalize(matrix); advanced_rows=finalize(advanced_matrix)
    matrix_rows.sort(key=lambda r:(-int(r.get("recommendation_score",0)),str(r["family"]),str(r["technique_label"]),str(r["view_label"])))
    advanced_rows.sort(key=lambda r:(str(r["family"]),str(r["technique_label"]),str(r["view_label"])))
    recommended=[]; seen=set(); per_tech=defaultdict(int)
    for r in matrix_rows:
        if not (r["dataset_count"]>=2 or r["unique_points"]>=2):
            continue
        semantic=_normalize_column(r.get("y_column") or r.get("view_id") or "")
        # Avoid presenting many aliases of the same scientific quantity as separate discoveries.
        fingerprint=(r["technique_key"],semantic)
        if fingerprint in seen or per_tech[r["technique_key"]]>=4:
            continue
        seen.add(fingerprint); per_tech[r["technique_key"]]+=1; recommended.append(r)
        if len(recommended)>=16: break
    return {"groups":sorted(out,key=lambda r:(str(r["family"]),str(r["technique_label"]))),"matrix":matrix_rows,"recommended":recommended,"advanced_matrix":advanced_rows,"comparison_dataset_count":comparable_count,"comparison_curve_count":sum(1 for d in datasets if not d.get("ui_hidden_default") and not d.get("ui_hidden_from_compare") and (d.get("preview") or {}).get("status")=="plot_ready"),"available_view_count":len(matrix_rows),"advanced_view_count":len(advanced_rows),"suppressed_technical_view_count":max(0,len(advanced_rows)-len(matrix_rows))}


def build_manifest(experiment_root: str | Path, *, analysis_root: str | Path | None = None, experiment_name: str | None = None, short_names_enabled: bool | None = None, create_batch_zips: bool = True) -> dict[str, Any]:
    root = Path(experiment_root).expanduser().resolve(); root.mkdir(parents=True, exist_ok=True)
    analysis, catalog_path = _find_analysis_root(root, analysis_root), _find_catalog(root)
    catalog_datasets = _catalog_datasets(root, analysis, catalog_path) if catalog_path else []
    # Always scan derived outputs, even when a raw-data catalog exists. v273 only
    # used the catalog and therefore omitted EIS fits, DRT peaks, Tafel results,
    # CA/CP summaries, maps, and other analysis products created later.
    scanned = _scan_datasets(root, analysis, skip_cache=bool(catalog_path))
    datasets = _merge_datasets(catalog_datasets, scanned)
    resolved_experiment_name = experiment_name or root.name
    for dataset in datasets:
        dataset["experiment_name"] = resolved_experiment_name
    _assign_ui_roles(datasets)
    short_names = _short_name_summary(root, enabled=short_names_enabled)
    batches = _write_batch_archives(root, datasets, short_names) if create_batch_zips else []
    files = _scan_files(root)
    media = _build_media(files, datasets)
    media_assets = _stage_media_assets(root, media, files, datasets)
    techniques: dict[str, int] = defaultdict(int); families: dict[str, int] = defaultdict(int); kinds: dict[str, int] = defaultdict(int); preview_status: dict[str, int] = defaultdict(int)
    for row in datasets:
        techniques[str(row.get("technique_key") or "OTHER")] += 1
        families[str(row.get("family") or "Other")] += 1
        kinds[str(row.get("data_kind") or "data")] += 1
        preview_status[str((row.get("preview") or {}).get("status") or "unavailable")] += 1
    comparison = _comparison_summary(datasets)
    discovery = _discovery_summary(datasets, comparison)
    coverage = {
        "catalog_datasets": len(catalog_datasets), "filesystem_discovered_datasets": len(scanned),
        "raw_or_data": kinds.get("raw",0)+kinds.get("data",0), "analysis": kinds.get("analysis",0), "media": kinds.get("media",0),
        "comparable": comparison.get("comparison_dataset_count",0), "plot_ready": preview_status.get("plot_ready",0), "table_only": preview_status.get("table_ready",0),
        "download_only": preview_status.get("download_only",0), "unavailable": preview_status.get("unavailable",0),
    }
    return {
        "schema": SCHEMA, "version": VERSION, "created": datetime.now().isoformat(timespec="seconds"),
        "experiment_name": resolved_experiment_name,
        "portable": True, "relative_paths_only": True, "requires_faradaiq": False, "requires_python": False, "requires_web_server": False, "requires_internet": False,
        "html_file": HTML_NAME, "manifest_file": MANIFEST_NAME,
        "analysis_root": _posix_rel(analysis, root) if analysis and analysis.exists() else "",
        "catalog_source": _posix_rel(catalog_path, root) if catalog_path and catalog_path.exists() else "filesystem_scan",
        "dataset_count": len(datasets), "batch_count": len(batches), "file_count": len(files), "media_count": len(media),
        "technique_counts": dict(sorted(techniques.items())), "family_counts": dict(sorted(families.items())), "data_kind_counts": dict(sorted(kinds.items())),
        "preview_status_counts": dict(sorted(preview_status.items())), "coverage": coverage,
        "technique_registry": _json_safe(TECHNIQUE_REGISTRY),
        "comparison": comparison, "discovery": discovery, "short_names": short_names, "primary_downloads": _find_primary_downloads(root),
        "media_assets": media_assets,
        "datasets": datasets, "batches": batches, "media": media, "files": files,
        "portability_note": "Move or copy the entire experiment folder, not the HTML file alone. All links are relative to this folder.",
        "browser_security_note": "Local web browsers cannot copy arbitrary folders. Individual files and pre-built batch ZIPs use normal browser download/save behavior.",
        "preview_policy": {
            "source": "authoritative processed tables plus all discovered derived analysis outputs",
            "full_file_sampling": True, "maximum_embedded_rows_per_table": MAX_PREVIEW_ROWS,
            "images_parsed_as_tables": False, "column_driven_classification": True,
            "portable_media_assets": True, "portable_media_asset_root": f"{ASSET_DIR_NAME}/{MEDIA_ASSET_DIR_NAME}",
            "robust_autoscale_available": True, "box_zoom_available": True, "direct_curve_selection_available": True,
            "multi_view_techniques": ["EIS/GEIS Nyquist and Bode", "DRT tau/frequency", "Tafel", "voltammetry", "CA/CP/deposition", "scientific analysis metrics", "spatial maps"],
            "discover_dashboard": True, "trend_finder": True, "technical_metadata_hidden_by_default": True,
        },
    }


_CSS = r"""
:root{color-scheme:dark;--bg:#070a0e;--panel:#0e151d;--panel2:#151f2a;--line:#2c3b4b;--text:#edf4f9;--muted:#9fb0c2;--accent:#58adff;--good:#67d78e;--warn:#ffd166;--shadow:0 12px 30px rgba(0,0,0,.28)}*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--bg);color:var(--text);font:14px Segoe UI,Arial,sans-serif}.top{position:sticky;top:0;z-index:20;background:linear-gradient(180deg,#0e1620,#091019);border-bottom:1px solid var(--line);padding:12px 18px}.brandline{display:flex;align-items:center;justify-content:space-between;gap:16px}.brand h1{font-size:21px;margin:0}.muted{color:var(--muted)}.stats{display:flex;gap:8px;flex-wrap:wrap;margin-top:7px}.pill,.badge{border:1px solid var(--line);border-radius:999px;padding:4px 9px;background:var(--panel)}.badge{display:inline-block;font-size:11px;color:#b9d8f2;background:#12263a}.nav{display:flex;gap:4px;margin-top:11px;overflow:auto}.nav button{border:1px solid transparent;background:transparent;color:var(--muted);padding:8px 13px;border-radius:6px;cursor:pointer;font-weight:650;white-space:nowrap}.nav button:hover,.nav button.active{background:#16283a;color:#fff;border-color:#31516e}.page{display:none;padding:14px 16px 26px}.page.active{display:block}.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;border:1px solid #3c536a;background:#172537;color:var(--text);border-radius:6px;padding:7px 10px;text-decoration:none;cursor:pointer}.btn:hover{border-color:var(--accent);background:#1d334b}.btn.primary{background:#0d4f85;border-color:#2e84c3}.btn.small{padding:4px 7px;font-size:12px}.actions,.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.dashboard{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-bottom:12px}.card,.control-panel,.matrix-card{background:var(--panel);border:1px solid var(--line);border-radius:9px;box-shadow:var(--shadow)}.metric{padding:13px}.metric b{font-size:24px;display:block}.metric span{color:var(--muted)}.compare-layout{display:grid;grid-template-columns:minmax(285px,340px) minmax(0,1fr);gap:12px}.control-panel{padding:12px;max-height:calc(100vh - 165px);overflow:auto;position:sticky;top:145px}.control-panel h3,.card h3{margin:3px 0 9px;font-size:15px}.field{margin:9px 0}.field label{display:block;color:#bfd0df;font-size:12px;margin-bottom:4px}.field select,.field input[type=number],.field input[type=search],.detail-view select{width:100%;background:#080d13;color:var(--text);border:1px solid var(--line);border-radius:5px;padding:7px}.field.inline{display:grid;grid-template-columns:1fr 1fr;gap:7px}.checks{display:grid;grid-template-columns:1fr 1fr;gap:5px 8px;margin:8px 0}.range-line{display:grid;grid-template-columns:1fr 48px;gap:8px;align-items:center}.range-line input{width:100%}.view-note{padding:8px;border:1px solid #31485f;background:#0a1119;border-radius:6px;font-size:12px;line-height:1.45}.plot-column{min-width:0}.plot-card{padding:10px}.plot-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:7px}.plot-title{font-size:17px;font-weight:700}.plot-sub{font-size:12px;color:var(--muted);margin-top:2px}.canvas-wrap{position:relative;background:#05080c;border:1px solid var(--line);border-radius:8px;overflow:auto;min-height:560px;max-height:76vh}.canvas-wrap canvas{display:block;width:100%;min-height:560px;cursor:crosshair}.plot-tip{position:absolute;display:none;pointer-events:none;background:rgba(8,13,19,.96);border:1px solid #49637e;border-radius:6px;padding:7px 9px;font-size:12px;max-width:300px;box-shadow:var(--shadow)}.plot-status{display:flex;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:12px;margin-top:7px}.curve-panel{margin-top:10px;padding:10px}.curve-actions{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}.curve-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));max-height:260px;overflow:auto;border-top:1px solid var(--line)}.curve-item{display:flex;align-items:flex-start;gap:7px;padding:7px;border-bottom:1px solid #22303e;font-size:12px}.curve-item.highlighted{background:#183754;outline:1px solid #4aa5f5}.curve-item small{display:block;color:var(--muted);margin-top:2px}.curve-name,.swatch-button{border:0;background:transparent;color:inherit;text-align:left;padding:0;cursor:pointer}.curve-name{min-width:0;flex:1}.swatch-button{width:24px;height:18px;display:flex;align-items:center}.swatch{width:20px;height:3px;border-radius:3px;flex:0 0 auto}.btn.active{background:#0d4f85;border-color:#58adff;box-shadow:0 0 0 1px rgba(88,173,255,.25)}.matrix-toolbar,.gallery-toolbar,.dataset-toolbar,.files-toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}.matrix-toolbar input,.gallery-toolbar input,.gallery-toolbar select,.dataset-toolbar input,.dataset-toolbar select,.files-toolbar input,.files-toolbar select{background:#080d13;color:var(--text);border:1px solid var(--line);border-radius:5px;padding:8px;min-width:190px}.matrix-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:10px}.matrix-card{padding:13px}.matrix-card h3{margin:8px 0 3px}.matrix-card p{margin:5px 0}.matrix-stats{margin:8px 0;color:#cfe4f4}.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}.media-card{background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}.media-frame{height:180px;background:#05080c;display:flex;align-items:center;justify-content:center;cursor:pointer}.media-frame img{max-width:100%;max-height:100%;object-fit:contain}.media-frame .empty{padding:14px;line-height:1.4}.interaction-row{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}.fit-row{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;margin-top:7px}.media-body{padding:9px}.media-title{font-weight:650;overflow-wrap:anywhere}.media-meta{color:var(--muted);font-size:12px;margin:4px 0}.dataset-layout{display:grid;grid-template-columns:minmax(300px,390px) minmax(0,1fr);gap:12px}.dataset-list{max-height:calc(100vh - 180px);overflow:auto}.section{background:var(--panel);border:1px solid var(--line);border-radius:7px;margin-bottom:8px;overflow:hidden}.section summary{padding:9px 10px;cursor:pointer;background:var(--panel2);font-weight:650}.dataset-row{padding:8px 10px;border-top:1px solid #243240;cursor:pointer}.dataset-row:hover,.dataset-row.selected{background:#16283a}.dataset-row .sub{color:var(--muted);font-size:12px;margin-top:3px}.detail{padding:14px;min-width:0}.detail h2{margin-top:0;overflow-wrap:anywhere}.meta{display:grid;grid-template-columns:minmax(130px,190px) minmax(0,1fr);gap:1px;margin:12px 0;background:var(--line);border:1px solid var(--line)}.meta>div{background:var(--panel);padding:6px 8px}.path{overflow-wrap:anywhere}.detail-canvas{background:#05080c;border:1px solid var(--line);border-radius:7px;overflow:hidden;margin:8px 0}.detail-canvas canvas{width:100%;height:400px;display:block}.detail-view{max-width:440px;margin:8px 0}.tablewrap{max-height:420px;overflow:auto;border:1px solid var(--line);border-radius:6px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid #263442;padding:5px 7px;text-align:left;white-space:nowrap}th{position:sticky;top:0;background:#182433}.file{display:grid;grid-template-columns:minmax(0,1fr) 90px auto;gap:8px;align-items:center;padding:7px 10px;border-top:1px solid #243240}.file a:first-child{overflow-wrap:anywhere}.note{border:1px solid #3d5267;background:#111c27;border-radius:7px;padding:10px;margin:10px 0;line-height:1.45}.note.good{border-color:#2e6d49;background:#0d2117}.empty{padding:18px;color:var(--muted);text-align:center}.image-detail{display:flex;justify-content:center;background:#05080c;border:1px solid var(--line);border-radius:7px;padding:8px}.image-detail img{max-width:100%;max-height:70vh}.lightbox{display:none;position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.92);padding:30px}.lightbox.open{display:flex;align-items:center;justify-content:center}.lightbox img{max-width:95vw;max-height:88vh}.lightbox .close{position:absolute;right:20px;top:15px;font-size:22px}@media(max-width:980px){.compare-layout,.dataset-layout{grid-template-columns:1fr}.control-panel{position:static;max-height:none}.canvas-wrap{max-height:none}.file{grid-template-columns:1fr}.top{position:static}}

.hero{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(260px,.7fr);gap:14px;margin-bottom:14px}.hero-main{padding:24px;background:linear-gradient(135deg,#102238,#0d1722 58%,#10261d);border:1px solid #31506b;border-radius:12px}.hero-main h2{font-size:28px;margin:0 0 7px}.hero-main p{font-size:15px;line-height:1.55;color:#c5d4e2;max-width:850px}.global-search{display:flex;gap:8px;margin-top:16px}.global-search input{flex:1;min-width:0;background:#071019;color:var(--text);border:1px solid #45647e;border-radius:8px;padding:11px 13px;font-size:14px}.quick-grid,.recommend-grid,.trend-grid,.technique-grid,.analysis-grid,.download-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:10px}.quick-card,.recommend-card,.trend-card,.technique-card,.analysis-card,.download-card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;box-shadow:var(--shadow)}.quick-card{cursor:pointer;min-height:118px}.quick-card:hover,.recommend-card:hover,.trend-card:hover{border-color:#4d83ad;transform:translateY(-1px)}.quick-card h3,.recommend-card h3,.trend-card h3,.technique-card h3,.analysis-card h3{margin:5px 0 6px}.section-head{display:flex;justify-content:space-between;gap:12px;align-items:end;margin:20px 0 9px}.section-head h2{margin:0;font-size:19px}.section-head p{margin:3px 0 0;color:var(--muted)}.insight{border-left:4px solid var(--accent);padding:9px 11px;background:#0c1722;border-radius:0 7px 7px 0;margin:8px 0}.insight.warn{border-left-color:var(--warn);background:#211b0d}.spark{width:100%;height:74px;display:block;background:#080d13;border:1px solid #26394b;border-radius:6px;margin:9px 0}.trend-summary{font-weight:650;color:#d7e9f7}.coverage-table td,.coverage-table th{text-align:left}.role-tabs,.compare-intent{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}.role-tabs button,.compare-intent button{border:1px solid #35506a;background:#101c28;color:#c1d1df;padding:7px 10px;border-radius:999px;cursor:pointer}.role-tabs button.active,.compare-intent button.active{background:#154f7a;border-color:#58adff;color:white}.advanced-box{margin-top:10px;border:1px solid #2a3d50;border-radius:7px;background:#0b1219}.advanced-box summary{cursor:pointer;padding:9px 11px;font-weight:650}.advanced-box>div{padding:0 11px 11px}.compare-toolbar{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:8px;padding:10px;margin-bottom:10px}.compare-toolbar select,.compare-toolbar input{width:100%;background:#080d13;color:var(--text);border:1px solid var(--line);border-radius:6px;padding:8px}.downloads-note{padding:12px;border:1px solid #31516e;background:#0d1b28;border-radius:8px;margin-bottom:10px}.hidden-technical{opacity:.78}.empty-state{padding:30px;text-align:center;border:1px dashed #385067;border-radius:9px;color:var(--muted)}@media(max-width:980px){.hero{grid-template-columns:1fr}.compare-toolbar{grid-template-columns:1fr 1fr}}@media(max-width:620px){.global-search{display:block}.global-search .btn{margin-top:8px;width:100%}.compare-toolbar{grid-template-columns:1fr}}

"""

_JS = r"""
const M=window.FARADAIQ_MANIFEST;
const ALL_TECH='__ALL__';
const q=s=>document.querySelector(s), qa=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const href=p=>String(p||'').split('/').map(encodeURIComponent).join('/');
const fmt=n=>{n=Number(n||0);if(n<1024)return n+' B';if(n<1048576)return(n/1024).toFixed(1)+' KB';if(n<1073741824)return(n/1048576).toFixed(1)+' MB';return(n/1073741824).toFixed(2)+' GB'};
const num=v=>typeof v==='number'&&Number.isFinite(v), clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const nice=v=>{if(!Number.isFinite(v))return'';const a=Math.abs(v);return a!==0&&(a>=1e4||a<1e-3)?v.toExponential(3):v.toPrecision(5).replace(/\.?0+$/,'')};
let compare={family:'',tech:'',view:'',method:'',search:'',mode:'auto',metric:'max',norm:'none',selected:new Set(),pointMin:null,pointMax:null,opacity:.55,width:1.2,offset:.12,logX:false,logY:false,invertY:false,legend:true,scaleMode:'robust',interaction:'pan',highlighted:'',viewBounds:null,drag:null,box:null,lastRender:[],currentView:null,clippedCount:0,includeAlternates:false};
let current=null,detailViewId='';
function colorFor(i,n,a=1){const h=(210+i*137.508)%360;return `hsla(${h},78%,62%,${a})`}
function showPage(name){qa('.page').forEach(x=>x.classList.toggle('active',x.id==='page-'+name));qa('.nav button').forEach(x=>x.classList.toggle('active',x.dataset.page===name));if(name==='overview')renderOverview();if(name==='compare')requestAnimationFrame(renderComparison);if(name==='trends')renderTrends();if(name==='matrix')renderMatrix();if(name==='gallery')renderGallery();if(name==='datasets')renderDatasetList();if(name==='downloads')renderDownloads();if(name==='files')renderFiles()}
function comparable(){return (M.datasets||[]).filter(d=>!d.ui_hidden_default&&(compare.includeAlternates||!d.ui_hidden_from_compare)&&(((d.plot_views||[]).length>0)||Object.keys(d.summary_metrics||{}).length>0))}
function techniqueRows(){return comparable().filter(d=>(!compare.family||d.family===compare.family)&&(!compare.tech||compare.tech===ALL_TECH||d.technique_key===compare.tech))}
function viewCatalog(rows=techniqueRows()){const map=new Map();rows.forEach(d=>(d.plot_views||[]).filter(v=>!(d.ui_suppressed_view_ids||[]).includes(v.id)).forEach(v=>{const old=map.get(v.id)||{...v,count:0,datasets:new Set()};old.count++;old.datasets.add(d.id);map.set(v.id,old)}));return [...map.values()].sort((a,b)=>(a.priority??50)-(b.priority??50)||String(a.label).localeCompare(String(b.label)))}
function findView(d,id=compare.view){if((d.ui_suppressed_view_ids||[]).includes(id))return null;return (d.plot_views||[]).find(v=>v.id===id)||null}
function methodsFor(rows){return [...new Set(rows.map(d=>d.method_label||'Method'))].sort()}
function filteredCandidates(requireSelected=false){const term=compare.search.trim().toLowerCase();return comparable().filter(d=>{
  if(compare.family&&d.family!==compare.family)return false;if(compare.tech&&compare.tech!==ALL_TECH&&d.technique_key!==compare.tech)return false;if(compare.method&&d.method_label!==compare.method)return false;
  if(compare.view&&!findView(d))return false;if(d.point!=null&&compare.pointMin!=null&&d.point<compare.pointMin)return false;if(d.point!=null&&compare.pointMax!=null&&d.point>compare.pointMax)return false;
  if(term&&!(`${d.display_name} ${d.method_label} ${d.physical_path} ${d.role} ${d.family}`).toLowerCase().includes(term))return false;
  return !requireSelected||compare.selected.has(d.id);
})}
function selectOption(el,value){[...el.options].forEach(o=>o.selected=o.value===String(value??''))}
function chooseDefaults(){const groups=M.comparison?.groups||[];const first=groups.find(g=>g.dataset_count>0)||groups[0];compare.family=first?.family||comparable()[0]?.family||'';compare.tech=ALL_TECH;const rows=comparable().filter(d=>!compare.family||d.family===compare.family);const views=viewCatalog(rows);compare.view=views[0]?.id||'';const candidates=rows.filter(d=>!compare.view||findView(d));compare.method='';compare.selected=new Set(candidates.map(d=>d.id));const pts=candidates.map(d=>d.point).filter(v=>v!=null);compare.pointMin=pts.length?Math.min(...pts):null;compare.pointMax=pts.length?Math.max(...pts):null;applyViewDefaults(views[0])}
function applyViewDefaults(view){if(!view)return;compare.currentView=view;compare.logX=view.x_scale==='log';compare.logY=view.y_scale==='log';compare.invertY=false;compare.mode=view.plot_type==='spatial'?'spatial':'auto';compare.viewBounds=null}
function populateControls(resetSelection=false,resetView=false){
 const families=[...new Set(comparable().map(d=>d.family||'Other'))].sort();q('#cmpFamily').innerHTML='<option value="">All families</option>'+families.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');selectOption(q('#cmpFamily'),compare.family);
 const techs=[...new Set(comparable().filter(d=>!compare.family||d.family===compare.family).map(d=>d.technique_key))].sort();if(!compare.tech||(compare.tech!==ALL_TECH&&!techs.includes(compare.tech)))compare.tech=ALL_TECH;q('#cmpTech').innerHTML=`<option value="${ALL_TECH}">All compatible techniques${compare.family?' in this family':''}</option>`+techs.map(t=>`<option value="${esc(t)}">${esc((M.technique_registry?.[t]?.label)||t)}</option>`).join('');selectOption(q('#cmpTech'),compare.tech);
 const base=comparable().filter(d=>(!compare.family||d.family===compare.family)&&(!compare.tech||compare.tech===ALL_TECH||d.technique_key===compare.tech));const views=viewCatalog(base);if(resetView||!views.some(v=>v.id===compare.view)){compare.view=views[0]?.id||'';applyViewDefaults(views[0])}q('#cmpView').innerHTML=views.map(v=>`<option value="${esc(v.id)}">${esc(v.label)} (${v.count})</option>`).join('');selectOption(q('#cmpView'),compare.view);
 const candidates=base.filter(d=>!compare.view||findView(d));const methods=methodsFor(candidates);if(compare.method&&!methods.includes(compare.method))compare.method='';q('#cmpMethod').innerHTML='<option value="">All methods / groups</option>'+methods.map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join('');selectOption(q('#cmpMethod'),compare.method);
 if(resetSelection)compare.selected=new Set(candidates.map(d=>d.id));const pts=candidates.map(d=>d.point).filter(v=>v!=null);const lo=pts.length?Math.min(...pts):null,hi=pts.length?Math.max(...pts):null;if(lo!=null&&(compare.pointMin==null||compare.pointMin<lo||compare.pointMin>hi))compare.pointMin=lo;if(hi!=null&&(compare.pointMax==null||compare.pointMax<lo||compare.pointMax>hi))compare.pointMax=hi;q('#pointMin').value=compare.pointMin??'';q('#pointMax').value=compare.pointMax??'';
 q('#logX').checked=compare.logX;q('#logY').checked=compare.logY;q('#invertY').checked=compare.invertY;q('#showLegend').checked=compare.legend;q('#cmpMode').value=compare.mode;q('#metricField').style.display=compare.mode==='metric'?'block':'none';renderCurveList();updateViewDescription();
}
function updateViewDescription(){const v=viewCatalog().find(x=>x.id===compare.view);q('#viewDescription').innerHTML=v?`<b>${esc(v.label)}</b><br>${esc(v.description||`${v.x_column} vs ${v.y_column}`)}<br><span class="muted">${esc(v.x_column||'row index')} → ${esc(v.y_column||'derived metric')}</span>`:'No compatible view.'}
function curveLabel(d){const bits=[];if(experimentNames().length>1)bits.push(d.experiment_name||M.experiment_name);if(compare.tech===ALL_TECH)bits.push((M.technique_registry?.[d.technique_key]?.label)||d.technique_key);if(d.point!=null)bits.push('Point '+String(d.point).padStart(3,'0'));if(d.method_label)bits.push(d.method_label);if(d.cycle!=null)bits.push('Cycle '+d.cycle);if(d.sweep!=null)bits.push('Sweep '+d.sweep);return bits.join(' · ')||d.display_name}
function renderCurveList(){const rows=filteredCandidates(false);q('#curveCount').textContent=`${filteredCandidates(true).length} selected / ${rows.length} compatible datasets${compare.highlighted?' · 1 highlighted':''}`;q('#curveList').innerHTML=rows.map((d,i)=>`<div class="curve-item ${compare.highlighted===d.id?'highlighted':''}"><input type="checkbox" data-curve="${d.id}" ${compare.selected.has(d.id)?'checked':''}><button class="swatch-button" data-highlight="${d.id}" title="Highlight this curve"><span class="swatch" style="background:${colorFor(i,rows.length)}"></span></button><button class="curve-name" data-highlight="${d.id}" title="Highlight this curve"><b>${esc(curveLabel(d))}</b><small>${esc(d.data_kind)} · ${esc(d.role)}</small></button><button class="btn small" data-solo="${d.id}" title="Show only this curve">Solo</button></div>`).join('')||'<div class="empty">No datasets match this technique, view, and filter.</div>';qa('[data-curve]').forEach(el=>el.onchange=()=>{el.checked?compare.selected.add(el.dataset.curve):compare.selected.delete(el.dataset.curve);if(!el.checked&&compare.highlighted===el.dataset.curve)compare.highlighted='';renderCurveList();renderComparison()});qa('[data-highlight]').forEach(el=>el.onclick=()=>{compare.highlighted=compare.highlighted===el.dataset.highlight?'':el.dataset.highlight;if(compare.highlighted)compare.selected.add(compare.highlighted);compare.viewBounds=null;renderCurveList();renderComparison()});qa('[data-solo]').forEach(el=>el.onclick=()=>{compare.selected=new Set([el.dataset.solo]);compare.highlighted=el.dataset.solo;compare.scaleMode='highlighted';q('#scaleMode').value='highlighted';compare.viewBounds=null;renderCurveList();renderComparison()})}
function transform(v,name){if(!num(v))return null;switch(name){case'negate':return-v;case'abs':return Math.abs(v);case'log10_abs':return Math.log10(Math.max(Math.abs(v),1e-300));case'sqrt_positive':return v>=0?Math.sqrt(v):null;case'inverse_sqrt_positive':return v>0?1/Math.sqrt(v):null;case'inverse_2pi_positive':return v>0?1/(2*Math.PI*v):null;default:return v}}
function viewPoints(d,view){const p=d.preview||{},rows=p.rows||[],cols=d.columns||[];if(!rows.length||!view)return[];let pts=[];for(let ri=0;ri<rows.length;ri++){const r=rows[ri];let x=view.synthetic_x?ri:r[view.x_index];let y;const xd=view.x_derived_columns||[],yd=view.derived_columns||[];if(view.x_transform==='admittance_real_columns'){const a=r[xd[0]],b=r[xd[1]],den=num(a)&&num(b)?a*a+b*b:0;x=den?a/den:null}else x=transform(x,view.x_transform||'identity');if(view.y_transform==='hypot_columns'){const a=r[yd[0]],b=r[yd[1]];y=num(a)&&num(b)?Math.hypot(a,b):null}else if(view.y_transform==='inverse_hypot_columns'){const a=r[yd[0]],b=r[yd[1]],den=num(a)&&num(b)?Math.hypot(a,b):0;y=den?1/den:null}else if(view.y_transform==='atan2_deg_columns'){const a=r[yd[0]],b=r[yd[1]];y=num(a)&&num(b)?Math.atan2(a,b)*180/Math.PI:null}else if(view.y_transform==='admittance_imag_columns'){const a=r[yd[0]],b=r[yd[1]],den=num(a)&&num(b)?a*a+b*b:0;y=den?-b/den:null}else if(view.y_transform==='series_capacitance_columns'){const f=r[yd[0]],zi=r[yd[1]];y=num(f)&&num(zi)&&f>0&&zi!==0?-1/(2*Math.PI*f*zi):null}else{y=r[view.y_index];y=transform(y,view.y_transform||'identity')}if(!num(x)||!num(y))continue;if(compare.logX){if(x<=0)continue;x=Math.log10(x)}if(compare.logY){if(y<=0)continue;y=Math.log10(y)}pts.push([x,y,r,ri])}
 if(!pts.length)return pts;const ys=pts.map(v=>v[1]);if(compare.norm==='maxabs'){const s=Math.max(...ys.map(Math.abs))||1;pts=pts.map(v=>[v[0],v[1]/s,v[2],v[3]])}else if(compare.norm==='minmax'){const lo=Math.min(...ys),hi=Math.max(...ys),s=hi-lo||1;pts=pts.map(v=>[v[0],(v[1]-lo)/s,v[2],v[3]])}else if(compare.norm==='baseline'){const b=ys[0];pts=pts.map(v=>[v[0],v[1]-b,v[2],v[3]])}else if(compare.norm==='area'){let a=0;for(let i=1;i<pts.length;i++)a+=Math.abs((pts[i][0]-pts[i-1][0])*(pts[i][1]+pts[i-1][1])/2);a=a||1;pts=pts.map(v=>[v[0],v[1]/a,v[2],v[3]])}if(compare.invertY)pts=pts.map(v=>[v[0],-v[1],v[2],v[3]]);return pts}
function quantile(values,p){const a=values.filter(num).sort((x,y)=>x-y);if(!a.length)return null;const k=(a.length-1)*p,i=Math.floor(k),f=k-i;return a[i+1]===undefined?a[i]:a[i]+f*(a[i+1]-a[i])}function paddedBounds(x0,x1,y0,y1){if(x0===x1){x0-=.5;x1+=.5}if(y0===y1){y0-=.5;y1+=.5}const px=(x1-x0)*.05,py=(y1-y0)*.08;return{x0:x0-px,x1:x1+px,y0:y0-py,y1:y1+py}}function fitBounds(series,mode=compare.scaleMode){const all=series.filter(s=>s.pts?.length);if(!all.length)return{x0:0,x1:1,y0:0,y1:1,clipped:0};let chosen=all;if(mode==='highlighted'&&compare.highlighted){const hit=all.filter(s=>s.d.id===compare.highlighted);if(hit.length)chosen=hit}const xs=all.flatMap(s=>s.pts.map(v=>v[0])),x0=Math.min(...xs),x1=Math.max(...xs);if(mode==='full'||chosen.length<4){const ys=chosen.flatMap(s=>s.pts.map(v=>v[1]+(s.offset||0)));return{...paddedBounds(x0,x1,Math.min(...ys),Math.max(...ys)),clipped:Math.max(0,all.length-chosen.length)}}const stats=chosen.map(s=>{const ys=s.pts.map(v=>v[1]+(s.offset||0));const lo=quantile(ys,.02),hi=quantile(ys,.98),span=Math.max(Math.abs((hi??0)-(lo??0)),1e-300),amp=Math.max(Math.abs(lo??0),Math.abs(hi??0),1e-300);return{s,lo,hi,span,amp}});const medSpan=quantile(stats.map(v=>v.span),.5)||1,medAmp=quantile(stats.map(v=>v.amp),.5)||1;let kept=stats.filter(v=>v.span<=medSpan*30&&v.amp<=medAmp*30);if(kept.length<Math.max(2,Math.ceil(stats.length*.5)))kept=stats;const ys=kept.flatMap(v=>v.s.pts.map(p=>p[1]+(v.s.offset||0))),y0=quantile(ys,.01),y1=quantile(ys,.99);return{...paddedBounds(x0,x1,y0??Math.min(...ys),y1??Math.max(...ys)),clipped:stats.length-kept.length}}
function prepCanvas(c,height=560){const r=c.getBoundingClientRect(),d=window.devicePixelRatio||1;c.style.height=height+'px';c.width=Math.max(600,Math.round(r.width*d));c.height=Math.round(height*d);const x=c.getContext('2d');x.setTransform(d,0,0,d,0,0);return{x,w:r.width,h:height}}
function axisLabels(view){return{x:(compare.logX?'log10 ':'')+(view?.x_column||'row index'),y:(compare.logY?'log10 ':'')+(compare.invertY?'− ':'')+(view?.y_column||'value')}}
function axes(ctx,w,h,b,view,L=78,R=30,T=28,B=58){ctx.strokeStyle='#60758b';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(L,T);ctx.lineTo(L,h-B);ctx.lineTo(w-R,h-B);ctx.stroke();ctx.font='12px Segoe UI';for(let i=0;i<=5;i++){const f=i/5,px=L+f*(w-L-R),py=h-B-f*(h-T-B);ctx.strokeStyle='rgba(96,117,139,.24)';ctx.beginPath();ctx.moveTo(px,T);ctx.lineTo(px,h-B);ctx.moveTo(L,py);ctx.lineTo(w-R,py);ctx.stroke();ctx.fillStyle='#aab9c8';ctx.fillText(nice(b.x0+f*(b.x1-b.x0)),px-18,h-B+20);ctx.fillText(nice(b.y0+f*(b.y1-b.y0)),6,py+4)}const labels=axisLabels(view);ctx.fillStyle='#dbe8f1';ctx.fillText(labels.x,w/2-55,h-13);ctx.save();ctx.translate(18,h/2+35);ctx.rotate(-Math.PI/2);ctx.fillText(labels.y,0,0);ctx.restore();return{L,R,T,B}}
function emptyPlot(ctx,text){ctx.fillStyle='#9fb0c2';ctx.font='15px Segoe UI';ctx.textAlign='center';ctx.fillText(text,ctx.canvas.width/(2*(window.devicePixelRatio||1)),90);ctx.textAlign='left'}
function activeSeries(){return filteredCandidates(true).map((d,i)=>{const v=findView(d);return{d,view:v,pts:viewPoints(d,v),i,offset:0}}).filter(s=>s.pts.length>0)}
function effectiveMode(view){if(compare.mode!=='auto')return compare.mode;return view?.plot_type==='spatial'?'spatial':(view?.plot_type==='metric'?'overlay':'overlay')}
function renderComparison(){const c=q('#compareCanvas');if(!c)return;const view=viewCatalog().find(v=>v.id===compare.view)||compare.currentView;let series=activeSeries(),mode=effectiveMode(view);let height=560;if(mode==='small'){const cols=Math.max(1,Math.ceil(Math.sqrt(series.length*1.6))),rows=Math.ceil(series.length/cols);height=clamp(rows*170,560,6000)}const {x:ctx,w,h}=prepCanvas(c,height);ctx.fillStyle='#05080c';ctx.fillRect(0,0,w,h);if(!series.length){emptyPlot(ctx,'No selected dataset contains this scientific view.');compare.lastRender=[];updatePlotText(series,view,mode);return}if(mode==='spatial'){drawSpatial(ctx,w,h,series,view);updatePlotText(series,view,mode);return}if(mode==='metric'){drawMetric(ctx,w,h,series,view);updatePlotText(series,view,mode);return}if(mode==='small'){drawSmall(ctx,w,h,series);updatePlotText(series,view,mode);return}if(mode==='waterfall'){const all=series.flatMap(s=>s.pts.map(v=>v[1]));const span=(Math.max(...all)-Math.min(...all)||1)*compare.offset;series.forEach((s,i)=>s.offset=i*span)}const fitted=fitBounds(series),b=compare.viewBounds||fitted;compare.clippedCount=compare.viewBounds?0:(fitted.clipped||0);const pad=axes(ctx,w,h,b,view),sx=v=>pad.L+(v-b.x0)/(b.x1-b.x0)*(w-pad.L-pad.R),sy=v=>pad.T+(1-(v-b.y0)/(b.y1-b.y0))*(h-pad.T-pad.B);ctx.save();ctx.beginPath();ctx.rect(pad.L,pad.T,w-pad.L-pad.R,h-pad.T-pad.B);ctx.clip();series.forEach((s,i)=>{const hi=compare.highlighted===s.d.id;ctx.strokeStyle=colorFor(i,series.length,hi?1:(compare.highlighted?Math.min(.24,compare.opacity):compare.opacity));ctx.lineWidth=hi?Math.max(2.6,compare.width+1.8):compare.width;ctx.beginPath();s.pts.forEach((v,j)=>{const px=sx(v[0]),py=sy(v[1]+s.offset);j?ctx.lineTo(px,py):ctx.moveTo(px,py)});ctx.stroke()});if(compare.box){ctx.fillStyle='rgba(88,173,255,.14)';ctx.strokeStyle='#58adff';ctx.lineWidth=1.2;const x=Math.min(compare.box.x0,compare.box.x1),y=Math.min(compare.box.y0,compare.box.y1),bw=Math.abs(compare.box.x1-compare.box.x0),bh=Math.abs(compare.box.y1-compare.box.y0);ctx.fillRect(x,y,bw,bh);ctx.strokeRect(x,y,bw,bh)}ctx.restore();if(compare.legend&&series.length<=40){ctx.font='11px Segoe UI';series.slice(0,40).forEach((s,i)=>{const x=w-230,y=18+i*14;ctx.fillStyle=colorFor(i,series.length,1);ctx.fillRect(x,y-7,18,2);ctx.fillStyle=compare.highlighted===s.d.id?'#ffffff':'#d9e4ec';ctx.fillText(curveLabel(s.d).slice(0,34),x+24,y-3)})}compare.lastRender=series.map(s=>({...s,b,pad,sx,sy}));updatePlotText(series,view,mode)}
function drawSmall(ctx,w,h,series){const cols=Math.max(1,Math.ceil(Math.sqrt(series.length*w/h))),rows=Math.ceil(series.length/cols),cw=w/cols,ch=h/rows;series.forEach((s,i)=>{const col=i%cols,row=Math.floor(i/cols),x0=col*cw,y0=row*ch,b=fitBounds([s]),L=x0+8,R=x0+cw-7,T=y0+22,B=y0+ch-9,sx=v=>L+(v-b.x0)/(b.x1-b.x0)*(R-L),sy=v=>T+(1-(v-b.y0)/(b.y1-b.y0))*(B-T);ctx.strokeStyle='#263747';ctx.strokeRect(x0+.5,y0+.5,cw-1,ch-1);ctx.strokeStyle=colorFor(i,series.length,.95);ctx.lineWidth=1.1;ctx.beginPath();s.pts.forEach((v,j)=>j?ctx.lineTo(sx(v[0]),sy(v[1])):ctx.moveTo(sx(v[0]),sy(v[1])));ctx.stroke();ctx.fillStyle='#d9e4ec';ctx.font='11px Segoe UI';ctx.fillText(curveLabel(s.d).slice(0,30),L,y0+14)});compare.lastRender=[]}
function metricValue(pts,mode){if(!pts.length)return null;const ys=pts.map(v=>v[1]);if(mode==='min')return Math.min(...ys);if(mode==='final')return ys[ys.length-1];if(mode==='mean')return ys.reduce((a,b)=>a+b,0)/ys.length;if(mode==='area'){let a=0;for(let i=1;i<pts.length;i++)a+=(pts[i][0]-pts[i-1][0])*(pts[i][1]+pts[i-1][1])/2;return a}if(mode==='xmax'){let k=0;for(let i=1;i<ys.length;i++)if(ys[i]>ys[k])k=i;return pts[k][0]}return Math.max(...ys)}
function drawMetric(ctx,w,h,series,view){const rows=series.map((s,i)=>({s,x:s.d.point??s.d.line??i+1,y:metricValue(s.pts,compare.metric)})).filter(r=>num(r.y)).sort((a,b)=>a.x-b.x);if(!rows.length){emptyPlot(ctx,'No numeric metric could be extracted.');return}const fake=[{pts:rows.map(r=>[r.x,r.y]),offset:0}],b=fitBounds(fake),pad=axes(ctx,w,h,b,{x_column:'point / ordered dataset',y_column:`${compare.metric} of ${view?.y_column||'value'}`}),sx=v=>pad.L+(v-b.x0)/(b.x1-b.x0)*(w-pad.L-pad.R),sy=v=>pad.T+(1-(v-b.y0)/(b.y1-b.y0))*(h-pad.T-pad.B);ctx.strokeStyle='#5eb5ff';ctx.lineWidth=1.4;ctx.beginPath();rows.forEach((r,i)=>i?ctx.lineTo(sx(r.x),sy(r.y)):ctx.moveTo(sx(r.x),sy(r.y)));ctx.stroke();rows.forEach((r,i)=>{ctx.fillStyle=colorFor(i,rows.length,1);ctx.beginPath();ctx.arc(sx(r.x),sy(r.y),4,0,Math.PI*2);ctx.fill()});compare.lastRender=[]}
function drawSpatial(ctx,w,h,series,view){const points=[];series.forEach((s,si)=>{const yi=view?.spatial_y_index;const xi=view?.x_index,vi=view?.y_index;(s.d.preview?.rows||[]).forEach((r,ri)=>{const x=r[xi],y=r[yi],v=r[vi];if(num(x)&&num(y)&&num(v))points.push({x,y,v,s,si,ri})})});if(!points.length){emptyPlot(ctx,'This spatial view has no finite X, Y, and metric triplets.');return}let x0=Math.min(...points.map(p=>p.x)),x1=Math.max(...points.map(p=>p.x)),y0=Math.min(...points.map(p=>p.y)),y1=Math.max(...points.map(p=>p.y)),v0=Math.min(...points.map(p=>p.v)),v1=Math.max(...points.map(p=>p.v));if(x0===x1){x0-=.5;x1+=.5}if(y0===y1){y0-=.5;y1+=.5}if(v0===v1){v0-=.5;v1+=.5}const L=78,R=90,T=30,B=58,sx=v=>L+(v-x0)/(x1-x0)*(w-L-R),sy=v=>T+(1-(v-y0)/(y1-y0))*(h-T-B);ctx.strokeStyle='#60758b';ctx.strokeRect(L,T,w-L-R,h-T-B);points.forEach(p=>{const f=(p.v-v0)/(v1-v0),hue=240-240*f;ctx.fillStyle=`hsl(${hue},82%,58%)`;ctx.beginPath();ctx.arc(sx(p.x),sy(p.y),clamp(3+Math.sqrt(points.length<100?3:1),3,6),0,Math.PI*2);ctx.fill()});for(let i=0;i<=100;i++){const f=i/100;ctx.fillStyle=`hsl(${240-240*f},82%,58%)`;ctx.fillRect(w-R+25,T+(1-f)*(h-T-B),18,(h-T-B)/100+1)}ctx.fillStyle='#dbe8f1';ctx.font='12px Segoe UI';ctx.fillText(view?.x_column||'X position',w/2-40,h-13);ctx.save();ctx.translate(18,h/2+30);ctx.rotate(-Math.PI/2);ctx.fillText(view?.spatial_y_column||'Y position',0,0);ctx.restore();ctx.fillText(nice(v1),w-R+48,T+8);ctx.fillText(nice(v0),w-R+48,h-B);ctx.fillText(view?.y_column||'metric',w-R+18,h-B+22);compare.lastRender=[]}
function updatePlotText(series,view,mode){const techLabel=compare.tech===ALL_TECH?`Cross-technique${compare.family?' · '+compare.family:''}`:((M.technique_registry?.[compare.tech]?.label)||compare.tech||'Comparison');q('#plotTitle').textContent=`${techLabel} · ${view?.label||'No view'}`;q('#plotSub').textContent=`${series.length} selected datasets · ${compare.method||'all methods'} · ${mode}`;q('#plotStatus').innerHTML=`<span>${series.length} datasets rendered</span><span>${view?.x_column||'row'} → ${view?.y_column||'value'}</span><span>${compare.scaleMode} autoscale${compare.clippedCount?` · ${compare.clippedCount} outlier curve(s) clipped from scale`:''}</span><span>wheel zoom · ${compare.interaction==='box'?'drag box zoom':compare.interaction==='select'?'click curve to select':'drag pan'} · double-click fit</span>`}
function resetView(){compare.viewBounds=null;compare.box=null;renderComparison()}function fullFit(){compare.scaleMode='full';q('#scaleMode').value='full';resetView()}function robustFit(){compare.scaleMode='robust';q('#scaleMode').value='robust';resetView()}
function downloadPlot(){const a=document.createElement('a');a.download=`FaradaIQ_${compare.tech===ALL_TECH?'cross_technique':compare.tech}_${compare.view}_${effectiveMode(compare.currentView)}.png`;a.href=q('#compareCanvas').toDataURL('image/png');a.click()}
function canvasPoint(e){const c=q('#compareCanvas'),r=c.getBoundingClientRect();return{x:e.clientX-r.left,y:e.clientY-r.top,r}}function nearestCurve(e,limit=260){if(!compare.lastRender.length)return null;const p=canvasPoint(e);let best=null;compare.lastRender.forEach(s=>s.pts.forEach(v=>{const px=s.sx(v[0]),py=s.sy(v[1]+(s.offset||0)),dd=(px-p.x)**2+(py-p.y)**2;if(dd<limit&&(!best||dd<best.dd))best={dd,px,py,v,s}}));return best}function bindCanvas(){const c=q('#compareCanvas');c.addEventListener('wheel',e=>{e.preventDefault();if(!compare.lastRender.length)return;const p=canvasPoint(e),b=compare.viewBounds||compare.lastRender[0].b,z=e.deltaY<0?.82:1.22,cx=b.x0+(p.x/p.r.width)*(b.x1-b.x0),cy=b.y1-(p.y/p.r.height)*(b.y1-b.y0);compare.viewBounds={x0:cx+(b.x0-cx)*z,x1:cx+(b.x1-cx)*z,y0:cy+(b.y0-cy)*z,y1:cy+(b.y1-cy)*z};renderComparison()},{passive:false});c.addEventListener('pointerdown',e=>{if(!compare.lastRender.length)return;const p=canvasPoint(e);c.setPointerCapture(e.pointerId);if(compare.interaction==='box'){compare.box={x0:p.x,y0:p.y,x1:p.x,y1:p.y};renderComparison();return}compare.drag={x:e.clientX,y:e.clientY,b:{...(compare.viewBounds||compare.lastRender[0].b)},moved:false,mode:compare.interaction}});c.addEventListener('pointermove',e=>{hoverPlot(e);const p=canvasPoint(e);if(compare.box){compare.box.x1=p.x;compare.box.y1=p.y;renderComparison();return}if(!compare.drag||compare.drag.mode!=='pan')return;const dx=(e.clientX-compare.drag.x)/p.r.width,dy=(e.clientY-compare.drag.y)/p.r.height,b=compare.drag.b;if(Math.abs(dx)+Math.abs(dy)>.003)compare.drag.moved=true;compare.viewBounds={x0:b.x0-dx*(b.x1-b.x0),x1:b.x1-dx*(b.x1-b.x0),y0:b.y0+dy*(b.y1-b.y0),y1:b.y1+dy*(b.y1-b.y0)};renderComparison()});c.addEventListener('pointerup',e=>{if(compare.box&&compare.lastRender.length){const p=canvasPoint(e),box={...compare.box,x1:p.x,y1:p.y},s=compare.lastRender[0],pad=s.pad,b=s.b,x0=Math.min(box.x0,box.x1),x1=Math.max(box.x0,box.x1),y0=Math.min(box.y0,box.y1),y1=Math.max(box.y0,box.y1);compare.box=null;if(x1-x0>8&&y1-y0>8){const W=p.r.width-pad.L-pad.R,H=p.r.height-pad.T-pad.B,tx0=clamp((x0-pad.L)/W,0,1),tx1=clamp((x1-pad.L)/W,0,1),ty0=clamp((y0-pad.T)/H,0,1),ty1=clamp((y1-pad.T)/H,0,1);compare.viewBounds={x0:b.x0+tx0*(b.x1-b.x0),x1:b.x0+tx1*(b.x1-b.x0),y0:b.y1-ty1*(b.y1-b.y0),y1:b.y1-ty0*(b.y1-b.y0)}}renderComparison();return}const drag=compare.drag;compare.drag=null;if(compare.interaction==='select'||(drag&&!drag.moved&&e.shiftKey)){const hit=nearestCurve(e,400);if(hit){compare.highlighted=hit.s.d.id;if(e.shiftKey){compare.selected=new Set([hit.s.d.id]);compare.scaleMode='highlighted';q('#scaleMode').value='highlighted'}else compare.selected.add(hit.s.d.id);compare.viewBounds=null;renderCurveList();renderComparison()}}});c.addEventListener('pointerleave',()=>{compare.drag=null;compare.box=null;q('#plotTip').style.display='none'});c.ondblclick=resetView}
function hoverPlot(e){if(compare.drag||compare.box||!compare.lastRender.length)return;const best=nearestCurve(e,180),tip=q('#plotTip'),r=q('#compareCanvas').getBoundingClientRect();if(!best){tip.style.display='none';return}tip.style.display='block';tip.style.left=clamp(best.px+12,8,r.width-300)+'px';tip.style.top=clamp(best.py+12,8,r.height-100)+'px';tip.innerHTML=`<b>${esc(best.s.d.display_name)}</b><br>${esc(best.s.view?.x_column)}: ${nice(best.v[0])}<br>${esc(best.s.view?.y_column)}: ${nice(best.v[1])}<br><span class="muted">Click in Select mode to highlight · Shift-click to solo</span>`}

function openRecommended(t,v,metric='final'){compare.family='';compare.tech=t;compare.view=v;compare.method='';compare.search='';compare.mode='auto';compare.metric=metric;compare.viewBounds=null;compare.selected=new Set(comparable().filter(d=>d.technique_key===t&&findView(d,v)).map(d=>d.id));const vv=viewCatalog(comparable().filter(d=>d.technique_key===t)).find(x=>x.id===v);applyViewDefaults(vv);populateControls(false,false);showPage('compare');renderComparison()}
function niceUnit(v,u=''){return `${nice(v)}${u?' '+u:''}`}
function sparkSvg(values){if(!values?.length)return '';const w=360,h=70,p=7,xv=values.map(v=>Number(v.point)),yv=values.map(v=>Number(v.value)).filter(Number.isFinite);if(!yv.length)return '';let x0=Math.min(...xv),x1=Math.max(...xv),y0=Math.min(...yv),y1=Math.max(...yv);if(x0===x1){x0-=1;x1+=1}if(y0===y1){const q=Math.abs(y0)*.05||1;y0-=q;y1+=q}const pts=values.map(v=>`${p+(Number(v.point)-x0)/(x1-x0)*(w-2*p)},${h-p-(Number(v.value)-y0)/(y1-y0)*(h-2*p)}`).join(' ');return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline fill="none" stroke="#58adff" stroke-width="2.2" points="${pts}"/><line x1="${p}" y1="${h-p}" x2="${w-p}" y2="${h-p}" stroke="#2d4357"/></svg>`}
function renderOverview(){const D=M.discovery||{},rec=D.recommended_views||[],tr=D.trend_cards||[],tech=D.techniques||[],un=D.unusual_points||[];q('#overviewMetrics').innerHTML=`<div class="card metric"><b>${D.point_count||0}</b><span>points / locations</span></div><div class="card metric"><b>${tech.length}</b><span>measurement and analysis types</span></div><div class="card metric"><b>${D.method_count||0}</b><span>methods / groups</span></div><div class="card metric"><b>${D.analysis_count||0}</b><span>scientific analysis results</span></div>`;q('#quickStart').innerHTML=[['Compare points','Overlay measurements and inspect how response changes from point to point.',rec[0]],['Find trends','Extract comparable metrics across points and flag potentially unusual behavior.',tr[0]],['Browse analyses','Open DRT, fits, maps, kinetic results, and other derived outputs.',null],['View photos and figures','Browse point photos separately from generated analysis plots.',null]].map((x,i)=>`<article class="quick-card" data-quick="${i}"><div class="badge">${i+1}</div><h3>${x[0]}</h3><p class="muted">${x[1]}</p></article>`).join('');qa('[data-quick]').forEach(el=>el.onclick=()=>{const i=Number(el.dataset.quick);if(i===0&&rec[0])openRecommended(rec[0].technique_key,rec[0].view_id);else if(i===1)showPage('trends');else if(i===2){q('#dataKind').value='analysis';showPage('datasets');renderDatasetList()}else showPage('gallery')});q('#recommended').innerHTML=rec.slice(0,8).map(r=>`<article class="recommend-card"><div class="badge">${esc(r.family)}</div><h3>${esc(r.view_label)}</h3><p>${esc(r.technique_label)}</p><div class="matrix-stats"><b>${r.dataset_count}</b> datasets · <b>${r.unique_points}</b> points · ${r.methods.length} methods</div><p class="muted">${esc(r.description||'Compare compatible measurements using the scientifically appropriate axes.')}</p><button class="btn primary" data-rec="${esc(r.technique_key)}|${esc(r.view_id)}">Open comparison</button></article>`).join('')||'<div class="empty-state">No multi-dataset scientific comparison is available yet.</div>';qa('[data-rec]').forEach(b=>b.onclick=()=>{const [t,v]=b.dataset.rec.split('|');openRecommended(t,v)});q('#overviewTrends').innerHTML=tr.slice(0,4).map(t=>`<article class="trend-card"><div class="badge">${esc(t.technique_label)}</div><h3>${esc(t.metric_label)} across points</h3>${sparkSvg(t.values)}<div class="trend-summary">${esc(t.summary)}</div><p class="muted">Point ${esc(t.min_point)}: ${niceUnit(t.min_value,t.unit)} · Point ${esc(t.max_point)}: ${niceUnit(t.max_value,t.unit)}</p><button class="btn" data-trend-open="${esc(t.technique_key)}|${esc(t.view_id)}|${esc(t.metric)}">Inspect trend</button></article>`).join('')||'<div class="empty-state">Trend cards require at least three comparable points.</div>';qa('[data-trend-open]').forEach(b=>b.onclick=()=>{const [t,v,m]=b.dataset.trendOpen.split('|');openRecommended(t,v,m)});q('#unusualOverview').innerHTML=un.length?un.slice(0,5).map(u=>`<div class="insight warn"><b>Point ${esc(u.point)}</b> is relatively unusual for ${esc(u.metric_label.toLowerCase())} in ${esc(u.technique_label)}. Value: ${niceUnit(u.value,u.unit)}. <button class="btn small" data-unusual="${esc(u.technique_key)}|${esc(u.view_id)}|${esc(u.metric)}">Review</button></div>`).join(''):'<div class="note good"><b>No strong robust outliers were detected</b> in the trend metrics currently available. This is a screening result, not a scientific conclusion.</div>';qa('[data-unusual]').forEach(b=>b.onclick=()=>{const [t,v,m]=b.dataset.unusual.split('|');openRecommended(t,v,m)});q('#techniqueCoverage').innerHTML='<table class="coverage-table"><thead><tr><th>Measurement / analysis</th><th>Family</th><th>Datasets</th><th>Points</th><th>Methods</th><th></th></tr></thead><tbody>'+tech.map(t=>`<tr><td><b>${esc(t.technique_label)}</b></td><td>${esc(t.family)}</td><td>${t.dataset_count}</td><td>${t.point_count}</td><td>${t.method_count}</td><td><button class="btn small" data-tech-open="${esc(t.technique_key)}">Explore</button></td></tr>`).join('')+'</tbody></table>';qa('[data-tech-open]').forEach(b=>b.onclick=()=>{compare.family='';compare.tech=b.dataset.techOpen;compare.view='';populateControls(true,true);showPage('compare');renderComparison()})}
function renderTrends(){const rows=M.discovery?.trend_cards||[];q('#trendCount').textContent=`${rows.length} trend summaries`;q('#trendGrid').innerHTML=rows.map(t=>`<article class="trend-card"><div class="badge">${esc(t.family)} · ${esc(t.technique_label)}</div><h3>${esc(t.metric_label)} across points</h3><p class="muted">Based on ${t.point_count} points using ${esc(t.view_label)}.</p>${sparkSvg(t.values)}<div class="trend-summary">${esc(t.summary)}</div><div class="insight">Lowest at Point ${esc(t.min_point)}: ${niceUnit(t.min_value,t.unit)}<br>Highest at Point ${esc(t.max_point)}: ${niceUnit(t.max_value,t.unit)}</div><div class="actions"><button class="btn primary" data-trend-open="${esc(t.technique_key)}|${esc(t.view_id)}|${esc(t.metric)}">Open full comparison</button><button class="btn" data-trend-data="${esc(t.dataset_ids?.[0]||'')}">Open source data</button></div></article>`).join('')||'<div class="empty-state">No trend has at least three comparable point values. Use Compare to inspect the available curves directly.</div>';qa('[data-trend-open]').forEach(b=>b.onclick=()=>{const [t,v,m]=b.dataset.trendOpen.split('|');openRecommended(t,v,m)});qa('[data-trend-data]').forEach(b=>b.onclick=()=>b.dataset.trendData&&openDataset(b.dataset.trendData));q('#trendUnusual').innerHTML=(M.discovery?.unusual_points||[]).map(u=>`<div class="insight warn"><b>Point ${esc(u.point)}</b> · ${esc(u.technique_label)} · ${esc(u.metric_label)} = ${niceUnit(u.value,u.unit)} · robust deviation ${nice(u.robust_z)}. This is a screening flag, not a conclusion.</div>`).join('')||'<div class="note good">No strong robust outliers were identified in the available trend metrics.</div>'}
function renderDownloads(){const p=M.primary_downloads||{},items=[];if(p.canonical_h5)items.push({title:'Canonical H5 bundle',desc:'Complete numeric bundle for FaradaIQ and programmatic analysis.',href:p.canonical_h5});if(p.canonical_json)items.push({title:'Canonical JSON manifest',desc:'Experiment structure, metadata, and provenance.',href:p.canonical_json});items.push({title:'Portable browser manifest',desc:'All browser records, scientific identities, aliases, and preview metadata.',href:M.manifest_file});q('#primaryDownloads').innerHTML=items.map(x=>`<article class="download-card"><h3>${esc(x.title)}</h3><p class="muted">${esc(x.desc)}</p><a class="btn primary" href="${href(x.href)}" download>Download</a></article>`).join('');q('#batchDownloads').innerHTML=(M.batches||[]).map(b=>`<article class="download-card"><div class="badge">${esc(b.technique_key)}</div><h3>${esc(b.label)}</h3><p class="muted">${b.dataset_count} datasets · ${fmt(b.size_bytes)}</p><a class="btn" href="${b.zip_href||href(b.zip_path)}" download>Download batch ZIP</a></article>`).join('')||'<div class="empty-state">No batch ZIPs were generated.</div>'}

function experimentNames(){return [...new Set((M.datasets||[]).map(d=>d.experiment_name||M.experiment_name).filter(Boolean))]}
function technicalHeuristic(d){const p=`${d.physical_path||''} ${d.original_scientific_path||''} ${d.role||''}`.toLowerCase();return d.technique_key==='SUMMARY'&&/(semantic_section|heka_hierarchy|heka_metadata|raw_section|raw_trace|parser_warning|source_and_check|provenance|record_offset)/.test(p)}
async function importExperimentFiles(files){let added=0,experiments=[];for(const file of files){try{const text=await file.text(),payload=JSON.parse(text);if(!Array.isArray(payload.datasets))throw new Error('No datasets array');const exp=payload.experiment_name||file.name.replace(/\.json$/i,'');const prefix=`imp${Date.now()}_${Math.random().toString(16).slice(2,8)}_`;payload.datasets.forEach((raw,i)=>{const d={...raw};d.id=prefix+(raw.id||i);d.experiment_name=exp;d.imported_manifest=true;d.physical_href='';d.batch_zip_href='';if(d.ui_hidden_default==null)d.ui_hidden_default=technicalHeuristic(d);if(!d.ui_role)d.ui_role=d.ui_hidden_default?'technical':(d.data_kind==='analysis'?'analysis':d.data_kind==='media'?'media':'measurement');M.datasets.push(d);added++});experiments.push(exp)}catch(err){console.warn('FaradaIQ manifest import failed',file.name,err)}}q('#importStatus').textContent=added?`Loaded ${added} datasets from ${experiments.join(', ')} for in-browser comparison. Imported previews are comparison-only; their source-file download links are not relocated.`:'No compatible FaradaIQ manifest was loaded.';compare.selected=new Set(comparable().map(d=>d.id));populateControls(true,true);renderOverview();renderComparison();renderDatasetList();q('#stats').innerHTML=`<span class="pill">${experimentNames().length} experiments</span><span class="pill">${comparable().length} comparable datasets</span><span class="pill">${M.discovery?.analysis_count||0} local analyses</span><span class="pill">offline session</span>`}
function globalSearch(term){term=String(term||'').trim();if(!term)return;showPage('datasets');q('#dataSearch').value=term;renderDatasetList()}
function renderMatrix(){const term=q('#matrixSearch').value.trim().toLowerCase(),showTechnical=q('#showTechnicalViews')?.checked;const source=showTechnical?(M.comparison?.advanced_matrix||[]):(M.comparison?.matrix||[]);const rows=source.filter(r=>!term||(`${r.family} ${r.technique_label} ${r.view_label} ${r.description}`).toLowerCase().includes(term));q('#matrixCount').textContent=`${rows.length} ${showTechnical?'all detected':'scientific'} comparison views`;q('#matrixGrid').innerHTML=rows.map(r=>`<article class="matrix-card ${r.score<35?'hidden-technical':''}"><div class="badge">${esc(r.family)}</div><h3>${esc(r.view_label)}</h3><p>${esc(r.technique_label)}</p><div class="matrix-stats"><b>${r.dataset_count}</b> datasets · <b>${r.unique_points}</b> points · ${r.methods.length} methods</div><small>${esc(r.x_column)} → ${esc(r.y_column)} · ${esc(r.plot_type)}</small><p class="muted">${esc(r.description||((r.score||0)>=35?'Scientifically relevant comparison detected from the data columns.':'Technical/internal column view.'))}</p><button class="btn primary" data-open-view="${esc(r.technique_key)}|${esc(r.view_id)}">Open comparison</button></article>`).join('')||'<div class="empty-state">No comparison view matches the search.</div>';qa('[data-open-view]').forEach(b=>b.onclick=()=>{const [t,v]=b.dataset.openView.split('|');openRecommended(t,v)})}
function renderGallery(){const term=q('#gallerySearch').value.trim().toLowerCase(),kind=q('#galleryKind').value,lo=Number(q('#galleryMin').value||-Infinity),hi=Number(q('#galleryMax').value||Infinity);const rows=(M.media||[]).filter(m=>(!term||(`${m.display_name} ${m.path}`).toLowerCase().includes(term))&&(!kind||m.category===kind)&&(m.point==null||m.point>=lo)&&(m.point==null||m.point<=hi));q('#galleryCount').textContent=`${rows.length} media files`;q('#gallery').innerHTML=rows.map(m=>`<div class="media-card"><div class="media-frame" data-lightbox="${m.id}">${m.preview_available?`<img loading="lazy" src="${m.preview_href}" alt="${esc(m.display_name)}" onerror="mediaError(this)">`:`<div class="empty">${esc(m.preview_error||'Browser preview unavailable; file remains downloadable.')}</div>`}</div><div class="media-body"><div class="media-title">${esc(m.display_name||m.name)}</div><div class="media-meta">${m.point!=null?'Point '+String(m.point).padStart(3,'0')+' · ':m.frame_index!=null?'Frame '+String(m.frame_index).padStart(3,'0')+' · ':''}${esc(m.media_role||m.category)} · ${fmt(m.size_bytes)}</div><div class="actions">${m.preview_available?`<a class="btn small" href="${m.preview_href}" target="_blank">Open preview</a>`:''}<a class="btn small" href="${m.download_href||m.source_href||m.href||href(m.path)}" download>Download source</a>${m.dataset_id?`<button class="btn small" data-dataset="${m.dataset_id}">Dataset</button>`:''}</div></div></div>`).join('')||'<div class="empty">No media match the current filters.</div>';qa('[data-lightbox]').forEach(el=>el.onclick=()=>openLightbox(el.dataset.lightbox));qa('[data-dataset]').forEach(el=>el.onclick=()=>openDataset(el.dataset.dataset))}
function openLightbox(id){const m=(M.media||[]).find(x=>x.id===id);if(!m||!m.preview_available)return;q('#lightboxImage').src=m.preview_href;q('#lightboxTitle').textContent=m.display_name||m.name;q('#lightbox').classList.add('open')}
function closeLightbox(){q('#lightbox').classList.remove('open');q('#lightboxImage').src=''}
function renderDatasetList(){const term=q('#dataSearch').value.trim().toLowerCase(),tech=q('#dataTech').value,kind=q('#dataKind').value,showTechnical=q('#showTechnicalData')?.checked;const groups={};(M.datasets||[]).forEach(d=>{if(!showTechnical&&d.ui_hidden_default)return;const hay=(`${d.display_name} ${d.original_scientific_path} ${d.physical_path} ${d.role} ${d.family}`).toLowerCase();if(term&&!hay.includes(term))return;if(tech&&d.technique_key!==tech)return;if(kind&&d.data_kind!==kind)return;(groups[`${d.ui_role==='analysis'?'Analysis results':d.ui_role==='measurement'?'Measurements':d.ui_role==='media'?'Media':'Supporting data'} · ${d.technique_label||d.technique_key}`]??=[]).push(d)});q('#datasetList').innerHTML=Object.entries(groups).sort().map(([name,rows],gi)=>`<details class="section" ${gi<3?'open':''}><summary>${esc(name)} <span class="muted">(${rows.length})</span></summary>${rows.map(d=>`<div class="dataset-row ${current?.id===d.id?'selected':''}" data-select-dataset="${d.id}"><b>${esc(d.display_name)}</b><div class="sub">${esc(d.ui_alternate_copy?'alternate copy · '+d.data_kind:d.data_kind)} · ${(d.plot_views||[]).length} views · ${fmt(d.size_bytes)} · ${esc(d.preview?.status||'unavailable')}</div></div>`).join('')}</details>`).join('')||'<div class="empty">No matching datasets.</div>';qa('[data-select-dataset]').forEach(el=>el.onclick=()=>selectDataset(el.dataset.selectDataset))}
function metaRow(k,v){if(v===null||v===undefined||v==='')return'';return`<div>${esc(k)}</div><div class="path">${esc(Array.isArray(v)?v.join(', '):v)}</div>`}
function selectDataset(id){current=(M.datasets||[]).find(x=>x.id===id);renderDatasetList();if(!current)return;const d=current,p=d.preview||{},views=d.plot_views||[];if(!views.some(v=>v.id===detailViewId))detailViewId=views[0]?.id||'';const acts=[`<a class="btn primary" href="${d.physical_href||href(d.physical_path)}" download>Download data file</a>`,`<a class="btn" href="${d.physical_href||href(d.physical_path)}" target="_blank">Open file</a>`];if(d.batch_zip_href)acts.push(`<a class="btn" href="${d.batch_zip_href}" download>Download batch ZIP</a>`);let preview='';if(p.kind==='image'){preview=p.browser_renderable?`<div class="image-detail"><img src="${p.image_href||href(p.image_path)}" alt="${esc(d.display_name)}"></div>`:`<div class="note">This media format may not render directly. Open or download the original file.</div>`}else if(p.rows?.length){preview=`${views.length?`<div class="detail-view"><label>Scientific view <select id="detailView">${views.map(v=>`<option value="${esc(v.id)}" ${v.id===detailViewId?'selected':''}>${esc(v.label)}</option>`).join('')}</select></label></div><div class="detail-canvas"><canvas id="detailCanvas"></canvas></div>`:''}<div id="previewTable" class="tablewrap"></div>`}else if(p.columns?.length){preview=`<div class="note"><b>Table structure detected.</b> ${esc(p.error||'This table currently has no data rows.')}<br><span class="muted">Columns: ${esc(p.columns.join(', '))}</span></div>`}else{preview=`<div class="note"><b>Preview status:</b> ${esc(p.status||'unavailable')}. ${esc(p.error||'No readable numeric or tabular content was embedded.')} The original file remains available above.</div>`}q('#datasetDetail').innerHTML=`<h2>${esc(d.display_name)}</h2><div class="actions">${acts.join('')}</div><div class="meta">${metaRow('Experiment',d.experiment_name||M.experiment_name)}${metaRow('Family',d.family)}${metaRow('Technique / analysis',d.technique_label)}${metaRow('Data kind',d.data_kind)}${metaRow('Method',d.method_label)}${metaRow('Point',d.point)}${metaRow('Cycle',d.cycle)}${metaRow('Sweep',d.sweep)}${metaRow('Role',d.role)}${metaRow('Available views',views.map(v=>v.label))}${metaRow('Actual columns',d.columns)}${metaRow('Rows in source',d.n_rows)}${metaRow('Rows embedded',p.sampled_rows)}${metaRow('Original scientific path',d.original_scientific_path)}${metaRow('Physical relative path',d.physical_path)}${metaRow('Canonical key',d.canonical_key)}</div><h3>Preview</h3>${preview}`;if(p.kind==='table'&&p.rows?.length){if(views.length){q('#detailView').onchange=e=>{detailViewId=e.target.value;drawDetail(d)};drawDetail(d)}renderPreviewTable(p)}}
function openDataset(id){showPage('datasets');selectDataset(id)}
function drawDetail(d){const c=q('#detailCanvas');if(!c)return;const view=(d.plot_views||[]).find(v=>v.id===detailViewId)||(d.plot_views||[])[0];const old={...compare};compare.logX=view?.x_scale==='log';compare.logY=view?.y_scale==='log';compare.norm='none';compare.invertY=false;const pts=viewPoints(d,view);const {x:ctx,w,h}=prepCanvas(c,400);ctx.fillStyle='#05080c';ctx.fillRect(0,0,w,h);if(pts.length<1){emptyPlot(ctx,'This view has no finite values.');Object.assign(compare,old);return}if(view?.plot_type==='spatial'){drawSpatial(ctx,w,h,[{d,view,pts}],view);Object.assign(compare,old);return}const series=[{d,view,pts,offset:0}],b=fitBounds(series),pad=axes(ctx,w,h,b,view),sx=v=>pad.L+(v-b.x0)/(b.x1-b.x0)*(w-pad.L-pad.R),sy=v=>pad.T+(1-(v-b.y0)/(b.y1-b.y0))*(h-pad.T-pad.B);ctx.strokeStyle='#58adff';ctx.lineWidth=1.7;ctx.beginPath();pts.forEach((v,i)=>i?ctx.lineTo(sx(v[0]),sy(v[1])):ctx.moveTo(sx(v[0]),sy(v[1])));ctx.stroke();Object.assign(compare,old)}
function renderPreviewTable(p){const box=q('#previewTable');if(!box)return;const cols=p.columns||[];box.innerHTML='<table><thead><tr>'+cols.map(c=>`<th>${esc(c)}</th>`).join('')+'</tr></thead><tbody>'+(p.rows||[]).slice(0,160).map(r=>'<tr>'+cols.map((_,i)=>`<td>${esc(num(r[i])?nice(r[i]):r[i]??'')}</td>`).join('')+'</tr>').join('')+'</tbody></table>'}
function renderFiles(){const term=q('#fileSearch').value.trim().toLowerCase(),cat=q('#fileCategory').value;const groups={};(M.files||[]).forEach(f=>{if(term&&!(`${f.path} ${f.name}`).toLowerCase().includes(term))return;if(cat&&f.category!==cat)return;(groups[f.category]??=[]).push(f)});q('#fileGroups').innerHTML=Object.entries(groups).sort().map(([g,rows])=>`<details class="section" open><summary>${esc(g)} <span class="muted">(${rows.length})</span></summary>${rows.map(f=>`<div class="file"><a href="${f.href||href(f.path)}" target="_blank">${esc(f.path)}</a><span>${fmt(f.size_bytes)}</span><span class="actions"><a class="btn small" href="${f.href||href(f.path)}" target="_blank">Open</a><a class="btn small" href="${f.href||href(f.path)}" download>Download</a></span></div>`).join('')}</details>`).join('')||'<div class="empty">No files match the filters.</div>'}
function setInteraction(mode){compare.interaction=mode;['pan','box','select'].forEach(m=>q('#'+(m==='pan'?'panMode':m==='box'?'boxMode':'pickMode')).classList.toggle('active',m===mode));q('#compareCanvas').style.cursor=mode==='pan'?'grab':mode==='box'?'crosshair':'pointer'}
function bindControls(){q('#cmpFamily').onchange=e=>{compare.family=e.target.value;compare.tech=ALL_TECH;compare.view='';compare.method='';compare.viewBounds=null;populateControls(true,true);renderComparison()};q('#cmpTech').onchange=e=>{compare.tech=e.target.value;compare.view='';compare.method='';compare.viewBounds=null;populateControls(true,true);renderComparison()};q('#cmpView').onchange=e=>{compare.view=e.target.value;const v=viewCatalog().find(x=>x.id===compare.view);applyViewDefaults(v);populateControls(true,false);renderComparison()};q('#cmpMethod').onchange=e=>{compare.method=e.target.value;compare.viewBounds=null;renderCurveList();renderComparison()};q('#cmpSearch').oninput=e=>{compare.search=e.target.value;renderCurveList();renderComparison()};q('#cmpMode').onchange=e=>{compare.mode=e.target.value;q('#metricField').style.display=compare.mode==='metric'?'block':'none';compare.viewBounds=null;renderComparison()};q('#norm').onchange=e=>{compare.norm=e.target.value;compare.viewBounds=null;renderComparison()};q('#metricMode').onchange=e=>{compare.metric=e.target.value;renderComparison()};['pointMin','pointMax'].forEach(id=>q('#'+id).onchange=e=>{compare[id]=e.target.value===''?null:Number(e.target.value);renderCurveList();renderComparison()});q('#opacity').oninput=e=>{compare.opacity=Number(e.target.value);q('#opacityVal').textContent=compare.opacity.toFixed(2);renderComparison()};q('#lineWidth').oninput=e=>{compare.width=Number(e.target.value);q('#widthVal').textContent=compare.width.toFixed(1);renderComparison()};q('#offset').oninput=e=>{compare.offset=Number(e.target.value);q('#offsetVal').textContent=compare.offset.toFixed(2);renderComparison()};q('#logX').onchange=e=>{compare.logX=e.target.checked;compare.viewBounds=null;renderComparison()};q('#logY').onchange=e=>{compare.logY=e.target.checked;compare.viewBounds=null;renderComparison()};q('#invertY').onchange=e=>{compare.invertY=e.target.checked;compare.viewBounds=null;renderComparison()};q('#showLegend').onchange=e=>{compare.legend=e.target.checked;renderComparison()};q('#includeAlternates').onchange=e=>{compare.includeAlternates=e.target.checked;compare.selected=new Set(comparable().map(d=>d.id));populateControls(true,true);renderCurveList();renderComparison()};q('#selectAll').onclick=()=>{filteredCandidates(false).forEach(d=>compare.selected.add(d.id));renderCurveList();renderComparison()};q('#selectNone').onclick=()=>{filteredCandidates(false).forEach(d=>compare.selected.delete(d.id));renderCurveList();renderComparison()};q('#invertSelect').onclick=()=>{filteredCandidates(false).forEach(d=>compare.selected.has(d.id)?compare.selected.delete(d.id):compare.selected.add(d.id));renderCurveList();renderComparison()};q('#resetView').onclick=resetView;q('#downloadPlot').onclick=downloadPlot;q('#scaleMode').onchange=e=>{compare.scaleMode=e.target.value;compare.viewBounds=null;renderComparison()};q('#panMode').onclick=()=>setInteraction('pan');q('#boxMode').onclick=()=>setInteraction('box');q('#pickMode').onclick=()=>setInteraction('select');q('#robustFit').onclick=robustFit;q('#fullFit').onclick=fullFit}
function init(){q('#title').textContent=M.experiment_name;const cov=M.coverage||{},D=M.discovery||{};q('#stats').innerHTML=`<span class="pill">${D.point_count||0} points</span><span class="pill">${D.measurement_count||0} measurements</span><span class="pill">${D.analysis_count||0} analyses</span><span class="pill">${M.media_count||0} photos / figures</span><span class="pill">offline and portable</span>`;qa('.nav button').forEach(b=>b.onclick=()=>showPage(b.dataset.page));chooseDefaults();populateControls(true,false);const techOptions=[...new Set((M.datasets||[]).filter(d=>!d.ui_hidden_default).map(d=>d.technique_key))].sort();q('#dataTech').innerHTML='<option value="">All measurements and analyses</option>'+techOptions.map(t=>`<option value="${esc(t)}">${esc((M.technique_registry?.[t]?.label)||t)}</option>`).join('');const kinds=[...new Set((M.datasets||[]).filter(d=>!d.ui_hidden_default).map(d=>d.data_kind))].sort();q('#dataKind').innerHTML='<option value="">All user-facing data</option>'+kinds.map(t=>`<option>${esc(t)}</option>`).join('');const cats=[...new Set((M.files||[]).map(f=>f.category))].sort();q('#fileCategory').innerHTML='<option value="">All file categories</option>'+cats.map(t=>`<option>${esc(t)}</option>`).join('');const mediaCats=[...new Set((M.media||[]).map(f=>f.category))].sort();q('#galleryKind').innerHTML='<option value="">All media</option>'+mediaCats.map(t=>`<option>${esc(t)}</option>`).join('');const mediaPts=(M.media||[]).map(m=>m.point).filter(v=>v!=null);if(mediaPts.length){q('#galleryMin').value=Math.min(...mediaPts);q('#galleryMax').value=Math.max(...mediaPts)}const p=M.primary_downloads||{},top=[];top.push(`<button class="btn primary" id="topSearchButton">Find data</button>`);top.push(`<button class="btn" id="topDownloadButton">Downloads</button>`);q('#primary').innerHTML=top.join('');q('#topSearchButton').onclick=()=>q('#globalSearch').focus();q('#topDownloadButton').onclick=()=>showPage('downloads');q('#shortNote').innerHTML=M.short_names.enabled?`<div class="note good"><b>Short physical names enabled.</b> Original scientific identities and all alias mappings remain in the HTML and JSON manifests.</div>`:`<div class="note good"><b>Scientific names preserved.</b> All links are relative and portable.</div>`;bindControls();bindCanvas();q('#scaleMode').value=compare.scaleMode;setInteraction(compare.interaction);q('#matrixSearch').oninput=renderMatrix;q('#showTechnicalViews').onchange=renderMatrix;q('#showTechnicalData').onchange=renderDatasetList;['gallerySearch','galleryKind','galleryMin','galleryMax'].forEach(id=>q('#'+id).addEventListener('input',renderGallery));['dataSearch','dataTech','dataKind'].forEach(id=>q('#'+id).addEventListener('input',renderDatasetList));['fileSearch','fileCategory'].forEach(id=>q('#'+id).addEventListener('input',renderFiles));q('#globalSearchButton').onclick=()=>globalSearch(q('#globalSearch').value);q('#globalSearch').addEventListener('keydown',e=>{if(e.key==='Enter')globalSearch(e.target.value)});q('#importExperimentButton').onclick=()=>q('#importManifests').click();q('#importManifests').onchange=e=>importExperimentFiles([...e.target.files]);q('#lightbox').onclick=e=>{if(e.target.id==='lightbox'||e.target.classList.contains('close'))closeLightbox()};document.addEventListener('keydown',e=>{if(e.key==='Escape')closeLightbox()});renderOverview();renderComparison();renderTrends();renderMatrix();renderGallery();renderDatasetList();renderDownloads();renderFiles();const firstVisible=(M.datasets||[]).find(d=>!d.ui_hidden_default);if(firstVisible)selectDataset(firstVisible.id);showPage('overview');window.addEventListener('resize',()=>{if(q('#page-compare').classList.contains('active'))renderComparison()})}

document.addEventListener('DOMContentLoaded',init);
"""


def _render_html(manifest: Mapping[str, Any]) -> str:
    payload = json.dumps(_json_safe(manifest), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    title = html.escape(str(manifest.get("experiment_name") or "FaradaIQ Data Browser"))
    comp = manifest.get("comparison", {}); cov = manifest.get("coverage", {})
    return f"""<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{title} — FaradaIQ Data Browser</title><style>{_CSS}</style></head><body>
<header class=\"top\"><div class=\"brandline\"><div class=\"brand\"><h1>FaradaIQ Experiment Explorer</h1><span id=\"title\" class=\"muted\"></span></div><div id=\"primary\" class=\"actions\"></div></div><div id=\"stats\" class=\"stats\"></div><nav class=\"nav\"><button class=\"active\" data-page=\"overview\">Discover</button><button data-page=\"compare\">Compare</button><button data-page=\"trends\">Trends</button><button data-page=\"datasets\">Data and analyses</button><button data-page=\"gallery\">Photos and figures</button><button data-page=\"downloads\">Downloads</button><button data-page=\"matrix\">More comparisons</button><button data-page=\"files\">All files</button><button data-page=\"about\">About</button></nav></header>
<section id=\"page-overview\" class=\"page active\"><div class=\"hero\"><div class=\"hero-main\"><div class=\"badge\">Portable scientific workspace</div><h2>Discover trends, compare conditions, and find the data that matters</h2><p>Start with a scientific question instead of navigating folders. FaradaIQ prioritizes measurements and analysis results, while parser and provenance tables remain available only when needed.</p><div class=\"global-search\"><input id=\"globalSearch\" type=\"search\" placeholder=\"Search point, technique, method, analysis, filename, or result…\"><button id=\"globalSearchButton\" class=\"btn primary\">Find data</button></div><div class=\"actions\" style=\"margin-top:10px\"><button id=\"importExperimentButton\" class=\"btn\">Add another experiment manifest</button><span id=\"importStatus\" class=\"muted\">Compare this experiment with another portable FaradaIQ manifest in the same browser session.</span><input id=\"importManifests\" type=\"file\" accept=\".json,application/json\" multiple hidden></div></div><div id=\"overviewMetrics\" class=\"dashboard\"></div></div><div class=\"section-head\"><div><h2>Start here</h2><p>Choose what you are trying to learn from the experiment.</p></div></div><div id=\"quickStart\" class=\"quick-grid\"></div><div class=\"section-head\"><div><h2>Recommended comparisons</h2><p>High-value comparisons ranked from the actual scientific columns and available point coverage.</p></div><button class=\"btn\" onclick=\"showPage('matrix')\">See more</button></div><div id=\"recommended\" class=\"recommend-grid\"></div><div class=\"section-head\"><div><h2>Trend snapshots</h2><p>Compact point-wise summaries. Open any card to inspect the full curves.</p></div><button class=\"btn\" onclick=\"showPage('trends')\">Open Trend Finder</button></div><div id=\"overviewTrends\" class=\"trend-grid\"></div><div class=\"section-head\"><div><h2>Potentially unusual points</h2><p>Robust screening flags for review. They are not scientific conclusions.</p></div></div><div id=\"unusualOverview\"></div><div class=\"section-head\"><div><h2>Experiment coverage</h2><p>What is available by measurement and analysis type.</p></div></div><div class=\"tablewrap\"><div id=\"techniqueCoverage\"></div></div></section>
<section id=\"page-compare\" class=\"page\"><div class=\"section-head\"><div><h2>Compare measurements and analyses</h2><p>Select a technique and scientific view. Advanced scaling and interaction controls are available only when needed.</p></div></div><div class=\"compare-intent\"><button onclick=\"compare.mode='overlay';renderComparison()\">Compare curves</button><button onclick=\"compare.mode='metric';renderComparison()\">Compare metrics by point</button><button onclick=\"compare.mode='waterfall';renderComparison()\">See ordered progression</button><button onclick=\"compare.mode='small';renderComparison()\">Inspect small multiples</button></div><div class=\"dashboard\"><div class=\"card metric\"><b>{comp.get('available_view_count',0)}</b><span>scientific comparison views</span></div><div class=\"card metric\"><b>{cov.get('analysis',0)}</b><span>derived analysis datasets</span></div><div class=\"card metric\"><b>{manifest.get('media_count',0)}</b><span>photos and figures</span></div><div class=\"card metric\"><b>{manifest.get('dataset_count',0)}</b><span>total indexed datasets</span></div></div><div class=\"compare-layout\"><aside class=\"control-panel\"><h3>Technique-aware comparison</h3><div class=\"field\"><label>Family</label><select id=\"cmpFamily\"></select></div><div class=\"field\"><label>Technique or analysis</label><select id=\"cmpTech\"></select></div><div class=\"field\"><label>Scientific view</label><select id=\"cmpView\"></select></div><div id=\"viewDescription\" class=\"view-note\"></div><div class=\"field\"><label>Method / group</label><select id=\"cmpMethod\"></select></div><div class=\"field\"><label>Search within compatible datasets</label><input id=\"cmpSearch\" type=\"search\" placeholder=\"point, method, fit, path…\"></div><div class=\"field inline\"><div><label>Point from</label><input id=\"pointMin\" type=\"number\"></div><div><label>Point to</label><input id=\"pointMax\" type=\"number\"></div></div><div class=\"field\"><label>Plot mode</label><select id=\"cmpMode\"><option value=\"auto\">Automatic for selected view</option><option value=\"overlay\">Overlay all compatible curves</option><option value=\"waterfall\">Waterfall / stacked</option><option value=\"small\">Small multiples</option><option value=\"metric\">Extracted metric vs point / file</option><option value=\"spatial\">Spatial map</option></select></div><div id=\"metricField\" class=\"field\" style=\"display:none\"><label>Extracted metric</label><select id=\"metricMode\"><option value=\"max\">Maximum</option><option value=\"min\">Minimum</option><option value=\"final\">Final value</option><option value=\"mean\">Mean</option><option value=\"area\">Signed area</option><option value=\"xmax\">X at maximum</option></select></div><div class=\"field\"><label>Normalization</label><select id=\"norm\"><option value=\"none\">None</option><option value=\"maxabs\">Maximum absolute</option><option value=\"minmax\">0–1 min/max</option><option value=\"baseline\">Subtract first value</option><option value=\"area\">Normalize by absolute area</option></select></div><div class=\"checks\"><label><input id=\"logX\" type=\"checkbox\"> Log X</label><label><input id=\"logY\" type=\"checkbox\"> Log Y</label><label><input id=\"invertY\" type=\"checkbox\"> Invert Y</label><label><input id=\"showLegend\" type=\"checkbox\" checked> Legend</label><label><input id=\"includeAlternates\" type=\"checkbox\"> Alternate copies</label></div><div class=\"field\"><label>Opacity</label><div class=\"range-line\"><input id=\"opacity\" type=\"range\" min=\"0.05\" max=\"1\" step=\"0.01\" value=\"0.55\"><span id=\"opacityVal\">0.55</span></div></div><div class=\"field\"><label>Line width</label><div class=\"range-line\"><input id=\"lineWidth\" type=\"range\" min=\"0.4\" max=\"4\" step=\"0.1\" value=\"1.2\"><span id=\"widthVal\">1.2</span></div></div><div class=\"field\"><label>Waterfall spacing</label><div class=\"range-line\"><input id=\"offset\" type=\"range\" min=\"0\" max=\"1\" step=\"0.01\" value=\"0.12\"><span id=\"offsetVal\">0.12</span></div></div><div class=\"field\"><label>Autoscale</label><select id=\"scaleMode\"><option value=\"robust\">Robust selected curves</option><option value=\"full\">Full selected curves</option><option value=\"highlighted\">Highlighted curve</option></select></div><div class=\"field\"><label>Mouse interaction</label><div class=\"interaction-row\"><button id=\"panMode\" class=\"btn active\">Pan</button><button id=\"boxMode\" class=\"btn\">Box zoom</button><button id=\"pickMode\" class=\"btn\">Select curve</button></div><div class=\"fit-row\"><button id=\"robustFit\" class=\"btn\">Robust fit</button><button id=\"fullFit\" class=\"btn\">Full fit</button></div></div><div class=\"actions\"><button id=\"resetView\" class=\"btn\">Reset current zoom</button><button id=\"downloadPlot\" class=\"btn primary\">Save plot PNG</button></div></aside><div class=\"plot-column\"><div class=\"card plot-card\"><div class=\"plot-head\"><div><div id=\"plotTitle\" class=\"plot-title\"></div><div id=\"plotSub\" class=\"plot-sub\"></div></div></div><div class=\"canvas-wrap\"><canvas id=\"compareCanvas\"></canvas><div id=\"plotTip\" class=\"plot-tip\"></div></div><div id=\"plotStatus\" class=\"plot-status\"></div></div><div class=\"card curve-panel\"><div class=\"curve-actions\"><b id=\"curveCount\"></b><button id=\"selectAll\" class=\"btn small\">Select all compatible</button><button id=\"selectNone\" class=\"btn small\">Select none</button><button id=\"invertSelect\" class=\"btn small\">Invert selection</button></div><div id=\"curveList\" class=\"curve-list\"></div></div></div></div></section>
<section id=\"page-trends\" class=\"page\"><div class=\"section-head\"><div><h2>Trend Finder</h2><p>Point-wise metrics extracted from compatible scientific curves.</p></div><b id=\"trendCount\"></b></div><div id=\"trendGrid\" class=\"trend-grid\"></div><div class=\"section-head\"><div><h2>Potentially unusual points</h2><p>Robust median-deviation screening for review.</p></div></div><div id=\"trendUnusual\"></div></section>
<section id=\"page-downloads\" class=\"page\"><div class=\"section-head\"><div><h2>Download and share</h2><p>Use compact bundles instead of opening deep folder trees.</p></div></div><div class=\"downloads-note\"><b>Portable sharing:</b> copy the complete experiment folder. The HTML and all links remain relative. Batch ZIPs contain compact filenames plus manifests preserving the original scientific identity.</div><div id=\"primaryDownloads\" class=\"download-grid\"></div><div class=\"section-head\"><div><h2>Technique, method, and point batches</h2><p>Download only the subset needed for another analysis or collaborator.</p></div></div><div id=\"batchDownloads\" class=\"download-grid\"></div></section>
<section id=\"page-matrix\" class=\"page\"><div class=\"section-head\"><div><h2>More scientific comparisons</h2><p>Recommended views are shown first. Technical parser-column views are hidden by default.</p></div></div><div class=\"matrix-toolbar\"><input id=\"matrixSearch\" type=\"search\" placeholder=\"Search Nyquist, DRT, Tafel, charge, map…\"><label><input id=\"showTechnicalViews\" type=\"checkbox\"> Show technical/internal views</label><b id=\"matrixCount\"></b></div><div id=\"matrixGrid\" class=\"matrix-grid\"></div></section>
<section id=\"page-gallery\" class=\"page\"><div class=\"gallery-toolbar\"><input id=\"gallerySearch\" type=\"search\" placeholder=\"Search photos or figures…\"><select id=\"galleryKind\"></select><input id=\"galleryMin\" type=\"number\" title=\"Minimum point\"><input id=\"galleryMax\" type=\"number\" title=\"Maximum point\"><b id=\"galleryCount\"></b></div><div id=\"gallery\" class=\"gallery\"></div></section>
<section id=\"page-datasets\" class=\"page\"><div class=\"section-head\"><div><h2>Data and analysis results</h2><p>Measurements and scientific results are shown first. Technical metadata stays hidden unless requested.</p></div></div><div class=\"dataset-toolbar\"><input id=\"dataSearch\" type=\"search\" placeholder=\"Search point, method, analysis, result, or filename…\"><select id=\"dataTech\"></select><select id=\"dataKind\"></select><label><input id=\"showTechnicalData\" type=\"checkbox\"> Show technical/provenance data</label></div><div class=\"dataset-layout\"><aside id=\"datasetList\" class=\"dataset-list\"></aside><main id=\"datasetDetail\" class=\"card detail\"><div class=\"empty\">Select a dataset.</div></main></div></section>
<section id=\"page-files\" class=\"page\"><div class=\"files-toolbar\"><input id=\"fileSearch\" type=\"search\" placeholder=\"Search every file…\"><select id=\"fileCategory\"></select></div><div id=\"fileGroups\"></div></section>
<section id=\"page-about\" class=\"page\"><div class=\"card detail\"><h2>Portable FaradaIQ Experiment Explorer</h2><div id=\"shortNote\"></div><p>The Discover dashboard prioritizes scientific measurements, analyses, trends, and recommended comparisons. Parser hierarchy, record offsets, and provenance tables remain searchable in advanced views but are hidden from the normal scientific workflow.</p><p>Gallery previews are copied to compact relative paths under <code>browser_assets/media</code>. Plots provide checkbox and direct curve selection, Solo, highlighted-curve fitting, robust outlier-resistant autoscaling, full-range autoscaling, wheel zoom, pan, and drag-box zoom.</p><div class=\"meta\"><div>Version</div><div>{VERSION}</div><div>Catalog source</div><div class=\"path\">{html.escape(str(manifest.get('catalog_source','')))}</div><div>Analysis root</div><div class=\"path\">{html.escape(str(manifest.get('analysis_root','')))}</div><div>Catalog datasets</div><div>{cov.get('catalog_datasets',0)}</div><div>Additional discovered outputs</div><div>{cov.get('filesystem_discovered_datasets',0)}</div><div>Available comparison views</div><div>{comp.get('available_view_count',0)}</div><div>Internet required</div><div>No</div><div>FaradaIQ required</div><div>No</div></div><div class=\"note\"><b>Portable folder rule:</b> Copy or move the complete experiment folder. Do not move this HTML file by itself. All data, image, report, manifest, and ZIP links are relative.</div></div></section>
<div id=\"lightbox\" class=\"lightbox\"><button class=\"btn close\">×</button><img id=\"lightboxImage\"><div id=\"lightboxTitle\" style=\"position:absolute;bottom:15px;left:30px;right:30px;text-align:center\"></div></div><script>window.FARADAIQ_MANIFEST={payload};</script><script>{_JS}</script></body></html>"""


def write_portable_browser(experiment_root: str | Path, *, analysis_root: str | Path | None = None, experiment_name: str | None = None, short_names_enabled: bool | None = None, create_batch_zips: bool = True) -> dict[str, Any]:
    root = Path(experiment_root).expanduser().resolve()
    manifest = build_manifest(root, analysis_root=analysis_root, experiment_name=experiment_name, short_names_enabled=short_names_enabled, create_batch_zips=create_batch_zips)
    manifest_path, html_path, readme_path = root / MANIFEST_NAME, root / HTML_NAME, root / README_NAME
    manifest_path.write_text(json.dumps(_json_safe(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_render_html(manifest), encoding="utf-8")
    readme_path.write_text(
        "FaradaIQ portable data browser\n================================\n\n"
        f"Open {HTML_NAME} in a modern web browser. The first page is a guided Discover dashboard for trends, recommended comparisons, analyses, photos, and downloads.\n\n"
        "Copy or share the complete experiment folder. The HTML uses only relative paths and does not require FaradaIQ, Python, a web server, or internet access.\n"
        "Scientific measurements and analysis results are prioritized. Technical parser/provenance tables are hidden by default. Use Discover for recommended comparisons and Trend Finder for point-wise summaries, then open the full comparison workspace for selective zoom, pan, and export.\n"
        f"Photos, GIFs, and figures use compact portable preview copies under {ASSET_DIR_NAME}/{MEDIA_ASSET_DIR_NAME}, while original scientific paths remain recorded for provenance and source downloads.\n"
        f"Individual batch downloads are stored under {DOWNLOAD_DIR_NAME}. Scientific names are preserved in {MANIFEST_NAME} and inside every batch ZIP manifest even when physical short names are enabled.\n",
        encoding="utf-8",
    )
    return {
        "html_path": str(html_path), "manifest_path": str(manifest_path), "readme_path": str(readme_path),
        "download_dir": str(root / DOWNLOAD_DIR_NAME), "asset_dir": str(root / ASSET_DIR_NAME),
        "media_preview_count": manifest.get("media_assets", {}).get("preview_count", 0),
        "dataset_count": manifest.get("dataset_count", 0),
        "comparison_curve_count": manifest.get("comparison", {}).get("comparison_curve_count", 0),
        "available_view_count": manifest.get("comparison", {}).get("available_view_count", 0),
        "analysis_dataset_count": manifest.get("coverage", {}).get("analysis", 0),
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
        if not getattr(old_defaults, "_faradaiq_v277_portable", False):
            def defaults(_old=old_defaults) -> dict[str, Any]:
                values = dict(_old()); values.setdefault("portable_html_browser", True); return values
            defaults._faradaiq_v277_portable = True; jobs.default_export_settings = defaults
        BatchJob, old_init = jobs.BatchJob, jobs.BatchJob.__init__
        if not getattr(old_init, "_faradaiq_v277_portable", False):
            def init(self: Any, *args: Any, _old=old_init, _jobs=jobs, **kwargs: Any) -> None:
                _old(self, *args, **kwargs); merged = _jobs.default_export_settings(); merged.update(getattr(self, "export_settings", {}) or {}); self.export_settings = merged
            init._faradaiq_v277_portable = True; BatchJob.__init__ = init
        Window, old_build = jobs.BatchQueueWindow, jobs.BatchQueueWindow._build
        if not getattr(old_build, "_faradaiq_v277_portable", False):
            def build(self: Any, _old=old_build) -> Any:
                result = _old(self); tk, ttk = self.tk, self.ttk; var = tk.BooleanVar(master=self.win, value=True); self.portable_html_browser_var = var; self.export_vars["portable_html_browser"] = var
                box = _find_widget_by_text(self.win, "Export and parser settings")
                if box is not None:
                    try: row = max(7, int(box.grid_size()[1]))
                    except Exception: row = 7
                    ttk.Checkbutton(box, text="Create portable HTML data browser", variable=var).grid(row=row, column=0, columnspan=2, sticky="w", pady=(5, 1))
                    ttk.Label(box, text="Default ON. Technique-aware comparisons for raw data, analyses, maps, media, and portable batch ZIPs.", style="Muted.TLabel").grid(row=row+1, column=0, columnspan=2, sticky="w", padx=(22, 8), pady=(0, 3))
                return result
            build._faradaiq_v277_portable = True; Window._build = build
        old_run = jobs.run_batch_job
        if not getattr(old_run, "_faradaiq_v277_portable", False):
            def run(job: Any, *args: Any, _old=old_run, **kwargs: Any) -> Any:
                result = _old(job, *args, **kwargs); enabled = bool((job.export_settings or {}).get("portable_html_browser", True))
                if enabled:
                    progress_cb = args[1] if len(args) > 1 and callable(args[1]) else kwargs.get("progress")
                    job.status, job.stage, job.progress, job.message, job.finished_at = "Running", "portable_browser", 100.0, "Building all technique and analysis comparisons, media gallery, and portable batch downloads.", ""
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
            run._faradaiq_v277_portable = True; jobs.run_batch_job = run
    try:
        from faradaiq import v233_batch_jobs as active
        for key in ("_v233_default_export_settings", "_v232_default_export_settings", "_v230_default_export_settings", "_v229_default_export_settings"): core[key] = active.default_export_settings
        for key in ("_v233_run_batch_job", "_v232_run_batch_job", "_v230_run_batch_job", "_v229_run_batch_job"): core[key] = active.run_batch_job
    except Exception: pass


def _patch_main_gui(core: MutableMapping[str, Any]) -> None:
    Session, tk, ttk = core.get("SessionReaderGUI"), core.get("tk"), core.get("ttk")
    if Session is None or tk is None or ttk is None: return
    old_build = Session._build_ui
    if not getattr(old_build, "_faradaiq_v277_portable", False):
        def build(self: Any, *args: Any, **kwargs: Any) -> Any:
            if not hasattr(self, "portable_html_browser_var"): self.portable_html_browser_var = tk.BooleanVar(master=self, value=True)
            result = old_build(self, *args, **kwargs); label = _find_widget_by_text(self, "Use shortened names for generated folders and files"); short_frame = getattr(label, "master", None) if label is not None else None; top = getattr(short_frame, "master", None) if short_frame is not None else None
            if top is not None:
                try: row = max(4, int(top.grid_size()[1]))
                except Exception: row = 4
                portable = ttk.Frame(top); portable.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(1, 2)); ttk.Checkbutton(portable, text="Create portable HTML data browser", variable=self.portable_html_browser_var).pack(side="left"); ttk.Label(portable, text="Default ON. All-technique comparisons, analysis views, maps, media, dataset previews, and compact ZIPs.", style="Muted.TLabel").pack(side="left", padx=(10, 0))
            return result
        build._faradaiq_v277_portable = True; Session._build_ui = build
    old_export = Session.export_outputs
    if not getattr(old_export, "_faradaiq_v277_portable", False):
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
        export._faradaiq_v277_portable = True; Session.export_outputs = export


def install(core: MutableMapping[str, Any]) -> None:
    core["FARADAIQ_PUBLIC_VERSION"] = VERSION; core["FARADAIQ_APP_VERSION"] = VERSION; core["APP_TITLE"] = APP_TITLE
    core["_v277_write_portable_browser"] = write_portable_browser; core["_v277_build_portable_browser_manifest"] = build_manifest
    collector = core.get("_v177_collect_full_session")
    if callable(collector) and not getattr(collector, "_faradaiq_v277_portable", False):
        def collect_full_session(gui: Any) -> Any:
            payload = collector(gui)
            if isinstance(payload, dict): payload["portable_html_browser"] = {"enabled": bool(getattr(getattr(gui, "portable_html_browser_var", None), "get", lambda: True)()), "default_enabled": True, "html_name": HTML_NAME, "relative_paths_only": True, "comparison_first": True, "technique_registry": True, "cross_technique_views": True}
            return payload
        collect_full_session._faradaiq_v277_portable = True; core["_v177_collect_full_session"] = collect_full_session
    applier = core.get("_v177_apply_full_session")
    if callable(applier) and not getattr(applier, "_faradaiq_v277_portable", False):
        def apply_full_session(gui: Any, payload: Any, *args: Any, **kwargs: Any) -> Any:
            result = applier(gui, payload, *args, **kwargs)
            try:
                settings = payload.get("portable_html_browser", {}) if isinstance(payload, dict) else {}
                if hasattr(gui, "portable_html_browser_var") and "enabled" in settings: gui.portable_html_browser_var.set(bool(settings.get("enabled")))
            except Exception: pass
            return result
        apply_full_session._faradaiq_v277_portable = True; core["_v177_apply_full_session"] = apply_full_session
    _patch_batch_jobs(core); _patch_main_gui(core)
