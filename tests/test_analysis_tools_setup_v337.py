"""Optional analysis tools install only after a failed local readiness check."""
import json
from pathlib import Path

import pytest

from scripts import setup_analysis_tools as setup


@pytest.fixture
def target(tmp_path, monkeypatch):
    root = tmp_path / 'eStudio V337'
    root.mkdir()
    python = root / '.faradaiq_venv_macos' / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(setup, 'private_python', lambda _root: python)
    return root, python


def test_setup_reuses_verified_tools_without_pip(target, monkeypatch):
    root, python = target
    calls = []
    def execute(command, **_kwargs):
        calls.append([str(part) for part in command])
        return 0
    monkeypatch.setattr(setup, 'run_logged', execute)
    assert setup.setup(root) == 0
    assert calls == [[str(python), str(root / '_check_deareis_optional.py')],
                     [str(python), str(root / '_check_pydrttools_optional.py')]]
    receipt = json.loads((root / 'analysis_tools_status_v337.json').read_text())
    assert receipt['tools']['DearEIS']['status'] == receipt['tools']['pyDRTtools']['status'] == 'already_ready'


def test_setup_recovers_and_checks_both_tools_using_target_python(target, monkeypatch):
    root, python = target
    calls = []
    attempts = {}
    def execute(command, **_kwargs):
        calls.append([str(part) for part in command])
        stage = Path(command[1]).name
        attempts[stage] = attempts.get(stage, 0) + 1
        return 2 if stage.startswith('_check') and attempts[stage] == 1 else 0
    monkeypatch.setattr(setup, 'run_logged', execute)
    assert setup.setup(root) == 0
    deareis = next(command for command in calls if '_install_deareis_optional.py' in command[1])
    assert deareis[0] == str(python)
    assert deareis[deareis.index('--seed-python') + 1] == str(python)
    assert '--auto' in deareis
    prereq, official = [command for command in calls if 'pip' in command]
    assert '--constraint' in prereq and '--only-binary=:all:' in prereq
    assert '--no-deps' in official and 'pyDRTtools==0.2' in official
    assert all(command[0] == str(python) for command in calls)
    receipt = json.loads((root / 'analysis_tools_status_v337.json').read_text())
    assert receipt['tools']['DearEIS']['status'] == receipt['tools']['pyDRTtools']['status'] == 'repaired'


def test_failed_deareis_stage_keeps_pydrttools_and_bundled_drt_available(target, monkeypatch):
    root, _ = target
    def execute(command, **_kwargs):
        return 2 if 'deareis' in Path(command[1]).name.casefold() else 0
    monkeypatch.setattr(setup, 'run_logged', execute)
    assert setup.setup(root) == 1
    receipt = json.loads((root / 'analysis_tools_status_v337.json').read_text())
    assert receipt['tools']['DearEIS']['status'] == 'needs_attention'
    assert receipt['tools']['pyDRTtools']['status'] == 'already_ready'
    assert receipt['bundled_drt_available']


def test_apple_silicon_qt_dependency_is_optional_and_binary_pinned():
    root = Path(__file__).resolve().parents[1]
    optional = (root / 'requirements_official_pydrttools_optional.txt').read_text(encoding='utf-8')
    core = (root / 'requirements_macos.txt').read_text(encoding='utf-8')
    assert 'PyQt5==5.15.10' in optional
    assert 'PyQt5-Qt5==5.15.19; sys_platform == "darwin"' in optional
    assert "platform_machine != 'arm64'" in core
