"""FaradaIQ Studio v302 simplified canonical-H5 Browser UI.

This layer intentionally changes presentation, not scientific capability.  The
legacy/task-oriented widgets remain alive and can be revealed from More or
Advanced workspace.  The default browser emphasizes browse -> view -> analyze
-> export, with automatic preview on tree selection and technique-aware actions.
"""
from __future__ import annotations

from typing import Any, Mapping, MutableMapping

VERSION = "v302"


def _walk(widget: Any):
    yield widget
    try:
        for child in widget.winfo_children():
            yield from _walk(child)
    except Exception:
        return


def _text(widget: Any) -> str:
    try:
        if widget.winfo_class() not in {
            "Label", "TLabel", "Button", "TButton", "Checkbutton", "TCheckbutton",
            "Radiobutton", "TRadiobutton", "Labelframe", "TLabelframe", "Menubutton", "TMenubutton",
        }:
            return ""
        return str(widget.cget("text") or "")
    except Exception:
        return ""


def _forget(widget: Any) -> None:
    # Invoke only the active geometry manager. Calling place_forget on a
    # packed widget can crash native Tk 9 while delayed browser bars refresh.
    try:
        manager=widget.winfo_manager()
        method={'pack':'pack_forget','grid':'grid_remove','place':'place_forget'}.get(manager)
        if method:getattr(widget,method)()
    except Exception:
        pass


def _pack_before(widget: Any, before: Any, **kwargs: Any) -> None:
    try:
        widget.pack(before=before, **kwargs)
    except Exception:
        widget.pack(**kwargs)


def _selected_context(browser: Any) -> tuple[str, int]:
    try:
        from .ui_tabs_v226 import _selected_context as base
        return base(browser)
    except Exception:
        pass
    try:
        paths = list(browser.selected_paths(plottable_only=False) or [])
    except Exception:
        paths = []
    return ("Selected data" if paths else "Nothing selected", len(paths))


def _simple_selected_context(browser: Any) -> tuple[str, int]:
    """Prefer the actual selected tree branch over stale last-preview metadata."""
    evidence: list[str] = []
    count = 0
    try:
        tree = browser.tree
        selected = list(tree.selection() or [])
        count = len(selected)
        for iid in selected:
            cur = iid; seen: set[str] = set()
            while cur and cur not in seen:
                seen.add(cur)
                item = tree.item(cur) or {}
                evidence.append(str(item.get("text") or ""))
                evidence.extend(str(v) for v in (item.get("values") or ()))
                cur = tree.parent(cur)
    except Exception:
        pass
    text = " ".join(evidence).lower()
    kinds: list[str] = []
    if any(t in text for t in ("open-circuit", "ocp", "ocv")): kinds.append("OCP")
    if any(t in text for t in ("eis", "nyquist", "bode", "impedance", "geis")): kinds.append("EIS")
    if any(t in text for t in ("tafel", "iv /", "i-v", "polarization", "corrosion")): kinds.append("IV/Tafel")
    if "OCP" not in kinds and any(t in text for t in ("cv /", "cv_", "lsv", "cyclic", "voltamm")) and "IV/Tafel" not in kinds: kinds.append("CV/LSV")
    if any(t in text for t in ("chronoamper", "ca /", "ca_", "deposition")): kinds.append("CA")
    if "OCP" not in kinds and any(t in text for t in ("chronopot", "cp /", "cp_")): kinds.append("CP")
    if "drt" in text: kinds.append("DRT")
    if kinds:
        try:
            paths = list(browser.selected_paths(plottable_only=False) or [])
            if paths: count = len(paths)
        except Exception:
            pass
        return (" + ".join(dict.fromkeys(kinds)), max(1, count))
    return _selected_context(browser)


def _all_tree_items(tree: Any) -> list[str]:
    out: list[str] = []
    def rec(parent: str = "") -> None:
        try:
            children = tree.get_children(parent)
        except Exception:
            children = ()
        for iid in children:
            out.append(iid)
            rec(iid)
    rec("")
    return out


def _open_parents(tree: Any, iid: str) -> None:
    try:
        cur = tree.parent(iid)
        while cur:
            tree.item(cur, open=True)
            cur = tree.parent(cur)
    except Exception:
        pass


def _default_tree_state(browser: Any) -> None:
    tree = getattr(browser, "tree", None)
    if tree is None:
        return
    try:
        for iid in _all_tree_items(tree):
            tree.item(iid, open=False)
        for iid in tree.get_children(""):
            tree.item(iid, open=True)
    except Exception:
        pass


def _simplify_header(browser: Any, core: Mapping[str, Any], ttk: Any, tk: Any) -> None:
    # Remove only redundant always-visible controls. Their behavior remains in More.
    for widget in list(_walk(browser)):
        t = _text(widget).strip()
        if t in {"Split all", "Collapse all", "Command palette", "Command palette  Ctrl+K"}:
            _forget(widget)
        elif t == "Bundle folder":
            try: widget.configure(text="Folder")
            except Exception: pass

    # Locate the top header (the parent containing Refresh/Folder/Close).
    header = None
    for widget in _walk(browser):
        try:
            child_text = {_text(c).strip() for c in widget.winfo_children()}
        except Exception:
            continue
        if "Refresh" in child_text and ("Folder" in child_text or "Bundle folder" in child_text or "Open bundle folder" in child_text):
            header = widget
            break
    if header is None:
        return

    browser._v302_auto_preview_var = getattr(browser, "_v302_auto_preview_var", tk.BooleanVar(value=True))
    browser._v302_tree_details_var = getattr(browser, "_v302_tree_details_var", tk.BooleanVar(value=False))

    def toggle_tree_details() -> None:
        tree = getattr(browser, "tree", None)
        if tree is None: return
        try:
            if bool(browser._v302_tree_details_var.get()):
                tree.configure(show="tree headings", displaycolumns=("kind", "size"))
            else:
                tree.configure(show="tree", displaycolumns=())
        except Exception:
            pass

    def show_advanced(tab_name: str | None = None) -> None:
        nb = getattr(browser, "_v226_side_notebook", None)
        if nb is None: return
        try:
            if not bool(getattr(browser, "_v302_advanced_visible", False)):
                nb.pack(fill="both", expand=True, pady=(6, 0))
                browser._v302_advanced_visible = True
                if hasattr(browser, "_v302_advanced_btn"):
                    browser._v302_advanced_btn.configure(text="Hide advanced")
            if tab_name:
                for tab in nb.tabs():
                    if str(nb.tab(tab, "text")).lower().startswith(tab_name.lower()):
                        nb.select(tab); break
        except Exception:
            pass

    def toggle_advanced() -> None:
        nb = getattr(browser, "_v226_side_notebook", None)
        if nb is None: return
        visible = bool(getattr(browser, "_v302_advanced_visible", False))
        try:
            if visible:
                nb.pack_forget(); browser._v302_advanced_visible = False
                if hasattr(browser, "_v302_advanced_btn"):
                    browser._v302_advanced_btn.configure(text="Advanced")
            else:
                show_advanced()
        except Exception:
            pass

    browser._v302_show_advanced = show_advanced
    browser._v302_toggle_advanced = toggle_advanced
    browser._v302_toggle_tree_details = toggle_tree_details

    menu = tk.Menu(browser, tearoff=False)
    menu.add_checkbutton(label="Preview on single click", variable=browser._v302_auto_preview_var)
    menu.add_checkbutton(label="Show Type / Size columns", variable=browser._v302_tree_details_var, command=toggle_tree_details)
    menu.add_separator()
    menu.add_command(label="Expand experiment tree", command=lambda: browser.expand_or_collapse_all(True))
    menu.add_command(label="Collapse experiment tree", command=lambda: browser.expand_or_collapse_all(False))
    menu.add_command(label="Saved views", command=lambda: show_advanced("Views"))
    menu.add_command(label="Analyze tools", command=lambda: show_advanced("Analyze"))
    menu.add_command(label="QA / activity", command=lambda: show_advanced("Activity"))
    try:
        from .v228_intelligence import open_command_palette
        menu.add_command(label="Command palette", command=lambda: open_command_palette(browser, core))
    except Exception:
        pass
    menu.add_command(label="Popout workspace", command=lambda: __import__("faradaiq.ui_tabs_v226", fromlist=["open_browser_popout"]).open_browser_popout(browser, core, ttk, tk))
    menu.add_separator()
    menu.add_command(label="Open bundle folder", command=browser.open_bundle_folder)

    def popup() -> None:
        try:
            x = more.winfo_rootx(); y = more.winfo_rooty() + more.winfo_height()
            menu.tk_popup(x, y)
        finally:
            try: menu.grab_release()
            except Exception: pass

    more = ttk.Button(header, text="More ▾", command=popup)
    more.pack(side="right", padx=(3, 3))
    browser._v302_more_menu = menu
    browser._v302_more_btn = more


def _simplify_tree(browser: Any, ttk: Any, tk: Any) -> None:
    tree = getattr(browser, "tree", None)
    if tree is None:
        return
    try:
        tree.configure(show="tree", displaycolumns=())
        tree.column("#0", width=390, minwidth=220, stretch=True)
    except Exception:
        pass

    tree_frame = getattr(tree, "master", None)
    left = getattr(tree_frame, "master", None) if tree_frame is not None else None
    if left is None:
        return

    for widget in list(left.winfo_children()):
        t = _text(widget).strip()
        if t in {"Wider map", "Narrower map", "Experiment map from H5/JSON"}:
            _forget(widget)
        try:
            if widget.winfo_class() == "TFrame":
                texts = {_text(c).strip() for c in widget.winfo_children()}
                if texts & {"Wider map", "Narrower map"}:
                    _forget(widget)
        except Exception:
            pass

    browse = ttk.Frame(left, style="Card.TFrame")
    _pack_before(browse, tree_frame, fill="x", pady=(0, 5))
    ttk.Label(browse, text="Browse", style="Section.TLabel").pack(side="left", padx=(0, 8))
    search_var = tk.StringVar(value="")
    browser._v302_search_var = search_var
    entry = ttk.Entry(browse, textvariable=search_var, width=24)
    entry.pack(side="left", fill="x", expand=True, padx=(0, 3))
    status = tk.StringVar(value="")
    browser._v302_search_status_var = status
    idx_state = {"term": "", "index": -1, "matches": []}

    def find_next(_event: Any = None) -> str:
        term = str(search_var.get() or "").strip().lower()
        if not term:
            status.set(""); return "break"
        if term != idx_state["term"]:
            matches = []
            for iid in _all_tree_items(tree):
                try:
                    item = tree.item(iid)
                    hay = " ".join([str(item.get("text") or "")] + [str(v) for v in (item.get("values") or ())]).lower()
                    if term in hay: matches.append(iid)
                except Exception:
                    pass
            idx_state.update(term=term, index=-1, matches=matches)
        matches = idx_state["matches"]
        if not matches:
            status.set("No matches"); return "break"
        idx_state["index"] = (idx_state["index"] + 1) % len(matches)
        iid = matches[idx_state["index"]]
        _open_parents(tree, iid)
        try:
            tree.selection_set(iid); tree.focus(iid); tree.see(iid)
        except Exception: pass
        status.set(f"{idx_state['index'] + 1}/{len(matches)}")
        return "break"

    ttk.Button(browse, text="Find", command=find_next).pack(side="left")
    ttk.Label(browse, textvariable=status, style="Muted.TLabel", width=7).pack(side="left", padx=(4, 0))
    entry.bind("<Return>", find_next)

    hint = ttk.Label(left, text="Click a technique or group to preview it. Ctrl-click adds selections.", style="Muted.TLabel", wraplength=400, justify="left")
    try: hint.pack(fill="x", pady=(0, 4), before=tree_frame)
    except Exception: hint.pack(fill="x", pady=(0, 4))

    # Debounced automatic preview. Heavy groups still honor the existing Max curves.
    def schedule_preview(_event: Any = None) -> None:
        try:
            if not bool(browser._v302_auto_preview_var.get()): return
        except Exception:
            return
        old = getattr(browser, "_v302_preview_after", None)
        if old:
            try: browser.after_cancel(old)
            except Exception: pass
        def run() -> None:
            try:
                family, _n = _simple_selected_context(browser)
                last_family = str(getattr(browser, "_v302_last_preview_family", ""))
                if family and family != "Nothing selected" and family != last_family:
                    try: browser.plot_mode_var.set("Auto")
                    except Exception: pass
                    browser._v302_last_preview_family = family
            except Exception:
                pass
            try:
                paths = list(browser.selected_paths(plottable_only=True) or [])
            except Exception:
                paths = []
            if not paths:
                return
            try:
                nb = getattr(browser, "_v226_center_notebook", None)
                tab = getattr(browser, "_v226_quick_plot_tab", None)
                if nb is not None and tab is not None: nb.select(tab)
            except Exception:
                pass
            try:
                browser.plot_selected(clear=True)
            except Exception as exc:
                try: browser.log(f"Auto preview skipped: {exc}")
                except Exception: pass
        try: browser._v302_preview_after = browser.after(280, run)
        except Exception: pass

    tree.bind("<<TreeviewSelect>>", schedule_preview, add="+")
    tree.bind("<Double-1>", lambda _e: (schedule_preview(), "break")[1], add="+")
    browser._v302_schedule_preview = schedule_preview
    try: browser.after(120, lambda: _default_tree_state(browser))
    except Exception: pass


def _simplify_quick_plot(browser: Any, ttk: Any) -> None:
    quick = getattr(browser, "_v226_quick_plot_tab", None)
    holder = getattr(browser, "plot_holder", None)
    if quick is None or holder is None:
        return
    old_rows = [c for c in quick.winfo_children() if c is not holder]
    for row in old_rows:
        _forget(row)

    bar = ttk.Frame(quick, style="Card.TFrame")
    _pack_before(bar, holder, fill="x", pady=(0, 5))
    ttk.Button(bar, text="View selected", style="Accent.TButton", command=lambda: browser.plot_selected(clear=True)).pack(side="left", padx=(0, 4))
    ttk.Button(bar, text="Add overlay", command=lambda: browser.plot_selected(clear=False)).pack(side="left", padx=(0, 4))
    ttk.Button(bar, text="Clear", command=browser.clear_plot).pack(side="left", padx=(0, 8))
    ttk.Label(bar, text="View", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    values = []
    try: values = list(browser.plot_mode.cget("values"))
    except Exception: pass
    combo = ttk.Combobox(bar, textvariable=browser.plot_mode_var, state="readonly", width=25, values=values)
    combo.pack(side="left", padx=(0, 5))
    try: combo.bind("<<ComboboxSelected>>", getattr(browser, "_v292_apply_tafel_mode"), add="+")
    except Exception: pass

    panel = getattr(browser, "_v206_pub_panel", None) or getattr(browser, "_v200_pub_panel", None)
    panel_rows: list[Any] = []
    canvas_frame = None
    if panel is not None:
        try:
            canvas_frame = panel.canvas_frame
            panel_rows = [c for c in panel.outer.winfo_children() if c is not canvas_frame]
            for r in panel_rows: _forget(r)
        except Exception:
            panel_rows = []

    browser._v302_plot_settings_visible = False
    def toggle_settings() -> None:
        visible = bool(getattr(browser, "_v302_plot_settings_visible", False))
        if visible:
            for r in old_rows + panel_rows: _forget(r)
            browser._v302_plot_settings_visible = False
            settings_btn.configure(text="Plot settings")
            return
        for r in old_rows:
            _pack_before(r, holder, fill="x", pady=(0, 4))
        if canvas_frame is not None:
            for r in panel_rows:
                _pack_before(r, canvas_frame, fill="x", pady=(0, 3))
        browser._v302_plot_settings_visible = True
        settings_btn.configure(text="Hide settings")
    settings_btn = ttk.Button(bar, text="Plot settings", command=toggle_settings)
    settings_btn.pack(side="right")
    browser._v302_plot_settings_btn = settings_btn


def _simplify_spatial(browser: Any, ttk: Any) -> None:
    tab = getattr(browser, "_v226_spatial_tab", None)
    options = getattr(browser, "_v226_spatial_options_notebook", None)
    if tab is None or options is None:
        return
    _forget(options)
    # Hide verbose result history by default; keep the concise status label.
    result = getattr(browser, "_v217_multidim_result_box", None)
    if result is not None: _forget(result)
    try:
        outer = options.master
    except Exception:
        return
    browser._v302_spatial_settings_visible = False
    row = ttk.Frame(outer, style="Card.TFrame")
    try: row.pack(fill="x", pady=(0, 4), before=options)
    except Exception: row.pack(fill="x", pady=(0, 4))
    ttk.Label(row, text="Advanced map controls are hidden until needed.", style="Muted.TLabel").pack(side="left")
    def toggle() -> None:
        vis = bool(getattr(browser, "_v302_spatial_settings_visible", False))
        if vis:
            _forget(options)
            if result is not None: _forget(result)
            btn.configure(text="Map settings")
            browser._v302_spatial_settings_visible = False
        else:
            options.pack(fill="x", pady=(0, 5), after=row)
            if result is not None: result.pack(fill="both", expand=True)
            btn.configure(text="Hide settings")
            browser._v302_spatial_settings_visible = True
    btn = ttk.Button(row, text="Map settings", command=toggle); btn.pack(side="right")
    browser._v302_spatial_settings_btn = btn


def _simplify_center_tabs(browser: Any) -> None:
    nb = getattr(browser, "_v226_center_notebook", None)
    if nb is None: return
    hidden_schema = None
    try:
        for tab in list(nb.tabs()):
            text = str(nb.tab(tab, "text"))
            if text == "Quick Plot": nb.tab(tab, text="View")
            elif text == "Spatial / Cube": nb.tab(tab, text="Map / 3D")
            elif "Schema" in text:
                hidden_schema = tab
                if not bool(getattr(browser, "_v302_schema_user_open", False)):
                    nb.hide(tab)
    except Exception:
        pass
    browser._v302_schema_tab = hidden_schema
    def show_schema() -> None:
        if hidden_schema is None: return
        browser._v302_schema_user_open = True
        try: nb.add(hidden_schema); nb.select(hidden_schema)
        except Exception: pass
    browser._v302_show_schema = show_schema
    # Remove the always-visible three-step / popout strip and scientific handoff strip.
    parent = nb.master
    for child in list(parent.winfo_children()):
        if child is nb: continue
        texts = {_text(w).strip() for w in _walk(child)}
        if any(t.startswith("1  Select data") for t in texts) or "Scientific handoff" in texts:
            _forget(child)


def _simplify_right(browser: Any, core: Mapping[str, Any], ttk: Any, tk: Any) -> None:
    nb = getattr(browser, "_v226_side_notebook", None)
    if nb is None: return
    right = nb.master
    # Hide title labels and bottom duplicate export; keep notebook alive but collapsed.
    for child in list(right.winfo_children()):
        if child is nb: continue
        tset = {_text(w).strip() for w in _walk(child)}
        if "Workspace" in tset or any(t.startswith("Export current view") for t in tset):
            _forget(child)
    nb.pack_forget()
    browser._v302_advanced_visible = False

    card = ttk.Frame(right, style="Card.TFrame", padding=(6, 4))
    card.pack(fill="x", expand=False)
    ttk.Label(card, text="Selected data", style="Section.TLabel").pack(anchor="w")
    context_var = tk.StringVar(value="Choose a technique, method, point, or result on the left.")
    browser._v302_context_var = context_var
    ttk.Label(card, textvariable=context_var, style="Muted.TLabel", wraplength=360, justify="left").pack(anchor="w", fill="x", pady=(0, 8))

    ocp_opts = ttk.Frame(card, style="Card.TFrame")
    action_frame = ttk.Frame(card, style="Card.TFrame")
    action_frame.pack(fill="x")
    footer = ttk.Frame(card, style="Card.TFrame")
    footer.pack(side="bottom", fill="x", pady=(8, 0))

    def call(name: str, *args: Any, **kwargs: Any) -> Any:
        fn = core.get(name)
        if callable(fn): return fn(*args, **kwargs)
        return None

    def ask_scilink() -> Any:
        fn = core.get("_v239_show_analysis_review")
        if callable(fn): return fn(browser, "scilink")
        return call("_v262_open_scilink_technique_prompt", browser, None)

    def clear_actions() -> None:
        for child in list(action_frame.winfo_children()):
            try: child.destroy()
            except Exception: pass
        for child in list(ocp_opts.winfo_children()):
            try: child.destroy()
            except Exception: pass
        ocp_opts.pack_forget()

    def add_button(text: str, command: Any, accent: bool = False) -> None:
        kwargs = {"text": text, "command": command}
        if accent: kwargs["style"] = "Accent.TButton"
        ttk.Button(action_frame, **kwargs).pack(fill="x", pady=(0, 4))

    def rebuild(_event: Any = None) -> None:
        kind, count = _simple_selected_context(browser)
        context_var.set(f"{kind} · {count} item{'s' if count != 1 else ''}\nPreview updates automatically when possible.")
        clear_actions()
        low = kind.lower()
        if "ocp" in low:
            ocp_opts.pack(fill="x", pady=(0, 6), before=action_frame)
            row = ttk.Frame(ocp_opts, style="Card.TFrame"); row.pack(fill="x", pady=(0, 3))
            ttk.Label(row, textvariable=getattr(browser, "_v291_ocp_tail_label_var", tk.StringVar(value="Final time window: 30%")), style="Muted.TLabel").pack(side="left")
            try:
                ttk.Scale(row, from_=1, to=100, variable=browser._v291_ocp_tail_percent_var, command=browser._v291_set_ocp_tail_percent).pack(side="right", fill="x", expand=True, padx=(6, 0))
            except Exception: pass
            row2 = ttk.Frame(ocp_opts, style="Card.TFrame"); row2.pack(fill="x")
            ttk.Label(row2, text="Horizontal axis", style="Muted.TLabel").pack(side="left")
            try: ttk.Combobox(row2, textvariable=browser._v294_ocp_axis_var, state="readonly", values=("Auto","Point","X","Y","Distance"), width=10).pack(side="right")
            except Exception: pass
            add_button("Average OCP vs position", lambda: call("_v291_open_ocp_tail_analysis", browser), True)
        elif "eis" in low:
            add_button("Analyze EIS with DearEIS", lambda: call("_v138_open_deareis_with_selected", browser, launch=True), True)
            add_button("Open in pyDRTtools", lambda: call("_v225_open_pydrttools_with_selected", browser))
        elif "iv/tafel" in low or "tafel" in low:
            add_button("Robust corrosion metrics", lambda: call("_v298_open_corrosion_metrics", browser), True)
            add_button("Tafel analysis", lambda: call("_v130_open_tafel_window", browser))
            add_button("Open in PySimpleEChem", browser._open_pysimple)
        elif "cv/lsv" in low:
            add_button("Open in PySimpleEChem", browser._open_pysimple, True)
            add_button("Tafel analysis", lambda: call("_v130_open_tafel_window", browser))
        elif "ca" in low or "cp" in low:
            add_button("Charge / transient metrics", lambda: call("_v262_open_ca_cp_analysis", browser), True)
        elif "nothing" in low:
            ttk.Label(action_frame, text="Click data on the left to view it and reveal the relevant analysis actions.", style="Muted.TLabel", wraplength=340, justify="left").pack(fill="x", pady=(4, 8))
        else:
            add_button("View selected", lambda: browser.plot_selected(clear=True), True)
        if "nothing" not in low:
            add_button("Ask SciLink", ask_scilink)

    browser._v302_refresh_actions = rebuild
    try: browser.tree.bind("<<TreeviewSelect>>", rebuild, add="+")
    except Exception: pass

    ttk.Button(footer, text="Export current view", command=lambda: browser.export_current_view("all")).pack(fill="x", pady=(0, 4))
    tools = ttk.Frame(footer, style="Card.TFrame"); tools.pack(fill="x")
    ttk.Button(tools, text="Views", command=lambda: browser._v302_show_advanced("Views")).pack(side="left", fill="x", expand=True, padx=(0, 2))
    browser._v302_advanced_btn = ttk.Button(tools, text="Advanced", command=browser._v302_toggle_advanced)
    browser._v302_advanced_btn.pack(side="left", fill="x", expand=True, padx=2)
    ttk.Button(tools, text="Log", command=lambda: browser._v302_show_advanced("Activity")).pack(side="left", fill="x", expand=True, padx=(2, 0))
    rebuild()


def _hide_transient_clutter(browser: Any) -> None:
    """Hide bars injected by historical wrappers after the simplified UI is built."""
    bar = getattr(browser, "_v239_handoff_bar", None)
    if bar is not None:
        _forget(bar)
    # Also locate any delayed handoff strip by its label.
    for w in list(_walk(browser)):
        if _text(w).strip() == "Scientific handoff":
            try: _forget(w.master)
            except Exception: pass


def _simplify(browser: Any, core: Mapping[str, Any], ttk: Any, tk: Any) -> None:
    if bool(getattr(browser, "_v302_simplified", False)):
        return
    browser._v302_simplified = True
    def _force_title() -> None:
        try: browser.title(f"FaradaIQ Studio {VERSION} — Data Browser")
        except Exception: pass
    _force_title()
    for delay in (80, 350, 900, 1800):
        try: browser.after(delay, _force_title)
        except Exception: pass
    try: browser.geometry("1660x940"); browser.minsize(1120, 700)
    except Exception: pass
    _simplify_header(browser, core, ttk, tk)
    _simplify_tree(browser, ttk, tk)
    _simplify_center_tabs(browser)
    _simplify_quick_plot(browser, ttk)
    _simplify_spatial(browser, ttk)
    _simplify_right(browser, core, ttk, tk)
    _hide_transient_clutter(browser)
    # Some historical schema/intelligence/handoff wrappers finish on idle callbacks.
    # Re-apply the compact tab policy after those callbacks settle.
    # Historical wrappers may recreate the handoff strip asynchronously. Keep
    # the simplified default stable by re-hiding that strip through startup.
    for delay in tuple(range(120, 6200, 180)) + (7000, 8500, 10000):
        try: browser.after(delay, lambda: (_simplify_center_tabs(browser), _hide_transient_clutter(browser)))
        except Exception: pass
    for delay in (450, 1200):
        try: browser.after(delay, lambda: _default_tree_state(browser) if not browser.tree.selection() else None)
        except Exception: pass

    # Add schema to More after the center tabs have been inspected.
    menu = getattr(browser, "_v302_more_menu", None)
    if menu is not None and callable(getattr(browser, "_v302_show_schema", None)) and getattr(browser, "_v302_schema_tab", None):
        try:
            menu.insert_command(7, label="Visual data schema", command=browser._v302_show_schema)
        except Exception:
            try: menu.add_command(label="Visual data schema", command=browser._v302_show_schema)
            except Exception: pass

    try:
        browser.log("v302 simplified Browser active: click-to-preview, contextual analysis, advanced controls collapsed by default.")
    except Exception:
        pass


def _install_handoff_hiders() -> None:
    """Keep legacy SciLink handoff bars available internally but hidden in the compact default UI."""
    for mod_name, fn_name in (("faradaiq.v239_scilink_edison", "_add_browser_handoff_bar"), ("faradaiq.v244_workflow_cleanup", "add_browser_handoff_bar")):
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            fn = getattr(mod, fn_name, None)
            if not callable(fn) or getattr(fn, "_v302_hidden_wrapper", False):
                continue
            def _make(orig):
                def wrapped(browser: Any, *args: Any, **kwargs: Any) -> Any:
                    out = orig(browser, *args, **kwargs)
                    if bool(getattr(browser, "_v302_simplified", False)):
                        _hide_transient_clutter(browser)
                        try: browser.after_idle(lambda: _hide_transient_clutter(browser))
                        except Exception: pass
                    return out
                wrapped._v302_hidden_wrapper = True
                return wrapped
            setattr(mod, fn_name, _make(fn))
        except Exception:
            pass


def install(core: MutableMapping[str, Any]) -> None:
    Browser = core.get("FaradaIQH5Browser")
    ttk = core.get("ttk"); tk = core.get("tk")
    if Browser is None or ttk is None or tk is None:
        raise RuntimeError("FaradaIQ Browser UI is unavailable")
    if bool(getattr(Browser, "_v302_simple_browser_installed", False)):
        return
    _install_handoff_hiders()
    previous = Browser._build_visible_ui_first
    previous_refresh = Browser.refresh_tree
    def build(self: Any) -> None:
        previous(self)
        try: self.after_idle(lambda: _simplify(self, core, ttk, tk))
        except Exception: _simplify(self, core, ttk, tk)
    def refresh_tree(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = previous_refresh(self, *args, **kwargs)
        def settle() -> None:
            try:
                if not self.tree.selection(): _default_tree_state(self)
                _hide_transient_clutter(self)
            except Exception: pass
        try: self.after(180, settle)
        except Exception: settle()
        return result
    Browser._build_visible_ui_first = build
    Browser.refresh_tree = refresh_tree
    Browser._v302_simple_browser_installed = True
    core["_v302_simplify_browser"] = lambda browser: _simplify(browser, core, ttk, tk)
