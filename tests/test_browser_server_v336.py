"""Run the launcher against real loopback sockets and Streamlit HTTP health."""

import os
from pathlib import Path
from queue import Empty, Queue
import re
import signal
import socket
import subprocess
import sys
from threading import Thread
import time
from urllib.request import build_opener, ProxyHandler

import pytest


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'faradaiq_browser_v336.py'
OPENER = build_opener(ProxyHandler({}))


@pytest.fixture
def local_environment(tmp_path):
    # Use a private runtime directory on every OS. The real installed app is untouched.
    return {**os.environ, 'FARADAIQ_BROWSER_RUNTIME_DIR': str(tmp_path)}


def test_explicit_busy_port_is_rejected(local_environment):
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        result = subprocess.run(
            [sys.executable, str(ENTRY), '--no-open', '--port', str(port)],
            cwd=ROOT, env=local_environment, text=True, capture_output=True, timeout=15,
        )
    assert result.returncode == 2
    assert f'Port {port} is in use' in result.stderr


def test_default_server_reuses_same_version_and_bypasses_older_port(local_environment):
    with socket.socket() as occupied:
        try:
            occupied.bind(('127.0.0.1', 8512))
        except OSError:
            pytest.skip('The default browser port is already in use by an unrelated application.')
        occupied.listen()
        command = [sys.executable, str(ENTRY), '--no-open']
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        server = subprocess.Popen(
            command, cwd=ROOT, env=local_environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags,
        )
        output = Queue()

        def read_output():
            for line in server.stdout:
                output.put(line)

        reader = Thread(target=read_output, daemon=True)
        reader.start()
        try:
            try:
                line = output.get(timeout=20)
            except Empty:
                pytest.fail(f'No browser startup output (exit {server.poll()}).')
            match = re.search(r'http://127\.0\.0\.1:(\d+)', line)
            assert match, line
            port = int(match.group(1))
            assert port != 8512
            url = match.group(0)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with OPENER.open(url + '/_stcore/health', timeout=0.5) as response:
                        assert response.read() == b'ok'
                    break
                except OSError:
                    assert server.poll() is None, list(output.queue)
                    time.sleep(0.1)
            else:
                pytest.fail(f'Browser never became healthy. {list(output.queue)}')
            second = subprocess.run(
                command, cwd=ROOT, env=local_environment, text=True, capture_output=True, timeout=20,
            )
            assert second.returncode == 0, second.stderr
            assert f'already running at {url}' in second.stdout
        finally:
            if server.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(server.pid), '/T', '/F'], capture_output=True, timeout=15)
                else:
                    server.send_signal(signal.SIGINT)
            server.wait(timeout=15)
            reader.join(timeout=5)
