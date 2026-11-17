"""Guided-hybrid electrochemistry routing for FaradaIQ Studio v290.

Standard SciLink is the default. Deterministic preprocessing is opt-in through
``deterministic_electrochemistry_enabled`` in the reviewed job settings.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from . import v290_deterministic_electrochemistry as deterministic

VERSION = "v290"
STANDARD_ROUTE = "standard_scilink_guided_electrochemistry"
OPTIONAL_DETERMINISTIC_ROUTE = "technique_specific_deterministic_electrochemistry"
PROHIBITED_SCILINK_TOOLS = deterministic.PROHIBITED_SCILINK_TOOLS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_load(path: Path) -> Any:
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return {}


def _collect_authorized_paths(request: Mapping[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for key in ("selected_data_paths", "supporting_file_paths", "metadata_paths"):
        for raw in request.get(key, []) if isinstance(request.get(key), list) else []:
            try:
                p = Path(str(raw)).expanduser().resolve(strict=True)
            except Exception:
                continue
            if p.is_file() and p not in paths:
                paths.append(p)
    return paths


def _walk_json(obj: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(obj, Mapping):
        yield obj
        for value in obj.values(): yield from _walk_json(value)
    elif isinstance(obj, list):
        for value in obj: yield from _walk_json(value)


def _technique_tokens(text: str) -> list[str]:
    upper = re.sub(r"[^A-Z0-9]+", " ", text.upper())
    found: list[str] = []
    aliases = [
        ("OCP", (" OCP ", "OPEN CIRCUIT")), ("GEIS", (" GEIS ", "GALVANOSTATIC EIS")),
        ("EIS", (" EIS ", "IMPEDANCE")), ("DPV", (" DPV ", "DIFFERENTIAL PULSE")),
        ("SWV", (" SWV ", "SQUARE WAVE")), ("STRIPPING", ("STRIPPING",)),
        ("DEPOSITION", ("DEPOSITION", "ELECTRODEPOSITION")), ("SECCM", ("SECCM", "SPATIAL ELECTROCHEM")),
        ("CA", (" CHRONOAMP", " CA ")), ("CP", (" CHRONOPOT", " CP ")),
        ("CV", (" CYCLIC VOLT", " CV ")), ("LSV", (" LINEAR SWEEP", " LSV ")),
        ("DRT", (" DRT ", "DISTRIBUTION OF RELAXATION")),
    ]
    padded = f" {upper} "
    for name, pats in aliases:
        if any(p in padded for p in pats): found.append(name)
    return found


def detect_techniques(request: Mapping[str, Any], prompt: str = "") -> list[str]:
    found: list[str] = []
    def add(items: Iterable[str]) -> None:
        for item in items:
            if item and item not in found: found.append(item)
    add(_technique_tokens(prompt))
    for p in _collect_authorized_paths(request):
        add(_technique_tokens(p.name))
        if p.suffix.lower() in {".json", ".md", ".txt"} and p.stat().st_size <= 5_000_000:
            obj = _json_load(p) if p.suffix.lower() == ".json" else None
            if obj:
                for node in _walk_json(obj):
                    for key in ("technique", "technique_key", "techniques", "method", "title", "label", "experiment_title"):
                        value = node.get(key)
                        if isinstance(value, str): add(_technique_tokens(value))
                        elif isinstance(value, list):
                            for v in value:
                                if isinstance(v, str): add(_technique_tokens(v))
            else:
                try: add(_technique_tokens(p.read_text(encoding="utf-8", errors="replace")[:200_000]))
                except Exception: pass
    # CV takes precedence when the file/metadata explicitly identifies cyclic voltammetry.
    if "CV" in found and "LSV" in found:
        found.remove("LSV")
    return found


def _standard_receipt(job: Path, request: Mapping[str, Any], prompt: str, settings: Mapping[str, Any], skill_receipt: Mapping[str, Any]) -> dict[str, Any]:
    techniques = detect_techniques(request, prompt)
    transaction = deterministic.classify_transaction(prompt, request, settings)
    route_path = job / "electrochemistry_routing_plan.json"
    status_path = job / "electrochemistry_gui_status.json"
    routing = {
        "schema": "faradaiq-electrochemistry-routing-plan-v290", "version": VERSION,
        "created_at": _now(), "analysis_mode": "standard_scilink", "deterministic_electrochemistry_enabled": False,
        "route": STANDARD_ROUTE, "techniques_detected": techniques, "transaction_type": transaction,
        "skill_version": skill_receipt.get("skill_version"), "skill_path": skill_receipt.get("job_skill_path"),
        "skill_sha256": skill_receipt.get("sha256"), "routing_plan_path": str(route_path),
        "deterministic_analysis_path": "", "gui_status_path": str(status_path),
        "requires_generic_curvefit": True, "generic_curve_fitting_allowed": True,
        "curve_fitting_agent_required": False, "spectral_peak_fitting_allowed": "scientific_choice",
        "best_of_n_allowed": "scientific_choice", "adaptive_refit_allowed": "scientific_choice",
        "generic_r2_gate_allowed": False, "deterministic_analysis_required": False,
        "run_analysis_allowed": True, "select_agent_allowed": True, "generated_code_allowed": True,
        "interpretation_only": False, "direct_analysis_count": 0, "routing_conflicts": [],
        "drt_policy": {"eligible": bool("EIS" in techniques or "GEIS" in techniques), "requires_linked_eis_or_geis": True},
        "raw_paths_preserved": True, "approved": False,
        "gui_status": {
            "Electrochemistry skill": "Applied", "Analysis mode": "Standard SciLink",
            "SciLink tools": "Available", "Detected technique": ", ".join(techniques) or "Not identified",
            "SciLink status": "Ready to start",
        },
    }
    route_path.write_text(json.dumps(routing, ensure_ascii=False, indent=2), encoding="utf-8")
    status_path.write_text(json.dumps(routing["gui_status"], ensure_ascii=False, indent=2), encoding="utf-8")
    return routing


def prepare_job_routing(job_dir: str | Path, request: Mapping[str, Any], prompt: str, settings: Mapping[str, Any], skill_receipt: Mapping[str, Any]) -> dict[str, Any]:
    job = Path(job_dir).resolve()
    enabled = bool(settings.get("deterministic_electrochemistry_enabled", False))
    if not enabled:
        return _standard_receipt(job, request, prompt, settings, skill_receipt)
    routing = deterministic.prepare_job_routing(job, request, prompt, settings, skill_receipt)
    routing["analysis_mode"] = "optional_deterministic"
    routing["deterministic_electrochemistry_enabled"] = True
    routing["deterministic_results_are_supporting_evidence"] = True
    routing["deterministic_results_may_be_corrected_by_scilink"] = True
    routing["gui_status"] = {
        "Electrochemistry skill": "Applied",
        "Analysis mode": "Deterministic electrochemistry",
        "Deterministic preprocessing": "Completed",
        "Detected technique": ", ".join(routing.get("techniques_detected") or []) or "Not identified",
        "SciLink status": "Ready to start",
        "Files, stages, points or cycles analyzed": (routing.get("gui_status") or {}).get("Files, stages, points or cycles analyzed", {}),
    }
    Path(str(routing["routing_plan_path"])).write_text(json.dumps(routing, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    Path(str(routing["gui_status_path"])).write_text(json.dumps(routing["gui_status"], ensure_ascii=False, indent=2), encoding="utf-8")
    return routing


def update_request_with_routing(path: str | Path, routing: Mapping[str, Any]) -> None:
    p = Path(path); payload = _json_load(p)
    if not isinstance(payload, MutableMapping): return
    deterministic_enabled = bool(routing.get("deterministic_electrochemistry_enabled"))
    payload["application_version"] = VERSION
    payload["transaction_type"] = routing.get("transaction_type")
    payload["analysis_mode"] = routing.get("analysis_mode")
    payload["deterministic_electrochemistry_enabled"] = deterministic_enabled
    payload["electrochemistry_routing"] = {
        "plan_path": routing.get("routing_plan_path"), "deterministic_analysis_path": routing.get("deterministic_analysis_path"),
        "gui_status_path": routing.get("gui_status_path"), "techniques_detected": routing.get("techniques_detected"),
        "route": routing.get("route"), "run_analysis_allowed": routing.get("run_analysis_allowed"),
        "select_agent_allowed": routing.get("select_agent_allowed"), "generated_code_allowed": routing.get("generated_code_allowed", not deterministic_enabled),
        "interpretation_only": routing.get("interpretation_only"), "deterministic_analysis_required": routing.get("deterministic_analysis_required"),
        "deterministic_results_are_supporting_evidence": routing.get("deterministic_results_are_supporting_evidence", False),
        "drt_eligible": (routing.get("drt_policy") or {}).get("eligible"),
    }
    payload["route"] = routing.get("route")
    payload["run_analysis_allowed"] = routing.get("run_analysis_allowed")
    payload["select_agent_allowed"] = routing.get("select_agent_allowed")
    payload["interpretation_only"] = routing.get("interpretation_only")
    payload["approved"] = False
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def prompt_guard_text(routing: Mapping[str, Any]) -> str:
    if not routing.get("deterministic_electrochemistry_enabled"):
        return ""
    direct_path = str(routing.get("deterministic_analysis_path") or "")
    route_path = str(routing.get("routing_plan_path") or "")
    return (
        "\n\n### Optional deterministic electrochemistry evidence\n"
        f"FaradaIQ precalculated technique-specific metrics in `{direct_path}` with routing context in `{route_path}`. "
        "Use these as supporting evidence rather than unquestionable truth. Verify them against the raw data and metadata, and correct signs, peak assignments, units, file identities, sweep directions, or inconsistent derived values when needed. "
        "Preserve raw data and provenance.\n"
    )


def write_validated_series_metadata(context_path: str | Path | None, job_dir: str | Path) -> dict[str, Any]:
    return deterministic.write_validated_series_metadata(context_path, job_dir)


def finalize_campaign_outputs(job_dir: str | Path) -> dict[str, Any]:
    return deterministic.finalize_campaign_outputs(job_dir)


def finalize_job(job_dir: str | Path) -> dict[str, Any]:
    return deterministic.finalize_job(job_dir)


def update_scilink_status(job_dir: str | Path, status: str) -> dict[str, Any]:
    return deterministic.update_scilink_status(job_dir, status)

# Re-export scientific analyzers for compatibility and QA.
inspect_canonical_hdf5 = deterministic.inspect_canonical_hdf5
potential_direction_change_count = deterministic.potential_direction_change_count
refine_cv_lsv_technique = deterministic.refine_cv_lsv_technique
analyze_electrochemical_record = deterministic.analyze_electrochemical_record
analyze_dummy_eis = deterministic.analyze_dummy_eis
analyze_drt = deterministic.analyze_drt
_dummy_impedance = deterministic._dummy_impedance

# ---- FaradaIQ Studio v291 public release identity ----
VERSION = "v292"
# ---- End FaradaIQ Studio v291 public release identity ----
