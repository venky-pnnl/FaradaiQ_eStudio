"""FaradaIQ Studio v290 visual schema and job-ready browser catalog.

This release makes the browser schema a real connected block diagram and
changes the batch-job completion contract so a job is not Completed until the
canonical browser cache and unified catalog are both ready and validated.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import webbrowser
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

VERSION = "v290"
_CORE: MutableMapping[str, Any] = {}
_ORIGINAL_BUILD_CATALOG = None
_ORIGINAL_RUN_BATCH_JOB = None


def _g(name: str, default: Any = None) -> Any:
    return _CORE.get(name, default)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temp.replace(path)


def _cache_signature(cache_root: Path | str) -> str:
    """Return a stable signature without rescanning every extracted table."""
    root = Path(cache_root).resolve()
    source = _read_json(root / "_bundle_source.json")
    h5_path = Path(str(source.get("h5_path") or "")) if source.get("h5_path") else None
    h5_stat: dict[str, Any] = {}
    try:
        if h5_path and h5_path.exists():
            stat = h5_path.stat()
            h5_stat = {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
    except Exception:
        pass
    payload = {
        "h5_path": os.path.normcase(str(h5_path.resolve())) if h5_path and h5_path.exists() else str(source.get("h5_path") or ""),
        "h5_mtime": source.get("h5_mtime"),
        "h5_stat": h5_stat,
        "n_tables": int(source.get("n_tables", 0) or 0),
        "n_media": int(source.get("n_media", 0) or 0),
        "extract_policy_version": str(source.get("extract_policy_version") or ""),
        "json_path": str(source.get("json_path") or ""),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _catalog_path(cache_root: Path | str) -> Path:
    return Path(cache_root).resolve() / "_faradaiq_browser_catalog_v216.json"


def _ready_marker_path(cache_root: Path | str) -> Path:
    return Path(cache_root).resolve() / "_faradaiq_browser_ready_v240.json"


def _load_ready_catalog(cache_root: Path | str) -> Optional[dict[str, Any]]:
    root = Path(cache_root).resolve()
    path = _catalog_path(root)
    if not path.exists():
        return None
    data = _read_json(path)
    if not data or not isinstance(data.get("records"), list):
        return None
    if str(data.get("schema") or "") != "faradaiq-browser-catalog-v290":
        return None
    signature = _cache_signature(root)
    if str(data.get("source_signature") or "") != signature:
        return None
    if os.path.normcase(str(data.get("cache_root") or "")) != os.path.normcase(str(root)):
        return None
    data["catalog_path"] = str(path)
    data["catalog_ready"] = True
    data["catalog_reused"] = True
    return data


def build_catalog_cached(
    cache_root: Path | str,
    bundle_root: Optional[Path | str] = None,
    manifest: Optional[Mapping[str, Any]] = None,
    base_name: str = "",
    fallback_collector: Any = None,
) -> dict[str, Any]:
    """Reuse the job-built catalog when the canonical cache is unchanged."""
    global _ORIGINAL_BUILD_CATALOG
    root = Path(cache_root).resolve()
    cached = _load_ready_catalog(root)
    if cached is not None:
        return cached
    if not callable(_ORIGINAL_BUILD_CATALOG):
        raise RuntimeError("The unified FaradaIQ catalog builder is unavailable.")
    result = dict(_ORIGINAL_BUILD_CATALOG(
        root,
        bundle_root=bundle_root,
        manifest=manifest,
        base_name=base_name,
        fallback_collector=fallback_collector,
    ))
    result["source_signature"] = _cache_signature(root)
    result["catalog_ready"] = True
    result["catalog_reused"] = False
    result["catalog_built_at"] = datetime.now().isoformat(timespec="seconds")
    result["catalog_path"] = str(_catalog_path(root))
    _write_json(_catalog_path(root), result)
    return result


def _clean_progress_message(message: Any, percent: float) -> str:
    text = str(message or "").strip()
    text = re.sub(r"\s*\|\s*\d+(?:\.\d+)?%", "", text)
    return f"{text} | {percent:.1f}%" if text else f"{percent:.1f}%"


def _load_manifest_for_job(job: Any) -> dict[str, Any]:
    bundle = (getattr(job, "outputs", {}) or {}).get("canonical_bundle") or {}
    path = Path(str(bundle.get("json_path") or ""))
    return _read_json(path) if path.exists() else {}


def _build_job_catalog(job: Any, core: Mapping[str, Any], progress: Any) -> dict[str, Any]:
    outputs = getattr(job, "outputs", {}) or {}
    cache = outputs.get("browser_cache") or {}
    cache_root = Path(str(cache.get("cache_root") or cache.get("cache_dir") or ""))
    if not cache_root.exists():
        raise RuntimeError("Browser cache folder was not created, so the Browser catalog cannot be prepared.")
    bundle = outputs.get("canonical_bundle") or {}
    bundle_root = Path(str(outputs.get("analysis_ready_root") or getattr(job, "output_dir", "") or cache_root.parent))
    manifest = _load_manifest_for_job(job)
    fallback = (
        core.get("_v209_collect_browser_records")
        or core.get("_v208_collect_browser_records")
        or core.get("_v206_collect_browser_records")
    )
    progress("Running", "browser_catalog", 93.0, "Building the unified Browser catalog inside the export job | 93.0%")
    catalog = build_catalog_cached(
        cache_root,
        bundle_root=bundle_root,
        manifest=manifest,
        base_name=str(getattr(job, "base_name", "") or manifest.get("base_name") or ""),
        fallback_collector=fallback,
    )
    progress("Running", "browser_catalog", 97.0, "Validating Browser catalog and cache readiness | 97.0%")
    catalog_path = Path(str(catalog.get("catalog_path") or _catalog_path(cache_root)))
    n_tables = int(cache.get("n_tables", 0) or 0)
    record_count = int(catalog.get("record_count", len(catalog.get("records") or [])) or 0)
    if not catalog_path.exists():
        raise RuntimeError("Browser catalog validation failed because its JSON index is missing.")
    if n_tables > 0 and record_count <= 0:
        raise RuntimeError(f"Browser catalog validation found 0 records for a cache containing {n_tables} table(s).")
    signature = _cache_signature(cache_root)
    if str(catalog.get("source_signature") or "") != signature:
        raise RuntimeError("Browser catalog validation failed because the cache signature changed during the job.")
    marker = {
        "schema": "faradaiq-browser-ready-v240",
        "version": VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "job_id": str(getattr(job, "job_id", "")),
        "cache_root": str(cache_root.resolve()),
        "catalog_path": str(catalog_path.resolve()),
        "source_signature": signature,
        "n_tables": n_tables,
        "record_count": record_count,
        "technique_counts": catalog.get("technique_counts") or {},
        "ready": True,
    }
    marker_path = _ready_marker_path(cache_root)
    _write_json(marker_path, marker)
    info = {
        "ready": True,
        "cache_root": str(cache_root.resolve()),
        "catalog_path": str(catalog_path.resolve()),
        "ready_marker": str(marker_path.resolve()),
        "record_count": record_count,
        "technique_counts": catalog.get("technique_counts") or {},
        "method_counts": catalog.get("method_counts") or {},
        "point_counts": catalog.get("point_counts") or {},
        "source_signature": signature,
        "catalog_reused": bool(catalog.get("catalog_reused")),
    }
    cache.update({
        "catalog_path": info["catalog_path"],
        "catalog_ready_marker": info["ready_marker"],
        "catalog_record_count": record_count,
        "catalog_ready": True,
        "source_signature": signature,
    })
    outputs["browser_cache"] = cache
    outputs["browser_catalog"] = info
    job.outputs = outputs
    if "browser_catalog" not in job.completed_stages:
        job.completed_stages.append("browser_catalog")
    return info


def run_batch_job_with_catalog(
    job: Any,
    core: Mapping[str, Any],
    progress: Any,
    *,
    cancel_event: Any = None,
    process_callback: Any = None,
) -> Any:
    """Prevent Completed until Browser cache + catalog are ready."""
    global _ORIGINAL_RUN_BATCH_JOB
    if not callable(_ORIGINAL_RUN_BATCH_JOB):
        raise RuntimeError("The FaradaIQ batch-job runner is unavailable.")
    settings = getattr(job, "export_settings", {}) or {}
    needs_catalog = bool(settings.get("canonical_h5_json", True) and settings.get("build_browser_cache", True))
    if not needs_catalog:
        return _ORIGINAL_RUN_BATCH_JOB(
            job, core, progress, cancel_event=cancel_event, process_callback=process_callback,
        )

    def mapped_progress(status: str, stage: str, percent: float, message: str) -> None:
        mapped = max(0.0, min(92.0, float(percent or 0.0) * 0.92))
        if status == "Completed" or stage == "completed":
            job.status = "Running"
            job.stage = "browser_catalog"
            job.progress = 92.0
            job.finished_at = ""
            progress(
                "Running", "browser_catalog", 92.0,
                "Export, parser, canonical bundle, and table cache are finished. Building the Browser catalog before completion | 92.0%",
            )
            return
        progress(status, stage, mapped, _clean_progress_message(message, mapped))

    result = _ORIGINAL_RUN_BATCH_JOB(
        job, core, mapped_progress, cancel_event=cancel_event, process_callback=process_callback,
    )
    if cancel_event is not None and cancel_event.is_set():
        jobs = __import__("faradaiq.v233_batch_jobs", fromlist=["ExportCancelled"])
        raise jobs.ExportCancelled("Export was cancelled before Browser catalog validation.")
    job.status = "Running"
    job.stage = "browser_catalog"
    job.progress = 92.0
    job.finished_at = ""
    info = _build_job_catalog(job, core, progress)
    job.status = "Completed"
    job.stage = "completed"
    job.progress = 100.0
    job.progress_current = int(info.get("record_count", 0) or 0)
    job.progress_total = job.progress_current
    job.progress_detail = "browser cache + catalog ready"
    job.eta_seconds = 0.0
    job.message = (
        f"Job completed. Browser cache and catalog are ready with {job.progress_current} record(s); "
        "the Browser can load immediately without recataloging."
    )
    job.error = ""
    job.traceback = ""
    job.finished_at = datetime.now().isoformat(timespec="seconds")
    job.touch()
    progress("Completed", "completed", 100.0, f"{job.message} | 100.0%")
    return result


def _format_size(value: Any) -> str:
    try:
        n = float(value or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
    except Exception:
        pass
    return ""


def _safe_count(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _browser_catalog(browser: Any) -> dict[str, Any]:
    catalog = getattr(browser, "_browser_catalog_v209", None)
    if isinstance(catalog, dict) and isinstance(catalog.get("records"), list):
        return catalog
    root = getattr(browser, "cache_root", None)
    if root:
        cached = _load_ready_catalog(root)
        if cached:
            return cached
    return {"records": [], "technique_counts": {}, "method_counts": {}, "point_counts": {}}


def _output_counts(browser: Any) -> dict[str, int]:
    previous = __import__("faradaiq.v239_scilink_edison", fromlist=["collect_share_candidates"])
    counts = Counter()
    try:
        for item in previous.collect_share_candidates(browser, previous.load_sharing_settings()):
            group = str(item.get("source_group") or item.get("role") or "Other")
            counts[group] += 1
    except Exception:
        pass
    return dict(counts)


def _schema_model(browser: Any) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    previous = __import__("faradaiq.v239_scilink_edison", fromlist=["_canonical_pair"])
    catalog = _browser_catalog(browser)
    records = list(catalog.get("records") or [])
    technique_counts = dict(catalog.get("technique_counts") or {})
    if not technique_counts:
        technique_counts = dict(Counter(str(r.get("technique_key") or r.get("technique") or "Other") for r in records))
    method_counts = dict(catalog.get("method_counts") or {})
    point_counts = dict(catalog.get("point_counts") or {})
    manifest = getattr(browser, "manifest", None)
    manifest = manifest if isinstance(manifest, dict) else {}
    h5, js = previous._canonical_pair(browser)
    cache_root = Path(str(getattr(browser, "cache_root", "") or ""))
    source_info = _read_json(cache_root / "_bundle_source.json") if cache_root.exists() else {}
    catalog_ready = bool(_load_ready_catalog(cache_root)) if cache_root.exists() else False
    output_counts = _output_counts(browser)
    base_name = str(getattr(browser, "base_name", "") or manifest.get("base_name") or "FaradaIQ dataset")

    blocks: list[dict[str, Any]] = []
    edges: list[tuple[str, str, str]] = []

    def add(block_id: str, column: int, order: int, title: str, subtitle: str, kind: str, details: str, path: Any = None, status: str = "") -> None:
        blocks.append({
            "id": block_id, "column": column, "order": order, "title": title,
            "subtitle": subtitle, "kind": kind, "details": details,
            "path": str(path) if path else "", "status": status,
        })

    raw_count = _safe_count(manifest.get("n_source_files") or manifest.get("source_file_count") or (manifest.get("data_structure") or {}).get("validation", {}).get("source_files"))
    add("source", 0, 0, "SOURCE FILES", base_name, "source", f"Loaded source experiment. Reported source files: {raw_count or 'not specified'}.")
    add("inspect", 1, 0, "INSPECT", "Vendor · hierarchy · methods", "process", "FaradaIQ inspects the source and identifies the experiment structure before export.")
    add("export", 2, 0, "EXPORT JOB", "Archive + analysis-ready tables", "process", "Creates the controlled analysis output, parser inputs, metadata, and optional raw-vendor JSON.")

    add("parsers", 0, 1, "PARSERS", "CV · EIS · DRT · CA · CP · OCP", "process", "Technique-aware parsers normalize vendor outputs and preserve methods, points, cycles, and sweeps.")
    add("h5", 1, 1, "CANONICAL H5", _format_size(h5.stat().st_size) if h5 and h5.exists() else "Not available", "canonical", "Numeric canonical bundle used by the Browser and downstream analysis.", h5, "ready" if h5 and h5.exists() else "missing")
    add("json", 2, 1, "CANONICAL JSON", _format_size(js.stat().st_size) if js and js.exists() else "Not available", "canonical", "Metadata, hierarchy, validation, technique classification, and provenance manifest.", js, "ready" if js and js.exists() else "missing")

    n_tables = _safe_count(source_info.get("n_tables") or len(records))
    add("cache", 0, 2, "TABLE CACHE", f"{n_tables} extracted table(s)", "cache", "Local extracted cache created inside the export job. It should not be rebuilt when the canonical H5 is unchanged.", cache_root if cache_root.exists() else None, "ready" if cache_root.exists() else "missing")
    add("catalog", 1, 2, "CATALOG / INDEX", f"{len(records)} record(s) · {'READY' if catalog_ready else 'needs build'}", "catalog", "Unified technique/method/point index. v240 builds and validates this inside the job before status becomes Completed.", _catalog_path(cache_root) if cache_root.exists() else None, "ready" if catalog_ready else "warning")
    technique_rows = sorted(technique_counts.items(), key=lambda row: (-_safe_count(row[1]), str(row[0]).lower()))
    compact = " · ".join(f"{name} {_safe_count(count)}" for name, count in technique_rows[:7]) or "No detected techniques"
    if len(technique_rows) > 7:
        compact += f" · +{len(technique_rows)-7} more"
    technique_details = ["Detected technique groups:"]
    for name, count in technique_rows:
        technique_details.append(
            f"• {name}: {_safe_count(count)} record(s), {_safe_count(method_counts.get(name))} method(s), {_safe_count(point_counts.get(name))} point(s)"
        )
    add("techniques", 2, 2, "TECHNIQUES", compact, "technique", "\n".join(technique_details))

    analysis_tables = sum(v for k, v in output_counts.items() if k in {"Analysis tables", "Current selection"})
    figures = sum(v for k, v in output_counts.items() if k == "Figures")
    other_outputs = max(0, sum(output_counts.values()) - analysis_tables - figures)
    add("analysis_tables", 0, 3, "ANALYSIS DATA", f"{analysis_tables} table/file candidate(s)", "output", "Normalized CSV/tables available for plotting, fitting, comparison, and export.")
    add("analysis_results", 1, 3, "RESULTS", f"{other_outputs} derived output(s)", "output", "DRT, Tafel, fits, quality checks, reports, and other derived scientific outputs.")
    add("figures", 2, 3, "FIGURES", f"{figures} figure candidate(s)", "output", "Saved plots, contour maps, cubes, images, and report-ready visualizations.")

    add("text_prompt", 0, 4, "PROMPT + SCHEMA", "Text-only default · files unchecked", "handoff", "Editable scientific prompt plus this derived data structure. Raw files remain unchecked unless the user explicitly opts in.", status="ready")
    add("scilink", 1, 4, "SCILINK", "New independent CLI session", "service", "Starts a separate persistent SciLink CLI job using the reviewed prompt, LLM settings, LabRIC MCP settings, and optional checked files.")
    add("edison", 2, 4, "EDISON", "Reviewed text-first job", "service", "Starts a separate Edison Scientific job using the reviewed editable prompt and only explicitly checked files.")

    edges.extend([
        ("source", "inspect", "load"),
        ("inspect", "export", "inspect"),
        ("export", "parsers", "run"),
        ("parsers", "h5", "bundle"),
        ("parsers", "json", "metadata"),
        ("h5", "cache", "extract"),
        ("json", "catalog", "classify"),
        ("cache", "catalog", "index"),
        ("catalog", "techniques", "detect"),
        ("techniques", "analysis_tables", "analyze"),
        ("analysis_tables", "analysis_results", "derive"),
        ("analysis_results", "figures", "visualize"),
        ("catalog", "text_prompt", "schema"),
        ("text_prompt", "scilink", "review"),
        ("text_prompt", "edison", "review"),
    ])
    return blocks, edges

def _palette(kind: str, status: str = "") -> tuple[str, str, str]:
    if status == "missing":
        return "#4b5563", "#f9fafb", "#9ca3af"
    if status == "warning":
        return "#78350f", "#fff7ed", "#f59e0b"
    table = {
        "source": ("#1d4ed8", "#ffffff", "#60a5fa"),
        "process": ("#6d28d9", "#ffffff", "#a78bfa"),
        "canonical": ("#0f766e", "#ffffff", "#5eead4"),
        "cache": ("#075985", "#ffffff", "#38bdf8"),
        "catalog": ("#065f46", "#ffffff", "#34d399"),
        "technique": ("#9a3412", "#ffffff", "#fb923c"),
        "output": ("#166534", "#ffffff", "#4ade80"),
        "handoff": ("#374151", "#ffffff", "#facc15"),
        "service": ("#4338ca", "#ffffff", "#818cf8"),
    }
    return table.get(kind, ("#374151", "#ffffff", "#9ca3af"))


def _draw_visual_schema(browser: Any, *, fit: bool = False) -> None:
    canvas = getattr(browser, "_v240_schema_canvas", None)
    if canvas is None:
        return
    try:
        if not canvas.winfo_exists():
            return
    except Exception:
        return
    blocks, edges = _schema_model(browser)
    browser._v240_schema_blocks = {row["id"]: row for row in blocks}
    canvas.delete("all")

    # The Browser center pane can be narrow when the experiment tree and
    # workspace are both visible.  Keep the blocks readable by default and
    # allow scrolling; Fit all is an explicit overview mode.
    base_column_width = 238
    base_gap = 34
    base_left = 108
    base_top = 34
    base_block_height = 104
    base_vgap = 20
    columns = 3
    max_rows = max([1] + [1 + int(row["order"]) for row in blocks])
    layout_width = base_left + 28 + columns * base_column_width + (columns - 1) * base_gap
    layout_height = base_top + max_rows * (base_block_height + base_vgap) + 28

    try:
        canvas.update_idletasks()
        visible_w = max(100, int(canvas.winfo_width()))
        visible_h = max(100, int(canvas.winfo_height()))
    except Exception:
        visible_w, visible_h = 1200, 620
    scale = float(getattr(browser, "_v240_schema_scale", 0.9) or 0.9)
    if fit or getattr(browser, "_v240_schema_auto_fit", False):
        scale = min(1.08, max(0.52, min((visible_w - 14) / layout_width, (visible_h - 14) / layout_height)))
        browser._v240_schema_scale = scale
    s = scale
    column_width = base_column_width * s
    gap = base_gap * s
    left = base_left * s
    top = base_top * s
    block_height = base_block_height * s
    vgap = base_vgap * s
    font_title = ("Segoe UI", max(9, int(round(12 * s))), "bold")
    font_sub = ("Segoe UI", max(8, int(round(9.5 * s))))
    font_head = ("Segoe UI", max(8, int(round(10 * s))), "bold")
    canvas.configure(background="#0b1220")

    row_titles = ["INPUT / JOB", "CANONICAL", "BROWSER", "ANALYSIS", "HANDOFF"]
    for row_index, title in enumerate(row_titles):
        y = top + row_index * (block_height + vgap) + block_height / 2
        canvas.create_text(12 * s, y, text=title, fill="#cbd5e1", font=font_head, anchor="w", width=86 * s)
        canvas.create_line(92 * s, y, left - 8 * s, y, fill="#334155", width=max(1, int(2 * s)))

    positions: dict[str, tuple[float, float, float, float]] = {}
    for row in blocks:
        col = int(row["column"])
        order = int(row["order"])
        x1 = left + col * (column_width + gap)
        y1 = top + order * (block_height + vgap)
        x2 = x1 + column_width
        y2 = y1 + block_height
        positions[row["id"]] = (x1, y1, x2, y2)

    # Draw connectors first so blocks remain legible.
    for src, dst, label in edges:
        if src not in positions or dst not in positions:
            continue
        sx1, sy1, sx2, sy2 = positions[src]
        dx1, dy1, dx2, dy2 = positions[dst]
        if dx1 >= sx2:
            start = (sx2, (sy1 + sy2) / 2)
            end = (dx1, (dy1 + dy2) / 2)
            midx = (start[0] + end[0]) / 2
            points = [start[0], start[1], midx, start[1], midx, end[1], end[0], end[1]]
        else:
            start = ((sx1 + sx2) / 2, sy2)
            end = ((dx1 + dx2) / 2, dy1)
            midy = (start[1] + end[1]) / 2
            points = [start[0], start[1], start[0], midy, end[0], midy, end[0], end[1]]
        canvas.create_line(*points, fill="#64748b", width=max(1, int(2 * s)), arrow="last", arrowshape=(8 * s, 10 * s, 4 * s), smooth=False, tags=("schema_edge",))
        if label and s >= 0.72:
            mx = (start[0] + end[0]) / 2
            my = (start[1] + end[1]) / 2
            canvas.create_text(mx, my - 6 * s, text=label, fill="#94a3b8", font=("Segoe UI", max(7, int(8 * s))), tags=("schema_edge",))

    for row in blocks:
        x1, y1, x2, y2 = positions[row["id"]]
        fill, fg, outline = _palette(str(row.get("kind") or ""), str(row.get("status") or ""))
        tag = f"block::{row['id']}"
        outline_tag = f"outline::{row['id']}"
        canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline, width=max(2, int(2.5 * s)), tags=("schema_outline", outline_tag, tag))
        canvas.create_rectangle(x1, y1, x1 + 7 * s, y2, fill=outline, outline=outline, tags=("schema_block", tag))
        canvas.create_text(x1 + 18 * s, y1 + 15 * s, text=str(row["title"]), fill=fg, font=font_title, anchor="nw", width=column_width - 34 * s, tags=("schema_block", tag))
        canvas.create_text(x1 + 18 * s, y1 + 58 * s, text=str(row["subtitle"]), fill="#e2e8f0", font=font_sub, anchor="nw", width=column_width - 34 * s, tags=("schema_block", tag))

        def select_block(_event: Any = None, block_id: str = row["id"]) -> None:
            _show_block_details(browser, block_id)

        def open_block(_event: Any = None, block_id: str = row["id"]) -> None:
            block = getattr(browser, "_v240_schema_blocks", {}).get(block_id, {})
            path = Path(str(block.get("path") or ""))
            if path.exists():
                try:
                    os.startfile(str(path))  # type: ignore[attr-defined]
                except Exception:
                    try:
                        webbrowser.open(path.resolve().as_uri())
                    except Exception:
                        pass

        canvas.tag_bind(tag, "<Button-1>", select_block)
        canvas.tag_bind(tag, "<Double-Button-1>", open_block)
        canvas.tag_bind(tag, "<Enter>", lambda _e, t=outline_tag: canvas.itemconfigure(t, width=max(3, int(4 * s))))
        canvas.tag_bind(tag, "<Leave>", lambda _e, t=outline_tag: canvas.itemconfigure(t, width=max(2, int(2.5 * s))))

    total_w = layout_width * s + 20
    total_h = layout_height * s + 20
    canvas.configure(scrollregion=(0, 0, total_w, total_h))
    try:
        if fit or getattr(browser, "_v240_schema_auto_fit", True):
            canvas.xview_moveto(0.0)
            canvas.yview_moveto(0.0)
    except Exception:
        pass
    summary = getattr(browser, "_v240_schema_summary_var", None)
    if summary is not None:
        ready = "job-built catalog ready" if _load_ready_catalog(getattr(browser, "cache_root", "")) else "catalog not yet validated"
        mode = "Fit overview" if getattr(browser, "_v240_schema_auto_fit", False) else "Readable view"
        summary.set(f"{mode} · {len(blocks)} blocks · {len(_browser_catalog(browser).get('records') or [])} records · {ready}")


def _show_block_details(browser: Any, block_id: str) -> None:
    block = getattr(browser, "_v240_schema_blocks", {}).get(block_id)
    details = getattr(browser, "_v240_schema_details", None)
    canvas = getattr(browser, "_v240_schema_canvas", None)
    if not block or details is None:
        return
    if canvas is not None:
        try:
            canvas.itemconfigure("schema_outline", width=2)
            canvas.itemconfigure(f"outline::{block_id}", width=5)
        except Exception:
            pass
    lines = [str(block.get("title") or ""), "", str(block.get("subtitle") or ""), "", str(block.get("details") or "")]
    path = str(block.get("path") or "")
    if path:
        lines.extend(["", f"Local path: {path}", f"Exists: {Path(path).exists()}"])
    try:
        details.configure(state="normal")
        details.delete("1.0", "end")
        details.insert("1.0", "\n".join(lines))
        details.configure(state="disabled")
    except Exception:
        pass


def _zoom_schema(browser: Any, factor: float) -> None:
    browser._v240_schema_auto_fit = False
    current = float(getattr(browser, "_v240_schema_scale", 1.0) or 1.0)
    browser._v240_schema_scale = max(0.45, min(1.6, current * factor))
    _draw_visual_schema(browser)


def _fit_schema(browser: Any) -> None:
    browser._v240_schema_auto_fit = True
    _draw_visual_schema(browser, fit=True)


def _readable_schema(browser: Any) -> None:
    """Restore a readable default scale instead of shrinking every block."""
    browser._v240_schema_auto_fit = False
    browser._v240_schema_scale = 0.9
    _draw_visual_schema(browser)


def _schedule_schema_draw(browser: Any, delay: int = 80, *, fit: bool = False) -> None:
    old = getattr(browser, "_v240_schema_after_id", None)
    if old:
        try:
            browser.after_cancel(old)
        except Exception:
            pass
    try:
        browser._v240_schema_after_id = browser.after(delay, lambda: _draw_visual_schema(browser, fit=fit))
    except Exception:
        _draw_visual_schema(browser, fit=fit)


def _populate_detail_tree(browser: Any) -> None:
    tree = getattr(browser, "_v240_schema_tree", None)
    if tree is None:
        return
    tree.delete(*tree.get_children(""))
    catalog = _browser_catalog(browser)
    root = tree.insert("", "end", text=str(getattr(browser, "base_name", "") or "FaradaIQ dataset"), values=("Dataset", len(catalog.get("records") or [])), open=True)
    by_tech: dict[str, list[dict[str, Any]]] = {}
    for rec in catalog.get("records") or []:
        by_tech.setdefault(str(rec.get("technique_key") or rec.get("technique") or "Other"), []).append(rec)
    for tech, rows in sorted(by_tech.items(), key=lambda item: item[0].lower()):
        tech_iid = tree.insert(root, "end", text=tech, values=("Technique", len(rows)), open=False)
        methods: dict[int, list[dict[str, Any]]] = {}
        for rec in rows:
            methods.setdefault(_safe_count(rec.get("method_index")) or 1, []).append(rec)
        for method, method_rows in sorted(methods.items()):
            method_iid = tree.insert(tech_iid, "end", text=f"Method {method:02d}", values=("Method", len(method_rows)), open=False)
            for rec in method_rows[:500]:
                label = str(rec.get("label") or rec.get("name") or Path(str(rec.get("path") or "record")).name)
                tree.insert(method_iid, "end", text=label, values=("Record", str(rec.get("path") or "")))


def populate_visual_schema(browser: Any) -> None:
    _populate_detail_tree(browser)
    _schedule_schema_draw(browser, 40, fit=False)
    browser._v240_schema_first_draw = False


def add_visual_schema_tab(browser: Any) -> None:
    if getattr(browser, "_v240_visual_schema_added", False):
        populate_visual_schema(browser)
        return
    tk = _g("tk")
    ttk = _g("ttk")
    ScrolledText = _g("ScrolledText")
    notebook = getattr(browser, "_v226_center_notebook", None) or getattr(browser, "_v217_center_notebook", None) or getattr(browser, "_v216_center_notebook", None)
    if notebook is None:
        return
    tab = ttk.Frame(notebook, style="Card.TFrame", padding=6)
    notebook.add(tab, text="Visual Data Schema")
    try:
        target = notebook.index("end") - 1
        for index, tab_id in enumerate(notebook.tabs()):
            label = str(notebook.tab(tab_id, "text") or "").lower()
            if "visual" in label and "schema" not in label:
                target = index + 1
        notebook.insert(target, tab)
    except Exception:
        pass

    head = ttk.Frame(tab, style="Card.TFrame")
    head.pack(fill="x", pady=(0, 5))
    head.columnconfigure(0, weight=1)
    browser._v240_schema_summary_var = tk.StringVar(value="Preparing visual block schema…")
    ttk.Label(head, textvariable=browser._v240_schema_summary_var, style="Muted.TLabel", wraplength=1050).grid(row=0, column=0, sticky="ew", columnspan=6, pady=(0, 4))
    ttk.Button(head, text="Read", command=lambda: _readable_schema(browser)).grid(row=1, column=0, sticky="w")
    ttk.Button(head, text="Fit all", command=lambda: _fit_schema(browser)).grid(row=1, column=1, sticky="w", padx=(5, 0))
    ttk.Button(head, text="+", width=4, command=lambda: _zoom_schema(browser, 1.15)).grid(row=1, column=2, sticky="w", padx=(5, 0))
    ttk.Button(head, text="−", width=4, command=lambda: _zoom_schema(browser, 1 / 1.15)).grid(row=1, column=3, sticky="w", padx=(5, 0))
    ttk.Button(head, text="Refresh", command=lambda: populate_visual_schema(browser)).grid(row=1, column=5, sticky="e", padx=(5, 0))

    inner = ttk.Notebook(tab)
    inner.pack(fill="both", expand=True)
    visual_tab = ttk.Frame(inner, style="Card.TFrame")
    tree_tab = ttk.Frame(inner, style="Card.TFrame")
    inner.add(visual_tab, text="Block diagram")
    inner.add(tree_tab, text="Detailed tree")

    visual_tab.rowconfigure(0, weight=1)
    visual_tab.columnconfigure(0, weight=1)
    canvas_frame = ttk.Frame(visual_tab, style="Card.TFrame")
    details_frame = ttk.Frame(visual_tab, style="Card.TFrame")
    canvas_frame.grid(row=0, column=0, sticky="nsew")
    details_frame.grid(row=1, column=0, sticky="ew", pady=(5, 0))
    canvas_frame.rowconfigure(0, weight=1)
    canvas_frame.columnconfigure(0, weight=1)
    canvas = tk.Canvas(canvas_frame, background="#0b1220", highlightthickness=1, highlightbackground="#334155")
    xbar = ttk.Scrollbar(canvas_frame, orient="horizontal", command=canvas.xview)
    ybar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
    canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    ybar.grid(row=0, column=1, sticky="ns")
    xbar.grid(row=1, column=0, sticky="ew")
    details = ScrolledText(details_frame, wrap="word", height=4)
    details.pack(fill="both", expand=True)
    details.insert("1.0", "Click a block to see its role and local readiness. Double-click a block with a local path to open it.")
    details.configure(state="disabled")

    tree_frame = ttk.Frame(tree_tab, style="Card.TFrame")
    tree_frame.pack(fill="both", expand=True)
    tree_frame.rowconfigure(0, weight=1)
    tree_frame.columnconfigure(0, weight=1)
    tree = ttk.Treeview(tree_frame, columns=("kind", "summary"), show="tree headings", selectmode="browse")
    tree.heading("#0", text="Technique / method / record")
    tree.heading("kind", text="Type")
    tree.heading("summary", text="Count or local path")
    tree.column("#0", width=430, stretch=True)
    tree.column("kind", width=120, stretch=False)
    tree.column("summary", width=560, stretch=True)
    tree_y = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=tree_y.set, xscrollcommand=tree_x.set)
    tree.grid(row=0, column=0, sticky="nsew")
    tree_y.grid(row=0, column=1, sticky="ns")
    tree_x.grid(row=1, column=0, sticky="ew")

    browser._v240_schema_canvas = canvas
    browser._v240_schema_details = details
    browser._v240_schema_tree = tree
    browser._v240_schema_inner_notebook = inner
    browser._v240_schema_tab = tab
    browser._v240_schema_scale = 0.9
    browser._v240_schema_auto_fit = False
    browser._v240_schema_first_draw = True
    browser._v240_visual_schema_added = True
    # Compatibility flag used by the v239 Browser refresh wrapper.
    browser._v239_schema_tab_added = True

    def on_canvas_configure(_event: Any = None) -> None:
        if getattr(browser, "_v240_schema_auto_fit", False):
            _schedule_schema_draw(browser, 140, fit=True)

    def on_wheel(event: Any) -> str:
        delta = -1 if getattr(event, "delta", 0) > 0 else 1
        if getattr(event, "state", 0) & 0x0001:
            canvas.xview_scroll(delta, "units")
        else:
            canvas.yview_scroll(delta, "units")
        return "break"

    canvas.bind("<Configure>", on_canvas_configure)
    canvas.bind("<MouseWheel>", on_wheel)
    canvas.bind("<Shift-MouseWheel>", on_wheel)

    def on_outer_tab(_event: Any = None) -> None:
        try:
            if notebook.select() == str(tab):
                _schedule_schema_draw(browser, 80, fit=bool(getattr(browser, "_v240_schema_auto_fit", True)))
        except Exception:
            pass

    notebook.bind("<<NotebookTabChanged>>", on_outer_tab, add="+")
    for delay in (50, 200, 650, 1400):
        try:
            browser.after(delay, lambda b=browser: populate_visual_schema(b))
        except Exception:
            pass


def _patch_previous_schema() -> None:
    previous = __import__("faradaiq.v239_scilink_edison", fromlist=["VERSION"])
    previous.VERSION = VERSION
    previous._add_schema_tab = add_visual_schema_tab
    previous._populate_schema = populate_visual_schema


def _patch_catalog_builder() -> None:
    global _ORIGINAL_BUILD_CATALOG
    catalog_mod = __import__("faradaiq.browser_catalog_v209", fromlist=["build_catalog"])
    ui_mod = __import__("faradaiq.browser_ui_v209", fromlist=["build_catalog"])
    if getattr(catalog_mod, "_v240_catalog_cache_installed", False):
        return
    _ORIGINAL_BUILD_CATALOG = catalog_mod.build_catalog
    catalog_mod.build_catalog = build_catalog_cached
    ui_mod.build_catalog = build_catalog_cached
    catalog_mod._v240_catalog_cache_installed = True


def _patch_batch_jobs() -> None:
    global _ORIGINAL_RUN_BATCH_JOB
    jobs = __import__("faradaiq.v233_batch_jobs", fromlist=["run_batch_job"])
    if getattr(jobs, "_v240_catalog_completion_installed", False):
        return
    _ORIGINAL_RUN_BATCH_JOB = jobs.run_batch_job
    jobs.run_batch_job = run_batch_job_with_catalog
    jobs.VERSION = VERSION
    jobs.APP_VERSION = "0.240.0"
    # Make the visible setting describe the actual completion contract.
    jobs._v240_catalog_completion_installed = True


def _patch_titles(core: MutableMapping[str, Any]) -> None:
    def apply_title(window: Any, base: str = "FaradaIQ Studio v290") -> None:
        try:
            value = re.sub(r"v\d+(?:\.\d+)?", VERSION, str(base or "FaradaIQ Studio v290"))
            window.tk.call("wm", "title", window._w, value)
        except Exception:
            try:
                window.title("FaradaIQ Studio v290")
            except Exception:
                pass
    core["_v240_apply_title"] = apply_title
    core["_v753_apply_title"] = apply_title


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE
    _CORE = core
    _patch_catalog_builder()
    _patch_batch_jobs()
    _patch_previous_schema()
    _patch_titles(core)
    core["_v240_build_catalog_cached"] = build_catalog_cached
    core["_v240_load_ready_catalog"] = _load_ready_catalog
    core["_v240_add_visual_schema_tab"] = add_visual_schema_tab
    core["_v240_populate_visual_schema"] = populate_visual_schema
    core["_v240_run_batch_job_with_catalog"] = run_batch_job_with_catalog
    for name in ("_v233_run_batch_job", "_v232_run_batch_job", "_v230_run_batch_job", "_v229_run_batch_job"):
        core[name] = run_batch_job_with_catalog
    help_tabs = core.get("_V207_HELP_TABS")
    help_title = "v240 visual block schema and job-ready Browser catalog"
    if isinstance(help_tabs, list) and not any(str(row[0]) == help_title for row in help_tabs if isinstance(row, (list, tuple)) and row):
        help_tabs.append((help_title, r"""
VISUAL DATA SCHEMA

Open the Browser and select Visual Data Schema beside the visualization tabs. Block diagram is the default view. It shows the source experiment, job pipeline, canonical H5/JSON, Browser cache and catalog, detected techniques, analysis outputs, and the text-first SciLink/Edison handoff. Click a block for details. Double-click a block with a local path to open it. Use Read for legible blocks with scrolling, Fit all for a complete overview, + or - for zoom, and Refresh after new outputs appear. Detailed tree shows the technique, method, and record hierarchy.

JOB COMPLETION AND IMMEDIATE BROWSER LOADING

When Build canonical H5 + JSON and Prebuild browser cache + catalog are enabled, a job cannot become Completed until all of these stages pass: parse/export, technique parsers, canonical H5/JSON, extracted Browser table cache, unified Browser catalog/index, and readiness validation. The completed job stores _faradaiq_browser_catalog_v216.json and _faradaiq_browser_ready_v240.json inside .browser_cache_from_h5. Loading the Browser reuses this catalog when the H5/cache signature is unchanged, so cataloging does not start after the job finishes. Changed H5 data invalidates the catalog and triggers one controlled rebuild.
"""))
