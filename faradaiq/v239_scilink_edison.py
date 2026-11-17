"""FaradaIQ Studio v273 consolidated SciLink / Edison workflow.

This patch intentionally separates text-only LLM prompt generation from data sharing.
Files are staged only for SciLink or Edison Scientific after an explicit review dialog.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

VERSION = "v273"
DEFAULT_MCP_URL = "https://mcp.labric.co/mcp"
_CORE: dict[str, Any] = {}

_DEFAULT_SHARING = {
    "canonical_h5": True,
    "canonical_json": True,
    "selected_files": True,
    "analysis_tables": True,
    "figures": True,
    "raw_source": False,
    "session_metadata": True,
    "literature_folder_enabled": False,
    "literature_folder": "",
    "additional_folder_enabled": False,
    "additional_folder": "",
    "max_file_mb": 500,
    "max_files": 400,
    "labric_mcp_enabled": False,
    "scilink_labric_cloud_context_enabled": False,
    "labric_mcp_url": DEFAULT_MCP_URL,
    "scilink_executable": "",
    "scilink_unsafe_execution_ok": True,
    "scilink_literature_analysis_enabled": True,
    "scilink_wait_for_user_questions": True,
    "edison_base_url": "https://platform.edisonscientific.com/",
    "edison_upload_url": "",
}


def _g(name: str, default: Any = None) -> Any:
    return _CORE.get(name, default)


def _settings_path() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "FaradaIQ" / "Studio" / VERSION / "data_sharing_settings.json"
    return Path.home() / ".faradaiq" / "studio" / VERSION / "data_sharing_settings.json"


def load_sharing_settings() -> dict[str, Any]:
    cfg = dict(_DEFAULT_SHARING)
    try:
        p = _settings_path()
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg.update(raw)
    except Exception:
        pass
    # Session environment always wins for service endpoints.
    cfg["labric_mcp_url"] = os.environ.get("LABRIC_MCP_URL", os.environ.get("MCP_SERVER_URL", cfg["labric_mcp_url"]))
    cfg["edison_base_url"] = os.environ.get("EDISON_BASE_URL", os.environ.get("EDISON_SCIENTIFIC_BASE_URL", cfg["edison_base_url"]))
    cfg["edison_upload_url"] = os.environ.get("EDISON_UPLOAD_URL", os.environ.get("EDISON_SCIENTIFIC_UPLOAD_URL", cfg["edison_upload_url"]))
    cfg["scilink_executable"] = os.environ.get("SCILINK_EXE", os.environ.get("SCILINK_CLI", cfg["scilink_executable"]))
    return cfg


def save_sharing_settings(cfg: Mapping[str, Any]) -> Path:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    safe = dict(_DEFAULT_SHARING)
    safe.update(dict(cfg))
    p.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _safe_name(value: Any, fallback: str = "faradaiq_job") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    return text[:90] or fallback


def _format_size(value: int | float | None) -> str:
    try:
        n = float(value or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
    except Exception:
        return ""
    return ""


def _gui_from(source: Any) -> Any:
    return getattr(source, "gui", source)


def _browser_from(source: Any) -> Any | None:
    if source is None:
        return None
    if hasattr(source, "tree") and (hasattr(source, "analysis_root") or hasattr(source, "cache_root")):
        return source
    return None


def _base_name(gui: Any) -> str:
    fn = _g("_v27_current_base_name")
    if callable(fn):
        try:
            value = fn(gui)
            if value:
                return str(value)
        except Exception:
            pass
    for name in ("base_name", "current_base_name"):
        value = getattr(gui, name, None)
        if value:
            return str(value)
    try:
        path = str(gui.file_var.get()).strip()
        if path:
            return Path(path).stem
    except Exception:
        pass
    return "FaradaIQ_analysis"


def _analysis_root(source: Any) -> Path:
    browser = _browser_from(source)
    if browser is not None:
        for name in ("analysis_root", "cache_root", "analyzed_root"):
            value = getattr(browser, name, None)
            if value:
                try:
                    return Path(value).resolve()
                except Exception:
                    pass
        h5 = getattr(browser, "h5_path", None)
        if h5:
            return Path(h5).resolve().parent
    gui = _gui_from(source)
    fn = _g("_v55_find_existing_analysis_root")
    if callable(fn):
        try:
            root = fn(gui, base_name=_base_name(gui))
            if root:
                return Path(root).resolve()
        except Exception:
            pass
    for name in ("output_var", "file_var"):
        try:
            value = str(getattr(gui, name).get()).strip()
            if value:
                p = Path(value).resolve()
                return p if p.is_dir() else p.parent
        except Exception:
            pass
    return Path.cwd().resolve()


def _canonical_pair(source: Any) -> tuple[Path | None, Path | None]:
    browser = _browser_from(source)
    if browser is not None:
        h5 = getattr(browser, "h5_path", None)
        js = getattr(browser, "json_path", None) or getattr(browser, "manifest_path", None)
        if h5 and Path(h5).exists():
            hp = Path(h5).resolve()
            jp = Path(js).resolve() if js and Path(js).exists() else None
            if jp is None:
                guesses = list(hp.parent.glob("*faradaiq_bundle*.json")) + list(hp.parent.glob("*.json"))
                jp = guesses[0].resolve() if guesses else None
            return hp, jp
    fn = _g("_v177_find_canonical_pair")
    if callable(fn):
        try:
            h5, js = fn(_analysis_root(source), base_name=_base_name(_gui_from(source)))
            return (Path(h5).resolve() if h5 else None, Path(js).resolve() if js else None)
        except Exception:
            pass
    root = _analysis_root(source)
    h5s = sorted(root.rglob("*faradaiq_bundle*.h5"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not h5s:
        h5s = sorted(root.rglob("*.h5"), key=lambda p: p.stat().st_mtime, reverse=True)
    h5 = h5s[0].resolve() if h5s else None
    js = None
    if h5:
        candidates = list(h5.parent.glob("*faradaiq_bundle*.json")) + list(h5.parent.glob("*.json"))
        if candidates:
            js = max(candidates, key=lambda p: p.stat().st_mtime).resolve()
    return h5, js


def _selected_browser_files(browser: Any) -> list[Path]:
    if browser is None:
        return []
    out: list[Path] = []
    try:
        fn = getattr(browser, "selected_paths", None)
        if callable(fn):
            out.extend(Path(p).resolve() for p in fn(plottable_only=False) if Path(p).exists())
    except Exception:
        pass
    try:
        for iid in browser.tree.selection():
            p = getattr(browser, "_path_by_iid", {}).get(iid)
            if p and Path(p).exists():
                out.append(Path(p).resolve())
    except Exception:
        pass
    seen: set[str] = set()
    unique: list[Path] = []
    for p in out:
        key = os.path.normcase(str(p))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _iter_files(folder: Path, extensions: set[str], limit: int, max_bytes: int) -> Iterable[Path]:
    skip = {
        ".faradaiq_venv", ".deareis_venv", ".pytest_cache", "__pycache__",
        "FaradaIQ_SciLink_Edison_Jobs", "AI_Bundles", "EdisonScientific_handoff",
    }
    count = 0
    try:
        for p in sorted(folder.rglob("*"), key=lambda x: str(x).lower()):
            if count >= limit:
                return
            try:
                if not p.is_file() or p.suffix.lower() not in extensions:
                    continue
                if any(part in skip for part in p.parts):
                    continue
                if p.stat().st_size > max_bytes:
                    continue
                count += 1
                yield p.resolve()
            except Exception:
                continue
    except Exception:
        return


def collect_share_candidates(source: Any, cfg: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg = dict(load_sharing_settings() if cfg is None else cfg)
    root = _analysis_root(source)
    browser = _browser_from(source)
    max_files = max(10, int(cfg.get("max_files", 400) or 400))
    max_bytes = max(1, int(cfg.get("max_file_mb", 500) or 500)) * 1024 * 1024
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(path: Path | str | None, role: str, selected: bool = True, source_group: str = "") -> None:
        if path is None or len(candidates) >= max_files:
            return
        try:
            p = Path(path).resolve()
            if not p.exists() or not p.is_file():
                return
            size = int(p.stat().st_size)
            if size > max_bytes:
                return
            key = os.path.normcase(str(p))
            if key in seen:
                return
            seen.add(key)
            candidates.append({
                "path": str(p), "role": role, "selected": False,
                "size_bytes": size, "source_group": source_group or role,
            })
        except Exception:
            return

    h5, js = _canonical_pair(source)
    if cfg.get("canonical_h5", True):
        add(h5, "Canonical H5 numeric bundle", True, "Canonical bundle")
    if cfg.get("canonical_json", True):
        add(js, "Canonical JSON manifest", True, "Canonical bundle")
    if cfg.get("selected_files", True):
        for p in _selected_browser_files(browser):
            add(p, "Browser-selected data", True, "Current selection")

    remaining = max_files - len(candidates)
    if cfg.get("analysis_tables", True) and remaining > 0 and root.exists():
        table_ext = {".csv", ".tsv", ".txt", ".asc", ".xlsx", ".xls", ".json", ".npz", ".npy"}
        for p in _iter_files(root, table_ext, min(220, remaining), max_bytes):
            add(p, "Analysis-ready table / metadata", True, "Analysis outputs")
    remaining = max_files - len(candidates)
    if cfg.get("figures", True) and remaining > 0 and root.exists():
        fig_ext = {".png", ".jpg", ".jpeg", ".svg", ".pdf", ".html"}
        for p in _iter_files(root, fig_ext, min(90, remaining), max_bytes):
            add(p, "Figure / visualization", True, "Figures")

    gui = _gui_from(source)
    if cfg.get("raw_source", False):
        try:
            src = Path(str(gui.file_var.get()).strip()).resolve()
            if src.is_file():
                add(src, "Raw source file", True, "Raw source")
            elif src.is_dir():
                raw_ext = {".dat", ".zip", ".pssession", ".psmethod", ".csv", ".txt", ".par", ".psbd"}
                for p in _iter_files(src, raw_ext, min(80, max_files - len(candidates)), max_bytes):
                    add(p, "Raw source file", True, "Raw source")
        except Exception:
            pass
    for enabled_key, folder_key, role, group in (
        ("literature_folder_enabled", "literature_folder", "Local literature", "Literature folder"),
        ("additional_folder_enabled", "additional_folder", "Additional user-selected file", "Additional folder"),
    ):
        if cfg.get(enabled_key) and cfg.get(folder_key):
            folder = Path(str(cfg.get(folder_key))).expanduser()
            ext = {".pdf", ".txt", ".md", ".docx", ".csv", ".xlsx", ".json", ".h5", ".png", ".jpg", ".jpeg"}
            if folder.is_file():
                add(folder, role, True, group)
            elif folder.is_dir():
                for p in _iter_files(folder, ext, min(100, max_files - len(candidates)), max_bytes):
                    add(p, role, True, group)

    # Session metadata is generated only after approval, but it must still be
    # visible and individually controllable in the review checklist.
    if cfg.get("session_metadata", True) and len(candidates) < max_files:
        candidates.append({
            "path": "",
            "role": "FaradaIQ session metadata",
            "selected": False,
            "size_bytes": 0,
            "source_group": "Generated job metadata",
            "generated": "session_metadata",
            "display_path": "(generated after approval inside the new job folder)",
        })
    return candidates


def _get_prompt_text(source: Any) -> str:
    gui = _gui_from(source)
    try:
        text = str(gui.desc_text.get("1.0", "end")).strip()
        if text:
            return text
    except Exception:
        pass
    browser = _browser_from(source)
    for name in ("smart_compare_prompt_text", "query_text", "custom_prompt_text"):
        try:
            widget = getattr(browser, name)
            text = str(widget.get("1.0", "end")).strip()
            if text:
                return text
        except Exception:
            pass
    return (
        "Analyze the selected FaradaIQ electrochemistry data. Identify the techniques and data-quality issues, "
        "perform the scientifically appropriate quantitative analyses, compare relevant groups, state assumptions, "
        "separate observations from interpretations, and recommend the next experiments."
    )


def _active_provider_settings() -> dict[str, Any]:
    selected = str(_g("_FARADAIQ_SELECTED_LLM_PROVIDER", "") or os.environ.get("FARADAIQ_LLM_PROVIDER", "Ollama"))
    candidates: list[dict[str, Any]] = []
    fn = _g("_v40_provider_candidates")
    if callable(fn):
        try:
            candidates = list(fn(include_unavailable=True) or [])
        except Exception:
            candidates = []
    candidate = next((c for c in candidates if str(c.get("name")) == selected), None)
    if candidate is None and candidates:
        candidate = next((c for c in candidates if c.get("available")), candidates[0])
    candidate = dict(candidate or {})
    return {
        "provider": candidate.get("name") or selected or "Ollama",
        "kind": candidate.get("kind") or "openai",
        "model": candidate.get("model") or os.environ.get("FARADAIQ_LLM_MODEL", os.environ.get("OLLAMA_MODEL", "")),
        "base_url": candidate.get("base_url") or os.environ.get("OPENAI_BASE_URL", os.environ.get("OLLAMA_BASE_URL", "")),
        "api_key_present": bool(candidate.get("api_key") or os.environ.get("OPENAI_API_KEY") or os.environ.get("OLLAMA_API_KEY")),
        "aws_profile": os.environ.get("AWS_PROFILE", ""),
        "aws_region": os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "")),
    }


def _service_settings(cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    cfg = dict(load_sharing_settings() if cfg is None else cfg)
    provider = _active_provider_settings()
    edison_key = (
        os.environ.get("EDISON_PLATFORM_API_KEY") or os.environ.get("EDISON_API_KEY") or
        os.environ.get("EDISON_SCIENTIFIC_API_KEY") or ""
    )
    return {
        "llm": provider,
        "scilink_executable_override": cfg.get("scilink_executable", ""),
        # v261 always unlocks the launched SciLink CLI process for generated code.
        "scilink_unsafe_execution_ok": True,
        "scilink_literature_analysis_enabled": bool(cfg.get("scilink_literature_analysis_enabled", True)),
        "labric_mcp_enabled": bool(cfg.get("scilink_labric_cloud_context_enabled", cfg.get("labric_mcp_enabled", False))),
        "scilink_labric_cloud_context_enabled": bool(cfg.get("scilink_labric_cloud_context_enabled", cfg.get("labric_mcp_enabled", False))),
        "labric_mcp_url": cfg.get("labric_mcp_url") or DEFAULT_MCP_URL,
        "labric_api_key_present": bool(os.environ.get("LABRIC_API_KEY") or os.environ.get("MCP_AUTH_TOKEN")),
        "edison_base_url": cfg.get("edison_base_url") or "https://platform.edisonscientific.com/",
        "edison_upload_url": cfg.get("edison_upload_url") or "",
        "edison_api_key_present": bool(edison_key),
    }



def build_smart_suggestion_context(browser: Any, user_prompt: str, max_files: int = 260) -> dict[str, Any]:
    """Build a text-only, derived schema/index for Smart Suggestions.

    No file bytes, table rows, spectra, curves, images, or absolute local paths are
    included.  The configured LLM receives only the typed request and compact
    structural labels needed to propose plot recipes; FaradaIQ resolves those
    recipes locally against the canonical bundle/cache.
    """
    manifest = getattr(browser, "manifest", None)
    manifest = manifest if isinstance(manifest, dict) else {}
    validation = ((manifest.get("data_structure") or {}).get("validation") or {})
    safe_validation: dict[str, Any] = {}
    for key, value in validation.items() if isinstance(validation, dict) else ():
        key_l = str(key).lower()
        # Keep structural/count/status fields; omit raw arrays and long samples.
        if any(token in key_l for token in ("count", "shape", "rows", "columns", "status", "valid", "missing", "warning", "technique")):
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe_validation[str(key)] = value
            elif isinstance(value, (list, tuple)):
                safe_validation[str(key)] = [str(x)[:120] for x in list(value)[:30]]

    labels: list[str] = []
    cache_root = getattr(browser, "cache_root", None)
    try:
        root = Path(cache_root).resolve() if cache_root else None
        if root and root.exists():
            for path in sorted(root.rglob("*.csv"), key=lambda x: x.as_posix().lower())[: max(1, int(max_files))]:
                try:
                    labels.append(path.relative_to(root).as_posix())
                except Exception:
                    labels.append(path.name)
    except Exception:
        pass

    selected_labels: list[str] = []
    try:
        for path in _selected_browser_files(browser)[:60]:
            try:
                root = Path(cache_root).resolve() if cache_root else None
                selected_labels.append(path.relative_to(root).as_posix() if root else path.name)
            except Exception:
                selected_labels.append(path.name)
    except Exception:
        pass

    selected_view: dict[str, Any] | None = None
    try:
        views = list(getattr(browser, "_v69_plot_views", None) or getattr(browser, "plot_views", None) or [])
        listbox = getattr(browser, "plot_view_list", None)
        indexes = list(listbox.curselection()) if listbox is not None else []
        if indexes and 0 <= int(indexes[0]) < len(views):
            view = views[int(indexes[0])] or {}
            view_labels = []
            for raw in list(view.get("paths") or [])[:40]:
                pp = Path(str(raw))
                try:
                    root = Path(cache_root).resolve() if cache_root else None
                    view_labels.append(pp.resolve().relative_to(root).as_posix() if root else pp.name)
                except Exception:
                    view_labels.append(pp.name)
            selected_view = {
                "name": str(view.get("name") or "Selected view")[:160],
                "mode": str(view.get("mode") or "")[:80],
                "relative_labels": view_labels,
            }
    except Exception:
        pass

    points: set[int] = set()
    cycles: set[int] = set()
    scan_rates_mvs: set[float] = set()
    techniques_from_labels: set[str] = set()
    for label in labels:
        low = label.lower()
        for match in re.finditer(r"(?:point|pt|p)[_\- ]*0*(\d{1,5})(?!\d)", low):
            try: points.add(int(match.group(1)))
            except Exception: pass
        for match in re.finditer(r"(?:cycle|cyc|c)[_\- ]*0*(\d{1,4})(?!\d)", low):
            try: cycles.add(int(match.group(1)))
            except Exception: pass
        for match in re.finditer(r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:mv[/_\- ]?s|mvs)", low):
            try: scan_rates_mvs.add(float(match.group(1)))
            except Exception: pass
        for token, label_name in (("cv", "CV / LSV"), ("lsv", "CV / LSV"), ("eis", "EIS"), ("drt", "DRT"), ("tafel", "IV / Tafel"), ("ocp", "OCP"), ("ca", "CA"), ("cp", "CP"), ("dpv", "DPV"), ("swv", "SWV")):
            if re.search(rf"(?:^|[/_.\-]){re.escape(token)}(?:$|[/_.\-])", low):
                techniques_from_labels.add(label_name)

    return {
        "request": str(user_prompt or "").strip(),
        "dataset_label": str(getattr(browser, "base_name", "") or "FaradaIQ dataset")[:160],
        "techniques_detected": manifest.get("techniques_detected") or sorted(techniques_from_labels),
        "technique_counts": manifest.get("technique_counts"),
        "available_points": sorted(points)[:1000],
        "available_cycles": sorted(cycles)[:100],
        "available_scan_rates_mV_s": sorted(scan_rates_mvs)[:100],
        "structural_validation": safe_validation,
        # Compatibility key used by the existing JSON Smart Compare workflow.
        # Values are relative labels only; they are not file attachments or bytes.
        "files_from_h5_browser_cache": labels,
        "relative_cache_labels": labels,
        "selected_relative_labels": selected_labels,
        "selected_plot_view": selected_view,
        "data_policy": (
            "Text-only Smart Suggestions context. No file bytes, table rows, numeric curves, images, "
            "raw source data, canonical H5/JSON contents, or absolute local paths are sent to the LLM. "
            "FaradaIQ resolves returned JSON plot recipes locally."
        ),
        "response_format": {
            "suggestions": [
                {
                    "title": "comparison title",
                    "technique": "detected technique",
                    "plot_mode": "supported plot mode",
                    "selection": {"scan_rate_mVs": [], "cycle": [], "points": []},
                    "reason": "brief rationale",
                }
            ]
        },
    }



_STRUCTURE_MARKER = "FARADAIQ DATA STRUCTURE (TEXT ONLY)"


def _sanitize_structure_value(value: Any, key: str = "", depth: int = 0) -> Any:
    """Keep compact structural metadata while omitting paths and raw data content."""
    if depth > 7:
        return "[nested structure omitted]"
    low = str(key or "").lower()
    if any(token in low for token in (
        "absolute_path", "source_path", "source_file", "output_path", "local_path",
        "raw_values", "raw_rows", "file_bytes", "binary", "payload_bytes",
    )):
        return None
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in list(value.items())[:160]:
            cleaned = _sanitize_structure_value(v, str(k), depth + 1)
            if cleaned is not None:
                out[str(k)] = cleaned
        return out
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_structure_value(v, key, depth + 1) for v in list(value)[:160]]
    if isinstance(value, str):
        text = value.strip()
        if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("/") or text.startswith("\\\\"):
            return "[local path omitted]"
        return text[:600]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:300]


def _browser_tree_schema(browser: Any, max_nodes: int = 450) -> list[dict[str, Any]]:
    tree = getattr(browser, "tree", None)
    if tree is None:
        return []
    count = 0

    def walk(parent: str, depth: int = 0) -> list[dict[str, Any]]:
        nonlocal count
        nodes: list[dict[str, Any]] = []
        if count >= max_nodes or depth > 10:
            return nodes
        try:
            children = list(tree.get_children(parent))
        except Exception:
            return nodes
        for iid in children:
            if count >= max_nodes:
                break
            count += 1
            try:
                item = tree.item(iid) or {}
                values = list(item.get("values") or ())
                node = {
                    "label": str(item.get("text") or "")[:220],
                    "kind": str(values[0])[:120] if values else ("group" if depth else "technique/group"),
                    "child_count": len(tree.get_children(iid)),
                }
                nested = walk(iid, depth + 1)
                if nested:
                    node["children"] = nested
                nodes.append(node)
            except Exception:
                continue
        return nodes

    return walk("")


def build_text_data_structure(source: Any) -> dict[str, Any]:
    """Return the compact, text-only data structure included in every service prompt."""
    browser = _browser_from(source)
    gui = _gui_from(source)
    payload: dict[str, Any] = {
        "schema": "faradaiq-text-data-structure-v239",
        "dataset_label": _base_name(gui),
        "data_policy": (
            "Derived structure only. No file bytes, raw numeric rows, spectra, images, canonical bundle contents, "
            "or absolute local paths are included. Data files remain unselected unless the user explicitly checks them."
        ),
    }
    if browser is not None:
        context = build_smart_suggestion_context(browser, "")
        for key in (
            "techniques_detected", "technique_counts", "available_points", "available_cycles",
            "available_scan_rates_mV_s", "structural_validation", "selected_plot_view",
        ):
            if context.get(key) not in (None, [], {}, ""):
                payload[key] = context.get(key)
        hierarchy = _browser_tree_schema(browser)
        if hierarchy:
            payload["visual_browser_hierarchy"] = hierarchy
        labels = list(context.get("relative_cache_labels") or [])[:260]
        if labels:
            payload["relative_structure_labels"] = labels
    md = getattr(gui, "_v92_structured_metadata", None)
    if isinstance(md, dict):
        selected = {
            "human_metadata": md.get("human_metadata"),
            "inferred_from_parser_and_gui": md.get("inferred_from_parser_and_gui"),
            "recommended_data_structure": md.get("recommended_data_structure"),
            "smart_comparison_suggestions": md.get("smart_comparison_suggestions"),
        }
        cleaned = _sanitize_structure_value(selected)
        if cleaned:
            payload["studio_structure_metadata"] = cleaned
    manifest = getattr(browser, "manifest", None) if browser is not None else None
    if not isinstance(manifest, dict):
        try:
            _h5, manifest_path = _canonical_pair(source)
            if manifest_path and manifest_path.exists():
                loaded = json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
                manifest = loaded if isinstance(loaded, dict) else None
        except Exception:
            manifest = None
    if isinstance(manifest, dict):
        manifest_structure = {
            key: manifest.get(key) for key in (
                "techniques_detected", "technique_counts", "h5_counts", "data_structure",
                "experiment_structure_from_dat", "source_policy",
            ) if key in manifest
        }
        cleaned = _sanitize_structure_value(manifest_structure)
        if cleaned:
            payload["manifest_structure_summary"] = cleaned
    if browser is None:
        try:
            root = _analysis_root(source)
            labels: list[str] = []
            if root.exists() and root.is_dir():
                for path in sorted(root.rglob("*"), key=lambda x: x.as_posix().lower()):
                    if len(labels) >= 260:
                        break
                    if not path.is_file() or path.suffix.lower() not in {".csv", ".tsv", ".txt", ".json", ".h5"}:
                        continue
                    if any(part in {".faradaiq_venv", ".deareis_venv", "FaradaIQ_SciLink_Edison_Jobs"} for part in path.parts):
                        continue
                    labels.append(path.relative_to(root).as_posix())
            if labels:
                payload["relative_analysis_structure_labels"] = labels
        except Exception:
            pass
    return payload


def compose_text_only_review_prompt(source: Any, scientific_prompt: str) -> str:
    text = str(scientific_prompt or "").strip()
    if _STRUCTURE_MARKER in text:
        return text
    structure = json.dumps(build_text_data_structure(source), ensure_ascii=False, indent=2, default=str)
    return f"""{text}

{_STRUCTURE_MARKER}
-------------------------------------------
{structure}

DATA-SHARING DEFAULT
--------------------
No data files are selected. This job sends the editable text prompt and the derived data structure above. A data file is included only when you explicitly check that individual file in the Files to Share tab before starting the job.
""".strip()

def build_llm_guided_prompt_messages(gui: Any) -> list[dict[str, str]]:
    """Create the LLM-guided-prompt request without attaching or reading data files."""
    def widget_text(name: str, limit: int) -> str:
        try:
            value = str(getattr(gui, name).get("1.0", "end")).strip()
            return value[:limit]
        except Exception:
            return ""

    md = getattr(gui, "_v92_structured_metadata", None)
    md = md if isinstance(md, dict) else {}
    human = md.get("human_metadata") if isinstance(md.get("human_metadata"), dict) else {}
    inferred = md.get("inferred_from_parser_and_gui") if isinstance(md.get("inferred_from_parser_and_gui"), dict) else {}
    recommended = md.get("recommended_data_structure") if isinstance(md.get("recommended_data_structure"), dict) else {}
    safe_metadata = {
        "dataset_label": str(md.get("base_name") or _base_name(gui))[:160],
        "human_metadata": human,
        "inferred_inventory": inferred,
        "recommended_data_structure": recommended,
        "smart_comparison_goals": list(md.get("smart_comparison_suggestions") or [])[:40],
        "missing_metadata_warnings": list(((md.get("ai_bundle_priorities") or {}).get("flag_missing_metadata_before_quantitative_claims") or []))[:40],
    }
    # Explicitly remove path-like fields and bound all serialized text.
    safe_metadata.pop("source_file_or_folder", None)
    metadata_text = json.dumps(safe_metadata, ensure_ascii=False, indent=2, default=str)[:50000]
    experiment = widget_text("desc_text", 30000)
    summary = widget_text("summary_text", 16000)
    system = (
        "You are an expert electrochemist and scientific-analysis planner. Write a rigorous downstream "
        "analysis prompt. Do not analyze results now. Do not invent measurements, mechanisms, or citations. "
        "The supplied context is text-only metadata; no raw files or numeric tables are attached."
    )
    user = f"""Create a publication-quality analysis prompt for this FaradaIQ electrochemistry job.

The final prompt must:
1. Reconstruct the experiment hierarchy from the supplied metadata.
2. Specify appropriate technique-specific analyses and quantitative outputs.
3. Define comparisons by condition, point, potential, scan rate, cycle, or replicate when present.
4. State data-quality checks, exclusions, uncertainty, and missing metadata.
5. Separate observations, calculations, interpretations, and speculation.
6. Tell SciLink or Edison Scientific to inspect only the files later approved in the review manifest.
7. Recommend concrete next experiments.

Return only the finished analysis prompt.

TEXT-ONLY DATA POLICY
No files, file bytes, raw numeric rows, images, H5/JSON contents, or absolute local paths are included in this LLM call.

STRUCTURED METADATA
{metadata_text}

EXPERIMENTAL DESCRIPTION
{experiment}

SESSION SUMMARY
{summary}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _session_metadata_payload(source: Any) -> dict[str, Any]:
    gui = _gui_from(source)
    meta: dict[str, Any] = {
        "base_name": _base_name(gui),
        "experimental_description_or_prompt": _get_prompt_text(source),
        "source_path": "",
        "output_path": str(_analysis_root(source)),
    }
    try:
        meta["source_path"] = str(gui.file_var.get()).strip()
    except Exception:
        pass
    try:
        summary = getattr(gui, "current_summary", None)
        if summary is not None:
            meta["current_summary"] = summary
    except Exception:
        pass
    return meta


def _build_analysis_prompt(source: Any, service: str, prompt_text: str, candidates: list[dict[str, Any]]) -> str:
    service_name = "SciLink" if service == "scilink" else "Edison Scientific"
    prompt_text = compose_text_only_review_prompt(source, prompt_text)
    base = _base_name(_gui_from(source))
    roles: dict[str, int] = {}
    for item in candidates:
        roles[item["role"]] = roles.get(item["role"], 0) + 1
    role_lines = "\n".join(f"- {role}: {count}" for role, count in sorted(roles.items())) or "- No data files selected; use prompt and metadata only."
    extra = (
        "SciLink may use the configured LabRIC MCP server and Edison Scientific for literature, novelty, or external context. "
        "Keep local measurements, external evidence, and model inference clearly separated in the audit trail."
        if service == "scilink" else
        "Use only the uploaded checked files and prompt. Clearly separate measured results, literature context, and inference."
    )
    return f"""FaradaIQ Studio {VERSION} — {service_name} analysis request

Dataset / job: {base}

Scientific question and requested analysis
------------------------------------------
{prompt_text.strip()}

Checked data roles
------------------
{role_lines}

Required behavior
-----------------
1. Begin with the editable text prompt and FaradaIQ data structure. Read a data file only when it appears in the explicitly approved files_to_share_manifest.json.
2. Identify techniques, hierarchy, points, scan rates, cycles, potentials, and available metadata before analysis.
3. Choose analyses appropriate to the actual data; do not invent missing measurements, files, values, citations, or mechanisms.
4. Report data-quality problems, exclusions, assumptions, uncertainty, and reproducible analysis steps.
5. Provide key comparisons, figures/tables to create, and concrete next experiments.
6. When files were explicitly selected, preserve their provenance. When no files were selected, clearly state that the analysis is structure-guided and no raw data were inspected.
7. {extra}

The default handoff is text-only: editable prompt plus derived data structure. FaradaIQ does not upload data files directly to the selected LLM endpoint. Files are included only after explicit per-file opt-in.
"""


def _job_root(source: Any) -> Path:
    root = _analysis_root(source)
    if root.is_file():
        root = root.parent
    out = root / "FaradaIQ_SciLink_Edison_Jobs"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _write_job(source: Any, service: str, prompt: str, selected: list[dict[str, Any]], cfg: Mapping[str, Any]) -> dict[str, Any]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_id = f"{service}_{stamp}_{uuid.uuid4().hex[:6]}"
    base = _safe_name(_base_name(_gui_from(source)))
    original_job_folder = f"{base}_{job_id}"
    job_folder_alias = None
    try:
        from faradaiq.v273_short_names import MAX_SUBFOLDER_CHARS, alias_entry as _folder_alias_entry, is_enabled as _short_names_enabled, short_component as _short_component
        generated_job_folder = (
            _short_component(original_job_folder, max_chars=MAX_SUBFOLDER_CHARS, default=f"{service}_job")
            if _short_names_enabled() else original_job_folder
        )
        job_folder_alias = _folder_alias_entry(original_job_folder, generated_job_folder, kind=f"{service}_job_folder", context="job_root")
    except Exception:
        generated_job_folder = original_job_folder
    job_dir = _job_root(source) / generated_job_folder
    job_dir.mkdir(parents=True, exist_ok=False)
    prompt_path = job_dir / "analysis_prompt.md"
    prompt_path.write_text(prompt.rstrip() + "\n", encoding="utf-8")
    settings = _service_settings(cfg)
    settings_path = job_dir / "service_settings.json"
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_items = []
    name_aliases = [job_folder_alias] if job_folder_alias else []
    try:
        from faradaiq.v273_short_names import alias_entry, policy_payload, short_component, write_policy_json
    except Exception:
        alias_entry = None
        policy_payload = lambda **_kwargs: {"enabled": False, "name_aliases": []}
        short_component = lambda value, **_kwargs: str(value)
        write_policy_json = None
    for index, item in enumerate(selected, start=1):
        generated = str(item.get("generated") or "").strip()
        if generated == "session_metadata":
            p = job_dir / "faradaiq_session_metadata.json"
            p.write_text(json.dumps(_session_metadata_payload(source), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        else:
            raw_path = str(item.get("path") or "").strip()
            if not raw_path:
                continue
            p = Path(raw_path).resolve()
        shared_name = f"{len(manifest_items) + 1:04d}_{short_component(p.name, max_chars=44, default='file')}"
        if callable(alias_entry):
            row = alias_entry(p.name, shared_name, kind="edison_shared_file", context="shared_files")
            if row:
                name_aliases.append(row)
        manifest_items.append({
            "index": len(manifest_items) + 1,
            "path": str(p),
            "name": p.name,
            "shared_name": shared_name,
            "original_name": p.name,
            "role": item.get("role", "Data file"),
            "source_group": item.get("source_group", ""),
            "generated": generated or None,
            "size_bytes": int(p.stat().st_size if p.exists() else item.get("size_bytes", 0) or 0),
            "exists": p.exists(),
        })
    manifest = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "application": "FaradaIQ Studio",
        "version": VERSION,
        "job_id": job_id,
        "service": service,
        "analysis_root": str(_analysis_root(source)),
        "job_folder": str(job_dir),
        "prompt_file": str(prompt_path),
        "file_count": len(manifest_items),
        "files": manifest_items,
        "short_name_policy": policy_payload(aliases=name_aliases, scope=f"{service}_handoff"),
        "filename_interpretation": "Use short_name_policy.name_aliases to map shared_name back to original_name.",
        "policy": "Explicit user-reviewed file list. No data files are sent directly to the selected LLM provider.",
    }
    manifest_path = job_dir / "files_to_share_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    short_name_map_path = None
    if callable(write_policy_json):
        try:
            short_name_map_path = write_policy_json(job_dir, name_aliases, filename="short_name_map.json", scope=f"{service}_handoff")
        except Exception:
            short_name_map_path = None

    result = {
        "job_id": job_id, "job_dir": job_dir, "prompt_path": prompt_path,
        "manifest_path": manifest_path, "settings_path": settings_path,
        "manifest": manifest, "settings": settings,
        "short_name_map_path": short_name_map_path,
    }
    if service == "edison":
        zip_name = short_component(f"{base}_{job_id}_for_Edison.zip", max_chars=44, default="edison_request.zip")
        zip_path = job_dir / zip_name
        used: set[str] = set()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # Text-only handoff by default. Local service settings are never uploaded.
            zf.write(prompt_path, "analysis_prompt.md")
            if manifest_items:
                zf.write(manifest_path, "files_to_share_manifest.json")
            if manifest_items and short_name_map_path and Path(short_name_map_path).is_file():
                zf.write(short_name_map_path, "short_name_map.json")
            for idx, item in enumerate(manifest_items, start=1):
                p = Path(item["path"])
                if not p.exists():
                    continue
                role = _safe_name(item.get("role"), "data")
                arc = f"shared_files/{role}/{item.get('shared_name') or f'{idx:04d}_{short_component(p.name, max_chars=44, default="file")}' }"
                base_arc = arc
                counter = 2
                while arc.lower() in used:
                    arc = f"{Path(base_arc).with_suffix('').as_posix()}_{counter}{p.suffix}"
                    counter += 1
                used.add(arc.lower())
                zf.write(p, arc)
        result["zip_path"] = zip_path
    return result


def _write_scilink_launcher(job: Mapping[str, Any], cfg: Mapping[str, Any]) -> Path:
    job_dir = Path(job["job_dir"])
    script = Path(__file__).resolve().parent.parent / "faradaiq_scilink_cli_job.py"
    bat = job_dir / "START_NEW_SCILINK_CLI_SESSION.bat"
    python_exe = Path(sys.executable).resolve()
    lines = [
        "@echo off",
        "setlocal EnableExtensions",
        f'title FaradaIQ SciLink {job["job_id"]}',
        f'set "FARADAIQ_SCILINK_JOB_DIR={job_dir}"',
        f'set "FARADAIQ_SCILINK_PROMPT_FILE={job["prompt_path"]}"',
        f'set "FARADAIQ_SCILINK_MANIFEST={job["manifest_path"]}"',
        f'set "LABRIC_MCP_URL={cfg.get("labric_mcp_url") or DEFAULT_MCP_URL}"',
        f'set "FARADAIQ_LABRIC_MCP_ENABLED={1 if cfg.get("labric_mcp_enabled") else 0}"',
        "echo ============================================================",
        f"echo FaradaIQ Studio {VERSION} - new SciLink analysis session",
        "echo ============================================================",
        f"echo Job folder: {job_dir}",
        f"echo Prompt:     {job['prompt_path']}",
        f"echo Request JSON: {job['manifest_path']}",
        "echo.",
        f'"{python_exe}" "{script}" --job-dir "{job_dir}"',
        "echo.",
        "echo SciLink process returned. This terminal remains open for review or follow-up commands.",
        "echo Job folder: %FARADAIQ_SCILINK_JOB_DIR%",
        "endlocal",
    ]
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return bat


def _launch_scilink(source: Any, job: Mapping[str, Any], cfg: Mapping[str, Any]) -> None:
    bat = _write_scilink_launcher(job, cfg)
    gui = _gui_from(source)
    try:
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            subprocess.Popen(["cmd.exe", "/k", str(bat)], cwd=str(Path(job["job_dir"])), creationflags=flags)
        else:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent.parent / "faradaiq_scilink_cli_job.py"), "--job-dir", str(job["job_dir"])], cwd=str(job["job_dir"]))
        try:
            gui.set_status(f"New SciLink CLI session opened: {job['job_id']}")
        except Exception:
            pass
    except Exception as exc:
        messagebox = _g("messagebox")
        if messagebox:
            messagebox.showerror("SciLink launch failed", f"{exc}\n\nLauncher preserved at:\n{bat}", parent=gui)


def _open_edison_status(source: Any, job: Mapping[str, Any]) -> tuple[Any, Any]:
    tk, ttk = _g("tk"), _g("ttk")
    ScrolledText = _g("ScrolledText")
    gui = _gui_from(source)
    win = tk.Toplevel(gui)
    win.title(f"FaradaIQ {VERSION} — Edison Scientific job")
    win.geometry("1060x760")
    outer = ttk.Frame(win, style="Card.TFrame", padding=(12, 12)); outer.pack(fill="both", expand=True, padx=10, pady=10)
    ttk.Label(outer, text="Edison Scientific analysis job", style="Section.TLabel").pack(anchor="w")
    ttk.Label(outer, text=f"Job folder: {job['job_dir']}", style="Muted.TLabel", wraplength=1000).pack(anchor="w", pady=(2, 6))
    box = ScrolledText(outer, wrap="word", height=32); box.pack(fill="both", expand=True)
    box.insert("1.0", "Preparing the reviewed Edison Scientific job...\n")
    row = ttk.Frame(outer, style="Card.TFrame"); row.pack(fill="x", pady=(8, 0))
    ttk.Button(row, text="Open job folder", command=lambda: os.startfile(str(job["job_dir"])) if hasattr(os, "startfile") else None).pack(side="left")
    ttk.Button(row, text="Close", command=win.destroy).pack(side="right")
    return win, box


def _run_edison(source: Any, job: Mapping[str, Any], cfg: Mapping[str, Any]) -> None:
    gui = _gui_from(source)
    win, box = _open_edison_status(source, job)
    zip_path = Path(job["zip_path"])
    job_dir = Path(job["job_dir"])

    def append(text: Any) -> None:
        try:
            box.insert("end", str(text) + "\n"); box.see("end")
        except Exception:
            pass

    def safe_append(text: Any) -> None:
        try:
            win.after(0, lambda: append(text))
        except Exception:
            pass

    def worker() -> None:
        result: dict[str, Any] = {
            "status": "local_edison_job_created",
            "zip_path": str(zip_path),
            "job_dir": str(job_dir),
            "uploaded": False,
        }
        try:
            safe_append(f"Reviewed ZIP created: {zip_path}")
            api_key = (
                os.environ.get("EDISON_PLATFORM_API_KEY") or os.environ.get("EDISON_API_KEY") or
                os.environ.get("EDISON_SCIENTIFIC_API_KEY") or ""
            )
            upload_url = str(cfg.get("edison_upload_url") or "").strip()
            official = _g("_v58_run_official_edison_analysis")
            multipart = _g("_v37_multipart_upload")
            metadata = {
                "app": "FaradaIQ Studio", "version": VERSION, "job_id": job["job_id"],
                "handoff_mode": "text_only" if not job["manifest"].get("file_count", 0) else "text_plus_explicit_files",
                "file_count": job["manifest"].get("file_count", 0),
            }
            if api_key and callable(official):
                safe_append("Submitting the reviewed ZIP through the configured Edison Scientific client...")
                response = official(zip_path, job_dir, metadata, api_key)
                if isinstance(response, Mapping):
                    result.update(dict(response))
                else:
                    result["response"] = str(response)
                result["uploaded"] = True
                result["status"] = result.get("status") or "completed_official_edison_client"
            elif api_key and upload_url and callable(multipart):
                safe_append("Submitting the reviewed ZIP through the configured Edison upload URL...")
                response = multipart(upload_url, api_key, fields={"metadata": json.dumps(metadata)}, files={"file": zip_path}, timeout=1200)
                result.update({"uploaded": True, "status": "uploaded_custom_url", "response": response})
            else:
                result["status"] = "local_handoff_only_missing_or_unavailable_edison_client"
                result["message"] = "The reviewed Edison ZIP is ready. Install/configure the official edison-client package and EDISON_API_KEY in LLM Settings, then retry. A custom upload URL is only a legacy fallback."
                safe_append(result["message"])
                try:
                    webbrowser.open_new_tab(str(cfg.get("edison_base_url") or "https://platform.edisonscientific.com/"))
                except Exception:
                    pass
        except Exception as exc:
            result.update({"status": "edison_job_failed_local_zip_preserved", "error": str(exc), "traceback": traceback.format_exc()})
        (job_dir / "edison_job_response.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        formatter = _g("_v177_format_edison_response")
        try:
            pretty = formatter(result) if callable(formatter) else json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception:
            pretty = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        (job_dir / "edison_job_response.txt").write_text(pretty, encoding="utf-8")
        def done() -> None:
            try:
                box.delete("1.0", "end"); box.insert("1.0", pretty); box.see("1.0")
                gui.set_status(f"Edison Scientific job finished: {result.get('status')}")
            except Exception:
                pass
        try:
            win.after(0, done)
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True).start()


def show_analysis_review(source: Any, service: str, prompt_override: str | None = None) -> Any:
    tk, ttk = _g("tk"), _g("ttk")
    ScrolledText = _g("ScrolledText")
    filedialog, messagebox = _g("filedialog"), _g("messagebox")
    gui = _gui_from(source)
    cfg = load_sharing_settings()
    candidates = collect_share_candidates(source, cfg)
    scientific_prompt = prompt_override.strip() if prompt_override and prompt_override.strip() else _get_prompt_text(source)
    base_prompt = compose_text_only_review_prompt(source, scientific_prompt)
    service_name = "SciLink" if service == "scilink" else "Edison Scientific"

    win = tk.Toplevel(gui)
    win.title(f"Review {service_name} prompt and files")
    win.geometry("1180x820")
    outer = ttk.Frame(win, style="Card.TFrame", padding=(12, 12)); outer.pack(fill="both", expand=True, padx=10, pady=10)
    ttk.Label(outer, text=f"Review before starting {service_name}", style="Section.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text="Nothing starts until you approve. The default is text-only: editable prompt plus derived data structure. Every data-file checkbox starts unchecked and requires explicit opt-in.",
        style="Muted.TLabel", wraplength=1100,
    ).pack(anchor="w", pady=(2, 8))
    tabs = ttk.Notebook(outer); tabs.pack(fill="both", expand=True)
    prompt_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8); tabs.add(prompt_tab, text="Prompt")
    files_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8); tabs.add(files_tab, text="Files to Share")
    settings_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8); tabs.add(settings_tab, text="Service Settings")

    ttk.Label(
        prompt_tab,
        text="Edit the complete text-only request below. It contains your scientific question and FaradaIQ's derived data structure. No raw data are embedded.",
        style="Muted.TLabel", wraplength=1080,
    ).pack(anchor="w", fill="x", pady=(0, 5))
    prompt_box = ScrolledText(prompt_tab, wrap="word"); prompt_box.pack(fill="both", expand=True)
    prompt_box.insert("1.0", base_prompt)

    summary_var = tk.StringVar(value=f"{len(candidates)} optional file item(s) identified. All are unchecked. Check only files you explicitly want to share for this job.")
    ttk.Label(files_tab, textvariable=summary_var, style="Muted.TLabel", wraplength=1080).pack(anchor="w", fill="x", pady=(0, 6))
    actions = ttk.Frame(files_tab, style="Card.TFrame"); actions.pack(fill="x", pady=(0, 5))
    canvas = tk.Canvas(files_tab, highlightthickness=0)
    scrollbar = ttk.Scrollbar(files_tab, orient="vertical", command=canvas.yview)
    rows = ttk.Frame(canvas, style="Card.TFrame")
    window_id = canvas.create_window((0, 0), window=rows, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True); scrollbar.pack(side="right", fill="y")
    rows.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width))
    file_vars: list[tuple[dict[str, Any], Any]] = []
    for idx, item in enumerate(candidates):
        var = tk.BooleanVar(value=False)
        row = ttk.Frame(rows, style="Card.TFrame"); row.pack(fill="x", pady=1)
        ttk.Checkbutton(row, variable=var).pack(side="left", padx=(0, 4))
        ttk.Label(row, text=item.get("role", "Data"), style="Card.TLabel", width=28).pack(side="left", padx=(0, 5))
        size_text = "generated" if item.get("generated") else _format_size(item.get("size_bytes"))
        path_text = item.get("display_path") or item.get("path", "")
        ttk.Label(row, text=size_text, style="Muted.TLabel", width=10).pack(side="left", padx=(0, 5))
        ttk.Label(row, text=path_text, style="Muted.TLabel", wraplength=720).pack(side="left", fill="x", expand=True)
        file_vars.append((item, var))
    def refresh_summary(*_args: Any) -> None:
        checked = [item for item, var in file_vars if var.get()]
        total_bytes = sum(int(item.get("size_bytes") or 0) for item in checked)
        generated = sum(1 for item in checked if item.get("generated"))
        suffix = f" + {generated} generated metadata item(s)" if generated else ""
        summary_var.set(f"Selected {len(checked)} of {len(file_vars)} item(s), {_format_size(total_bytes)} existing files{suffix}.")
    for _item, var in file_vars:
        try: var.trace_add("write", refresh_summary)
        except Exception: pass
    refresh_summary()
    ttk.Label(
        files_tab,
        text="Job-control files analysis_prompt.md, files_to_share_manifest.json, and service_settings.json are always generated and shown in the other review tabs.",
        style="Muted.TLabel", wraplength=1080,
    ).pack(anchor="w", fill="x", before=canvas)
    def set_all(value: bool) -> None:
        for _item, var in file_vars:
            var.set(value)
        refresh_summary()
    ttk.Button(actions, text="Select all", command=lambda: set_all(True)).pack(side="left", padx=(0, 5))
    ttk.Button(actions, text="Clear all", command=lambda: set_all(False)).pack(side="left", padx=(0, 5))
    ttk.Button(actions, text="Open Data Sharing settings", command=lambda: _g("_v37_show_llm_settings")(gui) if callable(_g("_v37_show_llm_settings")) else None).pack(side="left", padx=(8, 5))

    settings = _service_settings(cfg)
    settings_box = ScrolledText(settings_tab, wrap="word"); settings_box.pack(fill="both", expand=True)
    settings_box.insert("1.0", json.dumps(settings, ensure_ascii=False, indent=2))
    settings_box.configure(state="disabled")

    footer = ttk.Frame(outer, style="Card.TFrame"); footer.pack(fill="x", pady=(8, 0))
    status_var = tk.StringVar(value="Text-only by default. Review the editable prompt; optional files remain unchecked unless you opt in.")
    ttk.Label(footer, textvariable=status_var, style="Muted.TLabel").pack(side="left", fill="x", expand=True)
    def start() -> None:
        selected = [dict(item) for item, var in file_vars if var.get()]
        scientific_prompt = prompt_box.get("1.0", "end").strip()
        if not scientific_prompt:
            messagebox.showwarning("Prompt required", "Enter the analysis question or instructions before starting.", parent=win)
            return
        status_var.set("Creating the reviewed job manifest...")
        try:
            final_prompt = _build_analysis_prompt(source, service, scientific_prompt, selected)
            job = _write_job(source, service, final_prompt, selected, cfg)
            win.destroy()
            if service == "scilink":
                _launch_scilink(source, job, cfg)
            else:
                _run_edison(source, job, cfg)
        except Exception as exc:
            messagebox.showerror(f"Could not start {service_name}", f"{exc}\n\n{traceback.format_exc()}", parent=win)
    ttk.Button(footer, text=f"Start new {service_name} job", style="Accent.TButton", command=start).pack(side="right", padx=(6, 0))
    ttk.Button(footer, text="Cancel", command=win.destroy).pack(side="right")
    return win


def open_consolidated_ai_chat(source: Any) -> Any:
    tk, ttk = _g("tk"), _g("ttk")
    ScrolledText = _g("ScrolledText")
    gui = _gui_from(source)
    win = tk.Toplevel(gui)
    win.title(f"FaradaIQ Studio {VERSION} — SciLink / Edison AI Chat")
    win.geometry("1040x760")
    outer = ttk.Frame(win, style="Card.TFrame", padding=(12, 12)); outer.pack(fill="both", expand=True, padx=10, pady=10)
    ttk.Label(outer, text="AI Chat — route each question through SciLink or Edison Scientific", style="Section.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text="This composer does not send data files directly to an LLM. Each question opens an editable text prompt containing the data structure. Optional file checkboxes start unchecked, and every request creates a separate job/session.",
        style="Muted.TLabel", wraplength=980,
    ).pack(anchor="w", pady=(2, 8))
    history = ScrolledText(outer, wrap="word", height=21); history.pack(fill="both", expand=True)
    history.insert("1.0", "FaradaIQ SciLink / Edison AI Chat\n\nEnter a question below, then choose Ask SciLink or Ask Edison Scientific.\n")
    history.configure(state="disabled")
    ttk.Label(outer, text="Question / analysis instruction", style="Section.TLabel").pack(anchor="w", pady=(8, 2))
    composer = ScrolledText(outer, wrap="word", height=10); composer.pack(fill="x")
    composer.insert("1.0", _get_prompt_text(source))
    row = ttk.Frame(outer, style="Card.TFrame"); row.pack(fill="x", pady=(8, 0))
    def ask(service: str) -> None:
        text = composer.get("1.0", "end").strip()
        show_analysis_review(source, service, prompt_override=text)
    ttk.Button(row, text="Ask SciLink", style="Accent.TButton", command=lambda: ask("scilink")).pack(side="left", padx=(0, 6))
    ttk.Button(row, text="Ask Edison Scientific", command=lambda: ask("edison")).pack(side="left", padx=(0, 6))
    ttk.Button(row, text="LLM settings", command=lambda: _g("_v37_show_llm_settings")(gui) if callable(_g("_v37_show_llm_settings")) else None).pack(side="left", padx=(0, 6))
    ttk.Button(row, text="Clear question", command=lambda: (composer.delete("1.0", "end"), composer.focus_set())).pack(side="left")
    ttk.Button(row, text="Close", command=win.destroy).pack(side="right")
    return win


def build_data_sharing_tab(gui: Any, parent: Any) -> None:
    tk, ttk = _g("tk"), _g("ttk")
    filedialog, messagebox = _g("filedialog"), _g("messagebox")
    cfg = load_sharing_settings()
    ttk.Label(parent, text="Data Sharing — SciLink / Edison only", style="Section.TLabel").pack(anchor="w")
    ttk.Label(
        parent,
        text="Direct file upload to the selected LLM is disabled. These controls only identify files that may be reviewed and shared with SciLink or Edison Scientific.",
        style="Muted.TLabel", wraplength=1000,
    ).pack(anchor="w", fill="x", pady=(2, 8))
    body = ttk.Frame(parent, style="Card.TFrame"); body.pack(fill="both", expand=True)
    left = ttk.LabelFrame(body, text="Default candidate categories", padding=(10, 8)); left.pack(side="left", fill="both", expand=True, padx=(0, 5))
    right = ttk.LabelFrame(body, text="SciLink / Edison service settings", padding=(10, 8)); right.pack(side="left", fill="both", expand=True, padx=(5, 0))
    vars_bool: dict[str, Any] = {}
    labels = [
        ("canonical_h5", "Canonical H5 numeric bundle"),
        ("canonical_json", "Canonical JSON manifest"),
        ("selected_files", "Currently selected Browser files"),
        ("analysis_tables", "Parsed / analysis-ready tables and metadata"),
        ("figures", "Figures, plots, and visualization outputs"),
        ("raw_source", "Raw source files (off by default)"),
        ("session_metadata", "Experimental description and session metadata"),
    ]
    for key, label in labels:
        var = tk.BooleanVar(value=bool(cfg.get(key)))
        vars_bool[key] = var
        ttk.Checkbutton(left, text=label, variable=var).pack(anchor="w", pady=2)
    ttk.Separator(left).pack(fill="x", pady=7)
    lit_enable = tk.BooleanVar(value=bool(cfg.get("literature_folder_enabled"))); vars_bool["literature_folder_enabled"] = lit_enable
    lit_var = tk.StringVar(value=str(cfg.get("literature_folder", "")))
    ttk.Checkbutton(left, text="Include local literature folder", variable=lit_enable).pack(anchor="w")
    lit_row = ttk.Frame(left, style="Card.TFrame"); lit_row.pack(fill="x", pady=(2, 6))
    ttk.Entry(lit_row, textvariable=lit_var).pack(side="left", fill="x", expand=True)
    ttk.Button(lit_row, text="Browse", command=lambda: lit_var.set(filedialog.askdirectory(parent=parent) or lit_var.get())).pack(side="left", padx=(5, 0))
    add_enable = tk.BooleanVar(value=bool(cfg.get("additional_folder_enabled"))); vars_bool["additional_folder_enabled"] = add_enable
    add_var = tk.StringVar(value=str(cfg.get("additional_folder", "")))
    ttk.Checkbutton(left, text="Include another local folder", variable=add_enable).pack(anchor="w")
    add_row = ttk.Frame(left, style="Card.TFrame"); add_row.pack(fill="x", pady=(2, 6))
    ttk.Entry(add_row, textvariable=add_var).pack(side="left", fill="x", expand=True)
    ttk.Button(add_row, text="Browse", command=lambda: add_var.set(filedialog.askdirectory(parent=parent) or add_var.get())).pack(side="left", padx=(5, 0))
    limits = ttk.Frame(left, style="Card.TFrame"); limits.pack(fill="x", pady=(5, 0))
    max_mb = tk.StringVar(value=str(cfg.get("max_file_mb", 500))); max_files = tk.StringVar(value=str(cfg.get("max_files", 400)))
    ttk.Label(limits, text="Max MB/file", style="Muted.TLabel").pack(side="left")
    ttk.Entry(limits, textvariable=max_mb, width=8).pack(side="left", padx=(4, 12))
    ttk.Label(limits, text="Max files", style="Muted.TLabel").pack(side="left")
    ttk.Entry(limits, textvariable=max_files, width=8).pack(side="left", padx=4)

    mcp_enabled = tk.BooleanVar(value=bool(cfg.get("scilink_labric_cloud_context_enabled", cfg.get("labric_mcp_enabled", False))))
    vars_bool["labric_mcp_enabled"] = mcp_enabled
    vars_bool["scilink_labric_cloud_context_enabled"] = mcp_enabled
    # Generated-code execution is always unlocked for SciLink CLI sessions in v261.
    unsafe_execution_ok = tk.BooleanVar(value=True); vars_bool["scilink_unsafe_execution_ok"] = unsafe_execution_ok
    literature_analysis = tk.BooleanVar(value=bool(cfg.get("scilink_literature_analysis_enabled", True))); vars_bool["scilink_literature_analysis_enabled"] = literature_analysis
    wait_for_questions = tk.BooleanVar(value=bool(cfg.get("scilink_wait_for_user_questions", True))); vars_bool["scilink_wait_for_user_questions"] = wait_for_questions
    mcp_url = tk.StringVar(value=str(cfg.get("labric_mcp_url") or DEFAULT_MCP_URL))
    scilink_exe = tk.StringVar(value=str(cfg.get("scilink_executable", "")))
    edison_base = tk.StringVar(value=str(cfg.get("edison_base_url", "https://platform.edisonscientific.com/")))
    edison_upload = tk.StringVar(value=str(cfg.get("edison_upload_url", "")))
    edison_key = tk.StringVar(value=os.environ.get("EDISON_PLATFORM_API_KEY") or os.environ.get("EDISON_API_KEY") or os.environ.get("EDISON_SCIENTIFIC_API_KEY") or "")
    labric_key = tk.StringVar(value=os.environ.get("LABRIC_API_KEY") or os.environ.get("MCP_AUTH_TOKEN") or "")
    ttk.Checkbutton(
        right,
        text="Use LabRIC MCP cloud context for cross-instrument comparison, prior-data analysis, and experiment planning",
        variable=mcp_enabled,
    ).pack(anchor="w", pady=(0, 1))
    ttk.Label(
        right,
        text="When checked, every new SciLink session connects the package-local LabRIC stdio bridge before analysis. SciLink can query authorized prior measurements from other instruments, compare them with the current dataset, and use that evidence when proposing follow-up experiments.",
        style="Muted.TLabel", wraplength=470,
    ).pack(anchor="w", fill="x", pady=(0, 5))
    ttk.Checkbutton(
        right,
        text="Ask SciLink to perform literature analysis and novelty assessment",
        variable=literature_analysis,
    ).pack(anchor="w", pady=(2, 1))
    ttk.Label(
        right,
        text="Default: enabled. Edison/FutureHouse credentials remain available inside SciLink in every launch mode. This checkbox controls whether the current SciLink job is asked to use literature and novelty tools.",
        style="Muted.TLabel", wraplength=470,
    ).pack(anchor="w", fill="x", pady=(0, 5))
    ttk.Checkbutton(
        right,
        text="Pause and wait for my input when SciLink asks a question",
        variable=wait_for_questions,
    ).pack(anchor="w", pady=(2, 1))
    ttk.Label(
        right,
        text="Default: enabled. FaradaIQ launches SciLink in Co-Pilot mode so questions and review gates wait for your answer instead of automatically rerunning the data.",
        style="Muted.TLabel", wraplength=470,
    ).pack(anchor="w", fill="x", pady=(0, 5))
    ttk.Checkbutton(
        right,
        text="SciLink generated-code execution unlocked for every CLI session",
        variable=unsafe_execution_ok, state="disabled",
    ).pack(anchor="w", pady=(2, 1))
    ttk.Label(
        right,
        text="FaradaIQ always sets UNSAFE_EXECUTION_OK=true only in the launched SciLink CLI subprocess. The global Windows and Studio environments are not modified.",
        style="Muted.TLabel", wraplength=470,
    ).pack(anchor="w", fill="x", pady=(0, 5))
    ttk.Label(right, text="LabRIC API key (session only)", style="Muted.TLabel").pack(anchor="w", pady=(4, 1))
    labric_key_entry = ttk.Entry(right, textvariable=labric_key, show="*")
    labric_key_entry.pack(fill="x")
    show_labric_key = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        right, text="Show LabRIC key", variable=show_labric_key,
        command=lambda: labric_key_entry.configure(show="" if show_labric_key.get() else "*"),
    ).pack(anchor="w")
    mcp_status = tk.StringVar(value="Package-local LabRIC MCP bridge: not tested")
    ttk.Label(right, textvariable=mcp_status, style="Muted.TLabel", wraplength=470).pack(anchor="w", fill="x", pady=(2, 2))

    def test_labric_connection() -> None:
        mcp_status.set("Testing package-local LabRIC MCP connection...")
        test_url = mcp_url.get().strip() or DEFAULT_MCP_URL
        test_key = labric_key.get().strip()
        def worker() -> None:
            try:
                from faradaiq import v261_labric_mcp
                result = v261_labric_mcp.test_connection(test_url, test_key)
            except Exception as exc:
                result = {"status": "error", "error": repr(exc)}
            def finish() -> None:
                if result.get("status") == "connected":
                    tools = ", ".join(result.get("tools") or [])
                    mcp_status.set(f"Connected: {result.get('tool_count', 0)} tool(s). {tools}")
                else:
                    mcp_status.set(f"Connection failed: {result.get('error', 'unknown error')}")
            try:
                parent.after(0, finish)
            except Exception:
                finish()
        threading.Thread(target=worker, daemon=True).start()

    ttk.Button(right, text="Test LabRIC MCP connection", command=test_labric_connection).pack(anchor="w", pady=(1, 4))
    for label, var in (
        ("LabRIC MCP URL", mcp_url),
        ("SciLink executable override", scilink_exe),
        ("Edison Scientific platform", edison_base),
        ("Legacy custom upload URL (normally leave blank)", edison_upload),
    ):
        ttk.Label(right, text=label, style="Muted.TLabel").pack(anchor="w", pady=(4, 1))
        ttk.Entry(right, textvariable=var).pack(fill="x")
    ttk.Label(right, text="Edison API key (session only)", style="Muted.TLabel").pack(anchor="w", pady=(4, 1))
    key_entry = ttk.Entry(right, textvariable=edison_key, show="*"); key_entry.pack(fill="x")
    show_key = tk.BooleanVar(value=False)
    ttk.Checkbutton(right, text="Show Edison key", variable=show_key, command=lambda: key_entry.configure(show="" if show_key.get() else "*")).pack(anchor="w")
    ttk.Label(
        right,
        text="Every Ask SciLink click creates a new isolated CLI session. Generated-code execution is unlocked only for that process. Literature analysis, LabRIC MCP cloud context, and wait-for-user behavior are independently reviewable. The package-local LabRIC bridge uses FaradaIQ private Python and does not require C:\\SciLink\\scilink-labric-demo. The separate Ask Edison action still uses the official edison-client package and reviewed file upload.",
        style="Muted.TLabel", wraplength=470,
    ).pack(anchor="w", fill="x", pady=(8, 4))

    status = tk.StringVar(value=f"Settings file: {_settings_path()}")
    row = ttk.Frame(parent, style="Card.TFrame"); row.pack(fill="x", pady=(8, 0))
    ttk.Label(row, textvariable=status, style="Muted.TLabel").pack(side="left", fill="x", expand=True)
    def save() -> None:
        new = dict(cfg)
        for key, var in vars_bool.items():
            new[key] = bool(var.get())
        new["scilink_unsafe_execution_ok"] = True
        new.update({
            "literature_folder": lit_var.get().strip(), "additional_folder": add_var.get().strip(),
            "max_file_mb": int(max_mb.get() or 500), "max_files": int(max_files.get() or 400),
            "labric_mcp_url": mcp_url.get().strip() or DEFAULT_MCP_URL,
            "scilink_executable": scilink_exe.get().strip(),
            "edison_base_url": edison_base.get().strip() or "https://platform.edisonscientific.com/",
            "edison_upload_url": edison_upload.get().strip(),
        })
        path = save_sharing_settings(new)
        os.environ["LABRIC_MCP_URL"] = new["labric_mcp_url"]
        os.environ["MCP_SERVER_URL"] = new["labric_mcp_url"]
        if labric_key.get().strip():
            os.environ["LABRIC_API_KEY"] = labric_key.get().strip()
            os.environ["MCP_AUTH_TOKEN"] = labric_key.get().strip()
        if new["scilink_executable"]:
            os.environ["SCILINK_EXE"] = new["scilink_executable"]
        if new["edison_base_url"]:
            os.environ["EDISON_BASE_URL"] = new["edison_base_url"]
        if new["edison_upload_url"]:
            os.environ["EDISON_UPLOAD_URL"] = new["edison_upload_url"]
        if edison_key.get().strip():
            os.environ["EDISON_PLATFORM_API_KEY"] = edison_key.get().strip()
        status.set(f"Saved session settings: {path}")
        try:
            gui.set_status("SciLink / Edison data-sharing settings applied for this session.")
        except Exception:
            pass
    ttk.Button(row, text="Apply / save sharing settings", style="Accent.TButton", command=save).pack(side="right")


def _walk_widgets(root: Any) -> Iterable[Any]:
    yield root
    try:
        for child in root.winfo_children():
            yield from _walk_widgets(child)
    except Exception:
        return


def _widget_text(widget: Any) -> str:
    try:
        return str(widget.cget("text")).strip()
    except Exception:
        return ""


def _find_ai_root(gui: Any) -> Any | None:
    fn = _g("_v89_find_ai_toolbar_root")
    if callable(fn):
        try:
            found = fn(gui)
            if found is not None:
                return found
        except Exception:
            pass
    for widget in _walk_widgets(gui):
        if _widget_text(widget) == "AI workflow":
            return getattr(widget, "master", None)
    return None


def apply_v239_title(window: Any, base: str = "FaradaIQ Studio v273") -> None:
    try:
        text = re.sub(r"v\d+(?:\.\d+)?", VERSION, str(base or "FaradaIQ Studio v273"))
        if "FaradaIQ Studio" in text and VERSION not in text:
            text = f"FaradaIQ Studio {VERSION}"
        context_fn = _g("_v753_launch_context")
        context = context_fn() if callable(context_fn) else {}
        session_id = str((context or {}).get("session_id") or "").strip()
        if session_id and "session" not in text.lower():
            text = f"{text} — session {session_id}"
        window.title(text)
    except Exception:
        try:
            window.title(f"FaradaIQ Studio {VERSION}")
        except Exception:
            pass


def configure_main_ai_toolbar(gui: Any) -> None:
    ttk = _g("ttk")
    root = _find_ai_root(gui)
    if root is None:
        return
    if getattr(root, "_v239_consolidated_ai_toolbar", False):
        texts = {_widget_text(w) for w in _walk_widgets(root)}
        expected = {"LLM settings", "Generate LLM-guided prompt", "Ask SciLink", "Ask Edison Scientific", "AI Chat"}
        forbidden = {"Build default AI bundle", "Build LLM-guided AI bundle", "Send AI bundle to Edison", "SciLink prompt"}
        if expected.issubset(texts) and not texts.intersection(forbidden):
            return
        root._v239_consolidated_ai_toolbar = False
    for child in list(root.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass
    outer = ttk.Frame(root, style="Card.TFrame"); outer.pack(fill="x", pady=(0, 2))
    line = ttk.Frame(outer, style="Card.TFrame"); line.pack(fill="x", pady=(0, 3))
    ttk.Label(line, text="AI workflow", style="Muted.TLabel").pack(side="left", padx=(0, 8))
    ttk.Button(line, text="LLM settings", command=lambda: _g("_v37_show_llm_settings")(gui)).pack(side="left", padx=(0, 5))
    gen = _g("_v89_generate_llm_prompt") or _g("_v67_generate_ai_prompt_with_llm") or _g("_v45_generate_ai_prompt_with_llm")
    ttk.Button(line, text="Generate LLM-guided prompt", command=lambda: gen(gui) if callable(gen) else None).pack(side="left", padx=(0, 5))
    ttk.Button(line, text="Ask SciLink", style="Accent.TButton", command=lambda: show_analysis_review(gui, "scilink")).pack(side="left", padx=(0, 5))
    ttk.Button(line, text="Ask Edison Scientific", command=lambda: show_analysis_review(gui, "edison")).pack(side="left", padx=(0, 5))
    ttk.Button(line, text="AI Chat", command=lambda: open_consolidated_ai_chat(gui)).pack(side="left", padx=(0, 5))
    ttk.Label(
        outer,
        text="Text-only LLM prompt generation. Data files are reviewed and shared only through SciLink or Edison Scientific.",
        style="Muted.TLabel",
    ).pack(anchor="w")
    root._v239_consolidated_ai_toolbar = True
    root._v89_ai_toolbar_ready = True
    root._v84_ai_toolbar_ready = True
    try:
        gui.set_status("AI workflow consolidated: LLM-guided prompt, Ask SciLink, Ask Edison Scientific, and AI Chat.")
    except Exception:
        pass


def _collapse_or_expand(tree: Any, open_state: bool) -> None:
    def rec(iid: str) -> None:
        try:
            tree.item(iid, open=open_state)
            for child in tree.get_children(iid):
                rec(child)
        except Exception:
            pass
    for iid in tree.get_children(""):
        rec(iid)


def _populate_schema(browser: Any) -> None:
    tree = getattr(browser, "_v239_schema_tree", None)
    details = getattr(browser, "_v239_schema_details", None)
    summary_var = getattr(browser, "_v239_schema_summary_var", None)
    if tree is None:
        return
    tree.delete(*tree.get_children(""))
    browser._v239_schema_path_by_iid = {}
    root_label = _base_name(_gui_from(browser))
    root_iid = tree.insert("", "end", text=root_label, values=("Dataset", "FaradaIQ parsed hierarchy", ""), open=True)

    # Canonical bundle and metadata.
    bundle = tree.insert(root_iid, "end", text="Canonical bundle", values=("Schema", "H5 / JSON", ""), open=True)
    h5, js = _canonical_pair(browser)
    for label, path, role in (("H5 numeric bundle", h5, "Canonical H5"), ("JSON manifest", js, "Canonical JSON")):
        if path:
            iid = tree.insert(bundle, "end", text=label, values=(role, _format_size(path.stat().st_size), str(path)))
            browser._v239_schema_path_by_iid[iid] = path

    # Clone the parsed data tree exactly as displayed by FaradaIQ.
    parsed = tree.insert(root_iid, "end", text="Parsed data hierarchy", values=("Schema", "Techniques / groups / files", ""), open=True)
    source_tree = getattr(browser, "tree", None)
    source_paths = getattr(browser, "_path_by_iid", {})
    counts = {"nodes": 0, "files": 0, "techniques": 0}
    def clone(parent_src: str, parent_dst: str, depth: int = 0) -> None:
        if source_tree is None:
            return
        for iid in source_tree.get_children(parent_src):
            item = source_tree.item(iid)
            values = list(item.get("values") or ())
            kind = str(values[0]) if values else ("Technique / group" if depth == 0 else "Group")
            size = str(values[1]) if len(values) > 1 else ""
            p = source_paths.get(iid)
            path_text = str(p) if p else ""
            new = tree.insert(parent_dst, "end", text=item.get("text", ""), values=(kind, size, path_text), open=bool(item.get("open", depth < 1)))
            counts["nodes"] += 1
            if depth == 0:
                counts["techniques"] += 1
            if p:
                counts["files"] += 1
                browser._v239_schema_path_by_iid[new] = Path(p)
            clone(iid, new, depth + 1)
    clone("", parsed)

    manifest = getattr(browser, "manifest", None)
    if not isinstance(manifest, dict) and js and js.exists():
        try:
            manifest = json.loads(js.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            manifest = {}
    metadata = tree.insert(root_iid, "end", text="Metadata / validation", values=("Metadata", "JSON fields", ""), open=False)
    if isinstance(manifest, dict):
        for key in ("techniques_detected", "technique_counts", "h5_counts", "source_policy", "gui_metadata", "data_structure", "experiment_structure_from_dat"):
            if key in manifest:
                value = manifest.get(key)
                preview = json.dumps(value, ensure_ascii=False, default=str)
                if len(preview) > 220:
                    preview = preview[:217] + "..."
                tree.insert(metadata, "end", text=key, values=("Metadata field", preview, ""))
    outputs = tree.insert(root_iid, "end", text="Analysis-ready outputs", values=("Output schema", str(_analysis_root(browser)), ""), open=False)
    for item in collect_share_candidates(browser, load_sharing_settings())[:150]:
        if item.get("source_group") not in {"Canonical bundle", "Current selection"}:
            p = Path(item["path"])
            iid = tree.insert(outputs, "end", text=p.name, values=(item.get("role", "Output"), _format_size(item.get("size_bytes")), str(p)))
            browser._v239_schema_path_by_iid[iid] = p
    if summary_var is not None:
        summary_var.set(f"Visual schema: {counts['techniques']} top-level groups, {counts['nodes']} hierarchy nodes, {counts['files']} mapped files. Root: {_analysis_root(browser)}")
    if details is not None:
        try:
            details.configure(state="normal"); details.delete("1.0", "end")
            details.insert("1.0", "Select a schema node to view its type, local path, metadata, and child structure.")
            details.configure(state="disabled")
        except Exception:
            pass


def _add_schema_tab(browser: Any) -> None:
    if getattr(browser, "_v239_schema_tab_added", False):
        return
    tk, ttk, ScrolledText = _g("tk"), _g("ttk"), _g("ScrolledText")
    notebook = getattr(browser, "_v226_center_notebook", None) or getattr(browser, "_v217_center_notebook", None) or getattr(browser, "_v216_center_notebook", None)
    if notebook is None:
        return
    tab = ttk.Frame(notebook, style="Card.TFrame", padding=7)
    notebook.add(tab, text="Data Schema / Tree")
    head = ttk.Frame(tab, style="Card.TFrame"); head.pack(fill="x", pady=(0, 5))
    browser._v239_schema_summary_var = tk.StringVar(value="Building visual data schema...")
    ttk.Label(head, textvariable=browser._v239_schema_summary_var, style="Muted.TLabel", wraplength=900).pack(side="left", fill="x", expand=True)
    ttk.Button(head, text="Refresh schema", command=lambda: _populate_schema(browser)).pack(side="right", padx=(5, 0))
    ttk.Button(head, text="Expand", command=lambda: _collapse_or_expand(browser._v239_schema_tree, True)).pack(side="right", padx=(5, 0))
    ttk.Button(head, text="Collapse", command=lambda: _collapse_or_expand(browser._v239_schema_tree, False)).pack(side="right", padx=(5, 0))
    pane = ttk.Panedwindow(tab, orient="vertical"); pane.pack(fill="both", expand=True)
    tree_frame = ttk.Frame(pane, style="Card.TFrame"); pane.add(tree_frame, weight=4)
    details_frame = ttk.Frame(pane, style="Card.TFrame"); pane.add(details_frame, weight=1)
    tree = ttk.Treeview(tree_frame, columns=("kind", "summary", "path"), show="tree headings", selectmode="browse")
    tree.heading("#0", text="Schema node / hierarchy")
    tree.heading("kind", text="Type")
    tree.heading("summary", text="Summary / size")
    tree.heading("path", text="Local path")
    tree.column("#0", width=370, stretch=True); tree.column("kind", width=160, stretch=False)
    tree.column("summary", width=250, stretch=True); tree.column("path", width=460, stretch=True)
    y = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview); x = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
    tree.grid(row=0, column=0, sticky="nsew"); y.grid(row=0, column=1, sticky="ns"); x.grid(row=1, column=0, sticky="ew")
    tree_frame.rowconfigure(0, weight=1); tree_frame.columnconfigure(0, weight=1)
    details = ScrolledText(details_frame, wrap="word", height=8); details.pack(fill="both", expand=True)
    browser._v239_schema_tree = tree; browser._v239_schema_details = details
    def show_details(_event: Any = None) -> None:
        sel = tree.selection()
        if not sel:
            return
        iid = sel[0]; item = tree.item(iid); values = list(item.get("values") or ())
        path = getattr(browser, "_v239_schema_path_by_iid", {}).get(iid)
        lines = [f"Node: {item.get('text', '')}"]
        if values:
            lines.append(f"Type: {values[0]}")
        if len(values) > 1 and values[1]:
            lines.append(f"Summary: {values[1]}")
        if path:
            lines.extend([f"Local path: {path}", f"Exists: {Path(path).exists()}"])
            try:
                lines.append(f"Size: {_format_size(Path(path).stat().st_size)}")
            except Exception:
                pass
        lines.append(f"Children: {len(tree.get_children(iid))}")
        details.configure(state="normal"); details.delete("1.0", "end"); details.insert("1.0", "\n".join(lines)); details.configure(state="disabled")
    tree.bind("<<TreeviewSelect>>", show_details)
    browser._v239_schema_tab_added = True
    _populate_schema(browser)


def _add_browser_handoff_bar(browser: Any) -> None:
    if getattr(browser, "_v239_handoff_bar_added", False):
        return
    ttk = _g("ttk")
    notebook = getattr(browser, "_v226_center_notebook", None) or getattr(browser, "_v217_center_notebook", None) or getattr(browser, "_v216_center_notebook", None)
    if notebook is None:
        return
    parent = notebook.master
    bar = ttk.Frame(parent, style="Card.TFrame")
    try:
        bar.pack(fill="x", pady=(0, 4), before=notebook)
    except Exception:
        bar.pack(fill="x", pady=(0, 4))
    ttk.Label(bar, text="Scientific handoff", style="Muted.TLabel").pack(side="left", padx=(2, 8))
    ttk.Button(bar, text="Ask SciLink", style="Accent.TButton", command=lambda: show_analysis_review(browser, "scilink")).pack(side="left", padx=(0, 5))
    ttk.Button(bar, text="Ask Edison Scientific", command=lambda: show_analysis_review(browser, "edison")).pack(side="left", padx=(0, 5))
    ttk.Label(bar, text="Text prompt + data structure by default; optional files start unchecked.", style="Muted.TLabel").pack(side="left", padx=(8, 0))
    browser._v239_handoff_bar_added = True


def _smart_service_prompt(browser: Any) -> str:
    try:
        text = str(browser.ai_compare_entry.get()).strip()
        if text:
            return text
    except Exception:
        pass
    try:
        indexes = list(browser.suggestion_list.curselection())
        if indexes:
            return str(browser.suggestion_list.get(indexes[0])).strip()
    except Exception:
        pass
    return "Analyze this FaradaIQ dataset using the displayed data structure. Identify appropriate comparisons, quality checks, and next analyses."


def _declutter_browser_llm(browser: Any) -> None:
    """Keep LLM Smart Suggestions, but route custom scientific requests to SciLink/Edison."""
    ttk = _g("ttk")
    relabel = {
        "LLM / Local transactions": "Smart Suggestion transactions",
        "Ask AI for custom comparison suggestions": "Scientific analysis request",
        "AI-suggested comparisons": "LLM-assisted Smart Suggestions",
        "Suggest comparisons": "Smart Suggestions",
        "Use LLM for Ask AI": "Use LLM for Smart Suggestions",
    }
    for widget in list(_walk_widgets(browser)):
        text = _widget_text(widget)
        if text in relabel:
            try:
                widget.configure(text=relabel[text])
            except Exception:
                pass
        elif text in {"Ask AI", "Ask Local"}:
            try:
                if widget.winfo_class() in {"TButton", "Button"}:
                    widget.destroy()
            except Exception:
                pass
        elif (
            "Suggest sends the H5/JSON" in text or "Ask AI = LLM" in text or
            "Ask AI can use LLM" in text or "Ask Local never" in text
        ):
            try:
                widget.configure(
                    text=(
                        "Suggest may use the configured LLM with the typed request and compact derived structure only. "
                        "Ask SciLink and Ask Edison Scientific open an editable prompt containing the data structure; "
                        "all optional file checkboxes start unchecked."
                    )
                )
            except Exception:
                pass

    # Rename the browser tab while retaining Suggest and the audited LLM recipe workflow.
    for widget in list(_walk_widgets(browser)):
        try:
            if widget.winfo_class() not in {"TNotebook", "Notebook"}:
                continue
            for tab_id in widget.tabs():
                if str(widget.tab(tab_id, "text")).strip() == "AI / Compare":
                    widget.tab(tab_id, text="Analysis / Compare")
        except Exception:
            continue

    # Add only the two service buttons for custom requests. Suggest remains the
    # LLM-assisted text-only Smart Suggestion action.
    tab_ai = getattr(getattr(browser, "suggestion_list", None), "master", None)
    if tab_ai is not None and not getattr(browser, "_v239_smart_service_row_added", False):
        row = ttk.Frame(tab_ai, style="Card.TFrame")
        ttk.Button(
            row, text="Ask SciLink", style="Accent.TButton",
            command=lambda: show_analysis_review(browser, "scilink", prompt_override=_smart_service_prompt(browser)),
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(
            row, text="Ask Edison Scientific",
            command=lambda: show_analysis_review(browser, "edison", prompt_override=_smart_service_prompt(browser)),
        ).pack(side="left", fill="x", expand=True)
        try:
            row.pack(fill="x", pady=(3, 4), before=browser.suggestion_list)
        except Exception:
            row.pack(fill="x", pady=(3, 4))
        browser._v239_smart_service_row = row
        browser._v239_smart_service_row_added = True
    try:
        if hasattr(browser, "smart_compare_status_var"):
            browser.smart_compare_status_var.set(
                "Ready. Suggest may use the configured LLM with text-only structure. Custom requests open Ask SciLink or Ask Edison Scientific."
            )
    except Exception:
        pass

def _normalize_class_title_method(cls: Any) -> None:
    if cls is None or getattr(cls, "_v239_title_normalizer_installed", False):
        return

    # Several inherited release patches wrap ``title()`` and normalize every
    # version back to their own release number.  Calling that previous wrapper
    # would therefore turn v239 into v237 again.  Talk directly to Tk's window
    # manager instead, while preserving the normal title() getter/setter API.
    def normalized(self: Any, text: Any = None) -> Any:
        if text is None:
            return self.tk.call("wm", "title", self._w)
        value = re.sub(r"v\d+(?:\.\d+)?", VERSION, str(text))
        self.tk.call("wm", "title", self._w, value)
        return None

    cls.title = normalized
    cls.wm_title = normalized
    cls._v239_title_normalizer_installed = True


def _install_browser_patch() -> None:
    # The unified task-oriented Browser is FaradaIQH5Browser.  Fall back to the
    # older browser class only for legacy builds.
    Browser = _g("FaradaIQH5Browser") or _g("FaradaIQDataBrowser")
    if Browser is None or getattr(Browser, "_v239_workflow_installed", False):
        return
    _normalize_class_title_method(Browser)
    prev_init = Browser.__init__
    prev_refresh = getattr(Browser, "refresh_tree", None)
    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        prev_init(self, *args, **kwargs)
        def apply() -> None:
            try:
                _add_browser_handoff_bar(self); _add_schema_tab(self); _declutter_browser_llm(self)
            except Exception as exc:
                try:
                    self.log(f"v239 SciLink/Edison browser patch warning: {exc}")
                except Exception:
                    pass
        for delay in (100, 350, 900, 1800, 3500):
            try:
                self.after(delay, apply)
            except Exception:
                pass
    Browser.__init__ = init
    if callable(prev_refresh):
        def refresh(self: Any, *args: Any, **kwargs: Any) -> Any:
            result = prev_refresh(self, *args, **kwargs)
            try:
                if getattr(self, "_v239_schema_tab_added", False):
                    self.after(10, lambda: _populate_schema(self))
            except Exception:
                pass
            return result
        Browser.refresh_tree = refresh
    # Do not replace ask_ai_comparisons, suggest_comparisons, or
    # ai_suggest_comparisons.  Their LLM/local Smart Suggestion split remains
    # available; only the context builder is sanitized below.
    Browser._v239_workflow_installed = True


def _install_main_patch() -> None:
    Gui = _g("SessionReaderGUI")
    if Gui is None or getattr(Gui, "_v239_workflow_installed", False):
        return
    _normalize_class_title_method(Gui)
    prev_init = Gui.__init__
    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        prev_init(self, *args, **kwargs)
        apply_v239_title(self, "FaradaIQ Studio v273")
        def refresh_v239(g=self) -> None:
            apply_v239_title(g, 'FaradaIQ Studio v273')
            configure_main_ai_toolbar(g)
        for delay in (50, 250, 700, 1500, 3000, 6000, 10000):
            try:
                self.after(delay, refresh_v239)
            except Exception:
                pass
    Gui.__init__ = init
    Gui._v239_workflow_installed = True


def install(core: dict[str, Any]) -> None:
    global _CORE
    _CORE = core
    core["_v239_load_sharing_settings"] = load_sharing_settings
    core["_v239_save_sharing_settings"] = save_sharing_settings
    core["_v239_collect_share_candidates"] = collect_share_candidates
    core["_v239_build_smart_suggestion_context"] = build_smart_suggestion_context
    core["_v239_build_llm_guided_prompt_messages"] = build_llm_guided_prompt_messages
    core["_v92_generate_ai_prompt_messages"] = build_llm_guided_prompt_messages
    # Existing Smart Compare code resolves these names dynamically from the
    # core module, so overriding both generations enforces the text-only policy.
    core["_v177_browser_context_for_llm"] = build_smart_suggestion_context
    core["_v138_browser_context_for_llm"] = build_smart_suggestion_context
    core["_v239_build_data_sharing_tab"] = build_data_sharing_tab
    core["_v239_show_analysis_review"] = show_analysis_review
    core["_v239_open_ai_chat"] = open_consolidated_ai_chat
    core["_v239_configure_main_ai_toolbar"] = configure_main_ai_toolbar
    core["_v239_apply_title"] = apply_v239_title
    core["_v753_apply_title"] = apply_v239_title
    # Route all legacy chat and toolbar entry points to the consolidated workflow.
    core["_v93_open_ai_chat"] = open_consolidated_ai_chat
    core["_v98_open_ai_chat"] = open_consolidated_ai_chat
    core["_v100_open_ai_chat"] = open_consolidated_ai_chat
    core["_v101_open_ai_chat"] = open_consolidated_ai_chat
    core["_v104_open_ai_chat"] = open_consolidated_ai_chat
    core["_v105_open_ai_chat"] = open_consolidated_ai_chat
    for name in ("_v89_configure_ai_workflow_toolbar", "_v84_configure_ai_workflow_toolbar", "_v66_deduplicate_and_relabel_ai_toolbar", "_v105_configure_ai_workflow_toolbar", "_v105_configure_ai_workflow_toolbar_final"):
        core[name] = configure_main_ai_toolbar
    _install_browser_patch()
    _install_main_patch()
