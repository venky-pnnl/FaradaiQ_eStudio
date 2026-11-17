"""FaradaIQ Studio v273 context synchronization and exact SciLink routing.

Production fixes:
- Main Studio description, summary, and data-structure panes refresh whenever a
  job/session/output folder is loaded or a Browser is opened.
- The synchronized panes are persisted as three small text context files plus a
  JSON manifest and exposed as unchecked SciLink/Edison share candidates.
- Ollama model identifiers are passed to SciLink exactly as configured. In
  particular, ``gpt-oss:120b-cloud`` is not prefixed or shortened.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

VERSION = "v273"
_CORE: MutableMapping[str, Any] = {}
_CONTEXT_BEGIN = "FARADAIQ_AUTO_CONTEXT_BEGIN"
_CONTEXT_END = "FARADAIQ_AUTO_CONTEXT_END"
_CONTEXT_NAMES = {
    "description": "faradaiq_experimental_description.txt",
    "summary": "faradaiq_session_summary.txt",
    "structure": "faradaiq_data_structure_preview.txt",
    "manifest": "faradaiq_main_context.json",
}
_SKIP_DIRS = {
    ".faradaiq_venv", ".faradaiq_test_venv", ".deareis_venv", ".pytest_cache",
    "__pycache__", "FaradaIQ_SciLink_Edison_Jobs", "FaradaIQ_launch_sessions",
}


def _g(name: str, default: Any = None) -> Any:
    return _CORE.get(name, default)


def _gui_from(source: Any) -> Any:
    return getattr(source, "gui", source)


def _clean_root(path: Path | str | None) -> Path | None:
    if not path:
        return None
    try:
        p = Path(str(path)).expanduser().resolve()
    except Exception:
        return None
    if p.is_file():
        p = p.parent
    if p.name.lower() == ".browser_cache_from_h5":
        p = p.parent
    return p


def _analysis_root(source: Any, explicit: Path | str | None = None) -> Path | None:
    gui = _gui_from(source)
    choices: list[Any] = [explicit]
    for obj, attr in (
        (source, "analysis_root"), (source, "cache_root"),
        (gui, "current_analysis_root"), (gui, "_faradaiq_analysis_root"),
    ):
        try:
            choices.append(getattr(obj, attr, None))
        except Exception:
            pass
    for var_name in ("output_dir_var", "file_var"):
        try:
            var = getattr(gui, var_name, None)
            choices.append(var.get() if var is not None else None)
        except Exception:
            pass
    try:
        import faradaiq.v239_scilink_edison as old
        choices.insert(1, old._analysis_root(source))
    except Exception:
        pass
    for raw in choices:
        p = _clean_root(raw)
        if p is not None and p.exists():
            # Prefer the sample-specific folder over the generic analysis root.
            return p
    return None


def _latest(root: Path, patterns: Sequence[str]) -> Path | None:
    found: list[Path] = []
    search_roots = [root]
    cache = root / ".browser_cache_from_h5"
    if cache.exists():
        search_roots.append(cache)
    for base in search_roots:
        for pattern in patterns:
            try:
                found.extend(p for p in base.glob(pattern) if p.is_file())
            except Exception:
                pass
    if not found:
        return None
    try:
        return max(found, key=lambda p: p.stat().st_mtime_ns)
    except Exception:
        return found[0]


def _load_json(path: Path | None, max_bytes: int = 24 * 1024 * 1024) -> Any:
    if path is None:
        return None
    try:
        if path.stat().st_size > max_bytes:
            return None
        return json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception:
        return None


def _walk_values(node: Any, wanted: set[str], out: list[Any], depth: int = 0) -> None:
    if depth > 8 or len(out) > 5000:
        return
    if isinstance(node, Mapping):
        for key, value in node.items():
            key_l = str(key).lower()
            if key_l in wanted:
                out.append(value)
            _walk_values(value, wanted, out, depth + 1)
    elif isinstance(node, list):
        for value in node[:5000]:
            _walk_values(value, wanted, out, depth + 1)


def _technique_counts(*payloads: Any) -> dict[str, int]:
    counts: Counter[str] = Counter()
    direct: list[Any] = []
    for payload in payloads:
        _walk_values(payload, {"technique_counts", "aggregate_technique_counts"}, direct)
    for value in direct:
        if isinstance(value, Mapping):
            for key, n in value.items():
                try:
                    counts[str(key).upper()] = max(counts[str(key).upper()], int(n))
                except Exception:
                    pass
    # Catalogs often contain one technique label per record rather than counts.
    labels: list[Any] = []
    for payload in payloads:
        _walk_values(payload, {"technique", "technique_key"}, labels)
    label_counts: Counter[str] = Counter(str(x).upper() for x in labels if isinstance(x, (str, int, float)) and str(x).strip())
    for key, n in label_counts.items():
        if key not in {"TECHNIQUE", "OTHER", "UNKNOWN", "NONE"}:
            counts[key] = max(counts[key], n)
    return dict(sorted(counts.items()))


def _first_number(payloads: Sequence[Any], names: set[str]) -> int | None:
    values: list[Any] = []
    for payload in payloads:
        _walk_values(payload, names, values)
    nums: list[int] = []
    for value in values:
        try:
            if isinstance(value, bool):
                continue
            n = int(value)
            if n >= 0:
                nums.append(n)
        except Exception:
            pass
    return max(nums) if nums else None


def _inventory(root: Path, limit: int = 12000) -> dict[str, Any]:
    files = 0
    dirs = 0
    extensions: Counter[str] = Counter()
    top_groups: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        dirs += len(dirnames)
        current_path = Path(current)
        try:
            rel = current_path.relative_to(root)
            top = rel.parts[0] if rel.parts else "root"
        except Exception:
            top = "root"
        for name in filenames:
            if files >= limit:
                break
            files += 1
            suffix = Path(name).suffix.lower() or "(no extension)"
            extensions[suffix] += 1
            top_groups[top] += 1
            if len(examples.setdefault(top, [])) < 4:
                examples[top].append(name)
        if files >= limit:
            break
    return {
        "file_count": files,
        "directory_count": dirs,
        "truncated": files >= limit,
        "extensions": dict(extensions.most_common(16)),
        "top_groups": dict(top_groups.most_common(24)),
        "examples": examples,
    }


def _strip_auto_block(text: str) -> str:
    raw = str(text or "")
    pattern = re.compile(re.escape(_CONTEXT_BEGIN) + r".*?" + re.escape(_CONTEXT_END), re.S)
    return pattern.sub("", raw).strip()


def _widget_text(gui: Any, attr: str) -> str:
    try:
        return str(getattr(gui, attr).get("1.0", "end-1c"))
    except Exception:
        return ""


def _set_widget(gui: Any, attr: str, text: str) -> None:
    try:
        widget = getattr(gui, attr)
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
    except Exception:
        pass


def _source_path(gui: Any) -> str:
    try:
        return str(gui.file_var.get() or "").strip()
    except Exception:
        return ""


def _format_counts(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{key}: {value}" for key, value in counts.items()) if counts else "Not detected"


def build_context(source: Any, root: Path | str | None = None) -> dict[str, Any]:
    gui = _gui_from(source)
    analysis_root = _analysis_root(source, explicit=root)
    if analysis_root is None:
        raise FileNotFoundError("No loaded FaradaIQ analysis/output folder was found.")

    bundle_json = _latest(analysis_root, ("*_faradaiq_bundle.json", "*canonical*.json"))
    bundle_h5 = _latest(analysis_root, ("*_faradaiq_bundle.h5", "*_faradaiq_bundle.hdf5", "*.h5", "*.hdf5"))
    catalog_json = _latest(analysis_root, ("_faradaiq_browser_catalog_v216.json", "faradaiq_browser_catalog_v216.json", "*browser_catalog*.json"))
    ready_json = _latest(analysis_root, ("_faradaiq_browser_ready_v240.json", "faradaiq_browser_ready_v240.json", "*browser_ready*.json"))
    bundle = _load_json(bundle_json)
    catalog = _load_json(catalog_json)
    ready = _load_json(ready_json)
    current_summary = getattr(gui, "current_summary", None)
    payloads = [x for x in (current_summary, bundle, catalog, ready) if x is not None]
    techniques = _technique_counts(*payloads)
    point_count = _first_number(payloads, {"n_unique_points", "unique_points", "point_count", "n_points", "spatial_points"})
    record_count = _first_number(payloads, {"record_count", "n_records", "n_tables", "table_count", "mapped_files", "n_files"})
    group_count = _first_number(payloads, {"group_count", "n_groups", "top_level_groups"})
    inventory = _inventory(analysis_root)
    if record_count is None:
        record_count = inventory["file_count"]

    source_path = _source_path(gui)
    experiment_name = analysis_root.name or Path(source_path).stem or "FaradaIQ experiment"
    auto_lines = [
        _CONTEXT_BEGIN,
        f"Experiment: {experiment_name}",
        f"Source: {source_path or '(loaded from output folder/session)' }",
        f"Analysis/output folder: {analysis_root}",
        f"Detected techniques: {_format_counts(techniques)}",
        f"Mapped records/files: {record_count}",
    ]
    if point_count is not None:
        auto_lines.append(f"Spatial/acquisition points: {point_count}")
    if group_count is not None:
        auto_lines.append(f"Detected groups: {group_count}")
    if bundle_h5:
        auto_lines.append(f"Canonical H5: {bundle_h5.name}")
    if bundle_json:
        auto_lines.append(f"Canonical JSON: {bundle_json.name}")
    if catalog_json:
        auto_lines.append(f"Browser catalog: {catalog_json.name}")
    auto_lines.extend([
        "The Session summary and Data structure preview below are synchronized from the loaded job/output folder.",
        _CONTEXT_END,
    ])
    user_description = _strip_auto_block(_widget_text(gui, "desc_text"))
    description = "\n\n".join(part for part in (user_description, "\n".join(auto_lines)) if part).strip()

    summary_lines = [
        f"Experiment: {experiment_name}",
        f"Source: {source_path or '(loaded from output folder/session)'}",
        f"Analysis/output folder: {analysis_root}",
        f"Context refreshed: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"Detected techniques: {_format_counts(techniques)}",
        f"Mapped records/files: {record_count}",
        f"Files inventoried: {inventory['file_count']}{' (limit reached)' if inventory['truncated'] else ''}",
        f"Directories inventoried: {inventory['directory_count']}",
    ]
    if point_count is not None:
        summary_lines.append(f"Spatial/acquisition points: {point_count}")
    if group_count is not None:
        summary_lines.append(f"Detected groups: {group_count}")
    summary_lines.extend([
        "",
        "Canonical/browser readiness:",
        f"  H5 numeric bundle: {bundle_h5 or 'not found'}",
        f"  JSON manifest: {bundle_json or 'not found'}",
        f"  Browser catalog: {catalog_json or 'not found'}",
        f"  Browser readiness marker: {ready_json or 'not found'}",
        "",
        "File types: " + (", ".join(f"{k}: {v}" for k, v in inventory["extensions"].items()) or "none"),
    ])
    summary_text = "\n".join(summary_lines)

    tree = [
        experiment_name,
        "├─ Source and session",
        f"│  ├─ Source: {source_path or '(output/session load)'}",
        f"│  └─ Output: {analysis_root}",
        "├─ Canonical bundle",
        f"│  ├─ H5: {bundle_h5.name if bundle_h5 else 'not found'}",
        f"│  └─ JSON: {bundle_json.name if bundle_json else 'not found'}",
        "├─ Parsed techniques",
    ]
    if techniques:
        items = list(techniques.items())
        for idx, (name, count) in enumerate(items):
            branch = "└─" if idx == len(items) - 1 else "├─"
            tree.append(f"│  {branch} {name} ({count})")
    else:
        tree.append("│  └─ No technique labels found")
    tree.extend([
        "├─ Browser/cache",
        f"│  ├─ Catalog: {catalog_json.name if catalog_json else 'not found'}",
        f"│  └─ Ready marker: {ready_json.name if ready_json else 'not found'}",
        "├─ Analysis-ready output groups",
    ])
    groups = list(inventory["top_groups"].items())
    if groups:
        for idx, (name, count) in enumerate(groups):
            branch = "└─" if idx == len(groups) - 1 else "├─"
            tree.append(f"│  {branch} {name} ({count} files)")
    else:
        tree.append("│  └─ No files found")
    tree.extend([
        "└─ Main Studio synchronized context",
        f"   ├─ {_CONTEXT_NAMES['description']}",
        f"   ├─ {_CONTEXT_NAMES['summary']}",
        f"   ├─ {_CONTEXT_NAMES['structure']}",
        f"   └─ {_CONTEXT_NAMES['manifest']}",
    ])
    structure_text = "\n".join(tree)

    return {
        "schema": "faradaiq-main-context-v251",
        "version": VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "experiment_name": experiment_name,
        "source_path": source_path,
        "analysis_root": str(analysis_root),
        "technique_counts": techniques,
        "point_count": point_count,
        "record_count": record_count,
        "group_count": group_count,
        "canonical_h5": str(bundle_h5 or ""),
        "canonical_json": str(bundle_json or ""),
        "browser_catalog": str(catalog_json or ""),
        "browser_ready": str(ready_json or ""),
        "inventory": inventory,
        "description_text": description,
        "summary_text": summary_text,
        "structure_text": structure_text,
    }


def refresh_main_context(source: Any, root: Path | str | None = None, *, update_widgets: bool = True) -> dict[str, Any] | None:
    gui = _gui_from(source)
    try:
        context = build_context(source, root=root)
        analysis_root = Path(context["analysis_root"])
        if update_widgets:
            _set_widget(gui, "desc_text", context["description_text"])
            _set_widget(gui, "summary_text", context["summary_text"])
            _set_widget(gui, "structure_text", context["structure_text"])
        paths = {
            "description": analysis_root / _CONTEXT_NAMES["description"],
            "summary": analysis_root / _CONTEXT_NAMES["summary"],
            "structure": analysis_root / _CONTEXT_NAMES["structure"],
            "manifest": analysis_root / _CONTEXT_NAMES["manifest"],
        }
        paths["description"].write_text(context["description_text"], encoding="utf-8")
        paths["summary"].write_text(context["summary_text"], encoding="utf-8")
        paths["structure"].write_text(context["structure_text"], encoding="utf-8")
        manifest = dict(context)
        manifest["context_files"] = {key: str(path) for key, path in paths.items()}
        paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        gui._v251_context_files = {key: str(path) for key, path in paths.items()}
        gui.current_analysis_root = str(analysis_root)
        try:
            gui.set_status("Loaded context synchronized: description, session summary, and data structure preview updated.")
        except Exception:
            pass
        return manifest
    except Exception as exc:
        try:
            gui.set_status(f"Main context refresh deferred: {exc}")
        except Exception:
            pass
        return None


def _schedule_refresh(gui: Any, root: Path | str | None = None, delay: int = 40) -> None:
    try:
        gui.after(delay, lambda: refresh_main_context(gui, root=root))
    except Exception:
        refresh_main_context(gui, root=root)


def _install_load_hooks() -> None:
    GUI = _g("SessionReaderGUI")
    if GUI is not None and not getattr(GUI, "_v251_context_load_hook", False):
        previous = GUI.load_and_inspect
        def load_and_inspect(self: Any, *args: Any, **kwargs: Any):
            result = previous(self, *args, **kwargs)
            _schedule_refresh(self, delay=60)
            return result
        GUI.load_and_inspect = load_and_inspect
        GUI._v251_context_load_hook = True

    apply_session = _g("_v177_apply_full_session")
    if callable(apply_session) and not getattr(apply_session, "_v251_context_hook", False):
        def wrapped_apply(gui: Any, payload: Any, *args: Any, **kwargs: Any):
            result = apply_session(gui, payload, *args, **kwargs)
            root = None
            try:
                root = payload.get("current_analysis_root") or payload.get("output_dir")
            except Exception:
                pass
            _schedule_refresh(gui, root=root, delay=70)
            return result
        wrapped_apply._v251_context_hook = True  # type: ignore[attr-defined]
        _CORE["_v177_apply_full_session"] = wrapped_apply

    open_browser = _g("_v64_open_data_browser")
    if callable(open_browser) and not getattr(open_browser, "_v251_context_hook", False):
        def wrapped_open(gui: Any, *args: Any, **kwargs: Any):
            try:
                root_fn = _g("_v64_analysis_root_for_browser")
                root = root_fn(gui)[0] if callable(root_fn) else None
            except Exception:
                root = None
            refresh_main_context(gui, root=root)
            return open_browser(gui, *args, **kwargs)
        wrapped_open._v251_context_hook = True  # type: ignore[attr-defined]
        _CORE["_v64_open_data_browser"] = wrapped_open

    open_cache = _g("_v138_open_browser_from_cache")
    if callable(open_cache) and not getattr(open_cache, "_v251_context_hook", False):
        def wrapped_cache(gui: Any, cache_info: Any, base_name: Any = None):
            root = None
            try:
                root = cache_info.get("cache_root") or cache_info.get("h5_path")
            except Exception:
                pass
            refresh_main_context(gui, root=root)
            return open_cache(gui, cache_info, base_name)
        wrapped_cache._v251_context_hook = True  # type: ignore[attr-defined]
        _CORE["_v138_open_browser_from_cache"] = wrapped_cache
        # Older aliases remain active in some code paths.
        _CORE["_v130_open_browser_from_cache"] = wrapped_cache

    Window = _g("BatchQueueWindow")
    if Window is not None and not getattr(Window, "_v251_context_job_hook", False):
        previous_job = getattr(Window, "load_selected_in_main", None)
        if callable(previous_job):
            def load_selected_in_main(self: Any, *args: Any, **kwargs: Any):
                try:
                    job = self.selected_job()
                    root = getattr(job, "output_dir", None)
                except Exception:
                    root = None
                result = previous_job(self, *args, **kwargs)
                _schedule_refresh(self.parent, root=root, delay=90)
                return result
            Window.load_selected_in_main = load_selected_in_main
            Window._v251_context_job_hook = True


def _install_share_candidates() -> None:
    import faradaiq.v239_scilink_edison as old
    previous = old.collect_share_candidates
    if getattr(previous, "_v251_context_candidates", False):
        return

    def collect(source: Any, cfg: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        manifest = refresh_main_context(source)
        items = list(previous(source, cfg))
        gui = _gui_from(source)
        context_files = getattr(gui, "_v251_context_files", {}) or {}
        if manifest and not context_files:
            context_files = manifest.get("context_files") or {}
        additions = [
            ("description", "Main Studio experimental description"),
            ("summary", "Main Studio session summary"),
            ("structure", "Main Studio data structure preview"),
            ("manifest", "Main Studio synchronized context manifest"),
        ]
        by_path = {
            os.path.normcase(str(Path(str(item.get("path"))).resolve())): item
            for item in items if item.get("path")
        }
        context_items: list[dict[str, Any]] = []
        context_norms: set[str] = set()
        for key, role in additions:
            raw = context_files.get(key)
            if not raw:
                continue
            path = Path(str(raw)).resolve()
            if not path.exists() or not path.is_file():
                continue
            norm = os.path.normcase(str(path))
            context_norms.add(norm)
            existing = by_path.get(norm)
            if existing is not None:
                existing["role"] = role
                existing["source_group"] = "Main Studio context"
                existing["selected"] = False
                existing["size_bytes"] = int(path.stat().st_size)
                context_items.append(existing)
            else:
                context_items.append({
                    "path": str(path),
                    "role": role,
                    "selected": False,
                    "size_bytes": int(path.stat().st_size),
                    "source_group": "Main Studio context",
                })
        remaining = [
            item for item in items
            if not item.get("path") or os.path.normcase(str(Path(str(item.get("path"))).resolve())) not in context_norms
        ]
        return context_items + remaining

    collect._v251_context_candidates = True  # type: ignore[attr-defined]
    old.collect_share_candidates = collect


def _normalize_titles() -> None:
    for cls_name in ("SessionReaderGUI", "FaradaIQH5Browser", "FaradaIQDataBrowser"):
        cls = _g(cls_name)
        if cls is None or getattr(cls, "_v251_title_patch", False):
            continue
        old_title = getattr(cls, "title", None)
        if not callable(old_title):
            continue
        def title(self: Any, text: Any = None, _old=old_title):
            if text is None:
                return _old(self)
            return _old(self, re.sub(r"v\d+(?:\.\d+)?", VERSION, str(text)))
        cls.title = title
        cls.wm_title = title
        cls._v251_title_patch = True


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE
    _CORE = core
    import faradaiq.v239_scilink_edison as old
    import faradaiq.v245_queue_scilink as minimal
    import faradaiq.v247_experience as experience
    import faradaiq.v250_reliability as reliability
    for module in (old, minimal, experience, reliability):
        try:
            module.VERSION = VERSION
        except Exception:
            pass
    _install_load_hooks()
    _install_share_candidates()
    _normalize_titles()
    core["_v251_refresh_main_context"] = refresh_main_context
    core["_v251_build_main_context"] = build_context
    core["FARADAIQ_CONTEXT_SHARE_GROUP"] = "Main Studio context"
