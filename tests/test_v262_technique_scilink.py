from pathlib import Path
import csv
import json
import zipfile

import pytest

from faradaiq import v262_technique_scilink as v262
from faradaiq.ca_cp_analysis_v262 import analyze_file


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _ca_file(tmp_path: Path, point: int = 1) -> Path:
    return _write(
        tmp_path,
        f"CA_point_{point:03d}_method_01_cycle_01.csv",
        "time_s,point_index,current,potential\n"
        f"0,{point},1e-6,-0.9\n"
        f"1,{point},-5e-7,-0.9\n"
        f"2,{point},-2e-7,-0.9\n"
        f"3,{point},-1e-7,-0.9\n",
    )


def _headers(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle))


def test_ca_projection_uses_time_and_current_not_point_index(tmp_path):
    source = _ca_file(tmp_path)
    target = tmp_path / "prepared.csv"
    info = v262._prepare_scilink_table(source, target, ["CA"])
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "time_s,current_A"
    assert lines[1].split(",") == ["0", "1e-6"]
    assert info["scilink_columns"] == ["time_s", "current_A"]
    assert info["technique_contract"] == "chronoamperometry"
    assert "point_index" not in info["scilink_columns"]


def test_one_letter_alias_does_not_match_point_index():
    headers = ["time_s", "point_index", "current", "potential"]
    assert v262._find_column(headers, ("current_A", "current", "I")) == 2
    assert v262._find_column(headers, ("potential_V", "potential", "E")) == 3


def test_deposition_handoff_uses_time_and_current(tmp_path):
    source = _write(
        tmp_path,
        "DEPOSITION_point_002.csv",
        "time_s,point_index,current_A,potential_V\n0,2,-1e-6,-0.9\n1,2,-5e-7,-0.9\n",
    )
    target = tmp_path / "deposition.csv"
    info = v262._prepare_scilink_table(source, target, ["DEPOSITION"])
    assert _headers(target) == ["time_s", "current_A"]
    assert info["technique_contract"] == "deposition_current_transient"


def test_cp_projection_uses_time_and_potential(tmp_path):
    source = _write(
        tmp_path,
        "CP_point_003.csv",
        "time_ms,point_index,current_mA,potential_mV\n0,3,0.5,-100\n1000,3,0.5,-250\n",
    )
    target = tmp_path / "cp.csv"
    info = v262._prepare_scilink_table(source, target, ["CP"])
    assert _headers(target) == ["time_s", "potential_V"]
    rows = list(csv.reader(target.open(encoding="utf-8")))
    assert [float(v) for v in rows[2]] == pytest.approx([1.0, -0.25])
    assert info["technique_contract"] == "chronopotentiometry"


def test_ocp_projection_uses_time_and_potential(tmp_path):
    source = _write(tmp_path, "OCP_trace.csv", "time_s,potential_V\n0,0.15\n1,0.149\n")
    target = tmp_path / "ocp.csv"
    info = v262._prepare_scilink_table(source, target, ["OCP"])
    assert _headers(target) == ["time_s", "potential_V"]
    assert info["technique_contract"] == "open_circuit_potential"


@pytest.mark.parametrize("technique", ["CV", "LSV", "DPV", "SWV", "NPV", "STRIPPING", "TAFEL", "IV"])
def test_voltammetry_and_pulse_handoffs_use_potential_current(tmp_path, technique):
    source = _write(
        tmp_path,
        f"{technique}_curve.csv",
        "point_index,potential_mV,current_uA,time_s\n1,-200,-2,0\n1,100,4,1\n",
    )
    target = tmp_path / f"{technique}_prepared.csv"
    info = v262._prepare_scilink_table(source, target, [technique])
    assert _headers(target) == ["potential_V", "current_A"]
    rows = list(csv.reader(target.open(encoding="utf-8")))
    assert [float(v) for v in rows[1]] == pytest.approx([-0.2, -2e-6])
    assert info["technique_contract"] == "voltammetry"


def test_eis_handoff_uses_frequency_and_complex_impedance_without_sign_flip(tmp_path):
    source = _write(
        tmp_path,
        "EIS_point_001.csv",
        "frequency_kHz,Zre/Ohm,Zim/Ohm,point_index\n1,100,-50,1\n0.1,120,-70,1\n",
    )
    target = tmp_path / "eis.csv"
    info = v262._prepare_scilink_table(source, target, ["EIS"])
    assert _headers(target) == ["frequency_Hz", "z_real_ohm", "z_imag_ohm"]
    rows = list(csv.reader(target.open(encoding="utf-8")))
    assert [float(v) for v in rows[1]] == pytest.approx([1000.0, 100.0, -50.0])
    assert info["imaginary_sign_convention"].startswith("preserved")


def test_eis_prime_notation_keeps_real_and_imaginary_columns_distinct(tmp_path):
    source = _write(tmp_path, "EIS_prime.csv", "Frequency/Hz,Z',Z''\n1000,10,-4\n")
    target = tmp_path / "eis_prime.csv"
    v262._prepare_scilink_table(source, target, ["EIS"])
    rows = list(csv.reader(target.open(encoding="utf-8")))
    assert rows[0] == ["frequency_Hz", "z_real_ohm", "z_imag_ohm"]
    assert [float(v) for v in rows[1]] == pytest.approx([1000.0, 10.0, -4.0])


def test_geis_uses_same_complex_impedance_contract(tmp_path):
    source = _write(tmp_path, "GEIS.csv", "freq,zreal,zimag\n1000,2,-1\n")
    target = tmp_path / "geis.csv"
    info = v262._prepare_scilink_table(source, target, ["GEIS"])
    assert _headers(target) == ["frequency_Hz", "z_real_ohm", "z_imag_ohm"]
    assert info["technique_contract"] == "complex_impedance"


def test_drt_handoff_uses_tau_and_gamma(tmp_path):
    source = _write(tmp_path, "DRT_position001.csv", "tau_ms,gamma_kOhm,position\n1,2,1\n10,3,1\n")
    target = tmp_path / "drt.csv"
    info = v262._prepare_scilink_table(source, target, ["DRT"])
    assert _headers(target) == ["tau_s", "gamma_ohm"]
    rows = list(csv.reader(target.open(encoding="utf-8")))
    assert [float(v) for v in rows[1]] == pytest.approx([0.001, 2000.0])
    assert info["technique_contract"] == "distribution_of_relaxation_times"


def test_gcd_handoff_uses_time_and_potential(tmp_path):
    source = _write(tmp_path, "GCD_cycle_01.csv", "time_s,current_A,potential_V,capacity_C\n0,0.001,2.5,0\n1,0.001,2.6,0.001\n")
    target = tmp_path / "gcd.csv"
    info = v262._prepare_scilink_table(source, target, ["GCD"])
    assert _headers(target) == ["time_s", "potential_V"]
    assert info["technique_contract"] == "chronopotentiometry"


def test_generic_curve_fallback_excludes_index_columns(tmp_path):
    source = _write(tmp_path, "unknown_curve.csv", "point_index,time_s,signal\n1,0,4\n1,1,5\n")
    target = tmp_path / "generic.csv"
    info = v262._prepare_scilink_table(source, target, [])
    assert _headers(target) == ["time_s", "signal"]
    assert info["technique_contract"] == "generic_numeric_curve"


def test_spatial_series_is_generated_from_100um_step(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    paths = []
    provenance = []
    for point in (1, 2, 3):
        source = _ca_file(tmp_path, point)
        target = data / f"{point:04d}_{source.name}"
        provenance.append(v262._prepare_scilink_table(source, target, ["CA"]))
        paths.append(target)
    context = v262._write_scilink_context(
        tmp_path, paths, ["Method 01 CA"],
        "Experiment name: sample_linescan_17mm_100umstep_run3", provenance,
    )
    payload = json.loads(context.read_text(encoding="utf-8"))
    assert payload["series_metadata"]["variable"] == "position_mm"
    assert payload["series_metadata"]["step_mm"] == 0.1
    assert list(payload["series_metadata"]["values"].values()) == [0.0, 0.1, 0.2]
    contract = payload["data_structure"]["technique_curve_contracts"][0]
    assert contract["columns"] == ["time_s", "current_A"]
    assert "point_index" in contract["excluded_control_columns"]


def test_review_shows_bundle_and_context_json_checked_by_default(tmp_path, monkeypatch):
    source_file = _ca_file(tmp_path)
    monkeypatch.setattr(v262, "_browser_selection_paths", lambda _source: [source_file])
    reviewed = v262._review_candidates(object(), [{"path": str(tmp_path / "other.txt"), "role": "Other"}])
    assert [item["generated"] for item in reviewed[:2]] == ["browser_selection_bundle", "browser_selection_context_json"]
    assert reviewed[0]["selected"] is True
    assert reviewed[1]["selected"] is True
    assert reviewed[2]["selected"] is False


def test_materialized_bundle_and_json_are_both_present_and_audited(tmp_path, monkeypatch):
    source_file = _ca_file(tmp_path)
    monkeypatch.setattr(v262, "_selected_record_names", lambda _source: ["Method 01 CA"])
    monkeypatch.setattr(v262, "_experiment_description", lambda _source: "10 mM CoCl2, 100umstep line scan")
    selected = [
        {
            "generated": "browser_selection_bundle",
            "bundle_source_paths": [str(source_file)],
            "selected": True,
        },
        {
            "generated": "browser_selection_context_json",
            "selected": True,
        },
    ]
    data, support, metadata, bundle = v262._materialize_selected_files(object(), selected, tmp_path / "job")
    assert len(data) == 1
    assert support == []
    assert len(metadata) == 1
    assert Path(metadata[0]).name == "scilink_bundle_context.json"
    assert bundle is not None and bundle.is_file()
    assert _headers(Path(data[0])) == ["time_s", "current_A"]
    with zipfile.ZipFile(bundle) as zf:
        names = set(zf.namelist())
    assert any(name.startswith("data/") and name.endswith(".csv") for name in names)
    assert "data/scilink_bundle_context.json" in names
    assert any(name.startswith("source_data/") for name in names)
    preflight = json.loads((tmp_path / "job" / "scilink_data_preflight.json").read_text())
    assert preflight["bundle_selected_in_review"] is True
    assert preflight["context_json_selected_in_review"] is True


def test_preview_lists_separate_bundle_and_context_json(tmp_path, monkeypatch):
    source_file = _ca_file(tmp_path)
    monkeypatch.setattr(v262, "_selected_record_names", lambda _source: ["CA"])
    selected = [
        {"generated": "browser_selection_bundle", "bundle_source_paths": [str(source_file)]},
        {"generated": "browser_selection_context_json"},
    ]
    preview = v262._preview_request(object(), selected, [])
    assert preview["selected_bundle_paths"] == ["<new SciLink job>/scilink_ready_browser_selection.zip"]
    assert preview["metadata_paths"] == ["<new SciLink job>/data/scilink_bundle_context.json"]
    assert len(preview["selected_data_paths"]) == 1


def test_ca_native_analysis_reports_charge_and_sh_applicability(tmp_path):
    source = _ca_file(tmp_path)
    result = analyze_file(source, position_step_mm=0.1)
    assert result.technique == "CA"
    assert result.charge_C is not None
    assert result.cathodic_charge_C is not None
    assert result.scharifker_hills_class in {"instantaneous", "progressive", "not_applicable"}


def test_technique_prompt_templates_include_all_major_handoffs_and_are_user_visible():
    assert "Scharifker-Hills" in v262.technique_prompt("CA Scharifker-Hills nucleation analysis")
    assert "transition time" in v262.technique_prompt("CP transition-time and stability analysis")
    assert "DPV" in v262.technique_prompt("Pulse and stripping voltammetry")
    assert "Tafel" in v262.technique_prompt("Tafel and polarization analysis")
    assert "coulombic efficiency" in v262.technique_prompt("Galvanostatic charge-discharge analysis")
    source = Path(v262.__file__).read_text(encoding="utf-8")
    assert "Append editable template to prompt" in source
    assert "Templates are never added silently" in source
    assert "Prepared experimental data bundle" in source
    assert "Compact SciLink data-structure JSON" in source


def test_scilink_runner_passes_supported_data_and_metadata_flags():
    runner = Path(v262.__file__).resolve().parents[1] / "faradaiq_scilink_cli_job.py"
    text = runner.read_text(encoding="utf-8")
    assert '("--data", "--data-dir"' in text
    assert '("--manifest", "--input-manifest", "--data-manifest", "--metadata"' in text
