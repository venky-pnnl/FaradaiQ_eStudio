#!/usr/bin/env python
"""Run one exact-prompt, text-only LLM transaction.

The runner accepts an OpenAI-compatible HTTP endpoint, never uploads experiment
files, keeps credentials in process memory, and always writes a durable result
record for completed, blocked, and failed transactions.
"""
from __future__ import annotations

import argparse
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

try:
    from faradaiq.release_identity import APP_VERSION as VERSION
except Exception:
    VERSION = "v292"
DEFAULT_MODEL = "gpt-oss:120b-cloud"
DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_TIMEOUT_SECONDS = 3600


class JobConfigurationError(ValueError):
    """Raised when a local LLM job is incomplete or unsafe to submit."""


def _read_json(path: Path, *, required: bool = False) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise JobConfigurationError(f"Missing required JSON file: {path}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise JobConfigurationError(f"Invalid JSON in {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise JobConfigurationError(f"{path.name} must contain a JSON object.")
    return payload


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _normalize_endpoint(raw_url: str) -> tuple[str, str]:
    """Return ``(api_base, chat_completions_endpoint)`` for an HTTP(S) URL."""
    value = str(raw_url or "").strip().rstrip("/")
    if not value:
        raise JobConfigurationError("The LLM base URL is empty.")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise JobConfigurationError(
            "The LLM base URL must be an absolute http:// or https:// URL."
        )
    # Credentials embedded in URLs can leak into logs. Require headers instead.
    if parsed.username is not None or parsed.password is not None:
        raise JobConfigurationError("Do not embed credentials in the LLM base URL.")
    clean = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), parsed.query, ""))
    if clean.endswith("/chat/completions"):
        api_base = clean[: -len("/chat/completions")].rstrip("/")
        endpoint = clean
    elif clean.endswith("/v1"):
        api_base = clean
        endpoint = clean + "/chat/completions"
    else:
        api_base = clean + "/v1"
        endpoint = api_base + "/chat/completions"
    return api_base, endpoint


def _provider_key(provider: str, env: Mapping[str, str]) -> tuple[str, str]:
    """Resolve only credentials appropriate for the configured provider."""
    key = str(provider or "").strip().lower()
    if "ollama" in key:
        candidates = (
            ("OLLAMA_API_KEY", env.get("OLLAMA_API_KEY")),
            ("SCILINK_API_KEY", env.get("SCILINK_API_KEY")),
            ("OPENAI_API_KEY", env.get("OPENAI_API_KEY")),
        )
        fallback = ("Ollama local placeholder", "ollama")
    elif "litellm" in key or "proxy" in key:
        candidates = (
            ("LITELLM_KEY", env.get("LITELLM_KEY")),
            ("OPENAI_API_KEY", env.get("OPENAI_API_KEY")),
            ("CUSTOM_LLM_API_KEY", env.get("CUSTOM_LLM_API_KEY")),
        )
        fallback = ("", "")
    elif "incubator" in key or "pnnl" in key:
        candidates = (
            ("AI_INCUBATOR_API_KEY", env.get("AI_INCUBATOR_API_KEY")),
            ("OPENAI_API_KEY", env.get("OPENAI_API_KEY")),
        )
        fallback = ("", "")
    elif "custom" in key:
        candidates = (
            ("CUSTOM_LLM_API_KEY", env.get("CUSTOM_LLM_API_KEY")),
            ("OPENAI_API_KEY", env.get("OPENAI_API_KEY")),
        )
        fallback = ("", "")
    else:
        candidates = (
            ("OPENAI_API_KEY", env.get("OPENAI_API_KEY")),
            ("CUSTOM_LLM_API_KEY", env.get("CUSTOM_LLM_API_KEY")),
        )
        fallback = ("", "")
    for source, value in candidates:
        secret = str(value or "")
        if secret:
            try:
                secret.encode("ascii")
            except UnicodeEncodeError as exc:
                raise JobConfigurationError(
                    f"{source} contains a non-ASCII character and cannot be used in an HTTP header."
                ) from exc
            return source, secret
    return fallback


def _timeout_seconds(llm: Mapping[str, Any], env: Mapping[str, str]) -> int:
    raw: Any = llm.get("timeout_seconds")
    if raw is None or not str(raw).strip():
        raw = _first_nonempty(
            env.get("FARADAIQ_LLM_TIMEOUT_SECONDS"),
            env.get("LLM_TIMEOUT_SECONDS"),
            DEFAULT_TIMEOUT_SECONDS,
        )
    try:
        value = int(float(raw))
    except (TypeError, ValueError) as exc:
        raise JobConfigurationError(f"Invalid LLM timeout value: {raw!r}") from exc
    if value < 1 or value > 86400:
        raise JobConfigurationError("LLM timeout must be between 1 and 86400 seconds.")
    return value


def _extract_answer(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts)
    text = first.get("text")
    return text if isinstance(text, str) else ""


def _write_result(job: Path, result: Mapping[str, Any]) -> None:
    (job / "llm_response.json").write_text(
        json.dumps(dict(result), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (job / "llm_response.txt").write_text(
        str(result.get("answer") or result.get("error") or ""),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate prompt, settings, endpoint, timeout, and credential routing without sending a request.",
    )
    args = parser.parse_args(argv)
    job = Path(args.job_dir).expanduser().resolve()
    job.mkdir(parents=True, exist_ok=True)
    log_path = job / "llm_transaction.log"

    def log(value: object = "") -> None:
        text = str(value)
        print(text, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")

    started = datetime.now(timezone.utc).isoformat()
    result: dict[str, Any] = {
        "schema": "faradaiq-text-llm-response-v290",
        "version": VERSION,
        "started_utc": started,
        "status": "failed",
    }
    try:
        if not job.is_dir():
            raise JobConfigurationError(f"Job directory is not accessible: {job}")
        prompt_path = job / "analysis_prompt.md"
        settings_path = job / "service_settings.json"
        if not prompt_path.is_file():
            raise JobConfigurationError(f"Missing exact prompt file: {prompt_path}")
        prompt = prompt_path.read_text(encoding="utf-8", errors="replace")
        if not prompt.strip():
            raise JobConfigurationError("The exact user prompt is empty.")
        settings = _read_json(settings_path)
        llm = dict(settings.get("llm") or {})
        provider = _first_nonempty(
            llm.get("provider"), os.environ.get("FARADAIQ_LLM_PROVIDER"), "Ollama"
        )
        model = _first_nonempty(
            llm.get("model"),
            os.environ.get("FARADAIQ_LLM_MODEL"),
            os.environ.get("OLLAMA_MODEL"),
            DEFAULT_MODEL,
        )
        if not model:
            raise JobConfigurationError("The LLM model is empty.")
        raw_base = _first_nonempty(
            llm.get("base_url"),
            os.environ.get("FARADAIQ_LLM_BASE_URL"),
            os.environ.get("OPENAI_BASE_URL"),
            os.environ.get("OLLAMA_BASE_URL"),
            DEFAULT_BASE_URL,
        )
        api_base, endpoint = _normalize_endpoint(raw_base)
        timeout = _timeout_seconds(llm, os.environ)
        key_source, key = _provider_key(provider, os.environ)
        if not key:
            raise JobConfigurationError(
                f"No API key was found for provider {provider!r}. Configure it in LLM Settings."
            )

        result.update(
            {
                "provider": provider,
                "model": model,
                "base_url": api_base,
                "endpoint": endpoint,
                "timeout_seconds": timeout,
                "credential_source": key_source,
                "experimental_file_upload": False,
                "exact_prompt_file": str(prompt_path),
            }
        )
        log("=" * 72)
        log(f"FaradaIQ Studio {VERSION} -> text-only LLM transaction")
        log("=" * 72)
        log(f"Provider: {provider}")
        log(f"Model: {model}")
        log(f"Base URL: {api_base}")
        log(f"Timeout: {timeout} seconds")
        log(f"Credential source: {key_source}")
        log("Experimental files: none")
        log("")
        log("Exact user prompt")
        log("---------------- BEGIN USER PROMPT ----------------")
        log(prompt.rstrip())
        log("----------------- END USER PROMPT -----------------")

        preflight = {
            "schema": "faradaiq-text-llm-preflight-v290",
            "version": VERSION,
            "status": "ready",
            "provider": provider,
            "model": model,
            "base_url": api_base,
            "endpoint": endpoint,
            "timeout_seconds": timeout,
            "credential_source": key_source,
            "credential_present": bool(key),
            "credential_value_logged": False,
            "experimental_file_upload": False,
            "exact_prompt_bytes": len(prompt.encode("utf-8")),
        }
        (job / "llm_preflight.json").write_text(
            json.dumps(preflight, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if args.preflight_only:
            result.update({"status": "preflight_ready", "preflight_only": True})
            _write_result(job, result)
            log("\n[PREFLIGHT READY] No network request was sent.")
            return 0

        import requests

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "stream": False,
        }
        log(f"\n[REQUEST] POST {endpoint}")
        log(json.dumps(payload, ensure_ascii=False, indent=2))
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        log(f"[HTTP] {response.status_code}")
        if not response.ok:
            body = response.text[:8000]
            raise RuntimeError(f"LLM HTTP {response.status_code}: {body}")
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"LLM returned non-JSON content: {response.text[:8000]}"
            ) from exc
        answer = _extract_answer(data)
        if not answer.strip():
            raise RuntimeError("LLM response did not contain a non-empty assistant answer.")
        result.update(
            {
                "status": "completed",
                "answer": answer,
                "raw_response": data,
                "http_status": response.status_code,
            }
        )
        log("\n[RESPONSE]")
        log(answer)
        code = 0
    except JobConfigurationError as exc:
        result.update({"status": "blocked", "error": str(exc)})
        log("\n[BLOCKED]")
        log(str(exc))
        code = 2
    except Exception as exc:
        result.update(
            {
                "status": "failed",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        log("\n[ERROR]")
        log(str(exc))
        log(traceback.format_exc())
        code = 4
    result["finished_utc"] = datetime.now(timezone.utc).isoformat()
    _write_result(job, result)
    log(f"\nSaved: {job / 'llm_response.json'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
