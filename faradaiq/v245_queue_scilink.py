"""FaradaIQ Studio v273 queue-selection and minimal SciLink handoff patch.

This release keeps active batch jobs independent from session loading and makes
Ask SciLink submit only the exact user-entered prompt plus one explicit JSON
request object. No FaradaIQ scientific/skill prompt is appended.
"""
from __future__ import annotations

import json
import os
import queue as queue_module
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping

VERSION = "v273"
_CORE: MutableMapping[str, Any] = {}
_OLD: Any = None
_CLEANUP: Any = None
_PREV_REVIEW: Any = None


def _g(name: str, default: Any = None) -> Any:
    return _CORE.get(name, default)


def _gui_from(source: Any) -> Any:
    return getattr(source, "gui", source)


def _browser_from(source: Any) -> Any:
    try:
        return _OLD._browser_from(source)
    except Exception:
        return source if hasattr(source, "tree") else None


def _selected_record_names(source: Any) -> list[str]:
    """Return visible browser record labels only, never raw data content."""
    browser = _browser_from(source)
    names: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            names.append(text)

    if browser is None:
        return names
    try:
        tree = getattr(browser, "tree", None)
        for iid in list(tree.selection() or []) if tree is not None else []:
            try:
                item = tree.item(iid)
                add(item.get("text"))
                values = item.get("values") or []
                if values:
                    add(values[0])
            except Exception:
                continue
    except Exception:
        pass
    try:
        lb = getattr(browser, "plot_view_list", None)
        for index in list(lb.curselection() or []) if lb is not None else []:
            try:
                add(lb.get(index))
            except Exception:
                continue
    except Exception:
        pass
    return names


def _prepare_selected_paths(
    source: Any,
    selected: list[dict[str, Any]],
    job_dir: Path,
) -> tuple[list[str], list[str], list[str]]:
    data_paths: list[str] = []
    supporting_paths: list[str] = []
    metadata_paths: list[str] = []

    def append_unique(bucket: list[str], path: Path) -> None:
        value = str(path.resolve())
        if value not in bucket:
            bucket.append(value)

    for item in selected:
        generated = str(item.get("generated") or "").strip().lower()
        role = str(item.get("role") or "").strip().lower()
        group = str(item.get("source_group") or "").strip().lower()
        if generated == "session_metadata":
            path = job_dir / "faradaiq_session_metadata.json"
            payload = _OLD._session_metadata_payload(source)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            append_unique(metadata_paths, path)
            continue
        raw = str(item.get("path") or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if not path.exists() or not path.is_file():
            continue
        suffix = path.suffix.lower()
        metadata_role = (
            "metadata" in role
            or "manifest" in role
            or "metadata" in group
            or suffix in {".json", ".yaml", ".yml", ".xml"}
        )
        support_role = any(
            token in role or token in group
            for token in ("literature", "figure", "visualization", "additional", "support", "reference")
        )
        if metadata_role:
            append_unique(metadata_paths, path)
        elif support_role:
            append_unique(supporting_paths, path)
        else:
            append_unique(data_paths, path)
    return data_paths, supporting_paths, metadata_paths


def build_scilink_prompt(_source: Any, question: Any) -> str:
    """Return only caller-supplied text; never add FaradaIQ instructions."""
    return str(question or "")


def _write_minimal_scilink_job(
    source: Any,
    exact_prompt: str,
    selected: list[dict[str, Any]],
    cfg: Mapping[str, Any],
    *,
    mode: str = "autonomous",
    requested_filenames: list[str] | None = None,
) -> dict[str, Any]:
    """Create a SciLink job with only the exact prompt and explicit request JSON."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_id = f"scilink_{stamp}_{uuid.uuid4().hex[:6]}"
    base = _OLD._safe_name(_OLD._base_name(_gui_from(source)))
    job_dir = _OLD._job_root(source) / f"{base}_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=False)

    prompt_path = job_dir / "analysis_prompt.md"
    # Deliberately preserve the exact Tk text content. No heading, schema,
    # scientific instruction, modality rule, skill prompt, or trailing newline.
    prompt_path.write_text(exact_prompt, encoding="utf-8")

    data_paths, supporting_paths, metadata_paths = _prepare_selected_paths(source, selected, job_dir)
    request = {
        "selected_data_paths": data_paths,
        "supporting_file_paths": supporting_paths,
        "metadata_paths": metadata_paths,
        "output_folder": str(job_dir.resolve()),
        "selected_record_names": _selected_record_names(source),
        "requested_filenames": [str(x).strip() for x in (requested_filenames or []) if str(x).strip()],
    }
    # A single JSON object is the complete non-prompt handoff. Keep the
    # compatibility manifest_path alias pointed at this same file so older
    # SciLink launcher code cannot create or submit a second payload.
    request_path = job_dir / "scilink_request.json"
    encoded = json.dumps(request, ensure_ascii=False, indent=2)
    request_path.write_text(encoded, encoding="utf-8")
    manifest_path = request_path

    settings = dict(_CLEANUP._service_settings(cfg))
    settings.update({
        "scilink_mode": "autonomous" if str(mode).strip().lower().startswith("auto") else "co-pilot",
        "inject_faradaiq_skill_prompt": False,
        "handoff_contract": "exact_user_prompt_plus_request_json_only",
    })
    settings_path = job_dir / "service_settings.json"
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "job_id": job_id,
        "job_dir": job_dir,
        "prompt_path": prompt_path,
        "manifest_path": manifest_path,
        "request_path": request_path,
        "settings_path": settings_path,
        "manifest": request,
        "settings": settings,
    }


def show_analysis_review(source: Any, service: str, prompt_override: str | None = None) -> Any:
    """Open review UI. SciLink starts blank unless explicit user text is supplied."""
    if service != "scilink":
        # Edison keeps its reviewed upload workflow, but no generated main-GUI
        # description is injected when the caller supplied no prompt.
        return _PREV_REVIEW(source, service, prompt_override=prompt_override or "")

    tk, ttk = _g("tk"), _g("ttk")
    ScrolledText = _g("ScrolledText")
    messagebox = _g("messagebox")
    gui = _gui_from(source)
    cfg = _OLD.load_sharing_settings()
    candidates = _OLD.collect_share_candidates(source, cfg)
    # Every SciLink review begins blank. Text from Smart Suggestions, AI Chat,
    # main description fields, or earlier generated prompts is never injected.
    # Only text typed by the user in this review window is submitted.
    initial_prompt = ""

    win = tk.Toplevel(gui)
    win.title(f"FaradaIQ Studio {VERSION} - Review SciLink")
    win.geometry("1180x820")
    outer = ttk.Frame(win, style="Card.TFrame", padding=(12, 12))
    outer.pack(fill="both", expand=True, padx=10, pady=10)
    ttk.Label(outer, text="Review SciLink request", style="Section.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text=(
            "SciLink receives only the exact text in the Prompt tab and the JSON object shown in Request JSON. "
            "Files start unchecked. Submission contains only the exact prompt and request JSON."
        ),
        style="Muted.TLabel",
        wraplength=1100,
    ).pack(anchor="w", pady=(2, 8))

    tabs = ttk.Notebook(outer)
    tabs.pack(fill="both", expand=True)
    prompt_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    files_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    request_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    settings_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    tabs.add(prompt_tab, text="Prompt")
    tabs.add(files_tab, text="Files to Share")
    tabs.add(request_tab, text="Request JSON")
    tabs.add(settings_tab, text="Service Details")

    prompt_box = ScrolledText(prompt_tab, wrap="word")
    prompt_box.pack(fill="both", expand=True)
    # Deliberately leave the prompt widget blank.

    summary_var = tk.StringVar(value="Selected 0 files. Text prompt and path JSON only.")
    ttk.Label(files_tab, textvariable=summary_var, style="Muted.TLabel").pack(anchor="w", fill="x", pady=(0, 5))
    canvas = tk.Canvas(files_tab, highlightthickness=0, borderwidth=0)
    ybar = ttk.Scrollbar(files_tab, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=ybar.set)
    canvas.pack(side="left", fill="both", expand=True)
    ybar.pack(side="right", fill="y")
    inner = ttk.Frame(canvas, style="Card.TFrame")
    inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(inner_id, width=e.width))

    file_vars: list[tuple[dict[str, Any], Any]] = []
    for row, item in enumerate(candidates):
        var = tk.BooleanVar(value=False)
        file_vars.append((item, var))
        display_path = item.get("display_path") or item.get("path") or "(generated only if checked)"
        ttk.Checkbutton(inner, variable=var).grid(row=row, column=0, sticky="nw", padx=(0, 4), pady=2)
        ttk.Label(inner, text=str(item.get("role") or "Data file"), style="Card.TLabel").grid(row=row, column=1, sticky="nw", padx=(0, 8), pady=2)
        ttk.Label(inner, text=str(display_path), style="Muted.TLabel", wraplength=680).grid(row=row, column=2, sticky="nw", pady=2)
        ttk.Label(inner, text=_OLD._format_size(item.get("size_bytes")), style="Muted.TLabel").grid(row=row, column=3, sticky="ne", padx=(8, 0), pady=2)
    inner.columnconfigure(2, weight=1)

    request_box = ScrolledText(request_tab, wrap="word")
    request_box.pack(fill="both", expand=True)
    request_box.configure(state="disabled")

    mode_var = tk.StringVar(value="Autonomous")
    mode_row = ttk.Frame(settings_tab, style="Card.TFrame")
    mode_row.pack(fill="x", pady=(0, 8))
    ttk.Label(mode_row, text="SciLink mode", style="Card.TLabel").pack(side="left")
    mode_combo = ttk.Combobox(mode_row, textvariable=mode_var, values=("Autonomous", "Co-pilot"), state="readonly", width=18)
    mode_combo.pack(side="left", padx=(8, 0))
    ttk.Label(mode_row, text="Autonomous is the default.", style="Muted.TLabel").pack(side="left", padx=(8, 0))

    ttk.Label(settings_tab, text="Requested filenames (optional, one per line)", style="Card.TLabel").pack(anchor="w")
    filenames_box = ScrolledText(settings_tab, wrap="none", height=6)
    filenames_box.pack(fill="x", pady=(2, 8))
    detail_box = ScrolledText(settings_tab, wrap="word", height=18)
    detail_box.pack(fill="both", expand=True)
    details = _CLEANUP._service_details_text("scilink", cfg)
    details += "\n\nSubmission: exact prompt + request JSON only"
    detail_box.insert("1.0", details)
    detail_box.configure(state="disabled")

    def requested_names() -> list[str]:
        return [line.strip() for line in filenames_box.get("1.0", "end-1c").splitlines() if line.strip()]

    def preview_request() -> dict[str, Any]:
        selected = [dict(item) for item, var in file_vars if var.get()]
        # Preview uses paths only. Generated metadata is represented by its future name.
        data: list[str] = []
        support: list[str] = []
        metadata: list[str] = []
        for item in selected:
            role = str(item.get("role") or "").lower()
            group = str(item.get("source_group") or "").lower()
            generated = str(item.get("generated") or "").lower()
            path = str(item.get("path") or "").strip()
            if generated == "session_metadata":
                metadata.append("faradaiq_session_metadata.json (generated in new job folder)")
                continue
            if not path:
                continue
            suffix = Path(path).suffix.lower()
            if "metadata" in role or "manifest" in role or "metadata" in group or suffix in {".json", ".yaml", ".yml", ".xml"}:
                metadata.append(path)
            elif any(t in role or t in group for t in ("literature", "figure", "visualization", "additional", "support", "reference")):
                support.append(path)
            else:
                data.append(path)
        return {
            "selected_data_paths": data,
            "supporting_file_paths": support,
            "metadata_paths": metadata,
            "output_folder": "(new SciLink job folder created after Start)",
            "selected_record_names": _selected_record_names(source),
            "requested_filenames": requested_names(),
        }

    def refresh_summary(*_args: Any) -> None:
        selected = [item for item, var in file_vars if var.get()]
        total = sum(int(item.get("size_bytes") or 0) for item in selected)
        summary_var.set(f"Selected {len(selected)} of {len(file_vars)} item(s), {_OLD._format_size(total)}. All items were unchecked when opened.")
        try:
            request_box.configure(state="normal")
            request_box.delete("1.0", "end")
            request_box.insert("1.0", json.dumps(preview_request(), ensure_ascii=False, indent=2))
            request_box.configure(state="disabled")
        except Exception:
            pass

    for _item, var in file_vars:
        try:
            var.trace_add("write", refresh_summary)
        except Exception:
            pass
    try:
        filenames_box.bind("<KeyRelease>", refresh_summary)
    except Exception:
        pass
    refresh_summary()

    footer = ttk.Frame(outer, style="Card.TFrame")
    footer.pack(fill="x", pady=(8, 0))
    status_var = tk.StringVar(value="Prompt is blank until you enter text. Files are unchecked. Mode is Autonomous.")
    ttk.Label(footer, textvariable=status_var, style="Muted.TLabel").pack(side="left", fill="x", expand=True)

    def start() -> None:
        exact_prompt = prompt_box.get("1.0", "end-1c")
        if not exact_prompt.strip():
            messagebox.showwarning("Prompt required", "Enter the SciLink prompt before starting.", parent=win)
            return
        selected = [dict(item) for item, var in file_vars if var.get()]
        status_var.set("Creating exact-prompt SciLink job...")
        try:
            job = _write_minimal_scilink_job(
                source,
                exact_prompt,
                selected,
                cfg,
                mode=mode_var.get(),
                requested_filenames=requested_names(),
            )
            win.destroy()
            _OLD._launch_scilink(source, job, cfg)
        except Exception as exc:
            messagebox.showerror("Could not start SciLink", f"{exc}\n\n{traceback.format_exc()}", parent=win)

    ttk.Button(footer, text="Start new SciLink job", style="Accent.TButton", command=start).pack(side="right", padx=(6, 0))
    ttk.Button(footer, text="Cancel", command=win.destroy).pack(side="right")
    return win


def _patch_queue_classes() -> None:
    Model = _g("BatchQueueModel")
    Window = _g("BatchQueueWindow")
    if Window is None:
        try:
            from faradaiq.v233_batch_jobs import BatchQueueWindow as Window
        except Exception:
            Window = None
    if Model is not None and not getattr(Model, "_v245_poll_patch", False):
        def poll_events(self: Any) -> None:
            """Refresh progress without changing the user's queue selection."""
            while True:
                try:
                    kind, payload = self.events.get_nowait()
                except queue_module.Empty:
                    break
                if kind == "log":
                    job_id, line = payload
                    try:
                        if self.window is not None and self.window.winfo_exists():
                            self.window.append_log(job_id, line)
                    except Exception:
                        pass
                    try:
                        if self.transaction_window is not None and self.transaction_window.winfo_exists():
                            self.transaction_window.append_log(job_id, line)
                    except Exception:
                        pass
                elif kind in {"refresh", "done"}:
                    # Payload identifies the job that changed, not the job the
                    # user wants selected. Never pass it as select_job.
                    self.refresh_window(select_job=None)
            if self.running or not self.events.empty():
                try:
                    self.gui.after(120, self.poll_events)
                except Exception:
                    pass
        Model.poll_events = poll_events
        Model._v245_poll_patch = True

    if Window is not None and not getattr(Window, "_v245_restore_patch", False):
        def restore_selected_session(self: Any) -> None:
            """Restore main/browser state without mutating queue execution state."""
            job = self.selected_job()
            if job is None:
                return
            model = self.model
            runtime = {
                "pause_after_current": bool(model.pause_after_current),
                "stop_after_current": bool(model.stop_after_current),
                "running": bool(model.running),
                "current_job_id": model.current_job_id,
                "active_job_ids": set(model.active_job_ids),
                "run_job_ids": set(model._run_job_ids) if model._run_job_ids is not None else None,
                "last_run_job_ids": set(model._last_run_job_ids),
                "stop_event": bool(model.stop_event.is_set()),
            }
            selected_id = str(job.job_id)
            try:
                path = model.write_job_session(job, include_browser=bool(job.outputs.get("browser_cache")))
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
                # The job is already in the queue. Loading it into the main GUI
                # must not merge, replace, deduplicate, pause, or resume queue rows.
                payload = dict(payload)
                payload.pop("batch_job", None)
                apply_session = self.core.get("_v177_apply_full_session")
                if not callable(apply_session):
                    raise RuntimeError("FaradaIQ full-session loader is unavailable.")
                apply_session(self.parent, payload, open_browsers=True)
            except Exception as exc:
                if self.messagebox:
                    self.messagebox.showerror("Session restore failed", f"{exc}\n\n{traceback.format_exc()}", parent=self.win)
                return
            finally:
                model.pause_after_current = runtime["pause_after_current"]
                model.stop_after_current = runtime["stop_after_current"]
                model.running = runtime["running"]
                model.current_job_id = runtime["current_job_id"]
                model.active_job_ids = set(runtime["active_job_ids"])
                model._run_job_ids = set(runtime["run_job_ids"]) if runtime["run_job_ids"] is not None else None
                model._last_run_job_ids = set(runtime["last_run_job_ids"])
                if runtime["stop_event"]:
                    model.stop_event.set()
                else:
                    model.stop_event.clear()
            try:
                self.refresh(select_job=selected_id)
                self.parent.lift()
                self.status_var.set(
                    f"Loaded {job.experiment_name}. Queue execution and the selected completed row were preserved; running jobs continue unchanged."
                )
            except Exception:
                pass
        Window.restore_selected_session = restore_selected_session
        Window._v245_restore_patch = True


def _patch_generic_session_restore() -> None:
    previous = _g("_v177_apply_full_session")
    BatchJob = _g("BatchJob")
    if not callable(previous) or BatchJob is None or _g("_v245_session_restore_installed"):
        return

    def apply_session(gui: Any, payload: Mapping[str, Any], open_browsers: bool = True) -> Any:
        model = (
            getattr(gui, "_v233_batch_model", None)
            or getattr(gui, "_v232_batch_model", None)
            or getattr(gui, "_v230_batch_model", None)
            or getattr(gui, "_v229_batch_model", None)
        )
        if model is None:
            return previous(gui, payload, open_browsers=open_browsers)
        original_jobs = list(model.jobs)
        runtime = {
            "pause_after_current": bool(model.pause_after_current),
            "stop_after_current": bool(model.stop_after_current),
            "running": bool(model.running),
            "current_job_id": model.current_job_id,
            "active_job_ids": set(model.active_job_ids),
            "run_job_ids": set(model._run_job_ids) if model._run_job_ids is not None else None,
            "last_run_job_ids": set(model._last_run_job_ids),
            "stop_event": bool(model.stop_event.is_set()),
        }
        result = previous(gui, payload, open_browsers=open_browsers)

        # Rebuild by job ID from the pre-load snapshot. Active object references
        # always win. A restored job can replace only the same terminal job ID.
        restored_payload = payload.get("batch_job") if isinstance(payload, Mapping) else None
        restored = BatchJob.from_dict(restored_payload) if isinstance(restored_payload, Mapping) else None
        by_id: dict[str, Any] = {str(j.job_id): j for j in original_jobs}
        order = [str(j.job_id) for j in original_jobs]
        if restored is not None:
            rid = str(restored.job_id)
            existing = by_id.get(rid)
            active_statuses = {"Running", "Stopping", "Queued", "Paused", "Interrupted", "Inspecting"}
            if existing is None:
                by_id[rid] = restored
                order.append(rid)
            elif str(existing.status) not in active_statuses:
                by_id[rid] = restored
        model.jobs[:] = [by_id[jid] for jid in order if jid in by_id]
        model.pause_after_current = runtime["pause_after_current"]
        model.stop_after_current = runtime["stop_after_current"]
        model.running = runtime["running"]
        model.current_job_id = runtime["current_job_id"]
        model.active_job_ids = set(runtime["active_job_ids"])
        model._run_job_ids = set(runtime["run_job_ids"]) if runtime["run_job_ids"] is not None else None
        model._last_run_job_ids = set(runtime["last_run_job_ids"])
        if runtime["stop_event"]:
            model.stop_event.set()
        else:
            model.stop_event.clear()
        model.autosave_queue()
        model.refresh_window(select_job=str(restored.job_id) if restored is not None and model.find(str(restored.job_id)) else None)
        return result

    _CORE["_v177_apply_full_session"] = apply_session
    _CORE["_v245_session_restore_installed"] = True


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE, _OLD, _CLEANUP, _PREV_REVIEW
    _CORE = core
    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup
    _OLD = old
    _CLEANUP = cleanup
    _PREV_REVIEW = cleanup.show_analysis_review
    old.VERSION = VERSION
    cleanup.VERSION = VERSION
    cleanup.show_analysis_review = show_analysis_review
    cleanup.build_scilink_prompt = build_scilink_prompt
    old.show_analysis_review = show_analysis_review
    core["_v245_show_analysis_review"] = show_analysis_review
    core["_v245_build_scilink_prompt"] = build_scilink_prompt
    core["_v244_build_scilink_prompt"] = build_scilink_prompt
    core["_v244_show_analysis_review"] = show_analysis_review
    core["_v239_show_analysis_review"] = show_analysis_review
    core["_v245_write_minimal_scilink_job"] = _write_minimal_scilink_job
    _patch_queue_classes()
    _patch_generic_session_restore()
