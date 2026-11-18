"""Exercise the public Mac installer with simulated macOS system tools."""
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / 'Install_FaradaIQ_eStudio_v337.sh'
PYTHON_SHA = '8373e58da4ea146b3eb1c1f9834f19a319440b6b679b06050b1f9ee3237aa8e4'
PYTHON_URL = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg'


@pytest.fixture
def run_installer(tmp_path):
    if not Path('/bin/bash').is_file():
        pytest.skip('Bash required for the Mac launcher tests')

    tools = tmp_path / 'tools'
    tools.mkdir()
    (tools / 'dirname').symlink_to('/usr/bin/dirname')
    framework = tmp_path / 'Python Framework'
    framework.mkdir()
    package = tmp_path / 'eStudio package'
    package.mkdir()
    trace = tmp_path / 'steps.txt'
    fake_python = tools / 'usable-python'
    fake_payload = 'verified fake Python package'
    source = LAUNCHER.read_text()
    for system_path in ('/usr/bin/curl', '/usr/bin/uname', '/usr/sbin/pkgutil',
                        '/usr/bin/sudo'):
        source = source.replace(system_path, str(tools / Path(system_path).name))
    framework_path = '/Library/Frameworks/Python.framework/Versions/'
    source = source.replace(framework_path, str(framework) + '/')
    for prefix in ('/opt/homebrew/bin/', '/usr/local/bin/'):
        source = source.replace(prefix, str(tmp_path / 'unused' / 'bin') + '/')
    source = source.replace(PYTHON_SHA, hashlib.sha256(fake_payload.encode()).hexdigest())
    apple_shim = tools / 'python3'
    source = source.replace('/usr/bin/python3', str(apple_shim))
    launcher = package / LAUNCHER.name
    launcher.write_text(source)
    command = package / LAUNCHER.with_suffix('.command').name
    command.write_bytes((ROOT / command.name).read_bytes())

    commands = {
        'uname': 'printf "%s\\n" "${SYSTEM_NAME:-Darwin}"',
        'curl': '''printf 'download:%s\\n' "${@: -1}" >> "$TRACE"
while [[ "$1" != --output ]]; do shift; done
printf '%s' "${PKG_BODY}" > "$2"''',
        'pkgutil': '''printf 'signature\\n' >> "$TRACE"
[[ "$1" == --check-signature && -f "$2" && "${SIGNATURE_OK:-yes}" == yes ]]''',
        'sudo': '''printf 'install\\n' >> "$TRACE"
[[ "$1" == /usr/sbin/installer && "$2" == -pkg && -f "$3" && "$4" == -target && "$5" == / ]] || exit 3
[[ "${ADMIN_OK:-yes}" == yes ]] || exit 1
/bin/mkdir -p "$(/usr/bin/dirname "$FRAMEWORK_PYTHON")"
/bin/cp "$FAKE_PYTHON" "$FRAMEWORK_PYTHON"''',
        'usable-python': '''if [[ "$*" == *" -c "* || "$1" == -c ]]; then exit 0; fi
printf 'app:%s\\n' "$*" >> "$TRACE"''',
    }
    for name, script in commands.items():
        path = tools / name
        path.write_text('#!/bin/bash\nset -eu\n' + script + '\n')
        path.chmod(0o755)

    framework_python = framework / '3.12/bin/python3.12'

    def run(*args, existing=None, **overrides):
        if existing == 'framework':
            framework_python.parent.mkdir(parents=True)
            framework_python.write_bytes(fake_python.read_bytes())
            framework_python.chmod(0o755)
        elif existing == 'path':
            (tools / 'python3.12').symlink_to(fake_python)
        elif existing == 'apple_shim':
            apple_shim.write_text('#!/bin/bash\nprintf "apple-shim\\n" >> "$TRACE"\nexit 1\n')
            apple_shim.chmod(0o755)
        elif existing == 'without_tk':
            candidate = tools / 'python3.12'
            candidate.write_text('#!/bin/bash\n[[ "$*" != *tkinter* ]]\n')
            candidate.chmod(0o755)
        env = {
            'PATH': str(tools), 'HOME': str(tmp_path),
            'TMPDIR': str(tmp_path), 'TRACE': str(trace), 'PKG_BODY': fake_payload,
            'FRAMEWORK_PYTHON': str(framework_python), 'FAKE_PYTHON': str(fake_python),
            **overrides,
        }
        # Use the same .command entry point as Finder with a path containing spaces.
        result = subprocess.run(['/bin/bash', str(command), *args], env=env, input='\n',
                                capture_output=True, text=True, timeout=15)
        assert not list(tmp_path.glob('FaradaIQ-Python.*'))
        steps = trace.read_text().splitlines() if trace.is_file() else []
        return result, steps

    return run


@pytest.mark.parametrize('location', ['framework', 'path'])
def test_reuses_installed_python_without_download(run_installer, location):
    result, steps = run_installer('--no-launch', existing=location)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(steps) == 1 and steps[0].endswith('macos_install.py --no-launch')


@pytest.mark.parametrize('existing', [None, 'without_tk', 'apple_shim'])
def test_installs_verified_python_then_runs_estudio(run_installer, existing):
    result, steps = run_installer('--no-launch', existing=existing)
    assert result.returncode == 0, result.stdout + result.stderr
    assert steps[:3] == [f'download:{PYTHON_URL}', 'signature', 'install']
    assert len(steps) == 4 and steps[3].endswith('macos_install.py --no-launch')


@pytest.mark.parametrize(('overrides', 'expected', 'last_step'), [
    ({'PKG_BODY': 'wrong download'}, 'SHA-256', f'download:{PYTHON_URL}'),
    ({'SIGNATURE_OK': 'no'}, 'signature', 'signature'),
    ({'ADMIN_OK': 'no'}, 'administrator', 'install'),
])
def test_does_not_continue_when_preparation_fails(run_installer, overrides, expected, last_step):
    result, steps = run_installer(**overrides)
    assert result.returncode != 0
    assert expected.lower() in result.stderr.lower()
    assert steps[-1] == last_step
    assert not any(step.startswith('app:') for step in steps)


def test_plan_never_downloads_python(run_installer):
    result, steps = run_installer('--plan')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'would' in result.stdout.lower()
    assert steps == []


def test_never_downloads_macos_package_on_linux(run_installer):
    result, steps = run_installer(SYSTEM_NAME='Linux')
    assert result.returncode != 0
    assert 'macOS' in result.stderr
    assert steps == []


def test_uses_pinned_python_org_package():
    source = LAUNCHER.read_text()
    assert PYTHON_URL in source and PYTHON_SHA in source
