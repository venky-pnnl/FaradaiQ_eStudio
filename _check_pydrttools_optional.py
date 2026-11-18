"""Fast launch-time readiness check for the optional official pyDRTtools GUI.

This script is intentionally executed in a separate Python process by the
FaradaIQ launcher.  Importing pyDRTtools.GUI selects a Qt Matplotlib backend;
keeping that probe out of the main Tk process avoids changing FaradaIQ's active
plotting backend.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import os
import sys
import traceback

# Permit a non-interactive import on workstations that are launching through a
# remote session.  This does not affect the later real pyDRTtools GUI process.
os.environ["QT_QPA_PLATFORM"] = "offscreen"


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except Exception:
        return "unknown"


def main() -> int:
    print("Python executable:", sys.executable)
    print("Checking optional official ciuccislab pyDRTtools integration...")
    try:
        cvxopt = importlib.import_module("cvxopt")
        sklearn = importlib.import_module("sklearn")
        requests = importlib.import_module("requests")
        widgets = importlib.import_module("PyQt5.QtWidgets")
        application = widgets.QApplication.instance() or widgets.QApplication([])
        gui_module = importlib.import_module("pyDRTtools.GUI")
        runs_module = importlib.import_module("pyDRTtools.runs")

        gui = getattr(gui_module, "GUI", None)
        eis_object = getattr(runs_module, "EIS_object", None)
        if gui is None:
            raise RuntimeError("pyDRTtools.GUI.GUI was not found")
        if eis_object is None or not hasattr(eis_object, "from_file"):
            raise RuntimeError("pyDRTtools.runs.EIS_object.from_file was not found")

        window = gui()
        window.close()
        application.processEvents()

        print("PYDRTTOOLS_CHECK=READY")
        print("pyDRTtools distribution:", _version("pyDRTtools"))
        print("cvxopt:", getattr(cvxopt, "__version__", _version("cvxopt")))
        print("scikit-learn:", getattr(sklearn, "__version__", _version("scikit-learn")))
        print("requests:", getattr(requests, "__version__", _version("requests")))
        print(f"GUI construction and EIS selected-data loader verified ({application.platformName()}).")
        return 0
    except Exception:
        print("PYDRTTOOLS_CHECK=MISSING_OR_BROKEN")
        print(traceback.format_exc())
        print(
            "Run install_official_pydrttools_optional.bat (Windows) or "
            "install_official_pydrttools_optional.command (macOS/Linux)."
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
