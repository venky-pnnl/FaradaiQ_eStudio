from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pandas as pd

import faradaiq_pydrttools_adapter as adapter
import faradaiq_pydrttools_gui_loader as loader


def test_pydrttools_handoff_is_headerless_three_column_and_preserves_sign(tmp_path: Path):
    source = tmp_path / "point_01_EIS_Nyquist.csv"
    pd.DataFrame(
        {
            "Frequency / Hz": [1.0, 100.0, 10.0],
            "Zreal / ohm": [12.0, 10.0, 11.0],
            "-Zimag / ohm": [3.0, 1.0, 2.0],
        }
    ).to_csv(source, index=False)

    result = adapter.create_pydrttools_handoff([source], tmp_path / "handoff")
    assert len(result.strict_files) == 1
    strict = Path(result.strict_files[0])
    rows = pd.read_csv(strict, header=None)
    assert rows.shape == (3, 3)
    assert rows.iloc[:, 0].tolist() == [100.0, 10.0, 1.0]
    # Source column is explicitly -Zimag, so actual complex Zimag must be negative.
    assert rows.iloc[:, 2].tolist() == [-1.0, -2.0, -3.0]
    manifest = json.loads(Path(result.manifest).read_text(encoding="utf-8"))
    assert manifest["strict_files"] == [str(strict)]
    assert manifest["items"][0]["n_points"] == 3


def test_pydrttools_loader_opens_every_selected_spectrum_preloaded(tmp_path: Path, monkeypatch):
    paths = []
    for i in range(2):
        path = tmp_path / f"eis_{i}.csv"
        path.write_text("100,10,-1\n10,11,-2\n", encoding="utf-8")
        paths.append(path)

    class FakeSignal:
        def showMessage(self, *_args, **_kwargs):
            return None

    class FakeWindow:
        instances = []
        def __init__(self):
            self.data = None
            self.shown = False
            self.inductance_called = False
            FakeWindow.instances.append(self)
        def inductance_callback(self):
            self.inductance_called = True
        def setWindowTitle(self, title):
            self.title = title
        def statusBar(self):
            return FakeSignal()
        def show(self):
            self.shown = True

    class FakeEIS:
        @classmethod
        def from_file(cls, path):
            return {"loaded": str(path)}

    class FakeApp:
        current = None
        def __init__(self, _args):
            FakeApp.current = self
        @classmethod
        def instance(cls):
            return cls.current
        def exec_(self):
            return 0

    qtwidgets = types.SimpleNamespace(QApplication=FakeApp, QMessageBox=types.SimpleNamespace(warning=lambda *a, **k: None))
    pyqt5 = types.ModuleType("PyQt5")
    pyqt5.QtWidgets = qtwidgets
    gui_mod = types.ModuleType("pyDRTtools.GUI")
    gui_mod.GUI = FakeWindow
    runs_mod = types.ModuleType("pyDRTtools.runs")
    runs_mod.EIS_object = FakeEIS
    package_mod = types.ModuleType("pyDRTtools")

    monkeypatch.setitem(sys.modules, "PyQt5", pyqt5)
    monkeypatch.setitem(sys.modules, "pyDRTtools", package_mod)
    monkeypatch.setitem(sys.modules, "pyDRTtools.GUI", gui_mod)
    monkeypatch.setitem(sys.modules, "pyDRTtools.runs", runs_mod)

    assert loader.launch(paths) == 0
    assert len(FakeWindow.instances) == 2
    assert all(w.shown and w.inductance_called and w.data for w in FakeWindow.instances)


def test_browser_replaces_native_deareis_button_with_pydrttools_button():
    core = Path(__file__).resolve().parents[1] / "faradaiq_studio_core.py"
    text = core.read_text(encoding="utf-8")
    assert "text='Open native DearEIS GUI'" not in text
    assert text.count("Open selected EIS in pyDRTtools") >= 4
    assert "_v225_open_pydrttools_with_selected" in text


def test_core_selected_eis_button_creates_handoff_and_launches_loader(tmp_path: Path, monkeypatch):
    import faradaiq_studio_core as core

    source = tmp_path / "selected_eis.csv"
    pd.DataFrame(
        {
            "frequency_Hz": [1000.0, 100.0, 10.0],
            "Zreal_ohm": [10.0, 11.0, 12.0],
            "Zimag_ohm": [-1.0, -2.0, -3.0],
        }
    ).to_csv(source, index=False)

    class Browser:
        analyzed_root = tmp_path / "analysis"
        analysis_root = tmp_path / "analysis"
        base_name = "selected_test"
        def __init__(self):
            self.messages = []
            self.statuses = []
        def log(self, message):
            self.messages.append(str(message))
        def set_status(self, message):
            self.statuses.append(str(message))

    launched = []
    class FakeProc:
        pass
    def fake_popen(command, cwd=None, **_kwargs):
        launched.append((list(command), str(cwd or "")))
        return FakeProc()

    browser = Browser()
    monkeypatch.setattr(core, "_v130p_selected_eis_paths", lambda _browser: [source])
    monkeypatch.setattr(core, "_v225_pydrttools_import_ready", lambda: (True, ""))
    monkeypatch.setattr(core._v225_subprocess, "Popen", fake_popen)
    if getattr(core, "_v130p_subprocess", None) is not None:
        monkeypatch.setattr(core._v130p_subprocess, "Popen", fake_popen)
    monkeypatch.setattr(core.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(core.messagebox, "showerror", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))

    result = core._v225_open_pydrttools_with_selected(browser)
    assert result is not None
    assert len(result.strict_files) == 1
    assert Path(result.manifest).exists()
    reopen_bat = Path(result.output_dir, "OPEN_SELECTED_IN_PYDRTTOOLS.bat")
    assert reopen_bat.exists()
    bat_text = reopen_bat.read_text(encoding="utf-8")
    assert "install_official_pydrttools_optional.bat" in bat_text
    assert "--auto" in bat_text
    assert "faradaiq_pydrttools_gui_loader.py" in bat_text
    assert "--manifest" in bat_text
    assert launched
    command, cwd = launched[0]
    assert Path(command[1]).name == "faradaiq_pydrttools_gui_loader.py"
    assert command[2:] == ["--manifest", str(result.manifest)]
    assert cwd == str(result.output_dir)
    assert any("Opening 1 selected EIS" in status for status in browser.statuses)


def test_pydrttools_readiness_probe_is_out_of_process(monkeypatch):
    import faradaiq_studio_core as core

    calls = []
    class Completed:
        returncode = 0
        stdout = "ready"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((list(command), dict(kwargs)))
        return Completed()

    monkeypatch.setattr(core._v225_subprocess, "run", fake_run)
    ready, reason = core._v225_pydrttools_import_ready()
    assert ready is True
    assert reason == ""
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[0] == sys.executable
    assert command[1] == "-c"
    assert "from pyDRTtools.GUI import GUI" in command[2]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 30
