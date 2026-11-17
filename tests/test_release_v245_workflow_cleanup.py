from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import faradaiq_studio_core as core
import faradaiq.v244_workflow_cleanup as release

ROOT = Path(__file__).resolve().parents[1]


class Value:
    def __init__(self, value: str):
        self.value = value

    def get(self) -> str:
        return self.value


class Source:
    def __init__(self, root: Path):
        self.analysis_root = root
        self.h5_path = root / "sample_faradaiq_bundle.h5"
        self.json_path = root / "sample_faradaiq_bundle.json"
        self.h5_path.write_bytes(b"h5")
        self.json_path.write_text(json.dumps({"techniques_detected": ["CV"]}), encoding="utf-8")
        self.gui = SimpleNamespace(
            base_name="sample",
            file_var=Value(str(root)),
            output_var=Value(str(root)),
        )


def test_public_v245_identity_and_controlled_install_path():
    assert core.APP_TITLE == "FaradaIQ Studio v316"
    assert core.FARADAIQ_PUBLIC_VERSION == "v316"
    assert hasattr(core, "_v250_main_final")
    for name in (
        "faradaiq_studio_v316.py",
        "Start_FaradaIQ_Studio_v320.bat",
        "Install_FaradaIQ_Studio_v320.bat",
        "README_v316.md",
    ):
        assert (ROOT / name).exists(), name
    installer = (ROOT / "Install_FaradaIQ_Studio_v320.bat").read_text(encoding="utf-8")
    assert 'set "INSTALL_ROOT=C:\\FaradaIQ\\Studio"' in installer
    assert 'set "VERSION_FOLDER=V316"' in installer
    assert 'start "FaradaIQ Studio v316"' in installer


def test_launcher_has_terminal_service_selector_after_noninteractive_setup():
    launcher = (ROOT / "Start_FaradaIQ_Studio_v320.bat").read_text(encoding="utf-8")
    lower = launcher.lower()
    assert 'set "faradaiq_edison_enabled=1"' in lower
    assert 'set "faradaiq_science_mode=scilink_edison"' in lower
    assert "set /p" not in lower
    assert "choice /c 123" in lower
    assert "faradaiq_ai_launch_mode=scilink" in lower
    assert "faradaiq_ai_launch_mode=scilink_edison" in lower
    assert "faradaiq_ai_launch_mode=scilink_edison_llm" in lower
    assert "pause" not in [line.strip().lower() for line in launcher.splitlines()]
    assert "call :find_seed_python" not in lower
    assert "_ensure_faradaiq_venv_v316.ps1" in launcher
    assert "Preferred seed order: Python 3.12, 3.11, 3.13, then 3.10." in launcher


def test_scilink_prompt_is_focused_and_points_to_local_folder_and_json(tmp_path: Path):
    source = Source(tmp_path)
    prompt = release.build_scilink_prompt(
        source,
        "Analyze attached CV data for all points and estimate the diffusion coefficient.",
    )
    assert prompt == "Analyze attached CV data for all points and estimate the diffusion coefficient."
    assert "ASK SCILINK" not in prompt
    assert "Folder:" not in prompt
    assert "FARADAIQ DATA STRUCTURE" not in prompt
    assert "Required behavior" not in prompt


def test_edison_prompt_is_logically_formatted_and_explains_local_path_access(tmp_path: Path):
    source = Source(tmp_path)
    prompt = release.build_edison_prompt(source, "Find literature methods for CV diffusion-coefficient estimation.")
    for heading in ("Research question", "Purpose", "Local dataset reference", "Access rule", "Response requirements"):
        assert heading in prompt
    assert "Edison cannot read local files unless" in prompt
    assert "All file checkboxes start unchecked" in prompt
    assert "DOI" in prompt


def test_science_mode_controls_direct_edison_only(monkeypatch):
    monkeypatch.setenv("FARADAIQ_AI_LAUNCH_MODE", "scilink")
    monkeypatch.setenv("FARADAIQ_EDISON_ENABLED", "1")
    monkeypatch.setenv("FARADAIQ_DIRECT_EDISON_ENABLED", "0")
    monkeypatch.setenv("FARADAIQ_LLM_TOOLS_ENABLED", "1")
    assert release.edison_enabled() is False
    assert release.launch_mode_label() == "SciLink + LLM tools (Edison available inside SciLink)"
    settings = release._service_settings({})
    assert settings["edison_enabled"] is True
    assert settings["direct_edison_enabled"] is False
    assert settings["edison_available_to_scilink"] is True
    monkeypatch.setenv("FARADAIQ_AI_LAUNCH_MODE", "scilink_edison")
    monkeypatch.setenv("FARADAIQ_DIRECT_EDISON_ENABLED", "1")
    assert release.edison_enabled() is True
    assert release.launch_mode_label() == "SciLink + direct Edison Scientific + LLM tools"


def test_ai_chat_source_is_scilink_only():
    source = (ROOT / "faradaiq" / "v244_workflow_cleanup.py").read_text(encoding="utf-8")
    block = source[source.index("def open_scilink_chat"):source.index("def _walk_widgets")]
    assert 'text="Ask SciLink"' in block
    assert 'text="Ask Edison Scientific"' not in block
    assert "no direct LLM or direct Edison route" in block


def test_review_dialog_file_checkboxes_default_unchecked():
    source = (ROOT / "faradaiq" / "v244_workflow_cleanup.py").read_text(encoding="utf-8")
    block = source[source.index("def show_analysis_review"):source.index("def open_scilink_chat")]
    assert "var = tk.BooleanVar(value=False)" in block
    assert "Selected 0 files. Text prompt only." in block


def test_scilink_and_edison_keep_explicit_user_review_input():
    source = (ROOT / "faradaiq" / "v244_workflow_cleanup.py").read_text(encoding="utf-8")
    block = source[source.index("def show_analysis_review"):source.index("def open_scilink_chat")]
    assert "prompt_box = ScrolledText" in block
    assert 'tabs.add(prompt_tab, text="Prompt")' in block
    assert 'tabs.add(files_tab, text="Files to Share")' in block
    assert 'tabs.add(settings_tab, text="Service Details")' in block
    assert 'prompt_box.get("1.0", "end").strip()' in block
    assert 'text=f"Start new {service_name} job"' in block
    assert "Creating reviewed job" in block


def test_resource_footer_compact_tools_and_browser_preview_cache_are_installed():
    source = (ROOT / "faradaiq" / "v244_workflow_cleanup.py").read_text(encoding="utf-8")
    assert "CPU {cpu} | RAM {ram} | Disk {disk}" in source
    assert 'text="Analysis tool"' in source
    assert 'text="Auto parser/DRT after export"' in source
    assert "_v244_preview_cache" in source
    assert "self.after(140, run)" in source


def test_scilink_cli_reports_v290_and_keeps_edison_inside_scilink():
    source = (ROOT / "faradaiq_scilink_cli_job.py").read_text(encoding="utf-8")
    assert "FaradaIQ Studio {VERSION} -> SciLink interactive exact-prompt analysis job" in source
    assert "from faradaiq.release_identity import APP_VERSION as VERSION" in source
    assert '"FARADAIQ_EDISON_ENABLED": "1"' in source
    assert '"FARADAIQ_DIRECT_EDISON_ENABLED": "1" if settings.get("direct_edison_enabled") else "0"' in source
    assert '"SCILINK_LITERATURE_ANALYSIS_ENABLED"' in source


def test_loading_completed_job_preserves_running_job_with_same_source():
    class Job:
        def __init__(self, job_id: str, status: str, source_path: str):
            self.job_id = job_id
            self.status = status
            self.source_path = source_path

    class BatchJobFactory:
        @staticmethod
        def from_dict(payload):
            return Job(str(payload["job_id"]), str(payload["status"]), str(payload["source_path"]))

    running = Job("RUN-1", "Running", "C:/data/sample.dat")
    completed_old = Job("DONE-1", "Completed", "C:/data/sample.dat")
    model = SimpleNamespace(jobs=[completed_old])
    selected = release._merge_restored_jobs(
        model,
        snapshot=[running, completed_old],
        batch_payload={"job_id": "DONE-2", "status": "Completed", "source_path": "C:/data/sample.dat"},
        BatchJob=BatchJobFactory,
    )
    assert selected == "DONE-2"
    states = {(job.job_id, job.status) for job in model.jobs}
    assert ("RUN-1", "Running") in states
    assert ("DONE-1", "Completed") in states
    assert ("DONE-2", "Completed") in states
