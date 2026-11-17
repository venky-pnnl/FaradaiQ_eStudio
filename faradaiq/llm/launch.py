"""Paths for bundled LLM setup launchers."""
from __future__ import annotations

from pathlib import Path

from ..runtime.paths import bundled_path

def litellm_proxy_dir() -> Path:
    return bundled_path("litellm_proxy")

def start_litellm_proxy_bat() -> Path:
    preferred = litellm_proxy_dir() / "start_faradaiq_litellm_proxy.bat"
    return preferred if preferred.exists() else bundled_path("start_faradaiq_litellm_proxy.bat")

def llm_setup_wizard_bat() -> Path:
    return bundled_path("run_faradaiq_llm_setup_wizard.bat")
