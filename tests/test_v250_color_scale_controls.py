from pathlib import Path

import plotly.graph_objects as go

from faradaiq import v250_reliability as v250


def test_standalone_color_payload_covers_all_frames():
    fig = go.Figure(
        data=[go.Contour(z=[[0.0, 1.0], [2.0, 3.0]], colorscale="Viridis")],
        frames=[
            go.Frame(name="low", data=[go.Contour(z=[[-5.0, -2.0], [-1.0, 0.0]])]),
            go.Frame(name="high", data=[go.Contour(z=[[10.0, 20.0], [30.0, 40.0]])]),
        ],
    )
    payload = v250._figure_color_range_payload(
        fig,
        {"options": {"color_scale_mode": v250.COLOR_GLOBAL}},
    )
    assert payload["version"] == "v316"
    assert payload["global_range"] == [-5.0, 40.0]
    assert payload["frame_ranges"]["low"] == [-5.0, 0.0]
    assert payload["frame_ranges"]["high"] == [10.0, 40.0]


def test_standalone_html_gets_three_live_scale_modes(tmp_path: Path):
    fig = go.Figure(
        data=[go.Contour(z=[[1.0, 2.0], [3.0, 4.0]], colorscale="Viridis")],
        frames=[go.Frame(name="f1", data=[go.Contour(z=[[10.0, 11.0], [12.0, 13.0]])])],
    )
    out = tmp_path / "view.html"
    fig.write_html(out, include_plotlyjs=True, full_html=True, auto_open=False)
    payload = v250._figure_color_range_payload(
        fig,
        {"options": {"color_scale_mode": v250.COLOR_MANUAL, "range_min": "2", "range_max": "12"}},
    )
    v250._inject_standalone_color_controls(out, payload)
    v250._inject_standalone_color_controls(out, payload)
    text = out.read_text(encoding="utf-8")
    assert sum(text.count(f'id="faradaiq-color-scale-toolbar-{tag}"') for tag in ("v253", "v255", "v292", "v294")) == 1
    assert "Auto rescale each frame" in text
    assert "Fixed global min/max" in text
    assert "User-defined min/max" in text
    assert any(f"faradaiq-color-min-{tag}" in text for tag in ("v253", "v255", "v292", "v294"))
    assert any(f"faradaiq-color-max-{tag}" in text for tag in ("v253", "v255", "v292", "v294"))
    assert "plotly_sliderchange" in text
    assert "plotly_animated" in text
    assert '"manual_range":[2.0,12.0]' in text
