"""Check and repair the optional DearEIS and official pyDRTtools integrations."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from tempfile import NamedTemporaryFile
from threading import Thread
from time import monotonic


ROOT = Path(__file__).resolve().parents[1]
STATUS_NAME = 'analysis_tools_status_v337.json'
SETUP_LOG = 'analysis_tools_setup_v337.log'


def private_python(root):
    if sys.platform == 'win32':
        candidates = (root / '.faradaiq_venv' / 'Scripts' / 'python.exe',)
    elif sys.platform == 'darwin':
        candidates = (root / '.faradaiq_venv_macos' / 'bin' / 'python', root / '.faradaiq_venv' / 'bin' / 'python')
    else:
        candidates = (root / '.faradaiq_venv' / 'bin' / 'python',)
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def run_logged(command, *, root, log_path, label):
    command = [str(item) for item in command]
    environment = os.environ.copy()
    environment.pop('PYTHONPATH', None)
    environment.pop('PYTHONHOME', None)
    environment.update(PYTHONNOUSERSITE='1', PYTHONUNBUFFERED='1', PYTHONUTF8='1',
                       PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_NO_INPUT='1')
    with log_path.open('a', encoding='utf-8') as log:
        def write(line):
            print(line, flush=True)
            log.write(line + '\n')
            log.flush()

        write(f'{label}…')
        try:
            process = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       encoding='utf-8', errors='replace')
        except OSError as error:
            write(f'Could not start the analysis-tool command. {error}')
            return 1

        messages = Queue()

        def read_lines():
            with process.stdout:
                for line in process.stdout:
                    messages.put(line.rstrip('\r\n'))
            messages.put(None)

        reader = Thread(target=read_lines, name='estudio-analysis-tools-progress', daemon=True)
        reader.start()
        started = monotonic()
        try:
            while True:
                try:
                    line = messages.get(timeout=10)
                except Empty:
                    write(f'{label} is still running ({int(monotonic() - started)} s).')
                    continue
                if line is None:
                    break
                write(line)
            status = process.wait()
        except BaseException:
            if process.poll() is None:
                process.terminate()
            process.wait()
            raise
        write(f'{label} finished with exit code {status}.')
        return status


def _check(root, python, script, label, log_path):
    return run_logged([python, root / script], root=root, log_path=log_path, label=label)


def _deareis(root, python, log_path):
    name = 'DearEIS'
    script = '_check_deareis_optional.py'
    if _check(root, python, script, f'Checking {name}', log_path) == 0:
        return {'status': 'already_ready'}
    installation_log = log_path.with_name('deareis_install_v337.log')
    command = [python, root / '_install_deareis_optional.py', '--root', root, '--auto',
               '--seed-python', python, '--log', installation_log]
    result = run_logged(command, root=root, log_path=log_path, label=f'Installing or repairing {name}')
    verified = result == 0 and _check(root, python, script, f'Verifying {name}', log_path) == 0
    return {'status': 'repaired' if verified else 'needs_attention', 'install_log': str(installation_log)}


def _pydrttools(root, python, log_path):
    name = 'pyDRTtools'
    script = '_check_pydrttools_optional.py'
    if _check(root, python, script, f'Checking official {name}', log_path) == 0:
        return {'status': 'already_ready'}
    requirements = root / 'requirements_official_pydrttools_optional.txt'
    constraints = root / ('requirements_macos.txt' if sys.platform == 'darwin' else 'requirements_all.txt')
    pip = [python, '-m', 'pip', '--require-virtualenv', 'install', '--only-binary=:all:', '--timeout', '25', '--retries', '2']
    result = run_logged([*pip, '--constraint', constraints, '-r', requirements], root=root, log_path=log_path,
                        label='Installing Qt 5 and pyDRTtools prerequisites')
    if result == 0:
        result = run_logged([*pip, '--no-deps', '--force-reinstall', 'pyDRTtools==0.2'], root=root, log_path=log_path,
                            label='Installing the official Ciucci Lab pyDRTtools wheel')
    verified = result == 0 and _check(root, python, script, f'Verifying official {name}', log_path) == 0
    return {'status': 'repaired' if verified else 'needs_attention'}


def _write_status(root, receipt):
    with NamedTemporaryFile('w', encoding='utf-8', dir=root, prefix='.analysis-tools-', delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(receipt, handle, indent=2)
        handle.write('\n')
    try:
        temporary.replace(root / STATUS_NAME)
    finally:
        temporary.unlink(missing_ok=True)


def setup(root=ROOT, *, tool='all'):
    root = Path(root).expanduser().resolve()
    python = private_python(root)
    if python is None:
        print(f'Optional tools need the private eStudio Python runtime under {root}. Run the main eStudio installer first.',
              file=sys.stderr, flush=True)
        return 1
    logs = root / 'logs'
    logs.mkdir(exist_ok=True)
    log_path = logs / SETUP_LOG
    report = {'version': 'v337', 'checked_utc': datetime.now(timezone.utc).isoformat(),
              'python': str(python), 'setup_log': str(log_path), 'bundled_drt_available': True, 'tools': {}}
    tasks = {'deareis': ('DearEIS', _deareis), 'pydrttools': ('pyDRTtools', _pydrttools)}
    print(f'Checking optional analysis tools in {python.parent.parent}. Log {log_path}', flush=True)
    for key, (name, operation) in tasks.items():
        if tool not in ('all', key):
            continue
        try:
            status = operation(root, python, log_path)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            status = {'status': 'needs_attention', 'error': str(error)}
        report['tools'][name] = status
        outcome = status['status']
        if outcome == 'needs_attention':
            print(f'{name} needs attention. Review {status.get("install_log", log_path)}. eStudio can still use bundled DRT.', flush=True)
        else:
            print(f'{name} is {"already verified" if outcome == "already_ready" else "installed and verified"}.', flush=True)
    _write_status(root, report)
    print(f'Analysis tool status saved to {root / STATUS_NAME}', flush=True)
    return int(any(item['status'] == 'needs_attention' for item in report['tools'].values()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--tool', choices=('all', 'deareis', 'pydrttools'), default='all')
    parser.add_argument('--auto', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return setup(args.root, tool=args.tool)


if __name__ == '__main__':
    raise SystemExit(main())
