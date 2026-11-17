from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from faradaiq import v254_contour_prerender as pre


class Browser:
    def __init__(self, root: Path, records):
        self.base_name = "demo"
        self.root = root
        self.records = records
        self.messages = []
    def after(self, _delay, callback):
        callback()
    def log(self, message):
        self.messages.append(message)
    def set_status(self, message):
        self.messages.append(message)


def test_hyperspectral_opens_only_after_all_outputs_are_ready(tmp_path, monkeypatch):
    source = tmp_path / "source.csv"
    source.write_text("x,y\n0,1\n1,2\n", encoding="utf-8")
    browser = Browser(tmp_path, [{"path": str(source), "technique_key": "DRT", "point": 1}])
    opened = []
    build_state = {"complete": False, "calls": 0}

    fake_md = SimpleNamespace()
    fake_md._v216_options = lambda options=None: dict(options or {})
    fake_md._view_dir = lambda _browser: tmp_path
    fake_md._slug = lambda value: str(value).replace(" ", "_")
    fake_md._curve_groups = lambda curves, min_size=2: [("DRT method", curves)]
    fake_md._write_master_index = lambda *args, **kwargs: None
    fake_md._open_html_process = lambda path: opened.append((Path(path), build_state["complete"]))
    fake_md.generate = lambda *args, **kwargs: {"delegated": True}

    def make_group(_browser, _items, _label, _suffix, _options):
        build_state["calls"] += 1
        html = tmp_path / "prepared.html"
        cube = tmp_path / "prepared.cube.npz"
        meta = tmp_path / "prepared.visualization.json"
        html.write_text("<html>all frames serialized</html>", encoding="utf-8")
        cube.write_bytes(b"cube")
        meta.write_text("{}", encoding="utf-8")
        build_state["complete"] = True
        return {"html": str(html), "cube": str(cube), "metadata": str(meta), "total_slider_frames": 17}
    fake_md._make_hyperspectral_group = make_group

    monkeypatch.setattr(pre, "_selected_records", lambda _browser: list(browser.records))
    monkeypatch.setattr(pre, "_cache_root", lambda _browser, _md: tmp_path / "cache")
    import faradaiq.v228_intelligence as intel
    monkeypatch.setattr(intel, "_curves_from_records", lambda _browser, _records: [object(), object()])

    result = pre._build_hyperspectral(browser, "Auto", {}, browser.records, fake_md)
    assert build_state["complete"] is True
    assert result["pre_rendered_before_open"] is True
    assert result["pre_rendered_frame_count"] == 17
    assert opened == []


def test_cache_manifest_reuses_prepared_view(tmp_path):
    html = tmp_path / "cached.html"
    html.write_text("ready", encoding="utf-8")
    result = {"html": str(html), "total_slider_frames": 12, "pre_rendered": True}
    manifest = tmp_path / "cache.json"
    manifest.write_text(json.dumps({"schema": pre._CACHE_SCHEMA, "cache_key": "abc", "result": result}), encoding="utf-8")
    loaded = pre._read_cache(manifest, "abc")
    assert loaded is not None
    assert loaded["html"] == str(html)
    assert pre._frame_count(loaded) == 12


def test_invalid_cache_is_rejected(tmp_path):
    manifest = tmp_path / "cache.json"
    manifest.write_text(json.dumps({"schema": pre._CACHE_SCHEMA, "cache_key": "abc", "result": {"html": str(tmp_path / "missing.html")}}), encoding="utf-8")
    assert pre._read_cache(manifest, "abc") is None


def test_source_cache_key_changes_when_file_changes(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("a", encoding="utf-8")
    browser = Browser(tmp_path, [])
    records = [{"path": str(source), "technique_key": "DRT"}]
    key1, _ = pre._cache_key(browser, records, "Auto", {"render_mode": "Contour + points"})
    source.write_text("a much longer file", encoding="utf-8")
    key2, _ = pre._cache_key(browser, records, "Auto", {"render_mode": "Contour + points"})
    assert key1 != key2


def test_release_identity_is_v290():
    import faradaiq
    assert faradaiq.APP_VERSION == "v316"
    assert faradaiq.__version__ == "0.316.0"
    root = Path(__file__).resolve().parents[1]
    assert (root / "Install_FaradaIQ_Studio_v320.bat").is_file()
    assert (root / "Start_FaradaIQ_Studio_v320.bat").is_file()
    assert (root / "faradaiq_studio_v316.py").is_file()
