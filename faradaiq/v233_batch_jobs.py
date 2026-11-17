"""FaradaIQ Studio v290 parallel jobs, cached restore, and responsive controls.

Adds an XAFS-style workflow for drag/drop intake, per-experiment export and
DRT settings, persistent job sessions, sequential execution, recovery,
and direct browser-session restoration.  The implementation is non-destructive:
source files are never changed and every generated artifact is written under the
job's selected output folder.
"""
from __future__ import annotations

import json
import os
import queue as queue_module
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

VERSION = "v314"
APP_VERSION = "0.290.0"
JOBS_SCHEMA = "faradaiq-batch-export-jobs-v233"
QUEUE_SCHEMA = JOBS_SCHEMA  # private compatibility name used by older helpers
JOB_SCHEMA = "faradaiq-batch-job-v233"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _slug(value: Any, default: str = "experiment") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or default)).strip("._")
    return (text or default)[:120]


def _usable_work_area(win: Any) -> Tuple[int, int, int, int]:
    """Return the usable desktop work area as left, top, width, height.

    On Windows this excludes the taskbar through SPI_GETWORKAREA.  Other
    platforms use the reported screen bounds with a small safety margin.
    """
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                width = max(640, int(rect.right - rect.left))
                height = max(480, int(rect.bottom - rect.top))
                return int(rect.left), int(rect.top), width, height
        except Exception:
            pass
    try:
        width = max(640, int(win.winfo_screenwidth() or 1600))
        height = max(480, int(win.winfo_screenheight() or 900))
    except Exception:
        width, height = 1600, 900
    safety = 16 if os.name != "nt" else 64
    return 0, 0, width, max(480, height - safety)


def _place_in_work_area(win: Any, role: str = "batch") -> Tuple[int, int]:
    """Size and center a window without hiding its bottom controls."""
    left, top, work_w, work_h = _usable_work_area(win)
    if role == "main":
        target_w, target_h = int(work_w * 0.98), int(work_h * 0.96)
        min_w, min_h = 980, 620
    elif role == "transactions":
        target_w, target_h = min(1060, int(work_w * 0.78)), min(660, int(work_h * 0.72))
        min_w, min_h = 720, 420
    else:
        target_w, target_h = min(1660, int(work_w * 0.97)), min(980, int(work_h * 0.95))
        min_w, min_h = 980, 620
    # Tk geometry describes the client area; reserve space for native title and
    # resize borders so the decorated window remains inside the work area.
    safe_w = max(640, work_w - 28)
    safe_h = max(480, work_h - 64)
    width = max(min_w if safe_w >= min_w else 640, min(target_w, safe_w))
    height = max(min_h if safe_h >= min_h else 480, min(target_h, safe_h))
    width = min(width, safe_w)
    height = min(height, safe_h)
    x = left + max(4, (work_w - width) // 2)
    y = top + max(4, (work_h - height) // 2)
    try:
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.minsize(min(min_w, max(640, width - 240)), min(min_h, max(480, height - 200)))
    except Exception:
        pass
    return width, height


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    try:
        import numpy as np
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
    except Exception:
        pass
    return str(value)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def default_export_settings() -> Dict[str, Any]:
    return {
        "archive_npy_and_summary_json": True,
        "analysis_ready_csv": True,
        "raw_vendor_json": False,
        "run_technique_parsers": True,
        "canonical_h5_json": True,
        "build_browser_cache": True,
        "create_experiment_subfolder": False,
        "resume_completed_stages": True,
        "eis_n_grid": 100,
    }


def default_drt_settings() -> Dict[str, Any]:
    return {
        "enabled": True,
        "run_types": ["simple"],
        "plot_types": ["eis", "drt"],
        "export_types": ["drt"],
        "rbf_type": "gaussian",
        "data": "combined",
        "derivative": "1",
        "inductance": "without",
        "regularization": "1.0E-3",
        "shape_control": "fwhm",
        "coefficient": "0.5",
        "sample_number": "2000",
        "run_id": "",
    }


@dataclass
class BatchJob:
    source_path: str
    experiment_name: str
    output_dir: str
    job_id: str = field(default_factory=lambda: f"FQJOB_{_stamp()}_{uuid.uuid4().hex[:8]}")
    session_folder: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    status: str = "Draft"
    stage: str = "draft"
    progress: float = 0.0
    message: str = ""
    export_settings: Dict[str, Any] = field(default_factory=default_export_settings)
    drt_settings: Dict[str, Any] = field(default_factory=default_drt_settings)
    inspection_summary: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    completed_stages: List[str] = field(default_factory=list)
    error: str = ""
    traceback: str = ""
    session_json: str = ""
    log_path: str = ""
    progress_detail: str = ""
    progress_current: int = 0
    progress_total: int = 0
    elapsed_seconds: float = 0.0
    eta_seconds: Optional[float] = None
    started_at: str = ""
    finished_at: str = ""
    cancelled_at: str = ""

    def touch(self) -> None:
        self.updated_at = _now()

    @property
    def source(self) -> Path:
        return Path(self.source_path)

    @property
    def output(self) -> Path:
        return Path(self.output_dir)

    @property
    def base_name(self) -> str:
        return _slug(self.experiment_name or self.source.stem or self.source.name)

    @property
    def session_dir(self) -> Path:
        root=self.output / "FaradaIQ_job_sessions"
        legacy=root/self.job_id
        if legacy.exists():return legacy
        from .readable_names import new_folder,safe_component
        if self.session_folder:return root/safe_component(self.session_folder,max_chars=150)
        folder=new_folder(root,self.experiment_name or self.source.stem,reserve=72)
        self.session_folder=folder.name
        return folder

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BatchJob":
        allowed = set(cls.__dataclass_fields__)
        values = {k: v for k, v in dict(payload).items() if k in allowed}
        job = cls(**values)
        merged_export = default_export_settings(); merged_export.update(job.export_settings or {})
        merged_drt = default_drt_settings(); merged_drt.update(job.drt_settings or {})
        job.export_settings = merged_export
        job.drt_settings = merged_drt
        return job

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.source_path:
            errors.append("Source path is empty.")
        elif not self.source.exists():
            errors.append(f"Source path does not exist: {self.source}")
        if not self.experiment_name.strip():
            errors.append("Experiment name is empty.")
        if not self.output_dir.strip():
            errors.append("Output folder is empty.")
        try:
            n_grid = int(self.export_settings.get("eis_n_grid", 100))
            if n_grid < 10 or n_grid > 10000:
                errors.append("EIS parser grid must be between 10 and 10000.")
        except Exception:
            errors.append("EIS parser grid must be an integer.")
        if self.drt_settings.get("enabled"):
            try:
                reg = float(self.drt_settings.get("regularization", "1e-3"))
                if reg <= 0:
                    errors.append("DRT regularization must be positive.")
            except Exception:
                errors.append("DRT regularization is not numeric.")
            try:
                samples = int(self.drt_settings.get("sample_number", "2000"))
                if samples < 50:
                    errors.append("DRT sample number must be at least 50.")
            except Exception:
                errors.append("DRT sample number must be an integer.")
        return errors


class ExportCancelled(RuntimeError):
    """Raised when the user requests an immediate stop of active exports."""


def _format_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _progress_fraction_from_line(message: Any) -> Optional[float]:
    """Extract trustworthy live progress from parser/DRT console output.

    Supports explicit percentages and current/total forms such as ``83%``,
    ``6/10`` and ``6 of 10``. Values outside their valid range are ignored so
    scientific values in normal console text do not move the progress bar.
    """
    text = str(message or "")
    percentages = re.findall(r"(?<![\d.])(100(?:\.0+)?|\d{1,2}(?:\.\d+)?)\s*%", text)
    if percentages:
        try:
            value = float(percentages[-1])
            if 0.0 <= value <= 100.0:
                return value / 100.0
        except Exception:
            pass
    ratios = re.findall(r"(?<!\d)(\d{1,9})\s*(?:/|\bof\b)\s*(\d{1,9})(?!\d)", text, flags=re.IGNORECASE)
    for current_text, total_text in reversed(ratios):
        try:
            current, total = int(current_text), int(total_text)
            if 0 <= current <= total and 0 < total <= 10_000_000:
                return current / total
        except Exception:
            continue
    return None


class WeightedProgressTracker:
    """Translate per-stage completion into an accurate monotonic job percentage."""

    DEFAULT_WEIGHTS = {
        "inspection": 7.0,
        "export": 28.0,
        "technique_parsers": 24.0,
        "drt": 22.0,
        "canonical_bundle": 12.0,
        "browser_cache": 7.0,
    }

    def __init__(self, job: "BatchJob", report: Callable[[str, str, float, str], None], enabled: Sequence[str]) -> None:
        self.job = job
        self.report = report
        self.started = time.monotonic()
        self.last_percent = max(0.0, min(100.0, float(job.progress or 0.0)))
        stages = [stage for stage in self.DEFAULT_WEIGHTS if stage in set(enabled)]
        raw_total = sum(self.DEFAULT_WEIGHTS[stage] for stage in stages) or 1.0
        self.weights = {stage: 100.0 * self.DEFAULT_WEIGHTS[stage] / raw_total for stage in stages}
        self.starts: Dict[str, float] = {}
        cursor = 0.0
        for stage in stages:
            self.starts[stage] = cursor
            cursor += self.weights[stage]

    def update(
        self,
        stage: str,
        fraction: float,
        message: str,
        *,
        current: int = 0,
        total: int = 0,
        status: str = "Running",
    ) -> float:
        fraction = max(0.0, min(1.0, float(fraction)))
        if stage in self.weights:
            percent = self.starts[stage] + self.weights[stage] * fraction
        else:
            percent = self.last_percent
        percent = max(self.last_percent, min(99.8 if status == "Running" else 100.0, percent))
        self.last_percent = percent
        elapsed = max(0.0, time.monotonic() - self.started)
        eta = None
        if 0.5 < percent < 99.5 and elapsed > 0.5:
            eta = elapsed * (100.0 - percent) / percent
        self.job.progress_current = max(0, int(current or 0))
        self.job.progress_total = max(0, int(total or 0))
        self.job.elapsed_seconds = elapsed
        self.job.eta_seconds = eta
        count_text = f"{self.job.progress_current}/{self.job.progress_total}" if self.job.progress_total else ""
        self.job.progress_detail = " | ".join(x for x in (stage.replace("_", " "), count_text) if x)
        decorated = f"{message} | {percent:.1f}% | elapsed {_format_duration(elapsed)}"
        if eta is not None:
            decorated += f" | ETA {_format_duration(eta)}"
        if count_text:
            decorated += f" | {count_text}"
        self.report(status, stage, percent, decorated)
        return percent

    def complete(self, message: str) -> None:
        elapsed = max(0.0, time.monotonic() - self.started)
        self.job.elapsed_seconds = elapsed
        self.job.eta_seconds = 0.0
        self.job.progress_current = self.job.progress_total
        self.job.progress_detail = "completed"
        self.last_percent = 100.0
        self.report("Completed", "completed", 100.0, f"{message} | 100.0% | elapsed {_format_duration(elapsed)}")


class BatchQueueModel:
    def __init__(self, gui: Any, core: MutableMapping[str, Any]) -> None:
        self.gui = gui
        self.core = core
        self.jobs: List[BatchJob] = []
        self.queue_id = f"FQQUEUE_{_stamp()}_{uuid.uuid4().hex[:6]}"
        self.created_at = _now()
        self.updated_at = self.created_at
        self.queue_path: Optional[Path] = None
        self.window: Optional[Any] = None
        self.transaction_window: Optional[Any] = None
        self.pause_after_current = False
        self.stop_after_current = False
        self.running = False
        self.worker: Optional[threading.Thread] = None
        self.events: "queue_module.Queue[Tuple[str, Any]]" = queue_module.Queue()
        self._selected_job_id: Optional[str] = None
        self._persist_lock = threading.RLock()
        self._last_persist_at: Dict[str, float] = {}
        self._last_stage: Dict[str, str] = {}
        self.stop_event = threading.Event()
        self._active_processes: Dict[str, subprocess.Popen] = {}
        self._process_lock = threading.RLock()
        self._run_job_ids: Optional[set[str]] = None
        self._last_run_job_ids: set[str] = set()
        self.current_job_id: Optional[str] = None
        self.active_job_ids: set[str] = set()
        self.parallel_enabled = False
        self.parallel_workers = 1
        # v314: queue-level DRT defaults remember the most recently saved DRT
        # configuration and seed newly added jobs. Existing per-job settings
        # remain independent unless the user explicitly propagates them.
        self.drt_defaults: Dict[str, Any] = default_drt_settings()
        self._worker_threads: Dict[str, threading.Thread] = {}

    def default_output_for(self, source: Path) -> Path:
        try:
            root = Path(str(self.gui.output_dir_var.get() or ".")).expanduser().resolve()
        except Exception:
            root = Path.cwd()
        return root / _slug(source.stem if source.is_file() else source.name)

    def find(self, job_id: str) -> Optional[BatchJob]:
        return next((j for j in self.jobs if j.job_id == job_id), None)

    def find_source(self, path: Path) -> Optional[BatchJob]:
        """Return the existing job for a source, including completed jobs.

        Loading a completed job into the main GUI must never create a second job
        for the same source.  Non-terminal jobs are preferred when legacy job
        files already contain duplicates; otherwise the most recently updated
        completed/cancelled record is returned.
        """
        try:
            key = str(path.resolve()).casefold()
        except Exception:
            key = str(path).casefold()
        matches: List[BatchJob] = []
        for job in self.jobs:
            try:
                other = str(Path(job.source_path).resolve()).casefold()
            except Exception:
                other = str(job.source_path).casefold()
            if key == other:
                matches.append(job)
        if not matches:
            return None
        active = [job for job in matches if job.status not in {"Completed", "Cancelled"}]
        pool = active or matches
        return sorted(pool, key=lambda job: str(job.updated_at or job.created_at), reverse=True)[0]

    def add_paths(self, paths: Iterable[str | Path], status: str = "Draft", open_window: bool = True) -> List[BatchJob]:
        added: List[BatchJob] = []
        for raw in paths:
            path = Path(str(raw)).expanduser()
            if not path.exists():
                continue
            existing = self.find_source(path)
            if existing is not None:
                existing.status = status if existing.status == "Draft" else existing.status
                existing.touch()
                added.append(existing)
                continue
            name = path.stem if path.is_file() else path.name
            job = BatchJob(
                source_path=str(path.resolve()),
                experiment_name=name,
                output_dir=str(self.default_output_for(path)),
                status=status,
                stage="inspection_pending" if status.lower().startswith("inspect") else "draft",
                drt_settings=dict(self.drt_defaults),
            )
            self.jobs.append(job)
            self.write_job_session(job, include_browser=False)
            added.append(job)
        if added:
            self.updated_at = _now()
            self.autosave_queue()
            self.refresh_window(select_job=added[-1].job_id)
            if open_window:
                self.open_window(select_job=added[-1].job_id)
        return added

    def remove_jobs(self, ids: Sequence[str]) -> None:
        idset = set(ids)
        self.jobs = [j for j in self.jobs if j.job_id not in idset or j.status == "Running"]
        self.updated_at = _now()
        self.autosave_queue()
        self.refresh_window()

    def queue_root(self) -> Path:
        try:
            root = Path(str(self.gui.output_dir_var.get() or ".")).expanduser().resolve()
        except Exception:
            root = Path.cwd()
        out = root / "FaradaIQ_export_jobs"
        out.mkdir(parents=True, exist_ok=True)
        return out

    def autosave_queue(self) -> Optional[Path]:
        try:
            with self._persist_lock:
                if self.queue_path is None:
                    self.queue_path = self.queue_root() / f"{self.queue_id}.faradaiq_jobs.json"
                payload = {
                    "schema": QUEUE_SCHEMA,
                    "version": VERSION,
                    "queue_id": self.queue_id,
                    "created_at": self.created_at,
                    "updated_at": _now(),
                    "parallel_enabled": bool(self.parallel_enabled),
                    "parallel_workers": int(max(1, self.parallel_workers)),
                    "drt_defaults": _json_safe(self.drt_defaults),
                    "jobs": [j.to_dict() for j in self.jobs],
                }
                _atomic_json(self.queue_path, payload)
                return self.queue_path
        except Exception:
            return None

    def save_queue_as(self, path: str | Path) -> Path:
        self.queue_path = Path(path)
        saved = self.autosave_queue()
        if saved is None:
            raise OSError("Jobs file could not be saved.")
        return saved

    def load_queue(self, path: str | Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        schema = str(payload.get("schema", "")) if isinstance(payload, dict) else ""
        if not isinstance(payload, dict) or not (
            schema.startswith("faradaiq-batch-export-jobs")
            or schema.startswith("faradaiq-batch-export-queue")
        ):
            raise ValueError("This is not a FaradaIQ export-jobs JSON file.")
        self.queue_id = str(payload.get("queue_id") or f"FQQUEUE_{_stamp()}_{uuid.uuid4().hex[:6]}")
        self.created_at = str(payload.get("created_at") or _now())
        self.updated_at = str(payload.get("updated_at") or _now())
        self.parallel_enabled = bool(payload.get("parallel_enabled", False))
        try:
            self.parallel_workers = max(1, min(32, int(payload.get("parallel_workers", 1) or 1)))
        except Exception:
            self.parallel_workers = 1
        loaded_defaults = default_drt_settings()
        if isinstance(payload.get("drt_defaults"), Mapping):
            loaded_defaults.update(dict(payload.get("drt_defaults") or {}))
        self.drt_defaults = loaded_defaults
        self.jobs = [BatchJob.from_dict(row) for row in payload.get("jobs", []) if isinstance(row, dict)]
        for job in self.jobs:
            if job.status == "Running":
                job.status = "Interrupted"
                job.stage = "resume_available"
                job.message = "The previous application exited while this job was running. Select the job and press Run selected jobs to resume safely."
        self.queue_path = Path(path)
        self.autosave_queue()
        self.refresh_window()

    def _main_session_payload(self, job: BatchJob, include_browser: bool = True) -> Dict[str, Any]:
        cache = job.outputs.get("browser_cache") if isinstance(job.outputs, dict) else None
        bundle = job.outputs.get("canonical_bundle") if isinstance(job.outputs, dict) else None
        analysis_root = str(job.outputs.get("analysis_ready_root") or "")
        open_browsers: List[Dict[str, Any]] = []
        if include_browser and isinstance(cache, dict) and cache.get("cache_root"):
            h5_path = str((bundle or {}).get("h5_path") or cache.get("h5_path") or "")
            json_path = str((bundle or {}).get("json_path") or cache.get("json_path") or "")
            open_browsers.append({
                "schema": "faradaiq_open_browser_session_v177",
                "class_name": "FaradaIQH5Browser",
                "base_name": job.base_name,
                "analysis_root": analysis_root,
                "analyzed_root": str(Path(analysis_root) / f"{job.base_name}_FaradaIQ_analyzed_data") if analysis_root else "",
                "cache_root": str(cache.get("cache_root") or ""),
                "h5_path": h5_path,
                "json_path": json_path,
                "cache_info": _json_safe(cache),
                "vars": {},
                "plot_views": [],
                "last_plot_paths": [],
            })
        return {
            "schema": "faradaiq_main_session_v199",
            "app": "FaradaIQ Studio",
            "version": VERSION,
            "created": _now(),
            "main_vars": {
                "file_var": job.source_path,
                "output_dir_var": job.output_dir,
                "include_raw_json_var": bool(job.export_settings.get("raw_vendor_json")),
                "auto_run_pyseccm_after_export_var": bool(job.export_settings.get("run_technique_parsers")),
                "auto_build_browser_cache_after_parser_var": bool(job.export_settings.get("build_browser_cache")),
            },
            "main_texts": {
                "experimental_description": f"Experiment name: {job.experiment_name}\nBatch job: {job.job_id}",
                "session_summary": json.dumps(_json_safe(job.inspection_summary), ensure_ascii=False, indent=2),
                "data_structure_preview": "Batch job session generated by FaradaIQ Studio v290.",
            },
            "current_summary": _json_safe(job.inspection_summary),
            "current_analysis_root": analysis_root,
            "collection_state": {
                "source_path": job.source_path,
                "base_name": job.base_name,
                "entries_count": (job.inspection_summary or {}).get("n_entries", 1),
            },
            "open_browser_sessions": open_browsers,
            "batch_job": job.to_dict(),
            "notes": "Load this JSON with Load Session. Paths, settings, inspection summary, and canonical browser cache are restored when available.",
        }

    def write_job_session(self, job: BatchJob, include_browser: bool = True) -> Path:
        session_dir = job.session_dir
        session_dir.mkdir(parents=True, exist_ok=True)
        path = session_dir / "session.faradaiq_session.json"
        job.session_json = str(path)
        job.log_path = str(session_dir / "job.log")
        job.touch()
        payload = self._main_session_payload(job, include_browser=include_browser)
        _atomic_json(path, payload)
        _atomic_json(session_dir / "export_settings.json", job.export_settings)
        _atomic_json(session_dir / "drt_settings.json", job.drt_settings)
        _atomic_json(session_dir / "job_state.json", {"schema": JOB_SCHEMA, "version": VERSION, "job": job.to_dict()})
        return path

    def log(self, job: BatchJob, message: str, *, persist: bool = False) -> None:
        """Append a job log line without rewriting large session JSON for every parser line."""
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        job.message = message
        job.touch()
        try:
            path = Path(job.log_path or (job.session_dir / "job.log"))
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            job.log_path = str(path)
        except Exception:
            pass
        self.events.put(("log", (job.job_id, line)))
        now = time.monotonic()
        last = self._last_persist_at.get(job.job_id, 0.0)
        if persist or now - last >= 1.0:
            with self._persist_lock:
                self.write_job_session(job, include_browser=bool(job.outputs.get("browser_cache")))
                self.autosave_queue()
            self._last_persist_at[job.job_id] = now

    def set_state(self, job: BatchJob, status: str, stage: str, progress: float, message: str) -> None:
        previous_stage = self._last_stage.get(job.job_id, job.stage)
        job.status = status
        job.stage = stage
        job.progress = max(0.0, min(100.0, float(progress)))
        job.message = message
        job.touch()
        persist = status != "Running" or stage != previous_stage or job.progress in {0.0, 100.0}
        self._last_stage[job.job_id] = stage
        self.log(job, message, persist=persist)
        self.events.put(("refresh", job.job_id))

    def mark_completed_stage(self, job: BatchJob, stage: str) -> None:
        if stage not in job.completed_stages:
            job.completed_stages.append(stage)
        job.touch()
        self.write_job_session(job, include_browser=bool(job.outputs.get("browser_cache")))

    def inspect_completed_from_main(self, job: BatchJob) -> bool:
        matched = False
        try:
            current = str(getattr(self.gui.collection, "source_path", "") or "")
            if current and Path(current).resolve() == Path(job.source_path).resolve():
                matched = True
                job.inspection_summary = _json_safe(getattr(self.gui, "current_summary", {}) or {})
                job.status = "Inspected"
                job.stage = "inspected"
                job.progress = 10.0
                job.message = "Inspection completed in the main GUI. Customize the settings, then run this job or run all jobs."
                self.mark_completed_stage(job, "inspection")
            elif job.status == "Inspecting":
                job.status = "Inspection pending"
                job.stage = "inspection_pending"
                job.message = "The main GUI did not confirm this source. Select the job and press Load selected in main GUI to inspect again."
                self.write_job_session(job, include_browser=False)
        except Exception as exc:
            job.status = "Inspection failed"
            job.stage = "inspection_failed"
            job.error = str(exc)
            job.message = f"Inspection status could not be captured: {exc}"
            self.write_job_session(job, include_browser=False)
        self.autosave_queue()
        self.refresh_window(select_job=job.job_id)
        return matched

    def inspect_failed_from_main(self, job: BatchJob, exc: BaseException) -> None:
        job.status = "Inspection failed"
        job.stage = "inspection_failed"
        job.error = str(exc)
        job.traceback = traceback.format_exc()
        job.message = f"Load + inspect failed: {exc}"
        self.log(job, job.message, persist=True)
        self.refresh_window(select_job=job.job_id)

    def enqueue(self, ids: Sequence[str]) -> List[BatchJob]:
        out: List[BatchJob] = []
        for job_id in ids:
            job = self.find(job_id)
            if job is None or job.status == "Running":
                continue
            errors = job.validate()
            if errors:
                raise ValueError(f"{job.experiment_name}:\n" + "\n".join(errors))
            job.status = "Queued"
            job.stage = "queued"
            job.progress = 0.0
            job.progress_detail = "queued"
            job.progress_current = 0
            job.progress_total = 0
            job.elapsed_seconds = 0.0
            job.eta_seconds = None
            job.error = ""
            job.traceback = ""
            job.touch()
            self.write_job_session(job, include_browser=bool(job.outputs.get("browser_cache")))
            out.append(job)
        self.autosave_queue()
        self.refresh_window()
        return out

    def dequeue(self, ids: Sequence[str]) -> List[BatchJob]:
        """Compatibility helper: return selected waiting jobs to the editable Ready state."""
        changed: List[BatchJob] = []
        for job_id in ids:
            job = self.find(job_id)
            if job is None or job.status in {"Running", "Stopping"}:
                continue
            if job.status == "Queued":
                job.status = "Draft"
                job.stage = "draft"
                job.progress = 0.0
                job.progress_current = 0
                job.progress_total = 0
                job.progress_detail = "returned to ready"
                job.elapsed_seconds = 0.0
                job.eta_seconds = None
                job.message = "Returned to Ready. Settings and resumable session files were preserved."
                job.touch()
                self.write_job_session(job, include_browser=bool(job.outputs.get("browser_cache")))
                changed.append(job)
        self.autosave_queue()
        self.refresh_window()
        return changed

    def enqueue_pending(self, ids: Optional[Sequence[str]] = None) -> List[BatchJob]:
        selected = set(ids or [])
        candidates = [
            job.job_id for job in self.jobs
            if (not selected or job.job_id in selected)
            and job.status not in {"Running", "Completed"}
        ]
        if not candidates and selected:
            candidates = [job.job_id for job in self.jobs if job.job_id in selected and job.status != "Running"]
        return self.enqueue(candidates) if candidates else []

    def run_queue(
        self,
        job_ids: Optional[Sequence[str]] = None,
        *,
        parallel: Optional[bool] = None,
        max_workers: Optional[int] = None,
    ) -> None:
        if self.running:
            raise RuntimeError("An export run is already active.")
        target = set(str(x) for x in (job_ids or [])) or None
        pending = [
            job for job in self.jobs
            if job.status in {"Queued", "Interrupted", "Failed", "Paused"}
            and (target is None or job.job_id in target)
        ]
        if not pending:
            raise ValueError("No runnable jobs are ready. Add an experiment or select a job, then use Run selected jobs.")
        requested_parallel = self.parallel_enabled if parallel is None else bool(parallel)
        requested_workers = self.parallel_workers if max_workers is None else int(max_workers)
        self.parallel_enabled = bool(requested_parallel and len(pending) > 1)
        self.parallel_workers = max(1, min(32, int(requested_workers or 1)))
        if not self.parallel_enabled:
            self.parallel_workers = 1
        else:
            self.parallel_workers = min(self.parallel_workers, len(pending))
        self.open_transaction_window()
        self.running = True
        self.pause_after_current = False
        self.stop_after_current = False
        self.stop_event.clear()
        self._run_job_ids = {job.job_id for job in pending}
        self._last_run_job_ids = set(self._run_job_ids)
        self.active_job_ids.clear()
        self.refresh_window(select_job=pending[0].job_id if pending else None)
        self.worker = threading.Thread(target=self._worker_loop, name="FaradaIQ-v233-batch-jobs", daemon=True)
        self.worker.start()
        self.poll_events()

    def _register_process(self, job_id: str, proc: Optional[subprocess.Popen]) -> None:
        with self._process_lock:
            if proc is None:
                self._active_processes.pop(job_id, None)
            else:
                self._active_processes[job_id] = proc

    @staticmethod
    def _terminate_process_tree(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, check=False,
                )
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), 15)
                except Exception:
                    proc.terminate()
            try:
                proc.wait(timeout=4)
            except Exception:
                proc.kill()
        except Exception:
            try: proc.kill()
            except Exception: pass

    def stop_all_exports(self) -> None:
        self.stop_event.set()
        self.stop_after_current = True
        now = _now()
        for job in self.jobs:
            if job.status == "Running":
                job.status = "Stopping"
                job.stage = "stopping"
                job.message = "Stop requested. Ending the active process and stopping at the next safe checkpoint."
                job.cancelled_at = now
            elif job.status in {"Queued", "Interrupted", "Paused"}:
                job.status = "Cancelled"
                job.stage = "cancelled"
                job.message = "Cancelled before export started."
                job.cancelled_at = now
                job.eta_seconds = 0.0
        with self._process_lock:
            active = list(self._active_processes.values())
        for proc in active:
            self._terminate_process_tree(proc)
        self.autosave_queue()
        self.refresh_window()
        self.events.put(("refresh", self.current_job_id))

    def _run_one_job(self, job: BatchJob) -> None:
        if self.stop_event.is_set() or job.status not in {"Queued", "Interrupted", "Failed", "Paused"}:
            return
        self.active_job_ids.add(job.job_id)
        self.current_job_id = next(iter(self.active_job_ids), job.job_id)
        job.started_at = _now()
        job.finished_at = ""
        self.events.put(("refresh", job.job_id))
        try:
            run_batch_job(
                job, self.core,
                lambda st, sg, pc, msg: self.set_state(job, st, sg, pc, msg),
                cancel_event=self.stop_event,
                process_callback=lambda proc, jid=job.job_id: self._register_process(jid, proc),
            )
            job.finished_at = _now()
            self.write_job_session(job, include_browser=True)
        except ExportCancelled as exc:
            job.status = "Cancelled"
            job.stage = "cancelled"
            job.error = ""
            job.traceback = ""
            job.cancelled_at = _now()
            job.finished_at = _now()
            job.eta_seconds = 0.0
            self.log(job, str(exc) or "Export cancelled by user.", persist=True)
            self.write_job_session(job, include_browser=bool(job.outputs.get("browser_cache")))
        except Exception as exc:
            job.status = "Failed"
            job.stage = "failed"
            job.error = str(exc)
            job.traceback = traceback.format_exc()
            job.finished_at = _now()
            job.eta_seconds = 0.0
            self.log(job, f"Job failed: {exc}", persist=True)
            self.write_job_session(job, include_browser=bool(job.outputs.get("browser_cache")))
        finally:
            self._register_process(job.job_id, None)
            self.active_job_ids.discard(job.job_id)
            self.current_job_id = next(iter(self.active_job_ids), None)
            self.events.put(("refresh", job.job_id))

    def _worker_loop(self) -> None:
        try:
            pending = [
                job for job in self.jobs
                if (self._run_job_ids is None or job.job_id in self._run_job_ids)
                and job.status in {"Queued", "Interrupted", "Failed", "Paused"}
            ]
            if not self.parallel_enabled or self.parallel_workers <= 1:
                for job in pending:
                    if self.stop_event.is_set() or self.stop_after_current:
                        break
                    self._run_one_job(job)
                    if self.pause_after_current or self.stop_event.is_set() or self.stop_after_current:
                        break
            else:
                from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

                pending_iter = iter(pending)
                active: Dict[Any, BatchJob] = {}
                with ThreadPoolExecutor(max_workers=self.parallel_workers, thread_name_prefix="FaradaIQ-job") as executor:
                    def submit_next() -> bool:
                        if self.stop_event.is_set() or self.stop_after_current or self.pause_after_current:
                            return False
                        try:
                            job = next(pending_iter)
                        except StopIteration:
                            return False
                        future = executor.submit(self._run_one_job, job)
                        active[future] = job
                        return True

                    for _ in range(self.parallel_workers):
                        if not submit_next():
                            break
                    while active:
                        done, _not_done = wait(tuple(active), return_when=FIRST_COMPLETED)
                        for future in done:
                            active.pop(future, None)
                            try:
                                future.result()
                            except Exception as exc:
                                # _run_one_job normally captures failures; this is a final
                                # guard for unexpected executor-level errors.
                                self.events.put(("log", ("", f"Parallel worker error: {exc}")))
                        while len(active) < self.parallel_workers and submit_next():
                            pass
                        if self.stop_event.is_set() or self.stop_after_current or self.pause_after_current:
                            # Do not start additional jobs. Already-active jobs finish or
                            # honor the shared cancellation event.
                            continue
        finally:
            self.running = False
            self.current_job_id = None
            self.active_job_ids.clear()
            self._run_job_ids = None
            self.events.put(("done", None))
            self.autosave_queue()

    def poll_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue_module.Empty:
                break
            if kind == "log":
                job_id, line = payload
                try:
                    if self.window is not None and self.window.winfo_exists():
                        self.window.append_log(job_id, line)
                except Exception:
                    pass
                try:
                    if self.transaction_window is not None and self.transaction_window.winfo_exists():
                        self.transaction_window.append_log(job_id, line)
                except Exception:
                    pass
            elif kind in {"refresh", "done"}:
                self.refresh_window(select_job=payload if isinstance(payload, str) else None)
        if self.running or not self.events.empty():
            try:
                self.gui.after(120, self.poll_events)
            except Exception:
                pass

    def open_transaction_window(self) -> Any:
        if self.transaction_window is not None:
            try:
                if self.transaction_window.winfo_exists():
                    self.transaction_window.deiconify()
                    self.transaction_window.lift()
                    self.transaction_window.refresh()
                    return self.transaction_window
            except Exception:
                pass
        self.transaction_window = BatchTransactionWindow(self.gui, self, self.core)
        self.transaction_window.refresh()
        return self.transaction_window

    def open_window(self, select_job: Optional[str] = None) -> Any:
        if self.window is not None:
            try:
                if self.window.winfo_exists():
                    self.window.deiconify(); self.window.lift(); self.window.focus_force()
                    self.window.refresh(select_job=select_job)
                    return self.window
            except Exception:
                pass
        self.window = BatchQueueWindow(self.gui, self, self.core)
        self.window.refresh(select_job=select_job)
        return self.window

    def refresh_window(self, select_job: Optional[str] = None) -> None:
        try:
            if self.window is not None and self.window.winfo_exists():
                self.window.refresh(select_job=select_job)
        except Exception:
            pass
        try:
            if self.transaction_window is not None and self.transaction_window.winfo_exists():
                self.transaction_window.refresh()
        except Exception:
            pass
        try:
            label = getattr(self.gui, "_v233_queue_summary_var", None) or getattr(self.gui, "_v232_queue_summary_var", None) or getattr(self.gui, "_v229_queue_summary_var", None)
            active_ids = self._run_job_ids or self._last_run_job_ids or set()
            tracked = [j for j in self.jobs if j.job_id in active_ids]
            running_jobs = [j for j in self.jobs if j.status in {"Running", "Stopping"}]
            if label is not None:
                nready = sum(j.status not in {"Running", "Stopping", "Completed"} for j in self.jobs)
                overall = sum(j.progress for j in tracked) / len(tracked) if tracked else 0.0
                mode = f"parallel x{self.parallel_workers}" if self.parallel_enabled and self.parallel_workers > 1 else "sequential"
                label.set(f"Jobs: {len(self.jobs)} total | ready {nready} | running {len(running_jobs)} | {mode}" + (f" | {overall:.1f}%" if tracked else ""))
            status_var = getattr(self.gui, "_v233_main_job_status_var", None)
            if status_var is not None:
                if running_jobs:
                    lines = []
                    for job in running_jobs:
                        count = f" {job.progress_current}/{job.progress_total}" if job.progress_total else ""
                        lines.append(f"{job.experiment_name}: {job.progress:.1f}% · {job.stage.replace('_', ' ')}{count}")
                    status_var.set("\n".join(lines))
                elif tracked and not self.running:
                    lines = [f"{job.experiment_name}: {job.status} · {job.progress:.1f}%" for job in tracked[-4:]]
                    status_var.set("\n".join(lines))
                else:
                    status_var.set("No jobs are running.")
        except Exception:
            pass


def _find_script_root(core: Mapping[str, Any], source: Path) -> Path:
    fn = core.get("_v19_find_pyseccm_script_root")
    if callable(fn):
        found = fn([source.parent, Path.cwd(), Path(__file__).resolve().parents[1]])
        if found:
            return Path(found)
    return Path(__file__).resolve().parents[1] / "pyseccm_tools"


def _run_command(
    args: Sequence[Any],
    cwd: Path,
    log: Callable[[str], None],
    *,
    cancel_event: Optional[threading.Event] = None,
    process_callback: Optional[Callable[[Optional[subprocess.Popen]], None]] = None,
) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ExportCancelled("Export cancelled before the external process started.")
    rendered = " ".join(f'"{a}"' if " " in str(a) else str(a) for a in args)
    log(f"Running: {rendered}")
    popen_kwargs: Dict[str, Any] = {
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen([str(x) for x in args], **popen_kwargs)
    if process_callback is not None:
        process_callback(proc)
    captured: List[str] = []
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                if cancel_event is not None and cancel_event.is_set():
                    try:
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                        else:
                            os.killpg(os.getpgid(proc.pid), 15)
                    except Exception:
                        try: proc.terminate()
                        except Exception: pass
                    break
                line = line.rstrip()
                if line:
                    captured.append(line)
                    # Stream every parser/DRT transaction to the live details window.
                    # The on-disk job log remains the authoritative complete record.
                    log(line[:4000])
        rc = proc.wait()
        if cancel_event is not None and cancel_event.is_set():
            raise ExportCancelled("Export cancelled while an external parser or DRT process was running.")
        if rc != 0:
            tail = "\n".join(captured[-30:])
            raise RuntimeError(f"Command failed with exit code {rc}:\n{rendered}\n\n{tail}")
    finally:
        if process_callback is not None:
            process_callback(None)


def _check_cancel(cancel_event: Optional[threading.Event], message: str = "Export cancelled by user.") -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ExportCancelled(message)


def _count_source_items(source: Path) -> int:
    if source.is_file():
        return 1
    try:
        return sum(1 for path in source.rglob("*") if path.is_file())
    except Exception:
        return 1


def _analysis_data_dirs(analysis_root: Path) -> List[Path]:
    out: List[Path] = []
    for p in analysis_root.rglob("data"):
        if p.is_dir() and p.parent.name.endswith("_for_plotting_analysis"):
            out.append(p)
    if not out:
        for p in analysis_root.rglob("data_generated_from_HEKA"):
            if p.is_dir():
                out.append(p)
    seen: set[str] = set(); unique: List[Path] = []
    for p in out:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key); unique.append(p)
    return unique


def _find_drt_script(script_root: Path) -> Optional[Path]:
    for p in (
        script_root / "headless_pyDRTtools" / "pyDRTtools" / "headless_pyDRTtools.py",
        script_root / "pyDRTtools" / "headless_pyDRTtools.py",
        script_root / "headless_pyDRTtools.py",
    ):
        if p.exists():
            return p
    return None


def _drt_input_dirs(data_dir: Path, core: Mapping[str, Any]) -> List[Path]:
    out: List[Path] = []
    detector = core.get("_v19_detect_pyseccm_files")
    if callable(detector):
        try:
            detected = detector(data_dir)
            out.extend(Path(x) for x in detected.get("drt_dir", []) or [])
        except Exception:
            pass
    candidates = [
        data_dir.parent / "DRT_input_csv",
        data_dir / "Figures" / "Nyquist",
        data_dir.parent / "Figures" / "Nyquist",
    ]
    out.extend(p for p in candidates if p.exists() and p.is_dir())
    seen: set[str] = set(); unique: List[Path] = []
    for p in out:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key); unique.append(p)
    return unique


def _validated_drt_input_dir(path: Path) -> bool:
    """Require real EIS tables before the batch DRT stage can run.

    Batch export creates the traditional ``DRT_input_csv`` directory even for
    experiments that contain no EIS.  Directory existence therefore cannot be
    used as an eligibility signal.  Reuse the release-wide strict validator so
    an enabled DRT option is only actionable when at least one table contains
    positive frequency plus numeric Z-real and Z-imaginary columns.
    """
    try:
        from faradaiq.v290_release_integrity import valid_drt_input_dir
        return bool(valid_drt_input_dir(path))
    except Exception:
        return False


def _drt_command(script: Path, input_dir: Path, settings: Mapping[str, Any], job: BatchJob) -> List[str]:
    args: List[str] = [sys.executable, str(script), str(input_dir)]
    for flag, key in (("--run_types", "run_types"), ("--plot_types", "plot_types"), ("--export_types", "export_types")):
        values = [str(x) for x in settings.get(key, []) if str(x).strip()]
        if values:
            args.extend([flag, *values])
    args.extend([
        "--rbf_type", str(settings.get("rbf_type", "gaussian")),
        "--data", str(settings.get("data", "combined")),
        "--derivative", str(settings.get("derivative", "1")),
        "--inductance", str(settings.get("inductance", "without")),
        "--regularization", str(settings.get("regularization", "1.0E-3")),
        "--shape_control", str(settings.get("shape_control", "fwhm")),
        "--coefficient", str(settings.get("coefficient", "0.5")),
        "--sample_number", str(settings.get("sample_number", "2000")),
    ])
    run_id = str(settings.get("run_id") or "").strip() or f"{job.base_name}_{job.job_id[-8:]}"
    args.extend(["--run_id", run_id])
    return args


def _build_collection_export(
    job: BatchJob,
    core: Mapping[str, Any],
    log: Callable[[str], None],
    *,
    progress_callback: Optional[Callable[[str, float, int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    import numpy as np

    collection_cls = core.get("PSSessionCollection")
    if collection_cls is None:
        raise RuntimeError("PSSessionCollection backend is unavailable.")
    collection = collection_cls()
    source_items = max(1, _count_source_items(job.source))
    export_steps = ["load", "summary"]
    if job.export_settings.get("archive_npy_and_summary_json", True):
        export_steps.append("archive")
    need_analysis = bool(
        job.export_settings.get("analysis_ready_csv", True)
        or job.export_settings.get("run_technique_parsers", True)
        or job.drt_settings.get("enabled", True)
        or job.export_settings.get("canonical_h5_json", True)
    )
    if need_analysis:
        export_steps.append("analysis_csv")
    if job.export_settings.get("raw_vendor_json", False):
        export_steps.append("raw_json")
    total_steps = max(1, len(export_steps))
    completed = 0

    def update(label: str) -> None:
        nonlocal completed
        completed += 1
        if progress_callback is not None:
            progress_callback(label, completed / total_steps, completed, total_steps)

    _check_cancel(cancel_event)
    log(f"Inspecting source: {job.source_path} ({source_items} source file(s)).")
    if progress_callback is not None:
        progress_callback(f"Loading {source_items} source file(s).", 0.05, 0, total_steps)
    collection.load(job.source_path)
    _check_cancel(cancel_event)
    update("Source loading completed.")

    summary = collection.summarize()
    job.inspection_summary = _json_safe(summary)
    update("Inspection summary completed.")

    out = job.output
    if job.export_settings.get("create_experiment_subfolder"):
        out = out / job.base_name
    out.mkdir(parents=True, exist_ok=True)
    base = job.base_name
    description = f"Experiment name: {job.experiment_name}\nBatch job: {job.job_id}"
    outputs: Dict[str, Any] = {"output_dir": str(out), "source_file_count": source_items}

    if job.export_settings.get("archive_npy_and_summary_json", True):
        _check_cancel(cancel_event)
        log("Building parsed NPY archive and summary JSON.")
        npy_path = out / f"{base}_parsed.npy"
        json_path = out / f"{base}_summary.json"
        npy_bundle = collection.build_parsed_npy_bundle(description)
        np.save(npy_path, np.array(npy_bundle, dtype=object), allow_pickle=True)
        _atomic_json(json_path, collection.build_summary_json(description))
        outputs.update({"parsed_npy": str(npy_path), "summary_json": str(json_path)})
        update("Parsed NPY and summary JSON saved.")

    if need_analysis:
        _check_cancel(cancel_event)
        log("Exporting analysis-ready CSV tables.")
        info = collection.export_analysis_ready_csv(out, base)
        outputs["analysis_ready_root"] = str(info.get("analysis_ready_root"))
        outputs["analysis_ready_manifest"] = str(info.get("manifest_path"))
        outputs["analysis_ready_csv_count"] = int(info.get("n_csv_files", 0))
        update(f"Analysis-ready CSV export completed ({outputs['analysis_ready_csv_count']} file(s)).")

    if job.export_settings.get("raw_vendor_json", False):
        _check_cancel(cancel_event)
        log("Saving raw vendor-session JSON.")
        raw_path = out / f"{base}_raw_session.json"
        if collection.mode == "single":
            raw_payload = collection.entries[0]["parser"].session
        else:
            raw_payload = {entry["entry_name"]: entry["parser"].session for entry in collection.entries}
        _atomic_json(raw_path, raw_payload)
        outputs["raw_vendor_json"] = str(raw_path)
        update("Raw vendor JSON saved.")

    return outputs


def _existing_path(value: Any, *, directory: bool = False) -> bool:
    if not value:
        return False
    try:
        p = Path(str(value))
        return p.is_dir() if directory else p.exists()
    except Exception:
        return False


def _core_export_reusable(job: BatchJob) -> bool:
    """Return True only when all currently requested core-export outputs still exist."""
    out = job.outputs or {}
    settings = job.export_settings or {}
    if settings.get("archive_npy_and_summary_json", True):
        if not (_existing_path(out.get("parsed_npy")) and _existing_path(out.get("summary_json"))):
            return False
    if settings.get("analysis_ready_csv", True) or settings.get("run_technique_parsers", True) or job.drt_settings.get("enabled", True) or settings.get("canonical_h5_json", True):
        if not _existing_path(out.get("analysis_ready_root"), directory=True):
            return False
    if settings.get("raw_vendor_json", False) and not _existing_path(out.get("raw_vendor_json")):
        return False
    return bool(out.get("output_dir"))


def _canonical_reusable(job: BatchJob) -> bool:
    info = (job.outputs or {}).get("canonical_bundle") or {}
    return isinstance(info, Mapping) and _existing_path(info.get("h5_path")) and _existing_path(info.get("json_path"))


def _browser_cache_reusable(job: BatchJob) -> bool:
    info = (job.outputs or {}).get("browser_cache") or {}
    return isinstance(info, Mapping) and _existing_path(info.get("cache_root"), directory=True)


def run_batch_job(
    job: BatchJob,
    core: Mapping[str, Any],
    progress: Callable[[str, str, float, str], None],
    *,
    cancel_event: Optional[threading.Event] = None,
    process_callback: Optional[Callable[[Optional[subprocess.Popen]], None]] = None,
) -> BatchJob:
    errors = job.validate()
    if errors:
        raise ValueError("\n".join(errors))

    enabled = ["inspection", "export"]
    if job.export_settings.get("run_technique_parsers", True):
        enabled.append("technique_parsers")
    if job.drt_settings.get("enabled", True):
        enabled.append("drt")
    if job.export_settings.get("canonical_h5_json", True):
        enabled.append("canonical_bundle")
        if job.export_settings.get("build_browser_cache", True):
            enabled.append("browser_cache")
    tracker = WeightedProgressTracker(job, progress, enabled)
    job.started_at = job.started_at or _now()
    job.finished_at = ""
    _check_cancel(cancel_event)

    resume = bool(job.export_settings.get("resume_completed_stages", True))
    if resume and "export" in job.completed_stages and _core_export_reusable(job):
        tracker.update("inspection", 1.0, "Reused the validated inspection checkpoint.", current=1, total=1)
        tracker.update("export", 1.0, "Reused the validated core-export checkpoint.", current=1, total=1)
    else:
        if "export" in job.completed_stages:
            job.completed_stages.remove("export")

        def core_progress(label: str, _fraction: float, current: int, total: int) -> None:
            if current <= 2:
                tracker.update("inspection", min(1.0, current / 2.0), label, current=current, total=2)
            else:
                export_total = max(1, total - 2)
                tracker.update("export", min(1.0, (current - 2) / export_total), label, current=current - 2, total=export_total)

        outputs = _build_collection_export(
            job, core,
            lambda message: progress("Running", job.stage or "export", tracker.last_percent, message),
            progress_callback=core_progress,
            cancel_event=cancel_event,
        )
        job.outputs.update(outputs)
        if "inspection" not in job.completed_stages:
            job.completed_stages.append("inspection")
        if "export" not in job.completed_stages:
            job.completed_stages.append("export")
        tracker.update("inspection", 1.0, "Loading and inspection completed.", current=2, total=2)
        tracker.update("export", 1.0, f"Core export saved under {job.outputs.get('output_dir', job.output_dir)}.", current=1, total=1)

    _check_cancel(cancel_event)
    analysis_root = Path(str(job.outputs.get("analysis_ready_root") or ""))
    data_dirs = _analysis_data_dirs(analysis_root) if analysis_root.exists() else []
    script_root = _find_script_root(core, Path(job.source_path))

    if job.export_settings.get("run_technique_parsers", True):
        if resume and "technique_parsers" in job.completed_stages:
            tracker.update("technique_parsers", 1.0, "Reused the completed technique-parser checkpoint.", current=1, total=1)
        else:
            builder = core.get("_v27_build_auto_pyseccm_commands_for_data_dir")
            prepared: List[Tuple[List[str], Path]] = []
            notes_all: List[str] = []
            for data_dir in data_dirs:
                _check_cancel(cancel_event)
                if callable(builder):
                    commands, notes, _detected = builder(
                        data_dir, script_root=script_root, python_exe=sys.executable, include_drt=False,
                    )
                    notes_all.extend(str(note) for note in (notes or []))
                    for row in commands or []:
                        args = list(row[0]); cwd = Path(row[1])
                        if any(str(x).endswith("parse_EIS.py") for x in args) and "--n_grid" not in args:
                            args.extend(["--n_grid", str(job.export_settings.get("eis_n_grid", 100))])
                        prepared.append((args, cwd))
            for note in notes_all:
                progress("Running", "technique_parsers", job.progress, note)
            total_commands = len(prepared)
            if not total_commands:
                tracker.update("technique_parsers", 1.0, "No compatible technique-parser commands were required.", current=0, total=0)
            else:
                for idx, (args, cwd) in enumerate(prepared, 1):
                    _check_cancel(cancel_event)
                    tracker.update("technique_parsers", (idx - 1) / total_commands, f"Starting parser {idx}/{total_commands}: {Path(str(args[1] if len(args) > 1 else args[0])).name}", current=idx - 1, total=total_commands)
                    def parser_transaction(message: str, i: int = idx) -> None:
                        sub = _progress_fraction_from_line(message)
                        if sub is None:
                            sub = 0.12
                        fraction = min(0.995, ((i - 1) + sub) / total_commands)
                        tracker.update("technique_parsers", fraction, message, current=i - 1, total=total_commands)
                    _run_command(
                        args, cwd, parser_transaction,
                        cancel_event=cancel_event, process_callback=process_callback,
                    )
                    tracker.update("technique_parsers", idx / total_commands, f"Parser {idx}/{total_commands} completed.", current=idx, total=total_commands)
                if "technique_parsers" not in job.completed_stages:
                    job.completed_stages.append("technique_parsers")

    _check_cancel(cancel_event)
    if job.drt_settings.get("enabled", True):
        # v308: parse_EIS separates repeated frequency sweeps into *_Groups.
        # Rebuild DRT_input_csv from those group boundaries so many EIS acquisitions
        # collected at one physical point are analyzed independently instead of being
        # concatenated into one or two oversized spectra.
        if not (resume and "drt" in job.completed_stages):
            try:
                from .v309_drt_spectrum_inputs import rebuild_drt_inputs_from_parsed_groups
                _drt_input_repair = rebuild_drt_inputs_from_parsed_groups(data_dirs, clear_stale_outputs=True)
                if int(_drt_input_repair.get("spectra_written", 0) or 0) > 0:
                    progress(
                        "Running", "drt", job.progress,
                        f"Prepared {_drt_input_repair['spectra_written']} individual EIS spectrum input(s) for DRT "
                        f"across {_drt_input_repair['drt_dirs_rebuilt']} folder(s); repeated EIS at one point remain separate acquisitions."
                    )
                    job.outputs["drt_spectrum_input_repair"] = _drt_input_repair
            except Exception as exc:
                progress("Running", "drt", job.progress, f"DRT spectrum-boundary preparation warning: {exc}")
        if resume and "drt" in job.completed_stages:
            tracker.update("drt", 1.0, "Reused the completed DRT checkpoint.", current=1, total=1)
        else:
            drt_dirs: List[Path] = []
            for data_dir in data_dirs:
                drt_dirs.extend(_drt_input_dirs(data_dir, core))
            if analysis_root.exists():
                drt_dirs.extend(path for path in analysis_root.rglob("DRT_input_csv") if path.is_dir())
            seen: set[str] = set(); unique_dirs: List[Path] = []
            for path in drt_dirs:
                key = str(path.resolve()).lower()
                if key not in seen:
                    seen.add(key); unique_dirs.append(path)
            validated_dirs = [path for path in unique_dirs if _validated_drt_input_dir(path)]
            rejected_count = len(unique_dirs) - len(validated_dirs)
            if not validated_dirs:
                detail = (
                    "DRT skipped: no validated EIS input was found. Empty or stale DRT_input_csv "
                    "folders do not qualify, and CA/CP/CV/OCP or other non-EIS data are never sent "
                    "to pyDRTtools."
                )
                tracker.update("drt", 1.0, detail, current=0, total=0)
                if "drt" not in job.completed_stages:
                    job.completed_stages.append("drt")
            else:
                if rejected_count:
                    tracker.update(
                        "drt", 0.0,
                        f"Ignored {rejected_count} empty or invalid DRT input folder(s); running only validated EIS inputs.",
                        current=0, total=len(validated_dirs),
                    )
                unique_dirs = validated_dirs
                drt_script = _find_drt_script(script_root)
                if drt_script is None:
                    raise FileNotFoundError(f"DRT input exists, but headless_pyDRTtools.py was not found under {script_root}")
                total_drt = len(unique_dirs)
                for idx, input_dir in enumerate(unique_dirs, 1):
                    _check_cancel(cancel_event)
                    tracker.update("drt", (idx - 1) / total_drt, f"Starting DRT {idx}/{total_drt}: {input_dir.name}", current=idx - 1, total=total_drt)
                    def drt_transaction(message: str, i: int = idx) -> None:
                        sub = _progress_fraction_from_line(message)
                        if sub is None:
                            sub = 0.12
                        fraction = min(0.995, ((i - 1) + sub) / total_drt)
                        tracker.update("drt", fraction, message, current=i - 1, total=total_drt)
                    _run_command(
                        _drt_command(drt_script, input_dir, job.drt_settings, job), drt_script.parent, drt_transaction,
                        cancel_event=cancel_event, process_callback=process_callback,
                    )
                    try:
                        from .v309_drt_spectrum_inputs import validate_drt_outputs
                        _drt_validation = validate_drt_outputs(input_dir)
                        if not _drt_validation.get("complete"):
                            _missing = _drt_validation.get("missing") or []
                            raise RuntimeError(
                                f"DRT output validation failed: {_drt_validation.get('completed', 0)}/"
                                f"{_drt_validation.get('expected', 0)} EIS spectrum result(s) completed. "
                                f"Missing: {', '.join(_missing[:8])}"
                            )
                        progress(
                            "Running", "drt", job.progress,
                            f"DRT output validation passed: {_drt_validation['completed']}/{_drt_validation['expected']} individual EIS spectrum result(s) completed."
                        )
                    except RuntimeError:
                        raise
                    except Exception as exc:
                        raise RuntimeError(f"DRT output validation could not be completed: {exc}") from exc
                    tracker.update("drt", idx / total_drt, f"DRT {idx}/{total_drt} completed.", current=idx, total=total_drt)
                if "drt" not in job.completed_stages:
                    job.completed_stages.append("drt")

    _check_cancel(cancel_event)
    if job.export_settings.get("canonical_h5_json", True) and analysis_root.exists():
        import faradaiq_canonical_bundle as bundle
        if resume and "canonical_bundle" in job.completed_stages and _canonical_reusable(job):
            bundle_info = dict(job.outputs.get("canonical_bundle") or {})
            tracker.update("canonical_bundle", 1.0, "Reused the validated canonical H5/JSON checkpoint.", current=1, total=1)
        else:
            if "canonical_bundle" in job.completed_stages:
                job.completed_stages.remove("canonical_bundle")
            estimated_files = max(1, sum(1 for path in analysis_root.rglob("*") if path.is_file()))
            callback_count = {"n": 0}
            def bundle_progress(message: str) -> None:
                _check_cancel(cancel_event)
                match = re.search(r"(\d+)\s*/\s*(\d+)", str(message))
                if match:
                    current, total = int(match.group(1)), max(1, int(match.group(2)))
                    fraction = current / total
                elif "complete" in str(message).lower():
                    current, total, fraction = estimated_files, estimated_files, 1.0
                else:
                    callback_count["n"] += 1
                    current, total = min(callback_count["n"], estimated_files), estimated_files
                    fraction = min(0.95, 0.05 + 0.9 * current / total)
                tracker.update("canonical_bundle", fraction, str(message), current=current, total=total)
            tracker.update("canonical_bundle", 0.0, f"Building canonical H5/JSON from approximately {estimated_files} files.", current=0, total=estimated_files)
            bundle_info = bundle.build_canonical_bundle(
                analysis_root, base_name=job.base_name,
                gui_metadata={
                    "experiment_name": job.experiment_name, "batch_job_id": job.job_id,
                    "source_path": job.source_path, "export_settings": job.export_settings,
                    "drt_settings": job.drt_settings, "inspection_summary": job.inspection_summary,
                },
                progress_callback=bundle_progress, label="v233_batch_job",
            )
            job.outputs["canonical_bundle"] = _json_safe(bundle_info)
            if "canonical_bundle" not in job.completed_stages:
                job.completed_stages.append("canonical_bundle")
            tracker.update("canonical_bundle", 1.0, "Canonical H5 and JSON bundle completed.", current=estimated_files, total=estimated_files)

        _check_cancel(cancel_event)
        if job.export_settings.get("build_browser_cache", True) and bundle_info.get("h5_path"):
            if resume and "browser_cache" in job.completed_stages and _browser_cache_reusable(job):
                tracker.update("browser_cache", 1.0, "Reused the validated browser-cache checkpoint.", current=1, total=1)
            else:
                if "browser_cache" in job.completed_stages:
                    job.completed_stages.remove("browser_cache")
                n_tables = max(1, int(bundle_info.get("n_files_indexed", 1) or 1))
                tracker.update("browser_cache", 0.05, f"Extracting up to {n_tables} browser records from the canonical H5.", current=0, total=n_tables)
                cache = bundle.extract_h5_tables_for_browser(bundle_info["h5_path"], prefer_reuse=True)
                _check_cancel(cancel_event)
                job.outputs["browser_cache"] = _json_safe(cache)
                actual = int(cache.get("n_tables", n_tables) or n_tables)
                tracker.update("browser_cache", 1.0, f"Browser cache ready with {actual} table(s).", current=actual, total=actual)
                if "browser_cache" not in job.completed_stages:
                    job.completed_stages.append("browser_cache")

    _check_cancel(cancel_event)
    job.status = "Completed"
    job.stage = "completed"
    job.progress = 100.0
    job.message = "Job completed. The unique FaradaIQ session JSON can restore this experiment and browser cache."
    job.error = ""
    job.traceback = ""
    job.finished_at = _now()
    job.touch()
    tracker.complete(job.message)
    return job


class BatchTransactionWindow:
    """Live transaction monitor with one tab per requested/running job."""

    def __init__(self, parent: Any, model: BatchQueueModel, core: MutableMapping[str, Any]) -> None:
        self.parent = parent
        self.model = model
        self.core = core
        self.tk = core["tk"]
        self.ttk = core["ttk"]
        self.win = self.tk.Toplevel(parent)
        self.win.title(f"FaradaIQ Studio {VERSION} — Live job transactions")
        width, height = _place_in_work_area(self.win, "transactions")
        left, top, work_w, _work_h = _usable_work_area(self.win)
        try:
            x = left + max(4, work_w - width - 18)
            y = top + 18
            self.win.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            pass
        self.win.protocol("WM_DELETE_WINDOW", self.win.withdraw)
        self._tabs: Dict[str, Dict[str, Any]] = {}
        outer = self.ttk.Frame(self.win, padding=10)
        outer.pack(fill="both", expand=True)
        title = self.ttk.Frame(outer)
        title.pack(fill="x")
        self.ttk.Label(title, text="Live job transactions", style="Title.TLabel").pack(side="left")
        self.queue_var = self.tk.StringVar(value="Jobs idle")
        self.ttk.Label(title, textvariable=self.queue_var, style="Muted.TLabel").pack(side="left", padx=12)
        self.overall_detail_var = self.tk.StringVar(value="Ready")
        self.ttk.Label(outer, textvariable=self.overall_detail_var, style="Muted.TLabel").pack(fill="x", pady=(6, 3))
        overall = self.ttk.Frame(outer)
        overall.pack(fill="x", pady=(0, 7))
        self.ttk.Label(overall, text="All requested jobs").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.overall_progress_var = self.tk.DoubleVar(value=0.0)
        self.ttk.Progressbar(overall, variable=self.overall_progress_var, maximum=100).grid(row=0, column=1, sticky="ew")
        self.overall_percent_var = self.tk.StringVar(value="0.0%")
        self.ttk.Label(overall, textvariable=self.overall_percent_var, width=8).grid(row=0, column=2, padx=(6, 0))
        overall.columnconfigure(1, weight=1)
        self.notebook = self.ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        controls = self.ttk.Frame(outer)
        controls.pack(fill="x", pady=(8, 0))
        self.ttk.Button(controls, text="Stop jobs", command=self.model.stop_all_exports).pack(side="left")
        self.ttk.Button(controls, text="Open jobs", command=self.model.open_window).pack(side="left", padx=5)
        self.ttk.Button(controls, text="Clear selected details", command=self._clear_selected).pack(side="left", padx=5)
        self.ttk.Button(controls, text="Hide", command=self.win.withdraw).pack(side="right")
        try:
            apply_theme = core.get("_v36_apply_theme")
            if callable(apply_theme):
                apply_theme(self.win, str(getattr(parent, "theme_mode_var", None).get() if hasattr(parent, "theme_mode_var") else "High Contrast"))
        except Exception:
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self.win, name)

    def _ensure_tab(self, job: BatchJob) -> Dict[str, Any]:
        existing = self._tabs.get(job.job_id)
        if existing is not None:
            return existing
        frame = self.ttk.Frame(self.notebook, padding=8)
        status_var = self.tk.StringVar(value=f"{job.experiment_name} — {job.status}")
        detail_var = self.tk.StringVar(value=job.message or "Ready")
        self.ttk.Label(frame, textvariable=status_var, style="Card.TLabel").pack(fill="x", pady=(0, 2))
        self.ttk.Label(frame, textvariable=detail_var, style="Muted.TLabel").pack(fill="x", pady=(0, 5))
        bar_row = self.ttk.Frame(frame)
        bar_row.pack(fill="x", pady=(0, 6))
        progress_var = self.tk.DoubleVar(value=float(job.progress or 0.0))
        self.ttk.Progressbar(bar_row, variable=progress_var, maximum=100).pack(side="left", fill="x", expand=True)
        percent_var = self.tk.StringVar(value=f"{job.progress:.1f}%")
        self.ttk.Label(bar_row, textvariable=percent_var, width=8).pack(side="left", padx=(6, 0))
        from tkinter.scrolledtext import ScrolledText
        log_text = ScrolledText(frame, wrap="word", height=18)
        log_text.pack(fill="both", expand=True)
        try:
            if job.log_path and Path(job.log_path).exists():
                log_text.insert("1.0", Path(job.log_path).read_text(encoding="utf-8", errors="replace")[-80000:])
                log_text.see("end")
        except Exception:
            pass
        self.notebook.add(frame, text=job.experiment_name[:28])
        data = {
            "frame": frame,
            "status_var": status_var,
            "detail_var": detail_var,
            "progress_var": progress_var,
            "percent_var": percent_var,
            "log_text": log_text,
        }
        self._tabs[job.job_id] = data
        # Compatibility aliases used by earlier GUI smoke tests and scripts.
        self.job_progress_var = progress_var
        self.job_percent_var = percent_var
        self.log_text = log_text
        return data

    def _clear_selected(self) -> None:
        try:
            selected = self.notebook.select()
            for tab in self._tabs.values():
                if str(tab["frame"]) == str(selected):
                    tab["log_text"].delete("1.0", "end")
                    break
        except Exception:
            pass

    def append_log(self, job_id: str, line: str) -> None:
        job = self.model.find(job_id)
        if job is None:
            return
        tab = self._ensure_tab(job)
        try:
            text = tab["log_text"]
            text.insert("end", str(line) + "\n")
            text.see("end")
            if int(float(text.index("end-1c").split(".")[0])) > 6000:
                text.delete("1.0", "1000.0")
        except Exception:
            pass

    def refresh(self) -> None:
        active_ids = self.model._run_job_ids or self.model._last_run_job_ids
        tracked = [job for job in self.model.jobs if job.job_id in active_ids]
        for job in tracked:
            tab = self._ensure_tab(job)
            count = f"{job.progress_current}/{job.progress_total}" if job.progress_total else ""
            timing = f"elapsed {_format_duration(job.elapsed_seconds)}"
            if job.eta_seconds is not None:
                timing += f" | ETA {_format_duration(job.eta_seconds)}"
            tab["status_var"].set(f"{job.experiment_name} — {job.status} — {job.stage.replace('_', ' ')}")
            tab["detail_var"].set(" | ".join(x for x in (job.message, count, timing) if x))
            tab["progress_var"].set(job.progress)
            tab["percent_var"].set(f"{job.progress:.1f}%")
            try:
                self.notebook.tab(tab["frame"], text=f"{job.experiment_name[:20]} {job.progress:.0f}%")
            except Exception:
                pass
        overall = sum(job.progress for job in tracked) / len(tracked) if tracked else 0.0
        completed = sum(job.status == "Completed" for job in tracked)
        running = sum(job.status in {"Running", "Stopping"} for job in tracked)
        self.overall_progress_var.set(overall)
        self.overall_percent_var.set(f"{overall:.1f}%")
        mode = f"parallel, {self.model.parallel_workers} workers" if self.model.parallel_enabled and self.model.parallel_workers > 1 else "sequential"
        self.queue_var.set(f"{completed}/{len(tracked)} complete · {running} running · {mode}" if tracked else "Jobs idle")
        self.overall_detail_var.set(
            "Each running job has its own transaction tab. Stop jobs ends all active and pending exports."
            if tracked else "Ready"
        )


class BatchQueueWindow:
    """Task-oriented export-jobs editor. Tk widgets are created lazily for testability."""

    def __init__(self, parent: Any, model: BatchQueueModel, core: MutableMapping[str, Any]) -> None:
        self.parent = parent
        self.model = model
        self.core = core
        self.tk = core["tk"]; self.ttk = core["ttk"]
        self.messagebox = core.get("messagebox"); self.filedialog = core.get("filedialog")
        self.win = self.tk.Toplevel(parent)
        self.win.title(f"FaradaIQ Studio {VERSION} — Experiment export jobs")
        width, height = _place_in_work_area(self.win, "batch")
        self._initial_width = width
        self._initial_height = height
        self._editing_job_id: Optional[str] = None
        self.win.protocol("WM_DELETE_WINDOW", self._hide_window)
        self._build()
        self._enable_dnd()
        try:
            self.win.after_idle(self._apply_initial_layout)
            self.win.after(300, self._apply_initial_layout)
        except Exception:
            pass
        try:
            apply_theme = core.get("_v36_apply_theme")
            if callable(apply_theme):
                apply_theme(self.win, str(getattr(parent, "theme_mode_var", None).get() if hasattr(parent, "theme_mode_var") else "High Contrast"))
        except Exception:
            pass

    def _apply_initial_layout(self) -> None:
        """Keep every fixed action visible and give settings a usable width."""
        try:
            width, _height = _place_in_work_area(self.win, "batch")
            self.win.update_idletasks()
            if hasattr(self, "pane"):
                self.pane.sashpos(0, max(520, int(width * 0.56)))
            if hasattr(self, "right_canvas"):
                self.right_canvas.yview_moveto(0.0)
            self.win.lift()
        except Exception:
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self.win, name)

    def _build(self) -> None:
        ttk = self.ttk; tk = self.tk
        outer = ttk.Frame(self.win, padding=10)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Experiment export jobs", style="Title.TLabel").pack(side="left")
        self.queue_label = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.queue_label, style="Muted.TLabel").pack(side="left", padx=12)
        ttk.Button(header, text="Save jobs", command=self.save_queue_dialog).pack(side="right", padx=(4, 0))
        ttk.Button(header, text="Load jobs", command=self.load_queue_dialog).pack(side="right", padx=(4, 0))
        ttk.Button(header, text="Live transactions", command=self.model.open_transaction_window).pack(side="right", padx=(4, 0))

        drop = ttk.LabelFrame(outer, text="Add experiment jobs", padding=8)
        drop.pack(fill="x", pady=(8, 8))
        self.drop_label = ttk.Label(
            drop,
            text="Drag and drop files or folders here, or use Add files / Add folder. Every item becomes a job with independent output and DRT settings.",
            anchor="center",
            padding=10,
        )
        self.drop_label.pack(side="left", fill="x", expand=True)
        ttk.Button(drop, text="Add files", command=self.add_files).pack(side="left", padx=3)
        ttk.Button(drop, text="Add folder", command=self.add_folder).pack(side="left", padx=3)

        queue_controls = ttk.LabelFrame(outer, text="Job controls", padding=(7, 5))
        queue_controls.pack(fill="x", pady=(0, 8))
        self.selected_actions = queue_controls  # compatibility alias; actions now live here
        self.parallel_enabled_var = tk.BooleanVar(value=bool(self.model.parallel_enabled))
        self.parallel_workers_var = tk.IntVar(value=max(1, int(self.model.parallel_workers or 1)))
        primary_actions = [
            ("Run all jobs", self.run_batch_export, "Accent.TButton"),
            ("Run selected jobs", self.run_selected_export, None),
            ("Stop jobs", self.stop_all_exports, None),
            ("Pause after active", self.pause_after_current, None),
            ("Live transactions", self.model.open_transaction_window, None),
            ("Apply & save settings", self.apply_settings, None),
            ("Duplicate job", self.duplicate_selected, None),
        ]
        for col, (label, command, style) in enumerate(primary_actions):
            kwargs = {"text": label, "command": command}
            if style:
                kwargs["style"] = style
            ttk.Button(queue_controls, **kwargs).grid(row=0, column=col, sticky="ew", padx=3, pady=2)
        secondary_actions = [
            ("Remove selected", self.remove_selected),
            ("Clear completed", self.clear_completed),
            ("Load selected in main GUI", self.load_selected_in_main),
            ("Restore session/browser", self.restore_selected_session),
            ("Open session folder", self.open_selected_session),
        ]
        for col, (label, command) in enumerate(secondary_actions):
            ttk.Button(queue_controls, text=label, command=command).grid(row=1, column=col, sticky="ew", padx=3, pady=2)
        ttk.Checkbutton(
            queue_controls, text="Run jobs in parallel", variable=self.parallel_enabled_var,
            command=self.configure_parallel,
        ).grid(row=1, column=5, sticky="w", padx=(8, 3), pady=2)
        ttk.Label(queue_controls, text="Workers").grid(row=1, column=6, sticky="e", padx=(3, 2))
        self.parallel_workers_spin = ttk.Spinbox(
            queue_controls, from_=1, to=32, width=5, textvariable=self.parallel_workers_var,
            command=self._sync_parallel_settings,
        )
        self.parallel_workers_spin.grid(row=1, column=7, sticky="w", padx=(0, 3), pady=2)
        self.parallel_workers_spin.bind("<FocusOut>", lambda _e: self._sync_parallel_settings())
        for col in range(8):
            queue_controls.columnconfigure(col, weight=1 if col < 7 else 0)

        pane = ttk.Panedwindow(outer, orient="horizontal")
        pane.pack(fill="both", expand=True)
        self.pane = pane
        left = ttk.Frame(pane, padding=(0, 0, 8, 0))
        right_host = ttk.Frame(pane, padding=(8, 0, 0, 0))
        pane.add(left, weight=11); pane.add(right_host, weight=9)
        right_host.rowconfigure(0, weight=1)
        right_host.columnconfigure(0, weight=1)
        right_canvas = tk.Canvas(right_host, highlightthickness=0, borderwidth=0)
        self.right_canvas = right_canvas
        right_scroll = ttk.Scrollbar(right_host, orient="vertical", command=right_canvas.yview)
        right_canvas.configure(yscrollcommand=right_scroll.set)
        right_canvas.grid(row=0, column=0, sticky="nsew")
        right_scroll.grid(row=0, column=1, sticky="ns")
        right = ttk.Frame(right_canvas)
        right_window = right_canvas.create_window((0, 0), window=right, anchor="nw")
        right.bind("<Configure>", lambda _e: right_canvas.configure(scrollregion=right_canvas.bbox("all")))
        right_canvas.bind("<Configure>", lambda e: right_canvas.itemconfigure(right_window, width=e.width))

        columns = ("status", "experiment", "source", "output", "drt", "progress")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended")
        headings = {"status":"Status", "experiment":"Experiment", "source":"Source", "output":"Output folder", "drt":"DRT", "progress":"Progress"}
        widths = {"status":125, "experiment":190, "source":340, "output":290, "drt":60, "progress":150}
        for col in columns:
            self.tree.heading(col, text=headings[col]); self.tree.column(col, width=widths[col], anchor="w", stretch=True)
        ybar = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew"); ybar.grid(row=0, column=1, sticky="ns"); xbar.grid(row=1, column=0, sticky="ew")
        left.rowconfigure(0, weight=1); left.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _e: self.load_selected_in_main())

        editor = ttk.LabelFrame(right, text="Selected experiment settings", padding=8)
        editor.pack(fill="both", expand=True)
        self.source_var = tk.StringVar(); self.name_var = tk.StringVar(); self.output_var = tk.StringVar(); self.eis_grid_var = tk.StringVar(value="100")
        row = 0
        ttk.Label(editor, text="Source").grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(editor, textvariable=self.source_var, state="readonly").grid(row=row, column=1, columnspan=3, sticky="ew", pady=3); row += 1
        ttk.Label(editor, text="Experiment name").grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(editor, textvariable=self.name_var).grid(row=row, column=1, columnspan=3, sticky="ew", pady=3); row += 1
        ttk.Label(editor, text="Output folder").grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(editor, textvariable=self.output_var).grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Button(editor, text="Choose", command=self.choose_output).grid(row=row, column=3, padx=(5, 0), pady=3); row += 1

        export_box = ttk.LabelFrame(editor, text="Export and parser settings", padding=6)
        export_box.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(8, 4)); row += 1
        self.export_vars: Dict[str, Any] = {}
        export_labels = [
            ("archive_npy_and_summary_json", "Parsed NPY + summary JSON"),
            ("analysis_ready_csv", "Analysis-ready CSV"),
            ("raw_vendor_json", "Raw vendor JSON"),
            ("run_technique_parsers", "Run CV/EIS/IV/OCP parsers"),
            ("canonical_h5_json", "Build canonical H5 + JSON"),
            ("build_browser_cache", "Prebuild browser cache + catalog"),
            ("create_experiment_subfolder", "Create experiment subfolder"),
            ("resume_completed_stages", "Resume validated checkpoints"),
        ]
        for idx, (key, label) in enumerate(export_labels):
            var = tk.BooleanVar(value=default_export_settings()[key]); self.export_vars[key] = var
            ttk.Checkbutton(export_box, text=label, variable=var).grid(row=idx // 2, column=idx % 2, sticky="w", padx=(0, 12), pady=2)
        ttk.Label(export_box, text="EIS parser grid").grid(row=4, column=0, sticky="w", pady=2)
        ttk.Entry(export_box, textvariable=self.eis_grid_var, width=10).grid(row=4, column=1, sticky="w", pady=2)

        drt_box = ttk.LabelFrame(editor, text="DRT settings", padding=6)
        self.drt_box = drt_box
        drt_box.grid(row=row, column=0, columnspan=4, sticky="ew", pady=4); row += 1
        self.drt_enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(drt_box, text="Run DRT", variable=self.drt_enabled_var).grid(row=0, column=0, sticky="w")
        self.drt_vars: Dict[str, Any] = {
            "rbf_type": tk.StringVar(value="gaussian"), "data": tk.StringVar(value="combined"),
            "derivative": tk.StringVar(value="1"), "inductance": tk.StringVar(value="without"),
            "regularization": tk.StringVar(value="1.0E-3"), "shape_control": tk.StringVar(value="fwhm"),
            "coefficient": tk.StringVar(value="0.5"), "sample_number": tk.StringVar(value="2000"),
            "run_id": tk.StringVar(value=""),
        }
        combos = [
            ("RBF", "rbf_type", ["gaussian", "c0", "c2", "c4", "c6", "inverse_quadratic", "inverse_quadric", "cauchy"]),
            ("Data", "data", ["re", "im", "combined"]),
            ("Derivative", "derivative", ["1", "2"]),
            ("Inductance", "inductance", ["with", "without", "discard"]),
            ("Shape", "shape_control", ["fwhm", "shape"]),
        ]
        for idx, (label, key, values) in enumerate(combos, 1):
            r = (idx + 1) // 2; c = 0 if idx % 2 else 2
            ttk.Label(drt_box, text=label).grid(row=r, column=c, sticky="w", padx=(0, 4), pady=2)
            ttk.Combobox(drt_box, textvariable=self.drt_vars[key], values=values, state="readonly", width=17).grid(row=r, column=c+1, sticky="ew", pady=2)
        r = 4
        for label, key in (("Regularization", "regularization"), ("Coefficient", "coefficient"), ("Samples", "sample_number"), ("Run ID", "run_id")):
            c = 0 if key in {"regularization", "sample_number"} else 2
            rr = r if c == 0 else r
            ttk.Label(drt_box, text=label).grid(row=rr, column=c, sticky="w", padx=(0, 4), pady=2)
            ttk.Entry(drt_box, textvariable=self.drt_vars[key], width=18).grid(row=rr, column=c+1, sticky="ew", pady=2)
            if c == 2: r += 1
        self.run_type_vars = {k: tk.BooleanVar(value=(k == "simple")) for k in ("simple", "bayesian", "bht")}
        self.plot_type_vars = {k: tk.BooleanVar(value=(k in {"eis", "drt"})) for k in ("eis", "magnitude", "phase", "re", "im", "drt", "score")}
        self.export_type_vars = {k: tk.BooleanVar(value=(k == "drt")) for k in ("drt", "eis")}
        run_types_row = ttk.Frame(drt_box); run_types_row.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        ttk.Label(run_types_row, text="Run:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        for index, (k, v) in enumerate(self.run_type_vars.items(), 1):
            ttk.Checkbutton(run_types_row, text=k, variable=v).grid(row=0, column=index, sticky="w", padx=(0, 5))
        plot_types_row = ttk.Frame(drt_box); plot_types_row.grid(row=7, column=0, columnspan=4, sticky="ew")
        ttk.Label(plot_types_row, text="Plot:").grid(row=0, column=0, sticky="nw", padx=(0, 4))
        for index, (k, v) in enumerate(self.plot_type_vars.items()):
            ttk.Checkbutton(plot_types_row, text=k, variable=v).grid(row=index // 4, column=(index % 4) + 1, sticky="w", padx=(0, 5))
        export_types_row = ttk.Frame(drt_box); export_types_row.grid(row=8, column=0, columnspan=4, sticky="ew")
        ttk.Label(export_types_row, text="Export:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        for index, (k, v) in enumerate(self.export_type_vars.items(), 1):
            ttk.Checkbutton(export_types_row, text=k, variable=v).grid(row=0, column=index, sticky="w", padx=(0, 5))
        drt_apply_row = ttk.Frame(drt_box)
        drt_apply_row.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(7, 0))
        ttk.Button(
            drt_apply_row, text="Apply DRT to selected jobs", command=self.apply_drt_to_selected_jobs
        ).pack(side="left")
        ttk.Button(
            drt_apply_row, text="Apply DRT to all jobs", command=self.apply_drt_to_all_jobs
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            drt_apply_row,
            text="DRT edits are remembered automatically when switching jobs.",
            style="Muted.TLabel",
        ).pack(side="left", padx=(10, 0))
        for c in range(4): drt_box.columnconfigure(c, weight=1 if c in {1,3} else 0)

        editor.columnconfigure(1, weight=1); editor.columnconfigure(2, weight=1)

        log_box = ttk.LabelFrame(editor, text="Job activity", padding=5)
        log_box.grid(row=row, column=0, columnspan=4, sticky="nsew", pady=(5, 0)); editor.rowconfigure(row, weight=1)
        from tkinter.scrolledtext import ScrolledText
        self.log_text = ScrolledText(log_box, wrap="word", height=10)
        self.log_text.pack(fill="both", expand=True)

        bottom = ttk.Frame(outer); bottom.pack(fill="x", pady=(8, 0))
        self.status_var = tk.StringVar(value="Ready. Add experiments, customize each job, then use Run all jobs or Run selected jobs.")
        ttk.Label(bottom, textvariable=self.status_var, anchor="w").pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(bottom, text="Live transactions", command=self.model.open_transaction_window).pack(side="right", padx=3)

    def _enable_dnd(self) -> None:
        try:
            from tkinterdnd2 import DND_FILES, TkinterDnD
            TkinterDnD._require(self.win)
            for widget in (self.drop_label, self.tree, self.win):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
            self.drop_label.configure(text="Drop files or folders here. Multiple items are accepted. You can also use Add files / Add folder.")
        except Exception as exc:
            self.drop_label.configure(text=f"Drag/drop support is unavailable ({exc}). Use Add files / Add folder; the launcher installs tkinterdnd2 automatically.")

    def _on_drop(self, event: Any) -> str:
        try:
            paths = list(self.win.tk.splitlist(event.data))
        except Exception:
            paths = [str(getattr(event, "data", ""))]
        self.model.add_paths(paths, open_window=False)
        return "break"

    def selected_ids(self) -> List[str]:
        return [str(x) for x in self.tree.selection()]

    def selected_job(self) -> Optional[BatchJob]:
        ids = self.selected_ids()
        return self.model.find(ids[0]) if ids else None

    def refresh(self, select_job: Optional[str] = None) -> None:
        if not self.model.running:
            try:
                self.parallel_enabled_var.set(bool(self.model.parallel_enabled))
                self.parallel_workers_var.set(max(1, int(self.model.parallel_workers or 1)))
            except Exception:
                pass
        current = select_job or (self.selected_ids()[0] if self.selected_ids() else self._editing_job_id)
        existing = set(self.tree.get_children())
        for job in self.model.jobs:
            detail = f"{job.progress_current}/{job.progress_total}" if job.progress_total else ""
            progress_text = f"{job.progress:.1f}%" + (f" · {detail}" if detail else "")
            display_status = {
                "Draft": "Ready",
                "Queued": "Ready",
                "Interrupted": "Resume available",
            }.get(job.status, job.status)
            status_text = display_status if job.stage in {"draft", "queued", "completed", "cancelled", "resume_available"} else f"{display_status} · {job.stage.replace('_', ' ')}"
            values = (
                status_text, job.experiment_name, job.source_path, job.output_dir,
                "On" if job.drt_settings.get("enabled") else "Off", progress_text,
            )
            if job.job_id in existing:
                self.tree.item(job.job_id, values=values); existing.remove(job.job_id)
            else:
                self.tree.insert("", "end", iid=job.job_id, values=values)
        for stale in existing: self.tree.delete(stale)
        self.queue_label.set(f"{len(self.model.jobs)} job(s) | jobs file: {self.model.queue_path or 'autosave pending'}")
        if current and self.model.find(current):
            self.tree.selection_set(current); self.tree.focus(current); self.tree.see(current)
            if select_job is not None or current != self._editing_job_id:
                self._load_editor(self.model.find(current))
        elif self.model.jobs:
            job = self.model.jobs[0]; self.tree.selection_set(job.job_id)
            if job.job_id != self._editing_job_id:
                self._load_editor(job)
        active_ids = self.model._run_job_ids or {j.job_id for j in self.model.jobs if j.status in {"Queued", "Running", "Stopping", "Completed", "Failed", "Cancelled"}}
        tracked = [j for j in self.model.jobs if j.job_id in active_ids]
        running_jobs = [j for j in self.model.jobs if j.status in {"Running", "Stopping"}]
        running = running_jobs[0] if running_jobs else None
        if running_jobs:
            mode = f"parallel x{self.model.parallel_workers}" if self.model.parallel_enabled and self.model.parallel_workers > 1 else "sequential"
            summary = " | ".join(f"{job.experiment_name} {job.progress:.1f}% · {job.stage.replace('_', ' ')}" for job in running_jobs)
            self.status_var.set(f"Running {len(running_jobs)} job(s), {mode}: {summary}")
        elif self.model.running:
            self.status_var.set("Batch worker is stopping or moving to the next job.")
        else:
            self.status_var.set("Ready. Use Run all jobs or Run selected jobs. Stop jobs cancels pending work and terminates active parser/DRT processes.")
        self._update_queue_toggle_label()

    def _current_drt_editor_settings(self) -> Dict[str, Any]:
        settings = {key: var.get() for key, var in self.drt_vars.items()}
        settings["enabled"] = bool(self.drt_enabled_var.get())
        settings["run_types"] = [k for k, v in self.run_type_vars.items() if v.get()]
        settings["plot_types"] = [k for k, v in self.plot_type_vars.items() if v.get()]
        settings["export_types"] = [k for k, v in self.export_type_vars.items() if v.get()]
        return settings

    def _remember_current_drt_editor(self, *, persist: bool = True) -> Optional[BatchJob]:
        """Remember DRT edits for the currently edited job without requiring Apply.

        This deliberately does not validate numeric fields while the user is
        moving between rows; validation still occurs before a job is queued or
        when Apply & save settings is pressed. This prevents partially typed DRT
        values from being lost merely because another job is selected.
        """
        job = self.model.find(self._editing_job_id or "")
        if job is None or not hasattr(self, "drt_vars"):
            return None
        job.drt_settings = self._current_drt_editor_settings()
        job.touch()
        if persist:
            try:
                self.model.write_job_session(job, include_browser=bool(job.outputs.get("browser_cache")))
            except Exception:
                pass
            self.model.autosave_queue()
        return job

    def _hide_window(self) -> None:
        self._remember_current_drt_editor(persist=True)
        self.win.withdraw()

    def _on_select(self, _event: Any = None) -> None:
        job = self.selected_job()
        if job is not None and self._editing_job_id and job.job_id != self._editing_job_id:
            self._remember_current_drt_editor(persist=True)
        if job: self._load_editor(job)
        self._update_queue_toggle_label()

    def _apply_drt_to_jobs(self, jobs: Sequence[BatchJob]) -> Tuple[int, int]:
        settings = self._current_drt_editor_settings()
        self.model.drt_defaults = dict(settings)
        changed = 0
        skipped = 0
        seen: set[str] = set()
        for job in jobs:
            if job.job_id in seen:
                continue
            seen.add(job.job_id)
            if job.status in {"Running", "Stopping"}:
                skipped += 1
                continue
            job.drt_settings = dict(settings)
            job.touch()
            try:
                self.model.write_job_session(job, include_browser=bool(job.outputs.get("browser_cache")))
            except Exception:
                pass
            changed += 1
        self.model.autosave_queue()
        self.refresh(select_job=self._editing_job_id)
        return changed, skipped

    def apply_drt_to_selected_jobs(self) -> None:
        jobs = [self.model.find(job_id) for job_id in self.selected_ids()]
        jobs = [job for job in jobs if job is not None]
        if not jobs:
            if self.messagebox:
                self.messagebox.showwarning("No selection", "Select one or more experiments first.", parent=self.win)
            return
        changed, skipped = self._apply_drt_to_jobs(jobs)
        message = f"Applied the current DRT settings to {changed} selected job(s)."
        if skipped:
            message += f" {skipped} active job(s) were left unchanged."
        self.status_var.set(message)

    def apply_drt_to_all_jobs(self) -> None:
        changed, skipped = self._apply_drt_to_jobs(list(self.model.jobs))
        message = f"Applied the current DRT settings to {changed} job(s). New jobs will inherit these DRT settings."
        if skipped:
            message += f" {skipped} active job(s) were left unchanged."
        self.status_var.set(message)

    def _update_queue_toggle_label(self) -> None:
        # v233 removes the separate user-facing queue membership step. Every
        # listed experiment is a job; Run all jobs or Run selected jobs prepares
        # the internal execution queue automatically.
        return

    def _load_editor(self, job: Optional[BatchJob]) -> None:
        if job is None: return
        self._editing_job_id = job.job_id
        self.source_var.set(job.source_path); self.name_var.set(job.experiment_name); self.output_var.set(job.output_dir)
        settings = default_export_settings(); settings.update(job.export_settings or {})
        for key, var in self.export_vars.items(): var.set(bool(settings.get(key)))
        self.eis_grid_var.set(str(settings.get("eis_n_grid", 100)))
        drt = default_drt_settings(); drt.update(job.drt_settings or {})
        self.drt_enabled_var.set(bool(drt.get("enabled")))
        for key, var in self.drt_vars.items(): var.set(str(drt.get(key, "")))
        for key, var in self.run_type_vars.items(): var.set(key in drt.get("run_types", []))
        for key, var in self.plot_type_vars.items(): var.set(key in drt.get("plot_types", []))
        for key, var in self.export_type_vars.items(): var.set(key in drt.get("export_types", []))
        try:
            self.log_text.delete("1.0", "end")
            if job.log_path and Path(job.log_path).exists():
                self.log_text.insert("1.0", Path(job.log_path).read_text(encoding="utf-8", errors="replace")[-40000:])
            elif job.message: self.log_text.insert("1.0", job.message)
        except Exception: pass

    def apply_settings(self, show_message: bool = True) -> Optional[BatchJob]:
        job = self.model.find(self._editing_job_id or "")
        if job is None: return None
        job.experiment_name = self.name_var.get().strip()
        job.output_dir = self.output_var.get().strip()
        job.export_settings = {key: bool(var.get()) for key, var in self.export_vars.items()}
        job.export_settings["eis_n_grid"] = self.eis_grid_var.get().strip() or "100"
        job.drt_settings = self._current_drt_editor_settings()
        self.model.drt_defaults = dict(job.drt_settings)
        errors = job.validate()
        if errors:
            if self.messagebox: self.messagebox.showerror("Invalid job settings", "\n".join(errors), parent=self.win)
            return None
        job.touch(); self.model.write_job_session(job, include_browser=bool(job.outputs.get("browser_cache"))); self.model.autosave_queue(); self.refresh(select_job=job.job_id)
        if show_message and self.messagebox:
            self.messagebox.showinfo("Settings saved", f"Saved settings and resumable session JSON:\n{job.session_json}", parent=self.win)
        return job

    def toggle_queue_selected(self) -> None:
        ids = self.selected_ids()
        if not ids:
            if self.messagebox: self.messagebox.showwarning("No selection", "Select one or more experiments first.", parent=self.win)
            return
        jobs = [self.model.find(job_id) for job_id in ids]
        jobs = [job for job in jobs if job is not None]
        try:
            if jobs and all(job.status == "Queued" for job in jobs):
                changed = self.model.dequeue(ids)
                self.status_var.set(f"Returned {len(changed)} selected job(s) to Ready.")
            else:
                self.apply_settings(show_message=False)
                changed = self.model.enqueue(ids)
                self.status_var.set(f"Prepared {len(changed)} selected job(s) for the next run.")
            self.refresh(select_job=ids[0])
        except Exception as exc:
            if self.messagebox: self.messagebox.showerror("Jobs update failed", str(exc), parent=self.win)

    def enqueue_selected(self) -> None:
        """Compatibility alias for older saved actions and scripts."""
        self.toggle_queue_selected()

    def duplicate_selected(self) -> None:
        job = self.apply_settings(show_message=False)
        if job is None: return
        copy = BatchJob.from_dict(job.to_dict())
        copy.session_folder = ""; copy.job_id = f"FQJOB_{_stamp()}_{uuid.uuid4().hex[:8]}"; copy.created_at = _now(); copy.updated_at = _now()
        copy.status = "Draft"; copy.stage = "draft"; copy.progress = 0; copy.completed_stages = []; copy.outputs = {}; copy.error = ""; copy.traceback = ""; copy.session_json = ""; copy.log_path = ""
        copy.experiment_name = f"{job.experiment_name}_copy"
        copy.output_dir = str(Path(job.output_dir).parent / _slug(copy.experiment_name))
        self.model.jobs.append(copy); self.model.write_job_session(copy, include_browser=False); self.model.autosave_queue(); self.refresh(select_job=copy.job_id)

    def add_files(self) -> None:
        if not self.filedialog: return
        paths = self.filedialog.askopenfilenames(title="Add electrochemical files as jobs", parent=self.win)
        if paths: self.model.add_paths(paths, open_window=False)

    def add_folder(self) -> None:
        if not self.filedialog: return
        path = self.filedialog.askdirectory(title="Add experiment folder as a job", parent=self.win)
        if path: self.model.add_paths([path], open_window=False)

    def choose_output(self) -> None:
        if not self.filedialog: return
        p = self.filedialog.askdirectory(title="Choose output folder for this experiment", initialdir=self.output_var.get() or str(Path.cwd()), parent=self.win)
        if p: self.output_var.set(p)

    def remove_selected(self) -> None:
        self.model.remove_jobs(self.selected_ids())

    def clear_completed(self) -> None:
        self.model.remove_jobs([j.job_id for j in self.model.jobs if j.status == "Completed"])

    def save_queue_dialog(self) -> None:
        if not self.filedialog: return
        p = self.filedialog.asksaveasfilename(title="Save FaradaIQ jobs", initialdir=str(self.model.queue_root()), initialfile=f"{self.model.queue_id}.faradaiq_jobs.json", defaultextension=".json", filetypes=[("FaradaIQ jobs", "*.faradaiq_jobs.json"), ("Legacy FaradaIQ queue", "*.faradaiq_queue.json"), ("JSON", "*.json")], parent=self.win)
        if p:
            self.model.save_queue_as(p); self.refresh()

    def load_queue_dialog(self) -> None:
        if not self.filedialog: return
        p = self.filedialog.askopenfilename(title="Load FaradaIQ jobs", initialdir=str(self.model.queue_root()), filetypes=[("FaradaIQ jobs", "*.faradaiq_jobs.json"), ("Legacy FaradaIQ queue", "*.faradaiq_queue.json"), ("JSON", "*.json")], parent=self.win)
        if p:
            try: self.model.load_queue(p); self.refresh()
            except Exception as exc:
                if self.messagebox: self.messagebox.showerror("Jobs file load failed", f"{exc}\n\n{traceback.format_exc()}", parent=self.win)

    def _sync_parallel_settings(self) -> None:
        try:
            workers = max(1, min(32, int(self.parallel_workers_var.get() or 1)))
        except Exception:
            workers = 1
            self.parallel_workers_var.set(1)
        self.model.parallel_workers = workers
        self.model.parallel_enabled = bool(self.parallel_enabled_var.get() and workers > 1)
        try:
            self.parent._v233_parallel_enabled_var.set(self.model.parallel_enabled)
            self.parent._v233_parallel_workers_var.set(self.model.parallel_workers)
        except Exception:
            pass
        self.model.autosave_queue()

    def configure_parallel(self) -> None:
        enabled = bool(self.parallel_enabled_var.get())
        if enabled:
            current = max(2, int(self.parallel_workers_var.get() or 2))
            try:
                from tkinter import simpledialog
                answer = simpledialog.askinteger(
                    "Parallel jobs",
                    "How many jobs can FaradaIQ run in parallel?\n\n"
                    "Use a conservative value for large H5/DRT jobs. Recommended: 2–4.",
                    parent=self.win, initialvalue=current, minvalue=2, maxvalue=32,
                )
            except Exception:
                answer = current
            if answer is None:
                self.parallel_enabled_var.set(False)
                self.parallel_workers_var.set(1)
            else:
                self.parallel_workers_var.set(int(answer))
        else:
            self.parallel_workers_var.set(1)
        self._sync_parallel_settings()
        self.status_var.set(
            f"Parallel execution enabled for up to {self.model.parallel_workers} jobs."
            if self.model.parallel_enabled else "Sequential execution enabled."
        )

    def run_batch_export(self) -> None:
        self.apply_settings(show_message=False)
        try:
            candidates = [job.job_id for job in self.model.jobs if job.status not in {"Running", "Completed"}]
            queued = self.model.enqueue_pending(candidates)
            queued_ids = [job.job_id for job in queued]
            if not queued_ids:
                queued_ids = [job.job_id for job in self.model.jobs if job.status == "Queued"]
            self._sync_parallel_settings()
            self.model.run_queue(
                queued_ids or None,
                parallel=self.model.parallel_enabled,
                max_workers=self.model.parallel_workers,
            )
            self.status_var.set(f"Running all jobs: {len(queued_ids) if queued_ids else 'all ready'} job(s).")
        except Exception as exc:
            if self.messagebox: self.messagebox.showwarning("Run all jobs could not start", str(exc), parent=self.win)

    def run_selected_export(self) -> None:
        self.apply_settings(show_message=False)
        ids = self.selected_ids()
        if not ids:
            if self.messagebox: self.messagebox.showwarning("No selection", "Select one or more experiments first.", parent=self.win)
            return
        try:
            queued = self.model.enqueue(ids)
            run_ids = [job.job_id for job in queued]
            self._sync_parallel_settings()
            self.model.run_queue(
                run_ids,
                parallel=self.model.parallel_enabled,
                max_workers=self.model.parallel_workers,
            )
            self.status_var.set(f"Running selected jobs: {len(run_ids)} job(s).")
        except Exception as exc:
            if self.messagebox: self.messagebox.showwarning("Run selected jobs could not start", str(exc), parent=self.win)

    def run_jobs(self) -> None:
        self.run_batch_export()

    def pause_after_current(self) -> None:
        self.model.pause_after_current = True
        self.status_var.set("Jobs will pause after the current job finishes.")

    def stop_all_exports(self) -> None:
        self.model.stop_all_exports()
        self.status_var.set("Stopping jobs. Active parser/DRT process trees are being terminated; in-process file writing stops at the next safe checkpoint.")

    def stop_after_current(self) -> None:
        self.model.stop_after_current = True
        self.status_var.set("Jobs will stop after the current job finishes.")

    def append_log(self, job_id: str, line: str) -> None:
        if job_id != self._editing_job_id: return
        try: self.log_text.insert("end", line + "\n"); self.log_text.see("end")
        except Exception: pass

    def load_selected_in_main(self) -> None:
        """Load a job without duplicating it or repeating completed inspection.

        Completed/inspected jobs restore their unique session and prebuilt browser
        cache directly. Draft jobs still use the normal Load + inspect workflow.
        """
        job = self.selected_job()
        if job is None:
            return
        try:
            has_finished_inspection = (
                "inspection" in job.completed_stages
                or job.status == "Completed"
                or bool(job.inspection_summary)
            )
            has_restorable_outputs = bool(job.outputs.get("browser_cache") or job.outputs.get("canonical_bundle"))
            if has_finished_inspection or has_restorable_outputs:
                self.restore_selected_session()
                self.status_var.set(
                    f"Loaded {job.experiment_name} from its saved job session; inspection, browser cache, and catalog were reused."
                )
                return
            self.parent.file_var.set(job.source_path)
            self.parent.output_dir_var.set(job.output_dir)
            self.parent.load_and_inspect()
            self.parent.lift()
        except Exception as exc:
            if self.messagebox:
                self.messagebox.showerror("Load job failed", str(exc), parent=self.win)

    def restore_selected_session(self) -> None:
        job = self.selected_job()
        if job is None:
            return
        try:
            path = self.model.write_job_session(job, include_browser=bool(job.outputs.get("browser_cache")))
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            apply_session = self.core.get("_v177_apply_full_session")
            if not callable(apply_session):
                raise RuntimeError("FaradaIQ full-session loader is unavailable.")
            apply_session(self.parent, payload, open_browsers=True)
            self.parent.lift()
            self.status_var.set(f"Restored {job.experiment_name}; prebuilt browser cache and catalog opened when available.")
        except Exception as exc:
            if self.messagebox:
                self.messagebox.showerror("Session restore failed", f"{exc}\n\n{traceback.format_exc()}", parent=self.win)

    def open_selected_session(self) -> None:
        job = self.selected_job()
        if job is None: return
        self.model.write_job_session(job, include_browser=bool(job.outputs.get("browser_cache")))
        try:
            if os.name == "nt": os.startfile(str(Path(job.session_json).parent))
            else:
                import webbrowser
                webbrowser.open(Path(job.session_json).parent.resolve().as_uri())
        except Exception: pass


def _walk_widgets(root: Any) -> Iterable[Any]:
    yield root
    try:
        for child in root.winfo_children():
            yield from _walk_widgets(child)
    except Exception:
        return


def _widget_text(widget: Any) -> str:
    try: return str(widget.cget("text") or "")
    except Exception: return ""


def _enable_main_drop(gui: Any, model: BatchQueueModel) -> None:
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
        TkinterDnD._require(gui)
        targets = []
        for w in _walk_widgets(gui):
            try:
                if str(w.cget("textvariable")) == str(gui.file_var): targets.append(w)
            except Exception: pass
        if not targets: targets = [gui]
        def dropped(event: Any) -> str:
            try: paths = list(gui.tk.splitlist(event.data))
            except Exception: paths = [str(getattr(event, "data", ""))]
            jobs = model.add_paths(paths, status="Draft", open_window=True)
            if jobs:
                try: gui.file_var.set(jobs[-1].source_path)
                except Exception: pass
            return "break"
        for target in targets:
            target.drop_target_register(DND_FILES); target.dnd_bind("<<Drop>>", dropped)
        gui._v229_drop_targets = targets
    except Exception as exc:
        gui._v229_drop_error = str(exc)


def install(core: MutableMapping[str, Any]) -> None:
    Session = core.get("SessionReaderGUI"); ttk = core.get("ttk"); tk = core.get("tk")
    if Session is None or ttk is None or tk is None or getattr(Session, "_v233_batch_queue_installed", False):
        return

    # Replace the legacy screen-size calculation for the main window with a
    # work-area-aware calculation. On Windows this excludes the taskbar, which
    # keeps the persistent status row and Details button completely visible.
    previous_responsive = core.get("_v109_apply_responsive_window")
    def responsive_window(win: Any, role: str = "main") -> Any:
        if str(role).lower() == "main":
            return _place_in_work_area(win, "main")
        if callable(previous_responsive):
            return previous_responsive(win, role)
        return _place_in_work_area(win, "batch")
    core["_v109_apply_responsive_window"] = responsive_window

    previous_build = Session._build_ui
    def build_ui(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = previous_build(self, *args, **kwargs)
        model = BatchQueueModel(self, core)
        self._v233_batch_model = model
        self._v232_batch_model = model  # compatibility with v232 sessions/scripts
        self._v230_batch_model = model  # compatibility with v230 sessions/scripts
        self._v229_batch_model = model  # compatibility with v229 sessions/scripts
        self.open_batch_export_queue = lambda: model.open_window()

        def show_error(title: str, exc: BaseException) -> None:
            box = core.get("messagebox")
            if box is not None:
                try: box.showwarning(title, str(exc), parent=self)
                except Exception: pass

        self._v233_parallel_enabled_var = tk.BooleanVar(value=False)
        self._v233_parallel_workers_var = tk.IntVar(value=1)

        def sync_parallel_from_main(ask: bool = False) -> None:
            enabled = bool(self._v233_parallel_enabled_var.get())
            try:
                workers = max(1, min(32, int(self._v233_parallel_workers_var.get() or 1)))
            except Exception:
                workers = 1
            if enabled and ask:
                try:
                    from tkinter import simpledialog
                    answer = simpledialog.askinteger(
                        "Parallel jobs",
                        "How many jobs can FaradaIQ run in parallel?\n\n"
                    "Use a conservative value for large H5/DRT jobs. Recommended: 2–4.",
                        parent=self, initialvalue=max(2, workers), minvalue=2, maxvalue=32,
                    )
                except Exception:
                    answer = max(2, workers)
                if answer is None:
                    self._v233_parallel_enabled_var.set(False)
                    workers = 1
                else:
                    workers = int(answer)
                    self._v233_parallel_workers_var.set(workers)
            if not bool(self._v233_parallel_enabled_var.get()):
                workers = 1
                self._v233_parallel_workers_var.set(1)
            model.parallel_enabled = bool(self._v233_parallel_enabled_var.get() and workers > 1)
            model.parallel_workers = workers
            try:
                if model.window is not None and model.window.winfo_exists():
                    model.window.parallel_enabled_var.set(model.parallel_enabled)
                    model.window.parallel_workers_var.set(model.parallel_workers)
            except Exception:
                pass
            model.autosave_queue()

        def toggle_parallel_from_main() -> None:
            sync_parallel_from_main(ask=bool(self._v233_parallel_enabled_var.get()))
            try:
                model.refresh_window()
            except Exception:
                pass

        def run_batch_from_main() -> None:
            try:
                candidates = [job.job_id for job in model.jobs if job.status not in {"Running", "Completed"}]
                queued = model.enqueue_pending(candidates)
                run_ids = [job.job_id for job in queued] or [job.job_id for job in model.jobs if job.status == "Queued"]
                sync_parallel_from_main()
                model.open_window()
                model.run_queue(
                    run_ids or None,
                    parallel=model.parallel_enabled,
                    max_workers=model.parallel_workers,
                )
            except Exception as exc:
                show_error("Run all jobs could not start", exc)

        def run_selected_from_main() -> None:
            try:
                source = Path(str(self.file_var.get() or "").strip())
                if not source.exists():
                    raise ValueError("Choose a valid input file or folder first.")
                job = model.find_source(source)
                if job is None:
                    created = model.add_paths([source], status="Draft", open_window=False)
                    job = created[-1] if created else None
                if job is None:
                    raise ValueError("The selected input could not be added as a job.")
                output_text = str(self.output_dir_var.get() or "").strip()
                if output_text:
                    job.output_dir = str(Path(output_text).expanduser())
                model.write_job_session(job, include_browser=bool(job.outputs.get("browser_cache")))
                model.enqueue([job.job_id])
                sync_parallel_from_main()
                model.open_window(select_job=job.job_id)
                model.run_queue(
                    [job.job_id],
                    parallel=model.parallel_enabled,
                    max_workers=model.parallel_workers,
                )
            except Exception as exc:
                show_error("Run selected jobs could not start", exc)

        def stop_exports_from_main() -> None:
            model.stop_all_exports()

        self.run_all_jobs = run_batch_from_main
        self.run_selected_jobs = run_selected_from_main
        self.stop_jobs = stop_exports_from_main
        self.run_batch_export = run_batch_from_main
        self.run_selected_batch_export = run_selected_from_main
        self.stop_all_exports = stop_exports_from_main
        self.open_live_export_transactions = model.open_transaction_window
        try:
            load_button = next((w for w in _walk_widgets(self) if _widget_text(w).strip().lower() in {"load and inspect", "load + inspect"}), None)
            input_buttons = load_button.master if load_button is not None else self
            controls = getattr(input_buttons, "master", self)
            row = ttk.Frame(controls, style="Card.TFrame")
            try:
                row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(5, 0))
            except Exception:
                row.pack(fill="x", pady=(5, 0))
            ttk.Label(row, text="Export jobs", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
            ttk.Button(row, text="Open jobs", command=self.open_batch_export_queue).grid(row=0, column=1, sticky="ew", padx=3)
            ttk.Button(row, text="Run all jobs", command=run_batch_from_main, style="Accent.TButton").grid(row=0, column=2, sticky="ew", padx=3)
            ttk.Button(row, text="Run selected jobs", command=run_selected_from_main).grid(row=0, column=3, sticky="ew", padx=3)
            ttk.Button(row, text="Stop jobs", command=stop_exports_from_main).grid(row=0, column=4, sticky="ew", padx=3)
            ttk.Button(row, text="Live transactions", command=model.open_transaction_window).grid(row=0, column=5, sticky="ew", padx=3)
            self._v233_queue_summary_var = tk.StringVar(value="Jobs: 0")
            self._v232_queue_summary_var = self._v233_queue_summary_var
            self._v230_queue_summary_var = self._v233_queue_summary_var
            self._v229_queue_summary_var = self._v233_queue_summary_var
            ttk.Label(row, textvariable=self._v233_queue_summary_var, style="Muted.TLabel", anchor="w").grid(row=0, column=6, sticky="ew", padx=(8, 0))
            ttk.Checkbutton(
                row, text="Run in parallel", variable=self._v233_parallel_enabled_var,
                command=toggle_parallel_from_main,
            ).grid(row=1, column=1, sticky="w", padx=3, pady=(4, 0))
            ttk.Label(row, text="Workers").grid(row=1, column=2, sticky="e", padx=(3, 2), pady=(4, 0))
            workers_spin = ttk.Spinbox(row, from_=1, to=32, width=5, textvariable=self._v233_parallel_workers_var)
            workers_spin.grid(row=1, column=3, sticky="w", padx=(0, 3), pady=(4, 0))
            workers_spin.bind("<FocusOut>", lambda _e: sync_parallel_from_main())
            self._v233_main_job_status_var = tk.StringVar(value="No jobs are running.")
            ttk.Label(
                row, textvariable=self._v233_main_job_status_var, style="Muted.TLabel",
                anchor="w", justify="left",
            ).grid(row=1, column=4, columnspan=3, sticky="ew", padx=(8, 0), pady=(4, 0))
            row.columnconfigure(6, weight=1)
        except Exception:
            try:
                row = ttk.Frame(self); row.pack(fill="x", padx=10, pady=2)
                for label, command in (("Open jobs", self.open_batch_export_queue), ("Run all jobs", run_batch_from_main), ("Run selected jobs", run_selected_from_main), ("Stop jobs", stop_exports_from_main), ("Live transactions", model.open_transaction_window)):
                    ttk.Button(row, text=label, command=command).pack(side="left", padx=2)
            except Exception: pass
        try: self.after(100, lambda: _enable_main_drop(self, model))
        except Exception: pass
        for delay in (0, 180, 700, 1500):
            try: self.after(delay, lambda g=self: _place_in_work_area(g, "main"))
            except Exception: pass
        return result
    Session._build_ui = build_ui

    previous_load = Session.load_and_inspect
    def load_and_inspect(self: Any, *args: Any, **kwargs: Any) -> Any:
        model = getattr(self, "_v233_batch_model", None) or getattr(self, "_v232_batch_model", None) or getattr(self, "_v230_batch_model", None) or getattr(self, "_v229_batch_model", None)
        job: Optional[BatchJob] = None
        try:
            source = Path(str(self.file_var.get() or "").strip())
            if source.exists() and model is not None:
                jobs = model.add_paths([source], status="Inspecting", open_window=False)
                job = jobs[-1] if jobs else None
        except Exception: pass
        try:
            result = previous_load(self, *args, **kwargs)
        except Exception as exc:
            if job is not None and model is not None:
                model.inspect_failed_from_main(job, exc)
            raise
        if job is not None and model is not None:
            attempts = {"n": 0}
            def poll() -> None:
                attempts["n"] += 1
                busy = bool(getattr(self, "_v225_load_busy", False))
                if busy and attempts["n"] < 15000:
                    try: self.after(100, poll)
                    except Exception: pass
                    return
                model.inspect_completed_from_main(job)
            try: self.after(120, poll)
            except Exception: pass
        return result
    Session.load_and_inspect = load_and_inspect

    previous_apply_session = core.get("_v177_apply_full_session")
    if callable(previous_apply_session) and not core.get("_v233_batch_session_restore_installed"):
        def apply_full_session_with_batch(gui: Any, payload: Mapping[str, Any], open_browsers: bool = True) -> Any:
            result = previous_apply_session(gui, payload, open_browsers=open_browsers)
            batch_payload = payload.get("batch_job") if isinstance(payload, Mapping) else None
            model = getattr(gui, "_v233_batch_model", None) or getattr(gui, "_v232_batch_model", None) or getattr(gui, "_v230_batch_model", None) or getattr(gui, "_v229_batch_model", None)
            if isinstance(batch_payload, Mapping) and model is not None:
                restored = BatchJob.from_dict(batch_payload)
                if restored.status == "Running":
                    restored.status = "Interrupted"
                    restored.stage = "resume_available"
                    restored.message = "Restored after interruption; validated checkpoints can be resumed."
                current = model.find(restored.job_id)
                if current is None:
                    current = model.find_source(Path(restored.source_path))
                if current is None:
                    model.jobs.append(restored)
                else:
                    index = model.jobs.index(current)
                    model.jobs[index] = restored
                    # Remove any legacy duplicate records for the same source.
                    try:
                        source_key = str(Path(restored.source_path).resolve()).casefold()
                    except Exception:
                        source_key = str(restored.source_path).casefold()
                    model.jobs = [
                        job for pos, job in enumerate(model.jobs)
                        if pos == index or (
                            (str(Path(job.source_path).resolve()).casefold() if Path(job.source_path).exists() else str(job.source_path).casefold()) != source_key
                        )
                    ]
                model.queue_path = model.queue_path or model.queue_root() / f"{model.queue_id}.faradaiq_jobs.json"
                model.autosave_queue()
                model.refresh_window(select_job=restored.job_id)
            return result
        core["_v177_apply_full_session"] = apply_full_session_with_batch
        core["_v233_batch_session_restore_installed"] = True
        core["_v229_batch_session_restore_installed"] = True

    Session._v233_batch_queue_installed = True
    Session._v232_batch_queue_installed = True
    Session._v230_batch_queue_installed = True
    Session._v229_batch_queue_installed = True
    core["BatchJob"] = BatchJob
    core["BatchQueueModel"] = BatchQueueModel
    core["_v233_run_batch_job"] = run_batch_job
    core["_v232_run_batch_job"] = run_batch_job
    core["_v230_run_batch_job"] = run_batch_job
    core["_v229_run_batch_job"] = run_batch_job
    core["_v233_default_export_settings"] = default_export_settings
    core["_v232_default_export_settings"] = default_export_settings
    core["_v230_default_export_settings"] = default_export_settings
    core["_v229_default_export_settings"] = default_export_settings
    core["_v233_default_drt_settings"] = default_drt_settings
    core["_v232_default_drt_settings"] = default_drt_settings
    core["_v230_default_drt_settings"] = default_drt_settings
    core["_v229_default_drt_settings"] = default_drt_settings

    help_tabs = core.get("_V207_HELP_TABS")
    title = "v237 parallel jobs, cached restore, live transaction tabs, and duplicate prevention"
    if isinstance(help_tabs, list) and not any(str(x[0]) == title for x in help_tabs if isinstance(x, (list, tuple)) and x):
        help_tabs.append((title, r"""
EXPERIMENT EXPORT JOBS

Press Open jobs below Load + inspect, or drag files/folders onto the main input field. Every file or folder becomes a job. Load + inspect automatically creates or selects the corresponding job.

Each job has an independent experiment name, output folder, archive/CSV/raw-JSON options, parser grid, parser execution choice, canonical H5/JSON choice, browser-cache choice, and complete headless pyDRTtools settings. Press Apply & save settings to write the unique session JSON. There is no separate Add to queue step: every listed experiment is a job, and the run buttons prepare the internal execution schedule automatically.

Use Run all jobs, Run selected jobs, and Stop jobs from either the main GUI or the jobs window. Enable Run jobs in parallel and choose a conservative worker count when independent experiments can safely run together. Sequential mode remains the default. Stop jobs cancels pending work immediately and terminates every active parser/DRT subprocess tree; in-process file writes stop at the next safe checkpoint.

The Live transactions window opens automatically whenever a run starts. It has one tab for every requested job, including parallel jobs, and shows per-job progress, stage, elapsed time, ETA, work counts, and parser/DRT console output. The main GUI also lists every running job and its current percentage.

Every job creates:
  <output>/FaradaIQ_job_sessions/<job-id>/
with a full *.faradaiq_session.json, export_settings.json, drt_settings.json, job_state.json, and job.log. Inspection, canonical H5/JSON creation, and browser-cache and unified catalog generation are completed inside the job before the status becomes Completed. Load selected in main GUI restores the finished job session and prebuilt browser cache directly instead of repeating Load + inspect. Matching source paths and job IDs are deduplicated so restoring a completed job does not create a second job. Saved jobs files can be reloaded after an interruption; a job that was Running is marked Interrupted and can be safely resumed.

Drag/drop uses tkinterdnd2. The launcher checks and installs it with the normal FaradaIQ dependencies. Add files and Add folder remain available if native drag/drop is unavailable on a particular desktop.
"""))
