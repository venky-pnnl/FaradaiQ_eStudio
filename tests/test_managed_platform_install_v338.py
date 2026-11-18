"""Exercise quiet eStudio launch and the three-app installer contract."""

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_windows_installer_manages_scilink_and_uses_windowless_launchers():
    installer = (ROOT / 'scripts' / 'install.ps1').read_text()
    assert 'faradaiq.managed_scilink update' in installer
    assert 'Holaris Apps\\SciLink.lnk' in installer
    assert 'bootstrap\\Scripts\\pythonw.exe' in installer
    assert 'scilink_launch.py' in installer and 'active.json' in installer
    assert 'launch_estudio.pyw' in installer
    assert '$shortcut.TargetPath = $quietPython' in installer
    assert '$browserShortcut.TargetPath = $quietPython' in installer
    assert 'if (-not $NoLaunch)' in installer
    assert 'Python 3.11' not in (ROOT / '_ensure_faradaiq_venv_v338.ps1').read_text()
    shortcut_builder = (ROOT / 'scripts' / 'install_scilink_shortcut.ps1').read_text()
    assert 'install_scilink_shortcut.ps1' in installer
    assert 'bootstrap\\Scripts\\pythonw.exe' in shortcut_builder
    assert ' --root .' in shortcut_builder and '$shortcut.WorkingDirectory = $SciLinkRoot' in shortcut_builder


def test_windows_full_installer_runs_shared_tools_once_after_core_provisioning():
    installer = (ROOT / 'scripts' / 'install.ps1').read_text()
    start = (ROOT / 'Start_FaradaIQ_eStudio_v338.bat').read_text()
    assert installer.count('setup_analysis_tools.py') == 1
    assert installer.index('--install-only') < installer.index('setup_analysis_tools.py')
    assert installer.index('setup_analysis_tools.py') < installer.index('faradaiq.managed_scilink update')
    assert installer.index('faradaiq.managed_scilink update') < installer.index('if (-not $NoLaunch)')
    assert 'analysis_tools_exit_code' in installer
    assert 'install_deareis_optional.bat' not in start
    assert 'install_official_pydrttools_optional.bat' not in start


@pytest.mark.skipif(not Path('/bin/bash').is_file(), reason='Uses a POSIX test runtime')
@pytest.mark.parametrize('mode,entry,log_name', [
    ('desktop', 'faradaiq_estudio_v338.py', 'startup_v338.log'),
    ('browser', 'faradaiq_browser_v338.py', 'browser_v338.log'),
])
def test_quiet_launcher_detaches_the_private_worker_and_preserves_logs(tmp_path, monkeypatch, mode, entry, log_name):
    script = ROOT / 'scripts' / 'launch_estudio.pyw'
    loader = importlib.machinery.SourceFileLoader('launch_estudio', str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    release_launcher = importlib.util.module_from_spec(spec)
    loader.exec_module(release_launcher)

    target = tmp_path / "Venky's installed eStudio"
    runtime = target / '.faradaiq_venv_macos' / 'bin'
    runtime.mkdir(parents=True)
    (target / 'release.json').write_text(json.dumps({'version': 'v338'}))
    (target / entry).touch()
    python = runtime / 'python'
    python.write_text('#!/bin/bash\nprintf "%s\\n" "$1" "$2" "$FARADAIQ_QUIET_LAUNCH"\n')
    python.chmod(0o755)
    logs = tmp_path / 'logs'
    monkeypatch.setattr(release_launcher, 'ROOT', target)
    monkeypatch.setattr(release_launcher, 'log_directory', lambda: logs)
    assert release_launcher.main([mode]) == 0
    log = logs / log_name
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and str(target / entry) not in log.read_text():
        time.sleep(0.02)
    assert log.read_text().endswith(f'-u\n{target / entry}\n1\n')


@pytest.mark.parametrize('active', [None, {'commit': 'a' * 40, 'version': '0.1.0'}])
def test_mac_installer_sources_scilink_from_standalone_bootstrap(tmp_path, monkeypatch, active):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    import macos_install

    target = tmp_path / 'V338'
    target.mkdir()
    runtime = target / '.faradaiq_venv_macos' / 'bin' / 'python'
    scilink = tmp_path / 'SciLink with spaces'
    for name in ('bootstrap/bin/python', 'scilink_launch.py', 'active.json'):
        path = scilink / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        path.chmod(0o755)
    monkeypatch.setenv('FARADAIQ_SCILINK_ROOT', str(scilink))
    calls = []

    def fail_update(command, **kwargs):
        calls.append((command, kwargs))
        if command[-1] == 'status':
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(active))
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(subprocess, 'run', fail_update)
    assert macos_install.prepare_scilink(runtime, target) == (scilink if active else None)
    assert calls[0][0] == [str(runtime), '-m', 'faradaiq.managed_scilink', 'update']
    assert calls[0][1]['env']['FARADAIQ_SCILINK_ROOT'] == str(scilink)
    assert calls[1][0] == [str(runtime), '-m', 'faradaiq.managed_scilink', 'status']


def test_mac_updater_uses_new_release_installer_for_seed_python_bootstrap():
    installer = (ROOT / 'Install_FaradaIQ_eStudio_v338.sh').read_text()
    assert '(3, 12)' in installer and '(3, 11)' not in installer
    updater = (ROOT / 'scripts' / 'macos_update.py').read_text()
    assert '/bin/bash' in updater and 'Install_FaradaIQ_eStudio_' in updater


@pytest.mark.skipif(sys.platform == 'win32', reason='macOS installer integration test')
@pytest.mark.parametrize('no_launch', [False, True])
@pytest.mark.parametrize('analysis_exit', [0, 1])
def test_mac_setup_can_open_all_three_or_defer_every_open(tmp_path, monkeypatch, no_launch, analysis_exit):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    import macos_install

    install_root = tmp_path / 'eStudio'
    installed = install_root / 'V338'
    runtime = installed / '.faradaiq_venv_macos' / 'bin' / 'python'
    runtime.parent.mkdir(parents=True)
    runtime.touch()
    (installed / 'PACKAGE_MANIFEST_SHA256_v338.txt').touch()
    scilink_root = tmp_path / 'SciLink'
    scilink_root.mkdir()
    tk = SimpleNamespace(withdraw=lambda: None, update=lambda: None, destroy=lambda: None)
    monkeypatch.setitem(sys.modules, 'tkinter', SimpleNamespace(Tk=lambda: tk))
    monkeypatch.setattr(macos_install.sys, 'platform', 'darwin')
    monkeypatch.setattr(macos_install, 'ROOT', installed)
    monkeypatch.setattr(macos_install, 'verify', lambda path: {
        'version': 'v338', 'version_number': 338, 'app_name': 'FaradaIQ eStudio',
        'package_asset': 'FaradaIQ_eStudio_v338_package.zip',
    })
    phases = []

    def prepare_scilink(*args):
        phases.append('SciLink')
        return scilink_root

    def application_launchers(*args):
        phases.append('shortcuts')
        return 'Desktop', 'Browser'

    monkeypatch.setattr(macos_install, 'prepare_scilink', prepare_scilink)
    monkeypatch.setattr(macos_install, 'install_application_launchers', application_launchers)
    monkeypatch.setattr(macos_install, 'install_scilink_launcher', lambda *args: 'SciLink')
    opens = []

    def fake_process(command, **kwargs):
        if command[1:3] == ['-m', 'pip']:
            phases.append('pip')
        if command[-1] == '--check':
            phases.append('browser check')
        if Path(command[-1]).name == 'setup_analysis_tools.py':
            assert kwargs['cwd'] == installed
            assert command[0] == str(runtime)
            phases.append('analysis tools')
            return subprocess.CompletedProcess(command, analysis_exit)
        if command[0] == '/usr/bin/open' or any(part.endswith('scilink_launch.py') for part in command):
            opens.append(command)
            phases.append('open')
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, 'run', fake_process)
    for fresh in (True, False):
        phases.clear()
        opens.clear()
        macos_install.install(str(install_root), no_launch=no_launch)
        expected = ['browser check', 'analysis tools', 'SciLink', 'shortcuts']
        assert phases == (['pip', 'pip'] if fresh else []) + expected + ([] if no_launch else ['open'] * 3)
        marker = json.loads((installed / 'installation_complete.json').read_text())
        assert marker['analysis_tools_exit_code'] == analysis_exit
        if no_launch:
            assert opens == []
        else:
            assert [Path(command[1]).name for command in opens] == [
                'FaradaIQ eStudio.app', 'FaradaIQ eStudio Browser.app', 'scilink_launch.py',
            ]
            assert opens[-1][0] == str(scilink_root / 'bootstrap' / 'bin' / 'python')
