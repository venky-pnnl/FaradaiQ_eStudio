"""Centralized path helpers for the bundled FaradaIQ distribution."""
from __future__ import annotations

from pathlib import Path

def package_root() -> Path:
    """Return the top-level FaradaIQ bundle folder."""
    return Path(__file__).resolve().parents[2]

def bundled_path(*parts: str) -> Path:
    """Return an absolute path inside the FaradaIQ bundle."""
    return package_root().joinpath(*parts)
