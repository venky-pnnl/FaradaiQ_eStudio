from __future__ import annotations

import threading
import time
from pathlib import Path

from faradaiq import v255_browser_color as fix


def test_color_tick_values_have_intermediate_labels():
    ticks = fix._color_tick_values(0.0, 5_000_000.0, 6)
    assert len(ticks) == 6
    assert ticks[0] == 0.0
    assert ticks[-1] == 5_000_000.0
    assert ticks[1:-1] == [1_000_000.0, 2_000_000.0, 3_000_000.0, 4_000_000.0]


def test_color_controls_use_exact_active_frame_range_and_six_ticks(tmp_path: Path):
    html = tmp_path / "plot.html"
    html.write_text("<html><body><div class='plotly-graph-div'></div></body></html>", encoding="utf-8")
    payload = {
        "initial_mode": "Auto per frame",
        "global_range": [0.0, 100.0],
        "initial_range": [0.0, 10.0],
        "manual_range": [2.0, 8.0],
        "frame_ranges": {"f0": [0.0, 10.0], "f1": [40.0, 60.0]},
    }
    fix._inject_color_controls(html, payload)
    text = html.read_text(encoding="utf-8")
    assert 'id="faradaiq-color-scale-toolbar-v255"' in text
    assert "activeFrameRange" in text
    assert "payload.frame_ranges" in text
    assert "zauto=false" in text
    assert "count=6" in text
    assert "colorbar.tickvals" in text
    assert "faradaiq_frame_changed" in text
    assert "Active frame range:" in text


def test_frame_dock_publishes_active_frame_after_redraw(tmp_path: Path):
    html = tmp_path / "plot.html"
    html.write_text("<html><body><div class='plotly-graph-div'></div></body></html>", encoding="utf-8")
    fix._inject_frame_controls(html, {"frame_names": ["f0", "f1"], "frame_labels": ["0 ms", "1 ms"], "frame_duration_ms": 100})
    text = html.read_text(encoding="utf-8")
    assert 'id="faradaiq-frame-dock-v255"' in text
    assert "faradaiq_frame_changed" in text
    assert "_faradaiqActiveFrameName" in text
    assert ".then(()=>ensureSpatialLayer(gd)).then(()=>announce(gd))" in text


class _Tree:
    def selection(self):
        return ("leaf",)
    def item(self, iid, option=None):
        return "Point 017" if option == "text" else {"text": "Point 017"}
    def get_children(self, *_):
        raise AssertionError("selection callback must not enumerate descendants")
    def unbind(self, *_):
        pass
    def bind(self, *_):
        pass


class _Text:
    def __init__(self):
        self.value = ""
    def configure(self, **_):
        pass
    def delete(self, *_):
        self.value = ""
    def insert(self, where, value):
        if where == "1.0":
            self.value = str(value)
        else:
            self.value += str(value)
    def get(self, *_):
        return self.value


class _Var:
    def set(self, value):
        self.value = value


class _Browser:
    def __init__(self, path: Path, reader):
        self.tree = _Tree()
        self._record_by_iid = {"leaf": {"path": str(path), "technique_key": "DRT", "technique_label": "DRT", "method_index": 1, "point": 17}}
        self._path_by_iid = {}
        self._node_meta = {"leaf": {"view_mode": "DRT"}}
        self._v209_core = {"_v138_csv_read": reader}
        self.qa_text = _Text()
        self.plot_mode_var = _Var(); self.plot_xscale_var = _Var(); self.plot_yscale_var = _Var(); self.cv_view_var = _Var()
        self.selected_df = None
        self.done = threading.Event()
    def after(self, delay, callback):
        def run():
            callback()
            if self.selected_df is not None:
                self.done.set()
        if delay <= 0:
            run()
        else:
            timer = threading.Timer(delay / 1000.0, run)
            timer.daemon = True
            timer.start()
        return "after"
    def log(self, *_):
        pass


def test_browser_selection_returns_before_slow_csv_preview(monkeypatch, tmp_path: Path):
    from faradaiq import browser_ui_v209 as bu
    path = tmp_path / "slow.csv"
    path.write_text("x,y\n1,2\n", encoding="utf-8")
    def reader(_path, max_rows=0):
        time.sleep(0.35)
        return {"rows": max_rows}
    browser = _Browser(path, reader)
    fix._CORE = {}
    fix._install_browser_selection()
    start = time.perf_counter()
    bu._on_tree_select(browser)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.12
    assert "loading in background" in browser.qa_text.value
    assert browser.done.wait(1.5)
    assert browser.selected_df == {"rows": 1200}
    assert "Preview: ready" in browser.qa_text.value


def test_release_identity_is_v290():
    import faradaiq
    assert faradaiq.APP_VERSION == "v316"
    assert faradaiq.__version__ == "0.316.0"
    root = Path(__file__).resolve().parents[1]
    assert (root / "Install_FaradaIQ_Studio_v320.bat").is_file()
    assert (root / "Start_FaradaIQ_Studio_v320.bat").is_file()
    assert (root / "faradaiq_studio_v316.py").is_file()
