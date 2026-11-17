"""FaradaIQ Studio v273 package-local LabRIC MCP stdio proxy.

The proxy converts SciLink's local stdio MCP connection into LabRIC's
streamable HTTP MCP endpoint. Credentials are read from the launched process
environment only and are never stored by this module.
"""
from __future__ import annotations

import os
import sys


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    print(f"[FaradaIQ LabRIC MCP] Missing required environment variable: {name}", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    from fastmcp.client.transports import StreamableHttpTransport
    try:
        from fastmcp.server.proxy import create_proxy
    except Exception:
        from fastmcp.server import create_proxy

    transport = StreamableHttpTransport(
        _required("LABRIC_MCP_URL"),
        headers={"Authorization": f"Bearer {_required('LABRIC_API_KEY')}"},
    )
    create_proxy(transport, name="Labric").run(transport="stdio", show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
