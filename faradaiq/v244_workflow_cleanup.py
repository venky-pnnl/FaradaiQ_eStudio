"""FaradaIQ Studio v273 inherited workflow cleanup and performance patch.

This release keeps LLM use text-only, routes AI Chat only through SciLink CLI,
lets the launcher enable/disable Edison consistently, shortens service prompts,
preserves active jobs during completed-session restore, adds resource status,
compacts the parser/DRT strip, and debounces Browser previews.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import re
import shutil
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping

VERSION = "v273"
_CORE: MutableMapping[str, Any] = {}
_OLD: Any = None
_OLD_SERVICE_SETTINGS: Any = None


def _g(name: str, default: Any = None) -> Any:
    return _CORE.get(name, default)


def edison_enabled() -> bool:
    raw = str(os.environ.get("FARADAIQ_EDISON_ENABLED", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled", "scilink_only"}


def launch_mode_label() -> str:
    return "SciLink + Edison Scientific" if edison_enabled() else "SciLink only"


def _gui_from(source: Any) -> Any:
    return getattr(source, "gui", source)


def _analysis_root(source: Any) -> Path:
    return Path(_OLD._analysis_root(source)).resolve()


def _canonical_json(source: Any) -> Path | None:
    try:
        _h5, js = _OLD._canonical_pair(source)
        if js and Path(js).exists():
            return Path(js).resolve()
    except Exception:
        pass
    root = _analysis_root(source)
    patterns = (
        "*_faradaiq_bundle.json",
        "*_canonical*.json",
        "_faradaiq_browser_catalog*.json",
        "*.faradaiq_session.json",
    )
    found: list[Path] = []
    for pattern in patterns:
        try:
            found.extend(p for p in root.rglob(pattern) if p.is_file())
        except Exception:
            pass
    if not found:
        return None
    try:
        return max(found, key=lambda p: p.stat().st_mtime).resolve()
    except Exception:
        return found[0].resolve()


def _clean_question(text: Any) -> str:
    value = str(text or "").strip()
    if not value:
        return (
            "Analyze the electrochemical data across all available points and conditions. "
            "Report the quantitative method, assumptions, units, uncertainty, data-quality "
            "limitations, and scientifically justified conclusions."
        )
    for marker in (
        "FARADAIQ DATA STRUCTURE (TEXT ONLY)",
        "DATA-SHARING DEFAULT",
        "Checked data roles",
        "Required behavior",
    ):
        if marker in value:
            value = value.split(marker, 1)[0].strip()
    heading = "Scientific question and requested analysis"
    if heading in value:
        value = value.split(heading, 1)[1]
        value = re.sub(r"^[\s\-:]+", "", value).strip()
    # Strip old release/service header if present.
    value = re.sub(
        r"^FaradaIQ Studio v\d+\s*[—-]\s*(?:SciLink|Edison Scientific) analysis request\s*",
        "",
        value,
        flags=re.I,
    ).strip()
    return value[:16000]


def _dataset_name(source: Any) -> str:
    try:
        return str(_OLD._base_name(_gui_from(source)) or "FaradaIQ dataset")
    except Exception:
        return "FaradaIQ dataset"


def build_scilink_prompt(source: Any, question: Any) -> str:
    q = _clean_question(question)
    root = _analysis_root(source)
    js = _canonical_json(source)
    json_line = str(js) if js else "Not found. Inspect the local analysis folder for the current FaradaIQ JSON map."
    return f"""ASK SCILINK

Scientific question
{q}

Local data location
Folder: {root}
FaradaIQ JSON map: {json_line}

Analysis requirements
Analyze every relevant point, cycle, and condition needed to answer the question. For any calculated parameter, report the method, equation, inputs, units, assumptions, fit range, uncertainty, exclusions, and limitations. Do not estimate values when required inputs are missing. Save scripts, tables, figures, and the final summary in the SciLink job folder.
""".strip()


def build_edison_prompt(source: Any, question: Any) -> str:
    q = _clean_question(question)
    root = _analysis_root(source)
    js = _canonical_json(source)
    return f"""ASK EDISON SCIENTIFIC

Research question
{q}

Purpose
Find the most relevant scientific literature, precedent, accepted equations, and methodological limits needed to answer the question or interpret the electrochemical study.

Local dataset reference
Dataset: {_dataset_name(source)}
Folder: {root}
FaradaIQ JSON map: {str(js) if js else 'not found'}

Access rule
These Windows paths are references only. Edison cannot read local files unless the user explicitly checks files in the Files to Share tab. All file checkboxes start unchecked.

Response requirements
Answer the question directly, provide traceable citations and DOI values where available, distinguish literature evidence from inference, and do not claim to have analyzed experimental curves when no files were uploaded.
""".strip()


def _service_settings(cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    original = _OLD_SERVICE_SETTINGS
    if not callable(original):
        # Keep the settings reader usable for diagnostics and preflight tests
        # even when this release module is imported before the core installer.
        try:
            from . import v239_scilink_edison as legacy
            original = legacy._service_settings
        except Exception as exc:
            raise RuntimeError("Original SciLink/Edison settings provider is unavailable") from exc
    base = dict(original(cfg))
    base.update({
        "launch_mode": launch_mode_label(),
        # Edison/FutureHouse remains available to SciLink in every launch mode.
        # The direct Ask Edison button is controlled separately.
        "direct_edison_enabled": edison_enabled(),
        "edison_enabled": True,
        "edison_available_to_scilink": True,
        "edison_client_package": "edison-client>=0.15.0",
        "edison_import_name": "edison_client",
        "edison_authentication": "EDISON_API_KEY or explicit api_key",
        "edison_platform": "https://platform.edisonscientific.com/",
        "edison_profile": "https://platform.edisonscientific.com/profile",
        "edison_docs": "https://docs.edisonscientific.com/edison-client",
        "edison_base_url": "https://platform.edisonscientific.com/",
        "edison_upload_url": "",
        "edison_supported_jobs": ["LITERATURE", "LITERATURE_HIGH", "ANALYSIS", "PRECEDENT", "MOLECULES"],
    })
    if not edison_enabled():
        base["direct_edison_disabled_reason"] = "The separate Ask Edison button is hidden by launch mode; Edison remains available inside SciLink."
    return base


def _service_details_text(service: str, cfg: Mapping[str, Any]) -> str:
    settings = _service_settings(cfg)
    llm = settings.get("llm") or {}
    if service == "scilink":
        return (
            "SciLink CLI route\n"
            "-----------------\n"
            f"Launch mode: {settings.get('launch_mode')}\n"
            f"LLM provider: {llm.get('provider', '')}\n"
            f"Model: {llm.get('model', '')}\n"
            f"Base URL: {llm.get('base_url', '')}\n"
            f"LabRIC MCP cloud context: {'enabled' if settings.get('scilink_labric_cloud_context_enabled') or settings.get('labric_mcp_enabled') else 'disabled'}\n"
            "LabRIC MCP transport: stdio\n"
            "LabRIC MCP server name: labric\n"
            "LabRIC MCP command: package-local FaradaIQ bridge\n"
            f"LabRIC MCP URL: {settings.get('labric_mcp_url', '')}\n"
            f"Edison available to SciLink: {'yes' if settings.get('edison_api_key_present') else 'enabled, but API key not detected'}\n"
            f"Literature analysis for new SciLink jobs: {'enabled' if settings.get('scilink_literature_analysis_enabled', True) else 'disabled'}\n"
            f"Wait for user questions: {'enabled' if settings.get('scilink_wait_for_user_questions', True) else 'disabled'}\n"
            "HTML report auto-open: enabled\n\n"
            "Every Ask SciLink action starts a new independent CLI session. Co-pilot mode is used by default so questions wait for user input. AI Chat also routes only through SciLink CLI."
        )
    return (
        "Edison Scientific official client route\n"
        "--------------------------------------\n"
        "Python package: edison-client 0.15.0 or newer\n"
        "Import name: edison_client\n"
        "Import: from edison_client import EdisonClient, JobNames\n"
        "Authentication: EDISON_API_KEY or EdisonClient(api_key=...)\n"
        "Platform: https://platform.edisonscientific.com/\n"
        "Supported job families: Literature, Literature High, Analysis, Precedent, Molecules\n"
        "API operation: EdisonClient.run_tasks_until_done(...)\n"
        f"API key detected: {'yes' if settings.get('edison_api_key_present') else 'no'}\n\n"
        "Local Windows paths are not remotely accessible. Edison receives only the reviewed prompt and any files explicitly checked for upload."
    )


def show_analysis_review(source: Any, service: str, prompt_override: str | None = None) -> Any:
    if service == "edison" and not edison_enabled():
        box = _g("messagebox")
        if box:
            box.showinfo(
                "Edison Scientific disabled",
                "This launch is in SciLink-only mode. Restart FaradaIQ and choose SciLink + Edison Scientific to enable Edison.",
                parent=_gui_from(source),
            )
        return None
    tk, ttk = _g("tk"), _g("ttk")
    ScrolledText = _g("ScrolledText")
    messagebox = _g("messagebox")
    gui = _gui_from(source)
    cfg = _OLD.load_sharing_settings()
    candidates = _OLD.collect_share_candidates(source, cfg)
    question = prompt_override if prompt_override and prompt_override.strip() else _OLD._get_prompt_text(source)
    prompt = build_scilink_prompt(source, question) if service == "scilink" else build_edison_prompt(source, question)
    service_name = "SciLink" if service == "scilink" else "Edison Scientific"

    win = tk.Toplevel(gui)
    win.title(f"FaradaIQ Studio {VERSION} — Review {service_name}")
    win.geometry("1180x800")
    outer = ttk.Frame(win, style="Card.TFrame", padding=(12, 12))
    outer.pack(fill="both", expand=True, padx=10, pady=10)
    ttk.Label(outer, text=f"Review {service_name} request", style="Section.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text="Edit the focused scientific prompt. Optional files start unchecked and are shared only after explicit selection.",
        style="Muted.TLabel",
        wraplength=1100,
    ).pack(anchor="w", pady=(2, 8))

    tabs = ttk.Notebook(outer)
    tabs.pack(fill="both", expand=True)
    prompt_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    files_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    settings_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    tabs.add(prompt_tab, text="Prompt")
    tabs.add(files_tab, text="Files to Share")
    tabs.add(settings_tab, text="Service Details")

    prompt_box = ScrolledText(prompt_tab, wrap="word")
    prompt_box.pack(fill="both", expand=True)
    prompt_box.insert("1.0", prompt)

    summary_var = tk.StringVar(value="Selected 0 files. Text prompt only.")
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
        display_path = item.get("display_path") or item.get("path") or "(generated after approval)"
        ttk.Checkbutton(inner, variable=var).grid(row=row, column=0, sticky="nw", padx=(0, 4), pady=2)
        ttk.Label(inner, text=str(item.get("role") or "Data file"), style="Card.TLabel").grid(row=row, column=1, sticky="nw", padx=(0, 8), pady=2)
        ttk.Label(inner, text=str(display_path), style="Muted.TLabel", wraplength=690).grid(row=row, column=2, sticky="nw", pady=2)
        ttk.Label(inner, text=_OLD._format_size(item.get("size_bytes")), style="Muted.TLabel").grid(row=row, column=3, sticky="ne", padx=(8, 0), pady=2)
    inner.columnconfigure(2, weight=1)

    def refresh_summary(*_args: Any) -> None:
        selected = [item for item, var in file_vars if var.get()]
        total = sum(int(item.get("size_bytes") or 0) for item in selected)
        summary_var.set(f"Selected {len(selected)} of {len(file_vars)} item(s), {_OLD._format_size(total)}. All items were unchecked when opened.")

    for _item, var in file_vars:
        try:
            var.trace_add("write", refresh_summary)
        except Exception:
            pass
    refresh_summary()

    detail_box = ScrolledText(settings_tab, wrap="word")
    detail_box.pack(fill="both", expand=True)
    detail_box.insert("1.0", _service_details_text(service, cfg))
    detail_box.configure(state="disabled")

    footer = ttk.Frame(outer, style="Card.TFrame")
    footer.pack(fill="x", pady=(8, 0))
    status_var = tk.StringVar(value="Ready for review.")
    ttk.Label(footer, textvariable=status_var, style="Muted.TLabel").pack(side="left", fill="x", expand=True)

    def start() -> None:
        selected = [dict(item) for item, var in file_vars if var.get()]
        final_prompt = prompt_box.get("1.0", "end").strip()
        if not final_prompt:
            messagebox.showwarning("Prompt required", "Enter the scientific question before starting.", parent=win)
            return
        status_var.set("Creating reviewed job...")
        try:
            job = _OLD._write_job(source, service, final_prompt, selected, cfg)
            win.destroy()
            if service == "scilink":
                _OLD._launch_scilink(source, job, cfg)
            else:
                _OLD._run_edison(source, job, cfg)
        except Exception as exc:
            messagebox.showerror(f"Could not start {service_name}", str(exc), parent=win)

    ttk.Button(footer, text=f"Start new {service_name} job", style="Accent.TButton", command=start).pack(side="right", padx=(6, 0))
    ttk.Button(footer, text="Cancel", command=win.destroy).pack(side="right")
    return win


def open_scilink_chat(source: Any) -> Any:
    tk, ttk = _g("tk"), _g("ttk")
    ScrolledText = _g("ScrolledText")
    gui = _gui_from(source)
    win = tk.Toplevel(gui)
    win.title(f"FaradaIQ Studio {VERSION} — SciLink CLI Chat")
    win.geometry("1020x720")
    outer = ttk.Frame(win, style="Card.TFrame", padding=(12, 12))
    outer.pack(fill="both", expand=True, padx=10, pady=10)
    ttk.Label(outer, text="AI Chat through SciLink CLI", style="Section.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text="AI Chat has no direct LLM or direct Edison route. Each submitted question opens the SciLink review window and starts a new independent SciLink CLI session after approval.",
        style="Muted.TLabel",
        wraplength=960,
    ).pack(anchor="w", pady=(2, 8))
    context = ScrolledText(outer, wrap="word", height=8)
    context.pack(fill="x", pady=(0, 8))
    context.insert(
        "1.0",
        f"Dataset: {_dataset_name(source)}\nAnalysis folder: {_analysis_root(source)}\nFaradaIQ JSON map: {_canonical_json(source) or 'not found'}",
    )
    context.configure(state="disabled")
    ttk.Label(outer, text="Scientific question", style="Section.TLabel").pack(anchor="w")
    composer = ScrolledText(outer, wrap="word", height=20)
    composer.pack(fill="both", expand=True, pady=(3, 8))
    composer.insert("1.0", _clean_question(_OLD._get_prompt_text(source)))
    row = ttk.Frame(outer, style="Card.TFrame")
    row.pack(fill="x")
    ttk.Button(
        row,
        text="Ask SciLink",
        style="Accent.TButton",
        command=lambda: show_analysis_review(source, "scilink", prompt_override=composer.get("1.0", "end").strip()),
    ).pack(side="left", padx=(0, 6))
    ttk.Button(row, text="Clear", command=lambda: composer.delete("1.0", "end")).pack(side="left")
    ttk.Button(row, text="Close", command=win.destroy).pack(side="right")
    return win


def _walk_widgets(root: Any) -> Iterable[Any]:
    yield root
    try:
        for child in root.winfo_children():
            yield from _walk_widgets(child)
    except Exception:
        return


def _text(widget: Any) -> str:
    try:
        return str(widget.cget("text") or "").strip()
    except Exception:
        return ""


def _find_ai_root(gui: Any) -> Any | None:
    try:
        return _OLD._find_ai_root(gui)
    except Exception:
        return None


def configure_main_ai_toolbar(gui: Any) -> None:
    ttk = _g("ttk")
    root = _find_ai_root(gui)
    if root is None:
        return
    for child in list(root.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass
    outer = ttk.Frame(root, style="Card.TFrame")
    outer.pack(fill="x")
    line = ttk.Frame(outer, style="Card.TFrame")
    line.pack(fill="x")
    ttk.Label(line, text="AI workflow", style="Muted.TLabel").pack(side="left", padx=(0, 8))
    settings_fn = _g("_v37_show_llm_settings")
    ttk.Button(line, text="LLM settings", command=lambda: settings_fn(gui) if callable(settings_fn) else None).pack(side="left", padx=(0, 5))
    gen = _g("_v89_generate_llm_prompt") or _g("_v67_generate_ai_prompt_with_llm") or _g("_v45_generate_ai_prompt_with_llm")
    ttk.Button(line, text="Generate LLM-guided prompt", command=lambda: gen(gui) if callable(gen) else None).pack(side="left", padx=(0, 5))
    ttk.Button(line, text="Ask SciLink", style="Accent.TButton", command=lambda: show_analysis_review(gui, "scilink")).pack(side="left", padx=(0, 5))
    if edison_enabled():
        ttk.Button(line, text="Ask Edison Scientific", command=lambda: show_analysis_review(gui, "edison")).pack(side="left", padx=(0, 5))
    ttk.Button(line, text="AI Chat", command=lambda: open_scilink_chat(gui)).pack(side="left", padx=(0, 5))
    ttk.Label(
        outer,
        text=f"Launch mode: {launch_mode_label()}. AI Chat uses SciLink CLI only. LLM prompt generation remains text-only.",
        style="Muted.TLabel",
    ).pack(anchor="w", pady=(2, 0))
    root._v239_consolidated_ai_toolbar = True
    root._v244_ai_toolbar = True


def _remove_edison_widgets(root: Any) -> None:
    if edison_enabled():
        return
    for widget in list(_walk_widgets(root)):
        if "Edison" in _text(widget):
            try:
                if widget.winfo_class() in {"TButton", "Button", "TCheckbutton", "Checkbutton"}:
                    widget.destroy()
            except Exception:
                pass


def add_browser_handoff_bar(browser: Any) -> None:
    ttk = _g("ttk")
    old_bar = getattr(browser, "_v239_handoff_bar", None)
    try:
        if old_bar is not None and old_bar.winfo_exists():
            old_bar.destroy()
    except Exception:
        pass
    notebook = (
        getattr(browser, "_v226_center_notebook", None)
        or getattr(browser, "_v217_center_notebook", None)
        or getattr(browser, "_v216_center_notebook", None)
    )
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
    if edison_enabled():
        ttk.Button(bar, text="Ask Edison Scientific", command=lambda: show_analysis_review(browser, "edison")).pack(side="left", padx=(0, 5))
    ttk.Label(bar, text="Focused prompt; local folder and JSON map included; files unchecked by default.", style="Muted.TLabel").pack(side="left", padx=(8, 0))
    browser._v239_handoff_bar = bar
    browser._v239_handoff_bar_added = True


def _smart_prompt(browser: Any) -> str:
    try:
        value = str(browser.ai_compare_entry.get()).strip()
        if value:
            return value
    except Exception:
        pass
    try:
        indexes = list(browser.suggestion_list.curselection())
        if indexes:
            return str(browser.suggestion_list.get(indexes[0])).strip()
    except Exception:
        pass
    return "Analyze the selected FaradaIQ data and report the scientifically appropriate comparisons."


def declutter_browser(browser: Any) -> None:
    ttk = _g("ttk")
    # Keep Suggest. Remove legacy Ask AI/Ask Local and any stale service row.
    for widget in list(_walk_widgets(browser)):
        text = _text(widget)
        if text in {"Ask AI", "Ask Local"}:
            try:
                widget.destroy()
            except Exception:
                pass
        elif text == "Use LLM for Ask AI":
            try:
                widget.configure(text="Use LLM for Smart Suggestions")
            except Exception:
                pass
    old_row = getattr(browser, "_v239_smart_service_row", None)
    try:
        if old_row is not None and old_row.winfo_exists():
            old_row.destroy()
    except Exception:
        pass
    tab_ai = getattr(getattr(browser, "suggestion_list", None), "master", None)
    if tab_ai is not None:
        row = ttk.Frame(tab_ai, style="Card.TFrame")
        ttk.Button(
            row,
            text="Ask SciLink",
            style="Accent.TButton",
            command=lambda: show_analysis_review(browser, "scilink", prompt_override=_smart_prompt(browser)),
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        if edison_enabled():
            ttk.Button(
                row,
                text="Ask Edison Scientific",
                command=lambda: show_analysis_review(browser, "edison", prompt_override=_smart_prompt(browser)),
            ).pack(side="left", fill="x", expand=True)
        try:
            row.pack(fill="x", pady=(3, 4), before=browser.suggestion_list)
        except Exception:
            row.pack(fill="x", pady=(3, 4))
        browser._v239_smart_service_row = row
        browser._v239_smart_service_row_added = True
    _remove_edison_widgets(browser)
    try:
        browser.smart_compare_status_var.set(
            f"Smart Suggestions may use text-only LLM context. Custom requests route through {launch_mode_label()}."
        )
    except Exception:
        pass


# Resource footer -----------------------------------------------------------
class _MemStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _filetime_value(ft: Any) -> int:
    return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)


def _system_metrics(state: dict[str, Any], disk_path: Path) -> tuple[str, str, str]:
    cpu = "n/a"
    ram = "n/a"
    if os.name == "nt":
        try:
            idle = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
            current = (_filetime_value(idle), _filetime_value(kernel), _filetime_value(user))
            previous = state.get("cpu_times")
            state["cpu_times"] = current
            if previous:
                idle_delta = current[0] - previous[0]
                total_delta = (current[1] - previous[1]) + (current[2] - previous[2])
                if total_delta > 0:
                    cpu = f"{max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta))):.0f}%"
        except Exception:
            pass
        try:
            mem = _MemStatus()
            mem.dwLength = ctypes.sizeof(_MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
                ram = f"{int(mem.dwMemoryLoad)}%"
        except Exception:
            pass
    else:
        try:
            load = os.getloadavg()[0]
            cpus = os.cpu_count() or 1
            cpu = f"{min(100.0, 100.0 * load / cpus):.0f}%"
        except Exception:
            pass
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            avail = os.sysconf("SC_AVPHYS_PAGES")
            ram = f"{100.0 * (pages - avail) / pages:.0f}%"
        except Exception:
            pass
    try:
        probe = disk_path if disk_path.exists() else Path.cwd()
        usage = shutil.disk_usage(probe)
        disk = f"{100.0 * (usage.total - usage.free) / usage.total:.0f}%"
    except Exception:
        disk = "n/a"
    return cpu, ram, disk


def add_resource_footer(gui: Any) -> None:
    tk, ttk = _g("tk"), _g("ttk")
    frame = getattr(gui, "_v218_persistent_status_frame", None)
    if frame is None or getattr(gui, "_v244_resource_footer", False):
        return
    try:
        details = getattr(gui, "_v218_status_details_button", None)
        if details is not None:
            details.grid_configure(column=4)
        frame.columnconfigure(2, weight=1)
        var = tk.StringVar(value="CPU -- | RAM -- | Disk --")
        ttk.Label(frame, textvariable=var, style="Muted.TLabel", anchor="e").grid(row=0, column=3, sticky="e", padx=(10, 4))
        state: dict[str, Any] = {}

        def tick() -> None:
            try:
                raw = str(getattr(gui, "output_dir_var", None).get() or "") if hasattr(gui, "output_dir_var") else ""
                disk_path = Path(raw).expanduser() if raw else Path.cwd()
                cpu, ram, disk = _system_metrics(state, disk_path)
                var.set(f"CPU {cpu} | RAM {ram} | Disk {disk}")
                gui.after(2000, tick)
            except Exception:
                pass
        tick()
        gui._v244_resource_var = var
        gui._v244_resource_footer = True
    except Exception:
        pass


# Compact parser/DRT strip --------------------------------------------------
def compact_tools(gui: Any) -> None:
    ttk, tk = _g("ttk"), _g("tk")
    target = None
    for widget in _walk_widgets(gui):
        try:
            if widget.winfo_class() in {"TLabelframe", "Labelframe"} and _text(widget) == "pySECCM / DRT runners":
                target = widget
                break
        except Exception:
            pass
    if target is None or getattr(target, "_v244_compact", False):
        return
    # Remove only parser buttons/options; retain the AI toolbar and browser rows.
    for child in list(target.winfo_children()):
        texts = {_text(w) for w in _walk_widgets(child)}
        if texts.intersection({"CV", "EIS", "IV / Tafel", "OCP", "pyDRT", "All parser/DRT tools", "Dependencies check"}):
            try:
                child.destroy()
            except Exception:
                pass
        elif any("After export: create traditional" in t or t == "Save raw JSON copy" for t in texts):
            try:
                child.destroy()
            except Exception:
                pass
    row = ttk.Frame(target, style="Card.TFrame")
    try:
        row.pack(fill="x", before=target.winfo_children()[0], pady=(0, 2))
    except Exception:
        row.pack(fill="x", pady=(0, 2))
    ttk.Label(row, text="Analysis tool", style="Muted.TLabel").pack(side="left", padx=(0, 5))
    tool_var = tk.StringVar(value="All parser/DRT tools")
    values = ["CV", "EIS", "IV / Tafel", "OCP", "pyDRT", "All parser/DRT tools", "Dependencies check"]
    ttk.Combobox(row, textvariable=tool_var, values=values, state="readonly", width=23).pack(side="left", padx=(0, 5))

    def run_tool() -> None:
        value = tool_var.get()
        mapping = {"CV": "cv", "EIS": "eis", "IV / Tafel": "iv", "OCP": "ocp", "pyDRT": "drt"}
        if value in mapping:
            gui.open_pyseccm_tool(mapping[value])
        elif value == "Dependencies check":
            gui.check_install_pyseccm_dependencies()
        else:
            gui.open_seccm_plot_tools()

    ttk.Button(row, text="Open", style="Accent.TButton", command=run_tool).pack(side="left", padx=(0, 10))
    ttk.Checkbutton(row, text="Auto parser/DRT after export", variable=gui.auto_run_pyseccm_after_export_var).pack(side="left", padx=(0, 10))
    ttk.Checkbutton(row, text="Save raw JSON", variable=gui.include_raw_json_var).pack(side="left")
    target._v244_compact = True


# Browser navigation --------------------------------------------------------
def install_browser_speed_patch() -> None:
    Browser = _g("FaradaIQH5Browser") or _g("FaradaIQDataBrowser")
    if Browser is None or getattr(Browser, "_v244_speed_patch", False):
        return
    previous_preview = getattr(Browser, "preview_selected", None)
    if callable(previous_preview):
        def preview_selected(self: Any) -> None:
            try:
                pending = getattr(self, "_v244_preview_after", None)
                if pending is not None:
                    self.after_cancel(pending)
            except Exception:
                pass

            def run() -> None:
                self._v244_preview_after = None
                paths = self.selected_paths(plottable_only=True)
                if len(paths) != 1:
                    if len(paths) > 1:
                        try:
                            self.log(f"Selected group with {len(paths)} table(s). Preview is lazy; plotting starts only when requested.")
                        except Exception:
                            pass
                    return
                path = Path(paths[0])
                try:
                    key = (str(path.resolve()), int(path.stat().st_mtime_ns), 2000)
                except Exception:
                    key = (str(path), 0, 2000)
                cache: OrderedDict[Any, Any] = getattr(self, "_v244_preview_cache", OrderedDict())
                if key in cache:
                    df = cache.pop(key)
                    cache[key] = df
                    self.selected_path = path
                    self.selected_df = df
                    try:
                        self.log(f"Selected table from preview cache: {path.name} | rows={len(df)} cols={len(df.columns)}")
                    except Exception:
                        pass
                    return
                try:
                    df = _g("_v138_csv_read")(path, max_rows=2000)
                    cache[key] = df
                    while len(cache) > 24:
                        cache.popitem(last=False)
                    self._v244_preview_cache = cache
                    self.selected_path = path
                    self.selected_df = df
                    self.log(f"Selected table: {path.name} | rows={len(df)} cols={len(df.columns)}")
                except Exception as exc:
                    try:
                        self.log(f"Preview failed for {path.name}: {exc}")
                    except Exception:
                        pass
            try:
                self._v244_preview_after = self.after(140, run)
            except Exception:
                run()
        Browser.preview_selected = preview_selected
    Browser._v244_speed_patch = True


# Preserve running jobs -----------------------------------------------------
_ACTIVE_JOB_STATUSES = {"Running", "Stopping", "Queued", "Paused", "Interrupted", "Inspecting"}


def _merge_restored_jobs(model: Any, snapshot: list[Any], batch_payload: Mapping[str, Any] | None, BatchJob: Any) -> str | None:
    """Merge a restored completed session without hiding active queue entries."""
    current_by_id = {str(job.job_id): job for job in list(model.jobs)}
    for job in snapshot:
        job_id = str(job.job_id)
        if str(job.status) in _ACTIVE_JOB_STATUSES and job_id not in current_by_id:
            model.jobs.append(job)
            current_by_id[job_id] = job

    selected_id: str | None = None
    if isinstance(batch_payload, Mapping):
        restored = BatchJob.from_dict(batch_payload)
        selected_id = str(restored.job_id)
        existing = current_by_id.get(selected_id)
        if existing is None:
            model.jobs.append(restored)
            current_by_id[selected_id] = restored
        elif str(existing.status) not in _ACTIVE_JOB_STATUSES:
            idx = model.jobs.index(existing)
            model.jobs[idx] = restored
            current_by_id[selected_id] = restored

    # Deduplicate by job_id only. Never deduplicate by source path because a
    # completed job and a running job may intentionally use the same source.
    unique: list[Any] = []
    index_by_id: dict[str, int] = {}
    for job in model.jobs:
        job_id = str(job.job_id)
        if job_id not in index_by_id:
            index_by_id[job_id] = len(unique)
            unique.append(job)
            continue
        existing = unique[index_by_id[job_id]]
        if str(job.status) in _ACTIVE_JOB_STATUSES and str(existing.status) not in _ACTIVE_JOB_STATUSES:
            unique[index_by_id[job_id]] = job
    model.jobs[:] = unique
    return selected_id


def install_job_restore_patch() -> None:
    previous = _g("_v177_apply_full_session")
    BatchJob = _g("BatchJob")
    if not callable(previous) or BatchJob is None or _g("_v244_job_restore_installed"):
        return

    def apply_session(gui: Any, payload: Mapping[str, Any], open_browsers: bool = True) -> Any:
        model = (
            getattr(gui, "_v233_batch_model", None)
            or getattr(gui, "_v232_batch_model", None)
            or getattr(gui, "_v230_batch_model", None)
            or getattr(gui, "_v229_batch_model", None)
        )
        snapshot = list(model.jobs) if model is not None else []
        result = previous(gui, payload, open_browsers=open_browsers)
        if model is None:
            return result
        batch_payload = payload.get("batch_job") if isinstance(payload, Mapping) else None
        selected_id = _merge_restored_jobs(model, snapshot, batch_payload if isinstance(batch_payload, Mapping) else None, BatchJob)
        model.autosave_queue()
        model.refresh_window(select_job=selected_id)
        return result

    _CORE["_v177_apply_full_session"] = apply_session
    _CORE["_v244_job_restore_installed"] = True


def _normalize_titles() -> None:
    for cls_name in ("SessionReaderGUI", "FaradaIQH5Browser", "FaradaIQDataBrowser"):
        cls = _g(cls_name)
        if cls is None:
            continue
        old_title = getattr(cls, "title", None)
        if not callable(old_title) or getattr(cls, "_v244_title_patch", False):
            continue
        def title(self: Any, text: Any = None, _old=old_title) -> Any:
            if text is None:
                return _old(self)
            return _old(self, re.sub(r"v\d+(?:\.\d+)?", VERSION, str(text)))
        cls.title = title
        cls.wm_title = title
        cls._v244_title_patch = True


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE, _OLD, _OLD_SERVICE_SETTINGS
    _CORE = core
    import faradaiq.v239_scilink_edison as old
    _OLD = old
    _OLD_SERVICE_SETTINGS = old._service_settings
    old.VERSION = VERSION
    old._service_settings = _service_settings
    old.show_analysis_review = show_analysis_review
    old.open_consolidated_ai_chat = open_scilink_chat
    old.configure_main_ai_toolbar = configure_main_ai_toolbar
    old._add_browser_handoff_bar = add_browser_handoff_bar
    old._declutter_browser_llm = declutter_browser

    core["_v244_edison_enabled"] = edison_enabled
    core["_v244_build_scilink_prompt"] = build_scilink_prompt
    core["_v244_build_edison_prompt"] = build_edison_prompt
    core["_v244_show_analysis_review"] = show_analysis_review
    core["_v239_show_analysis_review"] = show_analysis_review
    core["_v244_open_scilink_chat"] = open_scilink_chat
    core["_v239_open_ai_chat"] = open_scilink_chat
    for name in ("_v93_open_ai_chat", "_v98_open_ai_chat", "_v100_open_ai_chat", "_v101_open_ai_chat", "_v104_open_ai_chat", "_v105_open_ai_chat"):
        core[name] = open_scilink_chat
    core["_v244_configure_main_ai_toolbar"] = configure_main_ai_toolbar
    core["_v239_configure_main_ai_toolbar"] = configure_main_ai_toolbar

    install_browser_speed_patch()
    install_job_restore_patch()
    _normalize_titles()

    Session = core.get("SessionReaderGUI")
    if Session is not None and not getattr(Session, "_v244_cleanup_installed", False):
        prev_init = Session.__init__
        def init(self: Any, *args: Any, _prev_init=prev_init, **kwargs: Any) -> None:
            _prev_init(self, *args, **kwargs)
            try:
                self.title(f"FaradaIQ eStudio {VERSION}")
            except Exception:
                pass
            def apply() -> None:
                try:
                    self.tk.call("wm", "title", self._w, f"FaradaIQ eStudio {VERSION}")
                except Exception:
                    pass
                configure_main_ai_toolbar(self)
                compact_tools(self)
                add_resource_footer(self)
                _remove_edison_widgets(self)
            for delay in (40, 180, 500, 1200, 2500, 5000, 8000):
                try:
                    self.after(delay, apply)
                except Exception:
                    pass
        Session.__init__ = init
        Session._v244_cleanup_installed = True

    Browser = core.get("FaradaIQH5Browser") or core.get("FaradaIQDataBrowser")
    if Browser is not None and not getattr(Browser, "_v244_cleanup_installed", False):
        prev_init = Browser.__init__
        def binit(self: Any, *args: Any, _prev_init=prev_init, **kwargs: Any) -> None:
            _prev_init(self, *args, **kwargs)
            def apply() -> None:
                add_browser_handoff_bar(self)
                declutter_browser(self)
                _remove_edison_widgets(self)
                try:
                    self.title(f"FaradaIQ Studio {VERSION} — Data Browser")
                except Exception:
                    pass
            for delay in (80, 300, 900, 1800, 3500):
                try:
                    self.after(delay, apply)
                except Exception:
                    pass
        Browser.__init__ = binit
        Browser._v244_cleanup_installed = True
