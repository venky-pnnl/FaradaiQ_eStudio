"""FaradaIQ Studio v315 release identity and full potential-dependent EIS modeling."""
from __future__ import annotations
from typing import Any, MutableMapping
import importlib
from . import v314_release_integrity as _base

VERSION="v315"; VERSION_NUMBER=315; SEMVER="0.315.0"; APP_NAME="FaradaIQ Studio"; APP_TITLE=f"{APP_NAME} {VERSION}"; INSTALL_TARGET=r"C:\FaradaIQ\Studio\V315"

def _configure_base() -> None:
    _base.VERSION=VERSION; _base.VERSION_NUMBER=VERSION_NUMBER; _base.SEMVER=SEMVER
    _base.APP_NAME=APP_NAME; _base.APP_TITLE=APP_TITLE; _base.INSTALL_TARGET=INSTALL_TARGET


def install(core: MutableMapping[str, Any]) -> None:
    _configure_base(); _base.install(core)
    for name in ("v290_portable_browser","v290_powerpoint_summary","browser_ui_v209","v233_batch_jobs"):
        try:
            m=importlib.import_module(f"faradaiq.{name}")
            if hasattr(m,"VERSION"): m.VERSION=VERSION
            if hasattr(m,"APP_TITLE"): m.APP_TITLE=APP_TITLE
            if name=="v290_portable_browser" and hasattr(m,"SCHEMA"): m.SCHEMA="faradaiq-portable-browser-v315"
            if name=="v290_powerpoint_summary" and hasattr(m,"FRONT_SLIDE_FOOTER"): m.FRONT_SLIDE_FOOTER=f"Made by FaradaIQ Studio {VERSION}"
        except Exception:
            pass
    try:
        from .v315_potential_eis_modeling import install as _install
        _install(core)
    except Exception as exc:
        try: print(f"[FaradaIQ v315] potential-dependent EIS modeling patch warning: {exc}")
        except Exception: pass
    core["FARADAIQ_PUBLIC_VERSION"]=VERSION; core["FARADAIQ_APP_VERSION"]=VERSION; core["APP_TITLE"]=APP_TITLE
    core["FARADAIQ_RELEASE_VERSION"]=VERSION; core["FARADAIQ_RELEASE_TITLE"]=APP_TITLE; core["FARADAIQ_INSTALL_TARGET"]=INSTALL_TARGET
    core["FARADAIQ_DEFAULT_OUTPUT_DIR"] = r"C:\Data\ANALYSIS\Faradaiq"
