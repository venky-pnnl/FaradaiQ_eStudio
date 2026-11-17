from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from types import SimpleNamespace

import faradaiq.v245_queue_scilink as patch
from faradaiq import v262_technique_scilink as v273
import faradaiq.v239_scilink_edison as old
import faradaiq.v244_workflow_cleanup as cleanup


class FakeOld:
    def __init__(self, root: Path):
        self.root = root

    @staticmethod
    def _safe_name(value):
        return str(value).replace(" ", "_")

    @staticmethod
    def _base_name(_gui):
        return "demo"

    def _job_root(self, _source):
        out = self.root / "jobs"
        out.mkdir(exist_ok=True)
        return out

    @staticmethod
    def _session_metadata_payload(_source):
        return {"session": "metadata"}


class FakeCleanup:
    @staticmethod
    def _service_settings(_cfg):
        return {"llm": {"provider": "Ollama", "model": "demo"}}


def test_minimal_scilink_job_exact_prompt_and_exact_json_keys(tmp_path, monkeypatch):
    data = tmp_path / "data.csv"
    data.write_text("x,y\n1,2\n", encoding="utf-8")
    meta = tmp_path / "map.json"
    meta.write_text("{}", encoding="utf-8")
    support = tmp_path / "paper.pdf"
    support.write_bytes(b"PDF")

    monkeypatch.setattr(old, "_job_root", lambda _source: tmp_path / "jobs")
    monkeypatch.setattr(old, "_base_name", lambda _gui: "demo")
    monkeypatch.setattr(old, "_safe_name", lambda value: str(value).replace(" ", "_"))
    monkeypatch.setattr(cleanup, "_service_settings", lambda _cfg: {"llm": {"provider": "Ollama", "model": "demo"}})
    monkeypatch.setattr(v273, "_selected_record_names", lambda _source: ["CV point 3", "cycle 2"])

    exact = "  exact user prompt — preserve UTF-8 and spaces  "
    selected = [
        {"path": str(data), "role": "Browser-selected data", "source_group": "Current selection"},
        {"path": str(meta), "role": "Canonical JSON manifest", "source_group": "Canonical bundle"},
        {"path": str(support), "role": "Local literature", "source_group": "Literature folder"},
    ]
    job = v273.write_scilink_job(
        object(), exact, selected, {}, mode="Autonomous", requested_filenames=["final_report.md", "plots.csv"]
    )

    assert Path(job["prompt_path"]).read_text(encoding="utf-8") == exact
    request = json.loads(Path(job["manifest_path"]).read_text(encoding="utf-8"))
    assert list(request) == [
        "selected_data_paths",
        "supporting_file_paths",
        "metadata_paths",
        "output_folder",
        "selected_record_names",
        "requested_filenames",
    ]
    job_dir = Path(job["job_dir"]).resolve()
    assert len(request["selected_data_paths"]) == 1
    assert len(request["supporting_file_paths"]) == 1
    assert len(request["metadata_paths"]) == 1
    assert Path(request["metadata_paths"][0]).name == "scilink_bundle_context.json"
    assert all(Path(path).is_file() for path in request["selected_data_paths"] + request["supporting_file_paths"] + request["metadata_paths"])
    assert all(str(Path(path).resolve()).startswith(str(job_dir)) for path in request["selected_data_paths"] + request["supporting_file_paths"] + request["metadata_paths"])
    assert request["selected_record_names"] == ["CV point 3", "cycle 2"]
    assert request["requested_filenames"] == ["final_report.md", "plots.csv"]
    assert json.loads(Path(job["request_path"]).read_text(encoding="utf-8")) == request
    assert Path(job["manifest_path"]) == Path(job["request_path"])
    assert not (job_dir / "files_to_share_manifest.json").exists()
    assert Path(job["bundle_zip"]).is_file()
    settings = json.loads(Path(job["settings_path"]).read_text(encoding="utf-8"))
    assert settings["scilink_mode"] == "autonomous"
    assert settings["scilink_requested_mode"] == "autonomous"
    assert settings["scilink_wait_for_user_questions"] is True
    assert settings["inject_faradaiq_skill_prompt"] is False
    assert settings["handoff_contract"] == "exact_user_prompt_plus_job_local_request_json"
    assert settings["job_local_data_bundle"] is True
    assert settings["scilink_unsafe_execution_ok"] is True
    assert settings["unsafe_execution_authorization_scope"] == "current SciLink CLI subprocess only"
    prompt_text = Path(job["prompt_path"]).read_text(encoding="utf-8")
    for hidden in ("Required behavior", "Analysis requirements", "FaradaIQ data structure", "ASK SCILINK"):
        assert hidden not in prompt_text


def test_background_refresh_never_changes_queue_selection(monkeypatch):
    class FakeModel:
        pass

    class FakeWindow:
        pass

    core = {"BatchQueueModel": FakeModel, "BatchQueueWindow": FakeWindow}
    monkeypatch.setattr(patch, "_CORE", core)
    patch._patch_queue_classes()

    calls = []
    instance = FakeModel()
    instance.events = queue.Queue()
    instance.events.put(("refresh", "running-job-id"))
    instance.events.put(("done", "completed-job-id"))
    instance.running = False
    instance.window = None
    instance.transaction_window = None
    instance.gui = SimpleNamespace(after=lambda *_args: None)
    instance.refresh_window = lambda select_job=None: calls.append(select_job)
    instance.poll_events()
    assert calls == [None, None]


def test_restore_completed_session_preserves_running_state_and_removes_batch_payload(tmp_path, monkeypatch):
    class FakeModel:
        pass

    class FakeWindow:
        pass

    core = {"BatchQueueModel": FakeModel, "BatchQueueWindow": FakeWindow}
    monkeypatch.setattr(patch, "_CORE", core)
    patch._patch_queue_classes()

    session_path = tmp_path / "job.faradaiq_session.json"
    session_path.write_text(json.dumps({"main_vars": {"file": "done"}, "batch_job": {"job_id": "done"}}), encoding="utf-8")
    running = SimpleNamespace(job_id="run", status="Running")
    completed = SimpleNamespace(
        job_id="done", status="Completed", outputs={"browser_cache": {}}, experiment_name="completed", session_json=str(session_path)
    )
    model = FakeModel()
    model.jobs = [running, completed]
    model.pause_after_current = False
    model.stop_after_current = False
    model.running = True
    model.current_job_id = "run"
    model.active_job_ids = {"run"}
    model._run_job_ids = {"run"}
    model._last_run_job_ids = {"run"}
    model.stop_event = threading.Event()
    model.write_job_session = lambda *_args, **_kwargs: session_path

    applied = []
    refreshed = []
    window = FakeWindow()
    window.model = model
    window.core = {"_v177_apply_full_session": lambda _parent, payload, open_browsers=True: applied.append(payload) or True}
    window.parent = SimpleNamespace(lift=lambda: None)
    window.win = object()
    window.messagebox = None
    window.status_var = SimpleNamespace(set=lambda _value: None)
    window.selected_job = lambda: completed
    window.refresh = lambda select_job=None: refreshed.append(select_job)

    window.restore_selected_session()

    assert len(model.jobs) == 2
    assert model.jobs[0] is running
    assert model.running is True
    assert model.current_job_id == "run"
    assert model.active_job_ids == {"run"}
    assert model.pause_after_current is False
    assert model.stop_after_current is False
    assert applied and "batch_job" not in applied[0]
    assert refreshed == ["done"]


def test_cli_defaults_to_copilot_when_waiting_for_user_and_disables_skill_prompt():
    text = Path("faradaiq_scilink_cli_job.py").read_text(encoding="utf-8")
    assert 'return "co-pilot"' in text
    assert 'scilink_wait_for_user_questions' in text
    assert 'FARADAIQ_DISABLE_SKILL_PROMPT' in text
    assert 'SCILINK_DISABLE_SKILL_PROMPT' in text
    assert 'inject_faradaiq_skill_prompt": False' in text
    assert 'add("--mode", _effective_scilink_mode(settings))' in text
