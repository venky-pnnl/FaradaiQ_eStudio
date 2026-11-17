from pathlib import Path


def test_manifest_declared_mott_schottky_beats_capacitance_substring():
    import faradaiq_canonical_bundle as c
    entry = {
        'technique': 'MOTT_SCHOTTKY',
        'relative_csv_path': 'sample_Mott_Schottky/sample__mott_schottky_capacitance_summary.csv',
        'curve_title': 'Mott-Schottky capacitance summary',
    }
    tech, role = c._v145_canonical_from_manifest_entry(entry, Path(entry['relative_csv_path']), ['potential_V','capacitance_parallel_F'])
    assert tech == 'Mott-Schottky'
    assert 'Mott' in role


def test_candidate_circuit_table_is_eis_analysis_not_cv():
    import faradaiq_canonical_bundle as c
    entry = {
        'technique': 'EIS_CIRCUIT_MODEL',
        'relative_csv_path': 'sample_Advanced_Potential_EIS/sample__candidate_circuits.csv',
        'curve_title': 'Potential-dependent EIS candidate circuits',
    }
    tech, role = c._v145_canonical_from_manifest_entry(entry, Path(entry['relative_csv_path']), ['potential_V','model','aicc','rmse'])
    assert tech == 'EIS analysis'
    assert 'circuit' in role.lower()


def test_v317_manifest_normalization_recomputes_family_counts():
    from faradaiq.v317_advanced_potential_eis import _normalize_v317_manifest_datasets
    manifest = {
        'datasets': [
            {'id':'n','technique_key':'SEMICONDUCTOR_ANALYSIS','technique_label':'Mott-Schottky','family':'Semiconductor analysis','physical_path':'x__Mott-Schottky_0.5000_V_Nyquist.csv'},
            {'id':'b','technique_key':'SEMICONDUCTOR_ANALYSIS','technique_label':'Mott-Schottky','family':'Semiconductor analysis','physical_path':'x__Mott-Schottky_0.5000_V_Bode_mag.csv'},
            {'id':'m','technique_key':'MOTT_SCHOTTKY_DIAGNOSTIC','technique_label':'Mott diag','family':'EIS analysis','physical_path':'x__mott_schottky_strict_diagnostics.csv'},
        ],
        'technique_counts': {'SEMICONDUCTOR_ANALYSIS':2,'MOTT_SCHOTTKY_DIAGNOSTIC':1},
        'family_counts': {'Semiconductor analysis':2,'EIS analysis':1},
    }
    out = _normalize_v317_manifest_datasets(manifest)
    assert out['technique_counts'] == {'EIS':2,'MOTT_SCHOTTKY_DIAGNOSTIC':1}
    assert out['family_counts'] == {'EIS analysis':1,'Impedance':2}


def test_app_prefers_v317_entrypoint(monkeypatch):
    import faradaiq.app as app
    calls=[]
    class M:
        def _v317_main_final(self): calls.append('317')
        def _v316_main_final(self): calls.append('316')
        def _v315_main_final(self): calls.append('315')
    monkeypatch.setattr(app, 'legacy_module', lambda: M())
    app.main([])
    assert calls == ['317']


def test_v317_canonical_includes_completed_drt_but_not_staging_input(tmp_path):
    import faradaiq_canonical_bundle as cb

    staging = tmp_path / "DRT_input_csv" / "sample__freq_zreal_zimag.csv"
    result = tmp_path / "DRT_input_csv" / "DRT" / "run_001" / "sample__simple_DRT.csv"
    staging.parent.mkdir(parents=True, exist_ok=True)
    result.parent.mkdir(parents=True, exist_ok=True)
    staging.write_text("frequency_Hz,Zreal_ohm,Zimag_ohm\n1,10,-2\n", encoding="utf-8")
    result.write_text("tau,gamma\n1e-3,0.5\n", encoding="utf-8")

    assert cb._include_nonheka(staging) is False
    assert cb._include_nonheka(result) is True
    tech, role = cb.infer_technique_nonheka(result, ["tau", "gamma"])
    assert tech == "DRT"
    assert "DRT" in role


def test_v317_analysis_manifest_serializes_path_source(tmp_path):
    from pathlib import Path
    import json
    import faradaiq_studio_core as core

    collection = core.PSSessionCollection()
    collection.source_path = Path(tmp_path / "sample.par")
    collection.mode = "single"
    info = collection.export_analysis_ready_csv(tmp_path, "sample")

    manifest = json.loads(Path(info["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["source_file"] == str(collection.source_path)


def test_v317_full_complex_capacitance_grid_is_manifested(tmp_path):
    import numpy as np
    from faradaiq.v317_advanced_potential_eis import _export_advanced

    rows = []
    for i, v in enumerate([0.5, 0.0, -0.35], 1):
        f = np.geomspace(1e6, 1, 9)
        w = 2 * np.pi * f
        z = 320.0 + 1j * w * 5.5e-5 + 1 / (1.4e-6 * (1j * w) ** 0.92)
        for j, (ff, zz) in enumerate(zip(f, z), 1):
            rows.append([i, j, v, ff, zz.real, zz.imag])
    ms = {
        "array": np.asarray(rows, float),
        "columns": ["potential_step_index", "frequency_index", "potential_V", "frequency_Hz", "Zreal_ohm", "Zimag_ohm"],
        "n_potential_steps": 3,
        "n_frequencies": 9,
        "title": "synthetic potential EIS",
    }
    class Parser:
        decoded = {"mott_schottky": [ms]}

    info = _export_advanced(Parser(), tmp_path, "demo")
    entries = {e["relative_csv_path"]: e for e in info["csv_entries"]}
    matches = [(p, e) for p, e in entries.items() if p.endswith("__complex_capacitance_all_points.csv")]
    assert len(matches) == 1
    assert matches[0][1]["technique"] == "EIS_CAPACITANCE"
    assert matches[0][1]["n_points"] == 27
