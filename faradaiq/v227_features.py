"""FaradaIQ Studio v273 plot-aspect, contour-cache, and theme integration.

This patch is intentionally isolated from the proven legacy core.  It adds:
- responsive Plotly aspect presets for spatial/cube figures;
- persistent aspect controls in the browser;
- a source/options keyed contour-view cache;
- non-blocking prebuild of spatial contour/cube views after catalog refresh;
- theme propagation helpers for external analysis applications.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

VERSION = "v273"
_CACHE_SCHEMA = "faradaiq-contour-view-cache-v227"
_CACHE_LOCK = threading.RLock()
_PREBUILD_LOCK = threading.Lock()


def _theme_name(widget: Any = None) -> str:
    """Return a stable user-facing FaradaIQ theme name for child processes."""
    candidates = []
    if widget is not None:
        candidates.append(widget)
        try:
            candidates.append(widget.winfo_toplevel())
        except Exception:
            pass
        candidates.append(getattr(widget, "browser", None))
    for item in candidates:
        if item is None:
            continue
        try:
            var = getattr(item, "theme_mode_var", None)
            if var is not None:
                value = str(var.get()).strip()
                if value:
                    return value
        except Exception:
            pass
        try:
            value = str(getattr(item, "_faradaiq_theme_mode", "")).strip()
            if value:
                return value.title()
        except Exception:
            pass
    return os.environ.get("FARADAIQ_THEME", "High Contrast") or "High Contrast"


def theme_environment(widget: Any = None) -> Dict[str, str]:
    env = os.environ.copy()
    name = _theme_name(widget)
    env["FARADAIQ_THEME"] = name
    env["FARADAIQ_THEME_NORMALIZED"] = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    env["FARADAIQ_HIGH_CONTRAST"] = "1" if "contrast" in name.lower() else "0"
    return env


def qt_stylesheet(theme: str = "High Contrast") -> str:
    """Application-wide Qt style sheet used by bundled/external Qt tools."""
    t = str(theme or "High Contrast").lower()
    if "light" in t:
        bg, panel, field, fg, muted, border, accent, selected = (
            "#f4f7fb", "#ffffff", "#ffffff", "#102033", "#526274", "#9aa9ba", "#1769aa", "#2f6fad"
        )
    elif "warm" in t:
        bg, panel, field, fg, muted, border, accent, selected = (
            "#211d18", "#2b251e", "#17130f", "#fff7e8", "#d7c7aa", "#75634e", "#f0b429", "#805d17"
        )
    elif "slate" in t:
        bg, panel, field, fg, muted, border, accent, selected = (
            "#111827", "#1f2937", "#0b1220", "#f3f4f6", "#cbd5e1", "#475569", "#38bdf8", "#075985"
        )
    elif "midnight" in t:
        bg, panel, field, fg, muted, border, accent, selected = (
            "#050816", "#0b1024", "#020617", "#eef2ff", "#b6c2e2", "#26345e", "#7dd3fc", "#164e63"
        )
    elif "contrast" in t:
        bg, panel, field, fg, muted, border, accent, selected = (
            "#000000", "#050505", "#000000", "#ffffff", "#f5f5f5", "#ffffff", "#fff200", "#0047ff"
        )
    else:
        bg, panel, field, fg, muted, border, accent, selected = (
            "#0f1724", "#162235", "#0b1220", "#e6edf7", "#aebdd0", "#52647e", "#5ea7e0", "#2d5f8b"
        )
    return f"""
QWidget {{ background-color: {bg}; color: {fg}; }}
QMainWindow, QDialog, QDockWidget {{ background-color: {bg}; }}
QFrame, QGroupBox, QTabWidget::pane {{ background-color: {panel}; border-color: {border}; }}
QGroupBox {{ margin-top: 0.75em; padding-top: 0.6em; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
QLabel {{ color: {fg}; background: transparent; }}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox,
QTableView, QTreeView, QListView {{ background-color: {field}; color: {fg}; border: 1px solid {border}; selection-background-color: {selected}; selection-color: #ffffff; }}
QPushButton, QToolButton {{ background-color: {panel}; color: {fg}; border: 1px solid {border}; padding: 5px 9px; }}
QPushButton:hover, QToolButton:hover {{ border: 2px solid {accent}; }}
QPushButton:pressed, QToolButton:pressed {{ background-color: {selected}; }}
QTabBar::tab {{ background: {panel}; color: {fg}; border: 1px solid {border}; padding: 6px 10px; }}
QTabBar::tab:selected {{ border-bottom: 3px solid {accent}; }}
QHeaderView::section {{ background-color: {panel}; color: {fg}; border: 1px solid {border}; padding: 4px; }}
QMenu, QMenuBar {{ background-color: {panel}; color: {fg}; }}
QMenu::item:selected {{ background-color: {selected}; }}
QStatusBar {{ background-color: {panel}; color: {muted}; border-top: 1px solid {border}; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: {field}; }}
QScrollBar::handle {{ background: {border}; min-width: 18px; min-height: 18px; }}
QProgressBar {{ border: 1px solid {border}; background: {field}; color: {fg}; text-align: center; }}
QProgressBar::chunk {{ background-color: {accent}; }}
"""


def apply_qt_theme(app: Any, theme: Optional[str] = None) -> str:
    name = str(theme or os.environ.get("FARADAIQ_THEME") or "High Contrast")
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    try:
        app.setStyleSheet(qt_stylesheet(name))
    except Exception:
        pass
    return name


def _cache_manifest_path(md: Any, browser: Any) -> Path:
    root = Path(md._view_dir(browser))
    root.mkdir(parents=True, exist_ok=True)
    return root / ".faradaiq_contour_cache_v227.json"


def _source_stamp(path: Path) -> Dict[str, Any]:
    try:
        st = path.stat()
        return {"path": str(path.resolve()), "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}
    except Exception:
        return {"path": str(path), "size": -1, "mtime_ns": -1}


def _cache_key(curves: Sequence[Any], group_label: str, suffix: str, options: Mapping[str, Any]) -> str:
    payload = {
        "schema": _CACHE_SCHEMA,
        "group": str(group_label),
        "options": dict(options),
        "sources": [_source_stamp(Path(c.path)) for c in curves],
        "axes": [[str(c.x_name), str(c.y_name), int(c.point), int(c.method), int(c.cycle)] for c in curves],
    }
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_manifest(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schema") == _CACHE_SCHEMA:
            data.setdefault("entries", {})
            return data
    except Exception:
        pass
    return {"schema": _CACHE_SCHEMA, "version": VERSION, "updated": "", "entries": {}}


def _result_exists(result: Mapping[str, Any]) -> bool:
    html = result.get("html")
    if not html or not Path(str(html)).exists():
        return False
    for key in ("cube", "metadata", "cube_index"):
        value = result.get(key)
        if value and not Path(str(value)).exists():
            return False
    return True


def _save_manifest(path: Path, data: Mapping[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _add_aspect_menu(fig: Any, preset: str = "Auto fit") -> None:
    """Add visible Plotly relayout buttons without changing measured coordinates."""
    try:
        current = list(fig.layout.updatemenus or [])
    except Exception:
        current = []
    # Avoid duplicate menus when a cached figure is regenerated through wrappers.
    try:
        for menu in current:
            if getattr(menu, "name", None) == "faradaiq-aspect-v227":
                return
    except Exception:
        pass
    buttons = [
        dict(label="Auto", method="relayout", args=[{"autosize": True, "width": None, "height": None, "xaxis.scaleanchor": None, "xaxis.autorange": True, "yaxis.autorange": True}]),
        dict(label="Equal XY", method="relayout", args=[{"autosize": True, "width": None, "height": None, "xaxis.scaleanchor": "y", "xaxis.scaleratio": 1, "xaxis.autorange": True, "yaxis.autorange": True}]),
        dict(label="Square", method="relayout", args=[{"autosize": False, "width": 900, "height": 900, "xaxis.scaleanchor": None, "xaxis.autorange": True, "yaxis.autorange": True}]),
        dict(label="Rectangle 4:3", method="relayout", args=[{"autosize": False, "width": 1120, "height": 840, "xaxis.scaleanchor": None, "xaxis.autorange": True, "yaxis.autorange": True}]),
        dict(label="Wide 16:9", method="relayout", args=[{"autosize": False, "width": 1280, "height": 720, "xaxis.scaleanchor": None, "xaxis.autorange": True, "yaxis.autorange": True}]),
    ]
    menu = dict(
        type="buttons", direction="right", x=0.01, xanchor="left", y=1.105, yanchor="top",
        showactive=True, pad={"r": 8, "t": 0}, buttons=buttons, name="faradaiq-aspect-v227",
    )
    current.append(menu)
    fig.update_layout(updatemenus=current)
    p = str(preset or "Auto fit").lower()
    if "equal" in p:
        fig.update_layout(autosize=True, width=None, height=None)
        fig.update_xaxes(scaleanchor="y", scaleratio=1)
    elif "square" in p:
        fig.update_layout(autosize=False, width=900, height=900)
        fig.update_xaxes(scaleanchor=None)
    elif "16:9" in p or "wide" in p:
        fig.update_layout(autosize=False, width=1280, height=720)
        fig.update_xaxes(scaleanchor=None)
    elif "4:3" in p or "rectangle" in p:
        fig.update_layout(autosize=False, width=1120, height=840)
        fig.update_xaxes(scaleanchor=None)
    else:
        fig.update_layout(autosize=True, width=None, height=None)
        fig.update_xaxes(scaleanchor=None)


def _load_curves_from_records(md: Any, browser: Any, records: Sequence[Mapping[str, Any]], max_curves: int = 10000) -> List[Any]:
    pos = md._position_lookup(browser)
    curves: List[Any] = []
    for i, rec0 in enumerate(list(records)[:max_curves], 1):
        rec = dict(rec0)
        p = Path(str(rec.get("path") or ""))
        if not p.exists() or p.suffix.lower() not in md._TABLE_EXTS:
            continue
        try:
            df = md._read_table(p, rec)
            roles = md._column_roles(df)
            xn, yn = md._preferred_axes(rec, roles)
            if not xn or not yn:
                numeric_cols = [c for c in df.columns if md.pd.to_numeric(df[c], errors="coerce").notna().sum() >= 2]
                if len(numeric_cols) < 2:
                    continue
                xn, yn = numeric_cols[:2]
            x, y = md._finite_xy(md._numeric(df, xn), md._numeric(df, yn))
            if len(x) < 2:
                continue
            pt = md._safe_int(rec.get("point"), i)
            xc = md._v216_record_coord_m(rec, "x")
            yc = md._v216_record_coord_m(rec, "y")
            zc = md._v216_record_coord_m(rec, "z")
            if pt in pos:
                px, py, pz = pos[pt]
                xc = xc if xc is not None else px
                yc = yc if yc is not None else py
                zc = zc if zc is not None else pz
            curves.append(md.Curve(
                path=p, record=rec, x_name=str(xn), y_name=str(yn), x=x, y=y,
                point=pt, method=md._safe_int(md._coalesce(rec.get("method_index"), rec.get("method")), 1),
                cycle=md._safe_int(md._coalesce(rec.get("cycle"), rec.get("sweep")), 0),
                line=md._safe_int(rec.get("line"), 0), x_coord=xc, y_coord=yc, z_coord=zc,
            ))
        except Exception:
            continue
    return curves


def _notify(browser: Any, text: str) -> None:
    def apply() -> None:
        try:
            browser.log(text)
        except Exception:
            pass
        try:
            if hasattr(browser, "set_status"):
                browser.set_status(text)
            elif hasattr(browser, "status_var"):
                browser.status_var.set(text)
        except Exception:
            pass
    try:
        browser.after(0, apply)
    except Exception:
        apply()


def _prebuild_worker(md: Any, browser: Any) -> None:
    if not _PREBUILD_LOCK.acquire(blocking=False):
        return
    try:
        records = list(getattr(browser, "_catalog_records", []) or [])
        if not records:
            return
        _notify(browser, "Browser catalog ready. Prebuilding spatial contour/cube cache…")
        curves = _load_curves_from_records(md, browser, records)
        groups = []
        for label, items in md._curve_groups(curves, min_size=3):
            finite = sum(1 for c in items if c.x_coord is not None and c.y_coord is not None)
            if finite >= 3:
                groups.append((label, items))
        if not groups:
            _notify(browser, "Contour cache: no spatial groups with at least three measured locations were found.")
            return
        options = md._v216_options(md._v222_load_persisted_options() if hasattr(md, "_v222_load_persisted_options") else {})
        built = reused = failed = 0
        total = len(groups)
        for i, (label, items) in enumerate(groups, 1):
            _notify(browser, f"Prebuilding contour/cube cache {i}/{total}: {label}")
            try:
                result = md._make_hyperspectral_group(browser, items, label, f"catalog_g{i:03d}", options)
                if result.get("cache_hit"):
                    reused += 1
                else:
                    built += 1
            except Exception as exc:
                failed += 1
                try:
                    browser.after(0, lambda e=exc, l=label: browser.log(f"Contour prebuild skipped {l}: {e}"))
                except Exception:
                    pass
        _notify(browser, f"Contour/cube cache ready: {built} built, {reused} reused, {failed} skipped. Interactive views now open from cache when settings and sources match.")
    finally:
        _PREBUILD_LOCK.release()


def schedule_contour_prebuild(browser: Any) -> None:
    if getattr(browser, "_v227_contour_prebuild_running", False):
        return
    browser._v227_contour_prebuild_running = True
    def worker() -> None:
        try:
            import faradaiq.multidim_v216 as md
            _prebuild_worker(md, browser)
        finally:
            browser._v227_contour_prebuild_running = False
    threading.Thread(target=worker, daemon=True, name="FaradaIQ-v227-contour-prebuild").start()


def _find_viz_bar(widget: Any) -> Any:
    try:
        for child in widget.winfo_children():
            try:
                text = str(child.cget("text"))
            except Exception:
                text = ""
            if "hyperspectral visualization" in text.lower() or "1d / 2d / 3d" in text.lower():
                return child
            found = _find_viz_bar(child)
            if found is not None:
                return found
    except Exception:
        pass
    return None


def install(core: Dict[str, Any]) -> None:
    import faradaiq.multidim_v216 as md

    # Extend option normalization while retaining every previous option.
    if not getattr(md, "_v227_options_installed", False):
        md._V216_DEFAULT_OPTIONS.setdefault("aspect_preset", "Auto fit")
        original_options = md._v216_options
        def options(raw: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
            out = original_options(raw)
            value = str(out.get("aspect_preset") or "Auto fit")
            allowed = {"Auto fit", "Equal XY units", "Square plot", "Rectangle 4:3", "Wide 16:9"}
            out["aspect_preset"] = value if value in allowed else "Auto fit"
            # Backward-compatible checkbox maps to equal-unit scaling.
            if bool(out.get("lock_xy_aspect")) and out["aspect_preset"] == "Auto fit":
                out["aspect_preset"] = "Equal XY units"
            return out
        md._v216_options = options
        md._v227_options_installed = True

    # Add aspect buttons to every 2-D spatial Plotly figure before serialization.
    if not getattr(md, "_v227_write_installed", False):
        original_write = md._v216_write_figure
        def write_figure(fig: Any, out: Path, meta: Mapping[str, Any]) -> Any:
            try:
                schema = str(meta.get("schema") or "").lower()
                if bool(meta.get("coordinates_available")) and "hyperspectral" in schema:
                    opts = md._v216_options(meta.get("options") or {})
                    _add_aspect_menu(fig, opts.get("aspect_preset", "Auto fit"))
            except Exception:
                pass
            return original_write(fig, out, meta)
        md._v216_write_figure = write_figure
        md._v227_write_installed = True

    # Cache complete generated contour/cube views by source stamps + visualization options.
    if not getattr(md, "_v227_cache_installed", False):
        original_make = md._make_hyperspectral_group
        def cached_make(browser: Any, curves: Sequence[Any], group_label: str, suffix: str, options: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
            opts = md._v216_options(options)
            path = _cache_manifest_path(md, browser)
            key = _cache_key(curves, group_label, suffix, opts)
            with _CACHE_LOCK:
                manifest = _load_manifest(path)
                entry = dict((manifest.get("entries") or {}).get(key) or {})
                result = entry.get("result") if isinstance(entry, dict) else None
                if isinstance(result, dict) and _result_exists(result):
                    hit = dict(result)
                    hit["cache_hit"] = True
                    hit["cache_key"] = key
                    return hit
            result = dict(original_make(browser, curves, group_label, suffix, opts))
            result["cache_hit"] = False
            result["cache_key"] = key
            with _CACHE_LOCK:
                manifest = _load_manifest(path)
                entries = manifest.setdefault("entries", {})
                entries[key] = {
                    "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "group_label": group_label,
                    "source_count": len(curves),
                    "result": result,
                }
                # Bound stale historical option combinations without affecting current sources.
                if len(entries) > 500:
                    keys = list(entries.keys())
                    for old in keys[:-500]:
                        entries.pop(old, None)
                manifest["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                _save_manifest(path, manifest)
            return result
        md._make_hyperspectral_group = cached_make
        md._v227_cache_installed = True

    Browser = core.get("FaradaIQH5Browser")
    ttk = core.get("ttk")
    tk = core.get("tk")
    if Browser is not None and ttk is not None and tk is not None and not getattr(Browser, "_v227_browser_installed", False):
        previous_build = Browser._build_visible_ui_first
        def build(self: Any) -> None:
            previous_build(self)
            try:
                persisted = md._v216_options(getattr(self, "_v222_visualization_options", None) or md._v222_load_persisted_options())
                self._v227_aspect_var = tk.StringVar(value=persisted.get("aspect_preset", "Auto fit"))
                bar = getattr(self, "_v216_multidim_bar", None) or _find_viz_bar(self)
                if bar is not None:
                    row = ttk.Frame(bar)
                    row.pack(fill="x", pady=(4, 0))
                    ttk.Label(row, text="Plot shape").pack(side="left", padx=(0, 6))
                    for label, value in (
                        ("Auto", "Auto fit"), ("Equal XY", "Equal XY units"), ("Square", "Square plot"),
                        ("Rectangle 4:3", "Rectangle 4:3"), ("Wide 16:9", "Wide 16:9"),
                    ):
                        ttk.Radiobutton(row, text=label, value=value, variable=self._v227_aspect_var).pack(side="left", padx=2)
                    ttk.Label(row, text="The same buttons are available inside each Plotly contour window.").pack(side="left", padx=(10, 0))
                previous_collect = getattr(self, "_v216_multidim_options", None)
                if callable(previous_collect):
                    def collect() -> Dict[str, Any]:
                        opts = dict(previous_collect())
                        opts["aspect_preset"] = self._v227_aspect_var.get()
                        opts = md._v216_options(opts)
                        self._v222_visualization_options = dict(opts)
                        md._v222_save_persisted_options(opts)
                        return opts
                    self._v216_multidim_options = collect
            except Exception as exc:
                try:
                    self.log(f"Plot-shape controls warning: {exc}")
                except Exception:
                    pass
        Browser._build_visible_ui_first = build

        previous_refresh = getattr(Browser, "refresh_tree", None)
        if callable(previous_refresh):
            def refresh(self: Any, *args: Any, **kwargs: Any) -> Any:
                result = previous_refresh(self, *args, **kwargs)
                try:
                    self.after(150, lambda b=self: schedule_contour_prebuild(b))
                except Exception:
                    schedule_contour_prebuild(self)
                return result
            Browser.refresh_tree = refresh
        Browser._v227_browser_installed = True

    # CV/LSV/IV selection routing for Tafel. Earlier QA deliberately excluded
    # canonical CV cache files; v227 restores them with explicit branch control.
    def selected_tafel_paths(browser: Any = None, generous: bool = False) -> List[Path]:
        try:
            getter = core.get("_v130p_get_selected_view_paths")
            paths = list(getter(browser) if callable(getter) else browser.selected_paths(plottable_only=True))
        except Exception:
            paths = []
        out: List[Path] = []
        seen = set()
        try:
            kind = str(core.get("_v83_current_plot_view_kind")(browser)).lower() if callable(core.get("_v83_current_plot_view_kind")) else ""
        except Exception:
            kind = ""
        kind_ok = any(token in kind for token in ("cv", "lsv", "iv", "tafel", "polar"))
        for raw in paths:
            try:
                path = Path(raw)
                text = path.as_posix().lower()
                name = path.name.lower()
                if any(token in text for token in ("eis", "impedance", "nyquist", "bode", "zreal", "zimag", "drt", "ocp")):
                    continue
                keep = generous or kind_ok or any(token in text for token in ("/cv_lsv/", "cv_point_", "cyclic", "voltam", "linear_sweep", "lsv", "tafel", "polar", "current-voltage", "current_voltage"))
                if not keep and path.exists():
                    try:
                        head = path.read_text(encoding="utf-8", errors="ignore")[:4096].lower()
                        keep = (any(t in head for t in ("potential", "voltage", "ewe", "eta")) and any(t in head for t in ("current", "i_a", "j_", "j/")))
                    except Exception:
                        pass
                if not keep:
                    continue
                key = str(path.resolve()).lower() if path.exists() else str(path).lower()
                if key not in seen:
                    seen.add(key)
                    out.append(path)
            except Exception:
                continue
        return out
    core["_v130_selected_iv_lsv_paths"] = selected_tafel_paths
    core["_v138_selected_iv_lsv_paths"] = selected_tafel_paths

    # Full theme for FaradaIQ-owned Tafel window.
    Tafel = core.get("FaradaIQTafelAnalysisWindow")
    if Tafel is not None and not getattr(Tafel, "_v227_theme_installed", False):
        original_init = Tafel.__init__
        def tafel_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)
            try:
                self.title(f"FaradaIQ Tafel Analysis {VERSION}")
                parent = getattr(self, "browser", None)
                mode = _theme_name(parent)
                core["_v36_apply_theme"](self, mode)
                self.after(100, lambda: core["_v36_apply_theme"](self, mode))
            except Exception:
                pass
        Tafel.__init__ = tafel_init
        Tafel._v227_theme_installed = True

    # Publish helpers to the legacy core for child-process launchers.
    core["_v227_theme_environment"] = theme_environment
    core["_v227_theme_name"] = _theme_name
    core["_v227_apply_qt_theme"] = apply_qt_theme
    core["_v227_schedule_contour_prebuild"] = schedule_contour_prebuild

    help_tabs = core.get("_V207_HELP_TABS")
    title = "v228 plot shape, contour cache, themed analysis, and CV Tafel branches"
    if isinstance(help_tabs, list) and not any(str(x[0]) == title for x in help_tabs if isinstance(x, (list, tuple)) and x):
        help_tabs.append((title, r"""
PLOT SHAPE

Spatial and hyperspectral Plotly windows provide Auto, Equal XY, Square, Rectangle 4:3, and Wide 16:9 controls. These adjust only plot layout/aspect scaling. Measured X/Y coordinates and signal values are not modified. Equal XY preserves equal physical units; the other presets optimize the display panel shape.

CONTOUR PREBUILD CACHE

After the browser catalog is ready, FaradaIQ prebuilds every compatible spatial contour/cube group on a background worker. The cache key includes source path, size, modification time, axes, point/method/cycle identity, and every visualization option. Opening a matching view reuses the prebuilt HTML/cube immediately. Changed data or changed contour settings automatically create a new cache entry.

THEMED ANALYSIS TOOLS

The FaradaIQ theme is passed to PySimpleEChem and pyDRTtools and applied through an application-wide Qt style. Tafel analysis uses the active Tk theme. DearEIS receives the FaradaIQ theme preference at launch and retains its own plotting/theme behavior where its installed version does not expose a compatible external theme hook.

CV TAFEL BRANCHES

Tafel analysis accepts CV as well as IV, LSV, and polarization files. Choose whole trace, forward scan, reverse scan, or forward and reverse separately. Branch splitting follows acquisition order and the potential turning point; each selected branch receives an independent Eeq, current-density normalization, fits, figure, comparison table, and summary.
"""))
