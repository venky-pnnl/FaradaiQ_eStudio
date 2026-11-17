"""HEKA/PatchMaster/POTMASTER parser wrappers."""
from __future__ import annotations
from .._legacy import require_attr

HekaSection = require_attr("HekaSection")
HekaTraceRec = require_attr("HekaTraceRec")
HekaEISGroup = require_attr("HekaEISGroup")
HekaBlock = require_attr("HekaBlock")
HEKAPotmasterParser = require_attr("HEKAPotmasterParser")
