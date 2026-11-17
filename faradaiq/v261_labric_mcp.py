"""Package-local LabRIC MCP integration for FaradaIQ Studio v290."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

VERSION = "v290"
DEFAULT_URL = "https://mcp.labric.co/mcp"
SERVER_NAME = "labric"
TRANSPORT = "stdio"


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def tool_root() -> Path:
    return package_root() / "integrated_tools" / "LabRIC_MCP"


def proxy_script() -> Path:
    return tool_root() / "labric_mcp_proxy.py"


def faradaiq_python() -> Path:
    root = package_root()
    candidate = (
        root / ".faradaiq_venv" / "Scripts" / "python.exe"
        if os.name == "nt"
        else root / ".faradaiq_venv" / "bin" / "python"
    )
    return candidate if candidate.is_file() else Path(sys.executable).resolve()


def wrapper_path() -> Path:
    return tool_root() / ("run_labric_mcp.bat" if os.name == "nt" else "run_labric_mcp.sh")


def command_list() -> list[str]:
    """Return the package-local stdio command supplied to SciLink --mcp."""
    wrapper = wrapper_path()
    if os.name == "nt":
        return ["cmd", "/d", "/c", str(wrapper)]
    return [str(wrapper)]


def command_display() -> str:
    wrapper = wrapper_path()
    if os.name == "nt":
        return f'cmd /d /c "{wrapper}"'
    return str(wrapper)


def resolve_api_key(settings: Mapping[str, Any] | None = None) -> str:
    settings = settings or {}
    return str(
        settings.get("labric_api_key_session")
        or os.environ.get("LABRIC_API_KEY")
        or os.environ.get("MCP_AUTH_TOKEN")
        or ""
    ).strip()


def connection_config(settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    return {
        "transport": TRANSPORT,
        "server_name": SERVER_NAME,
        "command": command_list(),
        "command_display": command_display(),
        "proxy_script": str(proxy_script()),
        "python": str(faradaiq_python()),
        "url": str(settings.get("labric_mcp_url") or DEFAULT_URL),
        "package_local": True,
        "external_scilink_demo_required": False,
    }


def validate_local_runtime() -> dict[str, Any]:
    cfg = connection_config({})
    missing = [name for name, path in (
        ("FaradaIQ private Python", Path(cfg["python"])),
        ("LabRIC MCP proxy", Path(cfg["proxy_script"])),
        ("LabRIC MCP wrapper", wrapper_path()),
    ) if not path.is_file()]
    return {**cfg, "ready": not missing, "missing": missing}


def write_scilink_mcp_config(job_dir: Path, settings: Mapping[str, Any]) -> Path:
    """Write a secret-free SciLink --mcp JSON configuration.

    The package-local proxy receives LABRIC_API_KEY and LABRIC_MCP_URL from the
    authorized SciLink child-process environment. The key is therefore never
    written into this file.
    """
    cfg = connection_config(settings)
    payload = {
        "name": SERVER_NAME,
        "transport": TRANSPORT,
        "command": cfg["command"],
    }
    path = Path(job_dir) / "labric_mcp_scilink_config.json"
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return path


async def _test_async(url: str, api_key: str) -> dict[str, Any]:
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
        names = sorted(
            str(getattr(tool, "name", None) or (tool.get("name") if isinstance(tool, dict) else tool))
            for tool in tools
        )
    return {
        "status": "connected",
        "url": url,
        "tool_count": len(names),
        "tools": names,
        "expected_tools_present": all(name in names for name in ("get_schema", "execute_sql")),
    }


def test_connection(url: str, api_key: str, timeout_s: float = 30.0) -> dict[str, Any]:
    url = str(url or DEFAULT_URL).strip()
    api_key = str(api_key or "").strip()
    if not api_key:
        return {"status": "blocked", "error": "LABRIC_API_KEY is missing", "url": url}
    runtime = validate_local_runtime()
    if not runtime["ready"]:
        return {"status": "blocked", "error": "; ".join(runtime["missing"]), "url": url}

    async def runner() -> dict[str, Any]:
        return await asyncio.wait_for(_test_async(url, api_key), timeout=timeout_s)

    try:
        return asyncio.run(runner())
    except RuntimeError as exc:
        if "asyncio.run() cannot be called" not in str(exc):
            return {"status": "error", "error": repr(exc), "url": url}
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(runner())
        except Exception as inner:
            return {"status": "error", "error": repr(inner), "url": url}
        finally:
            loop.close()
    except Exception as exc:
        return {"status": "error", "error": repr(exc), "url": url}


def apply_process_environment(
    env: dict[str, str],
    settings: Mapping[str, Any],
    job_dir: Path,
) -> dict[str, Any]:
    enabled = bool(
        settings.get("scilink_labric_cloud_context_enabled")
        or settings.get("labric_mcp_enabled")
    )
    cfg = connection_config(settings)
    key = resolve_api_key(settings)
    env["FARADAIQ_LABRIC_MCP_ENABLED"] = "1" if enabled else "0"
    env["FARADAIQ_LABRIC_MCP_SERVER_NAME"] = SERVER_NAME
    env["FARADAIQ_LABRIC_MCP_TRANSPORT"] = TRANSPORT
    env["LABRIC_MCP_URL"] = cfg["url"]
    env["MCP_SERVER_URL"] = cfg["url"]
    if key:
        env["LABRIC_API_KEY"] = key
        env["MCP_AUTH_TOKEN"] = key
    else:
        env.pop("LABRIC_API_KEY", None)
        env.pop("MCP_AUTH_TOKEN", None)

    config_path = write_scilink_mcp_config(job_dir, settings) if enabled else None
    return {
        **cfg,
        "enabled": enabled,
        "api_key_available": bool(key),
        "scilink_mcp_config": str(config_path) if config_path else "",
        "secret_written_to_config": False,
        "integration_method": "supported_scilink_cli_mcp_option",
    }
