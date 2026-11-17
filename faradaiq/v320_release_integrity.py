"""FaradaIQ Studio v320 release identity and Mott-Schottky PowerPoint log-axis rendering enforcement."""
from __future__ import annotations
from typing import Any, MutableMapping
import importlib
from . import v319_release_integrity as _base

VERSION="v320"; VERSION_NUMBER=320; SEMVER="0.320.0"; APP_NAME="FaradaIQ Studio"; APP_TITLE=f"{APP_NAME} {VERSION}"; INSTALL_TARGET=r"C:\FaradaIQ\Studio\V320"

def _configure_base() -> None:
    _base.VERSION=VERSION; _base.VERSION_NUMBER=VERSION_NUMBER; _base.SEMVER=SEMVER
    _base.APP_NAME=APP_NAME; _base.APP_TITLE=APP_TITLE; _base.INSTALL_TARGET=INSTALL_TARGET


def _apply_public_identity() -> None:
    for name in (
        "v290_portable_browser","v290_powerpoint_summary","browser_ui_v209","v233_batch_jobs",
        "v250_reliability","v290_electrochemistry_skill",
    ):
        try:
            m=importlib.import_module(f"faradaiq.{name}")
            if hasattr(m,"VERSION"): m.VERSION=VERSION
            if hasattr(m,"APP_VERSION"): m.APP_VERSION=SEMVER
            if hasattr(m,"APPLICATION_SEMVER"): m.APPLICATION_SEMVER=SEMVER
            if hasattr(m,"APP_TITLE"): m.APP_TITLE=APP_TITLE
            if name=="v290_portable_browser" and hasattr(m,"SCHEMA"):
                m.SCHEMA="faradaiq-portable-browser-v320"
            if name=="v290_powerpoint_summary" and hasattr(m,"FRONT_SLIDE_FOOTER"):
                m.FRONT_SLIDE_FOOTER=f"Made by FaradaIQ Studio {VERSION}"
        except Exception:
            pass
    try:
        canonical=importlib.import_module("faradaiq_canonical_bundle")
        canonical.VERSION=VERSION; canonical.FARADAIQ_PUBLIC_VERSION=VERSION
    except Exception:
        pass


def install(core: MutableMapping[str, Any]) -> None:
    _configure_base(); _base.install(core); _apply_public_identity(); _apply_public_identity()
    core["FARADAIQ_PUBLIC_VERSION"]=VERSION; core["FARADAIQ_APP_VERSION"]=VERSION; core["APP_TITLE"]=APP_TITLE
    core["FARADAIQ_RELEASE_VERSION"]=VERSION; core["FARADAIQ_RELEASE_TITLE"]=APP_TITLE; core["FARADAIQ_INSTALL_TARGET"]=INSTALL_TARGET
    core["FARADAIQ_DEFAULT_OUTPUT_DIR"] = r"C:\Data\ANALYSIS\Faradaiq"
