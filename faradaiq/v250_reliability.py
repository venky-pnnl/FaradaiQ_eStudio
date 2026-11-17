"""FaradaIQ Studio v290 reliability, service, and visualization refinements.

This release focuses on the reported production failures:
- exact-prompt SciLink/Edison/LLM transactions with persistent consoles
- SciLink credential inheritance from the active GUI/environment
- filtered file selection for both SciLink and Edison
- startup AI service-mode selection
- independent DRT/CV plot state
- three explicit color-scale behaviors and non-overlapping Plotly controls
- reliable CV peak/charge/branch maps on NumPy 1.26
- unclipped parallel-worker dialog
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zipfile
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Sequence

VERSION = "v290"
_CORE: MutableMapping[str, Any] = {}
_REVIEW_INSTALLED = False

AI_MODE_SCILINK = "scilink"
AI_MODE_SCILINK_EDISON = "scilink_edison"
AI_MODE_ALL = "scilink_edison_llm"
AI_MODE_LABELS = {
    AI_MODE_SCILINK: "SciLink + LLM tools (Edison available inside SciLink)",
    AI_MODE_SCILINK_EDISON: "SciLink + direct Edison Scientific + LLM tools",
    AI_MODE_ALL: "All services: SciLink + direct Edison Scientific + LLM tools",
}

COLOR_AUTO_FRAME = "Auto per frame"
COLOR_GLOBAL = "Global fixed (all frames)"
COLOR_MANUAL = "User-defined min/max"
COLOR_MODES = (COLOR_AUTO_FRAME, COLOR_GLOBAL, COLOR_MANUAL)


def _g(name: str, default: Any = None) -> Any:
    return _CORE.get(name, default)


def _gui_from(source: Any) -> Any:
    return getattr(source, "gui", source)


def _ai_mode() -> str:
    explicit = str(os.environ.get("FARADAIQ_AI_LAUNCH_MODE", "") or "").strip().lower()
    if explicit in AI_MODE_LABELS:
        return explicit
    edison_legacy = str(os.environ.get("FARADAIQ_EDISON_ENABLED", "")).strip().lower()
    llm_legacy = str(os.environ.get("FARADAIQ_LLM_TOOLS_ENABLED", "")).strip().lower()
    if edison_legacy in {"0", "false", "no", "off"}:
        return AI_MODE_SCILINK
    if llm_legacy in {"0", "false", "no", "off"}:
        return AI_MODE_SCILINK_EDISON
    return AI_MODE_ALL


def _edison_enabled() -> bool:
    """Return whether the separate Ask Edison button is enabled.

    Edison credentials remain available to SciLink in every launch mode. This
    function controls only the direct Edison Scientific GUI action. An explicit
    launch mode is authoritative so stale environment values cannot expose the
    separate Edison button in SciLink-only mode.
    """
    explicit_mode = str(os.environ.get("FARADAIQ_AI_LAUNCH_MODE", "") or "").strip().lower()
    if explicit_mode in AI_MODE_LABELS:
        return explicit_mode in {AI_MODE_SCILINK_EDISON, AI_MODE_ALL}
    direct = str(os.environ.get("FARADAIQ_DIRECT_EDISON_ENABLED", "")).strip().lower()
    if direct in {"0", "false", "no", "off"}:
        return False
    if direct in {"1", "true", "yes", "on"}:
        return True
    return _ai_mode() in {AI_MODE_SCILINK_EDISON, AI_MODE_ALL}


def _llm_tools_enabled() -> bool:
    """Direct text-only LLM tools are available in every launch mode.

    Launch choices control SciLink/Edison service availability only. They never
    hide LLM-guided prompt generation, Smart Suggestions, AI Chat, or Ask LLM.
    """
    return True


def _launch_mode_label() -> str:
    if not _edison_enabled():
        return AI_MODE_LABELS[AI_MODE_SCILINK]
    if _ai_mode() == AI_MODE_ALL:
        return AI_MODE_LABELS[AI_MODE_ALL]
    return AI_MODE_LABELS[AI_MODE_SCILINK_EDISON]


def _set_ai_mode(mode: str) -> str:
    selected = mode if mode in AI_MODE_LABELS else AI_MODE_ALL
    os.environ["FARADAIQ_AI_LAUNCH_MODE"] = selected
    # Edison remains available inside SciLink in every mode. Only the separate
    # Ask Edison GUI action follows the direct-service selection.
    os.environ["FARADAIQ_EDISON_ENABLED"] = "1"
    os.environ["FARADAIQ_DIRECT_EDISON_ENABLED"] = "1" if selected in {AI_MODE_SCILINK_EDISON, AI_MODE_ALL} else "0"
    os.environ["FARADAIQ_LLM_TOOLS_ENABLED"] = "1"
    return selected


def _walk_widgets(root: Any):
    yield root
    try:
        for child in root.winfo_children():
            yield from _walk_widgets(child)
    except Exception:
        return


def _widget_text(widget: Any) -> str:
    try:
        return str(widget.cget("text") or "").strip()
    except Exception:
        return ""


def _find_ai_root(gui: Any) -> Any | None:
    finder = _g("_v89_find_ai_toolbar_root")
    if callable(finder):
        try:
            root = finder(gui)
            if root is not None:
                return root
        except Exception:
            pass
    for widget in _walk_widgets(gui):
        if _widget_text(widget) == "AI workflow":
            return getattr(widget, "master", None)
    return None


def _configure_main_ai_toolbar(gui: Any) -> None:
    """Rebuild the compact Main Studio AI row for the selected startup mode."""
    ttk = _g("ttk")
    root = _find_ai_root(gui)
    if root is None or ttk is None:
        return
    try:
        for child in list(root.winfo_children()):
            child.destroy()
    except Exception:
        return
    import faradaiq.v239_scilink_edison as svc

    outer = ttk.Frame(root, style="Card.TFrame")
    outer.pack(fill="x", pady=(0, 2))
    line = ttk.Frame(outer, style="Card.TFrame")
    line.pack(fill="x", pady=(0, 3))
    ttk.Label(line, text="AI workflow", style="Muted.TLabel").pack(side="left", padx=(0, 8))
    settings = _g("_v37_show_llm_settings")
    ttk.Button(line, text="LLM settings", command=lambda: settings(gui) if callable(settings) else None).pack(side="left", padx=(0, 5))
    generator = _g("_v89_generate_llm_prompt") or _g("_v67_generate_ai_prompt_with_llm") or _g("_v45_generate_ai_prompt_with_llm")
    ttk.Button(line, text="Generate LLM-guided prompt", command=lambda: generator(gui) if callable(generator) else None).pack(side="left", padx=(0, 5))
    ttk.Button(line, text="Ask SciLink", style="Accent.TButton", command=lambda: show_analysis_review(gui, "scilink")).pack(side="left", padx=(0, 5))
    if _edison_enabled():
        ttk.Button(line, text="Ask Edison Scientific", command=lambda: show_analysis_review(gui, "edison")).pack(side="left", padx=(0, 5))
    ttk.Button(line, text="AI Chat", command=lambda: open_transaction_chat(gui)).pack(side="left", padx=(0, 5))
    ttk.Label(
        outer,
        text=(
            f"Startup mode: {AI_MODE_LABELS[_ai_mode()]}. "
            "Edison remains available inside SciLink in every mode. LLM file upload is disabled; "
            "files can be selected only in reviewed SciLink/Edison jobs."
        ),
        style="Muted.TLabel",
    ).pack(anchor="w")


def _show_startup_selector(gui: Any) -> Any:
    """Apply the launcher-selected AI mode without opening a GUI prompt.

    Start_FaradaIQ_Studio_v261.bat is the only interactive service selector.
    The environment it sets is authoritative for the Main Studio and Browser.
    """
    selected = _set_ai_mode(_ai_mode())
    _configure_main_ai_toolbar(gui)
    try:
        gui.set_status(f"AI services selected in startup terminal: {AI_MODE_LABELS[selected]}")
    except Exception:
        pass
    return None

def _candidate_extension(item: Mapping[str, Any]) -> str:
    raw = str(item.get("path") or item.get("display_path") or "")
    return Path(raw).suffix.lower() or "(generated)"


def _candidate_group(item: Mapping[str, Any]) -> str:
    return str(item.get("source_group") or item.get("group") or "Other").strip() or "Other"


def _candidate_role(item: Mapping[str, Any]) -> str:
    return str(item.get("role") or "Data file").strip() or "Data file"


def _selected_record_names(source: Any) -> list[str]:
    try:
        import faradaiq.v247_experience as v247
        return list(v247._selected_record_names(source))
    except Exception:
        return []


def _request_json(source: Any, selected: Sequence[Mapping[str, Any]], requested_filenames: Sequence[str], service: str) -> dict[str, Any]:
    data: list[str] = []
    support: list[str] = []
    metadata: list[str] = []
    for item in selected:
        path = str(item.get("path") or "").strip()
        generated = str(item.get("generated") or "").lower()
        role = _candidate_role(item).lower()
        group = _candidate_group(item).lower()
        if generated == "session_metadata":
            metadata.append("faradaiq_session_metadata.json (generated in job folder)")
            continue
        if not path:
            continue
        suffix = Path(path).suffix.lower()
        if "metadata" in role or "manifest" in role or "metadata" in group or suffix in {".json", ".yaml", ".yml", ".xml"}:
            metadata.append(path)
        elif any(token in role or token in group for token in ("literature", "figure", "visualization", "support", "reference", "additional")):
            support.append(path)
        else:
            data.append(path)
    return {
        "selected_data_paths": data,
        "supporting_file_paths": support,
        "metadata_paths": metadata,
        "output_folder": f"(new {service} job folder created after Start)",
        "selected_record_names": _selected_record_names(source),
        "requested_filenames": [str(v).strip() for v in requested_filenames if str(v).strip()],
    }


def _write_exact_edison_job(
    source: Any,
    exact_prompt: str,
    selected: list[dict[str, Any]],
    cfg: Mapping[str, Any],
    requested_filenames: Sequence[str],
) -> dict[str, Any]:
    """Create an Edison job whose checked files are authoritative and auditable.

    The older compatibility writer is used only to create the isolated job folder
    and service settings.  The manifest, request JSON, ZIP, and upload plan are
    then rebuilt directly from the current review-window selection.  This avoids
    a stale/empty compatibility manifest silently dropping checked files.
    """
    import faradaiq.v239_scilink_edison as old

    # Create the isolated job directory. The compatibility writer may create a
    # preliminary ZIP; every handoff artifact below is rewritten authoritatively.
    job = old._write_job(source, "edison", exact_prompt, selected, cfg)
    job_dir = Path(job["job_dir"]).resolve()
    prompt_path = Path(job["prompt_path"]).resolve()
    prompt_path.write_text(exact_prompt, encoding="utf-8")
    (job_dir / "AI_prompt_current.txt").write_text(exact_prompt, encoding="utf-8")

    manifest_items: list[dict[str, Any]] = []
    missing_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for reviewed_index, item in enumerate(selected, start=1):
        generated = str(item.get("generated") or "").strip()
        if generated == "session_metadata":
            shared = job_dir / "faradaiq_session_metadata.json"
            shared.write_text(
                json.dumps(old._session_metadata_payload(source), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        else:
            raw = str(item.get("path") or "").strip()
            if not raw:
                missing_items.append({
                    "reviewed_index": reviewed_index,
                    "role": _candidate_role(item),
                    "reason": "empty path",
                })
                continue
            shared = Path(raw).expanduser().resolve()
        norm = os.path.normcase(str(shared))
        if norm in seen:
            continue
        seen.add(norm)
        if not shared.exists() or not shared.is_file():
            missing_items.append({
                "reviewed_index": reviewed_index,
                "path": str(shared),
                "role": _candidate_role(item),
                "reason": "file does not exist",
            })
            continue
        manifest_items.append({
            "index": len(manifest_items) + 1,
            "reviewed_index": reviewed_index,
            "path": str(shared),
            "name": shared.name,
            "role": _candidate_role(item),
            "source_group": _candidate_group(item),
            "generated": generated or None,
            "size_bytes": int(shared.stat().st_size),
            "exists": True,
        })

    manifest = {
        "schema": "faradaiq-edison-reviewed-files-v253",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "application": "FaradaIQ Studio",
        "version": VERSION,
        "job_id": str(job.get("job_id") or job_dir.name),
        "service": "edison",
        "analysis_root": str(old._analysis_root(source)),
        "job_folder": str(job_dir),
        "prompt_file": str(prompt_path),
        "reviewed_checkbox_count": len(selected),
        "file_count": len(manifest_items),
        "missing_file_count": len(missing_items),
        "files": manifest_items,
        "missing_files": missing_items,
        "policy": "Only files explicitly checked in the Edison review window are uploaded.",
    }
    manifest_path = job_dir / "files_to_share_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    request = _request_json(source, manifest_items, requested_filenames, "Edison Scientific")
    request["output_folder"] = str(job_dir)
    request_path = job_dir / "edison_request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")

    zip_path = Path(job.get("zip_path") or (job_dir / f"{job_dir.name}_for_Edison.zip")).resolve()
    if zip_path.exists():
        zip_path.unlink()
    packaged: list[dict[str, Any]] = []
    used: set[str] = set()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.write(prompt_path, "analysis_prompt.md")
        zf.write(request_path, "edison_request.json")
        zf.write(manifest_path, "files_to_share_manifest.json")
        for item in manifest_items:
            shared = Path(item["path"])
            role = old._safe_name(item.get("role"), "data")
            arc = f"shared_files/{role}/{int(item['index']):04d}_{shared.name}"
            base_arc = arc
            counter = 2
            while arc.lower() in used:
                stem = Path(base_arc).with_suffix("").as_posix()
                arc = f"{stem}_{counter}{shared.suffix}"
                counter += 1
            used.add(arc.lower())
            zf.write(shared, arc)
            packaged.append({**item, "archive_path": arc})

    with zipfile.ZipFile(zip_path, "r") as zf:
        zip_members = zf.namelist()
        bad_member = zf.testzip()
    audit = {
        "schema": "faradaiq-edison-zip-audit-v253",
        "version": VERSION,
        "zip_path": str(zip_path),
        "zip_size_bytes": int(zip_path.stat().st_size),
        "reviewed_checkbox_count": len(selected),
        "manifest_file_count": len(manifest_items),
        "packaged_file_count": len(packaged),
        "missing_file_count": len(missing_items),
        "zip_member_count": len(zip_members),
        "zip_members": zip_members,
        "packaged_files": packaged,
        "missing_files": missing_items,
        "zip_crc_error_member": bad_member,
        "ready": bad_member is None and len(packaged) == len(manifest_items),
    }
    audit_path = job_dir / "edison_zip_audit.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    upload_plan_path = job_dir / "edison_upload_plan.json"
    upload_plan_path.write_text(
        json.dumps({
            "schema": "faradaiq-edison-upload-plan-v253",
            "version": VERSION,
            "exact_prompt_file": str(prompt_path),
            "request_json": str(request_path),
            "audit_zip": str(zip_path),
            "selected_files": manifest_items,
            "upload_policy": "Upload every checked file individually and fail before task creation if any selected file cannot be uploaded.",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if selected and not manifest_items:
        raise RuntimeError(
            "Edison file review contained checked items, but none resolved to readable files. "
            f"See {manifest_path}."
        )
    if bad_member is not None or len(packaged) != len(manifest_items):
        raise RuntimeError(f"Edison reviewed ZIP verification failed. See {audit_path}.")

    job.update({
        "prompt_path": prompt_path,
        "request_path": request_path,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "zip_path": zip_path,
        "zip_audit_path": audit_path,
        "upload_plan_path": upload_plan_path,
        "selected_file_count": len(manifest_items),
        "packaged_file_count": len(packaged),
    })
    return job


def _write_edison_launcher(job: Mapping[str, Any]) -> Path:
    job_dir = Path(job["job_dir"])
    script = Path(__file__).resolve().parent.parent / "faradaiq_edison_cli_job.py"
    python_exe = Path(sys.executable).resolve()
    bat = job_dir / "START_NEW_EDISON_TRANSACTION.bat"
    lines = [
        "@echo off",
        "setlocal EnableExtensions",
        f"title FaradaIQ Edison {job.get('job_id','job')}",
        "chcp 65001 >nul",
        "echo ============================================================",
        f"echo FaradaIQ Studio {VERSION} - Edison Scientific transaction",
        "echo ============================================================",
        f"echo Job folder: {job_dir}",
        f"echo Prompt:     {job.get('prompt_path')}",
        f"echo Request:    {job.get('request_path','')}",
        f"echo ZIP:        {job.get('zip_path','')}",
        "echo.",
        f'"{python_exe}" "{script}" --job-dir "{job_dir}"',
        "set \"FQ_EXIT=%ERRORLEVEL%\"",
        "echo.",
        "echo Edison transaction process returned with code %FQ_EXIT%.",
        "echo This terminal remains open. The complete log and response are in the job folder.",
        "endlocal",
    ]
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return bat


def _launch_edison_transaction(source: Any, job: Mapping[str, Any]) -> None:
    gui = _gui_from(source)
    bat = _write_edison_launcher(job)
    try:
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            subprocess.Popen(["cmd.exe", "/k", str(bat)], cwd=str(job["job_dir"]), env=dict(os.environ), creationflags=flags)
        else:
            script = Path(__file__).resolve().parent.parent / "faradaiq_edison_cli_job.py"
            subprocess.Popen([sys.executable, str(script), "--job-dir", str(job["job_dir"])], cwd=str(job["job_dir"]), env=dict(os.environ))
        try:
            gui.set_status(f"New Edison Scientific transaction opened: {job.get('job_id')}")
        except Exception:
            pass
    except Exception as exc:
        box = _g("messagebox")
        if box:
            box.showerror("Edison launch failed", f"{exc}\n\nLauncher preserved at:\n{bat}", parent=gui)


def _write_llm_job(source: Any, exact_prompt: str) -> dict[str, Any]:
    import faradaiq.v239_scilink_edison as old
    stamp = time.strftime("%Y%m%d_%H%M%S")
    job_id = f"llm_{stamp}_{uuid.uuid4().hex[:6]}"
    base = old._safe_name(old._base_name(_gui_from(source)))
    job_dir = old._job_root(source) / f"{base}_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=False)
    prompt_path = job_dir / "analysis_prompt.md"
    prompt_path.write_text(exact_prompt, encoding="utf-8")
    settings = old._service_settings(old.load_sharing_settings())
    settings_path = job_dir / "service_settings.json"
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    request_path = job_dir / "llm_request.json"
    request_path.write_text(json.dumps({"prompt_file": str(prompt_path), "file_upload": False}, indent=2), encoding="utf-8")
    return {"job_id": job_id, "job_dir": job_dir, "prompt_path": prompt_path, "settings_path": settings_path, "request_path": request_path, "settings": settings}


def _llm_process_environment(job: Mapping[str, Any]) -> dict[str, str]:
    """Build a process-only, provider-specific environment for text LLM jobs."""
    env = dict(os.environ)
    settings = dict(job.get("settings") or {})
    llm = dict(settings.get("llm") or {})
    provider = str(llm.get("provider") or env.get("FARADAIQ_LLM_PROVIDER") or "Ollama").strip()
    provider_key = provider.lower()
    key = _active_gui_llm_secret(settings)
    if key:
        key.encode("ascii")
        if "ollama" in provider_key:
            env["OLLAMA_API_KEY"] = key
            # Preserve OpenAI-compatible client compatibility; the v253 runner
            # still resolves OLLAMA_API_KEY first and ignores stale cloud keys.
            env["OPENAI_API_KEY"] = key
        elif "litellm" in provider_key or "proxy" in provider_key:
            env["LITELLM_KEY"] = key
        elif "incubator" in provider_key or "pnnl" in provider_key:
            env["AI_INCUBATOR_API_KEY"] = key
        elif "custom" in provider_key:
            env["CUSTOM_LLM_API_KEY"] = key
        else:
            env["OPENAI_API_KEY"] = key
    env["FARADAIQ_LLM_PROVIDER"] = provider
    if llm.get("model"):
        env["FARADAIQ_LLM_MODEL"] = str(llm["model"])
    if llm.get("base_url"):
        env["FARADAIQ_LLM_BASE_URL"] = str(llm["base_url"])
    timeout = str(llm.get("timeout_seconds") or env.get("FARADAIQ_LLM_TIMEOUT_SECONDS") or "3600").strip()
    env["FARADAIQ_LLM_TIMEOUT_SECONDS"] = timeout or "3600"
    return env


def _launch_llm_transaction(source: Any, exact_prompt: str) -> None:
    gui = _gui_from(source)
    job = _write_llm_job(source, exact_prompt)
    script = Path(__file__).resolve().parent.parent / "faradaiq_llm_cli_job.py"
    bat = Path(job["job_dir"]) / "START_NEW_LLM_TRANSACTION.bat"
    py = Path(sys.executable).resolve()
    lines = [
        "@echo off", "setlocal EnableExtensions", "chcp 65001 >nul",
        f"title FaradaIQ LLM {job['job_id']}",
        "echo ============================================================",
        f"echo FaradaIQ Studio {VERSION} - LLM text transaction",
        "echo ============================================================",
        f"echo Job folder: {job['job_dir']}",
        f"echo Prompt:     {job['prompt_path']}",
        "echo No experimental files are sent to the LLM.",
        "echo.",
        f'"{py}" "{script}" --job-dir "{job["job_dir"]}"',
        "set \"FQ_EXIT=%ERRORLEVEL%\"", "echo.",
        "echo LLM transaction returned with code %FQ_EXIT%.",
        "echo This terminal remains open. Full transaction files are in the job folder.",
        "endlocal",
    ]
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    try:
        if os.name == "nt":
            subprocess.Popen(["cmd.exe", "/k", str(bat)], cwd=str(job["job_dir"]), env=_llm_process_environment(job), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        else:
            subprocess.Popen([sys.executable, str(script), "--job-dir", str(job["job_dir"])], cwd=str(job["job_dir"]), env=_llm_process_environment(job))
        try:
            gui.set_status(f"New LLM transaction opened: {job['job_id']}")
        except Exception:
            pass
    except Exception as exc:
        box = _g("messagebox")
        if box:
            box.showerror("LLM transaction launch failed", f"{exc}\n\nLauncher preserved at:\n{bat}", parent=gui)


def show_analysis_review(source: Any, service: str, prompt_override: str | None = None) -> Any:
    """Universal filtered review for SciLink and Edison Scientific."""
    if service == "edison" and not _edison_enabled():
        box = _g("messagebox")
        if box:
            box.showinfo("Edison Scientific disabled", "Choose a startup mode that includes Edison Scientific.", parent=_gui_from(source))
        return None
    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup
    import faradaiq.v245_queue_scilink as minimal

    tk, ttk, ScrolledText = _g("tk"), _g("ttk"), _g("ScrolledText")
    messagebox = _g("messagebox")
    gui = _gui_from(source)
    cfg = old.load_sharing_settings()
    candidates = old.collect_share_candidates(source, cfg)
    initial_prompt = "" if prompt_override is None else str(prompt_override)
    service_name = "SciLink" if service == "scilink" else "Edison Scientific"

    win = tk.Toplevel(gui)
    win.title(f"FaradaIQ Studio {VERSION} - Review {service_name}")
    win.geometry("1240x870")
    win.minsize(960, 700)
    outer = ttk.Frame(win, style="Card.TFrame", padding=(12, 12))
    outer.pack(fill="both", expand=True, padx=10, pady=10)
    ttk.Label(outer, text=f"Review {service_name} request", style="Section.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text=(
            f"{service_name} receives the exact text visible in Prompt. Nothing starts until you approve. "
            "All files begin unchecked. Use search, role, source-group, and file-type filters before explicitly selecting files."
        ),
        style="Muted.TLabel", wraplength=1160,
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
    tabs.add(settings_tab, text="Service Settings")

    prompt_box = ScrolledText(prompt_tab, wrap="word")
    prompt_box.pack(fill="both", expand=True)
    if initial_prompt:
        prompt_box.insert("1.0", initial_prompt)

    search_var = tk.StringVar(value="")
    role_var = tk.StringVar(value="All roles")
    group_var = tk.StringVar(value="All groups")
    type_var = tk.StringVar(value="All types")
    selected_only_var = tk.BooleanVar(value=False)
    summary_var = tk.StringVar(value=f"Selected 0 of {len(candidates)} item(s). All files start unchecked.")

    toolbar = ttk.Frame(files_tab, style="Card.TFrame")
    toolbar.pack(fill="x", pady=(0, 6))
    ttk.Label(toolbar, text="Search", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 4))
    ttk.Entry(toolbar, textvariable=search_var, width=32).grid(row=0, column=1, sticky="ew", padx=(0, 6))
    roles = sorted({_candidate_role(item) for item in candidates})
    groups = sorted({_candidate_group(item) for item in candidates})
    types = sorted({_candidate_extension(item) for item in candidates})
    ttk.Combobox(toolbar, textvariable=role_var, values=["All roles", *roles], state="readonly", width=20).grid(row=0, column=2, padx=3)
    ttk.Combobox(toolbar, textvariable=group_var, values=["All groups", *groups], state="readonly", width=20).grid(row=0, column=3, padx=3)
    ttk.Combobox(toolbar, textvariable=type_var, values=["All types", *types], state="readonly", width=14).grid(row=0, column=4, padx=3)
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
        var = tk.BooleanVar(value=False)
        row = ttk.Frame(inner, style="Card.TFrame", padding=(2, 2))
        row.grid(row=index, column=0, sticky="ew")
        row.columnconfigure(2, weight=1)
        display = item.get("display_path") or item.get("path") or "(generated only if checked)"
        ttk.Checkbutton(row, variable=var).grid(row=0, column=0, sticky="nw", padx=(0, 4))
        ttk.Label(row, text=_candidate_role(item), style="Card.TLabel", width=22).grid(row=0, column=1, sticky="nw", padx=(0, 8))
        ttk.Label(row, text=str(display), style="Muted.TLabel", wraplength=720).grid(row=0, column=2, sticky="nw")
        ttk.Label(row, text=old._format_size(item.get("size_bytes")), style="Muted.TLabel", width=10).grid(row=0, column=3, sticky="ne", padx=(8, 0))
        file_rows.append({"item": item, "var": var, "row": row, "visible": True})
    inner.columnconfigure(0, weight=1)

    mode_var = tk.StringVar(value="Autonomous")
    filenames_box = ScrolledText(settings_tab, wrap="none", height=6)
    if service == "scilink":
        mode_row = ttk.Frame(settings_tab, style="Card.TFrame")
        mode_row.pack(fill="x", pady=(0, 8))
        ttk.Label(mode_row, text="SciLink mode", style="Card.TLabel").pack(side="left")
        ttk.Combobox(mode_row, textvariable=mode_var, values=("Autonomous", "Co-pilot"), state="readonly", width=18).pack(side="left", padx=(8, 0))
    ttk.Label(settings_tab, text="Requested filenames (optional, one per line)", style="Card.TLabel").pack(anchor="w")
    filenames_box.pack(fill="x", pady=(2, 8))
    details_box = ScrolledText(settings_tab, wrap="word", height=20)
    details_box.pack(fill="both", expand=True)
    details_box.insert("1.0", cleanup._service_details_text(service, cfg))
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
        summary_var.set(
            f"Selected {len(selected)} of {len(file_rows)} item(s), {old._format_size(total)}. "
            f"Visible after filter: {len(visible_rows())}."
        )
        request_box.configure(state="normal")
        request_box.delete("1.0", "end")
        request_box.insert("1.0", json.dumps(_request_json(source, selected_items(), requested_names(), service_name), ensure_ascii=False, indent=2))
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
    status_var = tk.StringVar(value="Nothing starts until you approve. Prompt is editable, and files are unchecked until explicitly selected.")
    ttk.Label(footer, text="Nothing starts until you approve.", textvariable=status_var, style="Muted.TLabel").pack(side="left", fill="x", expand=True)

    def start() -> None:
        exact_prompt = prompt_box.get("1.0", "end-1c")
        if not exact_prompt.strip():
            messagebox.showwarning("Prompt required", f"Enter the {service_name} prompt before starting.", parent=win)
            return
        try:
            if service == "scilink":
                job = minimal._write_minimal_scilink_job(source, exact_prompt, selected_items(), cfg, mode=mode_var.get(), requested_filenames=requested_names())
                win.destroy()
                _launch_scilink_transaction(source, job, cfg)
            else:
                job = _write_exact_edison_job(source, exact_prompt, selected_items(), cfg, requested_names())
                win.destroy()
                _launch_edison_transaction(source, job)
        except Exception as exc:
            messagebox.showerror(f"Could not start {service_name}", f"{exc}\n\n{traceback.format_exc()}", parent=win)

    ttk.Button(footer, text=f"Start new {service_name} job", style="Accent.TButton", command=start).pack(side="right", padx=(6, 0))
    ttk.Button(footer, text="Cancel", command=win.destroy).pack(side="right")
    return win


def open_transaction_chat(source: Any) -> Any:
    tk, ttk, ScrolledText = _g("tk"), _g("ttk"), _g("ScrolledText")
    gui = _gui_from(source)
    win = tk.Toplevel(gui)
    win.title(f"FaradaIQ Studio {VERSION} - Scientific AI transactions")
    win.geometry("1080x780")
    outer = ttk.Frame(win, style="Card.TFrame", padding=(12, 12))
    outer.pack(fill="both", expand=True, padx=10, pady=10)
    ttk.Label(outer, text="Scientific AI transaction composer", style="Section.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text="Each action creates a new independent persistent terminal. Ask LLM is text-only and never receives experimental files.",
        style="Muted.TLabel", wraplength=1000,
    ).pack(anchor="w", pady=(2, 8))
    composer = ScrolledText(outer, wrap="word", height=25)
    composer.pack(fill="both", expand=True)
    row = ttk.Frame(outer, style="Card.TFrame")
    row.pack(fill="x", pady=(8, 0))

    def text() -> str:
        return composer.get("1.0", "end-1c")

    ttk.Button(row, text="Ask SciLink", style="Accent.TButton", command=lambda: show_analysis_review(source, "scilink", prompt_override=text())).pack(side="left", padx=(0, 6))
    if _edison_enabled():
        ttk.Button(row, text="Ask Edison Scientific", command=lambda: show_analysis_review(source, "edison", prompt_override=text())).pack(side="left", padx=(0, 6))
    if _llm_tools_enabled():
        def ask_llm() -> None:
            prompt = text()
            if not prompt.strip():
                box = _g("messagebox")
                if box:
                    box.showwarning("Prompt required", "Enter the LLM prompt before starting.", parent=win)
                return
            _launch_llm_transaction(source, prompt)
        ttk.Button(row, text="Ask LLM (text only)", command=ask_llm).pack(side="left", padx=(0, 6))
    ttk.Button(row, text="Clear", command=lambda: composer.delete("1.0", "end")).pack(side="left")
    ttk.Button(row, text="Close", command=win.destroy).pack(side="right")
    return win


def _active_gui_llm_secret(settings: Mapping[str, Any] | None = None) -> str:
    """Resolve the active GUI provider credential without writing it to disk."""
    settings = dict(settings or {})
    llm = dict(settings.get("llm") or {})
    provider_name = str(llm.get("provider") or _g("_FARADAIQ_SELECTED_LLM_PROVIDER", "") or os.environ.get("FARADAIQ_LLM_PROVIDER", "Ollama"))
    provider_key = provider_name.strip().lower()
    candidates_fn = _g("_v40_provider_candidates")
    if callable(candidates_fn):
        try:
            candidates = list(candidates_fn(include_unavailable=True) or [])
            candidate = next((c for c in candidates if str(c.get("name") or "").strip().lower() == provider_name.strip().lower()), None)
            if candidate is None:
                candidate = next((c for c in candidates if c.get("available")), None)
            if candidate and candidate.get("api_key"):
                return str(candidate.get("api_key"))
        except Exception:
            pass
    if "ollama" in provider_key:
        return str(os.environ.get("SCILINK_API_KEY") or os.environ.get("OLLAMA_API_KEY") or os.environ.get("OPENAI_API_KEY") or "ollama")
    return str(
        os.environ.get("SCILINK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LITELLM_KEY")
        or os.environ.get("AI_INCUBATOR_API_KEY")
        or os.environ.get("CUSTOM_LLM_API_KEY")
        or ""
    )


def _scilink_process_environment(job: Mapping[str, Any]) -> dict[str, str]:
    """Build the process-only environment used by the new SciLink terminal."""
    env = dict(os.environ)
    settings = dict(job.get("settings") or {})
    key = _active_gui_llm_secret(settings)
    if key:
        key.encode("ascii")
        env["SCILINK_API_KEY"] = key
        env["SCILINK_PROXY_API_KEY"] = key
    llm = dict(settings.get("llm") or {})
    provider = str(llm.get("provider") or "")
    model = str(llm.get("model") or "")
    base_url = str(llm.get("base_url") or "")
    if provider:
        env["FARADAIQ_LLM_PROVIDER"] = provider
    if model:
        env["FARADAIQ_LLM_MODEL"] = model
    if base_url:
        env["FARADAIQ_LLM_BASE_URL"] = base_url
    return env


def _launch_scilink_transaction(source: Any, job: Mapping[str, Any], cfg: Mapping[str, Any]) -> None:
    """Open a new persistent SciLink console with active GUI credentials."""
    import faradaiq.v239_scilink_edison as old
    gui = _gui_from(source)
    bat = old._write_scilink_launcher(job, cfg)
    try:
        env = _scilink_process_environment(job)
        if os.name == "nt":
            subprocess.Popen(
                ["cmd.exe", "/k", str(bat)],
                cwd=str(Path(job["job_dir"])),
                env=env,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        else:
            script = Path(__file__).resolve().parent.parent / "faradaiq_scilink_cli_job.py"
            subprocess.Popen([sys.executable, str(script), "--job-dir", str(job["job_dir"])], cwd=str(job["job_dir"]), env=env)
        try:
            gui.set_status(f"New SciLink CLI session opened: {job.get('job_id')}")
        except Exception:
            pass
    except UnicodeEncodeError:
        box = _g("messagebox")
        if box:
            box.showerror("SciLink credential error", "The active LLM/SciLink API key contains a non-ASCII character and cannot be used in an HTTP header.", parent=gui)
    except Exception as exc:
        box = _g("messagebox")
        if box:
            box.showerror("SciLink launch failed", f"{exc}\n\nLauncher preserved at:\n{bat}", parent=gui)


def _patch_scilink_credentials() -> None:
    import faradaiq_scilink_cli_job as cli
    previous = cli.prepare_scilink_environment
    if getattr(previous, "_v250_credentials", False):
        return

    def prepare(settings: Mapping[str, Any], base_env: Mapping[str, str] | None = None):
        env, report = previous(settings, base_env)
        llm = dict(settings.get("llm") or {})
        provider = str(report.get("provider_key") or "").lower()
        source = dict(base_env or os.environ)
        if provider == "ollama":
            key = source.get("SCILINK_API_KEY") or source.get("OLLAMA_API_KEY") or source.get("OPENAI_API_KEY") or "ollama"
        else:
            key = (
                source.get("SCILINK_API_KEY") or source.get("OPENAI_API_KEY") or source.get("LITELLM_KEY")
                or source.get("AI_INCUBATOR_API_KEY") or source.get("CUSTOM_LLM_API_KEY") or ""
            )
        if key:
            try:
                str(key).encode("ascii")
                env["SCILINK_API_KEY"] = str(key)
                env["SCILINK_PROXY_API_KEY"] = str(key)
                report.setdefault("header_fields_checked", []).append("SCILINK_API_KEY")
                report["scilink_api_key_source"] = (
                    "existing SCILINK_API_KEY" if source.get("SCILINK_API_KEY") else
                    "active Ollama/OpenAI-compatible GUI environment" if provider == "ollama" else
                    "active Main Studio LLM environment"
                )
            except UnicodeEncodeError:
                report.setdefault("errors", []).append("SCILINK_API_KEY contains a non-ASCII character and cannot be used as an HTTP header.")
                report["status"] = "blocked"
        elif provider != "bedrock":
            report.setdefault("errors", []).append("No SciLink/proxy API key could be resolved from the active Main Studio LLM environment.")
            report["status"] = "blocked"
        report["header_fields_checked"] = list(dict.fromkeys(report.get("header_fields_checked", [])))
        return env, report

    prepare._v250_credentials = True  # type: ignore[attr-defined]
    cli.prepare_scilink_environment = prepare
    cli.VERSION = VERSION


def _finite_trace_values(trace: Any) -> list[float]:
    """Return finite values that drive a Plotly trace's color mapping."""
    try:
        import numpy as np
    except Exception:
        return []
    candidates: list[Any] = []
    try:
        z = getattr(trace, "z", None)
        if z is not None:
            candidates.append(z)
    except Exception:
        pass
    try:
        marker = getattr(trace, "marker", None)
        color = getattr(marker, "color", None) if marker is not None else None
        if color is not None and not isinstance(color, (str, bytes)):
            candidates.append(color)
    except Exception:
        pass
    values: list[float] = []
    for candidate in candidates:
        try:
            arr = np.asarray(candidate, dtype=float).ravel()
            arr = arr[np.isfinite(arr)]
            values.extend(float(v) for v in arr.tolist())
        except Exception:
            continue
    return values


def _safe_range(values: Sequence[float]) -> tuple[float, float] | None:
    finite = [float(v) for v in values if math.isfinite(float(v))]
    if not finite:
        return None
    lo, hi = min(finite), max(finite)
    if hi <= lo:
        pad = max(abs(lo), 1.0) * 1e-9
        lo, hi = lo - pad, hi + pad
    return float(lo), float(hi)


def _figure_color_range_payload(fig: Any, metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Build range metadata for standalone HTML color-scale controls."""
    all_values: list[float] = []
    initial_values: list[float] = []
    frames: dict[str, list[float]] = {}
    for trace in list(getattr(fig, "data", ()) or ()):
        vals = _finite_trace_values(trace)
        initial_values.extend(vals)
        all_values.extend(vals)
    for frame in list(getattr(fig, "frames", ()) or ()):
        frame_values: list[float] = []
        for trace in list(getattr(frame, "data", ()) or ()):
            vals = _finite_trace_values(trace)
            frame_values.extend(vals)
            all_values.extend(vals)
        rng = _safe_range(frame_values)
        if rng is not None:
            frames[str(getattr(frame, "name", "") or "")] = [rng[0], rng[1]]
    global_range = _safe_range(all_values) or _safe_range(initial_values) or (0.0, 1.0)
    initial_range = _safe_range(initial_values) or global_range
    opts = dict(metadata.get("options") or {}) if isinstance(metadata, Mapping) else {}
    mode = str(opts.get("color_scale_mode") or COLOR_GLOBAL)
    if mode not in COLOR_MODES:
        mode = COLOR_GLOBAL
    try:
        manual_lo = float(str(opts.get("range_min", "")).strip())
        manual_hi = float(str(opts.get("range_max", "")).strip())
        if not (math.isfinite(manual_lo) and math.isfinite(manual_hi) and manual_hi > manual_lo):
            raise ValueError
    except Exception:
        manual_lo, manual_hi = global_range
    return {
        "schema": "faradaiq-standalone-color-range-controls-v253",
        "version": VERSION,
        "initial_mode": mode,
        "global_range": [global_range[0], global_range[1]],
        "initial_range": [initial_range[0], initial_range[1]],
        "manual_range": [manual_lo, manual_hi],
        "frame_ranges": frames,
    }


def _inject_standalone_color_controls(path: Path, payload: Mapping[str, Any]) -> None:
    """Inject live color-range controls into a generated Plotly HTML view."""
    if VERSION == "v255":
        from faradaiq.v255_browser_color import _inject_color_controls
        return _inject_color_controls(path, payload)
    try:
        html = path.read_text(encoding="utf-8")
    except Exception:
        return
    if "faradaiq-color-scale-toolbar-v253" in html:
        return
    payload_json = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
    toolbar = '''
<div id="faradaiq-color-scale-toolbar-v253" role="group" aria-label="Contour color scale">
  <strong>Color scale</strong>
  <select id="faradaiq-color-mode-v253" aria-label="Color scale mode">
    <option value="auto">Auto rescale each frame</option>
    <option value="global">Fixed global min/max</option>
    <option value="manual">User-defined min/max</option>
  </select>
  <label>Min <input id="faradaiq-color-min-v253" type="number" step="any"></label>
  <label>Max <input id="faradaiq-color-max-v253" type="number" step="any"></label>
  <button id="faradaiq-color-apply-v253" type="button">Apply</button>
  <span id="faradaiq-color-status-v253"></span>
</div>
<style>
#faradaiq-color-scale-toolbar-v253{position:sticky;top:0;z-index:10000;display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 12px;border-bottom:1px solid #bcc7d6;background:#f7f9fc;color:#17365d;font:14px/1.3 Arial,sans-serif;box-sizing:border-box}
#faradaiq-color-scale-toolbar-v253 select,#faradaiq-color-scale-toolbar-v253 input,#faradaiq-color-scale-toolbar-v253 button{font:inherit;padding:5px 7px;border:1px solid #9ba9bc;border-radius:3px;background:white;color:#17365d}
#faradaiq-color-scale-toolbar-v253 input{width:120px}
#faradaiq-color-scale-toolbar-v253 button{cursor:pointer;font-weight:600}
#faradaiq-color-status-v253{color:#4b5f78}
</style>
'''
    script = '''
<script id="faradaiq-color-scale-script-v253">
(function(){
  const payload = __PAYLOAD__;
  const modeEl = document.getElementById('faradaiq-color-mode-v253');
  const minEl = document.getElementById('faradaiq-color-min-v253');
  const maxEl = document.getElementById('faradaiq-color-max-v253');
  const applyEl = document.getElementById('faradaiq-color-apply-v253');
  const statusEl = document.getElementById('faradaiq-color-status-v253');
  const modeMap = {'Auto per frame':'auto','Global fixed (all frames)':'global','User-defined min/max':'manual'};
  let currentMode = modeMap[payload.initial_mode] || 'global';

  function finiteValues(obj){
    const values=[];
    function add(value){
      if(Array.isArray(value)){ value.forEach(add); return; }
      const n=Number(value); if(Number.isFinite(n)) values.push(n);
    }
    if(obj && obj.z!=null) add(obj.z);
    if(obj && obj.marker && obj.marker.color!=null && typeof obj.marker.color!=='string') add(obj.marker.color);
    return values;
  }
  function safeRange(values){
    if(!values.length) return null;
    let lo=Math.min.apply(null,values), hi=Math.max.apply(null,values);
    if(!(Number.isFinite(lo)&&Number.isFinite(hi))) return null;
    if(hi<=lo){ const pad=Math.max(Math.abs(lo),1)*1e-9; lo-=pad; hi+=pad; }
    return [lo,hi];
  }
  function format(n){
    if(!Number.isFinite(n)) return '';
    const a=Math.abs(n); return (a!==0 && (a>=1e5 || a<1e-3)) ? n.toExponential(6) : String(Number(n.toPrecision(8)));
  }
  function graph(){ return document.querySelector('.plotly-graph-div'); }
  function frameList(gd){
    try{return (gd._transitionData && gd._transitionData._frames) || [];}catch(_){return [];}
  }
  function setTraceRange(trace, mode, lo, hi){
    if(!trace) return;
    const hasZ = trace.z!=null || ['contour','heatmap','surface','mesh3d'].includes(String(trace.type||'').toLowerCase());
    const hasMarker = trace.marker && trace.marker.color!=null && typeof trace.marker.color!=='string';
    if(hasZ){
      if(mode==='auto'){ trace.zauto=true; delete trace.zmin; delete trace.zmax; }
      else { trace.zauto=false; trace.zmin=lo; trace.zmax=hi; }
    }
    if(hasMarker){
      trace.marker.cauto = mode==='auto';
      if(mode==='auto'){ delete trace.marker.cmin; delete trace.marker.cmax; }
      else { trace.marker.cmin=lo; trace.marker.cmax=hi; }
    }
  }
  function currentRange(gd){
    const values=[]; (gd.data||[]).forEach(t=>values.push.apply(values,finiteValues(t)));
    return safeRange(values) || payload.initial_range || payload.global_range;
  }
  function selectedRange(gd, mode){
    if(mode==='manual'){
      const lo=Number(minEl.value), hi=Number(maxEl.value);
      if(!(Number.isFinite(lo)&&Number.isFinite(hi)&&hi>lo)) throw new Error('Enter a finite minimum smaller than the maximum.');
      return [lo,hi];
    }
    if(mode==='global') return payload.global_range;
    return currentRange(gd);
  }
  function mutateFrames(gd, mode, fixedRange){
    frameList(gd).forEach(frame=>{
      const range = mode==='auto' ? (payload.frame_ranges[String(frame.name||'')] || null) : fixedRange;
      (frame.data||[]).forEach(trace=>{
        if(mode==='auto') setTraceRange(trace,'auto',null,null);
        else if(range) setTraceRange(trace,mode,range[0],range[1]);
      });
    });
  }
  function restyleCurrent(gd, mode, range){
    const zIdx=[], markerIdx=[];
    (gd.data||[]).forEach((trace,i)=>{
      if(trace && (trace.z!=null || ['contour','heatmap','surface','mesh3d'].includes(String(trace.type||'').toLowerCase()))) zIdx.push(i);
      if(trace && trace.marker && trace.marker.color!=null && typeof trace.marker.color!=='string') markerIdx.push(i);
    });
    const jobs=[];
    if(zIdx.length){
      jobs.push(Plotly.restyle(gd, mode==='auto' ? {'zauto':true,'zmin':null,'zmax':null} : {'zauto':false,'zmin':range[0],'zmax':range[1]}, zIdx));
    }
    if(markerIdx.length){
      jobs.push(Plotly.restyle(gd, mode==='auto' ? {'marker.cauto':true,'marker.cmin':null,'marker.cmax':null} : {'marker.cauto':false,'marker.cmin':range[0],'marker.cmax':range[1]}, markerIdx));
    }
    return Promise.all(jobs);
  }
  function syncInputs(mode){
    const disabled=mode!=='manual'; minEl.disabled=disabled; maxEl.disabled=disabled;
    if(mode==='global'){ minEl.value=format(payload.global_range[0]); maxEl.value=format(payload.global_range[1]); }
    else if(mode==='manual' && !minEl.value){ minEl.value=format(payload.manual_range[0]); maxEl.value=format(payload.manual_range[1]); }
  }
  function apply(silent){
    const gd=graph(); if(!gd || typeof Plotly==='undefined') return;
    currentMode=modeEl.value;
    try{
      const range=selectedRange(gd,currentMode);
      mutateFrames(gd,currentMode,currentMode==='auto'?null:range);
      restyleCurrent(gd,currentMode,range).then(()=>{
        if(!silent) statusEl.textContent = currentMode==='auto' ? 'Each slider frame rescales independently.' : 'Active range: '+format(range[0])+' to '+format(range[1]);
      });
      syncInputs(currentMode);
      try{localStorage.setItem('faradaiq-color-mode-v253',currentMode);}catch(_){}
    }catch(err){ statusEl.textContent=String(err && err.message || err); }
  }
  function start(){
    const gd=graph(); if(!gd || typeof Plotly==='undefined'){setTimeout(start,80);return;}
    try{ const saved=localStorage.getItem('faradaiq-color-mode-v253'); if(['auto','global','manual'].includes(saved)) currentMode=saved; }catch(_){}
    modeEl.value=currentMode;
    minEl.value=format(payload.manual_range[0]); maxEl.value=format(payload.manual_range[1]);
    syncInputs(currentMode); apply(true);
    modeEl.addEventListener('change',()=>apply(false));
    applyEl.addEventListener('click',()=>apply(false));
    minEl.addEventListener('keydown',e=>{if(e.key==='Enter')apply(false);});
    maxEl.addEventListener('keydown',e=>{if(e.key==='Enter')apply(false);});
    if(gd.on){
      gd.on('plotly_sliderchange',()=>setTimeout(()=>apply(true),0));
      gd.on('plotly_animated',()=>setTimeout(()=>apply(true),0));
    }
    const g=payload.global_range; statusEl.textContent='Global data range: '+format(g[0])+' to '+format(g[1]);
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start); else start();
})();
</script>
'''.replace('__PAYLOAD__', payload_json)
    if '<body>' in html:
        html = html.replace('<body>', '<body>'+toolbar, 1)
    else:
        html = toolbar + html
    if '</body>' in html:
        html = html.replace('</body>', script+'</body>', 1)
    else:
        html += script
    path.write_text(html, encoding='utf-8')

def _install_plotly_layout_and_color_modes() -> None:
    import faradaiq.multidim_v216 as md
    import faradaiq.v227_features as v227
    import faradaiq.v247_experience as v247

    md._V216_DEFAULT_OPTIONS.setdefault("color_scale_mode", COLOR_GLOBAL)
    original_options = md._v216_options
    if not getattr(original_options, "_v250_color_modes", False):
        def options(raw: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
            raw_map = dict(raw or {})
            raw_mode = str(raw_map.get("color_scale_mode") or "").strip()
            explicit_mode = raw_mode in COLOR_MODES
            out = original_options(raw)
            if explicit_mode:
                requested = raw_mode
            elif str(raw_map.get("range_mode") or "").startswith("Manual"):
                requested = COLOR_MANUAL
            elif str(raw_map.get("range_scope") or "") == "Per frame":
                requested = COLOR_AUTO_FRAME
            else:
                requested = str(out.get("color_scale_mode") or COLOR_GLOBAL).strip()
                if requested not in COLOR_MODES:
                    requested = COLOR_GLOBAL
            out["color_scale_mode"] = requested
            if requested == COLOR_AUTO_FRAME:
                out["range_scope"] = "Per frame"
                if explicit_mode:
                    out["range_mode"] = "Full data range"
            elif requested == COLOR_GLOBAL:
                out["range_scope"] = "Lock across cube"
                if explicit_mode:
                    out["range_mode"] = "Full data range"
            else:
                out["range_scope"] = "Lock across cube"
                out["range_mode"] = "Manual"
            return out
        options._v250_color_modes = True  # type: ignore[attr-defined]
        md._v216_options = options

    def animation_buttons(frame_duration: int = 280) -> list[dict[str, Any]]:
        return [dict(
            type="buttons", direction="right", x=0.01, xanchor="left", y=1.20, yanchor="top",
            showactive=False, pad={"r": 8, "t": 0}, name="faradaiq-playback-v253",
            buttons=[
                dict(label="▶ Play", method="animate", args=[None, {"fromcurrent": True, "mode": "immediate", "frame": {"duration": frame_duration, "redraw": True}, "transition": {"duration": 0}}]),
                dict(label="❚❚ Pause", method="animate", args=[[None], {"mode": "immediate", "frame": {"duration": 0, "redraw": False}, "transition": {"duration": 0}}]),
            ],
        )]
    v247._animation_buttons = animation_buttons

    def add_aspect_menu(fig: Any, preset: str = "Auto fit") -> None:
        # Reuse the same relayout semantics with a separate, non-overlapping row.
        try:
            current = [m for m in list(fig.layout.updatemenus or []) if getattr(m, "name", None) != "faradaiq-aspect-v227"]
        except Exception:
            current = []
        buttons = [
            dict(label="Auto", method="relayout", args=[{"autosize": True, "width": None, "height": None, "xaxis.scaleanchor": None, "xaxis.autorange": True, "yaxis.autorange": True}]),
            dict(label="Equal XY", method="relayout", args=[{"autosize": True, "width": None, "height": None, "xaxis.scaleanchor": "y", "xaxis.scaleratio": 1, "xaxis.autorange": True, "yaxis.autorange": True}]),
            dict(label="Square", method="relayout", args=[{"autosize": False, "width": 900, "height": 900, "xaxis.scaleanchor": None, "xaxis.autorange": True, "yaxis.autorange": True}]),
            dict(label="Rectangle 4:3", method="relayout", args=[{"autosize": False, "width": 1120, "height": 840, "xaxis.scaleanchor": None, "xaxis.autorange": True, "yaxis.autorange": True}]),
            dict(label="Wide 16:9", method="relayout", args=[{"autosize": False, "width": 1280, "height": 720, "xaxis.scaleanchor": None, "xaxis.autorange": True, "yaxis.autorange": True}]),
        ]
        current.append(dict(type="buttons", direction="right", x=0.17, xanchor="left", y=1.20, yanchor="top", showactive=True, pad={"r": 8, "t": 0}, buttons=buttons, name="faradaiq-aspect-v227"))
        fig.update_layout(updatemenus=current)
        p = str(preset or "Auto fit").lower()
        if "equal" in p:
            fig.update_layout(autosize=True, width=None, height=None); fig.update_xaxes(scaleanchor="y", scaleratio=1)
        elif "square" in p:
            fig.update_layout(autosize=False, width=900, height=900); fig.update_xaxes(scaleanchor=None)
        elif "16:9" in p or "wide" in p:
            fig.update_layout(autosize=False, width=1280, height=720); fig.update_xaxes(scaleanchor=None)
        elif "4:3" in p or "rectangle" in p:
            fig.update_layout(autosize=False, width=1120, height=840); fig.update_xaxes(scaleanchor=None)
        else:
            fig.update_layout(autosize=True, width=None, height=None); fig.update_xaxes(scaleanchor=None)
    v227._add_aspect_menu = add_aspect_menu

    current_write = md._v216_write_figure
    if not getattr(current_write, "_v250_layout", False):
        def write_figure(fig: Any, out: Path, meta: Mapping[str, Any]) -> Any:
            try:
                fig.update_layout(
                    title=dict(text=getattr(fig.layout.title, "text", None), x=0.5, xanchor="center", y=0.98, yanchor="top"),
                    margin=dict(
                        l=max(70, int(getattr(fig.layout.margin, "l", 0) or 0)),
                        r=max(50, int(getattr(fig.layout.margin, "r", 0) or 0)),
                        b=max(100, int(getattr(fig.layout.margin, "b", 0) or 0)),
                        t=max(165, int(getattr(fig.layout.margin, "t", 0) or 0)),
                    ),
                )
                for annotation in list(fig.layout.annotations or []):
                    if getattr(annotation, "yref", None) == "paper" and float(getattr(annotation, "y", 0) or 0) > 1.0:
                        annotation.y = 1.08
                for menu in list(fig.layout.updatemenus or []):
                    labels = [str(getattr(button, "label", "")) for button in list(getattr(menu, "buttons", []) or [])]
                    if any("Play" in label for label in labels):
                        menu.x, menu.y, menu.xanchor, menu.yanchor = 0.01, 1.20, "left", "top"
            except Exception:
                pass
            enriched = dict(meta)
            try:
                enriched["color_scale_mode"] = md._v216_options(meta.get("options") or {}).get("color_scale_mode")
            except Exception:
                pass
            result = current_write(fig, out, enriched)
            try:
                _inject_standalone_color_controls(Path(out), _figure_color_range_payload(fig, enriched))
            except Exception:
                pass
            return result
        write_figure._v250_layout = True  # type: ignore[attr-defined]
        md._v216_write_figure = write_figure

    Browser = _g("FaradaIQH5Browser")
    ttk, tk = _g("ttk"), _g("tk")
    if Browser is not None and ttk is not None and tk is not None and not getattr(Browser, "_v250_color_controls", False):
        previous_build = Browser._build_visible_ui_first
        def build(self: Any) -> None:
            previous_build(self)
            try:
                bar = getattr(self, "_v216_multidim_bar", None) or v227._find_viz_bar(self)
                if bar is None:
                    return
                current = md._v216_options(getattr(self, "_v222_visualization_options", None) or md._v222_load_persisted_options())
                self._v250_color_scale_mode_var = tk.StringVar(value=current.get("color_scale_mode", COLOR_GLOBAL))
                row = ttk.Frame(bar)
                row.pack(fill="x", pady=(4, 0))
                ttk.Label(row, text="Color scale behavior").pack(side="left", padx=(0, 5))
                ttk.Combobox(row, textvariable=self._v250_color_scale_mode_var, values=COLOR_MODES, state="readonly", width=28).pack(side="left", padx=(0, 8))
                ttk.Label(row, text="Auto rescales each slider frame; Global uses min/max across the cube; User uses the min/max fields above.", style="Muted.TLabel").pack(side="left", fill="x", expand=True)
                previous_collect = getattr(self, "_v216_multidim_options", None)
                if callable(previous_collect):
                    def collect() -> dict[str, Any]:
                        opts = dict(previous_collect())
                        opts["color_scale_mode"] = self._v250_color_scale_mode_var.get()
                        opts = md._v216_options(opts)
                        self._v222_visualization_options = dict(opts)
                        md._v222_save_persisted_options(opts)
                        return opts
                    self._v216_multidim_options = collect
            except Exception as exc:
                try:
                    self.log(f"Color-scale controls warning: {exc}")
                except Exception:
                    pass
        Browser._build_visible_ui_first = build
        Browser._v250_color_controls = True


def _install_per_technique_state() -> None:
    import faradaiq.browser_ui_v209 as bu
    previous = bu._set_mode_defaults
    if getattr(previous, "_v250_per_technique", False):
        return

    attrs = (
        "plot_xscale_var", "plot_yscale_var", "plot_grid_var", "cv_view_var",
        "plot_xmin_var", "plot_xmax_var", "plot_ymin_var", "plot_ymax_var",
    )

    def get_values(browser: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in attrs:
            var = getattr(browser, name, None)
            try:
                out[name] = var.get()
            except Exception:
                pass
        return out

    def apply_values(browser: Any, values: Mapping[str, Any]) -> None:
        for name, value in values.items():
            var = getattr(browser, name, None)
            try:
                var.set(value)
            except Exception:
                pass

    def defaults(browser: Any, mode: str) -> None:
        ml = mode.lower()
        try:
            browser.plot_mode_var.set(mode)
        except Exception:
            pass
        try:
            browser.plot_xscale_var.set("Log" if any(token in ml for token in ("bode", "drt")) else "Linear")
        except Exception:
            pass
        try:
            browser.plot_yscale_var.set("Log" if "bode magnitude" in ml else "Linear")
        except Exception:
            pass
        try:
            if mode == "CV / LSV":
                browser.cv_view_var.set("I-V")
            elif "current vs time" in ml:
                browser.cv_view_var.set("I-t")
            elif "potential vs time" in ml:
                browser.cv_view_var.set("E-t")
        except Exception:
            pass

    def set_mode_defaults(browser: Any, mode: str) -> None:
        states = getattr(browser, "_v250_technique_view_states", None)
        if states is None:
            states = {}
            browser._v250_technique_view_states = states
        old_key = getattr(browser, "_v250_active_view_key", None)
        if old_key:
            states[old_key] = get_values(browser)
        new_key = str(mode or "Auto")
        if new_key in states:
            try:
                browser.plot_mode_var.set(mode)
            except Exception:
                pass
            apply_values(browser, states[new_key])
        else:
            defaults(browser, mode)
            states[new_key] = get_values(browser)
        browser._v250_active_view_key = new_key

    set_mode_defaults._v250_per_technique = True  # type: ignore[attr-defined]
    bu._set_mode_defaults = set_mode_defaults


def _install_numpy_and_cv_reliability() -> None:
    import numpy as np
    import faradaiq.v228_intelligence as intel
    import faradaiq.multidim_v216 as md
    if not hasattr(np, "trapezoid"):
        np.trapezoid = np.trapz  # type: ignore[attr-defined]

    previous_scan = intel._scan_rate_v_s
    if not getattr(previous_scan, "_v250_numeric", False):
        def scan_rate(record: Mapping[str, Any]) -> Optional[float]:
            for key in ("scan_rate_v_s", "scan_rate_V_s", "scan_rate"):
                raw = record.get(key)
                if raw not in (None, ""):
                    try:
                        value = float(raw)
                        if math.isfinite(value) and value > 0:
                            return value
                    except Exception:
                        pass
            return previous_scan(record)
        scan_rate._v250_numeric = True  # type: ignore[attr-defined]
        intel._scan_rate_v_s = scan_rate

    previous_cv = intel.analyze_cv
    if getattr(previous_cv, "_v250_maps", False):
        return

    def analyze_cv(browser: Any) -> dict[str, Any]:
        result = dict(previous_cv(browser))
        metrics_path = Path(str(result.get("metrics_csv") or ""))
        if not metrics_path.exists():
            return result
        import pandas as pd
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        table = pd.read_csv(metrics_path)
        spatial = table.dropna(subset=["x_coord_m", "y_coord_m"])
        if len(spatial) < 4:
            result["map_note"] = "Fewer than four coordinate-bearing CV branches; metrics were saved but spatial contour maps were not generated."
            intel._atomic_json(Path(result["output_dir"]) / "cv_analysis.json", result)
            return result
        metrics = [
            ("anodic_peak_current_A", "Anodic peak current / A"),
            ("cathodic_peak_current_A", "Cathodic peak current / A"),
            ("anodic_peak_potential_V", "Anodic peak potential / V"),
            ("cathodic_peak_potential_V", "Cathodic peak potential / V"),
            ("charge_C", "Charge / C"),
        ]
        output_dir = Path(result["output_dir"])
        pages: list[str] = []
        for metric, label in metrics:
            data = spatial.dropna(subset=[metric])
            if len(data) < 4:
                continue
            branches = list(dict.fromkeys(str(v) for v in data["branch"].fillna("branch")))
            fig = make_subplots(rows=1, cols=len(branches), subplot_titles=branches)
            for col, branch in enumerate(branches, start=1):
                b = data[data["branch"].astype(str) == branch]
                x = b["x_coord_m"].to_numpy(float) * 1e6
                y = b["y_coord_m"].to_numpy(float) * 1e6
                z = b[metric].to_numpy(float)
                if len(b) >= 4:
                    gx, gy, gz, _regular = md._v216_grid_values(x, y, z, "Linear")
                    fig.add_trace(go.Contour(x=gx, y=gy, z=gz, contours_coloring="heatmap", colorbar=dict(title=label) if col == len(branches) else None, showscale=col == len(branches), name=branch), row=1, col=col)
                fig.add_trace(go.Scatter(x=x, y=y, mode="markers", marker=dict(size=7, color="white", line=dict(color="black", width=1)), text=[f"Point {p}" for p in b["point"]], name=f"{branch} measured", showlegend=False), row=1, col=col)
                fig.update_xaxes(title_text="X position / µm", row=1, col=col)
                fig.update_yaxes(title_text="Y position / µm", row=1, col=col, scaleanchor=f"x{col}" if col > 1 else "x", scaleratio=1)
            fig.update_layout(title=f"FaradaIQ {VERSION}: CV {label} branch maps", height=680, width=max(900, 650 * len(branches)), margin=dict(t=100, l=70, r=90, b=70))
            path = output_dir / f"contour_map_{metric}.html"
            fig.write_html(path, include_plotlyjs="cdn")
            pages.append(str(path))
        result["spatial_maps"] = pages
        result["branch_map_engine"] = "Plotly contour + measured points"
        result["numpy_trapezoid_compatibility"] = "np.trapz fallback active for NumPy 1.26"
        intel._atomic_json(output_dir / "cv_analysis.json", result)
        return result

    analyze_cv._v250_maps = True  # type: ignore[attr-defined]
    intel.analyze_cv = analyze_cv
    actions = getattr(intel, "ANALYSIS_ACTIONS", None)
    if isinstance(actions, dict) and "analyze_cv" in actions:
        try:
            actions["analyze_cv"]["function"] = analyze_cv
        except Exception:
            pass
    # Existing button lambdas resolve the module global at click time.


def _parallel_worker_dialog(parent: Any, current: int) -> Optional[int]:
    tk, ttk = _g("tk"), _g("ttk")
    if tk is None or ttk is None:
        return current
    result: dict[str, Optional[int]] = {"value": None}
    win = tk.Toplevel(parent)
    win.title("Parallel jobs")
    win.geometry("640x300")
    win.minsize(600, 270)
    try:
        win.transient(parent)
        win.grab_set()
    except Exception:
        pass
    outer = ttk.Frame(win, style="Card.TFrame", padding=(18, 16))
    outer.pack(fill="both", expand=True, padx=10, pady=10)
    ttk.Label(outer, text="How many jobs can FaradaIQ run in parallel?", style="Section.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text="Use a conservative value for large H5, Browser-catalog, or DRT jobs. Recommended: 2-4. The queue remains visible while jobs run.",
        style="Muted.TLabel", wraplength=560,
    ).pack(anchor="w", fill="x", pady=(6, 14))
    value = tk.IntVar(value=max(2, min(32, int(current or 2))))
    row = ttk.Frame(outer, style="Card.TFrame")
    row.pack(fill="x")
    ttk.Label(row, text="Parallel workers", style="Card.TLabel").pack(side="left", padx=(0, 10))
    spin = ttk.Spinbox(row, from_=2, to=32, textvariable=value, width=8)
    spin.pack(side="left")
    ttk.Label(row, text="2-4 is recommended", style="Muted.TLabel").pack(side="left", padx=(10, 0))
    footer = ttk.Frame(outer, style="Card.TFrame")
    footer.pack(fill="x", pady=(22, 0))

    def accept() -> None:
        try:
            result["value"] = max(2, min(32, int(value.get())))
        except Exception:
            result["value"] = max(2, int(current or 2))
        try:
            win.grab_release()
        except Exception:
            pass
        win.destroy()

    def cancel() -> None:
        result["value"] = None
        try:
            win.grab_release()
        except Exception:
            pass
        win.destroy()

    ttk.Button(footer, text="OK", style="Accent.TButton", command=accept).pack(side="right", padx=(6, 0))
    ttk.Button(footer, text="Cancel", command=cancel).pack(side="right")
    win.protocol("WM_DELETE_WINDOW", cancel)
    try:
        spin.focus_set()
        win.wait_window()
    except Exception:
        pass
    return result["value"]


def _install_parallel_dialog() -> None:
    try:
        import tkinter.simpledialog as simpledialog
    except Exception:
        return
    previous = simpledialog.askinteger
    if getattr(previous, "_v250_parallel", False):
        return
    def askinteger(title: str, prompt: str, *args: Any, **kwargs: Any):
        if str(title).strip().lower() == "parallel jobs":
            return _parallel_worker_dialog(kwargs.get("parent"), int(kwargs.get("initialvalue") or 2))
        return previous(title, prompt, *args, **kwargs)
    askinteger._v250_parallel = True  # type: ignore[attr-defined]
    simpledialog.askinteger = askinteger


def _apply_browser_mode_visibility(browser: Any) -> None:
    try:
        browser.smart_compare_use_llm_var.set(True)
    except Exception:
        pass
    if _edison_enabled():
        return
    for widget in _walk_widgets(browser):
        if "Edison Scientific" in _widget_text(widget):
            try:
                widget.destroy()
            except Exception:
                try:
                    widget.pack_forget()
                except Exception:
                    try:
                        widget.grid_remove()
                    except Exception:
                        pass


def _install_browser_mode_visibility() -> None:
    Browser = _g("FaradaIQH5Browser")
    if Browser is None or getattr(Browser, "_v250_mode_visibility", False):
        return
    previous = Browser._build_visible_ui_first
    def build(self: Any) -> None:
        previous(self)
        def apply() -> None:
            _apply_browser_mode_visibility(self)
        for delay in (25, 250, 750, 1800, 3500):
            try:
                self.after(delay, apply)
            except Exception:
                pass
        try:
            for widget in _walk_widgets(self):
                if widget.winfo_class() == "TNotebook":
                    widget.bind("<<NotebookTabChanged>>", lambda _event: self.after(25, apply), add="+")
        except Exception:
            pass
    Browser._build_visible_ui_first = build
    Browser._v250_mode_visibility = True


def _install_startup_selector() -> None:
    """Apply terminal-selected service visibility after the Main Studio UI is built."""
    Session = _g("SessionReaderGUI")
    if Session is None or getattr(Session, "_v250_terminal_service_mode", False):
        return
    previous = Session._build_ui
    def build(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = previous(self, *args, **kwargs)
        def finish() -> None:
            try:
                _show_startup_selector(self)
            except Exception:
                pass
            try:
                if hasattr(self, "_v244_resource_var"):
                    self._v245_resource_var = self._v244_resource_var
                    self._v250_resource_var = self._v244_resource_var
            except Exception:
                pass
        for delay in (0, 100, 500, 1500, 3000, 5200, 8200):
            try:
                self.after(delay, finish)
            except Exception:
                pass
        return result
    Session._build_ui = build
    Session._v250_terminal_service_mode = True

def _normalize_titles() -> None:
    for cls_name in ("SessionReaderGUI", "FaradaIQH5Browser", "FaradaIQDataBrowser"):
        cls = _g(cls_name)
        if cls is None or getattr(cls, "_v250_title_patch", False):
            continue
        old = getattr(cls, "title", None)
        if not callable(old):
            continue
        def title(self: Any, text: Any = None, _old=old):
            if text is None:
                return _old(self)
            return _old(self, re.sub(r"v\d+(?:\.\d+)?", VERSION, str(text)))
        cls.title = title
        cls.wm_title = title
        cls._v250_title_patch = True


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE, _REVIEW_INSTALLED
    _CORE = core
    _set_ai_mode(_ai_mode())

    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup
    import faradaiq.v245_queue_scilink as minimal
    import faradaiq.v247_experience as v247
    import faradaiq.v228_intelligence as intel
    import faradaiq.multidim_v216 as md

    for module in (old, cleanup, minimal, v247, intel, md):
        try:
            module.VERSION = VERSION
        except Exception:
            pass

    # Universal review with the same filters for SciLink and Edison.
    old.show_analysis_review = show_analysis_review
    old._launch_scilink = _launch_scilink_transaction
    old.configure_main_ai_toolbar = _configure_main_ai_toolbar
    cleanup.configure_main_ai_toolbar = _configure_main_ai_toolbar
    cleanup.show_analysis_review = show_analysis_review
    minimal.show_analysis_review = show_analysis_review
    v247._enhanced_scilink_review = show_analysis_review
    for key in ("_v250_show_analysis_review", "_v247_show_analysis_review", "_v245_show_analysis_review", "_v244_show_analysis_review", "_v239_show_analysis_review"):
        core[key] = show_analysis_review

    # Edison visibility is now controlled by the startup mode selector.
    cleanup.edison_enabled = _edison_enabled
    cleanup.launch_mode_label = _launch_mode_label

    _patch_scilink_credentials()
    _install_plotly_layout_and_color_modes()
    _install_per_technique_state()
    _install_numpy_and_cv_reliability()
    _install_parallel_dialog()
    _install_browser_mode_visibility()
    _install_startup_selector()
    _normalize_titles()

    core["_v250_open_transaction_chat"] = open_transaction_chat
    core["_v250_ai_mode"] = _ai_mode
    core["_v250_color_modes"] = COLOR_MODES
    _REVIEW_INSTALLED = True

# ---- FaradaIQ Studio v291 public release identity ----
# Keep the historical implementation module while generated UI payloads report the active release.
VERSION = "v292"
# ---- End FaradaIQ Studio v291 public release identity ----

# Resolve standalone payload identity from the installed package release.
# This keeps direct imports deterministic instead of depending on a later
# release-integrity installer having already mutated this historical module.
try:
    from . import APP_VERSION as _ACTIVE_PACKAGE_VERSION
    if str(_ACTIVE_PACKAGE_VERSION or '').startswith('v'):
        VERSION = str(_ACTIVE_PACKAGE_VERSION)
except Exception:
    pass
