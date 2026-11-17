from __future__ import annotations

import json
from pathlib import Path

import faradaiq.v244_workflow_cleanup as workflow
import faradaiq_scilink_cli_job as scilink_job

ROOT = Path(__file__).resolve().parents[1]


def _settings(provider: str = "Ollama") -> dict:
    return {
        "llm": {
            "provider": provider,
            "model": "gpt-oss:120b-cloud",
            "base_url": "http://localhost:11434/v1",
            "api_key_present": True,
        },
        "labric_mcp_enabled": False,
        "labric_mcp_url": "https://mcp.labric.co/mcp",
        "edison_enabled": True,
        "edison_api_key_present": True,
    }


def test_ollama_preflight_isolates_stale_anthropic_and_forces_litellm_provider():
    base = {
        "ANTHROPIC_API_KEY": "stale-key",
        "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
        "AWS_BEDROCK_MODEL_ID": "anthropic.claude-test",
        "OPENAI_API_KEY": "old-cloud-key",
        "PATH": "C:/Windows",
    }
    env, report = scilink_job.prepare_scilink_environment(_settings(), base)
    assert report["status"] == "ready"
    assert report["provider_key"] == "ollama"
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert "AWS_BEDROCK_MODEL_ID" not in env
    assert env["LITELLM_MODEL"] == "gpt-oss:120b-cloud"
    assert env["SCILINK_LLM_PROVIDER"] == "ollama"
    assert env["OPENAI_BASE_URL"] == "http://localhost:11434/v1"
    assert env["OLLAMA_HOST"] == "http://localhost:11434"
    assert env["OPENAI_API_KEY"] == "ollama"
    assert "ANTHROPIC_API_KEY" in report["cleared_environment_variables"]


def test_preflight_blocks_non_ascii_header_secret_without_disclosing_value():
    secret = "abcdefghijklmnopqrstu—invalid"
    env, report = scilink_job.prepare_scilink_environment(
        {
            "llm": {
                "provider": "Custom",
                "model": "model-1",
                "base_url": "https://example.invalid/v1",
            }
        },
        {"CUSTOM_LLM_API_KEY": secret},
    )
    assert report["status"] == "blocked"
    serialized = json.dumps(report)
    assert "CUSTOM_LLM_API_KEY" in serialized
    assert secret not in serialized
    assert "—" not in serialized
    assert env["CUSTOM_LLM_API_KEY"] == secret



def test_preflight_ignores_unrelated_application_passwords():
    unrelated = "local—password"
    _env, report = scilink_job.prepare_scilink_environment(
        _settings(),
        {"UNRELATED_APP_PASSWORD": unrelated},
    )
    assert report["status"] == "ready"
    assert "UNRELATED_APP_PASSWORD" not in json.dumps(report)

def test_typographic_metadata_is_ascii_normalized_but_prompt_policy_remains_utf8():
    settings = {
        "llm": {
            "provider": "Ollama — local",
            "model": "gpt-oss:120b-cloud",
            "base_url": "http://localhost:11434/v1",
        }
    }
    _env, report = scilink_job.prepare_scilink_environment(settings, {})
    assert report["status"] == "ready"
    assert report["selected_provider"] == "Ollama - local"
    assert any("UTF-8" in note for note in report["notes"])


def test_edison_service_details_match_official_client_route():
    settings = workflow._service_settings({})
    assert settings["edison_client_package"] == "edison-client>=0.15.0"
    assert settings["edison_import_name"] == "edison_client"
    assert settings["edison_base_url"] == "https://platform.edisonscientific.com/"
    assert settings["edison_upload_url"] == ""
    assert settings["edison_profile"].endswith("/profile")
    text = workflow._service_details_text("edison", {})
    assert "from edison_client import EdisonClient, JobNames" in text
    assert "run_tasks_until_done" in text
    assert "https://edison.science/" not in text


def test_cv_visualizer_uses_one_continuous_slider_and_play_pause():
    source = (ROOT / "faradaiq" / "v247_experience.py").read_text(encoding="utf-8")
    block = source[source.index("def _continuous_cv_cycle_view"):source.index("def _generic_with_play")]
    assert '"slider_layout": "continuous_forward_then_reverse"' in block
    assert '"play_pause": True' in block
    assert 'updatemenus=_animation_buttons(260)' in block
    assert 'currentvalue={"prefix": "Scan: ", "visible": True}' in block
    assert 'Forward scan potential' not in block
    assert 'Reverse / return scan potential' not in block


def test_scilink_launcher_writes_preflight_launch_config_and_exact_prompt_audit():
    source = (ROOT / "faradaiq_scilink_cli_job.py").read_text(encoding="utf-8")
    assert "scilink_preflight.json" in source
    assert "scilink_launch_config.json" in source
    assert "scilink_prompt_submission.json" in source
    assert "SCILINK_INITIAL_PROMPT" in source
    assert "SCILINK_USER_PROMPT" in source
    assert "SCILINK_QUERY" in source
    assert "BEGIN USER PROMPT" in source
    assert '"exact_prompt_preserved": True' in source
    assert "LITELLM_NUM_RETRIES" in source
    assert "SCILINK_CONFIG_FILE" in source
    assert "Provider isolation cleared" in source
