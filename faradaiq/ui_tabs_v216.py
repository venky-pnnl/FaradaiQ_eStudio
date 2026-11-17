"""FaradaIQ Studio v273 browser tabs and branding refinements.

This module keeps the existing classic Matplotlib browser intact, but places all
classic controls and the plot canvas in a dedicated notebook tab.  The v216
Altair / Plotly / hyperspectral controls are moved from the bottom of the
browser into a second dedicated visualization tab.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

VERSION = "v273"
TAGLINE = "Electrochemical Intelligence for Analyze, Control and Discover"


def _widget_text(widget: Any) -> str:
    try:
        return str(widget.cget("text") or "")
    except Exception:
        return ""


def _descendant_text(widget: Any) -> str:
    parts: List[str] = []
    stack = [widget]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        text = _widget_text(current)
        if text:
            parts.append(text)
        try:
            stack.extend(current.winfo_children())
        except Exception:
            pass
    return " | ".join(parts)


def _forget(widget: Any) -> None:
    for method in ("pack_forget", "grid_remove", "place_forget"):
        try:
            getattr(widget, method)()
        except Exception:
            pass


def _is_direct_outer_container(widget: Any, center: Any) -> bool:
    """Return True when a direct child owns the main paned browser hierarchy."""
    try:
        node = center
        while node is not None:
            if node is widget:
                return True
            node = node.master
    except Exception:
        pass
    return False


def _selected_summary(browser: Any) -> str:
    try:
        selected = list(browser.selected_paths(plottable_only=True))
    except Exception:
        selected = []
    lines = [
        "The experiment-map selection is shared by both tabs.",
        "",
        f"Selected plottable records: {len(selected)}",
    ]
    if selected:
        suffix_counts: Dict[str, int] = {}
        for path in selected:
            suffix = Path(path).suffix.lower() or "table"
            suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
        lines.append("Formats: " + ", ".join(f"{k} {v}" for k, v in sorted(suffix_counts.items())))
        lines.append("")
        lines.append("Selected records:")
        for path in selected[:30]:
            lines.append(f"• {Path(path).name}")
        if len(selected) > 30:
            lines.append(f"• … and {len(selected) - 30} more")
    else:
        lines.extend([
            "",
            "Select a technique, method, point, cycle, map, spectrum, or trace in the experiment tree.",
            "Then choose Altair interactive 2D, Plotly true 3D, or Hyperspectral slider / cube.",
        ])
    return "\n".join(lines)


def _apply_tagline(root: Any) -> None:
    """Normalize the visible tagline and make it slightly larger."""
    try:
        style = root.tk.call("ttk::style", "theme", "use")
        _ = style
    except Exception:
        pass
    stack = [root]
    seen: set[int] = set()
    while stack:
        widget = stack.pop()
        if id(widget) in seen:
            continue
        seen.add(id(widget))
        text = _widget_text(widget)
        lower = text.lower()
        if (
            "electrochemical intelligence for analyze" in lower
            or "designed by venky prabhakaran" in lower
        ):
            try:
                widget.configure(text=TAGLINE, font=("Segoe UI Semibold", 14))
            except Exception:
                try:
                    widget.configure(text=TAGLINE)
                except Exception:
                    pass
        try:
            stack.extend(widget.winfo_children())
        except Exception:
            pass


def _install_browser_tabs(browser: Any, ttk: Any, tk: Any, scrolled_text: Any) -> None:
    if getattr(browser, "_v216_tabbed_plot_workspace_installed", False):
        return
    plot_holder = getattr(browser, "plot_holder", None)
    if plot_holder is None:
        return
    center = getattr(plot_holder, "master", None)
    if center is None:
        return

    # Preserve the current center-column widgets in their existing creation order.
    center_children = list(center.winfo_children())
    for child in center_children:
        _forget(child)

    # The v209 media toolbar and the v216 multidimensional toolbar are direct
    # children of the browser window. Move them into the appropriate tab.
    media_bar = None
    multidim_bar = None
    try:
        for child in list(browser.winfo_children()):
            if _is_direct_outer_container(child, center):
                continue
            content = _descendant_text(child).lower()
            if "media browser" in content:
                media_bar = child
            if "hyperspectral visualization" in content or "altair interactive 2d" in content:
                multidim_bar = child
    except Exception:
        pass
    for child in (media_bar, multidim_bar):
        if child is not None:
            _forget(child)

    notebook = ttk.Notebook(center)
    notebook.pack(fill="both", expand=True)
    classic = ttk.Frame(notebook, style="Card.TFrame", padding=(4, 4))
    multidim = ttk.Frame(notebook, style="Card.TFrame", padding=(8, 8))
    notebook.add(classic, text="Classic - 2D")
    notebook.add(multidim, text="1D/2D/3D Visualization")

    if media_bar is not None:
        media_bar.pack(in_=classic, fill="x", pady=(0, 4))

    for child in center_children:
        if child is plot_holder:
            child.pack(in_=classic, fill="both", expand=True)
        else:
            child.pack(in_=classic, fill="x", pady=(0, 4))

    if multidim_bar is not None:
        try:
            multidim_bar.configure(text="Interactive visualization controls")
        except Exception:
            pass
        multidim_bar.pack(in_=multidim, fill="x", pady=(0, 8))

    guide = ttk.LabelFrame(multidim, text="Dimensional visualization workspace", padding=(10, 8))
    guide.pack(fill="both", expand=True)
    intro = (
        "Use the same experiment-map selection for point-dependent, position-dependent, spatial, "
        "3D, and hyperspectral views. Altair provides linked interactive 2D maps and traces. "
        "Plotly provides rotatable 3D surfaces, point stacks, heatmaps, and contours. CV hyperspectral "
        "mode preserves forward and reverse scan branches and separates cycles. Spatial, signal, and "
        "slider-axis units are selectable. Optional background images are off by default and can be "
        "resized or fitted to spatial data coordinates."
    )
    ttk.Label(guide, text=intro, wraplength=850, justify="left").pack(anchor="w", fill="x", pady=(0, 8))

    row = ttk.Frame(guide, style="Card.TFrame")
    row.pack(fill="x", pady=(0, 6))
    ttk.Label(row, text="Selection summary", style="Section.TLabel").pack(side="left")

    summary = scrolled_text(guide, wrap="word", height=18)
    summary.pack(fill="both", expand=True)
    try:
        browser._style_text(summary)
    except Exception:
        pass

    def refresh_summary(*_args: Any) -> None:
        try:
            summary.configure(state="normal")
            summary.delete("1.0", "end")
            summary.insert("1.0", _selected_summary(browser))
            summary.configure(state="disabled")
        except Exception:
            pass

    ttk.Button(row, text="Refresh selected-data summary", command=refresh_summary).pack(side="right")
    try:
        browser.tree.bind("<<TreeviewSelect>>", lambda _e: refresh_summary(), add="+")
    except Exception:
        pass
    refresh_summary()

    browser._v216_center_notebook = notebook
    browser._v216_classic_tab = classic
    browser._v216_multidim_tab = multidim
    browser._v216_multidim_summary = summary
    browser._v216_tabbed_plot_workspace_installed = True
    try:
        notebook.select(classic)
    except Exception:
        pass


def install(core: Dict[str, Any]) -> None:
    Browser = core.get("FaradaIQH5Browser")
    SessionReaderGUI = core.get("SessionReaderGUI")
    ttk = core.get("ttk")
    tk = core.get("tk")
    scrolled_text = core.get("ScrolledText")
    if Browser is None or ttk is None or tk is None or scrolled_text is None:
        raise RuntimeError("FaradaIQ GUI classes are unavailable")
    if getattr(Browser, "_v216_tabbed_plot_workspace_wrapper", False):
        return

    previous_build = Browser._build_visible_ui_first

    def build_ui(self: Any) -> None:
        previous_build(self)
        _install_browser_tabs(self, ttk, tk, scrolled_text)
        _apply_tagline(self)
        try:
            self.title(f"FaradaIQ Studio {VERSION} — unified data browser — {getattr(self, 'base_name', '')}")
        except Exception:
            pass

    Browser._build_visible_ui_first = build_ui
    Browser._v216_tabbed_plot_workspace_wrapper = True

    if SessionReaderGUI is not None and not getattr(SessionReaderGUI, "_v216_branding_wrapper", False):
        previous_init = SessionReaderGUI.__init__

        def init(self: Any, *args: Any, **kwargs: Any) -> None:
            previous_init(self, *args, **kwargs)
            _apply_tagline(self)
            for delay in (50, 250, 800, 1800):
                try:
                    self.after(delay, lambda w=self: _apply_tagline(w))
                except Exception:
                    pass

        SessionReaderGUI.__init__ = init
        SessionReaderGUI._v216_branding_wrapper = True
