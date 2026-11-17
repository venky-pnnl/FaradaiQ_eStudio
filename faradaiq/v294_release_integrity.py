"""FaradaIQ Studio v294 final release-identity layer.

Reuses the audited v293 integrity/gating/help implementation while reasserting the
v294 public identity after all historical compatibility installers have loaded.
"""
from __future__ import annotations

from typing import Any, MutableMapping

from . import v293_release_integrity as _base

VERSION = "v294"
VERSION_NUMBER = 294
SEMVER = "0.294.0"
APP_NAME = "FaradaIQ Studio"
APP_TITLE = f"{APP_NAME} {VERSION}"
INSTALL_TARGET = r"C:\FaradaIQ\Studio\V294"


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
    core["FARADAIQ_PUBLIC_VERSION"] = VERSION
    core["FARADAIQ_APP_VERSION"] = VERSION
    core["APP_TITLE"] = APP_TITLE
    core["FARADAIQ_RELEASE_VERSION"] = VERSION
    core["FARADAIQ_RELEASE_TITLE"] = APP_TITLE
    core["FARADAIQ_INSTALL_TARGET"] = INSTALL_TARGET
    core["_v294_normalize_release_text"] = core.get("_v293_normalize_release_text")
