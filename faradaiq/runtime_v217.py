"""FaradaIQ Studio v273 runtime/status/theme/cache refinements."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

VERSION = "v273"
DEFAULT_THEME = "High Contrast"
THEME_NAMES = ("Dark", "Light", "Slate", "Warm", "Midnight", "High Contrast")


def _fmt_elapsed(seconds: float) -> str:
    sec=max(0,int(seconds)); h,rem=divmod(sec,3600); m,s=divmod(rem,60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _active_status(text: str) -> bool:
    s=str(text or "").lower()
    active=("loading","inspecting","running","building","catalog","extract","preparing","exporting","parsing","queued","writing","rebuild")
    done=("ready to open","finished","complete","failed","idle","cancelled","error")
    return any(k in s for k in active) and not any(k in s for k in done)


def _status_percent(text: str) -> Optional[float]:
    """Extract a trustworthy 0..100 progress value when status text contains one.

    Percent text has priority.  Otherwise the first completed/total token is
    used.  Indeterminate work such as a single-file import returns None.
    """
    value = str(text or "")
    match = re.search(r"(?:\(|\b)(\d{1,3}(?:\.\d+)?)\s*%", value)
    if match:
        try:
            return max(0.0, min(100.0, float(match.group(1))))
        except Exception:
            return None
    for match in re.finditer(r"(?<![\d.])(\d+)\s*/\s*(\d+)(?![\d.])", value):
        try:
            done = float(match.group(1)); total = float(match.group(2))
            if total > 0 and 0 <= done <= total:
                return max(0.0, min(100.0, 100.0 * done / total))
        except Exception:
            continue
    return None


def _install_persistent_status(gui: Any, ttk: Any, tk: Any) -> None:
    """Install one persistent, non-flickering status footer.

    Legacy layers may write several Tk variables in quick succession.  The
    footer coalesces those bursts, keeps the active source stable, and updates
    only when the rendered text actually changes.  It never repeatedly raises
    or re-places the frame during the one-second elapsed-time tick.
    """
    if getattr(gui, "_v218_persistent_status_installed", False):
        return

    gui._v218_status_display_var = tk.StringVar(value="Ready")
    gui._v218_status_started = None
    gui._v218_status_base = "Ready"
    gui._v218_status_source = None
    gui._v218_status_last_rendered = ""
    gui._v218_status_pending_after = None
    gui._v218_status_history = []

    frame = ttk.Frame(gui, style="Card.TFrame", padding=(6, 3), height=34)
    try:
        frame.place(relx=0.0, rely=1.0, relwidth=1.0, anchor="sw")
        frame.lift()
    except Exception:
        return
    try:
        frame.pack_propagate(False)
    except Exception:
        pass

    # Keep the progress indicator visually attached to the live status instead
    # of floating beside the Details button.  This fixed row reads left-to-right:
    # Status tracking -> progress -> actual operation text -> Details.
    # Use a four-column grid so the Details button always retains its requested
    # width.  The prior left-packed expanding label could consume the remaining
    # space on high-DPI or narrow windows and leave Details visibly clipped.
    frame.columnconfigure(2, weight=1)
    frame.rowconfigure(0, weight=1)
    prefix = ttk.Label(frame, text="Status tracking:", style="Section.TLabel", anchor="w")
    prefix.grid(row=0, column=0, sticky="w", padx=(0, 7))
    progress = ttk.Progressbar(frame, mode="indeterminate", length=150, maximum=100)
    progress.grid(row=0, column=1, sticky="ew", padx=(0, 9), pady=(2, 2))
    lbl = ttk.Label(
        frame,
        textvariable=gui._v218_status_display_var,
        style="Status.TLabel",
        anchor="w",
        width=1,
    )
    lbl.grid(row=0, column=2, sticky="ew")

    def show_details():
        win = getattr(gui, "_v217_progress_window", None)
        try:
            if win is not None and win.winfo_exists():
                win.deiconify()
                win.lift()
                return
        except Exception:
            pass
        try:
            import tkinter as _tk
            w = _tk.Toplevel(gui)
            w.title(f"FaradaIQ Studio {VERSION} — detailed status")
            w.geometry("980x520")
            box = _tk.Text(w, wrap="word")
            box.pack(fill="both", expand=True)
            history = getattr(gui, "_v218_status_history", []) or [gui._v218_status_base]
            box.insert("1.0", "\n".join(history))
            box.configure(state="disabled")
        except Exception:
            pass

    details_button = ttk.Button(frame, text="Details", command=show_details, width=11)
    details_button.grid(row=0, column=3, sticky="e", padx=(8, 0))
    gui._v218_status_details_button = details_button

    progress_state = {"active": False, "mode": "indeterminate", "value": None}

    def set_progress_for_status(text: str) -> None:
        active = _active_status(text)
        percent = _status_percent(text) if active else None
        try:
            if not active:
                if progress_state["active"]:
                    progress.stop()
                progress.configure(mode="determinate", value=0)
                progress_state.update(active=False, mode="determinate", value=0.0)
                return
            if percent is None:
                if progress_state["mode"] != "indeterminate":
                    progress.stop()
                    progress.configure(mode="indeterminate", value=0)
                if not progress_state["active"] or progress_state["mode"] != "indeterminate":
                    progress.start(12)
                progress_state.update(active=True, mode="indeterminate", value=None)
            else:
                if progress_state["mode"] == "indeterminate" and progress_state["active"]:
                    progress.stop()
                progress.configure(mode="determinate", value=percent)
                progress_state.update(active=True, mode="determinate", value=percent)
        except Exception:
            pass

    gui._v218_set_status_progress = set_progress_for_status
    gui._v218_status_progress = progress
    gui._v218_status_prefix_label = prefix
    gui._v218_status_label = lbl

    def _read_sources():
        out = []
        for name in ("_faradaiq_load_status_var", "_faradaiq_pyseccm_status_var", "browser_cache_status_var", "status_var"):
            var = getattr(gui, name, None)
            if var is None:
                continue
            try:
                val = str(var.get() or "").strip()
            except Exception:
                continue
            if val:
                out.append((name, val))
        return out

    def _choose_source(candidates):
        by_name = {name: value for name, value in candidates}
        priority = ("_faradaiq_load_status_var", "_faradaiq_pyseccm_status_var", "browser_cache_status_var", "status_var")
        active_names = [name for name in priority if by_name.get(name) and _active_status(by_name[name])]
        current = getattr(gui, "_v218_status_source", None)
        if active_names:
            highest = active_names[0]
            # Keep the current source only when it is still active and no
            # higher-priority source has become active.
            if current in active_names and priority.index(current) <= priority.index(highest):
                return current, by_name[current]
            return highest, by_name[highest]
        # Stable priority. Parser/DRT owns the footer while active, then cache,
        # then the general application status.
        ready = by_name.get("browser_cache_status_var", "")
        if "ready to open" in ready.lower():
            return "browser_cache_status_var", ready
        for name in ("_faradaiq_load_status_var", "status_var", "_faradaiq_pyseccm_status_var", "browser_cache_status_var"):
            value = by_name.get(name)
            if value:
                return name, value
        return None, "Ready"

    def render_status():
        gui._v218_status_pending_after = None
        source, text = _choose_source(_read_sources())
        previous_base = getattr(gui, "_v218_status_base", "Ready")
        previous_source = getattr(gui, "_v218_status_source", None)
        if text != previous_base or source != previous_source:
            gui._v218_status_base = text
            gui._v218_status_source = source
            gui._v218_status_history.append(f"[{time.strftime('%H:%M:%S')}] {text}")
            gui._v218_status_history = gui._v218_status_history[-500:]
            if _active_status(text):
                if gui._v218_status_started is None or source != previous_source:
                    gui._v218_status_started = time.monotonic()
            else:
                gui._v218_status_started = None
        elapsed = ""
        if gui._v218_status_started is not None:
            elapsed = f" | elapsed {_fmt_elapsed(time.monotonic() - gui._v218_status_started)}"
        rendered = text + elapsed
        if rendered != gui._v218_status_last_rendered:
            gui._v218_status_last_rendered = rendered
            gui._v218_status_display_var.set(rendered)
        set_progress_for_status(text)

    def schedule_render(*_args):
        # Coalesce the common sequence: parser var -> general status -> cache var.
        pending = getattr(gui, "_v218_status_pending_after", None)
        if pending is not None:
            try:
                gui.after_cancel(pending)
            except Exception:
                pass
        try:
            gui._v218_status_pending_after = gui.after(140, render_status)
        except Exception:
            render_status()

    for name in ("_faradaiq_load_status_var", "_faradaiq_pyseccm_status_var", "browser_cache_status_var", "status_var"):
        var = getattr(gui, name, None)
        try:
            var.trace_add("write", schedule_render)
        except Exception:
            pass

    def tick():
        render_status()
        try:
            gui.after(1000, tick)
        except Exception:
            pass

    render_status()
    try:
        gui.after(1000, tick)
    except Exception:
        pass
    gui._v218_persistent_status_frame = frame
    gui._v218_persistent_status_installed = True
    # Compatibility names used by the v217 QA and older session code.
    gui._v217_persistent_status_frame = frame
    gui._v217_persistent_status_installed = True
    gui._v217_status_display_var = gui._v218_status_display_var


def _palette(mode: str) -> Dict[str,str]:
    key=str(mode or DEFAULT_THEME).strip().lower().replace("_"," ")
    if key.startswith("light"):
        return {'mode':'light','display':'Light','bg':'#f4f7fb','surface':'#ffffff','surface2':'#eef3f8','surface3':'#e3ebf4','ink':'#102033','muted':'#526274','hero':'#eaf4fb','hero_title':'#0b3558','hero_sub':'#2a5f7e','hero_credit':'#557083','text_bg':'#ffffff','text_fg':'#102033','text_border':'#bdc8d6','button_bg':'#f7f9fc','button_fg':'#102033','button_active':'#e8eef6','button_pressed':'#d7e1ec','accent':'#3f6d9d','accent_hover':'#335c86','accent_pressed':'#284a6f','accent_fg':'#ffffff','select_bg':'#2f5f8f','select_fg':'#ffffff','danger':'#b42318'}
    if key.startswith("slate"):
        return {'mode':'slate','display':'Slate','bg':'#20262e','surface':'#2b333d','surface2':'#353f4b','surface3':'#414d5b','ink':'#edf2f7','muted':'#bdc8d4','hero':'#252f3a','hero_title':'#f8fafc','hero_sub':'#c9d6e2','hero_credit':'#aebdca','text_bg':'#171c22','text_fg':'#edf2f7','text_border':'#4a5867','button_bg':'#3a4653','button_fg':'#f4f7fb','button_active':'#4a5867','button_pressed':'#303a45','accent':'#6f8fad','accent_hover':'#84a3c0','accent_pressed':'#58748f','accent_fg':'#ffffff','select_bg':'#58748f','select_fg':'#ffffff','danger':'#ff8b8b'}
    if key.startswith("warm"):
        return {'mode':'warm','display':'Warm','bg':'#f4efe7','surface':'#fffaf2','surface2':'#eee5d8','surface3':'#e1d4c2','ink':'#3f3428','muted':'#756759','hero':'#efe3d2','hero_title':'#513b27','hero_sub':'#7a5a3d','hero_credit':'#8c7560','text_bg':'#fffdf8','text_fg':'#3f3428','text_border':'#cdbda8','button_bg':'#f4eadc','button_fg':'#3f3428','button_active':'#e7d8c5','button_pressed':'#dbc7ae','accent':'#8a6b4d','accent_hover':'#9d7d5e','accent_pressed':'#6f5238','accent_fg':'#ffffff','select_bg':'#8a6b4d','select_fg':'#ffffff','danger':'#a83232'}
    if key.startswith("midnight"):
        return {'mode':'midnight','display':'Midnight','bg':'#080d18','surface':'#0f1726','surface2':'#162238','surface3':'#1d2d49','ink':'#dce9fb','muted':'#93a8c5','hero':'#0a1425','hero_title':'#f5f9ff','hero_sub':'#a9c4e7','hero_credit':'#88a6cb','text_bg':'#050913','text_fg':'#dce9fb','text_border':'#263b5f','button_bg':'#1b2a43','button_fg':'#eaf2ff','button_active':'#263b5f','button_pressed':'#111d31','accent':'#4779b7','accent_hover':'#5b91d2','accent_pressed':'#345f94','accent_fg':'#ffffff','select_bg':'#3e6ea8','select_fg':'#ffffff','danger':'#ff7b7b'}
    if key.startswith("high"):
        return {'mode':'high contrast','display':'High Contrast','bg':'#000000','surface':'#050505','surface2':'#141414','surface3':'#232323','ink':'#ffffff','muted':'#e8e8e8','hero':'#000000','hero_title':'#ffffff','hero_sub':'#ffff66','hero_credit':'#ffffff','text_bg':'#000000','text_fg':'#ffffff','text_border':'#ffffff','button_bg':'#111111','button_fg':'#ffffff','button_active':'#333333','button_pressed':'#000000','accent':'#005fcc','accent_hover':'#2f8cff','accent_pressed':'#003f88','accent_fg':'#ffffff','select_bg':'#005fcc','select_fg':'#ffffff','danger':'#ff4d4d'}
    return {'mode':'dark','display':'Dark','bg':'#0f1724','surface':'#162235','surface2':'#1f2d44','surface3':'#263650','ink':'#e6edf7','muted':'#aebdd0','hero':'#101e31','hero_title':'#f4f8ff','hero_sub':'#c6d7eb','hero_credit':'#b7c8dc','text_bg':'#0b1220','text_fg':'#e6edf7','text_border':'#33445f','button_bg':'#263650','button_fg':'#e8eef8','button_active':'#324765','button_pressed':'#1e2c42','accent':'#587aa4','accent_hover':'#6b8fba','accent_pressed':'#46698f','accent_fg':'#ffffff','select_bg':'#4f75a0','select_fg':'#ffffff','danger':'#f87171'}


def _is_plot_canvas(widget: Any) -> bool:
    """Return True for Tk canvases that contain rasterized plot/image content.

    Plain scrolling canvases should inherit the application theme, while
    Matplotlib/TkAgg and image canvases keep their plot-specific background.
    """
    try:
        if str(widget.winfo_class()) != "Canvas":
            return False
        item_types = {str(widget.type(item)) for item in widget.find_all()}
        return bool(item_types.intersection({"image", "bitmap"}))
    except Exception:
        return False


def _apply_classic_widget_theme(root: Any, palette: Mapping[str, str]) -> None:
    """Theme every classic Tk text/list/editor widget, including late popups.

    ttk styles do not affect tk.Text, ScrolledText, Listbox, Canvas-based
    scrolling regions, or classic Entry/Spinbox controls.  A recursive pass
    closes that gap and is intentionally tolerant of destroyed/read-only
    widgets.
    """
    p = dict(palette or {})
    bg = p.get("bg", "#000000")
    surface = p.get("surface", bg)
    surface2 = p.get("surface2", surface)
    text_bg = p.get("text_bg", surface)
    text_fg = p.get("text_fg", p.get("ink", "#ffffff"))
    border = p.get("text_border", p.get("muted", text_fg))
    accent = p.get("accent", "#005fcc")
    select_bg = p.get("select_bg", accent)
    select_fg = p.get("select_fg", "#ffffff")
    button_bg = p.get("button_bg", surface2)
    button_fg = p.get("button_fg", text_fg)
    muted = p.get("muted", text_fg)
    seen: set[int] = set()

    def configure(widget: Any) -> None:
        ident = id(widget)
        if ident in seen:
            return
        seen.add(ident)
        try:
            cls = str(widget.winfo_class())
        except Exception:
            cls = ""
        try:
            if cls == "Text":
                widget.configure(
                    background=text_bg, foreground=text_fg, insertbackground=text_fg,
                    selectbackground=select_bg, selectforeground=select_fg,
                    highlightbackground=border, highlightcolor=accent,
                    relief="solid", borderwidth=max(1, int(float(widget.cget("borderwidth") or 1))),
                )
            elif cls == "Listbox":
                widget.configure(
                    background=text_bg, foreground=text_fg,
                    selectbackground=select_bg, selectforeground=select_fg,
                    highlightbackground=border, highlightcolor=accent,
                    disabledforeground=muted,
                )
            elif cls in {"Entry", "Spinbox"}:
                widget.configure(
                    background=text_bg, foreground=text_fg, insertbackground=text_fg,
                    selectbackground=select_bg, selectforeground=select_fg,
                    highlightbackground=border, highlightcolor=accent,
                    readonlybackground=text_bg, disabledbackground=surface2,
                    disabledforeground=muted,
                )
            elif cls == "Canvas" and not _is_plot_canvas(widget):
                widget.configure(background=bg, highlightbackground=border, highlightcolor=accent)
            elif cls in {"Frame", "Labelframe", "Panedwindow"}:
                widget.configure(background=bg)
            elif cls in {"Label", "Message"}:
                widget.configure(background=bg, foreground=text_fg)
            elif cls in {"Button", "Menubutton"}:
                widget.configure(
                    background=button_bg, foreground=button_fg,
                    activebackground=p.get("button_active", surface2),
                    activeforeground=button_fg, highlightbackground=border,
                )
            elif cls in {"Checkbutton", "Radiobutton"}:
                widget.configure(
                    background=bg, foreground=text_fg,
                    activebackground=surface2, activeforeground=text_fg,
                    selectcolor=text_bg, highlightbackground=border,
                )
            elif cls == "Scrollbar":
                widget.configure(
                    background=button_bg, troughcolor=bg,
                    activebackground=accent, highlightbackground=border,
                )
            elif cls == "Menu":
                widget.configure(
                    background=text_bg, foreground=text_fg,
                    activebackground=select_bg, activeforeground=select_fg,
                    selectcolor=accent,
                )
            elif cls == "Scale":
                widget.configure(
                    background=bg, foreground=text_fg,
                    troughcolor=surface2, activebackground=accent,
                    highlightbackground=border,
                )
        except Exception:
            pass
        try:
            for child in list(widget.winfo_children()):
                configure(child)
        except Exception:
            pass

    try:
        configure(root)
    except Exception:
        pass


def _install_late_widget_theme_hook(root: Any, palette_fn: Any) -> None:
    """Theme widgets created after the initial theme pass without polling forever."""
    if getattr(root, "_v222_theme_map_hook", False):
        return
    try:
        def on_map(event: Any) -> None:
            widget = getattr(event, "widget", None)
            if widget is None:
                return
            try:
                mode = getattr(root, "_faradaiq_theme_mode", DEFAULT_THEME)
                _apply_classic_widget_theme(widget, palette_fn(mode))
            except Exception:
                pass
        root.bind_all("<Map>", on_map, add="+")
        root._v222_theme_map_hook = True
    except Exception:
        pass


def _install_theme_selector(gui: Any, core: Mapping[str,Any], ttk: Any, tk: Any) -> None:
    if getattr(gui,"_v217_theme_selector_installed",False): return
    frame=getattr(gui,"_v120_header_controls_frame",None)
    if frame is None: return
    for w in list(frame.winfo_children()):
        try:
            if w.winfo_class() in {"TButton","Button"} and str(w.cget("text") or "") in {"Dark","Light","Slate","Warm","Midnight","High Contrast"}: w.pack_forget()
        except Exception: pass
    var=getattr(gui,"theme_mode_var",None)
    if var is None:
        var=tk.StringVar(value=DEFAULT_THEME); gui.theme_mode_var=var
    current=getattr(gui,"_faradaiq_theme_mode","high contrast")
    var.set(_palette(current).get("display",DEFAULT_THEME))
    combo=ttk.Combobox(frame,textvariable=var,state="readonly",width=14,values=THEME_NAMES)
    combo.pack(side="left")
    apply_fn=core.get("_v36_apply_theme")
    def changed(_e=None):
        if callable(apply_fn): apply_fn(gui,var.get())
    combo.bind("<<ComboboxSelected>>",changed)
    gui._v217_theme_selector=combo; gui._v217_theme_selector_installed=True


def _quick_cache_info(h5_path: Path) -> Optional[Dict[str,Any]]:
    cache=h5_path.parent/".browser_cache_from_h5"; source=cache/"_bundle_source.json"
    if not cache.exists() or not source.exists(): return None
    try:
        obj=json.loads(source.read_text(encoding="utf-8",errors="replace"))
        recorded=Path(str(obj.get("h5_path") or ""))
        same_name=recorded.name.lower()==h5_path.name.lower()
        same_mtime=abs(float(obj.get("h5_mtime") or -1)-float(h5_path.stat().st_mtime))<0.01
        table_files=list(cache.rglob("*.csv"))+list(cache.rglob("*.tsv"))+list(cache.rglob("*.txt"))
        media=[Path(x) for x in (obj.get("extracted_media") or [])]
        if same_name and same_mtime and table_files and all(p.exists() for p in media):
            return {"cache_root":str(cache),"h5_path":str(h5_path),"json_path":str(h5_path.with_suffix('.json')) if h5_path.with_suffix('.json').exists() else "","manifest":obj.get("manifest") or {},"n_tables":int(obj.get("n_tables") or len(table_files)),"n_media":int(obj.get("n_media") or len(media)),"extracted_media":[str(p) for p in media],"cache_reused":True,"quick_reuse":True,"reuse_reason":"H5 path, modification time, table cache, and media cache are current"}
    except Exception: return None
    return None


def _prime_startup_theme(gui: Any, core: Mapping[str, Any], tk: Any) -> None:
    """Prime High Contrast before any visible paint occurs."""
    try:
        gui._faradaiq_theme_mode = "high contrast"
    except Exception:
        pass
    try:
        var = getattr(gui, "theme_mode_var", None)
        if var is None:
            var = tk.StringVar(master=gui, value=DEFAULT_THEME)
            gui.theme_mode_var = var
        else:
            var.set(DEFAULT_THEME)
    except Exception:
        pass
    apply_fn = core.get("_v36_apply_theme")
    try:
        if callable(apply_fn):
            apply_fn(gui, DEFAULT_THEME)
        else:
            gui.configure(background="#000000")
    except Exception:
        try:
            gui.configure(background="#000000")
        except Exception:
            pass


def install(core: Dict[str,Any]) -> None:
    Session=core.get("SessionReaderGUI"); Browser=core.get("FaradaIQH5Browser")
    ttk=core.get("ttk"); tk=core.get("tk")
    if Session is None or ttk is None or tk is None: raise RuntimeError("GUI classes unavailable")

    # Extend the palette and apply function used by all later windows.
    core["_v36_theme_palette"]=_palette
    old_style=core.get("_v109_style_configure"); old_colors=core.get("_v109_apply_widget_colors"); old_fonts=core.get("_v80_reapply_fonts")
    def apply_theme(root: Any, mode: str=DEFAULT_THEME):
        p=old_style(root,mode) if callable(old_style) else _palette(mode)
        # Reassert field and notebook colors after the legacy style function,
        # which used a fixed dark-blue entry field even in High Contrast.
        try:
            style = ttk.Style(root)
            field_bg = p.get("text_bg", "#000000")
            text_fg = p.get("text_fg", p.get("ink", "#ffffff"))
            style.configure("TEntry", fieldbackground=field_bg, foreground=text_fg, insertcolor=text_fg)
            style.configure("TSpinbox", fieldbackground=field_bg, foreground=text_fg, insertcolor=text_fg)
            style.configure("TCombobox", fieldbackground=field_bg, background=p.get("button_bg", field_bg), foreground=text_fg, arrowcolor=text_fg)
            style.map("TCombobox", fieldbackground=[("readonly", field_bg)], foreground=[("readonly", text_fg)])
            style.configure("TNotebook", background=p.get("bg", field_bg), bordercolor=p.get("text_border", text_fg))
            style.configure("TNotebook.Tab", background=p.get("surface2", field_bg), foreground=text_fg)
            style.map("TNotebook.Tab", background=[("selected", p.get("surface3", field_bg)), ("active", p.get("button_active", field_bg))], foreground=[("selected", text_fg), ("active", text_fg)])
            style.configure("Treeview", background=field_bg, fieldbackground=field_bg, foreground=text_fg, bordercolor=p.get("text_border", text_fg))
            style.map("Treeview", background=[("selected", p.get("select_bg", p.get("accent", field_bg)))], foreground=[("selected", p.get("select_fg", text_fg))])
            style.configure("Treeview.Heading", background=p.get("surface2", field_bg), foreground=text_fg)
            style.configure("TProgressbar", background=p.get("accent", text_fg), troughcolor=p.get("surface2", field_bg), bordercolor=p.get("text_border", text_fg))
        except Exception:
            pass
        try: root._faradaiq_theme_mode=p.get("mode",str(mode).lower())
        except Exception: pass
        try:
            if hasattr(root,"theme_mode_var"): root.theme_mode_var.set(p.get("display",str(mode).title()))
        except Exception: pass
        try:
            if callable(old_colors): old_colors(root,p)
            _apply_classic_widget_theme(root,p)
            _install_late_widget_theme_hook(root,_palette)
            if callable(old_fonts): old_fonts(root)
        except Exception: pass
        # A few complex windows add editor widgets on idle callbacks.  These
        # finite follow-up sweeps cover them without a permanent timer.
        try:
            for delay in (0, 40, 160, 500, 1200, 3000):
                root.after(delay, lambda r=root, pp=dict(p): _apply_classic_widget_theme(r, pp))
        except Exception:
            pass
        return p
    core["_v36_apply_theme"]=apply_theme

    # Intercept stale legacy title writes instead of repainting the title on a
    # timer.  This removes another source of startup flashing while preserving
    # descriptive suffixes such as the active sample name.
    def _install_title_guard(cls: Any) -> None:
        if cls is None or getattr(cls, "_v218_title_guard", False):
            return
        previous_title = cls.title
        def guarded_title(self: Any, text: Any = None):
            if text is None:
                return previous_title(self)
            value = str(text)
            if "FaradaIQ Studio" in value:
                value = re.sub(r"FaradaIQ Studio\s+v\d+", f"FaradaIQ Studio {VERSION}", value, count=1)
            return previous_title(self, value)
        cls.title = guarded_title
        cls._v218_title_guard = True

    _install_title_guard(Session)
    _install_title_guard(Browser)

    # Reusable progress logger.
    prev_progress=core.get("_v63_analysis_progress_window")
    def progress_window(gui: Any, title: str="FaradaIQ detailed background progress"):
        win,log=prev_progress(gui,title) if callable(prev_progress) else (None,lambda m:None)
        start=time.monotonic()
        try:
            mode = getattr(gui, "_faradaiq_theme_mode", DEFAULT_THEME)
            palette = _palette(mode)
            if win is not None:
                win.configure(background=palette.get("bg", "#000000"))
                _apply_classic_widget_theme(win, palette)
                for delay in (0, 40, 160, 500):
                    win.after(delay, lambda w=win, pp=dict(palette): _apply_classic_widget_theme(w, pp))
        except Exception:
            pass
        def wrapped(msg: Any):
            log(f"[elapsed {_fmt_elapsed(time.monotonic()-start)}] {msg}")
        gui._v217_progress_window=win; gui._v217_progress_log=wrapped
        return win,wrapped
    core["_v63_analysis_progress_window"]=progress_window
    core["_v41_analysis_progress_window"]=progress_window

    # Quick cache validation on browser reopen.
    prev_extract=core.get("_v130_extract_cache_worker")
    def extract_worker(h5_path: Any, progress_queue=None):
        h5=Path(h5_path).resolve(); quick=_quick_cache_info(h5)
        if quick is not None:
            msg=f"[Browser cache] Current cache reused immediately: {quick['n_tables']} table(s), {quick['n_media']} media item(s)."
            try:
                if progress_queue is not None: progress_queue.append(msg)
            except Exception: pass
            return quick
        if progress_queue is not None:
            try: progress_queue.append("[Browser cache] Cache missing or stale; rebuilding only changed content.")
            except Exception: pass
        return prev_extract(h5_path,progress_queue=progress_queue) if callable(prev_extract) else None
    core["_v130_extract_cache_worker"]=extract_worker

    if not getattr(Session,"_v217_runtime_wrapper",False):
        prev_init=Session.__init__
        def init(self: Any,*a:Any,**k:Any):
            prev_init(self,*a,**k)

            try:
                self.title(f"FaradaIQ Studio {VERSION}")
            except Exception:
                pass

            def settle_public_ui():
                """Install final public controls while the startup root is hidden."""
                try:
                    _prime_startup_theme(self, core, tk)
                    _install_persistent_status(self, ttk, tk)
                    _install_theme_selector(self, core, ttk, tk)
                except Exception:
                    pass

            # Build and style the first frame before it is mapped.  This prevents
            # the legacy Dark/default-Tk frame from appearing before High Contrast.
            settle_public_ui()
            for delay in (40, 160):
                try:
                    self.after(delay, settle_public_ui)
                except Exception:
                    pass
            def attach_progress_traces():
                for name in ("_faradaiq_load_status_var","_faradaiq_pyseccm_status_var","browser_cache_status_var"):
                    var=getattr(self,name,None)
                    if var is None: continue
                    def on_change(*_x,v=var,n=name):
                        try:
                            text=str(v.get())
                            log=getattr(self,"_v217_progress_log",None)
                            if callable(log): log(f"[{n}] {text}")
                        except Exception: pass
                    try: var.trace_add("write",on_change)
                    except Exception: pass
            try: self.after(1200,attach_progress_traces)
            except Exception: pass
        Session.__init__=init; Session._v217_runtime_wrapper=True

    if Browser is not None and not getattr(Browser,"_v217_catalog_wrapper",False):
        prev_refresh=Browser.refresh_tree
        def refresh(self: Any,*a:Any,**k:Any):
            start=time.monotonic()
            prebuilt_catalog=False
            try:
                from faradaiq.v240_visual_schema_catalog import _load_ready_catalog
                prebuilt_catalog=bool(_load_ready_catalog(getattr(self,"cache_root","")))
            except Exception:
                prebuilt_catalog=False
            try:
                owner=getattr(self,"gui",None)
                # A completed v240 export job already built and validated the
                # catalog.  Do not reopen the long-running progress window or
                # announce a new cataloging stage when the Browser is merely
                # populating its tree from that ready index.
                if (not prebuilt_catalog) and owner is not None and not callable(getattr(owner,"_v217_progress_log",None)):
                    progress_window(owner,"FaradaIQ export, parser, bundle, and browser progress")
                log=getattr(owner,"_v217_progress_log",None)
                if callable(log):
                    if prebuilt_catalog:
                        log(f"[Browser catalog] Loading job-built catalog for {getattr(self,'base_name','bundle')}.")
                    else:
                        log(f"[Browser catalog] Cache/index is missing or stale; building catalog for {getattr(self,'base_name','bundle')}.")
            except Exception: pass
            result=prev_refresh(self,*a,**k)
            try:
                top=list(self.tree.get_children("")); labels=[self.tree.item(i,"text") for i in top]
                action="Loaded job-built catalog" if prebuilt_catalog else "Catalog ready"
                msg=f"[Browser catalog] {action} in {_fmt_elapsed(time.monotonic()-start)} | {len(top)} categories | " + " | ".join(labels[:12])
                self.log(msg)
                log=getattr(getattr(self,"gui",None),"_v217_progress_log",None)
                if callable(log): log(msg)
            except Exception: pass
            return result
        Browser.refresh_tree=refresh; Browser._v217_catalog_wrapper=True

    # Parser progress mirror into detailed progress window.
    prev_monitor=core.get("_v67_start_pyseccm_monitor")
    def monitor(gui: Any,progress_file: Any,total: Any):
        result=prev_monitor(gui,progress_file,total) if callable(prev_monitor) else None
        if not callable(getattr(gui,"_v217_progress_log",None)):
            try: progress_window(gui,"FaradaIQ export, parser, bundle, and browser progress")
            except Exception: pass
        try:
            log=getattr(gui,"_v217_progress_log",None)
            if callable(log): log(f"[Parser/DRT] Monitoring {int(total)} command(s): {progress_file}")
        except Exception: pass
        path=Path(progress_file); state={"last":"","start":time.monotonic()}
        def poll():
            try: text=path.read_text(encoding="utf-8",errors="replace").strip()
            except Exception: text=""
            if text and text!=state["last"]:
                state["last"]=text
                log=getattr(gui,"_v217_progress_log",None)
                if callable(log): log(f"[Parser/DRT] {text}")
            if "FINISHED" not in text.upper():
                try: gui.after(900,poll)
                except Exception: pass
            else:
                log=getattr(gui,"_v217_progress_log",None)
                if callable(log): log(f"[Parser/DRT] Finished after {_fmt_elapsed(time.monotonic()-state['start'])}.")
        try: gui.after(250,poll)
        except Exception: pass
        return result
    core["_v67_start_pyseccm_monitor"]=monitor

    help_text = (
        "v232 stable status, themed editors, and background progress",
        r"""
STABLE STATUS FOOTER

FaradaIQ uses one persistent footer that stays visible when the main window is resized. Parser/DRT progress has priority while active, followed by browser-cache progress and then general application status. Rapid writes from those sources are coalesced before display, so the footer does not alternate or flash.

The elapsed clock updates once per second without raising, replacing, or resizing the footer. The same text is written only when it actually changes. Select Details to open the shared export/parser/bundle/browser progress window and accumulated status history.

PARSER STATUS

The dedicated parser row continues to show command count, files parsed, input files, size, row estimate, ETA range, confidence, and elapsed time. Intermediate parser updates are no longer duplicated into the legacy general status row.

BROWSER CACHE STATUS

Intermediate cache messages stay in the dedicated cache status and detailed progress window. The general status is updated only for terminal states such as Ready to open or failure. Current caches are reused after a quick H5 modification-time and file-presence check.

THEMED TEXT AND LOG WINDOWS

High Contrast and the other FaradaIQ themes now reach classic Tk Text, ScrolledText, Listbox, Entry, Spinbox, Canvas scrolling regions, menus, and late-created popout windows. The export/parser/bundle progress log no longer keeps the legacy dark-blue editor background when High Contrast is active. Plot canvases retain their scientific rendering background.

WINDOW TITLE

Stale delayed title writes from compatibility layers are intercepted and normalized to the current release. FaradaIQ no longer repaints the window title on a repeating timer.
""",
    )
    tabs = core.get("_V207_HELP_TABS")
    if isinstance(tabs, list) and not any(str(item[0]) == help_text[0] for item in tabs if isinstance(item, (list, tuple)) and item):
        tabs.append(help_text)
