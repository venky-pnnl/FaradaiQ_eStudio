"""Main GUI compatibility wrappers."""
from __future__ import annotations

from typing import Any

from .._legacy import require_attr

def get_main_window_class() -> type[Any]:
    """Return the FaradaIQDataBrowser Tkinter class."""
    return require_attr("FaradaIQDataBrowser")

def get_session_reader_class() -> type[Any]:
    return require_attr("SessionReaderGUI")

def launch_gui() -> int | None:
    return require_attr("_v120_main_final")()
