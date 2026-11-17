from pathlib import Path


def test_new_session_creates_file_named_tracking_context(tmp_path, monkeypatch):
    import faradaiq_studio_core as core

    class Var:
        def __init__(self, value=""):
            self.value = value
        def get(self):
            return self.value
        def set(self, value):
            self.value = value

    class Text:
        def __init__(self, value=""):
            self.value = value
        def get(self, *_):
            return self.value
        def delete(self, *_):
            self.value = ""
        def insert(self, _where, value):
            self.value = value

    class Collection:
        def __init__(self):
            self.entries = []

    class Gui:
        def __init__(self):
            self.file_var = Var(str(tmp_path / "CuNi scan 01.dat"))
            self.output_dir_var = Var(str(tmp_path))
            self.collection = Collection()
            self.current_summary = {"old": True}
            self.current_analysis_root = "old"
            self.summary_text = Text("old summary")
            self.structure_text = Text("old structure")
            self.desc_text = Text("User description\n\nLOADED_SOURCE_CONTEXT_BEGIN\nold\nLOADED_SOURCE_CONTEXT_END")
            self.status_var = Var("Ready")
        @staticmethod
        def _strip_loaded_context_block(text):
            return text.split("LOADED_SOURCE_CONTEXT_BEGIN", 1)[0].strip()
        def set_status(self, value):
            self.status_var.set(value)
        def title(self, *_):
            pass

    gui = Gui()
    monkeypatch.setenv("FARADAIQ_LAUNCH_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(core, "_v219_rebuild_session_banner", lambda _gui: None)
    monkeypatch.setattr(core, "_v753_apply_title", lambda *_a, **_k: None)
    monkeypatch.setattr(core, "_v753_track_event", lambda *_a, **_k: None)
    monkeypatch.setattr(core, "_v177_find_open_browsers", lambda _gui: [])

    source = tmp_path / "CuNi scan 01.dat"
    source.write_text("stub", encoding="utf-8")
    ctx = core._v219_start_new_session(gui, source, reason="test")

    assert ctx["session_name"] == "CuNi scan 01"
    assert "CuNi_scan_01" in ctx["session_id"]
    assert Path(ctx["session_dir"]).is_dir()
    assert Path(ctx["session_dir"], "session_context.json").exists()
    assert gui.current_summary is None
    assert gui.current_analysis_root == ""
    assert gui.summary_text.value == ""
    assert gui.structure_text.value == ""
    assert gui.desc_text.value == "User description"


def test_load_and_inspect_wrapper_starts_named_session_before_parse():
    text = Path("faradaiq_studio_core.py").read_text(encoding="utf-8")
    start = text.index("def _v219_load_and_inspect")
    end = text.index("try:\n    if callable(_V219_PREV_LOAD_AND_INSPECT)", start)
    block = text[start:end]
    assert "_v219_start_new_session" in block
    assert block.index("_v219_start_new_session") < block.index("_V219_PREV_LOAD_AND_INSPECT")


def test_dependency_checker_requires_interactive_visualization_stack():
    text = Path("_check_bridge_dependencies.py").read_text(encoding="utf-8")
    assert '("altair", "altair>=6.0,<7"' in text
    assert '("plotly", "plotly>=6.0,<7"' in text
    assert '("vl_convert", "vl-convert-python>=1.9,<2"' in text
