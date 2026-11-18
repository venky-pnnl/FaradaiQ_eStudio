"""Streamlit controls for reviewed science-service transactions."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import streamlit as st

from .browser_services import (
    DEFAULT_ENDPOINT, DEFAULT_MODEL, EDISON_ENDPOINT, prepare_review, run_reviewed,
    test_labric, validate_endpoint,
)
from .scilink_browser_handoff import open_local_folder, streamlit_handoff_files
from .v261_labric_mcp import DEFAULT_URL as DEFAULT_LABRIC_URL


REVIEW = "Service review"
RUN = "Service job"
LABRIC_CHECK = "LabRIC connection test"


def _lines(text):
    return [line.strip().strip('"') for line in text.splitlines() if line.strip()]


def _first_env(*names):
    return next((os.environ[name] for name in names if os.environ.get(name)), "")


def _scilink_python_default():
    from . import managed_scilink

    return _first_env("SCILINK_PYTHON", "FARADAIQ_SCILINK_PYTHON") or str(managed_scilink.runtime_python() or sys.executable)


def _bundle_options(bundle):
    if bundle is None:
        return {}
    try:
        files = streamlit_handoff_files(bundle)
    except (OSError, ValueError):
        return {}
    return {f"Bundle {role.lower()} · {files[role].name}": (role, files[role])
            for role in ("Data", "Metadata")}


def _prepare_tab(service, workspace, submit, busy, bundle):
    choices = _bundle_options(bundle)
    with st.form("service_new_review", clear_on_submit=False):
        st.write("Choose only the inputs you want this service to receive. An empty file list sends the prompt alone.")
        selected_bundle = st.multiselect("Files from the current SciLink bundle", list(choices)) if choices else []
        data = st.text_area("Data file paths", help="One local file path per line. Leave blank for a text-only request.")
        metadata = st.text_area("Metadata file paths", help="SciLink accepts one JSON or TXT metadata file.")
        support = st.text_area("Supporting file paths") if service == "edison" else ""
        question = st.text_area("Exact analysis prompt", height=135)
        stage = st.form_submit_button("Prepare local review", type="primary", disabled=busy())
    if stage:
        selected = [choices[name] for name in selected_bundle]
        selected += [(role, file) for role, paths in (("Data", data), ("Metadata", metadata),
                                                      ("Supporting file", support)) for file in _lines(paths)]
        try:
            destination = workspace()
        except (OSError, ValueError) as error:
            st.error(str(error))
        else:
            submit(REVIEW, lambda cancel, progress: prepare_review(
                destination, service, question, selected, cancel_event=cancel, progress=progress), cancelable=True)


def _labric_fields(key):
    endpoint = st.text_input("LabRIC MCP endpoint", value=_first_env("LABRIC_MCP_URL", "MCP_SERVER_URL") or DEFAULT_LABRIC_URL,
                             key=key + ":endpoint")
    key_text = st.text_input("LabRIC bearer key", type="password", key=key + ":key",
                             placeholder="Uses LABRIC_API_KEY if supplied in the launch environment")
    return endpoint, key_text or _first_env("LABRIC_API_KEY", "MCP_AUTH_TOKEN")


def _submit_tab(review, submit, busy):
    service = "SciLink" if review.service == "scilink" else "Edison Scientific"
    key = "service-review:" + str(review.folder)
    try:
        exact_prompt = review.prompt.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as error:
        st.error(f"Could not open this review. {error}")
        return
    st.subheader(f"Review {service} inputs")
    st.code(exact_prompt, language=None)
    if review.files:
        st.dataframe([{"Role": item.role, "Name": item.label, "Bytes": item.bytes, "SHA-256": item.digest}
                      for item in review.files], hide_index=True)
    else:
        st.caption("This review contains the prompt and zero experimental files.")
    st.code(str(review.folder), language=None)
    if review.service == "scilink":
        default_python = _scilink_python_default()
        executable = st.text_input("Python executable with SciLink installed", key=key + ":python", placeholder=default_python,
                                   help="Leave blank to use the active SciLink runtime. Enter a Python path to use a different installation.")
        endpoint = st.text_input("Model endpoint", value=DEFAULT_ENDPOINT, key=key + ":endpoint")
        model = st.text_input("Model name", value=DEFAULT_MODEL, key=key + ":model")
        api_key = st.text_input("SciLink model key", type="password", key=key + ":scilink-key",
                                help="Uses SCILINK_API_KEY from the launch environment when the field is empty.")
        use_labric = st.checkbox("Allow LabRIC MCP for this run", key=key + ":labric")
        labric_endpoint, labric_key = _labric_fields(key + ":labric") if use_labric else ("", "")
        allow_code = st.checkbox("Allow SciLink to run generated analysis code for this job", key=key + ":allow-code")
        st.caption("Headless SciLink runs one autonomous task and accepts follow-up questions automatically. Open SciLink Streamlit above for interactive review.")
        try:
            endpoint = validate_endpoint(endpoint)
            if use_labric:
                labric_endpoint = validate_endpoint(labric_endpoint)
        except ValueError as error:
            st.warning(str(error))
            return
        api_key = api_key or _first_env("SCILINK_API_KEY")
        settings = {"python": executable.strip() or default_python, "endpoint": endpoint, "model": model, "allow_code": allow_code,
                    "use_labric": use_labric, "labric_endpoint": labric_endpoint, "labric_key": labric_key}
        st.caption(f"Model endpoint for this request · {endpoint}")
        can_run = allow_code and (not use_labric or bool(labric_key))
    else:
        api_key = st.text_input("Edison Scientific API key", type="password", key=key + ":edison-key",
                                help="Uses EDISON_PLATFORM_API_KEY or EDISON_API_KEY from the launch environment.")
        api_key = api_key or _first_env("EDISON_PLATFORM_API_KEY", "EDISON_API_KEY", "EDISON_SCIENTIFIC_API_KEY")
        settings = {}
        st.caption(f"Edison receives each selected file and a reviewed audit ZIP at {EDISON_ENDPOINT}. "
                   "STOP ends local tracking. A task already created can continue in Edison.")
        can_run = bool(api_key)
    acknowledged = st.checkbox(f"I reviewed the files and prompt and authorize this {service} run.", key=key + ":confirm")
    if st.button(f"Start reviewed {service} job", type="primary", key=key + ":start",
                 disabled=not (acknowledged and can_run) or busy()):
        submit(RUN, lambda cancel, progress: run_reviewed(review, settings, api_key,
                                                        cancel_event=cancel, progress=progress), cancelable=True)


def _show_receipt(review, show_file):
    path = review.folder / "submission.json"
    if not path.is_file():
        return False
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    status = receipt.get("state", "unknown")
    label = f"Service job · {status}"
    ({"completed": st.success, "preparing": st.info, "running": st.info}.get(status, st.warning))(label)
    if st.button("Open service job folder", key="service-open:" + str(review.folder)):
        try:
            open_local_folder(review.folder)
        except OSError as error:
            st.error(str(error))
    response = receipt.get("response")
    if response and Path(response).is_file():
        show_file("Download service result", response)
    if (review.folder / "service.log").is_file():
        show_file("Download service log", review.folder / "service.log")
    if review.service == "edison":
        task = review.folder / "edison_task_receipt.json"
        try:
            trajectory = json.loads(task.read_text(encoding="utf-8")).get("live_url")
        except (OSError, ValueError):
            trajectory = None
        if isinstance(trajectory, str) and trajectory.startswith("https://platform.edisonscientific.com/trajectories/"):
            st.link_button("View Edison task", trajectory)
    if review.service == "scilink":
        reports = sorted((review.folder / "session").rglob("*.html"))
        for index, report in enumerate(reports, 1):
            show_file(f"Download report {index} · {report.name}", report)
    return True


def show_direct_services(*, workspace, submit, busy, show_file, bundle=None):
    """Display the reviewed service route below the manual SciLink handoff."""
    with st.expander("Run SciLink or Edison Scientific from eStudio"):
        service = st.radio("Analysis service", ["SciLink", "Edison Scientific"], horizontal=True)
        service_key = "scilink" if service == "SciLink" else "edison"
        _prepare_tab(service_key, workspace, submit, busy, bundle)
        review = st.session_state.get(REVIEW)
        if review is not None and not _show_receipt(review, show_file):
            _submit_tab(review, submit, busy)

    with st.expander("LabRIC MCP connection"):
        st.caption("The connection test lists available tools. It does not run a query or attach experiment files.")
        url, key = _labric_fields("labric-test")
        if st.button("Test LabRIC connection", disabled=not key or busy()):
            submit(LABRIC_CHECK, lambda _cancel, _progress: test_labric(url, key))
        if LABRIC_CHECK in st.session_state:
            st.json(st.session_state[LABRIC_CHECK])
