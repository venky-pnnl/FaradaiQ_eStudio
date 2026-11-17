from __future__ import annotations

import inspect
import os
from pathlib import Path


def test_launcher_keeps_llm_tools_enabled_for_all_choices():
    root = Path(__file__).resolve().parents[1]
    text = (root / "Start_FaradaIQ_Studio_v320.bat").read_text(encoding="utf-8")
    assert text.count('set "FARADAIQ_LLM_TOOLS_ENABLED=1"') >= 4
    assert 'Direct LLM tools stay hidden' not in text
    for label in ("LLM-guided prompt", "Smart Suggestions", "AI Chat", "Ask LLM"):
        assert label in text


def test_runtime_never_hides_direct_llm_tools(monkeypatch):
    monkeypatch.setenv("FARADAIQ_SKIP_STARTUP_AI_SELECTOR", "1")
    import faradaiq_studio_core as core
    from faradaiq import v250_reliability as v250
    source = inspect.getsource(v250._apply_browser_mode_visibility)
    assert 'AI Chat' not in source or 'not _llm_tools_enabled()' not in source
    for mode in (v250.AI_MODE_SCILINK, v250.AI_MODE_SCILINK_EDISON, v250.AI_MODE_ALL):
        monkeypatch.setenv("FARADAIQ_AI_LAUNCH_MODE", mode)
        monkeypatch.setenv("FARADAIQ_LLM_TOOLS_ENABLED", "0")
        assert v250._llm_tools_enabled() is True
