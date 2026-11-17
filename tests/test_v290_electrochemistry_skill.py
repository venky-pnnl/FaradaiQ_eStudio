from __future__ import annotations

import hashlib
import json
from pathlib import Path

from faradaiq import v290_electrochemistry_skill as skill

EXPECTED_SKILL_HASH = "624e31ad3754c22df0958b4adf2dd8ffd4fa0932c87546fcc7c26b5b076df06a"
EXPECTED_REGISTRY_HASH = "3e278fd1f9515e98dd0a6eb2e0bbb84413df3d672eea72d5e60c7036c68589e4"
EXPECTED_GUIDANCE_HASH = "3b4028555848a8de29dea1b55828a0d138b799302a0487706930d8fa52ed8550"


def test_v7_skill_is_bundled_unchanged_with_canonical_headings():
    path = Path(skill._required_file(skill.SKILL_FILENAME, "electrochemistry skill"))
    validation = skill.validate_skill_file(path)
    assert validation["revision"] == "7.0-guided-hybrid-analysis"
    assert validation["h2_headings"] == skill.CANONICAL_H2
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_SKILL_HASH


def test_v7_registry_and_analysis_mode_guidance_are_unchanged():
    registry = Path(skill._required_file(skill.MODEL_REGISTRY_FILENAME, "registry"))
    guidance = Path(skill._required_file(skill.ANALYSIS_MODE_GUIDANCE_FILENAME, "guidance"))
    assert hashlib.sha256(registry.read_bytes()).hexdigest() == EXPECTED_REGISTRY_HASH
    assert hashlib.sha256(guidance.read_bytes()).hexdigest() == EXPECTED_GUIDANCE_HASH
    assert skill.validate_model_registry(registry)["model_names"]
    assert skill.validate_analysis_mode_guidance(guidance)["default_mode"] == "standard_scilink"


def test_job_receipt_copies_v7_assets_and_preserves_approved_false(tmp_path):
    job = tmp_path / "job"; job.mkdir()
    request = job / "scilink_request.json"
    request.write_text(json.dumps({"approved": True, "selected_data_paths": []}), encoding="utf-8")
    receipt = skill.prepare_job_skill(job)
    skill.update_request_manifest(request, receipt)
    assert hashlib.sha256((job / "skills/electrochemistry_skill.md").read_bytes()).hexdigest() == EXPECTED_SKILL_HASH
    assert hashlib.sha256((job / "skills/electrodeposition_model_registry.json").read_bytes()).hexdigest() == EXPECTED_REGISTRY_HASH
    assert hashlib.sha256((job / "skills/analysis_mode_guidance.json").read_bytes()).hexdigest() == EXPECTED_GUIDANCE_HASH
    payload = json.loads(request.read_text(encoding="utf-8"))
    assert payload["approved"] is False
    assert payload["electrochemistry_skill"]["version"] == skill.SKILL_REVISION
    assert payload["skill_provenance"]["application_version"] == "v316"


def test_standard_prompt_is_short_and_keeps_tools_available(tmp_path):
    job = tmp_path / "job"; job.mkdir()
    receipt = skill.prepare_job_skill(job)
    effective, path, mode = skill.build_effective_prompt(
        original_prompt="", job_dir=job, receipt=receipt,
        cli_skill_flag_supported=True, routing_guard="", analysis_mode="standard_scilink",
    )
    assert mode == "native_cli_skill_standard_scilink"
    assert "select_agent and run_analysis remain available" in effective
    assert "Do not call select_agent" not in effective
    assert "Do not call run_analysis" not in effective
    assert "Best-of-N" not in effective
    assert len(effective.split()) < 180
    assert path.is_file()
