from pathlib import Path

import numpy as np
import pandas as pd

from faradaiq.v309_drt_spectrum_inputs import rebuild_drt_inputs_from_parsed_groups


def _write_group(path: Path, shift: float):
    f = np.logspace(np.log10(24000.0), 0.0, 39)
    zr = 1000.0 + shift + np.arange(len(f), dtype=float)
    zi = -(500.0 + shift + np.arange(len(f), dtype=float))
    pd.DataFrame({"f2[Hz]": f, "RealZ[Ohm]": zr, "ImagZ[Ohm]": zi}).to_csv(path, index=False)


def test_many_eis_at_one_point_are_one_drt_file_per_acquisition(tmp_path):
    data = tmp_path / "sample_for_plotting_analysis" / "data"
    groups = data / "fullEIS_Groups"
    groups.mkdir(parents=True)
    for i in range(16):
        _write_group(groups / f"fullEIS_{i:02d}.txt", float(i))

    drt = data.parent / "DRT_input_csv"
    drt.mkdir()
    # Reproduce the legacy failure shape: two concatenated 624-row inputs.
    for method in (1, 2):
        pd.DataFrame({
            "frequency_Hz": np.linspace(624, 1, 624),
            "Zreal_ohm": np.arange(624, dtype=float),
            "Zimag_ohm": -np.arange(624, dtype=float),
        }).to_csv(drt / f"method{method:02d}_position001__freq_zreal_zimag.csv", index=False)
    stale = drt / "DRT" / "old_run"
    stale.mkdir(parents=True)
    (stale / "stale.csv").write_text("x\n1\n")

    out = rebuild_drt_inputs_from_parsed_groups([data])
    assert out["spectra_written"] == 16
    assert out["old_inputs_removed"] == 2
    assert not (drt / "DRT").exists()
    files = sorted(drt.glob("*__freq_zreal_zimag.csv"))
    assert len(files) == 16
    assert all(len(pd.read_csv(p)) == 39 for p in files)
    assert (drt / "DRT_input_spectrum_manifest.json").exists()


def test_no_parsed_eis_groups_leaves_existing_input_untouched(tmp_path):
    data = tmp_path / "sample_for_plotting_analysis" / "data"
    data.mkdir(parents=True)
    drt = data.parent / "DRT_input_csv"
    drt.mkdir()
    p = drt / "existing__freq_zreal_zimag.csv"
    p.write_text("frequency_Hz,Zreal_ohm,Zimag_ohm\n1000,1,-1\n100,2,-2\n10,3,-3\n1,4,-4\n")
    out = rebuild_drt_inputs_from_parsed_groups([data])
    assert out["spectra_written"] == 0
    assert p.exists()


def test_batch_job_prepares_grouped_drt_before_execution():
    src = Path(__file__).resolve().parents[1] / "faradaiq" / "v233_batch_jobs.py"
    text = src.read_text(encoding="utf-8")
    assert "rebuild_drt_inputs_from_parsed_groups" in text
    assert "repeated EIS at one point remain separate acquisitions" in text


def test_drt_output_validation_requires_one_result_per_input(tmp_path):
    from faradaiq.v309_drt_spectrum_inputs import validate_drt_outputs
    drt = tmp_path / "DRT_input_csv"; drt.mkdir()
    for i in range(3):
        (drt / f"spectrum{i+1:03d}__freq_zreal_zimag.csv").write_text(
            "frequency_Hz,Zreal_ohm,Zimag_ohm\n1000,1,-1\n100,2,-2\n10,3,-3\n1,4,-4\n"
        )
    out = drt / "DRT" / "run"; out.mkdir(parents=True)
    for i in range(2):
        stem = f"spectrum{i+1:03d}__freq_zreal_zimag"
        (out / f"{stem}_simple_drt_data.csv").write_text("tau,gamma\n1,1\n")
    chk = validate_drt_outputs(drt)
    assert chk["expected"] == 3
    assert chk["completed"] == 2
    assert chk["complete"] is False
    assert len(chk["missing"]) == 1
