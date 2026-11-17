from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import importlib.util

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("faradaiq_llm_cli_job_v253", ROOT / "faradaiq_llm_cli_job.py")
assert _SPEC is not None and _SPEC.loader is not None
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


class _RecorderHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    response_status = 200
    response_body: dict | str = {
        "choices": [{"message": {"content": "FaradaIQ test response"}}]
    }

    def do_POST(self):  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(size)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = None
        type(self).requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "payload": payload,
            }
        )
        body = type(self).response_body
        encoded = (
            json.dumps(body).encode("utf-8")
            if isinstance(body, dict)
            else str(body).encode("utf-8")
        )
        self.send_response(type(self).response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        return


def _job(tmp_path: Path, *, prompt: str, provider: str, model: str, base_url: str, timeout=3600) -> Path:
    job = tmp_path / "job"
    job.mkdir()
    (job / "analysis_prompt.md").write_text(prompt, encoding="utf-8")
    (job / "service_settings.json").write_text(
        json.dumps(
            {
                "llm": {
                    "provider": provider,
                    "model": model,
                    "base_url": base_url,
                    "timeout_seconds": timeout,
                    "api_key_present": True,
                }
            }
        ),
        encoding="utf-8",
    )
    return job


def test_preflight_preserves_exact_model_prompt_and_hides_secret(monkeypatch, tmp_path):
    job = _job(
        tmp_path,
        prompt="Exact user prompt\nwith formatting.",
        provider="Ollama",
        model="gpt-oss:120b-cloud",
        base_url="http://localhost:11434",
    )
    monkeypatch.setenv("OLLAMA_API_KEY", "do-not-write-this-secret")
    assert runner.main(["--job-dir", str(job), "--preflight-only"]) == 0
    preflight = json.loads((job / "llm_preflight.json").read_text(encoding="utf-8"))
    result = json.loads((job / "llm_response.json").read_text(encoding="utf-8"))
    assert preflight["status"] == "ready"
    assert preflight["model"] == "gpt-oss:120b-cloud"
    assert preflight["endpoint"] == "http://localhost:11434/v1/chat/completions"
    assert preflight["timeout_seconds"] == 3600
    assert result["status"] == "preflight_ready"
    assert result["experimental_file_upload"] is False
    all_text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in job.iterdir() if p.is_file())
    assert "do-not-write-this-secret" not in all_text


def test_openai_compatible_transaction_uses_exact_prompt_model_and_endpoint(monkeypatch, tmp_path):
    _RecorderHandler.requests = []
    _RecorderHandler.response_status = 200
    _RecorderHandler.response_body = {"choices": [{"message": {"content": "completed answer"}}]}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecorderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        exact = "Analyze this exact text only.\nNo files."
        job = _job(tmp_path, prompt=exact, provider="Custom OpenAI-compatible", model="model:exact", base_url=base)
        monkeypatch.setenv("CUSTOM_LLM_API_KEY", "custom-key")
        monkeypatch.setenv("OPENAI_API_KEY", "stale-openai-key")
        assert runner.main(["--job-dir", str(job)]) == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert len(_RecorderHandler.requests) == 1
    request = _RecorderHandler.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == "Bearer custom-key"
    assert request["payload"]["model"] == "model:exact"
    assert request["payload"]["messages"] == [{"role": "user", "content": exact}]
    result = json.loads((job / "llm_response.json").read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert result["answer"] == "completed answer"
    assert result["credential_source"] == "CUSTOM_LLM_API_KEY"


def test_litellm_key_wins_over_unrelated_openai_key(monkeypatch, tmp_path):
    _RecorderHandler.requests = []
    _RecorderHandler.response_status = 200
    _RecorderHandler.response_body = {"choices": [{"message": {"content": "proxy answer"}}]}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecorderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/api/v1/chat/completions"
        job = _job(tmp_path, prompt="proxy prompt", provider="LiteLLM", model="proxy-model", base_url=endpoint)
        monkeypatch.setenv("LITELLM_KEY", "litellm-key")
        monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
        assert runner.main(["--job-dir", str(job)]) == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert _RecorderHandler.requests[0]["path"] == "/api/v1/chat/completions"
    assert _RecorderHandler.requests[0]["authorization"] == "Bearer litellm-key"


def test_invalid_url_and_empty_prompt_are_blocked_with_durable_results(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    job = _job(tmp_path, prompt="", provider="OpenAI", model="model", base_url="not-a-url")
    assert runner.main(["--job-dir", str(job), "--preflight-only"]) == 2
    result = json.loads((job / "llm_response.json").read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert "prompt is empty" in result["error"].lower()

    (job / "analysis_prompt.md").write_text("valid prompt", encoding="utf-8")
    assert runner.main(["--job-dir", str(job), "--preflight-only"]) == 2
    result = json.loads((job / "llm_response.json").read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert "absolute http" in result["error"].lower()


def test_invalid_timeout_is_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama")
    job = _job(tmp_path, prompt="valid", provider="Ollama", model="m", base_url="http://localhost:11434/v1", timeout=0)
    assert runner.main(["--job-dir", str(job), "--preflight-only"]) == 2
    result = json.loads((job / "llm_response.json").read_text(encoding="utf-8"))
    assert "timeout" in result["error"].lower()
