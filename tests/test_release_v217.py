from pathlib import Path
import json, time

import faradaiq_studio_core as core
from faradaiq.runtime_v217 import _palette, _quick_cache_info
from faradaiq.multidim_v216 import _v216_options


def test_public_version_v219():
    assert core.APP_TITLE == "FaradaIQ Studio v316"
    assert core.FARADAIQ_PUBLIC_VERSION == "v316"
    assert hasattr(core, "_v225_main_final")


def test_theme_palette_set():
    for name in ("Dark", "Light", "Slate", "Warm", "Midnight", "High Contrast"):
        p = _palette(name)
        assert p["bg"].startswith("#") and p["surface"].startswith("#")
        assert p["display"] == name


def test_free_zoom_default():
    opts = _v216_options({})
    assert opts["lock_xy_aspect"] is False


def test_quick_cache_reuse(tmp_path: Path):
    h5 = tmp_path / "sample_faradaiq_bundle.h5"
    h5.write_bytes(b"stub")
    cache = tmp_path / ".browser_cache_from_h5"
    cache.mkdir()
    (cache / "table.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    source = {
        "h5_path": str(h5),
        "h5_mtime": h5.stat().st_mtime,
        "n_tables": 1,
        "n_media": 0,
        "extracted_media": [],
        "manifest": {"base_name": "sample"},
    }
    (cache / "_bundle_source.json").write_text(json.dumps(source), encoding="utf-8")
    info = _quick_cache_info(h5)
    assert info and info["quick_reuse"] is True and info["n_tables"] == 1
