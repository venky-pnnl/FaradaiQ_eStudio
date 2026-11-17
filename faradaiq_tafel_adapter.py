"""FaradaIQ native Tafel analysis adapter.

This module implements a Python-native Tafel workflow for FaradaIQ Studio:

1. Classical/manual branch-specific Tafel slope fits.
2. Auto-linear Tafel slope search inspired by the PyPI ``tafel`` tool style
   of finding high-R² linear windows, without requiring the external package.
3. Taffit-style nonlinear Butler-Volmer fit to log10(|j|) near η = 0.
4. One combined comparison table plus normalized data, JSON, and figures.

Credit: the Taffit concept and Excel/VBA implementation are due to Joshua
Coduto and Johna Leddy, "Taffit: An Excel Tool for Fitting Tafel Data",
ACS Measurement Science Au (2025), DOI: 10.1021/acsmeasuresciau.5c00038.
FaradaIQ does not redistribute Taffit.xlsm; this module adopts the documented
algorithmic idea in Python for local selected IV/LSV data.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    from scipy import optimize, stats
except Exception:  # pragma: no cover - FaradaIQ requirements normally include scipy
    optimize = None
    stats = None

FARADAY_C_PER_MOL = 96485.3321
GAS_CONSTANT_J_MOL_K = 8.31446261815324

CREDIT_TEXT = (
    "Taffit-style nonlinear BV fit credit: Joshua Coduto and Johna Leddy, "
    "Taffit: An Excel Tool for Fitting Tafel Data, ACS Measurement Science Au "
    "2025, DOI 10.1021/acsmeasuresciau.5c00038. FaradaIQ implements a "
    "Python-native workflow inspired by the published algorithm and SI, not "
    "the Excel workbook itself. Auto-linear slope search is inspired by the "
    "PyPI tafel package's goal of automated Tafel slope extraction."
)


@dataclass
class TafelSettings:
    surface_area_cm2: float = 1.0
    n_electrons: int = 1
    temperature_K: float = 298.15
    eeq_V: Optional[float] = None
    current_adjust: bool = True
    fitting_window_mV: float = 120.0
    eta_scan_windows_mV: Tuple[float, ...] = (30.0, 60.0, 90.0, 120.0, 180.0, 240.0)
    manual_cathodic_range_V: Tuple[float, float] = (-0.180, -0.060)
    manual_anodic_range_V: Tuple[float, float] = (0.060, 0.180)
    auto_min_points: int = 8
    auto_min_abs_eta_V: float = 0.035
    auto_max_abs_eta_V: float = 0.250
    auto_min_window_V: float = 0.030
    auto_min_r2: float = 0.970
    stability_max_relative_std: float = 0.20
    bootstrap_samples: int = 100
    enable_bootstrap: bool = True
    local_slope_window_points: int = 11
    uncompensated_resistance_ohm: float = 0.0
    optimizer_mode: str = "Balanced"
    scan_branch_mode: str = "Whole trace / LSV"
    label: str = "FaradaIQ Tafel analysis"


@dataclass
class BranchFit:
    method: str
    branch: str
    slope_dec_per_V: Optional[float] = None
    tafel_slope_mV_dec: Optional[float] = None
    intercept_log10_j0: Optional[float] = None
    j0_A_cm2: Optional[float] = None
    r2: Optional[float] = None
    n_points: int = 0
    eta_min_V: Optional[float] = None
    eta_max_V: Optional[float] = None
    rmse: Optional[float] = None
    quality_score: Optional[float] = None
    quality_grade: str = ""
    stability_relative_std: Optional[float] = None
    slope_ci_low_mV_dec: Optional[float] = None
    slope_ci_high_mV_dec: Optional[float] = None
    log10_j0_ci_low: Optional[float] = None
    log10_j0_ci_high: Optional[float] = None
    warning: str = ""


@dataclass
class BVFit:
    method: str
    eta_window_mV: float
    j0_A_cm2: Optional[float] = None
    neg_log10_j0: Optional[float] = None
    log10_j0: Optional[float] = None
    alpha: Optional[float] = None
    cathodic_slope_mV_dec: Optional[float] = None
    anodic_slope_mV_dec: Optional[float] = None
    sigma: Optional[float] = None
    n_points: int = 0
    eta_min_V: Optional[float] = None
    eta_max_V: Optional[float] = None
    success: bool = False
    warning: str = ""


@dataclass
class TafelAnalysisResult:
    source_file: str
    output_dir: str
    normalized_csv: str
    comparison_csv: str
    eta_scan_csv: str
    summary_json: str
    stability_csv: str = ""
    figure_png: str = ""
    eeq_V: Optional[float] = None
    settings: Dict = field(default_factory=dict)
    comparison_rows: List[Dict] = field(default_factory=list)
    eta_scan_rows: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _safe_name(text: object, default: str = "tafel") -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or default)).strip("._")
    return s or default


def _read_text_table(path: Path) -> pd.DataFrame:
    path = Path(path)
    # Try flexible delimiter first; fall back through common delimiters.
    errors = []
    for kwargs in (
        dict(sep=None, engine="python", comment="#"),
        dict(sep=",", engine="python", comment="#"),
        dict(sep="\t", engine="python", comment="#"),
        dict(delim_whitespace=True, engine="python", comment="#"),
    ):
        try:
            df = pd.read_csv(path, **kwargs)
            if df.shape[1] >= 2:
                df = df.dropna(axis=1, how="all")
                # Remove unnamed empty columns.
                df = df.loc[:, [c for c in df.columns if not str(c).lower().startswith("unnamed") or df[c].notna().any()]]
                if df.shape[1] >= 2:
                    return df
        except Exception as exc:
            errors.append(str(exc))
    # Headerless fallback.
    try:
        arr = np.loadtxt(str(path), comments="#", delimiter=None)
        if arr.ndim == 1:
            arr = arr.reshape(-1, 2)
        cols = [f"col{i+1}" for i in range(arr.shape[1])]
        return pd.DataFrame(arr, columns=cols)
    except Exception as exc:
        errors.append(str(exc))
    raise ValueError(f"Could not read numeric IV/LSV table {path}: {' | '.join(errors[-3:])}")


def _numeric_columns(df: pd.DataFrame) -> List[str]:
    out = []
    for c in df.columns:
        vals = pd.to_numeric(df[c], errors="coerce")
        if vals.notna().sum() >= max(3, int(0.4 * len(df))):
            out.append(str(c))
    return out


def _score_column(name: str, role: str) -> int:
    n = name.lower().strip()
    score = 0
    if role == "potential":
        positive = ["potential", "ewe", "e_we", "e vs", "e/v", "voltage", "overpotential", "eta", "η", "e"]
        negative = ["current", "time", "cycle", "scan", "zreal", "zimag", "freq"]
    else:
        positive = ["current", "i/", "i (", "<i>", "j/", "j (", "density", "amp", "ma", "ua", "na"]
        negative = ["potential", "voltage", "time", "cycle", "freq", "zreal", "zimag"]
    for i, tok in enumerate(positive):
        if tok in n:
            score += 100 - i
    for tok in negative:
        if tok in n:
            score -= 90
    # Single-letter columns are common in hand exports.
    if role == "potential" and n in {"e", "v", "x", "potential_v", "e_v"}:
        score += 80
    if role == "current" and n in {"i", "j", "y", "current_a", "i_a"}:
        score += 80
    return score


def _detect_columns(df: pd.DataFrame) -> Tuple[str, str, List[str]]:
    nums = _numeric_columns(df)
    warnings: List[str] = []
    if len(nums) < 2:
        raise ValueError("Need at least two numeric columns for IV/LSV Tafel analysis.")
    pot_scores = sorted((( _score_column(c, "potential"), c) for c in nums), reverse=True)
    cur_scores = sorted((( _score_column(c, "current"), c) for c in nums), reverse=True)
    pot_col = pot_scores[0][1]
    cur_col = next((c for _, c in cur_scores if c != pot_col), nums[1] if nums[0] == pot_col else nums[0])
    if pot_scores[0][0] <= 0:
        pot_col = nums[0]
        warnings.append(f"Potential column was not obvious; using first numeric column: {pot_col}")
    if _score_column(cur_col, "current") <= 0:
        fallback = nums[1] if nums[0] == pot_col else nums[0]
        cur_col = fallback
        warnings.append(f"Current column was not obvious; using numeric column: {cur_col}")
    return pot_col, cur_col, warnings


def _unit_scale_from_name(name: str, role: str, values: np.ndarray) -> float:
    n = name.lower()
    if role == "potential":
        if "mv" in n or np.nanmedian(np.abs(values)) > 5:
            return 1e-3
        return 1.0
    # current units -> A
    if "micro" in n or "µa" in n or "μa" in n or "ua" in n:
        return 1e-6
    if "ma" in n:
        return 1e-3
    if "na" in n:
        return 1e-9
    if "pa" in n and "potential" not in n:
        return 1e-12
    # Heuristic: very large absolute currents in generic columns are probably mA/uA only if labels are absent.
    return 1.0


def normalize_iv_lsv(path: Path, settings: TafelSettings = TafelSettings()) -> Tuple[pd.DataFrame, List[str], str, str]:
    df0 = _read_text_table(Path(path))
    pot_col, cur_col, warnings = _detect_columns(df0)
    pot_raw = pd.to_numeric(df0[pot_col], errors="coerce").to_numpy(dtype=float)
    cur_raw = pd.to_numeric(df0[cur_col], errors="coerce").to_numpy(dtype=float)
    ok = np.isfinite(pot_raw) & np.isfinite(cur_raw)
    pot_raw = pot_raw[ok]
    cur_raw = cur_raw[ok]
    if len(pot_raw) < 8:
        raise ValueError(f"Too few numeric IV/LSV points in {path}; found {len(pot_raw)}.")
    pscale = _unit_scale_from_name(pot_col, "potential", pot_raw)
    iscale = _unit_scale_from_name(cur_col, "current", cur_raw)
    E_V = pot_raw * pscale
    I_A = cur_raw * iscale
    ru = float(getattr(settings, "uncompensated_resistance_ohm", 0.0) or 0.0)
    if np.isfinite(ru) and abs(ru) > 0:
        E_V = E_V - I_A * ru
        warnings.append(f"Applied post-hoc iR compensation: E_corrected = E_measured - I*{ru:.6g} ohm.")
    area = float(settings.surface_area_cm2 or 1.0)
    if not np.isfinite(area) or area <= 0:
        warnings.append("Invalid surface area; using 1 cm^2.")
        area = 1.0
    j = I_A / area
    if settings.eeq_V is None:
        # Match the Taffit idea: auto-detect the minimum current / Tafel minimum as η = 0.
        finite = np.isfinite(j)
        idx = int(np.nanargmin(np.abs(j[finite])))
        finite_idx = np.arange(len(j))[finite][idx]
        eeq = float(E_V[finite_idx])
        warnings.append(f"Eeq/OCP auto-detected from minimum |current|: {eeq:.6g} V")
    else:
        eeq = float(settings.eeq_V)
    eta = E_V - eeq
    if settings.current_adjust and settings.eeq_V is not None:
        idx0 = int(np.nanargmin(np.abs(eta)))
        j = j - float(j[idx0])
        warnings.append(f"Current adjusted by subtracting j at nearest Eeq: {float(j[idx0]):.6g} A/cm^2 after adjustment reference.")
    absj = np.abs(j)
    floor = max(1e-30, np.nanmax(absj) * 1e-15 if np.nanmax(absj) > 0 else 1e-30)
    logj = np.log10(np.clip(absj, floor, None))
    out = pd.DataFrame({
        "acquisition_index": np.arange(len(E_V), dtype=int),
        "E_V": E_V,
        "eta_V": eta,
        "current_A": I_A,
        "j_A_cm2": j,
        "abs_j_A_cm2": absj,
        "log10_abs_j_A_cm2": logj,
    })
    out = out.replace([np.inf, -np.inf], np.nan).dropna()
    out = out.sort_values("eta_V").reset_index(drop=True)
    return out, warnings, pot_col, cur_col


def _linregress(x: np.ndarray, y: np.ndarray) -> Optional[Tuple[float, float, float]]:
    """Return a finite linear regression or ``None`` for a degenerate window.

    Local Tafel diagnostics intentionally scan many overlapping windows.  Flat
    or quantized log-current windows are valid data situations, not fatal
    analysis errors, so they must be skipped rather than allowed to escape from
    SciPy's ``linregress``.
    """
    x = np.asarray(x, dtype=float).reshape(-1)
    y = np.asarray(y, dtype=float).reshape(-1)
    finite = np.isfinite(x) & np.isfinite(y)
    x = x[finite]
    y = y[finite]
    if len(x) < 3:
        return None
    # np.std alone can be misleading for extremely small floating-point ranges.
    # Explicitly reject exact/near-constant windows before calling scipy.
    x_scale = max(1.0, float(np.nanmax(np.abs(x))))
    y_scale = max(1.0, float(np.nanmax(np.abs(y))))
    x_span = float(np.nanmax(x) - np.nanmin(x))
    y_span = float(np.nanmax(y) - np.nanmin(y))
    if x_span <= np.finfo(float).eps * x_scale * 16.0:
        return None
    if y_span <= np.finfo(float).eps * y_scale * 16.0:
        return None
    if stats is not None:
        try:
            r = stats.linregress(x, y)
        except (ValueError, FloatingPointError):
            return None
        vals = (float(r.slope), float(r.intercept), float(r.rvalue) ** 2)
        return vals if all(np.isfinite(vals)) else None
    # Small fallback if scipy is unavailable.
    try:
        slope, intercept = np.polyfit(x, y, 1)
    except (ValueError, np.linalg.LinAlgError, FloatingPointError):
        return None
    pred = slope * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot else 0.0
    vals = (float(slope), float(intercept), float(r2))
    return vals if all(np.isfinite(vals)) else None


def _branch_fit(df: pd.DataFrame, branch: str, eta_range: Tuple[float, float], method: str) -> BranchFit:
    lo, hi = sorted([float(eta_range[0]), float(eta_range[1])])
    sub = df[(df["eta_V"] >= lo) & (df["eta_V"] <= hi)].copy()
    if len(sub) < 3:
        return BranchFit(method=method, branch=branch, n_points=len(sub), eta_min_V=lo, eta_max_V=hi, warning="too few points")
    x = sub["eta_V"].to_numpy(float)
    y = sub["log10_abs_j_A_cm2"].to_numpy(float)
    lr = _linregress(x, y)
    if lr is None:
        return BranchFit(method=method, branch=branch, n_points=len(sub), eta_min_V=float(np.min(x)), eta_max_V=float(np.max(x)), warning="linear regression failed")
    slope, intercept, r2 = lr
    tafel = 1000.0 / slope if abs(slope) > 1e-30 else np.nan
    return BranchFit(
        method=method,
        branch=branch,
        slope_dec_per_V=slope,
        tafel_slope_mV_dec=float(tafel),
        intercept_log10_j0=intercept,
        j0_A_cm2=float(10 ** intercept),
        r2=r2,
        n_points=len(sub),
        eta_min_V=float(np.min(x)),
        eta_max_V=float(np.max(x)),
    )


def classical_manual_fits(df: pd.DataFrame, settings: TafelSettings) -> List[BranchFit]:
    return [
        _branch_fit(df, "cathodic", settings.manual_cathodic_range_V, "Classical/manual linear"),
        _branch_fit(df, "anodic", settings.manual_anodic_range_V, "Classical/manual linear"),
    ]


def _prefix_regression_arrays(x: np.ndarray, y: np.ndarray):
    """Return cumulative moments used for O(1) regression of contiguous windows."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    return (
        np.concatenate(([0.0], np.cumsum(x))),
        np.concatenate(([0.0], np.cumsum(y))),
        np.concatenate(([0.0], np.cumsum(x * x))),
        np.concatenate(([0.0], np.cumsum(y * y))),
        np.concatenate(([0.0], np.cumsum(x * y))),
    )


def _prefix_window_regression(prefix, i: int, j: int) -> Optional[Tuple[float, float, float, float]]:
    """Slope, intercept, R2 and RMSE for [i:j] without repeated scipy calls."""
    sx, sy, sxx, syy, sxy = prefix
    n = int(j - i)
    if n < 3:
        return None
    xsum = float(sx[j] - sx[i]); ysum = float(sy[j] - sy[i])
    xx = float(sxx[j] - sxx[i]); yy = float(syy[j] - syy[i]); xy = float(sxy[j] - sxy[i])
    denom = n * xx - xsum * xsum
    if abs(denom) <= 1e-30:
        return None
    slope = (n * xy - xsum * ysum) / denom
    intercept = (ysum - slope * xsum) / n
    # SSE from sufficient statistics.
    sse = yy + slope * slope * xx + n * intercept * intercept + 2.0 * slope * intercept * xsum - 2.0 * slope * xy - 2.0 * intercept * ysum
    sse = max(0.0, float(sse))
    sst = yy - (ysum * ysum / n)
    r2 = 1.0 - sse / sst if sst > 1e-30 else 0.0
    rmse = math.sqrt(sse / n)
    return float(slope), float(intercept), float(r2), float(rmse)


def _candidate_window_lengths(n: int, min_pts: int, mode: str = "Balanced") -> List[int]:
    """Bound the search cost while preserving short and broad kinetic windows."""
    if n <= min_pts:
        return [n]
    mode_l = str(mode or "Balanced").lower()
    target = 12 if mode_l.startswith("fast") else 20 if mode_l.startswith("balanced") else 34
    dense_end = min(n, min_pts + 10)
    lengths = set(range(min_pts, dense_end + 1))
    for value in np.linspace(min_pts, n, target):
        lengths.add(max(min_pts, min(n, int(round(value)))))
    return sorted(lengths)


def _window_candidates(pool: pd.DataFrame, desired_sign: int, settings: TafelSettings, *, require_r2: bool) -> List[Dict]:
    xall = pool["eta_V"].to_numpy(float)
    yall = pool["log10_abs_j_A_cm2"].to_numpy(float)
    n = len(pool); min_pts = max(5, int(settings.auto_min_points))
    if n < min_pts:
        return []
    prefix = _prefix_regression_arrays(xall, yall)
    lengths = _candidate_window_lengths(n, min_pts, getattr(settings, "optimizer_mode", "Balanced"))
    candidates: List[Dict] = []
    # For very dense records, stride the start position while always retaining edge windows.
    max_starts = 180 if str(getattr(settings, "optimizer_mode", "Balanced")).lower().startswith("fast") else 320
    for length in lengths:
        possible = n - length + 1
        stride = max(1, int(math.ceil(possible / max_starts)))
        starts = list(range(0, possible, stride))
        if starts[-1] != possible - 1:
            starts.append(possible - 1)
        for i in starts:
            j = i + length
            span = float(xall[j - 1] - xall[i])
            if span < float(settings.auto_min_window_V):
                continue
            lr = _prefix_window_regression(prefix, i, j)
            if lr is None:
                continue
            slope, intercept, r2, rmse = lr
            if desired_sign * slope <= 0:
                continue
            if require_r2 and r2 < float(settings.auto_min_r2):
                continue
            candidates.append({
                "i": i, "j": j, "x": xall[i:j], "y": yall[i:j],
                "slope": slope, "intercept": intercept, "r2": r2,
                "span": span, "rmse": rmse,
                "center": float((xall[i] + xall[j - 1]) / 2.0),
            })
    return candidates


def _auto_branch_search(df: pd.DataFrame, branch: str, settings: TafelSettings) -> BranchFit:
    if branch == "cathodic":
        pool = df[(df["eta_V"] <= -settings.auto_min_abs_eta_V) & (df["eta_V"] >= -settings.auto_max_abs_eta_V)].copy()
        desired_sign = -1
    else:
        pool = df[(df["eta_V"] >= settings.auto_min_abs_eta_V) & (df["eta_V"] <= settings.auto_max_abs_eta_V)].copy()
        desired_sign = 1
    pool = pool.sort_values("eta_V").reset_index(drop=True)
    n = len(pool)
    if n < settings.auto_min_points:
        return BranchFit(method="Auto-linear R2 search", branch=branch, n_points=n, warning="too few branch points")
    candidates = _window_candidates(pool, desired_sign, settings, require_r2=False)
    if not candidates:
        return BranchFit(method="Auto-linear R2 search", branch=branch, n_points=n, warning="no linear segment passed sign/window filters")
    for c in candidates:
        c["score"] = (max(0.0, c["r2"]) ** 3) * (len(c["x"]) ** 0.35) * (1.0 + 0.5 * min(c["span"] / 0.15, 1.0)) / (1.0 + 4.0 * c["rmse"])
    best = max(candidates, key=lambda z: z["score"])
    return BranchFit(
        method="Auto-linear R2 search", branch=branch,
        slope_dec_per_V=best["slope"], tafel_slope_mV_dec=float(1000.0 / best["slope"]),
        intercept_log10_j0=best["intercept"], j0_A_cm2=float(10 ** best["intercept"]),
        r2=best["r2"], n_points=len(best["x"]),
        eta_min_V=float(np.min(best["x"])), eta_max_V=float(np.max(best["x"])), rmse=best["rmse"],
    )

def auto_linear_fits(df: pd.DataFrame, settings: TafelSettings) -> List[BranchFit]:
    return [_auto_branch_search(df, "cathodic", settings), _auto_branch_search(df, "anodic", settings)]


def _bootstrap_linear_ci(x: np.ndarray, y: np.ndarray, samples: int, seed: int = 234) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    if samples <= 0 or len(x) < 4:
        return None, None, None, None
    rng = np.random.default_rng(seed)
    slopes, intercepts = [], []
    for _ in range(int(samples)):
        idx = rng.integers(0, len(x), len(x))
        lr = _linregress(x[idx], y[idx])
        if lr is None:
            continue
        slopes.append(float(lr[0])); intercepts.append(float(lr[1]))
    if len(slopes) < max(10, int(samples * 0.2)):
        return None, None, None, None
    slopes = np.asarray(slopes, float)
    valid = np.abs(slopes) > 1e-30
    tafel = 1000.0 / slopes[valid]
    return (
        float(np.nanpercentile(tafel, 2.5)), float(np.nanpercentile(tafel, 97.5)),
        float(np.nanpercentile(intercepts, 2.5)), float(np.nanpercentile(intercepts, 97.5)),
    )


def _quality_from_fit(r2: float, rmse: float, n_points: int, span: float, stability: float) -> Tuple[float, str]:
    score = 100.0
    score -= max(0.0, 0.995 - float(r2)) * 900.0
    score -= min(30.0, max(0.0, float(rmse)) * 220.0)
    if n_points < 8: score -= 25.0
    elif n_points < 12: score -= 10.0
    if span < 0.04: score -= 25.0
    elif span < 0.06: score -= 10.0
    score -= min(30.0, max(0.0, float(stability)) * 100.0)
    score = float(max(0.0, min(100.0, score)))
    grade = "Excellent" if score >= 90 else "Good" if score >= 75 else "Questionable" if score >= 55 else "Unsuitable"
    return score, grade


def _stable_branch_search(df: pd.DataFrame, branch: str, settings: TafelSettings) -> BranchFit:
    if branch == "cathodic":
        pool = df[(df["eta_V"] <= -settings.auto_min_abs_eta_V) & (df["eta_V"] >= -settings.auto_max_abs_eta_V)].copy()
        desired_sign = -1
    else:
        pool = df[(df["eta_V"] >= settings.auto_min_abs_eta_V) & (df["eta_V"] <= settings.auto_max_abs_eta_V)].copy()
        desired_sign = 1
    pool = pool.sort_values("eta_V").reset_index(drop=True)
    candidates = _window_candidates(pool, desired_sign, settings, require_r2=True)
    if not candidates:
        return BranchFit(method="Stable-window Tafel fit", branch=branch, n_points=len(pool), warning="no stable linear window passed R2/sign/span filters")
    # Estimate stability only from nearby overlapping candidates. This preserves
    # the window-scan concept without the former O(candidate^2) GUI freeze.
    ordered = sorted(range(len(candidates)), key=lambda k: candidates[k]["center"])
    centers = np.asarray([candidates[k]["center"] for k in ordered], float)
    for pos, idx in enumerate(ordered):
        c = candidates[idx]
        radius = max(0.012, c["span"] * 0.45)
        lo = int(np.searchsorted(centers, c["center"] - radius, side="left"))
        hi = int(np.searchsorted(centers, c["center"] + radius, side="right"))
        # Limit to closest 24 windows for predictable performance.
        near_ids = ordered[max(lo, pos - 12):min(hi, pos + 13)]
        slopes = []
        for qidx in near_ids:
            q = candidates[qidx]
            overlap = max(0, min(c["j"], q["j"]) - max(c["i"], q["i"]))
            if overlap >= max(3, int(0.35 * min(c["j"] - c["i"], q["j"] - q["i"]))):
                slopes.append(q["slope"])
        arr = np.asarray(slopes, float)
        rel_std = float(np.nanstd(arr) / max(abs(np.nanmedian(arr)), 1e-30)) if len(arr) > 1 else 0.0
        c["stability"] = rel_std
        c["score"] = (c["r2"] ** 6) * (len(c["x"]) ** 0.35) * (1 + min(c["span"] / 0.15, 1.0)) * math.exp(-4.0 * rel_std) / (1 + 6 * c["rmse"])
    best = max(candidates, key=lambda z: z["score"])
    qscore, grade = _quality_from_fit(best["r2"], best["rmse"], len(best["x"]), best["span"], best["stability"])
    ci = (None, None, None, None)
    if bool(getattr(settings, "enable_bootstrap", True)):
        ci = _bootstrap_linear_ci(best["x"], best["y"], int(getattr(settings, "bootstrap_samples", 100) or 0))
    warning = ""
    if best["stability"] > float(settings.stability_max_relative_std):
        warning = f"slope is not stable across overlapping windows (relative std {best['stability']:.3g})"
    return BranchFit(
        method="Stable-window Tafel fit", branch=branch,
        slope_dec_per_V=best["slope"], tafel_slope_mV_dec=float(1000.0 / best["slope"]),
        intercept_log10_j0=best["intercept"], j0_A_cm2=float(10 ** best["intercept"]),
        r2=best["r2"], n_points=len(best["x"]),
        eta_min_V=float(np.min(best["x"])), eta_max_V=float(np.max(best["x"])),
        rmse=best["rmse"], quality_score=qscore, quality_grade=grade,
        stability_relative_std=best["stability"], slope_ci_low_mV_dec=ci[0], slope_ci_high_mV_dec=ci[1],
        log10_j0_ci_low=ci[2], log10_j0_ci_high=ci[3], warning=warning,
    )

def stable_window_fits(df: pd.DataFrame, settings: TafelSettings) -> List[BranchFit]:
    return [_stable_branch_search(df, "cathodic", settings), _stable_branch_search(df, "anodic", settings)]


def local_slope_diagnostics(df: pd.DataFrame, settings: TafelSettings) -> pd.DataFrame:
    rows=[]
    win=max(5,int(getattr(settings,'local_slope_window_points',11) or 11))
    if win%2==0: win+=1
    half=win//2
    for branch, sub in (("cathodic",df[df['eta_V']<0]),("anodic",df[df['eta_V']>0])):
        sub=sub.sort_values('log10_abs_j_A_cm2').reset_index(drop=True)
        x=sub['log10_abs_j_A_cm2'].to_numpy(float); y=sub['eta_V'].to_numpy(float)
        for i in range(len(sub)):
            a=max(0,i-half); b=min(len(sub),i+half+1)
            if b-a<5: continue
            lr=_linregress(x[a:b],y[a:b])
            if lr is None: continue
            slope_v_dec,intercept,r2=lr
            rows.append({'branch':branch,'eta_V':float(y[i]),'log10_abs_j_A_cm2':float(x[i]),'local_tafel_slope_mV_dec':float(1000*slope_v_dec),'local_r2':float(r2),'window_points':int(b-a)})
    return pd.DataFrame(rows)


def _bv_current_density(eta: np.ndarray, log10_j0: float, alpha: float, n: int, T: float) -> np.ndarray:
    # Same sign convention used by the SI VBA formula for log(abs(j0 * (exp(-alpha nF eta/RT) - exp((1-alpha)nF eta/RT))))).
    j0 = 10.0 ** log10_j0
    f = FARADAY_C_PER_MOL / (GAS_CONSTANT_J_MOL_K * float(T))
    a1 = np.clip(-float(alpha) * int(n) * f * eta, -700, 700)
    a2 = np.clip((1.0 - float(alpha)) * int(n) * f * eta, -700, 700)
    return j0 * (np.exp(a1) - np.exp(a2))


def taffit_style_bv_fit(df: pd.DataFrame, settings: TafelSettings, window_mV: Optional[float] = None) -> BVFit:
    win = float(settings.fitting_window_mV if window_mV is None else window_mV)
    half = win / 1000.0 / 2.0
    sub = df[(np.abs(df["eta_V"]) <= half) & (df["abs_j_A_cm2"] > 0)].copy()
    sub = sub[np.abs(sub["eta_V"]) > 1e-9]
    if len(sub) < 5:
        return BVFit(method="Taffit-style nonlinear BV", eta_window_mV=win, n_points=len(sub), warning="too few points inside eta window")
    eta = sub["eta_V"].to_numpy(float)
    y = sub["log10_abs_j_A_cm2"].to_numpy(float)
    finite = np.isfinite(eta) & np.isfinite(y)
    eta = eta[finite]
    y = y[finite]
    if len(eta) < 5:
        return BVFit(method="Taffit-style nonlinear BV", eta_window_mV=win, n_points=len(eta), warning="too few finite points")
    ymin, ymax = float(np.min(y)), float(np.max(y))
    lo = max(-30.0, ymin - 4.0)
    hi = min(5.0, ymax + 2.0)
    if hi <= lo:
        lo, hi = -15.0, 1.0

    def residual(par: Sequence[float]) -> np.ndarray:
        logj0, alpha = float(par[0]), float(par[1])
        pred_j = np.abs(_bv_current_density(eta, logj0, alpha, settings.n_electrons, settings.temperature_K))
        floor = 10.0 ** (lo - 6.0)
        pred = np.log10(np.clip(pred_j, floor, None))
        return pred - y

    try:
        if optimize is None:
            raise RuntimeError("scipy.optimize unavailable")
        mode = str(getattr(settings, "optimizer_mode", "Balanced") or "Balanced").lower()
        bounds_lo = [lo, 1e-6]; bounds_hi = [hi, 0.999999]
        if mode.startswith("thorough"):
            de = optimize.differential_evolution(
                lambda p: float(np.mean(residual(p) ** 2)),
                bounds=[(lo, hi), (1e-6, 0.999999)], tol=1e-7, polish=False,
                seed=12345, updating="immediate", workers=1, maxiter=60,
            )
            starts = [de.x]
            max_nfev = 1600
        else:
            guess_logj0 = float(np.clip(np.nanpercentile(y, 20) - 0.6, lo, hi))
            starts = [[guess_logj0, a] for a in ((0.5,) if mode.startswith("fast") else (0.25, 0.5, 0.75))]
            max_nfev = 300 if mode.startswith("fast") else 900
            de = None
        best_ls = None
        best_ss = None
        for start in starts:
            ls_try = optimize.least_squares(residual, start, bounds=(bounds_lo, bounds_hi), max_nfev=max_nfev)
            ss = float(np.mean(residual(ls_try.x) ** 2))
            if best_ss is None or ss < best_ss:
                best_ss, best_ls = ss, ls_try
        if best_ls is None:
            raise RuntimeError("least-squares optimizer produced no result")
        ls = best_ls
        logj0, alpha = float(ls.x[0]), float(ls.x[1])
        res = residual([logj0, alpha])
        denom = max(1, len(res) - 2)
        sigma = float(math.sqrt(float(np.sum(res ** 2)) / denom))
        success = bool(ls.success or (de is not None and de.success))
        warning = "" if success else str(getattr(ls, "message", "optimizer did not report success"))
    except Exception as exc:
        # Last-resort coarse grid.
        warning = f"optimizer fallback: {exc}"
        best = None
        for logj0 in np.linspace(lo, hi, 80):
            for alpha in np.linspace(0.05, 0.95, 50):
                r = residual([logj0, alpha])
                ss = float(np.mean(r ** 2))
                if best is None or ss < best[0]:
                    best = (ss, logj0, alpha, r)
        if best is None:
            return BVFit(method="Taffit-style nonlinear BV", eta_window_mV=win, n_points=len(eta), warning="BV fit failed")
        _, logj0, alpha, res = best
        denom = max(1, len(res) - 2)
        sigma = float(math.sqrt(float(np.sum(res ** 2)) / denom))
        success = False
    j0 = float(10.0 ** logj0)
    f = FARADAY_C_PER_MOL / (GAS_CONSTANT_J_MOL_K * float(settings.temperature_K))
    cath = -1000.0 * 2.303 / (float(alpha) * int(settings.n_electrons) * f) if alpha > 0 else None
    anod = 1000.0 * 2.303 / ((1.0 - float(alpha)) * int(settings.n_electrons) * f) if alpha < 1 else None
    warn2 = []
    if alpha <= 0.01 or alpha >= 0.99:
        warn2.append("alpha at/near constraint; consider changing n or eta window")
    if sigma > 0.1:
        warn2.append("sigma > 0.1; visually inspect fit and eta window")
    if warning:
        warn2.append(warning)
    return BVFit(
        method="Taffit-style nonlinear BV",
        eta_window_mV=win,
        j0_A_cm2=j0,
        neg_log10_j0=-logj0,
        log10_j0=logj0,
        alpha=float(alpha),
        cathodic_slope_mV_dec=float(cath) if cath is not None else None,
        anodic_slope_mV_dec=float(anod) if anod is not None else None,
        sigma=sigma,
        n_points=len(eta),
        eta_min_V=float(np.min(eta)),
        eta_max_V=float(np.max(eta)),
        success=success,
        warning="; ".join(warn2),
    )


def _branch_rows(fits: Iterable[BranchFit]) -> List[Dict]:
    rows = []
    for f in fits:
        rows.append({
            "method": f.method,
            "branch": f.branch,
            "slope_dec_per_V": f.slope_dec_per_V,
            "tafel_slope_mV_dec": f.tafel_slope_mV_dec,
            "log10_j0_A_cm2": f.intercept_log10_j0,
            "j0_A_cm2": f.j0_A_cm2,
            "alpha": None,
            "sigma": None,
            "r2": f.r2,
            "n_points": f.n_points,
            "eta_window_mV": None,
            "eta_min_V": f.eta_min_V,
            "eta_max_V": f.eta_max_V,
            "rmse": f.rmse,
            "quality_score": f.quality_score,
            "quality_grade": f.quality_grade,
            "stability_relative_std": f.stability_relative_std,
            "slope_ci_low_mV_dec": f.slope_ci_low_mV_dec,
            "slope_ci_high_mV_dec": f.slope_ci_high_mV_dec,
            "log10_j0_ci_low": f.log10_j0_ci_low,
            "log10_j0_ci_high": f.log10_j0_ci_high,
            "warning": f.warning,
        })
    return rows


def _bv_rows(fits: Iterable[BVFit]) -> List[Dict]:
    rows = []
    for f in fits:
        # One compact summary row plus branch-specific slope rows.
        rows.append({
            "method": f.method,
            "branch": "both",
            "slope_dec_per_V": None,
            "tafel_slope_mV_dec": None,
            "log10_j0_A_cm2": f.log10_j0,
            "j0_A_cm2": f.j0_A_cm2,
            "neg_log10_j0": f.neg_log10_j0,
            "alpha": f.alpha,
            "sigma": f.sigma,
            "r2": None,
            "n_points": f.n_points,
            "eta_window_mV": f.eta_window_mV,
            "eta_min_V": f.eta_min_V,
            "eta_max_V": f.eta_max_V,
            "success": f.success,
            "warning": f.warning,
        })
        rows.append({
            "method": f.method,
            "branch": "cathodic",
            "tafel_slope_mV_dec": f.cathodic_slope_mV_dec,
            "log10_j0_A_cm2": f.log10_j0,
            "j0_A_cm2": f.j0_A_cm2,
            "neg_log10_j0": f.neg_log10_j0,
            "alpha": f.alpha,
            "sigma": f.sigma,
            "n_points": f.n_points,
            "eta_window_mV": f.eta_window_mV,
            "eta_min_V": f.eta_min_V,
            "eta_max_V": f.eta_max_V,
            "success": f.success,
            "warning": f.warning,
        })
        rows.append({
            "method": f.method,
            "branch": "anodic",
            "tafel_slope_mV_dec": f.anodic_slope_mV_dec,
            "log10_j0_A_cm2": f.log10_j0,
            "j0_A_cm2": f.j0_A_cm2,
            "neg_log10_j0": f.neg_log10_j0,
            "alpha": f.alpha,
            "sigma": f.sigma,
            "n_points": f.n_points,
            "eta_window_mV": f.eta_window_mV,
            "eta_min_V": f.eta_min_V,
            "eta_max_V": f.eta_max_V,
            "success": f.success,
            "warning": f.warning,
        })
    return rows


def _write_figure(df: pd.DataFrame, output_png: Path, settings: TafelSettings, bv_fit: Optional[BVFit], branch_fits: Sequence[BranchFit]) -> str:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7.5, 5.2))
        ax.plot(df["eta_V"].to_numpy(), df["log10_abs_j_A_cm2"].to_numpy(), marker=".", linestyle="none", markersize=4, alpha=0.8, label="Measured log10|j|")
        xs = np.linspace(float(df["eta_V"].min()), float(df["eta_V"].max()), 800)
        if bv_fit and bv_fit.j0_A_cm2 and bv_fit.alpha is not None:
            pred = np.abs(_bv_current_density(xs, bv_fit.log10_j0, bv_fit.alpha, settings.n_electrons, settings.temperature_K))
            ax.plot(xs, np.log10(np.clip(pred, 1e-30, None)), linewidth=2, label=f"Taffit-style BV fit ({bv_fit.eta_window_mV:g} mV)")
        for f in branch_fits:
            if f.slope_dec_per_V is None or f.intercept_log10_j0 is None or f.eta_min_V is None or f.eta_max_V is None:
                continue
            xx = np.linspace(f.eta_min_V, f.eta_max_V, 80)
            yy = f.slope_dec_per_V * xx + f.intercept_log10_j0
            ax.plot(xx, yy, linewidth=1.5, label=f"{f.method} {f.branch}")
        ax.axvline(0, linewidth=1)
        ax.set_xlabel("Overpotential η (V)")
        ax.set_ylabel("log10(|j| / A cm$^{-2}$)")
        ax.set_title("FaradaIQ Tafel analysis — measured data and fitted kinetic regions")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(output_png, dpi=200)
        plt.close(fig)
        return str(output_png)
    except Exception:
        return ""


def analyze_file(path: Path, output_root: Path, settings: TafelSettings = TafelSettings()) -> TafelAnalysisResult:
    path = Path(path)
    output_root = Path(output_root)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    outdir = output_root / f"tafel_analysis_{_safe_name(path.stem)}_{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)
    df, warnings, pot_col, cur_col = normalize_iv_lsv(path, settings)
    norm_csv = outdir / f"{_safe_name(path.stem)}_normalized_tafel_data.csv"
    df.to_csv(norm_csv, index=False)
    manual = classical_manual_fits(df, settings)
    auto = auto_linear_fits(df, settings)
    stable = stable_window_fits(df, settings)
    main_bv = taffit_style_bv_fit(df, settings, settings.fitting_window_mV)
    scan_fits = [taffit_style_bv_fit(df, settings, w) for w in settings.eta_scan_windows_mV]
    comp_rows = []
    comp_rows.extend(_branch_rows(manual))
    comp_rows.extend(_branch_rows(auto))
    comp_rows.extend(_branch_rows(stable))
    comp_rows.extend(_bv_rows([main_bv]))
    comp_csv = outdir / f"{_safe_name(path.stem)}_method_comparison.csv"
    pd.DataFrame(comp_rows).to_csv(comp_csv, index=False)
    scan_rows = [asdict(x) for x in scan_fits]
    eta_scan_csv = outdir / f"{_safe_name(path.stem)}_taffit_eta_window_scan.csv"
    pd.DataFrame(scan_rows).to_csv(eta_scan_csv, index=False)
    stability_csv = outdir / f"{_safe_name(path.stem)}_tafel_slope_stability.csv"
    local_slope_diagnostics(df, settings).to_csv(stability_csv, index=False)
    fig_png = outdir / f"{_safe_name(path.stem)}_tafel_overlay.png"
    fig_path = _write_figure(df, fig_png, settings, main_bv, list(manual) + list(auto) + list(stable))
    summary = {
        "source_file": str(path),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "credit": CREDIT_TEXT,
        "input_columns": {"potential": pot_col, "current": cur_col},
        "eeq_V": float(df["E_V"].iloc[np.nanargmin(np.abs(df["eta_V"].to_numpy()))]) if len(df) else None,
        "settings": asdict(settings),
        "warnings": warnings,
        "main_bv_fit": asdict(main_bv),
        "files": {
            "normalized_csv": str(norm_csv),
            "comparison_csv": str(comp_csv),
            "eta_scan_csv": str(eta_scan_csv),
            "stability_csv": str(stability_csv),
            "figure_png": fig_path,
        },
    }
    summary_json = outdir / f"{_safe_name(path.stem)}_tafel_summary.json"
    summary_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    readme = outdir / "README_FaradaIQ_Tafel_Analysis.txt"
    readme.write_text(
        "FaradaIQ Tafel Analysis\n"
        "========================\n\n"
        "Includes: classical/manual linear fits, auto-linear R2 window search, "
        "Taffit-style nonlinear Butler-Volmer fit, eta-window scan, and one "
        "comparison table.\n\n"
        + CREDIT_TEXT + "\n\n"
        "Key files:\n"
        f"- {norm_csv.name}\n- {comp_csv.name}\n- {eta_scan_csv.name}\n- {summary_json.name}\n"
        + (f"- {Path(fig_path).name}\n" if fig_path else ""),
        encoding="utf-8",
    )
    return TafelAnalysisResult(
        source_file=str(path),
        output_dir=str(outdir),
        normalized_csv=str(norm_csv),
        comparison_csv=str(comp_csv),
        eta_scan_csv=str(eta_scan_csv),
        summary_json=str(summary_json),
        stability_csv=str(stability_csv),
        figure_png=fig_path,
        eeq_V=summary["eeq_V"],
        settings=summary["settings"],
        comparison_rows=comp_rows,
        eta_scan_rows=scan_rows,
        warnings=warnings + [r.get("warning", "") for r in comp_rows if r.get("warning")],
    )


def analyze_files(paths: Sequence[Path], output_root: Path, settings: TafelSettings = TafelSettings()) -> List[TafelAnalysisResult]:
    results = []
    for p in paths:
        results.append(analyze_file(Path(p), output_root, settings))
    # Aggregate result table for multi-file selected Plot/view.
    if results:
        root = Path(output_root)
        agg_dir = root / "tafel_analysis_combined"
        agg_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for r in results:
            for row in r.comparison_rows:
                rr = dict(row)
                rr["source_file"] = r.source_file
                rr["result_folder"] = r.output_dir
                rows.append(rr)
        if rows:
            pd.DataFrame(rows).to_csv(agg_dir / "FaradaIQ_Tafel_all_selected_method_comparison.csv", index=False)
    return results


def generate_dummy_dataset(output_root: Path, filename: str = "dummy_iv_lsv_for_tafel.csv", log10_j0: float = -6.0, alpha: float = 0.45, n: int = 1, T: float = 298.15, noise_std_dec: float = 0.025) -> Path:
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    eta = np.linspace(-0.22, 0.22, 361)
    j = _bv_current_density(eta, log10_j0, alpha, n, T)
    rng = np.random.default_rng(124)
    # Add log-normal-like noise while preserving sign away from zero; add tiny capacitive background.
    sign = np.sign(j)
    mag = np.abs(j) * (10 ** rng.normal(0.0, noise_std_dec, size=len(j)))
    current_density = sign * mag + 2e-8 * eta
    df = pd.DataFrame({
        "Potential_V": eta,  # Eeq = 0 in this dummy file
        "Current_A": current_density,  # area = 1 cm2
    })
    path = output_root / filename
    df.to_csv(path, index=False)
    return path


def run_dummy_test(output_root: Path) -> TafelAnalysisResult:
    dummy = generate_dummy_dataset(output_root)
    settings = TafelSettings(surface_area_cm2=1.0, n_electrons=1, temperature_K=298.15, eeq_V=0.0, current_adjust=False, fitting_window_mV=180.0)
    return analyze_file(dummy, output_root, settings)


def _parse_float_or_none(text: str) -> Optional[float]:
    s = str(text or "").strip()
    if not s:
        return None
    return float(s)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Run FaradaIQ native Tafel analysis on IV/LSV CSV/TXT files.")
    parser.add_argument("paths", nargs="*", help="IV/LSV CSV/TXT files")
    parser.add_argument("--output-root", default=".", help="Output root folder")
    parser.add_argument("--area", type=float, default=1.0, help="Electrode area in cm^2")
    parser.add_argument("--n", type=int, default=1, help="Number of electrons")
    parser.add_argument("--temperature", type=float, default=298.15, help="Temperature in K")
    parser.add_argument("--eeq", default="", help="Equilibrium/OCP potential in V; blank for auto-detect")
    parser.add_argument("--window-mv", type=float, default=120.0, help="Taffit-style total fitting window in mV around eta=0")
    parser.add_argument("--dummy-test", action="store_true", help="Generate and analyze a dummy IV/LSV data set")
    args = parser.parse_args(argv)
    settings = TafelSettings(surface_area_cm2=args.area, n_electrons=args.n, temperature_K=args.temperature, eeq_V=_parse_float_or_none(args.eeq), fitting_window_mV=args.window_mv)
    outroot = Path(args.output_root)
    if args.dummy_test:
        res = run_dummy_test(outroot)
        print(json.dumps(asdict(res), indent=2, default=str))
        return 0
    if not args.paths:
        parser.error("provide at least one file or --dummy-test")
    results = analyze_files([Path(p) for p in args.paths], outroot, settings)
    print(json.dumps([asdict(r) for r in results], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---- FaradaIQ Studio v273 CV forward/reverse Tafel analysis ----
_V227_ORIGINAL_ANALYZE_FILE = analyze_file


def _v227_split_cv_acquisition(df: pd.DataFrame) -> Dict[str, Tuple[pd.DataFrame, str]]:
    """Split one or more acquisition-ordered CV cycles into forward/reverse legs.

    The first monotonic leg is defined as forward. Subsequent persistent
    potential-direction reversals alternate reverse/forward. Multiple cycles
    are combined by branch while retaining acquisition and cycle-leg labels.
    """
    if "acquisition_index" in df.columns:
        ordered = df.sort_values("acquisition_index").reset_index(drop=True)
    else:
        ordered = df.reset_index(drop=True)
        ordered["acquisition_index"] = np.arange(len(ordered), dtype=int)
    x = ordered["E_V"].to_numpy(dtype=float)
    if len(x) < 8:
        return {"forward": (ordered, "Forward scan (whole short trace)")}
    dx = np.diff(x)
    span = max(float(np.nanmax(x) - np.nanmin(x)), 1e-15)
    threshold = span * 1e-9
    signs = np.zeros(len(dx), dtype=int)
    signs[dx > threshold] = 1
    signs[dx < -threshold] = -1
    # Fill flat/noisy increments from their nearest established direction.
    last = 0
    for i in range(len(signs)):
        if signs[i] != 0:
            last = signs[i]
        elif last:
            signs[i] = last
    last = 0
    for i in range(len(signs) - 1, -1, -1):
        if signs[i] != 0:
            last = signs[i]
        elif last:
            signs[i] = last
    nz = signs[signs != 0]
    if not len(nz):
        return {"forward": (ordered, "Forward scan (constant potential trace)")}
    initial = 1 if float(np.nanmedian(nz[:max(3, min(30, len(nz) // 5 or 3))])) >= 0 else -1
    # Detect only persistent direction changes so small digitization noise does
    # not create false CV legs.
    boundaries: List[int] = [0]
    current = initial
    i = 2
    persistence = 4
    while i < len(signs) - persistence:
        window = signs[i:i + persistence]
        candidate = int(np.sign(np.nanmedian(window)))
        if candidate and candidate != current and np.sum(window == candidate) >= persistence - 1:
            boundary = i + 1
            if boundary - boundaries[-1] >= 4:
                boundaries.append(boundary)
                current = candidate
            i += persistence
        else:
            i += 1
    boundaries.append(len(ordered))
    segments: List[pd.DataFrame] = []
    directions: List[str] = []
    for leg_index, (a, b) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        seg = ordered.iloc[a:b].copy()
        if len(seg) < 4:
            continue
        seg["cv_leg_index"] = leg_index + 1
        seg["cv_cycle_index"] = leg_index // 2 + 1
        direction = "increasing potential" if float(seg["E_V"].iloc[-1]) >= float(seg["E_V"].iloc[0]) else "decreasing potential"
        directions.append(direction)
        segments.append(seg)
    if len(segments) < 2:
        return {"forward": (ordered, "Forward scan (no return branch detected)")}
    forward_parts = [seg for idx, seg in enumerate(segments) if idx % 2 == 0]
    reverse_parts = [seg for idx, seg in enumerate(segments) if idx % 2 == 1]
    out: Dict[str, Tuple[pd.DataFrame, str]] = {}
    if forward_parts:
        out["forward"] = (
            pd.concat(forward_parts, ignore_index=True),
            f"Forward scan ({len(forward_parts)} acquisition leg{'s' if len(forward_parts) != 1 else ''}; {directions[0]})",
        )
    if reverse_parts:
        out["reverse"] = (
            pd.concat(reverse_parts, ignore_index=True),
            f"Reverse / return scan ({len(reverse_parts)} acquisition leg{'s' if len(reverse_parts) != 1 else ''}; {directions[1]})",
        )
    return out


def _v227_rezero_branch(df: pd.DataFrame, settings: TafelSettings) -> Tuple[pd.DataFrame, float, List[str]]:
    """Recompute Eeq, eta, current adjustment, and log current for one CV branch."""
    out = df.sort_values("acquisition_index" if "acquisition_index" in df.columns else "E_V").copy().reset_index(drop=True)
    warnings: List[str] = []
    E = out["E_V"].to_numpy(dtype=float)
    I = out["current_A"].to_numpy(dtype=float)
    area = float(settings.surface_area_cm2 or 1.0)
    if not np.isfinite(area) or area <= 0:
        area = 1.0
        warnings.append("Invalid surface area; using 1 cm^2.")
    j = I / area
    if settings.eeq_V is None:
        idx = int(np.nanargmin(np.abs(j)))
        eeq = float(E[idx])
        warnings.append(f"Branch Eeq/OCP auto-detected from minimum |current|: {eeq:.6g} V")
    else:
        eeq = float(settings.eeq_V)
    eta = E - eeq
    if settings.current_adjust and settings.eeq_V is not None:
        idx0 = int(np.nanargmin(np.abs(eta)))
        offset = float(j[idx0])
        j = j - offset
        warnings.append(f"Branch current adjusted by subtracting {offset:.6g} A/cm^2 at nearest Eeq.")
    absj = np.abs(j)
    max_abs = float(np.nanmax(absj)) if len(absj) else 0.0
    floor = max(1e-30, max_abs * 1e-15 if max_abs > 0 else 1e-30)
    out["eta_V"] = eta
    out["j_A_cm2"] = j
    out["abs_j_A_cm2"] = absj
    out["log10_abs_j_A_cm2"] = np.log10(np.clip(absj, floor, None))
    out = out.replace([np.inf, -np.inf], np.nan).dropna().sort_values("eta_V").reset_index(drop=True)
    return out, eeq, warnings


def _v227_analyze_dataframe(path: Path, output_root: Path, settings: TafelSettings, df: pd.DataFrame,
                            warnings: List[str], pot_col: str, cur_col: str,
                            branch_key: str = "whole", branch_label: str = "Whole trace / LSV") -> TafelAnalysisResult:
    path = Path(path)
    output_root = Path(output_root)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    branch_suffix = "" if branch_key == "whole" else f"_{_safe_name(branch_key)}"
    outdir = output_root / f"tafel_analysis_{_safe_name(path.stem)}{branch_suffix}_{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)
    norm_csv = outdir / f"{_safe_name(path.stem)}{branch_suffix}_normalized_tafel_data.csv"
    df.to_csv(norm_csv, index=False)
    manual = classical_manual_fits(df, settings)
    auto = auto_linear_fits(df, settings)
    stable = stable_window_fits(df, settings)
    main_bv = taffit_style_bv_fit(df, settings, settings.fitting_window_mV)
    scan_fits = [taffit_style_bv_fit(df, settings, w) for w in settings.eta_scan_windows_mV]
    comp_rows: List[Dict] = []
    comp_rows.extend(_branch_rows(manual))
    comp_rows.extend(_branch_rows(auto))
    comp_rows.extend(_branch_rows(stable))
    comp_rows.extend(_bv_rows([main_bv]))
    for row in comp_rows:
        row["scan_branch"] = branch_key
        row["scan_branch_label"] = branch_label
    comp_csv = outdir / f"{_safe_name(path.stem)}{branch_suffix}_method_comparison.csv"
    pd.DataFrame(comp_rows).to_csv(comp_csv, index=False)
    scan_rows = [asdict(x) for x in scan_fits]
    for row in scan_rows:
        row["scan_branch"] = branch_key
        row["scan_branch_label"] = branch_label
    eta_scan_csv = outdir / f"{_safe_name(path.stem)}{branch_suffix}_taffit_eta_window_scan.csv"
    pd.DataFrame(scan_rows).to_csv(eta_scan_csv, index=False)
    stability_csv = outdir / f"{_safe_name(path.stem)}{branch_suffix}_tafel_slope_stability.csv"
    try:
        local_slope_diagnostics(df, settings).to_csv(stability_csv, index=False)
    except Exception as exc:
        # Local-slope stability is a diagnostic artifact.  A pathological or
        # quantized window must never invalidate otherwise usable Tafel fits.
        warnings.append(f"local slope diagnostics skipped: {type(exc).__name__}: {exc}")
        pd.DataFrame(columns=[
            "branch", "eta_V", "log10_abs_j_A_cm2",
            "local_tafel_slope_mV_dec", "local_r2", "window_points",
        ]).to_csv(stability_csv, index=False)
    fig_png = outdir / f"{_safe_name(path.stem)}{branch_suffix}_tafel_overlay.png"
    fig_path = _write_figure(df, fig_png, settings, main_bv, list(manual) + list(auto) + list(stable))
    eeq = float(df["E_V"].iloc[np.nanargmin(np.abs(df["eta_V"].to_numpy()))]) if len(df) else None
    summary = {
        "source_file": str(path), "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "credit": CREDIT_TEXT,
        "input_columns": {"potential": pot_col, "current": cur_col}, "scan_branch": branch_key,
        "scan_branch_label": branch_label, "eeq_V": eeq, "settings": asdict(settings), "warnings": warnings,
        "main_bv_fit": asdict(main_bv),
        "files": {"normalized_csv": str(norm_csv), "comparison_csv": str(comp_csv),
                  "eta_scan_csv": str(eta_scan_csv), "stability_csv": str(stability_csv), "figure_png": fig_path},
    }
    summary_json = outdir / f"{_safe_name(path.stem)}{branch_suffix}_tafel_summary.json"
    summary_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (outdir / "README_FaradaIQ_Tafel_Analysis.txt").write_text(
        "FaradaIQ Tafel Analysis\n========================\n\n"
        f"Scan branch: {branch_label}\n\n"
        "Includes classical/manual fits, auto-linear R2 search, stable-window Tafel fitting with bootstrap uncertainty, local-slope stability diagnostics, Taffit-style nonlinear Butler-Volmer fitting, eta-window scans, and one comparison table.\n\n"
        + CREDIT_TEXT + "\n", encoding="utf-8")
    return TafelAnalysisResult(
        source_file=str(path), output_dir=str(outdir), normalized_csv=str(norm_csv), comparison_csv=str(comp_csv),
        eta_scan_csv=str(eta_scan_csv), summary_json=str(summary_json), stability_csv=str(stability_csv), figure_png=fig_path, eeq_V=eeq,
        settings=summary["settings"], comparison_rows=comp_rows, eta_scan_rows=scan_rows,
        warnings=warnings + [r.get("warning", "") for r in comp_rows if r.get("warning")],
    )


def analyze_file_variants(path: Path, output_root: Path, settings: TafelSettings = TafelSettings()) -> List[TafelAnalysisResult]:
    mode = str(getattr(settings, "scan_branch_mode", "Whole trace / LSV") or "Whole trace / LSV").lower()
    df, warnings, pot_col, cur_col = normalize_iv_lsv(Path(path), settings)
    if "whole" in mode or "lsv" in mode or "auto" in mode:
        return [_v227_analyze_dataframe(Path(path), output_root, settings, df, warnings, pot_col, cur_col)]
    branches = _v227_split_cv_acquisition(df)
    wanted: List[str]
    if "forward" in mode and "reverse" in mode:
        wanted = ["forward", "reverse"]
    elif "reverse" in mode:
        wanted = ["reverse"]
    else:
        wanted = ["forward"]
    results: List[TafelAnalysisResult] = []
    for key in wanted:
        item = branches.get(key)
        if item is None:
            continue
        branch_df, label = item
        if len(branch_df) < 8:
            continue
        branch_df, _eeq, branch_warnings = _v227_rezero_branch(branch_df, settings)
        results.append(_v227_analyze_dataframe(Path(path), output_root, settings, branch_df,
                                              list(warnings) + branch_warnings, pot_col, cur_col, key, label))
    if not results:
        raise ValueError(f"Could not identify the requested CV scan branch in {path}.")
    return results


def analyze_file(path: Path, output_root: Path, settings: TafelSettings = TafelSettings()) -> TafelAnalysisResult:
    return analyze_file_variants(path, output_root, settings)[0]


def analyze_files(paths: Sequence[Path], output_root: Path, settings: TafelSettings = TafelSettings()) -> List[TafelAnalysisResult]:
    results: List[TafelAnalysisResult] = []
    for p in paths:
        results.extend(analyze_file_variants(Path(p), output_root, settings))
    if results:
        root = Path(output_root)
        agg_dir = root / "tafel_analysis_combined"
        agg_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for r in results:
            for row in r.comparison_rows:
                rr = dict(row)
                rr["source_file"] = r.source_file
                rr["result_folder"] = r.output_dir
                rows.append(rr)
        if rows:
            pd.DataFrame(rows).to_csv(agg_dir / "FaradaIQ_Tafel_all_selected_method_comparison.csv", index=False)
    return results
# ---- End FaradaIQ Studio v273 CV branch analysis ----
