"""Start an installed eStudio interface without a terminal window."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def log_directory():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'FaradaIQ' / 'eStudio' / 'logs'
    return Path.home() / 'Library' / 'Logs' / 'FaradaIQ eStudio'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('desktop', 'browser'))
    mode = parser.parse_args(argv).mode
    version = json.loads((ROOT / 'release.json').read_text(encoding='utf-8'))['version']
    windows = os.name == 'nt'
    python = ROOT / ('.faradaiq_venv/Scripts/python.exe' if windows else '.faradaiq_venv_macos/bin/python')
    entry = ROOT / f"faradaiq_{'estudio' if mode == 'desktop' else 'browser'}_{version}.py"
    logs = log_directory()
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"{'startup' if mode == 'desktop' else 'browser'}_{version}.log"
    with log_path.open('a', encoding='utf-8', buffering=1) as log:
        print(f'\n=== FaradaIQ eStudio {version} {mode} · {datetime.now().astimezone().isoformat()} ===', file=log)
        if not python.is_file() or not entry.is_file():
            print(f'Launcher could not find the installed environment or entry point at {ROOT}.', file=log)
            return 1
        environment = os.environ.copy()
        environment.update(PYTHONNOUSERSITE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
                           PYTHONFAULTHANDLER='1', FARADAIQ_APP_VERSION=version, FARADAIQ_QUIET_LAUNCH='1')
        if mode == 'desktop':
            environment.update(FARADAIQ_AI_LAUNCH_MODE='scilink_edison_llm',
                               FARADAIQ_SCIENCE_MODE='scilink_edison_llm', FARADAIQ_EDISON_ENABLED='1',
                               FARADAIQ_DIRECT_EDISON_ENABLED='1', FARADAIQ_LLM_TOOLS_ENABLED='1',
                               FARADAIQ_SKIP_STARTUP_AI_SELECTOR='1',
                               FARADAIQ_RUNTIME_STATS_DIR=str(ROOT / 'FaradaIQ_runtime_estimates'))
        try:
            subprocess.Popen([str(python), '-u', str(entry)], cwd=ROOT, env=environment,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW if windows else 0,
                             start_new_session=not windows)
        except OSError as error:
            print(f'Launcher failed: {error}', file=log)
            if sys.stderr is not None:
                print(f'Could not start eStudio {mode}. See {log_path}.', file=sys.stderr)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
