from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np


class Var:
    def __init__(self, value=None):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


def _import_core(monkeypatch):
    monkeypatch.setenv("FARADAIQ_SKIP_STARTUP_AI_SELECTOR", "1")
    import faradaiq_studio_core as core
    return core


def test_release_identity_and_color_modes(monkeypatch):
    core = _import_core(monkeypatch)
    import faradaiq.multidim_v216 as md
    from faradaiq import v250_reliability as v250
    assert core.FARADAIQ_PUBLIC_VERSION == "v316"
    assert tuple(core._v250_color_modes) == v250.COLOR_MODES
    assert md._v216_options({"color_scale_mode": v250.COLOR_AUTO_FRAME})["range_scope"] == "Per frame"
    assert md._v216_options({"color_scale_mode": v250.COLOR_GLOBAL})["range_scope"] == "Lock across cube"
    assert md._v216_options({"color_scale_mode": v250.COLOR_MANUAL})["range_mode"] == "Manual"


def test_scilink_ollama_preflight_never_consumes_prompt_as_api_key(monkeypatch, tmp_path):
    _import_core(monkeypatch)
    import faradaiq_scilink_cli_job as cli
    env, report = cli.prepare_scilink_environment(
        {"llm": {"provider": "Ollama", "model": "gpt-oss:120b-cloud", "base_url": "http://localhost:11434/v1"}},
        {},
    )
    assert report["status"] == "ready"
    assert env["SCILINK_API_KEY"] == "ollama"
    assert env["SCILINK_PROXY_API_KEY"] == "ollama"
    assert env["LITELLM_MODEL"] == "gpt-oss:120b-cloud"


def test_scilink_process_uses_active_gui_provider_secret(monkeypatch):
    core = _import_core(monkeypatch)
    from faradaiq import v250_reliability as v250
    v250._CORE["_v40_provider_candidates"] = lambda include_unavailable=True: [{
        "name": "Custom", "available": True, "api_key": "gui-secret", "model": "m", "base_url": "http://local/v1"
    }]
    job = {"settings": {"llm": {"provider": "Custom", "model": "m", "base_url": "http://local/v1"}}}
    env = v250._scilink_process_environment(job)
    assert env["SCILINK_API_KEY"] == "gui-secret"
    assert env["SCILINK_PROXY_API_KEY"] == "gui-secret"


def test_review_has_filters_for_both_services_and_unchecked_default(monkeypatch):
    _import_core(monkeypatch)
    from faradaiq import v250_reliability as v250
    source = inspect.getsource(v250.show_analysis_review)
    for text in ("Search", "All roles", "All groups", "All types", "Selected only", "Select visible", "Clear visible", "Select all", "Clear all"):
        assert text in source
    assert "tk.BooleanVar(value=False)" in source
    assert 'service_name = "SciLink" if service == "scilink" else "Edison Scientific"' in source


def test_edison_script_uses_exact_prompt_and_no_hidden_bundle_instruction():
    import faradaiq_edison_cli_job as edison
    source = inspect.getsource(edison)
    assert "TaskRequest(name=job_name, query=prompt" in source
    assert "Analyze the attached FaradaIQ AI-analysis bundle" not in source
    assert "prompting_config" not in source
    assert "upload_succeeded" in source and "task_failed" in source


def test_per_technique_view_state_restores_drt_and_cv(monkeypatch):
    _import_core(monkeypatch)
    import faradaiq.browser_ui_v209 as bu
    b = SimpleNamespace(
        plot_mode_var=Var("Auto"), plot_xscale_var=Var("Linear"), plot_yscale_var=Var("Linear"),
        plot_grid_var=Var(True), cv_view_var=Var("I-V"), plot_xmin_var=Var(""), plot_xmax_var=Var(""),
        plot_ymin_var=Var(""), plot_ymax_var=Var(""),
    )
    bu._set_mode_defaults(b, "DRT")
    assert b.plot_xscale_var.get() == "Log"
    b.plot_xmin_var.set("1e-4")
    bu._set_mode_defaults(b, "CV / LSV")
    assert b.plot_xscale_var.get() == "Linear"
    assert b.cv_view_var.get() == "I-V"
    b.plot_xmin_var.set("-0.8")
    bu._set_mode_defaults(b, "DRT")
    assert b.plot_xscale_var.get() == "Log"
    assert b.plot_xmin_var.get() == "1e-4"
    bu._set_mode_defaults(b, "CV / LSV")
    assert b.plot_xmin_var.get() == "-0.8"


def test_cv_peak_charge_branch_maps_work_with_numpy_126_compat(monkeypatch, tmp_path):
    _import_core(monkeypatch)
    import faradaiq.v228_intelligence as intel
    import faradaiq.multidim_v216 as md

    curves = []
    for point in range(1, 7):
        forward = np.linspace(-0.65, 0.75, 60)
        reverse = np.linspace(0.75, -0.65, 60)[1:]
        potential = np.concatenate([forward, reverse])
        current = 1e-6 * (0.25 * potential + np.exp(-((potential - 0.28) / 0.09) ** 2) - 0.7 * np.exp(-((potential + 0.32) / 0.10) ** 2))
        current *= 1.0 + point * 0.05
        record = {"technique_key": "CV", "scan_rate_v_s": 0.05, "point": point, "cycle": 3, "method_index": 1}
        curves.append(md.Curve(
            path=tmp_path / f"p{point}.csv", record=record, x_name="E", y_name="I",
            x=potential, y=current, point=point, method=1, cycle=3, line=0,
            x_coord=((point - 1) % 3) * 10e-6, y_coord=((point - 1) // 3) * 10e-6,
        ))
    monkeypatch.setattr(intel, "_selected_records", lambda browser, techniques=None: [c.record for c in curves])
    monkeypatch.setattr(intel, "_curves_from_records", lambda browser, records, max_curves=2500: curves)
    browser = SimpleNamespace(analyzed_root=tmp_path, cache_root=tmp_path, base_name="cv_smoke", h5_path="")
    result = intel.analyze_cv(browser)
    assert result["branch_count"] >= 12
    assert Path(result["metrics_csv"]).exists()
    assert result["spatial_maps"]
    assert all(Path(p).exists() for p in result["spatial_maps"])
    table = __import__("pandas").read_csv(result["metrics_csv"])
    assert table["charge_C"].notna().all()


def test_startup_modes_are_exact(monkeypatch):
    _import_core(monkeypatch)
    from faradaiq import v250_reliability as v250
    assert list(v250.AI_MODE_LABELS.values()) == [
        "SciLink + LLM tools (Edison available inside SciLink)",
        "SciLink + direct Edison Scientific + LLM tools",
        "All services: SciLink + direct Edison Scientific + LLM tools",
    ]


def test_parallel_dialog_is_custom_and_not_simpledialog_prompt(monkeypatch):
    _import_core(monkeypatch)
    from faradaiq import v250_reliability as v250
    source = inspect.getsource(v250._parallel_worker_dialog)
    assert 'win.geometry("640x300")' in source
    assert "Spinbox" in source


def test_scilink_cli_submits_exact_prompt_after_automatic_key_resolution(tmp_path):
    import os, subprocess, sys
    root = Path(__file__).resolve().parents[1]
    fake = tmp_path / "fake_scilink"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "if '--help' in sys.argv:\n"
        "    print('analyze --mode --model --base-url --session-dir')\n"
        "    raise SystemExit(0)\n"
        "prompt=sys.stdin.readline()\n"
        "job=os.environ['FARADAIQ_SCILINK_JOB_DIR']\n"
        "open(os.path.join(job,'captured_fake_scilink.json'),'w',encoding='utf-8').write(json.dumps({'prompt':prompt,'key':os.environ.get('SCILINK_API_KEY'),'model':os.environ.get('LITELLM_MODEL'),'unsafe':os.environ.get('UNSAFE_EXECUTION_OK')}))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    job = tmp_path / "job"; job.mkdir()
    exact = "Analyze this CV vs scan rate and estimate diffusion coefficient."
    (job / "analysis_prompt.md").write_text(exact, encoding="utf-8")
    (job / "scilink_request.json").write_text(json.dumps({"selected_data_paths": []}), encoding="utf-8")
    (job / "service_settings.json").write_text(json.dumps({
        "scilink_executable_override": str(fake),
        "scilink_mode": "autonomous",
        "llm": {"provider": "Ollama", "model": "gpt-oss:120b-cloud", "base_url": "http://localhost:11434/v1"},
    }), encoding="utf-8")
    env = dict(os.environ)
    for key in ("SCILINK_API_KEY", "SCILINK_PROXY_API_KEY", "OLLAMA_API_KEY", "OPENAI_API_KEY"):
        env.pop(key, None)
    cp = subprocess.run([sys.executable, str(root / "faradaiq_scilink_cli_job.py"), "--job-dir", str(job)], env=env, text=True, capture_output=True, timeout=30)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    captured = json.loads((job / "captured_fake_scilink.json").read_text(encoding="utf-8"))
    assert captured["key"] == "ollama"
    assert captured["model"] == "gpt-oss:120b-cloud"
    assert captured["prompt"].startswith(exact + " ")
    assert "explicit SciLink `--skills` argument" in captured["prompt"]
    assert "select_agent and run_analysis remain available" in captured["prompt"]
    assert "do not call select_agent or run_analysis" not in captured["prompt"]
    assert "electrochemistry_skill.md" in captured["prompt"]
    assert "# SciLink Electrochemistry Skill" in captured["prompt"]
    assert (job / "analysis_prompt.md").read_text(encoding="utf-8") == exact
    submission = json.loads((job / "scilink_prompt_submission.json").read_text(encoding="utf-8"))
    effective = Path(submission["effective_prompt_file"])
    assert effective.is_file()
    assert "explicit SciLink `--skills` argument" in effective.read_text(encoding="utf-8")
    assert "select_agent and run_analysis remain available" in effective.read_text(encoding="utf-8")
    assert captured["unsafe"] == "true"


def test_edison_zip_contains_exact_prompt_request_and_no_hidden_settings(monkeypatch, tmp_path):
    _import_core(monkeypatch)
    from faradaiq import v250_reliability as v250
    import zipfile
    source = SimpleNamespace(output_var=Var(str(tmp_path)), file_var=Var(str(tmp_path)), base_name="edison_exact")
    exact = "Exact Edison question\nwith user formatting."
    job = v250._write_exact_edison_job(source, exact, [], {}, ["answer.csv"])
    assert Path(job["prompt_path"]).read_text(encoding="utf-8") == exact
    with zipfile.ZipFile(job["zip_path"]) as zf:
        names = set(zf.namelist())
        assert "analysis_prompt.md" in names
        assert "edison_request.json" in names
        assert "service_settings.json" not in names
        assert zf.read("analysis_prompt.md").decode("utf-8") == exact
        request = json.loads(zf.read("edison_request.json").decode("utf-8"))
        assert request["requested_filenames"] == ["answer.csv"]
        assert request["selected_data_paths"] == []


def test_llm_job_is_exact_text_only_and_uses_gui_secret(monkeypatch, tmp_path):
    _import_core(monkeypatch)
    from faradaiq import v250_reliability as v250
    v250._CORE["_v40_provider_candidates"] = lambda include_unavailable=True: [{
        "name": "Ollama", "available": True, "api_key": "gui-ollama", "model": "gpt-oss:120b-cloud", "base_url": "http://localhost:11434/v1"
    }]
    source = SimpleNamespace(output_var=Var(str(tmp_path)), file_var=Var(str(tmp_path)), base_name="llm_exact")
    exact = "Exact text-only prompt\nno files"
    job = v250._write_llm_job(source, exact)
    assert Path(job["prompt_path"]).read_text(encoding="utf-8") == exact
    request = json.loads(Path(job["request_path"]).read_text(encoding="utf-8"))
    assert request["file_upload"] is False
    env = v250._llm_process_environment(job)
    assert env["OPENAI_API_KEY"] == "gui-ollama"


def test_windows_launcher_owns_service_selection():
    root = Path(__file__).resolve().parents[1]
    text = (root / "Start_FaradaIQ_Studio_v320.bat").read_text(encoding="utf-8")
    assert "choice /C 123" in text
    assert "FARADAIQ_AI_LAUNCH_MODE=scilink" in text
    assert "FARADAIQ_AI_LAUNCH_MODE=scilink_edison" in text
    assert "FARADAIQ_AI_LAUNCH_MODE=scilink_edison_llm" in text
    assert "FARADAIQ_SKIP_STARTUP_AI_SELECTOR=1" in text


def test_no_gui_service_selector_is_created(monkeypatch):
    _import_core(monkeypatch)
    from faradaiq import v250_reliability as v250
    source = inspect.getsource(v250._show_startup_selector)
    assert "Toplevel" not in source
    assert "_configure_main_ai_toolbar" in source


def test_llm_tools_are_enabled_in_every_launch_mode(monkeypatch):
    _import_core(monkeypatch)
    from faradaiq import v250_reliability as v250
    for mode, edison in ((v250.AI_MODE_SCILINK, False), (v250.AI_MODE_SCILINK_EDISON, True), (v250.AI_MODE_ALL, True)):
        monkeypatch.setenv("FARADAIQ_AI_LAUNCH_MODE", mode)
        monkeypatch.setenv("FARADAIQ_EDISON_ENABLED", "1" if edison else "0")
        monkeypatch.setenv("FARADAIQ_LLM_TOOLS_ENABLED", "0")
        assert v250._llm_tools_enabled() is True
        assert v250._edison_enabled() is edison
