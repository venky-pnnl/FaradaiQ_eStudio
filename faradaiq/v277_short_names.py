"""FaradaIQ Studio v277 public wrapper for the Windows-safe short-name policy.

The underlying implementation was introduced in the v273 development layer.
This wrapper reasserts the v277 release identity before exposing the same tested
implementation, so active GUI controls, JSON policy payloads, jobs, and SciLink
bundles all identify the current release without duplicating the policy engine.
"""
from __future__ import annotations

from . import v273_short_names as _impl

VERSION = "v277"
VERSION_NUMBER = 277
SEMVER = "0.277.0"
APP_NAME = "FaradaIQ Studio"
APP_TITLE = f"{APP_NAME} {VERSION}"
INSTALL_TARGET = rf"C:\FaradaIQ\Studio\V{VERSION_NUMBER}"

# The implementation functions retain their defining module globals, so update
# those globals before re-exporting the public API.
_impl.VERSION = VERSION
_impl.VERSION_NUMBER = VERSION_NUMBER
_impl.SEMVER = SEMVER
_impl.APP_NAME = APP_NAME
_impl.APP_TITLE = APP_TITLE
_impl.INSTALL_TARGET = INSTALL_TARGET

from .v273_short_names import *  # noqa: F401,F403,E402

# Reassert after star import, which also imports the implementation constants.
VERSION = "v277"
VERSION_NUMBER = 277
SEMVER = "0.277.0"
APP_NAME = "FaradaIQ Studio"
APP_TITLE = f"{APP_NAME} {VERSION}"
INSTALL_TARGET = rf"C:\FaradaIQ\Studio\V{VERSION_NUMBER}"
