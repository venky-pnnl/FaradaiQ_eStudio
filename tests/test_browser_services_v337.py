import hashlib
import json
from pathlib import Path
import sys
import threading
import zipfile

import pytest

from faradaiq import browser_services as services


def test_local_review_stages_only_selected_files_and_unchanged_prompt(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "skip.csv").write_bytes(b"do not stage")
    selected = sources / "Cell EIS.csv"
    selected.write_bytes(b"frequency_hz,zreal_ohm,zimag_ohm\n1000,5,-1\n500,8,-3\n")
    prompt = "Review NiCo \N{GREEK SMALL LETTER OMEGA}.\n\nKeep all rows.\n"
    review = services.prepare_review(tmp_path / "jobs", "scilink", prompt, [("Data", selected)])

    assert review.prompt.read_bytes() == prompt.encode("utf-8")
    assert [item.label for item in review.files] == [selected.name]
    assert review.files[0].path.read_bytes() == selected.read_bytes()
    assert review.files[0].digest == hashlib.sha256(selected.read_bytes()).hexdigest()
    assert not list(review.folder.rglob("skip.csv"))
    assert not (review.folder / "submission.json").exists()


def test_invalid_or_modified_inputs_cannot_be_submitted(tmp_path):
    with pytest.raises(ValueError, match="prompt"):
        services.prepare_review(tmp_path, "edison", "\n", [])
    with pytest.raises(ValueError, match="HTTPS"):
        services.validate_endpoint("http://example.test/v1")
    with pytest.raises(ValueError, match="URL"):
        services.validate_endpoint("https://username:password@example.test/v1")
    review = services.prepare_review(tmp_path, "edison", "Original", [])
    review.prompt.write_text("Modified", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        services.run_reviewed(review, {}, "mock-edison-api-value")
    assert not (review.folder / "submission.json").exists()


def test_scilink_worker_uses_documented_headless_flags_and_reviewed_local_files(tmp_path, monkeypatch):
    cli = tmp_path / "packages/scilink/cli"
    cli.mkdir(parents=True)
    (cli / "analyze.py").write_text('''
import json, os
from pathlib import Path
import sys

def main(argv):
    v = lambda option: argv[argv.index(option)+1]
    data = Path(v('--data'))
    metadata = Path(v('--metadata'))
    assert data.is_file() and metadata.is_file()
    assert data.name == 'data.csv' and metadata.name == 'metadata.json'
    assert '--yes' in argv and v('--mode') == 'autonomous'
    assert '--skills' in argv and Path(v('--skills')).is_file()
    assert v('--output-format') == 'json'
    assert v('--base-url') == 'http://127.0.0.1:11434/v1' and v('--model') == 'gemma4:31b'
    assert os.environ['SCILINK_API_KEY'] == 'mock-per-run-credential'
    assert 'EDISON_API_KEY' not in os.environ and 'UNSAFE_EXECUTION_OK' not in os.environ
    print(json.dumps({'status': 'success', 'summary': v('-p'), 'data': data.read_text()}))
    return 0
''', encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "packages"))
    monkeypatch.setenv("EDISON_API_KEY", "unrelated-inherited-value")
    monkeypatch.setenv("UNSAFE_EXECUTION_OK", "true")
    table = tmp_path / "data.csv"
    table.write_text("x,y\n1,2\n", encoding="utf-8")
    meta = tmp_path / "metadata.json"
    meta.write_text('{"sample":"Ni"}', encoding="utf-8")
    prompt = "Review the EIS \N{GREEK SMALL LETTER OMEGA} values.\n\nShow the assumptions.\n"
    review = services.prepare_review(tmp_path / "jobs", "scilink", prompt, [("Data", table), ("Metadata", meta)])
    result = services.run_reviewed(review, {"python": sys.executable, "allow_code": True},
                                   "mock-per-run-credential", cancel_event=threading.Event())
    response = json.loads(Path(result["response"]).read_text(encoding="utf-8"))
    assert response == {"status": "success", "summary": prompt, "data": table.read_text()}
    assert json.loads((review.folder / "submission.json").read_text())["state"] == "completed"
    assert all("mock-per-run-credential" not in path.read_text(errors="ignore")
               for path in review.folder.rglob("*") if path.is_file())
    with pytest.raises(RuntimeError, match="already"):
        services.run_reviewed(review, {"python": sys.executable, "allow_code": True}, "mock-per-run-credential")


def test_edison_uses_shared_audited_job_writer_and_expected_worker(tmp_path, monkeypatch):
    selected = tmp_path / "Ni.csv"
    selected.write_text("t,i\n1,3\n", encoding="utf-8")
    review = services.prepare_review(tmp_path / "jobs", "edison", "Exact prompt\n", [("Data", selected)])

    def fake_worker(command, folder, environment, **_):
        assert command[-2:] == ["--job-dir", str(folder)]
        assert command[1].endswith("faradaiq_edison_cli_job.py")
        assert environment["EDISON_PLATFORM_API_KEY"] == "mock-edison-credential"
        manifest = json.loads((folder / "files_to_share_manifest.json").read_text())
        assert manifest["file_count"] == 1
        assert Path(manifest["files"][0]["path"]).read_bytes() == selected.read_bytes()
        assert Path(manifest["files"][0]["path"]).is_relative_to(review.folder)
        archive = next(folder.glob("*_for_Edison.zip"))
        with zipfile.ZipFile(archive) as payload:
            assert payload.read("analysis_prompt.md") == b"Exact prompt\n"
            assert payload.read(next(path for path in payload.namelist() if path.startswith("shared_files/"))) == selected.read_bytes()
            assert "service_settings.json" not in payload.namelist()
        log = folder / "service.log"
        log.touch()
        response = folder / "edison_job_response.json"
        response.write_text('{"status":"completed","trajectory_id":"mock"}')
        return 0

    monkeypatch.setattr(services, "_execute_worker", fake_worker)
    result = services.run_reviewed(review, {}, "mock-edison-credential")
    assert json.loads(Path(result["response"]).read_text())["trajectory_id"] == "mock"
    assert "mock-edison-credential" not in (review.folder / "service_settings.json").read_text()


def test_edison_worker_runs_real_transaction_against_isolated_client(tmp_path, monkeypatch):
    client = tmp_path / "packages/edison_client"
    models = client / "models"
    models.mkdir(parents=True)
    (models / "app.py").write_text('''
from types import SimpleNamespace
RuntimeConfig = SimpleNamespace
TaskRequest = SimpleNamespace
''', encoding="utf-8")
    (client / "__init__.py").write_text('''
import os
from pathlib import Path

class JobNames:
    ANALYSIS = 'analysis'

class EdisonClient:
    def __init__(self, api_key):
        assert api_key == 'mock-edison-per-run' and 'SCILINK_API_KEY' not in os.environ
        self.uploads = []
        self.key = api_key

    async def astore_file_content(self, *, name, file_path, description):
        path = Path(file_path)
        self.uploads.append(path.name)
        if path.suffix == '.csv':
            assert path.read_bytes() == b'x,y\\n1,2\\n'
        return {'data_storage': {'id': str(len(self.uploads))}, 'test_diagnostic': self.key}

    def create_task(self, request):
        assert request.query == 'Line 1\\r\\nLine 2\\r\\n'
        assert self.uploads == ['select.csv', 'reviewed_for_Edison.zip']
        assert request.runtime_config.environment_config['data_storage_uris'] == ['data_entry:1']
        return 'mock-edison-task'

    def get_task(self, task_id, verbose=False):
        return {'status': 'success', 'answer': 'Reviewed selected data'}
''', encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "packages"))
    monkeypatch.setenv("SCILINK_API_KEY", "mock-unrelated-scilink")
    selected = tmp_path / "select.csv"
    selected.write_bytes(b"x,y\n1,2\n")
    review = services.prepare_review(tmp_path / "jobs", "edison", "Line 1\r\nLine 2\r\n", [("Data", selected)])
    response = services.run_reviewed(review, {}, "mock-edison-per-run")
    payload = json.loads(Path(response["response"]).read_text())
    assert payload["task_status"] == "success" and payload["uploaded_checked_file_count"] == 1
    assert json.loads((review.folder / "edison_task_receipt.json").read_text())["trajectory_id"] == "mock-edison-task"
    assert "mock-edison-per-run" not in (review.folder / "edison_upload_receipts.json").read_text()


def test_labric_test_is_explicit_and_masks_credential_in_diagnostics(monkeypatch):
    from faradaiq import v261_labric_mcp

    result = {"status": "error", "error": "received mock-labric-credential"}
    monkeypatch.setattr(v261_labric_mcp, "test_connection", lambda url, key: result)
    checked = services.test_labric("https://labric.example.test/mcp", "mock-labric-credential")
    assert checked == {"status": "error", "error": "received [redacted]"}


def test_labric_is_attached_to_scilink_only_for_reviewed_runs(tmp_path):
    review = services.prepare_review(tmp_path, "scilink", "Inspect the observations", [])
    env = {}
    command, _ = services._scilink_worker(review, {"allow_code": True}, "", env)
    assert "--mcp" not in command and "LABRIC_API_KEY" not in env
    config = {"allow_code": True, "use_labric": True,
              "labric_endpoint": "https://labric.example.test/mcp", "labric_key": "mock-labric-per-run"}
    command, _ = services._scilink_worker(review, config, "", env)
    payload = json.loads(Path(command[command.index("--mcp") + 1]).read_text())
    assert payload == {"name": "labric", "transport": "http", "url": config["labric_endpoint"],
                       "headers": {"Authorization": "Bearer ${LABRIC_API_KEY}"}}
    assert env["LABRIC_API_KEY"] == config["labric_key"]


def test_browser_service_review_authorization_is_explicit(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from faradaiq import browser_service_ui as ui

    calls = []
    monkeypatch.setattr(ui, "run_reviewed", lambda *args, **kwargs: calls.append(args))
    app = AppTest.from_string(f'''
import pathlib
import threading
import streamlit as st
from faradaiq.browser_service_ui import show_direct_services

def submit(label, operation, **kwargs):
    st.session_state[label] = operation(threading.Event(), lambda message: None)

show_direct_services(workspace=lambda: pathlib.Path({str(tmp_path)!r}), submit=submit,
                     busy=lambda: False, show_file=lambda *args: None)
''').run()
    editor = next(node for node in app.text_area if node.label == "Exact analysis prompt")
    editor.set_value("Review the transport.")
    next(node for node in app.button if node.label == "Prepare local review").click().run()
    assert not app.exception and not calls
    start = lambda: next(node for node in app.button if node.label == "Start reviewed SciLink job")
    assert start().proto.disabled
    next(node for node in app.checkbox if node.label.startswith("I reviewed")).check().run()
    assert start().proto.disabled and not calls
    next(node for node in app.checkbox if node.label.startswith("Allow SciLink to run")).check().run()
    assert not start().proto.disabled and not calls
    start().click().run()
    assert not app.exception and len(calls) == 1
    assert calls[0][0].prompt.read_text() == "Review the transport."
