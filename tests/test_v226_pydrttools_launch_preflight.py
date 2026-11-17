from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_launchers_check_and_auto_install_pydrttools():
    bat = (ROOT / "Start_FaradaIQ_Studio_v320.bat").read_text(encoding="utf-8")
    command = (ROOT / "Start_FaradaIQ_Studio_v316.command").read_text(encoding="utf-8")
    sh = (ROOT / "Start_FaradaIQ_Studio_v316.sh").read_text(encoding="utf-8")
    for text in (bat, command, sh):
        assert "_check_pydrttools_optional.py" in text
        assert "install_official_pydrttools_optional" in text
        assert "pyDRTtools" in text
    assert 'if exist "_check_pydrttools_optional.py"' in bat
    assert 'call "%CD%\\install_official_pydrttools_optional.bat" --auto' in bat
    assert 'start "FaradaIQ pyDRTtools installer v316"' in bat


def test_checker_accepts_gui_and_eis_loader_contract(tmp_path: Path):
    _write(tmp_path / "cvxopt.py", "__version__='fake'\n")
    _write(tmp_path / "sklearn.py", "__version__='fake'\n")
    _write(tmp_path / "requests.py", "__version__='fake'\n")
    _write(tmp_path / "PyQt5" / "__init__.py", "")
    _write(tmp_path / "pyDRTtools" / "__init__.py", "")
    _write(tmp_path / "pyDRTtools" / "GUI.py", "class GUI: pass\n")
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
    bat = (ROOT / "install_official_pydrttools_optional.bat").read_text(encoding="utf-8")
    req = (ROOT / "requirements_official_pydrttools_optional.txt").read_text(encoding="utf-8")
    assert "--no-deps" in bat
    assert "ciuccislab/pyDRTtools" in bat
    assert "cvxopt" in req
    assert "scikit-learn" in req
    assert "PyQt5" in req
    assert "numpy==1.24.1" not in req
    assert "scipy==1.10.0" not in req
    assert "pandas==1.5.3" not in req
    assert "matplotlib==3.7.3" not in req
