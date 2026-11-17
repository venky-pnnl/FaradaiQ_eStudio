from pathlib import Path
import importlib

ROOT = Path(__file__).resolve().parents[1]


def test_v228_public_entry_points_and_metadata():
    assert (ROOT / 'faradaiq_studio_v316.py').exists()
    assert (ROOT / 'Start_FaradaIQ_Studio_v320.bat').exists()
    assert (ROOT / 'Start_FaradaIQ_Studio_v316.command').exists()
    assert (ROOT / 'Start_FaradaIQ_Studio_v316.sh').exists()
    pyproject = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    assert 'version = "0.316.0"' in pyproject
    assert 'readme = "README_v316.md"' in pyproject


def test_v228_core_and_app_entry():
    core = importlib.import_module('faradaiq_studio_core')
    app = importlib.import_module('faradaiq.app')
    assert core.APP_TITLE == 'FaradaIQ Studio v316'
    assert core.FARADAIQ_PUBLIC_VERSION == 'v316'
    assert core.FARADAIQ_APP_VERSION == 'v316'
    assert callable(getattr(core, '_v228_main_final'))
    assert callable(app.main)


def test_v228_help_and_requirements():
    core = importlib.import_module('faradaiq_studio_core')
    names = [str(item[0]) for item in core._V207_HELP_TABS]
    assert any(name.startswith('v232 scientific intelligence') for name in names)
    assert 'v232 stable status, themed editors, and background progress' in names
    req = (ROOT / 'requirements_all.txt').read_text(encoding='utf-8')
    assert 'impedance>=1.7,<2' in req
    assert 'python-docx>=1.1,<2' in req
