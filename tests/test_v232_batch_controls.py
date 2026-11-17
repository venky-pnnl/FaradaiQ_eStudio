from pathlib import Path
from types import SimpleNamespace
import threading

import faradaiq.v232_batch_jobs as batch


class Var:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class DummyGUI:
    def __init__(self, root): self.output_dir_var = Var(str(root))
    def after(self, _delay, callback): return callback()


class FakeCollection:
    def __init__(self):
        self.mode = "single"
        self.entries = []
        self.source_path = ""
    def load(self, path):
        self.source_path = str(path)
        self.entries = [{"entry_name": Path(path).name, "parser": SimpleNamespace(session={"raw": True})}]
    def summarize(self): return {"source_file": self.source_path, "n_measurements": 1}
    def build_parsed_npy_bundle(self, description): return {"description": description, "values": [1]}
    def build_summary_json(self, description): return {"description": description}
    def export_analysis_ready_csv(self, output_dir, base_name):
        root = Path(output_dir) / f"{base_name}_analysis_ready_csv"
        root.mkdir(parents=True, exist_ok=True)
        (root / "cv.csv").write_text("potential_V,current_A\n0,0\n", encoding="utf-8")
        manifest = Path(output_dir) / f"{base_name}_analysis_ready_manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        return {"analysis_ready_root": root, "manifest_path": manifest, "n_csv_files": 1}


def _simple_job(tmp_path, name="job"):
    src = tmp_path / f"{name}.pssession"
    src.write_text("fixture", encoding="utf-8")
    job = batch.BatchJob(str(src), name, str(tmp_path / f"{name}_out"))
    job.export_settings.update({
        "run_technique_parsers": False,
        "canonical_h5_json": False,
        "build_browser_cache": False,
    })
    job.drt_settings["enabled"] = False
    return job


def test_stage_weighted_progress_is_monotonic_and_finishes_at_100(tmp_path):
    job = _simple_job(tmp_path)
    events = []
    batch.run_batch_job(job, {"PSSessionCollection": FakeCollection}, lambda *row: events.append(row))
    values = [float(row[2]) for row in events]
    assert values == sorted(values)
    assert values[-1] == 100.0
    assert any("elapsed" in row[3].lower() for row in events)
    assert any(row[1] == "export" and row[2] > 20 for row in events)
    assert job.progress == 100.0
    assert job.finished_at


def test_worker_runs_only_selected_job_ids(tmp_path, monkeypatch):
    model = batch.BatchQueueModel(DummyGUI(tmp_path), {})
    one = _simple_job(tmp_path, "one"); two = _simple_job(tmp_path, "two")
    one.status = two.status = "Queued"
    model.jobs = [one, two]

    def fake_run(job, _core, progress, **_kwargs):
        progress("Completed", "completed", 100.0, "done")
        job.status = "Completed"; job.progress = 100.0
        return job

    monkeypatch.setattr(batch, "run_batch_job", fake_run)
    model._run_job_ids = {one.job_id}
    model.running = True
    model._worker_loop()
    assert one.status == "Completed"
    assert two.status == "Queued"


def test_stop_jobs_cancels_queued_and_terminates_active(tmp_path):
    model = batch.BatchQueueModel(DummyGUI(tmp_path), {})
    running = _simple_job(tmp_path, "running"); queued = _simple_job(tmp_path, "queued")
    running.status = "Running"; queued.status = "Queued"
    model.jobs = [running, queued]

    class FakeProcess:
        pid = 999999
        def __init__(self): self.terminated = False; self.killed = False
        def poll(self): return None
        def terminate(self): self.terminated = True
        def wait(self, timeout=None): return 0
        def kill(self): self.killed = True

    proc = FakeProcess()
    model._active_processes[running.job_id] = proc
    model.stop_all_exports()
    assert model.stop_event.is_set()
    assert running.status == "Stopping"
    assert queued.status == "Cancelled"
    assert proc.terminated or proc.killed


def test_cancel_event_stops_before_export(tmp_path):
    job = _simple_job(tmp_path, "cancel")
    event = threading.Event(); event.set()
    try:
        batch.run_batch_job(job, {"PSSessionCollection": FakeCollection}, lambda *_: None, cancel_event=event)
    except batch.ExportCancelled:
        pass
    else:
        raise AssertionError("Expected ExportCancelled")


def test_queue_remove_toggle_preserves_job_session_and_settings(tmp_path):
    model = batch.BatchQueueModel(DummyGUI(tmp_path), {})
    job = _simple_job(tmp_path, "toggle")
    model.jobs = [job]
    model.enqueue([job.job_id])
    assert job.status == "Queued"
    session_path = Path(job.session_json)
    export_before = dict(job.export_settings)
    changed = model.dequeue([job.job_id])
    assert changed == [job]
    assert job.status == "Draft"
    assert job.export_settings == export_before
    assert session_path.exists()
    assert "preserved" in job.message.lower()


def test_external_progress_parser_understands_percent_and_ratio():
    assert batch._progress_fraction_from_line("processed 83%") == 0.83
    assert batch._progress_fraction_from_line("step 6/10") == 0.6
    assert batch._progress_fraction_from_line("6 of 8 files") == 0.75
    assert batch._progress_fraction_from_line("potential -0.6 V") is None
    assert batch._progress_fraction_from_line("12/3 invalid") is None
