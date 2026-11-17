from __future__ import annotations

import json
from pathlib import Path

from faradaiq import v261_labric_mcp as mcp


ROOT = Path(__file__).resolve().parents[1]


def test_package_local_mcp_assets_exist_and_do_not_require_external_demo():
    cfg = mcp.connection_config({})
    assert cfg["package_local"] is True
    assert cfg["external_scilink_demo_required"] is False
    assert Path(cfg["proxy_script"]).is_file()
    assert mcp.wrapper_path().is_file()
    assert "scilink-labric-demo" not in cfg["command_display"].lower()


def test_mcp_proxy_is_stdio_to_streamable_http_bearer_bridge():
    text = mcp.proxy_script().read_text(encoding="utf-8")
    assert "StreamableHttpTransport" in text
    assert "Authorization" in text
    assert "Bearer" in text
    assert 'transport="stdio"' in text
    assert "LABRIC_MCP_URL" in text
    assert "LABRIC_API_KEY" in text


def test_enabled_job_receives_supported_scilink_mcp_config_without_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("LABRIC_API_KEY", "lbk_test_value")
    env = {"PYTHONPATH": "existing"}
    report = mcp.apply_process_environment(
        env,
        {
            "scilink_labric_cloud_context_enabled": True,
            "labric_mcp_url": "https://mcp.labric.co/mcp",
        },
        tmp_path,
    )
    assert report["enabled"] is True
    assert report["transport"] == "stdio"
    assert report["server_name"] == "labric"
    assert report["integration_method"] == "supported_scilink_cli_mcp_option"
    assert env["FARADAIQ_LABRIC_MCP_ENABLED"] == "1"
    assert env["LABRIC_API_KEY"] == "lbk_test_value"
    assert env["PYTHONPATH"] == "existing"
    config_path = Path(report["scilink_mcp_config"])
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["name"] == "labric"
    assert payload["transport"] == "stdio"
    assert payload["command"] == report["command"]
    assert "lbk_test_value" not in config_path.read_text(encoding="utf-8")
    assert report["secret_written_to_config"] is False


def test_disabled_job_does_not_write_mcp_config(monkeypatch, tmp_path):
    monkeypatch.delenv("LABRIC_API_KEY", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    env = {"PYTHONPATH": "existing"}
    report = mcp.apply_process_environment(env, {"scilink_labric_cloud_context_enabled": False}, tmp_path)
    assert report["enabled"] is False
    assert env["FARADAIQ_LABRIC_MCP_ENABLED"] == "0"
    assert env["PYTHONPATH"] == "existing"
    assert report["scilink_mcp_config"] == ""
    assert not (tmp_path / "labric_mcp_scilink_config.json").exists()


def test_scilink_cli_uses_supported_mcp_option_and_package_local_config():
    text = (ROOT / "faradaiq_scilink_cli_job.py").read_text(encoding="utf-8")
    assert "apply_process_environment" in text
    assert "labric_mcp_launch_preflight.json" in text
    assert 'cmd.extend(["--mcp", mcp_config])' in text
    assert "supported --mcp JSON configuration" in text
    assert "sitecustomize" not in text


def test_review_and_llm_settings_expose_cloud_context_choice_and_connection_button():
    review = (ROOT / "faradaiq" / "v262_technique_scilink.py").read_text(encoding="utf-8")
    settings = (ROOT / "faradaiq" / "v239_scilink_edison.py").read_text(encoding="utf-8")
    label = "Use LabRIC MCP cloud context for cross-instrument comparison"
    assert label in review
    assert label in settings
    assert "Test LabRIC MCP connection" in settings
    assert "labric_cloud_context_enabled=bool(labric_cloud_var.get())" in review
