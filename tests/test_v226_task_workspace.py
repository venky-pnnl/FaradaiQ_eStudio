from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v226_task_oriented_tabs_preserve_all_workflows():
    text = (ROOT / "faradaiq" / "ui_tabs_v226.py").read_text(encoding="utf-8")
    for label in ("Quick Plot", "Spatial / Cube", "Views", "Analyze", "Activity"):
        assert label in text
    for pop_label in ("Data", "Visualize", "Saved Views"):
        assert pop_label in text
    for function in ("DearEIS", "pyDRTtools", "PySimpleEChem", "Tafel analysis", "Smart Compare"):
        assert function in text
    for group in ("Data & display", "Surface", "Color & contours", "SECCM", "Background"):
        assert group in text


def test_v226_popout_is_four_primary_tabs_with_synchronized_subtabs():
    text = (ROOT / "faradaiq" / "ui_tabs_v226.py").read_text(encoding="utf-8")
    assert 'top.add(data_tab, text="Data")' in text
    assert 'top.add(visualize_tab, text="Visualize")' in text
    assert 'top.add(analyze_tab, text="Analyze")' in text
    assert 'top.add(activity_tab, text="Activity")' in text
    assert 'visual_tabs.add(quick, text="Quick Plot")' in text
    assert 'visual_tabs.add(spatial, text="Spatial / Cube")' in text
    assert 'visual_tabs.add(views, text="Saved Views")' in text
