"""FaradaIQ Studio v299 final release identity layer."""
from __future__ import annotations
from typing import Any, MutableMapping
import importlib
from . import v298_release_integrity as _base
VERSION="v299"; VERSION_NUMBER=299; SEMVER="0.299.0"; APP_NAME="FaradaIQ Studio"; APP_TITLE=f"{APP_NAME} {VERSION}"; INSTALL_TARGET=r"C:\FaradaIQ\Studio\V299"
def _configure_base():
    _base.VERSION=VERSION; _base.VERSION_NUMBER=VERSION_NUMBER; _base.SEMVER=SEMVER; _base.APP_NAME=APP_NAME; _base.APP_TITLE=APP_TITLE; _base.INSTALL_TARGET=INSTALL_TARGET
def install(core: MutableMapping[str, Any]) -> None:
    _configure_base(); _base.install(core)
    for name in ("v290_release_integrity","v291_release_integrity","v292_release_integrity","v293_release_integrity","v294_release_integrity","v295_release_integrity","v296_release_integrity","v297_release_integrity","v298_release_integrity"):
        try:
            m=importlib.import_module(f"faradaiq.{name}"); m.VERSION=VERSION; m.VERSION_NUMBER=VERSION_NUMBER; m.SEMVER=SEMVER; m.APP_NAME=APP_NAME; m.APP_TITLE=APP_TITLE; m.INSTALL_TARGET=INSTALL_TARGET
        except Exception: pass
    core["FARADAIQ_PUBLIC_VERSION"]=VERSION; core["FARADAIQ_APP_VERSION"]=VERSION; core["APP_TITLE"]=APP_TITLE; core["FARADAIQ_RELEASE_VERSION"]=VERSION; core["FARADAIQ_RELEASE_TITLE"]=APP_TITLE; core["FARADAIQ_INSTALL_TARGET"]=INSTALL_TARGET
