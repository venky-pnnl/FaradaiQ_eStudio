"""FaradaIQ Studio v290 interactive SciLink sessions and minimal Browser data handoff.

Production fixes:
- The hyperspectral range control can be dragged manually without Plotly redraws
  interrupting the pointer gesture. The selected frame is rendered on release,
  with previous/next buttons and keyboard navigation as fallbacks.
- Ask SciLink from the Browser creates one compact, job-local analysis dataset from the current Browser selection.
- The only context supplied with that dataset is the exact experiment description entered in Main Studio and a compact JSON structure description of the generated bundle.
- The SciLink CLI receives explicit --data and --metadata paths and the terminal remains interactive for follow-up questions.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import traceback
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

VERSION = "v290"
_CORE: MutableMapping[str, Any] = {}
_PREVIOUS_REVIEW: Any = None

_DATA_EXTENSIONS = {
    ".csv", ".tsv", ".txt", ".asc", ".dat", ".npy", ".npz", ".xlsx", ".xls",
}
_METADATA_EXTENSIONS = {".json", ".yaml", ".yml", ".xml"}
_SUPPORT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".pdf", ".html", ".md", ".docx"}


def _g(name: str, default: Any = None) -> Any:
    return _CORE.get(name, default)


def _gui_from(source: Any) -> Any:
    return getattr(source, "gui", source)


def _browser_from(source: Any) -> Any | None:
    try:
        import faradaiq.v239_scilink_edison as old
        return old._browser_from(source)
    except Exception:
        return source if hasattr(source, "tree") else None


def _safe_path_key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except Exception:
        return os.path.normcase(str(path))


def _browser_selection_paths(source: Any) -> list[Path]:
    """Resolve the current Browser selection only when Ask SciLink is clicked."""
    browser = _browser_from(source)
    if browser is None:
        return []
    paths: list[Path] = []
    try:
        selected = getattr(browser, "selected_paths", None)
        if callable(selected):
            paths.extend(Path(p).expanduser().resolve() for p in selected(plottable_only=False))
    except Exception:
        pass
    try:
        raw = getattr(browser, "selected_path", None)
        if raw:
            paths.append(Path(raw).expanduser().resolve())
    except Exception:
        pass
    try:
        tree = getattr(browser, "tree", None)
        path_by_iid = getattr(browser, "_path_by_iid", {}) or {}
        if tree is not None:
            for iid in list(tree.selection() or []):
                raw = path_by_iid.get(iid)
                if raw:
                    paths.append(Path(raw).expanduser().resolve())
    except Exception:
        pass
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            if not path.is_file():
                continue
            key = _safe_path_key(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        except Exception:
            continue
    return unique


def _selected_record_names(source: Any) -> list[str]:
    try:
        import faradaiq.v247_experience as v247
        return list(v247._selected_record_names(source))
    except Exception:
        return []


def _experiment_description(source: Any) -> str:
    """Return the operator-visible experiment description without auto-generated file paths.

    The Main Studio text widget can contain a FARADAIQ_AUTO_CONTEXT block with
    canonical H5/JSON names and the original experiment folder. That block caused
    SciLink to leave the reviewed job-local data folder and inspect unrelated
    parent files. The Browser handoff keeps only the operator-facing description
    that precedes the auto-context marker.
    """
    gui = _gui_from(source)
    text = ""
    for name in ("desc_text", "experiment_description_text", "description_text"):
        try:
            widget = getattr(gui, name)
            text = str(widget.get("1.0", "end-1c"))
            if text.strip():
                break
        except Exception:
            continue
    if not text.strip():
        for name in ("experiment_description", "description"):
            try:
                value = getattr(gui, name)
                if value is not None and str(value).strip():
                    text = str(value)
                    break
            except Exception:
                continue
    text = re.sub(
        r"(?is)\n?FARADAIQ_AUTO_CONTEXT_BEGIN.*?FARADAIQ_AUTO_CONTEXT_END\s*",
        "\n",
        text,
    )
    text = re.sub(r"(?is)\n?FARADAIQ_AUTO_CONTEXT_BEGIN.*$", "\n", text)
    clean_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if clean_lines and clean_lines[-1] != "":
                clean_lines.append("")
            continue
        if re.match(r"(?i)^(source|analysis/output folder|canonical h5|canonical json|browser catalog)\s*:", line):
            continue
        if re.search(r"(?:^[A-Za-z]:\\|^\\\\|/mnt/|/home/)", line):
            continue
        clean_lines.append(line)
    return "\n".join(clean_lines).strip()[:12000]


def _text_table_structure(path: Path) -> dict[str, Any]:
    """Inspect delimiter, headers, and row count without copying raw values into metadata."""
    info: dict[str, Any] = {"kind": "text_table", "columns": [], "row_count": None}
    try:
        sample = path.read_text(encoding="utf-8-sig", errors="replace")[:131072]
        lines = [line for line in sample.splitlines() if line.strip()]
        if not lines:
            return info
        first = lines[0]
        delimiter = "," if first.count(",") >= max(first.count("\t"), first.count(";")) else "\t" if first.count("\t") >= first.count(";") else ";"
        columns = [cell.strip() for cell in first.split(delimiter)]
        info["delimiter"] = "tab" if delimiter == "\t" else delimiter
        info["columns"] = columns[:200]
        # Count lines in a streaming pass. This is compact metadata only.
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            count = sum(1 for line in handle if line.strip())
        info["row_count"] = max(0, count - 1)
    except Exception as exc:
        info["inspection_warning"] = str(exc)[:300]
    return info


def _file_structure(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    base: dict[str, Any] = {
        "filename": path.name,
        "relative_path": f"data/{path.name}",
        "extension": suffix,
        "size_bytes": int(path.stat().st_size) if path.exists() else 0,
    }
    if suffix in {".csv", ".tsv", ".txt", ".asc", ".dat"}:
        base.update(_text_table_structure(path))
    elif suffix == ".npy":
        try:
            import numpy as np
            arr = np.load(path, mmap_mode="r", allow_pickle=False)
            base.update({"kind": "numpy_array", "shape": list(arr.shape), "dtype": str(arr.dtype)})
        except Exception as exc:
            base.update({"kind": "numpy_array", "inspection_warning": str(exc)[:300]})
    elif suffix == ".npz":
        try:
            import numpy as np
            with np.load(path, allow_pickle=False) as archive:
                base.update({
                    "kind": "numpy_archive",
                    "arrays": [{"name": key, "shape": list(archive[key].shape), "dtype": str(archive[key].dtype)} for key in list(archive.files)[:200]],
                })
        except Exception as exc:
            base.update({"kind": "numpy_archive", "inspection_warning": str(exc)[:300]})
    else:
        base["kind"] = "data_file"
    return base


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_column_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _find_column(headers: Sequence[str], candidates: Sequence[str]) -> int | None:
    normalized = [_normalized_column_name(value) for value in headers]
    wanted = {_normalized_column_name(value) for value in candidates}
    for index, value in enumerate(normalized):
        if value in wanted:
            return index
    for index, value in enumerate(normalized):
        if any(token and token in value for token in wanted):
            return index
    return None


def _filename_series_metadata(path: Path) -> dict[str, Any]:
    text = path.stem
    out: dict[str, Any] = {}
    point = re.search(r"(?i)(?:^|_)point[_-]?(\d+)", text)
    cycle = re.search(r"(?i)(?:^|_)cycle[_-]?(\d+)", text)
    scan = re.search(r"(?i)(?:^|_)scanrate[_-]?([0-9]+(?:[p.]?[0-9]+)?)mvs", text)
    if point:
        out["point_index"] = int(point.group(1))
    if cycle:
        out["cycle_index"] = int(cycle.group(1))
    if scan:
        out["scan_rate_mV_s"] = float(scan.group(1).replace("p", "."))
    return out


def _prepare_scilink_table(source: Path, destination: Path, techniques: Sequence[str]) -> dict[str, Any]:
    """Create an agent-ready lossless x/y projection for CV/LSV tables.

    SciLink's CurveFittingAgent internally serializes one-dimensional curves as
    two-column ``data.npy`` arrays. Sending a three-column time/potential/current
    CSV made generated scripts alternately assume two or three columns. For CV/LSV
    selections FaradaIQ now provides the raw potential/current pairs as an explicit
    two-column table while preserving the untouched source file in ``source_data``.
    """
    technique_set = {str(value).upper() for value in techniques}
    suffix = source.suffix.lower()
    result: dict[str, Any] = {
        "source_filename": source.name,
        "source_sha256": _sha256(source),
        "transformation": "exact_copy",
        "source_columns": [],
        "scilink_columns": [],
    }
    if suffix not in {".csv", ".tsv", ".txt", ".asc", ".dat"} or not ({"CV", "LSV"} & technique_set):
        shutil.copy2(source, destination)
        result["scilink_sha256"] = _sha256(destination)
        result.update(_filename_series_metadata(source))
        return result

    try:
        sample = source.read_text(encoding="utf-8-sig", errors="replace")[:65536]
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except Exception:
        dialect = csv.excel
        dialect.delimiter = ","
    with source.open("r", encoding="utf-8-sig", errors="replace", newline="") as src:
        reader = csv.reader(src, dialect)
        try:
            headers = next(reader)
        except StopIteration:
            headers = []
        p_index = _find_column(headers, ("potential_V", "potential", "E_V", "Ewe/V", "voltage_V"))
        i_index = _find_column(headers, ("current_A", "current", "I_A", "I/mA", "current_mA"))
        if p_index is None or i_index is None:
            shutil.copy2(source, destination)
            result["inspection_warning"] = "Potential/current columns were not identified; the original table was copied unchanged."
        else:
            with destination.open("w", encoding="utf-8", newline="") as dst:
                writer = csv.writer(dst, lineterminator="\n")
                writer.writerow(["potential_V", "current_A"])
                rows = 0
                for row in reader:
                    if max(p_index, i_index) >= len(row):
                        continue
                    potential = row[p_index].strip()
                    current = row[i_index].strip()
                    if not potential or not current:
                        continue
                    writer.writerow([potential, current])
                    rows += 1
            result.update({
                "transformation": "lossless_xy_projection",
                "source_columns": [str(value).strip() for value in headers],
                "scilink_columns": ["potential_V", "current_A"],
                "x_column": "potential_V",
                "y_column": "current_A",
                "row_count": rows,
            })
    result["scilink_sha256"] = _sha256(destination)
    result.update(_filename_series_metadata(source))
    return result


def _write_scilink_context(
    job_dir: Path,
    copied_paths: Sequence[Path],
    record_names: Sequence[str],
    experiment_description: str,
    file_provenance: Sequence[Mapping[str, Any]],
) -> Path:
    """Write the sole SciLink-visible metadata JSON inside the working data folder."""
    techniques = _infer_techniques(copied_paths, record_names)
    files: list[dict[str, Any]] = []
    for index, path in enumerate(copied_paths):
        item = _file_structure(path)
        item["relative_path"] = path.name
        if index < len(file_provenance):
            item["provenance"] = dict(file_provenance[index])
        item.update(_filename_series_metadata(path))
        files.append(item)
    series_values = {
        item["filename"]: item.get("scan_rate_mV_s")
        for item in files if item.get("scan_rate_mV_s") is not None
    }
    series_metadata = {
        "variable": "scan_rate_mV_s",
        "unit": "mV/s",
        "values": series_values,
    } if series_values else None
    payload = {
        "schema": "faradaiq-scilink-context-v261",
        "application": "FaradaIQ Studio",
        "version": VERSION,
        "experiment_description": experiment_description,
        # Keep this in the exact compact structure SciLink's CurveFittingAgent
        # requests when it detects a file series. This prevents an unnecessary
        # question followed by an autonomous re-run inferred from filenames.
        "series_metadata": series_metadata,
        "working_directory": ".",
        "data_access": {
            "scope": "current job data folder only",
            "parent_directory_search": False,
            "metadata_file": "scilink_bundle_context.json",
        },
        "data_structure": {
            "selection_scope": "current Browser selection only",
            "selected_record_names": list(record_names),
            "detected_techniques": techniques,
            "file_count": len(copied_paths),
            "files": files,
            "series_variable": series_metadata,
            "curve_agent_matrix_contract": {
                "techniques": [value for value in techniques if value in {"CV", "LSV"}],
                "columns": ["potential_V", "current_A"],
                "shape": "N x 2",
                "values": "raw potential/current pairs in original row order; no smoothing or resampling",
            } if ({"CV", "LSV"} & set(techniques)) else None,
        },
        "context_policy": "Only the operator-facing Main Studio description and compact structure of this job-local data folder are included. Absolute parent paths, canonical manifests, raw values, and hidden scientific prompts are excluded.",
    }
    path = job_dir / "data" / "scilink_bundle_context.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.resolve()


def _bundle_candidate(source: Any) -> dict[str, Any] | None:
    paths = _browser_selection_paths(source)
    if not paths:
        return None
    total = 0
    for path in paths:
        try:
            total += int(path.stat().st_size)
        except Exception:
            pass
    return {
        "path": "",
        "role": "SciLink-ready current Browser selection",
        "source_group": "Current Browser selection bundle",
        "generated": "browser_selection_bundle",
        "selected": True,
        "size_bytes": total,
        "bundle_source_paths": [str(p) for p in paths],
        "display_path": (
            f"Automatically create a compact job-local data bundle from {len(paths)} currently selected file(s). "
            "This bundle is selected by default and will be placed in the new SciLink job's data folder."
        ),
    }


def _review_candidates(source: Any, candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Put the generated Browser bundle first and leave every other item unchecked."""
    bundle = _bundle_candidate(source)
    out: list[dict[str, Any]] = []
    if bundle is not None:
        out.append(bundle)
    for raw in candidates:
        item = dict(raw)
        if bundle is not None and str(item.get("source_group") or "").strip().lower() == "current selection":
            # The generated bundle replaces redundant individual current-selection rows.
            continue
        item["selected"] = False
        out.append(item)
    return out


def _candidate_role(item: Mapping[str, Any]) -> str:
    return str(item.get("role") or "Data file").strip() or "Data file"


def _candidate_group(item: Mapping[str, Any]) -> str:
    return str(item.get("source_group") or item.get("group") or "Other").strip() or "Other"


def _candidate_extension(item: Mapping[str, Any]) -> str:
    if str(item.get("generated") or "") == "browser_selection_bundle":
        return "(generated bundle)"
    raw = str(item.get("path") or item.get("display_path") or "")
    return Path(raw).suffix.lower() or "(generated)"


def _unique_destination(folder: Path, index: int, source: Path) -> Path:
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source.stem).strip("._-")[:90] or "data"
    suffix = source.suffix.lower()
    candidate = folder / f"{index:04d}_{safe_stem}{suffix}"
    serial = 2
    while candidate.exists():
        candidate = folder / f"{index:04d}_{safe_stem}_{serial}{suffix}"
        serial += 1
    return candidate


def _copy_file(source: Path, destination_folder: Path, index: int) -> Path:
    destination_folder.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination(destination_folder, index, source)
    shutil.copy2(source, destination)
    return destination.resolve()


def _classify_item(item: Mapping[str, Any], path: Path) -> str:
    role = _candidate_role(item).lower()
    group = _candidate_group(item).lower()
    suffix = path.suffix.lower()
    if suffix in _METADATA_EXTENSIONS or "metadata" in role or "manifest" in role or "metadata" in group:
        return "metadata"
    if suffix in _SUPPORT_EXTENSIONS or any(token in role or token in group for token in ("literature", "figure", "visualization", "support", "reference", "additional")):
        return "support"
    return "data"


def _infer_techniques(paths: Sequence[Path], names: Sequence[str]) -> list[str]:
    text = " ".join([*(p.name for p in paths), *names]).upper()
    techniques = []
    for key in ("CV", "EIS", "DRT", "CA", "CP", "OCP", "LSV", "DPV", "SWV", "GEIS"):
        if re.search(rf"(?:^|[^A-Z]){re.escape(key)}(?:[^A-Z]|$)", text):
            techniques.append(key)
    return techniques


def _write_bundle_zip(
    job_dir: Path,
    data_paths: Sequence[Path],
    metadata_paths: Sequence[Path],
    support_paths: Sequence[Path],
    source_paths: Sequence[Path] = (),
) -> Path:
    zip_path = job_dir / "scilink_ready_browser_selection.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in data_paths:
            zf.write(path, f"data/{path.name}")
        for path in metadata_paths:
            if path.parent.name == "data":
                zf.write(path, f"data/{path.name}")
            else:
                zf.write(path, f"metadata/{path.name}")
        for path in support_paths:
            zf.write(path, f"supporting/{path.name}")
        for path in source_paths:
            zf.write(path, f"source_data/{path.name}")
    return zip_path.resolve()


def _materialize_selected_files(
    source: Any,
    selected: Sequence[Mapping[str, Any]],
    job_dir: Path,
) -> tuple[list[str], list[str], list[str], Path | None]:
    """Create a self-contained, validated SciLink dataset from the reviewed selection."""
    data_dir = job_dir / "data"
    source_dir = job_dir / "source_data"
    support_dir = job_dir / "supporting"
    data_dir.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)
    data_paths: list[Path] = []
    source_paths: list[Path] = []
    support_paths: list[Path] = []
    provenance: list[dict[str, Any]] = []
    counters = {"data": 0, "support": 0}
    record_names = _selected_record_names(source)

    def add_data(path: Path) -> None:
        counters["data"] += 1
        original_copy = _copy_file(path, source_dir, counters["data"])
        source_paths.append(original_copy)
        destination = _unique_destination(data_dir, counters["data"], path)
        info = _prepare_scilink_table(path, destination, _infer_techniques([path], record_names))
        data_paths.append(destination.resolve())
        provenance.append(info)

    def add_support(path: Path) -> None:
        counters["support"] += 1
        support_paths.append(_copy_file(path, support_dir, counters["support"]))

    for item in selected:
        generated = str(item.get("generated") or "").strip().lower()
        if generated == "browser_selection_bundle":
            for raw in list(item.get("bundle_source_paths") or []):
                path = Path(str(raw)).expanduser().resolve()
                if path.is_file() and path.suffix.lower() not in _METADATA_EXTENSIONS:
                    add_data(path)
            continue
        if generated == "session_metadata":
            continue
        raw = str(item.get("path") or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            continue
        kind = _classify_item(item, path)
        if kind == "metadata":
            continue
        add_data(path) if kind == "data" else add_support(path)

    metadata_paths: list[Path] = []
    if data_paths:
        context_path = _write_scilink_context(
            job_dir, data_paths, record_names, _experiment_description(source), provenance
        )
        metadata_paths.append(context_path)
        validation = {
            "schema": "faradaiq-scilink-data-preflight-v261",
            "version": VERSION,
            "status": "ready",
            "working_directory": str(data_dir.resolve()),
            "data_file_count": len(data_paths),
            "metadata_file": str(context_path),
            "all_data_files_exist": all(path.is_file() and path.stat().st_size > 0 for path in data_paths),
            "parent_paths_exposed_to_scilink": False,
            "files": [
                {
                    "filename": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                    "provenance": provenance[index],
                }
                for index, path in enumerate(data_paths)
            ],
        }
        (job_dir / "scilink_data_preflight.json").write_text(
            json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    bundle_zip = _write_bundle_zip(
        job_dir, data_paths, metadata_paths, support_paths, source_paths
    ) if (data_paths or support_paths) else None
    return (
        [str(path) for path in data_paths],
        [str(path) for path in support_paths],
        [str(path) for path in metadata_paths],
        bundle_zip,
    )


def write_scilink_job(
    source: Any,
    exact_prompt: str,
    selected: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
    *,
    mode: str = "autonomous",
    requested_filenames: Sequence[str] | None = None,
    unsafe_execution_ok: bool | None = None,
    literature_analysis_enabled: bool | None = None,
    labric_cloud_context_enabled: bool | None = None,
    wait_for_user_questions: bool | None = None,
) -> dict[str, Any]:
    """Create one self-contained SciLink job while preserving the exact prompt."""
    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_id = f"scilink_{stamp}_{uuid.uuid4().hex[:6]}"
    base = old._safe_name(old._base_name(_gui_from(source)))
    job_dir = old._job_root(source) / f"{base}_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=False)

    prompt_path = job_dir / "analysis_prompt.md"
    prompt_path.write_text(str(exact_prompt), encoding="utf-8")

    data_paths, supporting_paths, metadata_paths, bundle_zip = _materialize_selected_files(source, selected, job_dir)
    request = {
        "selected_data_paths": data_paths,
        "supporting_file_paths": supporting_paths,
        "metadata_paths": metadata_paths,
        "output_folder": str(job_dir.resolve()),
        "selected_record_names": _selected_record_names(source),
        "requested_filenames": [str(value).strip() for value in (requested_filenames or []) if str(value).strip()],
    }
    request_path = job_dir / "scilink_request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")

    settings = dict(cleanup._service_settings(cfg))
    # v261 always unlocks generated-code execution for the launched SciLink
    # child process. The optional argument remains for backward compatibility
    # but cannot disable the process-local override.
    authorized_unsafe_execution = True
    literature_enabled = bool(
        cfg.get("scilink_literature_analysis_enabled", True)
        if literature_analysis_enabled is None else literature_analysis_enabled
    )
    labric_cloud_enabled = bool(
        cfg.get("scilink_labric_cloud_context_enabled", cfg.get("labric_mcp_enabled", False))
        if labric_cloud_context_enabled is None else labric_cloud_context_enabled
    )
    wait_for_user = bool(
        cfg.get("scilink_wait_for_user_questions", True)
        if wait_for_user_questions is None else wait_for_user_questions
    )
    requested_mode = "autonomous" if str(mode).strip().lower().startswith("auto") else "co-pilot"
    effective_mode = "co-pilot" if wait_for_user else requested_mode
    settings.update({
        "scilink_mode": effective_mode,
        "scilink_requested_mode": requested_mode,
        "scilink_wait_for_user_questions": wait_for_user,
        "scilink_human_feedback_required": wait_for_user,
        "scilink_auto_continue_after_question": not wait_for_user,
        "inject_faradaiq_skill_prompt": False,
        "handoff_contract": "exact_user_prompt_plus_job_local_request_json",
        "job_local_data_bundle": bool(data_paths),
        "scilink_unsafe_execution_ok": True,
        "unsafe_execution_authorization_scope": "current SciLink CLI subprocess only",
        "scilink_literature_analysis_enabled": literature_enabled,
        "scilink_labric_cloud_context_enabled": labric_cloud_enabled,
        "labric_mcp_enabled": labric_cloud_enabled,
        "labric_mcp_transport": "stdio",
        "labric_mcp_server_name": "labric",
        "labric_mcp_package_local": True,
        "edison_enabled": True,
        "direct_edison_enabled": bool(settings.get("direct_edison_enabled", False)),
        "unsafe_execution_environment_variable": "UNSAFE_EXECUTION_OK",
        "scilink_auto_repair_dependencies": True,
        "scilink_curvefit_data_contract": "job-local validated x/y tables",
    })
    settings_path = job_dir / "service_settings.json"
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_path = Path(metadata_paths[0]) if metadata_paths else None

    return {
        "job_id": job_id,
        "job_dir": job_dir,
        "prompt_path": prompt_path,
        "manifest_path": request_path,
        "request_path": request_path,
        "settings_path": settings_path,
        "manifest": request,
        "settings": settings,
        "bundle_zip": bundle_zip,
        "bundle_summary_path": summary_path,
    }


def _preview_request(source: Any, selected: Sequence[Mapping[str, Any]], requested: Sequence[str]) -> dict[str, Any]:
    data: list[str] = []
    support: list[str] = []
    metadata: list[str] = []
    for item in selected:
        generated = str(item.get("generated") or "").strip().lower()
        if generated == "browser_selection_bundle":
            for index, raw in enumerate(list(item.get("bundle_source_paths") or []), start=1):
                path = Path(str(raw))
                safe = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._-")[:90] or "data"
                data.append(f"<new SciLink job>/data/{index:04d}_{safe}{path.suffix.lower()}")
            metadata.append("<new SciLink job>/data/scilink_bundle_context.json")
            continue
        if generated == "session_metadata":
            continue
        raw = str(item.get("path") or "").strip()
        if not raw:
            continue
        path = Path(raw)
        kind = _classify_item(item, path)
        target = f"<new SciLink job>/{'data' if kind == 'data' else 'metadata' if kind == 'metadata' else 'supporting'}/{path.name}"
        (data if kind == "data" else metadata if kind == "metadata" else support).append(target)
    return {
        "selected_data_paths": data,
        "supporting_file_paths": support,
        "metadata_paths": metadata,
        "output_folder": "<new SciLink job folder created after Start>",
        "selected_record_names": _selected_record_names(source),
        "requested_filenames": [str(value).strip() for value in requested if str(value).strip()],
    }


def show_analysis_review(source: Any, service: str, prompt_override: str | None = None) -> Any:
    """Review SciLink with one auto-selected Browser bundle; delegate Edison unchanged."""
    service_name = "SciLink" if service == "scilink" else "Edison Scientific"
    if service != "scilink":
        return _PREVIOUS_REVIEW(source, service, prompt_override=prompt_override)

    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup
    import faradaiq.v250_reliability as reliability

    tk, ttk, ScrolledText = _g("tk"), _g("ttk"), _g("ScrolledText")
    messagebox = _g("messagebox")
    gui = _gui_from(source)
    cfg = old.load_sharing_settings()
    candidates = _review_candidates(source, old.collect_share_candidates(source, cfg))
    initial_prompt = "" if prompt_override is None else str(prompt_override)
    bundle_present = bool(candidates and candidates[0].get("generated") == "browser_selection_bundle")

    win = tk.Toplevel(gui)
    win.title(f"FaradaIQ Studio {VERSION} - Review SciLink")
    win.geometry("1260x890")
    win.minsize(980, 720)
    outer = ttk.Frame(win, style="Card.TFrame", padding=(12, 12))
    outer.pack(fill="both", expand=True, padx=10, pady=10)
    ttk.Label(outer, text="Review SciLink request", style="Section.TLabel").pack(anchor="w")
    header_text = (
        "The current Browser selection has been prepared as one SciLink-ready bundle and selected automatically. "
        "Enter or edit the prompt, review the bundle, then start the job. Other files remain unchecked."
        if bundle_present else
        "No Browser data file is currently selected. Enter a prompt and explicitly select any files you want copied into the SciLink job."
    )
    ttk.Label(outer, text=header_text, style="Muted.TLabel", wraplength=1180).pack(anchor="w", pady=(2, 8))

    tabs = ttk.Notebook(outer)
    tabs.pack(fill="both", expand=True)
    prompt_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    files_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    request_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    settings_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    tabs.add(prompt_tab, text="Prompt")
    tabs.add(files_tab, text="Files to Share")
    tabs.add(request_tab, text="Request JSON")
    tabs.add(settings_tab, text="Service Settings")

    prompt_box = ScrolledText(prompt_tab, wrap="word")
    prompt_box.pack(fill="both", expand=True)
    if initial_prompt:
        prompt_box.insert("1.0", initial_prompt)
    try:
        prompt_box.focus_set()
    except Exception:
        pass

    search_var = tk.StringVar(value="")
    role_var = tk.StringVar(value="All roles")
    group_var = tk.StringVar(value="All groups")
    type_var = tk.StringVar(value="All types")
    selected_only_var = tk.BooleanVar(value=False)
    selected_initial = sum(1 for item in candidates if item.get("selected"))
    summary_var = tk.StringVar(value=f"Selected {selected_initial} of {len(candidates)} item(s).")

    toolbar = ttk.Frame(files_tab, style="Card.TFrame")
    toolbar.pack(fill="x", pady=(0, 6))
    ttk.Label(toolbar, text="Search", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 4))
    ttk.Entry(toolbar, textvariable=search_var, width=32).grid(row=0, column=1, sticky="ew", padx=(0, 6))
    roles = sorted({_candidate_role(item) for item in candidates})
    groups = sorted({_candidate_group(item) for item in candidates})
    types = sorted({_candidate_extension(item) for item in candidates})
    ttk.Combobox(toolbar, textvariable=role_var, values=["All roles", *roles], state="readonly", width=24).grid(row=0, column=2, padx=3)
    ttk.Combobox(toolbar, textvariable=group_var, values=["All groups", *groups], state="readonly", width=25).grid(row=0, column=3, padx=3)
    ttk.Combobox(toolbar, textvariable=type_var, values=["All types", *types], state="readonly", width=18).grid(row=0, column=4, padx=3)
    ttk.Checkbutton(toolbar, text="Selected only", variable=selected_only_var).grid(row=0, column=5, padx=(6, 3))
    toolbar.columnconfigure(1, weight=1)

    actions = ttk.Frame(files_tab, style="Card.TFrame")
    actions.pack(fill="x", pady=(0, 5))
    ttk.Label(actions, textvariable=summary_var, style="Muted.TLabel").pack(side="left", fill="x", expand=True)

    canvas = tk.Canvas(files_tab, highlightthickness=0, borderwidth=0)
    ybar = ttk.Scrollbar(files_tab, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=ybar.set)
    canvas.pack(side="left", fill="both", expand=True)
    ybar.pack(side="right", fill="y")
    inner = ttk.Frame(canvas, style="Card.TFrame")
    inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(inner_id, width=e.width))

    file_rows: list[dict[str, Any]] = []
    for index, item in enumerate(candidates):
        var = tk.BooleanVar(value=bool(item.get("selected")))
        row = ttk.Frame(inner, style="Card.TFrame", padding=(2, 3))
        row.grid(row=index, column=0, sticky="ew")
        row.columnconfigure(2, weight=1)
        display = item.get("display_path") or item.get("path") or "(generated only if checked)"
        ttk.Checkbutton(row, variable=var).grid(row=0, column=0, sticky="nw", padx=(0, 4))
        ttk.Label(row, text=_candidate_role(item), style="Card.TLabel", width=31).grid(row=0, column=1, sticky="nw", padx=(0, 8))
        ttk.Label(row, text=str(display), style="Muted.TLabel", wraplength=720).grid(row=0, column=2, sticky="nw")
        ttk.Label(row, text=old._format_size(item.get("size_bytes")), style="Muted.TLabel", width=10).grid(row=0, column=3, sticky="ne", padx=(8, 0))
        file_rows.append({"item": item, "var": var, "row": row, "visible": True})
    inner.columnconfigure(0, weight=1)

    wait_for_user_var = tk.BooleanVar(value=bool(cfg.get("scilink_wait_for_user_questions", True)))
    mode_var = tk.StringVar(value="Co-pilot" if wait_for_user_var.get() else "Autonomous")
    mode_row = ttk.Frame(settings_tab, style="Card.TFrame")
    mode_row.pack(fill="x", pady=(0, 8))
    ttk.Label(mode_row, text="SciLink mode", style="Card.TLabel").pack(side="left")
    mode_combo = ttk.Combobox(mode_row, textvariable=mode_var, values=("Co-pilot", "Autonomous"), state="readonly", width=18)
    mode_combo.pack(side="left", padx=(8, 0))
    ttk.Label(mode_row, text="Co-pilot waits at human review gates.", style="Muted.TLabel").pack(side="left", padx=(8, 0))
    interaction_box = ttk.LabelFrame(settings_tab, text="Interactive questions", padding=(10, 8))
    interaction_box.pack(fill="x", pady=(0, 8))
    ttk.Checkbutton(
        interaction_box,
        text="Pause and wait for my input when SciLink asks a question",
        variable=wait_for_user_var,
    ).pack(anchor="w")
    ttk.Label(
        interaction_box,
        text="Checked by default. The SciLink CLI is launched in Co-pilot mode and will not automatically rerun the sample after asking for missing information or review.",
        style="Muted.TLabel", wraplength=1080,
    ).pack(anchor="w", fill="x", pady=(2, 0))

    def sync_interaction_mode(*_args: Any) -> None:
        if wait_for_user_var.get():
            mode_var.set("Co-pilot")
            mode_combo.configure(state="disabled")
        else:
            mode_combo.configure(state="readonly")

    try:
        wait_for_user_var.trace_add("write", sync_interaction_mode)
    except Exception:
        pass
    sync_interaction_mode()
    literature_analysis_var = tk.BooleanVar(value=bool(cfg.get("scilink_literature_analysis_enabled", True)))
    literature_box = ttk.LabelFrame(settings_tab, text="SciLink literature analysis", padding=(10, 8))
    literature_box.pack(fill="x", pady=(0, 8))
    ttk.Checkbutton(
        literature_box,
        text="Ask SciLink to perform literature analysis and novelty assessment",
        variable=literature_analysis_var,
    ).pack(anchor="w")
    ttk.Label(
        literature_box,
        text="Checked by default. Edison/FutureHouse remains available inside SciLink in every launch mode. Uncheck this only when the current SciLink job should not use literature tools.",
        style="Muted.TLabel", wraplength=1080,
    ).pack(anchor="w", fill="x", pady=(2, 0))

    labric_cloud_var = tk.BooleanVar(value=bool(cfg.get("scilink_labric_cloud_context_enabled", cfg.get("labric_mcp_enabled", False))))
    labric_box = ttk.LabelFrame(settings_tab, text="LabRIC MCP cloud context", padding=(10, 8))
    labric_box.pack(fill="x", pady=(0, 8))
    ttk.Checkbutton(
        labric_box,
        text="Use LabRIC MCP cloud context for cross-instrument comparison, prior-data analysis, and experiment planning",
        variable=labric_cloud_var,
    ).pack(anchor="w")
    ttk.Label(
        labric_box,
        text="When checked, FaradaIQ connects the package-local LabRIC stdio MCP bridge before SciLink begins. SciLink can query only the LabRIC data authorized by your API key. Configure and test the endpoint and key in LLM Settings → Data Sharing.",
        style="Muted.TLabel", wraplength=1080,
    ).pack(anchor="w", fill="x", pady=(2, 0))

    unsafe_execution_var = tk.BooleanVar(value=True)
    # Keep the Tcl variable alive for the lifetime of the review window so the
    # disabled always-on checkbox remains visibly checked and introspectable.
    win._v261_unsafe_execution_var = unsafe_execution_var
    auth_box = ttk.LabelFrame(settings_tab, text="Generated-code execution", padding=(10, 8))
    auth_box.pack(fill="x", pady=(0, 8))
    ttk.Checkbutton(
        auth_box,
        text="SciLink CLI generated-code execution is unlocked",
        variable=unsafe_execution_var, state="disabled",
    ).pack(anchor="w")
    ttk.Label(
        auth_box,
        text="FaradaIQ always sets UNSAFE_EXECUTION_OK=true only in this SciLink CLI subprocess. The exact scientific prompt and global Windows environment are not modified.",
        style="Muted.TLabel", wraplength=1080,
    ).pack(anchor="w", fill="x", pady=(2, 0))
    ttk.Label(settings_tab, text="Requested filenames (optional, one per line)", style="Card.TLabel").pack(anchor="w")
    filenames_box = ScrolledText(settings_tab, wrap="none", height=6)
    filenames_box.pack(fill="x", pady=(2, 8))
    details_box = ScrolledText(settings_tab, wrap="word", height=20)
    details_box.pack(fill="both", expand=True)
    details = cleanup._service_details_text("scilink", cfg)
    details += (
        "\n\nBrowser bundle behavior\n"
        "- The currently selected Browser files are copied into the new job's data folder.\n"
        "- A compact metadata index is generated; the large canonical JSON is not auto-selected.\n"
        "- SciLink starts with its working directory set to the job-local data folder.\n"
        "- The scientific prompt remains exactly the text visible in the Prompt tab.\n"
        "- Co-pilot mode pauses for operator answers instead of automatically rerunning after a question.\n"
        "- Completed SciLink HTML/HTM reports open automatically in the default browser."
    )
    details_box.insert("1.0", details)
    details_box.configure(state="disabled")

    request_box = ScrolledText(request_tab, wrap="word")
    request_box.pack(fill="both", expand=True)
    request_box.configure(state="disabled")

    def requested_names() -> list[str]:
        return [line.strip() for line in filenames_box.get("1.0", "end-1c").splitlines() if line.strip()]

    def visible_rows() -> list[dict[str, Any]]:
        return [entry for entry in file_rows if entry.get("visible")]

    def selected_items() -> list[dict[str, Any]]:
        return [dict(entry["item"]) for entry in file_rows if entry["var"].get()]

    def refresh_summary(*_args: Any) -> None:
        selected = [entry for entry in file_rows if entry["var"].get()]
        total = sum(int(entry["item"].get("size_bytes") or 0) for entry in selected)
        auto_bundle = any(entry["item"].get("generated") == "browser_selection_bundle" for entry in selected)
        suffix = " Current Browser bundle is selected." if auto_bundle else ""
        summary_var.set(
            f"Selected {len(selected)} of {len(file_rows)} item(s), {old._format_size(total)}. "
            f"Visible after filter: {len(visible_rows())}.{suffix}"
        )
        request_box.configure(state="normal")
        request_box.delete("1.0", "end")
        request_box.insert("1.0", json.dumps(_preview_request(source, selected_items(), requested_names()), ensure_ascii=False, indent=2))
        request_box.configure(state="disabled")

    def apply_filter(*_args: Any) -> None:
        query = search_var.get().strip().lower()
        role_filter = role_var.get()
        group_filter = group_var.get()
        type_filter = type_var.get()
        selected_only = bool(selected_only_var.get())
        for entry in file_rows:
            item = entry["item"]
            haystack = " ".join(str(item.get(key) or "") for key in ("role", "source_group", "path", "display_path", "name", "label", "generated")).lower()
            match = (
                (not query or query in haystack)
                and (role_filter == "All roles" or _candidate_role(item) == role_filter)
                and (group_filter == "All groups" or _candidate_group(item) == group_filter)
                and (type_filter == "All types" or _candidate_extension(item) == type_filter)
                and (not selected_only or bool(entry["var"].get()))
            )
            entry["visible"] = bool(match)
            entry["row"].grid() if match else entry["row"].grid_remove()
        refresh_summary()

    def set_rows(rows: Sequence[dict[str, Any]], value: bool) -> None:
        for entry in rows:
            entry["var"].set(value)
        apply_filter()

    ttk.Button(actions, text="Select visible", command=lambda: set_rows(visible_rows(), True)).pack(side="right", padx=(4, 0))
    ttk.Button(actions, text="Clear visible", command=lambda: set_rows(visible_rows(), False)).pack(side="right", padx=(4, 0))
    ttk.Button(actions, text="Select all", command=lambda: set_rows(file_rows, True)).pack(side="right", padx=(4, 0))
    ttk.Button(actions, text="Clear all", command=lambda: set_rows(file_rows, False)).pack(side="right", padx=(4, 0))
    for var in (search_var, role_var, group_var, type_var, selected_only_var):
        try:
            var.trace_add("write", apply_filter)
        except Exception:
            pass
    for entry in file_rows:
        try:
            entry["var"].trace_add("write", refresh_summary)
        except Exception:
            pass
    filenames_box.bind("<KeyRelease>", refresh_summary)
    apply_filter()

    footer = ttk.Frame(outer, style="Card.TFrame")
    footer.pack(fill="x", pady=(8, 0))
    status_var = tk.StringVar(value="Nothing starts until you approve. The Browser bundle is selected automatically, literature and LabRIC cloud-context choices are reviewed separately, SciLink waits for your answers, and CLI code execution is unlocked for the launched process.")
    ttk.Label(footer, textvariable=status_var, style="Muted.TLabel").pack(side="left", fill="x", expand=True)

    def start() -> None:
        exact_prompt = prompt_box.get("1.0", "end-1c")
        if not exact_prompt.strip():
            messagebox.showwarning("Prompt required", "Enter the SciLink prompt before starting.", parent=win)
            return
        selected = selected_items()
        preview = _preview_request(source, selected, requested_names())
        if not preview.get("selected_data_paths"):
            messagebox.showwarning(
                "Experimental data required",
                "Select at least one Browser data file or keep the automatically generated current-selection bundle checked before starting SciLink.",
                parent=win,
            )
            tabs.select(files_tab)
            return
        status_var.set("Creating the job-local SciLink data bundle...")
        try:
            job = write_scilink_job(
                source,
                exact_prompt,
                selected,
                cfg,
                mode=mode_var.get(),
                requested_filenames=requested_names(),
                unsafe_execution_ok=True,
                literature_analysis_enabled=bool(literature_analysis_var.get()),
                labric_cloud_context_enabled=bool(labric_cloud_var.get()),
                wait_for_user_questions=bool(wait_for_user_var.get()),
            )
            if not job["manifest"].get("selected_data_paths"):
                raise RuntimeError("The reviewed SciLink bundle did not produce any readable experimental data files.")
            win.destroy()
            reliability._launch_scilink_transaction(source, job, cfg)
        except Exception as exc:
            messagebox.showerror("Could not start SciLink", f"{exc}\n\n{traceback.format_exc()}", parent=win)

    ttk.Button(footer, text="Start new SciLink job", style="Accent.TButton", command=start).pack(side="right", padx=(6, 0))
    ttk.Button(footer, text="Cancel", command=win.destroy).pack(side="right")
    return win


def _inject_frame_controls(path: Path, payload: Mapping[str, Any]) -> None:
    """Inject a drag-safe manual frame slider and deterministic playback controls."""
    if not payload.get("frame_names"):
        return
    try:
        html = path.read_text(encoding="utf-8")
    except Exception:
        return
    if "faradaiq-frame-dock-v261" in html:
        return
    # Remove the immediately preceding fixed dock so there is only one control surface.
    if "faradaiq-frame-dock-v255" in html:
        start = html.find('<div id="faradaiq-frame-dock-v255"')
        end = html.find('</style>', start)
        if start >= 0 and end >= 0:
            html = html[:start] + html[end + len('</style>'):]
        script_start = html.find('<script id="faradaiq-frame-script-v255"')
        script_end = html.find('</script>', script_start)
        if script_start >= 0 and script_end >= 0:
            html = html[:script_start] + html[script_end + len('</script>'):]

    payload_json = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
    dock = r'''
<div id="faradaiq-frame-dock-v261" role="group" aria-label="Hyperspectral frame controls">
  <button id="faradaiq-frame-play-v261" type="button">▶ Play</button>
  <button id="faradaiq-frame-pause-v261" type="button">❚❚ Pause</button>
  <button id="faradaiq-frame-prev-v261" type="button" title="Previous frame">◀ Previous</button>
  <label id="faradaiq-frame-caption-v261" for="faradaiq-frame-range-v261">Frame</label>
  <input id="faradaiq-frame-range-v261" type="range" min="0" max="0" value="0" step="1" aria-label="Drag to choose hyperspectral frame">
  <button id="faradaiq-frame-next-v261" type="button" title="Next frame">Next ▶</button>
  <output id="faradaiq-frame-value-v261">1 / 1</output>
</div>
<style id="faradaiq-frame-style-v261">
html,body{min-height:100%;}body{padding-bottom:76px!important;box-sizing:border-box;}
#faradaiq-frame-dock-v261{position:fixed;left:0;right:0;bottom:0;z-index:2147483000;display:grid;grid-template-columns:auto auto auto minmax(150px,auto) minmax(280px,1fr) auto auto;align-items:center;gap:8px;padding:9px 12px;border-top:1px solid #9ba9bc;background:rgba(247,249,252,.99);box-shadow:0 -2px 10px rgba(20,42,70,.18);color:#17365d;font:14px/1.25 Arial,sans-serif;box-sizing:border-box;pointer-events:auto}
#faradaiq-frame-dock-v261 button{font:inherit;padding:6px 10px;border:1px solid #9ba9bc;border-radius:4px;background:#fff;color:#17365d;cursor:pointer;font-weight:600;pointer-events:auto}
#faradaiq-frame-dock-v261 button:disabled{opacity:.55;cursor:default}
#faradaiq-frame-range-v261{display:block;width:100%;min-width:220px;height:28px;accent-color:#315b8a;cursor:grab;pointer-events:auto;touch-action:none;position:relative;z-index:2147483001}
#faradaiq-frame-range-v261:active{cursor:grabbing}
#faradaiq-frame-caption-v261{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}
#faradaiq-frame-value-v261{white-space:nowrap;min-width:76px;text-align:right}
@media(max-width:900px){#faradaiq-frame-dock-v261{grid-template-columns:auto auto auto 1fr auto auto}#faradaiq-frame-caption-v261{grid-column:1/-1;grid-row:1}.plotly-graph-div{max-width:100%!important}}
</style>
'''
    script = r'''
<script id="faradaiq-frame-script-v261">
(function(){
  const payload=__PAYLOAD__,names=Array.isArray(payload.frame_names)?payload.frame_names:[],labels=Array.isArray(payload.frame_labels)?payload.frame_labels:[];
  const range=document.getElementById('faradaiq-frame-range-v261'),caption=document.getElementById('faradaiq-frame-caption-v261'),value=document.getElementById('faradaiq-frame-value-v261');
  const play=document.getElementById('faradaiq-frame-play-v261'),pause=document.getElementById('faradaiq-frame-pause-v261');
  const prev=document.getElementById('faradaiq-frame-prev-v261'),next=document.getElementById('faradaiq-frame-next-v261');
  let index=0,playing=false,token=0,dragging=false,lastRendered=-1;
  function graph(){return document.querySelector('.plotly-graph-div');}
  function finiteZ(trace){if(!trace||trace.z==null)return false;const stack=[trace.z];while(stack.length){const item=stack.pop();if(Array.isArray(item)){for(let i=0;i<item.length;i++)stack.push(item[i]);}else if(Number.isFinite(Number(item)))return true;}return false;}
  function clamp(i){return Math.max(0,Math.min(names.length-1,Number(i)||0));}
  function sync(rendered){index=clamp(index);range.value=String(index);caption.textContent=labels[index]||('Frame '+(index+1));value.textContent=(index+1)+' / '+names.length+(dragging?' • release to render':'');play.disabled=playing||names.length<2;pause.disabled=!playing;prev.disabled=names.length<2;next.disabled=names.length<2;if(rendered)lastRendered=index;}
  function ensureSpatialLayer(gd){
    try{
      const visible=(gd.data||[]).some(t=>['contour','heatmap','surface','mesh3d'].includes(String(t.type||'').toLowerCase())&&finiteZ(t));
      if(!visible){
        const frames=(gd._transitionData&&gd._transitionData._frames)||[],frame=frames.find(f=>String(f.name||'')===String(names[index]||''));
        if(frame&&frame.data){const jobs=[];frame.data.forEach((t,i)=>{if(['contour','heatmap','surface','mesh3d'].includes(String(t.type||'').toLowerCase())&&finiteZ(t))jobs.push(Plotly.restyle(gd,{visible:true,z:[t.z]},[i]));});if(jobs.length)return Promise.all(jobs).then(()=>Plotly.redraw(gd));}
      }
      return Plotly.redraw(gd);
    }catch(_){return Promise.resolve();}
  }
  function announce(gd){const name=String(names[index]||'');try{gd._faradaiqActiveFrameName=name;if(gd.dataset)gd.dataset.faradaiqFrameName=name;}catch(_){}try{gd.dispatchEvent(new CustomEvent('faradaiq_frame_changed',{detail:{name:name,index:index,label:labels[index]||''}}));}catch(_){}}
  function show(i){
    const gd=graph();if(!gd||typeof Plotly==='undefined'||!names.length)return Promise.resolve();
    index=clamp(i);dragging=false;sync(false);
    return Plotly.animate(gd,[names[index]],{mode:'immediate',frame:{duration:0,redraw:true},transition:{duration:0}})
      .then(()=>ensureSpatialLayer(gd)).then(()=>{sync(true);announce(gd);})
      .catch(()=>{try{return Plotly.redraw(gd).then(()=>{sync(true);announce(gd);});}catch(_){sync(true);announce(gd);}});
  }
  function stop(){playing=false;token++;sync(false);}
  function run(my){if(!playing||my!==token)return;const target=(index+1)%names.length;show(target).finally(()=>{if(playing&&my===token)setTimeout(()=>run(my),Math.max(80,Number(payload.frame_duration_ms)||280));});}
  function beginDrag(){stop();dragging=true;index=clamp(range.value);sync(false);}
  function previewDrag(){dragging=true;index=clamp(range.value);sync(false);}
  function commitDrag(){const target=clamp(range.value);dragging=false;if(target===lastRendered){index=target;sync(true);return;}show(target);}
  function start(){
    const gd=graph();if(!gd||typeof Plotly==='undefined'){setTimeout(start,60);return;}
    range.max=String(Math.max(0,names.length-1));sync(false);show(0);
    range.addEventListener('pointerdown',beginDrag);range.addEventListener('mousedown',beginDrag);range.addEventListener('touchstart',beginDrag,{passive:true});
    range.addEventListener('input',previewDrag);
    range.addEventListener('change',commitDrag);range.addEventListener('pointerup',commitDrag);range.addEventListener('mouseup',commitDrag);range.addEventListener('touchend',commitDrag);
    range.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowDown','ArrowRight','ArrowUp','Home','End','PageUp','PageDown'].includes(e.key)){stop();setTimeout(()=>show(Number(range.value)),0);}});
    prev.addEventListener('click',()=>{stop();show(index-1);});next.addEventListener('click',()=>{stop();show(index+1);});
    play.addEventListener('click',()=>{if(names.length<2)return;playing=true;const my=++token;sync(false);run(my);});pause.addEventListener('click',stop);
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
</script>
'''.replace('__PAYLOAD__', payload_json)
    if '<body>' in html:
        html = html.replace('<body>', '<body>' + dock, 1)
    else:
        html = dock + html
    if '</body>' in html:
        html = html.replace('</body>', script + '</body>', 1)
    else:
        html += script
    path.write_text(html, encoding="utf-8")


def _install_visual_patch() -> None:
    import faradaiq.v253_visual_browser as visual
    import faradaiq.v254_contour_prerender as prerender

    visual.VERSION = VERSION
    prerender.VERSION = VERSION
    visual._inject_fixed_frame_controls = _inject_frame_controls
    prerender._CACHE_SCHEMA = "faradaiq-contour-prerender-cache-v261"
    prerender._CACHE_GENERATION = 258

    def cache_root(browser: Any, md: Any) -> Path:
        root = Path(md._view_dir(browser)) / "contour_prerender_cache_v261"
        root.mkdir(parents=True, exist_ok=True)
        return root

    prerender._cache_root = cache_root


def _install_review_patch() -> None:
    global _PREVIOUS_REVIEW
    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup
    import faradaiq.v245_queue_scilink as minimal
    import faradaiq.v250_reliability as reliability

    if _PREVIOUS_REVIEW is None:
        _PREVIOUS_REVIEW = reliability.show_analysis_review
    reliability.show_analysis_review = show_analysis_review
    old.show_analysis_review = show_analysis_review
    cleanup.show_analysis_review = show_analysis_review
    minimal.show_analysis_review = show_analysis_review
    minimal._write_minimal_scilink_job = write_scilink_job
    _CORE["show_analysis_review"] = show_analysis_review
    _CORE["FARADAIQ_SCILINK_BROWSER_BUNDLE_AUTO_SELECTED"] = True
    _CORE["FARADAIQ_SCILINK_JOB_LOCAL_DATA"] = True
    _CORE["FARADAIQ_SCILINK_UNSAFE_EXECUTION_SESSION_OVERRIDE"] = True
    _CORE["FARADAIQ_SCILINK_UNSAFE_EXECUTION_ALWAYS_ON"] = True
    _CORE["FARADAIQ_SCILINK_LITERATURE_CHECKBOX"] = True
    _CORE["FARADAIQ_SCILINK_LABRIC_CLOUD_CONTEXT_CHECKBOX"] = True
    _CORE["FARADAIQ_SCILINK_INTERACTIVE_SESSION"] = True


def _normalize_titles() -> None:
    for cls_name in ("SessionReaderGUI", "FaradaIQH5Browser", "FaradaIQDataBrowser"):
        cls = _g(cls_name)
        if cls is None or getattr(cls, "_v261_title_patch", False):
            continue
        old_title = getattr(cls, "title", None)
        if not callable(old_title):
            continue

        def title(self: Any, text: Any = None, _old=old_title) -> Any:
            if text is None:
                return _old(self)
            return _old(self, re.sub(r"v\d+(?:\.\d+)?", VERSION, str(text)))

        cls.title = title
        cls.wm_title = title
        cls._v261_title_patch = True


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE
    _CORE = core
    modules = []
    try:
        from faradaiq import (
            multidim_v216, v228_intelligence, v239_scilink_edison, v244_workflow_cleanup,
            v245_queue_scilink, v247_experience, v250_reliability, v251_context_scilink,
            v253_llm_qa, v253_visual_browser, v254_contour_prerender, v255_browser_color,
        )
        modules = [
            multidim_v216, v228_intelligence, v239_scilink_edison, v244_workflow_cleanup,
            v245_queue_scilink, v247_experience, v250_reliability, v251_context_scilink,
            v253_llm_qa, v253_visual_browser, v254_contour_prerender, v255_browser_color,
        ]
    except Exception:
        pass
    for module in modules:
        try:
            module.VERSION = VERSION
        except Exception:
            pass
    _install_visual_patch()
    _install_review_patch()
    _normalize_titles()
    core["FARADAIQ_MANUAL_FRAME_SLIDER"] = True
    core["FARADAIQ_MANUAL_FRAME_RENDER_ON_RELEASE"] = True
    core["FARADAIQ_CONTOUR_CACHE_GENERATION"] = 258
    core["FARADAIQ_SCILINK_CONTEXT_POLICY"] = "sanitized_operator_description_plus_job_local_validated_data_structure"
    core["FARADAIQ_SCILINK_CURVE_DATA_CONTRACT"] = "N x 2 potential_V,current_A for CV/LSV"
    core["FARADAIQ_SCILINK_DEPENDENCY_AUTO_REPAIR"] = True
    core["FARADAIQ_SCILINK_EDISON_AVAILABLE_ALL_MODES"] = True
    core["FARADAIQ_SCILINK_LITERATURE_ANALYSIS_DEFAULT"] = True
    core["FARADAIQ_SCILINK_LABRIC_MCP_PACKAGE_LOCAL"] = True
    core["FARADAIQ_SCILINK_WAIT_FOR_USER_DEFAULT"] = True
    core["FARADAIQ_SCILINK_AUTO_OPEN_HTML_REPORTS"] = True
