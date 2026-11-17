"""FaradaIQ Studio v273 task-oriented browser workspace.

This module keeps every browser function from v225 while reducing the number of
flat, competing tabs.  It groups the browser by the user's workflow:

* Quick Plot
* Spatial / Cube
* Views
* Analyze
* Activity

The synchronized popout uses four primary tabs (Data, Visualize, Analyze,
Activity) and secondary tabs only where they are useful.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

VERSION = "v292"
TAGLINE = "Electrochemical Intelligence for Analyze, Control and Discover"


def _forget(widget: Any) -> None:
    for method in ("pack_forget", "grid_remove", "place_forget"):
        try:
            getattr(widget, method)()
        except Exception:
            pass


def _walk(widget: Any):
    yield widget
    try:
        for child in widget.winfo_children():
            yield from _walk(child)
    except Exception:
        return


def _style_listbox(widget: Any) -> None:
    try:
        widget.configure(
            background="#020617",
            foreground="#e5eefb",
            selectbackground="#2563eb",
            selectforeground="#ffffff",
            font=("Segoe UI", 10),
            relief="solid",
            borderwidth=1,
            activestyle="dotbox",
            takefocus=True,
        )
    except Exception:
        pass


def _selected_context(browser: Any) -> tuple[str, int]:
    """Return the active Browser selection using files, tree records, and ancestry."""
    paths: list[Any] = []
    records: list[Mapping[str, Any]] = []
    try:
        from faradaiq.v262_technique_scilink import _browser_selection_paths
        paths = list(_browser_selection_paths(browser))
    except Exception:
        try:
            paths = list(browser.selected_paths(plottable_only=False) or [])
        except Exception:
            paths = []

    tree_items: list[str] = []
    evidence: list[str] = [str(p) for p in paths]
    try:
        tree = browser.tree
        tree_items = list(tree.selection() or [])
        try:
            from faradaiq.browser_ui_v209 import _tree_records
            for iid in tree_items:
                records.extend(_tree_records(browser, iid))
        except Exception:
            pass
        for iid in tree_items:
            current = iid
            visited: set[str] = set()
            while current and current not in visited:
                visited.add(current)
                item = tree.item(current) or {}
                evidence.append(str(item.get("text") or ""))
                evidence.extend(str(value) for value in (item.get("values") or ()))
                try:
                    current = tree.parent(current)
                except Exception:
                    current = ""
    except Exception:
        tree_items = []

    selected_path = getattr(browser, "selected_path", None)
    if selected_path:
        evidence.append(str(selected_path))
    for rec in records:
        evidence.extend(str(rec.get(key) or "") for key in ("path", "technique_key", "technique", "plot_mode", "label"))

    text = " ".join(evidence).lower()
    kinds: list[str] = []
    if any(token in text for token in ("eis", "nyquist", "bode", "impedance", "geis")):
        kinds.append("EIS")
    if any(token in text for token in ("drt", "gamma_tau", "gamma_ohm", "relaxation")):
        kinds.append("DRT")
    if any(token in text for token in ("cv", "lsv", "cyclic", "voltamm")):
        kinds.append("CV/LSV")
    if any(token in text for token in ("tafel", "iv_", "i-v", "polarization")):
        kinds.append("IV/Tafel")
    if any(token in text for token in ("ocp", "open_circuit", "open circuit")):
        kinds.append("OCP")
    if any(token in text for token in ("ca_", "chronoamper", "chrono amper", "chronopotstat", "deposition")):
        kinds.append("CA")
    if any(token in text for token in ("cp_", "chronopot", "chrono pot")):
        kinds.append("CP")
    if any(token in text for token in ("dpv", "swv", "stripping", "pulse volt")):
        kinds.append("Pulse/stripping")

    count = len(paths) or len(records) or len(tree_items)
    if not kinds and count:
        kinds.append("Mixed/other")
    return (" + ".join(dict.fromkeys(kinds)) if kinds else "Nothing selected", count)


def _sync_listbox_from_views(browser: Any, target: Any) -> None:
    """Populate the visible list directly from saved-view metadata."""
    try:
        target.delete(0, "end")
        views = list(getattr(browser, "plot_views", []) or [])
        if views:
            for idx, view in enumerate(views, 1):
                name = str(view.get("name") or f"View {idx:02d}")
                count = len(view.get("paths") or view.get("source_files") or [])
                mode = str(view.get("mode") or view.get("kind") or view.get("engine") or "Auto")
                target.insert("end", f"{idx:02d} {name} ({count} file(s), {mode})")
            return
        legacy = list(getattr(browser, "_v69_plot_views", []) or [])
        for view in legacy:
            target.insert("end", str(view.get("name") or "Plot/view"))
    except Exception:
        pass


def _load_view_from_index(browser: Any, index: int) -> None:
    try:
        browser.plot_view_list.selection_clear(0, "end")
        browser.plot_view_list.selection_set(index)
        browser.plot_view_list.see(index)
        browser.load_selected_plot_view()
    except Exception:
        pass


def _view_collection(browser: Any) -> list[Mapping[str, Any]]:
    """Return the active saved-view collection without rebuilding any plot."""
    views = getattr(browser, "plot_views", None)
    if isinstance(views, list):
        return views
    legacy = getattr(browser, "_v69_plot_views", None)
    return legacy if isinstance(legacy, list) else []


def _selected_view_summary(browser: Any) -> tuple[Optional[int], str]:
    try:
        selected = list(browser.plot_view_list.curselection())
    except Exception:
        selected = []
    if not selected:
        return None, "Select a saved view. Single-click only selects; it does not rebuild the plot."
    idx = int(selected[0])
    views = _view_collection(browser)
    if not (0 <= idx < len(views)):
        return idx, f"Selected row {idx + 1}. Double-click or use Open selected view."
    view = views[idx]
    name = str(view.get("name") or f"View {idx + 1}")
    file_count = len(view.get("paths") or view.get("source_files") or [])
    kind = str(view.get("kind") or view.get("mode") or view.get("engine") or "saved view")
    return idx, f"Selected: {name} | {kind} | {file_count} source file(s). Double-click, press Enter, or use Open selected view."


def _build_views_panel(browser: Any, parent: Any, ttk: Any, tk: Any) -> None:
    ttk.Label(parent, text="Saved plot/views", style="Section.TLabel").pack(anchor="w")
    ttk.Label(
        parent,
        text="Single-click selects instantly. Reopen only by double-click, Enter, or Open selected view, preventing large views from rebuilding while you browse.",
        style="Muted.TLabel",
        wraplength=500,
        justify="left",
    ).pack(anchor="w", fill="x", pady=(0, 5))

    frame = ttk.Frame(parent, style="Card.TFrame")
    frame.pack(fill="both", expand=True)
    list_frame = ttk.Frame(frame, style="Card.TFrame")
    list_frame.pack(fill="both", expand=True)
    browser.plot_view_list = tk.Listbox(
        list_frame, height=11, exportselection=False, selectmode="browse"
    )
    _style_listbox(browser.plot_view_list)
    y_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=browser.plot_view_list.yview)
    x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=browser.plot_view_list.xview)
    browser.plot_view_list.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
    browser.plot_view_list.pack(side="left", fill="both", expand=True)
    y_scroll.pack(side="right", fill="y")
    x_scroll.pack(fill="x")

    browser._v273_view_status_var = tk.StringVar(
        value="Select a saved view. Single-click only selects; it does not rebuild the plot."
    )
    status = ttk.Label(
        parent, textvariable=browser._v273_view_status_var, style="Muted.TLabel",
        wraplength=520, justify="left"
    )
    status.pack(anchor="w", fill="x", pady=(4, 2))

    def select_only(_event: Any = None) -> None:
        _idx, summary = _selected_view_summary(browser)
        browser._v273_view_status_var.set(summary)

    def open_selected(_event: Any = None) -> str:
        idx, summary = _selected_view_summary(browser)
        if idx is None:
            browser._v273_view_status_var.set(summary)
            return "break"
        if bool(getattr(browser, "_v273_view_open_busy", False)):
            browser._v273_view_status_var.set("A saved view is already opening. Please wait for it to finish.")
            return "break"
        browser._v273_view_open_busy = True
        try:
            browser._v273_open_view_btn.configure(state="disabled")
        except Exception:
            pass
        browser._v273_view_status_var.set(f"Opening selected view {idx + 1}…")

        def run() -> None:
            try:
                # Preserve the index that the user explicitly requested even if
                # another widget briefly changes focus before this idle callback.
                browser.plot_view_list.selection_clear(0, "end")
                browser.plot_view_list.selection_set(idx)
                browser.plot_view_list.activate(idx)
                browser.plot_view_list.see(idx)
                browser.load_selected_plot_view()
                browser._v273_loaded_view_index = idx
                _idx, done = _selected_view_summary(browser)
                browser._v273_view_status_var.set("Opened. " + done)
            except Exception as exc:
                browser._v273_view_status_var.set(f"Could not open saved view: {exc}")
                try:
                    browser.log(f"Could not open saved view {idx + 1}: {exc}")
                except Exception:
                    pass
            finally:
                browser._v273_view_open_busy = False
                try:
                    browser._v273_open_view_btn.configure(state="normal")
                except Exception:
                    pass

        # Let Tk paint the selection and status before any Matplotlib or file work.
        try:
            browser.after(25, run)
        except Exception:
            run()
        return "break"

    # Selection is deliberately lightweight. Large CA/cube views are no longer
    # reconstructed for every mouse click or keyboard-arrow movement.
    browser.plot_view_list.bind("<<ListboxSelect>>", select_only)
    browser.plot_view_list.bind("<Double-Button-1>", open_selected)
    browser.plot_view_list.bind("<Return>", open_selected)
    browser.plot_view_list.bind("<KP_Enter>", open_selected)
    _sync_listbox_from_views(browser, browser.plot_view_list)

    manage = ttk.LabelFrame(parent, text="Manage view", padding=(5, 5))
    manage.pack(fill="x", pady=(6, 4))
    browser._v273_open_view_btn = ttk.Button(
        manage, text="Open selected view", command=open_selected
    )
    browser._v273_open_view_btn.pack(fill="x", pady=(0, 4))
    manage_row = ttk.Frame(manage, style="Card.TFrame"); manage_row.pack(fill="x")
    ttk.Button(manage_row, text="Rename", command=browser.rename_selected_view).pack(side="left", fill="x", expand=True, padx=(0, 2))
    ttk.Button(manage_row, text="Remove", command=browser.clear_selected_view).pack(side="left", fill="x", expand=True, padx=(2, 0))
    ttk.Button(manage, text="Clear all views", command=browser.clear_plot_view_list).pack(fill="x", pady=(4, 0))

    persist = ttk.LabelFrame(parent, text="Save / restore", padding=(5, 5))
    persist.pack(fill="x", pady=(0, 4))
    row = ttk.Frame(persist, style="Card.TFrame"); row.pack(fill="x")
    ttk.Button(row, text="Save views", command=browser.save_plotviews_dialog).pack(side="left", fill="x", expand=True, padx=(0, 2))
    ttk.Button(row, text="Load views", command=browser.load_plotviews_dialog).pack(side="left", fill="x", expand=True, padx=(2, 0))
    row2 = ttk.Frame(persist, style="Card.TFrame"); row2.pack(fill="x", pady=(4, 0))
    ttk.Button(row2, text="Save session", command=browser.save_browser_session_dialog).pack(side="left", fill="x", expand=True, padx=(0, 2))
    ttk.Button(row2, text="Load session", command=browser.load_browser_session_dialog).pack(side="left", fill="x", expand=True, padx=(2, 0))


def _build_analyze_panel(browser: Any, parent: Any, core: Mapping[str, Any], ttk: Any, tk: Any) -> None:
    if not hasattr(browser, "_v226_analysis_context_var"):
        browser._v226_analysis_context_var = tk.StringVar(value="Selected data: Nothing selected")
    if not hasattr(browser, "_v226_ai_query_var"):
        browser._v226_ai_query_var = tk.StringVar(value="")
    if not hasattr(browser, "_v291_ocp_tail_percent_var"):
        browser._v291_ocp_tail_percent = 30.0
        browser._v291_ocp_tail_percent_var = tk.DoubleVar(value=30.0)
    if not hasattr(browser, "_v291_ocp_tail_label_var"):
        browser._v291_ocp_tail_label_var = tk.StringVar(value="Final time window: 30%")
    if not hasattr(browser, "_v294_ocp_axis_var"):
        browser._v294_ocp_axis = "Auto"
        browser._v294_ocp_axis_var = tk.StringVar(value="Auto")

    def _v291_set_ocp_tail_percent(value: Any = None) -> None:
        try:
            pct = float(value if value is not None else browser._v291_ocp_tail_percent_var.get())
            pct = max(1.0, min(100.0, round(pct)))
            browser._v291_ocp_tail_percent = pct
            if abs(float(browser._v291_ocp_tail_percent_var.get()) - pct) > 0.25:
                browser._v291_ocp_tail_percent_var.set(pct)
            browser._v291_ocp_tail_label_var.set(f"Final time window: {pct:g}%")
        except Exception:
            pass

    browser._v291_set_ocp_tail_percent = _v291_set_ocp_tail_percent

    ttk.Label(parent, text="Analyze selected data", style="Section.TLabel").pack(anchor="w")
    ttk.Label(parent, textvariable=browser._v226_analysis_context_var, style="Muted.TLabel", wraplength=360).pack(anchor="w", fill="x", pady=(0, 5))

    handoff = ttk.LabelFrame(parent, text="Technique-aware handoff", padding=(6, 5))
    handoff.pack(fill="x", pady=(0, 6))
    eis = ttk.Frame(handoff, style="Card.TFrame"); eis.pack(fill="x", pady=(0, 4))
    ttk.Label(eis, text="EIS", width=10, style="Muted.TLabel").pack(side="left")
    browser._v226_deareis_btn = ttk.Button(eis, text="DearEIS", command=lambda: core.get("_v138_open_deareis_with_selected", lambda *_a, **_k: None)(browser, launch=True))
    browser._v226_deareis_btn.pack(side="left", fill="x", expand=True, padx=(0, 2))
    browser._v226_pydrt_btn = ttk.Button(eis, text="pyDRTtools", command=lambda: core.get("_v225_open_pydrttools_with_selected", lambda *_a, **_k: None)(browser))
    browser._v226_pydrt_btn.pack(side="left", fill="x", expand=True, padx=(2, 0))

    cv = ttk.Frame(handoff, style="Card.TFrame"); cv.pack(fill="x", pady=(0, 4))
    ttk.Label(cv, text="CV / IV", width=10, style="Muted.TLabel").pack(side="left", anchor="n", pady=(3, 0))
    cv_body = ttk.Frame(cv, style="Card.TFrame"); cv_body.pack(side="left", fill="x", expand=True)
    cv_actions = ttk.Frame(cv_body, style="Card.TFrame"); cv_actions.pack(fill="x")
    browser._v226_pysimple_btn = ttk.Button(cv_actions, text="PySimpleEChem", command=browser._open_pysimple)
    browser._v226_pysimple_btn.pack(side="left", fill="x", expand=True, padx=(0, 2))
    browser._v226_tafel_btn = ttk.Button(cv_actions, text="Tafel analysis", command=lambda: core.get("_v130_open_tafel_window", lambda *_a, **_k: None)(browser))
    browser._v226_tafel_btn.pack(side="left", fill="x", expand=True, padx=(2, 0))
    cv_corrosion = ttk.Frame(cv_body, style="Card.TFrame"); cv_corrosion.pack(fill="x", pady=(3, 0))
    browser._v298_corrosion_metrics_btn = ttk.Button(cv_corrosion, text="Robust corrosion metrics · point/spatial", command=lambda: core.get("_v298_open_corrosion_metrics", lambda *_a, **_k: None)(browser))
    browser._v298_corrosion_metrics_btn.pack(fill="x", expand=True)

    ca = ttk.Frame(handoff, style="Card.TFrame"); ca.pack(fill="x", pady=(0, 4))
    ttk.Label(ca, text="CA / CP", width=10, style="Muted.TLabel").pack(side="left")
    browser._v262_ca_cp_btn = ttk.Button(ca, text="Charge / transient metrics", command=lambda: core.get("_v262_open_ca_cp_analysis", lambda *_a, **_k: None)(browser))
    browser._v262_ca_cp_btn.pack(side="left", fill="x", expand=True, padx=(0, 2))
    browser._v262_ca_cp_scilink_btn = ttk.Button(ca, text="Ask SciLink: CA/CP analysis…", command=lambda: core.get("_v262_open_scilink_technique_prompt", lambda *_a, **_k: None)(browser, "CA charge and transient metrics"))
    browser._v262_ca_cp_scilink_btn.pack(side="left", fill="x", expand=True, padx=(2, 0))

    ocp = ttk.Frame(handoff, style="Card.TFrame"); ocp.pack(fill="x", pady=(0, 4))
    ttk.Label(ocp, text="OCP", width=10, style="Muted.TLabel").pack(side="left", anchor="n", pady=(3, 0))
    ocp_body = ttk.Frame(ocp, style="Card.TFrame"); ocp_body.pack(side="left", fill="x", expand=True)
    ocp_slider_row = ttk.Frame(ocp_body, style="Card.TFrame"); ocp_slider_row.pack(fill="x")
    ttk.Label(ocp_slider_row, textvariable=browser._v291_ocp_tail_label_var, style="Muted.TLabel", width=22).pack(side="left")
    ttk.Scale(ocp_slider_row, from_=1, to=100, variable=browser._v291_ocp_tail_percent_var, command=_v291_set_ocp_tail_percent).pack(side="left", fill="x", expand=True, padx=(4, 0))
    ocp_axis_row = ttk.Frame(ocp_body, style="Card.TFrame"); ocp_axis_row.pack(fill="x", pady=(3, 0))
    ttk.Label(ocp_axis_row, text="Horizontal axis", style="Muted.TLabel", width=22).pack(side="left")
    ttk.Combobox(ocp_axis_row, textvariable=browser._v294_ocp_axis_var, state="readonly", values=("Auto", "Point", "X", "Y", "Distance"), width=12).pack(side="left", fill="x", expand=True, padx=(4, 0))
    ocp_buttons = ttk.Frame(ocp_body, style="Card.TFrame"); ocp_buttons.pack(fill="x", pady=(3, 0))
    browser._v291_ocp_avg_btn = ttk.Button(ocp_buttons, text="Average OCP · point/spatial", command=lambda: core.get("_v291_open_ocp_tail_analysis", lambda *_a, **_k: None)(browser))
    browser._v291_ocp_avg_btn.pack(side="left", fill="x", expand=True, padx=(0, 2))
    browser._v291_ocp_scilink_btn = ttk.Button(ocp_buttons, text="Ask SciLink: OCP spatial…", command=lambda: core.get("_v291_open_ocp_scilink_handoff", lambda *_a, **_k: None)(browser))
    browser._v291_ocp_scilink_btn.pack(side="left", fill="x", expand=True, padx=(2, 0))

    sl = ttk.Frame(handoff, style="Card.TFrame"); sl.pack(fill="x")
    ttk.Label(sl, text="SciLink", width=10, style="Muted.TLabel").pack(side="left")
    browser._v262_scilink_template_btn = ttk.Button(sl, text="Ask SciLink with technique template…", command=lambda: core.get("_v262_open_scilink_technique_prompt", lambda *_a, **_k: None)(browser, None))
    browser._v262_scilink_template_btn.pack(side="left", fill="x", expand=True)

    smart = ttk.LabelFrame(parent, text="Smart Compare", padding=(6, 5))
    smart.pack(fill="both", expand=True)
    ttk.Entry(smart, textvariable=browser._v226_ai_query_var).pack(fill="x", pady=(0, 4))
    actions = ttk.Frame(smart, style="Card.TFrame"); actions.pack(fill="x")

    def _copy_query() -> None:
        try:
            browser.ai_compare_entry.delete(0, "end")
            browser.ai_compare_entry.insert(0, browser._v226_ai_query_var.get())
        except Exception:
            pass

    def _suggest() -> None:
        _copy_query(); browser.suggest_comparisons()

    def _ask() -> None:
        _copy_query(); browser.ask_ai_comparisons()

    ttk.Button(actions, text="Suggest", command=_suggest).pack(side="left", fill="x", expand=True, padx=(0, 2))
    ttk.Button(actions, text="Ask AI", command=_ask).pack(side="left", fill="x", expand=True, padx=2)
    ttk.Button(actions, text="Add to views", command=browser.apply_selected_suggestion).pack(side="left", fill="x", expand=True, padx=(2, 0))

    browser.suggestion_list = tk.Listbox(smart, height=8, exportselection=False)
    _style_listbox(browser.suggestion_list)
    browser.suggestion_list.pack(fill="both", expand=True, pady=(5, 0))

    def refresh_context(_event: Any = None) -> None:
        kind, count = _selected_context(browser)
        browser._v226_analysis_context_var.set(f"Selected data: {kind} • {count} file{'s' if count != 1 else ''}")
        # Keep every handoff control clickable.  A click performs a fresh
        # selection/technique validation and shows a clear compatibility message
        # instead of presenting inert controls that appear broken.
        for button in (
            browser._v226_deareis_btn, browser._v226_pydrt_btn,
            browser._v226_pysimple_btn, browser._v226_tafel_btn,
            browser._v262_ca_cp_btn, browser._v262_ca_cp_scilink_btn,
            browser._v291_ocp_avg_btn, browser._v291_ocp_scilink_btn,
            browser._v262_scilink_template_btn,
        ):
            try:
                button.configure(state="normal", takefocus=True)
            except Exception:
                pass

    browser._v226_refresh_analysis_context = refresh_context
    try:
        browser.tree.bind("<<TreeviewSelect>>", refresh_context, add="+")
    except Exception:
        pass
    refresh_context()


def _build_activity_panel(browser: Any, parent: Any, ttk: Any, tk: Any) -> None:
    tabs = ttk.Notebook(parent)
    tabs.pack(fill="both", expand=True)
    qa_tab = ttk.Frame(tabs, style="Card.TFrame", padding=(5, 5)); tabs.add(qa_tab, text="QA")
    log_tab = ttk.Frame(tabs, style="Card.TFrame", padding=(5, 5)); tabs.add(log_tab, text="Log")

    qhead = ttk.Frame(qa_tab, style="Card.TFrame"); qhead.pack(fill="x")
    ttk.Label(qhead, text="Bundle quality summary", style="Section.TLabel").pack(side="left")
    ttk.Button(qhead, text="Refresh", command=browser._write_qa_summary).pack(side="right")
    browser.qa_text = tk.Text(qa_tab, wrap="word", height=14)
    browser.qa_text.pack(fill="both", expand=True, pady=(4, 0))
    try: browser._style_text(browser.qa_text)
    except Exception: pass

    lhead = ttk.Frame(log_tab, style="Card.TFrame"); lhead.pack(fill="x")
    ttk.Label(lhead, text="Browser activity", style="Section.TLabel").pack(side="left")
    browser.log_text = tk.Text(log_tab, wrap="word", height=16)
    browser.log_text.pack(fill="both", expand=True, pady=(4, 0))
    try: browser._style_text(browser.log_text)
    except Exception: pass
    browser._write_qa_summary()
    browser._v226_activity_notebook = tabs


def _find_right_panel(browser: Any) -> Optional[Any]:
    try:
        return browser.plot_view_list.master.master.master
    except Exception:
        pass
    # Fallback: the browser's first Panedwindow normally has left, center, right panes.
    try:
        for widget in _walk(browser):
            if widget.winfo_class() == "TPanedwindow":
                panes = widget.panes()
                if len(panes) >= 3:
                    return browser.nametowidget(panes[-1])
    except Exception:
        pass
    return None


def _redesign_side_panel(browser: Any, core: Mapping[str, Any], ttk: Any, tk: Any) -> None:
    right = _find_right_panel(browser)
    if right is None:
        return
    for child in list(right.winfo_children()):
        _forget(child)

    title = ttk.Frame(right, style="Card.TFrame")
    title.pack(fill="x", pady=(0, 4))
    ttk.Label(title, text="Workspace", style="Section.TLabel").pack(side="left")
    ttk.Label(title, text="Views • Analyze • Activity", style="Muted.TLabel").pack(side="right")

    notebook = ttk.Notebook(right)
    notebook.pack(fill="both", expand=True)
    views = ttk.Frame(notebook, style="Card.TFrame", padding=(7, 7)); notebook.add(views, text="Views")
    analyze = ttk.Frame(notebook, style="Card.TFrame", padding=(7, 7)); notebook.add(analyze, text="Analyze")
    activity = ttk.Frame(notebook, style="Card.TFrame", padding=(7, 7)); notebook.add(activity, text="Activity")

    _build_views_panel(browser, views, ttk, tk)
    # The Smart Compare callbacks expect ai_compare_entry to exist. Keep a hidden
    # compatibility entry; the visible entry shares the v227 StringVar.
    browser.ai_compare_entry = ttk.Entry(analyze, textvariable=getattr(browser, "_v226_ai_query_var", None))
    browser.ai_compare_entry.pack_forget()
    _build_analyze_panel(browser, analyze, core, ttk, tk)
    _build_activity_panel(browser, activity, ttk, tk)

    ttk.Button(
        right,
        text="Export current view — figure + data + H5 + summary",
        command=lambda: browser.export_current_view("all"),
    ).pack(fill="x", pady=(6, 0))

    browser._v226_side_notebook = notebook
    browser._v226_views_tab = views
    browser._v226_analyze_tab = analyze
    browser._v226_activity_tab = activity


def _apply_preset(browser: Any, name: str, multidim: Any) -> None:
    if name == "Raw measured":
        browser._v216_render_mode_var.set("Measured points")
        browser._v222_surface_model_var.set("Standard interpolation")
        browser._v216_smoothing_var.set("None")
    elif name == "Origin-style smooth":
        browser._v216_render_mode_var.set("Contour + points")
        browser._v222_surface_model_var.set("Origin-style smooth")
        browser._v216_smoothing_var.set("Light")
        browser._v216_adaptive_contours_var.set(True)
        browser._v216_mask_hull_var.set(True)
        browser._v222_clip_overshoot_var.set(True)
    elif name == "SECCM physical footprint":
        browser._v216_render_mode_var.set("Contour + points")
        browser._v222_surface_model_var.set("SECCM tip footprint")
        browser._v216_adaptive_contours_var.set(True)
        browser._v216_mask_hull_var.set(True)
        browser._v222_clip_overshoot_var.set(True)
    elif name == "Publication contour":
        browser._v216_render_mode_var.set("Contour + points")
        browser._v216_adaptive_contours_var.set(True)
        browser._v216_contour_line_width_var.set("1.1")
        browser._v216_contour_labels_var.set(False)
        browser._v222_grid_quality_var.set("Publication")
    try:
        from faradaiq.ui_tabs_v217 import _collect_multidim_options
        _collect_multidim_options(browser, getattr(multidim, "_v216_options", None))
    except Exception:
        pass


def _build_spatial_workspace(browser: Any, parent: Any, core: Mapping[str, Any], ttk: Any, tk: Any, filedialog: Any) -> Any:
    from faradaiq import multidim_v216 as multidim
    from faradaiq.ui_tabs_v217 import _ensure_multidim_vars, _collect_multidim_options, _add_multidim_plot_view

    _ensure_multidim_vars(browser, tk)
    if not hasattr(browser, "_v226_preset_var"):
        browser._v226_preset_var = tk.StringVar(value="Origin-style smooth")

    outer = ttk.Frame(parent, style="Card.TFrame", padding=(8, 8))
    outer.pack(fill="both", expand=True)

    header = ttk.Frame(outer, style="Card.TFrame"); header.pack(fill="x", pady=(0, 5))
    titles = ttk.Frame(header, style="Card.TFrame"); titles.pack(side="left", fill="x", expand=True)
    ttk.Label(titles, text="Spatial, 3D, and hyperspectral visualization", style="Section.TLabel").pack(anchor="w")
    ttk.Label(titles, text="Choose a view, then refine only the settings that matter.", style="Muted.TLabel").pack(anchor="w")

    primary = ttk.Frame(outer, style="Card.TFrame"); primary.pack(fill="x", pady=(0, 5))
    ttk.Label(primary, text="View", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(primary, textvariable=browser._v216_viz_mode_var, state="readonly", width=23,
                 values=("Altair interactive 2D", "Plotly true 3D", "Hyperspectral slider / cube")).pack(side="left", padx=(0, 7))
    ttk.Label(primary, text="Metric", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(primary, textvariable=browser._v216_viz_metric_var, state="readonly", width=13,
                 values=("Auto", "steady_state", "integral", "absolute_peak", "endpoint", "mean", "minimum", "maximum", "range")).pack(side="left", padx=(0, 7))
    ttk.Label(primary, text="Preset", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(primary, textvariable=browser._v226_preset_var, state="readonly", width=22,
                 values=("Raw measured", "Origin-style smooth", "SECCM physical footprint", "Publication contour")).pack(side="left", padx=(0, 3))
    ttk.Button(primary, text="Apply preset", command=lambda: _apply_preset(browser, browser._v226_preset_var.get(), multidim)).pack(side="left")
    action_row = ttk.Frame(outer, style="Card.TFrame"); action_row.pack(fill="x", pady=(0, 5))
    open_btn = ttk.Button(action_row, text="Create visualization from selected data")
    open_btn.pack(fill="x")

    options = ttk.Notebook(outer)
    options.pack(fill="x", pady=(0, 5))
    basics = ttk.Frame(options, style="Card.TFrame", padding=(7, 7)); options.add(basics, text="Data & display")
    surface = ttk.Frame(options, style="Card.TFrame", padding=(7, 7)); options.add(surface, text="Surface")
    contours = ttk.Frame(options, style="Card.TFrame", padding=(7, 7)); options.add(contours, text="Color & contours")
    seccm = ttk.Frame(options, style="Card.TFrame", padding=(7, 7)); options.add(seccm, text="SECCM")
    background = ttk.Frame(options, style="Card.TFrame", padding=(7, 7)); options.add(background, text="Background")

    # Data & display
    r = ttk.Frame(basics, style="Card.TFrame"); r.pack(fill="x", pady=(0, 4))
    for label, var, values, width in (
        ("X/Y unit", browser._v216_spatial_unit_var, ("Scientific", "nm", "µm", "mm", "m"), 10),
        ("Signal unit", browser._v216_signal_unit_var, ("Auto", "Scientific", "fA", "pA", "nA", "µA", "mA", "A", "mV", "V", "Ω", "kΩ", "MΩ", "fC", "pC", "nC", "µC", "mC", "C"), 12),
    ):
        ttk.Label(r, text=label, style="Muted.TLabel").pack(side="left", padx=(0, 3))
        ttk.Combobox(r, textvariable=var, state="readonly", width=width, values=values).pack(side="left", padx=(0, 12))
    r1b = ttk.Frame(basics, style="Card.TFrame"); r1b.pack(fill="x", pady=(0, 4))
    for label, var, values, width in (
        ("Slider unit", browser._v216_spectral_unit_var, ("Auto", "Scientific", "mV", "V", "µs", "ms", "s", "Hz", "kHz", "MHz", "nm", "µm", "mm", "m"), 12),
        ("Map style", browser._v216_render_mode_var, ("Measured points", "Heatmap", "Filled contour", "Contour lines", "Contour + points"), 20),
    ):
        ttk.Label(r1b, text=label, style="Muted.TLabel").pack(side="left", padx=(0, 3))
        ttk.Combobox(r1b, textvariable=var, state="readonly", width=width, values=values).pack(side="left", padx=(0, 12))
    r2 = ttk.Frame(basics, style="Card.TFrame"); r2.pack(fill="x")
    ttk.Checkbutton(r2, text="Overlay measured points", variable=browser._v216_overlay_points_var).pack(side="left", padx=(0, 8))
    ttk.Label(r2, text="Point size", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Entry(r2, textvariable=browser._v216_point_size_var, width=6).pack(side="left", padx=(0, 8))
    ttk.Label(r2, text="Opacity", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Entry(r2, textvariable=browser._v216_opacity_var, width=6).pack(side="left", padx=(0, 8))
    ttk.Checkbutton(r2, text="Lock X/Y aspect", variable=browser._v217_lock_xy_aspect_var).pack(side="left")
    ttk.Label(r2, text="Off = free rectangular zoom", style="Muted.TLabel").pack(side="left", padx=(5, 0))

    # Surface
    r = ttk.Frame(surface, style="Card.TFrame"); r.pack(fill="x", pady=(0, 4))
    ttk.Label(r, text="Reconstruction", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Combobox(r, textvariable=browser._v222_surface_model_var, state="readonly", width=24,
                 values=("Standard interpolation", "Origin-style smooth", "SECCM tip footprint")).pack(side="left", padx=(0, 8))
    ttk.Label(r, text="Interpolation", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Combobox(r, textvariable=browser._v216_interpolation_var, state="readonly", width=10,
                 values=("None", "Nearest", "Linear", "Cubic")).pack(side="left", padx=(0, 8))
    ttk.Label(r, text="Smoothing", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Combobox(r, textvariable=browser._v216_smoothing_var, state="readonly", width=10,
                 values=("None", "Light", "Medium", "Strong")).pack(side="left", padx=(0, 8))
    ttk.Label(r, text="Grid", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Combobox(r, textvariable=browser._v222_grid_quality_var, state="readonly", width=12,
                 values=("Auto", "Fine", "Publication")).pack(side="left")
    r2 = ttk.Frame(surface, style="Card.TFrame"); r2.pack(fill="x")
    ttk.Checkbutton(r2, text="Mask outside measured hull", variable=browser._v216_mask_hull_var).pack(side="left", padx=(0, 10))
    ttk.Checkbutton(r2, text="Prevent interpolation overshoot", variable=browser._v222_clip_overshoot_var).pack(side="left")

    # Color & contours
    r = ttk.Frame(contours, style="Card.TFrame"); r.pack(fill="x", pady=(0, 4))
    ttk.Label(r, text="Palette", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Combobox(r, textvariable=browser._v216_palette_var, state="readonly", width=12,
                 values=("Viridis", "Cividis", "Turbo", "Plasma", "Inferno", "Magma", "Greys", "RdBu", "Picnic", "Portland", "Jet")).pack(side="left", padx=(0, 3))
    ttk.Checkbutton(r, text="Reverse", variable=browser._v216_reverse_palette_var).pack(side="left", padx=(0, 8))
    ttk.Label(r, text="Range", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Combobox(r, textvariable=browser._v216_range_mode_var, state="readonly", width=18,
                 values=("Robust (2–98%)", "Full data range", "Manual min/max")).pack(side="left", padx=(0, 4))
    ttk.Combobox(r, textvariable=browser._v222_range_scope_var, state="readonly", width=16,
                 values=("Lock across cube", "Per frame")).pack(side="left", padx=(0, 4))
    ttk.Entry(r, textvariable=browser._v216_range_min_var, width=8).pack(side="left", padx=(0, 3))
    ttk.Entry(r, textvariable=browser._v216_range_max_var, width=8).pack(side="left")
    r2 = ttk.Frame(contours, style="Card.TFrame"); r2.pack(fill="x")
    ttk.Label(r2, text="Levels", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Entry(r2, textvariable=browser._v216_contour_levels_var, width=6).pack(side="left", padx=(0, 5))
    ttk.Checkbutton(r2, text="Smart optimization", variable=browser._v216_adaptive_contours_var).pack(side="left", padx=(0, 8))
    ttk.Label(r2, text="Line width", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Entry(r2, textvariable=browser._v216_contour_line_width_var, width=6).pack(side="left", padx=(0, 8))
    ttk.Checkbutton(r2, text="Contour labels", variable=browser._v216_contour_labels_var).pack(side="left")

    # SECCM
    if not hasattr(browser, "_v222_spacing_label_var"):
        browser._v222_spacing_label_var = tk.StringVar(value="")
    r = ttk.Frame(seccm, style="Card.TFrame"); r.pack(fill="x", pady=(0, 4))
    ttk.Label(r, text="Tip diameter / µm", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Entry(r, textvariable=browser._v222_tip_diameter_var, width=9).pack(side="left", padx=(0, 10))
    ttk.Label(r, text="Overlap / %", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Entry(r, textvariable=browser._v222_overlap_var, width=8).pack(side="left", padx=(0, 10))
    ttk.Button(r, text="Use physical footprint", command=lambda: _apply_preset(browser, "SECCM physical footprint", multidim)).pack(side="left")
    ttk.Label(seccm, textvariable=browser._v222_spacing_label_var, style="Muted.TLabel").pack(anchor="w")
    ttk.Label(seccm, text="The measured points remain unchanged; only the reconstructed field uses the physical probe footprint.", style="Muted.TLabel", wraplength=1000).pack(anchor="w", pady=(4, 0))

    def update_spacing(*_args: Any) -> None:
        try:
            diameter = max(0.001, float(browser._v222_tip_diameter_var.get()))
            overlap = max(0.0, min(99.5, float(browser._v222_overlap_var.get())))
            spacing = diameter * (1.0 - overlap / 100.0)
            browser._v222_spacing_label_var.set(f"Nominal center spacing: {spacing:g} µm • footprint FWHM: {diameter:g} µm")
        except Exception:
            browser._v222_spacing_label_var.set("Enter numeric tip diameter and overlap.")
    try:
        browser._v222_tip_diameter_var.trace_add("write", update_spacing)
        browser._v222_overlap_var.trace_add("write", update_spacing)
    except Exception:
        pass
    update_spacing()

    # Background
    bg_label = ttk.Label(background, text="No background loaded", style="Muted.TLabel")
    bg_label.pack(anchor="w", fill="x", pady=(0, 5))
    row = ttk.Frame(background, style="Card.TFrame"); row.pack(fill="x", pady=(0, 4))
    ttk.Checkbutton(row, text="Use background image", variable=browser._v216_background_enabled_var).pack(side="left", padx=(0, 8))

    def choose_background() -> None:
        if filedialog is None:
            return
        path = filedialog.askopenfilename(parent=browser, title="Choose spatial background image",
                                          filetypes=[("Images", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp"), ("All files", "*.*")])
        if path:
            browser._v216_background_path_var.set(path)
            browser._v216_background_enabled_var.set(True)
            bg_label.configure(text=Path(path).name)

    def clear_background() -> None:
        browser._v216_background_path_var.set("")
        browser._v216_background_enabled_var.set(False)
        bg_label.configure(text="No background loaded")

    ttk.Button(row, text="Choose image…", command=choose_background).pack(side="left", padx=(0, 3))
    ttk.Button(row, text="Clear", command=clear_background).pack(side="left")
    row2 = ttk.Frame(background, style="Card.TFrame"); row2.pack(fill="x")
    ttk.Label(row2, text="Fit", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Combobox(row2, textvariable=browser._v216_background_fit_var, state="readonly", width=20,
                 values=("Fit to data extent", "Preserve aspect", "Manual extent")).pack(side="left", padx=(0, 8))
    ttk.Label(row2, text="Opacity", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Entry(row2, textvariable=browser._v216_background_opacity_var, width=7).pack(side="left", padx=(0, 8))
    for label, var in (("X min", browser._v216_background_xmin_var), ("X max", browser._v216_background_xmax_var),
                       ("Y min", browser._v216_background_ymin_var), ("Y max", browser._v216_background_ymax_var)):
        ttk.Label(row2, text=label, style="Muted.TLabel").pack(side="left", padx=(0, 3))
        ttk.Entry(row2, textvariable=var, width=7).pack(side="left", padx=(0, 5))

    status_var = tk.StringVar(value="Select data in the experiment map, then create a visualization.")
    status = ttk.Label(outer, textvariable=status_var, style="Muted.TLabel", wraplength=1100)
    status.pack(anchor="w", fill="x", pady=(0, 3))
    result_box = tk.Text(outer, height=5, wrap="word")
    result_box.pack(fill="both", expand=True)
    try: browser._style_text(result_box)
    except Exception: pass
    result_box.insert("1.0", "Interactive outputs are saved under FaradaIQ_interactive_views and added to Views.\n")
    result_box.configure(state="disabled")

    def run() -> None:
        mode = browser._v216_viz_mode_var.get()
        metric = browser._v216_viz_metric_var.get()
        opts = _collect_multidim_options(browser, getattr(multidim, "_v216_options", None))
        open_btn.configure(state="disabled")
        status_var.set(f"Preparing {mode}…")
        state: Dict[str, Any] = {"done": False, "result": {}, "error": None}

        def worker() -> None:
            try:
                state["result"] = multidim.generate(browser, mode, metric, opts)
            except Exception as exc:
                state["error"] = exc
            finally:
                state["done"] = True

        def poll() -> None:
            if not state["done"]:
                browser.after(120, poll)
                return
            open_btn.configure(state="normal")
            if state["error"] is not None:
                message = f"Visualization failed: {state['error']}"
            else:
                result = state["result"] or {}
                _add_multidim_plot_view(browser, core, result, mode, metric, opts)
                message = f"Created {result.get('engine', mode)} and added it to Views: {result.get('html', '')}"
            status_var.set(message)
            try:
                result_box.configure(state="normal")
                result_box.insert("end", f"[{time.strftime('%H:%M:%S')}] {message}\n")
                result_box.see("end")
                result_box.configure(state="disabled")
                browser.log(message)
            except Exception:
                pass

        threading.Thread(target=worker, name="FaradaIQ-v227-spatial", daemon=True).start()
        browser.after(120, poll)

    open_btn.configure(command=run)
    browser._v217_run_multidim = run
    browser._v217_multidim_result_box = result_box
    browser._v226_spatial_options_notebook = options
    return outer


def _build_quick_plot(browser: Any, parent: Any, core: Mapping[str, Any], ttk: Any, tk: Any) -> None:
    from faradaiq.ui_tabs_v217 import _build_classic
    _build_classic(browser, parent, core, ttk, tk)


def _install_task_workspace(browser: Any, core: Mapping[str, Any], ttk: Any, tk: Any) -> None:
    old_holder = getattr(browser, "plot_holder", None)
    if old_holder is None:
        return
    center = getattr(old_holder, "master", None)
    if center is None:
        return
    # v217 placed plot_holder inside Classic tab -> outer notebook -> center pane.
    # Climb out of that compatibility notebook so v227 replaces it rather than
    # creating a confusing notebook inside another notebook.
    try:
        maybe_notebook = center.master
        if str(maybe_notebook.winfo_class()) == "TNotebook":
            center = maybe_notebook.master
    except Exception:
        pass
    for child in list(center.winfo_children()):
        _forget(child)

    flow = ttk.Frame(center, style="Card.TFrame")
    flow.pack(fill="x", pady=(0, 4))
    ttk.Label(flow, text="1  Select data", style="Muted.TLabel").pack(side="left", padx=(2, 8))
    ttk.Label(flow, text="→", style="Muted.TLabel").pack(side="left")
    ttk.Label(flow, text="2  Plot or map", style="Muted.TLabel").pack(side="left", padx=8)
    ttk.Label(flow, text="→", style="Muted.TLabel").pack(side="left")
    ttk.Label(flow, text="3  Save, analyze, or export", style="Muted.TLabel").pack(side="left", padx=8)
    ttk.Button(flow, text="Popout workspace", command=lambda: open_browser_popout(browser, core, ttk, tk)).pack(side="right")

    notebook = ttk.Notebook(center)
    notebook.pack(fill="both", expand=True)
    quick = ttk.Frame(notebook, style="Card.TFrame", padding=5)
    spatial = ttk.Frame(notebook, style="Card.TFrame", padding=5)
    notebook.add(quick, text="Quick Plot")
    notebook.add(spatial, text="Spatial / Cube")
    _build_quick_plot(browser, quick, core, ttk, tk)
    _build_spatial_workspace(browser, spatial, core, ttk, tk, core.get("filedialog"))

    browser._v226_center_notebook = notebook
    browser._v226_quick_plot_tab = quick
    browser._v226_spatial_tab = spatial
    # Compatibility aliases used by existing extensions/tests.
    browser._v217_center_notebook = notebook
    browser._v217_classic_tab = quick
    browser._v217_multidim_tab = spatial
    browser._v216_center_notebook = notebook
    browser._v216_classic_tab = quick
    browser._v216_multidim_tab = spatial
    try: notebook.select(quick)
    except Exception: pass


def _copy_tree(src: Any, dst: Any) -> dict[str, str]:
    mapping: dict[str, str] = {}
    def rec(parent_src: str, parent_dst: str) -> None:
        for iid in src.get_children(parent_src):
            vals = src.item(iid)
            new_iid = dst.insert(parent_dst, "end", text=vals.get("text", ""), values=vals.get("values", ()), open=bool(vals.get("open", False)))
            mapping[new_iid] = iid
            rec(iid, new_iid)
    rec("", "")
    return mapping


def _figure_snapshot(browser: Any, tk: Any):
    try:
        import io
        from PIL import Image, ImageTk
        fig = getattr(getattr(browser, "_v206_pub_panel", None), "figure", None) or getattr(browser, "_figure", None)
        if fig is None:
            return None
        buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=110, bbox_inches="tight"); buf.seek(0)
        image = Image.open(buf); image.thumbnail((1100, 650))
        return ImageTk.PhotoImage(image)
    except Exception:
        return None


def open_browser_popout(browser: Any, core: Mapping[str, Any], ttk: Any, tk: Any) -> Any:
    existing = getattr(browser, "_v226_browser_popout", None)
    try:
        if existing is not None and existing.winfo_exists():
            existing.lift(); return existing
    except Exception:
        pass

    win = tk.Toplevel(browser)
    win.title(f"FaradaIQ Studio {VERSION} — synchronized browser workspace")
    win.geometry("1460x920")
    top = ttk.Notebook(win); top.pack(fill="both", expand=True, padx=8, pady=8)
    data_tab = ttk.Frame(top, style="Card.TFrame", padding=7); top.add(data_tab, text="Data")
    visualize_tab = ttk.Frame(top, style="Card.TFrame", padding=7); top.add(visualize_tab, text="Visualize")
    analyze_tab = ttk.Frame(top, style="Card.TFrame", padding=7); top.add(analyze_tab, text="Analyze")
    activity_tab = ttk.Frame(top, style="Card.TFrame", padding=7); top.add(activity_tab, text="Activity")

    # Data
    ttk.Label(data_tab, text="Experiment map", style="Section.TLabel").pack(anchor="w")
    ttk.Label(data_tab, text="Selection is synchronized with the main browser.", style="Muted.TLabel").pack(anchor="w", pady=(0, 4))
    tree_frame = ttk.Frame(data_tab, style="Card.TFrame"); tree_frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(tree_frame, columns=("kind", "size"), show="tree headings", selectmode="extended")
    tree.heading("#0", text="Technique / group / file"); tree.heading("kind", text="Type"); tree.heading("size", text="Size")
    tree.column("#0", width=760); tree.column("kind", width=160); tree.column("size", width=100)
    y = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview); x = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
    tree.grid(row=0, column=0, sticky="nsew"); y.grid(row=0, column=1, sticky="ns"); x.grid(row=1, column=0, sticky="ew")
    tree_frame.rowconfigure(0, weight=1); tree_frame.columnconfigure(0, weight=1)
    mapping = _copy_tree(browser.tree, tree); reverse = {v: k for k, v in mapping.items()}; sync = {"busy": False}
    def pop_to_main(_event: Any = None) -> None:
        if sync["busy"]: return
        sync["busy"] = True
        try:
            browser.tree.selection_set([mapping[i] for i in tree.selection() if i in mapping])
            browser.preview_selected()
        finally: sync["busy"] = False
    def main_to_pop(_event: Any = None) -> None:
        if sync["busy"]: return
        sync["busy"] = True
        try: tree.selection_set([reverse[i] for i in browser.tree.selection() if i in reverse])
        finally: sync["busy"] = False
    tree.bind("<<TreeviewSelect>>", pop_to_main)
    browser.tree.bind("<<TreeviewSelect>>", main_to_pop, add="+")

    # Visualize: three secondary tabs only.
    visual_tabs = ttk.Notebook(visualize_tab); visual_tabs.pack(fill="both", expand=True)
    quick = ttk.Frame(visual_tabs, style="Card.TFrame", padding=7); visual_tabs.add(quick, text="Quick Plot")
    spatial = ttk.Frame(visual_tabs, style="Card.TFrame", padding=7); visual_tabs.add(spatial, text="Spatial / Cube")
    views = ttk.Frame(visual_tabs, style="Card.TFrame", padding=7); visual_tabs.add(views, text="Saved Views")

    qrow = ttk.Frame(quick, style="Card.TFrame"); qrow.pack(fill="x", pady=(0, 4))
    snap = ttk.Label(quick, text="No classic plot yet.", anchor="center"); snap.pack(fill="both", expand=True)
    snap_state: Dict[str, Any] = {"image": None}
    def refresh_snapshot() -> None:
        image = _figure_snapshot(browser, tk)
        if image is not None:
            snap_state["image"] = image; snap.configure(image=image, text="")
        else:
            snap.configure(image="", text="No classic plot yet.")
    def action(fn: Any):
        def wrapped() -> None:
            fn(); win.after(250, refresh_snapshot)
        return wrapped
    ttk.Button(qrow, text="Plot selected", command=action(lambda: browser.plot_selected(clear=True))).pack(side="left", padx=(0, 3))
    ttk.Button(qrow, text="Add overlay", command=action(lambda: browser.plot_selected(clear=False))).pack(side="left", padx=3)
    ttk.Button(qrow, text="Clear", command=action(browser.clear_plot)).pack(side="left", padx=3)
    ttk.Button(qrow, text="Axis / title", command=browser.open_axis_title_dialog).pack(side="left", padx=3)
    ttk.Combobox(qrow, textvariable=browser.plot_mode_var, state="readonly", width=24, values=browser.plot_mode.cget("values")).pack(side="left", padx=7)
    ttk.Button(qrow, text="Refresh preview", command=refresh_snapshot).pack(side="right")
    refresh_snapshot()

    _build_spatial_workspace(browser, spatial, core, ttk, tk, core.get("filedialog"))

    pop_views = tk.Listbox(views, exportselection=False); _style_listbox(pop_views); pop_views.pack(fill="both", expand=True)
    vrow = ttk.Frame(views, style="Card.TFrame"); vrow.pack(fill="x", pady=(5, 0))
    def refresh_views() -> None: _sync_listbox_from_views(browser, pop_views)
    def load_pop_view(_event: Any = None) -> None:
        sel = pop_views.curselection()
        if sel: _load_view_from_index(browser, int(sel[0]))
    pop_views.bind("<<ListboxSelect>>", load_pop_view)
    ttk.Button(vrow, text="Open selected", command=load_pop_view).pack(side="left", padx=(0, 3))
    ttk.Button(vrow, text="Refresh", command=refresh_views).pack(side="right")
    refresh_views()
    visual_tabs.bind("<<NotebookTabChanged>>", lambda _e: refresh_views() if visual_tabs.index("current") == 2 else None)

    # Analyze: use synchronized query and actions; display a mirrored suggestions list.
    pop_context = ttk.Label(analyze_tab, textvariable=browser._v226_analysis_context_var, style="Muted.TLabel")
    pop_context.pack(anchor="w", fill="x", pady=(0, 5))
    handoff = ttk.LabelFrame(analyze_tab, text="Technique-aware selected-data handoff", padding=(6, 5)); handoff.pack(fill="x", pady=(0, 6))
    row = ttk.Frame(handoff, style="Card.TFrame"); row.pack(fill="x", pady=(0, 4))
    ttk.Label(row, text="EIS", width=10, style="Muted.TLabel").pack(side="left")
    ttk.Button(row, text="DearEIS", command=lambda: core.get("_v138_open_deareis_with_selected", lambda *_a, **_k: None)(browser, launch=True)).pack(side="left", fill="x", expand=True, padx=(0, 2))
    ttk.Button(row, text="pyDRTtools", command=lambda: core.get("_v225_open_pydrttools_with_selected", lambda *_a, **_k: None)(browser)).pack(side="left", fill="x", expand=True, padx=(2, 0))
    row2 = ttk.Frame(handoff, style="Card.TFrame"); row2.pack(fill="x", pady=(0, 4))
    ttk.Label(row2, text="CV / IV", width=10, style="Muted.TLabel").pack(side="left", anchor="n", pady=(3, 0))
    pop_cv_body = ttk.Frame(row2, style="Card.TFrame"); pop_cv_body.pack(side="left", fill="x", expand=True)
    pop_cv_actions = ttk.Frame(pop_cv_body, style="Card.TFrame"); pop_cv_actions.pack(fill="x")
    ttk.Button(pop_cv_actions, text="PySimpleEChem", command=browser._open_pysimple).pack(side="left", fill="x", expand=True, padx=(0, 2))
    ttk.Button(pop_cv_actions, text="Tafel analysis", command=lambda: core.get("_v130_open_tafel_window", lambda *_a, **_k: None)(browser)).pack(side="left", fill="x", expand=True, padx=(2, 0))
    pop_cv_corrosion = ttk.Frame(pop_cv_body, style="Card.TFrame"); pop_cv_corrosion.pack(fill="x", pady=(3, 0))
    ttk.Button(pop_cv_corrosion, text="Robust corrosion metrics · point/spatial", command=lambda: core.get("_v298_open_corrosion_metrics", lambda *_a, **_k: None)(browser)).pack(fill="x", expand=True)
    row3 = ttk.Frame(handoff, style="Card.TFrame"); row3.pack(fill="x", pady=(0, 4))
    ttk.Label(row3, text="CA / CP", width=10, style="Muted.TLabel").pack(side="left")
    ttk.Button(row3, text="Charge / transient metrics", command=lambda: core.get("_v262_open_ca_cp_analysis", lambda *_a, **_k: None)(browser)).pack(side="left", fill="x", expand=True, padx=(0, 2))
    ttk.Button(row3, text="Ask SciLink: CA/CP analysis…", command=lambda: core.get("_v262_open_scilink_technique_prompt", lambda *_a, **_k: None)(browser, "CA charge and transient metrics")).pack(side="left", fill="x", expand=True, padx=(2, 0))
    row_ocp = ttk.Frame(handoff, style="Card.TFrame"); row_ocp.pack(fill="x", pady=(0, 4))
    ttk.Label(row_ocp, text="OCP", width=10, style="Muted.TLabel").pack(side="left", anchor="n", pady=(3, 0))
    pop_ocp_body = ttk.Frame(row_ocp, style="Card.TFrame"); pop_ocp_body.pack(side="left", fill="x", expand=True)
    pop_ocp_slider = ttk.Frame(pop_ocp_body, style="Card.TFrame"); pop_ocp_slider.pack(fill="x")
    ttk.Label(pop_ocp_slider, textvariable=browser._v291_ocp_tail_label_var, style="Muted.TLabel", width=22).pack(side="left")
    ttk.Scale(pop_ocp_slider, from_=1, to=100, variable=browser._v291_ocp_tail_percent_var, command=browser._v291_set_ocp_tail_percent).pack(side="left", fill="x", expand=True, padx=(4, 0))
    pop_ocp_axis = ttk.Frame(pop_ocp_body, style="Card.TFrame"); pop_ocp_axis.pack(fill="x", pady=(3, 0))
    ttk.Label(pop_ocp_axis, text="Horizontal axis", style="Muted.TLabel", width=22).pack(side="left")
    ttk.Combobox(pop_ocp_axis, textvariable=browser._v294_ocp_axis_var, state="readonly", values=("Auto", "Point", "X", "Y", "Distance"), width=12).pack(side="left", fill="x", expand=True, padx=(4, 0))
    pop_ocp_buttons = ttk.Frame(pop_ocp_body, style="Card.TFrame"); pop_ocp_buttons.pack(fill="x", pady=(3, 0))
    ttk.Button(pop_ocp_buttons, text="Average OCP · point/spatial", command=lambda: core.get("_v291_open_ocp_tail_analysis", lambda *_a, **_k: None)(browser)).pack(side="left", fill="x", expand=True, padx=(0, 2))
    ttk.Button(pop_ocp_buttons, text="Ask SciLink: OCP spatial…", command=lambda: core.get("_v291_open_ocp_scilink_handoff", lambda *_a, **_k: None)(browser)).pack(side="left", fill="x", expand=True, padx=(2, 0))
    row4 = ttk.Frame(handoff, style="Card.TFrame"); row4.pack(fill="x")
    ttk.Label(row4, text="SciLink", width=10, style="Muted.TLabel").pack(side="left")
    ttk.Button(row4, text="Ask SciLink with technique template…", command=lambda: core.get("_v262_open_scilink_technique_prompt", lambda *_a, **_k: None)(browser, None)).pack(side="left", fill="x", expand=True)
    smart = ttk.LabelFrame(analyze_tab, text="Smart Compare", padding=(6, 5)); smart.pack(fill="both", expand=True)
    ttk.Entry(smart, textvariable=browser._v226_ai_query_var).pack(fill="x", pady=(0, 4))
    ar = ttk.Frame(smart, style="Card.TFrame"); ar.pack(fill="x")
    pop_suggestions = tk.Listbox(smart, exportselection=False); _style_listbox(pop_suggestions); pop_suggestions.pack(fill="both", expand=True, pady=(5, 0))
    def refresh_suggestions() -> None:
        try:
            pop_suggestions.delete(0, "end")
            for idx in range(browser.suggestion_list.size()): pop_suggestions.insert("end", browser.suggestion_list.get(idx))
        except Exception: pass
    def do_suggest() -> None:
        browser.suggest_comparisons(); win.after(500, refresh_suggestions)
    def do_ask() -> None:
        try:
            browser.ai_compare_entry.delete(0, "end"); browser.ai_compare_entry.insert(0, browser._v226_ai_query_var.get())
        except Exception: pass
        browser.ask_ai_comparisons(); win.after(500, refresh_suggestions)
    def apply_suggestion() -> None:
        sel = pop_suggestions.curselection()
        if sel:
            try: browser.suggestion_list.selection_clear(0, "end"); browser.suggestion_list.selection_set(sel[0])
            except Exception: pass
        browser.apply_selected_suggestion(); refresh_views()
    ttk.Button(ar, text="Suggest", command=do_suggest).pack(side="left", fill="x", expand=True, padx=(0, 2))
    ttk.Button(ar, text="Ask AI", command=do_ask).pack(side="left", fill="x", expand=True, padx=2)
    ttk.Button(ar, text="Add to views", command=apply_suggestion).pack(side="left", fill="x", expand=True, padx=(2, 0))
    refresh_suggestions()

    # Activity
    activity_tabs = ttk.Notebook(activity_tab); activity_tabs.pack(fill="both", expand=True)
    qa = ttk.Frame(activity_tabs, style="Card.TFrame", padding=6); activity_tabs.add(qa, text="QA")
    log = ttk.Frame(activity_tabs, style="Card.TFrame", padding=6); activity_tabs.add(log, text="Log")
    qtext = tk.Text(qa, wrap="word"); qtext.pack(fill="both", expand=True)
    ltext = tk.Text(log, wrap="word"); ltext.pack(fill="both", expand=True)
    try: browser._style_text(qtext); browser._style_text(ltext)
    except Exception: pass
    def refresh_activity() -> None:
        for source, target in ((browser.qa_text, qtext), (browser.log_text, ltext)):
            try:
                target.configure(state="normal"); target.delete("1.0", "end"); target.insert("1.0", source.get("1.0", "end")); target.configure(state="disabled")
            except Exception: pass
    toolbar = ttk.Frame(activity_tab, style="Card.TFrame"); toolbar.pack(fill="x", pady=(5, 0))
    ttk.Button(toolbar, text="Refresh activity", command=refresh_activity).pack(side="right")
    refresh_activity()

    browser._v226_browser_popout = win
    browser._v217_browser_popout = win
    browser._v226_popout_notebook = top
    browser._v226_popout_visual_notebook = visual_tabs
    return win


def install(core: Dict[str, Any]) -> None:
    Browser = core.get("FaradaIQH5Browser")
    ttk = core.get("ttk"); tk = core.get("tk")
    if Browser is None or ttk is None or tk is None:
        raise RuntimeError("FaradaIQ browser classes are unavailable")
    if getattr(Browser, "_v226_task_workspace_wrapper", False):
        return

    # Replace the popout function used by the v217 button at call time.
    try:
        import faradaiq.ui_tabs_v217 as legacy_tabs
        legacy_tabs._open_browser_popout = open_browser_popout
    except Exception:
        pass

    previous = Browser._build_visible_ui_first
    def build(self: Any) -> None:
        previous(self)
        _install_task_workspace(self, core, ttk, tk)
        _redesign_side_panel(self, core, ttk, tk)
        try:
            self.title(f"FaradaIQ Studio {VERSION} — unified browser — {getattr(self, 'base_name', '')}")
        except Exception:
            pass
        for widget in _walk(self):
            try:
                if str(widget.cget("text")) == "Open bundle folder":
                    widget.configure(text="Bundle folder")
            except Exception:
                pass
    Browser._build_visible_ui_first = build
    Browser._v226_task_workspace_wrapper = True
