"""FaradaIQ Studio v273 responsive loading and session-banner refinements.

The compatibility module retained as loading_ui_v225 removes the duplicate live-status line from the session banner,
keeps session/tracking controls visible during long imports, and moves file
parsing off the Tk event thread.  The persistent footer remains the single
source of live loading status and elapsed time.
"""
from __future__ import annotations

import json
import queue
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

VERSION = "v273"


def _walk(widget: Any) -> Iterable[Any]:
    yield widget
    try:
        for child in widget.winfo_children():
            yield from _walk(child)
    except Exception:
        return


def _text(widget: Any) -> str:
    try:
        return str(widget.cget("text") or "")
    except Exception:
        return ""


def _textvariable(widget: Any) -> str:
    try:
        return str(widget.cget("textvariable") or "")
    except Exception:
        return ""


def _build_session_banner(parent: Any, title: str, ctx: Dict[str, Any], core: Dict[str, Any]) -> Any:
    """Create a responsive two-line session banner without a duplicate status row."""
    ttk = core["ttk"]
    try:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=(8, 4))
    except Exception:
        frame = ttk.Frame(parent, padding=(8, 4))
    root = parent.winfo_toplevel()
    for col in range(8):
        try:
            frame.columnconfigure(col, weight=1 if col == 0 else 0)
        except Exception:
            pass

    sid = str(ctx.get("session_id") or "unknown")
    full_folder = str(ctx.get("session_dir") or "")
    abbreviate = core.get("_v89_abbreviate_path")
    folder_short = abbreviate(full_folder, max_chars=150) if callable(abbreviate) else full_folder

    title_label = ttk.Label(frame, text=f"{title}: {sid}", style="Section.TLabel", anchor="w")
    title_label.grid(row=0, column=0, columnspan=2, sticky="ew", padx=(0, 14), pady=(0, 2))

    folder_label = ttk.Label(
        frame,
        text=f"Tracking only: {folder_short}",
        style="Muted.TLabel",
        anchor="w",
    )
    # The tracking path has its own full-width row.  It can no longer be squeezed
    # to 1 px by the session action buttons during resize or loading.
    folder_label.grid(row=1, column=0, columnspan=8, sticky="ew", padx=(0, 4), pady=(2, 0))
    try:
        folder_label.configure(cursor="hand2")
        folder_label.bind("<Button-1>", lambda _e=None, c=ctx: core["_v753_open_session_folder"](c))
    except Exception:
        pass

    ttk.Button(
        frame,
        text="Open tracking folder",
        command=lambda c=ctx: core["_v753_open_session_folder"](c),
    ).grid(row=0, column=6, sticky="e", padx=(4, 0), pady=(0, 2))
    ttk.Button(
        frame,
        text="Copy ID",
        command=lambda w=root, c=ctx: core["_v753_copy_session_id"](w, c),
    ).grid(row=0, column=7, sticky="e", padx=(4, 0), pady=(0, 2))

    # The persistent bottom footer is the only visible status line.  Keeping
    # status out of this banner prevents "Loading input..." from replacing or
    # visually covering the session name and tracking path.
    frame._v225_responsive_session_banner = True
    return frame


def _normalize_session_banner(gui: Any, core: Dict[str, Any], frame: Optional[Any] = None) -> bool:
    finder = core.get("_v177_find_live_session_banner_frame")
    if frame is None:
        try:
            frame = finder(gui) if callable(finder) else getattr(gui, "_v753_session_banner_frame", None)
        except Exception:
            frame = getattr(gui, "_v753_session_banner_frame", None)
    if frame is None:
        return False
    try:
        if not frame.winfo_exists():
            return False
    except Exception:
        return False

    status_var_name = ""
    try:
        status_var_name = str(gui.status_var)
    except Exception:
        pass

    # Remove only the nested duplicate status row.  Do not touch session,
    # tracking, autosave, or action widgets.
    for child in list(getattr(frame, "winfo_children", lambda: [])()):
        try:
            descendants = list(_walk(child))
            texts = {_text(w).strip() for w in descendants}
            vars_ = {_textvariable(w).strip() for w in descendants}
            is_duplicate_status = (
                _text(child).strip() == "Status:"
                or "Status:" in texts
                or (status_var_name and status_var_name in vars_ and child.winfo_class() in {"TFrame", "Frame"})
            )
            if is_duplicate_status and child.winfo_class() in {"TFrame", "Frame"}:
                child.destroy()
        except Exception:
            pass

    action_columns = {
        "New Session": 2,
        "Save Session": 3,
        "Load Session": 4,
        "Auto Save session": 5,
        "Open tracking folder": 6,
        "Copy ID": 7,
    }
    for child in list(getattr(frame, "winfo_children", lambda: [])()):
        text = _text(child).strip()
        try:
            if text.startswith("FaradaIQ launch session:") or text.startswith("FaradaIQ working session:"):
                child.grid_configure(row=0, column=0, columnspan=2, sticky="ew", padx=(0, 14), pady=(0, 2))
            elif text.startswith("Tracking only:"):
                child.grid_configure(row=1, column=0, columnspan=8, sticky="ew", padx=(0, 4), pady=(2, 0))
            elif text in action_columns:
                child.grid_configure(row=0, column=action_columns[text], sticky="e", padx=(4, 0), pady=(0, 2))
        except Exception:
            pass
    try:
        for col in range(8):
            frame.columnconfigure(col, weight=1 if col == 0 else 0)
        frame._v225_responsive_session_banner = True
    except Exception:
        pass
    return True


def _install_busy_indicator(gui: Any, core: Dict[str, Any]) -> None:
    """Reuse the progress bar embedded in the Status tracking row.

    Older compatibility code created a second progress bar beside Details.  At narrow
    widths that bar looked detached or floated outside the visible status text.
    The runtime footer now owns one fixed progress indicator directly after the
    ``Status tracking:`` label, so loading and parser work share the same row.
    """
    if getattr(gui, "_v225_busy_indicator_installed", False):
        return
    try:
        bar = getattr(gui, "_v218_status_progress", None)
        if bar is None:
            # The persistent footer may be installed by a delayed startup pass.
            return
        gui._v225_busy_progress = bar
        gui._v225_busy_indicator_installed = True
    except Exception:
        pass


def _load_buttons(gui: Any) -> list[Any]:
    buttons = []
    for widget in _walk(gui):
        try:
            if widget.winfo_class() in {"TButton", "Button"} and _text(widget).strip() in {
                "Load + inspect", "Load and inspect", "Loading…", "Loading..."
            }:
                buttons.append(widget)
        except Exception:
            pass
    return buttons


def _set_loading_ui(gui: Any, core: Dict[str, Any], busy: bool, message: str = "") -> None:
    _install_busy_indicator(gui, core)
    tk = core.get("tk")
    if not hasattr(gui, "_faradaiq_load_status_var") and tk is not None:
        try:
            gui._faradaiq_load_status_var = tk.StringVar(master=gui, value="")
        except Exception:
            pass
    load_var = getattr(gui, "_faradaiq_load_status_var", None)
    bar = getattr(gui, "_v225_busy_progress", None)
    # Progress animation/value is controlled by the shared status renderer so
    # it always corresponds to the actual selected status source.
    # Cancel an older terminal-message clear before publishing a new load
    # state.  This avoids an earlier callback erasing a later transaction.
    pending_clear = getattr(gui, "_v225_clear_load_status_after", None)
    if pending_clear is not None:
        try:
            gui.after_cancel(pending_clear)
        except Exception:
            pass
        gui._v225_clear_load_status_after = None

    value = str(message or ("Loading and inspecting..." if busy else ""))
    try:
        load_var.set(value)
    except Exception:
        pass

    # Publish immediately to the persistent footer.  Its regular one-second
    # clock keeps elapsed time current while busy.  A terminal success/failure
    # message remains visible for five seconds so delayed legacy startup writes
    # cannot instantly replace the result the user needs to see.
    try:
        import time as _time
        if value:
            gui._v218_status_base = value
            gui._v218_status_source = "_faradaiq_load_status_var"
            gui._v218_status_started = _time.monotonic() if busy else None
            gui._v218_status_last_rendered = value
            gui._v218_status_display_var.set(value)
    except Exception:
        pass

    if not busy and value:
        def clear_terminal(expected: str = value) -> None:
            try:
                if str(load_var.get() or "") == expected:
                    load_var.set("")
            except Exception:
                pass
            gui._v225_clear_load_status_after = None

        try:
            gui._v225_clear_load_status_after = gui.after(5000, clear_terminal)
        except Exception:
            pass
    for button in _load_buttons(gui):
        try:
            if busy:
                if not hasattr(button, "_v225_original_text"):
                    button._v225_original_text = _text(button) or "Load + inspect"
                button.configure(text="Loading…", state="disabled")
            else:
                button.configure(text=getattr(button, "_v225_original_text", "Load + inspect"), state="normal")
        except Exception:
            pass


def _write_session_completion(gui: Any, success: bool, error: str = "") -> None:
    try:
        ctx = getattr(gui, "_v219_working_session_context", None)
        if not isinstance(ctx, dict):
            return
        ctx["load_inspect_completed"] = bool(success)
        ctx["completed_at"] = datetime.now().isoformat(timespec="seconds")
        if error:
            ctx["load_inspect_error"] = str(error)
        else:
            ctx.pop("load_inspect_error", None)
        path = Path(ctx.get("session_dir", "")) / "session_context.json"
        if path.parent.exists():
            # The context values are scalar/path strings in normal use.
            path.write_text(json.dumps(ctx, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def install(core: Dict[str, Any]) -> None:
    Session = core.get("SessionReaderGUI")
    PSSessionCollection = core.get("PSSessionCollection")
    messagebox = core.get("messagebox")
    if Session is None or PSSessionCollection is None:
        raise RuntimeError("FaradaIQ SessionReaderGUI or PSSessionCollection is unavailable")
    if getattr(Session, "_v225_loading_ui_installed", False):
        return

    # Future session-banner rebuilds use the responsive two-line layout.
    core["_v84_build_session_banner_frame"] = lambda parent, title, ctx: _build_session_banner(parent, title, ctx, core)

    previous_insert = core.get("_v177_insert_main_session_buttons")
    if callable(previous_insert):
        def insert_buttons(gui: Any, frame: Optional[Any] = None) -> bool:
            result = previous_insert(gui, frame)
            _normalize_session_banner(gui, core, frame)
            return bool(result)
        core["_v177_insert_main_session_buttons"] = insert_buttons

    # Older delayed cleanup layers can recreate the Auto Save checkbox at
    # column 4 and shift Load Session to column 5 several seconds after startup.
    # Normalize immediately after every such legacy insertion so the requested
    # order remains stable: New -> Save -> Load -> Auto Save.
    previous_autosave_adder = core.get("_v177_add_autosave_checkbox_to_launch_row")
    if callable(previous_autosave_adder):
        def add_autosave_checkbox(gui: Any) -> bool:
            result = previous_autosave_adder(gui)
            _normalize_session_banner(gui, core)
            return bool(result)
        core["_v177_add_autosave_checkbox_to_launch_row"] = add_autosave_checkbox

    def load_and_inspect(self: Any, *_args: Any, **_kwargs: Any) -> None:
        if bool(getattr(self, "_v225_load_busy", False)):
            try:
                self.set_status("A file or folder is already loading. The current import remains active.")
            except Exception:
                pass
            return None

        try:
            path = str(self.file_var.get() or "").strip()
        except Exception:
            path = ""
        if not path:
            if messagebox is not None:
                messagebox.showwarning(
                    "No input selected",
                    "Please select a supported file, ZIP, or folder first.",
                    parent=self,
                )
            return None
        if not Path(path).exists():
            if messagebox is not None:
                messagebox.showerror("Path not found", f"Cannot find input path:\n{path}", parent=self)
            return None

        reset = core.get("_v209_reset_parser_status") or core.get("_v208_reset_parser_status")
        try:
            if callable(reset):
                reset(self, "load")
        except Exception:
            pass

        start_session = core.get("_v219_start_new_session")
        try:
            if callable(start_session):
                start_session(self, source_path=path, reason="load_and_inspect", preserve_description=True)
        except Exception as exc:
            try:
                self.set_status(f"Could not start named session for selected input: {exc}")
            except Exception:
                pass

        self._v225_load_busy = True
        self._v225_load_path = path
        _normalize_session_banner(self, core)
        _set_loading_ui(self, core, True, f"Loading and inspecting: {Path(path).name or path}")
        try:
            self.set_status(f"Loading and inspecting: {Path(path).name or path}")
        except Exception:
            pass

        results: "queue.Queue[tuple[Any, Any, Any, str]]" = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                collection = PSSessionCollection()
                collection.load(path)
                summary = collection.summarize()
                results.put((collection, summary, None, ""))
            except Exception as exc:  # parser exceptions return through the queue
                results.put((None, None, exc, traceback.format_exc()))

        def finish_from_queue() -> None:
            try:
                collection, summary, error, tb = results.get_nowait()
            except queue.Empty:
                try:
                    self.after(40, finish_from_queue)
                except Exception:
                    pass
                return
            completion_message = ""
            try:
                if error is None:
                    self.collection = collection
                    self.current_summary = summary
                    ui_warnings = []
                    for label, callback in (
                        ("loaded source context", self._refresh_description_with_loaded_context),
                        ("session summary", lambda: self._display_summary(summary)),
                        ("data structure preview", lambda: self._display_structure_preview(summary)),
                    ):
                        try:
                            callback()
                        except Exception as ui_exc:
                            ui_warnings.append(f"{label}: {ui_exc}")
                    if ui_warnings:
                        completion_message = f"Loaded and inspected: {Path(path).name or path}; one or more preview panels could not be refreshed."
                    else:
                        completion_message = f"Loaded and inspected: {Path(path).name or path}"
                    self.set_status(completion_message)
                    _write_session_completion(self, True, "; ".join(ui_warnings))
                    try:
                        if not hasattr(self, "auto_save_session_var"):
                            self.auto_save_session_var = core["tk"].BooleanVar(value=True)
                        if bool(self.auto_save_session_var.get()):
                            autosave = core.get("_v177_start_autosave_session")
                            if callable(autosave):
                                autosave(self, immediate=True, reason="load_and_inspect")
                    except Exception:
                        pass
                else:
                    completion_message = f"Load/inspect failed: {Path(path).name or path}"
                    self.set_status(completion_message)
                    _write_session_completion(self, False, str(error))
                    if messagebox is not None:
                        messagebox.showerror("Load error", f"{error}\n\n{tb}", parent=self)
            finally:
                self._v225_load_busy = False
                _set_loading_ui(self, core, False, completion_message)
                _normalize_session_banner(self, core)

        threading.Thread(target=worker, name="FaradaIQ-v225-load", daemon=True).start()
        try:
            self.after(40, finish_from_queue)
        except Exception:
            pass
        return None

    Session.load_and_inspect = load_and_inspect

    previous_init = Session.__init__
    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        previous_init(self, *args, **kwargs)
        try:
            self.title(f"FaradaIQ Studio {VERSION}")
        except Exception:
            pass
        try:
            if not hasattr(self, "_faradaiq_load_status_var"):
                self._faradaiq_load_status_var = core["tk"].StringVar(master=self, value="")
        except Exception:
            pass

        def settle() -> None:
            _normalize_session_banner(self, core)
            _install_busy_indicator(self, core)
            try:
                self.title(f"FaradaIQ Studio {VERSION}")
            except Exception:
                pass

        settle()
        for delay in (40, 180, 500, 1200, 2600):
            try:
                self.after(delay, settle)
            except Exception:
                pass

    Session.__init__ = init
    Session._v225_loading_ui_installed = True

    help_text = (
        "v232 responsive loading, visible session controls, and direct batch export controls",
        r"""
RESPONSIVE LOAD + INSPECT

Load + inspect now parses the selected file or folder on a background worker. The Tk event loop stays responsive while large local or network-share datasets are inspected. Input, output, session, parser, and toolbar controls remain painted and visible; the Load + inspect button is disabled only for the active transaction so the same input cannot be started twice.

SINGLE LIVE STATUS LOCATION

The duplicate Status line was removed from the launch/working-session banner. The persistent row reads Status tracking → progress → actual status → Details. Loading progress, elapsed time, parser priority, and cache priority remain together in that single row. During import it explicitly reports “Loading and inspecting: <selected name>”; parser, cache, export, and completion messages replace that text with their actual status.

SESSION BANNER LAYOUT

The session identifier stays on the action row. The complete abbreviated tracking path has a dedicated full-width second row and is not compressed by New Session, Save Session, Load Session, Auto Save session, Open tracking folder, or Copy ID. The layout is re-normalized after every new-session or loaded-session banner rebuild.
""",
    )
    tabs = core.get("_V207_HELP_TABS")
    if isinstance(tabs, list) and not any(str(item[0]) == help_text[0] for item in tabs if isinstance(item, (list, tuple)) and item):
        tabs.append(help_text)
