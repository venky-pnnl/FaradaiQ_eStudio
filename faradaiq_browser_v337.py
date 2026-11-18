"""Run the eStudio Streamlit interface using the installed private Python."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from faradaiq.release_identity import APP_VERSION as VERSION

ROOT = Path(__file__).resolve().parent
ADDRESS = "127.0.0.1"
DEFAULT_PORT = 8512
DEFAULT_PORTS = range(DEFAULT_PORT, DEFAULT_PORT + 10)


def streamlit_command(port):
    if not 1 <= port <= 65535:
        raise ValueError("The browser port must be between 1 and 65535.")
    return [
        sys.executable, "-m", "streamlit", "run", str(ROOT / "faradaiq/browser_app.py"),
        f"--server.address={ADDRESS}", f"--server.port={port}",
        f"--browser.serverAddress={ADDRESS}", f"--browser.serverPort={port}",
        "--global.developmentMode=false",
        "--server.headless=true", "--server.enableCORS=true",
        "--server.enableXsrfProtection=true", "--server.enableStaticServing=false",
        "--server.fileWatcherType=none", "--server.maxUploadSize=512",
        "--browser.gatherUsageStats=false",
    ]


def _is_ready(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/_stcore/health", timeout=0.5) as response:
            return response.read() == b"ok"
    except OSError:
        return False


def _open_when_ready(process, url):
    for _ in range(120):
        try:
            process.wait(timeout=0.25)
            return
        except subprocess.TimeoutExpired:
            pass
        if _is_ready(url):
            webbrowser.open(url, new=2)
            return
    print(f"Open {url} in your browser when Streamlit finishes starting.", flush=True)


def _available_port(ports):
    for port in ports:
        with socket.socket() as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind((ADDRESS, port))
            except OSError:
                continue
            return port
    return None


def _instance_path():
    if directory := os.environ.get("FARADAIQ_BROWSER_RUNTIME_DIR"):
        folder = Path(directory).expanduser()
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        folder = Path(os.environ["LOCALAPPDATA"]) / "FaradaIQ" / "eStudio" / "runtime"
    elif sys.platform == "darwin":
        folder = Path.home() / "Library" / "Caches" / "FaradaIQ eStudio"
    else:
        folder = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "faradaiq-estudio"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    install_id = hashlib.sha256(os.fsencode(os.path.normcase(ROOT))).hexdigest()[:16]
    return folder / f"browser_{VERSION}_{install_id}"


@contextmanager
def _instance_lock(path):
    # The OS releases the lock after abnormal exit. Keep a stable lockfile inode.
    with path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            yield False
        else:
            yield True


def _connect_to_running_browser(port_file, no_open):
    # A second launch can arrive before Streamlit starts accepting HTTP requests.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            port = int(port_file.read_text(encoding="ascii"))
        except (OSError, ValueError):
            port = None
        if port in DEFAULT_PORTS:
            url = f"http://{ADDRESS}:{port}"
            if _is_ready(url):
                print(f"FaradaIQ eStudio Browser {VERSION} is already running at {url}", flush=True)
                if not no_open:
                    webbrowser.open(url, new=2)
                return 0
        time.sleep(0.15)
    print("An eStudio Browser launch is already in progress. Check its terminal before retrying.", file=sys.stderr)
    return 2


def _run_server(port, no_open):
    url = f"http://{ADDRESS}:{port}"
    environment = os.environ.copy()
    environment.update(MPLBACKEND="Agg", FARADAIQ_APP_VERSION=VERSION, PYTHONUNBUFFERED="1")
    print(f"FaradaIQ eStudio Browser {VERSION} · {url}", flush=True)
    print("Press Ctrl+C in this window to stop the local server.", flush=True)
    quiet_windows = os.name == "nt" and environment.get("FARADAIQ_QUIET_LAUNCH") == "1"
    with subprocess.Popen(streamlit_command(port), cwd=ROOT, env=environment,
                          creationflags=subprocess.CREATE_NO_WINDOW if quiet_windows else 0) as process:
        if not no_open:
            threading.Thread(target=_open_when_ready, args=(process, url), daemon=True).start()
        try:
            return process.wait()
        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, help=f"use one specific port (default: first available starting at {DEFAULT_PORT})")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("The browser port must be between 1 and 65535.")
    try:
        import pyarrow
        import streamlit
        import numpy
        # Check the binary interoperability used by interactive tables.
        pyarrow.array(numpy.array([1.0]))
    except (ImportError, RuntimeError, ValueError) as error:
        print(f"Browser dependencies could not be loaded: {error}", file=sys.stderr)
        return 1
    if args.check:
        if not (ROOT / "faradaiq/browser_app.py").is_file():
            print("Browser entry file is missing.", file=sys.stderr)
            return 1
        print(f"eStudio Browser {VERSION}: Streamlit {streamlit.__version__}, PyArrow {pyarrow.__version__}")
        return 0
    if args.port is not None:
        if _available_port((args.port,)) is None:
            print(f"Port {args.port} is in use. Reopen an existing eStudio Browser tab or choose a different --port.", file=sys.stderr)
            return 2
        return _run_server(args.port, args.no_open)

    try:
        instance = _instance_path()
        port_file = instance.with_suffix(".port")
        with _instance_lock(instance.with_suffix(".lock")) as acquired:
            if not acquired:
                return _connect_to_running_browser(port_file, args.no_open)
            port_file.unlink(missing_ok=True)
            port = _available_port(DEFAULT_PORTS)
            if port is None:
                print(f"Local browser ports {DEFAULT_PORTS.start}–{DEFAULT_PORTS.stop - 1} are in use. Start with a different --port.", file=sys.stderr)
                return 2
            port_file.write_text(str(port), encoding="ascii")
            try:
                return _run_server(port, args.no_open)
            finally:
                port_file.unlink(missing_ok=True)
    except OSError as error:
        print(f"Could not start the local browser: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
