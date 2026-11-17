"""FaradaIQ Studio v273 LLM, SciLink, and Edison QA hardening.

This release keeps the v251 synchronized-context workflow and adds production
hardening for direct text-only LLM transactions, explicit service dependencies,
provider-specific credential routing, endpoint validation, durable preflight
records, and a 3600-second default transaction timeout.
"""
from __future__ import annotations

import os
import re
from typing import Any, MutableMapping

VERSION = "v273"
_CORE: MutableMapping[str, Any] = {}


def _install_service_timeout() -> None:
    import faradaiq.v239_scilink_edison as services

    previous = services._service_settings
    if getattr(previous, "_v253_timeout", False):
        return

    def service_settings(cfg=None):
        payload = dict(previous(cfg))
        llm = dict(payload.get("llm") or {})
        raw = str(
            llm.get("timeout_seconds")
            or os.environ.get("FARADAIQ_LLM_TIMEOUT_SECONDS")
            or "3600"
        ).strip()
        try:
            timeout = int(float(raw))
        except (TypeError, ValueError):
            timeout = 3600
        llm["timeout_seconds"] = max(1, min(86400, timeout))
        payload["llm"] = llm
        return payload

    service_settings._v253_timeout = True  # type: ignore[attr-defined]
    services._service_settings = service_settings



def _walk_widgets(root):
    yield root
    try:
        for child in root.winfo_children():
            yield from _walk_widgets(child)
    except Exception:
        return


def _clean_browser_llm_copy(browser) -> None:
    """Remove late-created legacy copy that implied raw H5/JSON LLM upload."""
    replacement = (
        "Smart Suggestions may send only the typed request and compact derived structure to the configured LLM. "
        "Experimental files remain local unless explicitly checked in a reviewed SciLink or Edison job."
    )
    relabel = {
        "Use LLM for Ask AI": "Use LLM for Smart Suggestions",
        "LLM / Local transactions": "Smart Suggestion transactions",
        "Ask AI for custom comparison suggestions": "Scientific analysis request",
        "AI-suggested comparisons": "LLM-assisted Smart Suggestions",
    }
    for widget in list(_walk_widgets(browser)):
        try:
            current = str(widget.cget("text") or "").strip()
        except Exception:
            current = ""
        if not current:
            continue
        if current in relabel:
            try:
                widget.configure(text=relabel[current])
            except Exception:
                pass
        elif any(
            phrase in current
            for phrase in (
                "Suggest sends the H5/JSON",
                "send the H5/JSON map",
                "Ask AI = LLM",
                "Ask AI can use LLM",
                "Ask Local never",
            )
        ):
            try:
                widget.configure(text=replacement)
            except Exception:
                pass
        elif current in {"Ask AI", "Ask Local"}:
            try:
                if widget.winfo_class() in {"TButton", "Button"}:
                    widget.destroy()
            except Exception:
                pass


def _install_browser_llm_copy_cleanup() -> None:
    Browser = _CORE.get("FaradaIQH5Browser")
    if Browser is None or getattr(Browser, "_v253_llm_copy_cleanup", False):
        return
    previous = Browser._build_visible_ui_first

    def build(self, *args, **kwargs):
        result = previous(self, *args, **kwargs)
        for delay in (0, 25, 150, 500, 1200, 2500, 5000):
            try:
                self.after(delay, lambda b=self: _clean_browser_llm_copy(b))
            except Exception:
                pass
        return result

    Browser._build_visible_ui_first = build
    Browser._v253_llm_copy_cleanup = True

def _normalize_titles() -> None:
    for cls_name in ("SessionReaderGUI", "FaradaIQH5Browser", "FaradaIQDataBrowser"):
        cls = _CORE.get(cls_name)
        if cls is None or getattr(cls, "_v253_title_patch", False):
            continue
        old_title = getattr(cls, "title", None)
        if not callable(old_title):
            continue

        def title(self, text=None, _old=old_title):
            if text is None:
                return _old(self)
            return _old(self, re.sub(r"v\d+(?:\.\d+)?", VERSION, str(text)))

        cls.title = title
        cls.wm_title = title
        cls._v253_title_patch = True


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE
    _CORE = core

    import faradaiq.multidim_v216 as multidim
    import faradaiq.v228_intelligence as intelligence
    import faradaiq.v239_scilink_edison as services
    import faradaiq.v244_workflow_cleanup as cleanup
    import faradaiq.v245_queue_scilink as queue
    import faradaiq.v247_experience as experience
    import faradaiq.v250_reliability as reliability
    import faradaiq.v251_context_scilink as context

    for module in (
        multidim,
        intelligence,
        services,
        cleanup,
        queue,
        experience,
        reliability,
        context,
    ):
        try:
            module.VERSION = VERSION
        except Exception:
            pass

    _install_service_timeout()
    _install_browser_llm_copy_cleanup()
    _normalize_titles()
    core["_v253_llm_default_timeout_seconds"] = 3600
    core["_v253_ai_transaction_hardening"] = True
