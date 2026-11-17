from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure


def _write_eis(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["frequency_Hz", "Zreal_ohm", "Zimag_ohm", "neg_Zimag_ohm", "Zmod_ohm", "phase_deg"])
        w.writeheader()
        for fr, zr, zi in [(24000, 10, -2), (1000, 14, -7), (100, 23, -12), (1, 45, -5)]:
            w.writerow({"frequency_Hz": fr, "Zreal_ohm": zr, "Zimag_ohm": zi, "neg_Zimag_ohm": -zi, "Zmod_ohm": (zr*zr+zi*zi)**0.5, "phase_deg": __import__('math').degrees(__import__('math').atan2(zi,zr))})


class Var:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class Panel:
    def __init__(self, path: Path):
        self.browser = SimpleNamespace(
            _catalog_by_path={str(path.resolve()).lower(): {"technique_key":"EIS", "method_index":1, "method_label":"Method 01 — EIS frequency program", "point":1, "plot_mode":"EIS Nyquist"}},
            plot_mode_var=Var("EIS Nyquist"), plot_xscale_var=Var("Linear"), plot_yscale_var=Var("Linear"), plot_grid_var=Var(True),
            plot_xmin_var=Var(""), plot_xmax_var=Var(""), plot_ymin_var=Var(""), plot_ymax_var=Var(""),
            legend_var=Var("On"), eis_cmap_var=Var("Blues"), eis_plot_style_var=Var("Line + Points"), cv_view_var=Var("I-V"),
            log=lambda *_a, **_k: None,
        )
        self.figure = Figure(); self.ax = self.figure.add_subplot(111); self.canvas = SimpleNamespace(draw_idle=lambda: None)
        self.last_recipe = {}; self.last_curves=[]
    def _get(self, name, default=None):
        v=getattr(self.browser,name,None); return v.get() if hasattr(v,'get') else default
    def _new_figure(self):
        self.figure.clear(); self.ax=self.figure.add_subplot(111)
    def _style_flags(self): return True, True
    def _apply_options(self, _has_cbar=False):
        self.ax.set_xscale('log' if str(self.browser.plot_xscale_var.get()).lower().startswith('log') else 'linear')
        self.ax.set_yscale('log' if str(self.browser.plot_yscale_var.get()).lower().startswith('log') else 'linear')
        self.ax.grid(bool(self.browser.plot_grid_var.get()))


def test_eis_all_browser_plot_modes_and_defaults(tmp_path: Path) -> None:
    from faradaiq.browser_ui_v209 import _load_curve, _render
    path=tmp_path/'EIS_point_001_method_01_Nyquist_Bode.csv'; _write_eis(path)
    panel=Panel(path)
    curve=_load_curve(panel,path)
    expected={
        'EIS Nyquist': ('Z′ / Ω','−Z″ / Ω','linear','EIS Nyquist overlay'),
        'EIS Nyquist — frequency gradient': ('Z′ / Ω','−Z″ / Ω','linear','EIS Nyquist frequency-gradient overlay'),
        'EIS Bode magnitude': ('Frequency / Hz','|Z| / Ω','log','EIS Bode magnitude overlay'),
        'EIS Bode phase': ('Frequency / Hz','Phase / degree','log','EIS Bode phase overlay'),
        'EIS Nyquist — stored Zimag': ('Z′ / Ω','Stored Zimag / Ω','linear','EIS stored-Zimag sign diagnostic'),
    }
    for mode,(xl,yl,xscale,title) in expected.items():
        panel.browser.plot_mode_var.set(mode)
        assert _render(panel,[curve]) is True
        assert panel.ax.get_xlabel()==xl
        assert panel.ax.get_ylabel()==yl
        assert panel.ax.get_xscale()==xscale
        assert panel.ax.get_title()==title
        assert len(panel.last_curves)==1
        assert len(panel.ax.lines) >= (0 if 'gradient' in mode else 1)


def test_eis_user_linear_override_is_preserved_in_same_bode_mode(tmp_path: Path) -> None:
    from faradaiq.browser_ui_v209 import _load_curve, _render
    path=tmp_path/'eis.csv'; _write_eis(path)
    panel=Panel(path); curve=_load_curve(panel,path)
    panel.browser.plot_mode_var.set('EIS Bode magnitude')
    _render(panel,[curve]); assert panel.ax.get_xscale()=='log'
    panel.browser.plot_xscale_var.set('Linear')
    _render(panel,[curve]); assert panel.ax.get_xscale()=='linear'
