"""Guided-hybrid SciLink electrochemistry skill integration for FaradaIQ Studio v290."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, MutableMapping

VERSION = "v290"
APPLICATION_SEMVER = "0.290.0"
SKILL_NAME = "electrochemistry_skill"
SKILL_FILENAME = "electrochemistry_skill.md"
SKILL_REVISION = "7.0-guided-hybrid-analysis"
MODEL_REGISTRY_FILENAME = "electrodeposition_model_registry.json"
MODEL_REGISTRY_SCHEMA = "faradaiq.electrochemistry.electrodeposition_model_registry.v1"
ANALYSIS_MODE_GUIDANCE_FILENAME = "analysis_mode_guidance.json"
ANALYSIS_MODE_GUIDANCE_SCHEMA = "faradaiq.electrochemistry.analysis_mode_guidance.v1"
NORMAL_CV_CONTRACT_FILENAME = "normal_cv_routing_contract.json"  # retained for optional deterministic mode
DETERMINISTIC_CV_REFERENCE_FILENAME = "deterministic_cv_analysis_reference.py"
CANONICAL_H2 = ["Overview", "Planning", "Implementation", "Analysis", "Validation", "Interpretation"]


def _package_candidates(filename: str) -> list[Path]:
    return [Path(__file__).resolve().parent / "skills" / filename, Path(__file__).resolve().parents[1] / "skills" / filename]


def _reject_extras(path: Path, label: str) -> None:
    if "extras" in {part.lower() for part in path.resolve().parts}:
        raise RuntimeError(f"Canonical {label} may not be loaded from extras: {path.resolve()}")


def _required_file(filename: str, label: str) -> Path:
    for candidate in _package_candidates(filename):
        if candidate.is_file():
            _reject_extras(candidate, label)
            return candidate.resolve()
    raise FileNotFoundError(f"Bundled canonical {label} is missing: {filename}")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def skill_revision(text: str) -> str:
    match = re.search(r"\*\*Revision:\*\*\s*`?([^`\n]+)`?", text)
    return match.group(1).strip() if match else "unknown"


def validate_skill_file(path: str | Path) -> dict[str, Any]:
    p = Path(path).resolve()
    _reject_extras(p, "electrochemistry skill")
    text = p.read_text(encoding="utf-8")
    headings = [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]
    if headings != CANONICAL_H2:
        raise RuntimeError(f"SciLink skill level-two heading contract failed. Expected {CANONICAL_H2}; found {headings}.")
    revision = skill_revision(text)
    if revision != SKILL_REVISION:
        raise RuntimeError(f"Wrong canonical electrochemistry skill revision. Expected {SKILL_REVISION}; found {revision}.")
    required = {
        "standard_default": "Standard SciLink analysis",
        "optional_deterministic": "Optional deterministic electrochemistry",
        "select_agent": "select_agent",
        "run_analysis": "run_analysis",
        "dummy_cell": "PalmSens AC Dummy Cell",
        "canonical_h5": "faradaiq-canonical-h5-bundle-v6",
        "approved_false": "approved=false",
        "drt_guard": "Never run or interpret DRT without source EIS or GEIS",
        "electrodeposition": "Scharifker-Hills",
        "seccm": "SECCM",
    }
    missing = [name for name, phrase in required.items() if phrase.lower() not in text.lower()]
    if missing:
        raise RuntimeError(f"Canonical electrochemistry skill content validation failed: {missing}")
    return {
        "path": str(p), "sha256": sha256_file(p), "bytes": p.stat().st_size,
        "line_count": len(text.splitlines()), "h2_headings": headings,
        "canonical_h2_exact": True, "revision": revision, "loaded_from_extras": False,
    }


def validate_model_registry(path: str | Path) -> dict[str, Any]:
    p = Path(path).resolve(); _reject_extras(p, "electrodeposition model registry")
    payload = json.loads(p.read_text(encoding="utf-8"))
    if payload.get("schema") != MODEL_REGISTRY_SCHEMA:
        raise RuntimeError(f"Unexpected electrodeposition model registry schema: {payload.get('schema')}")
    names = [str(row.get("name") or "") for row in payload.get("models", []) if isinstance(row, Mapping)]
    required = {"Direct transient metrics", "Cottrell", "Scharifker-Hills instantaneous", "Scharifker-Hills progressive", "Scharifker-Mostany", "Heerman-Tarallo", "Bewick-Fleischmann-Thirsk", "Avrami"}
    missing = sorted(required - set(names))
    if missing: raise RuntimeError(f"Electrodeposition model registry is incomplete: {missing}")
    return {"path": str(p), "sha256": sha256_file(p), "bytes": p.stat().st_size, "schema": payload["schema"], "skill_revision": payload.get("skill_revision"), "model_names": names}


def validate_analysis_mode_guidance(path: str | Path) -> dict[str, Any]:
    p = Path(path).resolve(); _reject_extras(p, "analysis-mode guidance")
    payload = json.loads(p.read_text(encoding="utf-8"))
    if payload.get("schema") != ANALYSIS_MODE_GUIDANCE_SCHEMA or payload.get("skill_revision") != SKILL_REVISION:
        raise RuntimeError("Guided-hybrid analysis-mode contract does not match the canonical v7 skill.")
    if payload.get("default_mode") != "standard_scilink":
        raise RuntimeError("Standard SciLink must remain the default analysis mode.")
    standard = payload.get("modes", {}).get("standard_scilink", {})
    if not all(standard.get(k) is True for k in ("select_agent_allowed", "run_analysis_allowed", "generated_code_allowed", "literature_allowed", "multimodal_allowed")):
        raise RuntimeError("Standard SciLink tool permissions are incomplete.")
    if standard.get("deterministic_preanalysis_required") is not False:
        raise RuntimeError("Deterministic preprocessing must be optional.")
    return {"path": str(p), "sha256": sha256_file(p), "bytes": p.stat().st_size, "schema": payload["schema"], "default_mode": payload["default_mode"]}


def _copy_unchanged(source: Path, destination: Path) -> str:
    digest = sha256_file(source)
    if not destination.is_file() or sha256_file(destination) != digest:
        shutil.copy2(source, destination)
    if sha256_file(destination) != digest:
        raise RuntimeError(f"Job-local copy does not match bundled source: {destination.name}")
    return digest


def prepare_job_skill(job_dir: str | Path, *, transaction_type: str | Mapping[str, Any] = "manual_ask_scilink", application_version: str = VERSION) -> dict[str, Any]:
    job = Path(job_dir).resolve(); destination = job / "skills"; destination.mkdir(parents=True, exist_ok=True)
    skill = validate_skill_file(_required_file(SKILL_FILENAME, "electrochemistry skill"))
    registry = validate_model_registry(_required_file(MODEL_REGISTRY_FILENAME, "electrodeposition model registry"))
    guidance = validate_analysis_mode_guidance(_required_file(ANALYSIS_MODE_GUIDANCE_FILENAME, "analysis-mode guidance"))
    copies: dict[str, dict[str, Any]] = {}
    for filename, label, meta in ((SKILL_FILENAME, "skill", skill), (MODEL_REGISTRY_FILENAME, "registry", registry), (ANALYSIS_MODE_GUIDANCE_FILENAME, "analysis_mode_guidance", guidance)):
        src = Path(str(meta["path"])); dst = destination / filename; digest = _copy_unchanged(src, dst)
        copies[label] = {**meta, "job_path": str(dst), "sha256": digest, "copied_unchanged": True}
    # Retain deterministic support assets only for the opt-in mode.
    for filename, label in ((NORMAL_CV_CONTRACT_FILENAME, "normal_cv_routing_contract"), (DETERMINISTIC_CV_REFERENCE_FILENAME, "deterministic_cv_reference")):
        try:
            src = _required_file(filename, label); dst = destination / filename; digest = _copy_unchanged(src, dst)
            copies[label] = {"package_path": str(src), "job_path": str(dst), "sha256": digest, "copied_unchanged": True}
        except FileNotFoundError:
            copies[label] = {}
    receipt = {
        "schema": "faradaiq-scilink-skill-receipt-v290", "version": VERSION,
        "application_version": application_version, "application_semver": APPLICATION_SEMVER,
        "transaction_type": transaction_type, "skill_name": SKILL_NAME, "skill_filename": SKILL_FILENAME,
        "skill_version": skill["revision"], "package_skill_path": skill["path"], "job_skill_path": copies["skill"]["job_path"],
        "sha256": skill["sha256"], "bytes": skill["bytes"], "line_count": skill["line_count"],
        "h2_headings": skill["h2_headings"], "canonical_h2_exact": True, "loaded_from_extras": False,
        "copied_unchanged": True, "analysis_only": True, "hardware_control_allowed": False,
        "approved_default_preserved": True, "default_analysis_mode": "standard_scilink",
        "analysis_mode_guidance": copies["analysis_mode_guidance"],
        "normal_cv_routing_contract": copies.get("normal_cv_routing_contract", {}),
        "deterministic_cv_reference": copies.get("deterministic_cv_reference", {}),
        "electrodeposition_model_registry": copies["registry"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (job / "scilink_skill_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt


def update_request_manifest(path: str | Path, receipt: Mapping[str, Any], *, transaction_type: str | Mapping[str, Any] | None = None) -> None:
    p = Path(path)
    try: payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception: return
    if not isinstance(payload, MutableMapping): return
    entry = {"name": SKILL_NAME, "path": str(receipt.get("job_skill_path") or ""), "filename": SKILL_FILENAME, "version": str(receipt.get("skill_version") or ""), "sha256": str(receipt.get("sha256") or ""), "required": True, "analysis_only": True, "copied_unchanged": True}
    skills = payload.get("skills") if isinstance(payload.get("skills"), list) else []
    payload["skills"] = [row for row in skills if not (isinstance(row, Mapping) and row.get("name") == SKILL_NAME)] + [entry]
    payload["electrochemistry_skill"] = entry
    payload["analysis_mode_guidance"] = receipt.get("analysis_mode_guidance")
    payload["electrodeposition_model_registry"] = receipt.get("electrodeposition_model_registry")
    payload["skill_provenance"] = {"application_version": VERSION, "transaction_type": transaction_type if transaction_type is not None else receipt.get("transaction_type"), "receipt_path": str(p.parent / "scilink_skill_receipt.json"), "skill_path": entry["path"], "skill_version": entry["version"], "sha256": entry["sha256"]}
    payload.update({"application_version": VERSION, "hardware_control_allowed": False, "skill_application_required": True, "approved": False})
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


STANDARD_GUIDANCE = (
    "Analyze the supplied electrochemical data using the attached electrochemistry skill. "
    "Identify the technique, preserve cycles, sweeps, stages, points, units, and provenance, and use the analysis methods that best answer the scientific question. "
    "Start with direct electrochemical observables and use fitting, simulation, literature, or alternative tools when scientifically useful. "
    "Clearly separate measured, calculated, fitted, and interpreted results. Do not invent missing metadata."
)


def required_prompt_instruction(job_skill_path: str | Path, *, analysis_mode: str = "standard_scilink", routing_guard: str = "") -> str:
    skill_path = Path(job_skill_path).resolve().as_posix()
    if analysis_mode == "optional_deterministic":
        mode_text = (
            "FaradaIQ performed optional deterministic preprocessing. Treat its JSON outputs as supporting evidence, verify them against raw data, and correct signs, units, identities, sweep directions, or derived values when needed. "
            "Interpretation-only execution may be used for this opt-in job when the routing plan requires it."
        )
    else:
        mode_text = (
            "Use the normal SciLink workflow. Analysis tools, generated code, literature, multimodal context, select_agent and run_analysis remain available when scientifically useful. "
            "Do not treat electrochemistry as spectroscopy merely because the files contain numeric X/Y columns."
        )
    return f"\n\n### Electrochemistry skill\nBind `{skill_path}` through the explicit SciLink `--skills` argument. {STANDARD_GUIDANCE} {mode_text}\n" + (routing_guard or "")


def build_effective_prompt(*, original_prompt: str, job_dir: str | Path, receipt: Mapping[str, Any], cli_skill_flag_supported: bool, routing_guard: str = "", analysis_mode: str = "standard_scilink") -> tuple[str, Path, str]:
    job = Path(job_dir).resolve()
    base = original_prompt.strip() or STANDARD_GUIDANCE
    instruction = required_prompt_instruction(receipt["job_skill_path"], analysis_mode=analysis_mode, routing_guard=routing_guard)
    if cli_skill_flag_supported:
        effective = base.rstrip() + instruction
        mode = f"native_cli_skill_{analysis_mode}"
    else:
        skill_text = Path(str(receipt["job_skill_path"])).read_text(encoding="utf-8")
        effective = base.rstrip() + instruction + "\n### Bundled canonical electrochemistry skill content\n\n" + skill_text.rstrip() + "\n"
        mode = f"full_skill_prompt_fallback_{analysis_mode}"
    effective_path = job / "analysis_prompt_with_electrochemistry_skill.md"
    effective_path.write_text(effective, encoding="utf-8")
    receipt_path = job / "scilink_skill_receipt.json"
    try: current = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception: current = dict(receipt)
    current.update({"application_mode": mode, "analysis_mode": analysis_mode, "native_cli_skill_flag_supported": bool(cli_skill_flag_supported), "effective_prompt_path": str(effective_path), "effective_prompt_sha256": hashlib.sha256(effective.encode("utf-8")).hexdigest(), "skill_content_appended_to_prompt": not cli_skill_flag_supported, "routing_guard_appended": bool(routing_guard)})
    receipt_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return effective, effective_path, mode

# ---- FaradaIQ Studio v291 public release identity ----
VERSION = "v292"
# ---- End FaradaIQ Studio v291 public release identity ----
