"""Verify installation artifacts and run the same terminal launcher on a test shell."""
import json
import os
import plistlib
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = json.loads((ROOT / 'release.json').read_text())
VERSION = RELEASE['version']


def test_finder_shortcut_replaces_legacy_symlink(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    from macos_shortcut import install_launchers

    desktop = tmp_path / 'Desktop' / 'Holaris Apps'
    desktop.mkdir(parents=True)
    shortcut = desktop / 'FaradaIQ eStudio.app'
    target = tmp_path / "Venky's Apps" / f'V{RELEASE["version_number"]}'
    target.mkdir(parents=True)
    shortcut.symlink_to(target.parent / 'V329' / shortcut.name)
    calls = []

    def fake_tool(argv, **kwargs):
        calls.append(argv)
        if argv[0] == '/usr/bin/osacompile':
            app = Path(argv[argv.index('-o') + 1])
            (app / 'Contents' / 'MacOS').mkdir(parents=True)
            executable = app / 'Contents' / 'MacOS' / 'applet'
            executable.write_text('fake native applet')
            executable.chmod(0o755)
            (app / 'Contents' / 'Resources').mkdir()
            with (app / 'Contents' / 'Info.plist').open('wb') as stream:
                plistlib.dump({'CFBundleExecutable': 'applet'}, stream)
        elif argv[0] == '/usr/bin/iconutil':
            Path(argv[argv.index('-o') + 1]).write_text('fake icon')
        elif argv[0] in ('/usr/bin/sips', '/usr/bin/codesign'):
            pass
        else:
            pytest.fail(f'Unexpected tool {argv[0]}')

    run_shell = subprocess.run
    monkeypatch.setattr(subprocess, 'run', fake_tool)
    app = install_launchers(target, shortcut, RELEASE)
    assert app.is_dir() and shortcut.is_dir() and not shortcut.is_symlink()
    local_launcher = target / f'Launch_FaradaIQ_eStudio_{VERSION}.command'
    assert local_launcher.is_file() and os.access(local_launcher, os.X_OK)
    args = next(a for a in calls if a[0] == '/usr/bin/osacompile')
    applescript = args[args.index('-e') + 1]
    assert str(local_launcher) in applescript and 'com.apple.Terminal' in applescript
    ids = []
    for bundle in (app, shortcut):
        data = plistlib.loads((bundle / 'Contents' / 'Info.plist').read_bytes())
        ids.append(data['CFBundleIdentifier'])
        assert data['CFBundleVersion'] == str(RELEASE['version_number'])
        assert data['CFBundleExecutable'] == 'applet'
        assert (bundle / 'Contents' / 'Resources' / 'estudio.icns').exists()
    assert len(set(ids)) == 2
    assert len([a for a in calls if a[0] == '/usr/bin/codesign' and '--verify' in a]) == 2
    if Path('/bin/bash').is_file():
        start = target / f'Start_FaradaIQ_eStudio_{VERSION}.command'
        start.write_text('printf "%s\\n" "$1"\n')
        outcome = run_shell(['/bin/bash', str(local_launcher), 'from shortcut'],
                            capture_output=True, text=True, check=True)
        assert outcome.stdout.strip() == 'from shortcut'
    # A reinstall may also replace a managed non-symlink launcher.
    assert install_launchers(target, shortcut, RELEASE) == app
    assert shortcut.is_dir() and not shortcut.is_symlink()


@pytest.mark.skipif(not Path('/bin/bash').is_file(), reason='Bash is required')
@pytest.mark.parametrize('status', [0, 7])
def test_terminal_launcher_keeps_startup_output(tmp_path, status):
    target = tmp_path / 'App folder'
    target.mkdir()
    launcher = target / f'Start_FaradaIQ_eStudio_{VERSION}.command'
    launcher.write_bytes((ROOT / launcher.name).read_bytes())
    worker = target / f'Start_FaradaIQ_eStudio_{VERSION}.sh'
    worker.write_text(f'printf "startup-message\\n" >&2\nexit {status}\n')
    result = subprocess.run(
        ['/bin/bash', str(launcher)], input='\n', capture_output=True, text=True,
        env={**os.environ, 'HOME': str(tmp_path)}, timeout=12,
    )
    assert result.returncode == status
    log = tmp_path / 'Library' / 'Logs' / 'FaradaIQ eStudio' / f'startup_{VERSION}.log'
    assert 'startup-message' in log.read_text()
    if status:
        assert str(log) in result.stdout + result.stderr


def test_finder_install_preserves_an_unmanaged_desktop_app(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    from macos_shortcut import install_launchers

    target = tmp_path / 'app'
    target.mkdir()
    shortcut = tmp_path / 'Desktop' / 'FaradaIQ eStudio.app'
    shortcut.mkdir(parents=True)
    marker = shortcut / 'existing file'
    marker.write_text('keep me')
    with pytest.raises(RuntimeError, match='existing non-installer item'):
        install_launchers(target, shortcut, RELEASE)
    assert marker.read_text() == 'keep me'
