"""FaradaIQ Studio v309 potential-aware EIS/GEIS/DRT reporting release identity."""
from __future__ import annotations
from typing import Any, MutableMapping
import importlib
from . import v308_release_integrity as _base

VERSION="v309"; VERSION_NUMBER=309; SEMVER="0.309.0"; APP_NAME="FaradaIQ Studio"; APP_TITLE=f"{APP_NAME} {VERSION}"; INSTALL_TARGET=r"C:\FaradaIQ\Studio\V309"

def _configure_base() -> None:
    _base.VERSION=VERSION; _base.VERSION_NUMBER=VERSION_NUMBER; _base.SEMVER=SEMVER
    _base.APP_NAME=APP_NAME; _base.APP_TITLE=APP_TITLE; _base.INSTALL_TARGET=INSTALL_TARGET

def install(core: MutableMapping[str, Any]) -> None:
    _configure_base(); _base.install(core)
    for name in ("v290_portable_browser","v290_powerpoint_summary","browser_ui_v209","v233_batch_jobs"):
        try:
            m=importlib.import_module(f"faradaiq.{name}")
            if hasattr(m,"VERSION"): m.VERSION=VERSION
            if name=="v290_portable_browser" and hasattr(m,"SCHEMA"): m.SCHEMA="faradaiq-portable-browser-v309"
        except Exception: pass
    core["FARADAIQ_PUBLIC_VERSION"]=VERSION; core["FARADAIQ_APP_VERSION"]=VERSION; core["APP_TITLE"]=APP_TITLE
    core["FARADAIQ_RELEASE_VERSION"]=VERSION; core["FARADAIQ_RELEASE_TITLE"]=APP_TITLE; core["FARADAIQ_INSTALL_TARGET"]=INSTALL_TARGET
