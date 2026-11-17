"""FaradaIQ Studio v296 final release-identity layer.

Reuses the audited v295 integrity/gating/help implementation while reasserting the
v296 public identity after all historical compatibility installers have loaded.
"""
from __future__ import annotations

from typing import Any, MutableMapping
import importlib

from . import v295_release_integrity as _base

VERSION = "v296"
VERSION_NUMBER = 296
SEMVER = "0.296.0"
APP_NAME = "FaradaIQ Studio"
APP_TITLE = f"{APP_NAME} {VERSION}"
INSTALL_TARGET = r"C:\FaradaIQ\Studio\V296"


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
    # Keep legacy integrity helpers release-consistent when older QA/help code
    # calls them directly after the final release layer has loaded.
    for name in ("v290_release_integrity", "v291_release_integrity", "v292_release_integrity", "v293_release_integrity", "v294_release_integrity", "v295_release_integrity"):
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
    core["_v296_normalize_release_text"] = core.get("_v295_normalize_release_text")
