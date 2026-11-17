from pathlib import Path

import numpy as np

from faradaiq import multidim_v216 as m


def _sample():
    x = np.array([0, 2.5, 5, 0, 2.5, 5, 0, 2.5, 5], dtype=float)
    y = np.array([0, 0, 0, 2.5, 2.5, 2.5, 5, 5, 5], dtype=float)
    v = np.array([1, 2, 3, 2, 5, 4, 1.5, 3.5, 4.5], dtype=float)
    return x, y, v


def test_seccm_spacing_and_physical_footprint():
    opts = m._v216_options({
        "surface_model": "SECCM tip footprint",
        "tip_diameter_um": 5,
        "overlap_percent": 50,
        "spatial_unit": "µm",
        "grid_quality": "Auto",
        "clip_overshoot": True,
    })
    assert m._v222_nominal_spacing(opts) == 2.5
    x, y, v = _sample()
    gx, gy, z, regular, descriptor = m._v222_surface_grid(x, y, v, opts)
    assert not regular
    assert z.shape == (len(gy), len(gx))
    assert np.isfinite(z).any()
    assert np.nanmin(z) >= np.nanmin(v) - 1e-12
    assert np.nanmax(z) <= np.nanmax(v) + 1e-12
    assert "5 µm tip" in descriptor
    assert "50% overlap" in descriptor
    assert "2.5 µm nominal spacing" in descriptor


def test_origin_style_surface_is_smooth_and_bounded():
    x, y, v = _sample()
    opts = m._v216_options({
        "surface_model": "Origin-style smooth",
        "smoothing": "Medium",
        "grid_quality": "Auto",
        "clip_overshoot": True,
    })
    gx, gy, z, regular, descriptor = m._v222_surface_grid(x, y, v, opts)
    assert not regular
    assert z.shape == (len(gy), len(gx))
    assert np.isfinite(z).sum() > 100
    assert np.nanmin(z) >= np.nanmin(v) - 1e-12
    assert np.nanmax(z) <= np.nanmax(v) + 1e-12
    assert "Origin-style" in descriptor


def test_color_range_scope_and_nice_contours():
    values = np.array([-100, 0, 1, 2, 3, 100], dtype=float)
    lo, hi = m._v222_color_range(values, {"range_mode": "Robust (2–98%)"})
    assert lo > -100 and hi < 100
    lo2, hi2 = m._v222_color_range(values, {"range_mode": "Manual min/max", "range_min": -2, "range_max": 4})
    assert (lo2, hi2) == (-2.0, 4.0)
    spec = m._v222_nice_contour_spec(-0.73, -0.24, 12)
    assert spec["size"] > 0
    assert spec["start"] < spec["end"]
    assert m._v216_options({"range_scope": "Per frame"})["range_scope"] == "Per frame"


def test_visualization_defaults_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("FARADAIQ_RUNTIME_SETTINGS_DIR", str(tmp_path))
    m._V222_LAST_OPTIONS = dict(m._V216_DEFAULT_OPTIONS)
    saved = m._v216_options({
        "surface_model": "SECCM tip footprint",
        "tip_diameter_um": 7.5,
        "overlap_percent": 40,
        "palette": "Cividis",
        "range_scope": "Lock across cube",
    })
    m._v222_save_persisted_options(saved)
    m._V222_LAST_OPTIONS = dict(m._V216_DEFAULT_OPTIONS)
    restored = m._v222_load_persisted_options()
    assert restored["surface_model"] == "SECCM tip footprint"
    assert restored["tip_diameter_um"] == 7.5
    assert restored["overlap_percent"] == 40.0
    assert restored["palette"] == "Cividis"
    assert (tmp_path / "visualization_defaults_v226.json").exists()


def test_visualization_options_are_included_in_session_payloads_and_restored(tmp_path, monkeypatch):
    monkeypatch.setenv("FARADAIQ_RUNTIME_SETTINGS_DIR", str(tmp_path))

    class Browser:
        pass

    applied = {}

    def base_collect(browser):
        return {"base": True}

    def base_apply(browser, payload, silent=False):
        applied["base"] = (payload, silent)
        return "applied"

    def base_v138(browser, include_bundle=True):
        return {"include_bundle": include_bundle}

    core = {
        "_v75_collect_session": base_collect,
        "_v75_apply_session_payload": base_apply,
        "_v138_session_payload": base_v138,
    }
    m._v222_install_session_hooks(core, Browser)

    browser = Browser()
    browser._v216_multidim_options = lambda: {
        "surface_model": "SECCM tip footprint",
        "tip_diameter_um": 8.0,
        "overlap_percent": 25.0,
        "palette": "Turbo",
        "range_scope": "Lock across cube",
    }

    saved = core["_v75_collect_session"](browser)
    assert saved["visualization_options"]["tip_diameter_um"] == 8.0
    assert saved["visualization_options"]["overlap_percent"] == 25.0
    assert saved["visualization_options"]["palette"] == "Turbo"

    browser_payload = core["_v138_session_payload"](browser, include_bundle=False)
    assert browser_payload["visualization_options"]["surface_model"] == "SECCM tip footprint"

    restored = {
        "visualization_options": {
            "surface_model": "Origin-style smooth",
            "smoothing": "Strong",
            "palette": "Cividis",
            "range_scope": "Per frame",
        }
    }
    assert core["_v75_apply_session_payload"](browser, restored, silent=True) == "applied"
    assert applied["base"] == (restored, True)
    assert browser._v222_visualization_options["surface_model"] == "Origin-style smooth"
    assert browser._v222_visualization_options["smoothing"] == "Strong"
    assert browser._v222_visualization_options["range_scope"] == "Per frame"
