from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd


def test_aspect_menu_has_all_presets():
    import plotly.graph_objects as go
    from faradaiq.v227_features import _add_aspect_menu
    fig = go.Figure(go.Scatter(x=[0, 1], y=[0, 2]))
    _add_aspect_menu(fig, "Rectangle 4:3")
    labels = [b.label for menu in fig.layout.updatemenus for b in menu.buttons]
    assert labels == ["Auto", "Equal XY", "Square", "Rectangle 4:3", "Wide 16:9"]
    assert fig.layout.width == 1120
    assert fig.layout.height == 840


def test_cache_key_reuses_same_group_across_generated_suffixes(tmp_path: Path):
    import faradaiq.multidim_v216 as md
    from faradaiq.v227_features import _cache_key
    p = tmp_path / "curve.csv"
    p.write_text("Potential_V,Current_A\n0,0\n1,1\n", encoding="utf-8")
    curve = md.Curve(path=p, record={"technique_key": "CV"}, x_name="Potential_V", y_name="Current_A",
                     x=np.array([0.0, 1.0]), y=np.array([0.0, 1.0]), point=1, method=1, cycle=1, line=0,
                     x_coord=0.0, y_coord=0.0)
    opts = md._v216_options({"aspect_preset": "Square plot"})
    assert _cache_key([curve], "CV — Method", "catalog_g001", opts) == _cache_key([curve], "CV — Method", "g01", opts)
    p.write_text("Potential_V,Current_A\n0,0\n1,2\n", encoding="utf-8")
    assert _cache_key([curve], "CV — Method", "catalog_g001", opts) != _cache_key([], "CV — Method", "g01", opts)


def test_cv_tafel_forward_reverse_separate(tmp_path: Path):
    import faradaiq_tafel_adapter as tafel
    e = np.r_[np.linspace(0.35, -0.35, 101), np.linspace(-0.35, 0.35, 101)[1:]]
    i = 2e-6 * np.sinh(9.0 * e)
    path = tmp_path / "cv_cycle.csv"
    pd.DataFrame({"Potential_V": e, "Current_A": i}).to_csv(path, index=False)
    settings = tafel.TafelSettings(scan_branch_mode="Forward + reverse separately")
    results = tafel.analyze_files([path], tmp_path, settings)
    assert len(results) == 2
    branches = {row["scan_branch"] for result in results for row in result.comparison_rows}
    assert branches == {"forward", "reverse"}
    assert all(Path(result.normalized_csv).exists() for result in results)


def test_qt_theme_styles_cover_high_contrast_fields():
    from faradaiq.v227_features import qt_stylesheet
    css = qt_stylesheet("High Contrast")
    assert "QTableView" in css
    assert "QProgressBar" in css
    assert "#fff200" in css
    assert "background-color: #000000" in css


def test_v227_options_persist_plot_shape():
    import faradaiq.multidim_v216 as md
    assert md._v216_options({})["aspect_preset"] == "Auto fit"
    assert md._v216_options({"aspect_preset": "Square plot"})["aspect_preset"] == "Square plot"

def test_selected_cv_cache_file_routes_to_tafel(tmp_path: Path, monkeypatch):
    import faradaiq_studio_core as core
    cv_dir = tmp_path / "CV_LSV" / "point_001"
    cv_dir.mkdir(parents=True)
    cv = cv_dir / "CV_point_001_cycle_01.csv"
    cv.write_text("potential_V,current_A\n0.2,1e-6\n-0.2,-1e-6\n", encoding="utf-8")
    monkeypatch.setattr(core, "_v130p_get_selected_view_paths", lambda _browser: [cv])
    paths = core._v130_selected_iv_lsv_paths(SimpleNamespace(), generous=False)
    assert paths == [cv]

def test_multicycle_cv_tafel_groups_forward_and_reverse_legs(tmp_path: Path):
    import faradaiq_tafel_adapter as tafel
    one = np.r_[np.linspace(0.3, -0.3, 61), np.linspace(-0.3, 0.3, 61)[1:]]
    e = np.r_[one, one[1:]]
    i = 1e-6 * np.sinh(8 * e)
    path = tmp_path / "cv_two_cycles.csv"
    pd.DataFrame({"Potential_V": e, "Current_A": i}).to_csv(path, index=False)
    df, _, _, _ = tafel.normalize_iv_lsv(path, tafel.TafelSettings())
    branches = tafel._v227_split_cv_acquisition(df)
    assert set(branches) == {"forward", "reverse"}
    assert branches["forward"][0]["cv_cycle_index"].nunique() == 2
    assert branches["reverse"][0]["cv_cycle_index"].nunique() == 2
