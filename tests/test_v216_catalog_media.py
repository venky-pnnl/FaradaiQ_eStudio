from __future__ import annotations

import csv
import json
from pathlib import Path

from PIL import Image


def _write_csv(path: Path, headers, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader(); w.writerows(rows)


def test_catalog_repairs_old_heka_points_and_scan_rates(tmp_path: Path) -> None:
    from faradaiq.browser_catalog_v209 import build_catalog

    cache = tmp_path / ".browser_cache_from_h5"
    records = []
    extracted = []
    # One Takedata anchor at group 2, four CV scan-rate methods at point 1, one true EIS.
    for gi, title, tech, series, sweep in [
        (2, "NewPoint", "Unknown HEKA", "Takedata", 1),
        (3, "CV-100mV", "CV / LSV", "CV", 1),
        (4, "CV-75mV", "CV / LSV", "CV", 1),
        (5, "CV-50mV", "CV / LSV", "CV", 1),
        (6, "CV-25mV", "CV / LSV", "CV", 1),
        (7, "E-1", "EIS", "EIS frequency Series aggregated by Group", 0),
    ]:
        p = cache / "HEKA" / f"group_{gi:03d}_{title}" / tech.replace(" / ", "_").replace(" ", "_") / "point_001" / f"series_{1 if tech != 'EIS' else 0:03d}_{series}" / f"sweep_{sweep:03d}" / f"g{gi}.csv"
        if tech == "EIS":
            _write_csv(p, ["frequency_Hz", "Zreal_ohm", "Zimag_ohm"], [{"frequency_Hz": 10, "Zreal_ohm": 1, "Zimag_ohm": -1}])
        else:
            _write_csv(p, ["time_s", "potential_V", "current_A"], [{"time_s": 0, "potential_V": 0, "current_A": 0}])
        extracted.append(str(p))
        records.append({"record_type": "table", "technique": tech, "group_index": gi, "group_title": title, "series_index": gi, "series_index_in_group": 1 if tech != "EIS" else 0, "series_title": series, "sweep_index": sweep, "point": 1, "heka_v210": True, "heka_technique": ("CV" if tech == "CV / LSV" else tech), "included_in_h5": True})
    payload = {"manifest": {"data_structure": {"records": records}}, "extracted_files": extracted}
    (cache / "_bundle_source.json").write_text(json.dumps(payload), encoding="utf-8")
    catalog = build_catalog(cache, base_name="fixture")
    assert catalog["technique_counts"]["POSITION"] == 1
    assert catalog["technique_counts"]["CV"] == 4
    assert catalog["method_counts"]["CV"] == 4
    assert sorted({r["scan_rate"] for r in catalog["records"] if r["technique_key"] == "CV"}) == ["100 mV/s", "25 mV/s", "50 mV/s", "75 mV/s"]
    assert catalog["technique_counts"]["EIS"] == 1


def test_canonical_media_roundtrip_and_catalog(tmp_path: Path) -> None:
    import faradaiq_canonical_bundle as bundle
    from faradaiq.browser_catalog_v209 import build_catalog
    from faradaiq.browser_ui_v209 import _load_media_frame

    root = tmp_path / "sample_HEKA_v216"
    table = root / "processed" / "cv.csv"
    _write_csv(table, ["time_s", "potential_V", "current_A"], [{"time_s": 0, "potential_V": 0, "current_A": 0}])
    photos = root / "photos"; photos.mkdir(parents=True)
    a = Image.new("RGB", (32, 20), "red"); b = Image.new("RGB", (32, 20), "blue")
    a.save(photos / "a.png"); a.save(photos / "anim.gif", save_all=True, append_images=[b], duration=80, loop=0)
    manifest = {"schema": "faradaiq-heka-structured-v216", "version": "v273", "source": {"original_source_filename": "sample.dat"}, "summary": {"scan_points": 1}, "technique_counts": {"CV": 1}, "modalities": {}, "hierarchy_counts": {}, "records": [{"relative_csv_path": table.relative_to(root).as_posix(), "technique": "CV / LSV", "heka_technique": "CV", "point": 1, "cycle": 1, "method": 1, "group_index": 1, "group_title": "CV-100mV", "series_index": 1, "series_index_in_group": 1, "series_title": "CV", "sweep_index": 1, "scan_rate_mV_s": 100, "scan_rate": "100 mV/s", "x_position": 1.25e-6, "y_position": 2.5e-6, "z_position": 3.75e-6, "coordinate_status": "decoded physical X/Y/Z from HEKA Takedata", "included_in_h5": True}]}
    (root / "sample_heka_v216_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = bundle.build_canonical_bundle(tmp_path, base_name="sample")
    extracted = bundle.extract_h5_tables_for_browser(result["h5_path"], prefer_reuse=False)
    assert extracted["n_media"] == 2
    catalog = build_catalog(extracted["cache_root"], base_name="sample")
    assert catalog["technique_counts"]["PHOTO"] == 1
    assert catalog["technique_counts"]["GIF"] == 1
    cv = next(r for r in catalog["records"] if r["technique_key"] == "CV")
    assert cv["x_position"] == 1.25e-6 and cv["y_position"] == 2.5e-6 and cv["z_position"] == 3.75e-6
    gif = next(Path(x) for x in extracted["extracted_media"] if x.endswith(".gif"))
    frames, durations, kind = _load_media_frame(gif)
    assert kind == "animated GIF" and len(frames) == 2 and len(durations) == 2


def test_catalog_deduplicates_cache_and_original_drt_suffixes(tmp_path: Path) -> None:
    """Cache extraction must not create or display .csv.csv DRT duplicates."""
    from faradaiq.browser_catalog_v209 import build_catalog

    cache = tmp_path / ".browser_cache_from_h5"
    cached = cache / "DRT" / "run" / "method_01" / "point_001" / "sample_simple_drt_data.csv.csv"
    _write_csv(cached, ["tau_s", "gamma_ohm"], [{"tau_s": 1.0, "gamma_ohm": 2.0}])
    original = tmp_path / "analysis" / "sample_simple_drt_data.csv"
    _write_csv(original, ["tau_s", "gamma_ohm"], [{"tau_s": 1.0, "gamma_ohm": 2.0}])
    payload = {
        "manifest": {"base_name": "sample"},
        "extracted_files": [str(cached)],
    }
    (cache / "_bundle_source.json").parent.mkdir(parents=True, exist_ok=True)
    (cache / "_bundle_source.json").write_text(json.dumps(payload), encoding="utf-8")

    def fallback(*_args, **_kwargs):
        return [{"path": original, "technique": "DRT", "point": 1}]

    catalog = build_catalog(cache, bundle_root=tmp_path, base_name="sample", fallback_collector=fallback)
    drt = [r for r in catalog["records"] if r["technique_key"] == "DRT"]
    assert len(drt) == 1
    assert catalog["technique_counts"]["DRT"] == 1
