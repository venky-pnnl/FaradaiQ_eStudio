"""FaradaIQ Studio v308 release identity and repeated-EIS DRT acquisition integrity."""
from __future__ import annotations
from typing import Any, MutableMapping
import importlib
from . import v307_release_integrity as _base

VERSION="v308"; VERSION_NUMBER=308; SEMVER="0.308.0"; APP_NAME="FaradaIQ Studio"; APP_TITLE=f"{APP_NAME} {VERSION}"; INSTALL_TARGET=r"C:\FaradaIQ\Studio\V308"

def _configure_base() -> None:
    _base.VERSION=VERSION; _base.VERSION_NUMBER=VERSION_NUMBER; _base.SEMVER=SEMVER
    _base.APP_NAME=APP_NAME; _base.APP_TITLE=APP_TITLE; _base.INSTALL_TARGET=INSTALL_TARGET

def install(core: MutableMapping[str, Any]) -> None:
    _configure_base(); _base.install(core)
    for name in (
        "v290_release_integrity","v291_release_integrity","v292_release_integrity","v293_release_integrity",
        "v294_release_integrity","v295_release_integrity","v296_release_integrity","v297_release_integrity",
        "v298_release_integrity","v299_release_integrity","v300_release_integrity","v302_release_integrity",
        "v303_release_integrity","v304_release_integrity","v307_release_integrity",
    ):
        try:
            m=importlib.import_module(f"faradaiq.{name}")
            m.VERSION=VERSION; m.VERSION_NUMBER=VERSION_NUMBER; m.SEMVER=SEMVER
            m.APP_NAME=APP_NAME; m.APP_TITLE=APP_TITLE; m.INSTALL_TARGET=INSTALL_TARGET
        except Exception:
            pass
    for name in (
        "v290_portable_browser","v290_powerpoint_summary","ocp_handoff_v291","corrosion_metrics_v298",
        "report_results_v302","takedata_v302","ui_tabs_v216","ui_tabs_v217","ui_tabs_v226",
        "browser_ui_v209","v244_workflow_cleanup","runtime_v217","loading_ui_v225","v228_intelligence","v239_scilink_edison",
    ):
        try:
            m=importlib.import_module(f"faradaiq.{name}")
            if hasattr(m,"VERSION"): m.VERSION=VERSION
            if hasattr(m,"VERSION_NUMBER"): m.VERSION_NUMBER=VERSION_NUMBER
            if hasattr(m,"SEMVER"): m.SEMVER=SEMVER
            if hasattr(m,"APP_TITLE"): m.APP_TITLE=APP_TITLE
            if name=="v290_portable_browser" and hasattr(m,"SCHEMA"): m.SCHEMA="faradaiq-portable-browser-v308"
        except Exception:
            pass
    core["FARADAIQ_PUBLIC_VERSION"]=VERSION; core["FARADAIQ_APP_VERSION"]=VERSION; core["APP_TITLE"]=APP_TITLE
    core["FARADAIQ_RELEASE_VERSION"]=VERSION; core["FARADAIQ_RELEASE_TITLE"]=APP_TITLE; core["FARADAIQ_INSTALL_TARGET"]=INSTALL_TARGET
