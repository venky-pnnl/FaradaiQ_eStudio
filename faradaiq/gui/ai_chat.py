"""AI chat window compatibility wrappers."""
from __future__ import annotations
from typing import Any
from .._legacy import require_attr

def get_ai_chat_window_class() -> type[Any]:
    """Return the newest AI chat window class exposed by the legacy GUI."""
    for name in ("FaradaIQAIChatWindowV105Prompt", "FaradaIQAIChatWindowV105", "FaradaIQAIChatWindowV101", "FaradaIQAIChatWindow"):
        try:
            return require_attr(name)
        except AttributeError:
            continue
    raise AttributeError("No FaradaIQ AI chat window class was found.")
