from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_controlled_installer_has_no_application_prompts_and_autostarts():
    text = _read("Install_FaradaIQ_Studio_v320.bat")
    lower = text.lower()
    assert 'set "install_root=c:\\faradaiq\\studio"' in lower
    assert 'set "version_folder=v316"' in lower
    assert 'call "%install_dir%\\%start_bat%" --install-only' in lower
    assert 'start "faradaiq studio v316"' in lower
    assert "set /p" not in lower
    assert "choice /c" not in lower
    assert not any(line.strip().lower() == "pause" for line in text.splitlines())


def test_launcher_uses_powershell_bootstrap_instead_of_batch_labels():
    text = _read("Start_FaradaIQ_Studio_v320.bat")
    lower = text.lower()
    assert "_ensure_faradaiq_venv_v316.ps1" in lower
    assert "-allowpythoninstall" in lower
    assert "call :find_seed_python" not in lower
    assert ":find_seed_python" not in lower
    assert "py install" not in lower
    assert "set /p" not in lower
    # Automatic dependency/bootstrap work asks no questions; the one allowed terminal input is the post-bootstrap AI service selector.
    assert 'choice /c 123' in lower
    install_only_pos = lower.index('if "%install_only%"=="1"')
    choice_pos = lower.index('choice /c 123')
    assert choice_pos > install_only_pos
    assert not any(line.strip().lower() == "pause" for line in text.splitlines())


def test_python_bootstrap_prefers_installed_312_and_only_uses_winget_as_fallback():
    text = _read("_ensure_faradaiq_venv_v316.ps1")
    index_312 = text.index('Arguments = @("-3.12")')
    index_311 = text.index('Arguments = @("-3.11")')
    index_313 = text.index('Arguments = @("-3.13")')
    index_310 = text.index('Arguments = @("-3.10")')
    assert index_312 < index_311 < index_313 < index_310
    assert "Python.Python.3.12" in text
    assert "--disable-interactivity" in text
    assert "No usable Python runtime was found. Installing Python 3.12 automatically" in text


def test_qa_environment_uses_same_automatic_bootstrap_and_keeps_altair_check():
    text = _read("Run_FaradaIQ_QA_v316.bat")
    lower = text.lower()
    assert "_ensure_faradaiq_venv_v316.ps1" in lower
    assert ".faradaiq_test_venv" in lower
    assert "_check_test_dependencies.py" in lower
    assert "python -m pytest" not in lower  # must use the QA venv executable explicitly
    assert '"%qa_py%" "%~dp0_run_faradaiq_qa_groups_v316.py"' in lower
    helper = _read("_run_faradaiq_qa_groups_v316.py").lower()
    assert '"-m", "pytest", "-ra"' in helper
    assert "--no-input" in lower
