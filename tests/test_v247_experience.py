from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import faradaiq_studio_core as core
from faradaiq import multidim_v216 as md
import faradaiq.v239_scilink_edison as scilink
import faradaiq.v247_experience as v247


def _cv_curves(tmp_path: Path):
    curves = []
    potential = np.concatenate([
        np.linspace(0.55, -0.55, 35, endpoint=True),
        np.linspace(-0.55, 0.55, 35, endpoint=True)[1:],
    ])
    for index in range(8):
        x = (index % 4) * 1000e-6
        y = (index // 4) * 120e-6
        current = (index + 1) * 1e-9 + 4e-7 * np.tanh(6 * potential) + 1e-7 * np.sin(8 * potential + index / 3)
        curves.append(md.Curve(
            path=tmp_path / f"point_{index+1}.csv",
            record={"technique_key": "CV", "scan_rate_v_s": 0.1},
            x_name="potential_V",
            y_name="current_A",
            x=potential.copy(),
            y=current,
            point=index + 1,
            method=1,
            cycle=1,
            line=1,
            x_coord=x,
            y_coord=y,
            z_coord=None,
        ))
    return curves


def test_v247_default_ollama_model():
    assert core._FARADAIQ_DEFAULT_OLLAMA_MODEL == "gpt-oss:120b-cloud"
    settings = scilink._active_provider_settings()
    assert settings["provider"] == "Ollama"
    assert settings["model"] == "gpt-oss:120b-cloud"


def test_v247_contour_grid_is_animation_sized():
    x = np.array([0, 1, 2, 0, 1, 2, 0.5, 1.5], dtype=float)
    y = np.array([0, 0, 0, 1, 1, 1, 2, 2], dtype=float)
    values = np.sin(x) + np.cos(y)
    gx, gy, z, regular = md._v216_grid_values(x, y, values, "Linear")
    assert z.shape == (len(gy), len(gx))
    assert max(len(gx), len(gy)) <= 58
    assert np.isfinite(z).any()


def test_v247_cv_has_one_forward_to_reverse_slider_and_play(tmp_path: Path):
    browser = SimpleNamespace(cache_root=tmp_path / "cache", base_name="demo")
    browser.cache_root.mkdir()
    result = md._v216_cv_cycle_view(browser, _cv_curves(tmp_path), "CV Method 01", "demo", md._v216_options())
    meta = json.loads(Path(result["metadata"]).read_text(encoding="utf-8"))
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert meta["slider_layout"] == "continuous_forward_then_reverse"
    assert meta["play_pause"] is True
    assert meta["total_slider_frames"] > 50
    assert "▶ Play" in html
    assert "❚❚ Pause" in html
    assert "Forward scan potential" not in html
    assert "Reverse / return scan potential" not in html


def test_scilink_review_preserves_caller_prompt_and_has_file_filters():
    source = inspect.getsource(v247._enhanced_scilink_review)
    assert 'initial_prompt = "" if prompt_override is None else str(prompt_override)' in source
    for label in ("Select visible", "Clear visible", "Select all", "Clear all", "All roles", "All groups", "All types"):
        assert label in source
    assert "BooleanVar(value=False)" in source


def test_completed_job_load_restores_main_before_browser():
    source = inspect.getsource(v247._install_main_and_browser_load)
    assert "apply_session(self.parent, clean_payload, open_browsers=False)" in source
    assert "self.parent.file_var.set(job.source_path)" in source
    assert "restore_browser(self.parent, browser_payload)" in source
    assert source.index("open_browsers=False") < source.index("restore_browser(self.parent, browser_payload)")


def test_browser_selection_is_debounced_cache_first():
    source = inspect.getsource(v247._install_fast_browser_selection)
    assert "browser.after(65, commit)" in source
    assert "_v247_preview_cache" in source
    assert "daemon=True" in source
