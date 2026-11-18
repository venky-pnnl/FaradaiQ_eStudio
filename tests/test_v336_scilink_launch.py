import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import venv

import pytest


LAUNCHER = Path(__file__).resolve().parents[1] / "scripts" / "scilink_launch.py"
OLD = "a" * 40
NEW = "b" * 40


def fake_revision(root, commit):
    target = root / "versions" / commit / "venv"
    venv.EnvBuilder(with_pip=False).create(target)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    paths = subprocess.check_output([str(python), "-c", "import site; print(site.getsitepackages()[0])"], text=True)
    package = Path(paths.strip()) / "scilink" / "cli"
    package.mkdir(parents=True)
    (package / "main.py").write_text('''
import http.server
import os
from pathlib import Path
import sys

assert sys.argv[1:] == ['ui', '--streamlit']
assert os.environ['STREAMLIT_SERVER_HEADLESS'] == 'true'
assert os.environ['STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION'] == 'true'
assert os.environ['STREAMLIT_SERVER_ENABLE_CORS'] == 'true'
assert os.environ['STREAMLIT_BROWSER_GATHER_USAGE_STATS'] == 'false'
host = os.environ['STREAMLIT_SERVER_ADDRESS']
assert host == '127.0.0.1'
port = int(os.environ['STREAMLIT_SERVER_PORT'])
path = '/'+os.environ['STREAMLIT_SERVER_BASE_URL_PATH']+'/_stcore/health'
Path('launch_probe.txt').write_text(os.environ['SCILINK_HOME'])

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == path else 404)
        self.end_headers()
        self.wfile.write(b'ok')

server = http.server.HTTPServer((host, port), Handler)
server.serve_forever()
''', encoding="utf-8")
    return target


def activate(root, commit, version="0.0.84"):
    (root / "active.json").write_text(json.dumps({"commit": commit, "version": version}), encoding="utf-8")


def invoke(root, *args):
    return subprocess.run([sys.executable, str(LAUNCHER), "--root", str(root), "--timeout", "6", *args],
                          check=False, text=True, capture_output=True, timeout=12)


def check_status(root):
    status = invoke(root, "--status")
    assert status.returncode == 0, status.stderr
    receipt = json.loads(status.stdout)
    assert receipt["url"].startswith(f"http://127.0.0.1:{receipt['port']}/")
    return receipt


def wait_for(condition, seconds=6):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return condition()


def health(receipt):
    conn = http.client.HTTPConnection("127.0.0.1", receipt["port"], timeout=0.35)
    try:
        route = receipt["url"].split(str(receipt["port"]), 1)[1].rstrip("/")
        conn.request("GET", route + "/_stcore/health")
        response = conn.getresponse()
        return response.status == 200 and response.read() == b"ok"
    except OSError:
        return False
    finally:
        conn.close()


@pytest.mark.skipif(os.name == "nt", reason="test supervisor shutdown signal uses POSIX")
def test_silent_launch_reuses_and_then_hands_over_to_new_revision(tmp_path):
    root = tmp_path / "FaradaIQ SciLink"
    fake_revision(root, OLD)
    fake_revision(root, NEW)
    activate(root, OLD)
    managed = []
    try:
        with socket.socket() as other:
            other.bind(("127.0.0.1", 0))
            other.listen()
            occupied_port = other.getsockname()[1]
            first = invoke(root, "--no-open", "--port", str(occupied_port))
            assert first.returncode == 0, first.stderr
            old = check_status(root)
            managed.append(old)
            assert old["commit"] == OLD and old["port"] != occupied_port
            assert health(old)
            repeat = invoke(root, "--no-open")
            assert repeat.returncode == 0, repeat.stderr
            assert check_status(root)["pid"] == old["pid"]
            activate(root, NEW, "0.0.85")
            update = invoke(root, "--no-open", "--port", str(old["port"]))
            assert update.returncode == 0, update.stderr
            new = check_status(root)
            managed.append(new)
            assert new["commit"] == NEW and new["version"] == "0.0.85"
            assert health(new) and new["port"] != old["port"]
            assert wait_for(lambda: not health(old))
            assert (root / "sessions" / "launch_probe.txt").read_text() == str(root / "scilink_home")
    finally:
        for item in managed:
            if health(item):
                try:
                    os.kill(item["supervisor_pid"], signal.SIGTERM)
                except ProcessLookupError:
                    pass
        assert wait_for(lambda: not (root / "runtime.json").exists())


def test_candidate_probe_checks_loopback_without_changing_active_or_sessions(tmp_path):
    root = tmp_path / "SciLink"
    candidate = fake_revision(root, NEW)
    activate(root, OLD)
    args = ("--probe-version", NEW, "--probe-path", str(candidate))
    run = invoke(root, *args)
    assert run.returncode == 0, run.stderr
    assert json.loads((root / "active.json").read_text())["commit"] == OLD
    assert not (root / "runtime.json").exists()
    assert not (root / "sessions" / "launch_probe.txt").exists()
    assert (root / "logs" / f"probe_{NEW[:12]}.log").is_file()
    assert invoke(root, "--probe-version", "../other").returncode != 0


def test_stale_receipt_does_not_direct_a_browser_to_an_external_url(tmp_path):
    root = tmp_path / "SciLink"
    root.mkdir()
    activate(root, OLD)
    (root / "runtime.json").write_text(json.dumps({"commit": OLD, "url": "https://example.invalid"}))
    assert invoke(root, "--status").returncode != 0
