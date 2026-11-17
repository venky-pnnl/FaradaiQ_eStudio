"""Compatibility alias for the FaradaIQ Studio v273 LabRIC MCP bridge."""
from . import v261_labric_mcp as _impl
for _name, _value in vars(_impl).items():
    if not _name.startswith('__'):
        globals()[_name] = _value
