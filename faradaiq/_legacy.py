"""Compatibility loader for the FaradaIQ Studio core compatibility script.

Do not add new application logic here. This module exists so stable faradaiq.* entry points can call the core implementation while pieces are gradually extracted into focused modules.
"""
from __future__ import annotations

import importlib
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

LEGACY_MODULE_NAME = "faradaiq_studio_core"

def project_root() -> Path:
    return Path(__file__).resolve().parents[1]

def ensure_project_root_on_path() -> Path:
    root = project_root()
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    return root

@lru_cache(maxsize=1)
def legacy_module() -> ModuleType:
    ensure_project_root_on_path()
    return importlib.import_module(LEGACY_MODULE_NAME)

def require_attr(name: str) -> Any:
    module = legacy_module()
    try:
        return getattr(module, name)
    except AttributeError as exc:
        raise AttributeError(f"FaradaIQ core module does not define {name!r}") from exc

def clear_legacy_cache() -> None:
    legacy_module.cache_clear()
