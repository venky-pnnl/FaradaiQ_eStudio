"""Out-of-process FaradaIQ Tafel worker.

The worker keeps nonlinear optimization, file parsing, and Matplotlib output away
from Tk's event loop. It emits one JSON object per stdout line for live progress.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List


def emit(event: str, **payload: Any) -> None:
    row = {"event": event, "time": time.time(), **payload}
    print(json.dumps(row, default=str), flush=True)


def atomic_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def run(job_file: Path) -> int:
    import pandas as pd
    import faradaiq_tafel_adapter as tafel

    request = json.loads(job_file.read_text(encoding="utf-8"))
    paths = [Path(x) for x in request.get("paths", [])]
    spatial_metadata = dict(request.get("spatial_metadata") or {})

    def spatial_for(path: Path) -> Dict[str, Any]:
        try:
            import os
            key = os.path.normcase(str(path.expanduser().resolve()))
        except Exception:
            key = str(path)
        item = spatial_metadata.get(key) or spatial_metadata.get(str(path)) or {}
        return dict(item) if isinstance(item, dict) else {}
    output_root = Path(request.get("output_root") or job_file.parent)
    output_root.mkdir(parents=True, exist_ok=True)
    settings_data: Dict[str, Any] = dict(request.get("settings") or {})
    # JSON converts tuples to lists; dataclass accepts them, but restore stable tuple semantics.
    for key in ("eta_scan_windows_mV", "manual_cathodic_range_V", "manual_anodic_range_V"):
        if isinstance(settings_data.get(key), list):
            settings_data[key] = tuple(settings_data[key])
    settings = tafel.TafelSettings(**settings_data)
    result_json = Path(request.get("result_json") or job_file.with_name(job_file.stem + "_results.json"))
    total = len(paths)
    emit("started", total_files=total, optimizer_mode=settings.optimizer_mode)
    results: List[Any] = []
    failures: List[Dict[str, Any]] = []
    started = time.monotonic()
    for index, path in enumerate(paths, start=1):
        emit("file_started", index=index, total=total, source=str(path), progress=(index - 1) / max(1, total) * 100.0)
        file_started = time.monotonic()
        try:
            variants = tafel.analyze_file_variants(path, output_root, settings)
        except Exception as exc:
            elapsed = time.monotonic() - file_started
            failure = {
                "source": str(path),
                "message": str(exc),
                "error_type": type(exc).__name__,
                "traceback": traceback.format_exc(),
                "elapsed_s": elapsed,
            }
            failures.append(failure)
            emit(
                "file_failed", index=index, total=total, source=str(path),
                message=str(exc), error_type=type(exc).__name__, elapsed_s=elapsed,
                progress=index / max(1, total) * 100.0, traceback=failure["traceback"],
            )
            continue
        results.extend(variants)
        elapsed = time.monotonic() - file_started
        overall = index / max(1, total) * 100.0
        emit(
            "file_completed", index=index, total=total, source=str(path), variants=len(variants),
            elapsed_s=elapsed, progress=overall,
            output_dirs=[x.output_dir for x in variants],
        )
    combined_dir = output_root / "tafel_analysis_combined"
    combined_dir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    for result in results:
        spatial = spatial_for(Path(result.source_file))
        for row in result.comparison_rows:
            item = dict(row)
            item["source_file"] = result.source_file
            item["result_folder"] = result.output_dir
            item.update(spatial)
            rows.append(item)
    combined_csv = combined_dir / "FaradaIQ_Tafel_all_selected_method_comparison.csv"
    if rows:
        pd.DataFrame(rows).to_csv(combined_csv, index=False)
    payload = {
        "schema": "faradaiq-tafel-worker-v237",
        "spatial_extension": "v297",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "job_file": str(job_file),
        "settings": asdict(settings),
        "combined_csv": str(combined_csv) if rows else "",
        "elapsed_s": time.monotonic() - started,
        "results": [{**asdict(x), "spatial": spatial_for(Path(x.source_file))} for x in results],
        "failures": failures,
        "failed_file_count": len(failures),
        "successful_file_count": total - len(failures),
    }
    atomic_json(result_json, payload)
    if results:
        emit(
            "completed", result_json=str(result_json), result_count=len(results),
            failed_file_count=len(failures), elapsed_s=payload["elapsed_s"], progress=100.0,
        )
        return 0
    emit(
        "failed", message=f"Tafel analysis produced no usable results ({len(failures)}/{total} source files failed).",
        result_json=str(result_json), failed_file_count=len(failures), progress=100.0,
    )
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    try:
        return run(Path(args.job))
    except KeyboardInterrupt:
        emit("cancelled", message="Tafel worker cancelled")
        return 130
    except Exception as exc:
        emit("failed", message=str(exc), traceback=traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
