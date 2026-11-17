"""FaradaIQ Studio v315 potential-dependent EIS model analysis.

Adds an additive, literature-informed analysis layer for Mott-Schottky style
potential-dependent EIS: equivalent-circuit screening, capacitance/CPE metrics,
Mott-Schottky window diagnostics, apparent series-capacitance decomposition, and
presentation/browser recipes.  The implementation is deliberately conservative:
it reports fitted quantities and assumptions, but does not label an extracted
term as quantum capacitance unless the user supplies the independent area and
Cdl/DOS assumptions needed to separate total capacitance.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
from pathlib import Path
from typing import Any, MutableMapping, Sequence

import numpy as np

VERSION = "v315"
DEFAULT_OUTPUT_DIR = r"C:\Data\ANALYSIS\Faradaiq"


def _safe_name(text: Any, default: str = "potential_eis") -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or default)).strip("._") or default


def _write_csv(path: Path, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(list(columns))
        for row in rows:
            out = []
            for x in row:
                if x is None:
                    out.append("")
                    continue
                try:
                    v = float(x)
                    out.append("" if not math.isfinite(v) else f"{v:.12g}")
                except Exception:
                    out.append(str(x))
            w.writerow(out)


def _finite(x: Any) -> float | None:
    try:
        v = float(x)
    except Exception:
        return None
    return v if math.isfinite(v) else None


def _cpe_capacitance_hsu_mansfeld(q: Any, n: Any, r: Any) -> float | None:
    q = _finite(q); n = _finite(n); r = _finite(r)
    if q is None or n is None or r is None or q <= 0 or r <= 0 or n <= 0:
        return None
    try:
        return float((q * (r ** (1.0 - n))) ** (1.0 / n))
    except Exception:
        return None


def _complex_residual(z_model: np.ndarray, z_obs: np.ndarray) -> np.ndarray:
    scale = np.maximum(np.abs(z_obs), np.nanmedian(np.abs(z_obs[np.isfinite(z_obs)])) if np.any(np.isfinite(z_obs)) else 1.0)
    scale = np.where(scale > 0, scale, 1.0)
    r = (z_model - z_obs) / scale
    return np.r_[r.real, r.imag]


def _parallel_r_cpe(r: float, q: float, n: float, omega: np.ndarray) -> np.ndarray:
    jw = 1j * omega
    return 1.0 / (1.0 / r + q * np.power(jw, n))


def _z_rq(params: np.ndarray, omega: np.ndarray) -> np.ndarray:
    rs, rct, q, n = params
    return rs + _parallel_r_cpe(rct, q, n, omega)


def _z_rq_w(params: np.ndarray, omega: np.ndarray) -> np.ndarray:
    rs, rct, q, n, sigma = params
    jw = 1j * omega
    zw = sigma / np.sqrt(jw)
    return rs + _parallel_r_cpe(rct, q, n, omega) + zw


def _z_rq_rq(params: np.ndarray, omega: np.ndarray) -> np.ndarray:
    rs, r1, q1, n1, r2, q2, n2 = params
    return rs + _parallel_r_cpe(r1, q1, n1, omega) + _parallel_r_cpe(r2, q2, n2, omega)


def _fit_model(freq: np.ndarray, zr: np.ndarray, zi: np.ndarray, model: str) -> dict[str, Any]:
    try:
        from scipy.optimize import least_squares
    except Exception as exc:
        return {"model": model, "fit_status": "scipy_unavailable", "fit_error": str(exc)}
    f = np.asarray(freq, dtype=float)
    z = np.asarray(zr, dtype=float) + 1j * np.asarray(zi, dtype=float)
    mask = np.isfinite(f) & np.isfinite(z.real) & np.isfinite(z.imag) & (f > 0)
    f, z = f[mask], z[mask]
    if len(f) < 8:
        return {"model": model, "fit_status": "insufficient_points", "n_points": int(len(f))}
    order = np.argsort(f)[::-1]
    f, z = f[order], z[order]
    omega = 2 * np.pi * f
    rs0 = max(1e-9, float(np.nanmin(z.real)))
    rspan = max(1e-6, float(np.nanmax(z.real) - rs0))
    zabs_med = max(1e-9, float(np.nanmedian(np.abs(z))))
    q0 = max(1e-12, 1.0 / (zabs_med * 2 * np.pi * max(float(np.nanmedian(f)), 1e-6)))
    try:
        if model == "Rs-(Rct||CPE)":
            x0 = np.array([rs0, rspan, q0, 0.88], dtype=float)
            lo = np.array([0.0, 1e-9, 1e-15, 0.35])
            hi = np.array([max(1e-6, zabs_med * 100), max(1e-6, zabs_med * 1000), 1e3, 1.0])
            zfun = _z_rq
            pnames = ["Rs_ohm", "Rct_ohm", "Q_F_s_n_minus1", "n_cpe"]
        elif model == "Rs-(Rct||CPE)-Warburg":
            x0 = np.array([rs0, rspan, q0, 0.88, max(1e-6, zabs_med / max(np.sqrt(float(np.nanmax(omega))), 1e-12))], dtype=float)
            lo = np.array([0.0, 1e-9, 1e-15, 0.35, 0.0])
            hi = np.array([max(1e-6, zabs_med * 100), max(1e-6, zabs_med * 1000), 1e3, 1.0, zabs_med * 1000])
            zfun = _z_rq_w
            pnames = ["Rs_ohm", "Rct_ohm", "Q_F_s_n_minus1", "n_cpe", "Warburg_sigma_ohm_s_minus_half"]
        else:
            x0 = np.array([rs0, max(rspan * 0.55, 1e-6), q0, 0.9, max(rspan * 0.45, 1e-6), q0 / 5, 0.75], dtype=float)
            lo = np.array([0.0, 1e-9, 1e-15, 0.35, 1e-9, 1e-15, 0.35])
            hi = np.array([max(1e-6, zabs_med * 100), max(1e-6, zabs_med * 1000), 1e3, 1.0, max(1e-6, zabs_med * 1000), 1e3, 1.0])
            zfun = _z_rq_rq
            pnames = ["Rs_ohm", "R1_ohm", "Q1_F_s_n_minus1", "n1_cpe", "R2_ohm", "Q2_F_s_n_minus1", "n2_cpe"]
        def residual(x: np.ndarray) -> np.ndarray:
            return _complex_residual(zfun(x, omega), z)
        res = least_squares(residual, x0, bounds=(lo, hi), max_nfev=1500, x_scale="jac", loss="soft_l1")
        rmse = float(np.sqrt(np.mean(res.fun ** 2))) if res.fun.size else float("nan")
        rss = float(np.sum(res.fun ** 2))
        nobs = int(res.fun.size)
        k = int(len(res.x))
        aic = float(nobs * np.log(max(rss / max(nobs, 1), 1e-300)) + 2 * k)
        aicc = float(aic + (2 * k * (k + 1)) / max(nobs - k - 1, 1))
        out: dict[str, Any] = {"model": model, "fit_status": "ok" if res.success else "not_converged", "n_points": int(len(f)), "normalized_complex_rmse": rmse, "aicc": aicc, "optimizer_message": str(res.message)}
        for name, val in zip(pnames, res.x):
            out[name] = float(val)
        if "Rct_ohm" in out:
            out["Ceff_from_CPE_F"] = _cpe_capacitance_hsu_mansfeld(out.get("Q_F_s_n_minus1"), out.get("n_cpe"), out.get("Rct_ohm"))
            q = _finite(out.get("Q_F_s_n_minus1")); n = _finite(out.get("n_cpe")); r = _finite(out.get("Rct_ohm"))
            out["tau_eff_s"] = float((r * q) ** (1.0 / n)) if q and n and r else None
        else:
            c1 = _cpe_capacitance_hsu_mansfeld(out.get("Q1_F_s_n_minus1"), out.get("n1_cpe"), out.get("R1_ohm"))
            c2 = _cpe_capacitance_hsu_mansfeld(out.get("Q2_F_s_n_minus1"), out.get("n2_cpe"), out.get("R2_ohm"))
            out["Ceff_1_F"] = c1; out["Ceff_2_F"] = c2
            out["Rct_total_ohm"] = (_finite(out.get("R1_ohm")) or 0.0) + (_finite(out.get("R2_ohm")) or 0.0)
        return out
    except Exception as exc:
        return {"model": model, "fit_status": "failed", "fit_error": str(exc), "n_points": int(len(f))}


def _best_circuit_for_step(block: np.ndarray, cols: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    idx = {c: cols.index(c) for c in ("frequency_Hz", "Zreal_ohm", "Zimag_ohm") if c in cols}
    fits = [_fit_model(block[:, idx["frequency_Hz"]], block[:, idx["Zreal_ohm"]], block[:, idx["Zimag_ohm"]], m) for m in ("Rs-(Rct||CPE)", "Rs-(Rct||CPE)-Warburg", "Rs-(R1||CPE1)-(R2||CPE2)")]
    good = [x for x in fits if x.get("fit_status") in {"ok", "not_converged"} and _finite(x.get("aicc")) is not None]
    if good:
        best = min(good, key=lambda x: float(x.get("aicc")))
    else:
        best = fits[0]
    return best, fits


def _capacitance_decomposition(block: np.ndarray, cols: list[str], assumed_cdl_F: float | None, area_cm2: float | None, eps_r: float | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "frequency_Hz" not in cols or "capacitance_parallel_F" not in cols:
        return out
    f = block[:, cols.index("frequency_Hz")]
    cp = block[:, cols.index("capacitance_parallel_F")]
    good = np.isfinite(f) & np.isfinite(cp) & (f > 0) & (cp > 0)
    if not np.any(good):
        return out
    # Representative capacitance from the highest-frequency decade where faradaic/diffusive tails are least dominant.
    f2, cp2 = f[good], cp[good]
    cutoff = np.nanmax(f2) / 10.0
    hf = cp2[f2 >= cutoff]
    ctot = float(np.nanmedian(hf if len(hf) else cp2))
    out["Ctotal_high_frequency_median_F"] = ctot
    if area_cm2 and area_cm2 > 0:
        out["Ctotal_high_frequency_median_uF_cm2"] = ctot / area_cm2 * 1e6
    if assumed_cdl_F and assumed_cdl_F > 0:
        denom = 1.0 / ctot - 1.0 / assumed_cdl_F
        out["assumed_Cdl_F"] = assumed_cdl_F
        out["apparent_material_or_quantum_capacitance_F"] = (1.0 / denom) if denom > 0 else None
        out["capacitance_decomposition_status"] = "series_decomposition_from_user_assumed_Cdl" if denom > 0 else "Ctotal_not_smaller_than_assumed_Cdl_no_positive_series_material_capacitance"
    else:
        out["capacitance_decomposition_status"] = "reported_total_capacitance_only_user_area_Cdl_required_for_Cdl_vs_quantum_or_space_charge_separation"
    if eps_r and area_cm2 and area_cm2 > 0:
        eps0_F_cm = 8.8541878128e-14
        width_cm = eps_r * eps0_F_cm * area_cm2 / ctot if ctot > 0 else None
        out["apparent_space_charge_width_cm_from_Ctotal"] = width_cm
    return out


def _auto_mott_windows(arr: np.ndarray, cols: list[str], area_cm2: float | None, eps_r: float | None, temperature_K: float) -> list[list[Any]]:
    needed = ["potential_V", "frequency_Hz", "inverse_capacitance_parallel_squared_F_minus2"]
    if not all(c in cols for c in needed):
        return []
    ci = {c: cols.index(c) for c in needed}
    potentials = np.unique(arr[:, ci["potential_V"]])
    freqs = np.unique(arr[:, ci["frequency_Hz"]])
    rows: list[list[Any]] = []
    for f in freqs:
        mask = np.isclose(arr[:, ci["frequency_Hz"]], f, rtol=1e-5, atol=1e-12)
        sub = arr[mask]
        if len(sub) < 12:
            continue
        x = sub[:, ci["potential_V"]]
        y = sub[:, ci["inverse_capacitance_parallel_squared_F_minus2"]]
        ok = np.isfinite(x) & np.isfinite(y) & (y > 0)
        x, y = x[ok], y[ok]
        if len(x) < 12:
            continue
        order = np.argsort(x)
        x, y = x[order], y[order]
        best = None
        min_win = max(12, int(len(x) * 0.25))
        for w in range(min_win, len(x) + 1):
            for s in range(0, len(x) - w + 1):
                xs, ys = x[s:s+w], y[s:s+w]
                # linear fit in y vs V
                p = np.polyfit(xs, ys, 1)
                yh = np.polyval(p, xs)
                ss_res = float(np.sum((ys - yh) ** 2))
                ss_tot = float(np.sum((ys - np.mean(ys)) ** 2))
                r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
                span = float(np.max(xs) - np.min(xs))
                score = r2 + 0.01 * w + 0.02 * span
                if best is None or score > best[0]:
                    best = (score, r2, p[0], p[1], xs[0], xs[-1], w)
        if best is None:
            continue
        _, r2, slope, intercept, vmin, vmax, npts = best
        vfb = -intercept / slope if slope != 0 else None
        carrier = None
        if area_cm2 and eps_r and area_cm2 > 0 and eps_r > 0 and slope != 0:
            # Mott-Schottky relation with total capacitance in F: slope = 2/(e eps eps0 A^2 N)
            e = 1.602176634e-19
            eps0 = 8.8541878128e-14  # F/cm
            carrier = 2.0 / (e * eps_r * eps0 * (area_cm2 ** 2) * abs(slope))
        rows.append([f, vmin, vmax, npts, slope, intercept, r2, vfb, "n-type" if slope > 0 else "p-type", carrier])
    # Keep representative decades only, not all 61, to avoid overwhelming Browser/PPT.
    if len(rows) > 18:
        targets = np.geomspace(max(min(r[0] for r in rows), 1.0), max(r[0] for r in rows), 18)
        chosen = []
        used = set()
        for t in targets:
            i = min(range(len(rows)), key=lambda k: abs(math.log10(rows[k][0]) - math.log10(t)) if rows[k][0] > 0 and t > 0 else 1e9)
            if i not in used:
                chosen.append(rows[i]); used.add(i)
        rows = sorted(chosen, key=lambda r: r[0], reverse=True)
    return rows


def analyze_mott_schottky_potential_eis(ms: dict[str, Any]) -> dict[str, Any]:
    arr = np.asarray(ms.get("array"), dtype=float)
    cols = list(ms.get("columns") or [])
    nsteps = int(ms.get("n_potential_steps") or 0)
    nfreq = int(ms.get("n_frequencies") or 0)
    if arr.ndim != 2 or nsteps <= 0 or nfreq <= 0 or len(arr) != nsteps * nfreq:
        return {"status": "invalid_mott_grid"}
    grid = arr.reshape(nsteps, nfreq, arr.shape[1])
    area = _finite(os.environ.get("FARADAIQ_ELECTRODE_AREA_CM2"))
    cdl_uf_cm2 = _finite(os.environ.get("FARADAIQ_CDL_UF_CM2"))
    eps_r = _finite(os.environ.get("FARADAIQ_SEMICONDUCTOR_EPS_R"))
    temp = _finite(os.environ.get("FARADAIQ_TEMPERATURE_K")) or 298.15
    assumed_cdl_F = cdl_uf_cm2 * 1e-6 * area if area and cdl_uf_cm2 else None
    idx_p = cols.index("potential_V") if "potential_V" in cols else None
    summary_rows: list[list[Any]] = []
    model_rows: list[list[Any]] = []
    for si in range(nsteps):
        block = grid[si]
        potential = float(np.nanmedian(block[:, idx_p])) if idx_p is not None else si + 1
        best, fits = _best_circuit_for_step(block, cols)
        decomp = _capacitance_decomposition(block, cols, assumed_cdl_F, area, eps_r)
        best_model = best.get("model")
        rct = best.get("Rct_ohm", best.get("Rct_total_ohm"))
        cap = best.get("Ceff_from_CPE_F", best.get("Ceff_1_F"))
        tau = best.get("tau_eff_s")
        summary_rows.append([
            si + 1, potential, best_model, best.get("fit_status"), best.get("normalized_complex_rmse"), best.get("aicc"),
            best.get("Rs_ohm"), rct, best.get("Q_F_s_n_minus1", best.get("Q1_F_s_n_minus1")), best.get("n_cpe", best.get("n1_cpe")), cap, tau,
            best.get("Warburg_sigma_ohm_s_minus_half"), decomp.get("Ctotal_high_frequency_median_F"), decomp.get("Ctotal_high_frequency_median_uF_cm2"),
            decomp.get("assumed_Cdl_F"), decomp.get("apparent_material_or_quantum_capacitance_F"), decomp.get("apparent_space_charge_width_cm_from_Ctotal"), decomp.get("capacitance_decomposition_status"),
        ])
        for fit in fits:
            model_rows.append([
                si + 1, potential, fit.get("model"), fit.get("fit_status"), fit.get("normalized_complex_rmse"), fit.get("aicc"),
                fit.get("Rs_ohm"), fit.get("Rct_ohm", fit.get("Rct_total_ohm")), fit.get("Q_F_s_n_minus1", fit.get("Q1_F_s_n_minus1")), fit.get("n_cpe", fit.get("n1_cpe")),
                fit.get("Ceff_from_CPE_F", fit.get("Ceff_1_F")), fit.get("Warburg_sigma_ohm_s_minus_half"), fit.get("optimizer_message", fit.get("fit_error")),
            ])
    window_rows = _auto_mott_windows(arr, cols, area, eps_r, temp)
    return {
        "status": "ok",
        "assumptions": {
            "electrode_area_cm2": area,
            "assumed_cdl_uF_cm2": cdl_uf_cm2,
            "assumed_cdl_F": assumed_cdl_F,
            "semiconductor_eps_r": eps_r,
            "temperature_K": temp,
            "quantum_capacitance_policy": "Report apparent material/quantum capacitance only when area and assumed Cdl are supplied; otherwise report total/CPE capacitance and flag the limitation.",
        },
        "summary_columns": ["potential_step_index","potential_V","best_model","fit_status","normalized_complex_rmse","aicc","Rs_ohm","Rct_or_Rpolarization_ohm","Q_or_Q1_F_s_n_minus1","n_or_n1_cpe","Ceff_from_CPE_F","tau_eff_s","Warburg_sigma_ohm_s_minus_half","Ctotal_high_frequency_median_F","Ctotal_high_frequency_median_uF_cm2","assumed_Cdl_F","apparent_material_or_quantum_capacitance_F","apparent_space_charge_width_cm_from_Ctotal","capacitance_decomposition_status"],
        "summary_rows": summary_rows,
        "model_columns": ["potential_step_index","potential_V","model","fit_status","normalized_complex_rmse","aicc","Rs_ohm","Rct_or_Rpolarization_ohm","Q_or_Q1_F_s_n_minus1","n_or_n1_cpe","Ceff_from_CPE_F","Warburg_sigma_ohm_s_minus_half","message"],
        "model_rows": model_rows,
        "mott_window_columns": ["frequency_Hz","window_potential_min_V","window_potential_max_V","n_points","slope_F_minus2_per_V","intercept_F_minus2","r_squared","flatband_potential_V","slope_type","carrier_density_cm_minus3_if_area_eps_supplied"],
        "mott_window_rows": window_rows,
    }


def _export_potential_eis_modeling(parser: Any, output_dir: Path, base_name: str) -> dict[str, Any]:
    output_dir = Path(output_dir)
    safe = _safe_name(base_name)
    out_dir = output_dir / f"{safe}_Potential_EIS_Modeling"
    entries: list[dict[str, Any]] = []
    notes: list[str] = []
    for mi, ms in enumerate(((getattr(parser, "decoded", None) or {}).get("mott_schottky") or []), start=1):
        result = analyze_mott_schottky_potential_eis(ms)
        if result.get("status") != "ok":
            notes.append(f"Mott-Schottky grid {mi}: {result.get('status')}")
            continue
        sum_csv = out_dir / f"{safe}__potential_eis_bestfit_parameters.csv"
        model_csv = out_dir / f"{safe}__potential_eis_candidate_model_fits.csv"
        win_csv = out_dir / f"{safe}__mott_schottky_auto_linear_windows.csv"
        _write_csv(sum_csv, result["summary_columns"], result["summary_rows"])
        _write_csv(model_csv, result["model_columns"], result["model_rows"])
        _write_csv(win_csv, result["mott_window_columns"], result["mott_window_rows"])
        for path, title, ylab in [
            (sum_csv, "Potential-dependent EIS best-fit parameters", "Rs, Rct/Rp, Ceff, tau, Warburg"),
            (model_csv, "Equivalent-circuit candidate comparison", "normalized complex RMSE and AICc"),
            (win_csv, "Mott-Schottky automated linear-window diagnostics", "flat-band and slope diagnostics"),
        ]:
            entries.append({
                "relative_csv_path": str(path.relative_to(output_dir)).replace("\\", "/"),
                "measurement_index": mi,
                "curve_index": len(entries) + 1,
                "measurement_title": ms.get("title", "Mott-Schottky potential-dependent EIS"),
                "curve_title": title,
                "method_id": "potential_dependent_eis_equivalent_circuit_modeling",
                "technique": "POTENTIAL_EIS_MODELING",
                "x_label": "Potential",
                "x_unit": "V",
                "y_label": ylab,
                "y_unit": "model-dependent",
                "n_points": len(result.get("summary_rows") or []),
            })
        overview = {k: v for k, v in result.items() if k not in {"summary_rows", "model_rows", "mott_window_rows"}}
        overview["literature_guidance"] = [
            "Potential-dependent EIS should retain full spectra at every bias, fit candidate equivalent circuits, and only then construct Mott-Schottky/capacitance plots.",
            "CPEs are permitted for nonideal interfaces but are converted to effective capacitance with explicit assumptions.",
            "Quantum/material capacitance is separable from measured total capacitance only with independent double-layer capacitance/area or a model for DOS; otherwise the safe output is apparent total capacitance and a flagged limitation.",
        ]
        (out_dir / f"{safe}__potential_eis_modeling_overview.json").write_text(json.dumps(overview, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"n_csv_files": len(entries), "csv_entries": entries, "skipped_entries": [], "potential_eis_modeling_dir": str(out_dir), "notes": notes}


def install(core: MutableMapping[str, Any]) -> None:
    Parser = core.get("VersaStudioParser")
    if Parser is not None and not getattr(Parser, "_v315_potential_eis_modeling_installed", False):
        old_export = getattr(Parser, "export_analysis_ready_outputs", None)
        def export_v315(self, output_dir: Path, base_name: str) -> dict[str, Any]:
            merged = {"n_csv_files": 0, "csv_entries": [], "skipped_entries": []}
            if callable(old_export):
                try:
                    old = old_export(self, output_dir, base_name) or {}
                    merged["n_csv_files"] += int(old.get("n_csv_files", 0))
                    merged["csv_entries"].extend(old.get("csv_entries", []) or [])
                    merged["skipped_entries"].extend(old.get("skipped_entries", []) or [])
                    for k, v in old.items():
                        if k not in merged:
                            merged[k] = v
                except Exception as exc:
                    merged["skipped_entries"].append({"reason": f"legacy/custom export failed before v315 potential-EIS modeling: {exc}"})
            try:
                new = _export_potential_eis_modeling(self, Path(output_dir), base_name) or {}
                merged["n_csv_files"] += int(new.get("n_csv_files", 0))
                merged["csv_entries"].extend(new.get("csv_entries", []) or [])
                merged["skipped_entries"].extend(new.get("skipped_entries", []) or [])
                if new.get("potential_eis_modeling_dir"):
                    merged["potential_eis_modeling_dir"] = new.get("potential_eis_modeling_dir")
            except Exception as exc:
                merged["skipped_entries"].append({"reason": f"v315 potential-dependent EIS modeling failed: {exc}"})
            return merged
        Parser._v315_export_base = old_export
        Parser.export_analysis_ready_outputs = export_v315
        Parser._v315_potential_eis_modeling_installed = True

    try:
        import faradaiq.v290_powerpoint_summary as ppt
        ppt.VERSION = VERSION
        ppt.APP_TITLE = f"FaradaIQ Studio {VERSION}"
        ppt.FRONT_SLIDE_FOOTER = f"Made by FaradaIQ Studio {VERSION}"
        ppt.TECHNIQUE_SCENARIOS["POTENTIAL_EIS_MODELING"] = [
            ("Equivalent-circuit parameter maps", "Track Rs, Rct/Rp, CPE-derived capacitance, tau, and Warburg terms versus applied bias."),
            ("Model quality", "Compare candidate circuits with normalized complex residuals and AICc before assigning elements."),
            ("Capacitance decomposition", "Separate measured total capacitance from assumed Cdl/space-charge/quantum terms only when independent assumptions are supplied."),
            ("Mott-Schottky fit windows", "Report automatic linear-window diagnostics and flat-band estimates as review prompts, not final claims."),
        ]
        ppt.TECHNIQUE_SLIDE_RECIPES_V304["POTENTIAL_EIS_MODELING"] = {
            "label": "Potential-dependent EIS modeling",
            "core": ["Rs/Rct/Ceff vs potential", "candidate model AICc/RMSE", "Mott-Schottky window diagnostics", "DRT peaks vs fitted tau", "capacitance decomposition caveats"],
            "appendix": ["all fitted circuit parameters", "fit residuals", "assumption table for Cdl/area/epsilon"],
        }
    except Exception:
        pass
    try:
        import faradaiq.v290_portable_browser as browser
        if not getattr(browser, "_v315_potential_eis_modeling_installed", False):
            old_tech = browser._technique_key
            old_plot = browser._plot_views
            def tech_v315(value: Any, *, path: Any = "", columns: Sequence[Any] = (), role: Any = "") -> str:
                text = " ".join(str(x or "") for x in (value, path, role)).upper()
                norms = {browser._normalize_column(c) for c in columns}
                if "POTENTIAL_EIS" in text or "EQUIVALENT_CIRCUIT" in text or {"rct_or_rpolarization_ohm", "ceff_from_cpe_f"} & norms:
                    return "POTENTIAL_EIS_MODELING"
                return old_tech(value, path=path, columns=columns, role=role)
            def plot_v315(technique: str, columns: Sequence[Any], numeric: Sequence[Any], *, path_hint: str = ""):
                if str(technique).upper() != "POTENTIAL_EIS_MODELING":
                    return old_plot(technique, columns, numeric, path_hint=path_hint)
                find = browser._find_column
                p = find(columns, ["potential_V", "potential", "E_V"])
                views = []
                def add(id_, label, y, yscale="linear", priority=5):
                    if p is None or y is None: return
                    views.append({"id": id_, "label": label, "x_index": p, "y_index": y, "x_column": str(columns[p]), "y_column": str(columns[y]), "x_scale": "linear", "y_scale": yscale, "plot_type": "curve", "synthetic_x": False, "priority": priority, "description": label})
                add("rct_vs_potential", "Rct/Rp vs potential", find(columns, ["Rct_or_Rpolarization_ohm", "Rct_ohm"]), "log", 1)
                add("ceff_vs_potential", "CPE-derived capacitance vs potential", find(columns, ["Ceff_from_CPE_F", "Ceff_1_F"]), "log", 2)
                add("rs_vs_potential", "Solution resistance vs potential", find(columns, ["Rs_ohm"]), "linear", 3)
                add("tau_vs_potential", "Characteristic time constant vs potential", find(columns, ["tau_eff_s"]), "log", 4)
                add("warburg_vs_potential", "Warburg coefficient vs potential", find(columns, ["Warburg_sigma_ohm_s_minus_half"]), "log", 5)
                add("fit_rmse_vs_potential", "Fit residual vs potential", find(columns, ["normalized_complex_rmse"]), "log", 6)
                add("apparent_quantum_cap_vs_potential", "Apparent material/quantum capacitance vs potential", find(columns, ["apparent_material_or_quantum_capacitance_F"]), "log", 7)
                return views or old_plot("SUMMARY", columns, numeric, path_hint=path_hint)
            browser._technique_key = tech_v315
            browser._plot_views = plot_v315
            try:
                browser.TECHNIQUE_REGISTRY["POTENTIAL_EIS_MODELING"] = {"label": "Potential-dependent EIS modeling", "family": "EIS analysis", "kind": "analysis"}
            except Exception:
                pass
            browser._v315_potential_eis_modeling_installed = True
    except Exception:
        pass
    core["FARADAIQ_DEFAULT_OUTPUT_DIR"] = DEFAULT_OUTPUT_DIR
