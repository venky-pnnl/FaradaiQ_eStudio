"""FaradaIQ Studio v290 Windows-safe generated-name policy.

The policy is enabled by default and is intentionally limited to generated
artifacts. Source files are never renamed. Readable prefixes are retained and
meaningful prefixes and suffixes survive any required shortening.
Every export and AI handoff records the original-to-short mapping in JSON.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

VERSION = "v290"
VERSION_NUMBER = 278
SEMVER = "0.290.0"
APP_NAME = "FaradaIQ Studio"
APP_TITLE = f"{APP_NAME} {VERSION}"
INSTALL_TARGET = rf"C:\FaradaIQ\Studio\V{VERSION_NUMBER}"

ENV_KEY = "FARADAIQ_SHORT_NAMES"
DEFAULT_ENABLED = True
MAX_BASE_CHARS = 100
MAX_COMPONENT_CHARS = 120
MAX_SUBFOLDER_CHARS = 100
ALGORITHM = "readable-prefix-and-suffix"

_FALSE = {"0", "false", "no", "off", "disabled"}
_TRUE = {"1", "true", "yes", "on", "enabled"}


def is_enabled(value: Any = None) -> bool:
    """Resolve the short-name state from an explicit value or the process env."""
    if value is not None:
        if isinstance(value, str):
            low = value.strip().lower()
            if low in _FALSE:
                return False
            if low in _TRUE:
                return True
        return bool(value)
    raw = str(os.environ.get(ENV_KEY, "1" if DEFAULT_ENABLED else "0")).strip().lower()
    return raw not in _FALSE


def set_enabled(value: Any) -> bool:
    enabled = is_enabled(value)
    os.environ[ENV_KEY] = "1" if enabled else "0"
    return enabled


def _ascii_slug(value: Any, default: str = "item") -> str:
    from .readable_names import safe_component
    return safe_component(value,max_chars=1000,default=default)


def _digest(value: Any) -> str:
    return hashlib.sha1(str(value).encode("utf-8", errors="replace")).hexdigest()[:8]


def short_component(value: Any, *, max_chars: int = MAX_COMPONENT_CHARS, default: str = "item") -> str:
    from .readable_names import safe_component
    return safe_component(value,max_chars=max_chars if is_enabled() else 240,default=default)


def short_base(value: Any, *, max_chars: int = MAX_BASE_CHARS, default: str = "experiment") -> str:
    return short_component(value, max_chars=max_chars, default=default)


def alias_entry(original: Any, shortened: Any, *, kind: str = "generated_name", context: str = "") -> dict[str, Any] | None:
    original_s = str(original)
    short_s = str(shortened)
    if original_s == short_s:
        return None
    return {
        "original_name": original_s,
        "short_name": short_s,
        "kind": kind,
        "context": context,
        "algorithm": ALGORITHM,
    }


def dedupe_aliases(rows: Iterable[Mapping[str, Any] | None]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in rows:
        if not raw:
            continue
        row = dict(raw)
        key = (str(row.get("original_name", "")), str(row.get("short_name", "")), str(row.get("context", "")))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def policy_payload(*, enabled: Any = None, aliases: Sequence[Mapping[str, Any]] = (), scope: str = "generated_outputs") -> dict[str, Any]:
    state = is_enabled(enabled)
    return {
        "schema": "faradaiq-short-name-policy-v290",
        "version": VERSION,
        "enabled": state,
        "default_enabled": DEFAULT_ENABLED,
        "scope": scope,
        "algorithm": ALGORITHM,
        "max_generated_base_chars": MAX_BASE_CHARS,
        "max_generated_component_chars": MAX_COMPONENT_CHARS,
        "max_generated_subfolder_chars": MAX_SUBFOLDER_CHARS,
        "source_files_renamed": False,
        "scientific_schema_names_renamed": False,
        "canonical_identity_changed": False,
        "original_names_preserved_in_json": True,
        "short_name_semantics": "A short name is a filesystem alias only; it never changes dataset identity, technique, method, point, cycle, or canonical keys.",
        "protected_scientific_components": [
            ".browser_cache_from_h5", "processed", "semantic_sections", "DRT_input_csv",
            "group_NNN_*", "series_NNN_*", "sweep_NNN_*", "point_NNN", "cycle_NN",
            "*_HEKA_vNNN", "*_for_plotting_analysis", "prepared_data", "source_data", "metadata",
        ],
        "applies_to": [
            "compact outer FaradaIQ-generated output folders",
            "safe report, figure, table, workbook, and result names",
            "job-local SciLink/Edison copies with explicit alias mapping",
            "SciLink/Edison job and archive names",
        ],
        "generated_output_name_requirement": {
            "enabled": state,
            "max_component_chars": MAX_COMPONENT_CHARS,
            "instruction": (
                "Keep newly generated report, figure, table, workbook, result, and non-schema subfolder names short. "
                "Do not rename canonical scientific hierarchy components or encode scientific identity only in a filename."
                if state else "No short-name requirement was requested for newly generated outputs."
            ),
        },
        "consumer_instructions": [
            "Use name_aliases to map short_name back to original_name.",
            "A numbered suffix distinguishes folders created with the same label and timestamp.",
            "Use original_name and canonical_key for scientific interpretation; use short_name only for filesystem access.",
            "Never infer technique, method, point, or cycle from a shortened alias when canonical metadata is available.",
        ],
        "note": (
            "Generated names may be shortened to reduce Windows path-length failures. Scientific schema folders and canonical identities are protected."
            if state else "Short generated names were disabled for this operation."
        ),
        "name_aliases": dedupe_aliases(aliases),
    }

_PROTECTED_EXACT = {
    ".browser_cache_from_h5", "processed", "semantic_sections", "DRT_input_csv",
    "prepared_data", "source_data", "metadata", "canonical", "data",
}
_PROTECTED_PATTERNS = (
    re.compile(r"^(?:group|series|sweep|point|cycle|method|line)_\d+(?:_|$)", re.I),
    re.compile(r".*_HEKA_v\d+$", re.I),
    re.compile(r".*_for_plotting_analysis$", re.I),
    re.compile(r"^(?:CV_LSV|CA|CP|OCP|EIS|DRT|PHOTO|OTHER)$", re.I),
)


def _protected_scientific_component(name: str) -> bool:
    value = str(name or "")
    if value in _PROTECTED_EXACT:
        return True
    return any(pattern.match(value) for pattern in _PROTECTED_PATTERNS)


def _inside_protected_branch(path: Path, base: Path) -> bool:
    try:
        rel = path.relative_to(base)
    except Exception:
        return True
    return any(_protected_scientific_component(part) for part in rel.parts)


def shorten_generated_tree(
    root: str | Path,
    *,
    max_component_chars: int = MAX_COMPONENT_CHARS,
    scope: str = "completed_job",
) -> list[dict[str, Any]]:
    """Shorten safe generated descendants without touching scientific schema.

    Canonical Browser caches, HEKA hierarchy folders, prepared/source data, and
    metadata are immutable. This routine is intended for completed report/result
    trees, not for parser output while canonical bundles are being constructed.
    """
    base = Path(root)
    if not is_enabled() or not base.is_dir():
        return []
    aliases: list[dict[str, Any]] = []
    paths = sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    for path in paths:
        try:
            if _inside_protected_branch(path, base):
                continue
            limit = min(max_component_chars, MAX_SUBFOLDER_CHARS) if path.is_dir() else max_component_chars
            old_name = path.name
            from .readable_names import path_component
            new_name = path_component(path.parent,old_name,reserve=32 if path.is_dir() else 0,max_chars=limit)
            if new_name==old_name:continue
            target = path.with_name(new_name)
            serial = 2
            while target.exists() and target != path:
                target = path.with_name(
                    short_component(
                        f"{Path(new_name).stem}_{serial}{Path(new_name).suffix}",
                        max_chars=limit,
                        default=f"item_{serial}",
                    )
                )
                serial += 1
            old_rel = str(path.relative_to(base)).replace("\\", "/")
            path.rename(target)
            new_rel = str(target.relative_to(base)).replace("\\", "/")
            aliases.append({
                "original_name": old_name,
                "short_name": target.name,
                "original_relative_path": old_rel,
                "short_relative_path": new_rel,
                "kind": "generated_folder" if target.is_dir() else "generated_file",
                "context": scope,
                "algorithm": ALGORITHM,
                "scientific_schema": False,
            })
        except Exception:
            continue
    return dedupe_aliases(aliases)

def _replace_alias_strings(value: Any, replacements: Sequence[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        text = value
        for old, new in replacements:
            if old:
                text = text.replace(old, new)
        return text
    if isinstance(value, list):
        return [_replace_alias_strings(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_alias_strings(item, replacements) for key, item in value.items()}
    return value


def update_json_path_references(paths: Iterable[str | Path], aliases: Sequence[Mapping[str, Any]]) -> None:
    replacements: list[tuple[str, str]] = []
    for row in aliases:
        old_rel = str(row.get("original_relative_path", ""))
        new_rel = str(row.get("short_relative_path", ""))
        if old_rel and new_rel:
            replacements.extend([(old_rel, new_rel), (old_rel.replace("/", "\\"), new_rel.replace("/", "\\"))])
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    if not replacements:
        return
    for raw in paths:
        path = Path(raw)
        if not path.is_file() or path.suffix.lower() != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload = _replace_alias_strings(payload, replacements)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            continue


def write_policy_json(folder: str | Path, aliases: Sequence[Mapping[str, Any]] = (), *, enabled: Any = None, filename: str = "short_name_map.json", scope: str = "generated_outputs") -> Path:
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename
    payload = policy_payload(enabled=enabled, aliases=aliases, scope=scope)
    payload["created"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def merge_policy_json(folder: str | Path, aliases: Sequence[Mapping[str, Any]] = (), *, enabled: Any = None, filename: str = "short_name_map.json", scope: str = "generated_outputs") -> Path:
    """Merge new aliases into an existing mapping without losing prior names."""
    root = Path(folder)
    path = root / filename
    existing_aliases: list[Mapping[str, Any]] = []
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            existing_aliases = list(payload.get("name_aliases") or []) if isinstance(payload, dict) else []
        except Exception:
            existing_aliases = []
    return write_policy_json(root, [*existing_aliases, *aliases], enabled=enabled, filename=filename, scope=scope)


def finalize_generated_tree(root: str | Path, *, enabled: Any = None, scope: str = "completed_job") -> dict[str, Any]:
    """Shorten safe completed result descendants and preserve an auditable map."""
    base = Path(root)
    state = is_enabled(enabled)
    aliases = shorten_generated_tree(base, scope=scope) if state else []
    json_paths = [path for path in base.rglob("*.json") if path.name != "short_name_map.json"] if base.is_dir() else []
    update_json_path_references(json_paths, aliases)
    map_path = merge_policy_json(base, aliases, enabled=state, scope=scope)
    receipt = {
        "schema": "faradaiq-short-name-finalization-v290",
        "version": VERSION,
        "enabled": state,
        "scope": scope,
        "renamed_component_count": len(aliases),
        "short_name_map_path": str(map_path),
        "folder_limit_chars": MAX_SUBFOLDER_CHARS,
        "file_limit_chars": MAX_COMPONENT_CHARS,
        "source_files_renamed": False,
        "scientific_schema_names_renamed": False,
        "canonical_identity_changed": False,
        "name_aliases": aliases,
    }
    receipt_path = base / "short_name_postprocess.json"
    try:
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    receipt["receipt_path"] = str(receipt_path)
    return receipt

def _find_widget_by_text(root: Any, text: str) -> Any | None:
    wanted = text.strip().lower()
    stack = [root]
    while stack:
        widget = stack.pop()
        try:
            if str(widget.cget("text")).strip().lower() == wanted:
                return widget
        except Exception:
            pass
        try:
            stack.extend(widget.winfo_children())
        except Exception:
            pass
    return None


def _patch_core_sanitizers(core: MutableMapping[str, Any]) -> None:
    """Do not alter parser sanitizers or scientific identity functions.

    The previous short-name implementation shortened parser-generated hierarchy components globally. That could
    remove markers such as ``_HEKA_v216`` and corrupt Browser grouping. v273
    deliberately leaves all parser and canonical naming functions untouched.
    """
    return None

def _patch_analysis_export(core: MutableMapping[str, Any]) -> None:
    Collection = core.get("PSSessionCollection")
    if Collection is None or not hasattr(Collection, "export_analysis_ready_csv"):
        return
    previous = Collection.export_analysis_ready_csv
    if getattr(previous, "_faradaiq_v273_short", False):
        return

    def export_analysis_ready_csv(self: Any, output_dir: Path, base_name: str) -> dict[str, Any]:
        enabled = is_enabled()
        logical_base = str(base_name)
        physical_base = __import__("faradaiq.readable_names",fromlist=["path_component"]).path_component(output_dir,logical_base,reserve=100,max_chars=100) if enabled else logical_base
        info = previous(self, Path(output_dir), physical_base)
        aliases: list[dict[str, Any]] = []
        row = alias_entry(logical_base, physical_base, kind="physical_output_base", context="analysis_ready_export")
        if row:
            aliases.append(row)

        analysis_root = Path(info["analysis_ready_root"])
        manifest_path = Path(info["manifest_path"])
        map_path = write_policy_json(analysis_root, aliases, enabled=enabled, scope="analysis_ready_export")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["short_name_policy"] = policy_payload(enabled=enabled, aliases=aliases, scope="analysis_ready_export")
            manifest["short_name_map_path"] = str(map_path)
            manifest["original_requested_base_name"] = logical_base
            manifest["physical_generated_base_name"] = physical_base
            manifest["scientific_schema_names_preserved"] = True
            manifest["canonical_identity_base_name"] = logical_base
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        result = dict(info)
        result.update({
            "short_names_enabled": enabled,
            "original_base_name": logical_base,
            "physical_base_name": physical_base,
            "short_name_map_path": map_path,
            "short_name_policy": policy_payload(enabled=enabled, aliases=aliases, scope="analysis_ready_export"),
        })
        return result

    export_analysis_ready_csv._faradaiq_v273_short = True
    Collection.export_analysis_ready_csv = export_analysis_ready_csv

def _patch_batch_jobs(core: MutableMapping[str, Any]) -> None:
    """Add the checkbox while preserving the original scientific base name."""
    modules: list[Any] = []
    for module_name in ("faradaiq.v232_batch_jobs", "faradaiq.v233_batch_jobs"):
        try:
            import importlib
            module = importlib.import_module(module_name)
            if module not in modules:
                modules.append(module)
        except Exception:
            continue

    for jobs in modules:
        old_defaults = jobs.default_export_settings
        if not getattr(old_defaults, "_faradaiq_v273_short", False):
            def defaults(_old=old_defaults) -> dict[str, Any]:
                values = dict(_old())
                values.setdefault("short_names", True)
                return values
            defaults._faradaiq_v273_short = True
            jobs.default_export_settings = defaults

        BatchJob = jobs.BatchJob
        if not getattr(BatchJob.__init__, "_faradaiq_v273_short_defaults", False):
            previous_job_init = BatchJob.__init__
            def job_init(self: Any, *args: Any, _previous=previous_job_init, _jobs=jobs, **kwargs: Any) -> None:
                _previous(self, *args, **kwargs)
                merged = _jobs.default_export_settings()
                merged.update(getattr(self, "export_settings", {}) or {})
                self.export_settings = merged
            job_init._faradaiq_v273_short_defaults = True
            BatchJob.__init__ = job_init

        Model = jobs.BatchQueueModel
        previous_default_output = Model.default_output_for
        if not getattr(previous_default_output, "_faradaiq_v273_short", False):
            def default_output_for(self: Any, source: Path, _previous=previous_default_output, _jobs=jobs) -> Path:
                original_path = _previous(self, source)
                original = _jobs._slug(source.stem if source.is_file() else source.name)
                try:
                    enabled = bool(getattr(self.gui, "short_names_var").get())
                except Exception:
                    enabled = True
                return original_path.parent / (short_base(original) if enabled else original)
            default_output_for._faradaiq_v273_short = True
            Model.default_output_for = default_output_for

        Window = jobs.BatchQueueWindow
        previous_build = Window._build
        if not getattr(previous_build, "_faradaiq_v273_short", False):
            def build(self: Any, _previous=previous_build) -> Any:
                result = _previous(self)
                tk = self.tk
                ttk = self.ttk
                var = tk.BooleanVar(master=self.win, value=True)
                self.short_names_var = var
                self.export_vars["short_names"] = var
                export_box = _find_widget_by_text(self.win, "Export and parser settings")
                if export_box is not None:
                    try:
                        row = max(5, int(export_box.grid_size()[1]))
                    except Exception:
                        row = 5
                    ttk.Checkbutton(
                        export_box,
                        text="Use shortened generated names (recommended for Windows copy/move)",
                        variable=var,
                    ).grid(row=row, column=0, columnspan=2, sticky="w", padx=(0, 12), pady=(5, 1))
                    ttk.Label(
                        export_box,
                        text="Shortens safe physical output paths only. HEKA, method, point, cycle, and canonical names remain unchanged.",
                        style="Muted.TLabel",
                    ).grid(row=row + 1, column=0, columnspan=2, sticky="w", padx=(22, 8), pady=(0, 3))
                return result
            build._faradaiq_v273_short = True
            Window._build = build

        previous_run = jobs.run_batch_job
        if not getattr(previous_run, "_faradaiq_v273_short", False):
            def run_batch_job(job: Any, *args: Any, _previous=previous_run, _jobs=jobs, **kwargs: Any) -> Any:
                old_env = os.environ.get(ENV_KEY)
                enabled = bool((job.export_settings or {}).get("short_names", True))
                set_enabled(enabled)
                try:
                    result = _previous(job, *args, **kwargs)
                    aliases = []
                    original = _jobs._slug(job.experiment_name or job.source.stem or job.source.name)
                    physical = Path(str(job.output_dir)).name
                    item = alias_entry(original, physical, kind="physical_job_folder", context="batch_job")
                    if item:
                        aliases.append(item)
                    policy = policy_payload(enabled=enabled, aliases=aliases, scope="batch_export_job")
                    job.outputs["short_name_policy"] = policy
                    try:
                        out = Path(str(job.outputs.get("output_dir") or job.output_dir))
                        map_path = write_policy_json(out, aliases, enabled=enabled, scope="batch_export_job")
                        job.outputs["short_name_map"] = str(map_path)
                    except Exception:
                        pass
                    return result
                finally:
                    if old_env is None:
                        os.environ.pop(ENV_KEY, None)
                    else:
                        os.environ[ENV_KEY] = old_env
            run_batch_job._faradaiq_v273_short = True
            jobs.run_batch_job = run_batch_job

    try:
        from faradaiq import v233_batch_jobs as active
        for key in ("_v233_default_export_settings", "_v232_default_export_settings", "_v230_default_export_settings", "_v229_default_export_settings"):
            core[key] = active.default_export_settings
        for key in ("_v233_run_batch_job", "_v232_run_batch_job", "_v230_run_batch_job", "_v229_run_batch_job"):
            core[key] = active.run_batch_job
    except Exception:
        pass

def _patch_scilink(core: MutableMapping[str, Any]) -> None:
    # v262 is the active handoff. Patching these helpers keeps both prepared
    # tables and untouched source copies below the configured component limit.
    try:
        from faradaiq import v239_scilink_edison as old
        old_safe = old._safe_name
        if not getattr(old_safe, "_faradaiq_v273_short", False):
            def safe(value: Any, fallback: str = "faradaiq_job") -> str:
                plain = old_safe(value, fallback)
                return short_component(plain, max_chars=MAX_COMPONENT_CHARS, default=fallback) if is_enabled() else plain
            safe._faradaiq_v273_short = True
            old._safe_name = safe

        old_job_root = old._job_root
        if not getattr(old_job_root, "_faradaiq_v273_short", False):
            def job_root(source: Any) -> Path:
                root = old._analysis_root(source)
                if root.is_file():
                    root = root.parent
                name = "FQ_AI_Jobs" if is_enabled() else "FaradaIQ_SciLink_Edison_Jobs"
                out = root / name
                out.mkdir(parents=True, exist_ok=True)
                return out
            job_root._faradaiq_v273_short = True
            old._job_root = job_root
    except Exception:
        pass

    try:
        from faradaiq import v262_technique_scilink as sc
        previous_unique = sc._unique_destination
        if not getattr(previous_unique, "_faradaiq_v273_short", False):
            def unique(folder: Path, index: int, source: Path) -> Path:
                if not is_enabled():
                    return previous_unique(folder, index, source)
                short_name = short_component(source.name, max_chars=MAX_COMPONENT_CHARS, default="data")
                candidate = folder / f"{index:04d}_{short_name}"
                serial = 2
                while candidate.exists():
                    stem, suffix = candidate.stem, candidate.suffix
                    candidate = folder / short_component(f"{stem}_{serial}{suffix}", max_chars=MAX_COMPONENT_CHARS, default=f"data_{serial}")
                    serial += 1
                return candidate
            unique._faradaiq_v273_short = True
            sc._unique_destination = unique
    except Exception:
        pass


def _patch_main_gui(core: MutableMapping[str, Any]) -> None:
    Session = core.get("SessionReaderGUI")
    tk = core.get("tk")
    ttk = core.get("ttk")
    if Session is None or tk is None or ttk is None:
        return

    previous_export = getattr(Session, "export_outputs", None)
    if callable(previous_export) and not getattr(previous_export, "_faradaiq_v273_short", False):
        def export_outputs(self: Any, *args: Any, **kwargs: Any) -> Any:
            result = previous_export(self, *args, **kwargs)
            try:
                enabled = bool(self.short_names_var.get())
                source = Path(str(getattr(self.collection, "source_path", "") or ""))
                logical = source.stem if source.is_file() else source.name or "experiment"
                physical = short_base(logical, max_chars=100) if enabled else logical
                output = Path(str(self.output_dir_var.get() or "."))
                aliases = []
                row = alias_entry(logical, physical, kind="physical_output_base", context="main_export")
                if row:
                    aliases.append(row)
                write_policy_json(output, aliases, enabled=enabled, scope="main_export")
            except Exception:
                pass
            return result
        export_outputs._faradaiq_v273_short = True
        Session.export_outputs = export_outputs

    previous_build = Session._build_ui
    if getattr(previous_build, "_faradaiq_v273_short", False):
        return

    def build_ui(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not hasattr(self, "short_names_var"):
            self.short_names_var = tk.BooleanVar(master=self, value=True)
        set_enabled(self.short_names_var.get())
        result = previous_build(self, *args, **kwargs)

        def apply_state(*_args: Any) -> None:
            enabled = set_enabled(self.short_names_var.get())
            model = getattr(self, "_v232_batch_model", None)
            if model is not None:
                for job in getattr(model, "jobs", []) or []:
                    job.export_settings["short_names"] = enabled
                try:
                    model.autosave_queue()
                except Exception:
                    pass
            try:
                self.set_status(
                    "Safe short paths enabled. Scientific hierarchy and canonical identities remain unchanged."
                    if enabled else "Short generated names disabled for new outputs."
                )
            except Exception:
                pass

        try:
            self.short_names_var.trace_add("write", apply_state)
        except Exception:
            pass

        label = _find_widget_by_text(self, "Output folder")
        top = getattr(label, "master", None) if label is not None else None
        if top is not None:
            try:
                row = max(3, int(top.grid_size()[1]))
            except Exception:
                row = 3
            frame = ttk.Frame(top)
            frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(3, 2))
            ttk.Checkbutton(
                frame,
                text="Use shortened names for generated folders and files",
                variable=self.short_names_var,
            ).pack(side="left")
            ttk.Label(
                frame,
                text="Default ON. Shortens safe physical paths only; HEKA/method/point/cycle names are protected and mapped in JSON.",
                style="Muted.TLabel",
            ).pack(side="left", padx=(10, 0))
        apply_state()
        return result

    build_ui._faradaiq_v273_short = True
    Session._build_ui = build_ui

def install(core: MutableMapping[str, Any]) -> None:
    """Install the v290 short-name policy after every historical feature layer."""
    set_enabled(os.environ.get(ENV_KEY, "1"))
    # Existing title/messagebox wrappers call functions from the previous
    # release-integrity layer. Rebinding that module's public globals makes
    # those already-installed wrappers normalize to v290 instead of an earlier release.
    try:
        from faradaiq import v290_release_integrity as release
        release.VERSION = VERSION
        release.VERSION_NUMBER = VERSION_NUMBER
        release.SEMVER = SEMVER
        release.APP_TITLE = APP_TITLE
        release.INSTALL_TARGET = INSTALL_TARGET
    except Exception:
        pass
    try:
        import importlib
        for module_name in (
            "faradaiq.v232_batch_jobs",
            "faradaiq.v233_batch_jobs",
            "faradaiq.v239_scilink_edison",
            "faradaiq.v261_interactive_scilink",
            "faradaiq.v261_labric_mcp",
            "faradaiq.v262_technique_scilink",
            "faradaiq.ca_cp_analysis_v262",
        ):
            module = importlib.import_module(module_name)
            setattr(module, "VERSION", VERSION)
            if hasattr(module, "APP_VERSION"):
                setattr(module, "APP_VERSION", SEMVER)
            if hasattr(module, "APP_TITLE"):
                setattr(module, "APP_TITLE", APP_TITLE)
    except Exception:
        pass
    core["FARADAIQ_PUBLIC_VERSION"] = VERSION
    core["FARADAIQ_APP_VERSION"] = VERSION
    core["APP_TITLE"] = APP_TITLE
    core["FARADAIQ_RELEASE_VERSION"] = VERSION
    core["FARADAIQ_RELEASE_TITLE"] = APP_TITLE
    core["FARADAIQ_INSTALL_TARGET"] = INSTALL_TARGET
    core["_v273_short_names_enabled"] = is_enabled
    core["_v273_short_component"] = short_component
    core["_v273_short_base"] = short_base
    core["_v273_short_name_policy"] = policy_payload
    core["_v273_write_short_name_map"] = write_policy_json
    core["_v273_shorten_generated_tree"] = shorten_generated_tree
    core["_v273_update_json_path_references"] = update_json_path_references
    core["_v273_merge_short_name_map"] = merge_policy_json
    core["_v273_finalize_generated_tree"] = finalize_generated_tree
    core["_v290_short_names_enabled"] = is_enabled
    core["_v290_short_component"] = short_component
    core["_v290_short_base"] = short_base
    core["_v290_short_name_policy"] = policy_payload
    core["_v290_write_short_name_map"] = write_policy_json
    core["_v290_shorten_generated_tree"] = shorten_generated_tree
    core["_v290_update_json_path_references"] = update_json_path_references
    core["_v290_merge_short_name_map"] = merge_policy_json
    core["_v290_finalize_generated_tree"] = finalize_generated_tree

    collector = core.get("_v177_collect_full_session")
    if callable(collector) and not getattr(collector, "_faradaiq_v273_version", False):
        def collect_full_session(gui: Any) -> Any:
            payload = collector(gui)
            if isinstance(payload, dict):
                payload["version"] = VERSION
                payload["short_name_policy"] = policy_payload(
                    enabled=getattr(getattr(gui, "short_names_var", None), "get", lambda: True)(),
                    scope="main_session",
                )
            return payload
        collect_full_session._faradaiq_v273_version = True
        core["_v177_collect_full_session"] = collect_full_session

    applier = core.get("_v177_apply_full_session")
    if callable(applier) and not getattr(applier, "_faradaiq_v273_short_names", False):
        def apply_full_session(gui: Any, payload: Any, *args: Any, **kwargs: Any) -> Any:
            result = applier(gui, payload, *args, **kwargs)
            try:
                policy = payload.get("short_name_policy", {}) if isinstance(payload, dict) else {}
                if "enabled" in policy and hasattr(gui, "short_names_var"):
                    gui.short_names_var.set(bool(policy.get("enabled")))
                    set_enabled(bool(policy.get("enabled")))
            except Exception:
                pass
            return result
        apply_full_session._faradaiq_v273_short_names = True
        core["_v177_apply_full_session"] = apply_full_session

    _patch_core_sanitizers(core)
    _patch_analysis_export(core)
    _patch_batch_jobs(core)
    _patch_scilink(core)
    _patch_main_gui(core)
