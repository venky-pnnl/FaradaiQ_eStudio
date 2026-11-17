from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from faradaiq import tafel_trace_v291 as trace


def test_tafel_trace_converts_units_and_log_abs_current():
    df = pd.DataFrame({
        "Potential/mV": [-800.0, -700.0, -600.0, -500.0],
        "Current/uA": [-1.0, -10.0, 100.0, 1000.0],
    })
    out, meta = trace.prepare_tafel_trace(df)
    np.testing.assert_allclose(out["potential_V"], [-0.8, -0.7, -0.6, -0.5])
    np.testing.assert_allclose(out["current_A"], [-1e-6, -1e-5, 1e-4, 1e-3])
    np.testing.assert_allclose(out["log10_abs_current_A"], [-6, -5, -4, -3])
    assert list(out["current_sign"]) == ["cathodic", "cathodic", "anodic", "anodic"]
    assert meta["transformation"] == "log10(abs(current_A))"


def test_tafel_trace_preserves_acquisition_order_and_omits_only_zero_current():
    df = pd.DataFrame({
        "potential_V": [-0.8, -0.6, -0.4, -0.6, -0.8],
        "current_A": [-1e-8, -1e-6, 0.0, 2e-6, 2e-8],
    })
    out, meta = trace.prepare_tafel_trace(df)
    assert list(out["acquisition_index"]) == [0, 1, 3, 4]
    assert list(out["potential_V"]) == [-0.8, -0.6, -0.6, -0.8]
    assert meta["zero_current_samples_omitted"] == 1
    assert meta["preserves_acquisition_order"] is True


def test_tafel_trace_does_not_apply_eeq_area_ir_or_fit():
    df = pd.DataFrame({"Ewe/V": [-0.5, -0.4, -0.3], "I/A": [1e-7, 1e-6, 1e-5]})
    out, _ = trace.prepare_tafel_trace(df)
    np.testing.assert_allclose(out["potential_V"], [-0.5, -0.4, -0.3])
    np.testing.assert_allclose(out["log10_abs_current_A"], [-7, -6, -5])
    assert "eta_V" not in out.columns
    assert "j_A_cm2" not in out.columns


def test_v294_ui_keeps_tafel_trace_as_plot_mode_not_analyze_button():
    root = Path(__file__).resolve().parents[1]
    v217 = (root / "faradaiq" / "ui_tabs_v217.py").read_text(encoding="utf-8")
    v226 = (root / "faradaiq" / "ui_tabs_v226.py").read_text(encoding="utf-8")
    assert "Tafel trace log|I| vs V" in v217
    assert "Tafel trace: log|I| vs V" not in v226
    assert "Tafel analysis" in v226


def test_core_installs_tafel_trace_before_v291_release_identity():
    root = Path(__file__).resolve().parents[1]
    core = (root / "faradaiq_studio_core.py").read_text(encoding="utf-8")
    assert "from faradaiq.tafel_trace_v291 import install" in core
    assert core.index("from faradaiq.tafel_trace_v291 import install") < core.index("# ---- FaradaIQ Studio v293 Tafel reliability + Analyze cleanup ----")


class _Var:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class _Panel:
    def __init__(self, frames):
        self.frames = frames
        self.rendered = None
    def _load_curve(self, path):
        return {"path": Path(path), "df": self.frames[Path(path).name], "label": Path(path).stem}
    def render(self, curves, clear=True):
        self.rendered = (curves, clear)


class _Browser:
    def __init__(self, panel):
        self._v206_pub_panel = panel
        self.plot_mode_var = _Var("Auto")
        self.max_curves_var = _Var("24")
        self.messages = []
    def log(self, message): self.messages.append(message)


def test_selected_tafel_trace_uses_explicit_iv_selector_and_sets_mode(tmp_path):
    iv = tmp_path / "CV_point_001.csv"
    ocp = tmp_path / "OCP_point_001.csv"
    iv.write_text("potential_V,current_A\n-0.6,-1e-7\n-0.5,-1e-6\n-0.4,1e-5\n", encoding="utf-8")
    ocp.write_text("time_s,potential_V\n0,-0.5\n1,-0.49\n", encoding="utf-8")
    panel = _Panel({
        iv.name: pd.read_csv(iv),
        ocp.name: pd.read_csv(ocp),
    })
    browser = _Browser(panel)
    core = {"_v130_selected_iv_lsv_paths": lambda _browser, generous=True: [iv]}
    accepted = trace.show_selected_tafel_trace(browser, core)
    assert accepted == [iv]
    assert browser.plot_mode_var.get() == trace.MODE_LABEL
    assert panel.rendered is not None
    curves, clear = panel.rendered
    assert clear is True and [Path(c["path"]).name for c in curves] == [iv.name]
