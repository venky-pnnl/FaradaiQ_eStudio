from __future__ import annotations

import json
import re
import subprocess
import zipfile
from pathlib import Path

from faradaiq.v290_portable_browser import HTML_NAME, MANIFEST_NAME, README_NAME, write_portable_browser


def _csv(root: Path, rel: str, columns: list[str], rows: list[tuple[object, ...]]) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(columns) + "\n" + "\n".join(",".join(map(str, row)) for row in rows), encoding="utf-8")
    return path


def _comprehensive_experiment(root: Path) -> None:
    n = 20
    _csv(root, "processed/CV/method_01/point_001/cv.csv", ["potential_V", "current_A"], [(-1+i*.1, i*1e-6) for i in range(n)])
    _csv(root, "processed/LSV/method_01/point_002/lsv.csv", ["potential_V", "current_A"], [(-1+i*.1, i*2e-6) for i in range(n)])
    _csv(root, "processed/DPV/method_01/point_003/dpv.csv", ["potential_V", "current_A"], [(-1+i*.1, i*3e-6) for i in range(n)])
    _csv(root, "processed/CA/method_01/point_001/ca.csv", ["time_s", "current_A", "charge_C"], [(i, i*1e-6, i*i*1e-6) for i in range(1, n+1)])
    _csv(root, "processed/CP/method_01/point_001/cp.csv", ["time_s", "potential_V"], [(i, .1*i) for i in range(n)])
    _csv(root, "processed/GCD/method_01/point_001/gcd.csv", ["time_s", "potential_V", "capacity_mAh"], [(i, .1*i, .01*i) for i in range(n)])
    _csv(root, "processed/EIS/method_01/point_001/eis.csv", ["frequency_Hz", "z_real_ohm", "z_imag_ohm"], [(10**(4-i/3), 100+i*5, -50+i) for i in range(n)])
    _csv(root, "analysis/DRT/point_001/drt.csv", ["tau_s", "gamma_ohm"], [(10**(-5+i/4), i*.3) for i in range(n)])
    _csv(root, "analysis/Tafel/point_001/tafel.csv", ["overpotential_V", "current_A"], [(i*.01, 10**(-6+i/10)) for i in range(n)])
    _csv(root, "analysis/EIS_fit_parameters.csv", ["point", "Rct_ohm", "Cdl_F", "chi2"], [(i, 100+i, 1e-6*i, .001*i) for i in range(1, 6)])
    _csv(root, "analysis/drt_peak_summary.csv", ["point", "peak_tau_s", "peak_gamma_ohm", "integrated_area"], [(i, 1e-3*i, 2*i, 4*i) for i in range(1, 6)])
    _csv(root, "analysis/ca_cp_analysis_results.csv", ["point", "duration_s", "charge_C", "transition_time_s", "drift_per_s"], [(i, 100, 1e-3*i, 5*i, .01*i) for i in range(1, 6)])
    _csv(root, "analysis/voltammetry_peak_analysis.csv", ["point", "scan_rate_mV_s", "peak_current_A", "peak_potential_V", "diffusion_coefficient"], [(i, 10*i, 1e-6*i, .2*i, 1e-5*i) for i in range(1, 6)])
    _csv(root, "analysis/spatial_map.csv", ["x_position_um", "y_position_um", "Rct_ohm", "point"], [(i % 3, i // 3, 100+i, i) for i in range(9)])
    _csv(root, "analysis/EQCM/eqcm.csv", ["time_s", "potential_V", "current_A", "mass_change_ng", "frequency_shift_Hz", "charge_C"], [(i, i*.05, i*1e-6, i*2, -i*4, i*1e-5) for i in range(n)])
    _csv(root, "analysis/GITT/gitt.csv", ["time_s", "potential_V", "current_A", "capacity_mAh"], [(i, .2+i*.01, 1e-3 if i % 2 else 0, i*.02) for i in range(n)])


def test_v290_indexes_raw_techniques_and_derived_analyses(tmp_path: Path) -> None:
    root = tmp_path / "comprehensive"
    root.mkdir()
    _comprehensive_experiment(root)
    receipt = write_portable_browser(root, analysis_root=root / "processed", create_batch_zips=False)
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    expected = {
        "CV", "LSV", "DPV", "CA", "CP", "GCD", "EIS", "DRT", "TAFEL", "EQCM", "GITT",
        "EIS_ANALYSIS", "DRT_ANALYSIS", "TRANSIENT_ANALYSIS", "VOLTAMMETRY_ANALYSIS", "SPATIAL_MAP",
    }
    assert expected <= set(manifest["technique_counts"])
    assert manifest["coverage"]["analysis"] >= 7
    assert manifest["comparison"]["available_view_count"] >= 40
    assert manifest["comparison"]["advanced_view_count"] >= manifest["comparison"]["available_view_count"]
    assert "recommended_views" in manifest["discovery"]
    assert "trend_cards" in manifest["discovery"]
    assert manifest["coverage"]["plot_ready"] == manifest["dataset_count"]


def test_v290_eis_drt_tafel_and_transient_views_are_semantically_complete(tmp_path: Path) -> None:
    root = tmp_path / "views"
    root.mkdir()
    _comprehensive_experiment(root)
    receipt = write_portable_browser(root, analysis_root=root / "processed", create_batch_zips=False)
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    by_tech = {}
    for row in manifest["datasets"]:
        by_tech.setdefault(row["technique_key"], []).append(row)
    eis_views = {v["id"] for v in by_tech["EIS"][0]["plot_views"]}
    assert {"nyquist", "bode_magnitude_derived", "bode_phase_derived", "admittance_nyquist", "bode_admittance_magnitude", "effective_series_capacitance"} <= eis_views
    assert {"drt_tau", "drt_frequency"} <= {v["id"] for v in by_tech["DRT"][0]["plot_views"]}
    assert "tafel_current" in {v["id"] for v in by_tech["TAFEL"][0]["plot_views"]}
    assert {"time_current", "time_charge", "anson", "cottrell"} <= {v["id"] for v in by_tech["CA"][0]["plot_views"]}
    assert {"time_potential", "sqrt_time_potential"} <= {v["id"] for v in by_tech["CP"][0]["plot_views"]}
    assert any(v["plot_type"] == "spatial" for v in by_tech["SPATIAL_MAP"][0]["plot_views"])


def test_v290_html_has_cross_technique_workspace_and_valid_javascript(tmp_path: Path) -> None:
    root = tmp_path / "portable"
    root.mkdir()
    _comprehensive_experiment(root)
    receipt = write_portable_browser(root, analysis_root=root / "processed", create_batch_zips=False)
    html_text = Path(receipt["html_path"]).read_text(encoding="utf-8")
    for phrase in ("FaradaIQ Experiment Explorer", "Consolidated results by technique", "Consolidated technique results", "Data and analysis results", "Add another experiment manifest", "Show technical/internal views"):
        assert phrase in html_text
    scripts = re.findall(r"<script>(.*?)</script>", html_text, flags=re.S)
    js_path = tmp_path / "portable_browser.js"
    js_path.write_text(scripts[-1], encoding="utf-8")
    checked = subprocess.run(["node", "--check", str(js_path)], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr


def test_v290_portability_and_batch_manifests_preserve_scientific_names(tmp_path: Path) -> None:
    root = tmp_path / "Experiment copied anywhere"
    root.mkdir()
    _csv(root, "processed/CA/method_01/point_001/ca.csv", ["time_s", "current_A"], [(i, -i*1e-6) for i in range(12)])
    (root / "short_name_map.json").write_text(json.dumps({"short_name_policy": {"enabled": True, "name_aliases": [{"original_name": "Very Long Experiment", "short_name": "exp__12345678", "kind": "folder"}]}}), encoding="utf-8")
    receipt = write_portable_browser(root, analysis_root=root / "processed", create_batch_zips=True)
    assert Path(receipt["html_path"]).is_file() and (root / README_NAME).is_file() and (root / MANIFEST_NAME).is_file()
    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["relative_paths_only"] is True and manifest["requires_web_server"] is False
    assert manifest["short_names"]["enabled"] is True
    batch = root / manifest["batches"][0]["zip_path"]
    with zipfile.ZipFile(batch) as archive:
        internal = json.loads(archive.read("FaradaIQ_batch_manifest.json"))
        assert internal["short_names"]["enabled"] is True
        assert internal["datasets"][0]["original_scientific_path"]
    moved = tmp_path / "different drive name"
    root.rename(moved)
    moved_manifest = json.loads((moved / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert (moved / moved_manifest["datasets"][0]["physical_path"]).is_file()
    assert (moved / moved_manifest["batches"][0]["zip_path"]).is_file()


def test_v290_registry_covers_extended_raw_and_analysis_families(tmp_path: Path) -> None:
    root = tmp_path / "extended_registry"
    root.mkdir()
    n = 8
    _csv(root, "processed/SWV/point_001/swv.csv", ["potential_V", "current_A"], [(i*.1, i*1e-6) for i in range(n)])
    _csv(root, "processed/NPV/point_001/npv.csv", ["potential_V", "current_A"], [(i*.1, i*1e-6) for i in range(n)])
    _csv(root, "processed/stripping/point_001/strip.csv", ["potential_V", "current_A"], [(i*.1, i*1e-6) for i in range(n)])
    _csv(root, "processed/OCP/point_001/ocp.csv", ["time_s", "potential_V"], [(i, i*.01) for i in range(n)])
    _csv(root, "processed/ZRA/point_001/zra.csv", ["time_s", "current_A"], [(i, i*1e-6) for i in range(n)])
    _csv(root, "processed/PITT/point_001/pitt.csv", ["time_s", "potential_V", "current_A"], [(i, i*.01, i*1e-6) for i in range(n)])
    _csv(root, "processed/conductivity/point_001/conductivity.csv", ["time_s", "conductivity_mS_cm", "temperature_C"], [(i, 10+i, 25+i*.1) for i in range(n)])
    _csv(root, "processed/approach/point_001/approach.csv", ["distance_um", "current_A"], [(i, i*1e-9) for i in range(n)])
    _csv(root, "processed/position/point_001/position.csv", ["time_s", "x_position_um", "y_position_um"], [(i, i, i*2) for i in range(n)])
    _csv(root, "analysis/battery_cycle_summary.csv", ["cycle", "specific_capacity_mAh_g", "coulombic_efficiency_pct"], [(i, 100-i, 99) for i in range(1, n)])
    _csv(root, "analysis/kinetics_summary.csv", ["point", "diffusion_coefficient_cm2_s", "khet_cm_s", "nicholson_psi"], [(i, 1e-5*i, 1e-3*i, .2*i) for i in range(1, n)])
    _csv(root, "analysis/eqcm_summary.csv", ["point", "mass_change_ng", "mass_per_charge_g_C", "sauerbrey_mass_ng"], [(i, i*2, i*.1, i*2.1) for i in range(1, n)])
    _csv(root, "analysis/replicate_statistics.csv", ["point", "mean_current_A", "std_current_A", "confidence_interval_A"], [(i, i, .1, .2) for i in range(1, n)])
    _csv(root, "analysis/nucleation_growth.csv", ["time_s", "normalized_current", "scharifker_hills_instantaneous", "scharifker_hills_progressive"], [(i, i*.1, i*.2, i*.3) for i in range(n)])
    _csv(root, "analysis/tafel_fit_summary.csv", ["point", "tafel_slope_mV_dec", "exchange_current_A", "r2"], [(i, 60+i, 1e-6*i, .99) for i in range(1, n)])

    receipt = write_portable_browser(root, analysis_root=root / "processed", create_batch_zips=False)
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    expected = {
        "SWV", "NPV", "STRIPPING", "OCP", "ZRA", "PITT", "CONDUCTIVITY", "APPROACH", "POSITION",
        "BATTERY_ANALYSIS", "KINETICS_ANALYSIS", "EQCM_ANALYSIS", "STATISTICS", "NUCLEATION_ANALYSIS", "TAFEL_ANALYSIS",
    }
    assert expected <= set(manifest["technique_counts"])
    assert manifest["coverage"]["plot_ready"] == manifest["dataset_count"]
    assert manifest["coverage"]["unavailable"] == 0
    assert all(row.get("plot_views") for row in manifest["datasets"])


def test_v290_media_previews_are_staged_to_compact_portable_assets(tmp_path: Path) -> None:
    root = tmp_path / "portable_media"
    source = root / "very" / "deep" / "generated" / "Figures" / "point_001" / "comparison figure.png"
    source.parent.mkdir(parents=True)
    # Valid 1 x 1 PNG.
    source.write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6360f8cff00000040101fef37b610000000049454e44ae426082"
    ))
    receipt = write_portable_browser(root, create_batch_zips=False)
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["media_assets"]["preview_count"] == 1
    assert manifest["media_assets"]["missing_count"] == 0
    media = manifest["media"][0]
    assert media["preview_available"] is True
    assert media["preview_asset_path"].startswith("browser_assets/media/m")
    assert (root / media["preview_asset_path"]).is_file()
    assert media["source_path"].endswith("comparison figure.png")
    html_text = Path(receipt["html_path"]).read_text(encoding="utf-8")
    assert "m.preview_href" in html_text
    assert "Download source" in html_text


def test_v290_plot_workspace_has_curve_selection_box_zoom_and_robust_autoscale(tmp_path: Path) -> None:
    root = tmp_path / "interactive"
    root.mkdir()
    for point, scale in ((1, 1.0), (2, 1.1), (3, 1.2), (4, 1e8), (5, 0.9)):
        _csv(
            root,
            f"analysis/DRT/point_{point:03d}/drt.csv",
            ["tau_s", "gamma_ohm"],
            [(10 ** (-5 + i / 5), scale * (i + 1)) for i in range(20)],
        )
    receipt = write_portable_browser(root, create_batch_zips=False)
    html_text = Path(receipt["html_path"]).read_text(encoding="utf-8")
    for phrase in (
        "Robust selected curves", "Full selected curves", "Highlighted curve",
        "Box zoom", "Select curve", "Solo", "outlier curve(s) clipped from scale",
        "Shift-click to solo", "function fitBounds(series,mode=compare.scaleMode)",
    ):
        assert phrase in html_text
    scripts = re.findall(r"<script>(.*?)</script>", html_text, flags=re.S)
    js_path = tmp_path / "interactive_browser.js"
    js_path.write_text(scripts[-1], encoding="utf-8")
    checked = subprocess.run(["node", "--check", str(js_path)], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr


def test_v290_media_distinguishes_generated_frames_from_acquisition_points(tmp_path: Path) -> None:
    root = tmp_path / "media_semantics"
    figure = root / ".browser_cache_from_h5" / "Media" / "Figures" / "comparison_current_007.png"
    photo = root / ".browser_cache_from_h5" / "Media" / "Photos" / "sample_redox_11.jpg"
    figure.parent.mkdir(parents=True, exist_ok=True)
    photo.parent.mkdir(parents=True, exist_ok=True)
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6360f8cff00000040101fef37b610000000049454e44ae426082"
    )
    figure.write_bytes(png)
    photo.write_bytes(png)
    receipt = write_portable_browser(root, create_batch_zips=False)
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    by_name = {m["name"]: m for m in manifest["media"]}
    assert by_name[figure.name]["point"] is None
    assert by_name[figure.name]["frame_index"] == 7
    assert by_name[figure.name]["media_role"] == "generated comparison frame"
    assert by_name[photo.name]["point"] == 11
    assert by_name[photo.name]["frame_index"] is None
    assert by_name[photo.name]["media_role"] == "acquisition photo"
    html_text = Path(receipt["html_path"]).read_text(encoding="utf-8")
    assert "m.frame_index!=null?'Frame '" in html_text


def test_v290_discover_hides_parser_tables_and_builds_trends(tmp_path: Path) -> None:
    root = tmp_path / "discover"
    root.mkdir()
    for point in range(1, 6):
        _csv(root, f"processed/CA/point_{point:03d}/ca.csv", ["time_s", "current_A", "charge_C"], [(i, -point*i*1e-9, point*i*1e-8) for i in range(1, 20)])
    _csv(root, "analysis/semantic_sections/tree.csv", ["record_offset", "record_size", "graph_index"], [(1,2,3),(4,5,6)])
    receipt = write_portable_browser(root, analysis_root=root / "processed", create_batch_zips=False)
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    technical = [d for d in manifest["datasets"] if d["ui_role"] == "technical"]
    assert len(technical) == 1 and technical[0]["ui_hidden_default"] is True
    assert manifest["comparison"]["suppressed_technical_view_count"] >= 1
    assert {r["view_id"] for r in manifest["discovery"]["recommended_views"]} >= {"time_current", "time_charge"}
    assert {r["view_id"] for r in manifest["discovery"]["trend_cards"]} >= {"time_current", "time_charge"}
    html_text = Path(receipt["html_path"]).read_text(encoding="utf-8")
    assert "Technical parser-column views are hidden by default" in html_text
    assert "function importExperimentFiles" in html_text


def test_v290_autorescale_right_click_tools_and_constant_trace_padding(tmp_path: Path) -> None:
    root = tmp_path / "interactive_context"
    root.mkdir()
    # Constant, very small current reproduces the position-trace scaling failure
    # where the old browser expanded the Y axis by +/-0.5 instead of scientific scale.
    _csv(
        root,
        "processed/POSITION/point_001/position.csv",
        ["time_s", "current_A", "x_position_m", "y_position_m"],
        [(0.0, 5e-10, 0.0, 0.0), (1e-5, 5e-10, 0.0, 0.0)],
    )
    _csv(
        root,
        "processed/EIS/point_001/eis.csv",
        ["frequency_Hz", "z_real_ohm", "z_imag_ohm"],
        [(1000, 10, -1), (100, 11, -2), (10, 12, -3)],
    )
    _csv(
        root,
        "analysis/DRT/point_001/drt.csv",
        ["tau_s", "gamma_ohm"],
        [(1e-6, 1), (1e-5, 3), (1e-4, 1)],
    )
    receipt = write_portable_browser(root, analysis_root=root / "processed", create_batch_zips=False)
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    policy = manifest["preview_policy"]
    assert policy["automatic_rescale_on_data_change"] is True
    assert policy["scientific_constant_trace_padding"] is True
    assert policy["right_click_plot_menu"] is True
    assert policy["pinned_data_cursor"] is True
    assert policy["technique_specific_plot_actions"] is True

    html_text = Path(receipt["html_path"]).read_text(encoding="utf-8")
    for phrase in (
        "Auto-rescale after filters, view, or curve selection changes",
        "Auto-fit now",
        "Read and pin nearest value",
        "Zoom X in",
        "Zoom Y in",
        "Pin highest-frequency row",
        "Pin largest DRT peak",
        "Pinned data cursor",
        "function equalRange(v,axis='y')",
        "right-click tools",
    ):
        assert phrase in html_text
    scripts = re.findall(r"<script>(.*?)</script>", html_text, flags=re.S)
    js_path = tmp_path / "context_browser.js"
    js_path.write_text(scripts[-1], encoding="utf-8")
    checked = subprocess.run(["node", "--check", str(js_path)], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
