"""Build Finder applications that launch eStudio and SciLink quietly."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import tempfile


DESKTOP_ID = 'org.faradaiq.estudio.desktop'
BROWSER_ID = 'org.faradaiq.estudio.browser.desktop'
SCILINK_ID = 'org.faradaiq.scilink'
SCILINK_DESKTOP_ID = 'org.faradaiq.scilink.desktop'
MANAGED_KEY = 'FaradaIQManagedLauncher'


def _can_replace(path, bundle_id, *, legacy_link=False):
    if path.is_symlink():
        destination = Path(os.readlink(path))
        return (legacy_link and destination.name == path.name
                and re.fullmatch(r'V\d+', destination.parent.name) is not None)
    if not path.exists():
        return True
    try:
        with (path / 'Contents' / 'Info.plist').open('rb') as stream:
            info = plistlib.load(stream)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False
    return info.get(MANAGED_KEY) is True and info.get('CFBundleIdentifier') == bundle_id


def _publish(staged_launchers):
    swaps = []
    try:
        for destination, staged in staged_launchers:
            backup = staged.with_name(f'previous-{staged.name}')
            if destination.exists() or destination.is_symlink():
                os.replace(destination, backup)
            swaps.append((destination, staged, backup))
            os.replace(staged, destination)
    except BaseException:
        for destination, staged, backup in reversed(swaps):
            if destination.exists() or destination.is_symlink():
                os.replace(destination, staged)
            if backup.exists() or backup.is_symlink():
                os.replace(backup, destination)
        raise


def _make_icon(source, icon, workspace):
    iconset = workspace / 'estudio.iconset'
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = '@2x' if scale == 2 else ''
            output = iconset / f'icon_{size}x{size}{suffix}.png'
            pixels = str(size * scale)
            subprocess.run(['/usr/bin/sips', '-z', pixels, pixels, str(source),
                            '--out', str(output)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['/usr/bin/iconutil', '-c', 'icns', str(iconset), '-o', str(icon)], check=True)


def _finish_bundle(bundle, meta, bundle_id, display_name, icon_name='estudio.icns'):
    plist = bundle / 'Contents' / 'Info.plist'
    with plist.open('rb') as stream:
        info = plistlib.load(stream)
    info.update({
        'CFBundleName': display_name,
        'CFBundleDisplayName': display_name,
        'CFBundleIdentifier': bundle_id,
        'CFBundleIconFile': icon_name,
        'CFBundlePackageType': 'APPL',
        'CFBundleShortVersionString': meta['semver'],
        'CFBundleVersion': str(meta['version_number']),
        'NSHighResolutionCapable': True,
        MANAGED_KEY: True,
    })
    with plist.open('wb') as stream:
        plistlib.dump(info, stream)
    executable = bundle / 'Contents' / 'MacOS' / info['CFBundleExecutable']
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError(f'Missing macOS applet executable in {bundle}')
    # The macOS-generated applet has edited resources and must be signed again locally.
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(bundle)], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(bundle)], check=True)


def install_launchers(target, shortcut, meta):
    with _prepare_launchers(target, shortcut, meta) as staged:
        _publish(staged)
    return target / shortcut.name


def install_browser_launchers(target, shortcut, meta):
    with _prepare_launchers(target, shortcut, meta, browser=True) as staged:
        _publish(staged)
    return target / shortcut.name


def install_application_launchers(target, desktop, meta):
    browser = desktop.with_name('FaradaIQ eStudio Browser.app')
    with _prepare_launchers(target, desktop, meta) as desktop_staged, \
            _prepare_launchers(target, browser, meta, browser=True) as browser_staged:
        _publish(desktop_staged + browser_staged)
    return target / desktop.name, target / browser.name


def _applet_script(command, display_name):
    escaped_command = json.dumps(shlex.join(map(str, command)), ensure_ascii=False)
    error_title = json.dumps(f'{display_name} could not start', ensure_ascii=False)
    return f'''on run
    try
        do shell script {escaped_command}
    on error errorMessage
        display alert {error_title} message errorMessage
    end try
end run'''


def install_scilink_launcher(root, shortcut, meta, icon_source):
    app = root / 'SciLink.app'
    if not _can_replace(app, SCILINK_ID) or not _can_replace(shortcut, SCILINK_DESKTOP_ID):
        raise RuntimeError(f'An unmanaged SciLink app already occupies {app} or {shortcut}.')
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    script = _applet_script((root / 'bootstrap/bin/python', root / 'scilink_launch.py', '--root', root), 'SciLink')
    with tempfile.TemporaryDirectory(prefix='.scilink-app-', dir=root) as app_tmp, \
            tempfile.TemporaryDirectory(prefix='.scilink-desktop-', dir=shortcut.parent) as desktop_tmp:
        staged_app = Path(app_tmp) / app.name
        staged_shortcut = Path(desktop_tmp) / shortcut.name
        subprocess.run(['/usr/bin/osacompile', '-o', str(staged_app), '-e', script], check=True)
        shutil.copy2(icon_source, staged_app / 'Contents/Resources/scilink.icns')
        shutil.copytree(staged_app, staged_shortcut, symlinks=True)
        _finish_bundle(staged_app, meta, SCILINK_ID, 'SciLink', 'scilink.icns')
        _finish_bundle(staged_shortcut, meta, SCILINK_DESKTOP_ID, 'SciLink', 'scilink.icns')
        _publish(((app, staged_app), (shortcut, staged_shortcut)))
    return app


@contextmanager
def _prepare_launchers(target, shortcut, meta, *, browser=False):
    app = target / shortcut.name
    version = meta['version']
    stem = 'FaradaIQ_eStudio_Browser' if browser else 'FaradaIQ_eStudio'
    app_id = f'org.faradaiq.estudio.browser.{version}' if browser else f'org.faradaiq.estudio.{version}'
    shortcut_id = BROWSER_ID if browser else DESKTOP_ID
    if not _can_replace(app, app_id):
        raise RuntimeError(f'Move the existing non-installer item first: {app}')
    if not _can_replace(shortcut, shortcut_id, legacy_link=not browser):
        raise RuntimeError(f'Move the existing non-installer item first: {shortcut}')

    shortcut.parent.mkdir(parents=True, exist_ok=True)
    launch_command = target / f'Launch_{stem}_{version}.command'
    start_command = target / f'Start_{stem}_{version}.command'
    display_name = shortcut.stem
    python = target / '.faradaiq_venv_macos/bin/python'
    mode = 'browser' if browser else 'desktop'
    applescript = _applet_script((python, target / 'scripts/launch_estudio.pyw', mode), display_name)

    with tempfile.TemporaryDirectory(prefix='.estudio-app-', dir=target) as app_tmp, \
            tempfile.TemporaryDirectory(prefix='.estudio-desktop-', dir=shortcut.parent) as desktop_tmp:
        workspace = Path(app_tmp)
        staged_app = workspace / app.name
        staged_shortcut = Path(desktop_tmp) / shortcut.name
        staged_command = workspace / launch_command.name
        staged_command.write_text(f'#!/bin/bash\nexec /bin/bash {shlex.quote(str(start_command))} "$@"\n',
                                  encoding='utf-8')
        staged_command.chmod(0o755)
        subprocess.run(['/usr/bin/osacompile', '-o', str(staged_app), '-e', applescript], check=True)
        icon = staged_app / 'Contents' / 'Resources' / 'estudio.icns'
        _make_icon(target / 'assets' / 'estudio.png', icon, workspace)
        shutil.copytree(staged_app, staged_shortcut, symlinks=True)
        _finish_bundle(staged_app, meta, app_id, display_name)
        _finish_bundle(staged_shortcut, meta, shortcut_id, display_name)
        yield ((launch_command, staged_command), (app, staged_app), (shortcut, staged_shortcut))
