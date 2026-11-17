from __future__ import annotations

from pathlib import Path

import pytest

from faradaiq import v232_batch_jobs, v233_batch_jobs


def _job(module, tmp_path: Path, *, valid_eis: bool):
    source = tmp_path / "sample.dat"
    source.write_text("CA only", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    analysis_root = output / "sample_analysis_ready_csv"
    data_dir = analysis_root / "sample_for_plotting_analysis" / "data"
    data_dir.mkdir(parents=True)
    drt_dir = data_dir.parent / "DRT_input_csv"
    drt_dir.mkdir()
    if valid_eis:
        (drt_dir / "position_001.csv").write_text(
            "frequency_Hz,z_real_ohm,z_imag_ohm\n"
            "1000,10,-1\n100,11,-2\n10,12,-3\n",
            encoding="utf-8",
        )

    job = module.BatchJob(
        source_path=str(source),
        experiment_name="CA batch DRT gate",
        output_dir=str(output),
    )
    job.export_settings.update(
        {
            "archive_npy_and_summary_json": False,
            "analysis_ready_csv": True,
            "raw_vendor_json": False,
            "run_technique_parsers": False,
            "canonical_h5_json": False,
            "build_browser_cache": False,
            "resume_completed_stages": True,
        }
    )
    job.drt_settings["enabled"] = True
    job.completed_stages = ["inspection", "export"]
    job.outputs = {
        "output_dir": str(output),
        "analysis_ready_root": str(analysis_root),
    }
    return job, drt_dir


@pytest.mark.parametrize("module", [v232_batch_jobs, v233_batch_jobs])
def test_empty_batch_drt_folder_is_skipped_without_launch(module, tmp_path, monkeypatch):
    job, drt_dir = _job(module, tmp_path, valid_eis=False)
    launched = []
    messages = []

    monkeypatch.setattr(module, "_run_command", lambda *a, **k: launched.append((a, k)))
    module.run_batch_job(
        job,
        {},
        lambda _status, _stage, _pct, message: messages.append(str(message)),
    )

    assert drt_dir.is_dir()
    assert launched == []
    assert "drt" in job.completed_stages
    assert any("DRT skipped: no validated EIS input" in message for message in messages)


@pytest.mark.parametrize("module", [v232_batch_jobs, v233_batch_jobs])
def test_batch_drt_validator_accepts_real_eis_tables(module, tmp_path):
    _job_obj, drt_dir = _job(module, tmp_path, valid_eis=True)
    assert module._validated_drt_input_dir(drt_dir)


@pytest.mark.parametrize("module", [v232_batch_jobs, v233_batch_jobs])
def test_batch_drt_validator_rejects_ca_tables(module, tmp_path):
    _job_obj, drt_dir = _job(module, tmp_path, valid_eis=False)
    (drt_dir / "ca.csv").write_text(
        "time_s,current_A\n0,1e-6\n1,5e-7\n2,2e-7\n",
        encoding="utf-8",
    )
    assert not module._validated_drt_input_dir(drt_dir)
