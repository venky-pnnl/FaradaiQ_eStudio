"""Analysis engine compatibility wrappers."""
from __future__ import annotations
from typing import Any
from .._legacy import require_attr

def get_analysis_engine_class() -> type[Any]:
    return require_attr("FaradaIQAnalysisEngine")

def create_analysis_engine(*args: Any, **kwargs: Any) -> Any:
    return get_analysis_engine_class()(*args, **kwargs)
