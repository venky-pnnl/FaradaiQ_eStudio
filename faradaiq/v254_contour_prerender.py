"""FaradaIQ Studio v273 contour pre-render and cache pipeline.

Hyperspectral/stack views are now completely generated before a visualization
window is opened.  Every contour frame is materialized, serialized, and
validated first.  The prepared result is cached by selected source files and
visualization settings so reopening the same view is immediate.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Sequence

VERSION = "v273"
_CORE: MutableMapping[str, Any] = {}
_CACHE_SCHEMA = "faradaiq-contour-prerender-cache-v254"
_CACHE_GENERATION = 254


def _notify(browser: Any, message: str) -> None:
    """Send status/log text to Tk safely when possible."""
    def emit() -> None:
        try:
            browser.log(message)
        except Exception:
            pass
        try:
            if hasattr(browser, "set_status"):
                browser.set_status(message)
        except Exception:
            pass
    try:
        browser.after(0, emit)
    except Exception:
        emit()


def _selected_records(browser: Any) -> list[dict[str, Any]]:
    try:
        from faradaiq import v228_intelligence as intel
        return [dict(item) for item in intel._selected_records(browser)]
    except Exception:
        rows: list[dict[str, Any]] = []
        try:
            selected = list(browser.tree.selection() or [])
        except Exception:
            selected = []
        record_by_iid = getattr(browser, "_record_by_iid", {}) or {}
        for iid in selected:
            rec = record_by_iid.get(iid)
            if rec:
                rows.append(dict(rec))
        return rows


def _source_fingerprint(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        raw = str(record.get("path") or "").strip()
        if not raw:
            continue
        path = Path(raw)
        try:
            resolved = str(path.resolve())
        except Exception:
            resolved = str(path)
        key = resolved.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            stat = path.stat()
            size = int(stat.st_size)
            mtime_ns = int(stat.st_mtime_ns)
        except Exception:
            size = -1
            mtime_ns = -1
        entries.append({
            "path": resolved,
            "size": size,
            "mtime_ns": mtime_ns,
            "technique": str(record.get("technique_key") or ""),
            "method": record.get("method_index"),
            "point": record.get("point"),
            "cycle": record.get("cycle") or record.get("sweep"),
        })
    entries.sort(key=lambda row: row["path"].lower())
    return entries


def _cache_key(browser: Any, records: Sequence[Mapping[str, Any]], metric: str, options: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    payload = {
        "schema": _CACHE_SCHEMA,
        "generation": _CACHE_GENERATION,
        "version": VERSION,
        "base_name": str(getattr(browser, "base_name", "") or ""),
        "metric": str(metric or "Auto"),
        "options": dict(options),
        "sources": _source_fingerprint(records),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24], payload


def _cache_root(browser: Any, md: Any) -> Path:
    root = Path(md._view_dir(browser)) / "contour_prerender_cache_v254"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temp, path)


def _iter_result_paths(result: Mapping[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for key in ("html", "cube", "metadata", "cube_index"):
        raw = result.get(key)
        if raw:
            paths.append(Path(str(raw)))
    for group_key in ("groups", "cycle_views"):
        for item in result.get(group_key, []) or []:
            if isinstance(item, Mapping):
                paths.extend(_iter_result_paths(item))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.resolve()).lower()
        except Exception:
            key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _result_is_ready(result: Mapping[str, Any]) -> bool:
    html = result.get("html")
    if not html or not Path(str(html)).is_file():
        return False
    for path in _iter_result_paths(result):
        if not path.exists() or (path.is_file() and path.stat().st_size <= 0):
            return False
    return True


def _stamp_prerender_artifacts(result: Mapping[str, Any]) -> None:
    """Mark every generated HTML/JSON artifact as fully pre-rendered."""
    frame_total = _frame_count(result)
    marker = (
        '<meta name="faradaiq-contour-prerender" content="v254; before-open; frames='
        + str(frame_total) + '">'
    )
    for path in _iter_result_paths(result):
        try:
            suffix = path.suffix.lower()
            if suffix == ".html":
                html = path.read_text(encoding="utf-8")
                if "faradaiq-contour-prerender" not in html:
                    if "</head>" in html:
                        html = html.replace("</head>", marker + "</head>", 1)
                    else:
                        html = marker + html
                    path.write_text(html, encoding="utf-8")
            elif suffix == ".json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    payload.update({
                        "pre_rendered": True,
                        "pre_rendered_before_open": True,
                        "pre_rendered_frame_count": frame_total,
                        "contour_cache_generation": _CACHE_GENERATION,
                    })
                    _atomic_json(path, payload)
        except Exception:
            # The final readiness gate still validates required files. A marker
            # write failure must not corrupt an otherwise valid visualization.
            continue


def _frame_count(result: Mapping[str, Any]) -> int:
    direct = result.get("total_slider_frames") or result.get("n_bands") or 0
    try:
        total = int(direct)
    except Exception:
        total = 0
    children = result.get("groups") or result.get("cycle_views") or []
    if children:
        child_total = sum(_frame_count(item) for item in children if isinstance(item, Mapping))
        if child_total:
            total = child_total
    return max(0, total)


def _read_cache(manifest_path: Path, expected_key: str) -> Optional[dict[str, Any]]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("schema") != _CACHE_SCHEMA or payload.get("cache_key") != expected_key:
            return None
        result = payload.get("result")
        if not isinstance(result, dict) or not _result_is_ready(result):
            return None
        return dict(result)
    except Exception:
        return None


def _build_hyperspectral(browser: Any, metric: str, options: Mapping[str, Any], records: Sequence[Mapping[str, Any]], md: Any) -> dict[str, Any]:
    """Build every selected group and every contour frame before returning."""
    from faradaiq import v228_intelligence as intel

    captured = [dict(item) for item in records]
    curves = intel._curves_from_records(browser, captured)
    if len(curves) < 2:
        raise ValueError("Hyperspectral/stack mode needs at least two compatible point or position curves.")
    groups = md._curve_groups(curves, min_size=2)
    if not groups:
        raise ValueError("No compatible repeated technique curves were found for hyperspectral mode.")

    outputs: list[dict[str, Any]] = []
    group_count = len(groups)
    for index, (label, items) in enumerate(groups, 1):
        _notify(browser, f"Pre-rendering contour group {index} of {group_count}: {label}")
        output = md._make_hyperspectral_group(browser, items, label, f"prerender_g{index:02d}", options)
        if not _result_is_ready(output):
            raise RuntimeError(f"Contour group {index} did not finish writing all required files.")
        outputs.append(dict(output))

    if len(outputs) == 1:
        result = dict(outputs[0])
    else:
        outdir = md._view_dir(browser)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        master = outdir / f"{md._slug(getattr(browser, 'base_name', 'data'))}_hyperspectral_prerendered_{stamp}.html"
        md._write_master_index(master, f"FaradaIQ {VERSION} pre-rendered multi-technique hyperspectral viewer", outputs)
        index = master.with_suffix(".cube_index.json")
        _atomic_json(index, {
            "schema": "faradaiq-hyperspectral-prerender-index-v254",
            "version": VERSION,
            "groups": outputs,
            "options": dict(options),
        })
        result = {
            **outputs[0],
            "html": str(master),
            "engine": "Plotly pre-rendered multi-technique hyperspectral",
            "groups": outputs,
            "group_count": len(outputs),
            "cube_index": str(index),
            "curves": len(curves),
        }

    frame_total = _frame_count(result)
    result.update({
        "version": VERSION,
        "pre_rendered": True,
        "pre_rendered_before_open": True,
        "pre_rendered_frame_count": frame_total,
        "contour_cache_generation": _CACHE_GENERATION,
        "cache_hit": False,
    })
    _stamp_prerender_artifacts(result)
    if not _result_is_ready(result):
        raise RuntimeError("The pre-rendered visualization failed final output validation.")
    return result


def _install_generation_pipeline() -> None:
    from faradaiq import multidim_v216 as md

    previous_generate = md.generate
    if getattr(previous_generate, "_v254_contour_prerender", False):
        return

    def generate(browser: Any, mode: str, metric: str = "Auto", options: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        mode_l = str(mode or "").lower()
        if not any(token in mode_l for token in ("hyper", "cube", "stack")):
            return previous_generate(browser, mode, metric, options)

        opts = md._v216_options(options)
        records = _selected_records(browser)
        if not records:
            raise ValueError("Select numeric technique data before opening a hyperspectral visualization.")
        cache_key, signature = _cache_key(browser, records, metric, opts)
        root = _cache_root(browser, md)
        manifest_path = root / f"{cache_key}.json"

        cached = _read_cache(manifest_path, cache_key)
        if cached is not None:
            cached.update({
                "version": VERSION,
                "pre_rendered": True,
                "pre_rendered_before_open": True,
                "cache_hit": True,
                "contour_cache_manifest": str(manifest_path),
            })
            _notify(browser, f"Using cached pre-rendered contour view ({_frame_count(cached)} frames). Opening now.")
            md._open_html_process(Path(str(cached["html"])))
            return cached

        _notify(browser, "Generating and pre-rendering every contour frame before opening the visualization...")
        started = time.perf_counter()
        result = _build_hyperspectral(browser, metric, opts, records, md)
        elapsed = time.perf_counter() - started
        result.update({
            "contour_cache_manifest": str(manifest_path),
            "pre_render_seconds": round(elapsed, 6),
        })
        manifest = {
            "schema": _CACHE_SCHEMA,
            "version": VERSION,
            "cache_key": cache_key,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "signature": signature,
            "result": result,
        }
        _atomic_json(manifest_path, manifest)
        # Hard gate: do not open a shell or the final viewer until every frame,
        # metadata file, cube, and master index has been written and validated.
        if not _result_is_ready(result) or not manifest_path.is_file():
            raise RuntimeError("Contour pre-render validation did not complete; visualization was not opened.")
        _notify(browser, f"Pre-rendered {_frame_count(result)} contour frames in {elapsed:.1f} s. Opening visualization.")
        md._open_html_process(Path(str(result["html"])))
        return result

    generate._v254_contour_prerender = True  # type: ignore[attr-defined]
    generate._v254_previous_generate = previous_generate  # type: ignore[attr-defined]
    md.generate = generate
    md._v254_contour_prerender_installed = True


def _normalize_titles() -> None:
    import re
    for cls_name in ("SessionReaderGUI", "FaradaIQH5Browser", "FaradaIQDataBrowser"):
        cls = _CORE.get(cls_name)
        if cls is None or getattr(cls, "_v254_title_patch", False):
            continue
        old = getattr(cls, "title", None)
        if not callable(old):
            continue
        def title(self: Any, text: Any = None, _old=old) -> Any:
            if text is None:
                return _old(self)
            return _old(self, re.sub(r"v\d+(?:\.\d+)?", VERSION, str(text)))
        cls.title = title
        cls.wm_title = title
        cls._v254_title_patch = True


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE
    _CORE = core
    from faradaiq import multidim_v216 as md
    import faradaiq.v228_intelligence as intel
    import faradaiq.v247_experience as experience
    import faradaiq.v250_reliability as reliability
    import faradaiq.v253_visual_browser as visual
    import faradaiq.v253_llm_qa as llmqa
    import faradaiq.v251_context_scilink as context
    import faradaiq.v245_queue_scilink as queue
    import faradaiq.v244_workflow_cleanup as cleanup
    import faradaiq.v239_scilink_edison as services
    for module in (md, intel, experience, reliability, visual, llmqa, context, queue, cleanup, services):
        try:
            module.VERSION = VERSION
        except Exception:
            pass
    _install_generation_pipeline()
    _normalize_titles()
    core["FARADAIQ_CONTOUR_PRERENDER_BEFORE_OPEN"] = True
    core["FARADAIQ_CONTOUR_PRERENDER_CACHE"] = True
    core["FARADAIQ_CONTOUR_CACHE_GENERATION"] = _CACHE_GENERATION
