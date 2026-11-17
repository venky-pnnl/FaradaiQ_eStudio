"""FaradaIQ Studio v297 final release-identity layer.

Reuses the audited v296 integrity/gating/help implementation while reasserting
v297 after all historical compatibility installers have loaded.
"""
from __future__ import annotations
from typing import Any, MutableMapping
import importlib
from . import v296_release_integrity as _base

VERSION = "v297"
VERSION_NUMBER = 297
SEMVER = "0.297.0"
APP_NAME = "FaradaIQ Studio"
APP_TITLE = f"{APP_NAME} {VERSION}"
INSTALL_TARGET = r"C:\FaradaIQ\Studio\V297"

def _configure_base() -> None:
    _base.VERSION = VERSION
    _base.VERSION_NUMBER = VERSION_NUMBER
    _base.SEMVER = SEMVER
    _base.APP_NAME = APP_NAME
    _base.APP_TITLE = APP_TITLE
    _base.INSTALL_TARGET = INSTALL_TARGET

def install(core: MutableMapping[str, Any]) -> None:
    _configure_base()
    _base.install(core)
    for name in ("v290_release_integrity", "v291_release_integrity", "v292_release_integrity", "v293_release_integrity", "v294_release_integrity", "v295_release_integrity", "v296_release_integrity"):
        try:
            module = importlib.import_module(f"faradaiq.{name}")
            module.VERSION = VERSION
            module.VERSION_NUMBER = VERSION_NUMBER
            module.SEMVER = SEMVER
            module.APP_NAME = APP_NAME
            module.APP_TITLE = APP_TITLE
            module.INSTALL_TARGET = INSTALL_TARGET
        except Exception:
            pass
    core["FARADAIQ_PUBLIC_VERSION"] = VERSION
    core["FARADAIQ_APP_VERSION"] = VERSION
    core["APP_TITLE"] = APP_TITLE
    core["FARADAIQ_RELEASE_VERSION"] = VERSION
    core["FARADAIQ_RELEASE_TITLE"] = APP_TITLE
    core["FARADAIQ_INSTALL_TARGET"] = INSTALL_TARGET
    core["_v297_normalize_release_text"] = core.get("_v296_normalize_release_text") or core.get("_v295_normalize_release_text")
