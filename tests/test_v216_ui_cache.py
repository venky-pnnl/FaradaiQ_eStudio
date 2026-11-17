from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_tabbed_workspace_and_branding_are_installed_v217():
    core = (ROOT / "faradaiq_studio_core.py").read_text(encoding="utf-8")
    tabs = (ROOT / "faradaiq" / "ui_tabs_v226.py").read_text(encoding="utf-8")
    assert "_v217_install_ui_tabs(globals())" in core
    assert "_v226_install_ui_tabs(globals())" in core
    assert 'notebook.add(quick, text="Quick Plot")' in tabs
    assert 'notebook.add(spatial, text="Spatial / Cube")' in tabs
    assert 'notebook.add(views, text="Views")' in tabs
    assert 'notebook.add(analyze, text="Analyze")' in tabs
    assert 'notebook.add(activity, text="Activity")' in tabs
    assert 'TAGLINE = "Electrochemical Intelligence for Analyze, Control and Discover"' in tabs
    assert 'TAGLINE = "Electrochemical Intelligence for Analyze, Control and Discover"' in tabs


def test_post_export_cache_control_is_default_enabled_and_installed():
    core = (ROOT / "faradaiq_studio_core.py").read_text(encoding="utf-8")
    module = (ROOT / "faradaiq" / "post_export_cache_v216.py").read_text(encoding="utf-8")
    assert "_v216_install_post_export_cache(globals())" in core
    assert 'CHECKBOX_TEXT = "After export + parser runs: automatically build browser cache"' in module
    assert "tk.BooleanVar(value=True)" in module
    assert "Browser cache ready to open:" in module
    assert "after_export_and_parser_runs" in module


def test_multidimensional_controls_are_rebuilt_inside_v217_tab():
    tabs = (ROOT / "faradaiq" / "ui_tabs_v226.py").read_text(encoding="utf-8")
    assert "_build_quick_plot(browser, quick" in tabs
    assert "_build_spatial_workspace(browser, spatial" in tabs
    assert "Data & display" in tabs
    assert "Color & contours" in tabs
    assert "SECCM" in tabs
