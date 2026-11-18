"""Launch and supervise the separately installed SciLink Streamlit app."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser


ADDRESS = "127.0.0.1"
DEFAULT_PORT = 8501
DEFAULT_TIMEOUT = 105.0
SHA = re.compile(r"[0-9a-f]{40}")
INSTANCE = re.compile(r"[0-9a-f]{24}")
RUNTIME = "runtime.json"


def _read_json(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path, payload):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _active(root):
    payload = _read_json(root / "active.json")
    if not SHA.fullmatch(str(payload.get("commit", ""))) or not str(payload.get("version", "")).strip():
        raise ValueError("No active SciLink version found. Run the SciLink installer or updater first.")
    return {"commit": payload["commit"], "version": str(payload["version"])}


def _python(root, commit, candidate_venv=None):
    if not SHA.fullmatch(commit):
        raise ValueError("The SciLink revision must be a 40-character hexadecimal Git commit.")
    venv = (root / "versions" / commit / "venv") if candidate_venv is None else candidate_venv.resolve()
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise FileNotFoundError(f"The SciLink environment is missing its Python executable: {python}")
    return python


def _base(instance):
    return f"scilink-{instance}"


def _url(port, instance):
    return f"http://{ADDRESS}:{port}/{_base(instance)}/"


def _healthy(port, instance, timeout=0.35):
    if type(port) is not int or not 1 <= port <= 65535 or not INSTANCE.fullmatch(str(instance)):
        return False
    connection = http.client.HTTPConnection(ADDRESS, port, timeout=timeout)
    try:
        connection.request("GET", f"/{_base(instance)}/_stcore/health")
        response = connection.getresponse()
        return response.status == 200 and response.read(8).strip().lower() == b"ok"
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def healthy_runtime(root, commit=None):
    """Return a validated receipt only for an active managed loopback server."""
    row = _read_json(root / RUNTIME)
    port, instance = row.get("port"), row.get("instance")
    if (not SHA.fullmatch(str(row.get("commit", ""))) or (commit and row["commit"] != commit)
            or row.get("url") != _url(port, instance) or not _healthy(port, instance)):
        return None
    return row


def _unused_port(start):
    candidates = (0,) if start == 0 else range(start, min(start + 32, 65536))
    for port in candidates:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
                candidate.bind((ADDRESS, port))
                return candidate.getsockname()[1]
        except OSError:
            continue
    raise OSError(f"No free SciLink loopback port from {start} through {min(start + 31, 65535)}.")


@contextmanager
def _startup_lock(root, timeout):
    """Serialize a startup and publish its receipt without blocking a running server."""
    lock_path = root / ".launch.lock"
    deadline = time.monotonic() + timeout
    with lock_path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()

            def set_lock(blocked):
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK if blocked else msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def set_lock(blocked):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB if blocked else fcntl.LOCK_UN)

        while True:
            try:
                set_lock(True)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Another SciLink launcher is still starting the app.") from None
                time.sleep(0.1)
        try:
            yield
        finally:
            set_lock(False)


def _server_env(home, port, instance, probe=False):
    without = {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "SCILINK_SESSION_ROOT", "SCILINK_HOME"}
    env = {k: v for k, v in os.environ.items() if k.upper() not in without and not k.upper().startswith("STREAMLIT_")}
    if probe:
        env = {k: v for k, v in env.items() if not any(secret in k.upper() for secret in
               ("TOKEN", "API_KEY", "PASSWORD", "SECRET", "AUTHORIZATION"))}
        env.update({"HOME": str(home), "USERPROFILE": str(home), "XDG_CACHE_HOME": str(home / "cache"),
                    "XDG_CONFIG_HOME": str(home / "config"), "XDG_DATA_HOME": str(home / "data")})
    env.update({
        "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "SCILINK_HOME": str(home),
        "STREAMLIT_SERVER_ADDRESS": ADDRESS, "STREAMLIT_BROWSER_SERVER_ADDRESS": ADDRESS,
        "STREAMLIT_SERVER_PORT": str(port), "STREAMLIT_BROWSER_SERVER_PORT": str(port),
        "STREAMLIT_SERVER_BASE_URL_PATH": _base(instance), "STREAMLIT_SERVER_HEADLESS": "true",
        "STREAMLIT_SERVER_ENABLE_CORS": "true", "STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION": "true",
        "STREAMLIT_GLOBAL_DEVELOPMENT_MODE": "false", "STREAMLIT_SERVER_ENABLE_STATIC_SERVING": "false",
        "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false", "STREAMLIT_SERVER_FILE_WATCHER_TYPE": "none",
    })
    return env


def _process_options(detached=False):
    if os.name != "nt":
        return {"start_new_session": True}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP
    flags |= subprocess.DETACHED_PROCESS if detached else subprocess.CREATE_NO_WINDOW
    return {"creationflags": flags}


def _start_server(python, cwd, home, port, instance, log_path, *, probe=False):
    cwd.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    with log_path.open("ab") as log:
        log.write(f"\n{_now()} · Starting SciLink at {_url(port, instance)}\n".encode("utf-8"))
        log.flush()
        return subprocess.Popen([str(python), "-m", "scilink.cli.main", "ui", "--streamlit"], cwd=cwd,
                                env=_server_env(home, port, instance, probe), stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, **_process_options())


def _stop_server(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=8, check=False)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
        process.wait(timeout=5)


def _wait_server(process, port, instance, timeout, stopped=None):
    event = stopped or threading.Event()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not event.is_set():
        if process.poll() is not None:
            raise RuntimeError(f"SciLink exited before opening its local browser endpoint (exit {process.returncode}).")
        if _healthy(port, instance):
            return
        event.wait(0.15)
    if event.is_set():
        raise InterruptedError("SciLink launch was stopped.")
    raise TimeoutError("SciLink did not become ready on its local browser endpoint.")


def _supervise(root, port_start, timeout):
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda _signal, _frame: stopped.set())
    process = None
    instance = secrets.token_hex(12)
    try:
        with _startup_lock(root, timeout):
            active = _active(root)
            if healthy_runtime(root, active["commit"]):
                return 0
            python = _python(root, active["commit"])
            port = _unused_port(port_start)
            log_path = root / "logs" / f"scilink_{active['commit'][:12]}.log"
            process = _start_server(python, root / "sessions", root / "scilink_home", port, instance, log_path)
            try:
                _wait_server(process, port, instance, timeout, stopped)
            except (OSError, RuntimeError) as error:
                raise RuntimeError(f"{error} See {log_path}") from error
            receipt = {**active, "url": _url(port, instance), "port": port, "pid": process.pid,
                       "supervisor_pid": os.getpid(), "instance": instance, "started_utc": _now(), "log": str(log_path)}
            _write_json(root / RUNTIME, receipt)

        while process.poll() is None and not stopped.wait(0.35):
            replacement = _read_json(root / RUNTIME)
            next_instance = replacement.get("instance")
            if next_instance and next_instance != instance and healthy_runtime(root):
                break
        return process.returncode or 0
    finally:
        if process is not None:
            _stop_server(process)
            try:
                with _startup_lock(root, timeout):
                    if _read_json(root / RUNTIME).get("instance") == instance:
                        (root / RUNTIME).unlink(missing_ok=True)
            except TimeoutError:
                pass


def _probe(root, commit, candidate_venv, timeout):
    python = _python(root, commit, candidate_venv)
    log_path = root / "logs" / f"probe_{commit[:12]}.log"
    port = _unused_port(0)
    instance = secrets.token_hex(12)
    with tempfile.TemporaryDirectory(prefix="scilink-probe-") as directory:
        probe_root = Path(directory)
        process = _start_server(python, probe_root / "sessions", probe_root / "home", port, instance, log_path, probe=True)
        try:
            _wait_server(process, port, instance, timeout)
        except (OSError, RuntimeError) as error:
            raise RuntimeError(f"{error} See {log_path}") from error
        finally:
            _stop_server(process)
    return {"commit": commit, "ready": True, "log": str(log_path)}


def _launch(root, port, timeout):
    current = _active(root)
    running = healthy_runtime(root, current["commit"])
    if running:
        return running
    _python(root, current["commit"])
    launcher = Path(__file__).resolve()
    supervisor_log = root / "logs" / "launcher.log"
    with supervisor_log.open("ab") as log:
        child = subprocess.Popen([sys.executable, str(launcher), "--root", str(root), "--supervise",
                                  "--port", str(port), "--timeout", str(timeout)], cwd=root,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 **_process_options(detached=True))
    deadline = time.monotonic() + timeout + 2
    while time.monotonic() < deadline:
        running = healthy_runtime(root, _active(root)["commit"])
        if running:
            return running
        if child.poll() not in (None, 0):
            break
        time.sleep(0.15)
    raise RuntimeError(f"SciLink did not open its local browser endpoint. See {supervisor_log}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="preferred loopback port")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="startup timeout in seconds")
    entry = parser.add_mutually_exclusive_group()
    entry.add_argument("--status", action="store_true", help="print the healthy running instance receipt")
    entry.add_argument("--no-open", action="store_true", help="start the server without launching a browser tab")
    entry.add_argument("--probe-version", metavar="COMMIT", help="start and stop an isolated candidate")
    entry.add_argument("--supervise", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--probe-path", type=Path, metavar="VENV", help="use a candidate virtual environment for a probe")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or not 0 < args.timeout <= 900:
        parser.error("Use a port from 1 to 65535 and a startup timeout between 0 and 900 seconds.")
    if args.probe_path is not None and args.probe_version is None:
        parser.error("--probe-path requires --probe-version.")
    root = args.root.expanduser().resolve()
    if args.status:
        runtime = healthy_runtime(root)
        if runtime is None:
            return 1
        print(json.dumps(runtime))
        return 0
    try:
        (root / "logs").mkdir(parents=True, exist_ok=True, mode=0o700)
        if args.supervise:
            return _supervise(root, args.port, args.timeout)
        if args.probe_version is not None:
            print(json.dumps(_probe(root, args.probe_version, args.probe_path, args.timeout)))
            return 0
        runtime = _launch(root, args.port, args.timeout)
        print(json.dumps(runtime))
        if not args.no_open and not webbrowser.open(runtime["url"], new=2):
            raise RuntimeError(f"SciLink is ready at {runtime['url']} but the default browser did not open.")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        if sys.stderr is not None:
            print(str(error), file=sys.stderr)
        try:
            with (root / "logs" / "launcher.log").open("a", encoding="utf-8") as log:
                log.write(f"{_now()} · {error}\n")
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
