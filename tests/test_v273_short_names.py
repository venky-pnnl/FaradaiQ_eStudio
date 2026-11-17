from __future__ import annotations

import json
import zipfile
from pathlib import Path

import faradaiq_studio_core as core
import faradaiq_scilink_cli_job as scilink_cli
from faradaiq import browser_catalog_v209 as catalog
from faradaiq import v232_batch_jobs as jobs
from faradaiq import v233_batch_jobs as active_jobs
from faradaiq import v262_technique_scilink as scilink
from faradaiq import v273_short_names as short


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class _Source:
    pass


def test_short_component_is_deterministic_and_bounded(monkeypatch):
    monkeypatch.setenv(short.ENV_KEY, "1")
    original = "A very long generated filename with sample concentration electrode potential cycle repeat and acquisition details.csv"
    first = short.short_component(original)
    second = short.short_component(original)
    assert first == second
    assert len(first) <= short.MAX_COMPONENT_CHARS
    assert first.endswith(".csv")
    assert "__" in first


def test_disabled_policy_preserves_full_generated_name(monkeypatch):
    monkeypatch.setenv(short.ENV_KEY, "0")
    original = "A very long generated name that must remain unshortened when the operator disables the checkbox.csv"
    assert short.short_component(original) == original.replace(" ", "_")


def test_jobs_default_to_safe_short_paths_without_changing_logical_base(monkeypatch):
    monkeypatch.setenv(short.ENV_KEY, "1")
    assert jobs.default_export_settings()["short_names"] is True
    long_name = "A very long experiment name containing sample chemistry geometry scan settings and repeated conditions"
    job = jobs.BatchJob(source_path="source.dat", experiment_name=long_name, output_dir="output")
    assert job.base_name == long_name.replace(" ", "_")
    assert job.to_dict()["export_settings"]["short_names"] is True
    assert active_jobs.default_export_settings()["short_names"] is True
    policy = short.policy_payload(enabled=True)
    assert policy["canonical_identity_changed"] is False
    assert policy["scientific_schema_names_renamed"] is False
    assert len(short.short_base(job.base_name)) <= short.MAX_BASE_CHARS


def test_analysis_export_shortens_outer_physical_root_but_preserves_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv(short.ENV_KEY, "1")

    class Parser:
        def export_analysis_ready_outputs(self, target, stem):
            return {"csv_entries": [], "skipped_entries": [], "n_csv_files": 0}

        def iter_primary_curve_exports(self):
            yield {
                "measurement_index": 1,
                "curve_index": 2,
                "curve_title": "CA",
                "rows": [(0.0, 1.0), (1.0, 2.0)],
                "measurement_title": "measurement",
                "method_id": "ChronoPotStat",
                "technique": "CA",
                "scan_rate": "",
                "x_label": "time",
                "x_unit": "s",
                "y_label": "current",
                "y_unit": "A",
            }

    original_subfolders = [
        "Long original source folder containing sample chemistry",
        "Long original acquisition folder containing instrument method",
    ]
    collection = core.PSSessionCollection()
    collection.source_path = "source"
    collection.mode = "directory"
    collection.entries = [{
        "path_metadata": {
            "subfolder_names": list(original_subfolders),
            "basename": "original_source_file.dat",
            "relative_path": "original/source.dat",
        },
        "parser": Parser(),
    }]

    logical = "A very long experiment base name containing sample chemistry geometry and all acquisition conditions"
    info = collection.export_analysis_ready_csv(tmp_path, logical)
    assert len(Path(info["analysis_ready_root"]).name) <= short.MAX_SUBFOLDER_CHARS
    assert collection.entries[0]["path_metadata"]["subfolder_names"] == original_subfolders

    manifest = json.loads(Path(info["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["original_requested_base_name"] == logical
    assert manifest["physical_generated_base_name"] != logical
    assert manifest["scientific_schema_names_preserved"] is True
    assert manifest["canonical_identity_base_name"] == logical
    policy = manifest["short_name_policy"]
    assert policy["enabled"] is True
    assert policy["scientific_schema_names_renamed"] is False
    assert policy["canonical_identity_changed"] is False
    assert Path(info["short_name_map_path"]).is_file()


def test_semantic_heka_tree_is_never_shortened(tmp_path, monkeypatch):
    monkeypatch.setenv(short.ENV_KEY, "1")
    heka = tmp_path / "Very_long_sample_name_that_must_remain_HEKA_v216"
    data = heka / "processed" / "group_003_ChronoPot" / "CA" / "point_001" / "series_001_ChronoPotStat"
    data.mkdir(parents=True)
    csv_path = data / "sweep_001_cycle_001_CA.csv"
    csv_path.write_text("time_s,current_A\n0,1e-6\n", encoding="utf-8")

    report_dir = tmp_path / "A very long generated figures folder containing complete manuscript description"
    report_dir.mkdir()
    report = report_dir / "A very long generated report filename containing all electrochemical conditions.html"
    report.write_text("<html>done</html>", encoding="utf-8")

    aliases = short.shorten_generated_tree(tmp_path, scope="regression")
    assert heka.is_dir()
    assert csv_path.is_file()
    assert any(row["kind"] == "generated_folder" for row in aliases)
    assert any(row["kind"] == "generated_file" for row in aliases)
    assert all("HEKA_v216" not in row["original_name"] for row in aliases)
    assert all("group_003" not in row["original_relative_path"] for row in aliases)


def test_browser_recovers_true_points_from_canonical_key_after_aliasing(tmp_path):
    ca_meta = {
        "technique": "CA",
        "columns": ["time_s", "current_A"],
        "point": 341,
        "method_id": "ChronoPotStat",
        "canonical_key": "Chronoamperometry_CA:hashed_alias/processed/group_341_ChronoPot/CA/point_170/series_001_ChronoPotStat/sweep_001_cycle_001_CA.csv",
    }
    cv_meta = {
        "technique": "CV / LSV",
        "columns": ["potential_V", "current_A"],
        "point": 340,
        "method_id": "TipDown",
        "canonical_key": "CV_LSV:hashed_alias/processed/group_340_NewPoint/Approach_curve/point_170/series_001_TipDown/sweep_001_cycle_001_Approach_curve.csv",
    }
    ca = catalog._normalize_record(tmp_path / "CA_point_341.csv", ca_meta, {}, 1)
    cv = catalog._normalize_record(tmp_path / "CV_point_340.csv", cv_meta, {}, 2)
    repaired = catalog._repair_heka_acquisition_blocks([ca, cv])
    by_tech = {row["technique_key"]: row for row in repaired}
    assert by_tech["CA"]["point"] == 170
    assert by_tech["CV/LSV"]["point"] == 170
    assert by_tech["CA"]["instrument"] == "HEKA"
    assert by_tech["CV/LSV"]["instrument"] == "HEKA"
    assert by_tech["CA"]["point_from_canonical_key"] is True


def test_scilink_bundle_contains_short_name_map_and_request_policy(tmp_path, monkeypatch):
    monkeypatch.setenv(short.ENV_KEY, "1")
    data = tmp_path / "A very long CA data filename containing sample concentration electrode potential repeat and conditions.csv"
    data.write_text("time_s,current_A,applied_potential_V\n0,1e-6,-0.9\n1,5e-7,-0.9\n", encoding="utf-8")
    source = _Source()
    source.output_dir_var = _Var(str(tmp_path))
    source.file_var = _Var(str(data))
    source.short_names_var = _Var(True)
    source.desc_text = None

    job = scilink.write_scilink_job(
        source,
        "Analyze the selected CA transient.",
        [{"path": str(data), "role": "Data file", "source_group": "Current Browser selection"}],
        {},
        requested_filenames=["analysis_report.html"],
    )
    request = json.loads(Path(job["request_path"]).read_text(encoding="utf-8"))
    context_payload = json.loads(Path(request["metadata_paths"][0]).read_text(encoding="utf-8"))
    assert context_payload["short_name_policy"]["enabled"] is True
    assert context_payload["short_name_policy"]["canonical_identity_changed"] is False
    map_path = Path(job["short_name_map_path"])
    assert map_path.is_file()
    assert len(Path(job["job_dir"]).name) <= short.MAX_SUBFOLDER_CHARS
    with zipfile.ZipFile(job["bundle_zip"]) as archive:
        assert "metadata/short_name_map.json" in archive.namelist()


def test_completed_job_postprocess_shortens_results_and_updates_json(tmp_path, monkeypatch):
    monkeypatch.setenv(short.ENV_KEY, "1")
    long_folder = tmp_path / "A very long generated figures folder containing sample chemistry and complete manuscript description"
    long_folder.mkdir()
    long_file = long_folder / "A very long generated figure filename containing all electrochemical conditions and analysis description.html"
    long_file.write_text("<html><body>report</body></html>", encoding="utf-8")
    manifest = tmp_path / "generated_outputs.json"
    manifest.write_text(json.dumps({"report": str(long_file.relative_to(tmp_path)).replace("\\", "/")}), encoding="utf-8")

    receipt = short.finalize_generated_tree(tmp_path, enabled=True, scope="completed_scilink_job")
    assert receipt["renamed_component_count"] == 2
    assert receipt["scientific_schema_names_renamed"] is False
    updated = json.loads(manifest.read_text(encoding="utf-8"))
    assert Path(updated["report"]).parts[0] != long_folder.name


def test_report_watcher_state_follows_postprocessed_html_path(tmp_path, monkeypatch):
    monkeypatch.setenv(short.ENV_KEY, "1")
    report_dir = tmp_path / "session" / "A very long report output folder containing all sample and analysis conditions"
    report_dir.mkdir(parents=True)
    report = report_dir / "A very long completed SciLink analysis report with manuscript details and figures.html"
    report.write_text("<html>done</html>", encoding="utf-8")
    old_key = str(report.resolve())
    state = {"opened": {old_key}, "stats": {old_key: (report.stat().st_size, report.stat().st_mtime_ns, 1)}, "receipts": [{"path": old_key, "opened": True}]}
    receipt = short.finalize_generated_tree(tmp_path, enabled=True, scope="completed_scilink_job")
    scilink_cli._remap_report_state_after_shortening(tmp_path, state, receipt)
    final_reports = scilink_cli._report_html_candidates(tmp_path)
    assert len(final_reports) == 1
    final_key = str(final_reports[0].resolve())
    assert final_key in state["opened"]
