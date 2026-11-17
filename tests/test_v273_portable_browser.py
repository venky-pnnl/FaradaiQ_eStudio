from __future__ import annotations

import json
import zipfile
from pathlib import Path

from faradaiq.v273_portable_browser import (
    HTML_NAME,
    MANIFEST_NAME,
    README_NAME,
    write_portable_browser,
)


def _write_csv(path: Path, rows: int = 12) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("time_s,current_A\n" + "\n".join(f"{i},{-i*1e-6}" for i in range(rows)), encoding="utf-8")


def test_portable_browser_uses_relative_paths_and_builds_batch_zip(tmp_path: Path) -> None:
    root = tmp_path / "Experiment copied anywhere"
    analysis = root / "exp_analysis_ready_csv"
    processed = analysis / "sample" / "processed" / "group_001_Test" / "CA" / "point_001" / "series_001_Method" / "sweep_001_cycle_001_CA.csv"
    cache = root / ".browser_cache_from_h5" / "CA" / "method_01" / "point_001" / "CA_point_001_method_01_cycle_01.csv"
    _write_csv(processed)
    _write_csv(cache)
    catalog = {
        "records": [{
            "path": r"C:\Old\Machine\Experiment\.browser_cache_from_h5\CA\method_01\point_001\CA_point_001_method_01_cycle_01.csv",
            "technique_key": "CA",
            "technique_label": "Chronoamperometry (CA)",
            "method_index": 1,
            "method_label": "Method 01 — ChronoPotStat",
            "point": 1,
            "cycle": 1,
            "role": "CA current-time table",
            "columns": ["time_s", "current_A"],
            "canonical_key": "CA:sample/processed/group_001_Test/CA/point_001/series_001_Method/sweep_001_cycle_001_CA.csv",
            "metadata": {
                "relative_path": "sample/processed/group_001_Test/CA/point_001/series_001_Method/sweep_001_cycle_001_CA.csv",
                "n_rows": 12,
                "columns": ["time_s", "current_A"],
            },
        }]
    }
    (root / ".browser_cache_from_h5" / "_faradaiq_browser_catalog_v216.json").write_text(json.dumps(catalog), encoding="utf-8")
    (root / "short_name_map.json").write_text(json.dumps({
        "short_name_policy": {
            "enabled": True,
            "name_aliases": [{"original_name": "Very Long Experiment", "short_name": "exp__12345678", "kind": "folder"}],
        }
    }), encoding="utf-8")
    (root / "exp_faradaiq_bundle.h5").write_bytes(b"H5")

    receipt = write_portable_browser(root, analysis_root=analysis, experiment_name="Scientific Experiment")
    assert Path(receipt["html_path"]).is_file()
    assert (root / MANIFEST_NAME).is_file()
    assert (root / README_NAME).is_file()

    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["portable"] is True
    assert manifest["relative_paths_only"] is True
    assert manifest["dataset_count"] == 1
    assert manifest["batch_count"] == 1
    dataset = manifest["datasets"][0]
    assert dataset["display_name"].startswith("Chronoamperometry")
    assert dataset["physical_path"].startswith("exp_analysis_ready_csv/")
    assert not dataset["physical_path"].startswith("C:")
    assert dataset["original_scientific_path"].startswith("sample/processed/")
    assert manifest["short_names"]["enabled"] is True

    batch = root / manifest["batches"][0]["zip_path"]
    assert batch.is_file()
    with zipfile.ZipFile(batch) as zf:
        names = zf.namelist()
        assert "FaradaIQ_batch_manifest.json" in names
        assert any(name.startswith("data/") for name in names)
        batch_manifest = json.loads(zf.read("FaradaIQ_batch_manifest.json"))
        assert batch_manifest["short_names"]["enabled"] is True
        assert batch_manifest["datasets"][0]["original_scientific_path"].startswith("sample/processed/")

    html_text = (root / HTML_NAME).read_text(encoding="utf-8")
    assert "window.FARADAIQ_MANIFEST" in html_text
    assert "C:\\Old\\Machine" not in html_text
    assert "requires_web_server" in html_text

    moved = tmp_path / "Moved to a different computer path"
    root.rename(moved)
    moved_manifest = json.loads((moved / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert (moved / moved_manifest["datasets"][0]["physical_path"]).is_file()
    assert (moved / moved_manifest["batches"][0]["zip_path"]).is_file()


def test_portable_browser_falls_back_to_filesystem_scan(tmp_path: Path) -> None:
    root = tmp_path / "fallback"
    data = root / "processed" / "CV_LSV" / "method_01" / "point_007" / "cycle_02" / "curve.csv"
    _write_csv(data)
    receipt = write_portable_browser(root, analysis_root=root / "processed", create_batch_zips=False)
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["catalog_source"] == "filesystem_scan"
    assert manifest["dataset_count"] == 1
    assert manifest["datasets"][0]["technique_key"] == "CV_LSV"
    assert manifest["datasets"][0]["point"] == 7
    assert manifest["datasets"][0]["cycle"] == 2


def test_portable_browser_repairs_stale_group_number_points_from_canonical_key(tmp_path: Path) -> None:
    root = tmp_path / "old shortened output"
    analysis = root / "short_analysis_ready_csv"
    ca = analysis / "sample" / "processed" / "group_341_ChronoPot" / "CA" / "point_170" / "series_001_ChronoPotStat" / "sweep_001_cycle_001_CA.csv"
    cache = root / ".browser_cache_from_h5" / "CA" / "method_01" / "point_341" / "CA_point_341_method_01_cycle_01.csv"
    _write_csv(ca)
    _write_csv(cache)
    canonical = "Chronoamperometry_CA:hashed_alias/processed/group_341_ChronoPot/CA/point_170/series_001_ChronoPotStat/sweep_001_cycle_001_CA.csv"
    catalog = {"records": [{
        "path": str(cache),
        "technique_key": "CA",
        "technique_label": "Chronoamperometry (CA)",
        "method_index": 1,
        "method_label": "Method 01 — ChronoPotStat",
        "point": 341,
        "cycle": 1,
        "sweep": 0,
        "canonical_key": canonical,
        "columns": ["time_s", "current_A"],
        "metadata": {
            "relative_path": "sample/processed/group_341_ChronoPot/CA/point_170/series_001_ChronoPotStat/sweep_001_cycle_001_CA.csv",
            "canonical_key": canonical,
            "columns": ["time_s", "current_A"],
        },
    }]}
    catalog_path = root / ".browser_cache_from_h5" / "_faradaiq_browser_catalog_v216.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    receipt = write_portable_browser(root, analysis_root=analysis, create_batch_zips=False)
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    dataset = manifest["datasets"][0]
    assert dataset["point"] == 170
    assert dataset["sweep"] == 1
    assert "Point 170" in dataset["display_name"]
    assert dataset["metadata"]["instrument"] == "HEKA"
    assert dataset["metadata"]["hierarchy_recovered_from_canonical_key"] is True


def test_v273_full_file_sampling_preserves_tail_and_preferred_axes(tmp_path: Path) -> None:
    root = tmp_path / "full sampling"
    data = root / "processed" / "CA" / "point_001" / "long.csv"
    data.parent.mkdir(parents=True)
    data.write_text(
        "time_s,point_index,current_A\n" +
        "\n".join(f"{i},1,{(999.0 if i == 4999 else i * 1e-9)}" for i in range(5000)),
        encoding="utf-8",
    )
    receipt = write_portable_browser(root, analysis_root=root / "processed", create_batch_zips=False)
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    preview = manifest["datasets"][0]["preview"]
    assert preview["status"] == "plot_ready"
    assert preview["total_rows"] == 5000
    assert preview["sampled_rows"] <= 640
    assert preview["rows"][-1][0] == 4999.0
    assert preview["rows"][-1][2] == 999.0
    assert preview["preferred_axes"]["x_column"] == "time_s"
    assert preview["preferred_axes"]["y_column"] == "current_A"


def test_v273_media_are_gallery_items_not_binary_table_previews(tmp_path: Path) -> None:
    root = tmp_path / "media"
    analysis = root / "exp_analysis_ready_csv"
    photo = analysis / "sample" / "photos" / "Mag_dep_12.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"\xff\xd8\xff\xe0not-a-real-jpeg-but-valid-for-indexing")
    cache = root / ".browser_cache_from_h5" / "Media" / "Photos" / "Mag_dep_12.jpg"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(photo.read_bytes())
    catalog = {"records": [{
        "path": str(cache), "source_file": photo.name,
        "technique_key": "PHOTO", "technique_label": "Photos / images", "point": 0,
        "role": "media", "columns": ["binary garbage must not appear"],
        "metadata": {"relative_path": "sample/photos/Mag_dep_12.jpg"},
    }]}
    (root / ".browser_cache_from_h5" / "_faradaiq_browser_catalog_v216.json").write_text(json.dumps(catalog), encoding="utf-8")
    receipt = write_portable_browser(root, analysis_root=analysis, create_batch_zips=False)
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    dataset = manifest["datasets"][0]
    assert dataset["point"] == 12
    assert dataset["columns"] == []
    assert dataset["preview"]["kind"] == "image"
    assert dataset["preview"]["status"] == "image_ready"
    assert manifest["media_count"] == 1  # processed/cache duplicate is deduplicated in Gallery
    html_text = Path(receipt["html_path"]).read_text(encoding="utf-8")
    assert "Compare all points" in html_text
    assert "Photos and figures" in html_text
    assert "Overlay all curves" in html_text
    assert "Waterfall / stacked" in html_text
    assert "Small multiples" in html_text
    assert "Metric versus point" in html_text


def test_v273_comparison_manifest_counts_every_point_curve(tmp_path: Path) -> None:
    root = tmp_path / "all points"
    analysis = root / "exp_analysis_ready_csv"
    records = []
    for point in range(1, 8):
        rel = f"sample/processed/group_{point:03d}_ChronoPot/CA/point_{point:03d}/series_001_ChronoPotStat/sweep_001_cycle_001_CA.csv"
        path = analysis / rel
        _write_csv(path, rows=30)
        cache = root / ".browser_cache_from_h5" / "CA" / "method_01" / f"point_{point:03d}" / f"CA_point_{point:03d}.csv"
        _write_csv(cache, rows=3)
        records.append({
            "path": str(cache), "technique_key": "CA", "technique_label": "Chronoamperometry (CA)",
            "method_index": 1, "method_label": "Method 01 — ChronoPotStat", "point": point,
            "columns": ["time_s", "current_A"], "canonical_key": "CA:" + rel,
            "metadata": {"relative_path": rel, "columns": ["time_s", "current_A"]},
        })
    (root / ".browser_cache_from_h5" / "_faradaiq_browser_catalog_v216.json").write_text(json.dumps({"records": records}), encoding="utf-8")
    receipt = write_portable_browser(root, analysis_root=analysis, create_batch_zips=False)
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    group = next(g for g in manifest["comparison"]["groups"] if g["technique_key"] == "CA")
    assert group["curve_count"] == 7
    assert group["unique_points"] == 7
    assert group["point_min"] == 1
    assert group["point_max"] == 7
    assert all(d["preview"]["total_rows"] == 30 for d in manifest["datasets"])
