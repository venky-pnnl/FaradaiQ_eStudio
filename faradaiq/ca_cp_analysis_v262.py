"""Deterministic CA/CP transient analysis for FaradaIQ Studio v290.

This module provides a native, non-LLM analysis path for chronoamperometry and
chronopotentiometry selections. It intentionally keeps raw files unchanged and
exports auditable CSV, JSON, and HTML summaries.
"""
from __future__ import annotations

import csv
import html
import json
import math
import re
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

VERSION = "v290"
_CORE: dict[str, Any] = {}


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9µ]+", "", str(value or "").lower())


def _factor(header: str, quantity: str) -> float:
    text = str(header or "").lower().replace("μ", "µ")
    if quantity == "current":
        if "pa" in text: return 1e-12
        if "na" in text: return 1e-9
        if "µa" in text or "ua" in text: return 1e-6
        if "ma" in text: return 1e-3
    if quantity == "potential":
        if "mv" in text: return 1e-3
    if quantity == "time":
        if "ms" in text: return 1e-3
        if "µs" in text or "us" in text: return 1e-6
    return 1.0


def _find(headers: Sequence[str], candidates: Sequence[str]) -> int | None:
    normalized = [_norm(h) for h in headers]
    exact = {_norm(c) for c in candidates}
    for i, value in enumerate(normalized):
        if value in exact:
            return i
    for i, value in enumerate(normalized):
        if any(token and token in value for token in exact):
            return i
    return None


def _read_numeric_table(path: Path) -> tuple[list[str], list[list[float]]]:
    sample = path.read_text(encoding="utf-8-sig", errors="replace")[:65536]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except Exception:
        dialect = csv.excel
        dialect.delimiter = ","
    rows: list[list[float]] = []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.reader(handle, dialect)
        headers = next(reader, [])
        for row in reader:
            converted: list[float] = []
            for cell in row:
                try:
                    converted.append(float(str(cell).strip()))
                except Exception:
                    converted.append(float("nan"))
            if converted:
                rows.append(converted)
    return [str(h).strip() for h in headers], rows


def _finite_pair(x: Iterable[float], y: Iterable[float]) -> tuple[list[float], list[float]]:
    pairs = [(float(a), float(b)) for a, b in zip(x, y) if math.isfinite(float(a)) and math.isfinite(float(b))]
    pairs.sort(key=lambda item: item[0])
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _trapz(y: Sequence[float], x: Sequence[float]) -> float:
    return sum((x[i] - x[i - 1]) * (y[i] + y[i - 1]) * 0.5 for i in range(1, min(len(x), len(y))))


def _linear_fit(x: Sequence[float], y: Sequence[float]) -> tuple[float | None, float | None, float | None]:
    n = min(len(x), len(y))
    if n < 3:
        return None, None, None
    sx, sy = sum(x[:n]), sum(y[:n])
    sxx = sum(v * v for v in x[:n])
    sxy = sum(a * b for a, b in zip(x[:n], y[:n]))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-30:
        return None, None, None
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    fit = [slope * v + intercept for v in x[:n]]
    mean = sy / n
    ss_res = sum((a - b) ** 2 for a, b in zip(y[:n], fit))
    ss_tot = sum((a - mean) ** 2 for a in y[:n])
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    return slope, intercept, r2


def _median(values: Sequence[float]) -> float | None:
    finite = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not finite:
        return None
    m = len(finite) // 2
    return finite[m] if len(finite) % 2 else 0.5 * (finite[m - 1] + finite[m])


def _exp_fit(t: Sequence[float], y: Sequence[float]) -> tuple[float | None, float | None, float | None, float | None]:
    try:
        import numpy as np
        from scipy.optimize import curve_fit
        tx = np.asarray(t, dtype=float)
        yy = np.asarray(y, dtype=float)
        if tx.size < 6 or float(tx[-1] - tx[0]) <= 0:
            return None, None, None, None
        offset0 = float(np.median(yy[max(0, int(0.85 * yy.size)):]))
        amp0 = float(yy[0] - offset0)
        tau0 = max(float((tx[-1] - tx[0]) / 5.0), 1e-9)
        def model(x, offset, amplitude, tau):
            return offset + amplitude * np.exp(-(x - tx[0]) / np.maximum(tau, 1e-15))
        popt, _ = curve_fit(model, tx, yy, p0=[offset0, amp0, tau0], bounds=([-np.inf, -np.inf, 1e-12], [np.inf, np.inf, np.inf]), maxfev=20000)
        pred = model(tx, *popt)
        ss_res = float(np.sum((yy - pred) ** 2))
        ss_tot = float(np.sum((yy - np.mean(yy)) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
        return float(popt[0]), float(popt[1]), float(popt[2]), r2
    except Exception:
        return None, None, None, None


def _scharifker_hills(t: Sequence[float], current: Sequence[float], steady: float) -> tuple[str, float | None, float | None, str]:
    """Compare dimensionless instantaneous/progressive nucleation curves.

    The comparison is reported only when a resolved interior maximum exists.
    This avoids claiming a Scharifker-Hills mechanism for monotonically decaying
    transients whose maximum occurs at the first acquired sample.
    """
    if len(t) < 8:
        return "not_applicable", None, None, "too_few_points"
    signal = [abs(v - steady) for v in current]
    eligible = [i for i, value in enumerate(t) if value > 0 and math.isfinite(signal[i])]
    if len(eligible) < 6:
        return "not_applicable", None, None, "insufficient_positive_time_data"
    peak = max(eligible, key=lambda i: signal[i])
    if peak <= eligible[0] + 1 or peak >= eligible[-1] - 1:
        return "not_applicable", None, None, "no_resolved_interior_current_maximum"
    im = signal[peak]
    tm = t[peak]
    if im <= 0 or tm <= 0:
        return "not_applicable", None, None, "invalid_peak"
    obs: list[float] = []
    inst: list[float] = []
    prog: list[float] = []
    for tt, ii in zip(t, signal):
        x = tt / tm
        if x <= 0.05 or x > 4.0:
            continue
        obs.append((ii / im) ** 2)
        inst.append(1.9542 / x * (1.0 - math.exp(-1.2564 * x)) ** 2)
        prog.append(1.2254 / x * (1.0 - math.exp(-2.3367 * x * x)) ** 2)
    if len(obs) < 6:
        return "not_applicable", None, None, "insufficient_dimensionless_range"
    rmse_i = math.sqrt(sum((a - b) ** 2 for a, b in zip(obs, inst)) / len(obs))
    rmse_p = math.sqrt(sum((a - b) ** 2 for a, b in zip(obs, prog)) / len(obs))
    label = "instantaneous" if rmse_i < rmse_p else "progressive"
    return label, rmse_i, rmse_p, "resolved_peak"


@dataclass
class Result:
    file: str
    technique: str
    point_index: int | None
    position_mm: float | None
    row_count: int
    duration_s: float | None
    charge_C: float | None = None
    cathodic_charge_C: float | None = None
    anodic_charge_C: float | None = None
    peak_value: float | None = None
    peak_time_s: float | None = None
    final_value: float | None = None
    exponential_tau_s: float | None = None
    exponential_r2: float | None = None
    cottrell_slope_A_sqrt_s: float | None = None
    cottrell_r2: float | None = None
    scharifker_hills_class: str | None = None
    sh_instantaneous_rmse: float | None = None
    sh_progressive_rmse: float | None = None
    sh_applicability: str | None = None
    transition_time_s: float | None = None
    drift_per_s: float | None = None
    quality: str = "ready"


def analyze_file(path: Path, *, position_step_mm: float | None = None) -> Result:
    headers, rows = _read_numeric_table(path)
    t_idx = _find(headers, ("time_s", "time", "t_s", "elapsed_s", "elapsed time"))
    i_idx = _find(headers, ("current_A", "current", "I_A", "I/mA", "current_mA", "i"))
    e_idx = _find(headers, ("potential_V", "potential", "E_V", "Ewe/V", "voltage", "e"))
    p_idx = _find(headers, ("point_index", "point", "position_index"))
    if t_idx is None:
        raise ValueError(f"No time column identified in {path.name}")
    point_match = re.search(r"(?i)(?:^|_)point[_-]?(\d+)", path.stem)
    point = int(point_match.group(1)) if point_match else None
    if point is None and p_idx is not None:
        vals = [row[p_idx] for row in rows if p_idx < len(row) and math.isfinite(row[p_idx])]
        point = int(round(vals[0])) if vals else None
    position = (point - 1) * position_step_mm if point is not None and position_step_mm is not None else None
    time_factor = _factor(headers[t_idx], "time")
    t_all = [row[t_idx] * time_factor for row in rows if t_idx < len(row)]
    technique = "CA" if i_idx is not None else "CP" if e_idx is not None else "UNKNOWN"
    y_idx = i_idx if technique == "CA" else e_idx
    if y_idx is None:
        raise ValueError(f"No current or potential column identified in {path.name}")
    quantity = "current" if technique == "CA" else "potential"
    y_factor = _factor(headers[y_idx], quantity)
    y_all = [row[y_idx] * y_factor for row in rows if y_idx < len(row)]
    t, y = _finite_pair(t_all, y_all)
    if len(t) < 3:
        raise ValueError(f"Too few finite time/value pairs in {path.name}")
    duration = t[-1] - t[0]
    final = _median(y[max(0, int(len(y) * 0.9)):])
    if technique == "CA":
        charge = _trapz(y, t)
        cathodic = -_trapz([min(v, 0.0) for v in y], t)
        anodic = _trapz([max(v, 0.0) for v in y], t)
        dominant_cathodic = abs(min(y)) >= abs(max(y))
        peak_index = min(range(len(y)), key=lambda i: y[i]) if dominant_cathodic else max(range(len(y)), key=lambda i: y[i])
        exp_offset, exp_amp, exp_tau, exp_r2 = _exp_fit(t, y)
        positive = [(tt, vv) for tt, vv in zip(t, y) if tt > max(0.0, t[0])]
        cottrell_x = [1.0 / math.sqrt(tt - t[0]) for tt, _ in positive]
        cottrell_y = [vv for _, vv in positive]
        keep = max(4, int(len(cottrell_x) * 0.6))
        c_slope, _, c_r2 = _linear_fit(cottrell_x[:keep], cottrell_y[:keep])
        steady = float(final if final is not None else y[-1])
        sh_label, sh_i, sh_p, sh_reason = _scharifker_hills(t, y, steady)
        quality = "ready" if len(t) >= 10 else "limited_points"
        return Result(path.name, technique, point, position, len(t), duration, charge, cathodic, anodic,
                      y[peak_index], t[peak_index], final, exp_tau, exp_r2, c_slope, c_r2,
                      sh_label, sh_i, sh_p, sh_reason, quality=quality)
    # CP
    slope, _, r2 = _linear_fit(t, y)
    derivative = [abs((y[i] - y[i - 1]) / (t[i] - t[i - 1])) if t[i] != t[i - 1] else 0.0 for i in range(1, len(t))]
    transition_index = derivative.index(max(derivative)) + 1 if derivative else 0
    charge = None
    if i_idx is not None:
        i_factor = _factor(headers[i_idx], "current")
        currents = [row[i_idx] * i_factor for row in rows if i_idx < len(row)]
        ti, ci = _finite_pair(t_all, currents)
        charge = _trapz(ci, ti) if len(ti) >= 2 else None
    return Result(path.name, technique, point, position, len(t), duration, charge_C=charge,
                  peak_value=y[transition_index], peak_time_s=t[transition_index], final_value=final,
                  transition_time_s=t[transition_index], drift_per_s=slope,
                  quality="ready" if r2 is not None else "limited_points")


def _selected_paths(source: Any) -> list[Path]:
    try:
        from faradaiq import v262_technique_scilink as bridge
        return bridge._browser_selection_paths(source)
    except Exception:
        try:
            return [Path(p).resolve() for p in source.selected_paths(plottable_only=False)]
        except Exception:
            return []


def _step_from_names(paths: Sequence[Path]) -> float | None:
    text = " ".join(p.name for p in paths)
    match = re.search(r"(?i)(\d+(?:[p.]\d+)?)\s*(?:um|µm)[_-]?step", text)
    if not match:
        match = re.search(r"(?i)(\d+(?:[p.]\d+)?)umstep", text)
    return float(match.group(1).replace("p", ".")) / 1000.0 if match else None


def _write_outputs(folder: Path, results: Sequence[Result]) -> tuple[Path, Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    json_path = folder / "ca_cp_analysis_results.json"
    csv_path = folder / "ca_cp_analysis_results.csv"
    html_path = folder / "ca_cp_analysis_report.html"
    payload = {"schema": "faradaiq-ca-cp-analysis-v262", "version": VERSION, "created": datetime.now().isoformat(), "results": [asdict(r) for r in results]}
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    fields = list(asdict(results[0]).keys()) if results else list(Result.__dataclass_fields__)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results: writer.writerow(asdict(result))
    rows = []
    for result in results:
        d = asdict(result)
        rows.append("<tr>" + "".join(f"<td>{html.escape('' if d[f] is None else str(d[f]))}</td>" for f in fields) + "</tr>")
    html_path.write_text("<!doctype html><meta charset='utf-8'><title>FaradaIQ CA/CP analysis</title>"
                         "<style>body{font-family:Segoe UI,Arial;margin:24px}table{border-collapse:collapse;font-size:12px}th,td{border:1px solid #aaa;padding:4px 7px}th{position:sticky;top:0;background:#eee}</style>"
                         f"<h1>FaradaIQ Studio {VERSION} CA/CP transient analysis</h1><p>Files: {len(results)}</p>"
                         "<table><thead><tr>" + "".join(f"<th>{html.escape(f)}</th>" for f in fields) + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>", encoding="utf-8")
    return csv_path, json_path, html_path


def open_ca_cp_analysis(source: Any) -> Any:
    tk, ttk = _CORE.get("tk"), _CORE.get("ttk")
    messagebox = _CORE.get("messagebox")
    if tk is None or ttk is None:
        raise RuntimeError("Tk UI is unavailable")
    paths = [p for p in _selected_paths(source) if p.suffix.lower() in {".csv", ".tsv", ".txt", ".dat", ".asc"}]
    if not paths:
        messagebox.showwarning("CA/CP selection required", "Select CA or CP table files in the Browser first.", parent=getattr(source, "gui", source))
        return None
    parent = getattr(source, "gui", source)
    win = tk.Toplevel(parent)
    win.title(f"FaradaIQ Studio {VERSION} - CA/CP transient analysis")
    win.geometry("1320x720")
    outer = ttk.Frame(win, padding=10); outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="CA/CP charge and transient analysis", style="Section.TLabel").pack(anchor="w")
    ttk.Label(outer, text="Native deterministic analysis: charge, cathodic/anodic charge, exponential decay, Cottrell behavior, Scharifker-Hills applicability, CP transition time, and spatial trends.", style="Muted.TLabel", wraplength=1250).pack(anchor="w", pady=(2,8))
    status = tk.StringVar(value=f"Ready to analyze {len(paths)} selected file(s).")
    step_var = tk.StringVar(value=str(_step_from_names(paths) or ""))
    controls = ttk.Frame(outer); controls.pack(fill="x", pady=(0,6))
    ttk.Label(controls, text="Position step (mm, optional)").pack(side="left")
    ttk.Entry(controls, textvariable=step_var, width=10).pack(side="left", padx=(5,10))
    ttk.Label(controls, textvariable=status, style="Muted.TLabel").pack(side="left", fill="x", expand=True)
    columns = ("file","technique","point","position","charge","peak","tau","cottrell","sh","quality")
    tree = ttk.Treeview(outer, columns=columns, show="headings")
    headings = {"file":"File","technique":"Technique","point":"Point","position":"Position / mm","charge":"Charge / C","peak":"Peak I or E","tau":"Decay tau / s","cottrell":"Cottrell R²","sh":"Scharifker-Hills","quality":"Quality"}
    widths = {"file":320,"technique":75,"point":65,"position":95,"charge":115,"peak":115,"tau":100,"cottrell":95,"sh":180,"quality":100}
    for col in columns:
        tree.heading(col, text=headings[col]); tree.column(col, width=widths[col], anchor="w")
    ybar = ttk.Scrollbar(outer, orient="vertical", command=tree.yview); tree.configure(yscrollcommand=ybar.set)
    tree.pack(side="left", fill="both", expand=True); ybar.pack(side="left", fill="y")
    state: dict[str, Any] = {"results": [], "outputs": None}
    def populate(results: Sequence[Result]) -> None:
        tree.delete(*tree.get_children())
        for r in results:
            tree.insert("", "end", values=(r.file,r.technique,r.point_index,"" if r.position_mm is None else f"{r.position_mm:.4g}","" if r.charge_C is None else f"{r.charge_C:.6g}","" if r.peak_value is None else f"{r.peak_value:.6g}","" if r.exponential_tau_s is None else f"{r.exponential_tau_s:.5g}","" if r.cottrell_r2 is None else f"{r.cottrell_r2:.4f}",r.scharifker_hills_class or "",r.quality))
    def run() -> None:
        try: step = float(step_var.get()) if step_var.get().strip() else None
        except Exception:
            messagebox.showwarning("Invalid step", "Enter the spatial step in millimeters, or leave it blank.", parent=win); return
        status.set("Analyzing selected CA/CP files...")
        def worker() -> None:
            results: list[Result] = []
            errors: list[str] = []
            for path in paths:
                try: results.append(analyze_file(path, position_step_mm=step))
                except Exception as exc: errors.append(f"{path.name}: {exc}")
            out_root = paths[0].parent / f"FaradaIQ_CA_CP_Analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            outputs = _write_outputs(out_root, results) if results else None
            def done() -> None:
                state["results"], state["outputs"] = results, outputs
                populate(results)
                status.set(f"Completed {len(results)} file(s)" + (f"; {len(errors)} failed." if errors else "."))
                if errors: messagebox.showwarning("Some files were skipped", "\n".join(errors[:20]), parent=win)
            win.after(0, done)
        threading.Thread(target=worker, daemon=True, name="FaradaIQ-CA-CP-analysis").start()
    footer = ttk.Frame(win, padding=(10,6)); footer.pack(fill="x")
    ttk.Button(footer, text="Analyze", style="Accent.TButton", command=run).pack(side="right")
    def open_report() -> None:
        if not state.get("outputs"):
            messagebox.showinfo("Run analysis first", "Run the analysis before opening the report.", parent=win); return
        import webbrowser
        webbrowser.open_new_tab(state["outputs"][2].resolve().as_uri())
    ttk.Button(footer, text="Open HTML report", command=open_report).pack(side="right", padx=(0,6))
    ttk.Button(footer, text="Close", command=win.destroy).pack(side="right", padx=(0,6))
    run()
    return win


def install(core: Mapping[str, Any]) -> None:
    global _CORE
    _CORE = dict(core)
    if isinstance(core, dict):
        core["_v262_open_ca_cp_analysis"] = open_ca_cp_analysis
        core["_v262_analyze_ca_cp_file"] = analyze_file
