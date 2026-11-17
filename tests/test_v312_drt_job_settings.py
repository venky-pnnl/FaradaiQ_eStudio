from __future__ import annotations

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


def _editor_vars(window, *, regularization="2.5E-3", samples="3456"):
    window.drt_enabled_var = Var(True)
    window.drt_vars = {
        "rbf_type": Var("gaussian"),
        "data": Var("combined"),
        "derivative": Var("2"),
        "inductance": Var("without"),
        "regularization": Var(regularization),
        "shape_control": Var("fwhm"),
        "coefficient": Var("0.7"),
        "sample_number": Var(samples),
        "run_id": Var("shared"),
    }
    window.run_type_vars = {"simple": Var(True), "bayesian": Var(False), "bht": Var(False)}
    window.plot_type_vars = {k: Var(k in {"eis", "drt"}) for k in ("eis", "magnitude", "phase", "re", "im", "drt", "score")}
    window.export_type_vars = {"drt": Var(True), "eis": Var(False)}


def _job(tmp_path: Path, name: str, status="Draft"):
    src = tmp_path / f"{name}.dat"
    src.write_text("fixture", encoding="utf-8")
    job = batch.BatchJob(str(src), name, str(tmp_path / f"{name}_out"))
    job.status = status
    return job


def test_switching_jobs_remembers_unsaved_drt_settings(tmp_path):
    model = batch.BatchQueueModel(DummyGUI(tmp_path), {})
    first = _job(tmp_path, "first")
    second = _job(tmp_path, "second")
    model.jobs = [first, second]
    model.write_job_session = lambda *_a, **_k: Path(tmp_path / "session.json")
    model.autosave_queue = lambda: Path(tmp_path / "queue.json")

    window = batch.BatchQueueWindow.__new__(batch.BatchQueueWindow)
    window.model = model
    window._editing_job_id = first.job_id
    window.messagebox = None
    _editor_vars(window, regularization="4.2E-3", samples="4321")
    window.selected_job = lambda: second
    loaded = []
    window._load_editor = lambda job: (loaded.append(job.job_id), setattr(window, "_editing_job_id", job.job_id))
    window._update_queue_toggle_label = lambda: None

    batch.BatchQueueWindow._on_select(window)

    assert first.drt_settings["regularization"] == "4.2E-3"
    assert first.drt_settings["sample_number"] == "4321"
    assert first.drt_settings["derivative"] == "2"
    assert model.drt_defaults["regularization"] == batch.default_drt_settings()["regularization"]
    assert loaded == [second.job_id]


def test_apply_drt_to_all_jobs_copies_settings_and_skips_active(tmp_path):
    model = batch.BatchQueueModel(DummyGUI(tmp_path), {})
    jobs = [_job(tmp_path, "a"), _job(tmp_path, "b"), _job(tmp_path, "active", status="Running")]
    model.jobs = jobs
    model.write_job_session = lambda *_a, **_k: Path(tmp_path / "session.json")
    model.autosave_queue = lambda: Path(tmp_path / "queue.json")

    window = batch.BatchQueueWindow.__new__(batch.BatchQueueWindow)
    window.model = model
    window._editing_job_id = jobs[0].job_id
    _editor_vars(window, regularization="9E-4", samples="2500")
    window.refresh = lambda **_k: None

    changed, skipped = batch.BatchQueueWindow._apply_drt_to_jobs(window, model.jobs)

    assert (changed, skipped) == (2, 1)
    assert jobs[0].drt_settings["regularization"] == "9E-4"
    assert jobs[1].drt_settings["regularization"] == "9E-4"
    assert jobs[1].drt_settings["sample_number"] == "2500"
    assert jobs[2].drt_settings["regularization"] == batch.default_drt_settings()["regularization"]
    assert model.drt_defaults["regularization"] == "9E-4"


def test_new_jobs_inherit_remembered_queue_drt_defaults(tmp_path):
    model = batch.BatchQueueModel(DummyGUI(tmp_path), {})
    model.drt_defaults.update({"regularization": "7.5E-4", "sample_number": "2800", "run_id": "remembered"})
    src = tmp_path / "new_source.dat"
    src.write_text("fixture", encoding="utf-8")

    [job] = model.add_paths([src], open_window=False)

    assert job.drt_settings["regularization"] == "7.5E-4"
    assert job.drt_settings["sample_number"] == "2800"
    assert job.drt_settings["run_id"] == "remembered"


def test_queue_json_persists_drt_defaults_backward_compatibly(tmp_path):
    model = batch.BatchQueueModel(DummyGUI(tmp_path), {})
    model.drt_defaults.update({"regularization": "6E-4", "sample_number": "3100"})
    path = tmp_path / "jobs.json"
    model.save_queue_as(path)

    loaded = batch.BatchQueueModel(DummyGUI(tmp_path), {})
    loaded.load_queue(path)

    assert loaded.drt_defaults["regularization"] == "6E-4"
    assert loaded.drt_defaults["sample_number"] == "3100"
