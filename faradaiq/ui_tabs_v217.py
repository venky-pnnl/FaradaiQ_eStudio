"""FaradaIQ Studio v273 browser workspace and responsive UI refinements.

This module repairs the tabbed browser layout without attempting to re-parent
existing Tk widgets.  The classic Matplotlib controls and canvas are rebuilt in
one notebook tab, while Altair/Plotly/hyperspectral controls live in a second
notebook tab.  It also provides a synchronized multi-tab browser popout.
"""
from __future__ import annotations

import io
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

VERSION = "v273"
TAGLINE = "Electrochemical Intelligence for Analyze, Control and Discover"


def _forget(widget: Any) -> None:
    for meth in ("pack_forget", "grid_remove", "place_forget"):
        try:
            getattr(widget, meth)()
        except Exception:
            pass


def _walk(widget: Any):
    yield widget
    try:
        for child in widget.winfo_children():
            yield from _walk(child)
    except Exception:
        return


def _widget_text(widget: Any) -> str:
    try:
        return str(widget.cget("text") or "")
    except Exception:
        return ""


def _apply_tagline(root: Any) -> None:
    for widget in _walk(root):
        txt = _widget_text(widget).lower()
        if "electrochemical intelligence for analyze" in txt:
            try:
                widget.configure(text=TAGLINE, font=("Segoe UI Semibold", 15))
            except Exception:
                try:
                    widget.configure(text=TAGLINE)
                except Exception:
                    pass


def _ensure_multidim_vars(browser: Any, tk: Any) -> None:
    try:
        from faradaiq import multidim_v216 as multidim
        persisted = multidim._v222_load_persisted_options()
    except Exception:
        persisted = {}
    defaults = {
        "_v216_viz_mode_var": "Altair interactive 2D",
        "_v216_viz_metric_var": "Auto",
        "_v216_spatial_unit_var": persisted.get("spatial_unit", "µm"),
        "_v216_signal_unit_var": persisted.get("signal_unit", "Auto"),
        "_v216_spectral_unit_var": persisted.get("spectral_unit", "Auto"),
        "_v216_render_mode_var": persisted.get("render_mode", "Contour + points"),
        "_v216_interpolation_var": persisted.get("interpolation", "Linear"),
        "_v216_contour_levels_var": str(persisted.get("contour_levels", 16)),
        "_v216_palette_var": persisted.get("palette", "Viridis"),
        "_v216_range_mode_var": persisted.get("range_mode", "Robust (2–98%)"),
        "_v222_range_scope_var": persisted.get("range_scope", "Lock across cube"),
        "_v216_range_min_var": str(persisted.get("range_min", "")),
        "_v216_range_max_var": str(persisted.get("range_max", "")),
        "_v216_contour_line_width_var": str(persisted.get("contour_line_width", 1.0)),
        "_v216_smoothing_var": persisted.get("smoothing", "Light"),
        "_v216_opacity_var": str(persisted.get("opacity", 0.92)),
        "_v216_point_size_var": str(persisted.get("point_size", 13)),
        "_v222_surface_model_var": persisted.get("surface_model", "Standard interpolation"),
        "_v222_tip_diameter_var": str(persisted.get("tip_diameter_um", 5.0)),
        "_v222_overlap_var": str(persisted.get("overlap_percent", 0.0)),
        "_v222_grid_quality_var": persisted.get("grid_quality", "Auto"),
        "_v222_spacing_label_var": "",
        "_v216_background_path_var": persisted.get("background_path", ""),
        "_v216_background_fit_var": persisted.get("background_fit", "Fit to data extent"),
        "_v216_background_opacity_var": str(persisted.get("background_opacity", 0.35)),
        "_v216_background_xmin_var": str(persisted.get("background_xmin", "")),
        "_v216_background_xmax_var": str(persisted.get("background_xmax", "")),
        "_v216_background_ymin_var": str(persisted.get("background_ymin", "")),
        "_v216_background_ymax_var": str(persisted.get("background_ymax", "")),
    }
    bool_defaults = {
        "_v216_adaptive_contours_var": bool(persisted.get("adaptive_contours", True)),
        "_v216_reverse_palette_var": bool(persisted.get("reverse_palette", False)),
        "_v216_contour_labels_var": bool(persisted.get("contour_labels", False)),
        "_v216_mask_hull_var": bool(persisted.get("mask_outside_hull", True)),
        "_v216_overlay_points_var": bool(persisted.get("overlay_points", True)),
        "_v222_clip_overshoot_var": bool(persisted.get("clip_overshoot", True)),
        "_v216_background_enabled_var": bool(persisted.get("background_enabled", False)),
        "_v217_lock_xy_aspect_var": bool(persisted.get("lock_xy_aspect", False)),
    }
    for name, value in defaults.items():
        if not hasattr(browser, name):
            setattr(browser, name, tk.StringVar(value=value))
    for name, value in bool_defaults.items():
        if not hasattr(browser, name):
            setattr(browser, name, tk.BooleanVar(value=value))


def _collect_multidim_options(browser: Any, options_fn: Any) -> Dict[str, Any]:
    raw = {
        "spatial_unit": browser._v216_spatial_unit_var.get(),
        "signal_unit": browser._v216_signal_unit_var.get(),
        "spectral_unit": browser._v216_spectral_unit_var.get(),
        "render_mode": browser._v216_render_mode_var.get(),
        "interpolation": browser._v216_interpolation_var.get(),
        "contour_levels": browser._v216_contour_levels_var.get(),
        "adaptive_contours": browser._v216_adaptive_contours_var.get(),
        "palette": browser._v216_palette_var.get(),
        "reverse_palette": browser._v216_reverse_palette_var.get(),
        "range_mode": browser._v216_range_mode_var.get(),
        "range_scope": browser._v222_range_scope_var.get(),
        "range_min": browser._v216_range_min_var.get(),
        "range_max": browser._v216_range_max_var.get(),
        "contour_line_width": browser._v216_contour_line_width_var.get(),
        "contour_labels": browser._v216_contour_labels_var.get(),
        "smoothing": browser._v216_smoothing_var.get(),
        "mask_outside_hull": browser._v216_mask_hull_var.get(),
        "overlay_points": browser._v216_overlay_points_var.get(),
        "opacity": browser._v216_opacity_var.get(),
        "point_size": browser._v216_point_size_var.get(),
        "surface_model": browser._v222_surface_model_var.get(),
        "tip_diameter_um": browser._v222_tip_diameter_var.get(),
        "overlap_percent": browser._v222_overlap_var.get(),
        "clip_overshoot": browser._v222_clip_overshoot_var.get(),
        "grid_quality": browser._v222_grid_quality_var.get(),
        "background_enabled": browser._v216_background_enabled_var.get(),
        "background_path": browser._v216_background_path_var.get(),
        "background_fit": browser._v216_background_fit_var.get(),
        "background_opacity": browser._v216_background_opacity_var.get(),
        "background_xmin": browser._v216_background_xmin_var.get(),
        "background_xmax": browser._v216_background_xmax_var.get(),
        "background_ymin": browser._v216_background_ymin_var.get(),
        "background_ymax": browser._v216_background_ymax_var.get(),
        "lock_xy_aspect": browser._v217_lock_xy_aspect_var.get(),
    }
    opts = options_fn(raw) if callable(options_fn) else raw
    try:
        from faradaiq import multidim_v216 as multidim
        multidim._v222_save_persisted_options(opts)
        browser._v222_visualization_options = dict(opts)
    except Exception:
        pass
    return opts


def _add_multidim_plot_view(browser: Any, core: Mapping[str, Any], result: Mapping[str, Any], mode: str, metric: str, opts: Mapping[str, Any]) -> None:
    try:
        paths = list(browser.selected_paths(plottable_only=True))
    except Exception:
        paths = []
    add_view = core.get("_v138_add_plot_view")
    if callable(add_view) and paths:
        label = f"{mode} | {metric} | {len(paths)} source file(s) | {VERSION}"
        view = add_view(browser, label, paths, mode=mode, source="v217_multidimensional")
        if isinstance(view, dict):
            view["interactive_html"] = str(result.get("html") or "")
            view["interactive_engine"] = str(result.get("engine") or mode)
            view["interactive_metric"] = metric
            view["interactive_options"] = dict(opts)
            view["multidimensional"] = True
        try:
            update = core.get("_v138_update_plot_view_listbox")
            if callable(update):
                update(browser)
        except Exception:
            pass
    else:
        # Compatibility with older internal list implementation.
        try:
            if not hasattr(browser, "_v69_plot_views"):
                browser._v69_plot_views = []
            browser._v69_plot_views.append({
                "name": f"{mode} | {metric} | {VERSION}",
                "kind": mode,
                "paths": [str(p) for p in paths],
                "mode": mode,
                "interactive_html": str(result.get("html") or ""),
                "interactive_engine": str(result.get("engine") or mode),
                "interactive_options": dict(opts),
            })
            browser.plot_view_list.insert("end", browser._v69_plot_views[-1]["name"])
        except Exception:
            pass


def _build_multidim_controls(browser: Any, parent: Any, core: Mapping[str, Any], ttk: Any, tk: Any, filedialog: Any) -> Any:
    from faradaiq import multidim_v216 as multidim

    _ensure_multidim_vars(browser, tk)
    outer = ttk.Frame(parent, style="Card.TFrame", padding=(8, 8))
    outer.pack(fill="both", expand=True)

    title = ttk.Label(outer, text="Interactive 1D / 2D / 3D / hyperspectral visualization", style="Section.TLabel")
    title.pack(anchor="w", pady=(0, 3))
    ttk.Label(
        outer,
        text=(
            "Altair provides linked interactive 2D maps and traces. Plotly provides rotatable 3D surfaces and point-stacked traces. "
            "CV hyperspectral mode preserves forward and reverse branches and provides a free rectangular box zoom when X/Y aspect is unlocked."
        ),
        style="Muted.TLabel", wraplength=1200, justify="left",
    ).pack(anchor="w", fill="x", pady=(0, 5))

    row1 = ttk.Frame(outer, style="Card.TFrame"); row1.pack(fill="x", pady=(0, 4))
    ttk.Label(row1, text="Engine", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(row1, textvariable=browser._v216_viz_mode_var, state="readonly", width=25,
                 values=("Altair interactive 2D", "Plotly true 3D", "Hyperspectral slider / cube")).pack(side="left", padx=(0, 8))
    ttk.Label(row1, text="Metric", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(row1, textvariable=browser._v216_viz_metric_var, state="readonly", width=15,
                 values=("Auto", "steady_state", "integral", "absolute_peak", "endpoint", "mean", "minimum", "maximum", "range")).pack(side="left", padx=(0, 8))
    open_btn = ttk.Button(row1, text="Open selected data")
    open_btn.pack(side="left", padx=(0, 8))
    ttk.Checkbutton(row1, text="Lock X/Y aspect", variable=browser._v217_lock_xy_aspect_var).pack(side="left", padx=(0, 8))
    ttk.Label(row1, text="Unchecked gives free rectangular box zoom.", style="Muted.TLabel").pack(side="left")

    row2 = ttk.Frame(outer, style="Card.TFrame"); row2.pack(fill="x", pady=(0, 4))
    ttk.Label(row2, text="Spatial X/Y unit", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(row2, textvariable=browser._v216_spatial_unit_var, state="readonly", width=9,
                 values=("Scientific", "nm", "µm", "mm", "m")).pack(side="left", padx=(0, 8))
    ttk.Label(row2, text="Signal/color unit", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(row2, textvariable=browser._v216_signal_unit_var, state="readonly", width=10,
                 values=("Auto", "Scientific", "fA", "pA", "nA", "µA", "mA", "A", "mV", "V", "Ω", "kΩ", "MΩ", "fC", "pC", "nC", "µC", "mC", "C")).pack(side="left", padx=(0, 8))
    ttk.Label(row2, text="Slider axis unit", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(row2, textvariable=browser._v216_spectral_unit_var, state="readonly", width=10,
                 values=("Auto", "Scientific", "mV", "V", "µs", "ms", "s", "Hz", "kHz", "MHz", "nm", "µm", "mm", "m")).pack(side="left", padx=(0, 8))
    ttk.Label(row2, text="Map style", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(row2, textvariable=browser._v216_render_mode_var, state="readonly", width=18,
                 values=("Measured points", "Heatmap", "Filled contour", "Contour lines", "Contour + points")).pack(side="left")

    row3 = ttk.Frame(outer, style="Card.TFrame"); row3.pack(fill="x", pady=(0, 4))
    ttk.Label(row3, text="Interpolation", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(row3, textvariable=browser._v216_interpolation_var, state="readonly", width=9,
                 values=("None", "Nearest", "Linear", "Cubic")).pack(side="left", padx=(0, 8))
    ttk.Label(row3, text="Contour levels", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Entry(row3, textvariable=browser._v216_contour_levels_var, width=5).pack(side="left", padx=(0, 5))
    ttk.Checkbutton(row3, text="Smart contour optimization", variable=browser._v216_adaptive_contours_var).pack(side="left", padx=(0, 8))
    ttk.Checkbutton(row3, text="Overlay measured points", variable=browser._v216_overlay_points_var).pack(side="left", padx=(0, 8))
    ttk.Label(row3, text="Opacity", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Entry(row3, textvariable=browser._v216_opacity_var, width=6).pack(side="left", padx=(0, 8))
    ttk.Label(row3, text="Point size", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Entry(row3, textvariable=browser._v216_point_size_var, width=5).pack(side="left")

    row3b = ttk.Frame(outer, style="Card.TFrame"); row3b.pack(fill="x", pady=(0, 4))
    ttk.Label(row3b, text="Palette", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(row3b, textvariable=browser._v216_palette_var, state="readonly", width=11,
                 values=("Viridis", "Cividis", "Turbo", "Plasma", "Inferno", "Magma", "Greys", "RdBu", "Picnic", "Portland", "Jet")).pack(side="left", padx=(0, 4))
    ttk.Checkbutton(row3b, text="Reverse", variable=browser._v216_reverse_palette_var).pack(side="left", padx=(0, 8))
    ttk.Label(row3b, text="Palette range", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(row3b, textvariable=browser._v216_range_mode_var, state="readonly", width=18,
                 values=("Robust (2–98%)", "Full data range", "Manual min/max")).pack(side="left", padx=(0, 4))
    ttk.Combobox(row3b, textvariable=browser._v222_range_scope_var, state="readonly", width=16,
                 values=("Lock across cube", "Per frame")).pack(side="left", padx=(0, 4))
    ttk.Entry(row3b, textvariable=browser._v216_range_min_var, width=8).pack(side="left", padx=(0, 3))
    ttk.Entry(row3b, textvariable=browser._v216_range_max_var, width=8).pack(side="left", padx=(0, 8))
    ttk.Label(row3b, text="Smooth", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(row3b, textvariable=browser._v216_smoothing_var, state="readonly", width=9,
                 values=("None", "Light", "Medium", "Strong")).pack(side="left", padx=(0, 8))

    row3c = ttk.Frame(outer, style="Card.TFrame"); row3c.pack(fill="x", pady=(0, 4))
    ttk.Label(row3c, text="Contour line width", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Entry(row3c, textvariable=browser._v216_contour_line_width_var, width=5).pack(side="left", padx=(0, 6))
    ttk.Checkbutton(row3c, text="Contour labels", variable=browser._v216_contour_labels_var).pack(side="left", padx=(0, 8))
    ttk.Checkbutton(row3c, text="Mask outside measured hull", variable=browser._v216_mask_hull_var).pack(side="left", padx=(0, 8))
    ttk.Checkbutton(row3c, text="Prevent overshoot", variable=browser._v222_clip_overshoot_var).pack(side="left", padx=(0, 8))
    ttk.Label(row3c, text="Surface reconstruction", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(row3c, textvariable=browser._v222_surface_model_var, state="readonly", width=23,
                 values=("Standard interpolation", "Origin-style smooth", "SECCM tip footprint")).pack(side="left", padx=(0, 8))
    ttk.Label(row3c, text="Grid", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Combobox(row3c, textvariable=browser._v222_grid_quality_var, state="readonly", width=11,
                 values=("Auto", "Fine", "Publication")).pack(side="left")

    row3d = ttk.Frame(outer, style="Card.TFrame"); row3d.pack(fill="x", pady=(0, 4))
    ttk.Label(row3d, text="SECCM tip diameter / µm", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Entry(row3d, textvariable=browser._v222_tip_diameter_var, width=8).pack(side="left", padx=(0, 8))
    ttk.Label(row3d, text="Overlap / %", style="Muted.TLabel").pack(side="left", padx=(0, 4))
    ttk.Entry(row3d, textvariable=browser._v222_overlap_var, width=7).pack(side="left", padx=(0, 8))
    ttk.Label(row3d, textvariable=browser._v222_spacing_label_var, style="Muted.TLabel").pack(side="left", padx=(0, 12))

    def update_spacing_label(*_args: Any) -> None:
        try:
            diameter = max(0.001, float(browser._v222_tip_diameter_var.get()))
            overlap = max(0.0, min(99.5, float(browser._v222_overlap_var.get())))
            spacing = diameter * (1.0 - overlap / 100.0)
            browser._v222_spacing_label_var.set(f"Nominal center spacing {spacing:g} µm; footprint FWHM {diameter:g} µm")
        except Exception:
            browser._v222_spacing_label_var.set("Enter numeric tip diameter and overlap")
    try:
        browser._v222_tip_diameter_var.trace_add("write", update_spacing_label)
        browser._v222_overlap_var.trace_add("write", update_spacing_label)
    except Exception:
        pass
    update_spacing_label()

    def apply_preset(name: str) -> None:
        if name == "Raw measured":
            browser._v216_render_mode_var.set("Measured points"); browser._v222_surface_model_var.set("Standard interpolation"); browser._v216_smoothing_var.set("None")
        elif name == "Origin-style smooth":
            browser._v216_render_mode_var.set("Contour + points"); browser._v222_surface_model_var.set("Origin-style smooth"); browser._v216_smoothing_var.set("Light")
            browser._v216_adaptive_contours_var.set(True); browser._v216_mask_hull_var.set(True); browser._v222_clip_overshoot_var.set(True)
        elif name == "SECCM physical footprint":
            browser._v216_render_mode_var.set("Contour + points"); browser._v222_surface_model_var.set("SECCM tip footprint")
            browser._v216_adaptive_contours_var.set(True); browser._v216_mask_hull_var.set(True); browser._v222_clip_overshoot_var.set(True)
        elif name == "Publication contour":
            browser._v216_render_mode_var.set("Contour + points"); browser._v216_adaptive_contours_var.set(True)
            browser._v216_contour_line_width_var.set("1.1"); browser._v216_contour_labels_var.set(False); browser._v222_grid_quality_var.set("Publication")
        _collect_multidim_options(browser, getattr(multidim, "_v216_options", None))

    for preset in ("Raw measured", "Origin-style smooth", "SECCM physical footprint", "Publication contour"):
        ttk.Button(row3d, text=preset, command=lambda n=preset: apply_preset(n)).pack(side="left", padx=2)

    row4 = ttk.Frame(outer, style="Card.TFrame"); row4.pack(fill="x", pady=(0, 5))
    ttk.Checkbutton(row4, text="Use background image", variable=browser._v216_background_enabled_var).pack(side="left", padx=(0, 6))
    bg_label = ttk.Label(row4, text="No background loaded", style="Muted.TLabel"); bg_label.pack(side="left", padx=(0, 8))

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

    def background_settings() -> None:
        win = tk.Toplevel(browser); win.title(f"FaradaIQ Studio {VERSION} — background image settings")
        frame = ttk.Frame(win, padding=12); frame.pack(fill="both", expand=True)
        ttk.Checkbutton(frame, text="Use background image", variable=browser._v216_background_enabled_var).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Button(frame, text="Choose image…", command=choose_background).grid(row=1, column=0, sticky="ew", padx=(0, 5))
        ttk.Button(frame, text="Clear", command=clear_background).grid(row=1, column=1, sticky="ew")
        ttk.Label(frame, textvariable=browser._v216_background_path_var, wraplength=560).grid(row=2, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Label(frame, text="Fit / resize mode").grid(row=3, column=0, sticky="w")
        ttk.Combobox(frame, textvariable=browser._v216_background_fit_var, state="readonly",
                     values=("Fit to data extent", "Preserve aspect", "Manual extent"), width=24).grid(row=3, column=1, sticky="ew")
        ttk.Label(frame, text="Image opacity").grid(row=4, column=0, sticky="w")
        ttk.Entry(frame, textvariable=browser._v216_background_opacity_var).grid(row=4, column=1, sticky="ew")
        for r, (label, var) in enumerate((("X min", browser._v216_background_xmin_var), ("X max", browser._v216_background_xmax_var),
                                          ("Y min", browser._v216_background_ymin_var), ("Y max", browser._v216_background_ymax_var)), start=5):
            ttk.Label(frame, text=label).grid(row=r, column=0, sticky="w")
            ttk.Entry(frame, textvariable=var).grid(row=r, column=1, sticky="ew")
        ttk.Button(frame, text="Apply and close", command=win.destroy).grid(row=9, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        frame.columnconfigure(1, weight=1)

    ttk.Button(row4, text="Choose…", command=choose_background).pack(side="left", padx=2)
    ttk.Button(row4, text="Resize/settings…", command=background_settings).pack(side="left", padx=2)
    ttk.Button(row4, text="Clear", command=clear_background).pack(side="left", padx=2)
    ttk.Label(row4, text="Off by default.", style="Muted.TLabel").pack(side="left", padx=(8, 0))

    status_var = tk.StringVar(value="Select data in the experiment map, then open an interactive view.")
    ttk.Label(outer, textvariable=status_var, style="Muted.TLabel", wraplength=950).pack(anchor="w", fill="x", pady=(0, 4))
    result_box = tk.Text(outer, height=10, wrap="word")
    result_box.pack(fill="both", expand=True)
    try:
        browser._style_text(result_box)
    except Exception:
        pass
    result_box.insert("1.0", "Generated Altair, Plotly, and hyperspectral outputs are saved under FaradaIQ_interactive_views.\n")
    result_box.configure(state="disabled")

    def run() -> None:
        mode = browser._v216_viz_mode_var.get()
        metric = browser._v216_viz_metric_var.get()
        opts = _collect_multidim_options(browser, getattr(multidim, "_v216_options", None))
        open_btn.configure(state="disabled")
        status_var.set(f"Preparing {mode}…")
        try:
            browser.log(f"Preparing {mode}; metric={metric}; free-box zoom={'off' if opts.get('lock_xy_aspect') else 'on'}")
        except Exception:
            pass

        state: Dict[str, Any] = {"done": False, "result": {}, "error": None}

        def worker() -> None:
            try:
                state["result"] = multidim.generate(browser, mode, metric, opts)
            except Exception as exc:
                state["error"] = exc
            finally:
                state["done"] = True

        def poll_result() -> None:
            if not state["done"]:
                try:
                    browser.after(120, poll_result)
                except Exception:
                    pass
                return
            result = state.get("result") or {}
            err = state.get("error")
            open_btn.configure(state="normal")
            if err is not None:
                msg = f"Multidimensional visualization failed: {err}"
            else:
                _add_multidim_plot_view(browser, core, result, mode, metric, opts)
                msg = f"Opened {result.get('engine', mode)} and added it to Plot/view list: {result.get('html', '')}"
            status_var.set(msg)
            try:
                result_box.configure(state="normal")
                result_box.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
                result_box.see("end")
                result_box.configure(state="disabled")
            except Exception:
                pass
            try:
                browser.log(msg)
            except Exception:
                pass

        threading.Thread(target=worker, name="FaradaIQ-v217-multidim", daemon=True).start()
        browser.after(120, poll_result)

    open_btn.configure(command=run)
    browser._v217_run_multidim = run
    browser._v217_multidim_result_box = result_box
    return outer


def _build_classic(browser: Any, parent: Any, core: Mapping[str, Any], ttk: Any, tk: Any) -> None:
    ensure_vars = core.get("_v206_ensure_vars")
    if callable(ensure_vars):
        ensure_vars(browser)

    row = ttk.Frame(parent, style="Card.TFrame"); row.pack(fill="x", pady=(0, 4))
    ttk.Button(row, text="Plot/overlay selected", command=lambda: browser.plot_selected(clear=True)).pack(side="left", padx=(0, 4))
    ttk.Button(row, text="Overlay add", command=lambda: browser.plot_selected(clear=False)).pack(side="left", padx=(0, 4))
    ttk.Button(row, text="Clear plot", command=browser.clear_plot).pack(side="left", padx=(0, 4))
    ttk.Button(row, text="Axis/title", command=browser.open_axis_title_dialog).pack(side="left", padx=(0, 8))
    ttk.Label(row, text="Mode", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    modes = ["Auto", "CV / LSV", "CV I-V", "CV I-t", "CV E-t", "CV Q-t", "CA/CP I-t", "CA/CP Q-t",
             "EIS Nyquist", "EIS Nyquist freq-color (-Zimag)", "Bode magnitude", "Bode phase", "DRT", "Tafel trace log|I| vs V", "Numeric", "Approach curve"]
    browser.plot_mode = ttk.Combobox(row, textvariable=browser.plot_mode_var, state="readonly", width=24, values=modes)
    browser.plot_mode.pack(side="left", padx=(0, 8))

    def _v292_apply_tafel_mode(_event=None):
        """Immediately render the Tafel transformation when that mode is chosen."""
        try:
            mode_text = str(browser.plot_mode_var.get() or "").lower()
        except Exception:
            return
        if "tafel" not in mode_text:
            return
        # log10(|I|) is already a logarithmic transformation.  A second log
        # axis is both misleading and invalid for the usual negative log10
        # current values.
        for attr in ("plot_xscale_var", "plot_yscale_var"):
            try:
                getattr(browser, attr).set("Linear")
            except Exception:
                pass
        try:
            panel = getattr(browser, "_v206_pub_panel", None) or getattr(browser, "_v200_pub_panel", None)
            curves = list(getattr(panel, "last_curves", []) or []) if panel is not None else []
            if curves:
                panel.render(curves, clear=True)
            else:
                browser.plot_selected(clear=True)
        except Exception as exc:
            try:
                browser.log(f"Tafel mode apply failed: {exc}")
            except Exception:
                pass

    browser.plot_mode.bind("<<ComboboxSelected>>", _v292_apply_tafel_mode, add="+")
    browser._v292_apply_tafel_mode = _v292_apply_tafel_mode
    ttk.Label(row, text="Max curves", style="Muted.TLabel").pack(side="left", padx=(0, 3))
    ttk.Entry(row, textvariable=browser.max_curves_var, width=6).pack(side="left", padx=(0, 8))
    ttk.Button(row, text="Popout all sections", command=lambda: _open_browser_popout(browser, core, ttk, tk)).pack(side="right")

    export_row = ttk.Frame(parent, style="Card.TFrame"); export_row.pack(fill="x", pady=(0, 4))
    text = "Export current comparison view with figure + data + H5 + summary"
    cmd = getattr(browser, "export_current_comparison", None)
    if not callable(cmd):
        cmd = getattr(browser, "export_current_view", None)
    ttk.Button(export_row, text=text, command=cmd if callable(cmd) else (lambda: None)).pack(fill="x")

    browser.plot_holder = ttk.Frame(parent, style="Card.TFrame")
    browser.plot_holder.pack(fill="both", expand=True)
    try:
        browser._v206_pub_panel = None
        panel_cls = core.get("FaradaIQPublicationPlotPanelV206") or core.get("FaradaIQPublicationPlotPanel")
        if panel_cls is not None:
            browser._v206_pub_panel = panel_cls(browser)
            browser._v200_pub_panel = browser._v206_pub_panel
    except Exception as exc:
        ttk.Label(browser.plot_holder, text=f"Classic plot panel failed to initialize: {exc}", style="Muted.TLabel").pack(anchor="w")


def _copy_tree(src: Any, dst: Any) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    def rec(parent_src: str, parent_dst: str) -> None:
        for iid in src.get_children(parent_src):
            vals = src.item(iid)
            ni = dst.insert(parent_dst, "end", text=vals.get("text", ""), values=vals.get("values", ()), open=bool(vals.get("open", False)))
            mapping[ni] = iid
            rec(iid, ni)
    rec("", "")
    return mapping


def _figure_snapshot(browser: Any, tk: Any):
    try:
        from PIL import Image, ImageTk
        fig = getattr(getattr(browser, "_v206_pub_panel", None), "figure", None) or getattr(browser, "_figure", None)
        if fig is None:
            return None
        buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=110, bbox_inches="tight"); buf.seek(0)
        img = Image.open(buf); img.thumbnail((1050, 620))
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


def _open_browser_popout(browser: Any, core: Mapping[str, Any], ttk: Any, tk: Any) -> Any:
    existing = getattr(browser, "_v217_browser_popout", None)
    try:
        if existing is not None and existing.winfo_exists():
            existing.lift(); return existing
    except Exception:
        pass

    win = tk.Toplevel(browser); win.title(f"FaradaIQ Studio {VERSION} — browser sections popout")
    win.geometry("1420x900")
    tabs = ttk.Notebook(win); tabs.pack(fill="both", expand=True, padx=8, pady=8)

    # Experiment map tab.
    map_tab = ttk.Frame(tabs, padding=6); tabs.add(map_tab, text="Experiment map")
    tree = ttk.Treeview(map_tab, columns=("kind", "size"), show="tree headings", selectmode="extended")
    tree.heading("#0", text="Technique / group / file"); tree.heading("kind", text="Type"); tree.heading("size", text="Size")
    tree.column("#0", width=650); tree.column("kind", width=150); tree.column("size", width=100)
    y = ttk.Scrollbar(map_tab, orient="vertical", command=tree.yview); tree.configure(yscrollcommand=y.set)
    tree.pack(side="left", fill="both", expand=True); y.pack(side="right", fill="y")
    mapping = _copy_tree(browser.tree, tree)
    reverse = {v: k for k, v in mapping.items()}
    sync = {"busy": False}

    def pop_to_main(_event=None):
        if sync["busy"]: return
        sync["busy"] = True
        try:
            browser.tree.selection_set([mapping[i] for i in tree.selection() if i in mapping])
            browser.tree.see(mapping[tree.selection()[0]]) if tree.selection() else None
            browser.preview_selected()
        finally:
            sync["busy"] = False

    def main_to_pop(_event=None):
        if sync["busy"]: return
        sync["busy"] = True
        try:
            tree.selection_set([reverse[i] for i in browser.tree.selection() if i in reverse])
        finally:
            sync["busy"] = False

    tree.bind("<<TreeviewSelect>>", pop_to_main)
    browser.tree.bind("<<TreeviewSelect>>", main_to_pop, add="+")

    # Classic tab with synchronized controls and a live snapshot.
    classic = ttk.Frame(tabs, padding=8); tabs.add(classic, text="Classic - 2D")
    crow = ttk.Frame(classic); crow.pack(fill="x", pady=(0, 4))
    snap_label = ttk.Label(classic, text="No classic plot yet.", anchor="center"); snap_label.pack(fill="both", expand=True)
    snap_state = {"image": None}
    def refresh_snapshot():
        img = _figure_snapshot(browser, tk)
        if img is not None:
            snap_state["image"] = img; snap_label.configure(image=img, text="")
        else:
            snap_label.configure(image="", text="No classic plot yet.")
    def action(fn):
        def wrapped():
            fn(); win.after(350, refresh_snapshot)
        return wrapped
    ttk.Button(crow, text="Plot selected", command=action(lambda: browser.plot_selected(clear=True))).pack(side="left", padx=2)
    ttk.Button(crow, text="Overlay add", command=action(lambda: browser.plot_selected(clear=False))).pack(side="left", padx=2)
    ttk.Button(crow, text="Clear", command=action(browser.clear_plot)).pack(side="left", padx=2)
    ttk.Button(crow, text="Axis/title", command=browser.open_axis_title_dialog).pack(side="left", padx=2)
    pop_mode = ttk.Combobox(crow, textvariable=browser.plot_mode_var, state="readonly", width=24, values=browser.plot_mode.cget("values"))
    pop_mode.pack(side="left", padx=6)
    try:
        pop_mode.bind("<<ComboboxSelected>>", getattr(browser, "_v292_apply_tafel_mode"), add="+")
    except Exception:
        pass
    ttk.Entry(crow, textvariable=browser.max_curves_var, width=6).pack(side="left", padx=2)
    ttk.Button(crow, text="Refresh snapshot", command=refresh_snapshot).pack(side="right")
    refresh_snapshot()

    # Interactive tab reuses the same variables and actions.
    multi = ttk.Frame(tabs, padding=6); tabs.add(multi, text="1D/2D/3D Visualization")
    _build_multidim_controls(browser, multi, core, ttk, tk, core.get("filedialog"))

    # Plot/views tab.
    views_tab = ttk.Frame(tabs, padding=8); tabs.add(views_tab, text="Plot/views")
    vlist = tk.Listbox(views_tab, exportselection=False); vlist.pack(fill="both", expand=True)
    vrow = ttk.Frame(views_tab); vrow.pack(fill="x", pady=(6, 0))
    def refresh_views():
        vlist.delete(0, "end")
        src = getattr(browser, "plot_view_list", None)
        if src is not None:
            try:
                for i in range(src.size()): vlist.insert("end", src.get(i))
            except Exception: pass
    def sync_view_to_main(_event=None):
        sel = vlist.curselection()
        if not sel: return
        try:
            browser.plot_view_list.selection_clear(0, "end"); browser.plot_view_list.selection_set(sel[0]); browser.plot_view_list.see(sel[0])
        except Exception: pass
    vlist.bind("<<ListboxSelect>>", sync_view_to_main)
    def call_first(names: List[str]):
        for name in names:
            fn = getattr(browser, name, None)
            if callable(fn):
                fn(); break
        refresh_views(); refresh_snapshot()
    ttk.Button(vrow, text="Load selected", command=lambda: call_first(["load_selected_plot_view", "load_plot_view", "_load_plot_view"])).pack(side="left", padx=2)
    ttk.Button(vrow, text="Rename", command=lambda: call_first(["rename_selected_view", "rename_plot_view", "_rename_plot_view"])).pack(side="left", padx=2)
    ttk.Button(vrow, text="Clear list", command=lambda: call_first(["clear_plot_view_list", "clear_plot_views", "_clear_plot_views"])).pack(side="left", padx=2)
    ttk.Button(vrow, text="Refresh", command=refresh_views).pack(side="right")
    refresh_views()

    # QA/log tab.
    qa_tab = ttk.Frame(tabs, padding=8); tabs.add(qa_tab, text="Tools / QA / log")
    qtext = tk.Text(qa_tab, wrap="word"); qtext.pack(fill="both", expand=True)
    try: browser._style_text(qtext)
    except Exception: pass
    def refresh_qa():
        parts=[]
        for name in ("qa_text", "log_text"):
            w=getattr(browser,name,None)
            if w is not None:
                try: parts.append(w.get("1.0","end").strip())
                except Exception: pass
        qtext.configure(state="normal"); qtext.delete("1.0","end"); qtext.insert("1.0","\n\n".join(parts)); qtext.configure(state="disabled")
    ttk.Button(qa_tab, text="Refresh QA/log", command=refresh_qa).pack(anchor="e", pady=(0,4))
    refresh_qa()

    browser._v217_browser_popout = win
    return win


def _install_browser_tabs(browser: Any, core: Mapping[str, Any], ttk: Any, tk: Any) -> None:
    if getattr(browser, "_v217_tabs_installed", False):
        return
    old_plot_holder = getattr(browser, "plot_holder", None)
    if old_plot_holder is None:
        return
    center = getattr(old_plot_holder, "master", None)
    if center is None:
        return

    # Hide all old center widgets and the old bottom multidimensional bar.  We do
    # not re-parent them because Tk widget parents are immutable.
    for child in list(center.winfo_children()):
        _forget(child)
    for child in list(browser.winfo_children()):
        if child is center or child is getattr(center, "master", None):
            continue
        text = " | ".join(_widget_text(w) for w in _walk(child)).lower()
        if "hyperspectral visualization" in text or "altair interactive 2d" in text:
            _forget(child)

    notebook = ttk.Notebook(center)
    notebook.pack(fill="both", expand=True)
    classic = ttk.Frame(notebook, style="Card.TFrame", padding=5)
    multi = ttk.Frame(notebook, style="Card.TFrame", padding=5)
    notebook.add(classic, text="Classic - 2D")
    notebook.add(multi, text="1D/2D/3D Visualization")

    _build_classic(browser, classic, core, ttk, tk)
    _build_multidim_controls(browser, multi, core, ttk, tk, core.get("filedialog"))

    browser._v217_center_notebook = notebook
    browser._v217_classic_tab = classic
    browser._v217_multidim_tab = multi
    # Compatibility aliases retained for the existing v216 browser smoke tests
    # and for external helpers that still inspect these stable attributes.
    browser._v216_center_notebook = notebook
    browser._v216_classic_tab = classic
    browser._v216_multidim_tab = multi
    browser._v217_tabs_installed = True
    try: notebook.select(classic)
    except Exception: pass


def install(core: Dict[str, Any]) -> None:
    Browser = core.get("FaradaIQH5Browser")
    SessionReaderGUI = core.get("SessionReaderGUI")
    ttk = core.get("ttk"); tk = core.get("tk")
    if Browser is None or ttk is None or tk is None:
        raise RuntimeError("FaradaIQ browser GUI classes are unavailable")
    if getattr(Browser, "_v217_tabs_wrapper", False):
        return

    previous = Browser._build_visible_ui_first
    def build(self: Any) -> None:
        previous(self)
        _install_browser_tabs(self, core, ttk, tk)
        _apply_tagline(self)
        try: self.title(f"FaradaIQ Studio {VERSION} — unified data browser — {getattr(self, 'base_name', '')}")
        except Exception: pass
    Browser._build_visible_ui_first = build
    Browser._v217_tabs_wrapper = True

    # Interactive Plot/view entries reopen their generated HTML viewer instead of
    # being forced through the Matplotlib CSV loader. Classic entries keep the
    # existing load behavior.
    if not getattr(Browser, "_v217_interactive_view_loader", False):
        previous_load = getattr(Browser, "load_selected_plot_view", None)
        def load_selected_plot_view(self: Any, *args: Any, **kwargs: Any):
            try:
                sel = list(self.plot_view_list.curselection())
                if sel and sel[0] < len(getattr(self, "plot_views", [])):
                    view = self.plot_views[sel[0]]
                    html = Path(str(view.get("interactive_html") or ""))
                    if bool(view.get("multidimensional")) and html.exists():
                        from faradaiq.multidim_v216 import _open_html_process
                        _open_html_process(html)
                        self.current_view_index = sel[0]
                        try: self.log(f"Opened interactive Plot/view: {view.get('name')} | {html}")
                        except Exception: pass
                        return view
            except Exception as exc:
                try: self.log(f"Interactive Plot/view open failed; using classic loader: {exc}")
                except Exception: pass
            if callable(previous_load):
                return previous_load(self, *args, **kwargs)
            return None
        Browser.load_selected_plot_view = load_selected_plot_view
        Browser._v217_interactive_view_loader = True

    if SessionReaderGUI is not None and not getattr(SessionReaderGUI, "_v217_tagline_wrapper", False):
        prev_init = SessionReaderGUI.__init__
        def init(self: Any, *args: Any, **kwargs: Any) -> None:
            prev_init(self, *args, **kwargs)
            for delay in (20, 100, 400, 1200):
                try: self.after(delay, lambda w=self: _apply_tagline(w))
                except Exception: pass
        SessionReaderGUI.__init__ = init
        SessionReaderGUI._v217_tagline_wrapper = True
