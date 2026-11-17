"""FaradaIQ Studio v273 post-export browser-cache automation.

Adds a default-enabled GUI option that starts canonical H5 refresh and hidden
browser-cache extraction after the export/parser transaction completes.  Work
runs off the Tk thread and exposes a clear Ready to open state in the main
status line and beside the checkbox.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

VERSION = "v273"
CHECKBOX_TEXT = "After export + parser runs: automatically build browser cache"


def _set_status(gui: Any, message: str) -> None:
    """Update the dedicated cache status without repainting duplicate rows."""
    text = str(message)
    try:
        current = str(gui.browser_cache_status_var.get() or "")
        if current != text:
            gui.browser_cache_status_var.set(text)
    except Exception:
        pass
    # The persistent footer reads browser_cache_status_var directly.  Mirroring
    # every progress fragment into the legacy general status row caused resize
    # churn and visible flashing.  Only publish terminal states there.
    terminal = any(token in text.lower() for token in ("ready to open", "failed", "could not start"))
    if terminal:
        try:
            current = str(gui.status_var.get() or "")
            if current != text:
                gui.status_var.set(text)
        except Exception:
            try:
                gui.set_status(text)
            except Exception:
                pass


def _walk(widget: Any):
    yield widget
    try:
        for child in widget.winfo_children():
            yield from _walk(child)
    except Exception:
        return


def _find_tools_frame(gui: Any) -> Optional[Any]:
    for widget in _walk(gui):
        try:
            if str(widget.cget("text") or "").strip() == "pySECCM / DRT runners":
                return widget
        except Exception:
            pass
    return None


def _add_cache_controls(gui: Any, ttk: Any, tk: Any) -> bool:
    if getattr(gui, "_v216_cache_controls_installed", False):
        return True
    if not hasattr(gui, "auto_build_browser_cache_after_parser_var"):
        gui.auto_build_browser_cache_after_parser_var = tk.BooleanVar(value=True)
    else:
        try:
            # This is a new v216 control. It is checked by default on every fresh
            # launch, while a loaded session may subsequently change it.
            gui.auto_build_browser_cache_after_parser_var.set(True)
        except Exception:
            pass
    if not hasattr(gui, "browser_cache_status_var"):
        gui.browser_cache_status_var = tk.StringVar(value="Browser cache: auto-build enabled; waiting for export")

    tools = _find_tools_frame(gui)
    if tools is None:
        return False
    row = ttk.Frame(tools, style="Card.TFrame")
    row.pack(fill="x", pady=(4, 0))
    cb = ttk.Checkbutton(
        row,
        text=CHECKBOX_TEXT,
        variable=gui.auto_build_browser_cache_after_parser_var,
    )
    cb.pack(side="left")
    ttk.Label(row, textvariable=gui.browser_cache_status_var, style="Muted.TLabel").pack(
        side="left", padx=(14, 0), fill="x", expand=True
    )

    def changed(*_args: Any) -> None:
        try:
            enabled = bool(gui.auto_build_browser_cache_after_parser_var.get())
        except Exception:
            enabled = True
        _set_status(
            gui,
            "Browser cache: auto-build enabled; waiting for export"
            if enabled
            else "Browser cache: automatic build disabled; it will prepare when the browser is opened",
        )

    try:
        gui.auto_build_browser_cache_after_parser_var.trace_add("write", changed)
    except Exception:
        pass
    gui._v216_cache_controls_installed = True
    gui._v216_cache_controls_row = row
    return True


def install(core: Dict[str, Any]) -> None:
    SessionReaderGUI = core.get("SessionReaderGUI")
    ttk = core.get("ttk")
    tk = core.get("tk")
    if SessionReaderGUI is None or ttk is None or tk is None:
        raise RuntimeError("FaradaIQ SessionReaderGUI/Tk classes are unavailable")
    if getattr(SessionReaderGUI, "_v216_post_export_cache_installed", False):
        return

    analysis_root_for_browser = core.get("_v64_analysis_root_for_browser")
    metadata_snapshot = core.get("_v130_gui_metadata_snapshot")
    build_worker = core.get("_v130_build_bundle_worker")
    extract_worker = core.get("_v130_extract_cache_worker")
    start_monitor = core.get("_v67_start_pyseccm_monitor")

    def trigger(gui: Any, reason: str = "after_export_and_parsers") -> None:
        try:
            enabled = bool(gui.auto_build_browser_cache_after_parser_var.get())
        except Exception:
            enabled = True
        if not enabled or not bool(getattr(gui, "_v216_post_cache_requested", False)):
            return
        if bool(getattr(gui, "_v216_post_cache_triggered", False)):
            return
        gui._v216_post_cache_triggered = True
        gui._v216_browser_cache_building = True
        try:
            root, base_name = analysis_root_for_browser(gui)
            root = Path(root).resolve()
        except Exception as exc:
            gui._v216_browser_cache_building = False
            _set_status(gui, f"Browser cache could not start: analysis output is unavailable ({exc})")
            return
        try:
            meta = metadata_snapshot(gui) if callable(metadata_snapshot) else {}
        except Exception:
            meta = {}
        progress: list[str] = []
        state: Dict[str, Any] = {"done": False, "result": None, "error": None}
        _set_status(gui, "Parsers finished. Building canonical H5 and browser cache in background…")

        def worker() -> None:
            try:
                if not callable(build_worker) or not callable(extract_worker):
                    raise RuntimeError("canonical bundle/cache worker functions are unavailable")
                info = build_worker(root, base_name, meta, reason, progress_queue=progress)
                h5_path = Path(str((info or {}).get("h5_path") or ""))
                if not h5_path.exists():
                    raise FileNotFoundError("canonical H5 was not produced")
                cache = extract_worker(h5_path, progress_queue=progress)
                result = dict(info or {})
                result.update(cache or {})
                result["h5_path"] = str(h5_path)
                state["result"] = result
            except Exception as exc:
                state["error"] = exc
            finally:
                state["done"] = True

        threading.Thread(target=worker, name="FaradaIQ-v216-browser-cache", daemon=True).start()
        seen = {"count": 0}

        def poll() -> None:
            try:
                while seen["count"] < len(progress):
                    msg = progress[seen["count"]]
                    seen["count"] += 1
                    _set_status(gui, f"Browser cache: {msg}")
            except Exception:
                pass
            if not state["done"]:
                try:
                    gui.after(250, poll)
                except Exception:
                    pass
                return
            gui._v216_browser_cache_building = False
            if state["error"] is not None:
                gui._v216_post_cache_triggered = False
                _set_status(gui, f"Browser cache build failed: {state['error']}")
                return
            result = state["result"] or {}
            gui._v216_browser_cache_info = result
            gui._v216_browser_cache_ready = True
            tables = result.get("n_tables", "?")
            h5_name = Path(str(result.get("h5_path") or "canonical H5")).name
            _set_status(gui, f"Browser cache ready to open: {h5_name}; {tables} table(s). Open Data browser + analysis.")

        try:
            gui.after(100, poll)
        except Exception:
            poll()

    # Expose for tests and for future UI actions.
    core["_v216_trigger_post_export_browser_cache"] = trigger

    previous_init = SessionReaderGUI.__init__

    def init(gui: Any, *args: Any, **kwargs: Any) -> None:
        previous_init(gui, *args, **kwargs)
        gui._v216_post_cache_requested = False
        gui._v216_post_cache_triggered = False
        gui._v216_browser_cache_ready = False
        for delay in (10, 80, 250, 700, 1600):
            try:
                gui.after(delay, lambda g=gui: _add_cache_controls(g, ttk, tk))
            except Exception:
                pass

    SessionReaderGUI.__init__ = init

    previous_export = SessionReaderGUI.export_outputs

    def export_outputs(gui: Any, *args: Any, **kwargs: Any):
        try:
            requested = bool(gui.auto_build_browser_cache_after_parser_var.get())
        except Exception:
            requested = True
        try:
            wait_for_parser = bool(gui.auto_run_pyseccm_after_export_var.get())
        except Exception:
            wait_for_parser = False
        gui._v216_post_cache_requested = requested
        gui._v216_post_cache_triggered = False
        gui._v216_browser_cache_ready = False
        gui._v216_post_cache_wait_for_parser = wait_for_parser
        if requested:
            _set_status(
                gui,
                "Browser cache: queued after export and parser completion"
                if wait_for_parser
                else "Browser cache: queued after export completion",
            )
        result = previous_export(gui, *args, **kwargs)

        # The export may run on the v177 background worker. When no parser monitor
        # starts, this watcher is the completion trigger. Parser transactions are
        # handled by the monitor wrapper below.
        grace = {"idle_checks": 0}

        def watch_export() -> None:
            thread = getattr(gui, "_v177_export_thread", None)
            if thread is not None and getattr(thread, "is_alive", lambda: False)():
                try:
                    gui.after(500, watch_export)
                except Exception:
                    pass
                return
            if not requested or bool(getattr(gui, "_v216_post_cache_triggered", False)):
                return
            if bool(getattr(gui, "_v216_parser_monitor_active", False)):
                return
            if wait_for_parser and grace["idle_checks"] < 6:
                grace["idle_checks"] += 1
                try:
                    gui.after(700, watch_export)
                except Exception:
                    pass
                return
            trigger(gui, "after_export_no_parser_or_parser_not_required")

        try:
            gui.after(500, watch_export)
        except Exception:
            pass
        return result

    SessionReaderGUI.export_outputs = export_outputs

    if callable(start_monitor):
        def monitor(gui: Any, progress_file: Any, total: Any):
            gui._v216_parser_monitor_active = True
            result = start_monitor(gui, progress_file, total)
            path = Path(progress_file)
            stable = {"count": 0}

            def poll_finished() -> None:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    text = ""
                if "FINISHED" in text.upper():
                    stable["count"] += 1
                else:
                    stable["count"] = 0
                if stable["count"] >= 3:
                    gui._v216_parser_monitor_active = False
                    if bool(getattr(gui, "_v216_post_cache_requested", False)):
                        trigger(gui, "after_export_and_parser_runs")
                    return
                try:
                    gui.after(900, poll_finished)
                except Exception:
                    pass

            try:
                gui.after(300, poll_finished)
            except Exception:
                pass
            return result

        core["_v67_start_pyseccm_monitor"] = monitor

    SessionReaderGUI._v216_post_export_cache_installed = True
