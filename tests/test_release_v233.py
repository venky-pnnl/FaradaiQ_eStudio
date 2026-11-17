from pathlib import Path
import importlib

ROOT = Path(__file__).resolve().parents[1]


def test_v233_public_entry_points_and_metadata():
    assert (ROOT / "faradaiq_studio_v316.py").exists()
    assert (ROOT / "Start_FaradaIQ_Studio_v320.bat").exists()
    assert (ROOT / "Start_FaradaIQ_Studio_v316.command").exists()
    assert (ROOT / "Start_FaradaIQ_Studio_v316.sh").exists()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.316.0"' in pyproject
    assert 'readme = "README_v316.md"' in pyproject


def test_v233_core_and_jobs_patch():
    core = importlib.import_module("faradaiq_studio_core")
    assert core.APP_TITLE == "FaradaIQ Studio v316"
    assert core.FARADAIQ_PUBLIC_VERSION == "v316"
    assert core.FARADAIQ_APP_VERSION == "v316"
    assert callable(core._v233_main_final)
    assert core.SessionReaderGUI._v233_batch_queue_installed is True
    names = [str(row[0]) for row in core._V207_HELP_TABS if row]
    assert any(name.startswith("v237 parallel jobs") for name in names)


def test_v233_job_features_are_present():
    module = (ROOT / "faradaiq" / "v233_batch_jobs.py").read_text(encoding="utf-8")
    for token in (
        "Run jobs in parallel",
        "parallel_workers",
        "ThreadPoolExecutor",
        "self.notebook = self.ttk.Notebook",
        "Load selected in main GUI",
        "browser_cache",
        "find_source",
        "No jobs are running.",
    ):
        assert token in module
