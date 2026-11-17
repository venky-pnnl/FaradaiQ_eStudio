#!/usr/bin/env python
"""Run one exact-prompt FaradaIQ Edison Scientific transaction.

The console is launched with ``cmd /k`` and remains open. Every reviewed file
is uploaded individually, every upload receipt is logged, and the Edison task
is not created unless all checked files were successfully shared.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import traceback
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from faradaiq.release_identity import APP_VERSION as VERSION
except Exception:
    VERSION = "v292"


def _jsonable(obj: Any, depth: int = 0) -> Any:
    if depth > 10:
        return str(obj)
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in obj]
    for method in ("model_dump", "dict"):
        try:
            return _jsonable(getattr(obj, method)(), depth + 1)
        except Exception:
            pass
    try:
        return _jsonable(vars(obj), depth + 1)
    except Exception:
        return str(obj)


def _extract_answer(payload: Any) -> str:
    data = _jsonable(payload)
    if not isinstance(data, dict):
        return str(data or "")
    candidates = [data.get(k) for k in ("formatted_answer", "answer", "output", "result")]
    for path in (
        ("environment_frame", "state", "state", "answer"),
        ("environment_frame", "state", "info", "output_data"),
    ):
        cur: Any = data
        try:
            for key in path:
                cur = cur[key]
            candidates.append(cur)
        except Exception:
            pass
    for value in candidates:
        if value:
            return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    return ""


def _install_client() -> None:
    try:
        import edison_client  # noqa: F401
        return
    except Exception:
        pass
    print("edison-client not found. Installing into the FaradaIQ private environment...", flush=True)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "edison-client>=0.15.0,<0.16"])


def _storage_id(upload: Any) -> str:
    data = _jsonable(upload)
    try:
        value = upload.data_storage.id
        if value:
            return str(value)
    except Exception:
        pass
    if isinstance(data, dict):
        value = (data.get("data_storage") or {}).get("id")
        if value:
            return str(value)
    return ""


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.environ.get(name, default) or default).strip()
    try:
        value = int(float(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer; received {raw!r}.") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}; received {value}.")
    return value


def _human_size(value: int) -> str:
    size = float(max(0, int(value)))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{int(value)} B"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    args = parser.parse_args()
    job_dir = Path(args.job_dir).resolve()
    prompt_path = job_dir / "analysis_prompt.md"
    request_path = job_dir / "edison_request.json"
    settings_path = job_dir / "service_settings.json"
    manifest_path = job_dir / "files_to_share_manifest.json"
    audit_path = job_dir / "edison_zip_audit.json"
    zip_candidates = sorted(job_dir.glob("*_for_Edison.zip"))
    if not prompt_path.exists():
        print("[ERROR] Missing exact prompt:", prompt_path)
        return 2
    if not zip_candidates:
        print("[ERROR] Missing reviewed Edison ZIP in:", job_dir)
        return 2

    zip_path = zip_candidates[-1]
    prompt = prompt_path.read_text(encoding="utf-8", errors="replace")
    if not prompt.strip():
        print("[ERROR] The exact Edison prompt is empty:", prompt_path)
        return 2
    try:
        request = json.loads(request_path.read_text(encoding="utf-8")) if request_path.exists() else {}
        settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"files": []}
    except json.JSONDecodeError as exc:
        print(f"[ERROR] Invalid Edison job JSON: {exc}")
        return 2
    if not isinstance(request, dict) or not isinstance(settings, dict) or not isinstance(manifest, dict):
        print("[ERROR] Edison request, settings, and manifest files must contain JSON objects.")
        return 2
    reviewed_files = list(manifest.get("files") or [])
    log_path = job_dir / "edison_transaction.log"
    receipts_path = job_dir / "edison_upload_receipts.json"

    def log(message: Any = "") -> None:
        text = str(message)
        print(text, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")

    log("=" * 76)
    log(f"FaradaIQ Studio {VERSION} -> Edison Scientific exact-prompt transaction")
    log("=" * 76)
    log(f"Job folder: {job_dir}")
    log(f"Prompt file: {prompt_path}")
    log(f"Request JSON: {request_path}")
    log(f"Files manifest: {manifest_path}")
    log(f"Reviewed ZIP: {zip_path}")
    log("")
    log("Exact reviewed user prompt")
    log("---------------- BEGIN USER PROMPT ----------------")
    log(prompt.rstrip())
    log("----------------- END USER PROMPT -----------------")
    log("")
    log("Reviewed request JSON")
    log(json.dumps(request, ensure_ascii=False, indent=2))
    log("")

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.namelist()
            crc_error = zf.testzip()
    except Exception as exc:
        log(f"[ERROR] Reviewed ZIP cannot be opened: {exc}")
        return 2
    log(f"Reviewed ZIP members: {len(members)}; CRC status: {'OK' if crc_error is None else 'FAILED: ' + str(crc_error)}")
    for member in members:
        log(f"  [ZIP] {member}")
    if crc_error is not None:
        return 2

    log("")
    log(f"Files explicitly selected for Edison: {len(reviewed_files)}")
    total_bytes = 0
    missing: list[dict[str, Any]] = []
    for index, item in enumerate(reviewed_files, start=1):
        path = Path(str(item.get("path") or "")).expanduser()
        exists = path.is_file()
        size = int(path.stat().st_size) if exists else int(item.get("size_bytes") or 0)
        total_bytes += size if exists else 0
        log(f"  [{index:04d}/{len(reviewed_files):04d}] {item.get('role','Data file')} | {_human_size(size)} | {path}")
        if not exists:
            missing.append({"index": index, "path": str(path), "role": item.get("role"), "reason": "not readable at transaction start"})
    log(f"Selected-file total: {_human_size(total_bytes)}")
    if missing:
        log("[BLOCKED] One or more checked files are missing. No Edison task was created.")
        log(json.dumps(missing, ensure_ascii=False, indent=2))
        return 2

    if audit_path.exists():
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            log(f"ZIP audit ready: {audit.get('ready')}; packaged checked files: {audit.get('packaged_file_count')}")
            if audit.get("ready") is not True:
                log("[BLOCKED] Edison ZIP audit is not marked ready.")
                return 2
            if int(audit.get("packaged_file_count") or 0) != len(reviewed_files):
                log("[BLOCKED] Edison ZIP audit count does not match the reviewed file count.")
                return 2
        except Exception as exc:
            log(f"[BLOCKED] Could not validate Edison ZIP audit: {exc}")
            return 2

    api_key = (
        os.environ.get("EDISON_PLATFORM_API_KEY")
        or os.environ.get("EDISON_API_KEY")
        or os.environ.get("EDISON_SCIENTIFIC_API_KEY")
        or ""
    )
    if not api_key:
        log("[ERROR] No Edison API key was found in Main Studio or the process environment.")
        log("Set it in LLM Settings -> Data Sharing and start a new Edison job.")
        return 3
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError:
        log("[ERROR] The Edison API key contains a non-ASCII character and cannot be used in an HTTP header.")
        return 3

    try:
        _install_client()
        from edison_client import EdisonClient
        try:
            from edison_client import JobNames
        except Exception:
            try:
                from edison_client.models.app import JobNames
            except Exception:
                from edison_client.models import JobNames
        try:
            from edison_client.models.app import RuntimeConfig, TaskRequest
        except Exception:
            from edison_client.models import RuntimeConfig, TaskRequest
    except Exception as exc:
        log(f"[ERROR] Edison client initialization failed: {exc}")
        log(traceback.format_exc())
        return 4

    async def run() -> dict[str, Any]:
        client = EdisonClient(api_key=api_key)
        receipts: dict[str, Any] = {
            "schema": "faradaiq-edison-upload-receipts-v290",
            "version": VERSION,
            "job_folder": str(job_dir),
            "selected_file_count": len(reviewed_files),
            "selected_files": [],
            "audit_bundle": None,
        }

        async def upload_path(path: Path, name: str, description: str) -> tuple[str, Any]:
            upload = await client.astore_file_content(name=name, file_path=str(path), description=description)
            upload_json = _jsonable(upload)
            storage_id = _storage_id(upload)
            if not storage_id:
                raise RuntimeError(f"Edison returned no data storage ID for {path}")
            return storage_id, upload_json

        # Upload every checked file individually. These IDs, not only the ZIP,
        # are attached to the analysis runtime so Edison can access each file.
        selected_storage_ids: list[str] = []
        for index, item in enumerate(reviewed_files, start=1):
            path = Path(str(item["path"])).resolve()
            log(f"[FILE UPLOAD {index}/{len(reviewed_files)}] {path.name}")
            try:
                storage_id, upload_json = await upload_path(
                    path,
                    name=f"FaradaIQ reviewed file {index:04d}: {path.name}",
                    description=(
                        f"Explicitly checked FaradaIQ file. Role: {item.get('role','Data file')}. "
                        f"Source group: {item.get('source_group','')}"
                    ),
                )
            except Exception as exc:
                receipts["selected_files"].append({**item, "status": "failed", "error": str(exc)})
                receipts_path.write_text(json.dumps(receipts, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                raise RuntimeError(f"Checked file upload failed for {path}: {exc}") from exc
            selected_storage_ids.append(storage_id)
            receipt = {**item, "status": "uploaded", "data_storage_id": storage_id, "upload_response": upload_json}
            receipts["selected_files"].append(receipt)
            receipts_path.write_text(json.dumps(receipts, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            log(f"[FILE UPLOAD OK] data_storage_id={storage_id} | {path}")

        # Preserve one reviewed ZIP as an audit artifact. When no data files are
        # selected, this prompt/request ZIP is the only attached data entry.
        log("[AUDIT UPLOAD] Uploading the reviewed Edison ZIP...")
        audit_storage_id, audit_upload_json = await upload_path(
            zip_path,
            name=f"FaradaIQ reviewed Edison job: {job_dir.name}",
            description="Exact reviewed prompt, request JSON, manifest, and checked files for audit/provenance.",
        )
        receipts["audit_bundle"] = {
            "status": "uploaded",
            "path": str(zip_path),
            "data_storage_id": audit_storage_id,
            "upload_response": audit_upload_json,
        }
        receipts_path.write_text(json.dumps(receipts, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        log(f"[AUDIT UPLOAD OK] data_storage_id={audit_storage_id}")

        if reviewed_files and len(selected_storage_ids) != len(reviewed_files):
            raise RuntimeError("Not all checked files received an Edison data storage ID; task creation blocked.")
        data_ids = selected_storage_ids if selected_storage_ids else [audit_storage_id]
        log(f"[ATTACHMENTS READY] {len(data_ids)} Edison data entry/entries will be attached to the task.")
        for storage_id in data_ids:
            log(f"  data_entry:{storage_id}")

        language = os.environ.get("EDISON_ANALYSIS_LANGUAGE", "PYTHON") or "PYTHON"
        max_steps = _env_int("EDISON_MAX_STEPS", 30, minimum=1, maximum=500)
        runtime = RuntimeConfig(
            max_steps=max_steps,
            environment_config={
                "language": language,
                "data_storage_uris": [f"data_entry:{storage_id}" for storage_id in data_ids],
            },
        )
        try:
            job_name = JobNames.ANALYSIS
        except Exception:
            job_name = "analysis"
        task_request = TaskRequest(name=job_name, query=prompt, runtime_config=runtime)
        log("[TASK] Creating Edison Analysis task with the exact reviewed prompt and verified attachments...")
        task_id = client.create_task(task_request)
        live_url = f"https://platform.edisonscientific.com/trajectories/{task_id}"
        log(f"[TASK CREATED] {task_id}")
        log(f"[LIVE URL] {live_url}")

        poll_seconds = _env_int("EDISON_TASK_POLL_SECONDS", 15, minimum=0, maximum=3600)
        timeout_seconds = _env_int("EDISON_TASK_TIMEOUT_SECONDS", 3600, minimum=1, maximum=86400)
        started = time.time()
        status_json: dict[str, Any] = {}
        status = "queued"
        active = {"queued", "pending", "running", "in progress", "in_progress"}
        while str(status).lower() in active:
            if time.time() - started > timeout_seconds:
                raise TimeoutError(f"Edison task did not finish within {timeout_seconds} seconds. Task ID: {task_id}")
            task_status = client.get_task(task_id)
            status_json = _jsonable(task_status)
            status = status_json.get("status", getattr(task_status, "status", "unknown")) if isinstance(status_json, dict) else "unknown"
            log(f"[{datetime.now().isoformat(timespec='seconds')}] [STATUS] {status}")
            log(json.dumps(status_json, ensure_ascii=False, indent=2))
            if str(status).lower() not in active:
                break
            time.sleep(poll_seconds)

        log("[RESULT] Retrieving verbose task response...")
        verbose = client.get_task(task_id, verbose=True)
        verbose_json = _jsonable(verbose)
        answer = _extract_answer(verbose_json)
        final_status = str(status).lower()
        failure_reason = None
        if isinstance(verbose_json, dict):
            failure_reason = verbose_json.get("failure_reason") or verbose_json.get("error")
        return {
            "status": "completed" if final_status in {"success", "succeeded", "completed", "done"} else "task_failed",
            "upload_succeeded": True,
            "checked_file_count": len(reviewed_files),
            "uploaded_checked_file_count": len(selected_storage_ids),
            "attached_data_storage_ids": data_ids,
            "audit_bundle_data_storage_id": audit_storage_id,
            "upload_receipts_file": str(receipts_path),
            "task_status": str(status),
            "trajectory_id": str(task_id),
            "live_url": live_url,
            "exact_prompt": prompt,
            "request_json": request,
            "answer": answer,
            "failure_reason": failure_reason,
            "task_status_response": status_json,
            "task_result": verbose_json,
        }

    try:
        result = asyncio.run(run())
    except Exception as exc:
        result = {
            "status": "transaction_error",
            "upload_succeeded": False,
            "checked_file_count": len(reviewed_files),
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "exact_prompt": prompt,
            "request_json": request,
            "upload_receipts_file": str(receipts_path),
        }

    response_json = job_dir / "edison_job_response.json"
    response_txt = job_dir / "edison_job_response.txt"
    response_json.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    lines = [
        "FaradaIQ Edison Scientific response",
        "===================================",
        "",
        f"Status: {result.get('status')}",
        f"Upload succeeded: {result.get('upload_succeeded')}",
        f"Checked files: {result.get('checked_file_count', len(reviewed_files))}",
        f"Uploaded checked files: {result.get('uploaded_checked_file_count', '')}",
        f"Task status: {result.get('task_status', '')}",
        f"Trajectory/task ID: {result.get('trajectory_id', '')}",
        f"Live URL: {result.get('live_url', '')}",
        "",
    ]
    if result.get("answer"):
        lines += ["Edison answer", "-------------", str(result["answer"]), ""]
    if result.get("failure_reason") or result.get("error"):
        lines += ["Failure details", "---------------", str(result.get("failure_reason") or result.get("error")), ""]
    lines += ["Raw response JSON", "-----------------", json.dumps(result, ensure_ascii=False, indent=2, default=str)]
    response_txt.write_text("\n".join(lines), encoding="utf-8")
    log("")
    log("=" * 76)
    log("FINAL EDISON RESPONSE")
    log("=" * 76)
    log(response_txt.read_text(encoding="utf-8"))
    log(f"\nSaved response JSON: {response_json}")
    log(f"Saved response text: {response_txt}")
    return 0 if result.get("status") == "completed" else 5


if __name__ == "__main__":
    raise SystemExit(main())
