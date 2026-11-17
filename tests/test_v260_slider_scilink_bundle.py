from __future__ import annotations

import io
import json
import os
import threading
from pathlib import Path

import faradaiq.v262_technique_scilink as v273
import faradaiq_scilink_cli_job as cli


class FakeText:
    def __init__(self, text: str):
        self.text = text

    def get(self, *_args):
        return self.text


class FakeTree:
    def __init__(self, selection=("node",)):
        self._selection = selection

    def selection(self):
        return self._selection

    def item(self, _iid):
        return {"text": "Point 021", "values": ("point", "")}


class FakeGui:
    def __init__(self, description: str = "10 mM ferricyanide, 10 um disk electrode"):
        self.base_name = "demo_selection"
        self.desc_text = FakeText(description)


class FakeBrowser:
    def __init__(self, root: Path, paths: list[Path], description: str = "10 mM ferricyanide, 10 um disk electrode"):
        self.analysis_root = root
        self.cache_root = root
        self.base_name = "demo_selection"
        self.tree = FakeTree()
        self._paths = paths
        self._path_by_iid = {}
        self.gui = FakeGui(description)

    def selected_paths(self, plottable_only=False):
        return list(self._paths)


def test_review_candidates_auto_selects_bundle_and_context_json(tmp_path: Path):
    csv_path = tmp_path / "CV_point_021_scanrate_100mVs_cycle_01.csv"
    csv_path.write_text("E_V,I_A\n0,1e-9\n", encoding="utf-8")
    browser = FakeBrowser(tmp_path, [csv_path])
    existing = [
        {"path": str(csv_path), "role": "Browser-selected data", "source_group": "Current selection", "selected": False},
        {"path": str(tmp_path / "other.json"), "role": "Canonical JSON manifest", "source_group": "Canonical bundle", "selected": False},
    ]
    rows = v273._review_candidates(browser, existing)
    assert rows[0]["generated"] == "browser_selection_bundle"
    assert rows[0]["selected"] is True
    assert rows[0]["bundle_source_paths"] == [str(csv_path.resolve())]
    assert rows[1]["generated"] == "browser_selection_context_json"
    assert rows[1]["selected"] is True
    assert all(row.get("source_group") != "Current selection" for row in rows[2:])
    assert all(row.get("selected") is False for row in rows[2:])


def test_scilink_job_uses_one_context_json_and_process_local_authorization(tmp_path: Path, monkeypatch):
    csv_a = tmp_path / "CV_point_021_scanrate_100mVs_cycle_01.csv"
    csv_b = tmp_path / "CV_point_021_scanrate_50mVs_cycle_01.csv"
    csv_a.write_text("E_V,I_A\n0,1e-9\n1,2e-9\n", encoding="utf-8")
    csv_b.write_text("E_V,I_A\n0,2e-9\n1,3e-9\n", encoding="utf-8")
    browser = FakeBrowser(tmp_path, [csv_a, csv_b], "Exact experiment description from Main Studio")

    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup

    monkeypatch.setattr(old, "_job_root", lambda _source: tmp_path / "jobs")
    monkeypatch.setattr(old, "_base_name", lambda _gui: "demo")
    monkeypatch.setattr(old, "_safe_name", lambda value: str(value))
    monkeypatch.setattr(cleanup, "_service_settings", lambda _cfg: {"llm": {"provider": "Ollama", "model": "demo", "base_url": "http://localhost:11434/v1"}})

    selected = [v273._bundle_candidate(browser)]
    job = v273.write_scilink_job(
        browser,
        "Calculate D and khet",
        selected,
        {"scilink_unsafe_execution_ok": True},
        mode="Autonomous",
        unsafe_execution_ok=True,
    )
    request = json.loads(Path(job["request_path"]).read_text(encoding="utf-8"))
    settings = json.loads(Path(job["settings_path"]).read_text(encoding="utf-8"))

    assert len(request["selected_data_paths"]) == 2
    assert all(Path(path).is_file() for path in request["selected_data_paths"])
    assert all((Path(job["job_dir"]) / "data") in Path(path).parents for path in request["selected_data_paths"])
    assert [Path(path).name for path in request["metadata_paths"]] == ["scilink_bundle_context.json"]
    context = json.loads(Path(request["metadata_paths"][0]).read_text(encoding="utf-8"))
    assert context["experiment_description"] == "Exact experiment description from Main Studio"
    assert context["data_structure"]["file_count"] == 2
    assert context["data_structure"]["files"][0]["columns"] == ["potential_V", "current_A"]
    assert context["data_structure"]["curve_agent_matrix_contract"]["shape"] == "N x 2"
    assert context["series_metadata"] == {
        "variable": "scan_rate_mV_s",
        "unit": "mV/s",
        "values": {
            Path(request["selected_data_paths"][0]).name: 100.0,
            Path(request["selected_data_paths"][1]).name: 50.0,
        },
    }
    assert Path(request["metadata_paths"][0]).parent == Path(job["job_dir"]) / "data"
    assert (Path(job["job_dir"]) / "source_data").is_dir()
    assert (Path(job["job_dir"]) / "scilink_data_preflight.json").is_file()
    assert settings["scilink_auto_repair_dependencies"] is True
    assert "source_path" not in json.dumps(context).lower()
    assert "canonical" not in json.dumps(context["data_structure"]).lower()
    assert not list(Path(job["job_dir"]).rglob("faradaiq_data_index.csv"))
    assert settings["scilink_unsafe_execution_ok"] is True
    assert settings["scilink_wait_for_user_questions"] is True
    assert settings["scilink_mode"] == "autonomous"
    assert settings["scilink_auto_continue_after_question"] is False
    assert settings["unsafe_execution_authorization_scope"] == "current SciLink CLI subprocess only"
    assert Path(job["bundle_zip"]).is_file()
    assert Path(job["prompt_path"]).read_text(encoding="utf-8") == "Calculate D and khet"


def test_prepare_environment_authorizes_only_child_process(monkeypatch):
    monkeypatch.delenv("UNSAFE_EXECUTION_OK", raising=False)
    base = {"UNSAFE_EXECUTION_OK": "inherited-wrong", "PATH": os.environ.get("PATH", "")}
    settings = {
        "scilink_unsafe_execution_ok": True,
        "llm": {"provider": "Ollama", "model": "gpt-oss:120b-cloud", "base_url": "http://localhost:11434/v1"},
    }
    child, report = cli.prepare_scilink_environment(settings, base)
    assert base["UNSAFE_EXECUTION_OK"] == "inherited-wrong"
    assert child["UNSAFE_EXECUTION_OK"] == "true"
    assert report["unsafe_execution_authorized"] is True
    assert report["unsafe_execution_scope"] == "current SciLink CLI subprocess only"
    assert "UNSAFE_EXECUTION_OK" in report["cleared_environment_variables"]
    assert "UNSAFE_EXECUTION_OK" not in os.environ

    child_disabled, report_disabled = cli.prepare_scilink_environment({**settings, "scilink_unsafe_execution_ok": False}, base)
    assert child_disabled["UNSAFE_EXECUTION_OK"] == "true"
    assert report_disabled["unsafe_execution_authorized"] is True
    assert report_disabled["unsafe_execution_policy"].startswith("always enabled")


def test_edison_stays_available_when_literature_is_not_requested():
    base = {
        "PATH": os.environ.get("PATH", ""),
        "EDISON_API_KEY": "edison-test-key",
    }
    settings = {
        "scilink_literature_analysis_enabled": False,
        "scilink_wait_for_user_questions": True,
        "llm": {"provider": "Ollama", "model": "gpt-oss:120b-cloud", "base_url": "http://localhost:11434/v1"},
    }
    child, report = cli.prepare_scilink_environment(settings, base)
    assert child["EDISON_API_KEY"] == "edison-test-key"
    assert child["FUTUREHOUSE_API_KEY"] == "edison-test-key"
    assert child["SCILINK_LITERATURE_ANALYSIS_ENABLED"] == "0"
    assert report["edison_credentials_available"] is True
    assert report["literature_analysis_enabled"] is False
    assert report["effective_scilink_mode"] == "co-pilot"
    assert child["SCILINK_REQUIRE_HUMAN_FEEDBACK"] == "1"
    assert child["SCILINK_AUTO_RERUN"] == "0"


def test_wait_for_user_policy_preserves_reviewed_mode():
    assert cli._effective_scilink_mode({"scilink_mode": "autonomous", "scilink_wait_for_user_questions": True}) == "autonomous"
    assert cli._effective_scilink_mode({"scilink_mode": "autonomous", "scilink_wait_for_user_questions": False}) == "autonomous"
    assert cli._effective_scilink_mode({"scilink_mode": "auto-pilot", "scilink_wait_for_user_questions": True}) == "autopilot"


def test_completed_html_report_is_opened_once(tmp_path: Path):
    job = tmp_path / "job"
    report = job / "session" / "results" / "analysis_001" / "series_analysis_report.html"
    report.parent.mkdir(parents=True)
    report.write_text("<html><body>complete</body></html>", encoding="utf-8")
    opened: list[Path] = []
    state: dict[str, object] = {}
    cli._scan_and_open_html_reports(job, state, force=True, opener=lambda path: opened.append(path) or True)
    cli._scan_and_open_html_reports(job, state, force=True, opener=lambda path: opened.append(path) or True)
    assert opened == [report.resolve()]
    receipt = json.loads((job / "scilink_report_opening.json").read_text(encoding="utf-8"))
    assert receipt["auto_open_enabled"] is True
    assert receipt["reports"][0]["path"] == str(report.resolve())


def test_cli_prefers_job_local_data_folder(tmp_path: Path):
    job = tmp_path / "job"
    data = job / "data"
    data.mkdir(parents=True)
    csv_path = data / "0001_cv.csv"
    csv_path.write_text("E,I\n0,1\n", encoding="utf-8")
    folder, existing = cli._job_local_data_folder(job, {"selected_data_paths": [str(csv_path)]})
    assert folder == data.resolve()
    assert existing == [csv_path.resolve()]


def test_interactive_stdin_feeder_keeps_pipe_open(monkeypatch, tmp_path: Path):
    writes: list[str] = []
    done = threading.Event()

    class Sink:
        def write(self, value):
            writes.append(value)
            return len(value)

        def flush(self):
            return None

    class Proc:
        def __init__(self):
            self.stdin = Sink()
            self.returncode = 0

        def poll(self):
            return 0 if done.is_set() else None

        def wait(self):
            done.wait(0.15)
            done.set()
            return 0

    proc = Proc()
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("answer to SciLink\n"))
    code = cli._run_interactive_scilink(
        ["scilink"],
        cwd=tmp_path,
        env={},
        prompt="Initial exact prompt\n\nSecond instruction line",
        prompt_via_stdin=True,
    )
    done.set()
    assert code == 0
    assert writes[0] == "Initial exact prompt Second instruction line\n"
    assert writes.count("Initial exact prompt Second instruction line\n") == 1
    assert "answer to SciLink\n" in writes
    assert not hasattr(proc.stdin, "closed")


def test_multiline_prompt_transport_is_one_cli_turn():
    prompt = "First paragraph.\n\n- item one\n- item two\nFinal instruction."
    wire = cli._stdin_single_turn_prompt(prompt)
    assert wire == "First paragraph. - item one - item two Final instruction."
    assert "\n" not in wire
    assert cli._stdin_single_turn_prompt("\r\n  A  \rB\n") == "A B"



def test_default_authorization_and_review_ui_are_explicit():
    import inspect
    import faradaiq.v239_scilink_edison as sharing

    assert sharing._DEFAULT_SHARING["scilink_unsafe_execution_ok"] is True
    assert sharing._DEFAULT_SHARING["scilink_wait_for_user_questions"] is True
    sharing_source = inspect.getsource(sharing.build_data_sharing_tab)
    review_source = inspect.getsource(v273.show_analysis_review)
    assert "UNSAFE_EXECUTION_OK=true" in sharing_source
    assert "Pause and wait for my input when SciLink asks a question" in sharing_source
    assert "Pause and wait for my input when SciLink asks a question" in review_source
    assert "Completed SciLink HTML/HTM reports open automatically" in review_source
    assert 'text="Start SciLink"' in review_source
    assert 'win._faradaiq_scilink_start_button' in review_source
    assert review_source.index('top_actions = ttk.Frame') < review_source.index('tabs = ttk.Notebook')
    assert review_source.count('text="Start SciLink"') == 1


def test_context_path_is_preferred_over_request_json(tmp_path: Path):
    job = tmp_path / "job"
    job.mkdir()
    request_path = job / "scilink_request.json"
    request_path.write_text("{}", encoding="utf-8")
    context_path = job / "scilink_bundle_context.json"
    context_path.write_text("{}", encoding="utf-8")
    resolved = cli._scilink_context_path(job, {"metadata_paths": [str(context_path)]})
    assert resolved == context_path.resolve()


def test_auto_context_paths_are_removed_from_scilink_description(tmp_path: Path):
    csv_path = tmp_path / "CV_point_001_scanrate_25mVs_cycle_01.csv"
    csv_path.write_text("time_s,potential_V,current_A\n0,0.1,1e-9\n", encoding="utf-8")
    description = (
        "Experiment name: demo\nOperator note: 10 mM ferricyanide\n"
        "FARADAIQ_AUTO_CONTEXT_BEGIN\n"
        "Source: C:\\Data\\private\\demo.dat\n"
        "Canonical H5: demo.h5\n"
        "FARADAIQ_AUTO_CONTEXT_END"
    )
    browser = FakeBrowser(tmp_path, [csv_path], description)
    assert v273._experiment_description(browser) == "Experiment name: demo\nOperator note: 10 mM ferricyanide"


def test_cv_bundle_is_two_column_lossless_projection(tmp_path: Path):
    source = tmp_path / "CV_point_021_scanrate_100mVs_cycle_01.csv"
    source.write_text(
        "time_s,potential_V,current_A\n0.0,0.8,1.7e-8\n0.00512,0.79,1.6e-8\n",
        encoding="utf-8",
    )
    destination = tmp_path / "prepared.csv"
    info = v273._prepare_scilink_table(source, destination, ["CV"])
    assert destination.read_text(encoding="utf-8").splitlines() == [
        "potential_V,current_A", "0.8,1.7e-8", "0.79,1.6e-8"
    ]
    assert info["transformation"] == "lossless_xy_projection"
    assert info["source_columns"] == ["time_s", "potential_V", "current_A"]
    assert info["scilink_columns"] == ["potential_V", "current_A"]
    assert info["scan_rate_mV_s"] == 100.0
    assert info["point_index"] == 21
    assert info["cycle_index"] == 1


def test_runtime_probe_uses_companion_python(tmp_path: Path):
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    exe = scripts / "scilink.exe"
    python = scripts / "python.exe"
    exe.write_text("", encoding="utf-8")
    python.write_text("", encoding="utf-8")
    assert cli._scilink_python([str(exe)]) == python.resolve()


def test_manual_slider_injection_is_drag_safe(tmp_path: Path):
    html = tmp_path / "viewer.html"
    html.write_text("<html><body><div class='plotly-graph-div'></div></body></html>", encoding="utf-8")
    v273._inject_frame_controls(html, {"frame_names": ["a", "b"], "frame_labels": ["A", "B"], "frame_duration_ms": 200})
    text = html.read_text(encoding="utf-8")
    assert "faradaiq-frame-range-v262" in text
    assert "pointerdown" in text
    assert "previewDrag" in text
    assert "commitDrag" in text
    assert "release to render" in text
    assert "faradaiq-frame-prev-v262" in text
    assert "faradaiq-frame-next-v262" in text
    assert "Plotly.animate" in text


def test_runtime_repair_uses_faradaiq_managed_target_not_scilink_site_packages(tmp_path: Path, monkeypatch):
    package_root = tmp_path / "package"
    package_root.mkdir()
    fake_module_file = package_root / "faradaiq_scilink_cli_job.py"
    fake_module_file.write_text("# test", encoding="utf-8")
    (package_root / "requirements_scilink_curvefit.txt").write_text("numpy>=1.26,<3\n", encoding="utf-8")
    scripts = tmp_path / "SciLink" / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    scilink = scripts / "scilink.exe"
    python = scripts / "python.exe"
    scilink.write_text("", encoding="utf-8")
    python.write_text("", encoding="utf-8")
    job = tmp_path / "job"
    job.mkdir()

    probes = [
        {"python": str(python), "missing": ["numpy"], "returncode": 0},
        {"python": str(python), "missing": ["numpy"], "returncode": 0},
        {"python": str(python), "missing": [], "returncode": 0},
    ]
    calls = []

    monkeypatch.setattr(cli, "__file__", str(fake_module_file))
    monkeypatch.setattr(cli, "_python_abi_tag", lambda _python: "py311")
    monkeypatch.setattr(cli, "_probe_runtime", lambda *_args, **_kwargs: probes.pop(0))

    class Result:
        returncode = 0

    def fake_run(command, **kwargs):
        calls.append([str(value) for value in command])
        return Result()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    report = cli._ensure_scilink_runtime(
        [str(scilink)], {"scilink_auto_repair_dependencies": True}, job
    )
    assert report["status"] == "ready"
    assert report["external_scilink_site_packages_modified"] is False
    target = package_root / ".scilink_curvefit_runtime" / "py311"
    assert report["pythonpath"] == str(target)
    pip_call = calls[0]
    assert "--target" in pip_call
    assert pip_call[pip_call.index("--target") + 1] == str(target)
    assert str(scripts.parent / "Lib" / "site-packages") not in " ".join(pip_call)


def test_data_root_contract_rejects_parent_experiment_files(tmp_path: Path):
    job = tmp_path / "job"
    data = job / "data"
    data.mkdir(parents=True)
    local = data / "cv.csv"
    local.write_text("potential_V,current_A\n0,1\n", encoding="utf-8")
    context = data / "scilink_bundle_context.json"
    context.write_text("{}", encoding="utf-8")
    parent = tmp_path / "parent_experiment.csv"
    parent.write_text("x,y\n0,1\n", encoding="utf-8")
    errors = cli._validate_job_data_root(job, data, [local, parent], context)
    assert any("outside the job-local data folder" in error for error in errors)
