from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import pandas as pd
from matplotlib.figure import Figure

import faradaiq_studio_core as core
from faradaiq import browser_ui_v209
from faradaiq import tafel_trace_v292 as trace


class _Var:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class _Browser:
    def __init__(self, mode="IV / Tafel", yscale="Log"):
        self.plot_mode_var = _Var(mode)
        self.plot_xscale_var = _Var("Linear")
        self.plot_yscale_var = _Var(yscale)
        self.messages = []
    def log(self, message): self.messages.append(str(message))


class _Canvas:
    def draw_idle(self): pass


class _Panel:
    def __init__(self, mode="IV / Tafel", yscale="Log"):
        self.browser = _Browser(mode, yscale)
        self.canvas = _Canvas()
        self.figure = None
        self.ax = None
        self.last_curves = []
        self.last_recipe = {}
        self._v209_colorbar_mappable = None
        self._v209_colorbar_label = ""
        self._v209_colorbar_added = False
        self._v209_warning_keys = set()
    def _get(self, name, default=None):
        var = getattr(self.browser, name, None)
        if hasattr(var, "get"):
            return var.get()
        defaults = {
            "legend_var": "On",
            "eis_cmap_var": "Blues",
            "eis_plot_style_var": "Line + Points",
            "cv_view_var": "I-V",
            "plot_grid_var": True,
            "plot_xmin_var": "",
            "plot_xmax_var": "",
            "plot_ymin_var": "",
            "plot_ymax_var": "",
        }
        return defaults.get(name, default)
    def _new_figure(self):
        self.figure = Figure()
        self.ax = self.figure.add_subplot(111)
    def _plot_eis(self, *args, **kwargs): return None, False
    def _apply_options(self, has_cbar):
        self.ax.set_xscale(str(self.browser.plot_xscale_var.get()).lower())
        self.ax.set_yscale(str(self.browser.plot_yscale_var.get()).lower())


def _curve(tmp_path: Path):
    path = tmp_path / "CV_point_001.csv"
    df = pd.DataFrame({
        "Potential/V": [-0.80, -0.70, -0.60, -0.50],
        "Current/A": [-1e-9, -1e-8, 1e-7, 1e-6],
    })
    df.to_csv(path, index=False)
    return {"df": df, "path": path, "label": "P001"}


def test_legacy_iv_tafel_is_real_log_abs_current_trace(tmp_path):
    panel = _Panel("IV / Tafel", "Log")
    core.FaradaIQPublicationPlotPanelV206.render(panel, [_curve(tmp_path)], clear=True)
    assert panel.ax.lines[0].get_ydata().tolist() == [-9.0, -8.0, -7.0, -6.0]
    assert panel.browser.plot_yscale_var.get() == "Linear"
    assert panel.ax.get_yscale() == "linear"
    assert panel.ax.get_ylabel() == "log₁₀(|I| / A)"
    assert panel.ax.get_title().startswith("Tafel trace:")


def test_dedicated_tafel_mode_uses_active_browser_renderer(tmp_path):
    panel = _Panel(trace.MODE_LABEL, "Log")
    core.FaradaIQPublicationPlotPanelV206.render(panel, [_curve(tmp_path)], clear=True)
    assert panel.ax.lines[0].get_ydata().tolist() == [-9.0, -8.0, -7.0, -6.0]
    assert getattr(browser_ui_v209, "_v292_tafel_trace_patch", False) is True


def test_cv_i_v_remains_signed_raw_current(tmp_path):
    panel = _Panel("CV I-V", "Linear")
    core.FaradaIQPublicationPlotPanelV206.render(panel, [_curve(tmp_path)], clear=True)
    assert panel.ax.lines[0].get_ydata().tolist() == [-1e-9, -1e-8, 1e-7, 1e-6]


def test_ui_has_single_clear_tafel_mode_and_auto_apply_hook():
    root = Path(__file__).resolve().parents[1]
    text = (root / "faradaiq" / "ui_tabs_v217.py").read_text(encoding="utf-8")
    assert '"Tafel trace log|I| vs V"' in text
    assert '"IV / Tafel", "Tafel trace' not in text
    assert '<<ComboboxSelected>>' in text
    assert '_v292_apply_tafel_mode' in text
