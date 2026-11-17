FaradaIQ Studio v273 package-local LabRIC MCP bridge
====================================================

FaradaIQ launches this bridge through SciLink's supported --mcp option.

Transport: stdio
Server name: labric
Windows command: cmd /d /c <FaradaIQ install>\integrated_tools\LabRIC_MCP\run_labric_mcp.bat
macOS/Linux command: <FaradaIQ install>/integrated_tools/LabRIC_MCP/run_labric_mcp.sh
Remote endpoint: LABRIC_MCP_URL, default https://mcp.labric.co/mcp
Authentication: Authorization: Bearer <LABRIC_API_KEY>

The bridge uses FaradaIQ's private .faradaiq_venv and does not require
C:\SciLink\scilink-labric-demo or another separately installed demo package.
The SciLink MCP JSON configuration contains no API key. Credentials are passed
only in the authorized SciLink child-process environment.
