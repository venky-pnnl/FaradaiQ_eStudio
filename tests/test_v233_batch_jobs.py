from __future__ import annotations

import threading
import time
from pathlib import Path

import faradaiq.v233_batch_jobs as batch


class Var:
    def __init__(self, value):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class DummyGUI:
    def __init__(self, root: Path):
        self.output_dir_var = Var(str(root))
    def after(self, _delay, _callback):
        return None


def _job(tmp_path: Path, name: str, status: str = "Queued") -> batch.BatchJob:
    source = tmp_path / f"{name}.dat"
    source.write_text("fixture", encoding="utf-8")
    job = batch.BatchJob(str(source), name, str(tmp_path / f"{name}_output"))
    job.status = status
    job.export_settings.update({
        "run_technique_parsers": False,
        "canonical_h5_json": False,
        "build_browser_cache": False,
    })
    job.drt_settings["enabled"] = False
    return job


def test_completed_source_is_reused_and_not_duplicated(tmp_path):
    model = batch.BatchQueueModel(DummyGUI(tmp_path), {})
    completed = _job(tmp_path, "finished", status="Completed")
    completed.completed_stages = ["inspection", "export", "canonical_bundle", "browser_cache"]
    model.jobs = [completed]

    found = model.find_source(Path(completed.source_path))
    assert found is completed
    result = model.add_paths([completed.source_path], status="Inspecting", open_window=False)
    assert result == [completed]
    assert len(model.jobs) == 1
    assert model.jobs[0].job_id == completed.job_id


def test_parallel_worker_respects_requested_worker_count(tmp_path, monkeypatch):
    model = batch.BatchQueueModel(DummyGUI(tmp_path), {})
    jobs = [_job(tmp_path, f"job_{i}") for i in range(4)]
    model.jobs = jobs
    model._run_job_ids = {job.job_id for job in jobs}
    model.parallel_enabled = True
    model.parallel_workers = 2
    model.running = True

    lock = threading.Lock()
    active = 0
    maximum = 0

    def fake_run(job, _core, progress, **_kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        progress("Running", "export", 50.0, "working")
        time.sleep(0.08)
        job.status = "Completed"
        job.stage = "completed"
        job.progress = 100.0
        with lock:
            active -= 1
        return job

    monkeypatch.setattr(batch, "run_batch_job", fake_run)
    model._worker_loop()
    assert maximum == 2
    assert all(job.status == "Completed" for job in jobs)


def test_finished_job_load_uses_saved_session_instead_of_reinspection(tmp_path):
    job = _job(tmp_path, "cached", status="Completed")
    job.completed_stages = ["inspection", "canonical_bundle", "browser_cache"]
    job.inspection_summary = {"n_entries": 1}
    job.outputs = {"browser_cache": {"cache_root": str(tmp_path / "cache")}}

    class Parent:
        def __init__(self):
            self.file_var = Var("")
            self.output_dir_var = Var("")
            self.inspect_calls = 0
        def load_and_inspect(self):
            self.inspect_calls += 1
        def lift(self):
            pass

    parent = Parent()
    window = batch.BatchQueueWindow.__new__(batch.BatchQueueWindow)
    window.parent = parent
    window.model = type("M", (), {})()
    window.messagebox = None
    window.status_var = Var("")
    window.selected_job = lambda: job
    restored = {"count": 0}
    window.restore_selected_session = lambda: restored.__setitem__("count", restored["count"] + 1)

    batch.BatchQueueWindow.load_selected_in_main(window)
    assert restored["count"] == 1
    assert parent.inspect_calls == 0
    assert "reused" in window.status_var.get().lower()


def test_optional_installer_windows_are_noninteractive_and_close_independently():
    root = Path(__file__).resolve().parents[1]
    dear = (root / "install_deareis_optional.bat").read_text(encoding="utf-8")
    drt = (root / "install_official_pydrttools_optional.bat").read_text(encoding="utf-8")
    launcher = (root / "Start_FaradaIQ_Studio_v320.bat").read_text(encoding="utf-8")
    for text in (dear, drt):
        assert not any(line.strip().lower() == "pause" for line in text.splitlines())
        assert "choice /c" not in text.lower()
        assert "set /p" not in text.lower()
    assert 'start "FaradaIQ DearEIS installer v316"' in launcher
    assert 'start "FaradaIQ pyDRTtools installer v316"' in launcher
    assert 'install_deareis_optional.bat" --auto' in launcher
    assert 'install_official_pydrttools_optional.bat" --auto' in launcher


def test_jobs_window_no_bottom_progress_bar_and_transaction_tabs_present():
    text = Path(batch.__file__).read_text(encoding="utf-8")
    assert "self.notebook = self.ttk.Notebook" in text
    assert "Run jobs in parallel" in text
    assert "Load selected in main GUI" in text
    assert "self.progress = ttk.Progressbar(bottom" not in text
