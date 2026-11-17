#!/usr/bin/env python
"""Launch one reviewed FaradaIQ SciLink job in an isolated persistent console.

FaradaIQ keeps the scientific prompt as UTF-8 text, but validates every value
that can become an HTTP header before SciLink/LiteLLM starts.  The selected LLM
provider is also isolated from stale provider variables inherited from Windows.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

try:
    from faradaiq.release_identity import APP_VERSION as VERSION
except Exception:
    VERSION = "v292"

_TYPOGRAPHIC_ASCII = str.maketrans({
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00a0": " ",
})

_SECRET_SUFFIXES = (
    "_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_AUTHORIZATION", "_BEARER",
)
_HEADER_ENV_NAMES = {
    "OPENAI_API_KEY", "OPENAI_ORGANIZATION", "OPENAI_PROJECT",
    "ANTHROPIC_API_KEY", "ANTHROPIC_VERSION", "CLAUDE_API_KEY",
    "OLLAMA_API_KEY", "LITELLM_KEY", "LITELLM_MASTER_KEY",
    "AI_INCUBATOR_API_KEY", "CUSTOM_LLM_API_KEY",
    "EDISON_API_KEY", "EDISON_PLATFORM_API_KEY", "EDISON_SCIENTIFIC_API_KEY",
    "FUTUREHOUSE_API_KEY", "FUTURE_HOUSE_API_KEY", "FH_API_KEY",
    "LABRIC_API_KEY", "MCP_AUTH_TOKEN", "SCILINK_API_KEY",
    "LITELLM_HEADERS", "EXTRA_HEADERS", "SCILINK_HEADERS", "HTTP_HEADERS",
    "AUTHORIZATION", "X_API_KEY", "USER_AGENT", "HTTP_USER_AGENT",
}

_OLLAMA_CONFLICTING_ENV = {
    "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_BASE", "ANTHROPIC_VERSION",
    "CLAUDE_API_KEY", "AWS_BEDROCK_MODEL_ID", "BEDROCK_MODEL_ID", "BEDROCK_LITELLM_MODEL",
    "AWS_LITELLM_MODEL", "AI_INCUBATOR_BASE_URL", "AI_INCUBATOR_MODEL", "AI_INCUBATOR_API_KEY",
    "CUSTOM_LLM_BASE_URL", "CUSTOM_LLM_MODEL", "CUSTOM_LLM_API_KEY",
}

_SCILINK_RUNTIME_IMPORTS = (
    ("numpy", "numpy>=1.26,<3"),
    ("scipy", "scipy>=1.11,<2"),
    ("pandas", "pandas>=2.1,<3"),
    ("matplotlib", "matplotlib>=3.7,<4"),
    ("lmfit", "lmfit>=1.3,<2"),
    ("sklearn", "scikit-learn>=1.4,<2"),
    ("h5py", "h5py>=3.10,<4"),
    ("openpyxl", "openpyxl>=3.1,<4"),
    ("statsmodels", "statsmodels>=0.14,<1"),
)



def _candidate_commands(settings: Mapping[str, Any]) -> list[list[str]]:
    out: list[list[str]] = []
    override = str(
        settings.get("scilink_executable_override")
        or settings.get("scilink_executable")
        or os.environ.get("SCILINK_EXE")
        or os.environ.get("SCILINK_CLI")
        or ""
    ).strip()
    if override:
        raw_override = override.strip('"')
        p = Path(raw_override)
        resolved = str(p.resolve()) if p.exists() else (shutil.which(raw_override) or "")
        if resolved:
            candidate = Path(resolved)
            out.append([resolved, "-m", "scilink"] if candidate.name.lower().startswith("python") else [resolved])
    for name in ("scilink.exe", "scilink"):
        found = shutil.which(name)
        if found:
            out.append([found])
    patterns = [
        r"C:\SciLink\SciLink\scilink.exe",
        r"C:\SciLink\scilink.exe",
        r"C:\SciLink\SciLink\.venv\Scripts\scilink.exe",
        r"C:\SciLink\SciLink\.venv\Scripts\python.exe",
        r"C:\SciLink\scilink-labric-demo\.venv\Scripts\scilink.exe",
        r"C:\SciLink\scilink-labric-demo\.venv\Scripts\python.exe",
        r"C:\SciLink\.venv\Scripts\python.exe",
    ]
    for raw in patterns:
        p = Path(raw)
        if p.exists():
            out.append([str(p), "-m", "scilink"] if p.name.lower() == "python.exe" else [str(p)])
    for root in (Path(r"C:\SciLink"), Path(r"C:\FaradaiqSIED"), Path(r"C:\FaradaIQ")):
        if not root.exists():
            continue
        for pat in ("*/Scripts/scilink.exe", "*/*/Scripts/scilink.exe", "*/Scripts/python.exe", "*/*/Scripts/python.exe"):
            for p in list(root.glob(pat))[:20]:
                out.append([str(p), "-m", "scilink"] if p.name.lower() == "python.exe" else [str(p)])
    unique: list[list[str]] = []
    seen: set[str] = set()
    for cmd in out:
        key = "\0".join(x.lower() for x in cmd)
        if key not in seen:
            seen.add(key)
            unique.append(cmd)
    return unique


def _scilink_python(command: list[str]) -> Path | None:
    """Resolve the Python interpreter that owns the selected SciLink executable."""
    if not command:
        return None
    executable = Path(command[0])
    if executable.name.lower().startswith("python") and executable.is_file():
        return executable.resolve()
    if executable.name.lower() in {"scilink.exe", "scilink"}:
        candidates = [
            executable.with_name("python.exe"),
            executable.with_name("python"),
            executable.parent / "python.exe",
            executable.parent / "python",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
    return None


def _probe_runtime(python: Path, managed_pythonpath: Path | None = None) -> dict[str, Any]:
    modules = [name for name, _ in _SCILINK_RUNTIME_IMPORTS]
    code = "\n".join([
        "import importlib, json, sys",
        f"mods = {modules!r}",
        "out = {'python': sys.executable, 'version': sys.version, 'python_info': [sys.version_info.major, sys.version_info.minor], 'modules': {}, 'module_files': {}, 'missing': []}",
        "for name in mods:",
        "    try:",
        "        mod = importlib.import_module(name)",
        "        out['modules'][name] = getattr(mod, '__version__', 'installed')",
        "        out['module_files'][name] = str(getattr(mod, '__file__', '') or '')",
        "    except Exception as exc:",
        "        out['missing'].append(name)",
        "        out['modules'][name] = 'ERROR: ' + str(exc)",
        "print(json.dumps(out))",
    ])
    env = dict(os.environ)
    if managed_pythonpath is not None:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(managed_pythonpath) + (os.pathsep + existing if existing else "")
        env["PYTHONNOUSERSITE"] = "1"
    try:
        cp = subprocess.run(
            [str(python), "-c", code],
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
        lines = [line.strip() for line in (cp.stdout or "").splitlines() if line.strip()]
        payload = json.loads(lines[-1]) if lines else {}
        if not isinstance(payload, dict):
            payload = {}
        payload["returncode"] = cp.returncode
        payload["managed_pythonpath"] = str(managed_pythonpath or "")
        if cp.stderr:
            payload["stderr"] = cp.stderr[-4000:]
        return payload
    except Exception as exc:
        return {
            "python": str(python),
            "missing": [name for name, _ in _SCILINK_RUNTIME_IMPORTS],
            "probe_error": str(exc),
            "returncode": -1,
            "managed_pythonpath": str(managed_pythonpath or ""),
        }


def _python_abi_tag(python: Path) -> str:
    try:
        cp = subprocess.run(
            [str(python), "-c", "import sys; print(f'py{sys.version_info.major}{sys.version_info.minor}')"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        tag = (cp.stdout or "").strip().splitlines()[-1]
        if re.fullmatch(r"py\d{2,3}", tag):
            return tag
    except Exception:
        pass
    return "py_unknown"


def _ensure_scilink_runtime(command: list[str], settings: Mapping[str, Any], job: Path) -> dict[str, Any]:
    """Prepare generated-code dependencies without modifying SciLink itself.

    SciLink executes CurveFittingAgent scripts with the interpreter beside its
    ``scilink.exe``. FaradaIQ now keeps any missing numerical packages in a
    version-specific managed target under the FaradaIQ installation and exposes
    that target only through the launched SciLink process ``PYTHONPATH``. This
    avoids installing into or asking the user to approve paths under
    ``C:\\SciLink\\...\\site-packages`` while remaining compatible with the
    exact Python ABI used by the installed SciLink CLI.
    """
    python = _scilink_python(command)
    report: dict[str, Any] = {
        "schema": "faradaiq-scilink-runtime-preflight-v290",
        "version": VERSION,
        "status": "unchecked",
        "scilink_command": command,
        "python": str(python or ""),
        "auto_repair_enabled": bool(settings.get("scilink_auto_repair_dependencies", True)),
        "required_imports": [name for name, _ in _SCILINK_RUNTIME_IMPORTS],
        "install_attempted": False,
        "repair_mode": "FaradaIQ-managed PYTHONPATH target",
        "external_scilink_site_packages_modified": False,
    }
    path = job / "scilink_runtime_preflight.json"
    if python is None:
        report.update({
            "status": "warning",
            "warning": "The SciLink executable's companion Python interpreter could not be resolved; managed dependency preparation was not possible.",
        })
        path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
        return report

    before = _probe_runtime(python)
    report["before"] = before
    missing = list(before.get("missing") or [])
    if not missing and before.get("returncode") == 0:
        report.update({"status": "ready", "runtime_source": "existing SciLink environment", "pythonpath": ""})
        path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
        return report

    abi_tag = _python_abi_tag(python)
    managed_target = Path(__file__).resolve().parent / ".scilink_curvefit_runtime" / abi_tag
    managed_target.mkdir(parents=True, exist_ok=True)
    report["managed_target"] = str(managed_target)

    # A previous FaradaIQ-managed repair may already be complete.
    managed_before = _probe_runtime(python, managed_target)
    report["managed_before"] = managed_before
    if not list(managed_before.get("missing") or []) and managed_before.get("returncode") == 0:
        report.update({
            "status": "ready",
            "runtime_source": "FaradaIQ-managed target",
            "pythonpath": str(managed_target),
        })
        path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
        return report

    if not report["auto_repair_enabled"]:
        report.update({"status": "blocked", "missing": missing, "pythonpath": ""})
        path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
        return report

    requirement_file = Path(__file__).resolve().with_name("requirements_scilink_curvefit.txt")
    if not requirement_file.is_file():
        report.update({
            "status": "blocked",
            "missing": missing,
            "pythonpath": "",
            "error": f"Missing packaged requirements file: {requirement_file}",
        })
        path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
        return report

    report["install_attempted"] = True
    print("[SCILINK RUNTIME] Missing or broken generated-code imports:", ", ".join(missing) or "runtime probe failed")
    print("[SCILINK RUNTIME] Preparing a FaradaIQ-managed dependency target:", managed_target)
    print("[SCILINK RUNTIME] The external SciLink site-packages folder will not be modified.")
    try:
        cp = subprocess.run(
            [
                str(python), "-m", "pip", "install",
                "--disable-pip-version-check", "--no-input", "--prefer-binary", "--upgrade",
                "--target", str(managed_target), "-r", str(requirement_file),
            ],
            text=True,
            timeout=1800,
        )
        report["pip_returncode"] = cp.returncode
    except Exception as exc:
        report["pip_returncode"] = -1
        report["pip_error"] = str(exc)

    after = _probe_runtime(python, managed_target)
    report["after"] = after
    remaining = list(after.get("missing") or [])
    report["missing"] = remaining
    report["status"] = "ready" if not remaining and after.get("returncode") == 0 else "blocked"
    report["runtime_source"] = "FaradaIQ-managed target" if report["status"] == "ready" else "unavailable"
    report["pythonpath"] = str(managed_target) if report["status"] == "ready" else ""
    path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    return report


def _validate_job_data_root(job: Path, working_data_dir: Path, data_paths: list[Path], context_path: Path | None) -> list[str]:
    errors: list[str] = []
    try:
        root = working_data_dir.resolve(strict=True)
    except Exception as exc:
        return [f"SciLink working data folder does not resolve exactly: {exc}"]
    # A text-only SciLink conversation is valid without a Browser data bundle.
    # Once any data file is reviewed, however, the strict job-local data contract
    # is mandatory so SciLink cannot drift into the parent experiment folder.
    if not data_paths:
        return errors
    if root != (job / "data").resolve():
        errors.append("SciLink working directory is not the job-local data folder.")
    for path in data_paths:
        try:
            path.resolve(strict=True).relative_to(root)
        except Exception:
            errors.append(f"Selected data path is outside the job-local data folder: {path}")
    if context_path is None or not context_path.is_file():
        errors.append("Missing job-local SciLink context JSON.")
    else:
        try:
            context_path.resolve(strict=True).relative_to(root)
        except Exception:
            errors.append("SciLink context JSON is outside the job-local data folder.")
    return errors


def _help(command: list[str], subcommand: str | None = None) -> str:
    cmd = list(command)
    if subcommand:
        cmd.append(subcommand)
    cmd.append("--help")
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        return (cp.stdout or "") + "\n" + (cp.stderr or "")
    except Exception:
        return ""


def _supports(help_text: str, flag: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_-])" + re.escape(flag) + r"(?![A-Za-z0-9_-])", help_text) is not None


def _choose_command(settings: Mapping[str, Any]) -> tuple[list[str] | None, str, str]:
    for base in _candidate_commands(settings):
        top = _help(base)
        if top or Path(base[0]).exists():
            sub = "analyze" if re.search(r"\banalyze\b", top, re.I) else ""
            subhelp = _help(base, sub) if sub else top
            return base, sub, subhelp
    return None, "", ""


def _provider_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "ollama" in text:
        return "ollama"
    if "anthropic" in text or "claude" in text:
        return "anthropic"
    if "bedrock" in text or "aws" in text:
        return "bedrock"
    if "litellm" in text:
        return "litellm"
    if "incubator" in text:
        return "openai_compatible"
    if "openai" in text or "custom" in text or "api key" in text:
        return "openai_compatible"
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "openai_compatible"


def _ascii_metadata(value: Any) -> str:
    text = str(value or "").translate(_TYPOGRAPHIC_ASCII).strip()
    text.encode("ascii")
    return text


def _is_secret_name(name: str) -> bool:
    upper = name.upper()
    return upper in _HEADER_ENV_NAMES or upper.endswith(_SECRET_SUFFIXES)


def _root_ollama_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if value.lower().endswith("/v1"):
        value = value[:-3].rstrip("/")
    return value


def _openai_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if not value:
        return value
    return value if value.lower().endswith("/v1") else value + "/v1"


def _litellm_model(provider: str, model: str) -> str:
    """Preserve the exact configured model identifier.

    SciLink's OpenAI-compatible Ollama path must receive the native Ollama tag
    (for example ``gpt-oss:120b-cloud``). Prefixing it with ``ollama/`` caused
    SciLink to shorten the tag internally and request a nonexistent model.
    Provider routing is already supplied separately through environment values.
    """
    return model.strip()




def _model_for_cli(provider: str, raw_model: str, provider_flag_supported: bool) -> str:
    """Return the exact CLI model identifier expected by the selected endpoint."""
    if provider == "ollama":
        return raw_model.strip()
    return raw_model.strip() if provider_flag_supported else _litellm_model(provider, raw_model)

def _safe_url(value: str) -> str:
    """Normalize typography while preserving URL semantics."""
    text = _ascii_metadata(value)
    if not text:
        return ""
    parts = urlsplit(text)
    if parts.scheme and parts.netloc:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, parts.fragment))
    return text


def _effective_scilink_mode(settings: Mapping[str, Any]) -> str:
    """Return the exact reviewed SciLink autonomy mode.

    The question gate is an independent policy.  It may pause an Auto-pilot or
    Autonomous session when SciLink explicitly asks for missing information,
    but it must never rewrite the operator's selected CLI mode.
    """
    raw = str(settings.get("scilink_mode") or settings.get("scilink_requested_mode") or "co-pilot").strip().lower().replace("_", "-")
    if raw.startswith("co"):
        return "co-pilot"
    if raw in {"auto-pilot", "autopilot"} or raw.startswith("auto-p"):
        return "autopilot"
    return "autonomous"


def prepare_scilink_environment(
    settings: Mapping[str, Any],
    base_env: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Build an isolated SciLink environment and a secret-free preflight report."""
    source_env = dict(base_env or os.environ)
    env = dict(source_env)
    llm = dict(settings.get("llm") or {})
    display_provider = _ascii_metadata(llm.get("provider") or "")
    provider = _provider_key(display_provider)
    raw_model = _ascii_metadata(llm.get("model") or "")
    base_url = _safe_url(str(llm.get("base_url") or ""))
    report: dict[str, Any] = {
        "schema": "faradaiq-scilink-preflight-v290",
        "version": VERSION,
        "status": "ready",
        "selected_provider": display_provider,
        "provider_key": provider,
        "selected_model": raw_model,
        "base_url": base_url,
        "unsafe_execution_authorized": True,
        "unsafe_execution_policy": "always enabled for every launched SciLink CLI subprocess",
        "literature_analysis_enabled": bool(settings.get("scilink_literature_analysis_enabled", True)),
        "edison_available_to_scilink": True,
        "wait_for_user_questions": bool(settings.get("scilink_wait_for_user_questions", True)),
        "effective_scilink_mode": _effective_scilink_mode(settings),
        "unsafe_execution_environment_variable": "UNSAFE_EXECUTION_OK",
        "unsafe_execution_scope": "current SciLink CLI subprocess only",
        "cleared_environment_variables": [],
        "normalized_environment_variables": [],
        "header_fields_checked": [],
        "errors": [],
        "notes": [
            "Scientific prompt remains UTF-8 and is not converted to an HTTP header.",
            "Secret values are never written to this report.",
            "UNSAFE_EXECUTION_OK is removed from inherited environment state and set to true for every launched SciLink CLI subprocess.",
            "Edison/FutureHouse availability inside SciLink is independent of the separate Ask Edison GUI button.",
            "The reviewed Co-pilot, Auto-pilot, or Autonomous mode is preserved; the question gate independently waits for explicit user answers.",
        ],
    }

    if base_url:
        parsed_base = urlsplit(base_url)
        if parsed_base.scheme.lower() not in {"http", "https"} or not parsed_base.netloc:
            report["errors"].append("The configured LLM base URL must be an absolute http:// or https:// URL.")
        if parsed_base.username is not None or parsed_base.password is not None:
            report["errors"].append("Do not embed credentials in the configured LLM base URL.")

    # SciLink receives no FaradaIQ skill/system prompt. Clear inherited prompt
    # injection variables only for this subprocess and set explicit opt-out flags.
    for _prompt_env in (
        "FARADAIQ_SKILL_PROMPT", "FARADAIQ_SKILL_PROMPT_FILE",
        "FARADAIQ_SYSTEM_PROMPT", "FARADAIQ_SYSTEM_PROMPT_FILE",
        "SCILINK_SKILL_PROMPT", "SCILINK_SKILL_PROMPT_FILE",
        "SCILINK_SYSTEM_PROMPT", "SCILINK_SYSTEM_PROMPT_FILE",
        "SYSTEM_PROMPT_ADDITIONAL_GUIDELINES",
    ):
        if _prompt_env in env:
            env.pop(_prompt_env, None)
            report["cleared_environment_variables"].append(_prompt_env)
    env["FARADAIQ_DISABLE_SKILL_PROMPT"] = "1"
    env["SCILINK_DISABLE_SKILL_PROMPT"] = "1"

    def clear(name: str) -> None:
        if name in env:
            env.pop(name, None)
            report["cleared_environment_variables"].append(name)

    # Never inherit a machine-wide or parent-process no-sandbox override.
    # v290 always unlocks the launched SciLink CLI child process, while leaving
    # the parent Studio and global Windows environments unchanged.
    clear("UNSAFE_EXECUTION_OK")
    env["UNSAFE_EXECUTION_OK"] = "true"
    report["unsafe_execution_environment_value"] = "true"

    wait_for_user = bool(settings.get("scilink_wait_for_user_questions", True))
    interaction_env = {
        "FARADAIQ_SCILINK_WAIT_FOR_USER": "1" if wait_for_user else "0",
        "SCILINK_HUMAN_FEEDBACK_ENABLED": "1" if wait_for_user else "0",
        "SCILINK_REQUIRE_HUMAN_FEEDBACK": "1" if wait_for_user else "0",
        "SCILINK_INTERACTIVE_FEEDBACK": "1" if wait_for_user else "0",
        "SCILINK_AUTO_CONTINUE": "0" if wait_for_user else "1",
        "SCILINK_AUTO_RERUN": "0" if wait_for_user else "1",
        "SCILINK_AUTO_RETRY_ANALYSIS": "0" if wait_for_user else "1",
    }
    for name, value in interaction_env.items():
        env[name] = value

    def set_ascii(name: str, value: Any, *, secret: bool = False) -> None:
        if value is None:
            return
        original = str(value)
        if not original:
            return
        try:
            cleaned = _ascii_metadata(original)
        except UnicodeEncodeError:
            report["errors"].append(
                f"{name} contains a non-ASCII character and may be used as an HTTP header."
            )
            return
        env[name] = cleaned
        if cleaned != original:
            report["normalized_environment_variables"].append(name)
        if secret or _is_secret_name(name):
            report["header_fields_checked"].append(name)

    if provider == "ollama":
        for name in sorted(_OLLAMA_CONFLICTING_ENV):
            clear(name)
        ollama_root = _root_ollama_url(base_url or "http://localhost:11434/v1")
        openai_base = _openai_url(base_url or ollama_root)
        litellm_name = _litellm_model(provider, raw_model)
        for name, value in {
            "FARADAIQ_LLM_PROVIDER": "Ollama",
            "FARADAIQ_LLM_MODEL": raw_model,
            "FARADAIQ_LLM_BASE_URL": openai_base,
            "SCILINK_LLM_PROVIDER": "ollama",
            "SCILINK_PROVIDER": "ollama",
            "SCILINK_LLM_MODEL": raw_model,
            "SCILINK_LLM_BASE_URL": openai_base,
            "CUSTOM_LLM_PROVIDER": "ollama",
            "LITELLM_CUSTOM_LLM_PROVIDER": "ollama",
            "LITELLM_MODEL": litellm_name,
            "LITELLM_API_BASE": ollama_root,
            "OPENAI_BASE_URL": openai_base,
            "OPENAI_API_BASE": openai_base,
            "OLLAMA_HOST": ollama_root,
            "OLLAMA_API_BASE": ollama_root,
            "OLLAMA_MODEL": raw_model,
            "LITELLM_NUM_RETRIES": "0",
            "LITELLM_MAX_RETRIES": "0",
        }.items():
            set_ascii(name, value)
        # A local OpenAI-compatible Ollama endpoint needs a syntactically valid
        # ASCII bearer value, not a stale cloud/Anthropic key.
        set_ascii("OPENAI_API_KEY", env.get("OLLAMA_API_KEY") or "ollama", secret=True)
        set_ascii("LITELLM_KEY", "ollama", secret=True)
    else:
        for name, value in {
            "FARADAIQ_LLM_PROVIDER": display_provider,
            "FARADAIQ_LLM_MODEL": raw_model,
            "FARADAIQ_LLM_BASE_URL": base_url,
            "SCILINK_LLM_PROVIDER": provider,
            "SCILINK_PROVIDER": provider,
            "SCILINK_LLM_MODEL": raw_model,
            "SCILINK_LLM_BASE_URL": base_url,
            "LITELLM_MODEL": _litellm_model(provider, raw_model),
        }.items():
            set_ascii(name, value)
        if base_url:
            set_ascii("OPENAI_BASE_URL", base_url)
            set_ascii("LITELLM_API_BASE", base_url)

    # SciLink's CLI asks interactively for SCILINK_API_KEY before reading the
    # scientific prompt. Resolve it here so stdin always reaches the chat. For
    # local Ollama, a non-secret placeholder is sufficient because the endpoint
    # normally ignores bearer authentication.
    if provider == "ollama":
        resolved_key = (
            source_env.get("SCILINK_API_KEY")
            or source_env.get("OLLAMA_API_KEY")
            or source_env.get("OPENAI_API_KEY")
            or "ollama"
        )
    else:
        resolved_key = (
            source_env.get("SCILINK_API_KEY")
            or source_env.get("OPENAI_API_KEY")
            or source_env.get("LITELLM_KEY")
            or source_env.get("AI_INCUBATOR_API_KEY")
            or source_env.get("CUSTOM_LLM_API_KEY")
            or ""
        )
    if resolved_key:
        set_ascii("SCILINK_API_KEY", resolved_key, secret=True)
        set_ascii("SCILINK_PROXY_API_KEY", resolved_key, secret=True)
        report["scilink_api_key_source"] = (
            "existing environment" if source_env.get("SCILINK_API_KEY")
            else "active provider environment" if resolved_key != "ollama"
            else "local Ollama placeholder"
        )
    elif provider != "bedrock":
        report["errors"].append("No SciLink/proxy API key could be resolved from the active provider environment.")

    # SciLink literature analysis is an explicit per-job setting. Edison and
    # FutureHouse credentials are available to SciLink in every launch mode,
    # independent of whether the separate Ask Edison button is visible.
    literature_enabled = bool(settings.get("scilink_literature_analysis_enabled", True))
    for name, value in {
        "FARADAIQ_EDISON_ENABLED": "1",
        "FARADAIQ_SCILINK_LITERATURE_ANALYSIS": "1" if literature_enabled else "0",
        "SCILINK_LITERATURE_ANALYSIS_ENABLED": "1" if literature_enabled else "0",
        "SCILINK_ENABLE_LITERATURE_ANALYSIS": "1" if literature_enabled else "0",
        "SCILINK_NOVELTY_ASSESSMENT_ENABLED": "1" if literature_enabled else "0",
    }.items():
        set_ascii(name, value)

    literature_key_names = (
        "EDISON_API_KEY", "EDISON_PLATFORM_API_KEY", "EDISON_SCIENTIFIC_API_KEY",
        "FUTUREHOUSE_API_KEY", "FUTURE_HOUSE_API_KEY", "FH_API_KEY",
    )
    literature_key = next((source_env.get(name) for name in literature_key_names if source_env.get(name)), "")
    if literature_key:
        # Edison/FutureHouse stays available inside SciLink in every launch
        # mode. The per-job literature checkbox controls tool use, not whether
        # credentials disappear from the SciLink process.
        for name in literature_key_names:
            set_ascii(name, literature_key, secret=True)
        report["literature_credential_source"] = next(
            (name for name in literature_key_names if source_env.get(name)), "resolved alias"
        )
        report["edison_credentials_available"] = True
    else:
        report["literature_credential_source"] = "not detected"
        report["edison_credentials_available"] = False
        if literature_enabled:
            report["notes"].append("Literature analysis was requested, but no Edison/FutureHouse API key was detected.")

    # Validate provider-relevant header values that survived isolation.  Do not
    # scan every Windows environment variable, only credentials/header inputs.
    # Validate only known SciLink/LiteLLM/provider/service header inputs.
    # Do not inspect unrelated Windows passwords or application secrets.
    relevant = set(_HEADER_ENV_NAMES)
    for name in sorted(relevant):
        if name not in env or not env[name]:
            continue
        value = str(env[name])
        try:
            value.encode("ascii")
        except UnicodeEncodeError:
            # Never mutate a credential.  Report only its variable name.
            report["errors"].append(
                f"{name} contains a non-ASCII character and cannot be used safely as an HTTP header."
            )
        if name not in report["header_fields_checked"]:
            report["header_fields_checked"].append(name)

    # Remove duplicates while retaining deterministic order.
    for key in ("cleared_environment_variables", "normalized_environment_variables", "header_fields_checked", "errors"):
        report[key] = list(dict.fromkeys(report[key]))
    if report["errors"]:
        report["status"] = "blocked"
    return env, report


def _write_preflight(job: Path, report: Mapping[str, Any]) -> Path:
    path = job / "scilink_preflight.json"
    path.write_text(json.dumps(dict(report), ensure_ascii=True, indent=2), encoding="utf-8")
    return path


def _write_launch_config(job: Path, settings: Mapping[str, Any], report: Mapping[str, Any]) -> Path:
    llm = dict(settings.get("llm") or {})
    provider = str(report.get("provider_key") or "")
    model = _ascii_metadata(llm.get("model") or "")
    payload = {
        "schema": "faradaiq-scilink-launch-config-v290",
        "provider": provider,
        "provider_display": report.get("selected_provider", ""),
        "model": model,
        "litellm_model": _litellm_model(provider, model),
        "base_url": report.get("base_url", ""),
        "labric_mcp_enabled": bool(settings.get("scilink_labric_cloud_context_enabled") or settings.get("labric_mcp_enabled")),
        "scilink_labric_cloud_context_enabled": bool(settings.get("scilink_labric_cloud_context_enabled") or settings.get("labric_mcp_enabled")),
        "labric_mcp_url": settings.get("labric_mcp_url") or "",
        "labric_mcp_transport": "stdio",
        "labric_mcp_server_name": "labric",
        "labric_mcp_package_local": True,
        "edison_enabled": True,
        "direct_edison_enabled": bool(settings.get("direct_edison_enabled", False)),
        "literature_analysis_enabled": bool(settings.get("scilink_literature_analysis_enabled", True)),
        "mode": _effective_scilink_mode(settings),
        "requested_mode": str(settings.get("scilink_requested_mode") or settings.get("scilink_mode") or "autonomous"),
        "wait_for_user_questions": bool(settings.get("scilink_wait_for_user_questions", True)),
        "human_feedback_required": bool(settings.get("scilink_wait_for_user_questions", True)),
        "auto_continue_after_question": not bool(settings.get("scilink_wait_for_user_questions", True)),
        "auto_open_html_reports": True,
        "inject_faradaiq_skill_prompt": False,
        "handoff_contract": "exact_user_prompt_plus_job_local_request_json",
        "interactive_session": True,
        "unsafe_execution_authorized": True,
        "unsafe_execution_scope": "current SciLink CLI subprocess only",
    }
    path = job / "scilink_launch_config.json"
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return path



def _load_request_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload) if isinstance(payload, Mapping) else {}
    except Exception:
        return {}


def _scilink_context_path(job: Path, request: Mapping[str, Any]) -> Path | None:
    """Resolve the one compact metadata JSON intended for SciLink inspection."""
    for raw in list(request.get("metadata_paths") or []):
        try:
            path = Path(str(raw)).expanduser().resolve()
            if path.is_file() and path.suffix.lower() == ".json":
                return path
        except Exception:
            continue
    fallback = (job / "scilink_bundle_context.json").resolve()
    return fallback if fallback.is_file() else None


def _job_local_data_folder(job: Path, request: Mapping[str, Any]) -> tuple[Path, list[Path]]:
    """Choose a self-contained SciLink working folder from the reviewed request."""
    existing: list[Path] = []
    for raw in list(request.get("selected_data_paths") or []):
        try:
            path = Path(str(raw)).expanduser().resolve()
            if path.is_file():
                existing.append(path)
        except Exception:
            continue
    preferred = (job / "data").resolve()
    if preferred.is_dir() and any(path.is_file() for path in preferred.iterdir()):
        return preferred, existing
    local = [path for path in existing if str(path).lower().startswith(str(job.resolve()).lower())]
    if local:
        parents = {path.parent for path in local}
        if len(parents) == 1:
            return next(iter(parents)), existing
    return job.resolve(), existing


def _report_html_candidates(job: Path) -> list[Path]:
    """Return SciLink-generated HTML/HTM reports from the active session only."""
    session = (job / "session").resolve()
    if not session.is_dir():
        return []
    candidates: list[Path] = []
    for pattern in ("*.html", "*.htm"):
        for path in session.rglob(pattern):
            try:
                resolved = path.resolve()
                resolved.relative_to(session)
                if resolved.is_file() and resolved.stat().st_size > 0:
                    candidates.append(resolved)
            except Exception:
                continue
    return sorted(set(candidates), key=lambda value: (value.stat().st_mtime, str(value)))


def _open_html_report(path: Path) -> bool:
    """Open one completed local report in the user's default browser."""
    try:
        return bool(webbrowser.open_new_tab(path.resolve().as_uri()))
    except Exception as exc:
        print(f"[REPORT] Could not open {path}: {exc}", flush=True)
        return False


def _scan_and_open_html_reports(
    job: Path,
    state: dict[str, Any],
    *,
    force: bool = False,
    opener: Any = None,
) -> list[Path]:
    """Open new stable SciLink reports once and record an auditable receipt."""
    opener = opener or _open_html_report
    stats: dict[str, tuple[int, int, int]] = state.setdefault("stats", {})
    opened: set[str] = state.setdefault("opened", set())
    receipts: list[dict[str, Any]] = state.setdefault("receipts", [])
    newly_opened: list[Path] = []
    for path in _report_html_candidates(job):
        key = os.path.normcase(str(path))
        if key in opened:
            continue
        try:
            stat = path.stat()
            current = (int(stat.st_size), int(stat.st_mtime_ns))
        except Exception:
            continue
        previous = stats.get(key)
        stable_count = (previous[2] + 1) if previous and previous[:2] == current else 0
        stats[key] = (current[0], current[1], stable_count)
        # During the live session, require one unchanged polling interval so a
        # browser never opens a half-written report. At process exit, force the
        # final scan because SciLink has finished writing its outputs.
        if not force and stable_count < 1:
            continue
        success = bool(opener(path))
        opened.add(key)
        newly_opened.append(path)
        receipts.append({
            "path": str(path),
            "opened": success,
            "opened_at_utc": datetime.now(timezone.utc).isoformat(),
            "size_bytes": current[0],
        })
        print(f"[REPORT] {'Opened' if success else 'Detected'} SciLink HTML report: {path}", flush=True)
    if receipts:
        try:
            (job / "scilink_report_opening.json").write_text(
                json.dumps({
                    "schema": "faradaiq-scilink-report-opening-v290",
                    "version": VERSION,
                    "auto_open_enabled": True,
                    "reports": receipts,
                }, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
    return newly_opened


def _watch_html_reports(job: Path, stop_event: threading.Event, state: dict[str, Any]) -> None:
    while not stop_event.wait(1.5):
        _scan_and_open_html_reports(job, state)

def _finalize_short_generated_names(job: Path) -> dict[str, Any]:
    """Apply the reviewed short-name policy after SciLink exits."""
    try:
        from faradaiq.v273_short_names import finalize_generated_tree, is_enabled
        if not is_enabled():
            return {"enabled": False, "renamed_component_count": 0}
        receipt = finalize_generated_tree(job, enabled=True, scope="completed_scilink_job")
        count = int(receipt.get("renamed_component_count", 0) or 0)
        print(
            f"[NAMES] Short-name post-processing complete: {count} generated component(s) renamed. "
            f"Mapping: {receipt.get('short_name_map_path', '')}",
            flush=True,
        )
        return receipt
    except Exception as exc:
        print(f"[NAMES] Short-name post-processing warning: {exc}", flush=True)
        return {"enabled": True, "error": str(exc), "renamed_component_count": 0}


def _remap_report_state_after_shortening(job: Path, state: dict[str, Any], receipt: Mapping[str, Any]) -> None:
    """Keep the report watcher audit keyed to the final shortened paths."""
    aliases = list(receipt.get("name_aliases") or []) if isinstance(receipt, Mapping) else []
    replacements = [
        (str(row.get("original_relative_path") or "").replace("\\", "/"), str(row.get("short_relative_path") or "").replace("\\", "/"))
        for row in aliases if row.get("original_relative_path") and row.get("short_relative_path")
    ]
    if not replacements:
        return

    def remap(raw: str) -> str:
        try:
            rel = str(Path(raw).resolve().relative_to(job.resolve())).replace("\\", "/")
        except Exception:
            return raw
        for old, new in replacements:
            rel = rel.replace(old, new)
        return os.path.normcase(str((job / Path(rel)).resolve()))

    opened = state.get("opened")
    if isinstance(opened, set):
        state["opened"] = {remap(value) for value in opened}
    stats = state.get("stats")
    if isinstance(stats, dict):
        state["stats"] = {remap(key): value for key, value in stats.items()}
    receipts = state.get("receipts")
    if isinstance(receipts, list):
        for row in receipts:
            if isinstance(row, dict) and row.get("path"):
                row["path"] = remap(str(row["path"]))


def _stdin_single_turn_prompt(prompt: str) -> str:
    """Encode one reviewed multiline prompt as exactly one CLI chat turn.

    The installed SciLink chat loop reads one newline-terminated console line as
    one user message. Writing a Markdown prompt verbatim to that stdin therefore
    submits every Markdown line as a separate follow-up turn, which can make
    SciLink repeat or restart analysis without waiting for the operator. Keep the
    exact UTF-8 prompt in ``analysis_prompt.md`` and the process environment, but
    fold only the pipe transport copy to one whitespace-normalized line.
    """
    normalized = str(prompt).replace("\r\n", "\n").replace("\r", "\n")
    # Preserve all user-authored words and punctuation while converting line
    # boundaries to spaces. Blank paragraphs remain ordinary separation rather
    # than additional terminal submissions.
    return " ".join(part.strip() for part in normalized.split("\n") if part.strip())


def _run_interactive_scilink(
    cmd: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    prompt: str,
    prompt_via_stdin: bool,
    job_dir: Path | None = None,
) -> int:
    """Submit the first prompt and keep the same SciLink session open for follow-up input."""
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=dict(env),
        # A supported prompt flag leaves stdin attached directly to the native
        # terminal. Otherwise FaradaIQ submits the initial prompt through a pipe
        # and continuously forwards later operator input to that same process.
        stdin=subprocess.PIPE if prompt_via_stdin else None,
        text=True,
        bufsize=1,
    )
    stop_reports = threading.Event()
    report_state: dict[str, Any] = {}
    report_thread: threading.Thread | None = None
    if job_dir is not None:
        report_thread = threading.Thread(
            target=_watch_html_reports,
            args=(job_dir, stop_reports, report_state),
            name="FaradaIQ-SciLink-report-watcher",
            daemon=True,
        )
        report_thread.start()

    if prompt_via_stdin:
        if process.stdin is None:
            raise RuntimeError("SciLink stdin could not be opened for the interactive session.")
        wire_prompt = _stdin_single_turn_prompt(prompt)
        if not wire_prompt:
            raise RuntimeError("The reviewed SciLink prompt became empty after transport normalization.")
        process.stdin.write(wire_prompt + "\n")
        process.stdin.flush()
        print("[INTERACTIVE] Initial multiline prompt submitted as one SciLink user turn. Continue typing answers and follow-up messages in this terminal; SciLink remains in the reviewed mode until you exit it.", flush=True)

        def forward_console_input() -> None:
            while process.poll() is None:
                try:
                    line = sys.stdin.readline()
                except Exception:
                    return
                if line == "":
                    # Do not close the child's stdin. Closing it would make SciLink
                    # interpret the review launcher as a one-shot, noninteractive job.
                    return
                try:
                    process.stdin.write(line)
                    process.stdin.flush()
                except (BrokenPipeError, OSError, ValueError):
                    return

        threading.Thread(target=forward_console_input, name="FaradaIQ-SciLink-stdin", daemon=True).start()
    try:
        return int(process.wait())
    except KeyboardInterrupt:
        try:
            process.terminate()
        except Exception:
            pass
        raise
    finally:
        stop_reports.set()
        if report_thread is not None:
            report_thread.join(timeout=3.0)
        if job_dir is not None:
            # SciLink may create deeply nested figure/report trees. Rename only
            # after the process has exited, update JSON references, then open the
            # final stable HTML path.
            short_receipt = _finalize_short_generated_names(job_dir)
            _remap_report_state_after_shortening(job_dir, report_state, short_receipt)
            _scan_and_open_html_reports(job_dir, report_state, force=True)
            try:
                from faradaiq import v290_electrochemistry_routing
                campaign_receipt = v290_electrochemistry_routing.finalize_job(job_dir)
                v290_electrochemistry_routing.update_scilink_status(job_dir, "Completed")
                print("SciLink status: Completed")
                if campaign_receipt.get("campaign_status") == "Concluded":
                    print("[CAMPAIGN] Concluded. Managed transaction stopped; follow-up chat remains available.")
            except Exception as exc:
                print("[WARNING] Campaign conclusion finalization failed:", exc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    job = Path(args.job_dir).resolve()
    prompt_path = job / "analysis_prompt.md"
    manifest_path = job / "scilink_request.json"
    if not manifest_path.exists():
        manifest_path = job / "files_to_share_manifest.json"  # backward-compatible old job folder
    settings_path = job / "service_settings.json"
    if not prompt_path.is_file():
        print(f"[BLOCKED] Missing exact prompt file: {prompt_path}")
        return 2
    prompt = prompt_path.read_text(encoding="utf-8", errors="replace")
    if not prompt.strip():
        print(f"[BLOCKED] Exact user prompt is empty: {prompt_path}")
        return 2
    if not manifest_path.is_file():
        print(f"[BLOCKED] Missing SciLink request/manifest JSON: {manifest_path}")
        return 2
    request = _load_request_object(manifest_path)
    from faradaiq import v290_electrochemistry_skill
    try:
        skill_receipt = v290_electrochemistry_skill.prepare_job_skill(job)
        v290_electrochemistry_skill.update_request_manifest(manifest_path, skill_receipt)
        request = _load_request_object(manifest_path)
    except Exception as exc:
        print(f"[BLOCKED] Bundled electrochemistry skill preparation failed: {exc}")
        return 2
    job_skill_path = Path(str(skill_receipt["job_skill_path"])).resolve()
    working_data_dir, existing_data_paths = _job_local_data_folder(job, request)
    context_path = _scilink_context_path(job, request)
    data_root_errors = _validate_job_data_root(job, working_data_dir, existing_data_paths, context_path)
    if data_root_errors:
        print("[BLOCKED] The reviewed SciLink bundle is not self-contained:")
        for error in data_root_errors:
            print(" -", error)
        return 2
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    except json.JSONDecodeError as exc:
        print(f"[BLOCKED] Invalid service_settings.json: {exc}")
        return 2
    if not isinstance(settings, dict):
        print("[BLOCKED] service_settings.json must contain a JSON object.")
        return 2
    llm = dict(settings.get("llm") or {})

    from faradaiq import v290_electrochemistry_routing
    try:
        routing_receipt = v290_electrochemistry_routing.prepare_job_routing(
            job, request, prompt, settings, skill_receipt
        )
        skill_receipt["transaction_type"] = routing_receipt.get("transaction_type")
        (job / "scilink_skill_receipt.json").write_text(
            json.dumps(skill_receipt, ensure_ascii=True, indent=2), encoding="utf-8"
        )
        v290_electrochemistry_skill.update_request_manifest(
            manifest_path, skill_receipt, transaction_type=routing_receipt.get("transaction_type")
        )
        v290_electrochemistry_routing.update_request_with_routing(manifest_path, routing_receipt)
        request = _load_request_object(manifest_path)
        routing_guard = v290_electrochemistry_routing.prompt_guard_text(routing_receipt)
        series_metadata_receipt = v290_electrochemistry_routing.write_validated_series_metadata(context_path, job)
        routing_receipt["series_metadata_path"] = series_metadata_receipt.get("path")
        routing_receipt["series_metadata_json_validated"] = bool(series_metadata_receipt.get("json_validated"))
        Path(str(routing_receipt.get("routing_plan_path"))).write_text(
            json.dumps(routing_receipt, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    except Exception as exc:
        print(f"[BLOCKED] Electrochemistry application-level routing failed: {exc}")
        return 2

    deterministic_enabled = bool(routing_receipt.get("deterministic_electrochemistry_enabled"))
    analysis_mode = "optional_deterministic" if deterministic_enabled else "standard_scilink"

    env, report = prepare_scilink_environment(settings)
    preflight_path = _write_preflight(job, report)
    config_path = _write_launch_config(job, settings, report)
    env.update({
        "FARADAIQ_SCILINK_JOB_DIR": str(job),
        "SCILINK_INITIAL_PROMPT": prompt,
        "SCILINK_USER_PROMPT": prompt,
        "SCILINK_QUERY": prompt,
        "FARADAIQ_SCILINK_PROMPT_FILE": str(prompt_path),
        "FARADAIQ_SCILINK_REQUEST_JSON": str(manifest_path),
        "FARADAIQ_SCILINK_MANIFEST": str(context_path or manifest_path),
        "FARADAIQ_SCILINK_PREFLIGHT": str(preflight_path),
        "FARADAIQ_SCILINK_DATA_DIR": str(working_data_dir),
        "SCILINK_DATA_DIR": str(working_data_dir),
        "SCILINK_DATA_PATH": str(working_data_dir),
        "SCILINK_INPUT_DIR": str(working_data_dir),
        "SCILINK_INPUT_PATH": str(working_data_dir),
        "SCILINK_PROMPT_FILE": str(prompt_path),
        "SCILINK_DATA_MANIFEST": str(context_path or ""),
        "SCILINK_METADATA_PATH": str(context_path or ""),
        "SCILINK_CONTEXT_JSON": str(context_path or ""),
        "SCILINK_WORKSPACE_DIR": str(working_data_dir),
        "SCILINK_ALLOWED_DATA_ROOT": str(working_data_dir),
        "SCILINK_DISABLE_PARENT_DISCOVERY": "1",
        "FARADAIQ_SCILINK_PARENT_DISCOVERY": "0",
        "PYTHONUTF8": "1",
        "MPLBACKEND": "Agg",
        "SCILINK_CONFIG_FILE": str(config_path),
        "SCILINK_SESSION_CONFIG": str(config_path),
        "LABRIC_MCP_URL": str(settings.get("labric_mcp_url") or ""),
        "MCP_SERVER_URL": str(settings.get("labric_mcp_url") or ""),
        "FARADAIQ_LABRIC_MCP_ENABLED": "1" if (settings.get("scilink_labric_cloud_context_enabled") or settings.get("labric_mcp_enabled")) else "0",
        "FARADAIQ_EDISON_ENABLED": "1",
        "FARADAIQ_DIRECT_EDISON_ENABLED": "1" if settings.get("direct_edison_enabled") else "0",
        "FARADAIQ_SCILINK_LITERATURE_ANALYSIS": "1" if settings.get("scilink_literature_analysis_enabled", True) else "0",
        "FARADAIQ_DISABLE_SKILL_PROMPT": "0",
        "SCILINK_DISABLE_SKILL_PROMPT": "0",
        "FARADAIQ_SCILINK_ELECTROCHEMISTRY_SKILL": str(job_skill_path),
        "SCILINK_SKILLS": str(job_skill_path),
        "SCILINK_SKILL_FILES": str(job_skill_path),
        "FARADAIQ_ELECTROCHEMISTRY_ROUTING_PLAN": str(routing_receipt.get("routing_plan_path") or ""),
        "FARADAIQ_DETERMINISTIC_ECHEM_ANALYSIS": str(routing_receipt.get("deterministic_analysis_path") or ""),
        "FARADAIQ_ECHEM_TRANSACTION_TYPE": json.dumps(routing_receipt.get("transaction_type") or {}, ensure_ascii=True),
        "FARADAIQ_ECHEM_GENERIC_CURVEFIT_ALLOWED": "0",
        "FARADAIQ_ECHEM_ROUTE": "technique_specific_deterministic_electrochemistry",
        "FARADAIQ_ECHEM_INTERPRETATION_ONLY": "1",
        "FARADAIQ_ECHEM_RUN_ANALYSIS_ALLOWED": "0",
        "FARADAIQ_ECHEM_SELECT_AGENT_ALLOWED": "0",
        "SCILINK_N_CANDIDATES": "1",
        "SCILINK_BEST_OF_N": "0",
        "SCILINK_DISABLE_BEST_OF_N": "1",
        "SCILINK_DISABLE_ADAPTIVE_REFIT": "1",
        "SCILINK_DISABLE_GENERIC_R2_GATE": "1",
        "SCILINK_DISABLE_SPECTRAL_PEAK_FITTING": "1",
        "SCILINK_DISABLE_AUTOMATIC_COMPONENT_INSERTION": "1",
        "SCILINK_AUTO_RERUN": "0",
        "SCILINK_MAX_ANALYSIS_ATTEMPTS": "1",
        "SCILINK_MAX_CORRECTED_RETRIES": "0",
        "SCILINK_MAX_MODEL_GENERATION_ATTEMPTS": "1",
        "SCILINK_DISABLE_ANNEALING": "1",
        "SCILINK_DISABLE_REPEATED_VERIFIER_LOOPS": "1",
        "SCILINK_DISABLE_GENERIC_CURVEFIT_FOR_DIRECT_ROUTES": "1",
        "FARADAIQ_SCILINK_INTERACTIVE": "1",
        "SCILINK_INTERACTIVE_SESSION": "1",
        "FARADAIQ_SCILINK_AUTO_OPEN_HTML_REPORTS": "1",
        "FARADAIQ_SCILINK_WAIT_FOR_USER": "1" if settings.get("scilink_wait_for_user_questions", True) else "0",
    })
    if not deterministic_enabled:
        # Standard SciLink mode intentionally keeps the normal SciLink toolchain
        # available. The v7 skill guides scientific choices without imposing
        # application-level tool blocks.
        env.update({
            "FARADAIQ_ECHEM_GENERIC_CURVEFIT_ALLOWED": "1",
            "FARADAIQ_ECHEM_ROUTE": "standard_scilink_guided_electrochemistry",
            "FARADAIQ_ECHEM_INTERPRETATION_ONLY": "0",
            "FARADAIQ_ECHEM_RUN_ANALYSIS_ALLOWED": "1",
            "FARADAIQ_ECHEM_SELECT_AGENT_ALLOWED": "1",
        })
        for key in (
            "SCILINK_N_CANDIDATES", "SCILINK_BEST_OF_N", "SCILINK_DISABLE_BEST_OF_N",
            "SCILINK_DISABLE_ADAPTIVE_REFIT", "SCILINK_DISABLE_GENERIC_R2_GATE",
            "SCILINK_DISABLE_SPECTRAL_PEAK_FITTING", "SCILINK_DISABLE_AUTOMATIC_COMPONENT_INSERTION",
            "SCILINK_AUTO_RERUN", "SCILINK_MAX_ANALYSIS_ATTEMPTS", "SCILINK_MAX_CORRECTED_RETRIES",
            "SCILINK_MAX_MODEL_GENERATION_ATTEMPTS", "SCILINK_DISABLE_ANNEALING",
            "SCILINK_DISABLE_REPEATED_VERIFIER_LOOPS", "SCILINK_DISABLE_GENERIC_CURVEFIT_FOR_DIRECT_ROUTES",
        ):
            env.pop(key, None)

    from faradaiq import v261_labric_mcp
    mcp_report = v261_labric_mcp.apply_process_environment(env, settings, job)
    (job / "labric_mcp_launch_preflight.json").write_text(
        json.dumps(mcp_report, ensure_ascii=True, indent=2), encoding="utf-8"
    )

    print("=" * 72)
    print(f"FaradaIQ Studio {VERSION} -> SciLink interactive exact-prompt analysis job")
    print("=" * 72)
    print("Job folder:", job)
    print("Prompt:    ", prompt_path)
    print("Request JSON:", manifest_path)
    print("SciLink context JSON:", context_path or "not available")
    print("SciLink working data folder:", working_data_dir)
    print("Reviewed data files available:", len(existing_data_paths))
    gui_status = routing_receipt.get("gui_status") or {}
    print("Electrochemistry skill: Applied")
    print("Electrochemistry skill path:", job_skill_path)
    print("Electrochemistry skill SHA-256:", skill_receipt.get("sha256"))
    print("Electrochemistry skill version:", skill_receipt.get("skill_version"))
    print("Detected technique:", gui_status.get("Detected technique") or ", ".join(routing_receipt.get("techniques_detected") or []) or "none")
    if deterministic_enabled:
        print("Analysis mode: Deterministic electrochemistry")
        print("Technique-specific analysis route:", routing_receipt.get("route") or "optional deterministic electrochemistry")
        print("Deterministic preprocessing:", gui_status.get("Deterministic preprocessing") or "Completed")
        print("Files, stages, points or cycles analyzed:", json.dumps(gui_status.get("Files, stages, points or cycles analyzed") or {}, ensure_ascii=True))
        print("SciLink mode: interpretation-only" if routing_receipt.get("interpretation_only") else "SciLink mode: reviewed analysis")
    else:
        print("Analysis mode: Standard SciLink")
        print("SciLink tools: Available")
        print("SciLink mode: reviewed analysis")
    print("SciLink status: Ready to start")
    print("Electrochemistry transaction:", (routing_receipt.get("transaction_type") or {}).get("primary"))
    print("Application routing plan:", routing_receipt.get("routing_plan_path"))
    if deterministic_enabled:
        print("Deterministic analysis file:", routing_receipt.get("deterministic_analysis_path"))
    print("Preflight: ", preflight_path)
    print("Provider:  ", report.get("selected_provider", llm.get("provider", "")))
    print("Model:     ", report.get("selected_model", llm.get("model", "")))
    print("Base URL:  ", report.get("base_url", llm.get("base_url", "")))
    print("LabRIC MCP cloud context:", "enabled" if mcp_report.get("enabled") else "disabled")
    print("LabRIC MCP transport:", mcp_report.get("transport") if mcp_report.get("enabled") else "not launched")
    print("LabRIC MCP server:", mcp_report.get("server_name") if mcp_report.get("enabled") else "not launched")
    print("LabRIC MCP command:", mcp_report.get("command_display") if mcp_report.get("enabled") else "not launched")
    print("LabRIC MCP endpoint:", mcp_report.get("url") if mcp_report.get("enabled") else "not used")
    if routing_receipt.get("interpretation_only"):
        print("Generated-code execution: not used; no generated-code analysis tools exposed")
    else:
        print("Generated-code execution:", "always unlocked for this SciLink process (UNSAFE_EXECUTION_OK=true)")
    print("SciLink interaction mode:", _effective_scilink_mode(settings))
    print("Wait for user questions:", "enabled" if settings.get("scilink_wait_for_user_questions", True) else "disabled")
    print("Interactive follow-up:", "enabled")
    print("Auto-open HTML reports:", "enabled")
    print("Literature analysis:", "enabled" if settings.get("scilink_literature_analysis_enabled", True) else "disabled for this job")
    print("Edison inside SciLink:", "available" if report.get("edison_credentials_available") else "enabled, but key not detected")
    print("Direct Ask Edison button:", "enabled" if settings.get("direct_edison_enabled") else "hidden by launch mode")
    if report.get("cleared_environment_variables"):
        print("Provider isolation cleared:", ", ".join(report["cleared_environment_variables"]))
    print()

    if mcp_report.get("enabled") and not mcp_report.get("api_key_available"):
        print("[BLOCKED] LabRIC MCP cloud context was selected, but LABRIC_API_KEY is not available.")
        print("Open LLM Settings -> Data Sharing, enter the session-only LabRIC key, test the connection, and start a new SciLink job.")
        return 6
    if mcp_report.get("enabled"):
        local_runtime = v261_labric_mcp.validate_local_runtime()
        if not local_runtime.get("ready"):
            print("[BLOCKED] The package-local LabRIC MCP bridge is incomplete:")
            for item in local_runtime.get("missing", []):
                print(" -", item)
            return 6

    if report.get("status") != "ready":
        print("[BLOCKED] SciLink did not start because an HTTP-header value is not ASCII-safe.")
        for error in report.get("errors", []):
            print(" -", error)
        print("The variable name is shown above; its value was not logged or modified.")
        print("Correct that credential/header in LLM Settings or Windows environment, then start a new SciLink job.")
        return 4
    if args.preflight_only:
        print("[OK] SciLink provider/header preflight passed. No process was launched.")
        return 0

    deterministic_interpretation_only = bool(
        routing_receipt.get("interpretation_only")
        and routing_receipt.get("run_analysis_allowed") is False
        and routing_receipt.get("select_agent_allowed") is False
        and int(routing_receipt.get("direct_analysis_count") or 0) > 0
    )
    if deterministic_interpretation_only:
        original_prompt_path = prompt_path
        original_prompt = prompt
        prompt, prompt_path, skill_application_mode = v290_electrochemistry_skill.build_effective_prompt(
            original_prompt=original_prompt,
            job_dir=job,
            receipt=skill_receipt,
            cli_skill_flag_supported=True,
            routing_guard=routing_guard,
            analysis_mode="optional_deterministic",
        )
        env.update({
            "SCILINK_INITIAL_PROMPT": prompt,
            "SCILINK_USER_PROMPT": prompt,
            "SCILINK_QUERY": prompt,
            "FARADAIQ_SCILINK_PROMPT_FILE": str(prompt_path),
            "SCILINK_PROMPT_FILE": str(prompt_path),
            "FARADAIQ_SCILINK_SKILL_APPLICATION_MODE": "native_interpretation_only_skill_binding",
        })
        direct_path = Path(str(routing_receipt.get("deterministic_analysis_path") or "")).resolve(strict=True)
        direct_obj = json.loads(direct_path.read_text(encoding="utf-8"))
        if not isinstance(direct_obj.get("analyses"), list) or not direct_obj["analyses"]:
            print("[BLOCKED] deterministic_electrochemistry_analysis.json is empty. Generic fallback is prohibited.")
            return 7
        interpreter = Path(__file__).resolve().with_name("faradaiq_scilink_interpretation_only.py")
        if not interpreter.is_file():
            print("[BLOCKED] Missing packaged SciLink interpretation-only host:", interpreter)
            return 7
        model = str(report.get("selected_model") or llm.get("model") or "")
        base_url = str(report.get("base_url") or llm.get("base_url") or "")
        if not model or not base_url:
            print("[BLOCKED] Interpretation-only SciLink model/base URL is incomplete.")
            return 7
        cmd = [
            sys.executable, str(interpreter),
            "--job-dir", str(job),
            "--skills", str(job_skill_path),
            "--data", str(working_data_dir.resolve(strict=True)),
            "--metadata", str(Path(context_path).resolve(strict=True)),
            "--routing-plan", str(Path(str(routing_receipt.get("routing_plan_path"))).resolve(strict=True)),
            "--deterministic", str(direct_path),
            "--series-metadata", str(Path(str(series_metadata_receipt.get("path"))).resolve(strict=True)),
            "--prompt-file", str(Path(prompt_path).resolve(strict=True)),
            "--model", model,
            "--base-url", base_url,
            "--session-dir", str((job / "session").resolve()),
        ]
        runtime_report = {
            "schema": "faradaiq-scilink-runtime-preflight-v290",
            "version": VERSION,
            "status": "not_required_interpretation_only",
            "runtime_source": "FaradaIQ SciLink interpretation-only host",
            "python": sys.executable,
            "pythonpath": "",
            "generic_curvefit_skipped": True,
            "analysis_orchestrator_instantiated": False,
            "select_agent_exposed": False,
            "run_analysis_exposed": False,
            "external_scilink_site_packages_modified": False,
        }
        (job / "scilink_runtime_preflight.json").write_text(
            json.dumps(runtime_report, ensure_ascii=True, indent=2), encoding="utf-8"
        )
        submission_report = {
            "schema": "faradaiq-scilink-prompt-submission-v290",
            "version": VERSION,
            "original_prompt_file": str(original_prompt_path),
            "effective_prompt_file": str(prompt_path),
            "request_json": str(manifest_path),
            "submission_method": "SciLink interpretation-only host; no analysis tools",
            "interpretation_only": True,
            "available_tools": [],
            "blocked_tools": list(v290_electrochemistry_routing.PROHIBITED_SCILINK_TOOLS),
            "blocked_attempt_code": "BLOCKED_GENERIC_ANALYSIS_FOR_ELECTROCHEMISTRY",
            "run_analysis_allowed": False,
            "select_agent_allowed": False,
            "analysis_orchestrator_instantiated": False,
            "client_initialization": "OpenAI-compatible direct HTTP client initialized by interpretation-only host",
            "electrochemistry_skill": {
                "path": str(job_skill_path), "sha256": str(skill_receipt.get("sha256") or ""),
                "skill_version": str(skill_receipt.get("skill_version") or ""),
                "cli_flag": "--skills", "application_mode": "native_interpretation_only_skill_binding",
            },
            "electrochemistry_routing": {
                "route": "technique_specific_deterministic_electrochemistry",
                "techniques_detected": routing_receipt.get("techniques_detected"),
                "deterministic_analysis_path": str(direct_path),
                "routing_plan_path": routing_receipt.get("routing_plan_path"),
                "series_metadata_path": series_metadata_receipt.get("path"),
                "series_metadata_json_validated": True,
            },
            "exact_paths_resolved_strict": True,
            "exact_prompt_preserved": True,
            "interactive_follow_up_enabled": True,
            "auto_open_html_reports": True,
        }
        (job / "scilink_prompt_submission.json").write_text(
            json.dumps(submission_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("SciLink runtime Python:", sys.executable)
        print("SciLink analysis dependencies: not_required_interpretation_only")
        print("SciLink mode: interpretation-only")
        print("Deterministic preprocessing: Completed")
        print("Series metadata JSON: validated")
        print("Launching:")
        print("  " + " ".join('"' + x + '"' if " " in x else x for x in cmd))
        print("Any generated SciLink HTML/HTM report will open automatically in the default browser after the file is complete.")
        print("-" * 72)
        try:
            v290_electrochemistry_routing.update_scilink_status(job, "Running")
            print("SciLink status: Running")
            return _run_interactive_scilink(
                cmd,
                cwd=working_data_dir,
                env=env,
                prompt="",
                prompt_via_stdin=False,
                job_dir=job,
            )
        except KeyboardInterrupt:
            return 130
        except Exception as exc:
            print("[ERROR] SciLink interpretation-only launch failed:", exc)
            return 3

    base, sub, help_text = _choose_command(settings)
    if base is None:
        print("[ERROR] SciLink CLI was not found in PATH, C:\\SciLink, C:\\FaradaiqSIED, or the configured override.")
        print("The reviewed job is preserved. Set SCILINK_EXE or choose a SciLink executable in LLM Settings -> Data Sharing.")
        return 2

    skill_flag = next((flag for flag in ("--skills", "--skill-file", "--skill") if _supports(help_text, flag)), "")
    original_prompt_path = prompt_path
    original_prompt = prompt
    prompt, prompt_path, skill_application_mode = v290_electrochemistry_skill.build_effective_prompt(
        original_prompt=original_prompt,
        job_dir=job,
        receipt=skill_receipt,
        cli_skill_flag_supported=bool(skill_flag),
        routing_guard=routing_guard,
        analysis_mode=analysis_mode,
    )
    env.update({
        "SCILINK_INITIAL_PROMPT": prompt,
        "SCILINK_USER_PROMPT": prompt,
        "SCILINK_QUERY": prompt,
        "FARADAIQ_SCILINK_PROMPT_FILE": str(prompt_path),
        "SCILINK_PROMPT_FILE": str(prompt_path),
        "FARADAIQ_SCILINK_SKILL_APPLICATION_MODE": skill_application_mode,
    })

    if routing_receipt.get("requires_generic_curvefit"):
        runtime_report = _ensure_scilink_runtime(base, settings, job)
    else:
        runtime_report = {
            "schema": "faradaiq-scilink-runtime-preflight-v290",
            "version": VERSION,
            "status": "not_required_optional_deterministic_route",
            "runtime_source": "FaradaIQ optional deterministic electrochemistry routing",
            "python": "", "pythonpath": "",
            "generic_curvefit_skipped": True,
            "external_scilink_site_packages_modified": False,
        }
        (job / "scilink_runtime_preflight.json").write_text(
            json.dumps(runtime_report, ensure_ascii=True, indent=2), encoding="utf-8"
        )
    print("SciLink runtime Python:", runtime_report.get("python") or "not required for optional deterministic route")
    print("SciLink analysis dependencies:", runtime_report.get("status"))
    if runtime_report.get("status") == "blocked":
        print("[BLOCKED] SciLink CurveFittingAgent dependencies could not be prepared.")
        print("See:", job / "scilink_runtime_preflight.json")
        return 5
    managed_pythonpath = str(runtime_report.get("pythonpath") or "").strip()
    if managed_pythonpath:
        inherited_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = managed_pythonpath + (os.pathsep + inherited_pythonpath if inherited_pythonpath else "")
        env["PYTHONNOUSERSITE"] = "1"
        env["FARADAIQ_SCILINK_MANAGED_RUNTIME"] = managed_pythonpath
        print("SciLink managed dependency target:", managed_pythonpath)
        print("External SciLink site-packages modified: no")

    provider = str(report.get("provider_key") or "")
    raw_model = str(report.get("selected_model") or "")
    cmd = list(base)
    if sub:
        cmd.append(sub)

    def add(flag: str, value: str | None) -> None:
        if value and _supports(help_text, flag):
            cmd.extend([flag, str(value)])

    add("--mode", _effective_scilink_mode(settings))
    provider_flag_supported = _supports(help_text, "--provider")
    add("--provider", provider)
    # Preserve the exact model configured in Main Studio. In particular, do
    # not prefix Ollama models with ``ollama/`` or strip suffixes such as
    # ``-cloud``. The endpoint expects the native tag verbatim.
    model_for_cli = _model_for_cli(provider, raw_model, provider_flag_supported)
    add("--model", model_for_cli)
    add("--base-url", str(report.get("base_url") or ""))
    add("--session-dir", str(job / "session"))
    if skill_flag:
        cmd.extend([skill_flag, str(job_skill_path)])
    if existing_data_paths:
        for flag in ("--data", "--data-dir", "--input-dir", "--data-path", "--input-path", "--path"):
            if _supports(help_text, flag):
                add(flag, str(working_data_dir))
                break
    for flag in ("--config", "--config-file", "--settings"):
        if _supports(help_text, flag):
            add(flag, str(config_path))
            break
    if _supports(help_text, "--prompt-file"):
        add("--prompt-file", str(prompt_path))
    elif _supports(help_text, "--prompt"):
        add("--prompt", prompt)
    elif _supports(help_text, "--question"):
        add("--question", prompt)
    elif _supports(help_text, "--query"):
        add("--query", prompt)
    if context_path is not None:
        for flag in ("--manifest", "--input-manifest", "--data-manifest", "--metadata", "--metadata-path"):
            if _supports(help_text, flag):
                add(flag, str(context_path))
                break
    # Use SciLink's supported --mcp JSON configuration. The configuration
    # points to FaradaIQ's package-local stdio bridge; the remote HTTP endpoint
    # and bearer key remain in the authorized child-process environment.
    if mcp_report.get("enabled"):
        mcp_config = str(mcp_report.get("scilink_mcp_config") or "")
        if not _supports(help_text, "--mcp"):
            print("[BLOCKED] The selected SciLink CLI does not expose the supported --mcp option.")
            print("Update SciLink to a version with 'scilink analyze --mcp', then retry this preserved job.")
            return 6
        cmd.extend(["--mcp", mcp_config])

    prompt_flags = ("--prompt-file", "--prompt", "--question", "--query")
    submission_method = next((flag for flag in prompt_flags if flag in cmd), "stdin + prompt environment aliases")
    submission_report = {
        "schema": "faradaiq-scilink-prompt-submission-v290",
        "version": VERSION,
        "original_prompt_file": str(original_prompt_path),
        "prompt_file": str(prompt_path),
        "effective_prompt_file": str(prompt_path),
        "request_json": str(manifest_path),
        "electrochemistry_skill": {
            "name": "electrochemistry_skill",
            "path": str(job_skill_path),
            "sha256": str(skill_receipt.get("sha256") or ""),
            "copied_unchanged": True,
            "required": True,
            "analysis_only": True,
            "hardware_control_allowed": False,
            "cli_flag": skill_flag or None,
            "application_mode": skill_application_mode,
            "skill_version": str(skill_receipt.get("skill_version") or ""),
            "canonical_h2_exact": bool(skill_receipt.get("canonical_h2_exact")),
            "loaded_from_extras": False,
        },
        "electrochemistry_routing": {
            "transaction_type": routing_receipt.get("transaction_type"),
            "plan_path": routing_receipt.get("routing_plan_path"),
            "deterministic_analysis_path": routing_receipt.get("deterministic_analysis_path"),
            "techniques_detected": routing_receipt.get("techniques_detected"),
            "dummy_cell_detected": routing_receipt.get("dummy_cell_detected"),
            "requires_generic_curvefit": False,
            "generic_curve_fitting_allowed": False,
            "curve_fitting_agent_required": False,
            "spectral_peak_fitting_allowed": False,
            "best_of_n_allowed": False,
            "adaptive_refit_allowed": False,
            "generic_r2_gate_allowed": False,
            "deterministic_analysis_required": True,
            "routing_conflicts": routing_receipt.get("routing_conflicts"),
            "gui_status_path": routing_receipt.get("gui_status_path"),
            "drt_policy": routing_receipt.get("drt_policy"),
            "campaign": routing_receipt.get("campaign"),
        },
        "scilink_context_json": str(context_path or ""),
        "submission_method": submission_method,
        "prompt_character_count": len(prompt),
        "prompt_sha256": __import__("hashlib").sha256(prompt.encode("utf-8")).hexdigest(),
        "exact_prompt_preserved": True,
        "stdin_single_turn_transport": submission_method == "stdin + prompt environment aliases",
        "stdin_transport_character_count": len(_stdin_single_turn_prompt(prompt)) if submission_method == "stdin + prompt environment aliases" else None,
        "multiline_prompt_fragmentation_prevented": True,
        "interactive_follow_up_enabled": True,
        "wait_for_user_questions": bool(settings.get("scilink_wait_for_user_questions", True)),
        "effective_scilink_mode": _effective_scilink_mode(settings),
        "auto_open_html_reports": True,
        "unsafe_execution_authorized": bool(report.get("unsafe_execution_authorized")),
        "runtime_dependency_status": runtime_report.get("status"),
        "runtime_dependency_source": runtime_report.get("runtime_source"),
        "managed_runtime_pythonpath": runtime_report.get("pythonpath", ""),
        "external_scilink_site_packages_modified": False,
        "working_directory": str(working_data_dir),
        "parent_directory_discovery_disabled": True,
        "labric_mcp_cloud_context_enabled": bool(mcp_report.get("enabled")),
        "labric_mcp_transport": mcp_report.get("transport"),
        "labric_mcp_server_name": mcp_report.get("server_name"),
        "labric_mcp_command": mcp_report.get("command"),
        "labric_mcp_package_local": True,
        "labric_mcp_config": mcp_report.get("scilink_mcp_config"),
        "labric_mcp_secret_written_to_config": False,
    }
    (job / "scilink_prompt_submission.json").write_text(
        json.dumps(submission_report, ensure_ascii=True, indent=2), encoding="utf-8"
    )

    print("Exact user prompt that will be submitted:")
    print("---------------- BEGIN USER PROMPT ----------------")
    print(prompt)
    print("----------------- END USER PROMPT -----------------")
    print("Submission method:", submission_method)
    print("Electrochemistry skill application:", skill_application_mode)
    print("Electrochemistry skill CLI flag:", skill_flag or "prompt fallback")
    print()
    print("Launching:")
    print("  " + " ".join('"' + x + '"' if " " in x else x for x in cmd))
    print()
    print("The exact UTF-8 user prompt and request JSON are in the job folder.")
    if submission_method == "stdin + prompt environment aliases":
        print("Multiline prompt transport: one SciLink user turn; prompt lines will not replay as automatic follow-up requests.")
    print("This is a new independent, interactive SciLink session.")
    print("The terminal remains connected after the first response so you can answer questions and request follow-up analysis.")
    if settings.get("scilink_wait_for_user_questions", True):
        print("Question gate enabled: explicit SciLink questions wait for your answer without changing the reviewed autonomy mode.")
    print("Any generated SciLink HTML/HTM report will open automatically in the default browser after the file is complete.")
    print("-" * 72)
    try:
        v290_electrochemistry_routing.update_scilink_status(job, "Running")
        print("SciLink status: Running")
        use_stdin = not any(f in cmd for f in prompt_flags)
        return _run_interactive_scilink(
            cmd,
            cwd=working_data_dir,
            env=env,
            prompt=prompt,
            prompt_via_stdin=use_stdin,
            job_dir=job,
        )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print("[ERROR] SciLink launch failed:", exc)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
