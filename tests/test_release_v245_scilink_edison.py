from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import faradaiq.v239_scilink_edison as workflow

ROOT = Path(__file__).resolve().parents[1]


class Value:
    def __init__(self, value: str):
        self.value = value

    def get(self) -> str:
        return self.value


class Browser:
    def __init__(self, root: Path, h5: Path, manifest: Path, selected: Path):
        self.analysis_root = root
        self.h5_path = h5
        self.json_path = manifest
        self.gui = SimpleNamespace(
            base_name="electrochem_job",
            file_var=Value(str(root)),
            output_var=Value(str(root)),
        )
        self._selected = selected
        self.tree = SimpleNamespace(selection=lambda: ())
        self._path_by_iid = {}

    def selected_paths(self, plottable_only: bool = False):
        return [self._selected]


def make_browser(tmp_path: Path) -> Browser:
    h5 = tmp_path / "electrochem_job_faradaiq_bundle.h5"
    manifest = tmp_path / "electrochem_job_faradaiq_bundle.json"
    selected = tmp_path / "CV" / "point_001_cycle_3.csv"
    figure = tmp_path / "figures" / "cv_map.png"
    h5.write_bytes(b"h5-test")
    manifest.write_text(json.dumps({"techniques_detected": ["CV"]}), encoding="utf-8")
    selected.parent.mkdir()
    selected.write_text("Potential_V,Current_A\n0,0\n", encoding="utf-8")
    figure.parent.mkdir()
    figure.write_bytes(b"png-test")
    return Browser(tmp_path, h5, manifest, selected)


def test_v245_public_release_and_exact_controlled_install_path():
    required = (
        "faradaiq_studio_v316.py",
        "Start_FaradaIQ_Studio_v320.bat",
        "Install_FaradaIQ_Studio_v320.bat",
        "README_v316.md",
        "CHANGELOG_v316.txt",
        "KNOWN_LIMITATIONS_v316.md",
    )
    for name in required:
        assert (ROOT / name).exists(), name
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.316.0"' in pyproject
    assert 'readme = "README_v316.md"' in pyproject
    installer = (ROOT / "Install_FaradaIQ_Studio_v320.bat").read_text(encoding="utf-8")
    assert 'set "INSTALL_ROOT=C:\\FaradaIQ\\Studio"' in installer
    assert 'set "VERSION_FOLDER=V316"' in installer
    assert 'robocopy "%SOURCE_DIR%" "%INSTALL_DIR%"' in installer
    assert 'start "FaradaIQ Studio v316" /D "%INSTALL_DIR%" "%ComSpec%" /D /C' in installer
    assert 'call "%INSTALL_DIR%\\%START_BAT%" --install-only' in installer
    assert "C:\\FaradaIQ Studio" not in installer


def test_main_toolbar_is_consolidated_and_has_no_direct_file_to_llm_action():
    source = (ROOT / "faradaiq" / "v239_scilink_edison.py").read_text(encoding="utf-8")
    for label in ("LLM settings", "Generate LLM-guided prompt", "Ask SciLink", "Ask Edison Scientific", "AI Chat"):
        assert f'text="{label}"' in source
    toolbar = source[source.index("def configure_main_ai_toolbar"):source.index("def _collapse_or_expand")]
    assert 'text="Build LLM-guided AI bundle"' not in toolbar
    assert 'text="Send AI bundle to Edison"' not in toolbar
    assert "Text-only LLM prompt generation" in toolbar


def test_review_dialog_has_prompt_files_and_service_tabs():
    source = (ROOT / "faradaiq" / "v239_scilink_edison.py").read_text(encoding="utf-8")
    review = source[source.index("def show_analysis_review"):source.index("def open_consolidated_ai_chat")]
    for tab in ("Prompt", "Files to Share", "Service Settings"):
        assert f'text="{tab}"' in review
    assert "file_vars" in review
    assert "Start new {service_name} job" in review
    assert "Nothing starts until you approve" in review


def test_candidates_include_canonical_selected_tables_and_figures(tmp_path):
    browser = make_browser(tmp_path)
    cfg = dict(workflow._DEFAULT_SHARING)
    candidates = workflow.collect_share_candidates(browser, cfg)
    paths = {Path(item["path"]).name for item in candidates}
    roles = {item["role"] for item in candidates}
    assert browser.h5_path.name in paths
    assert browser.json_path.name in paths
    assert browser._selected.name in paths
    assert "cv_map.png" in paths
    assert "Canonical H5 numeric bundle" in roles
    assert "Canonical JSON manifest" in roles
    assert "Browser-selected data" in roles
    assert "Figure / visualization" in roles
    assert candidates
    assert all(item["selected"] is False for item in candidates)


def test_each_scilink_click_can_create_a_unique_persistent_job(tmp_path):
    browser = make_browser(tmp_path)
    cfg = dict(workflow._DEFAULT_SHARING)
    selected = workflow.collect_share_candidates(browser, cfg)[:4]
    prompt = workflow._build_analysis_prompt(browser, "scilink", "Analyze the third CV cycle.", selected)
    first = workflow._write_job(browser, "scilink", prompt, selected, cfg)
    second = workflow._write_job(browser, "scilink", prompt, selected, cfg)
    assert first["job_id"] != second["job_id"]
    assert Path(first["job_dir"]).is_dir()
    assert Path(second["job_dir"]).is_dir()
    launcher = workflow._write_scilink_launcher(first, cfg)
    text = launcher.read_text(encoding="utf-8")
    assert "FARADAIQ_SCILINK_PROMPT_FILE" in text
    assert "FARADAIQ_SCILINK_MANIFEST" in text
    assert "This terminal remains open" in text
    manifest = json.loads(Path(first["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["policy"].startswith("Explicit user-reviewed file list")
    assert manifest["file_count"] == len(selected)


def test_edison_job_is_prompt_only_when_no_files_are_checked(tmp_path):
    browser = make_browser(tmp_path)
    cfg = dict(workflow._DEFAULT_SHARING)
    prompt = workflow._build_analysis_prompt(browser, "edison", "Check data quality.", [])
    job = workflow._write_job(browser, "edison", prompt, [], cfg)
    zip_path = Path(job["zip_path"])
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        prompt_text = zf.read("analysis_prompt.md").decode("utf-8")
    assert names == ["analysis_prompt.md"]
    assert workflow._STRUCTURE_MARKER in prompt_text
    assert "No data files are selected" in prompt_text


def test_edison_job_includes_only_explicitly_checked_files(tmp_path):
    browser = make_browser(tmp_path)
    cfg = dict(workflow._DEFAULT_SHARING)
    candidates = workflow.collect_share_candidates(browser, cfg)
    selected = [item for item in candidates if item.get("path") and Path(item["path"]).suffix.lower() in {".h5", ".json"}]
    prompt = workflow._build_analysis_prompt(browser, "edison", "Check data quality.", selected)
    job = workflow._write_job(browser, "edison", prompt, selected, cfg)
    with zipfile.ZipFile(Path(job["zip_path"])) as zf:
        names = zf.namelist()
    assert "analysis_prompt.md" in names
    assert "files_to_share_manifest.json" in names
    assert "service_settings.json" not in names
    shared = [name for name in names if name.startswith("shared_files/")]
    assert len(shared) == len(selected)
    assert not any(name.endswith("point_001_cycle_3.csv") for name in shared)


def test_browser_surface_has_visual_block_schema_and_preserves_llm_smart_suggestions():
    workflow_source = (ROOT / "faradaiq" / "v239_scilink_edison.py").read_text(encoding="utf-8")
    visual_source = (ROOT / "faradaiq" / "v240_visual_schema_catalog.py").read_text(encoding="utf-8")
    assert 'text="Ask SciLink"' in workflow_source
    assert 'text="Ask Edison Scientific"' in workflow_source
    assert '"AI-suggested comparisons": "LLM-assisted Smart Suggestions"' in workflow_source
    assert '"Use LLM for Ask AI": "Use LLM for Smart Suggestions"' in workflow_source
    assert 'elif text in {"Ask AI", "Ask Local"}' in workflow_source
    assert 'notebook.add(tab, text="Visual Data Schema")' in visual_source
    assert 'inner.add(visual_tab, text="Block diagram")' in visual_source
    assert 'CATALOG / INDEX' in visual_source


def test_smart_suggestion_context_is_text_only_and_path_sanitized(tmp_path):
    browser = make_browser(tmp_path)
    browser.manifest = {
        "techniques_detected": ["CV"],
        "technique_counts": {"CV / LSV": 1},
        "data_structure": {"validation": {"row_count": 2, "raw_values": [1, 2, 3]}},
    }
    browser.cache_root = tmp_path
    browser.base_name = "electrochem_job"
    context = workflow.build_smart_suggestion_context(browser, "Compare cycle 3")
    serialized = json.dumps(context)
    assert context["request"] == "Compare cycle 3"
    assert context["techniques_detected"] == ["CV"]
    assert "relative_cache_labels" in context
    assert str(tmp_path) not in serialized
    assert "raw_values" not in serialized
    assert "No file bytes" in context["data_policy"]


def test_service_review_prompt_always_contains_text_data_structure_and_no_paths(tmp_path):
    browser = make_browser(tmp_path)
    browser.manifest = {
        "techniques_detected": ["CV"],
        "technique_counts": {"CV": 1},
        "data_structure": {"groups": ["scan_rate", "cycle"], "source_path": str(tmp_path / "private.dat")},
    }
    browser.cache_root = tmp_path
    prompt = workflow.compose_text_only_review_prompt(browser, "Compare cycle 3.")
    assert workflow._STRUCTURE_MARKER in prompt
    assert "Compare cycle 3." in prompt
    assert "No data files are selected" in prompt
    assert str(tmp_path) not in prompt
    assert "techniques_detected" in prompt


def test_review_dialog_code_forces_every_file_checkbox_unchecked():
    source = (ROOT / "faradaiq" / "v239_scilink_edison.py").read_text(encoding="utf-8")
    review = source[source.index("def show_analysis_review"):source.index("def open_consolidated_ai_chat")]
    assert "var = tk.BooleanVar(value=False)" in review
    assert "All are unchecked" in review
    assert "compose_text_only_review_prompt(source, scientific_prompt)" in review


def test_deareis_transactional_repair_is_version_normalized():
    helper = (ROOT / "_install_deareis_optional.py").read_text(encoding="utf-8")
    installer = (ROOT / "install_deareis_optional.bat").read_text(encoding="utf-8")
    assert ".deareis_venv.__backup_v292" in helper
    assert "FaradaIQ_Studio_v292_DearEIS_install.log" in helper
    assert "FaradaIQ DearEIS transactional installer v292" in helper
    assert "pip install --upgrade" not in installer + helper


def test_llm_guided_prompt_messages_are_text_only_and_do_not_include_local_paths(tmp_path):
    class Text:
        def __init__(self, value): self.value = value
        def get(self, *_args): return self.value
    gui = SimpleNamespace(
        desc_text=Text("Study CV scan-rate dependence."),
        summary_text=Text("CV: 12 curves; EIS: 4 spectra."),
        file_var=Value(str(tmp_path / "private" / "raw.dat")),
        base_name="safe_job",
        _v92_structured_metadata={
            "base_name": "safe_job",
            "source_file_or_folder": str(tmp_path / "private" / "raw.dat"),
            "human_metadata": {"experiment_goal": "Compare scan rates"},
            "inferred_from_parser_and_gui": {"technique_counts_or_presence": {"CV": 12}},
            "recommended_data_structure": {"primary_grouping_order": ["scan_rate", "cycle"]},
            "smart_comparison_suggestions": ["Compare third cycles"],
            "ai_bundle_priorities": {"flag_missing_metadata_before_quantitative_claims": ["electrode area"]},
        },
    )
    messages = workflow.build_llm_guided_prompt_messages(gui)
    serialized = json.dumps(messages)
    assert str(tmp_path) not in serialized
    assert "raw.dat" not in serialized
    assert "No files, file bytes, raw numeric rows" in serialized
    assert "SciLink or Edison Scientific" in serialized


def test_session_metadata_is_visible_review_item_and_only_written_when_checked(tmp_path):
    browser = make_browser(tmp_path)
    cfg = dict(workflow._DEFAULT_SHARING)
    candidates = workflow.collect_share_candidates(browser, cfg)
    generated = [item for item in candidates if item.get("generated") == "session_metadata"]
    assert len(generated) == 1
    assert generated[0]["role"] == "FaradaIQ session metadata"
    prompt = workflow._build_analysis_prompt(browser, "scilink", "Analyze quality.", generated)
    job = workflow._write_job(browser, "scilink", prompt, generated, cfg)
    manifest = json.loads(Path(job["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["file_count"] == 1
    assert manifest["files"][0]["generated"] == "session_metadata"
    assert Path(manifest["files"][0]["path"]).name == "faradaiq_session_metadata.json"
    assert Path(manifest["files"][0]["path"]).exists()

    no_meta = workflow._write_job(browser, "scilink", prompt, [], cfg)
    assert not (Path(no_meta["job_dir"]) / "faradaiq_session_metadata.json").exists()


def test_review_rebuilds_final_service_prompt_from_actual_checked_items():
    source = (ROOT / "faradaiq" / "v239_scilink_edison.py").read_text(encoding="utf-8")
    review = source[source.index("def show_analysis_review"):source.index("def open_consolidated_ai_chat")]
    assert 'final_prompt = _build_analysis_prompt(source, service, scientific_prompt, selected)' in review
    assert '_write_job(source, service, final_prompt, selected, cfg)' in review
    assert "generated after approval inside the new job folder" in source
