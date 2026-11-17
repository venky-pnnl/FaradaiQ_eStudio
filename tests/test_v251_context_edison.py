from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path
from types import SimpleNamespace


class Var:
    def __init__(self, value):
        self.value = value
    def get(self):
        return self.value


class TextBox:
    def __init__(self, value=""):
        self.value = value
    def get(self, *_args):
        return self.value
    def delete(self, *_args):
        self.value = ""
    def insert(self, *_args):
        self.value = _args[-1]


def _core(monkeypatch):
    monkeypatch.setenv("FARADAIQ_SKIP_STARTUP_AI_SELECTOR", "1")
    import faradaiq_studio_core as core
    return core


def test_main_context_refresh_writes_three_panes_and_four_share_files(monkeypatch, tmp_path):
    _core(monkeypatch)
    import faradaiq.v239_scilink_edison as workflow
    from faradaiq.v251_context_scilink import refresh_main_context

    (tmp_path / "sample_faradaiq_bundle.h5").write_bytes(b"h5")
    (tmp_path / "sample_faradaiq_bundle.json").write_text(
        json.dumps({"technique_counts": {"CV": 12, "EIS": 3}, "n_unique_points": 4}),
        encoding="utf-8",
    )
    cache = tmp_path / ".browser_cache_from_h5"
    cache.mkdir()
    (cache / "_faradaiq_browser_catalog_v216.json").write_text(json.dumps({"record_count": 15}), encoding="utf-8")

    gui = SimpleNamespace(
        file_var=Var(str(tmp_path / "source.dat")),
        output_dir_var=Var(str(tmp_path)),
        current_analysis_root=str(tmp_path),
        desc_text=TextBox("10 mM ferricyanide; 10 um working area."),
        summary_text=TextBox(),
        structure_text=TextBox(),
        set_status=lambda _message: None,
    )
    manifest = refresh_main_context(gui)
    assert manifest is not None
    assert "10 mM ferricyanide" in gui.desc_text.value
    assert "Detected techniques: CV: 12, EIS: 3" in gui.summary_text.value
    assert "sample_faradaiq_bundle.h5" in gui.structure_text.value
    assert all(Path(path).is_file() for path in manifest["context_files"].values())

    items = workflow.collect_share_candidates(gui, {})
    context = [item for item in items if item.get("source_group") == "Main Studio context"]
    assert len(context) == 4
    assert all(item.get("selected") is False for item in context)
    assert {item["role"] for item in context} == {
        "Main Studio experimental description",
        "Main Studio session summary",
        "Main Studio data structure preview",
        "Main Studio synchronized context manifest",
    }


def test_edison_checked_files_are_packaged_and_audited(monkeypatch, tmp_path):
    _core(monkeypatch)
    from faradaiq import v250_reliability as reliability

    data = tmp_path / "selected.csv"
    data.write_text("E,I\n0,1\n", encoding="utf-8")
    metadata = tmp_path / "metadata.json"
    metadata.write_text('{"sample":"ferricyanide"}', encoding="utf-8")
    source = SimpleNamespace(output_var=Var(str(tmp_path)), file_var=Var(str(tmp_path)), base_name="edison_files")
    job = reliability._write_exact_edison_job(
        source,
        "Analyze the checked files.",
        [
            {"path": str(data), "role": "Browser-selected data", "source_group": "Current selection"},
            {"path": str(metadata), "role": "Metadata", "source_group": "Main Studio context"},
        ],
        {},
        ["analysis.csv"],
    )
    assert job["selected_file_count"] == 2
    assert job["packaged_file_count"] == 2
    manifest = json.loads(Path(job["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["file_count"] == 2
    audit = json.loads(Path(job["zip_audit_path"]).read_text(encoding="utf-8"))
    assert audit["ready"] is True
    assert audit["packaged_file_count"] == 2
    with zipfile.ZipFile(job["zip_path"]) as zf:
        names = zf.namelist()
        assert any(name.endswith("selected.csv") for name in names)
        assert any(name.endswith("metadata.json") for name in names)
        assert "service_settings.json" not in names


def test_edison_cli_uploads_every_checked_file_individually(monkeypatch, tmp_path):
    _core(monkeypatch)
    from faradaiq import v250_reliability as reliability

    one = tmp_path / "one.csv"; one.write_text("x,y\n1,2\n", encoding="utf-8")
    two = tmp_path / "two.txt"; two.write_text("support", encoding="utf-8")
    source = SimpleNamespace(output_var=Var(str(tmp_path)), file_var=Var(str(tmp_path)), base_name="edison_live")
    job = reliability._write_exact_edison_job(
        source,
        "EXACT USER PROMPT",
        [{"path": str(one), "role": "Data"}, {"path": str(two), "role": "Support"}],
        {},
        [],
    )

    fake_root = tmp_path / "fake_edison"
    (fake_root / "edison_client" / "models").mkdir(parents=True)
    (fake_root / "edison_client" / "__init__.py").write_text(textwrap.dedent("""
        import json, os
        class _DS:
            def __init__(self, value): self.id = value
        class _Upload:
            def __init__(self, value, path): self.data_storage = _DS(value); self.path = path
            def model_dump(self): return {'data_storage': {'id': self.data_storage.id}, 'path': self.path}
        class EdisonClient:
            def __init__(self, api_key): self.count = 0
            async def astore_file_content(self, name, file_path, description):
                self.count += 1
                with open(os.environ['FQ_CAPTURE'], 'a', encoding='utf-8') as handle:
                    handle.write(json.dumps({'kind':'upload','path':file_path,'name':name}) + '\\n')
                return _Upload('id' + str(self.count), file_path)
            def create_task(self, request):
                with open(os.environ['FQ_CAPTURE'], 'a', encoding='utf-8') as handle:
                    handle.write(json.dumps({'kind':'task','query':request.query,'uris':request.runtime_config.environment_config['data_storage_uris']}) + '\\n')
                return 'task-1'
            def get_task(self, task_id, verbose=False): return {'status':'success','answer':'ok'}
        class JobNames: ANALYSIS = 'analysis'
    """), encoding="utf-8")
    (fake_root / "edison_client" / "models" / "__init__.py").write_text(
        "from .app import RuntimeConfig, TaskRequest, JobNames\n", encoding="utf-8"
    )
    (fake_root / "edison_client" / "models" / "app.py").write_text(textwrap.dedent("""
        class RuntimeConfig:
            def __init__(self, max_steps, environment_config): self.max_steps=max_steps; self.environment_config=environment_config
        class TaskRequest:
            def __init__(self, name, query, runtime_config): self.name=name; self.query=query; self.runtime_config=runtime_config
        class JobNames: ANALYSIS='analysis'
    """), encoding="utf-8")

    capture = tmp_path / "capture.jsonl"
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(fake_root) + os.pathsep + str(root),
        "EDISON_API_KEY": "test-key",
        "FQ_CAPTURE": str(capture),
        "EDISON_TASK_POLL_SECONDS": "0",
    })
    cp = subprocess.run(
        [sys.executable, str(root / "faradaiq_edison_cli_job.py"), "--job-dir", str(job["job_dir"])],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    events = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
    uploads = [event for event in events if event["kind"] == "upload"]
    assert len(uploads) == 3  # two checked files plus one audit ZIP
    task = next(event for event in events if event["kind"] == "task")
    assert task["query"] == "EXACT USER PROMPT"
    assert len(task["uris"]) == 2
    response = json.loads((Path(job["job_dir"]) / "edison_job_response.json").read_text(encoding="utf-8"))
    assert response["checked_file_count"] == 2
    assert response["uploaded_checked_file_count"] == 2


def test_scilink_ollama_model_tag_is_exact(monkeypatch):
    _core(monkeypatch)
    import faradaiq_scilink_cli_job as cli
    env, report = cli.prepare_scilink_environment(
        {"llm": {"provider": "Ollama", "model": "gpt-oss:120b-cloud", "base_url": "http://localhost:11434/v1"}},
        {},
    )
    assert report["selected_model"] == "gpt-oss:120b-cloud"
    assert env["LITELLM_MODEL"] == "gpt-oss:120b-cloud"
    assert env["SCILINK_LLM_MODEL"] == "gpt-oss:120b-cloud"
    assert cli._model_for_cli("ollama", "gpt-oss:120b-cloud", False) == "gpt-oss:120b-cloud"
