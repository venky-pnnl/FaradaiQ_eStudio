"""Technique-aware electrochemistry routing guards for FaradaIQ Studio v290.

This module runs before SciLink.  It inspects only authorized job-local files,
performs deterministic direct analyses for routes that must not use a generic
curve-fitting agent, validates canonical HDF5 structure read-only, and writes a
machine-readable routing contract for SciLink and downstream campaign UIs.
"""
from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

import numpy as np

from .numpy_compat import trapezoid

VERSION = "v290"
SEMVER = "0.290.0"
EXPECTED_DUMMY = {
    "Rs_ohm": 1_000.0,
    "Rp_ohm": 10_000.0,
    "C_F": 10e-6,
    "R_total_ohm": 11_000.0,
    "tau_s": 0.1,
    "fc_Hz": 1.0 / (2.0 * math.pi * 0.1),
}
ELECTROCHEM_TECHNIQUES = (
    "OCP", "CV", "LSV", "CA", "CP", "EIS", "GEIS", "DPV", "SWV",
    "STRIPPING", "DEPOSITION", "SECCM", "SPATIAL_ELECTROCHEMISTRY", "DRT",
)
TECHNIQUES = ELECTROCHEM_TECHNIQUES
GENERAL_ROUTING_POLICY = {
    "route": "technique_specific_deterministic_electrochemistry",
    "generic_curve_fitting_allowed": False,
    "curve_fitting_agent_required": False,
    "spectral_peak_fitting_allowed": False,
    "best_of_n_allowed": False,
    "adaptive_refit_allowed": False,
    "generic_r2_gate_allowed": False,
    "deterministic_analysis_required": True,
    "run_analysis_allowed": False,
    "select_agent_allowed": False,
    "interpretation_only": True,
}
PROHIBITED_SCILINK_TOOLS = (
    "select_agent", "run_analysis", "CurveFittingAgent", "Best-of-3", "Best-of-N",
    "adaptive refit", "annealing", "generic R2 verification", "generic spectroscopy peak fitting",
)
TECHNIQUE_ROUTE_NAMES = {
    "OCP": "deterministic_electrochemical_ocp_analysis",
    "CV": "deterministic_electrochemical_cv_analysis",
    "LSV": "deterministic_electrochemical_lsv_analysis",
    "CA": "deterministic_electrochemical_ca_analysis",
    "CP": "deterministic_electrochemical_cp_analysis",
    "EIS": "deterministic_electrochemical_eis_analysis",
    "GEIS": "deterministic_electrochemical_geis_analysis",
    "DPV": "deterministic_electrochemical_dpv_analysis",
    "SWV": "deterministic_electrochemical_swv_analysis",
    "STRIPPING": "deterministic_electrochemical_stripping_analysis",
    "DEPOSITION": "deterministic_electrochemical_electrodeposition_analysis",
    "SECCM": "deterministic_electrochemical_seccm_analysis",
    "SPATIAL_ELECTROCHEMISTRY": "deterministic_spatial_electrochemistry_analysis",
    "DRT": "deterministic_drt_linked_electrochemistry_analysis",
}
DIRECT_ROUTES = {
    "ocp_deterministic", "cv_deterministic", "lsv_deterministic", "ca_deterministic",
    "cp_deterministic", "eis_complex_deterministic", "geis_complex_deterministic",
    "dpv_deterministic", "swv_deterministic", "stripping_deterministic",
    "electrodeposition_deterministic", "spatial_electrochemistry_deterministic",
    "drt_linked_deterministic", "dummy_cell_cv_direct", "dummy_cell_eis_exact_complex",
    "blocked_drt_without_linked_eis_geis", "unknown_electrochemistry_descriptive",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _json_load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception:
        return None


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except Exception:
        return None


def _column_index(columns: Sequence[str], aliases: Sequence[str]) -> int | None:
    keys = [_norm(c) for c in columns]
    targets = {_norm(a) for a in aliases}
    for i, key in enumerate(keys):
        if key in targets:
            return i
    for i, key in enumerate(keys):
        if any(target and target in key for target in targets):
            return i
    return None


def _as_numeric(array: Any) -> np.ndarray:
    raw = np.asarray(array)
    if raw.ndim == 1:
        raw = raw.reshape(-1, 1)
    if raw.ndim != 2:
        return np.empty((0, 0), dtype=float)
    out = np.full(raw.shape, np.nan, dtype=float)
    for r in range(raw.shape[0]):
        for c in range(raw.shape[1]):
            try:
                out[r, c] = float(raw[r, c])
            except Exception:
                pass
    return out


def _read_delimited(path: Path) -> tuple[list[str], np.ndarray, dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return [], np.empty((0, 0)), {"error": "empty file"}
    candidates = [",", "\t", ";"]
    delimiter = max(candidates, key=lambda d: lines[0].count(d))
    if lines[0].count(delimiter) == 0:
        delimiter = None
    rows: list[list[str]] = []
    if delimiter:
        rows = list(csv.reader(lines, delimiter=delimiter))
    else:
        rows = [re.split(r"\s+", line.strip()) for line in lines]
    header_idx = 0
    for i, row in enumerate(rows[:100]):
        joined = " ".join(row).lower()
        if any(token in joined for token in ("time", "potential", "current", "frequency", "zreal", "zimag", "z_real", "z_imag")):
            header_idx = i
            break
    columns = [str(x).strip().strip('"') for x in rows[header_idx]]
    body = rows[header_idx + 1 :]
    width = len(columns)
    parsed: list[list[float]] = []
    for row in body:
        if len(row) < width:
            continue
        vals = [_finite(row[i]) for i in range(width)]
        if sum(v is not None for v in vals) < 2:
            continue
        parsed.append([np.nan if v is None else v for v in vals])
    return columns, np.asarray(parsed, dtype=float) if parsed else np.empty((0, width)), {
        "source_format": path.suffix.lower().lstrip("."),
        "header_row": header_idx,
        "row_count": len(parsed),
    }


def _decode_json_attr(value: Any) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    try:
        return json.loads(str(value))
    except Exception:
        return {}


def inspect_canonical_hdf5(path: str | Path, *, include_arrays: bool = False) -> dict[str, Any]:
    """Read a FaradaIQ canonical HDF5 bundle directly and read-only."""
    p = Path(path).resolve()
    report: dict[str, Any] = {
        "path": str(p), "opened_mode": "r", "read_only": True,
        "manifest_json_present": False, "tables_present": False,
        "metadata_present": False, "records": [], "errors": [],
    }
    try:
        import h5py
    except Exception as exc:
        report["errors"].append(f"h5py unavailable: {exc}")
        return report
    try:
        with h5py.File(p, "r") as h5:
            report["root_attributes"] = {str(k): str(v) for k, v in h5.attrs.items()}
            if "manifest_json" in h5:
                report["manifest_json_present"] = True
                raw = h5["manifest_json"][()]
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    manifest = json.loads(str(raw))
                except Exception:
                    manifest = {}
                report["manifest_schema"] = manifest.get("schema") if isinstance(manifest, Mapping) else None
                report["manifest_record_count"] = len(manifest.get("records") or []) if isinstance(manifest, Mapping) else 0
            report["tables_present"] = "tables" in h5
            report["metadata_present"] = "metadata" in h5
            if "metadata" in h5:
                meta_group = h5["metadata"]
                report["metadata_members"] = list(meta_group.keys())[:500]
            if "tables" in h5:
                for name, ds in h5["tables"].items():
                    if not isinstance(ds, h5py.Dataset):
                        continue
                    columns = _decode_json_attr(ds.attrs.get("columns_json", "[]"))
                    if not isinstance(columns, list):
                        columns = []
                    record = _decode_json_attr(ds.attrs.get("record_json", "{}"))
                    if not isinstance(record, Mapping):
                        record = {}
                    canonical_key = str(record.get("canonical_key") or ds.attrs.get("canonical_key") or "")
                    point = record.get("point")
                    if point is None:
                        point = record.get("point_index")
                    if point is None:
                        match = re.search(r"point[_:\-]?(\d+)", canonical_key, re.I)
                        point = int(match.group(1)) if match else None
                    coordinates = {
                        "x": record.get("x_position", record.get("x_m")),
                        "y": record.get("y_position", record.get("y_m")),
                        "z": record.get("z_position", record.get("z_m")),
                    }
                    coordinates = {k: v for k, v in coordinates.items() if v is not None and str(v) != ""}
                    item: dict[str, Any] = {
                        "dataset_path": f"/tables/{name}",
                        "shape": list(ds.shape),
                        "dtype": str(ds.dtype),
                        "columns_json": columns,
                        "record_json": dict(record),
                        "canonical_key": canonical_key or None,
                        "canonical_point_identity": {
                            "point_index": int(point) if _finite(point) is not None else None,
                            "canonical_key": canonical_key or None,
                            "coordinates": coordinates or None,
                            "coordinates_invented": False,
                        },
                    }
                    if include_arrays:
                        item["array"] = ds[()]
                    report["records"].append(item)
            report["record_count"] = len(report["records"])
    except Exception as exc:
        report["errors"].append(str(exc))
    return report



def _technique_from(columns: Sequence[str], record: Mapping[str, Any], name: str, prompt: str) -> str:
    """Detect electrochemical technique from structured metadata before waveform cues."""
    fields = [name, str(record.get("technique") or ""), str(record.get("record_type") or ""),
              str(record.get("role") or ""), str(record.get("method") or ""), prompt]
    blob = " ".join(fields).upper()
    colkeys = {_norm(c) for c in columns}
    spatial = any(token in blob for token in ("SECCM", "SPATIAL ELECTROCHEM", "POINT MAP", "LOCAL ELECTROCHEM"))
    if any(x in blob for x in ("GEIS", "GALVANOSTATIC IMPEDANCE")):
        return "GEIS"
    if any(x in blob for x in ("EIS", "IMPEDANCE")) or (
        any("frequency" in c or c in {"f", "freq"} for c in colkeys)
        and any("zreal" in c or "rez" in c for c in colkeys)
        and any("zimag" in c or "imz" in c for c in colkeys)
    ):
        return "EIS"
    if "DRT" in blob or ({"taus", "gammaohm"} <= colkeys):
        return "DRT"
    if any(x in blob for x in ("APPROACH CURVE", "TIPDOWN", "TIP DOWN", "SECCM", "SPATIAL ELECTROCHEM")):
        return "SECCM"
    if "OCP" in blob or "OPEN CIRCUIT" in blob:
        return "OCP"
    if any(x in blob for x in ("ELECTRODEPOSITION", "ELECTROCRYSTALLIZATION", "PLATING", "DEPOSIT")):
        return "DEPOSITION"
    if "STRIPPING" in blob:
        return "STRIPPING"
    if "DPV" in blob or "DIFFERENTIAL PULSE" in blob:
        return "DPV"
    if "SWV" in blob or "SQUARE WAVE" in blob:
        return "SWV"
    if re.search(r"\bLSV\b|LINEAR SWEEP", blob):
        return "LSV"
    if re.search(r"\bCV\b|CYCLIC VOLT", blob):
        return "CV"
    if re.search(r"\bCA\b|CHRONOAMP", blob):
        return "CA"
    if re.search(r"\bCP\b|CHRONOPOT", blob):
        return "CP"
    # Waveform/column cues after metadata.
    has_t = any("time" in c or c in {"t", "seconds"} for c in colkeys)
    has_e = any("potential" in c or c in {"e", "ev", "ewe", "voltage"} for c in colkeys)
    has_i = any("current" in c or c in {"i", "ia", "j"} for c in colkeys)
    if has_t and has_e and not has_i:
        return "OCP"
    if has_e and has_i:
        return "CV"
    if has_t and has_i:
        return "CA"
    if spatial:
        return "SPATIAL_ELECTROCHEMISTRY"
    return "UNKNOWN"


def _dummy_cell_context(prompt: str, objects: Iterable[Any]) -> bool:
    blob = (prompt + " " + " ".join(json.dumps(o, default=str)[:100000] for o in objects)).lower()
    return (
        "dummy cell" in blob
        or "ac dummy" in blob
        or ("palmsens" in blob and ("10 k" in blob or "10k" in blob or "10 µf" in blob or "10 uf" in blob))
        or "rs + (rp || c)" in blob
    )


def _linear_metrics(x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]; y = y[mask]
    if len(x) < 2 or np.ptp(x) == 0:
        return {"n": int(len(x)), "slope": None, "intercept": None, "r2": None}
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return {"n": int(len(x)), "slope": float(slope), "intercept": float(intercept), "r2": float(r2)}


def analyze_ocp(columns: Sequence[str], array: np.ndarray, identity: Mapping[str, Any]) -> dict[str, Any]:
    ti = _column_index(columns, ("time_s", "time", "elapsed_s", "seconds", "t"))
    ei = _column_index(columns, ("potential_V", "potential", "voltage", "E", "Ewe", "E_V"))
    if ti is None or ei is None:
        return {"route": "ocp_direct", "status": "blocked_missing_columns", "required": ["time", "potential"], "identity": dict(identity)}
    t = array[:, ti]; e = array[:, ei]
    mask = np.isfinite(t) & np.isfinite(e)
    t = t[mask]; e = e[mask]
    if len(t) < 2:
        return {"route": "ocp_direct", "status": "insufficient_rows", "identity": dict(identity)}
    order = np.argsort(t); t = t[order]; e = e[order]
    lm = _linear_metrics(t, e)
    nterm = max(3, int(math.ceil(len(e) * 0.2)))
    terminal = e[-nterm:]
    dt = float(t[-1] - t[0])
    return {
        "route": "ocp_direct", "status": "complete", "identity": dict(identity),
        "agent_policy": {"CurveFittingAgent": "prohibited", "automatic_exponential_fit": False},
        "sign_preserved": True,
        "metrics": {
            "n": int(len(e)), "duration_s": dt,
            "initial_potential_V": float(e[0]), "final_potential_V": float(e[-1]),
            "mean_potential_V": float(np.mean(e)), "median_potential_V": float(np.median(e)),
            "offset_from_zero_V": float(np.mean(e)),
            "total_drift_V": float(e[-1] - e[0]),
            "drift_rate_V_per_s": lm.get("slope"),
            "drift_rate_mV_per_min": float(lm["slope"] * 60_000.0) if lm.get("slope") is not None else None,
            "noise_std_terminal_V": float(np.std(terminal, ddof=1)) if len(terminal) > 1 else 0.0,
            "noise_peak_to_peak_terminal_V": float(np.ptp(terminal)),
            "max_abs_potential_V": float(np.max(np.abs(e))),
            "repeated_timestamp_count": int(len(t) - len(np.unique(t))),
            "nonmonotonic_time_count": int(np.sum(np.diff(t) < 0)),
        },
    }


def _segments_by_scan_direction(potential: np.ndarray) -> list[tuple[int, int, int]]:
    if len(potential) < 3:
        return [(0, len(potential), 0)]
    d = np.diff(potential)
    sign = np.sign(d)
    # Fill zero derivatives from neighboring non-zero values.
    for i in range(1, len(sign)):
        if sign[i] == 0:
            sign[i] = sign[i - 1]
    for i in range(len(sign) - 2, -1, -1):
        if sign[i] == 0:
            sign[i] = sign[i + 1]
    breaks = [0] + [i + 1 for i in range(1, len(sign)) if sign[i] != sign[i - 1]] + [len(potential)]
    out = []
    for a, b in zip(breaks[:-1], breaks[1:]):
        if b - a >= 3:
            direction = int(np.sign(potential[b - 1] - potential[a]))
            out.append((a, b, direction))
    return out or [(0, len(potential), int(np.sign(potential[-1] - potential[0])))]


def potential_direction_change_count(columns: Sequence[str], array: np.ndarray) -> int:
    """Count durable potential-sweep reversals while ignoring zero/noise steps."""
    ei = _column_index(columns, ("potential_V", "potential", "voltage", "E", "Ewe", "E_V"))
    data = _as_numeric(array)
    if ei is None or data.size == 0 or data.shape[0] < 3:
        return 0
    e = data[:, ei]
    e = e[np.isfinite(e)]
    if len(e) < 3:
        return 0
    d = np.diff(e)
    span = float(np.ptp(e))
    tol = max(span * 1e-9, np.finfo(float).eps * 100.0)
    signs = np.sign(d[np.abs(d) > tol])
    if len(signs) < 2:
        return 0
    # Collapse repeated signs. A sign transition is a true sweep-direction change.
    compact = [int(signs[0])]
    for value in signs[1:]:
        iv = int(value)
        if iv and iv != compact[-1]:
            compact.append(iv)
    return max(0, len(compact) - 1)


def refine_cv_lsv_technique(columns: Sequence[str], array: np.ndarray, technique: str) -> str:
    """Use the potential waveform to distinguish CV from LSV.

    A complete waveform with at least one durable direction reversal is CV even
    when upstream metadata contains an ambiguous LSV/CV label. A monotonic
    potential sweep is LSV. Other technique assignments are preserved.
    """
    current = str(technique or "UNKNOWN").upper()
    has_e = _column_index(columns, ("potential_V", "potential", "voltage", "E", "Ewe", "E_V")) is not None
    has_i = _column_index(columns, ("current_A", "current", "I_A", "I", "Imon")) is not None
    if current not in {"CV", "LSV", "UNKNOWN"} or not (has_e and has_i):
        return current
    return "CV" if potential_direction_change_count(columns, array) >= 1 else "LSV"


def analyze_dummy_cv(columns: Sequence[str], array: np.ndarray, identity: Mapping[str, Any]) -> dict[str, Any]:
    ei = _column_index(columns, ("potential_V", "potential", "voltage", "E", "Ewe", "E_V"))
    ii = _column_index(columns, ("current_A", "current", "I_A", "I", "Imon"))
    if ei is None or ii is None:
        return {"route": "dummy_cell_cv_direct", "status": "blocked_missing_columns", "required": ["potential", "current"], "identity": dict(identity)}
    e = array[:, ei]; i = array[:, ii]
    mask = np.isfinite(e) & np.isfinite(i)
    e = e[mask]; i = i[mask]
    if len(e) < 4:
        return {"route": "dummy_cell_cv_direct", "status": "insufficient_rows", "identity": dict(identity)}
    segments = []
    for index, (a, b, direction) in enumerate(_segments_by_scan_direction(e), 1):
        lm = _linear_metrics(e[a:b], i[a:b])
        slope = lm.get("slope")
        lm.update({
            "segment": index,
            "direction": "forward" if direction > 0 else "reverse" if direction < 0 else "flat",
            "start_potential_V": float(e[a]), "end_potential_V": float(e[b - 1]),
            "apparent_resistance_ohm": float(1.0 / slope) if slope not in (None, 0.0) else None,
            "minimum_current_A": float(np.min(i[a:b])), "maximum_current_A": float(np.max(i[a:b])),
        })
        segments.append(lm)
    overall = _linear_metrics(e, i)
    slope = overall.get("slope")
    symmetry = None
    if len(segments) >= 2:
        fwd = _segments_by_scan_direction(e)[0]
        rev = _segments_by_scan_direction(e)[1]
        ef, inf = e[fwd[0]:fwd[1]], i[fwd[0]:fwd[1]]
        er, inr = e[rev[0]:rev[1]], i[rev[0]:rev[1]]
        lo, hi = max(np.min(ef), np.min(er)), min(np.max(ef), np.max(er))
        if hi > lo:
            grid = np.linspace(lo, hi, min(200, max(20, len(ef), len(er))))
            yf = np.interp(grid, np.sort(ef), inf[np.argsort(ef)])
            yr = np.interp(grid, np.sort(er), inr[np.argsort(er)])
            denom = max(float(np.ptp(i)), np.finfo(float).eps)
            symmetry = {
                "matched_potential_rmse_A": float(np.sqrt(np.mean((yf - yr) ** 2))),
                "normalized_rmse": float(np.sqrt(np.mean((yf - yr) ** 2)) / denom),
                "correlation": float(np.corrcoef(yf, yr)[0, 1]) if np.std(yf) > 0 and np.std(yr) > 0 else None,
            }
    return {
        "route": "dummy_cell_cv_direct", "status": "complete", "identity": dict(identity),
        "agent_policy": {
            "CurveFittingAgent": "prohibited", "spectral_peak_fitting": False,
            "gaussian_fit": False, "sech_fit": False,
        },
        "sign_convention": {"raw_current_preserved": True, "negative_cathodic_current_preserved": bool(np.min(i) < 0)},
        "metrics": {
            "n": int(len(e)), "minimum_current_A": float(np.min(i)), "maximum_current_A": float(np.max(i)),
            "current_range_A": float(np.ptp(i)), "potential_range_V": float(np.ptp(e)),
            "overall_linearity_r2": overall.get("r2"),
            "overall_slope_A_per_V": slope,
            "overall_apparent_resistance_ohm": float(1.0 / slope) if slope not in (None, 0.0) else None,
            "segments": segments, "forward_reverse_symmetry": symmetry,
        },
    }


def _dummy_impedance(freq: np.ndarray, rs: float, rp: float, cap: float) -> np.ndarray:
    omega = 2.0 * math.pi * freq
    return rs + 1.0 / (1.0 / rp + 1j * omega * cap)


def _fit_dummy_once(freq: np.ndarray, z: np.ndarray, initial: Sequence[float]) -> tuple[np.ndarray, dict[str, Any]]:
    scale = max(float(np.median(np.abs(z))), 1.0)
    def residual(logp: np.ndarray) -> np.ndarray:
        rs, rp, cap = np.exp(logp)
        model = _dummy_impedance(freq, rs, rp, cap)
        diff = (model - z) / scale
        return np.concatenate((diff.real, diff.imag))
    x0 = np.log(np.maximum(np.asarray(initial, dtype=float), [1e-9, 1e-9, 1e-15]))
    try:
        from scipy.optimize import least_squares
        fit = least_squares(residual, x0, method="trf", max_nfev=4000, xtol=1e-12, ftol=1e-12, gtol=1e-12)
        params = np.exp(fit.x)
        return params, {"solver": "scipy.optimize.least_squares", "success": bool(fit.success), "nfev": int(fit.nfev), "cost": float(fit.cost)}
    except Exception:
        # Deterministic coordinate search fallback in log-space.
        x = x0.copy(); best = float(np.sum(residual(x) ** 2)); evaluations = 1
        for step in (2.0, 1.0, 0.5, 0.2, 0.08, 0.03, 0.01):
            improved = True
            while improved:
                improved = False
                for j in range(3):
                    for sign in (-1.0, 1.0):
                        trial = x.copy(); trial[j] += sign * step
                        score = float(np.sum(residual(trial) ** 2)); evaluations += 1
                        if score < best:
                            x, best, improved = trial, score, True
        return np.exp(x), {"solver": "deterministic_log_coordinate_search", "success": True, "evaluations": evaluations, "cost": best / 2.0}


def analyze_dummy_eis(columns: Sequence[str], array: np.ndarray, identity: Mapping[str, Any]) -> dict[str, Any]:
    fi = _column_index(columns, ("frequency_Hz", "frequency", "freq", "f_Hz", "f"))
    ri = _column_index(columns, ("z_real_ohm", "Zreal", "Zre", "ReZ", "Z_prime"))
    ii = _column_index(columns, ("z_imag_ohm", "Zimag", "Zim", "ImZ", "Z_double_prime"))
    if None in (fi, ri, ii):
        return {"route": "dummy_cell_eis_exact_complex", "status": "blocked_missing_columns", "required": ["frequency", "Zreal", "Zimag"], "identity": dict(identity)}
    f = array[:, int(fi)]; zr = array[:, int(ri)]; zi_raw = array[:, int(ii)]
    mask = np.isfinite(f) & np.isfinite(zr) & np.isfinite(zi_raw) & (f > 0)
    f, zr, zi_raw = f[mask], zr[mask], zi_raw[mask]
    if len(f) < 3:
        return {"route": "dummy_cell_eis_exact_complex", "status": "insufficient_rows", "identity": dict(identity)}
    order = np.argsort(f); f, zr, zi_raw = f[order], zr[order], zi_raw[order]
    rs0 = max(float(zr[-1]), 1e-6)
    rp0 = max(float(zr[0] - rs0), 1e-6)
    peak_idx = int(np.argmax(np.abs(zi_raw)))
    fc0 = max(float(f[peak_idx]), 1e-9)
    c0 = max(1.0 / (2.0 * math.pi * rp0 * fc0), 1e-15)
    attempts = []
    signs = [1.0]
    # One corrected retry only when the raw imaginary sign is opposite the passive RC convention.
    if float(np.median(zi_raw)) > 0:
        signs.append(-1.0)
    best = None
    for attempt_no, sign in enumerate(signs[:2], 1):
        zi = zi_raw * sign
        z = zr + 1j * zi
        params, meta = _fit_dummy_once(f, z, (rs0, rp0, c0))
        model = _dummy_impedance(f, *params)
        residual = model - z
        score = float(np.sqrt(np.mean(np.abs(residual) ** 2)))
        row = {
            "attempt": attempt_no, "imaginary_multiplier": sign,
            "parameters": {"Rs_ohm": float(params[0]), "Rp_ohm": float(params[1]), "C_F": float(params[2])},
            "complex_rmse_ohm": score, **meta,
        }
        attempts.append(row)
        if best is None or score < best[0]:
            best = (score, params, sign, model)
    assert best is not None
    rmse, params, chosen_sign, model = best
    rs, rp, cap = map(float, params)
    tau = rp * cap
    fc = 1.0 / (2.0 * math.pi * tau) if tau > 0 else None
    fitted = {"Rs_ohm": rs, "Rp_ohm": rp, "C_F": cap, "R_total_ohm": rs + rp, "tau_s": tau, "fc_Hz": fc}
    comparisons = {
        key: {
            "expected": expected,
            "fitted": fitted[key],
            "relative_error_percent": 100.0 * (fitted[key] - expected) / expected if expected else None,
        }
        for key, expected in EXPECTED_DUMMY.items()
    }
    return {
        "route": "dummy_cell_eis_exact_complex", "status": "complete", "identity": dict(identity),
        "model": "Rs + (Rp || C)", "complex_fit": True,
        "real_and_imaginary_used": True, "generic_xy_reduction": False,
        "complex_to_real_casting": False, "deterministic": True,
        "attempt_count": len(attempts), "maximum_attempts": 2,
        "corrected_retry_used": len(attempts) == 2,
        "chosen_imaginary_multiplier": chosen_sign,
        "prohibited_models": ["Gaussian", "sech", "CPE", "Warburg", "extra circuit branches"],
        "fitted": fitted, "expected": dict(EXPECTED_DUMMY), "comparison": comparisons,
        "fit_quality": {"complex_rmse_ohm": rmse, "frequency_min_Hz": float(np.min(f)), "frequency_max_Hz": float(np.max(f)), "n": int(len(f))},
        "attempts": attempts,
    }



def _robust_sigma(values: np.ndarray) -> float | None:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return None
    med = float(np.median(values))
    sigma = float(1.4826 * np.median(np.abs(values - med)))
    return sigma if math.isfinite(sigma) else None


def _trapz(y: np.ndarray, x: np.ndarray) -> float | None:
    mask = np.isfinite(x) & np.isfinite(y)
    if np.count_nonzero(mask) < 2:
        return None
    xx, yy = x[mask], y[mask]
    order = np.argsort(xx)
    return float(trapezoid(yy[order], xx[order]))


def _base_analysis(route: str, technique: str, identity: Mapping[str, Any], columns: Sequence[str], n: int) -> dict[str, Any]:
    return {
        "route": route,
        "technique": technique,
        "status": "complete" if n else "no_valid_rows",
        "identity": dict(identity),
        "rows": int(n),
        "columns": list(columns),
        "routing_policy": dict(GENERAL_ROUTING_POLICY),
        "generic_curve_fitting_used": False,
        "curve_fitting_agent_used": False,
        "spectral_peak_fitting_used": False,
        "best_of_n_used": False,
        "adaptive_refit_used": False,
        "generic_r2_gate_used": False,
        "raw_data_modified": False,
        "assumptions": [],
        "missing_metadata": [],
        "uncertainty": {},
    }


def _potential_current(columns: Sequence[str], array: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    ei = _column_index(columns, ("potential_V", "potential", "E", "Ewe", "voltage"))
    ii = _column_index(columns, ("current_A", "current", "I", "I_A"))
    ti = _column_index(columns, ("time_s", "time", "t", "elapsed_s", "seconds"))
    if ei is None or ii is None:
        return np.array([]), np.array([]), None
    e = array[:, ei].astype(float); i = array[:, ii].astype(float)
    t = array[:, ti].astype(float) if ti is not None else None
    mask = np.isfinite(e) & np.isfinite(i)
    if t is not None:
        mask &= np.isfinite(t)
    return e[mask], i[mask], (t[mask] if t is not None else None)


def analyze_cv_lsv(columns: Sequence[str], array: np.ndarray, identity: Mapping[str, Any], technique: str = "CV") -> dict[str, Any]:
    e, i, t = _potential_current(columns, array)
    route = "cv_deterministic" if technique == "CV" else "lsv_deterministic"
    out = _base_analysis(route, technique, identity, columns, len(e))
    if len(e) < 3:
        out["status"] = "blocked_missing_columns_or_rows"
        out["required_columns"] = ["potential", "current"]
        return out
    bounds = _segments_by_scan_direction(e) if technique == "CV" else [(0, len(e), 1 if e[-1] >= e[0] else -1)]
    sweeps = []
    for sweep_index, (start, stop, direction) in enumerate(bounds, 1):
        ee, ii = e[start:stop], i[start:stop]
        if len(ee) < 3:
            continue
        max_idx = int(np.nanargmax(ii)); min_idx = int(np.nanargmin(ii))
        baseline_n = max(3, min(len(ee), int(round(0.1 * len(ee)))))
        baseline_fit = _linear_metrics(ee[:baseline_n], ii[:baseline_n])
        baseline = np.polyval([baseline_fit.get("slope") or 0.0, baseline_fit.get("intercept") or float(ii[0])], ee)
        residual = ii - baseline
        sigma = _robust_sigma(residual[:baseline_n]) or _robust_sigma(np.diff(ii)) or 0.0
        signed = residual if direction > 0 else -residual
        onset = None
        threshold = 5.0 * max(sigma, np.finfo(float).eps)
        for k in range(baseline_n, max(baseline_n, len(ee) - 4)):
            if np.all(signed[k:k+5] > threshold):
                onset = float(ee[k]); break
        charge = None
        if t is not None:
            charge = _trapz(ii, t[start:stop])
        sweep = {
            "sweep_index": sweep_index,
            "direction": "positive-going" if direction > 0 else "negative-going",
            "row_start": int(start), "row_stop_exclusive": int(stop), "rows": int(len(ee)),
            "potential_start_V": float(ee[0]), "potential_end_V": float(ee[-1]),
            "current_min_A": float(ii[min_idx]), "potential_at_current_min_V": float(ee[min_idx]),
            "current_max_A": float(ii[max_idx]), "potential_at_current_max_V": float(ee[max_idx]),
            "onset_V": onset,
            "onset_method": "five consecutive points beyond initial linear baseline by five robust standard deviations",
            "charge_C": charge,
            "noise_robust_sigma_A": sigma,
            "spectral_peak_fit": None,
        }
        sweeps.append(sweep)
    out["metrics"] = {
        "sweep_count": len(sweeps), "sweeps": sweeps,
        "current_range_A": float(np.ptp(i)), "potential_range_V": float(np.ptp(e)),
        "negative_cathodic_current_preserved": bool(np.nanmin(i) < 0),
    }
    if len(sweeps) >= 2:
        out["metrics"]["hysteresis_charge_difference_C"] = (
            None if sweeps[0].get("charge_C") is None or sweeps[1].get("charge_C") is None
            else float(sweeps[0]["charge_C"] - sweeps[1]["charge_C"])
        )
    if t is None:
        out["missing_metadata"].append("time or scan rate required for charge")
    out["uncertainty"] = {"current_noise_robust_sigma_A": _robust_sigma(np.diff(i))}
    return out


def analyze_ca(columns: Sequence[str], array: np.ndarray, identity: Mapping[str, Any], technique: str = "CA") -> dict[str, Any]:
    ti = _column_index(columns, ("time_s", "time", "t", "elapsed_s", "seconds"))
    ii = _column_index(columns, ("current_A", "current", "I", "I_A"))
    route = "electrodeposition_deterministic" if technique == "DEPOSITION" else "ca_deterministic"
    out = _base_analysis(route, technique, identity, columns, len(array))
    if ti is None or ii is None:
        out["status"] = "blocked_missing_columns"
        out["required_columns"] = ["time", "current"]
        return out
    t, i = array[:, ti], array[:, ii]
    mask = np.isfinite(t) & np.isfinite(i); t, i = t[mask], i[mask]
    if len(t) < 2:
        out["status"] = "insufficient_rows"; return out
    order = np.argsort(t); t, i = t[order], i[order]
    terminal_n = max(3, min(len(i), int(round(0.1 * len(i)))))
    charge = _trapz(i, t)
    peak = int(np.nanargmax(np.abs(i)))
    cottrell_eligible = bool(np.all(t[1:] > 0) and len(t) >= 10)
    out["metrics"] = {
        "time_start_s": float(t[0]), "time_end_s": float(t[-1]),
        "initial_current_A": float(i[0]), "terminal_current_A": float(np.mean(i[-terminal_n:])),
        "terminal_current_std_A": float(np.std(i[-terminal_n:], ddof=1)) if terminal_n > 1 else 0.0,
        "current_min_A": float(np.min(i)), "current_max_A": float(np.max(i)),
        "maximum_absolute_current_A": float(np.abs(i[peak])), "time_at_maximum_absolute_current_s": float(t[peak]),
        "integrated_charge_C": charge,
        "cottrell_eligibility": {
            "eligible_for_diagnostic": cottrell_eligible,
            "diffusion_coefficient_fit_allowed": False,
            "blocking_metadata": ["electrode area", "concentration", "electron number", "temperature"],
        },
    }
    if technique == "DEPOSITION":
        out["metrics"]["electrodeposition_model_eligibility"] = {
            "direct_metrics_completed": True,
            "named_models_considered": ["Cottrell", "Scharifker-Hills", "Scharifker-Mostany", "Heerman-Tarallo", "Bewick-Fleischmann-Thirsk", "Avrami"],
            "model_fit_performed": False,
            "reason": "model assumptions and metadata must be evaluated before fitting",
        }
    out["uncertainty"] = {"terminal_current_standard_deviation_A": out["metrics"]["terminal_current_std_A"]}
    return out


def analyze_cp(columns: Sequence[str], array: np.ndarray, identity: Mapping[str, Any]) -> dict[str, Any]:
    ti = _column_index(columns, ("time_s", "time", "t", "elapsed_s", "seconds"))
    ei = _column_index(columns, ("potential_V", "potential", "E", "Ewe", "voltage"))
    ii = _column_index(columns, ("current_A", "current", "I", "I_A"))
    out = _base_analysis("cp_deterministic", "CP", identity, columns, len(array))
    if ti is None or ei is None:
        out["status"] = "blocked_missing_columns"; out["required_columns"] = ["time", "potential"]; return out
    t, e = array[:, ti], array[:, ei]
    mask = np.isfinite(t) & np.isfinite(e); t, e = t[mask], e[mask]
    if len(t) < 2: out["status"] = "insufficient_rows"; return out
    order=np.argsort(t); t,e=t[order],e[order]
    de=np.gradient(e,t) if len(t)>2 else np.array([0.0]*len(t))
    transition=int(np.nanargmax(np.abs(de)))
    metrics={
        "potential_start_V": float(e[0]), "potential_end_V": float(e[-1]),
        "potential_min_V": float(np.min(e)), "potential_max_V": float(np.max(e)),
        "transition_time_s": float(t[transition]), "maximum_abs_dE_dt_V_per_s": float(np.abs(de[transition])),
        "terminal_stability_std_V": float(np.std(e[-max(3,min(len(e),int(0.1*len(e)))):])),
    }
    if ii is not None:
        cur=array[:,ii][mask][order]
        valid=np.isfinite(cur)&(np.abs(cur)>np.finfo(float).eps)
        metrics["apparent_resistance_terminal_ohm"] = float(np.median(e[valid]/cur[valid])) if np.any(valid) else None
    else:
        out["missing_metadata"].append("applied current for resistance")
    out["metrics"]=metrics; out["uncertainty"]={"terminal_potential_std_V":metrics["terminal_stability_std_V"]}
    return out


def analyze_eis_general(columns: Sequence[str], array: np.ndarray, identity: Mapping[str, Any], technique: str = "EIS") -> dict[str, Any]:
    fi = _column_index(columns, ("frequency_Hz", "frequency", "freq", "f_Hz", "f"))
    ri = _column_index(columns, ("z_real_ohm", "Zreal", "Zre", "ReZ", "Z_prime"))
    ii = _column_index(columns, ("z_imag_ohm", "Zimag", "Zim", "ImZ", "Z_double_prime"))
    route = "geis_complex_deterministic" if technique == "GEIS" else "eis_complex_deterministic"
    out = _base_analysis(route, technique, identity, columns, len(array))
    if None in (fi,ri,ii):
        out["status"]="blocked_missing_columns"; out["required_columns"]=["frequency","Zreal","Zimag"]; return out
    f,zr,zi=array[:,fi],array[:,ri],array[:,ii]
    mask=np.isfinite(f)&np.isfinite(zr)&np.isfinite(zi)&(f>0); f,zr,zi=f[mask],zr[mask],zi[mask]
    if len(f)<2: out["status"]="insufficient_rows"; return out
    order=np.argsort(f)[::-1]; f,zr,zi=f[order],zr[order],zi[order]
    z=zr+1j*zi; mag=np.abs(z); phase=np.degrees(np.angle(z))
    out["metrics"]={
        "frequency_max_Hz":float(f[0]),"frequency_min_Hz":float(f[-1]),"frequency_points":int(len(f)),
        "high_frequency_real_intercept_ohm":float(zr[0]),"low_frequency_real_impedance_ohm":float(zr[-1]),
        "real_impedance_min_ohm":float(np.min(zr)),"real_impedance_max_ohm":float(np.max(zr)),
        "imaginary_impedance_min_ohm":float(np.min(zi)),"imaginary_impedance_max_ohm":float(np.max(zi)),
        "magnitude_min_ohm":float(np.min(mag)),"magnitude_max_ohm":float(np.max(mag)),
        "phase_min_deg":float(np.min(phase)),"phase_max_deg":float(np.max(phase)),
        "imaginary_sign_preserved":True,"real_and_imaginary_used":True,
        "equivalent_circuit_fit_performed":False,
    }
    out["assumptions"].append("No equivalent circuit selected without an explicit physically justified model request.")
    out["uncertainty"]={"high_frequency_tail_real_std_ohm":float(np.std(zr[:max(2,min(5,len(zr)))],ddof=1)) if len(zr)>1 else None}
    return out


def _direct_peak_indices(y: np.ndarray) -> list[int]:
    if len(y)<3: return []
    dy=np.diff(y)
    signs=np.sign(dy)
    return [k for k in range(1,len(y)-1) if signs[k-1]>0 and signs[k]<=0]


def analyze_pulse(columns: Sequence[str], array: np.ndarray, identity: Mapping[str, Any], technique: str) -> dict[str, Any]:
    e,i,_=_potential_current(columns,array)
    out=_base_analysis(f"{technique.lower()}_deterministic",technique,identity,columns,len(e))
    if len(e)<3: out["status"]="blocked_missing_columns_or_rows"; return out
    n=max(3,min(len(i),int(0.1*len(i))))
    coeff=np.polyfit(e[:n],i[:n],1); baseline=np.polyval(coeff,e); corrected=i-baseline
    peaks=_direct_peak_indices(corrected)
    ranked=sorted(peaks,key=lambda k:abs(corrected[k]),reverse=True)[:10]
    out["metrics"]={
        "baseline_method":"initial linear baseline","peak_count_direct":len(ranked),
        "peaks":[{"potential_V":float(e[k]),"baseline_corrected_current_A":float(corrected[k]),"raw_current_A":float(i[k])} for k in ranked],
        "current_range_A":float(np.ptp(i)),"repeatability_requires_replicates":True,
    }
    out["uncertainty"]={"baseline_noise_robust_sigma_A":_robust_sigma(corrected[:n])}
    out["missing_metadata"].extend(["pulse amplitude", "pulse width", "step size"])
    return out


def analyze_stripping(columns: Sequence[str], array: np.ndarray, identity: Mapping[str, Any]) -> dict[str, Any]:
    out=analyze_pulse(columns,array,identity,"STRIPPING")
    out["route"]="stripping_deterministic"
    out["metrics"]["deposition_history_required"] = True
    e,i,t=_potential_current(columns,array)
    out["metrics"]["integrated_stripping_charge_C"]=_trapz(i,t) if t is not None else None
    if t is None: out["missing_metadata"].append("time or scan rate for stripping charge")
    return out


def analyze_drt(columns: Sequence[str], array: np.ndarray, identity: Mapping[str, Any], linked: bool) -> dict[str, Any]:
    out=_base_analysis("drt_linked_deterministic" if linked else "blocked_drt_without_linked_eis_geis","DRT",identity,columns,len(array))
    if not linked:
        out["status"]="blocked_without_linked_eis_geis"; out["metrics"]={}; return out
    ti=_column_index(columns,("tau_s","tau","relaxation_time_s")); gi=_column_index(columns,("gamma_ohm","gamma","drt"))
    if ti is None or gi is None: out["status"]="blocked_missing_columns"; return out
    tau,g=array[:,ti],array[:,gi]; mask=np.isfinite(tau)&np.isfinite(g)&(tau>0); tau,g=tau[mask],g[mask]
    peaks=_direct_peak_indices(g)
    out["metrics"]={"linked_source_required":True,"time_constant_min_s":float(np.min(tau)) if len(tau) else None,
                    "time_constant_max_s":float(np.max(tau)) if len(tau) else None,
                    "direct_local_maxima":[{"tau_s":float(tau[k]),"gamma_ohm":float(g[k])} for k in peaks[:20]],
                    "unique_process_assignment_permitted":False}
    return out


def analyze_spatial(columns: Sequence[str], array: np.ndarray, identity: Mapping[str, Any]) -> dict[str, Any]:
    out=_base_analysis("spatial_electrochemistry_deterministic","SPATIAL_ELECTROCHEMISTRY",identity,columns,len(array))
    out["metrics"]={
        "canonical_point_identity":dict(identity),"coordinates_invented":False,
        "acquisition_order_preserved":True,"local_technique_required":True,
    }
    if not identity.get("canonical_key") and identity.get("point_index") is None:
        out["missing_metadata"].append("structured point identity")
    return out


def _record_identity(record: Mapping[str, Any], *, cycle: Any = None, sweep: Any = None) -> dict[str, Any]:
    ident=dict(record.get("identity") or {})
    meta=record.get("record_json") if isinstance(record.get("record_json"),Mapping) else {}
    def first(*keys):
        for key in keys:
            if meta.get(key) not in (None,""): return meta.get(key)
        return None
    ident.update({
        "source_path":record.get("source_path"),"dataset_path":record.get("dataset_path"),
        "stage_id":first("stage_id","stageId"),"stage_title":first("stage_title","title","experimental_title"),
        "cycle":cycle if cycle is not None else first("cycle","cycle_index"),
        "sweep":sweep,
        "spectrum_id":first("spectrum_id","spectrum"),
        "acquisition_order":first("acquisition_order","sequence_index","record_index"),
    })
    return ident


def analyze_electrochemical_record(record: Mapping[str, Any], *, dummy: bool, linked_eis_geis: bool) -> list[dict[str, Any]]:
    tech=str(record.get("technique") or "UNKNOWN").upper()
    columns=list(record.get("columns") or []); array=_as_numeric(record.get("array"))
    cycle_idx=_column_index(columns,("cycle","cycle_index"))
    groups=[(None,array)]
    if cycle_idx is not None and len(array):
        vals=array[:,cycle_idx]
        finite=sorted({int(v) for v in vals if np.isfinite(v)})
        if finite: groups=[(c,array[np.isfinite(vals)&(vals==c)]) for c in finite]
    results=[]
    for cycle, sub in groups:
        ident=_record_identity(record,cycle=cycle)
        if tech=="OCP": result=analyze_ocp(columns,sub,ident); result["route"]="ocp_deterministic"; result["technique"]="OCP"; result["routing_policy"]=dict(GENERAL_ROUTING_POLICY)
        elif tech in {"CV","LSV"}:
            result=analyze_dummy_cv(columns,sub,ident) if (dummy and tech=="CV") else analyze_cv_lsv(columns,sub,ident,tech)
        elif tech in {"CA","DEPOSITION"}: result=analyze_ca(columns,sub,ident,tech)
        elif tech=="CP": result=analyze_cp(columns,sub,ident)
        elif tech in {"EIS","GEIS"}:
            result=analyze_dummy_eis(columns,sub,ident) if dummy else analyze_eis_general(columns,sub,ident,tech)
        elif tech in {"DPV","SWV"}: result=analyze_pulse(columns,sub,ident,tech)
        elif tech=="STRIPPING": result=analyze_stripping(columns,sub,ident)
        elif tech=="DRT": result=analyze_drt(columns,sub,ident,linked_eis_geis)
        elif tech in {"SECCM","SPATIAL_ELECTROCHEMISTRY"}: result=analyze_spatial(columns,sub,ident)
        else:
            result=_base_analysis("unknown_electrochemistry_descriptive",tech,ident,columns,len(sub)); result["status"]="descriptive_only_unknown_technique"
        if tech in TECHNIQUE_ROUTE_NAMES:
            # Preserve a special diagnostic sub-route without changing the hard execution route.
            if result.get("route") not in {"dummy_cell_cv_direct", "dummy_cell_eis_exact_complex", "blocked_drt_without_linked_eis_geis"}:
                result["diagnostic_subroute"] = result.get("route")
                result["route"] = TECHNIQUE_ROUTE_NAMES[tech]
        result.setdefault("routing_policy",dict(GENERAL_ROUTING_POLICY))
        result.setdefault("generic_curve_fitting_used",False); result.setdefault("spectral_peak_fitting_used",False)
        result["run_analysis_allowed"] = False
        result["select_agent_allowed"] = False
        result["interpretation_only"] = True
        results.append(result)
        # Each CV sweep gets its own deterministic identity record.
        sweeps=((result.get("metrics") or {}).get("sweeps") if isinstance(result.get("metrics"),Mapping) else None)
        if isinstance(sweeps,list):
            for sw in sweeps:
                results.append({
                    **_base_analysis(result["route"],tech,_record_identity(record,cycle=cycle,sweep=sw.get("sweep_index")),columns,sw.get("rows") or 0),
                    "entity_kind":"sweep","parent_route":result["route"],"metrics":dict(sw),
                })
    return results


def _routing_conflicts(*objects: Any) -> list[dict[str, Any]]:
    conflicts=[]
    for obj in objects:
        for row in _iter_json_objects(obj):
            agent=str(row.get("agent") or row.get("selected_agent") or "")
            route=str(row.get("route") or "")
            if agent.lower()=="curvefittingagent" or route.lower()=="curvefit" or row.get("generic_curve_fitting_allowed") is True or row.get("curve_fitting_agent_required") is True:
                conflicts.append({"code":"ROUTING_CONFLICT_ELECTROCHEMISTRY","rejected":dict(row),"replacement":dict(GENERAL_ROUTING_POLICY)})
    return conflicts


def _iter_json_objects(obj: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(obj, Mapping):
        yield obj
        for value in obj.values():
            yield from _iter_json_objects(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_json_objects(value)


def _stage_inventory(paths: Sequence[Path]) -> list[dict[str, Any]]:
    stages: dict[str, dict[str, Any]] = {}
    for path in paths:
        if path.suffix.lower() != ".json" or not path.is_file() or path.stat().st_size > 20_000_000:
            continue
        obj = _json_load(path)
        for row in _iter_json_objects(obj):
            stage_id = row.get("stage_id", row.get("stageId", row.get("id") if "stage" in " ".join(map(str, row.keys())).lower() else None))
            title = row.get("experimental_title", row.get("stage_title", row.get("title", row.get("name"))))
            technique = row.get("technique")
            status = str(row.get("status", row.get("state", "")))
            if stage_id is None and not (title and technique):
                continue
            key = str(stage_id if stage_id is not None else f"{title}|{technique}")
            completed = status.strip().lower() in {"complete", "completed", "done", "success", "succeeded", "concluded"}
            if row.get("completed") is True:
                completed = True
            stages[key] = {
                "stage_id": stage_id, "experimental_title": title,
                "technique": technique, "status": status or None,
                "completed": completed, "source_json": str(path),
            }
    return sorted(stages.values(), key=lambda r: str(r.get("stage_id") or r.get("experimental_title") or ""))


def classify_transaction(prompt: str, request: Mapping[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    low = prompt.lower()
    selected = [Path(str(p)) for p in (request.get("selected_data_paths") or [])]
    tags: list[str] = []
    if any(p.suffix.lower() in {".h5", ".hdf5"} for p in selected): tags.append("hdf5")
    if "seccm" in low or any("seccm" in p.name.lower() for p in selected): tags.append("seccm")
    if settings.get("scilink_literature_analysis_enabled") or "literature" in low: tags.append("literature")
    if any(word in low for word in ("multimodal", "raman", "hyperspectral", "microscopy", "fluorescence", "holograph")): tags.append("multimodal")
    if any(word in low for word in ("next-or-conclude", "campaign review", "managed campaign", "completed stage")):
        primary = "campaign_review"
    elif any(word in low for word in ("post-analysis", "post analysis", "post-campaign", "post campaign")):
        primary = "post_analysis"
    elif any(word in low for word in ("follow-up", "follow up", "reply to", "continue the analysis")):
        primary = "follow_up"
    elif any(word in low for word in ("design an experiment", "experiment plan", "create a plan", "planning")):
        primary = "planning"
    else:
        primary = "manual_ask_scilink"
    return {"primary": primary, "tags": sorted(set(tags)), "all": [primary] + sorted(set(tags))}


def _collect_authorized_paths(request: Mapping[str, Any]) -> list[Path]:
    out: list[Path] = []
    for key in ("selected_data_paths", "supporting_file_paths", "metadata_paths"):
        for raw in request.get(key) or []:
            p = Path(str(raw))
            if p.is_file() and p not in out:
                out.append(p)
    return out



def _records_from_paths(paths: Sequence[Path], prompt: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    h5_reports: list[dict[str, Any]] = []
    for path in paths:
        if path.suffix.lower() in {".h5", ".hdf5"}:
            report = inspect_canonical_hdf5(path, include_arrays=True)
            h5_reports.append({k: v for k, v in report.items() if k != "records"})
            for item in report.get("records") or []:
                array = item.get("array", np.empty((0, 0)))
                columns = list(item.get("columns_json") or [])
                record_json = dict(item.get("record_json") or {})
                technique = _technique_from(columns, record_json, item.get("dataset_path", ""), prompt)
                technique = refine_cv_lsv_technique(columns, array, technique)
                records.append({
                    "source_path": str(path), "source_kind": "canonical_hdf5",
                    "dataset_path": item.get("dataset_path"), "columns": columns,
                    "array": _as_numeric(array), "record_json": record_json,
                    "identity": item.get("canonical_point_identity") or {}, "technique": technique,
                })
        elif path.suffix.lower() in {".csv", ".txt", ".tsv", ".asc", ".dat", ".mpt"}:
            try:
                columns, array, info = _read_delimited(path)
            except Exception as exc:
                columns, array, info = [], np.empty((0, 0)), {"error": str(exc)}
            record = {"name": path.name, **info}
            technique = _technique_from(columns, record, path.name, prompt)
            technique = refine_cv_lsv_technique(columns, array, technique)
            point_match = re.search(r"point[_-]?(\d+)|(?:^|[_-])p(\d{3})(?:[_-]|$)", path.name, re.I)
            point = int(next(x for x in point_match.groups() if x)) if point_match else None
            cycle_match = re.search(r"cycle[_-]?(\d+)", path.name, re.I)
            stage_match = re.search(r"stage[_-]?([A-Za-z0-9]+)", path.name, re.I)
            record.update({"cycle": int(cycle_match.group(1)) if cycle_match else None,
                           "stage_id": stage_match.group(1) if stage_match else None})
            records.append({
                "source_path": str(path), "source_kind": "table_file", "dataset_path": None,
                "columns": columns, "array": array, "record_json": record,
                "identity": {"point_index": point, "canonical_key": None, "coordinates": None, "coordinates_invented": False},
                "technique": technique,
            })
    return records, h5_reports



def prepare_job_routing(
    job_dir: str | Path,
    request: Mapping[str, Any],
    prompt: str,
    settings: Mapping[str, Any],
    skill_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    job = Path(job_dir).resolve()
    paths = _collect_authorized_paths(request)
    transaction = classify_transaction(prompt, request, settings)
    records, h5_reports = _records_from_paths(paths, prompt)
    dummy = _dummy_cell_context(prompt, [r.get("record_json") for r in records] + [_json_load(p) for p in paths if p.suffix.lower() == ".json"])
    techniques = sorted({str(r.get("technique") or "UNKNOWN") for r in records if r.get("technique")})
    linked_eis_geis = bool({"EIS", "GEIS"} & set(techniques))
    conflicts = _routing_conflicts(request, settings)
    routes: list[dict[str, Any]] = []
    deterministic: list[dict[str, Any]] = []
    for idx, record in enumerate(records, 1):
        analyses = analyze_electrochemical_record(record, dummy=dummy, linked_eis_geis=linked_eis_geis)
        deterministic.extend(analyses)
        route_name = analyses[0].get("route") if analyses else "unknown_electrochemistry_descriptive"
        routes.append({
            "record_index": idx, "source_path": record["source_path"], "dataset_path": record["dataset_path"],
            "technique": record["technique"], "route": route_name,
            "canonical_point_identity": record["identity"],
            **dict(GENERAL_ROUTING_POLICY),
            "route": route_name,
        })
    stages = _stage_inventory(paths)
    completed = [s for s in stages if s.get("completed")]
    unique_points = {str((r.get("canonical_point_identity") or {}).get("point_index")) for r in routes if (r.get("canonical_point_identity") or {}).get("point_index") is not None}
    unique_cycles = {str((a.get("identity") or {}).get("cycle")) for a in deterministic if (a.get("identity") or {}).get("cycle") is not None}
    unique_sweeps = {f"{(a.get('identity') or {}).get('source_path')}:{(a.get('identity') or {}).get('sweep')}" for a in deterministic if (a.get("identity") or {}).get("sweep") is not None}
    status = {
        "Electrochemistry skill": "Applied",
        "Detected technique": ", ".join(techniques) if techniques else "No recognized technique in authorized files",
        "Technique-specific analysis route": sorted({str(r.get("route")) for r in routes}),
        "Generic curve fitting": "Disabled",
        "Spectral peak fitting": "Disabled",
        "Deterministic analysis": "Completed" if records and deterministic else "No authorized electrochemistry records",
        "Files, stages, points or cycles analyzed": {
            "files": len({r.get("source_path") for r in records}), "records": len(records),
            "stages": len(stages), "completed_stages": len(completed), "points": len(unique_points),
            "cycles": len(unique_cycles), "sweeps": len(unique_sweeps), "analysis_records": len(deterministic),
        },
        "SciLink mode": "interpretation-only",
        "Generic CurveFittingAgent required": "no",
        "SciLink status": "Ready to start",
    }
    routing = {
        "schema": "faradaiq-electrochemistry-routing-v290", "version": VERSION,
        "application_version": VERSION, "application_semver": SEMVER,
        "created_at": _now(), "transaction_type": transaction,
        "general_routing_rule": dict(GENERAL_ROUTING_POLICY),
        "routing_conflicts": conflicts,
        "routing_conflict_code": "ROUTING_CONFLICT_ELECTROCHEMISTRY" if conflicts else None,
        "skill": {
            "name": skill_receipt.get("skill_name"), "version": skill_receipt.get("skill_version"),
            "path": skill_receipt.get("job_skill_path"), "sha256": skill_receipt.get("sha256"),
            "provenance_receipt": str(job / "scilink_skill_receipt.json"),
            "normal_cv_routing_contract": skill_receipt.get("normal_cv_routing_contract"),
            "electrodeposition_model_registry": skill_receipt.get("electrodeposition_model_registry"),
        },
        "authorized_file_count": len(paths), "electrochemical_record_count": len(records),
        "techniques_detected": techniques, "dummy_cell_detected": dummy,
        "dummy_cell_expected": dict(EXPECTED_DUMMY) if dummy else None,
        "routes": routes, "direct_analysis_count": len(deterministic),
        "requires_generic_curvefit": False,
        "generic_curve_fitting_allowed": False,
        "curve_fitting_agent_required": False,
        "spectral_peak_fitting_allowed": False,
        "best_of_n_allowed": False,
        "adaptive_refit_allowed": False,
        "generic_r2_gate_allowed": False,
        "deterministic_analysis_required": True,
        "run_analysis_allowed": False,
        "select_agent_allowed": False,
        "interpretation_only": True,
        "execution_guard": {
            "mode": "application_level_no_generic_tools",
            "blocked_tools": list(PROHIBITED_SCILINK_TOOLS),
            "blocked_attempt_code": "BLOCKED_GENERIC_ANALYSIS_FOR_ELECTROCHEMISTRY",
            "fallback_to_generic_analysis": False,
        },
        "prohibited_global_behaviors": [
            "CurveFittingAgent for recognized electrochemistry", "Gaussian fitting", "Lorentzian fitting",
            "Voigt fitting", "sech fitting", "arbitrary polynomial fitting", "Best-of-3", "Best-of-N",
            "adaptive refit", "annealing", "generic R2 acceptance gates", "automatic component insertion",
            "complex-to-real impedance casting", "generic spectroscopy terminology for electrochemistry",
        ],
        "retry_policy": {"dummy_cell_eis_deterministic_attempts": 1, "maximum_corrected_retries": 1, "maximum_total_attempts": 2},
        "drt_policy": {"linked_eis_or_geis_required": True, "eligible": linked_eis_geis},
        "hdf5": h5_reports,
        "seccm": {"structured_point_identity_required": True, "coordinates_may_be_invented": False,
                  "records_with_point_identity": len(unique_points), "acquisition_order_preserved": True},
        "campaign": {
            "stages": stages, "completed_stage_count": len(completed),
            "analyze_every_completed_stage": transaction["primary"] == "campaign_review",
            "preserve_stage_ids_and_titles": True, "repeat_completed_stage_on_generic_fit_failure": False,
            "conclusion_contract": {"experiment_plan": None, "campaign_status": "Concluded"},
        },
        "experiment_plan_policy": {"approved": False, "may_not_be_changed_by_analysis": True},
        "gui_status": status,
    }
    route_path = job / "electrochemistry_routing_plan.json"
    direct_path = job / "deterministic_electrochemistry_analysis.json"
    status_path = job / "electrochemistry_analysis_status.json"
    routing["routing_plan_path"] = str(route_path)
    routing["deterministic_analysis_path"] = str(direct_path)
    routing["gui_status_path"] = str(status_path)
    if records and not deterministic:
        raise RuntimeError("Deterministic electrochemistry analysis is empty; generic fallback is prohibited.")
    direct_payload = {
        "schema": "faradaiq-deterministic-electrochemistry-analysis-v290", "version": VERSION,
        "created_at": _now(), "raw_data_modified": False,
        **dict(GENERAL_ROUTING_POLICY),
        "general_routing_rule": dict(GENERAL_ROUTING_POLICY),
        "source_file_count": len({r.get("source_path") for r in records}),
        "analysis_record_count": len(deterministic), "analyses": deterministic,
    }
    direct_path.write_text(json.dumps(direct_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    route_path.write_text(json.dumps(routing, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return routing



def update_request_with_routing(path: str | Path, routing: Mapping[str, Any]) -> None:
    p = Path(path)
    payload = _json_load(p)
    if not isinstance(payload, MutableMapping):
        return
    payload["application_version"] = VERSION
    payload["transaction_type"] = routing.get("transaction_type")
    payload["electrochemistry_routing"] = {
        "plan_path": routing.get("routing_plan_path"),
        "deterministic_analysis_path": routing.get("deterministic_analysis_path"),
        "gui_status_path": routing.get("gui_status_path"),
        "techniques_detected": routing.get("techniques_detected"),
        "requires_generic_curvefit": False,
        "generic_curve_fitting_allowed": False,
        "curve_fitting_agent_required": False,
        "spectral_peak_fitting_allowed": False,
        "best_of_n_allowed": False,
        "adaptive_refit_allowed": False,
        "generic_r2_gate_allowed": False,
        "deterministic_analysis_required": True,
        "run_analysis_allowed": False,
        "select_agent_allowed": False,
        "interpretation_only": True,
        "blocked_tools": list(PROHIBITED_SCILINK_TOOLS),
        "routing_conflicts": routing.get("routing_conflicts"),
        "drt_eligible": (routing.get("drt_policy") or {}).get("eligible"),
    }
    if routing.get("routing_conflicts"):
        payload["routing_conflict"] = "ROUTING_CONFLICT_ELECTROCHEMISTRY"
    payload["agent"] = "SciLinkInterpretationOnly"
    payload["route"] = "technique_specific_deterministic_electrochemistry"
    payload["run_analysis_allowed"] = False
    payload["select_agent_allowed"] = False
    payload["interpretation_only"] = True
    payload["blocked_tools"] = list(PROHIBITED_SCILINK_TOOLS)
    payload["n_candidates"] = 1
    payload["best_of_n"] = False
    payload["adaptive_refit"] = False
    payload["approved"] = False
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")



def prompt_guard_text(routing: Mapping[str, Any]) -> str:
    route_path = str(routing.get("routing_plan_path") or "")
    direct_path = str(routing.get("deterministic_analysis_path") or "")
    return (
        "\n\n### FaradaIQ deterministic electrochemistry interpretation-only guard\n"
        "The deterministic electrochemistry analysis is complete.\n\n"
        "Do not call select_agent.\n"
        "Do not call run_analysis.\n"
        "Do not select CurveFittingAgent.\n"
        "Do not perform generic curve fitting.\n"
        "Do not fit Gaussian, Lorentzian, Voigt, sech, or arbitrary polynomial models.\n"
        "Do not use Best-of-N, adaptive refit, annealing, or generic R² gates.\n\n"
        f"Read the raw data, `{direct_path}`, `{route_path}`, metadata, and the bundled electrochemistry skill. "
        "Interpret the deterministic electrochemical results, compare conditions, identify limitations, and generate the SciLink HTML report. "
        "The application-level tool list excludes select_agent and run_analysis. Any attempted prohibited call must be logged as "
        "BLOCKED_GENERIC_ANALYSIS_FOR_ELECTROCHEMISTRY and must not trigger a generic fallback. Preserve raw paths, identities, units, provenance, and approved=false.\n"
    )

def write_validated_series_metadata(context_path: str | Path | None, job_dir: str | Path) -> dict[str, Any]:
    """Serialize series metadata only through json.dumps/json.loads and preserve exact keys."""
    job = Path(job_dir).resolve()
    source = Path(context_path).resolve(strict=True) if context_path else None
    context: Any = _json_load(source) if source is not None else {}
    series = context.get("series_metadata") if isinstance(context, Mapping) else None
    if not isinstance(series, Mapping):
        series = {}
    serialized = json.dumps(series, ensure_ascii=False)
    validated = json.loads(serialized)
    out = {
        "schema": "faradaiq-series-metadata-v290",
        "version": VERSION,
        "source_path": str(source) if source else None,
        "series_metadata": validated,
        "series_metadata_json": serialized,
        "json_validated": True,
    }
    path = job / "series_metadata.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    out["path"] = str(path)
    return out


def _contains_conclusion(obj: Any) -> bool:
    text = json.dumps(obj, default=str).lower()
    return any(term in text for term in ("concluded", "conclusion supported", "no further experiment", "goal is answered", "campaign complete"))


def _repeated_completed_plan(obj: Mapping[str, Any], completed_ids: set[str]) -> bool:
    plan = obj.get("experiment_plan")
    if not isinstance(plan, Mapping):
        return False
    ids = set()
    for row in _iter_json_objects(plan):
        sid = row.get("stage_id", row.get("stageId"))
        if sid is not None:
            ids.add(str(sid))
    return bool(ids) and ids <= completed_ids


def finalize_campaign_outputs(job_dir: str | Path) -> dict[str, Any]:
    job = Path(job_dir).resolve()
    routing = _json_load(job / "electrochemistry_routing_plan.json")
    if not isinstance(routing, Mapping) or (routing.get("transaction_type") or {}).get("primary") != "campaign_review":
        return {"status": "not_applicable"}
    completed_ids = {str(s.get("stage_id")) for s in (routing.get("campaign") or {}).get("stages", []) if s.get("completed") and s.get("stage_id") is not None}
    modified = []
    conclusion = False
    exclusions = {"scilink_request.json", "electrochemistry_routing_plan.json", "deterministic_electrochemistry_analysis.json", "scilink_skill_receipt.json"}
    for path in job.rglob("*.json"):
        if path.name in exclusions or path.stat().st_size > 20_000_000:
            continue
        obj = _json_load(path)
        if not isinstance(obj, MutableMapping):
            continue
        repeated = _repeated_completed_plan(obj, completed_ids)
        concluded = _contains_conclusion(obj)
        if repeated or concluded:
            obj["experiment_plan"] = None
            obj["campaign_status"] = "Concluded"
            obj["status"] = "Concluded"
            obj["approved"] = False
            obj["faradaiq_campaign_guard"] = {
                "version": VERSION, "repeated_completed_plan_removed": repeated,
                "conclusion_normalized": concluded, "completed_stage_ids": sorted(completed_ids),
            }
            path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
            modified.append(str(path))
            conclusion = True
    status = {
        "schema": "faradaiq-campaign-status-v290", "version": VERSION,
        "campaign_status": "Concluded" if conclusion else "Review complete",
        "experiment_plan": None if conclusion else "unchanged",
        "completed_stage_ids": sorted(completed_ids), "modified_outputs": modified,
        "managed_transaction_stopped": conclusion,
        "follow_up_chat_available": True, "created_at": _now(),
    }
    (job / "campaign_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return status


def finalize_job(job_dir: str | Path) -> dict[str, Any]:
    return finalize_campaign_outputs(job_dir)


def update_scilink_status(job_dir: str | Path, status: str) -> dict[str, Any]:
    """Update the GUI-readable electrochemistry status receipt without altering data."""
    job = Path(job_dir).resolve()
    path = job / "electrochemistry_analysis_status.json"
    payload = _json_load(path)
    if not isinstance(payload, MutableMapping):
        payload = {}
    payload["SciLink status"] = str(status)
    payload["updated_at"] = _now()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    route_path = job / "electrochemistry_routing_plan.json"
    route = _json_load(route_path)
    if isinstance(route, MutableMapping):
        gui = route.get("gui_status") if isinstance(route.get("gui_status"), MutableMapping) else {}
        gui["SciLink status"] = str(status)
        route["gui_status"] = gui
        route_path.write_text(json.dumps(route, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return dict(payload)

# ---- FaradaIQ Studio v291 public release identity ----
VERSION = "v292"
SEMVER = "0.291.0"
# ---- End FaradaIQ Studio v291 public release identity ----
