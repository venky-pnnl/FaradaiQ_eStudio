"""Install and open a separately managed SciLink from its official GitHub revision."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener, urlopen
import venv
import webbrowser
import zipfile

from .release_identity import APP_VERSION as ESTUDIO_VERSION

APP_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "https://github.com/ziatdinovmax/SciLink"
COMMIT_URL = "https://api.github.com/repos/ziatdinovmax/SciLink/commits/HEAD"
ARCHIVE_URL = "https://codeload.github.com/ziatdinovmax/SciLink/zip/"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
USER_AGENT = "FaradaIQ-eStudio/" + ESTUDIO_VERSION
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def install_root():
    """The same stable root is used by setup and both eStudio interfaces."""
    if override := os.environ.get("FARADAIQ_SCILINK_ROOT"):
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        return Path(r"C:\FaradaIQ\SciLink")
    if sys.platform == "darwin":
        return Path.home() / "Applications" / "FaradaIQ SciLink"
    return Path.home() / ".local" / "share" / "FaradaIQ" / "SciLink"


def _read_json(path):
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def _valid_commit(commit):
    return isinstance(commit, str) and COMMIT_PATTERN.fullmatch(commit) is not None


def _write_json(path, data):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", delete=False) as file:
        temp = Path(file.name)
        try:
            json.dump(data, file, indent=2)
            file.write("\n")
        except BaseException:
            file.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _copy_asset(source, destination):
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
        temp = Path(file.name)
    try:
        shutil.copyfile(source, temp)
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


def _runtime_paths(root, commit):
    env = root / "versions" / commit / "venv"
    if os.name == "nt":
        return env / "Scripts/python.exe", env / "Scripts/scilink.exe"
    return env / "bin/python", env / "bin/scilink"


def _bootstrap(root, *, background=False):
    if os.name == "nt":
        suffix = "pythonw.exe" if background else "python.exe"
        return root / "bootstrap" / "Scripts" / suffix
    return root / "bootstrap" / "bin" / "python"


def current():
    """Return the active, executable installation receipt, if installed."""
    root = install_root()
    data = _read_json(root / "active.json")
    commit = data.get("commit")
    if not _valid_commit(commit) or not isinstance(data.get("version"), str):
        return None
    if not all(path.is_file() for path in _runtime_paths(root, commit)):
        return None
    return data


def runtime_python():
    return _runtime_paths(install_root(), data["commit"])[0] if (data := current()) else None


def runtime_cli():
    return _runtime_paths(install_root(), data["commit"])[1] if (data := current()) else None


@contextmanager
def _update_lock(root, progress):
    # Keep a stable lock inode. The OS releases the lock on abnormal exit.
    with (root / "update.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        waiting = False
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in {errno.EAGAIN, errno.EACCES, errno.EDEADLK}:
                    raise
                if not waiting:
                    progress("Waiting for the other SciLink update to finish.")
                    waiting = True
                time.sleep(0.75)
        yield


def _latest_commit():
    request = Request(COMMIT_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=25) as response:
        commit = json.load(response).get("sha")
    if not _valid_commit(commit):
        raise RuntimeError("The official SciLink repository did not return a valid commit.")
    return commit


def _download_source(commit, destination):
    request = Request(ARCHIVE_URL + commit, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response, destination.open("xb") as file:
        shutil.copyfileobj(response, file, length=1024 * 1024)


def _unpack_source(archive, target, commit):
    top = f"SciLink-{commit}"
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            entry = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if (not entry.parts or entry.parts[0] != top or ".." in entry.parts
                    or "\\" in member.filename or ":" in member.filename or stat.S_ISLNK(mode)):
                raise ValueError("SciLink source archive contains an unsafe path.")
        package.extractall(target)
    source = target / top
    if not (source / "pyproject.toml").is_file():
        raise RuntimeError("The SciLink source archive is missing its Python project.")
    return source


def _prepare_bootstrap(root):
    if not _bootstrap(root, background=True).is_file():
        venv.EnvBuilder(with_pip=False).create(root / "bootstrap")
    _copy_asset(APP_ROOT / "scripts" / "scilink_launch.py", root / "scilink_launch.py")
    for name in ("scilink.ico", "scilink.icns", "scilink.png"):
        _copy_asset(APP_ROOT / "assets" / name, root / name)


def _run_logged(command, log, progress, action, *, cwd):
    with log.open("ab") as file:
        file.write(("\n" + action + "\n").encode("utf-8"))
        file.flush()
        with subprocess.Popen([str(arg) for arg in command], cwd=cwd, stdout=file,
                              stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                              creationflags=NO_WINDOW) as process:
            while True:
                try:
                    result = process.wait(timeout=8)
                    break
                except subprocess.TimeoutExpired:
                    progress(f"{action} See {log.name} for current output.")
            if result:
                raise RuntimeError(f"{action} failed (exit {result}). Log: {log}")


def _install_candidate(root, commit, progress):
    target = root / "versions" / commit
    (root / "logs").mkdir(exist_ok=True)
    log = root / "logs" / f"install_{commit[:12]}_{ESTUDIO_VERSION}.log"
    python, cli = _runtime_paths(root, commit)
    progress(f"Downloading official SciLink source at {commit[:12]}.")
    with tempfile.TemporaryDirectory(prefix="source-", dir=root) as scratch:
        source_folder = Path(scratch)
        archive = source_folder / "scilink.zip"
        _download_source(commit, archive)
        source = _unpack_source(archive, source_folder, commit)
        progress("Creating the separate SciLink Python environment.")
        venv.EnvBuilder(with_pip=True).create(target / "venv")
        progress("Installing SciLink and its Python dependencies.")
        _run_logged([python, "-I", "-m", "pip", "install", "--disable-pip-version-check", str(source)],
                    log, progress, "Installing SciLink dependencies", cwd=root)
    if not cli.is_file():
        raise RuntimeError(f"SciLink did not install its CLI. Log: {log}")
    version = subprocess.check_output(
        [str(python), "-I", "-c", "from importlib.metadata import version; print(version('scilink'))"],
        cwd=root, text=True, timeout=30, creationflags=NO_WINDOW
    ).strip()
    if not version:
        raise RuntimeError(f"SciLink did not report its installed version. Log: {log}")
    progress("Checking that the SciLink Streamlit server starts.")
    _run_logged([_bootstrap(root), root / "scilink_launch.py", "--root", root,
                 "--probe-version", commit], log, progress, "Checking the SciLink Streamlit server", cwd=root)
    return version


def _update_result(root, receipt, changed, refresh_shortcut, progress):
    result = {**receipt, "changed": changed}
    if not refresh_shortcut:
        return result
    try:
        if os.name == "nt":
            subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            str(APP_ROOT / "scripts" / "install_scilink_shortcut.ps1"),
                            "-SciLinkRoot", str(root)], check=True, capture_output=True,
                           creationflags=NO_WINDOW, timeout=60)
        elif sys.platform == "darwin":
            from scripts.macos_shortcut import install_scilink_launcher
            shortcut = Path.home() / "Desktop" / "Holaris Apps" / "SciLink.app"
            install_scilink_launcher(root, shortcut, _read_json(APP_ROOT / "release.json"), root / "scilink.icns")
    except (ImportError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        result["shortcut_warning"] = "SciLink is installed. Setup can repair its desktop shortcut."
        progress(f"{result['shortcut_warning']} {error}")
    return result


def update(*, progress=None, refresh_shortcut=True):
    """Install a new revision only after it passes its local Streamlit probe."""
    if sys.version_info < (3, 12):
        raise RuntimeError("SciLink requires Python 3.12 or newer.")
    say = progress or (lambda _message: None)
    root = install_root()
    root.mkdir(parents=True, exist_ok=True)
    with _update_lock(root, say):
        say("Checking the official SciLink GitHub repository.")
        commit = _latest_commit()
        previous = current()
        _prepare_bootstrap(root)
        if previous and previous["commit"] == commit:
            return _update_result(root, previous, False, refresh_shortcut, say)
        candidate = root / "versions" / commit
        if candidate.exists():
            # A failed or interrupted install can leave this unselected path.
            # Keep any earlier verified environment rather than moving its venv.
            recorded = _read_json(candidate / "verified.json")
            python, cli = _runtime_paths(root, commit)
            if recorded.get("commit") == commit and recorded.get("version") and python.is_file() and cli.is_file():
                _write_json(root / "active.json", recorded)
                return _update_result(root, recorded, True, refresh_shortcut, say)
            shutil.rmtree(candidate)
        try:
            version = _install_candidate(root, commit, say)
            receipt = {"commit": commit, "version": version, "source": UPSTREAM,
                       "installed_at": datetime.now(timezone.utc).isoformat()}
            _write_json(candidate / "verified.json", receipt)
            _write_json(root / "active.json", receipt)
        except BaseException:
            shutil.rmtree(candidate, ignore_errors=True)
            raise
        say(f"SciLink {version} is ready at Git revision {commit[:12]}.")
        return _update_result(root, receipt, True, refresh_shortcut, say)


def _running_url(commit=None):
    state = _read_json(install_root() / "runtime.json")
    url = state.get("url")
    if not _valid_commit(state.get("commit")) or not isinstance(url, str):
        return None
    if commit is not None and state["commit"] != commit:
        return None
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                or parsed.username or parsed.password or parsed.fragment or parsed.query):
            return None
        opener = build_opener(ProxyHandler({}))
        with opener.open(url.rstrip("/") + "/_stcore/health", timeout=0.6) as response:
            return url if response.read(16).strip() == b"ok" else None
    except (OSError, ValueError):
        return None


def running_url():
    return _running_url(data["commit"]) if (data := current()) else None


def launch(*, no_open=False):
    """Start the active standalone revision and wait for its browser endpoint."""
    if not (active := current()):
        raise RuntimeError("SciLink has no working local installation. Choose Update SciLink first.")
    url = _running_url(active["commit"])
    if not url:
        root = install_root()
        supervisor = root / "scilink_launch.py"
        python = _bootstrap(root, background=True)
        logs = root / "logs"
        logs.mkdir(exist_ok=True)
        log = logs / f"launch_{ESTUDIO_VERSION}.log"
        options = {"creationflags": NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        with log.open("ab") as file:
            process = subprocess.Popen([str(python), str(supervisor), "--root", str(root), "--no-open"],
                                       cwd=root, stdin=subprocess.DEVNULL, stdout=file,
                                       stderr=subprocess.STDOUT, **options)
        deadline = time.monotonic() + 105
        while not url and time.monotonic() < deadline:
            url = _running_url(active["commit"])
            if not url and process.poll() not in {None, 0}:
                break
            if not url:
                time.sleep(0.25)
        if not url:
            raise RuntimeError(f"SciLink did not open its local browser endpoint. Log: {log}")
    if not no_open:
        webbrowser.open(url, new=2)
    return url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("update", "launch", "status"))
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "update":
            # Platform installers create or repair shortcuts even when an upstream check fails.
            result = update(progress=lambda message: print(message, flush=True), refresh_shortcut=False)
            if not result["changed"]:
                print(f"SciLink is current at {result['commit'][:12]}.", flush=True)
        elif args.action == "launch":
            result = {"url": launch(no_open=args.no_open)}
        else:
            result = current()
        print(json.dumps(result), flush=True)
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"SciLink: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
