
"""
FaradaIQ v273 PySimpleEChem CV adapter.

This module keeps FaradaIQ's Tk browser independent from the PyQt event loop while
using the PySimpleEChem CV workflow model for CV/LSV analysis. The full upstream
PySimpleEChem source is bundled under integrated_tools/PySimpleEChem and can be
launched directly for manual slider-based analysis.

The analysis fields intentionally mirror PySimpleEChem's CV workflow:
file -> trim -> IR compensation/current density/reference conversion -> baseline 1/2
-> peak search mode/range/positions -> Jp1/Jp2 -> Ep1/Ep2 -> E1/2 -> Delta Ep
-> alpha estimates -> optional scan-rate diffusion/kinetics summaries.
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None

_THIS_DIR = Path(__file__).resolve().parent
_PSE_DIR = _THIS_DIR / "integrated_tools" / "PySimpleEChem"
if _PSE_DIR.exists() and str(_PSE_DIR) not in sys.path:
    sys.path.insert(0, str(_PSE_DIR))

_PSE_FC = None
_PSE_IMPORT_ERROR = None
try:  # Optional; can fail if statsmodels is absent.
    import function_collection as _PSE_FC  # type: ignore
except Exception as exc:  # pragma: no cover
    _PSE_FC = None
    _PSE_IMPORT_ERROR = str(exc)

REF_POTENTIALS_V = {
    'SHE': 0.0,
    'SCE (sat)': 0.241,
    'CSE': 0.314,
    'Ag/AgCl (sat)': 0.197,
    'Ag/AgCl (3 mol/kg)': 0.210,
    'Ag/AgCl (3.0 mol/L)': 0.22249,
    'Hg/Hg2SO4 (sat)': 0.64,
    'Hg/Hg2SO4 (0.5 M H2SO4)': 0.68,
}

@dataclass
class PySimpleCVParams:
    trim_start: int = 0
    trim_end: int = -1
    baseline_start_1: int = 0
    baseline_end_1: int = 0
    baseline_start_2: int = 0
    baseline_end_2: int = 0
    peak_pos_1: int = 0
    peak_pos_2: int = 0
    peak_range: int = 1
    peak_mode_1: str = 'max'
    peak_mode_2: str = 'min'
    scan_rate: float = 0.0
    elec_area: float = 1.0
    ir_compensation: float = 0.0
    nicholson_bool: bool = False
    jsp0: int = 0
    ref_from: str = 'SHE'
    ref_to: str = 'SHE'
    bulk_conc_mol_cm3: float = 0.0
    electron_n: float = 1.0


def _safe_float(x: Any, default: float = float('nan')) -> float:
    try:
        v = float(x)
        return v if np.isfinite(v) else default
    except Exception:
        return default


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(float(x))
    except Exception:
        return default


def convert_ref_elec(volt: np.ndarray, from_ref: str, to_ref: str = 'SHE') -> np.ndarray:
    if _PSE_FC is not None:
        try:
            return np.asarray(_PSE_FC.convert_ref_elec(volt, from_ref, to_ref), dtype=float)
        except Exception:
            pass
    return np.asarray(volt, dtype=float) + REF_POTENTIALS_V.get(from_ref, 0.0) - REF_POTENTIALS_V.get(to_ref, 0.0)


def infer_cv_format(path: str | Path) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    text = ''
    try:
        text = p.read_text(errors='ignore')[:20000]
    except Exception:
        text = ''
    low = (p.name + '\n' + text).lower()
    if ext == '.par' or 'definition=segment' in low or 'versastat' in low or 'princeton applied research' in low:
        return 'VersaSTAT'
    if ext == '.cor' or 'corrware' in low or 'end comments' in low:
        return 'CorrWare'
    if ext in {'.csv'}:
        return 'CSV'
    if ext in {'.txt', '.dat', '.tsv'}:
        if 'biologic' in low or 'ec-lab' in low or 'ewe' in low:
            return 'BioLogic/text'
        return 'text'
    return 'generic'


def _extract_scan_rate_from_text(path: Path) -> float:
    try:
        txt = path.read_text(errors='ignore')[:300000]
    except Exception:
        return 0.0
    pats = [
        r'Scan\s*Rate\s*\(V/s\)\s*=\s*([0-9.+\-Ee]+)',
        r'Scan\s*Rate\s*[:=]\s*([0-9.+\-Ee]+)\s*(m?V\s*/\s*s|V\s*/\s*s)?',
        r'([0-9.+\-Ee]+)\s*mV\s*/\s*s',
        r'([0-9.+\-Ee]+)\s*mVs',
    ]
    for pat in pats:
        m = re.search(pat, txt, flags=re.I)
        if m:
            val = _safe_float(m.group(1), 0.0)
            unit = m.group(2).lower() if len(m.groups()) >= 2 and m.group(2) else ''
            if 'mv' in unit or 'mvs' in pat.lower():
                return val / 1000.0
            return val
    m = re.search(r'(?:^|[_\-\s])([0-9]+(?:\.[0-9]+)?)\s*(mvs|mv_s|mVps)(?:[_\-\s]|$)', path.name, flags=re.I)
    if m:
        return _safe_float(m.group(1), 0.0) / 1000.0
    return 0.0


def _read_with_upstream(path: Path, fmt: str) -> Optional[Tuple[np.ndarray, np.ndarray, float, str]]:
    if _PSE_FC is None:
        return None
    try:
        # Upstream reader has known csv/text edge-case bugs in some revisions; use it where it is strongest.
        if fmt not in {'VersaSTAT', 'CorrWare'}:
            return None
        df, param_df, _defl = _PSE_FC.read_cv_format(str(path), fmt)
        if pd is None or df is None or df.shape[1] < 2:
            return None
        arr = df.iloc[:, :2].apply(pd.to_numeric, errors='coerce').dropna().to_numpy(dtype=float)
        if arr.shape[0] < 3:
            return None
        scan = 0.0
        try:
            scan = float(param_df.iloc[0].get('scan_rate', 0.0))
        except Exception:
            scan = 0.0
        return arr[:, 0], arr[:, 1], scan, fmt
    except Exception:
        return None


def _choose_numeric_columns(df) -> Tuple[np.ndarray, np.ndarray, str, str]:
    if pd is None:
        raise RuntimeError('pandas is required for generic CV table reading')
    numeric = df.apply(pd.to_numeric, errors='coerce')
    cols = list(numeric.columns)
    finite_counts = {c: int(np.isfinite(numeric[c].to_numpy(dtype=float)).sum()) for c in cols}
    usable = [c for c in cols if finite_counts.get(c, 0) >= 3]
    if len(usable) < 2:
        raise ValueError('Could not identify two numeric CV columns')
    def score_x(c):
        s = str(c).lower()
        score = 0
        for token in ['potential', 'voltage', 'volt', 'ewe', 'e/v', 'e_', 'v']:
            if token in s:
                score += 5
        if 'current' in s or 'i/' in s or 'a' == s.strip():
            score -= 2
        return score + min(finite_counts[c], 10000) / 10000.0
    def score_y(c):
        s = str(c).lower()
        score = 0
        for token in ['current', 'i/', '<i>', 'i_', 'amp', 'ma', 'ua', 'a']:
            if token in s:
                score += 5
        if 'potential' in s or 'volt' in s:
            score -= 2
        return score + min(finite_counts[c], 10000) / 10000.0
    xcol = max(usable, key=score_x)
    remaining = [c for c in usable if c != xcol]
    ycol = max(remaining, key=score_y) if remaining else usable[1]
    arr = numeric[[xcol, ycol]].dropna().to_numpy(dtype=float)
    return arr[:, 0], arr[:, 1], str(xcol), str(ycol)


def read_cv_file(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    fmt = infer_cv_format(p)
    up = _read_with_upstream(p, fmt)
    if up is not None:
        e, i, scan, reader = up
        return {'path': str(p), 'E': e, 'I': i, 'scan_rate': scan or _extract_scan_rate_from_text(p), 'reader': f'PySimpleEChem {reader}', 'format': fmt, 'xcol': 'potential', 'ycol': 'current'}
    if pd is None:
        raise RuntimeError('pandas is required for CV parsing')
    # Generic robust reader for CSV/TXT/TSV/exported parser tables.
    errors = []
    for kwargs in [
        dict(sep=None, engine='python'),
        dict(sep='\t', engine='python'),
        dict(sep=',', engine='python'),
        dict(delim_whitespace=True),
    ]:
        try:
            df = pd.read_csv(p, comment='#', **kwargs)
            if df.shape[1] >= 2:
                e, i, xcol, ycol = _choose_numeric_columns(df)
                if len(e) >= 3:
                    return {'path': str(p), 'E': e, 'I': i, 'scan_rate': _extract_scan_rate_from_text(p), 'reader': 'PySimpleEChem-compatible generic table', 'format': fmt, 'xcol': xcol, 'ycol': ycol}
        except Exception as exc:
            errors.append(str(exc))
    # Headerless fallback.
    try:
        df = pd.read_csv(p, comment='#', header=None, sep=None, engine='python')
        e, i, xcol, ycol = _choose_numeric_columns(df)
        return {'path': str(p), 'E': e, 'I': i, 'scan_rate': _extract_scan_rate_from_text(p), 'reader': 'PySimpleEChem-compatible headerless table', 'format': fmt, 'xcol': xcol, 'ycol': ycol}
    except Exception as exc:
        raise ValueError(f'Could not read CV file {p}: {exc}; earlier attempts: {errors[:3]}')


def second_derivative_candidates(E: np.ndarray, I: np.ndarray, max_candidates: int = 20) -> np.ndarray:
    if _PSE_FC is not None:
        try:
            idx = _PSE_FC.peak_2nd_deriv(E, I, 0.05, 0.05)
            vals = [int(x) for x in list(idx) if np.isfinite(x)]
            if vals:
                return np.asarray(vals[:max_candidates], dtype=int)
        except Exception:
            pass
    y = np.asarray(I, dtype=float)
    if len(y) < 5:
        return np.asarray([], dtype=int)
    try:
        dy2 = np.gradient(np.gradient(y))
        cand = np.argsort(np.abs(dy2))[::-1]
        cand = [int(i) for i in cand if 1 <= i < len(y)-1]
        return np.asarray(cand[:max_candidates], dtype=int)
    except Exception:
        return np.asarray([], dtype=int)


def default_params_for(E: np.ndarray, I: np.ndarray, scan_rate: float = 0.0) -> PySimpleCVParams:
    n = int(min(len(E), len(I)))
    if n <= 0:
        return PySimpleCVParams()
    y = np.asarray(I, dtype=float)
    p1 = int(np.nanargmax(y)) if np.isfinite(y).any() else 0
    p2 = int(np.nanargmin(y)) if np.isfinite(y).any() else n - 1
    # Baseline default follows PySimpleEChem's two-baseline model, but chooses useful ranges.
    seg = max(3, n // 12)
    half_window = max(3, n // 30)
    b1s = max(0, min(p1, n - 1) - 3 * half_window)
    b1e = max(b1s + 2, min(n - 1, min(p1, n - 1) - half_window))
    b2s = max(0, min(p2, n - 1) + half_window)
    b2e = max(b2s + 2, min(n - 1, min(p2, n - 1) + 3 * half_window))
    if b2e <= b2s + 1:
        b2s, b2e = max(0, n - seg - 1), n - 1
    return PySimpleCVParams(
        trim_start=0,
        trim_end=n - 1,
        baseline_start_1=b1s,
        baseline_end_1=b1e,
        baseline_start_2=b2s,
        baseline_end_2=b2e,
        peak_pos_1=p1,
        peak_pos_2=p2,
        peak_range=max(2, n // 40),
        peak_mode_1='max',
        peak_mode_2='min',
        scan_rate=float(scan_rate or 0.0),
        elec_area=1.0,
        ir_compensation=0.0,
        nicholson_bool=False,
        jsp0=max(0, min(n - 1, (p1 + p2) // 2)),
        ref_from='SHE',
        ref_to='SHE',
        electron_n=1.0,
    )


def _clamp_idx(v: int, n: int) -> int:
    if n <= 0:
        return 0
    return max(0, min(int(v), n - 1))


def _baseline_poly(E: np.ndarray, I: np.ndarray, start: int, end: int):
    n = len(E)
    start = _clamp_idx(start, n)
    end = _clamp_idx(end, n)
    if end < start:
        start, end = end, start
    if end <= start:
        end = min(n - 1, start + 1)
    xs = E[start:end+1]
    ys = I[start:end+1]
    mask = np.isfinite(xs) & np.isfinite(ys)
    if mask.sum() >= 2:
        coef = np.polynomial.polynomial.polyfit(xs[mask], ys[mask], 1)
    else:
        coef = np.asarray([float(np.nanmean(I)) if len(I) else 0.0, 0.0], dtype=float)
    return np.polynomial.Polynomial(coef), (start, end)


def _peak_from_mode(mode: str, E: np.ndarray, I: np.ndarray, peak_pos: int, peak_range: int, baseline_range: Tuple[int, int], defl: np.ndarray):
    n = len(E)
    peak_pos = _clamp_idx(peak_pos, n)
    peak_range = max(0, int(peak_range))
    mode = str(mode or 'exact').lower()
    low = max(0, peak_pos - peak_range)
    high = min(n - 1, peak_pos + peak_range)
    if mode == '2nd derivative' and len(defl):
        valid = [int(x) for x in defl if 0 <= int(x) < n]
        if valid:
            peak_pos = min(valid, key=lambda z: abs(z - peak_pos))
            low = high = peak_pos
    elif mode == 'max':
        local = I[low:high+1]
        peak_pos = low + int(np.nanargmax(local)) if len(local) else peak_pos
    elif mode == 'min':
        local = I[low:high+1]
        peak_pos = low + int(np.nanargmin(local)) if len(local) else peak_pos
    # exact uses peak_pos
    poly, (bs, be) = _baseline_poly(E, I, baseline_range[0], baseline_range[1])
    peak_E = float(E[peak_pos])
    peak_I = float(I[peak_pos])
    baseline_at_peak = float(poly(peak_E))
    jp = float(peak_I - baseline_at_peak)
    ep12 = float('nan')
    jp12 = float('nan')
    alpha = float('nan')
    try:
        lo = min(bs, peak_pos)
        hi = max(bs, peak_pos)
        xs = E[lo:hi+1]
        ys = I[lo:hi+1] - poly(E[lo:hi+1])
        if len(xs) >= 2 and np.isfinite(jp) and abs(jp) > 0:
            k = int(np.nanargmin(np.abs(ys - jp/2.0)))
            ep12 = float(xs[k])
            jp12 = float(ys[k] + poly(xs[k]))
            denom = abs(peak_E - ep12)
            if denom > 1e-12:
                alpha = float(1.0 - ((47.7/1000.0)/denom))
    except Exception:
        pass
    return {
        'mode': mode,
        'search_low_idx': int(low),
        'search_high_idx': int(high),
        'peak_idx': int(peak_pos),
        'peak_E_V': peak_E,
        'peak_current_density_or_current': peak_I,
        'baseline_current_at_peak': baseline_at_peak,
        'Jp': jp,
        'baseline_range': [int(bs), int(be)],
        'baseline_poly_coef': [float(x) for x in poly.coef],
        'Ep_half_estimate_V': ep12,
        'Jp_half_raw_current': jp12,
        'alpha_estimate': alpha,
    }


def analyze_cv_file(path: str | Path, params: Optional[Dict[str, Any] | PySimpleCVParams] = None) -> Dict[str, Any]:
    raw = read_cv_file(path)
    E0 = np.asarray(raw['E'], dtype=float)
    I0 = np.asarray(raw['I'], dtype=float)
    n0 = int(min(len(E0), len(I0)))
    E0 = E0[:n0]
    I0 = I0[:n0]
    defaults = default_params_for(E0, I0, raw.get('scan_rate', 0.0))
    if params is None:
        par = defaults
    elif isinstance(params, PySimpleCVParams):
        par = params
    else:
        d = asdict(defaults)
        d.update({k: v for k, v in dict(params).items() if k in d and v is not None})
        par = PySimpleCVParams(**d)
    if par.scan_rate in (None, 0, 0.0):
        par.scan_rate = float(raw.get('scan_rate', 0.0) or 0.0)
    area = float(par.elec_area or 1.0)
    if not np.isfinite(area) or area <= 0:
        area = 1.0
    # Match PySimpleEChem: volt_comp = raw E + raw current * R; then reference conversion; current density = current/area.
    E = convert_ref_elec(E0 + I0 * float(par.ir_compensation or 0.0), par.ref_from, par.ref_to)
    I = I0 / area
    n = len(E)
    ts = _clamp_idx(par.trim_start, n)
    te = _clamp_idx(par.trim_end if par.trim_end >= 0 else n-1, n)
    if te < ts:
        ts, te = te, ts
    E_trim = E[ts:te+1]
    I_trim = I[ts:te+1]
    # Recompute defaults after transform in case caller did not provide positions.
    if params is None:
        par = default_params_for(E, I, par.scan_rate)
    defl = second_derivative_candidates(E, I)
    p1 = _peak_from_mode(par.peak_mode_1, E, I, par.peak_pos_1, par.peak_range, (par.baseline_start_1, par.baseline_end_1), defl)
    p2 = _peak_from_mode(par.peak_mode_2, E, I, par.peak_pos_2, par.peak_range, (par.baseline_start_2, par.baseline_end_2), defl)
    j1 = abs(float(p1['Jp'])) if np.isfinite(p1['Jp']) else float('nan')
    j2 = abs(float(p2['Jp'])) if np.isfinite(p2['Jp']) else float('nan')
    dep = abs(float(p1['peak_E_V']) - float(p2['peak_E_V'])) if np.isfinite(p1['peak_E_V']) and np.isfinite(p2['peak_E_V']) else float('nan')
    ehalf = (float(p1['peak_E_V']) + float(p2['peak_E_V']))/2.0 if np.isfinite(dep) else float('nan')
    out = {
        'file': str(path),
        'file_name': Path(path).name,
        'type': 'CV / LSV',
        'backend': 'PySimpleEChem workflow via FaradaIQ adapter',
        'upstream_import_available': bool(_PSE_FC is not None),
        'upstream_import_error': _PSE_IMPORT_ERROR,
        'reader': raw.get('reader'),
        'format': raw.get('format'),
        'columns': {'potential': raw.get('xcol'), 'current': raw.get('ycol')},
        'params': asdict(par),
        'n_points': int(n),
        'trimmed_n_points': int(len(E_trim)),
        'E': E,
        'I': I,
        'E_trim': E_trim,
        'I_trim': I_trim,
        'deflection_candidate_indices': [int(x) for x in defl[:50]],
        'peak_1': p1,
        'peak_2': p2,
        'Jp1': j1,
        'Jp2': j2,
        'Jp1_Jp2_ratio': float(j1/j2) if j2 and np.isfinite(j1) and np.isfinite(j2) else float('nan'),
        'Ep1_V': float(p1['peak_E_V']),
        'Ep2_V': float(p2['peak_E_V']),
        'E_half_V': ehalf,
        'Delta_Ep_V': dep,
        'alpha1': float(p1['alpha_estimate']) if np.isfinite(p1['alpha_estimate']) else float('nan'),
        'alpha2': float(p2['alpha_estimate']) if np.isfinite(p2['alpha_estimate']) else float('nan'),
        'Jpc0': 0.0,
        'scan_rate_V_s': float(par.scan_rate or 0.0),
        'warnings': [],
        'assumptions': [
            'CV analysis uses the PySimpleEChem two-baseline/two-peak parameter workflow.',
            'Default baseline and peak indices are auto-initialized; use native PySimpleEChem for manual slider refinement when publishing quantitative values.',
        ],
    }
    if not np.isfinite(dep):
        out['warnings'].append('Delta Ep could not be computed because one or both peak potentials were invalid.')
    if par.elec_area == 1.0:
        out['warnings'].append('Electrode area is 1.0 by default; Jp values may be current rather than true current density.')
    if not par.scan_rate:
        out['warnings'].append('Scan rate was not detected/provided; scan-rate diffusion/kinetics cannot be calculated for this file alone.')
    return out


def compact_result(result: Dict[str, Any]) -> Dict[str, Any]:
    skip = {'E', 'I', 'E_trim', 'I_trim'}
    out = {k: v for k, v in result.items() if k not in skip}
    # Ensure JSON-safe NaN handling by converting numpy scalars.
    def conv(x):
        if isinstance(x, np.ndarray):
            return x.tolist()
        if isinstance(x, (np.floating, float)):
            return None if not np.isfinite(float(x)) else float(x)
        if isinstance(x, (np.integer, int)):
            return int(x)
        if isinstance(x, dict):
            return {str(k): conv(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [conv(v) for v in x]
        return x
    return conv(out)


def analyze_cv_files(paths: Sequence[str | Path], shared_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    results = []
    errors = []
    for p in paths:
        try:
            results.append(analyze_cv_file(p, shared_params))
        except Exception as exc:
            errors.append({'file': str(p), 'error': str(exc)})
    bundle = {
        'backend': 'PySimpleEChem workflow via FaradaIQ adapter',
        'n_files': len(paths),
        'n_results': len(results),
        'results': results,
        'errors': errors,
        'scan_rate_summary': scan_rate_diffusion_summary(results, shared_params or {}),
    }
    return bundle


def results_table(results: Sequence[Dict[str, Any]]):
    rows = []
    for r in results:
        rows.append({
            'file name': r.get('file_name'),
            'scan rate': r.get('scan_rate_V_s'),
            'Jp1': r.get('Jp1'),
            'Jp2': r.get('Jp2'),
            'Jp1/Jp2': r.get('Jp1_Jp2_ratio'),
            'Ep1': r.get('Ep1_V'),
            'Ep2': r.get('Ep2_V'),
            'E½': r.get('E_half_V'),
            'ΔEₚ': r.get('Delta_Ep_V'),
            'alpha1': r.get('alpha1'),
            'alpha2': r.get('alpha2'),
            'Jpc0': r.get('Jpc0'),
            'backend': r.get('backend'),
        })
    if pd is None:
        return rows
    return pd.DataFrame(rows)


def _linear_fit_r2(x: np.ndarray, y: np.ndarray):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]
    if len(x) < 2:
        return None
    coef = np.polynomial.polynomial.polyfit(x, y, 1)
    poly = np.polynomial.Polynomial(coef)
    fit = poly(x)
    ssr = float(np.sum((y - fit)**2))
    sst = float(np.sum((y - np.mean(y))**2))
    r2 = 1.0 - ssr/sst if sst else float('nan')
    return {'intercept': float(coef[0]), 'slope': float(coef[1]), 'x': x, 'fit': fit, 'r2': r2}


def scan_rate_diffusion_summary(results: Sequence[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
    scan = np.asarray([_safe_float(r.get('scan_rate_V_s')) for r in results], dtype=float)
    jp1 = np.asarray([_safe_float(r.get('Jp1')) for r in results], dtype=float)
    jp2 = np.asarray([_safe_float(r.get('Jp2')) for r in results], dtype=float)
    mask = np.isfinite(scan) & (scan > 0) & np.isfinite(jp1) & np.isfinite(jp2)
    out = {'available': bool(mask.sum() >= 2), 'n_scan_rates': int(mask.sum()), 'assumptions': [], 'warnings': []}
    if mask.sum() < 2:
        out['warnings'].append('Need at least two files with valid scan rate and Jp values for PySimpleEChem scan-rate workflow.')
        return out
    sqrt_scan = np.sqrt(scan[mask])
    f1 = _linear_fit_r2(sqrt_scan, np.abs(jp1[mask]))
    f2 = _linear_fit_r2(sqrt_scan, np.abs(jp2[mask]))
    out['sqrt_scan_rate_Vs_sqrt'] = sqrt_scan.tolist()
    out['Jp1_abs'] = np.abs(jp1[mask]).tolist()
    out['Jp2_abs'] = np.abs(jp2[mask]).tolist()
    if f1:
        out['Jp1_vs_sqrt_scan_fit'] = {'slope': f1['slope'], 'intercept': f1['intercept'], 'r2': f1['r2']}
    if f2:
        out['Jp2_vs_sqrt_scan_fit'] = {'slope': f2['slope'], 'intercept': f2['intercept'], 'r2': f2['r2']}
    conc = _safe_float(params.get('bulk_conc_mol_cm3', 0.0), 0.0)
    n_e = _safe_float(params.get('electron_n', 1.0), 1.0)
    alpha1 = np.nanmean([_safe_float(r.get('alpha1')) for r in results])
    alpha2 = np.nanmean([_safe_float(r.get('alpha2')) for r in results])
    if conc > 0 and n_e > 0 and f1 and f2:
        # Same constants and formulas used by PySimpleEChem's diffusion() helper.
        try:
            out['D_rev_peak1_cm2_s'] = float((f1['slope']/(2.69e5*n_e**1.5*conc))**2)
            out['D_rev_peak2_cm2_s'] = float((f2['slope']/(2.69e5*n_e**1.5*conc))**2)
            if np.isfinite(alpha1) and alpha1 > 0:
                out['D_irr_peak1_cm2_s'] = float((f1['slope']/(2.99e5*n_e**1.5*(alpha1**0.5)*conc))**2)
            if np.isfinite(alpha2) and alpha2 > 0:
                out['D_irr_peak2_cm2_s'] = float((f2['slope']/(2.99e5*n_e**1.5*(alpha2**0.5)*conc))**2)
        except Exception as exc:
            out['warnings'].append(f'Diffusion coefficient calculation failed: {exc}')
    else:
        out['assumptions'].append('Diffusion coefficients are reported only when bulk concentration in mol/cm^3 and electron count are provided.')
    return out


def write_results_bundle(bundle: Dict[str, Any], outdir: str | Path, stem: str = 'pysimpleechem_cv') -> Dict[str, str]:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    compact = {
        **{k: v for k, v in bundle.items() if k != 'results'},
        'results': [compact_result(r) for r in bundle.get('results', [])],
    }
    json_path = out / f'{stem}_results.json'
    json_path.write_text(json.dumps(compact, ensure_ascii=False, indent=2), encoding='utf-8')
    paths['json'] = str(json_path)
    if pd is not None:
        df = results_table(bundle.get('results', []))
        csv_path = out / f'{stem}_results_table.csv'
        df.to_csv(csv_path, index=False)
        paths['csv'] = str(csv_path)
    return paths
