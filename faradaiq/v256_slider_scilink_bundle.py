"""FaradaIQ Studio v273 manual frame control and SciLink-ready Browser bundles.

Production fixes:
- The hyperspectral range control can be dragged manually without Plotly redraws
  interrupting the pointer gesture. The selected frame is rendered on release,
  with previous/next buttons and keyboard navigation as fallbacks.
- Ask SciLink from the Browser creates one compact, job-local bundle from the
  current Browser selection. The generated bundle is the only item selected by
  default; all unrelated candidates remain unchecked.
- Every selected file is copied into the new SciLink job so the CLI works from a
  self-contained data directory instead of being given external Windows paths.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import traceback
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

VERSION = "v273"
_CORE: MutableMapping[str, Any] = {}
_PREVIOUS_REVIEW: Any = None

_DATA_EXTENSIONS = {
    ".csv", ".tsv", ".txt", ".asc", ".dat", ".npy", ".npz", ".xlsx", ".xls",
}
_METADATA_EXTENSIONS = {".json", ".yaml", ".yml", ".xml"}
_SUPPORT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".pdf", ".html", ".md", ".docx"}


def _g(name: str, default: Any = None) -> Any:
    return _CORE.get(name, default)


def _gui_from(source: Any) -> Any:
    return getattr(source, "gui", source)


def _browser_from(source: Any) -> Any | None:
    try:
        import faradaiq.v239_scilink_edison as old
        return old._browser_from(source)
    except Exception:
        return source if hasattr(source, "tree") else None


def _safe_path_key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except Exception:
        return os.path.normcase(str(path))


def _browser_selection_paths(source: Any) -> list[Path]:
    """Resolve the current Browser selection only when Ask SciLink is clicked."""
    browser = _browser_from(source)
    if browser is None:
        return []
    paths: list[Path] = []
    try:
        selected = getattr(browser, "selected_paths", None)
        if callable(selected):
            paths.extend(Path(p).expanduser().resolve() for p in selected(plottable_only=False))
    except Exception:
        pass
    try:
        raw = getattr(browser, "selected_path", None)
        if raw:
            paths.append(Path(raw).expanduser().resolve())
    except Exception:
        pass
    try:
        tree = getattr(browser, "tree", None)
        path_by_iid = getattr(browser, "_path_by_iid", {}) or {}
        if tree is not None:
            for iid in list(tree.selection() or []):
                raw = path_by_iid.get(iid)
                if raw:
                    paths.append(Path(raw).expanduser().resolve())
    except Exception:
        pass
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            if not path.is_file():
                continue
            key = _safe_path_key(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        except Exception:
            continue
    return unique


def _selected_record_names(source: Any) -> list[str]:
    try:
        import faradaiq.v247_experience as v247
        return list(v247._selected_record_names(source))
    except Exception:
        return []


def _bundle_candidate(source: Any) -> dict[str, Any] | None:
    paths = _browser_selection_paths(source)
    if not paths:
        return None
    total = 0
    for path in paths:
        try:
            total += int(path.stat().st_size)
        except Exception:
            pass
    return {
        "path": "",
        "role": "SciLink-ready current Browser selection",
        "source_group": "Current Browser selection bundle",
        "generated": "browser_selection_bundle",
        "selected": True,
        "size_bytes": total,
        "bundle_source_paths": [str(p) for p in paths],
        "display_path": (
            f"Automatically create a compact job-local data bundle from {len(paths)} currently selected file(s). "
            "This bundle is selected by default and will be placed in the new SciLink job's data folder."
        ),
    }


def _review_candidates(source: Any, candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Put the generated Browser bundle first and leave every other item unchecked."""
    bundle = _bundle_candidate(source)
    out: list[dict[str, Any]] = []
    if bundle is not None:
        out.append(bundle)
    for raw in candidates:
        item = dict(raw)
        if bundle is not None and str(item.get("source_group") or "").strip().lower() == "current selection":
            # The generated bundle replaces redundant individual current-selection rows.
            continue
        item["selected"] = False
        out.append(item)
    return out


def _candidate_role(item: Mapping[str, Any]) -> str:
    return str(item.get("role") or "Data file").strip() or "Data file"


def _candidate_group(item: Mapping[str, Any]) -> str:
    return str(item.get("source_group") or item.get("group") or "Other").strip() or "Other"


def _candidate_extension(item: Mapping[str, Any]) -> str:
    if str(item.get("generated") or "") == "browser_selection_bundle":
        return "(generated bundle)"
    raw = str(item.get("path") or item.get("display_path") or "")
    return Path(raw).suffix.lower() or "(generated)"


def _unique_destination(folder: Path, index: int, source: Path) -> Path:
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source.stem).strip("._-")[:90] or "data"
    suffix = source.suffix.lower()
    candidate = folder / f"{index:04d}_{safe_stem}{suffix}"
    serial = 2
    while candidate.exists():
        candidate = folder / f"{index:04d}_{safe_stem}_{serial}{suffix}"
        serial += 1
    return candidate


def _copy_file(source: Path, destination_folder: Path, index: int) -> Path:
    destination_folder.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination(destination_folder, index, source)
    shutil.copy2(source, destination)
    return destination.resolve()


def _classify_item(item: Mapping[str, Any], path: Path) -> str:
    role = _candidate_role(item).lower()
    group = _candidate_group(item).lower()
    suffix = path.suffix.lower()
    if suffix in _METADATA_EXTENSIONS or "metadata" in role or "manifest" in role or "metadata" in group:
        return "metadata"
    if suffix in _SUPPORT_EXTENSIONS or any(token in role or token in group for token in ("literature", "figure", "visualization", "support", "reference", "additional")):
        return "support"
    return "data"


def _infer_techniques(paths: Sequence[Path], names: Sequence[str]) -> list[str]:
    text = " ".join([*(p.name for p in paths), *names]).upper()
    techniques = []
    for key in ("CV", "EIS", "DRT", "CA", "CP", "OCP", "LSV", "DPV", "SWV", "GEIS"):
        if re.search(rf"(?:^|[^A-Z]){re.escape(key)}(?:[^A-Z]|$)", text):
            techniques.append(key)
    return techniques


def _write_selection_metadata(
    metadata_dir: Path,
    source_paths: Sequence[Path],
    copied_paths: Sequence[Path],
    record_names: Sequence[str],
) -> Path:
    metadata_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for source, copied in zip(source_paths, copied_paths):
        try:
            size = int(copied.stat().st_size)
        except Exception:
            size = 0
        entries.append({
            "source_name": source.name,
            "source_path": str(source),
            "bundled_name": copied.name,
            "bundled_path": str(copied),
            "suffix": copied.suffix.lower(),
            "size_bytes": size,
        })
    payload = {
        "schema": "faradaiq-scilink-browser-selection-v256",
        "application": "FaradaIQ Studio",
        "version": VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "selection_scope": "current Browser selection only",
        "selected_record_names": list(record_names),
        "detected_techniques": _infer_techniques(source_paths, record_names),
        "file_count": len(entries),
        "files": entries,
        "note": "Compact metadata index. Large canonical manifests are intentionally not auto-selected.",
    }
    path = metadata_dir / "faradaiq_selection_metadata.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.resolve()


def _write_bundle_zip(job_dir: Path, data_paths: Sequence[Path], metadata_paths: Sequence[Path], support_paths: Sequence[Path]) -> Path:
    zip_path = job_dir / "scilink_ready_browser_selection.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in data_paths:
            zf.write(path, f"data/{path.name}")
        for path in metadata_paths:
            zf.write(path, f"metadata/{path.name}")
        for path in support_paths:
            zf.write(path, f"supporting/{path.name}")
    return zip_path.resolve()


def _materialize_selected_files(
    source: Any,
    selected: Sequence[Mapping[str, Any]],
    job_dir: Path,
) -> tuple[list[str], list[str], list[str], Path | None]:
    """Copy reviewed files into the new job and create compact bundle metadata."""
    import faradaiq.v239_scilink_edison as old

    data_dir = job_dir / "data"
    metadata_dir = job_dir / "metadata"
    support_dir = job_dir / "supporting"
    data_paths: list[Path] = []
    metadata_paths: list[Path] = []
    support_paths: list[Path] = []
    copied_sources: list[Path] = []
    copied_bundle_data: list[Path] = []
    counters = {"data": 0, "metadata": 0, "support": 0}

    def add_existing(path: Path, kind: str) -> Path:
        counters[kind] += 1
        folder = data_dir if kind == "data" else metadata_dir if kind == "metadata" else support_dir
        copied = _copy_file(path, folder, counters[kind])
        bucket = data_paths if kind == "data" else metadata_paths if kind == "metadata" else support_paths
        if copied not in bucket:
            bucket.append(copied)
        return copied

    for item in selected:
        generated = str(item.get("generated") or "").strip().lower()
        if generated == "browser_selection_bundle":
            for raw in list(item.get("bundle_source_paths") or []):
                path = Path(str(raw)).expanduser().resolve()
                if not path.is_file():
                    continue
                kind = "data" if path.suffix.lower() in _DATA_EXTENSIONS else _classify_item(item, path)
                copied = add_existing(path, kind)
                if kind == "data":
                    copied_sources.append(path)
                    copied_bundle_data.append(copied)
            continue
        if generated == "session_metadata":
            metadata_dir.mkdir(parents=True, exist_ok=True)
            path = metadata_dir / "faradaiq_session_metadata.json"
            path.write_text(
                json.dumps(old._session_metadata_payload(source), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            metadata_paths.append(path.resolve())
            continue
        raw = str(item.get("path") or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            continue
        add_existing(path, _classify_item(item, path))

    record_names = _selected_record_names(source)
    if copied_bundle_data:
        metadata_paths.insert(0, _write_selection_metadata(metadata_dir, copied_sources, copied_bundle_data, record_names))

    # A simple CSV index is easy for SciLink to inspect without LLM-normalizing a huge manifest.
    if data_paths:
        metadata_dir.mkdir(parents=True, exist_ok=True)
        index_path = metadata_dir / "faradaiq_data_index.csv"
        with index_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["index", "filename", "relative_path", "size_bytes", "suffix"])
            for index, path in enumerate(data_paths, start=1):
                writer.writerow([index, path.name, str(path.relative_to(job_dir)), path.stat().st_size, path.suffix.lower()])
        metadata_paths.append(index_path.resolve())

    bundle_zip = _write_bundle_zip(job_dir, data_paths, metadata_paths, support_paths) if (data_paths or metadata_paths or support_paths) else None
    return (
        [str(path) for path in data_paths],
        [str(path) for path in support_paths],
        [str(path) for path in metadata_paths],
        bundle_zip,
    )


def write_scilink_job(
    source: Any,
    exact_prompt: str,
    selected: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
    *,
    mode: str = "autonomous",
    requested_filenames: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Create one self-contained SciLink job while preserving the exact prompt."""
    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_id = f"scilink_{stamp}_{uuid.uuid4().hex[:6]}"
    base = old._safe_name(old._base_name(_gui_from(source)))
    job_dir = old._job_root(source) / f"{base}_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=False)

    prompt_path = job_dir / "analysis_prompt.md"
    prompt_path.write_text(str(exact_prompt), encoding="utf-8")

    data_paths, supporting_paths, metadata_paths, bundle_zip = _materialize_selected_files(source, selected, job_dir)
    request = {
        "selected_data_paths": data_paths,
        "supporting_file_paths": supporting_paths,
        "metadata_paths": metadata_paths,
        "output_folder": str(job_dir.resolve()),
        "selected_record_names": _selected_record_names(source),
        "requested_filenames": [str(value).strip() for value in (requested_filenames or []) if str(value).strip()],
    }
    request_path = job_dir / "scilink_request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")

    settings = dict(cleanup._service_settings(cfg))
    settings.update({
        "scilink_mode": "autonomous" if str(mode).strip().lower().startswith("auto") else "co-pilot",
        "inject_faradaiq_skill_prompt": False,
        "handoff_contract": "exact_user_prompt_plus_job_local_request_json",
        "job_local_data_bundle": bool(data_paths),
    })
    settings_path = job_dir / "service_settings.json"
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

    bundle_summary = {
        "schema": "faradaiq-scilink-ready-bundle-v256",
        "version": VERSION,
        "job_id": job_id,
        "data_file_count": len(data_paths),
        "metadata_file_count": len(metadata_paths),
        "supporting_file_count": len(supporting_paths),
        "bundle_zip": str(bundle_zip) if bundle_zip else "",
        "working_data_folder": str((job_dir / "data").resolve()) if data_paths else "",
        "all_handoff_paths_are_job_local": all(str(job_dir.resolve()).lower() in str(path).lower() for path in [*data_paths, *metadata_paths, *supporting_paths]),
    }
    summary_path = job_dir / "scilink_bundle_summary.json"
    summary_path.write_text(json.dumps(bundle_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "job_id": job_id,
        "job_dir": job_dir,
        "prompt_path": prompt_path,
        "manifest_path": request_path,
        "request_path": request_path,
        "settings_path": settings_path,
        "manifest": request,
        "settings": settings,
        "bundle_zip": bundle_zip,
        "bundle_summary_path": summary_path,
    }


def _preview_request(source: Any, selected: Sequence[Mapping[str, Any]], requested: Sequence[str]) -> dict[str, Any]:
    data: list[str] = []
    support: list[str] = []
    metadata: list[str] = []
    for item in selected:
        generated = str(item.get("generated") or "").strip().lower()
        if generated == "browser_selection_bundle":
            for index, raw in enumerate(list(item.get("bundle_source_paths") or []), start=1):
                path = Path(str(raw))
                safe = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._-")[:90] or "data"
                data.append(f"<new SciLink job>/data/{index:04d}_{safe}{path.suffix.lower()}")
            metadata.extend([
                "<new SciLink job>/metadata/faradaiq_selection_metadata.json",
                "<new SciLink job>/metadata/faradaiq_data_index.csv",
            ])
            continue
        if generated == "session_metadata":
            metadata.append("<new SciLink job>/metadata/faradaiq_session_metadata.json")
            continue
        raw = str(item.get("path") or "").strip()
        if not raw:
            continue
        path = Path(raw)
        kind = _classify_item(item, path)
        target = f"<new SciLink job>/{'data' if kind == 'data' else 'metadata' if kind == 'metadata' else 'supporting'}/{path.name}"
        (data if kind == "data" else metadata if kind == "metadata" else support).append(target)
    return {
        "selected_data_paths": data,
        "supporting_file_paths": support,
        "metadata_paths": metadata,
        "output_folder": "<new SciLink job folder created after Start>",
        "selected_record_names": _selected_record_names(source),
        "requested_filenames": [str(value).strip() for value in requested if str(value).strip()],
    }


def show_analysis_review(source: Any, service: str, prompt_override: str | None = None) -> Any:
    """Review SciLink with one auto-selected Browser bundle; delegate Edison unchanged."""
    service_name = "SciLink" if service == "scilink" else "Edison Scientific"
    if service != "scilink":
        return _PREVIOUS_REVIEW(source, service, prompt_override=prompt_override)

    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup
    import faradaiq.v250_reliability as reliability

    tk, ttk, ScrolledText = _g("tk"), _g("ttk"), _g("ScrolledText")
    messagebox = _g("messagebox")
    gui = _gui_from(source)
    cfg = old.load_sharing_settings()
    candidates = _review_candidates(source, old.collect_share_candidates(source, cfg))
    initial_prompt = "" if prompt_override is None else str(prompt_override)
    bundle_present = bool(candidates and candidates[0].get("generated") == "browser_selection_bundle")

    win = tk.Toplevel(gui)
    win.title(f"FaradaIQ Studio {VERSION} - Review SciLink")
    win.geometry("1260x890")
    win.minsize(980, 720)
    outer = ttk.Frame(win, style="Card.TFrame", padding=(12, 12))
    outer.pack(fill="both", expand=True, padx=10, pady=10)
    ttk.Label(outer, text="Review SciLink request", style="Section.TLabel").pack(anchor="w")
    header_text = (
        "The current Browser selection has been prepared as one SciLink-ready bundle and selected automatically. "
        "Enter or edit the prompt, review the bundle, then start the job. Other files remain unchecked."
        if bundle_present else
        "No Browser data file is currently selected. Enter a prompt and explicitly select any files you want copied into the SciLink job."
    )
    ttk.Label(outer, text=header_text, style="Muted.TLabel", wraplength=1180).pack(anchor="w", pady=(2, 8))

    tabs = ttk.Notebook(outer)
    tabs.pack(fill="both", expand=True)
    prompt_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    files_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    request_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    settings_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    tabs.add(prompt_tab, text="Prompt")
    tabs.add(files_tab, text="Files to Share")
    tabs.add(request_tab, text="Request JSON")
    tabs.add(settings_tab, text="Service Settings")

    prompt_box = ScrolledText(prompt_tab, wrap="word")
    prompt_box.pack(fill="both", expand=True)
    if initial_prompt:
        prompt_box.insert("1.0", initial_prompt)
    try:
        prompt_box.focus_set()
    except Exception:
        pass

    search_var = tk.StringVar(value="")
    role_var = tk.StringVar(value="All roles")
    group_var = tk.StringVar(value="All groups")
    type_var = tk.StringVar(value="All types")
    selected_only_var = tk.BooleanVar(value=False)
    selected_initial = sum(1 for item in candidates if item.get("selected"))
    summary_var = tk.StringVar(value=f"Selected {selected_initial} of {len(candidates)} item(s).")

    toolbar = ttk.Frame(files_tab, style="Card.TFrame")
    toolbar.pack(fill="x", pady=(0, 6))
    ttk.Label(toolbar, text="Search", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 4))
    ttk.Entry(toolbar, textvariable=search_var, width=32).grid(row=0, column=1, sticky="ew", padx=(0, 6))
    roles = sorted({_candidate_role(item) for item in candidates})
    groups = sorted({_candidate_group(item) for item in candidates})
    types = sorted({_candidate_extension(item) for item in candidates})
    ttk.Combobox(toolbar, textvariable=role_var, values=["All roles", *roles], state="readonly", width=24).grid(row=0, column=2, padx=3)
    ttk.Combobox(toolbar, textvariable=group_var, values=["All groups", *groups], state="readonly", width=25).grid(row=0, column=3, padx=3)
    ttk.Combobox(toolbar, textvariable=type_var, values=["All types", *types], state="readonly", width=18).grid(row=0, column=4, padx=3)
    ttk.Checkbutton(toolbar, text="Selected only", variable=selected_only_var).grid(row=0, column=5, padx=(6, 3))
    toolbar.columnconfigure(1, weight=1)

    actions = ttk.Frame(files_tab, style="Card.TFrame")
    actions.pack(fill="x", pady=(0, 5))
    ttk.Label(actions, textvariable=summary_var, style="Muted.TLabel").pack(side="left", fill="x", expand=True)

    canvas = tk.Canvas(files_tab, highlightthickness=0, borderwidth=0)
    ybar = ttk.Scrollbar(files_tab, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=ybar.set)
    canvas.pack(side="left", fill="both", expand=True)
    ybar.pack(side="right", fill="y")
    inner = ttk.Frame(canvas, style="Card.TFrame")
    inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(inner_id, width=e.width))

    file_rows: list[dict[str, Any]] = []
    for index, item in enumerate(candidates):
        var = tk.BooleanVar(value=bool(item.get("selected")))
        row = ttk.Frame(inner, style="Card.TFrame", padding=(2, 3))
        row.grid(row=index, column=0, sticky="ew")
        row.columnconfigure(2, weight=1)
        display = item.get("display_path") or item.get("path") or "(generated only if checked)"
        ttk.Checkbutton(row, variable=var).grid(row=0, column=0, sticky="nw", padx=(0, 4))
        ttk.Label(row, text=_candidate_role(item), style="Card.TLabel", width=31).grid(row=0, column=1, sticky="nw", padx=(0, 8))
        ttk.Label(row, text=str(display), style="Muted.TLabel", wraplength=720).grid(row=0, column=2, sticky="nw")
        ttk.Label(row, text=old._format_size(item.get("size_bytes")), style="Muted.TLabel", width=10).grid(row=0, column=3, sticky="ne", padx=(8, 0))
        file_rows.append({"item": item, "var": var, "row": row, "visible": True})
    inner.columnconfigure(0, weight=1)

    mode_var = tk.StringVar(value="Autonomous")
    mode_row = ttk.Frame(settings_tab, style="Card.TFrame")
    mode_row.pack(fill="x", pady=(0, 8))
    ttk.Label(mode_row, text="SciLink mode", style="Card.TLabel").pack(side="left")
    ttk.Combobox(mode_row, textvariable=mode_var, values=("Autonomous", "Co-pilot"), state="readonly", width=18).pack(side="left", padx=(8, 0))
    ttk.Label(mode_row, text="Autonomous is the default.", style="Muted.TLabel").pack(side="left", padx=(8, 0))
    ttk.Label(settings_tab, text="Requested filenames (optional, one per line)", style="Card.TLabel").pack(anchor="w")
    filenames_box = ScrolledText(settings_tab, wrap="none", height=6)
    filenames_box.pack(fill="x", pady=(2, 8))
    details_box = ScrolledText(settings_tab, wrap="word", height=20)
    details_box.pack(fill="both", expand=True)
    details = cleanup._service_details_text("scilink", cfg)
    details += (
        "\n\nBrowser bundle behavior\n"
        "- The currently selected Browser files are copied into the new job's data folder.\n"
        "- A compact metadata index is generated; the large canonical JSON is not auto-selected.\n"
        "- SciLink starts with its working directory set to the job-local data folder.\n"
        "- The scientific prompt remains exactly the text visible in the Prompt tab."
    )
    details_box.insert("1.0", details)
    details_box.configure(state="disabled")

    request_box = ScrolledText(request_tab, wrap="word")
    request_box.pack(fill="both", expand=True)
    request_box.configure(state="disabled")

    def requested_names() -> list[str]:
        return [line.strip() for line in filenames_box.get("1.0", "end-1c").splitlines() if line.strip()]

    def visible_rows() -> list[dict[str, Any]]:
        return [entry for entry in file_rows if entry.get("visible")]

    def selected_items() -> list[dict[str, Any]]:
        return [dict(entry["item"]) for entry in file_rows if entry["var"].get()]

    def refresh_summary(*_args: Any) -> None:
        selected = [entry for entry in file_rows if entry["var"].get()]
        total = sum(int(entry["item"].get("size_bytes") or 0) for entry in selected)
        auto_bundle = any(entry["item"].get("generated") == "browser_selection_bundle" for entry in selected)
        suffix = " Current Browser bundle is selected." if auto_bundle else ""
        summary_var.set(
            f"Selected {len(selected)} of {len(file_rows)} item(s), {old._format_size(total)}. "
            f"Visible after filter: {len(visible_rows())}.{suffix}"
        )
        request_box.configure(state="normal")
        request_box.delete("1.0", "end")
        request_box.insert("1.0", json.dumps(_preview_request(source, selected_items(), requested_names()), ensure_ascii=False, indent=2))
        request_box.configure(state="disabled")

    def apply_filter(*_args: Any) -> None:
        query = search_var.get().strip().lower()
        role_filter = role_var.get()
        group_filter = group_var.get()
        type_filter = type_var.get()
        selected_only = bool(selected_only_var.get())
        for entry in file_rows:
            item = entry["item"]
            haystack = " ".join(str(item.get(key) or "") for key in ("role", "source_group", "path", "display_path", "name", "label", "generated")).lower()
            match = (
                (not query or query in haystack)
                and (role_filter == "All roles" or _candidate_role(item) == role_filter)
                and (group_filter == "All groups" or _candidate_group(item) == group_filter)
                and (type_filter == "All types" or _candidate_extension(item) == type_filter)
                and (not selected_only or bool(entry["var"].get()))
            )
            entry["visible"] = bool(match)
            entry["row"].grid() if match else entry["row"].grid_remove()
        refresh_summary()

    def set_rows(rows: Sequence[dict[str, Any]], value: bool) -> None:
        for entry in rows:
            entry["var"].set(value)
        apply_filter()

    ttk.Button(actions, text="Select visible", command=lambda: set_rows(visible_rows(), True)).pack(side="right", padx=(4, 0))
    ttk.Button(actions, text="Clear visible", command=lambda: set_rows(visible_rows(), False)).pack(side="right", padx=(4, 0))
    ttk.Button(actions, text="Select all", command=lambda: set_rows(file_rows, True)).pack(side="right", padx=(4, 0))
    ttk.Button(actions, text="Clear all", command=lambda: set_rows(file_rows, False)).pack(side="right", padx=(4, 0))
    for var in (search_var, role_var, group_var, type_var, selected_only_var):
        try:
            var.trace_add("write", apply_filter)
        except Exception:
            pass
    for entry in file_rows:
        try:
            entry["var"].trace_add("write", refresh_summary)
        except Exception:
            pass
    filenames_box.bind("<KeyRelease>", refresh_summary)
    apply_filter()

    footer = ttk.Frame(outer, style="Card.TFrame")
    footer.pack(fill="x", pady=(8, 0))
    status_var = tk.StringVar(value="Nothing starts until you approve. The current Browser bundle is selected automatically when available.")
    ttk.Label(footer, textvariable=status_var, style="Muted.TLabel").pack(side="left", fill="x", expand=True)

    def start() -> None:
        exact_prompt = prompt_box.get("1.0", "end-1c")
        if not exact_prompt.strip():
            messagebox.showwarning("Prompt required", "Enter the SciLink prompt before starting.", parent=win)
            return
        selected = selected_items()
        preview = _preview_request(source, selected, requested_names())
        if not preview.get("selected_data_paths"):
            messagebox.showwarning(
                "Experimental data required",
                "Select at least one Browser data file or keep the automatically generated current-selection bundle checked before starting SciLink.",
                parent=win,
            )
            tabs.select(files_tab)
            return
        status_var.set("Creating the job-local SciLink data bundle...")
        try:
            job = write_scilink_job(
                source,
                exact_prompt,
                selected,
                cfg,
                mode=mode_var.get(),
                requested_filenames=requested_names(),
            )
            if not job["manifest"].get("selected_data_paths"):
                raise RuntimeError("The reviewed SciLink bundle did not produce any readable experimental data files.")
            win.destroy()
            reliability._launch_scilink_transaction(source, job, cfg)
        except Exception as exc:
            messagebox.showerror("Could not start SciLink", f"{exc}\n\n{traceback.format_exc()}", parent=win)

    ttk.Button(footer, text="Start new SciLink job", style="Accent.TButton", command=start).pack(side="right", padx=(6, 0))
    ttk.Button(footer, text="Cancel", command=win.destroy).pack(side="right")
    return win


def _inject_frame_controls(path: Path, payload: Mapping[str, Any]) -> None:
    """Inject a drag-safe manual frame slider and deterministic playback controls."""
    if not payload.get("frame_names"):
        return
    try:
        html = path.read_text(encoding="utf-8")
    except Exception:
        return
    if "faradaiq-frame-dock-v256" in html:
        return
    # Remove the immediately preceding fixed dock so there is only one control surface.
    if "faradaiq-frame-dock-v255" in html:
        start = html.find('<div id="faradaiq-frame-dock-v255"')
        end = html.find('</style>', start)
        if start >= 0 and end >= 0:
            html = html[:start] + html[end + len('</style>'):]
        script_start = html.find('<script id="faradaiq-frame-script-v255"')
        script_end = html.find('</script>', script_start)
        if script_start >= 0 and script_end >= 0:
            html = html[:script_start] + html[script_end + len('</script>'):]

    payload_json = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
    dock = r'''
<div id="faradaiq-frame-dock-v256" role="group" aria-label="Hyperspectral frame controls">
  <button id="faradaiq-frame-play-v256" type="button">▶ Play</button>
  <button id="faradaiq-frame-pause-v256" type="button">❚❚ Pause</button>
  <button id="faradaiq-frame-prev-v256" type="button" title="Previous frame">◀ Previous</button>
  <label id="faradaiq-frame-caption-v256" for="faradaiq-frame-range-v256">Frame</label>
  <input id="faradaiq-frame-range-v256" type="range" min="0" max="0" value="0" step="1" aria-label="Drag to choose hyperspectral frame">
  <button id="faradaiq-frame-next-v256" type="button" title="Next frame">Next ▶</button>
  <output id="faradaiq-frame-value-v256">1 / 1</output>
</div>
<style id="faradaiq-frame-style-v256">
html,body{min-height:100%;}body{padding-bottom:76px!important;box-sizing:border-box;}
#faradaiq-frame-dock-v256{position:fixed;left:0;right:0;bottom:0;z-index:2147483000;display:grid;grid-template-columns:auto auto auto minmax(150px,auto) minmax(280px,1fr) auto auto;align-items:center;gap:8px;padding:9px 12px;border-top:1px solid #9ba9bc;background:rgba(247,249,252,.99);box-shadow:0 -2px 10px rgba(20,42,70,.18);color:#17365d;font:14px/1.25 Arial,sans-serif;box-sizing:border-box;pointer-events:auto}
#faradaiq-frame-dock-v256 button{font:inherit;padding:6px 10px;border:1px solid #9ba9bc;border-radius:4px;background:#fff;color:#17365d;cursor:pointer;font-weight:600;pointer-events:auto}
#faradaiq-frame-dock-v256 button:disabled{opacity:.55;cursor:default}
#faradaiq-frame-range-v256{display:block;width:100%;min-width:220px;height:28px;accent-color:#315b8a;cursor:grab;pointer-events:auto;touch-action:none;position:relative;z-index:2147483001}
#faradaiq-frame-range-v256:active{cursor:grabbing}
#faradaiq-frame-caption-v256{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}
#faradaiq-frame-value-v256{white-space:nowrap;min-width:76px;text-align:right}
@media(max-width:900px){#faradaiq-frame-dock-v256{grid-template-columns:auto auto auto 1fr auto auto}#faradaiq-frame-caption-v256{grid-column:1/-1;grid-row:1}.plotly-graph-div{max-width:100%!important}}
</style>
'''
    script = r'''
<script id="faradaiq-frame-script-v256">
(function(){
  const payload=__PAYLOAD__,names=Array.isArray(payload.frame_names)?payload.frame_names:[],labels=Array.isArray(payload.frame_labels)?payload.frame_labels:[];
  const range=document.getElementById('faradaiq-frame-range-v256'),caption=document.getElementById('faradaiq-frame-caption-v256'),value=document.getElementById('faradaiq-frame-value-v256');
  const play=document.getElementById('faradaiq-frame-play-v256'),pause=document.getElementById('faradaiq-frame-pause-v256');
  const prev=document.getElementById('faradaiq-frame-prev-v256'),next=document.getElementById('faradaiq-frame-next-v256');
  let index=0,playing=false,token=0,dragging=false,lastRendered=-1;
  function graph(){return document.querySelector('.plotly-graph-div');}
  function finiteZ(trace){if(!trace||trace.z==null)return false;const stack=[trace.z];while(stack.length){const item=stack.pop();if(Array.isArray(item)){for(let i=0;i<item.length;i++)stack.push(item[i]);}else if(Number.isFinite(Number(item)))return true;}return false;}
  function clamp(i){return Math.max(0,Math.min(names.length-1,Number(i)||0));}
  function sync(rendered){index=clamp(index);range.value=String(index);caption.textContent=labels[index]||('Frame '+(index+1));value.textContent=(index+1)+' / '+names.length+(dragging?' • release to render':'');play.disabled=playing||names.length<2;pause.disabled=!playing;prev.disabled=names.length<2;next.disabled=names.length<2;if(rendered)lastRendered=index;}
  function ensureSpatialLayer(gd){
    try{
      const visible=(gd.data||[]).some(t=>['contour','heatmap','surface','mesh3d'].includes(String(t.type||'').toLowerCase())&&finiteZ(t));
      if(!visible){
        const frames=(gd._transitionData&&gd._transitionData._frames)||[],frame=frames.find(f=>String(f.name||'')===String(names[index]||''));
        if(frame&&frame.data){const jobs=[];frame.data.forEach((t,i)=>{if(['contour','heatmap','surface','mesh3d'].includes(String(t.type||'').toLowerCase())&&finiteZ(t))jobs.push(Plotly.restyle(gd,{visible:true,z:[t.z]},[i]));});if(jobs.length)return Promise.all(jobs).then(()=>Plotly.redraw(gd));}
      }
      return Plotly.redraw(gd);
    }catch(_){return Promise.resolve();}
  }
  function announce(gd){const name=String(names[index]||'');try{gd._faradaiqActiveFrameName=name;if(gd.dataset)gd.dataset.faradaiqFrameName=name;}catch(_){}try{gd.dispatchEvent(new CustomEvent('faradaiq_frame_changed',{detail:{name:name,index:index,label:labels[index]||''}}));}catch(_){}}
  function show(i){
    const gd=graph();if(!gd||typeof Plotly==='undefined'||!names.length)return Promise.resolve();
    index=clamp(i);dragging=false;sync(false);
    return Plotly.animate(gd,[names[index]],{mode:'immediate',frame:{duration:0,redraw:true},transition:{duration:0}})
      .then(()=>ensureSpatialLayer(gd)).then(()=>{sync(true);announce(gd);})
      .catch(()=>{try{return Plotly.redraw(gd).then(()=>{sync(true);announce(gd);});}catch(_){sync(true);announce(gd);}});
  }
  function stop(){playing=false;token++;sync(false);}
  function run(my){if(!playing||my!==token)return;const target=(index+1)%names.length;show(target).finally(()=>{if(playing&&my===token)setTimeout(()=>run(my),Math.max(80,Number(payload.frame_duration_ms)||280));});}
  function beginDrag(){stop();dragging=true;index=clamp(range.value);sync(false);}
  function previewDrag(){dragging=true;index=clamp(range.value);sync(false);}
  function commitDrag(){const target=clamp(range.value);dragging=false;if(target===lastRendered){index=target;sync(true);return;}show(target);}
  function start(){
    const gd=graph();if(!gd||typeof Plotly==='undefined'){setTimeout(start,60);return;}
    range.max=String(Math.max(0,names.length-1));sync(false);show(0);
    range.addEventListener('pointerdown',beginDrag);range.addEventListener('mousedown',beginDrag);range.addEventListener('touchstart',beginDrag,{passive:true});
    range.addEventListener('input',previewDrag);
    range.addEventListener('change',commitDrag);range.addEventListener('pointerup',commitDrag);range.addEventListener('mouseup',commitDrag);range.addEventListener('touchend',commitDrag);
    range.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowDown','ArrowRight','ArrowUp','Home','End','PageUp','PageDown'].includes(e.key)){stop();setTimeout(()=>show(Number(range.value)),0);}});
    prev.addEventListener('click',()=>{stop();show(index-1);});next.addEventListener('click',()=>{stop();show(index+1);});
    play.addEventListener('click',()=>{if(names.length<2)return;playing=true;const my=++token;sync(false);run(my);});pause.addEventListener('click',stop);
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
</script>
'''.replace('__PAYLOAD__', payload_json)
    if '<body>' in html:
        html = html.replace('<body>', '<body>' + dock, 1)
    else:
        html = dock + html
    if '</body>' in html:
        html = html.replace('</body>', script + '</body>', 1)
    else:
        html += script
    path.write_text(html, encoding="utf-8")


def _install_visual_patch() -> None:
    import faradaiq.v253_visual_browser as visual
    import faradaiq.v254_contour_prerender as prerender

    visual.VERSION = VERSION
    prerender.VERSION = VERSION
    visual._inject_fixed_frame_controls = _inject_frame_controls
    prerender._CACHE_SCHEMA = "faradaiq-contour-prerender-cache-v256"
    prerender._CACHE_GENERATION = 256

    def cache_root(browser: Any, md: Any) -> Path:
        root = Path(md._view_dir(browser)) / "contour_prerender_cache_v256"
        root.mkdir(parents=True, exist_ok=True)
        return root

    prerender._cache_root = cache_root


def _install_review_patch() -> None:
    global _PREVIOUS_REVIEW
    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup
    import faradaiq.v245_queue_scilink as minimal
    import faradaiq.v250_reliability as reliability

    if _PREVIOUS_REVIEW is None:
        _PREVIOUS_REVIEW = reliability.show_analysis_review
    reliability.show_analysis_review = show_analysis_review
    old.show_analysis_review = show_analysis_review
    cleanup.show_analysis_review = show_analysis_review
    minimal.show_analysis_review = show_analysis_review
    minimal._write_minimal_scilink_job = write_scilink_job
    _CORE["show_analysis_review"] = show_analysis_review
    _CORE["FARADAIQ_SCILINK_BROWSER_BUNDLE_AUTO_SELECTED"] = True
    _CORE["FARADAIQ_SCILINK_JOB_LOCAL_DATA"] = True


def _normalize_titles() -> None:
    for cls_name in ("SessionReaderGUI", "FaradaIQH5Browser", "FaradaIQDataBrowser"):
        cls = _g(cls_name)
        if cls is None or getattr(cls, "_v256_title_patch", False):
            continue
        old_title = getattr(cls, "title", None)
        if not callable(old_title):
            continue

        def title(self: Any, text: Any = None, _old=old_title) -> Any:
            if text is None:
                return _old(self)
            return _old(self, re.sub(r"v\d+(?:\.\d+)?", VERSION, str(text)))

        cls.title = title
        cls.wm_title = title
        cls._v256_title_patch = True


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE
    _CORE = core
    modules = []
    try:
        from faradaiq import (
            multidim_v216, v228_intelligence, v239_scilink_edison, v244_workflow_cleanup,
            v245_queue_scilink, v247_experience, v250_reliability, v251_context_scilink,
            v253_llm_qa, v253_visual_browser, v254_contour_prerender, v255_browser_color,
        )
        modules = [
            multidim_v216, v228_intelligence, v239_scilink_edison, v244_workflow_cleanup,
            v245_queue_scilink, v247_experience, v250_reliability, v251_context_scilink,
            v253_llm_qa, v253_visual_browser, v254_contour_prerender, v255_browser_color,
        ]
    except Exception:
        pass
    for module in modules:
        try:
            module.VERSION = VERSION
        except Exception:
            pass
    _install_visual_patch()
    _install_review_patch()
    _normalize_titles()
    core["FARADAIQ_MANUAL_FRAME_SLIDER"] = True
    core["FARADAIQ_MANUAL_FRAME_RENDER_ON_RELEASE"] = True
    core["FARADAIQ_CONTOUR_CACHE_GENERATION"] = 256
