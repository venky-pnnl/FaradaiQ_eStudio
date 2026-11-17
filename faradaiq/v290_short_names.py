"""FaradaIQ Studio v290 public wrapper for the Windows-safe short-name policy.

The underlying implementation was introduced in the v273 development layer.
This wrapper reasserts the v290 release identity before exposing the same tested
implementation, so active GUI controls, JSON policy payloads, jobs, and SciLink
bundles all identify the current release without duplicating the policy engine.
"""
from __future__ import annotations

from . import v273_short_names as _impl

VERSION = "v290"
VERSION_NUMBER = 290
SEMVER = "0.290.0"
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
VERSION = "v290"
VERSION_NUMBER = 290
SEMVER = "0.290.0"
APP_NAME = "FaradaIQ Studio"
APP_TITLE = f"{APP_NAME} {VERSION}"
INSTALL_TARGET = rf"C:\FaradaIQ\Studio\V{VERSION_NUMBER}"

# ---- FaradaIQ Studio v291 public release identity ----
VERSION = "v292"
VERSION_NUMBER = 291
SEMVER = "0.291.0"
APP_NAME = "FaradaIQ Studio"
APP_TITLE = f"{APP_NAME} {VERSION}"
# ---- End FaradaIQ Studio v291 public release identity ----
