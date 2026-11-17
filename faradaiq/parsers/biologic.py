"""BioLogic parser wrappers from the legacy FaradaIQ script."""
from __future__ import annotations
from .._legacy import require_attr

BioLogicParser = require_attr("BioLogicParser")
MPRfile = require_attr("MPRfile")
MPTfile = require_attr("MPTfile")
MPTfileCSV = require_attr("MPTfileCSV")
parse_BioLogic_date = require_attr("parse_BioLogic_date")
