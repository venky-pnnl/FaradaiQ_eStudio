from pathlib import Path
from types import SimpleNamespace
import json

import numpy as np
import pandas as pd
from PIL import Image


class _Tree:
    def selection(self):
        return ("selected",)


class _Browser:
    def __init__(self, root: Path, records):
        self.tree = _Tree()
        self._catalog_records = []
        self.h5_path = root / "synthetic_bundle.h5"
        self.h5_path.write_bytes(b"test")
        self.cache_root = root / ".cache"
        self.cache_root.mkdir()
        self.base_name = "synthetic_spatial_cv"
        self._records = records


def _spatial_cv_records(root: Path):
    records = []
    for cycle in (1, 2, 3):
        for point in range(1, 13):
            x_um = ((point - 1) % 4) * 2.0
            y_um = ((point - 1) // 4) * 2.0
            forward = np.linspace(-0.6, 0.8, 121)
            reverse = np.linspace(0.8, -0.6, 121)[1:]
            potential = np.concatenate([forward, reverse])
            current = (
                -15e-9
                + (point * 0.7e-9)
                + cycle * 0.25e-9
                + 18e-9 * potential
                + np.where(np.arange(len(potential)) < len(forward), 2e-9, -3e-9)
            )
            path = root / f"cv_p{point:03d}_c{cycle:02d}.csv"
            pd.DataFrame({"potential_V": potential, "current_A": current}).to_csv(path, index=False)
            records.append({
                "path": str(path),
                "technique_key": "CV",
                "point": point,
                "method_index": 1,
                "method_label": "scan rate 50 mV/s",
                "cycle": cycle,
                "x_position": x_um * 1e-6,
                "y_position": y_um * 1e-6,
            })
    return records


def test_cv_hyperspectral_preserves_forward_reverse_cycles_units_contours_and_background(tmp_path, monkeypatch):
    from faradaiq import multidim_v216 as m

    records = _spatial_cv_records(tmp_path)
    browser = _Browser(tmp_path, records)
    monkeypatch.setattr(m, "_selected_records", lambda _browser: records)

    background = tmp_path / "background.png"
    Image.new("RGB", (120, 80), (220, 225, 230)).save(background)

    result = m.make_hyperspectral(browser, options={
        "spatial_unit": "µm",
        "signal_unit": "nA",
        "spectral_unit": "mV",
        "render_mode": "Filled contour",
        "interpolation": "Linear",
        "contour_levels": 18,
        "overlay_points": True,
        "background_enabled": True,
        "background_path": str(background),
        "background_fit": "Fit to data extent",
        "background_opacity": 0.30,
    })

    assert result["engine"] == "Plotly CV branch-aware multi-cycle hyperspectral"
    assert result["cycle_count"] == 3
    assert Path(result["html"]).exists()
    assert Path(result["cube_index"]).exists()

    for cycle_view in result["cycle_views"]:
        assert set(cycle_view["branches"]) == {"forward", "reverse"}
        assert cycle_view["total_slider_frames"] == 128
        assert cycle_view["spatial_unit"] == "µm"
        assert cycle_view["signal_unit"] == "nA"
        assert cycle_view["spectral_unit"] == "mV"
        assert cycle_view["background_loaded"] is True
        assert any("Filled contour" in mode for mode in cycle_view["effective_render_modes"])
        html = Path(cycle_view["html"]).read_text(encoding="utf-8")
        assert "Forward scan" in html
        assert "Reverse / return scan" in html or r"Reverse \u002f return scan" in html
        assert "FWD" in html and "REV" in html
        cube = np.load(cycle_view["cube"])
        assert cube["forward_potential_V"].shape == (64,)
        assert cube["reverse_potential_V"].shape == (64,)
        assert cube["forward_values_SI"].shape == (12, 64)
        assert cube["reverse_values_SI"].shape == (12, 64)


def test_background_is_off_by_default_and_scientific_spatial_units_are_available(tmp_path, monkeypatch):
    from faradaiq import multidim_v216 as m

    records = _spatial_cv_records(tmp_path)[:12]
    browser = _Browser(tmp_path, records)
    monkeypatch.setattr(m, "_selected_records", lambda _browser: records)

    result = m.make_hyperspectral(browser, options={
        "spatial_unit": "Scientific",
        "signal_unit": "Scientific",
        "render_mode": "Measured points",
    })
    assert result["background_loaded"] is False
    assert result["spatial_unit"] == "m"
    assert "Measured points" in result["effective_render_modes"]
