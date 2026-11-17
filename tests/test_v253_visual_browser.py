from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import plotly.graph_objects as go

from faradaiq import multidim_v216 as md
from faradaiq import v253_visual_browser as fix


def test_frame_trace_preserves_contour_and_point_style():
    contour = go.Contour(
        x=[0, 1], y=[0, 1], z=[[1, 2], [3, 4]],
        colorscale="Viridis", contours={"coloring": "heatmap", "showlabels": True},
        line={"width": 1.7}, colorbar={"title": "Signal"}, name="Filled contour",
        hovertemplate="z=%{z}", connectgaps=False,
    )
    copied = fix._styled_frame_trace(contour)
    assert copied.type == "contour"
    assert copied.contours.coloring == "heatmap"
    assert copied.contours.showlabels is True
    assert copied.line.width == 1.7
    assert copied.colorbar.title.text == "Signal"
    assert np.asarray(copied.z, dtype=float).shape == (2, 2)

    points = go.Scatter(x=[0, 1], y=[1, 2], mode="markers", marker={"symbol": "circle-open", "size": 9}, name="points")
    copied_points = fix._styled_frame_trace(points)
    assert copied_points.mode == "markers"
    assert copied_points.marker.symbol == "circle-open"
    assert copied_points.marker.size == 9


def test_fixed_frame_dock_is_always_visible_and_drives_redraw(tmp_path: Path):
    html = tmp_path / "view.html"
    html.write_text("<html><body><div class='plotly-graph-div'></div></body></html>", encoding="utf-8")
    payload = {
        "frame_names": ["band_000", "band_001"],
        "frame_labels": ["Time 1 ms", "Time 2 ms"],
        "frame_duration_ms": 200,
    }
    fix._inject_fixed_frame_controls(html, payload)
    body = html.read_text(encoding="utf-8")
    assert any(f'id="faradaiq-frame-dock-{tag}"' in body for tag in ("v253", "v255", "v262"))
    assert "position:fixed" in body
    assert "bottom:0" in body
    assert "Plotly.animate" in body
    assert "redraw:true" in body
    assert "ensureSpatialLayer" in body
    assert "Plotly.redraw" in body


def test_frame_payload_uses_slider_labels_and_hides_native_controls():
    fig = go.Figure(
        data=[go.Contour(z=[[1, 2], [3, 4]])],
        frames=[
            go.Frame(name="f0", data=[go.Contour(z=[[1, 2], [3, 4]])]),
            go.Frame(name="f1", data=[go.Contour(z=[[2, 3], [4, 5]])]),
        ],
    )
    fig.update_layout(
        sliders=[{"steps": [{"label": "1 ms", "method": "animate", "args": [["f0"]]}, {"label": "2 ms", "method": "animate", "args": [["f1"]]}]}],
        updatemenus=[{"buttons": [{"label": "▶ Play", "method": "animate", "args": [None]}]}, {"name": "aspect", "buttons": [{"label": "Auto", "method": "relayout", "args": [{}]}]}],
    )
    payload = fix._frame_control_payload(fig)
    assert payload["frame_names"] == ["f0", "f1"]
    assert payload["frame_labels"] == ["1 ms", "2 ms"]
    fix._hide_native_playback(fig)
    assert fig.layout.sliders[0].visible is False
    labels = [[b.label for b in menu.buttons] for menu in fig.layout.updatemenus]
    assert labels == [["Auto"]]


class FakeTree:
    def __init__(self):
        self.recursive_calls = 0
    def selection(self):
        return ("branch",)
    def item(self, iid, option=None):
        if option == "text":
            return "DRT"
        return {"text": "DRT"}
    def get_children(self, iid=None):
        self.recursive_calls += 1
        return tuple(f"child_{i}" for i in range(25000)) if iid == "branch" else ()
    def unbind(self, *_):
        pass
    def bind(self, *_):
        pass


class FakeText:
    def __init__(self):
        self.value = ""
    def configure(self, **_):
        pass
    def delete(self, *_):
        self.value = ""
    def insert(self, _where, value):
        self.value = value


class FakeVar:
    def __init__(self):
        self.value = None
    def set(self, value):
        self.value = value


class FakeBrowser:
    def __init__(self):
        self.tree = FakeTree()
        self._record_by_iid = {}
        self._path_by_iid = {}
        self._node_meta = {"branch": {"view_mode": "DRT"}}
        self.qa_text = FakeText()
        self.plot_mode_var = FakeVar()
        self.plot_xscale_var = FakeVar()
        self.plot_yscale_var = FakeVar()
        self.cv_view_var = FakeVar()
        self._v253_selection_after = None
    def after(self, _delay, callback):
        callback()
        return "after-id"
    def after_cancel(self, _token):
        pass
    def log(self, _message):
        pass


def test_browser_branch_selection_does_not_walk_all_descendants(monkeypatch):
    from faradaiq import browser_ui_v209 as bu
    monkeypatch.setattr(bu, "_tree_records", lambda *_: (_ for _ in ()).throw(AssertionError("recursive traversal must not run")))
    fix._CORE = {}
    fix._install_browser_tree_fix()
    browser = FakeBrowser()
    start = time.perf_counter()
    bu._on_tree_select(browser)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.2
    assert browser.tree.recursive_calls == 1
    assert "does not scan every descendant" in browser.qa_text.value
    assert browser.plot_mode_var.value == "DRT"


def test_release_identity_is_v290():
    import faradaiq
    assert faradaiq.APP_VERSION == "v316"
    assert faradaiq.__version__ == "0.316.0"
    root = Path(__file__).resolve().parents[1]
    assert (root / "Install_FaradaIQ_Studio_v320.bat").is_file()
    assert (root / "Start_FaradaIQ_Studio_v320.bat").is_file()
    assert (root / "faradaiq_studio_v316.py").is_file()
