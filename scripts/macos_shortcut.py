"""Build Finder launchers that start eStudio in Terminal."""
from __future__ import annotations

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


def _replace(path, staged):
    """Publish a complete bundle from the same volume, restoring the old entry on failure."""
    backup = staged.parent / 'previous.app'
    if path.exists() or path.is_symlink():
        os.replace(path, backup)
    try:
        os.replace(staged, path)
    except OSError:
        if backup.exists() or backup.is_symlink():
            os.replace(backup, path)
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


def _finish_bundle(bundle, meta, bundle_id):
    plist = bundle / 'Contents' / 'Info.plist'
    with plist.open('rb') as stream:
        info = plistlib.load(stream)
    info.update({
        'CFBundleName': 'FaradaIQ eStudio',
        'CFBundleDisplayName': 'FaradaIQ eStudio',
        'CFBundleIdentifier': bundle_id,
        'CFBundleIconFile': 'estudio.icns',
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
    app = target / shortcut.name
    version = meta['version']
    app_id = f'org.faradaiq.estudio.{version}'
    if not _can_replace(app, app_id):
        raise RuntimeError(f'Move the existing non-installer item first: {app}')
    if not _can_replace(shortcut, DESKTOP_ID, legacy_link=True):
        raise RuntimeError(f'Move the existing non-installer item first: {shortcut}')

    shortcut.parent.mkdir(parents=True, exist_ok=True)
    launch_command = target / f'Launch_FaradaIQ_eStudio_{version}.command'
    start_command = target / f'Start_FaradaIQ_eStudio_{version}.command'
    escaped_command = json.dumps(str(launch_command), ensure_ascii=False)
    applescript = f'''on run
    set launchFile to {escaped_command}
    try
        do shell script "/usr/bin/open -b com.apple.Terminal " & quoted form of launchFile
    on error errorMessage
        display alert "FaradaIQ eStudio could not open Terminal" message errorMessage
    end try
end run'''

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
        _finish_bundle(staged_app, meta, app_id)
        _finish_bundle(staged_shortcut, meta, DESKTOP_ID)
        os.replace(staged_command, launch_command)
        _replace(app, staged_app)
        _replace(shortcut, staged_shortcut)
    return app
