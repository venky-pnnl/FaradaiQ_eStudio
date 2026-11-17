"""FaradaIQ Studio v273 contour-map reliability patch."""
from __future__ import annotations
from typing import Any, Dict

VERSION = "v273"

def install(core: Dict[str, Any]) -> None:
    from faradaiq import multidim_v216 as md
    md.VERSION = VERSION
    md._V216_DEFAULT_OPTIONS["render_mode"] = "Contour + points"
    md._V216_DEFAULT_OPTIONS["interpolation"] = "Linear"
    if isinstance(getattr(md, "_V222_LAST_OPTIONS", None), dict):
        current = md._V222_LAST_OPTIONS
        if current.get("contour_default_generation") is None and current.get("render_mode") == "Measured points":
            current["render_mode"] = "Contour + points"
        current["contour_default_generation"] = 246
    core["FARADAIQ_CONTOUR_DEFAULT"] = "Contour + points"
