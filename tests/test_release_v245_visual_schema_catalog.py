from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import faradaiq_studio_core as core
import faradaiq.v240_visual_schema_catalog as release

ROOT = Path(__file__).resolve().parents[1]


def _make_cache(root: Path) -> tuple[Path, Path, Path]:
    cache = root / ".browser_cache_from_h5"
    table = cache / "CV_LSV" / "point_001" / "cycle_03" / "CV_point_001_cycle_03.csv"
    table.parent.mkdir(parents=True)
    table.write_text("Potential_V,Current_A\n0,0\n", encoding="utf-8")
    h5 = root / "experiment_faradaiq_bundle.h5"
    h5.write_bytes(b"test-h5")
    manifest = root / "experiment_faradaiq_bundle.json"
    manifest.write_text(json.dumps({"techniques_detected": ["CV / LSV"]}), encoding="utf-8")
    (cache / "_bundle_source.json").write_text(json.dumps({
        "h5_path": str(h5),
        "h5_mtime": h5.stat().st_mtime,
        "json_path": str(manifest),
        "n_tables": 1,
        "extract_policy_version": "v216",
    }), encoding="utf-8")
    return cache, h5, manifest


def test_public_v245_entry_and_feature_install():
    assert core.APP_TITLE == "FaradaIQ Studio v316"
    assert core.FARADAIQ_PUBLIC_VERSION == "v316"
    assert hasattr(core, "_v250_main_final")
    assert hasattr(core, "_v240_build_catalog_cached")
    assert hasattr(core, "_v240_add_visual_schema_tab")
    assert (ROOT / "faradaiq_studio_v316.py").exists()
    source = (ROOT / "faradaiq" / "v240_visual_schema_catalog.py").read_text(encoding="utf-8")
    assert "Visual Data Schema" in source
    assert "Block diagram" in source
    assert "CATALOG / INDEX" in source
    assert "Readable view" in source
    assert "Fit all" in source
    assert '"SCILINK"' in source
    assert '"EDISON"' in source
    runtime_source = (ROOT / "faradaiq" / "runtime_v217.py").read_text(encoding="utf-8")
    assert "Loading job-built catalog" in runtime_source
    assert "if (not prebuilt_catalog)" in runtime_source


def test_catalog_is_built_once_then_reused_without_rescanning(tmp_path: Path):
    cache, _h5, _manifest = _make_cache(tmp_path)
    first = release.build_catalog_cached(cache, base_name="experiment")
    second = release.build_catalog_cached(cache, base_name="experiment")
    assert first["record_count"] == 1
    assert first["catalog_reused"] is False
    assert second["catalog_reused"] is True
    assert second["record_count"] == 1
    assert Path(second["catalog_path"]).exists()
    assert release._load_ready_catalog(cache) is not None


def test_changed_h5_invalidates_prebuilt_catalog(tmp_path: Path):
    cache, h5, _manifest = _make_cache(tmp_path)
    first = release.build_catalog_cached(cache, base_name="experiment")
    h5.write_bytes(b"changed-h5-content")
    second = release.build_catalog_cached(cache, base_name="experiment")
    assert first["source_signature"] != second["source_signature"]
    assert second["catalog_reused"] is False


def test_job_does_not_report_completed_before_catalog_is_ready(tmp_path: Path):
    cache, h5, manifest = _make_cache(tmp_path)
    job = SimpleNamespace(
        export_settings={"canonical_h5_json": True, "build_browser_cache": True},
        outputs={
            "browser_cache": {"cache_root": str(cache), "n_tables": 1},
            "canonical_bundle": {"h5_path": str(h5), "json_path": str(manifest)},
            "analysis_ready_root": str(tmp_path),
        },
        completed_stages=[], status="Queued", stage="queued", progress=0.0,
        progress_current=0, progress_total=0, progress_detail="", eta_seconds=None,
        message="", error="", traceback="", finished_at="", job_id="J240",
        base_name="experiment", output_dir=str(tmp_path), touch=lambda: None,
    )
    events: list[tuple[str, str, float, str]] = []
    original = release._ORIGINAL_RUN_BATCH_JOB

    def fake_original(job, _core, report, **_kwargs):
        report("Running", "browser_cache", 99.8, "Browser cache ready | 99.8%")
        job.status = "Completed"
        job.stage = "completed"
        job.progress = 100.0
        report("Completed", "completed", 100.0, "Old premature completion | 100.0%")
        return job

    try:
        release._ORIGINAL_RUN_BATCH_JOB = fake_original
        release.run_batch_job_with_catalog(job, {}, lambda *row: events.append(row))
    finally:
        release._ORIGINAL_RUN_BATCH_JOB = original

    assert events[-1][0:3] == ("Completed", "completed", 100.0)
    assert not any(row[0] == "Completed" for row in events[:-1])
    assert any(row[1] == "browser_catalog" for row in events[:-1])
    assert job.status == "Completed"
    assert "browser_catalog" in job.completed_stages
    assert job.outputs["browser_catalog"]["ready"] is True
    assert Path(job.outputs["browser_catalog"]["ready_marker"]).exists()
    assert "without recataloging" in job.message


def test_visual_model_is_compact_three_column_block_flow(tmp_path: Path):
    cache, h5, manifest = _make_cache(tmp_path)
    release.build_catalog_cached(cache, base_name="experiment")
    browser = SimpleNamespace(
        cache_root=cache,
        h5_path=h5,
        json_path=manifest,
        manifest={"techniques_detected": ["CV / LSV"], "technique_counts": {"CV / LSV": 1}},
        base_name="experiment",
        analysis_root=tmp_path,
        tree=SimpleNamespace(selection=lambda: ()),
        _path_by_iid={},
        _browser_catalog_v209=release._load_ready_catalog(cache),
    )
    blocks, edges = release._schema_model(browser)
    ids = {row["id"] for row in blocks}
    assert len(blocks) == 15
    assert max(int(row["column"]) for row in blocks) == 2
    assert max(int(row["order"]) for row in blocks) == 4
    assert {"source", "parsers", "h5", "json", "cache", "catalog", "techniques", "analysis_tables", "text_prompt", "scilink", "edison"}.issubset(ids)
    assert ("cache", "catalog", "index") in edges
    assert ("catalog", "text_prompt", "schema") in edges
