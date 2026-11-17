"""Technique-aware report aggregation for FaradaIQ Studio v300.

Collects deterministic point/spatial electrochemistry results for use by both the
portable Browser and PowerPoint summary. The collector is intentionally generic:
it does not encode source experiment names and it never invents missing values.
"""
from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

VERSION = "v300"
SCHEMA = "faradaiq-report-electrochemistry-v300"


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except Exception:
        return None
    return v if math.isfinite(v) else None


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except Exception:
        return None


def _read_csv(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh)]
    except Exception:
        return []


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return path.name


def _roots(experiment_root: Path, analysis_root: Path | None) -> list[Path]:
    out: list[Path] = []
    for p in (analysis_root, experiment_root):
        if p is None:
            continue
        try:
            p = p.resolve()
        except Exception:
            pass
        if p.exists() and p not in out:
            out.append(p)
    return out


def _glob_unique(roots: Iterable[Path], pattern: str) -> list[Path]:
    seen: set[str] = set(); rows: list[Path] = []
    for root in roots:
        try:
            matches = root.rglob(pattern)
        except Exception:
            continue
        for path in matches:
            try:
                key = str(path.resolve()).lower()
            except Exception:
                key = str(path).lower()
            if key in seen or not path.is_file():
                continue
            seen.add(key); rows.append(path)
    rows.sort(key=lambda p: (p.stat().st_mtime if p.exists() else 0.0, str(p)), reverse=True)
    return rows


def _axis_available(rows: list[Mapping[str, Any]], key: str) -> bool:
    return any(_finite(r.get(key)) is not None for r in rows)


def _axis_options(rows: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    out = [{"id": "Point", "column": "point_index", "label": "Point", "unit": ""}]
    if _axis_available(rows, "x_position_m") or _axis_available(rows, "x_position_um"):
        out.append({"id": "X", "column": "x_position_um", "label": "X coordinate", "unit": "µm"})
    if _axis_available(rows, "y_position_m") or _axis_available(rows, "y_position_um"):
        out.append({"id": "Y", "column": "y_position_um", "label": "Y coordinate", "unit": "µm"})
    if _axis_available(rows, "scan_distance_m") or _axis_available(rows, "scan_distance_um"):
        out.append({"id": "Distance", "column": "scan_distance_um", "label": "Cumulative distance", "unit": "µm"})
    return out


def _normalize_spatial(row: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for mkey, ukey in (("x_position_m", "x_position_um"), ("y_position_m", "y_position_um"), ("z_position_m", "z_position_um"), ("scan_distance_m", "scan_distance_um")):
        uv = _finite(item.get(ukey))
        mv = _finite(item.get(mkey))
        item[ukey] = uv if uv is not None else (mv * 1e6 if mv is not None else None)
        item[mkey] = mv if mv is not None else (uv * 1e-6 if uv is not None else None)
    item["point_index"] = _int(item.get("point_index"))
    return item


def _collect_ocp(root: Path, roots: list[Path]) -> list[dict[str, Any]]:
    # Prefer the spatial table because it contains Point/X/Y/Distance in one file.
    candidates = _glob_unique(roots, "ocp_average_spatial.csv")
    seen_parent: set[str] = set(); out: list[dict[str, Any]] = []
    for path in candidates:
        parent_key = str(path.parent.resolve()).lower()
        if parent_key in seen_parent:
            continue
        seen_parent.add(parent_key)
        rows = [_normalize_spatial(r) for r in _read_csv(path)]
        rows = [r for r in rows if _finite(r.get("mean_ocp_V")) is not None]
        if not rows:
            continue
        manifest = _read_json(path.parent / "ocp_tail_handoff_manifest.json")
        calc = manifest.get("calculation") if isinstance(manifest.get("calculation"), Mapping) else {}
        percent = _finite(rows[0].get("final_time_percent"))
        if percent is None:
            text = str(calc.get("window") or "")
            import re
            m = re.search(r"final\s+([0-9.]+)%", text, flags=re.I)
            percent = float(m.group(1)) if m else None
        resolved = str(calc.get("horizontal_axis_resolved") or "Point")
        out.append({
            "id": f"ocp_{len(out)+1}",
            "kind": "ocp_tail_average",
            "title": "OCP final-window average",
            "final_time_percent": percent,
            "window_basis": str(rows[0].get("window_basis") or "elapsed_time"),
            "default_axis": resolved,
            "axis_options": _axis_options(rows),
            "metrics": [
                {"id":"mean_ocp_V","label":"Average OCP","unit":"V"},
                {"id":"std_ocp_V","label":"OCP standard deviation","unit":"V"},
                {"id":"sem_ocp_V","label":"OCP SEM","unit":"V"},
                {"id":"tail_drift_V_per_s","label":"Tail drift","unit":"V s⁻¹"},
            ],
            "row_count": len(rows),
            "rows": rows,
            "source_csv": _relative(path, root),
            "manifest_json": _relative(path.parent / "ocp_tail_handoff_manifest.json", root) if (path.parent / "ocp_tail_handoff_manifest.json").is_file() else "",
            "plot_png": _relative(next(iter(sorted(path.parent.glob("ocp_average_vs_*.png"))), Path()), root) if any(path.parent.glob("ocp_average_vs_*.png")) else "",
            "warnings": list(manifest.get("errors") or []) if isinstance(manifest.get("errors"), list) else [],
        })
    return out


def _corrosion_status_counts(rows: list[Mapping[str, Any]]) -> dict[str, int]:
    counter = Counter(str(r.get("ambiguity_status") or "UNKNOWN").upper() for r in rows)
    return {k: int(v) for k, v in sorted(counter.items())}


def _collect_corrosion(root: Path, roots: list[Path]) -> list[dict[str, Any]]:
    # Use JSON when available because it preserves settings/flags/candidate metadata.
    jsons = _glob_unique(roots, "robust_corrosion_metrics_*.json")
    latest_by_dir: dict[str, Path] = {}
    for path in jsons:
        key = str(path.parent.resolve()).lower()
        latest_by_dir.setdefault(key, path)
    out: list[dict[str, Any]] = []
    for path in latest_by_dir.values():
        payload = _read_json(path)
        rows_raw = payload.get("rows") if isinstance(payload.get("rows"), list) else []
        rows = [_normalize_spatial(r) for r in rows_raw if isinstance(r, Mapping)]
        if not rows:
            continue
        settings = payload.get("settings") if isinstance(payload.get("settings"), Mapping) else {}
        metrics = [
            {"id":"ecorr_V","label":"Ecorr","unit":"V"},
            {"id":"icorr_A","label":"Icorr","unit":"A"},
            {"id":"icorr_A_cm2","label":"icorr","unit":"A cm⁻²"},
            {"id":"cathodic_tafel_mV_dec","label":"Cathodic Tafel slope","unit":"mV dec⁻¹"},
            {"id":"anodic_tafel_mV_dec","label":"Anodic Tafel slope","unit":"mV dec⁻¹"},
            {"id":"cathodic_r2","label":"Cathodic fit R²","unit":""},
            {"id":"anodic_r2","label":"Anodic fit R²","unit":""},
            {"id":"corrosion_rate_mm_per_year","label":"Corrosion rate","unit":"mm year⁻¹"},
            {"id":"zero_crossing_cluster_count","label":"Zero-crossing families","unit":"count"},
            {"id":"dip_count","label":"Significant low-current dips","unit":"count"},
        ]
        available_metrics = [m for m in metrics if any(_finite(r.get(m["id"])) is not None for r in rows)]
        warning_rows = []
        for r in rows:
            status = str(r.get("ambiguity_status") or "").upper()
            flags = r.get("flags")
            if isinstance(flags, str): flags = [x for x in flags.replace(";", ",").split(",") if x.strip()]
            if status and status != "CLEAR":
                warning_rows.append({"point_index":r.get("point_index"),"status":status,"flags":list(flags or []),"zero_crossing_cluster_count":_int(r.get("zero_crossing_cluster_count")),"dip_count":_int(r.get("dip_count"))})
        out.append({
            "id": f"corrosion_{len(out)+1}",
            "kind": "robust_corrosion_metrics",
            "title": "Robust corrosion metrics",
            "default_axis": str(payload.get("horizontal_axis") or "Point"),
            "axis_options": _axis_options(rows),
            "metrics": available_metrics,
            "row_count": len(rows),
            "rows": rows,
            "status_counts": _corrosion_status_counts(rows),
            "review_rows": warning_rows,
            "settings": dict(settings),
            "source_json": _relative(path, root),
            "source_csv": _relative(path.with_suffix(".csv"), root) if path.with_suffix(".csv").is_file() else "",
        })
    return out



def _collect_takedata(root: Path, roots: list[Path]) -> list[dict[str, Any]]:
    """Collect authoritative Takedata station records for Browser/PPT reporting."""
    try:
        from .takedata_v300 import parse_takedata_table, parse_takedata_records, takedata_diagnostics
    except Exception:
        return []
    candidates=[]
    for pattern in ("*takedata*.txt","*takedata*.csv","*takedata*.tsv","*takedata*.asc"):
        candidates.extend(_glob_unique(roots, pattern))
    # Prefer physical/global tables, exact one-per-position tables, and cleaned tables.
    scored=[]
    for path in candidates:
        low=path.name.lower()
        if "synthetic_xyz" in low:
            synthetic=True
        else:
            synthetic=False
        rows=parse_takedata_table(path)
        if not rows:
            continue
        score=(0 if synthetic else 200) + len(rows)
        if any(t in low for t in ("edited","clean","cleaned","dedup")): score+=40
        if re.search(r"takedata_1(?:_|\.|$)", low): score+=30
        if any(t in low for t in ("_eis_","_ocp_","_iv_","tafel")): score-=15
        scored.append((score,path,rows,synthetic))
    if not scored:
        return []
    # Avoid reporting several method-specific copies of the same stage path.
    scored.sort(key=lambda x:(-x[0],str(x[1])))
    chosen=[]; signatures=set()
    for score,path,rows,synthetic in scored:
        sig=tuple((round(float(r.get("x_m") or 0.0),12),round(float(r.get("y_m") or 0.0),12),round(float(r.get("z_m") or 0.0),12)) for r in rows)
        if sig in signatures:
            continue
        signatures.add(sig); chosen.append((path,rows,synthetic))
        if len(chosen)>=8:
            break
    out=[]
    for path,station_rows,synthetic in chosen:
        diagnostics=takedata_diagnostics(path)
        raw_records=parse_takedata_records(path)
        rows=[]
        prev=None
        for r in station_rows:
            item={
                "point_index": _int(r.get("point_index")),
                "x_position_m": _finite(r.get("x_m")), "y_position_m": _finite(r.get("y_m")), "z_position_m": _finite(r.get("z_m")),
                "scan_distance_m": _finite(r.get("scan_distance_m")),
                "imon_A": _finite(r.get("imon_A")), "emon_V": _finite(r.get("emon_V")), "time_s": _finite(r.get("time_s")),
                "coordinate_repeat_count": _int(r.get("coordinate_repeat_count")) or 1,
                "sequence_repeat_count": _int(r.get("sequence_repeat_count")) or 1,
                "coordinate_quality": "synthetic compatibility" if synthetic else "measured Takedata",
            }
            item=_normalize_spatial(item)
            if prev is None:
                item["step_distance_um"]=0.0; item["delta_x_um"]=0.0; item["delta_y_um"]=0.0; item["delta_z_um"]=0.0
            else:
                dx=(item.get("x_position_um") or 0.0)-(prev.get("x_position_um") or 0.0)
                dy=(item.get("y_position_um") or 0.0)-(prev.get("y_position_um") or 0.0)
                z0,z1=prev.get("z_position_um"),item.get("z_position_um")
                dz=(z1-z0) if z0 is not None and z1 is not None else None
                item["delta_x_um"]=dx; item["delta_y_um"]=dy; item["delta_z_um"]=dz
                item["step_distance_um"]=math.hypot(dx,dy)
            prev=item; rows.append(item)
        raw_rows=[]
        t0=next((_finite(r.get("time_s")) for r in raw_records if _finite(r.get("time_s")) is not None),None)
        for r in raw_records[:5000]:
            tr=_finite(r.get("time_s"))
            raw_rows.append({
                "record_index":_int(r.get("record_index")), "sweep":_int(r.get("sweep")),
                "x_position_um":(_finite(r.get("x_m"))*1e6 if _finite(r.get("x_m")) is not None else None),
                "y_position_um":(_finite(r.get("y_m"))*1e6 if _finite(r.get("y_m")) is not None else None),
                "z_position_um":(_finite(r.get("z_m"))*1e6 if _finite(r.get("z_m")) is not None else None),
                "emon_V":_finite(r.get("emon_V")), "imon_A":_finite(r.get("imon_A")), "time_s":tr,
                "elapsed_from_start_s":(tr-t0 if tr is not None and t0 is not None else None),
                "raw_trajectory_distance_um":(_finite(r.get("raw_trajectory_distance_m"))*1e6 if _finite(r.get("raw_trajectory_distance_m")) is not None else None),
            })
        metrics=[
            {"id":"x_position_um","label":"X position","unit":"µm"},
            {"id":"y_position_um","label":"Y position","unit":"µm"},
            {"id":"z_position_um","label":"Z position","unit":"µm"},
            {"id":"scan_distance_um","label":"Cumulative distance","unit":"µm"},
            {"id":"step_distance_um","label":"Step distance","unit":"µm"},
            {"id":"delta_x_um","label":"ΔX per station","unit":"µm"},
            {"id":"delta_y_um","label":"ΔY per station","unit":"µm"},
            {"id":"delta_z_um","label":"ΔZ per station","unit":"µm"},
            {"id":"emon_V","label":"Station Takedata potential (Emon)","unit":"V"},
            {"id":"imon_A","label":"Station Takedata current (Imon)","unit":"A"},
            {"id":"time_s","label":"Station acquisition time","unit":"s"},
        ]
        metrics=[m for m in metrics if any(_finite(r.get(m["id"])) is not None for r in rows)]
        default=str(diagnostics.get("dominant_axis") or "Point")
        warnings=list(diagnostics.get("warnings") or [])
        if synthetic: warnings.insert(0,"Coordinates are a compatibility fallback rather than measured stage positions.")
        out.append({
            "id":f"takedata_{len(out)+1}", "kind":"takedata_spatial", "title":"Takedata spatial and acquisition diagnostics",
            "default_axis":default, "axis_options":_axis_options(rows), "metrics":metrics, "rows":rows, "raw_rows":raw_rows,
            "row_count":len(rows), "raw_record_count":len(raw_records),
            "source_table":_relative(path,root), "coordinate_quality":"synthetic compatibility" if synthetic else "measured Takedata",
            "diagnostics":diagnostics, "warnings":warnings,
        })
    return out

def collect_electrochemistry_results(experiment_root: str | Path, analysis_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(experiment_root).expanduser().resolve()
    analysis = Path(analysis_root).expanduser().resolve() if analysis_root else None
    roots = _roots(root, analysis)
    ocp = _collect_ocp(root, roots)
    corrosion = _collect_corrosion(root, roots)
    takedata = _collect_takedata(root, roots)
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "ocp": ocp,
        "corrosion": corrosion,
        "takedata": takedata,
        "ocp_result_count": len(ocp),
        "corrosion_result_count": len(corrosion),
        "takedata_result_count": len(takedata),
        "result_count": len(ocp) + len(corrosion) + len(takedata),
        "reporting_contract": {
            "shared_browser_and_powerpoint_data": True,
            "point_x_y_distance_axes": True,
            "ambiguity_flags_preserved": True,
            "missing_material_constants_not_invented": True,
        },
    }


def _candidate_report_roots(source: Any) -> list[Path]:
    browser = getattr(source, "gui", source)
    vals = []
    for name in ("analyzed_root", "cache_root", "bundle_root", "experiment_root", "output_root"):
        raw = getattr(browser, name, None)
        if raw:
            try: vals.append(Path(str(raw)).expanduser().resolve())
            except Exception: pass
    out: list[Path] = []
    for value in vals:
        cur = value if value.is_dir() else value.parent
        for _ in range(6):
            if cur in out: break
            out.append(cur)
            if cur.parent == cur: break
            cur = cur.parent
    return out


def refresh_existing_reports(source: Any) -> dict[str, Any]:
    """Refresh already-created portable Browser/PPT after a derived analysis.

    It never creates a report where the operator did not previously request one.
    PowerPoint refresh is deterministic and does not initiate a new LLM call.
    """
    results: dict[str, Any] = {"browser_refreshed": False, "powerpoint_refreshed": False, "errors": []}
    try:
        from .v290_portable_browser import HTML_NAME, write_portable_browser
        from .v290_powerpoint_summary import PPTX_NAME, write_powerpoint_summary
    except Exception as exc:
        results["errors"].append(str(exc)); return results
    browser = getattr(source, "gui", source)
    analysis_root = getattr(browser, "analyzed_root", None)
    for root in _candidate_report_roots(source):
        html_exists = (root / HTML_NAME).is_file()
        ppt_exists = (root / PPTX_NAME).is_file()
        if not (html_exists or ppt_exists):
            continue
        try:
            manifest = None
            if html_exists:
                receipt = write_portable_browser(root, analysis_root=analysis_root, create_batch_zips=False)
                results["browser_refreshed"] = True
                try:
                    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
                except Exception:
                    manifest = None
            if ppt_exists:
                write_powerpoint_summary(root, analysis_root=analysis_root, manifest=manifest, use_direct_llm=False, detail_level="comprehensive")
                results["powerpoint_refreshed"] = True
            results["report_root"] = str(root)
        except Exception as exc:
            results["errors"].append(f"{root}: {exc}")
        break
    return results
