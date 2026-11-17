from __future__ import annotations

from pathlib import Path

import faradaiq
import faradaiq_studio_core as core
from faradaiq import v292_release_integrity as v290


def test_release_identity_is_centralized():
    assert faradaiq.__version__ == "0.316.0"
    assert faradaiq.APP_VERSION == "v316"
    assert faradaiq.APP_TITLE == "FaradaIQ Studio v316"
    assert core.APP_TITLE == "FaradaIQ Studio v316"
    assert core.FARADAIQ_PUBLIC_VERSION == "v316"
    assert core.FARADAIQ_APP_VERSION == "v316"
    assert hasattr(core, "_v292_main_final")


def test_release_text_normalizes_old_public_titles():
    assert v290.normalize_release_text("About / credits — FaradaIQ Studio v262") == "About / credits — FaradaIQ Studio v316"
    assert v290.normalize_release_text("FaradaIQ v255 interactive visualization") == "FaradaIQ Studio v316 interactive visualization"
    assert v290.normalize_release_text("FaradaIQ Studio v262 — Experiment export jobs") == "FaradaIQ Studio v316 — Experiment export jobs"


def _write_eis(path: Path) -> None:
    path.write_text(
        "frequency_Hz,z_real_ohm,z_imag_ohm\n"
        "1000,10,-1\n100,11,-2\n10,12,-3\n",
        encoding="utf-8",
    )


def test_drt_gate_blocks_ca_even_when_stale_drt_folder_exists(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "sample_fullCA_1.asc").write_text(
        "Index,Time[s],Imon-2[A],Time[s],Emon-2[V]\n"
        "0,0,1e-6,0,-0.9\n1,1,5e-7,1,-0.9\n2,2,2e-7,2,-0.9\n",
        encoding="utf-8",
    )
    drt = tmp_path / "DRT_input_csv"
    drt.mkdir()
    _write_eis(drt / "stale_eis.csv")

    commands, notes, detected = core._v27_build_auto_pyseccm_commands_for_data_dir(
        data,
        script_root=Path(core.__file__).parent / "pyseccm_tools",
        python_exe="python",
        include_drt=True,
    )
    assert any(str(args[1]).endswith("parse_CA.py") for args, _cwd in commands)
    assert not any(v290._is_drt_command(args) for args, _cwd in commands)
    assert not detected.get("eis")
    assert any("DRT skipped: no valid EIS" in note for note in notes)


def test_drt_gate_allows_verified_eis_and_verified_drt_inputs(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "sample_fullEIS_6.txt").write_text(
        "Sweep #, f2[Hz], log(f), AdmitY[S], log(AdmitY), RealY[S], ImagY[S], Phase[deg], RealZ[Ohm], ImagZ[Ohm], ImpZ[Ohm], log(ImpZ)\n"
        "1,1000,3,0,0,0,0,-5,10,-1,10.05,1\n"
        "1,100,2,0,0,0,0,-10,11,-2,11.18,1\n"
        "1,10,1,0,0,0,0,-15,12,-3,12.37,1\n",
        encoding="utf-8",
    )
    drt = tmp_path / "DRT_input_csv"
    drt.mkdir()
    _write_eis(drt / "position_001.csv")

    commands, notes, _detected = core._v27_build_auto_pyseccm_commands_for_data_dir(
        data,
        script_root=Path(core.__file__).parent / "pyseccm_tools",
        python_exe="python",
        include_drt=True,
    )
    assert any(v290._is_drt_command(args) for args, _cwd in commands)
    assert not any("DRT skipped" in note for note in notes)


def test_help_is_workflow_organized_and_current():
    pages = v290._help_pages()
    names = [name for name, _content in pages]
    assert names == [
        "Start here",
        "Main Studio",
        "Browser and selection",
        "Technique analysis",
        "SciLink, Edison, LabRIC",
        "Plots and maps",
        "Sessions, jobs, export",
        "Safety and troubleshooting",
        "Release and credits",
    ]
    all_text = "\n".join(content for _name, content in pages)
    assert "FaradaIQ Studio v316" in all_text
    assert "DRT is never inferred merely because its checkbox is enabled" in all_text
    assert "short_name_map.json" in all_text
    assert "Use shortened names for generated folders and files" in all_text
    assert "FaradaIQ Studio v262" not in all_text


def test_active_public_subsystems_report_v290():
    v290.reassert_active_module_versions()
    from faradaiq import v233_batch_jobs, v232_batch_jobs, v261_interactive_scilink, v261_labric_mcp, v262_technique_scilink
    from faradaiq import ca_cp_analysis_v262

    modules = [
        v233_batch_jobs,
        v232_batch_jobs,
        v261_interactive_scilink,
        v261_labric_mcp,
        v262_technique_scilink,
        ca_cp_analysis_v262,
    ]
    assert all(getattr(module, "VERSION", "") == "v316" for module in modules)


def test_tracking_payload_and_canonical_bundle_report_v290(monkeypatch):
    recorded = []
    core = {"_v753_track_event": lambda event, payload=None, *args, **kwargs: recorded.append((event, payload))}
    v290._install_tracking_version_normalization(core)
    core["_v753_track_event"]("main_window_open", {"title": "FaradaIQ Studio v316", "nested": ["Version v262"]})
    assert recorded == [("main_window_open", {"title": "FaradaIQ Studio v316", "nested": ["Version v316"]})]

    import faradaiq_canonical_bundle as canonical
    assert canonical.VERSION == "v316"
