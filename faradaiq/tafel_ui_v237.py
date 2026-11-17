"""Responsive, non-blocking Tafel Analysis workspace for FaradaIQ Studio v273."""
from __future__ import annotations

import json
import os
import math
import queue
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


def _display_source_identity(value: Any) -> str:
    """Compact source identity that remains unique when point files share a basename."""
    path = Path(str(value or ""))
    if not path.name:
        return "unknown source"
    parent = path.parent.name
    if parent and parent not in (".", path.anchor):
        return f"{parent} / {path.name}"
    return path.name

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

VERSION = "v297"


def _safe(text: object) -> str:
    import re
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "tafel")).strip("._")
    return value or "tafel"


def _walk_text_button(root: tk.Misc, text: str):
    for child in root.winfo_children():
        try:
            if str(child.cget("text")) == text:
                return child
        except Exception:
            pass
        found = _walk_text_button(child, text)
        if found is not None:
            return found
    return None


class FaradaIQTafelAnalysisWindowV234(tk.Toplevel):
    """Tafel workspace that runs every expensive fit in a child process."""

    def __init__(self, browser=None, paths=None, core: Optional[Dict[str, Any]] = None):
        super().__init__(browser)
        self.browser = browser
        self._core = core or {}
        self.paths = [Path(p) for p in (paths or [])]
        output_func = self._core.get("_v130_tafel_output_root")
        self.output_root = Path(output_func(browser) if callable(output_func) else Path.cwd() / "FaradaIQ_tafel_analysis")
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.last_output_dir: Optional[str] = None
        self._process: Optional[subprocess.Popen] = None
        self._events: "queue.Queue[dict]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._poll_id = None
        self._started = 0.0
        self._job_file: Optional[Path] = None
        self._result_json: Optional[Path] = None
        self._result_payload: Dict[str, Any] = {}
        self._result_by_iid: Dict[str, Dict[str, Any]] = {}
        self._plot_result: Optional[Dict[str, Any]] = None
        self.title(f"FaradaIQ Tafel Analysis {VERSION}")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._set_geometry()
        try:
            icon = self._core.get("_v83_set_window_icon")
            if callable(icon):
                icon(self)
        except Exception:
            pass
        self._build()
        self._refresh_file_list()
        self._apply_theme()

    def _set_geometry(self):
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            width = max(1080, min(1440, int(sw * 0.91)))
            height = max(720, min(920, int(sh * 0.86)))
            x = max(0, (sw - width) // 2)
            y = max(0, (sh - height) // 3)
            self.geometry(f"{width}x{height}+{x}+{y}")
            self.minsize(980, 680)
        except Exception:
            self.geometry("1280x820")

    def _theme_name(self) -> str:
        parent = self.browser
        for obj in (parent, getattr(parent, "gui", None)):
            try:
                value = str(getattr(obj, "_faradaiq_theme_mode", "") or "")
                if value:
                    return value
            except Exception:
                pass
        return os.environ.get("FARADAIQ_THEME", "High Contrast")

    def _apply_theme(self):
        try:
            func = self._core.get("_v36_apply_theme")
            if callable(func):
                func(self, self._theme_name())
                self.after(120, lambda: func(self, self._theme_name()))
        except Exception:
            pass
        self.after(140, self._style_matplotlib)

    def _build(self):
        outer = ttk.Frame(self, padding=(10, 9))
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="FaradaIQ Tafel Analysis", style="Hero.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="Non-blocking CV/LSV/IV kinetics: stable-window Tafel fits, bootstrap uncertainty, local-slope plateau diagnostics, iR correction, and Butler–Volmer fitting.",
            style="Muted.TLabel", wraplength=920,
        ).pack(side="left", fill="x", expand=True, padx=(12, 0))

        status = ttk.Frame(outer)
        status.pack(fill="x", pady=(7, 7))
        self.status_var = tk.StringVar(value="Ready")
        self.elapsed_var = tk.StringVar(value="")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress = ttk.Progressbar(status, variable=self.progress_var, maximum=100.0, length=260)
        self.progress.pack(side="left", padx=(0, 8))
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Label(status, textvariable=self.elapsed_var, style="Muted.TLabel").pack(side="right")

        main = ttk.PanedWindow(outer, orient="horizontal")
        main.pack(fill="both", expand=True)
        left_shell = ttk.Frame(main)
        right = ttk.Frame(main)
        try:
            main.add(left_shell, weight=0)
            main.add(right, weight=1)
        except TypeError:
            main.add(left_shell); main.add(right)

        # Scrollable controls keep every setting usable on laptop displays.
        control_canvas = tk.Canvas(left_shell, width=430, highlightthickness=0, borderwidth=0)
        control_scroll = ttk.Scrollbar(left_shell, orient="vertical", command=control_canvas.yview)
        control_canvas.configure(yscrollcommand=control_scroll.set)
        control_scroll.pack(side="right", fill="y")
        control_canvas.pack(side="left", fill="both", expand=True)
        left = ttk.Frame(control_canvas, padding=(0, 0, 8, 0))
        canvas_window = control_canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind("<Configure>", lambda _e: control_canvas.configure(scrollregion=control_canvas.bbox("all")))
        control_canvas.bind("<Configure>", lambda e: control_canvas.itemconfigure(canvas_window, width=max(380, e.width)))

        files_box = ttk.LabelFrame(left, text="Selected CV / LSV / IV data", padding=(8, 8))
        files_box.pack(fill="x")
        self.files_text = tk.Text(files_box, width=46, height=7, wrap="word")
        self.files_text.pack(fill="x", expand=False)
        file_row = ttk.Frame(files_box); file_row.pack(fill="x", pady=(6, 0))
        ttk.Button(file_row, text="Choose files", command=self._choose_files).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(file_row, text="Use selected Plot/view", command=self._use_selected).pack(side="left", fill="x", expand=True)

        basic = ttk.LabelFrame(left, text="Electrode and fit settings", padding=(8, 8))
        basic.pack(fill="x", pady=(8, 0))
        self.area_var = tk.StringVar(value="1.0")
        self.n_var = tk.StringVar(value="1")
        self.temp_var = tk.StringVar(value="298.15")
        self.eeq_var = tk.StringVar(value="")
        self.ru_var = tk.StringVar(value="0.0")
        self.fit_mode_var = tk.StringVar(value="Balanced")
        self.scan_branch_var = tk.StringVar(value="Whole trace / LSV")
        rows = [
            ("Surface area / cm²", self.area_var),
            ("Electrons, n", self.n_var),
            ("Temperature / K", self.temp_var),
            ("Eeq or OCP / V (blank = auto)", self.eeq_var),
            ("Uncompensated resistance / Ω", self.ru_var),
        ]
        for row, (label, variable) in enumerate(rows):
            ttk.Label(basic, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(basic, textvariable=variable, width=22).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
        r = len(rows)
        ttk.Label(basic, text="Optimization mode").grid(row=r, column=0, sticky="w", pady=2)
        ttk.Combobox(basic, textvariable=self.fit_mode_var, values=("Fast", "Balanced", "Thorough"), state="readonly", width=20).grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=2)
        ttk.Label(basic, text="CV scan branch").grid(row=r+1, column=0, sticky="w", pady=2)
        ttk.Combobox(
            basic, textvariable=self.scan_branch_var, state="readonly", width=26,
            values=("Whole trace / LSV", "Forward scan only", "Reverse scan only", "Forward + reverse separately"),
        ).grid(row=r+1, column=1, sticky="ew", padx=(8, 0), pady=2)
        basic.columnconfigure(1, weight=1)

        advanced = ttk.LabelFrame(left, text="Tafel region and confidence", padding=(8, 8))
        advanced.pack(fill="x", pady=(8, 0))
        self.adjust_var = tk.BooleanVar(value=True)
        self.bootstrap_var = tk.BooleanVar(value=True)
        self.win_var = tk.StringVar(value="120")
        self.cath_var = tk.StringVar(value="-0.180, -0.060")
        self.anod_var = tk.StringVar(value="0.060, 0.180")
        self.r2_var = tk.StringVar(value="0.970")
        self.bootstrap_samples_var = tk.StringVar(value="100")
        arows = [
            ("Butler–Volmer total η window / mV", self.win_var),
            ("Manual cathodic η range / V", self.cath_var),
            ("Manual anodic η range / V", self.anod_var),
            ("Minimum automatic-window R²", self.r2_var),
            ("Bootstrap samples", self.bootstrap_samples_var),
        ]
        for row, (label, variable) in enumerate(arows):
            ttk.Label(advanced, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(advanced, textvariable=variable, width=22).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
        ttk.Checkbutton(advanced, text="Subtract current at supplied Eeq", variable=self.adjust_var).grid(row=len(arows), column=0, columnspan=2, sticky="w", pady=(5, 0))
        ttk.Checkbutton(advanced, text="Bootstrap slope and j₀ uncertainty", variable=self.bootstrap_var).grid(row=len(arows)+1, column=0, columnspan=2, sticky="w")
        advanced.columnconfigure(1, weight=1)

        actions = ttk.LabelFrame(left, text="Analysis controls", padding=(8, 8))
        actions.pack(fill="x", pady=(8, 0))
        self.run_button = ttk.Button(actions, text="Run Tafel analysis", command=self._run_selected)
        self.run_button.grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=2)
        self.cancel_button = ttk.Button(actions, text="Cancel analysis", command=self._cancel, state="disabled")
        self.cancel_button.grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Button(actions, text="Generate dummy test", command=self._dummy_test).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=2)
        ttk.Button(actions, text="Open output folder", command=self._open_last).grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Button(actions, text="Close", command=self._close).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        actions.columnconfigure(0, weight=1); actions.columnconfigure(1, weight=1)

        ttk.Label(
            left,
            text="Scientific guardrail: use the slope-stability and fit-quality diagnostics. A visually linear region alone is not proof of kinetic control.",
            style="Muted.TLabel", wraplength=400,
        ).pack(anchor="w", pady=(8, 5))

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)
        results_tab = ttk.Frame(self.notebook, padding=(5, 5))
        plot_tab = ttk.Frame(self.notebook, padding=(5, 5))
        diag_tab = ttk.Frame(self.notebook, padding=(5, 5))
        self.notebook.add(results_tab, text="Results")
        self.notebook.add(plot_tab, text="Interactive plot")
        self.notebook.add(diag_tab, text="Diagnostics and log")

        columns = ("point", "x", "y", "distance", "quality", "method", "branch", "slope", "ci", "logj0", "alpha", "sigma", "r2", "n", "window")
        self.results = ttk.Treeview(results_tab, columns=columns, show="headings", selectmode="browse")
        headers = [
            ("point", "Point", 55), ("x", "X / µm", 75), ("y", "Y / µm", 75), ("distance", "Distance / µm", 90),
            ("quality", "Quality", 85), ("method", "Method / source", 260), ("branch", "Branch", 85),
            ("slope", "Slope mV/dec", 105), ("ci", "95% slope CI", 130), ("logj0", "log10 j₀", 90),
            ("alpha", "α", 60), ("sigma", "σ", 60), ("r2", "R²", 65), ("n", "N", 45), ("window", "η window mV", 90),
        ]
        for key, label, width in headers:
            self.results.heading(key, text=label)
            self.results.column(key, width=width, stretch=(key == "method"))
        yscroll = ttk.Scrollbar(results_tab, orient="vertical", command=self.results.yview)
        xscroll = ttk.Scrollbar(results_tab, orient="horizontal", command=self.results.xview)
        self.results.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.results.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        results_tab.columnconfigure(0, weight=1); results_tab.rowconfigure(0, weight=1)
        self.results.bind("<<TreeviewSelect>>", self._on_result_selected)

        toolbar_row = ttk.Frame(plot_tab)
        toolbar_row.pack(fill="x", pady=(0, 4))
        self.plot_holder = ttk.Frame(plot_tab)
        self.plot_holder.pack(fill="both", expand=True)
        self._figure = None; self._canvas = None; self._nav = None
        self._build_plot_canvas()
        for text, command in (
            ("Home", lambda: self._nav_call("home")), ("Back", lambda: self._nav_call("back")),
            ("Forward", lambda: self._nav_call("forward")), ("Pan", lambda: self._nav_call("pan")),
            ("Box zoom", lambda: self._nav_call("zoom")), ("Fit view", self._fit_view),
            ("Save figure", self._save_figure),
        ):
            ttk.Button(toolbar_row, text=text, command=command).pack(side="left", padx=(0, 4))
        ttk.Label(toolbar_row, text="Text controls replace low-contrast Matplotlib icon buttons.", style="Muted.TLabel").pack(side="right")

        self.log = ScrolledText(diag_tab, wrap="word")
        self.log.pack(fill="both", expand=True)
        self._log(f"Ready. Output root: {self.output_root}")

    def _build_plot_canvas(self):
        try:
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
            self._figure = Figure(figsize=(9.4, 7.0), dpi=100)
            self._canvas = FigureCanvasTkAgg(self._figure, master=self.plot_holder)
            self._canvas.get_tk_widget().pack(fill="both", expand=True)
            # Keep the native toolbar hidden; its callbacks power the visible text buttons.
            self._nav = NavigationToolbar2Tk(self._canvas, self.plot_holder, pack_toolbar=False)
            self._draw_empty_plot()
        except Exception as exc:
            ttk.Label(self.plot_holder, text=f"Matplotlib preview unavailable: {exc}").pack(fill="both", expand=True)

    def _style_matplotlib(self):
        if self._figure is None:
            return
        dark = str(self._theme_name()).lower() not in ("light", "warm")
        face = "#000000" if dark else "#ffffff"
        fg = "#ffffff" if dark else "#111111"
        grid = "#555555" if dark else "#d0d0d0"
        try:
            self._figure.set_facecolor(face)
            for ax in self._figure.axes:
                ax.set_facecolor(face)
                ax.tick_params(colors=fg)
                ax.xaxis.label.set_color(fg); ax.yaxis.label.set_color(fg); ax.title.set_color(fg)
                for spine in ax.spines.values(): spine.set_color(fg)
                ax.grid(True, alpha=0.25, color=grid)
                leg = ax.get_legend()
                if leg:
                    leg.get_frame().set_facecolor(face); leg.get_frame().set_edgecolor(grid)
                    for text in leg.get_texts(): text.set_color(fg)
            self._canvas.draw_idle()
        except Exception:
            pass

    def _draw_empty_plot(self):
        if self._figure is None:
            return
        self._figure.clear()
        ax = self._figure.add_subplot(111)
        ax.text(0.5, 0.5, "Run an analysis to display polarization, Tafel fits, and local-slope stability.", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        self._style_matplotlib()

    def _nav_call(self, method: str):
        try:
            getattr(self._nav, method)()
        except Exception:
            pass

    def _fit_view(self):
        try:
            for ax in self._figure.axes:
                ax.relim(); ax.autoscale_view()
            self._canvas.draw_idle()
        except Exception:
            pass

    def _save_figure(self):
        if self._figure is None:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".png", filetypes=[("PNG", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg"), ("TIFF", "*.tif")])
        if path:
            self._figure.savefig(path, dpi=300, bbox_inches="tight")
            self._log(f"Saved interactive Tafel figure: {path}")

    def _parse_pair(self, text: str, default):
        parser = self._core.get("_v130_parse_range_pair")
        if callable(parser):
            return parser(text, default)
        try:
            values = [float(x.strip()) for x in text.replace(";", ",").split(",") if x.strip()]
            return tuple(values[:2]) if len(values) >= 2 else default
        except Exception:
            return default

    def _settings(self):
        import faradaiq_tafel_adapter as tafel
        eeq_text = self.eeq_var.get().strip()
        mode = self.fit_mode_var.get()
        if mode == "Fast":
            scan_windows = (60.0, 120.0, 180.0)
            bootstrap = min(50, int(float(self.bootstrap_samples_var.get() or 50)))
        elif mode == "Thorough":
            scan_windows = (30.0, 60.0, 90.0, 120.0, 180.0, 240.0, 300.0)
            bootstrap = max(200, int(float(self.bootstrap_samples_var.get() or 200)))
        else:
            scan_windows = (30.0, 60.0, 90.0, 120.0, 180.0, 240.0)
            bootstrap = max(50, int(float(self.bootstrap_samples_var.get() or 100)))
        return tafel.TafelSettings(
            surface_area_cm2=float(self.area_var.get()), n_electrons=int(float(self.n_var.get())),
            temperature_K=float(self.temp_var.get()), eeq_V=None if not eeq_text else float(eeq_text),
            current_adjust=bool(self.adjust_var.get()), fitting_window_mV=float(self.win_var.get()),
            eta_scan_windows_mV=scan_windows,
            manual_cathodic_range_V=self._parse_pair(self.cath_var.get(), (-0.18, -0.06)),
            manual_anodic_range_V=self._parse_pair(self.anod_var.get(), (0.06, 0.18)),
            auto_min_r2=float(self.r2_var.get()), bootstrap_samples=bootstrap,
            enable_bootstrap=bool(self.bootstrap_var.get()), uncompensated_resistance_ohm=float(self.ru_var.get() or 0.0),
            optimizer_mode=mode, scan_branch_mode=self.scan_branch_var.get(),
        )

    def _log(self, text: str):
        stamp = time.strftime("%H:%M:%S")
        message = f"[{stamp}] {text}"
        try:
            self.log.insert("end", message + "\n"); self.log.see("end")
        except Exception:
            pass
        try:
            if self.browser is not None:
                self.browser.log(message)
        except Exception:
            pass

    def _refresh_file_list(self):
        try:
            self.files_text.configure(state="normal"); self.files_text.delete("1.0", "end")
            if not self.paths:
                self.files_text.insert("end", "No CV/LSV/IV files selected. Select a Plot/view or choose files manually.\n")
            else:
                for path in self.paths: self.files_text.insert("end", str(path) + "\n")
            self.files_text.configure(state="disabled")
        except Exception:
            pass

    def _choose_files(self):
        files = filedialog.askopenfilenames(parent=self, title="Choose CV/LSV/IV/Tafel files", filetypes=[("Electrochemical tables", "*.csv *.txt *.tsv *.dat *.asc *.mpt *.DTA"), ("All files", "*.*")])
        if files:
            self.paths = [Path(x) for x in files]; self._refresh_file_list()

    def _use_selected(self):
        selector = self._core.get("_v130_selected_iv_lsv_paths")
        self.paths = list(selector(self.browser, generous=True)) if callable(selector) else []
        self._refresh_file_list(); self._log(f"Loaded {len(self.paths)} selected Plot/view file(s).")

    def _clear_results(self):
        for iid in self.results.get_children(): self.results.delete(iid)
        self._result_by_iid.clear()
        self._draw_empty_plot()

    @staticmethod
    def _fmt(value, digits=4):
        try:
            if value is None or value == "": return ""
            return f"{float(value):.{digits}g}"
        except Exception:
            return str(value)

    def _populate(self, payload: Dict[str, Any]):
        self._clear_results()
        self._result_payload = payload
        first = None
        for result_index, result in enumerate(payload.get("results", [])):
            if first is None: first = result
            source = Path(result.get("source_file", "")).name
            spatial = result.get("spatial") if isinstance(result.get("spatial"), dict) else {}
            def _um(key: str) -> str:
                try:
                    v = float(spatial.get(key)); return f"{v * 1e6:.6g}" if math.isfinite(v) else ""
                except Exception:
                    return ""
            rows = result.get("comparison_rows", [])
            for row_index, row in enumerate(rows):
                lo = row.get("slope_ci_low_mV_dec"); hi = row.get("slope_ci_high_mV_dec")
                ci = f"{self._fmt(lo)} to {self._fmt(hi)}" if lo is not None and hi is not None else ""
                iid = self.results.insert("", "end", values=(
                    spatial.get("point_index", ""), _um("x_position_m"), _um("y_position_m"), _um("scan_distance_m"),
                    row.get("quality_grade", ""), f"{source}: {row.get('method','')}", row.get("branch", ""),
                    self._fmt(row.get("tafel_slope_mV_dec")), ci, self._fmt(row.get("log10_j0_A_cm2")),
                    self._fmt(row.get("alpha")), self._fmt(row.get("sigma")), self._fmt(row.get("r2")),
                    row.get("n_points", ""), self._fmt(row.get("eta_window_mV")),
                ))
                self._result_by_iid[iid] = result
        if first:
            self._plot_result_data(first)
            self.notebook.select(1)

    def _plot_result_data(self, result: Dict[str, Any]):
        if self._figure is None:
            return
        try:
            import numpy as np
            import pandas as pd
            import faradaiq_tafel_adapter as tafel
            df = pd.read_csv(result["normalized_csv"])
            comp = pd.read_csv(result["comparison_csv"]) if Path(result.get("comparison_csv", "")).exists() else pd.DataFrame()
            stability = pd.read_csv(result["stability_csv"]) if Path(result.get("stability_csv", "")).exists() else pd.DataFrame()
            summary = json.loads(Path(result["summary_json"]).read_text(encoding="utf-8"))
            self._figure.clear()
            gs = self._figure.add_gridspec(2, 2, height_ratios=(1.0, 0.72), hspace=0.34, wspace=0.28)
            ax_pol = self._figure.add_subplot(gs[0, 0])
            ax_taf = self._figure.add_subplot(gs[0, 1])
            ax_slope = self._figure.add_subplot(gs[1, :])

            ax_pol.plot(df["eta_V"], df["j_A_cm2"], linewidth=1.2)
            ax_pol.scatter(df["eta_V"], df["j_A_cm2"], s=9, alpha=0.55)
            ax_pol.axvline(0, linewidth=0.8)
            ax_pol.axhline(0, linewidth=0.8)
            ax_pol.set_yscale("symlog", linthresh=max(1e-12, float(np.nanmax(np.abs(df["j_A_cm2"]))) * 1e-4))
            ax_pol.set_xlabel("Overpotential η / V"); ax_pol.set_ylabel("Current density j / A cm⁻²")
            ax_pol.set_title("Polarization curve")

            ax_taf.scatter(df["eta_V"], df["log10_abs_j_A_cm2"], s=12, alpha=0.7, label="Measured")
            for _, row in comp.iterrows():
                slope = row.get("slope_dec_per_V"); intercept = row.get("log10_j0_A_cm2")
                lo = row.get("eta_min_V"); hi = row.get("eta_max_V")
                if all(pd.notna(x) for x in (slope, intercept, lo, hi)):
                    xx = np.linspace(float(lo), float(hi), 80)
                    ax_taf.plot(xx, float(slope) * xx + float(intercept), linewidth=1.4, label=f"{row.get('method','fit')} {row.get('branch','')}")
            bv = summary.get("main_bv_fit") or {}
            if bv.get("log10_j0") is not None and bv.get("alpha") is not None:
                xx = np.linspace(float(df["eta_V"].min()), float(df["eta_V"].max()), 500)
                pred = np.abs(tafel._bv_current_density(xx, float(bv["log10_j0"]), float(bv["alpha"]), int(summary.get("settings", {}).get("n_electrons", 1)), float(summary.get("settings", {}).get("temperature_K", 298.15))))
                ax_taf.plot(xx, np.log10(np.clip(pred, 1e-30, None)), linewidth=2.0, label="Butler–Volmer")
            ax_taf.axvline(0, linewidth=0.8)
            ax_taf.set_xlabel("Overpotential η / V"); ax_taf.set_ylabel("log₁₀(|j| / A cm⁻²)")
            ax_taf.set_title("Tafel fits")
            handles, labels = ax_taf.get_legend_handles_labels()
            if handles: ax_taf.legend(fontsize=7, loc="best")

            if not stability.empty:
                for branch, group in stability.groupby("branch"):
                    ax_slope.plot(group["eta_V"], group["local_tafel_slope_mV_dec"], marker=".", linewidth=1.1, label=str(branch))
                ax_slope.axhline(0, linewidth=0.7)
                ax_slope.set_ylabel("Local Tafel slope / mV dec⁻¹")
                ax_slope.set_xlabel("Overpotential η / V")
                ax_slope.set_title("Slope-stability diagnostic — a plateau is more credible than one isolated linear window")
                ax_slope.legend(fontsize=8)
            else:
                ax_slope.text(0.5, 0.5, "No local-slope diagnostic was generated.", ha="center", va="center", transform=ax_slope.transAxes)
            self._figure.suptitle(Path(result.get("source_file", "Tafel result")).name, fontsize=11)
            self._style_matplotlib(); self._canvas.draw_idle()
            self._plot_result = result
        except Exception as exc:
            self._log(f"Could not build interactive result plot: {exc}")

    def _on_result_selected(self, _event=None):
        selected = self.results.selection()
        if selected:
            result = self._result_by_iid.get(selected[0])
            if result and result is not self._plot_result:
                self._plot_result_data(result)

    def _v294_spatial_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Return per-source physical Takedata coordinates for the worker job."""
        out: Dict[str, Dict[str, Any]] = {}
        browser = self.browser
        records = list(getattr(browser, "_catalog_records", []) or []) if browser is not None else []
        try:
            from .spatial_points_v297 import enrich_catalog_records
            enrich_catalog_records(records, search_roots=[getattr(browser, "analyzed_root", ""), getattr(browser, "cache_root", ""), getattr(browser, "bundle_root", "")])
        except Exception:
            pass
        by_path: Dict[str, Dict[str, Any]] = {}
        for rec in records:
            try:
                key = os.path.normcase(str(Path(str(rec.get("path") or "")).expanduser().resolve()))
            except Exception:
                continue
            by_path[key] = rec
        for path in self.paths:
            try:
                key = os.path.normcase(str(path.expanduser().resolve()))
            except Exception:
                key = os.path.normcase(str(path))
            rec = by_path.get(key)
            if not rec:
                continue
            item = {
                "point_index": rec.get("point"),
                "x_position_m": rec.get("x_position_m"),
                "y_position_m": rec.get("y_position_m"),
                "z_position_m": rec.get("z_position_m"),
                "scan_distance_m": rec.get("scan_distance_m"),
                "coordinate_source": rec.get("spatial_coordinate_source") or rec.get("coordinate_source") or "",
                "coordinate_status": rec.get("spatial_coordinate_status") or rec.get("coordinate_status") or "",
            }
            out[key] = item
        return out

    def _run_selected(self):
        if self._process is not None and self._process.poll() is None:
            self.status_var.set("Tafel analysis is already running.")
            return
        if not self.paths:
            messagebox.showinfo("No CV/LSV/IV files", "Select CV, LSV, IV, or polarization files first.", parent=self)
            return
        try:
            settings = self._settings()
        except Exception as exc:
            messagebox.showerror("Invalid Tafel settings", str(exc), parent=self)
            return
        self._clear_results()
        jobs = self.output_root / ".tafel_jobs"
        jobs.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time_ns() % 1000000):06d}"
        self._job_file = jobs / f"tafel_job_{stamp}.json"
        self._result_json = jobs / f"tafel_job_{stamp}_results.json"
        request = {"schema": "faradaiq-tafel-job-v237", "spatial_extension": "v297", "paths": [str(x) for x in self.paths], "output_root": str(self.output_root), "settings": asdict(settings), "result_json": str(self._result_json), "spatial_metadata": self._v294_spatial_metadata()}
        self._job_file.write_text(json.dumps(request, indent=2, default=str), encoding="utf-8")
        cmd = [sys.executable, "-m", "faradaiq.tafel_worker_v237", "--job", str(self._job_file)]
        kwargs = dict(cwd=str(Path(__file__).resolve().parents[1]), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        if os.name == "nt": kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(cmd, **kwargs)
        self._started = time.monotonic()
        self.progress_var.set(0.0); self.status_var.set(f"Starting {settings.optimizer_mode} Tafel analysis for {len(self.paths)} file(s)…")
        self.run_button.configure(state="disabled"); self.cancel_button.configure(state="normal")
        self._log(f"Started non-blocking Tafel worker PID {self._process.pid}. Main FaradaIQ windows remain responsive.")
        self._reader = threading.Thread(target=self._read_worker, daemon=True); self._reader.start()
        self._poll_worker()

    def _read_worker(self):
        process = self._process
        if process is None or process.stdout is None: return
        for line in process.stdout:
            text = line.strip()
            if not text: continue
            try: self._events.put(json.loads(text))
            except Exception: self._events.put({"event": "log", "message": text})

    def _poll_worker(self):
        while True:
            try: event = self._events.get_nowait()
            except queue.Empty: break
            self._handle_event(event)
        if self._process is not None and self._process.poll() is None:
            elapsed = time.monotonic() - self._started
            self.elapsed_var.set(f"elapsed {int(elapsed//60):02d}:{int(elapsed%60):02d}")
            self._poll_id = self.after(120, self._poll_worker)
            return
        if self._process is not None:
            code = self._process.poll()
            self._process = None
            self.run_button.configure(state="normal"); self.cancel_button.configure(state="disabled")
            if code not in (0, None) and not self._result_payload:
                self.status_var.set(f"Tafel worker stopped with exit code {code}. See Diagnostics and log.")
            self._poll_id = None

    def _handle_event(self, event: Dict[str, Any]):
        kind = event.get("event")
        if kind == "started":
            self.status_var.set(f"Running Tafel analysis — {event.get('total_files', 0)} file(s), {event.get('optimizer_mode', '')} mode")
        elif kind == "file_started":
            self.progress_var.set(float(event.get("progress", 0.0)))
            self.status_var.set(f"Analyzing {event.get('index')}/{event.get('total')}: {_display_source_identity(event.get('source',''))}")
            self._log(self.status_var.get())
        elif kind == "file_completed":
            self.progress_var.set(float(event.get("progress", 0.0)))
            self.status_var.set(f"Completed {event.get('index')}/{event.get('total')}: {_display_source_identity(event.get('source',''))} ({event.get('elapsed_s',0):.1f} s)")
            self._log(self.status_var.get())
        elif kind == "file_failed":
            self.progress_var.set(float(event.get("progress", 0.0)))
            self.status_var.set(
                f"Skipped {event.get('index')}/{event.get('total')}: "
                f"{_display_source_identity(event.get('source',''))} — {event.get('message','analysis error')}"
            )
            self._log(self.status_var.get())
            if event.get("traceback"):
                self._log(event["traceback"])
        elif kind == "completed":
            self.progress_var.set(100.0)
            result_json = Path(event.get("result_json", ""))
            try:
                payload = json.loads(result_json.read_text(encoding="utf-8"))
                self._result_payload = payload
                self._populate(payload)
                results = payload.get("results", [])
                if results:
                    self.last_output_dir = results[-1].get("output_dir")
                failed_count = int(payload.get("failed_file_count", 0) or 0)
                if failed_count:
                    self.status_var.set(
                        f"Completed {len(results)} Tafel result(s) with {failed_count} source file(s) skipped "
                        f"in {float(event.get('elapsed_s',0)):.1f} s. See Diagnostics for details."
                    )
                else:
                    self.status_var.set(f"Completed {len(results)} Tafel result(s) in {float(event.get('elapsed_s',0)):.1f} s. Results and plots are ready.")
                self._log(self.status_var.get())
            except Exception as exc:
                self.status_var.set(f"Analysis finished, but result loading failed: {exc}")
                self._log(self.status_var.get())
        elif kind == "failed":
            self.status_var.set("Tafel analysis failed: " + str(event.get("message", "unknown error")))
            self._log(self.status_var.get())
            if event.get("traceback"): self._log(event["traceback"])
            self.notebook.select(2)
        elif kind == "cancelled":
            self.status_var.set("Tafel analysis cancelled. Existing completed outputs were preserved.")
            self._log(self.status_var.get())
        else:
            self._log(str(event.get("message") or event))

    def _cancel(self):
        process = self._process
        if process is None or process.poll() is not None:
            return
        self.status_var.set("Stopping Tafel analysis…")
        self._log("Cancellation requested. Completed files and saved outputs will be preserved.")
        try:
            if os.name == "nt":
                subprocess.Popen(["taskkill", "/PID", str(process.pid), "/T", "/F"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                process.terminate()
        except Exception:
            try: process.kill()
            except Exception: pass

    def _dummy_test(self):
        if self._process is not None and self._process.poll() is None: return
        try:
            import faradaiq_tafel_adapter as tafel
            dummy = tafel.generate_dummy_dataset(self.output_root)
            self.paths = [Path(dummy)]; self.eeq_var.set("0.0"); self.adjust_var.set(False)
            self._refresh_file_list(); self._run_selected()
        except Exception as exc:
            messagebox.showerror("Dummy Tafel test failed", str(exc), parent=self)

    def _open_last(self):
        path = Path(self.last_output_dir or self.output_root)
        opener = self._core.get("_v130_open_path")
        if callable(opener): opener(path)
        elif os.name == "nt": os.startfile(str(path))

    def _close(self):
        if self._process is not None and self._process.poll() is None:
            if not messagebox.askyesno("Tafel analysis is running", "Stop the Tafel worker and close this window? Completed outputs will be preserved.", parent=self):
                return
            self._cancel()
        try:
            if self._poll_id is not None: self.after_cancel(self._poll_id)
        except Exception: pass
        self.destroy()


def install(core: Dict[str, Any]) -> None:
    """Install the v237 window and redirect every Tafel action to it."""
    def factory(browser=None, paths=None):
        return FaradaIQTafelAnalysisWindowV234(browser, paths=paths, core=core)

    def open_window(browser=None):
        try:
            selector = core.get("_v130_selected_iv_lsv_paths")
            paths = selector(browser, generous=False) if callable(selector) else []
            return factory(browser, paths)
        except Exception as exc:
            messagebox.showerror("FaradaIQ Tafel Analysis failed to open", str(exc), parent=browser)
            return None

    def quick(browser=None):
        selector = core.get("_v130_selected_iv_lsv_paths")
        paths = selector(browser, generous=True) if callable(selector) else []
        win = factory(browser, paths)
        if paths: win.after(180, win._run_selected)
        return win

    core["FaradaIQTafelAnalysisWindow"] = FaradaIQTafelAnalysisWindowV234
    core["_v130_open_tafel_window"] = open_window
    core["_v130_run_tafel_selected_quick"] = quick
    core["_v237_open_tafel_window"] = open_window
    core["_v237_tafel_worker_module"] = "faradaiq.tafel_worker_v237"

    help_tabs = core.get("_V207_HELP_TABS")
    title = "v237 responsive Tafel Analysis"
    if isinstance(help_tabs, list) and not any(str(x[0]) == title for x in help_tabs if isinstance(x, (list, tuple)) and x):
        help_tabs.append((title, r"""
RESPONSIVE TAFEL ANALYSIS

FaradaIQ runs file parsing, automatic window searches, bootstrap resampling, eta-window scans, and nonlinear Butler-Volmer fitting in a separate worker process. The Tafel window, browser, jobs window, and main GUI therefore remain responsive. Cancel analysis terminates only the Tafel worker and preserves outputs already written.

The interactive result page uses visible text buttons—Home, Back, Forward, Pan, Box zoom, Fit view, and Save figure—instead of low-contrast native Matplotlib toolbar icons. It displays the polarization curve, fitted Tafel regions, and a local-slope-stability plot.

The stable-window fit scans overlapping potential windows, rewards high R2 and adequate span, penalizes slope variation across neighboring windows, and reports bootstrap 95% intervals. The local Tafel-slope plot is a diagnostic: a stable plateau is more credible than one isolated straight segment. Optional post-hoc iR correction uses E_corrected = E_measured - I*Ru.

Fast mode uses multistart least-squares and fewer eta-window scans. Balanced is the default. Thorough mode retains global differential-evolution initialization and expanded uncertainty scans.

FaradaIQ keeps the native Python workflow rather than requiring another GUI. The algorithmic design draws on the LBNL/NIST Tafel-Fitter window-scan approach and the published Taffit Butler-Volmer concept. Scientific suitability still requires user review of mass transport, uncompensated resistance, steady state, and reaction assumptions.
"""))
