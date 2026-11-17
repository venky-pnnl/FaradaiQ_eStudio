from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from faradaiq import v290_electrochemistry_routing as route


def _write_cv(path: Path) -> None:
    rows = ["potential_V,current_A,cycle"]
    for cycle in (1, 2, 3):
        for e in (-0.6, -0.3, 0.0, 0.4, 0.8, 0.4, 0.0, -0.3, -0.6):
            rows.append(f"{e},{e*1e-8},{cycle}")
    path.write_text("\n".join(rows), encoding="utf-8")


def _receipt(job: Path) -> dict:
    skill_path = job / "skills" / "electrochemistry_skill.md"
    skill_path.parent.mkdir(exist_ok=True)
    skill_path.write_text("## Overview\n", encoding="utf-8")
    return {"skill_name": "electrochemistry_skill", "skill_version": "7.0-guided-hybrid-analysis", "job_skill_path": str(skill_path), "sha256": "x"}


def test_standard_scilink_is_default_and_does_not_precalculate(tmp_path):
    data = tmp_path / "GenericSeries_A1_CV.csv"; _write_cv(data)
    job = tmp_path / "job"; job.mkdir()
    routing = route.prepare_job_routing(job, {"selected_data_paths": [str(data)]}, "Analyze the CV series", {}, _receipt(job))
    assert routing["analysis_mode"] == "standard_scilink"
    assert routing["select_agent_allowed"] is True
    assert routing["run_analysis_allowed"] is True
    assert routing["generated_code_allowed"] is True
    assert routing["interpretation_only"] is False
    assert routing["deterministic_analysis_required"] is False
    assert not (job / "deterministic_electrochemistry_analysis.json").exists()
    assert routing["gui_status"]["Analysis mode"] == "Standard SciLink"
    assert routing["gui_status"]["SciLink tools"] == "Available"


def test_optional_deterministic_mode_populates_supporting_evidence(tmp_path):
    data = tmp_path / "GenericSeries_A1_CV.csv"; _write_cv(data)
    job = tmp_path / "job"; job.mkdir()
    settings = {"deterministic_electrochemistry_enabled": True}
    routing = route.prepare_job_routing(job, {"selected_data_paths": [str(data)]}, "Analyze the CV series", settings, _receipt(job))
    direct = json.loads((job / "deterministic_electrochemistry_analysis.json").read_text(encoding="utf-8"))
    assert routing["analysis_mode"] == "optional_deterministic"
    assert routing["deterministic_electrochemistry_enabled"] is True
    assert routing["deterministic_results_are_supporting_evidence"] is True
    assert direct["analyses"]
    assert routing["gui_status"]["Analysis mode"] == "Deterministic electrochemistry"
    assert routing["gui_status"]["Deterministic preprocessing"] == "Completed"


def test_cv_direction_change_overrides_lsv_label():
    e = np.concatenate([np.linspace(-0.6, 0.8, 200), np.linspace(0.8, -0.6, 200)[1:]])
    i = 1e-8 * np.sin(np.linspace(0, 4*np.pi, len(e)))
    arr = np.column_stack([e, i])
    assert route.potential_direction_change_count(["potential_V", "current_A"], arr) >= 1
    assert route.refine_cv_lsv_technique(["potential_V", "current_A"], arr, "LSV") == "CV"


def test_dummy_cell_eis_complex_fit_and_drt_guard_are_preserved():
    f = np.logspace(5, -2, 101)
    z = route._dummy_impedance(f, 1000.0, 10000.0, 10e-6)
    arr = np.column_stack([f, z.real, z.imag])
    result = route.analyze_electrochemical_record({
        "technique": "EIS", "columns": ["frequency_Hz", "z_real_ohm", "z_imag_ohm"],
        "array": arr, "identity": {}, "record_json": {}, "source_path": "dummy_eis.csv", "dataset_path": None,
    }, dummy=True, linked_eis_geis=True)[0]
    assert result["model"] == "Rs + (Rp || C)"
    assert result["attempt_count"] == 1
    assert abs(result["fitted"]["Rs_ohm"] - 1000.0) < 1e-3
    blocked = route.analyze_drt(["tau_s", "gamma_ohm"], np.array([[0.1, 1.0], [1.0, 2.0]]), {}, False)
    assert blocked["status"] == "blocked_without_linked_eis_geis"


def test_series_metadata_is_structured_json_and_paths_are_exact(tmp_path):
    context = tmp_path / "GenericSeries_A1_context.json"
    context.write_text(json.dumps({"series_metadata": {"variable": "scan_rate_mV_s", "unit": "mV/s", "values": {"a.csv": 25.0}}}), encoding="utf-8")
    receipt = route.write_validated_series_metadata(context, tmp_path)
    assert receipt["json_validated"] is True
    parsed = json.loads(receipt["series_metadata_json"])
    assert parsed["values"]["a.csv"] == 25.0
    assert "GenericSeries_A1" in str(context.resolve(strict=True))
    assert "GenericSeries_AA1" not in str(context.resolve(strict=True))


def test_gui_checkbox_contract_is_default_unchecked():
    source = (Path(__file__).resolve().parents[1] / "faradaiq" / "v262_technique_scilink.py").read_text(encoding="utf-8")
    assert "deterministic_electrochemistry_var = tk.BooleanVar(value=False)" in source
    assert 'text="Deterministic electrochemistry"' in source
    assert "Precalculate technique-specific electrochemical metrics before SciLink analysis." in source
