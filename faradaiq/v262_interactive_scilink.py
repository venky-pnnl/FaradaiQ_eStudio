"""Compatibility alias for the active FaradaIQ Studio v273 SciLink patch."""
from . import v262_technique_scilink as _impl
for _name, _value in vars(_impl).items():
    if not _name.startswith('__'):
        globals()[_name] = _value
