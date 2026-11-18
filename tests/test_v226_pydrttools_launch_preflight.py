from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_installers_check_and_auto_install_pydrttools():
    for platform in ("install.ps1", "macos_install.py"):
        assert "setup_analysis_tools.py" in (ROOT / "scripts" / platform).read_text(encoding="utf-8")
    for extension in (".bat", ".sh"):
        wrapper = (ROOT / f"install_official_pydrttools_optional{extension}").read_text(encoding="utf-8")
        assert "setup_analysis_tools.py" in wrapper
        assert "--tool pydrttools --auto" in wrapper


def test_checker_accepts_gui_and_eis_loader_contract(tmp_path: Path):
    _write(tmp_path / "cvxopt.py", "__version__='fake'\n")
    _write(tmp_path / "sklearn.py", "__version__='fake'\n")
    _write(tmp_path / "requests.py", "__version__='fake'\n")
    _write(tmp_path / "PyQt5" / "__init__.py", "")
    _write(tmp_path / "PyQt5" / "QtWidgets.py",
           "class QApplication:\n"
           "    @classmethod\n    def instance(cls): return None\n"
           "    def __init__(self, args): pass\n"
           "    def processEvents(self): pass\n"
           "    def platformName(self): return 'offscreen'\n")
    _write(tmp_path / "pyDRTtools" / "__init__.py", "")
    _write(tmp_path / "pyDRTtools" / "GUI.py", "class GUI:\n    def close(self): pass\n")
    _write(
        tmp_path / "pyDRTtools" / "runs.py",
        "class EIS_object:\n    @classmethod\n    def from_file(cls, path):\n        return cls()\n",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path)
    cp = subprocess.run(
        [sys.executable, str(ROOT / "_check_pydrttools_optional.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "PYDRTTOOLS_CHECK=READY" in cp.stdout


def test_installer_preserves_faradaiq_scientific_pins():
    setup = (ROOT / "scripts" / "setup_analysis_tools.py").read_text(encoding="utf-8")
    req = (ROOT / "requirements_official_pydrttools_optional.txt").read_text(encoding="utf-8")
    assert "--no-deps" in setup
    assert "pyDRTtools==0.2" in setup
    assert "--constraint" in setup
    assert "cvxopt" in req
    assert "scikit-learn" in req
    assert "PyQt5" in req
    assert "numpy==1.24.1" not in req
    assert "scipy==1.10.0" not in req
    assert "pandas==1.5.3" not in req
    assert "matplotlib==3.7.3" not in req
