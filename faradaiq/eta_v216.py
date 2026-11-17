"""Persistent, feature-aware ETA learning for FaradaIQ Studio v273."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

VERSION = "v273"


def runtime_dir() -> Path:
    raw = os.environ.get("FARADAIQ_RUNTIME_STATS_DIR", "").strip()
    d = Path(raw) if raw else Path(__file__).resolve().parents[1] / "FaradaIQ_runtime_estimates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def history_path() -> Path:
    return runtime_dir() / "processing_runtime_history_v216.jsonl"


def summary_path() -> Path:
    return runtime_dir() / "processing_runtime_summary_v216.json"


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def parser_kind(detail: Any = "") -> str:
    d = str(detail or "").lower()
    if "headless_pydrt" in d or "pydrt" in d or re.search(r"\bdrt\b", d): return "DRT"
    if "parse_eis" in d or "fulleis" in d or re.search(r"\beis\b", d): return "EIS"
    if "parse_cv" in d or "current_vs_potential" in d or re.search(r"\bcv\b", d): return "CV"
    if "parse_ca" in d or "fullca" in d or re.search(r"\bca\b", d): return "CA"
    if "parse_cp" in d or "fullcp" in d or re.search(r"\bcp\b", d): return "CP"
    if "parse_iv" in d or "tafel" in d or re.search(r"\biv\b", d): return "IV_Tafel"
    if "parse_ocp" in d or re.search(r"\bocp\b", d): return "OCP"
    if "bundle" in d or "export" in d: return "EXPORT"
    if "media" in d or "image" in d or "photo" in d or "video" in d: return "MEDIA"
    return "parser_other"


def _is_network(path: Path) -> bool:
    s = str(path)
    return s.startswith("\\\\") or s.startswith("//") or "://" in s


def _safe_stat(path: Path) -> Tuple[int, int]:
    """Return file_count,total_bytes for a file or a bounded recursive folder."""
    try:
        if path.is_file():
            return 1, int(path.stat().st_size)
        if path.is_dir():
            count = 0; total = 0
            for p in path.rglob("*"):
                if p.is_file():
                    try:
                        count += 1; total += int(p.stat().st_size)
                    except Exception:
                        pass
                    if count >= 20000:
                        break
            return count, total
    except Exception:
        pass
    return 0, 0


def _row_count(path: Path, max_files: int = 40) -> int:
    files: List[Path] = []
    try:
        if path.is_file() and path.suffix.lower() in {".csv", ".txt", ".tsv", ".asc"}: files = [path]
        elif path.is_dir(): files = [p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in {".csv", ".txt", ".tsv", ".asc"}][:max_files]
    except Exception:
        return 0
    rows = 0
    for p in files:
        try:
            with p.open("rb") as f:
                # Count newline bytes in bounded chunks. Exact for ordinary text tables.
                for chunk_i in range(32):
                    chunk = f.read(1024 * 1024)
                    if not chunk: break
                    rows += chunk.count(b"\n")
        except Exception:
            pass
    return int(rows)


def _extract_paths(args: Sequence[Any], cwd: Any = None) -> List[Path]:
    out: List[Path] = []
    base = Path(cwd) if cwd else Path.cwd()
    for raw in args:
        s = str(raw)
        if not s or s.startswith("-"):
            continue
        try:
            p = Path(s)
            if not p.is_absolute(): p = base / p
            if p.exists(): out.append(p.resolve())
        except Exception:
            pass
    # De-duplicate while retaining order.
    seen = set(); final = []
    for p in out:
        key = str(p).lower()
        if key not in seen: seen.add(key); final.append(p)
    return final


def command_profile(args: Sequence[Any], cwd: Any = None, index: int = 0) -> Dict[str, Any]:
    args_s = [str(x) for x in args]
    detail = " ".join(args_s)
    kind = parser_kind(detail)
    paths = _extract_paths(args_s, cwd)
    file_count = 0; total_bytes = 0; rows = 0
    extensions: Dict[str, int] = {}
    network = False
    for p in paths:
        c, b = _safe_stat(p); file_count += c; total_bytes += b
        rows += _row_count(p)
        network = network or _is_network(p)
        if p.is_file(): extensions[p.suffix.lower() or "<none>"] = extensions.get(p.suffix.lower() or "<none>", 0) + 1
    mb = total_bytes / (1024.0 * 1024.0)
    if mb < 1: bucket = "tiny_lt1MB"
    elif mb < 10: bucket = "small_1_10MB"
    elif mb < 100: bucket = "medium_10_100MB"
    elif mb < 1000: bucket = "large_100MB_1GB"
    else: bucket = "huge_ge1GB"
    script = ""
    for token in args_s:
        if token.lower().endswith(".py"):
            script = Path(token).name; break
    flags = [x for x in args_s if x.startswith("--")]
    normalized = "|".join([kind, script.lower(), *sorted(flags)])
    fingerprint = hashlib.sha1(normalized.encode("utf-8", errors="replace")).hexdigest()[:14]
    point_tokens = set(re.findall(r"(?i)(?:point|position|pos|pt)[_\- ]*0*(\d+)", detail))
    method_tokens = set(re.findall(r"(?i)(?:method|m)[_\- ]*0*(\d+)", detail))

    def count_bucket(value: int, edges: Sequence[int], labels: Sequence[str]) -> str:
        for edge, label in zip(edges, labels):
            if value <= edge:
                return label
        return labels[-1]

    file_bucket = count_bucket(int(file_count), [1, 4, 16, 64, 256, 10**9], ["1", "2_4", "5_16", "17_64", "65_256", "gt256"])
    row_bucket = count_bucket(int(rows), [1_000, 10_000, 100_000, 1_000_000, 10_000_000, 10**18], ["lt1k", "1k_10k", "10k_100k", "100k_1m", "1m_10m", "gt10m"])
    point_bucket = count_bucket(len(point_tokens), [0, 1, 8, 32, 128, 10**9], ["0", "1", "2_8", "9_32", "33_128", "gt128"])
    method_bucket = count_bucket(len(method_tokens), [0, 1, 4, 16, 64, 10**9], ["0", "1", "2_4", "5_16", "17_64", "gt64"])
    extension_signature = "+".join(f"{k}:{v}" for k, v in sorted(extensions.items())) or "none"
    storage_kind = "network" if network else "local"
    shape_key = f"{kind}|files={file_bucket}|rows={row_bucket}|points={point_bucket}|methods={method_bucket}|storage={storage_kind}"
    return {
        "index": int(index), "kind": kind, "script": script, "fingerprint": fingerprint,
        "normalized_command": normalized, "cwd": str(cwd or ""),
        "input_paths": [str(p) for p in paths[:50]], "file_count": int(file_count),
        "total_bytes": int(total_bytes), "total_MiB": round(mb, 4), "row_count_sampled": int(rows),
        "size_bucket": bucket, "file_count_bucket": file_bucket, "row_count_bucket": row_bucket,
        "point_count_bucket": point_bucket, "method_count_bucket": method_bucket,
        "network_path": bool(network), "storage_kind": storage_kind, "extensions": extensions,
        "extension_signature": extension_signature, "shape_key": shape_key,
        "point_token_count": len(point_tokens), "method_token_count": len(method_tokens),
    }


def profiles_sidecar(progress_file: Path) -> Path:
    return Path(progress_file).with_name("_faradaiq_eta_command_profiles_v216.json")


def write_profiles(progress_file: Path, commands: Sequence[Tuple[Sequence[Any], Any]]) -> Path:
    profiles = [command_profile(args, cwd, i) for i, (args, cwd) in enumerate(commands, 1)]
    payload = {"schema": "faradaiq-eta-command-profiles-v216", "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "profiles": profiles}
    p = profiles_sidecar(Path(progress_file)); p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def read_profiles(progress_file: Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(profiles_sidecar(Path(progress_file)).read_text(encoding="utf-8"))
        return [dict(x) for x in data.get("profiles", [])]
    except Exception:
        return []


def _read_summary() -> Dict[str, Any]:
    try:
        data = json.loads(summary_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_summary(data: Mapping[str, Any]) -> None:
    p = summary_path(); tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"); tmp.replace(p)


def _describe(values: Sequence[float]) -> Dict[str, Any]:
    vals = [float(v) for v in values if math.isfinite(float(v)) and float(v) > 0][-100:]
    if not vals: return {"count": 0, "recent": []}
    vals_sorted = sorted(vals)
    q = lambda p: vals_sorted[min(len(vals_sorted)-1, max(0, int(round((len(vals_sorted)-1)*p))))]
    med = statistics.median(vals)
    mean = statistics.fmean(vals)
    ewma = vals[0]
    for v in vals[1:]: ewma = 0.25 * v + 0.75 * ewma
    return {
        "count": len(vals), "mean": round(mean, 4), "median": round(med, 4),
        "p25": round(q(0.25), 4), "p75": round(q(0.75), 4), "p90": round(q(0.90), 4),
        "ewma": round(ewma, 4), "recent": [round(v, 4) for v in vals[-100:]],
    }


def _describe_nonnegative(values: Sequence[float]) -> Dict[str, Any]:
    vals = [float(v) for v in values if math.isfinite(float(v)) and float(v) >= 0][-100:]
    if not vals:
        return {"count": 0, "recent": []}
    vals_sorted = sorted(vals)
    q = lambda p: vals_sorted[min(len(vals_sorted)-1, max(0, int(round((len(vals_sorted)-1)*p))))]
    med = statistics.median(vals)
    mean = statistics.fmean(vals)
    ewma = vals[0]
    for v in vals[1:]:
        ewma = 0.25 * v + 0.75 * ewma
    return {
        "count": len(vals), "mean": round(mean, 4), "median": round(med, 4),
        "p25": round(q(0.25), 4), "p75": round(q(0.75), 4), "p90": round(q(0.90), 4),
        "ewma": round(ewma, 4), "recent": [round(v, 4) for v in vals[-100:]],
    }


def record(profile: Mapping[str, Any], seconds: float, returncode: Optional[int] = None, detail: str = "", predicted_seconds: Optional[float] = None) -> None:
    seconds = float(seconds)
    if not math.isfinite(seconds) or seconds <= 0 or seconds > 7 * 86400: return
    row = dict(profile)
    predicted = None
    try:
        predicted = float(predicted_seconds) if predicted_seconds is not None and math.isfinite(float(predicted_seconds)) and float(predicted_seconds) > 0 else None
    except Exception:
        predicted = None
    row.update({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "version": VERSION,
        "duration_seconds": round(seconds, 4), "returncode": returncode, "detail": str(detail)[:500],
        "predicted_seconds": round(predicted, 4) if predicted is not None else None,
        "prediction_error_seconds": round(seconds - predicted, 4) if predicted is not None else None,
        "absolute_prediction_error_seconds": round(abs(seconds - predicted), 4) if predicted is not None else None,
        "absolute_prediction_error_fraction": round(abs(seconds - predicted) / max(seconds, 1e-9), 6) if predicted is not None else None,
        "seconds_per_MiB": round(seconds / max(float(profile.get("total_MiB") or 0), 1e-6), 6) if float(profile.get("total_MiB") or 0) > 0 else None,
        "seconds_per_file": round(seconds / max(int(profile.get("file_count") or 0), 1), 6),
        "seconds_per_1000_rows": round(seconds / max(float(profile.get("row_count_sampled") or 0) / 1000.0, 1e-6), 6) if float(profile.get("row_count_sampled") or 0) > 0 else None,
    })
    with history_path().open("a", encoding="utf-8") as f: f.write(json.dumps(row) + "\n")
    summary = _read_summary(); summary["schema"] = "faradaiq-processing-runtime-summary-v216"; summary["updated"] = row["timestamp"]
    for group_name, key in (
        ("by_kind", str(profile.get("kind") or "parser_other")),
        ("by_fingerprint", str(profile.get("fingerprint") or "unknown")),
        ("by_kind_shape", str(profile.get("shape_key") or "unknown")),
        ("by_kind_size", f"{profile.get('kind')}|{profile.get('size_bucket')}"),
        ("by_kind_network", f"{profile.get('kind')}|network={bool(profile.get('network_path'))}"),
        ("by_kind_extensions", f"{profile.get('kind')}|{profile.get('extension_signature') or 'none'}"),
    ):
        group = summary.setdefault(group_name, {}).setdefault(key, {})
        durations = list(group.get("durations") or []) + [seconds]
        group["durations"] = [round(v, 4) for v in durations[-100:]]
        group["duration_stats"] = _describe(durations)
        success = returncode in (None, 0)
        group["success_count"] = int(group.get("success_count") or 0) + (1 if success else 0)
        group["failure_count"] = int(group.get("failure_count") or 0) + (0 if success else 1)
        if row.get("absolute_prediction_error_seconds") is not None:
            residuals = list(group.get("absolute_prediction_errors") or []) + [float(row["absolute_prediction_error_seconds"])]
            group["absolute_prediction_errors"] = [round(v, 4) for v in residuals[-100:]]
            group["prediction_error_stats"] = _describe_nonnegative(residuals)
        for field in ("seconds_per_MiB", "seconds_per_file", "seconds_per_1000_rows"):
            v = row.get(field)
            vals = list(group.get(field) or [])
            if v is not None and math.isfinite(float(v)): vals.append(float(v))
            group[field] = [round(x, 6) for x in vals[-100:]]
            group[field + "_stats"] = _describe(vals)
    _write_summary(summary)


_HEURISTIC = {"CV": 18.0, "EIS": 42.0, "DRT": 180.0, "CA": 22.0, "CP": 20.0, "IV_Tafel": 18.0, "OCP": 15.0, "EXPORT": 35.0, "MEDIA": 12.0, "parser_other": 30.0}


def _group_prediction(group: Mapping[str, Any]) -> Tuple[Optional[float], int, Optional[float]]:
    stats = group.get("duration_stats") or {}
    count = int(stats.get("count") or 0)
    if count <= 0: return None, 0, None
    pred = 0.55 * float(stats.get("median") or 0) + 0.30 * float(stats.get("ewma") or 0) + 0.15 * float(stats.get("mean") or 0)
    spread = max(0.0, float(stats.get("p75") or pred) - float(stats.get("p25") or pred))
    return pred, count, spread


def predict(profile: Mapping[str, Any]) -> Dict[str, Any]:
    summary = _read_summary(); kind = str(profile.get("kind") or "parser_other")
    candidates: List[Tuple[float, float, int, float, str]] = []
    lookups = [
        ("by_fingerprint", str(profile.get("fingerprint") or "unknown"), 0.40, "exact command"),
        ("by_kind_shape", str(profile.get("shape_key") or "unknown"), 0.25, "kind + file/row/point shape"),
        ("by_kind_size", f"{kind}|{profile.get('size_bucket')}", 0.16, "kind + size"),
        ("by_kind_network", f"{kind}|network={bool(profile.get('network_path'))}", 0.07, "kind + storage"),
        ("by_kind_extensions", f"{kind}|{profile.get('extension_signature') or 'none'}", 0.05, "kind + file types"),
        ("by_kind", kind, 0.07, "kind"),
    ]
    for group_name, key, weight, label in lookups:
        group = (summary.get(group_name) or {}).get(key) or {}
        pred, count, spread = _group_prediction(group)
        err_stats = group.get("prediction_error_stats") or {}
        calibrated_error = float(err_stats.get("p75") or err_stats.get("median") or 0.0) if int(err_stats.get("count") or 0) else 0.0
        if pred and pred > 0:
            candidates.append((pred, weight, count, max(spread or 0.0, calibrated_error), label))
    # Throughput-based estimates adapt to file size and row count.
    kind_group = (summary.get("by_kind") or {}).get(kind) or {}
    mb = float(profile.get("total_MiB") or 0); files = int(profile.get("file_count") or 0); rows = int(profile.get("row_count_sampled") or 0)
    for field, amount, weight, label in (
        ("seconds_per_MiB_stats", mb, 0.18, "MiB throughput"),
        ("seconds_per_file_stats", files, 0.10, "file throughput"),
        ("seconds_per_1000_rows_stats", rows / 1000.0, 0.10, "row throughput"),
    ):
        st = kind_group.get(field) or {}
        if amount > 0 and int(st.get("count") or 0) >= 2:
            rate = float(st.get("median") or 0)
            if rate > 0: candidates.append((rate * amount, weight, int(st.get("count") or 0), 0.0, label))
    if not candidates:
        base = _HEURISTIC.get(kind, 30.0)
        # Scale heuristics gently for exceptionally large input.
        if mb > 100: base *= min(6.0, 1.0 + math.log10(max(1.0, mb / 100.0)))
        return {"seconds": base, "uncertainty": base * 0.75, "confidence": "low", "basis": ["heuristic"], "samples": 0}
    total_w = sum(w for _, w, _, _, _ in candidates)
    seconds = sum(v * w for v, w, _, _, _ in candidates) / max(total_w, 1e-9)
    samples = max(c for _, _, c, _, _ in candidates)
    weighted_spread = sum(max(s, v * 0.15) * w for v, w, _, s, _ in candidates) / max(total_w, 1e-9)
    confidence = "high" if samples >= 12 else ("medium" if samples >= 4 else "low")
    return {"seconds": max(0.1, seconds), "uncertainty": max(1.0, weighted_spread), "confidence": confidence, "basis": [x[4] for x in candidates], "samples": samples}


def predict_remaining(profiles: Sequence[Mapping[str, Any]], completed: int, current_elapsed: float = 0.0) -> Dict[str, Any]:
    completed = max(0, int(completed)); preds = [predict(p) for p in profiles]
    remaining = preds[completed:]
    seconds = sum(p["seconds"] for p in remaining)
    uncertainty = math.sqrt(sum(float(p["uncertainty"]) ** 2 for p in remaining)) if remaining else 0.0
    if current_elapsed > 0 and completed < len(preds):
        expected = float(preds[completed]["seconds"])
        seconds = max(0.0, seconds - min(float(current_elapsed), expected * 0.9))
    samples = max([int(p.get("samples") or 0) for p in remaining] or [0])
    confidence = "high" if samples >= 12 else ("medium" if samples >= 4 else "low")
    return {"seconds": seconds, "uncertainty": uncertainty, "confidence": confidence, "samples": samples}


def format_eta(seconds: float, uncertainty: float = 0.0, confidence: str = "low") -> str:
    def fmt(v: float) -> str:
        v = max(0.0, float(v))
        if v < 60: return f"{int(round(v))} s"
        if v < 3600: return f"{int(v//60)} min {int(round(v%60))} s"
        return f"{v/3600:.1f} h"
    return f"ETA ~{fmt(seconds)} ± {fmt(uncertainty)} ({confidence} confidence)"
