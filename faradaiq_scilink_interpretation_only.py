#!/usr/bin/env python
"""SciLink interpretation-only host for deterministic electrochemistry.

This route intentionally does not instantiate SciLink's AnalysisOrchestratorAgent
and exposes no analysis-agent tools. FaradaIQ performs deterministic,
technique-specific electrochemistry first; the selected OpenAI-compatible model
then interprets the preserved raw-data context and deterministic results and
writes an HTML report. Interactive follow-up remains available.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

VERSION = "v290"
SCHEMA = "faradaiq-scilink-interpretation-only-v290"
BLOCK_CODE = "BLOCKED_GENERIC_ANALYSIS_FOR_ELECTROCHEMISTRY"
BLOCKED_TOOLS = [
    "select_agent", "run_analysis", "CurveFittingAgent", "Best-of-3", "Best-of-N",
    "adaptive refit", "annealing", "generic R² verification", "generic spectroscopy peak fitting",
]
SAFE_READ_ONLY_TOOLS = ["list_job_files", "read_job_file"]
DATA_EXTENSIONS = {".csv", ".txt", ".tsv", ".asc", ".dat", ".mpt", ".json", ".h5", ".hdf5"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig", errors="strict"))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_exact(path: str | Path, *, allowed_root: Path | None = None) -> Path:
    resolved = Path(path).resolve(strict=True)
    if allowed_root is not None:
        root = allowed_root.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Path is outside the authorized job-local root: {resolved}") from exc
    return resolved


def _series_metadata(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    payload = _load_json(_resolve_exact(path))
    series = payload.get("series_metadata") if isinstance(payload, Mapping) else None
    if not isinstance(series, Mapping):
        series = {}
    # Required transport: serialize and validate through structured JSON only.
    serialized = json.dumps(series, ensure_ascii=False)
    return json.loads(serialized)


def _uniform_indices(n: int, count: int) -> list[int]:
    if n <= 0:
        return []
    if n <= count:
        return list(range(n))
    return sorted({round(i * (n - 1) / (count - 1)) for i in range(count)})


def _text_raw_context(path: Path, *, max_sample_rows: int = 96) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    nonempty = [line for line in lines if line.strip()]
    indices = _uniform_indices(len(nonempty), max_sample_rows)
    samples = [{"line": i + 1, "text": nonempty[i][:2000]} for i in indices]
    return {
        "path": str(path),
        "name": path.name,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "line_count": len(lines),
        "nonempty_line_count": len(nonempty),
        "header": nonempty[0][:4000] if nonempty else "",
        "sampled_raw_rows": samples,
        "sampling": {"method": "uniform_preserve_endpoints", "maximum_rows": max_sample_rows},
    }


def _hdf5_raw_context(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path), "name": path.name, "sha256": _sha256(path),
        "size_bytes": path.stat().st_size, "opened_mode": "r", "read_only": True,
        "manifest_json": None, "tables": [], "metadata_members": [],
    }
    try:
        import h5py
        with h5py.File(path, "r") as h5:
            if "manifest_json" in h5:
                raw = h5["manifest_json"][()]
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    result["manifest_json"] = json.loads(str(raw))
                except Exception:
                    result["manifest_json"] = str(raw)[:10000]
            if "metadata" in h5:
                result["metadata_members"] = list(h5["metadata"].keys())[:500]
            if "tables" in h5:
                for name, ds in h5["tables"].items():
                    if not hasattr(ds, "shape"):
                        continue
                    columns_raw = ds.attrs.get("columns_json", "[]")
                    record_raw = ds.attrs.get("record_json", "{}")
                    if isinstance(columns_raw, bytes):
                        columns_raw = columns_raw.decode("utf-8", errors="replace")
                    if isinstance(record_raw, bytes):
                        record_raw = record_raw.decode("utf-8", errors="replace")
                    try: columns = json.loads(str(columns_raw))
                    except Exception: columns = []
                    try: record = json.loads(str(record_raw))
                    except Exception: record = {}
                    arr = ds[()]
                    sample = []
                    try:
                        rows = arr.tolist()
                        for idx in _uniform_indices(len(rows), 32):
                            sample.append({"row": idx, "values": rows[idx]})
                    except Exception:
                        pass
                    result["tables"].append({
                        "dataset_path": f"/tables/{name}", "shape": list(ds.shape),
                        "dtype": str(ds.dtype), "columns_json": columns,
                        "record_json": record, "sampled_raw_rows": sample,
                    })
    except Exception as exc:
        result["read_error"] = str(exc)
    return result


def build_raw_context(data_dir: Path, *, output_path: Path) -> dict[str, Any]:
    root = _resolve_exact(data_dir)
    files: list[dict[str, Any]] = []
    exact_paths: list[str] = []
    for candidate in sorted(root.rglob("*"), key=lambda p: str(p).lower()):
        if not candidate.is_file() or candidate.suffix.lower() not in DATA_EXTENSIONS:
            continue
        path = _resolve_exact(candidate, allowed_root=root)
        exact_paths.append(str(path))
        if path.suffix.lower() in {".h5", ".hdf5"}:
            files.append(_hdf5_raw_context(path))
        elif path.stat().st_size <= 20_000_000:
            files.append(_text_raw_context(path))
        else:
            files.append({
                "path": str(path), "name": path.name, "sha256": _sha256(path),
                "size_bytes": path.stat().st_size, "sampling": "omitted_large_file",
            })
    payload = {
        "schema": "faradaiq-raw-electrochemistry-context-v290",
        "version": VERSION,
        "created_at": _now(),
        "data_root": str(root),
        "exact_paths_resolved_strict": True,
        "path_reconstruction_by_model_allowed": False,
        "raw_data_modified": False,
        "file_count": len(files),
        "exact_file_paths": exact_paths,
        "files": files,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


def _normalize_base_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("OpenAI-compatible base URL is empty")
    if base.endswith("/chat/completions"):
        return base
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/chat/completions"


def _resolve_api_key() -> str:
    for name in ("SCILINK_API_KEY", "OPENAI_API_KEY", "OLLAMA_API_KEY", "LITELLM_KEY", "CUSTOM_LLM_API_KEY"):
        value = os.environ.get(name)
        if value:
            return value
    return "ollama"


def _safe_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "list_job_files",
                "description": "List exact authorized job-local files. Read-only. Does not analyze or modify data.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_job_file",
                "description": "Read an exact job-local text/JSON/CSV file by path and optional 1-based line range. Read-only.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def _execute_safe_tool(name: str, arguments: Mapping[str, Any], *, job_root: Path) -> str:
    if name in {"select_agent", "run_analysis", "CurveFittingAgent"} or name not in SAFE_READ_ONLY_TOOLS:
        raise RuntimeError(f"{BLOCK_CODE}: Tool prohibited by deterministic electrochemistry route")
    root = _resolve_exact(job_root)
    if name == "list_job_files":
        rows = []
        for path in sorted(root.rglob("*"), key=lambda p: str(p).lower()):
            if path.is_file():
                exact = _resolve_exact(path, allowed_root=root)
                rows.append({"path": str(exact), "relative_path": exact.relative_to(root).as_posix(), "size_bytes": exact.stat().st_size})
        return json.dumps({"job_root": str(root), "files": rows}, ensure_ascii=False)
    raw_path = str(arguments.get("path") or "")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    exact = _resolve_exact(candidate, allowed_root=root)
    if exact.suffix.lower() in {".h5", ".hdf5"}:
        return json.dumps(_hdf5_raw_context(exact), ensure_ascii=False, default=str)
    if exact.stat().st_size > 20_000_000:
        return json.dumps({"error": "file too large for direct text read", "path": str(exact), "size_bytes": exact.stat().st_size})
    text = exact.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    start = max(1, int(arguments.get("start_line") or 1))
    end = min(len(lines), int(arguments.get("end_line") or min(len(lines), start + 999)))
    if end < start:
        end = start
    selected = lines[start - 1:end]
    content = "\n".join(selected)
    if len(content) > 100_000:
        content = content[:100_000] + "\n[TRUNCATED AT 100000 CHARACTERS]"
    return json.dumps({
        "path": str(exact), "sha256": _sha256(exact), "start_line": start, "end_line": end,
        "total_lines": len(lines), "content": content,
    }, ensure_ascii=False)


@dataclass
class OpenAICompatibleClient:
    base_url: str
    model: str
    api_key: str
    timeout_s: int = 900

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)

    def complete(self, messages: list[dict[str, Any]], *, job_root: Path) -> tuple[str, int]:
        tool_call_count = 0
        for _round in range(20):
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.1,
                "stream": False,
                "tools": _safe_tool_definitions(),
                "tool_choice": "auto",
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                _normalize_base_url(self.base_url), data=data, method="POST",
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as response:
                    body = response.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"OpenAI-compatible HTTP {exc.code}: {detail[:4000]}") from exc
            except Exception as exc:
                raise RuntimeError(f"OpenAI-compatible request failed: {exc}") from exc
            obj = json.loads(body)
            choices = obj.get("choices") or []
            if not choices:
                raise RuntimeError(f"No completion choices returned: {body[:2000]}")
            message = choices[0].get("message") or {}
            tool_calls = message.get("tool_calls") or []
            if message.get("function_call"):
                raise RuntimeError(f"{BLOCK_CODE}: legacy function call is not permitted")
            if tool_calls:
                assistant_message = {"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls}
                messages.append(assistant_message)
                for call in tool_calls:
                    function = call.get("function") or {}
                    name = str(function.get("name") or "")
                    if name in BLOCKED_TOOLS or name not in SAFE_READ_ONLY_TOOLS:
                        raise RuntimeError(f"{BLOCK_CODE}: Tool prohibited by deterministic electrochemistry route: {name}")
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except Exception:
                        arguments = {}
                    result = _execute_safe_tool(name, arguments, job_root=job_root)
                    tool_call_count += 1
                    messages.append({"role": "tool", "tool_call_id": call.get("id") or f"safe_{tool_call_count}", "name": name, "content": result})
                continue
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(str(item.get("text") or item) if isinstance(item, Mapping) else str(item) for item in content)
            if not str(content or "").strip():
                raise RuntimeError("The interpretation-only response was empty")
            return str(content).strip(), tool_call_count
        raise RuntimeError("Interpretation-only read-tool loop exceeded 20 rounds without a final response")


def _markdown_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    in_code = False
    in_list = False
    for line in lines:
        if line.strip().startswith("```"):
            if in_list:
                out.append("</ul>"); in_list = False
            out.append("</code></pre>" if in_code else "<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line))
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            if in_list:
                out.append("</ul>"); in_list = False
            level = len(m.group(1))
            out.append(f"<h{level}>{html.escape(m.group(2))}</h{level}>")
            continue
        if re.match(r"^\s*[-*]\s+", line):
            if not in_list:
                out.append("<ul>"); in_list = True
            text = re.sub(r"^\s*[-*]\s+", "", line)
            out.append(f"<li>{html.escape(text)}</li>")
            continue
        if in_list:
            out.append("</ul>"); in_list = False
        if not line.strip():
            out.append("")
        else:
            escaped = html.escape(line)
            escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
            out.append(f"<p>{escaped}</p>")
    if in_list:
        out.append("</ul>")
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


def write_html_report(path: Path, *, title: str, response_text: str, provenance: Mapping[str, Any], conversation: list[dict[str, str]]) -> None:
    provenance_json = html.escape(json.dumps(provenance, ensure_ascii=False, indent=2, default=str))
    conversation_html = []
    for message in conversation:
        role = html.escape(message.get("role", ""))
        content = html.escape(message.get("content", ""))
        conversation_html.append(f"<details><summary>{role}</summary><pre>{content}</pre></details>")
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{--bg:#07131f;--panel:#102335;--text:#eaf2f8;--muted:#a9bdcb;--accent:#31c4d8;--ok:#61d095}}
body{{font-family:Segoe UI,Arial,sans-serif;margin:0;background:var(--bg);color:var(--text);line-height:1.5}}
header{{padding:28px 5vw;background:linear-gradient(120deg,#0d2d48,#07131f);border-bottom:1px solid #24445c}}
main{{max-width:1180px;margin:auto;padding:28px 5vw 60px}} section{{background:var(--panel);padding:24px;margin:18px 0;border-radius:12px}}
h1,h2,h3{{color:#fff}} h2{{border-bottom:1px solid #31526a;padding-bottom:8px}} code,pre{{background:#06101a;color:#d8f4f8;border-radius:7px}}
pre{{padding:14px;overflow:auto}} .badge{{display:inline-block;padding:5px 10px;margin:3px;border-radius:999px;background:#173b50;color:#dffaff}}
.ok{{background:#164438;color:#bff6de}} .muted{{color:var(--muted)}} details{{margin:8px 0}}
</style></head><body>
<header><h1>{html.escape(title)}</h1><span class="badge ok">SciLink interpretation-only</span><span class="badge">Generic curve fitting disabled</span><span class="badge">Deterministic electrochemistry complete</span><p class="muted">Made by FaradaIQ Studio {VERSION}</p></header>
<main><section>{_markdown_to_html(response_text)}</section>
<section><h2>Execution guard</h2><p>No analysis-agent tools were exposed. <code>select_agent</code>, <code>run_analysis</code>, CurveFittingAgent, Best-of-N, adaptive refit, annealing, and generic spectroscopy fitting were unavailable.</p></section>
<section><h2>Provenance</h2><pre>{provenance_json}</pre></section>
<section><h2>Conversation record</h2>{''.join(conversation_html)}</section></main></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")


def _build_initial_messages(*, prompt: str, skill_text: str, routing: Any, deterministic: Any, metadata: Any, series: Any, raw_context: Any) -> list[dict[str, str]]:
    system = """You are SciLink operating in FaradaIQ's deterministic electrochemistry interpretation-only mode.
The local FaradaIQ deterministic analysis is complete. Interpret and compare; do not refit.
No analysis tools are available. You cannot call select_agent or run_analysis and cannot select CurveFittingAgent.
Do not perform Gaussian, Lorentzian, Voigt, sech, arbitrary polynomial, Best-of-N, adaptive-refit, annealing, or generic R²-gated analysis.
Use electrochemical terminology, preserve negative cathodic current, preserve cycles/sweeps/stages/points, distinguish observations from calculations and hypotheses, do not invent missing metadata, and preserve approved=false.
Prepare a scientifically conservative Markdown report with: Executive summary, Dataset coverage, Technique-specific findings, Scan-rate/condition comparison, Data quality and uncertainty, Interpretation, Limitations, Recommended follow-up, and Provenance.
"""
    context = {
        "user_request": prompt,
        "routing_plan": routing,
        "deterministic_electrochemistry_analysis": deterministic,
        "series_metadata": series,
        "metadata": metadata,
        "raw_electrochemistry_context": raw_context,
        "bundled_electrochemistry_skill": skill_text,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "Interpret the following complete FaradaIQ electrochemistry context. Do not run another analysis pipeline.\n\n" + json.dumps(context, ensure_ascii=False, default=str)},
    ]


def _guard_receipt(job: Path, *, skill: Path, routing: Path, deterministic: Path, raw_context: Path, series_metadata: Path | None, model: str, base_url: str) -> dict[str, Any]:
    receipt = {
        "schema": SCHEMA,
        "version": VERSION,
        "created_at": _now(),
        "mode": "interpretation-only",
        "route": "technique_specific_deterministic_electrochemistry",
        "client_initialized": True,
        "client_type": "OpenAI-compatible direct HTTP client",
        "analysis_orchestrator_instantiated": False,
        "analysis_orchestrator_client_error_possible": False,
        "tool_calls_allowed": True,
        "analysis_tool_calls_allowed": False,
        "available_tools": list(SAFE_READ_ONLY_TOOLS),
        "blocked_tools": BLOCKED_TOOLS,
        "blocked_attempt_code": BLOCK_CODE,
        "generic_fallback_allowed": False,
        "skill_path": str(skill),
        "skill_sha256": _sha256(skill),
        "routing_plan_path": str(routing),
        "deterministic_analysis_path": str(deterministic),
        "raw_context_path": str(raw_context),
        "series_metadata_path": str(series_metadata) if series_metadata else None,
        "model": model,
        "base_url": base_url,
    }
    (job / "scilink_interpretation_only_guard.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--skills", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--routing-plan", required=True)
    parser.add_argument("--deterministic", required=True)
    parser.add_argument("--series-metadata")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    job = _resolve_exact(args.job_dir)
    data_dir = _resolve_exact(args.data, allowed_root=job)
    skill = _resolve_exact(args.skills, allowed_root=job)
    metadata_path = _resolve_exact(args.metadata, allowed_root=job)
    routing_path = _resolve_exact(args.routing_plan, allowed_root=job)
    deterministic_path = _resolve_exact(args.deterministic, allowed_root=job)
    prompt_path = _resolve_exact(args.prompt_file, allowed_root=job)
    session_dir = Path(args.session_dir).resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    series_path = _resolve_exact(args.series_metadata, allowed_root=job) if args.series_metadata else None

    routing = _load_json(routing_path)
    deterministic = _load_json(deterministic_path)
    if not isinstance(deterministic, Mapping) or not isinstance(deterministic.get("analyses"), list) or not deterministic["analyses"]:
        print("[BLOCKED] deterministic_electrochemistry_analysis.json is empty. Generic fallback is prohibited.")
        return 7
    if routing.get("interpretation_only") is not True or routing.get("run_analysis_allowed") is not False or routing.get("select_agent_allowed") is not False:
        print("[BLOCKED] Deterministic electrochemistry execution guard is incomplete.")
        return 7

    raw_context_path = job / "raw_electrochemistry_context.json"
    raw_context = build_raw_context(data_dir, output_path=raw_context_path)
    metadata = _load_json(metadata_path)
    series = _series_metadata(series_path)
    skill_text = skill.read_text(encoding="utf-8-sig", errors="strict")
    prompt = prompt_path.read_text(encoding="utf-8-sig", errors="strict")

    client = OpenAICompatibleClient(args.base_url, args.model, _resolve_api_key())
    assert client.ready, "OpenAI-compatible interpretation client was not initialized"
    guard = _guard_receipt(job, skill=skill, routing=routing_path, deterministic=deterministic_path,
                           raw_context=raw_context_path, series_metadata=series_path,
                           model=args.model, base_url=args.base_url)

    print("=" * 72)
    print(f"FaradaIQ Studio {VERSION} -> SciLink interpretation-only electrochemistry")
    print("=" * 72)
    print("Detected technique:", ", ".join(routing.get("techniques_detected") or []) or "unknown")
    routes = sorted({str(row.get("route")) for row in routing.get("routes") or []})
    print("Technique-specific route:", ", ".join(routes))
    print("Generic curve fitting: Disabled")
    print("Spectral peak fitting: Disabled")
    print("Deterministic analysis: Completed")
    print("SciLink mode: interpretation-only")
    print("Available tools: read-only file access only (list_job_files, read_job_file)")
    print("Available analysis tools: none")
    print("Blocked tools:", ", ".join(BLOCKED_TOOLS))
    print("Skill binding: --skills", skill)
    print("Exact job-local data root:", data_dir)
    print("Exact path preservation: enabled (Path.resolve(strict=True))")
    print("Client initialized: yes")
    print("-" * 72)

    messages = _build_initial_messages(prompt=prompt, skill_text=skill_text, routing=routing,
                                       deterministic=deterministic, metadata=metadata,
                                       series=series, raw_context=raw_context)
    if args.dry_run:
        response_text = "# Dry-run interpretation-only validation\n\nThe deterministic context is complete and the prohibited analysis tools were not exposed."
        tool_call_count = 0
    else:
        try:
            response_text, tool_call_count = client.complete(messages, job_root=job)
        except Exception as exc:
            error = {**guard, "status": "error", "error": str(exc), "completed_at": _now()}
            (job / "scilink_interpretation_only_result.json").write_text(json.dumps(error, ensure_ascii=False, indent=2), encoding="utf-8")
            print("[ERROR] SciLink interpretation-only request failed:", exc)
            return 8
    messages.append({"role": "assistant", "content": response_text})
    print("\n🤖 SciLink interpretation-only:\n")
    print(response_text)

    result_dir = session_dir / "results" / "scilink_interpretation_only"
    report_path = result_dir / "analysis_report.html"
    markdown_path = result_dir / "analysis_report.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(response_text, encoding="utf-8")
    provenance = {**guard, "status": "complete", "completed_at": _now(), "report_path": str(report_path),
                  "tool_call_count": tool_call_count, "raw_file_count": raw_context.get("file_count"),
                  "deterministic_analysis_count": deterministic.get("analysis_record_count")}
    write_html_report(report_path, title="SciLink Electrochemistry Interpretation Report",
                      response_text=response_text, provenance=provenance, conversation=messages)
    (job / "scilink_interpretation_only_result.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSciLink HTML report:", report_path)
    print("SciLink status: Completed")

    if args.non_interactive or args.dry_run or not sys.stdin:
        return 0
    print("Interactive follow-up is available. Type /exit or /quit to finish.")
    while True:
        try:
            line = input("\n👤 You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line.lower() in {"/exit", "/quit", "exit", "quit"}:
            break
        messages.append({"role": "user", "content": line})
        try:
            reply, followup_tool_calls = client.complete(messages, job_root=job)
            provenance["tool_call_count"] = int(provenance.get("tool_call_count") or 0) + followup_tool_calls
        except Exception as exc:
            print("[ERROR] Follow-up failed:", exc)
            continue
        messages.append({"role": "assistant", "content": reply})
        print("\n🤖 SciLink interpretation-only:\n")
        print(reply)
        response_text += "\n\n## Follow-up\n\n" + reply
        markdown_path.write_text(response_text, encoding="utf-8")
        provenance["follow_up_turns"] = int(provenance.get("follow_up_turns") or 0) + 1
        provenance["completed_at"] = _now()
        write_html_report(report_path, title="SciLink Electrochemistry Interpretation Report",
                          response_text=response_text, provenance=provenance, conversation=messages)
        (job / "scilink_interpretation_only_result.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
