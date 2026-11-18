"""Reviewed SciLink, Edison and LabRIC jobs for the loopback eStudio browser."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import asdict, dataclass
from hashlib import sha256
from ipaddress import ip_address
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit

from .readable_names import new_folder, path_component
from .release_identity import APP_TITLE, APP_VERSION


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "gemma4:31b"
DEFAULT_ENDPOINT = "http://127.0.0.1:11434/v1"
EDISON_ENDPOINT = "https://api.platform.edisonscientific.com"
ROLES = {"Data", "Metadata", "Supporting file"}


@dataclass(frozen=True)
class ReviewedFile:
    role: str
    label: str
    path: Path
    bytes: int
    digest: str


@dataclass(frozen=True)
class ServiceReview:
    service: str
    folder: Path
    prompt: Path
    prompt_digest: str
    files: tuple[ReviewedFile, ...]


def _write_json(path, payload):
    scratch = path.with_name(path.name + ".tmp")
    try:
        scratch.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        scratch.replace(path)
    finally:
        scratch.unlink(missing_ok=True)


def _check_stop(event):
    if event is not None and event.is_set():
        raise InterruptedError("The local service job was stopped.")


def _digest(source, event, destination=None):
    digest = sha256()
    with source.open("rb") as stream:
        size = 0
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            _check_stop(event)
            if destination is not None:
                destination.write(block)
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def prepare_review(workspace, service, prompt, selections, *, sample_name=None,
                   cancel_event=None, progress=lambda message: None):
    """Save the exact prompt and only the explicitly selected files, without a network call."""
    if service not in {"scilink", "edison"}:
        raise ValueError("Select SciLink or Edison Scientific.")
    if not prompt.strip():
        raise ValueError("Enter an analysis prompt.")
    paths = {}
    for role, raw in selections:
        if role not in ROLES or (service == "scilink" and role == "Supporting file"):
            raise ValueError(f"The selected service does not accept the {role} role.")
        path = Path(raw).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"Choose a file: {path}")
        if path in paths and paths[path] != role:
            raise ValueError(f"Choose one role for {path.name}.")
        paths[path] = role
    metadata = [path for path, role in paths.items() if role == "Metadata"]
    if service == "scilink" and (len(metadata) > 1 or any(p.suffix.lower() not in {".json", ".txt"} for p in metadata)):
        raise ValueError("For SciLink, select one JSON or TXT metadata file.")

    _check_stop(cancel_event)
    label = "SciLink" if service == "scilink" else "Edison"
    folder = new_folder(Path(workspace).expanduser().resolve() / "Service jobs", f"{sample_name or 'Experiment'}_{label}", reserve=55)
    try:
        if os.name != "nt":
            folder.chmod(0o700)
        prompt_path = folder / "analysis_prompt.md"
        prompt_bytes = prompt.encode("utf-8")
        prompt_path.write_bytes(prompt_bytes)
        seen = set()
        staged = []
        for index, (source, role) in enumerate(paths.items(), 1):
            _check_stop(cancel_event)
            progress(f"Copying selected file {index}/{len(paths)} · {source.name}")
            directory = folder / {"Data": "data", "Metadata": "metadata", "Supporting file": "support"}[role]
            directory.mkdir(exist_ok=True)
            name = path_component(directory, source.name, reserve=0, max_chars=104)
            if (directory, name.casefold()) in seen:
                name = path_component(directory, f"{index:04d}_{name}", reserve=0, max_chars=104)
            seen.add((directory, name.casefold()))
            destination = directory / name
            before = source.stat()
            with destination.open("xb") as stream:
                size, digest = _digest(source, cancel_event, stream)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or size != after.st_size:
                raise ValueError(f"Source file changed while preparing the review: {source.name}")
            staged.append(ReviewedFile(role, source.name, destination, size, digest))
        _check_stop(cancel_event)
        review = ServiceReview(service, folder, prompt_path, sha256(prompt_bytes).hexdigest(), tuple(staged))
        _write_json(folder / "review.json", {"service": service, "launch_mode": "local browser",
                    "app_version": APP_VERSION,
                    "prompt_sha256": review.prompt_digest, "files": [asdict(item) for item in review.files]})
        return review
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def validate_endpoint(value):
    """Send authentication only to HTTPS or a plain-HTTP loopback endpoint."""
    address = str(value).strip()
    try:
        parsed = urlsplit(address)
        host = parsed.hostname
        if (not host or parsed.scheme not in {"http", "https"} or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or parsed.port == 0
                or any(char.isspace() or char == "\\" or ord(char) < 32 for char in address)):
            raise ValueError
    except ValueError:
        raise ValueError("Enter an absolute endpoint URL without credentials, query, or fragment.") from None
    if parsed.scheme == "http" and not _is_loopback(host):
        raise ValueError("Use HTTPS for an endpoint outside this computer.")
    return parsed.geturl()


def _is_loopback(host):
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _require_ascii_key(key):
    key = str(key or "").strip()
    if not key:
        raise ValueError("Enter the API key for this service or supply its environment variable.")
    if not key.isascii() or any(char.isspace() or ord(char) < 32 for char in key):
        raise ValueError("The API key contains an invalid HTTP header character.")
    return key


def test_labric(endpoint, key):
    from .v261_labric_mcp import test_connection

    key = _require_ascii_key(key)
    response = dict(test_connection(validate_endpoint(endpoint), key))
    if response.get("error"):
        response["error"] = str(response["error"]).replace(key, "[redacted]")
    return response


def _child_environment():
    forbidden = {"UNSAFE_EXECUTION_OK", "SCILINK_ACCEPT_CODE_EXECUTION", "OPENAI_BASE_URL", "OPENAI_API_BASE"}
    return {key: value for key, value in os.environ.items()
            if key.upper() not in forbidden
            and not any(fragment in key.upper() for fragment in ("API_KEY", "TOKEN", "PASSWORD", "SECRET", "AUTHORIZATION"))}


def _scilink_worker(review, settings, api_key, env):
    from .v290_electrochemistry_skill import prepare_job_skill

    endpoint = validate_endpoint(settings.get("endpoint") or DEFAULT_ENDPOINT)
    model = str(settings.get("model") or DEFAULT_MODEL).strip()
    if not model:
        raise ValueError("Enter the SciLink model name.")
    selected_python = str(settings.get("python") or sys.executable)
    executable = shutil.which(str(Path(selected_python).expanduser()))
    if not executable:
        raise ValueError("Choose the Python executable from your SciLink environment.")
    local = _is_loopback(urlsplit(endpoint).hostname)
    env["SCILINK_API_KEY"] = _require_ascii_key(api_key or ("ollama" if local else ""))
    if not settings.get("allow_code"):
        raise ValueError("Approve code execution for this headless SciLink run, or use interactive SciLink Streamlit.")
    job = review.folder
    env.update({"SCILINK_HOME": str(job / "scilink_home"), "MPLBACKEND": "Agg"})
    mode = ["--output-format", "json", "--mode", "autonomous", "--yes",
            "--session-dir", str(job / "session"), "--base-url", endpoint, "--model", model]
    selected_data = [item.path for item in review.files if item.role == "Data"]
    metadata = [item.path for item in review.files if item.role == "Metadata"]
    if selected_data:
        mode += ["--data", str(selected_data[0] if len(selected_data) == 1 else job / "data")]
    if metadata:
        mode += ["--metadata", str(metadata[0])]
    if settings.get("use_labric"):
        url = validate_endpoint(settings["labric_endpoint"])
        env["LABRIC_API_KEY"] = _require_ascii_key(settings.get("labric_key"))
        # SciLink expands header variables in its own child environment.
        # Its stdio MCP transport forwards a limited set of inherited values.
        mcp_config = job / "labric_mcp_scilink_config.json"
        _write_json(mcp_config, {"name": "labric", "transport": "http", "url": url,
                                "headers": {"Authorization": "Bearer ${LABRIC_API_KEY}"}})
        mode += ["--mcp", str(mcp_config)]
    skill = prepare_job_skill(job)
    mode += ["--skills", str(skill["job_skill_path"])]
    return ([executable, str(ROOT / "faradaiq" / "browser_scilink_worker.py"), str(review.prompt), *mode],
            job / "scilink_result.json")


def _edison_worker(review, api_key, env, event):
    from .v250_reliability import _write_exact_edison_job

    env["EDISON_PLATFORM_API_KEY"] = _require_ascii_key(api_key)
    folder = review.folder
    source = SimpleNamespace(base_name=folder.name, output_var=SimpleNamespace(get=lambda: str(folder)))
    selection = [{"path": str(item.path), "role": item.role, "source_group": "Reviewed in eStudio Browser"}
                 for item in review.files]
    settings = {"edison_base_url": EDISON_ENDPOINT, "edison_api_key_present": True}
    _write_exact_edison_job(source, review.prompt.read_bytes().decode("utf-8"), selection, settings, [],
                            prepared_job_dir=folder, cancel_event=event)
    return ([sys.executable, str(ROOT / "faradaiq_edison_cli_job.py"), "--job-dir", str(folder)],
            folder / "edison_job_response.json")


def _stop_worker(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
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
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def _execute_worker(command, folder, environment, *, cancel_event, log_path, stdout_path=None):
    group_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    with ExitStack() as stack:
        log = stack.enter_context(log_path.open("wb"))
        log.write(f"{APP_TITLE} Browser service job\n".encode("utf-8"))
        log.flush()
        output = stack.enter_context(stdout_path.open("wb")) if stdout_path else log
        process = subprocess.Popen(command, cwd=folder, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=output,
                                   stderr=log if stdout_path else subprocess.STDOUT, **group_options)
        try:
            while process.poll() is None:
                if cancel_event.wait(0.25):
                    _stop_worker(process)
                    _check_stop(cancel_event)
            return process.returncode
        except BaseException:
            if process.poll() is None:
                _stop_worker(process)
            raise


def run_reviewed(review, settings, api_key, *, cancel_event=None, progress=lambda message: None):
    """Run one previously reviewed job and retain its service receipt."""
    event = cancel_event or threading.Event()
    _check_stop(event)
    if _digest(review.prompt, event)[1] != review.prompt_digest:
        raise ValueError("The reviewed prompt changed. Prepare a new review.")
    for index, item in enumerate(review.files, 1):
        progress(f"Verifying reviewed file {index}/{len(review.files)} · {item.label}")
        if _digest(item.path, event) != (item.bytes, item.digest):
            raise ValueError(f"The reviewed file changed: {item.label}")
    state = review.folder / "submission.json"
    try:
        with state.open("x", encoding="utf-8") as receipt:
            json.dump({"service": review.service, "state": "preparing"}, receipt)
    except FileExistsError:
        raise RuntimeError("This review already started. Prepare a new review to run another job.") from None

    env = _child_environment()
    log = review.folder / "service.log"
    try:
        if review.service == "scilink":
            command, response = _scilink_worker(review, settings, api_key, env)
        else:
            progress("Creating and verifying the Edison audit ZIP…")
            command, response = _edison_worker(review, api_key, env, event)
        _check_stop(event)
    except BaseException:
        state.unlink(missing_ok=True)
        raise

    progress(f"Starting the reviewed {review.service} job. The local log is saved in {review.folder}.")
    _write_json(state, {"service": review.service, "state": "running", "log": str(log)})
    try:
        run_dir = review.folder
        if review.service == "scilink" and (run_dir / "data").is_dir():
            run_dir = run_dir / "data"
        code = _execute_worker(command, run_dir, env, cancel_event=event, log_path=log,
                               stdout_path=response if review.service == "scilink" else None)
    except BaseException as exc:
        status = "local tracking stopped" if isinstance(exc, InterruptedError) else "worker failed"
        _write_json(state, {"service": review.service, "state": status,
                            "log": str(log), "folder": str(review.folder)})
        raise
    status = "completed" if code == 0 else "worker failed"
    payload = {"service": review.service, "state": status, "exit_code": code, "folder": str(review.folder),
               "log": str(log), "response": str(response) if response.is_file() else ""}
    _write_json(state, payload)
    if code != 0:
        raise RuntimeError(f"The {review.service} worker returned exit code {code}. Review the service log in {review.folder}.")
    return payload
