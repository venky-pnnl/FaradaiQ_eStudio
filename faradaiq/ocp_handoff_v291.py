"""FaradaIQ Studio v291 OCP tail-average vs point handoff.

Provides a first-class OCP technique-aware workflow:
- operator-selected final-time window (30% default);
- mean OCP from the final X% of *elapsed time*, not row count;
- point/cycle/acquisition identity retained without inventing coordinates;
- local plot/table output and a dedicated SciLink handoff containing the
  original OCP time series plus deterministic derived summary tables.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

import numpy as np

VERSION = "v300"
DEFAULT_FINAL_TIME_PERCENT = 30.0
MIN_FINAL_TIME_PERCENT = 1.0
MAX_FINAL_TIME_PERCENT = 100.0
_CORE: MutableMapping[str, Any] = {}


def _g(name: str, default: Any = None) -> Any:
    return _CORE.get(name, default)


def _browser_from(source: Any) -> Any:
    try:
        from faradaiq.v262_technique_scilink import _browser_from as resolver
        browser = resolver(source)
        if browser is not None:
            return browser
    except Exception:
        pass
    return getattr(source, "gui", source)


def _path_key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.expanduser().resolve()))
    except Exception:
        return os.path.normcase(str(path))


def _selected_paths(source: Any) -> list[Path]:
    try:
        from faradaiq.v262_technique_scilink import _browser_selection_paths
        return list(_browser_selection_paths(source))
    except Exception:
        browser = _browser_from(source)
        try:
            return [Path(p).expanduser().resolve() for p in browser.selected_paths(plottable_only=False)]
        except Exception:
            return []


def _selected_record_map(source: Any) -> dict[str, dict[str, Any]]:
    browser = _browser_from(source)
    out: dict[str, dict[str, Any]] = {}
    try:
        tree = browser.tree
        selected = list(tree.selection() or [])
        from faradaiq.browser_ui_v209 import _tree_records
        records: list[Mapping[str, Any]] = []
        for iid in selected:
            try:
                records.extend(_tree_records(browser, iid))
            except Exception:
                pass
        for rec in records:
            raw = str(rec.get("path") or "").strip()
            if raw:
                out[_path_key(Path(raw))] = dict(rec)
    except Exception:
        pass
    return out


def _point_from_path(path: Path) -> int | None:
    sources = [path.name, *reversed(path.parts[-5:])]
    patterns = (
        r"(?i)(?:^|[_\-\s])point[_\-\s]*0*(\d+)(?:[_\-\s.]|$)",
        r"(?i)(?:^|[_\-\s])position[_\-\s]*0*(\d+)(?:[_\-\s.]|$)",
        r"(?i)(?:^|[_\-\s])pos[_\-\s]*0*(\d+)(?:[_\-\s.]|$)",
        r"(?i)(?:^|[_\-\s])P0*(\d+)(?:[_\-\s.]|$)",
    )
    for source in sources:
        text = str(source)
        # Avoid interpreting folder labels such as point_11points as point 11.
        text = re.sub(r"(?i)point[_\-\s]*\d+\s*points?\b", "", text)
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return int(match.group(1))
                except Exception:
                    pass
    return None


def _cycle_from_path(path: Path) -> int | None:
    for source in (path.name, path.as_posix()):
        match = re.search(r"(?i)(?:^|[_\-\s])(?:cycle|cyc)[_\-\s]*0*(\d+)(?:[_\-\s.]|$)", source)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                pass
    return None


def _record_technique(record: Mapping[str, Any]) -> str:
    return str(record.get("technique_key") or record.get("technique") or record.get("kind") or "").upper()


def _is_ocp_path(path: Path, record: Mapping[str, Any] | None = None) -> bool:
    record = record or {}
    tech = _record_technique(record)
    if tech in {"OCP", "OCV", "OPEN CIRCUIT", "OPEN_CIRCUIT"} or "OCP" in tech:
        return True
    try:
        from faradaiq.v262_technique_scilink import _infer_techniques
        if "OCP" in _infer_techniques([path], []):
            return True
    except Exception:
        pass
    name = path.as_posix().lower()
    return bool(re.search(r"(?:^|[/_\-])ocp(?:[/_\-.]|$)|open[_\- ]?circuit", name))


def selected_ocp_sources(source: Any) -> list[tuple[Path, dict[str, Any]]]:
    """Return selected OCP time-series files only, preserving Browser record metadata."""
    paths = _selected_paths(source)
    records = _selected_record_map(source)
    out: list[tuple[Path, dict[str, Any]]] = []
    seen: set[str] = set()
    for path in paths:
        key = _path_key(path)
        record = records.get(key, {})
        if path.is_file() and _is_ocp_path(path, record) and key not in seen:
            out.append((path, record))
            seen.add(key)
    # Group selections sometimes resolve records more reliably than selected_paths.
    for key, record in records.items():
        try:
            path = Path(str(record.get("path") or "")).expanduser().resolve()
        except Exception:
            continue
        if path.is_file() and _is_ocp_path(path, record) and key not in seen:
            out.append((path, record))
            seen.add(key)
    return out


def _read_ocp_table(path: Path, record: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray, str, str]:
    """Read a full OCP table and resolve time/potential columns."""
    try:
        from faradaiq.multidim_v216 import _column_roles, _read_table
        # OCP traces are normally small; the high ceiling prevents truncating the
        # final-time window while retaining the mature FaradaIQ delimiter/header logic.
        df = _read_table(path, record, max_rows=5_000_000)
        roles = _column_roles(df)
        tcol = roles.get("time")
        pcol = roles.get("potential")
        if not pcol and str(record.get("technique_key") or "").upper() == "OCP" and df.shape[1] >= 2:
            # Headerless OCP files can be normalized by _read_table as time/signal.
            candidates = [c for c in df.columns if str(c) != str(tcol)]
            pcol = candidates[0] if candidates else None
        if not tcol or not pcol:
            raise ValueError(f"could not resolve time and potential columns (columns={list(df.columns)[:12]})")
        import pandas as pd
        t = pd.to_numeric(df[tcol], errors="coerce").to_numpy(dtype=float)
        e = pd.to_numeric(df[pcol], errors="coerce").to_numpy(dtype=float)
        return t, e, str(tcol), str(pcol)
    except Exception as exc:
        raise ValueError(f"Could not read OCP time series {path.name}: {exc}") from exc


def _tail_metrics(
    time_s: np.ndarray,
    potential_v: np.ndarray,
    final_time_percent: float,
) -> dict[str, Any]:
    percent = float(final_time_percent)
    if not math.isfinite(percent) or percent < MIN_FINAL_TIME_PERCENT or percent > MAX_FINAL_TIME_PERCENT:
        raise ValueError(f"Final-time window must be between {MIN_FINAL_TIME_PERCENT:g}% and {MAX_FINAL_TIME_PERCENT:g}%.")
    n = min(len(time_s), len(potential_v))
    if n <= 0:
        raise ValueError("OCP trace contains no samples.")
    t = np.asarray(time_s[:n], dtype=float)
    e = np.asarray(potential_v[:n], dtype=float)
    mask = np.isfinite(t) & np.isfinite(e)
    t = t[mask]; e = e[mask]
    if len(t) == 0:
        raise ValueError("OCP trace contains no finite time/potential samples.")

    fraction = percent / 100.0
    dt = np.diff(t)
    monotonic = bool(len(t) < 2 or np.all(dt >= 0))
    time_span = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    if monotonic and time_span > 0:
        threshold = float(t[0] + (1.0 - fraction) * time_span)
        tail_mask = t >= threshold
        basis = "elapsed_time"
    else:
        # If the source time axis is unusable, preserve acquisition order rather
        # than sorting or inventing a time coordinate. This fallback is explicit.
        count = max(1, int(math.ceil(len(t) * fraction)))
        tail_mask = np.zeros(len(t), dtype=bool)
        tail_mask[-count:] = True
        threshold = float(t[np.flatnonzero(tail_mask)[0]])
        basis = "sample_count_fallback_nonmonotonic_time"

    tt = t[tail_mask]; ee = e[tail_mask]
    if len(ee) == 0:
        # Floating-point edge guard at a 1% window.
        tt = t[-1:]; ee = e[-1:]
        threshold = float(tt[0])
    mean = float(np.mean(ee))
    std = float(np.std(ee, ddof=1)) if len(ee) >= 2 else float("nan")
    sem = float(std / math.sqrt(len(ee))) if len(ee) >= 2 and math.isfinite(std) else float("nan")
    median = float(np.median(ee))
    slope = float("nan")
    if len(ee) >= 2 and float(np.ptp(tt)) > 0:
        try:
            slope = float(np.polyfit(tt, ee, 1)[0])
        except Exception:
            pass
    return {
        "final_time_percent": percent,
        "window_basis": basis,
        "time_start_s": float(t[0]),
        "time_end_s": float(t[-1]),
        "time_span_s": time_span,
        "tail_start_s": threshold,
        "tail_end_s": float(tt[-1]),
        "n_total": int(len(t)),
        "n_tail": int(len(ee)),
        "mean_ocp_V": mean,
        "std_ocp_V": std,
        "sem_ocp_V": sem,
        "median_ocp_V": median,
        "min_ocp_V": float(np.min(ee)),
        "max_ocp_V": float(np.max(ee)),
        "tail_drift_V_per_s": slope,
    }


def _clean_json(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json(v) for v in value]
    if isinstance(value, np.generic):
        return _clean_json(value.item())
    return value


def _output_root(source: Any) -> Path:
    browser = _browser_from(source)
    root = Path(getattr(browser, "analyzed_root", "") or getattr(browser, "cache_root", "") or Path.cwd())
    out = root / "OCP_Tail_Average_v294"
    out.mkdir(parents=True, exist_ok=True)
    return out


@dataclass
class OCPHandoffResult:
    final_time_percent: float
    horizontal_axis: str
    output_dir: Path
    trace_csv: Path
    point_csv: Path
    spatial_csv: Path
    manifest_json: Path
    plot_png: Path | None
    source_paths: list[Path]
    trace_rows: list[dict[str, Any]]
    errors: list[str]


def prepare_ocp_tail_handoff(
    source: Any,
    final_time_percent: float | None = None,
    *,
    horizontal_axis: str | None = None,
    write_plot: bool = True,
) -> OCPHandoffResult:
    browser = _browser_from(source)
    if final_time_percent is None:
        final_time_percent = float(getattr(browser, "_v291_ocp_tail_percent", DEFAULT_FINAL_TIME_PERCENT))
    percent = float(final_time_percent)
    if horizontal_axis is None:
        horizontal_axis = _axis_from_source(source)
    requested_axis = str(horizontal_axis or "Auto").title()
    sources = selected_ocp_sources(source)
    if not sources:
        raise ValueError("No OCP time-series data are selected. Select OCP points, an OCP method/group, or an OCP Plot/view first.")

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for order, (path, record) in enumerate(sources, start=1):
        try:
            t, e, time_col, potential_col = _read_ocp_table(path, record)
            metrics = _tail_metrics(t, e, percent)
            point = record.get("point")
            try:
                point = int(point) if point is not None and str(point).strip() else None
            except Exception:
                point = None
            point_source = "browser_record"
            if point is None:
                point = _point_from_path(path)
                point_source = "filename_or_folder" if point is not None else "selection_order_fallback"
            if point is None:
                point = order
            cycle = record.get("cycle")
            try:
                cycle = int(cycle) if cycle is not None and str(cycle).strip() else None
            except Exception:
                cycle = None
            if cycle is None:
                cycle = _cycle_from_path(path)
            row: dict[str, Any] = {
                "point_index": int(point),
                "point_source": point_source,
                "cycle_index": cycle,
                "acquisition_order": order,
                "source_filename": path.name,
                "source_path": str(path),
                "time_column": time_col,
                "potential_column": potential_col,
            }
            # v294: attach physical coordinates propagated from HEKA Takedata.
            # Explicit synthetic compatibility XYZ is rejected by the shared helper.
            try:
                from .spatial_points_v297 import coordinate_m
                row["x_position_m"] = coordinate_m(record, "x")
                row["y_position_m"] = coordinate_m(record, "y")
                row["z_position_m"] = coordinate_m(record, "z")
                row["scan_distance_m"] = record.get("scan_distance_m")
                row["coordinate_source"] = record.get("spatial_coordinate_source") or record.get("coordinate_source") or ""
                row["coordinate_status"] = record.get("spatial_coordinate_status") or record.get("coordinate_status") or ""
            except Exception:
                pass
            row.update(metrics)
            rows.append(row)
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")

    if not rows:
        raise ValueError("Selected OCP files could not be reduced to tail-average metrics. " + (errors[0] if errors else ""))

    # Some selected records may omit coordinates while another technique/position
    # record at the same point retains them. Propagate the browser's canonical
    # Takedata point map before deriving the spatial axis.
    try:
        from .spatial_points_v297 import enrich_catalog_records, auto_axis_for_records, spatial_axis_value
        catalog_records = list(getattr(browser, "_catalog_records", []) or [])
        if catalog_records:
            enrich_catalog_records(catalog_records, search_roots=[getattr(browser, "analyzed_root", ""), getattr(browser, "cache_root", ""), getattr(browser, "bundle_root", "")])
            by_point = {}
            for rec in catalog_records:
                try: pt = int(rec.get("point") or 0)
                except Exception: pt = 0
                if pt > 0 and rec.get("spatial_coordinate_available"):
                    by_point.setdefault(pt, rec)
            for row in rows:
                rec = by_point.get(int(row.get("point_index") or 0))
                if rec:
                    for key in ("x_position_m", "y_position_m", "z_position_m", "scan_distance_m"):
                        if row.get(key) is None: row[key] = rec.get(key)
                    if not row.get("coordinate_source"): row["coordinate_source"] = rec.get("spatial_coordinate_source") or rec.get("coordinate_source") or ""
                    if not row.get("coordinate_status"): row["coordinate_status"] = rec.get("spatial_coordinate_status") or rec.get("coordinate_status") or ""
                    row["spatial_coordinate_is_synthetic"] = bool(rec.get("spatial_coordinate_is_synthetic"))
                    row["spatial_coordinate_quality"] = rec.get("spatial_coordinate_quality") or ""
        resolved_axis = requested_axis if requested_axis in {"Point", "X", "Y", "Distance"} else auto_axis_for_records(rows)
        # Explicit coordinate requests fall back to Point only when unavailable.
        if resolved_axis != "Point" and not any(spatial_axis_value(r, resolved_axis) is not None for r in rows):
            resolved_axis = "Point"
    except Exception:
        resolved_axis = "Point"

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    out = _output_root(source) / f"ocp_final_{percent:g}pct_{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    trace_csv = out / "ocp_tail_trace_metrics.csv"
    point_csv = out / "ocp_average_vs_point.csv"
    spatial_csv = out / "ocp_average_spatial.csv"
    manifest_json = out / "ocp_tail_handoff_manifest.json"

    trace_fields = [
        "point_index", "point_source", "cycle_index", "acquisition_order", "source_filename",
        "final_time_percent", "window_basis", "time_start_s", "time_end_s", "time_span_s",
        "tail_start_s", "tail_end_s", "n_total", "n_tail", "mean_ocp_V", "std_ocp_V",
        "sem_ocp_V", "median_ocp_V", "min_ocp_V", "max_ocp_V", "tail_drift_V_per_s",
        "time_column", "potential_column", "source_path",
    ]
    extras = sorted({key for row in rows for key in row if key not in trace_fields})
    trace_fields.extend(extras)
    with trace_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=trace_fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if isinstance(value, float) and not math.isfinite(value) else value for key, value in row.items()})

    # The point table intentionally keeps one row per trace/cycle. If multiple
    # traces share a point, they stay visible rather than being silently pooled.
    point_fields = [
        "point_index", "x_position_m", "y_position_m", "z_position_m", "scan_distance_m", "coordinate_source", "coordinate_status", "spatial_coordinate_is_synthetic", "spatial_coordinate_quality",
        "cycle_index", "acquisition_order", "mean_ocp_V", "std_ocp_V", "sem_ocp_V",
        "tail_drift_V_per_s", "n_tail", "tail_start_s", "tail_end_s", "final_time_percent", "window_basis", "source_filename",
    ]
    with point_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=point_fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (int(r["point_index"]), int(r.get("cycle_index") or 0), int(r["acquisition_order"]))):
            writer.writerow({key: "" if isinstance(row.get(key), float) and not math.isfinite(row.get(key)) else row.get(key, "") for key in point_fields})

    spatial_fields = list(point_fields) + ["x_position_um", "y_position_um", "scan_distance_um"]
    with spatial_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=spatial_fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (int(r["point_index"]), int(r.get("cycle_index") or 0), int(r["acquisition_order"]))):
            item = dict(row)
            for src, dst in (("x_position_m", "x_position_um"), ("y_position_m", "y_position_um"), ("scan_distance_m", "scan_distance_um")):
                try:
                    value = float(item.get(src)); item[dst] = value * 1e6 if math.isfinite(value) else ""
                except Exception:
                    item[dst] = ""
            writer.writerow({key: "" if isinstance(item.get(key), float) and not math.isfinite(item.get(key)) else item.get(key, "") for key in spatial_fields})

    plot_png: Path | None = None
    if write_plot:
        try:
            import matplotlib
            matplotlib.use("Agg", force=True)
            from matplotlib import pyplot as plt
            from .spatial_points_v297 import spatial_axis_value, scale_m
            raw_x = [spatial_axis_value(r, resolved_axis) for r in rows]
            keep = [i for i, value in enumerate(raw_x) if value is not None]
            if not keep:
                resolved_axis = "Point"
                raw_x = [float(r["point_index"]) for r in rows]
                keep = list(range(len(rows)))
            x = np.asarray([float(raw_x[i]) for i in keep], dtype=float)
            if resolved_axis != "Point":
                x, unit = scale_m(x, "µm")
                xlabel = f"{resolved_axis} position / {unit}" if resolved_axis in {"X", "Y"} else f"Cumulative scan distance / {unit}"
            else:
                xlabel = "Point / acquisition position"
            y = np.asarray([float(rows[i]["mean_ocp_V"]) for i in keep], dtype=float)
            yerr = np.asarray([float(rows[i]["std_ocp_V"]) if math.isfinite(float(rows[i]["std_ocp_V"])) else 0.0 for i in keep], dtype=float)
            order = np.argsort(x, kind="stable")
            fig, ax = plt.subplots(figsize=(8.2, 5.0))
            ax.errorbar(x[order], y[order], yerr=yerr[order], marker="o", linestyle="-")
            ax.set_xlabel(xlabel)
            ax.set_ylabel("Average OCP / V")
            ax.set_title(f"OCP average over final {percent:g}% of elapsed time vs {resolved_axis}")
            ax.grid(True, alpha=0.25)
            fig.tight_layout()
            plot_png = out / f"ocp_average_vs_{resolved_axis.lower()}.png"
            fig.savefig(plot_png, dpi=180)
            plt.close(fig)
        except Exception as exc:
            errors.append(f"plot: {exc}")

    manifest = {
        "schema": "faradaiq-ocp-tail-handoff-v294",
        "version": VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "calculation": {
            "quantity": "mean open-circuit potential",
            "window": f"final {percent:g}% of elapsed time span per trace",
            "default_final_time_percent": DEFAULT_FINAL_TIME_PERCENT,
            "time_based_not_row_based": True,
            "formula": "mean(potential_V for time_s >= t_start + (1 - fraction) * (t_end - t_start))",
            "nonmonotonic_time_fallback": "final fraction of samples in acquisition order; explicitly labeled",
            "no_coordinate_jitter_or_invention": True,
            "horizontal_axis_requested": requested_axis,
            "horizontal_axis_resolved": resolved_axis,
            "coordinate_source": "physical HEKA Takedata/canonical point metadata when available",
        },
        "trace_count": len(rows),
        "source_file_count": len(sources),
        "failed_file_count": len(errors),
        "errors": errors,
        "outputs": {
            "trace_metrics_csv": trace_csv.name,
            "average_vs_point_csv": point_csv.name,
            "average_spatial_csv": spatial_csv.name,
            "plot_png": plot_png.name if plot_png else None,
        },
        "trace_metrics": rows,
    }
    manifest_json.write_text(json.dumps(_clean_json(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
    return OCPHandoffResult(percent, resolved_axis, out, trace_csv, point_csv, spatial_csv, manifest_json, plot_png, [p for p, _r in sources], rows, errors)


def _open_path(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":  # pragma: no cover
            subprocess.Popen(["open", str(path)])
        else:  # pragma: no cover
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def _percent_from_source(source: Any) -> float:
    browser = _browser_from(source)
    var = getattr(browser, "_v291_ocp_tail_percent_var", None)
    if var is not None:
        try:
            value = float(var.get())
            browser._v291_ocp_tail_percent = value
            return value
        except Exception:
            pass
    return float(getattr(browser, "_v291_ocp_tail_percent", DEFAULT_FINAL_TIME_PERCENT))


def _axis_from_source(source: Any) -> str:
    browser = _browser_from(source)
    var = getattr(browser, "_v294_ocp_axis_var", None)
    if var is not None:
        try:
            value = str(var.get() or "Auto").title()
            if value in {"Auto", "Point", "X", "Y", "Distance"}:
                browser._v294_ocp_axis = value
                return value
        except Exception:
            pass
    value = str(getattr(browser, "_v294_ocp_axis", "Auto") or "Auto").title()
    return value if value in {"Auto", "Point", "X", "Y", "Distance"} else "Auto"


def open_ocp_tail_analysis(source: Any) -> OCPHandoffResult | None:
    browser = _browser_from(source)
    try:
        result = prepare_ocp_tail_handoff(source, _percent_from_source(source), write_plot=True)
        try:
            import threading
            from .report_results_v300 import refresh_existing_reports
            threading.Thread(target=refresh_existing_reports, args=(source,), name="FaradaIQReportRefreshOCP", daemon=True).start()
        except Exception:
            pass
    except Exception as exc:
        messagebox = _g("messagebox")
        if messagebox is not None:
            try:
                messagebox.showwarning("OCP average vs point", str(exc), parent=browser)
            except Exception:
                pass
        return None

    tk, ttk = _g("tk"), _g("ttk")
    if tk is None or ttk is None:
        return result
    win = tk.Toplevel(browser)
    win.title(f"FaradaIQ Studio {_g('FARADAIQ_PUBLIC_VERSION', VERSION)} - OCP point/spatial average")
    win.geometry("920x680")
    win.minsize(760, 520)
    frame = ttk.Frame(win, style="Card.TFrame", padding=(10, 10)); frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=f"OCP average over final {result.final_time_percent:g}% of elapsed time · X axis: {result.horizontal_axis}", style="Section.TLabel").pack(anchor="w")
    ttk.Label(
        frame,
        text=f"{len(result.trace_rows)} OCP trace(s) • deterministic time-window reduction • original time series preserved for SciLink",
        style="Muted.TLabel",
    ).pack(anchor="w", pady=(0, 6))
    synthetic_count = sum(1 for r in result.trace_rows if r.get("spatial_coordinate_is_synthetic"))
    if synthetic_count:
        ttk.Label(frame, text=f"Spatial axis uses Takedata compatibility coordinates for {synthetic_count} point(s); these are synthetic ordered fallback coordinates, not measured HEKA stage positions.", style="Muted.TLabel", wraplength=860).pack(anchor="w", pady=(0, 6))

    # Show the requested average-OCP-vs-point view directly in the handoff window.
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        plot_frame = ttk.Frame(frame, style="Card.TFrame")
        plot_frame.pack(fill="both", expand=True, pady=(2, 8))
        fig = Figure(figsize=(7.8, 3.0), dpi=100)
        ax = fig.add_subplot(111)
        plot_rows = sorted(result.trace_rows, key=lambda r: (int(r["point_index"]), int(r.get("cycle_index") or 0), int(r["acquisition_order"])))
        from .spatial_points_v297 import spatial_axis_value, scale_m
        raw_x = [spatial_axis_value(r, result.horizontal_axis) for r in plot_rows]
        keep = [i for i, value in enumerate(raw_x) if value is not None]
        if not keep:
            raw_x = [float(r["point_index"]) for r in plot_rows]; keep = list(range(len(plot_rows))); axis = "Point"
        else:
            axis = result.horizontal_axis
        x = np.asarray([float(raw_x[i]) for i in keep], dtype=float)
        if axis != "Point":
            x, unit = scale_m(x, "µm")
            xlabel = f"{axis} position / {unit}" if axis in {"X", "Y"} else f"Cumulative scan distance / {unit}"
        else:
            xlabel = "Point / acquisition position"
        y = np.asarray([float(plot_rows[i]["mean_ocp_V"]) for i in keep], dtype=float)
        yerr = np.asarray([float(plot_rows[i]["std_ocp_V"]) if math.isfinite(float(plot_rows[i]["std_ocp_V"])) else 0.0 for i in keep], dtype=float)
        order = np.argsort(x, kind="stable")
        ax.errorbar(x[order], y[order], yerr=yerr[order], marker="o", linestyle="-")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("Average OCP / V")
        ax.set_title(f"Final {result.final_time_percent:g}% of elapsed time")
        ax.grid(True, alpha=0.25)
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=plot_frame)
        canvas.draw(); canvas.get_tk_widget().pack(fill="both", expand=True)
        win._v291_ocp_canvas = canvas
    except Exception:
        pass

    # Native Tk table remains available even if Matplotlib/Tk embedding is unavailable.
    columns = ("point", "x", "y", "distance", "cycle", "mean", "std", "drift", "n", "source")
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=9)
    headings = {
        "point": "Point", "x": "X / µm", "y": "Y / µm", "distance": "Distance / µm", "cycle": "Cycle", "mean": "Avg OCP / V", "std": "SD / V",
        "drift": "Tail drift / V s⁻¹", "n": "Tail n", "source": "Source",
    }
    widths = {"point": 65, "x": 80, "y": 80, "distance": 95, "cycle": 65, "mean": 105, "std": 90, "drift": 125, "n": 60, "source": 250}
    for col in columns:
        tree.heading(col, text=headings[col]); tree.column(col, width=widths[col], anchor="w")
    for row in sorted(result.trace_rows, key=lambda r: (r["point_index"], r.get("cycle_index") or 0)):
        def fmt(value: Any, digits: int = 6) -> str:
            try:
                value = float(value)
                return f"{value:.{digits}g}" if math.isfinite(value) else ""
            except Exception:
                return str(value or "")
        def um(key: str) -> str:
            try:
                value = float(row.get(key)); return f"{value * 1e6:.6g}" if math.isfinite(value) else ""
            except Exception:
                return ""
        tree.insert("", "end", values=(
            row.get("point_index"), um("x_position_m"), um("y_position_m"), um("scan_distance_m"), row.get("cycle_index") or "", fmt(row.get("mean_ocp_V")),
            fmt(row.get("std_ocp_V")), fmt(row.get("tail_drift_V_per_s")), row.get("n_tail"), row.get("source_filename"),
        ))
    tree.pack(fill="both", expand=True)
    controls = ttk.Frame(frame, style="Card.TFrame"); controls.pack(fill="x", pady=(8, 0))
    ttk.Button(controls, text="Open output folder", command=lambda: _open_path(result.output_dir)).pack(side="left")
    ttk.Button(controls, text="Ask SciLink with this OCP handoff", command=lambda: open_ocp_scilink_handoff(source, prepared=result)).pack(side="right")
    if result.errors:
        ttk.Label(frame, text="Warnings: " + " | ".join(result.errors[:3]), style="Muted.TLabel", wraplength=850).pack(anchor="w", fill="x", pady=(6, 0))
    return result


def open_ocp_scilink_handoff(source: Any, prepared: OCPHandoffResult | None = None) -> Any:
    percent = _percent_from_source(source)
    try:
        result = prepared or prepare_ocp_tail_handoff(source, percent, write_plot=True)
    except Exception as exc:
        browser = _browser_from(source)
        messagebox = _g("messagebox")
        if messagebox is not None:
            try:
                messagebox.showwarning("OCP SciLink handoff", str(exc), parent=browser)
            except Exception:
                pass
        return None

    prompt = (
        f"Analyze the selected OCP spatial series. FaradaIQ deterministically calculated the average open-circuit potential for each trace "
        f"from the final {result.final_time_percent:g}% of that trace's elapsed time span, not the final {result.final_time_percent:g}% of rows. "
        "Use ocp_average_vs_point.csv for the original point-index comparison, ocp_average_spatial.csv for physical X/Y/cumulative-distance comparisons, and ocp_tail_trace_metrics.csv for window statistics and provenance. "
        "Verify the derived values against the supplied original OCP time-series files when useful. Preserve exact point, cycle, and acquisition order. "
        f"The operator-selected horizontal axis is {result.horizontal_axis}. Compare average OCP versus point and, when physical Takedata coordinates are available, versus X, Y, and cumulative scan distance; also inspect tail standard deviation/SEM, tail drift, jumps, outliers, and spatial trends. Distinguish genuine spatial variation "
        "from incomplete equilibration, temporal drift, meniscus/contact changes, and noise. Do not invent coordinates or missing metadata, and do not force "
        "a relaxation model unless the time-series behavior and metadata justify it. Clearly separate measured OCP, FaradaIQ-derived tail averages, and interpretation."
    )
    bundle_paths = [*result.source_paths, result.point_csv, result.spatial_csv, result.trace_csv]
    try:
        import faradaiq.v262_technique_scilink as technique
        return technique.show_analysis_review(
            source,
            "scilink",
            prompt_override=prompt,
            bundle_source_paths=bundle_paths,
            bundle_record_names=[],
            bundle_display_label=f"OCP time series + final {result.final_time_percent:g}% point/spatial average summary",
        )
    except Exception as exc:
        browser = _browser_from(source)
        messagebox = _g("messagebox")
        if messagebox is not None:
            try:
                messagebox.showerror("OCP SciLink handoff", f"Could not open SciLink review: {exc}", parent=browser)
            except Exception:
                pass
        return None


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE
    _CORE = core
    core["_v291_prepare_ocp_tail_handoff"] = prepare_ocp_tail_handoff
    core["_v291_open_ocp_tail_analysis"] = open_ocp_tail_analysis
    core["_v291_open_ocp_scilink_handoff"] = open_ocp_scilink_handoff
    core["FARADAIQ_OCP_TAIL_DEFAULT_PERCENT"] = DEFAULT_FINAL_TIME_PERCENT
    core["FARADAIQ_OCP_TAIL_WINDOW_BASIS"] = "final percentage of elapsed time span"
