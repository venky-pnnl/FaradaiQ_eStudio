from pathlib import Path
import importlib

ROOT = Path(__file__).resolve().parents[1]


def test_v232_public_entry_points_and_metadata():
    assert (ROOT / "faradaiq_studio_v316.py").exists()
    assert (ROOT / "Start_FaradaIQ_Studio_v320.bat").exists()
    assert (ROOT / "Start_FaradaIQ_Studio_v316.command").exists()
    assert (ROOT / "Start_FaradaIQ_Studio_v316.sh").exists()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.316.0"' in pyproject
    assert 'readme = "README_v316.md"' in pyproject


def test_v232_core_and_jobs_help():
    core = importlib.import_module("faradaiq_studio_core")
    assert core.APP_TITLE == "FaradaIQ Studio v316"
    assert core.FARADAIQ_PUBLIC_VERSION == "v316"
    assert core.FARADAIQ_APP_VERSION == "v316"
    assert callable(core._v233_main_final)
    assert core.SessionReaderGUI._v233_batch_queue_installed is True
    names = [str(x[0]) for x in core._V207_HELP_TABS]
    assert any(name.startswith("v237 parallel jobs") for name in names)


def test_v232_drag_drop_jobs_controls_and_transactions():
    req = (ROOT / "requirements_all.txt").read_text(encoding="utf-8")
    checker = (ROOT / "_check_bridge_dependencies.py").read_text(encoding="utf-8")
    module = (ROOT / "faradaiq" / "v233_batch_jobs.py").read_text(encoding="utf-8")
    assert "tkinterdnd2>=0.4,<1" in req
    assert "tkinterdnd2" in checker
    for label in ("Open jobs", "Run all jobs", "Run selected jobs", "Stop jobs", "Live transactions"):
        assert label in module
    assert "Add selected to queue" not in module
    assert "Remove selected from queue" not in module
    assert "BatchTransactionWindow" in module
    assert "_progress_fraction_from_line" in module
    assert "WeightedProgressTracker" in module
    assert "taskkill" in module
    assert "SPI_GETWORKAREA" in module
    assert "self.selected_actions" in module
