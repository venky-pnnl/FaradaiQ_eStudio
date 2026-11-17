"""FaradaIQ Studio v298 robust corrosion metrics point/spatial workflow.

Deterministic screening of selected polarization/Tafel traces.  The workflow is
purposefully conservative: it reports ambiguity rather than manufacturing a
single Ecorr/Icorr when the trace contains multiple crossings/minima or never
crosses zero within the acquired potential window.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

import numpy as np
import pandas as pd

try:
    from scipy.signal import find_peaks
    from scipy.ndimage import median_filter
except Exception:  # pragma: no cover
    find_peaks = None
    median_filter = None

VERSION = "v300"
_CORE: MutableMapping[str, Any] = {}


@dataclass
class CorrosionSettings:
    surface_area_cm2: float | None = None
    equivalent_weight_g_per_equiv: float | None = None
    density_g_cm3: float | None = None
    crossing_cluster_mV: float = 4.0
    minimum_dip_separation_mV: float = 10.0
    minimum_dip_prominence_dec: float = 0.10
    fit_max_seed_offset_mV: float = 90.0


def _g(name: str, default: Any = None) -> Any:
    return _CORE.get(name, default)


def _browser(source: Any) -> Any:
    return getattr(source, "gui", source)


def _normpath(path: Path | str) -> str:
    try:
        return os.path.normcase(str(Path(path).expanduser().resolve()))
    except Exception:
        return os.path.normcase(str(path))


def _point_from_path(path: Path) -> int | None:
    for text in (path.name, path.as_posix()):
        m = re.search(r"(?i)(?:point|position|pos)[_\- ]*0*(\d+)", text)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass
    return None


def selected_corrosion_paths(source: Any) -> list[Path]:
    selector = _g("_v130_selected_iv_lsv_paths") or _g("_v138_selected_iv_lsv_paths")
    browser = _browser(source)
    try:
        paths = list(selector(browser, generous=True)) if callable(selector) else []
    except Exception:
        paths = []
    out: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        path = Path(raw)
        key = _normpath(path)
        if path.is_file() and key not in seen:
            out.append(path); seen.add(key)
    return out


def _spatial_metadata(source: Any, paths: Sequence[Path]) -> dict[str, dict[str, Any]]:
    browser = _browser(source)
    records = list(getattr(browser, "_catalog_records", []) or [])
    try:
        from .spatial_points_v297 import enrich_catalog_records
        enrich_catalog_records(records, search_roots=[getattr(browser, "analyzed_root", ""), getattr(browser, "cache_root", ""), getattr(browser, "bundle_root", "")])
    except Exception:
        pass
    by_path: dict[str, Mapping[str, Any]] = {}
    by_point: dict[int, Mapping[str, Any]] = {}
    for rec in records:
        raw = str(rec.get("path") or "")
        if raw:
            by_path[_normpath(raw)] = rec
        try:
            pt = int(rec.get("point") or 0)
        except Exception:
            pt = 0
        if pt > 0 and rec.get("spatial_coordinate_available"):
            by_point.setdefault(pt, rec)
    out: dict[str, dict[str, Any]] = {}
    for path in paths:
        rec = by_path.get(_normpath(path), {})
        pt = None
        try: pt = int(rec.get("point") or 0) or None
        except Exception: pass
        if pt is None: pt = _point_from_path(path)
        spatial = by_point.get(int(pt or 0), rec)
        out[_normpath(path)] = {
            "point_index": pt,
            "x_position_m": spatial.get("x_position_m"),
            "y_position_m": spatial.get("y_position_m"),
            "scan_distance_m": spatial.get("scan_distance_m"),
            "coordinate_source": spatial.get("spatial_coordinate_source") or spatial.get("coordinate_source") or "",
            "coordinate_status": spatial.get("spatial_coordinate_status") or spatial.get("coordinate_status") or "",
            "coordinate_is_synthetic": bool(spatial.get("spatial_coordinate_is_synthetic")),
        }
    return out


def _read_e_i(path: Path) -> tuple[np.ndarray, np.ndarray, str, str]:
    import faradaiq_tafel_adapter as tafel
    df = tafel._read_text_table(path)
    pot_col, cur_col, _warnings = tafel._detect_columns(df)
    E = pd.to_numeric(df[pot_col], errors="coerce").to_numpy(float)
    I = pd.to_numeric(df[cur_col], errors="coerce").to_numpy(float)
    # Match the adapter's unit handling rather than assuming canonical headers.
    try:
        E = E * float(tafel._unit_scale_from_name(pot_col, "potential", E))
        I = I * float(tafel._unit_scale_from_name(cur_col, "current", I))
    except Exception:
        pass
    finite = np.isfinite(E) & np.isfinite(I)
    E, I = E[finite], I[finite]
    if len(E) < 8:
        raise ValueError("too few finite potential/current samples")
    return E, I, str(pot_col), str(cur_col)


def _zero_crossings(E: np.ndarray, I: np.ndarray) -> list[float]:
    vals: list[float] = []
    for k in range(len(I) - 1):
        a, b = float(I[k]), float(I[k + 1])
        ea, eb = float(E[k]), float(E[k + 1])
        if not all(map(math.isfinite, (a, b, ea, eb))):
            continue
        if a == 0.0:
            vals.append(ea); continue
        if b == 0.0 or a * b < 0.0:
            den = b - a
            vals.append(ea if abs(den) < 1e-300 else ea + (-a) * (eb - ea) / den)
    return vals


def _cluster(values: Sequence[float], tolerance_V: float) -> list[float]:
    vals = sorted(float(x) for x in values if math.isfinite(float(x)))
    if not vals: return []
    groups: list[list[float]] = [[vals[0]]]
    for value in vals[1:]:
        if value - groups[-1][-1] <= tolerance_V:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [float(np.median(g)) for g in groups]


def _dip_candidates(E: np.ndarray, I: np.ndarray, settings: CorrosionSettings) -> tuple[list[dict[str, float]], int, bool]:
    order = np.argsort(E, kind="stable")
    x = np.asarray(E[order], float); y = np.asarray(I[order], float)
    floor = max(1e-30, float(np.nanmax(np.abs(y))) * 1e-15)
    logi = np.log10(np.clip(np.abs(y), floor, None))
    n = len(x)
    kernel = max(3, min(11, (n // 40) * 2 + 3))
    if kernel % 2 == 0: kernel += 1
    smooth = median_filter(logi, size=kernel, mode="nearest") if median_filter is not None else logi
    resid = logi - smooth
    mad = float(np.nanmedian(np.abs(resid - np.nanmedian(resid)))) if len(resid) else 0.0
    prominence = max(float(settings.minimum_dip_prominence_dec), 6.0 * mad)
    med_step = float(np.nanmedian(np.abs(np.diff(x)))) if n > 1 else 0.0
    distance_n = max(2, int(round((settings.minimum_dip_separation_mV * 1e-3) / max(med_step, 1e-12))))
    indices: list[int] = []
    prominences: list[float] = []
    if find_peaks is not None and n >= 7:
        idx, props = find_peaks(-smooth, prominence=prominence, distance=distance_n)
        indices = [int(i) for i in idx]
        prominences = [float(v) for v in props.get("prominences", np.zeros(len(idx)))]
    global_idx = int(np.nanargmin(logi))
    boundary = global_idx <= max(1, int(0.02*n)) or global_idx >= n - 1 - max(1, int(0.02*n))
    if global_idx not in indices:
        indices.append(global_idx); prominences.append(0.0)
    rows = sorted(({"potential_V": float(x[i]), "log10_abs_current_A": float(logi[i]), "prominence_dec": float(p)} for i,p in zip(indices,prominences)), key=lambda r: r["potential_V"])
    return rows, global_idx, boundary


def _choose_branch_fits(E: np.ndarray, I: np.ndarray, seed_ecorr: float):
    import faradaiq_tafel_adapter as tafel
    # 1 cm2 is only an algebraic scale here: fitted current is therefore in A.
    settings = tafel.TafelSettings(surface_area_cm2=1.0, eeq_V=float(seed_ecorr), current_adjust=False, enable_bootstrap=True, bootstrap_samples=100)
    frame = pd.DataFrame({"E_V": E, "current_A": I})
    frame["acquisition_index"] = np.arange(len(frame), dtype=int)
    frame["eta_V"] = frame["E_V"] - float(seed_ecorr)
    frame["j_A_cm2"] = frame["current_A"]
    frame["abs_j_A_cm2"] = np.abs(frame["current_A"])
    floor = max(1e-30, float(np.nanmax(frame["abs_j_A_cm2"])) * 1e-15)
    frame["log10_abs_j_A_cm2"] = np.log10(np.clip(frame["abs_j_A_cm2"], floor, None))
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna().sort_values("eta_V").reset_index(drop=True)
    stable = tafel.stable_window_fits(frame, settings)
    auto = tafel.auto_linear_fits(frame, settings)
    chosen = {}
    for branch in ("cathodic", "anodic"):
        good = [f for f in stable if f.branch == branch and f.slope_dec_per_V is not None]
        if not good:
            good = [f for f in auto if f.branch == branch and f.slope_dec_per_V is not None]
        chosen[branch] = good[0] if good else None
    return chosen, frame


def analyze_trace(path: Path | str, settings: CorrosionSettings | None = None, spatial: Mapping[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or CorrosionSettings()
    path = Path(path)
    E, I, pot_col, cur_col = _read_e_i(path)
    crossings_raw = _zero_crossings(E, I)
    crossing_clusters = _cluster(crossings_raw, settings.crossing_cluster_mV * 1e-3)
    dips, global_idx_sorted, boundary_min = _dip_candidates(E, I, settings)
    global_min_E = min(dips, key=lambda r: r["log10_abs_current_A"])["potential_V"]
    if crossing_clusters:
        seed = min(crossing_clusters, key=lambda e: abs(e - global_min_E))
    else:
        seed = float(global_min_E)

    fits, frame = _choose_branch_fits(E, I, seed)
    cath, anod = fits.get("cathodic"), fits.get("anodic")
    fit_ecorr = None; icorr_A = None; fit_warning = ""
    if cath is not None and anod is not None:
        mc, ma = float(cath.slope_dec_per_V), float(anod.slope_dec_per_V)
        bc, ba = float(cath.intercept_log10_j0), float(anod.intercept_log10_j0)
        den = mc - ma
        if abs(den) > 1e-12:
            eta_cross = (ba - bc) / den
            candidate = float(seed + eta_cross)
            logI = mc * eta_cross + bc
            if min(E) <= candidate <= max(E) and abs(candidate - seed) <= settings.fit_max_seed_offset_mV * 1e-3 and math.isfinite(logI):
                fit_ecorr = candidate
                icorr_A = float(10.0 ** logI)
            else:
                fit_warning = "Tafel branch intersection falls outside the accepted Ecorr neighborhood"
    ecorr = float(fit_ecorr if fit_ecorr is not None else seed)

    flags: list[str] = []
    if len(crossing_clusters) > 1: flags.append("MULTIPLE_ZERO_CROSSINGS")
    # A corrosion-relevant competing dip must be both locally prominent and
    # reasonably close to the deepest low-current region.  This avoids calling
    # ordinary high-current shoulders several decades above Imin a second Ecorr.
    deepest_logi = min(float(d.get("log10_abs_current_A")) for d in dips) if dips else float("inf")
    prominent = [d for d in dips if d.get("prominence_dec", 0.0) >= settings.minimum_dip_prominence_dec and float(d.get("log10_abs_current_A")) <= deepest_logi + 1.25]
    if len(prominent) > 1: flags.append("MULTIPLE_DIPS")
    if not crossing_clusters: flags.append("NO_ZERO_CROSSING")
    if boundary_min: flags.append("BOUNDARY_MINIMUM")
    if fit_warning: flags.append("FIT_INCONSISTENT")
    if cath is None: flags.append("NO_ROBUST_CATHODIC_WINDOW")
    if anod is None: flags.append("NO_ROBUST_ANODIC_WINDOW")
    if not flags: flags.append("CLEAR")
    ambiguity = "AMBIGUOUS" if any(x.startswith("MULTIPLE") for x in flags) else ("NO_CLEAR_ECORR" if "NO_ZERO_CROSSING" in flags and "BOUNDARY_MINIMUM" in flags else "REVIEW" if flags != ["CLEAR"] else "CLEAR")
    # A boundary minimum with no sign crossing is not an Ecorr measurement. Keep
    # the candidate seed for diagnostics, but leave reported Ecorr/Icorr blank.
    if ambiguity == "NO_CLEAR_ECORR":
        ecorr = None
        icorr_A = None
        fit_ecorr = None

    area = settings.surface_area_cm2 if settings.surface_area_cm2 and settings.surface_area_cm2 > 0 else None
    icorr_A_cm2 = float(icorr_A / area) if icorr_A is not None and area is not None else None
    corrosion_rate = None
    ew = settings.equivalent_weight_g_per_equiv if settings.equivalent_weight_g_per_equiv and settings.equivalent_weight_g_per_equiv > 0 else None
    density = settings.density_g_cm3 if settings.density_g_cm3 and settings.density_g_cm3 > 0 else None
    if icorr_A_cm2 is not None and ew is not None and density is not None:
        corrosion_rate = float(3.27e-3 * (icorr_A_cm2 * 1e6) * ew / density)

    spatial = dict(spatial or {})
    point = spatial.get("point_index") or _point_from_path(path)
    return {
        "point_index": point,
        "x_position_m": spatial.get("x_position_m"), "y_position_m": spatial.get("y_position_m"), "scan_distance_m": spatial.get("scan_distance_m"),
        "coordinate_source": spatial.get("coordinate_source", ""), "coordinate_status": spatial.get("coordinate_status", ""), "coordinate_is_synthetic": bool(spatial.get("coordinate_is_synthetic")),
        "source_path": str(path), "source_filename": path.name,
        "potential_column": pot_col, "current_column": cur_col, "n_samples": int(len(E)), "potential_min_V": float(np.min(E)), "potential_max_V": float(np.max(E)),
        "ecorr_V": ecorr, "ecorr_seed_V": float(seed), "ecorr_fit_intersection_V": fit_ecorr,
        "icorr_A": icorr_A, "icorr_A_cm2": icorr_A_cm2,
        "cathodic_tafel_mV_dec": abs(float(cath.tafel_slope_mV_dec)) if cath and cath.tafel_slope_mV_dec is not None else None,
        "anodic_tafel_mV_dec": abs(float(anod.tafel_slope_mV_dec)) if anod and anod.tafel_slope_mV_dec is not None else None,
        "cathodic_r2": float(cath.r2) if cath and cath.r2 is not None else None, "anodic_r2": float(anod.r2) if anod and anod.r2 is not None else None,
        "cathodic_quality": str(getattr(cath, "quality_grade", "") or "") if cath else "", "anodic_quality": str(getattr(anod, "quality_grade", "") or "") if anod else "",
        "zero_crossing_count_raw": len(crossings_raw), "zero_crossing_cluster_count": len(crossing_clusters), "zero_crossing_candidates_V": crossing_clusters,
        "dip_count": len(prominent), "dip_candidates": dips,
        "ambiguity_status": ambiguity, "flags": flags, "fit_warning": fit_warning,
        "surface_area_cm2": area, "equivalent_weight_g_per_equiv": ew, "density_g_cm3": density,
        "corrosion_rate_mm_per_year": corrosion_rate,
    }


def _output_root(source: Any) -> Path:
    browser = _browser(source)
    base = Path(getattr(browser, "analyzed_root", "") or getattr(browser, "cache_root", "") or Path.cwd())
    base.mkdir(parents=True, exist_ok=True)
    return base / "corrosion_metrics"


def analyze_selected(source: Any, settings: CorrosionSettings | None = None, *, write_outputs: bool = True) -> dict[str, Any]:
    settings = settings or CorrosionSettings()
    paths = selected_corrosion_paths(source)
    if not paths:
        raise ValueError("Select IV / Tafel / polarization files first.")
    spatial = _spatial_metadata(source, paths)
    rows: list[dict[str, Any]] = []; errors: list[str] = []
    for i, path in enumerate(paths, 1):
        try:
            rows.append(analyze_trace(path, settings, spatial.get(_normpath(path), {})))
        except Exception as exc:
            errors.append(f"{path}: {type(exc).__name__}: {exc}")
    if not rows:
        raise ValueError("No selected polarization trace could be analyzed. " + (errors[0] if errors else ""))
    rows.sort(key=lambda r: (r.get("point_index") is None, r.get("point_index") or 10**9, r.get("source_path", "")))
    # Resolve a useful horizontal axis using the same spatial contract as OCP.
    axis = "Point"
    try:
        from .spatial_points_v297 import auto_axis_for_records
        axis = auto_axis_for_records(rows)
    except Exception:
        pass
    result = {"schema": "faradaiq-corrosion-metrics-v298", "version": VERSION, "settings": asdict(settings), "horizontal_axis": axis, "rows": rows, "errors": errors}
    if write_outputs:
        out = _output_root(source)
        out.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        csv_path = out / f"robust_corrosion_metrics_{stamp}.csv"
        dip_path = out / f"robust_corrosion_dip_candidates_{stamp}.csv"
        json_path = out / f"robust_corrosion_metrics_{stamp}.json"
        scalar_fields = [
            "point_index","x_position_m","y_position_m","scan_distance_m","ecorr_V","ecorr_seed_V","ecorr_fit_intersection_V","icorr_A","icorr_A_cm2",
            "cathodic_tafel_mV_dec","anodic_tafel_mV_dec","cathodic_r2","anodic_r2","cathodic_quality","anodic_quality","corrosion_rate_mm_per_year",
            "zero_crossing_count_raw","zero_crossing_cluster_count","dip_count","ambiguity_status","flags","coordinate_source","coordinate_status","source_filename","source_path"
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=scalar_fields); w.writeheader()
            for row in rows:
                flat = {k: row.get(k) for k in scalar_fields}; flat["flags"] = ";".join(row.get("flags", [])); w.writerow(flat)
        with dip_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["point_index","candidate_type","potential_V","prominence_dec","source_path"]); w.writeheader()
            for row in rows:
                for e in row.get("zero_crossing_candidates_V", []): w.writerow({"point_index":row.get("point_index"),"candidate_type":"zero_crossing","potential_V":e,"prominence_dec":"","source_path":row.get("source_path")})
                for d in row.get("dip_candidates", []): w.writerow({"point_index":row.get("point_index"),"candidate_type":"log_abs_I_minimum","potential_V":d.get("potential_V"),"prominence_dec":d.get("prominence_dec"),"source_path":row.get("source_path")})
        json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        result.update({"output_dir": str(out), "metrics_csv": str(csv_path), "dip_candidates_csv": str(dip_path), "summary_json": str(json_path)})
    return result


def _fmt(value: Any, digits: int = 5) -> str:
    try:
        v=float(value); return f"{v:.{digits}g}" if math.isfinite(v) else ""
    except Exception: return ""


def _open_folder(path: str) -> None:
    try:
        if os.name == "nt": os.startfile(path)  # type: ignore[attr-defined]
        else:
            import subprocess; subprocess.Popen(["xdg-open", path])
    except Exception: pass


def _apply_material_constants(result: MutableMapping[str, Any], area: float | None, ew: float | None, density: float | None) -> None:
    area = float(area) if area is not None and float(area) > 0 else None
    ew = float(ew) if ew is not None and float(ew) > 0 else None
    density = float(density) if density is not None and float(density) > 0 else None
    for row in result.get("rows", []):
        Icorr = row.get("icorr_A")
        try: Icorr = float(Icorr) if Icorr is not None and math.isfinite(float(Icorr)) else None
        except Exception: Icorr = None
        row["surface_area_cm2"] = area; row["equivalent_weight_g_per_equiv"] = ew; row["density_g_cm3"] = density
        row["icorr_A_cm2"] = (Icorr / area) if Icorr is not None and area is not None else None
        row["corrosion_rate_mm_per_year"] = (3.27e-3 * row["icorr_A_cm2"] * 1e6 * ew / density) if row.get("icorr_A_cm2") is not None and ew is not None and density is not None else None
    result.setdefault("settings", {}).update({"surface_area_cm2": area, "equivalent_weight_g_per_equiv": ew, "density_g_cm3": density})


def _rewrite_metrics_output(result: Mapping[str, Any]) -> None:
    raw = result.get("metrics_csv")
    if not raw: return
    path=Path(str(raw)); path.parent.mkdir(parents=True, exist_ok=True)
    fields=["point_index","x_position_m","y_position_m","scan_distance_m","ecorr_V","ecorr_seed_V","ecorr_fit_intersection_V","icorr_A","icorr_A_cm2","cathodic_tafel_mV_dec","anodic_tafel_mV_dec","cathodic_r2","anodic_r2","cathodic_quality","anodic_quality","corrosion_rate_mm_per_year","surface_area_cm2","equivalent_weight_g_per_equiv","density_g_cm3","zero_crossing_count_raw","zero_crossing_cluster_count","dip_count","ambiguity_status","flags","coordinate_source","coordinate_status","source_filename","source_path"]
    with path.open("w",newline="",encoding="utf-8") as fh:
        w=csv.DictWriter(fh,fieldnames=fields); w.writeheader()
        for row in result.get("rows",[]):
            flat={k:row.get(k) for k in fields}; flat["flags"]=";".join(row.get("flags",[])); w.writerow(flat)
    if result.get("summary_json"):
        Path(str(result["summary_json"])).write_text(json.dumps(dict(result),indent=2,default=str),encoding="utf-8")


def _show_result(source: Any, result: MutableMapping[str, Any]) -> Any:
    tk, ttk = _g("tk"), _g("ttk")
    if tk is None or ttk is None: return result
    browser = _browser(source)
    win = tk.Toplevel(browser)
    win.title(f"FaradaIQ Studio {_g('FARADAIQ_PUBLIC_VERSION', VERSION)} - robust corrosion metrics")
    win.geometry("1240x820")
    try: _g("_v36_apply_theme")(win, getattr(browser, "theme_var", None).get() if getattr(browser, "theme_var", None) else "dark")
    except Exception: pass
    outer=ttk.Frame(win,padding=10); outer.pack(fill="both",expand=True)
    rows=list(result.get("rows",[])); ambiguous=[r for r in rows if r.get("ambiguity_status") != "CLEAR"]
    ttk.Label(outer,text=f"Robust corrosion metrics · {len(rows)} polarization trace(s)",style="Section.TLabel").pack(anchor="w")
    if ambiguous:
        pts=", ".join(f"P{int(r['point_index']):03d}" if r.get('point_index') else Path(r.get('source_path','')).stem for r in ambiguous[:20])
        ttk.Label(outer,text=f"Review required: {len(ambiguous)} trace(s) contain multiple corrosion-region minima/crossings, no in-range zero crossing, a boundary minimum, or weak/inconsistent branch fits: {pts}",style="Muted.TLabel",wraplength=1180).pack(anchor="w",fill="x",pady=(2,6))
    else:
        ttk.Label(outer,text="No ambiguity flags were raised by deterministic screening. Fit quality and experimental context still require scientific review.",style="Muted.TLabel").pack(anchor="w",pady=(2,6))

    controls=ttk.LabelFrame(outer,text="Spatial view and corrosion-rate inputs",padding=(6,5)); controls.pack(fill="x",pady=(0,6))
    metric_var=tk.StringVar(value="Ecorr / V")
    axis_var=tk.StringVar(value=str(result.get("horizontal_axis") or "Point"))
    area_var=tk.StringVar(value=""); ew_var=tk.StringVar(value=""); density_var=tk.StringVar(value="")
    ttk.Label(controls,text="Metric").pack(side="left")
    ttk.Combobox(controls,textvariable=metric_var,state="readonly",width=22,values=("Ecorr / V","Icorr / A","icorr / A cm⁻²","Cathodic βc / mV dec⁻¹","Anodic βa / mV dec⁻¹","Corrosion rate / mm y⁻¹")).pack(side="left",padx=(4,10))
    ttk.Label(controls,text="X axis").pack(side="left")
    ttk.Combobox(controls,textvariable=axis_var,state="readonly",width=10,values=("Point","X","Y","Distance")).pack(side="left",padx=(4,12))
    ttk.Label(controls,text="Area cm²").pack(side="left"); ttk.Entry(controls,textvariable=area_var,width=8).pack(side="left",padx=(3,8))
    ttk.Label(controls,text="Eq. wt g/eq").pack(side="left"); ttk.Entry(controls,textvariable=ew_var,width=8).pack(side="left",padx=(3,8))
    ttk.Label(controls,text="Density g/cm³").pack(side="left"); ttk.Entry(controls,textvariable=density_var,width=8).pack(side="left",padx=(3,8))

    plotframe=ttk.Frame(outer); plotframe.pack(fill="both",expand=True)
    fig=ax=canvas=None
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        fig=Figure(figsize=(9.0,3.1),dpi=100); ax=fig.add_subplot(111)
        canvas=FigureCanvasTkAgg(fig,master=plotframe); canvas.get_tk_widget().pack(fill="both",expand=True)
    except Exception: pass

    cols=("pt","x","y","dist","ecorr","Icorr","icorr","bc","ba","cr","status","cross","dips","source")
    tree=ttk.Treeview(outer,columns=cols,show="headings",height=13)
    headings={"pt":"Point","x":"X / µm","y":"Y / µm","dist":"Distance / µm","ecorr":"Ecorr / V","Icorr":"Icorr / A","icorr":"icorr / A cm⁻²","bc":"βc / mV dec⁻¹","ba":"βa / mV dec⁻¹","cr":"Corrosion / mm y⁻¹","status":"Status","cross":"Crossings","dips":"Dips","source":"Source"}
    widths={"pt":50,"x":65,"y":65,"dist":75,"ecorr":78,"Icorr":88,"icorr":95,"bc":95,"ba":95,"cr":100,"status":110,"cross":60,"dips":48,"source":160}
    for c in cols: tree.heading(c,text=headings[c]); tree.column(c,width=widths[c],anchor="w")
    try: tree.tag_configure("review",foreground="#ffcc55")
    except Exception: pass
    tree.pack(fill="x",pady=(6,0))

    metric_key={"Ecorr / V":"ecorr_V","Icorr / A":"icorr_A","icorr / A cm⁻²":"icorr_A_cm2","Cathodic βc / mV dec⁻¹":"cathodic_tafel_mV_dec","Anodic βa / mV dec⁻¹":"anodic_tafel_mV_dec","Corrosion rate / mm y⁻¹":"corrosion_rate_mm_per_year"}
    ylabels={"ecorr_V":"Ecorr / V","icorr_A":"Icorr / A","icorr_A_cm2":"icorr / A cm⁻²","cathodic_tafel_mV_dec":"Cathodic βc / mV dec⁻¹","anodic_tafel_mV_dec":"Anodic βa / mV dec⁻¹","corrosion_rate_mm_per_year":"Corrosion rate / mm y⁻¹"}
    axis_keys={"X":"x_position_m","Y":"y_position_m","Distance":"scan_distance_m"}

    def repopulate():
        for iid in tree.get_children(): tree.delete(iid)
        for row in rows:
            um=lambda k:_fmt(float(row[k])*1e6) if row.get(k) is not None else ""
            vals=(row.get("point_index") or "",um("x_position_m"),um("y_position_m"),um("scan_distance_m"),_fmt(row.get("ecorr_V")),_fmt(row.get("icorr_A")),_fmt(row.get("icorr_A_cm2")),_fmt(row.get("cathodic_tafel_mV_dec"),4),_fmt(row.get("anodic_tafel_mV_dec"),4),_fmt(row.get("corrosion_rate_mm_per_year")),row.get("ambiguity_status"),row.get("zero_crossing_cluster_count"),row.get("dip_count"),row.get("source_filename"))
            tree.insert("","end",values=vals,tags=("review",) if row.get("ambiguity_status") != "CLEAR" else ())

    def redraw(*_a):
        if ax is None or canvas is None: return
        ax.clear(); mkey=metric_key.get(metric_var.get(),"ecorr_V"); akey=axis_keys.get(axis_var.get())
        goodx=[]; goody=[]; badx=[]; bady=[]
        for idx,row in enumerate(rows,1):
            value=row.get(mkey)
            try: y=float(value)
            except Exception: continue
            if not math.isfinite(y): continue
            x=row.get(akey) if akey else row.get("point_index") or idx
            try: x=float(x)*(1e6 if akey else 1.0)
            except Exception: x=float(row.get("point_index") or idx)
            goodx.append(x); goody.append(y)
            if row.get("ambiguity_status") != "CLEAR": badx.append(x); bady.append(y)
        if goodx: ax.plot(goodx,goody,"o-",label=metric_var.get())
        if badx: ax.scatter(badx,bady,marker="x",s=70,label="Review / ambiguous")
        ax.set_ylabel(ylabels.get(mkey,mkey)); ax.set_xlabel(f"{axis_var.get()} / µm" if akey else "Point / acquisition position"); ax.grid(True,alpha=.25)
        if goodx: ax.legend(loc="best")
        canvas.draw_idle()

    def parse_optional(var):
        text=str(var.get() or "").strip()
        if not text: return None
        value=float(text)
        if not math.isfinite(value) or value <= 0: raise ValueError("material constants must be positive finite values")
        return value

    def apply_material():
        try:
            area=parse_optional(area_var); ew=parse_optional(ew_var); density=parse_optional(density_var)
            _apply_material_constants(result,area,ew,density); _rewrite_metrics_output(result); repopulate(); redraw()
            try:
                from .report_results_v300 import refresh_existing_reports
                threading.Thread(target=refresh_existing_reports,args=(source,),name="FaradaIQReportRefreshCorrosion",daemon=True).start()
            except Exception:
                pass
        except Exception as exc:
            mb=_g("messagebox")
            if mb:
                try: mb.showerror("Corrosion-rate inputs",str(exc),parent=win)
                except Exception: pass

    ttk.Button(controls,text="Apply / recalculate",command=apply_material).pack(side="right",padx=(8,0))
    metric_var.trace_add("write",redraw); axis_var.trace_add("write",redraw)
    repopulate(); redraw()
    foot=ttk.Frame(outer); foot.pack(fill="x",pady=(7,0))
    ttk.Label(foot,text="Ecorr/Icorr and βc/βa are obtained from robust branch screening. Multiple corrosion-region minima/crossings are flagged rather than collapsed silently. Penetration rate uses the conventional 3.27×10⁻³·icorr(µA/cm²)·EW/ρ conversion only after you explicitly supply area, equivalent weight, and density.",style="Muted.TLabel",wraplength=920).pack(side="left",fill="x",expand=True)
    if result.get("output_dir"): ttk.Button(foot,text="Open output folder",command=lambda:_open_folder(str(result.get("output_dir")))).pack(side="right")
    return win

def open_corrosion_metrics(source: Any) -> Any:
    browser=_browser(source); messagebox=_g("messagebox")
    paths=selected_corrosion_paths(source)
    if not paths:
        if messagebox:
            try: messagebox.showinfo("Robust corrosion metrics","Select IV / Tafel / polarization data first.",parent=browser)
            except Exception: pass
        return None
    # Do computation outside the Tk event loop. The first deterministic pass does
    # not assume electrode area/material constants; users retain Icorr in A.
    progress = None
    tk,ttk=_g("tk"),_g("ttk")
    if tk and ttk:
        progress=tk.Toplevel(browser); progress.title(f"FaradaIQ Studio {_g('FARADAIQ_PUBLIC_VERSION',VERSION)} - corrosion metrics")
        frame=ttk.Frame(progress,padding=14); frame.pack(fill="both",expand=True)
        ttk.Label(frame,text=f"Analyzing {len(paths)} selected polarization trace(s)…",style="Section.TLabel").pack(anchor="w")
        ttk.Label(frame,text="Robust Ecorr candidates, branch fits, Icorr and ambiguity diagnostics are being calculated. Main FaradaIQ windows remain responsive.",style="Muted.TLabel",wraplength=520).pack(anchor="w",pady=(6,0))
        try: progress.update_idletasks()
        except Exception: pass
    def worker():
        try: res=analyze_selected(source, CorrosionSettings(), write_outputs=True); err=None
        except Exception as exc: res=None; err=exc
        def finish():
            try:
                if progress is not None: progress.destroy()
            except Exception: pass
            if err is not None:
                if messagebox:
                    try: messagebox.showerror("Robust corrosion metrics",str(err),parent=browser)
                    except Exception: pass
            else:
                try:
                    from .report_results_v300 import refresh_existing_reports
                    threading.Thread(target=refresh_existing_reports,args=(source,),name="FaradaIQReportRefreshCorrosion",daemon=True).start()
                except Exception:
                    pass
                _show_result(source,res)
        try: browser.after(0,finish)
        except Exception: finish()
    threading.Thread(target=worker,daemon=True).start()
    return progress


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE
    _CORE=core
    core["_v298_analyze_corrosion_trace"] = analyze_trace
    core["_v298_analyze_selected_corrosion"] = analyze_selected
    core["_v298_open_corrosion_metrics"] = open_corrosion_metrics
    core["FARADAIQ_CORROSION_METRICS_SCHEMA"] = "faradaiq-corrosion-metrics-v298"
