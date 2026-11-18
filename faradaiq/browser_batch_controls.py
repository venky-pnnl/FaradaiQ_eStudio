"""Headless access to the existing eStudio batch queue and its job settings."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from queue import Empty

import streamlit as st

from .v233_batch_jobs import JOBS_SCHEMA, BatchJob, default_drt_settings, default_export_settings


EXPORT_OPTIONS = {
    "archive_npy_and_summary_json": "Parsed NPY and summary JSON",
    "analysis_ready_csv": "Analysis-ready CSV",
    "raw_vendor_json": "Raw vendor JSON",
    "run_technique_parsers": "Technique parsers",
    "canonical_h5_json": "Canonical HDF5 and JSON",
    "build_browser_cache": "Desktop data browser cache",
    "create_experiment_subfolder": "Create an experiment subfolder",
    "resume_completed_stages": "Resume completed stages",
}


def save_queue_file(model, path):
    path = Path(path).expanduser().resolve()
    if path.suffix.casefold() != ".json":
        raise ValueError("Use a .json file for the job queue.")
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("Choose a different file. The destination is not a saved job queue.") from error
        if not isinstance(previous, dict) or previous.get("schema") != JOBS_SCHEMA:
            raise ValueError("Choose a different file. The destination is not a saved job queue.")
    original = model.queue_path
    try:
        return model.save_queue_as(path)
    except OSError:
        model.queue_path = original
        raise


def run_queue(model, queued, workers, cancel, report):
    """Run selected jobs with the same scheduler, checkpoints, and STOP as Tk."""
    model.stop_event = cancel
    model.stop_after_current = False
    model.running = True
    model.parallel_workers = min(workers, len(queued))
    model.parallel_enabled = model.parallel_workers > 1
    model._run_job_ids = {job.job_id for job in queued}
    original = model.set_state

    def set_state(job, state, stage, percent, message):
        original(job, state, stage, percent, message)
        report(f"{job.experiment_name} · {percent:.1f}% · {message}")
        while True:
            try:
                model.events.get_nowait()
            except Empty:
                break

    model.set_state = set_state
    try:
        model._worker_loop()
    finally:
        del model.set_state
    if cancel.is_set():
        raise InterruptedError("Batch stopped.")
    failed = [job.experiment_name for job in queued if job.status == "Failed"]
    if failed:
        raise RuntimeError(f"Review failed jobs: {', '.join(failed)}")
    return {"queue_path": str(model.queue_path), "waiting": sum(job.status == "Queued" for job in queued)}


def render_job_settings(model, labels, *, busy, show_file):
    with st.expander("Job settings and outputs"):
        job_id = st.selectbox("Job to configure", tuple(labels), format_func=labels.get)
        job = next(row for row in model.jobs if row.job_id == job_id)
        settings = default_export_settings() | job.export_settings
        drt = default_drt_settings() | job.drt_settings
        with st.form(f"batch-settings:{job_id}"):
            name = st.text_input("Experiment name", job.experiment_name, disabled=busy)
            folder = st.text_input("Job output folder", job.output_dir, disabled=busy)
            formats = {key: st.checkbox(label, bool(settings[key]), disabled=busy)
                       for key, label in EXPORT_OPTIONS.items()}
            grid = st.number_input("EIS parser grid size", min_value=10, max_value=10000,
                                   value=int(settings["eis_n_grid"]), disabled=busy)
            enabled = st.checkbox("Enable batch DRT", value=bool(drt["enabled"]), disabled=busy)
            regularization = st.number_input("DRT regularization", min_value=1e-12,
                                             value=float(drt["regularization"]), format="%.3e", disabled=busy)
            save = st.form_submit_button("Save job settings", disabled=busy)
        if save:
            export_settings = settings | formats | {"eis_n_grid": grid}
            drt_settings = drt | {"enabled": enabled, "regularization": str(regularization)}
            updated = replace(job, experiment_name=name.strip(), output_dir=folder.strip(),
                              export_settings=export_settings, drt_settings=drt_settings)
            errors = updated.validate()
            if errors:
                st.error("; ".join(errors))
            elif updated != job:
                updated.completed_stages = []
                updated.outputs = {}
                updated.status, updated.stage, updated.progress = "Draft", "draft", 0.0
                if updated.output_dir != job.output_dir or updated.experiment_name != job.experiment_name:
                    updated.session_folder = ""
                try:
                    model.write_job_session(updated, include_browser=False)
                    index = model.jobs.index(job)
                    model.jobs[index] = updated
                    if model.autosave_queue() is None:
                        model.jobs[index] = job
                        raise OSError("Could not save the queue.")
                except (ValueError, OSError) as error:
                    st.error(str(error))
                else:
                    st.success("Saved the job settings.")
                    job = updated
        if job.session_json:
            show_file("Download this job's session", job.session_json)
        if job.log_path and Path(job.log_path).is_file():
            show_file("Download this job's log", job.log_path)
        for public_key, label in (("canonical_bundle", "Download this job's HDF5"),):
            output = job.outputs.get(public_key) or {}
            if isinstance(output, dict) and output.get("h5_path"):
                show_file(label, output["h5_path"])
