"""Launch the version-matched local workbench from the native desktop."""
from pathlib import Path
import subprocess
import os
import sys
from .release_identity import APP_VERSION


def launch(source=None):
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, str(root/f'faradaiq_browser_{APP_VERSION}.py'), '--workbench']
    if source and Path(source).expanduser().exists(): command.extend(['--source', str(source)])
    subprocess.Popen(command, cwd=root, env=dict(os.environ, FARADAIQ_QUIET_LAUNCH='1'),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0,
                     start_new_session=sys.platform!='win32')
    return command
