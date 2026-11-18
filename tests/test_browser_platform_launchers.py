"""Check the independent browser launcher wiring and Finder bundle metadata."""

import json
import hashlib
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
BROWSER_START = 'Start_FaradaIQ_eStudio_Browser_v336'


def test_windows_installs_a_second_shortcut_and_checks_browser_dependencies():
    installer = (ROOT / 'scripts' / 'install.ps1').read_text()
    assert 'FaradaIQ eStudio Browser.lnk' in installer
    assert 'Start_FaradaIQ_eStudio_Browser_' in installer
    assert '$browserShortcut.Save()' in installer
    launcher = (ROOT / f'{BROWSER_START}.bat').read_text()
    assert '.faradaiq_venv\\Scripts\\python.exe' in launcher
    assert 'faradaiq_browser_v336.py' in launcher
    for requirements in ('requirements_all.txt', 'requirements_macos.txt'):
        assert '-r requirements_browser.txt' in (ROOT / requirements).read_text()


@pytest.mark.skipif(not Path('/bin/bash').is_file(), reason='Bash is required')
def test_browser_terminal_launcher_uses_private_python_and_logs_exit(tmp_path):
    target = tmp_path / "eStudio in Venky's Apps"
    runtime = target / '.faradaiq_venv_macos' / 'bin'
    runtime.mkdir(parents=True)
    python = runtime / 'python'
    python.write_text('#!/bin/bash\nprintf "%s\\n" "$1" "$2" "$3"\nexit 17\n')
    python.chmod(0o755)
    launcher = target / f'{BROWSER_START}.command'
    launcher.write_bytes((ROOT / launcher.name).read_bytes())
    result = subprocess.run(
        ['/bin/bash', str(launcher), '--test-argument'], capture_output=True, text=True,
        env={**os.environ, 'HOME': str(tmp_path)}, timeout=10,
    )
    assert result.returncode == 17
    log = tmp_path / 'Library' / 'Logs' / 'FaradaIQ eStudio' / 'browser_v336.log'
    assert str(target / 'faradaiq_browser_v336.py') in log.read_text()
    assert '--test-argument' in log.read_text()
    assert str(log) in result.stderr


@pytest.fixture
def fake_finder_tools(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    calls = []

    def fake_tool(argv, **kwargs):
        calls.append(argv)
        if argv[0] == '/usr/bin/osacompile':
            contents = Path(argv[argv.index('-o') + 1]) / 'Contents'
            executable = contents / 'MacOS' / 'applet'
            executable.parent.mkdir(parents=True)
            executable.write_text('fake native applet')
            executable.chmod(0o755)
            (contents / 'Resources').mkdir()
            (contents / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': 'applet'}))
        elif argv[0] == '/usr/bin/iconutil':
            Path(argv[argv.index('-o') + 1]).write_text('fake icon')
        else:
            assert argv[0] in ('/usr/bin/sips', '/usr/bin/codesign')

    monkeypatch.setattr(subprocess, 'run', fake_tool)
    return calls


def test_browser_finder_bundle_is_separate_from_desktop_bundle(tmp_path, fake_finder_tools):
    from macos_shortcut import install_browser_launchers

    target = tmp_path / 'V336'
    target.mkdir()
    folder = tmp_path / 'Desktop' / 'Holaris Apps'
    folder.mkdir(parents=True)
    shortcut = folder / 'FaradaIQ eStudio Browser.app'
    desktop = folder / 'FaradaIQ eStudio.app'
    desktop.write_text('existing desktop link')
    meta = json.loads((ROOT / 'release.json').read_text())
    app = install_browser_launchers(target, shortcut, meta)
    assert desktop.read_text() == 'existing desktop link'
    assert app == target / shortcut.name
    ids = []
    for bundle in (app, shortcut):
        info = plistlib.loads((bundle / 'Contents' / 'Info.plist').read_bytes())
        assert info['CFBundleDisplayName'] == 'FaradaIQ eStudio Browser'
        assert info['CFBundleVersion'] == str(meta['version_number'])
        ids.append(info['CFBundleIdentifier'])
    assert len(set(ids)) == 2
    assert all('browser' in identifier for identifier in ids)
    launch_command = target / 'Launch_FaradaIQ_eStudio_Browser_v336.command'
    assert BROWSER_START in launch_command.read_text()
    script = next(argv[-1] for argv in fake_finder_tools if argv[0] == '/usr/bin/osacompile')
    assert '/scripts/launch_estudio.pyw' in script and ' browser' in script
    assert 'com.apple.Terminal' not in script


def test_managed_scilink_finder_shortcut_uses_only_its_own_root(tmp_path, fake_finder_tools):
    from macos_shortcut import install_scilink_launcher

    target = tmp_path / "Venky's SciLink"
    target.mkdir()
    shortcut = tmp_path / 'Desktop' / 'Holaris Apps' / 'SciLink.app'
    meta = json.loads((ROOT / 'release.json').read_text())
    icon = ROOT / 'assets' / 'scilink.icns'
    installed = install_scilink_launcher(target, shortcut, meta, icon)
    assert installed == target / 'SciLink.app'
    script = next(argv[-1] for argv in fake_finder_tools if argv[0] == '/usr/bin/osacompile')
    assert '/bootstrap/bin/python' in script and '/scilink_launch.py' in script
    assert 'launch_estudio' not in script and 'com.apple.Terminal' not in script
    for bundle in (installed, shortcut):
        contents = bundle / 'Contents'
        info = plistlib.loads((contents / 'Info.plist').read_bytes())
        assert info['CFBundleDisplayName'] == 'SciLink'
        assert info['CFBundleIconFile'] == 'scilink.icns'
        assert (contents / 'Resources' / info['CFBundleIconFile']).read_bytes() == icon.read_bytes()


@pytest.mark.skipif(os.name == 'nt', reason='The legacy Finder shortcut is a macOS symlink')
@pytest.mark.parametrize('failure_stage', ['sign', 'publish'])
def test_finder_preserves_both_shortcuts_when_browser_setup_fails(tmp_path, monkeypatch, fake_finder_tools, failure_stage):
    from macos_shortcut import BROWSER_ID, MANAGED_KEY, install_application_launchers

    meta = json.loads((ROOT / 'release.json').read_text())
    target = tmp_path / 'V336'
    target.mkdir()
    folder = tmp_path / 'Desktop' / 'Holaris Apps'
    folder.mkdir(parents=True)
    desktop = folder / 'FaradaIQ eStudio.app'
    old = tmp_path / 'V332' / desktop.name
    desktop.symlink_to(old)
    browser = folder / 'FaradaIQ eStudio Browser.app'
    browser_contents = browser / 'Contents'
    browser_contents.mkdir(parents=True)
    browser_plist = browser_contents / 'Info.plist'
    previous = plistlib.dumps({MANAGED_KEY: True, 'CFBundleIdentifier': BROWSER_ID, 'CFBundleVersion': '332'})
    browser_plist.write_bytes(previous)

    fake_commands = subprocess.run
    replace = os.replace

    def fail_sign(args, **kwargs):
        if args[0] == '/usr/bin/codesign' and 'Browser.app' in args[-1]:
            raise subprocess.CalledProcessError(1, args)
        return fake_commands(args, **kwargs)

    def fail_publish(source, destination):
        if Path(destination) == browser and Path(source).name == browser.name:
            raise PermissionError('Simulated Finder shortcut permission change')
        return replace(source, destination)

    if failure_stage == 'sign':
        monkeypatch.setattr(subprocess, 'run', fail_sign)
    else:
        monkeypatch.setattr(os, 'replace', fail_publish)
    with pytest.raises((subprocess.CalledProcessError, PermissionError)):
        install_application_launchers(target, desktop, meta)
    assert desktop.is_symlink() and desktop.readlink() == old
    assert browser_plist.read_bytes() == previous
    assert not (target / desktop.name).exists()
    assert not (target / browser.name).exists()


@pytest.mark.skipif(os.name == 'nt', reason='The macOS installer applies Unix executable bits')
def test_mac_install_restores_execute_bits_for_nested_payload_scripts(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    import macos_install

    source = tmp_path / 'extracted archive'
    source.mkdir()
    metadata = json.loads((ROOT / 'release.json').read_text())
    meta_path = source / 'release.json'
    meta_path.write_text(json.dumps(metadata))
    relative = 'integrated_tools/LabRIC_MCP/run_labric_mcp.sh'
    script = source / relative
    script.parent.mkdir(parents=True)
    script.write_text('#!/bin/bash\n')
    script.chmod(0o644)
    manifest = source / f"PACKAGE_MANIFEST_SHA256_{metadata['version']}.txt"
    manifest.write_text(''.join(f'{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.relative_to(source).as_posix()}\n'
                                for file in (meta_path, script)))

    install_root = tmp_path / 'custom install root'
    target = install_root / f"V{metadata['version_number']}"
    runtime = target / '.faradaiq_venv_macos' / 'bin'
    runtime.mkdir(parents=True)
    (runtime / 'python').touch()
    tk = SimpleNamespace(withdraw=lambda: None, update=lambda: None, destroy=lambda: None)
    monkeypatch.setitem(sys.modules, 'tkinter', SimpleNamespace(Tk=lambda: tk))
    monkeypatch.setattr(macos_install.sys, 'platform', 'darwin')
    monkeypatch.setattr(macos_install, 'ROOT', source)
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: None)
    monkeypatch.setattr(macos_install, 'install_application_launchers', lambda *args: ('desktop', 'browser'))
    monkeypatch.setattr(macos_install, 'prepare_analysis_tools', lambda *args: 0)
    monkeypatch.setattr(macos_install, 'prepare_scilink', lambda *args: None)
    macos_install.install(str(install_root), no_launch=True)
    assert os.access(target / relative, os.X_OK)


@pytest.mark.skipif(not Path('/bin/bash').is_file(), reason='Bash is required')
@pytest.mark.parametrize('launcher', ['LabRIC', 'official pyDRTtools'])
def test_platform_integration_wrappers_use_mac_private_python(tmp_path, launcher):
    target = tmp_path / 'installed eStudio'
    runtime = target / '.faradaiq_venv_macos' / 'bin'
    runtime.mkdir(parents=True)
    python = runtime / 'python'
    python.write_text('#!/bin/bash\nprintf "%s\\n" "used mac private Python: $*"\n')
    python.chmod(0o755)
    env = {**os.environ, 'LABRIC_API_KEY': 'test-only'}
    if launcher == 'LabRIC':
        relative = Path('integrated_tools/LabRIC_MCP/run_labric_mcp.sh')
        (target / relative.parent).mkdir(parents=True)
    else:
        relative = Path('install_official_pydrttools_optional.sh')
        (target / 'requirements_official_pydrttools_optional.txt').touch()
    (target / relative).write_bytes((ROOT / relative).read_bytes())
    result = subprocess.run(['/bin/bash', str(target / relative), '--auto'], timeout=15, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'used mac private Python:' in result.stdout
