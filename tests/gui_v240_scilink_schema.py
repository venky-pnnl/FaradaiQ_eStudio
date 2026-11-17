from __future__ import annotations

import tempfile
import time
import tkinter as tk
from pathlib import Path

import faradaiq_studio_core as core
import faradaiq.v239_scilink_edison as workflow


class DummyRoot(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()
        self._faradaiq_theme_mode = "dark"
        self.base_name = "v240_smoke"

    def set_status(self, *_args, **_kwargs):
        return None


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


root = DummyRoot()
with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp)
    csv = folder / "CV" / "point_01" / "scanrate_25mVs" / "cycle_03.csv"
    csv.parent.mkdir(parents=True)
    csv.write_text("Potential_V,Current_A\n0,0\n", encoding="utf-8")
    browser = core.FaradaIQH5Browser(
        root,
        cache_info={
            "cache_root": str(folder),
            "manifest": {"techniques_detected": ["CV / LSV"], "technique_counts": {"CV / LSV": 1}},
        },
        base_name="v240_smoke",
    )
    browser.withdraw()
    end = time.time() + 4.2
    while time.time() < end:
        browser.update_idletasks()
        browser.update()
        time.sleep(0.05)

    texts = []
    notebook_tabs = []
    for widget in walk(browser):
        try:
            value = str(widget.cget("text")).strip()
            if value:
                texts.append(value)
        except Exception:
            pass
        try:
            if widget.winfo_class() in {"TNotebook", "Notebook"}:
                notebook_tabs.extend(str(widget.tab(tab, "text")) for tab in widget.tabs())
        except Exception:
            pass

    assert browser.title().startswith("FaradaIQ Studio v293")
    assert getattr(browser, "_v240_visual_schema_added", False)
    assert getattr(browser, "_v239_handoff_bar_added", False)
    assert "Visual Data Schema" in notebook_tabs
    assert "Ask SciLink" in texts
    assert "Ask Edison Scientific" in texts
    assert "Ask AI" not in texts
    assert "Ask Local" not in texts
    assert "Use LLM for Smart Suggestions" in texts
    assert not any("sends the H5/JSON" in text for text in texts)

    review = workflow.show_analysis_review(browser, "scilink", prompt_override="Compare the third CV cycle.")
    review.withdraw()
    review.update_idletasks()
    review_tabs = []
    review_texts = []
    for widget in walk(review):
        try:
            value = str(widget.cget("text")).strip()
            if value:
                review_texts.append(value)
        except Exception:
            pass
        try:
            if widget.winfo_class() in {"TNotebook", "Notebook"}:
                review_tabs.extend(str(widget.tab(tab, "text")) for tab in widget.tabs())
        except Exception:
            pass
    assert {"Prompt", "Files to Share", "Service Settings"}.issubset(set(review_tabs))
    assert "Start SciLink" in review_texts
    assert any("Nothing starts until you approve" in text for text in review_texts)
    checkbox_values = []
    checkbox_labels = []
    prompt_bodies = []
    for widget in walk(review):
        try:
            if widget.winfo_class() in {"TCheckbutton", "Checkbutton"}:
                checkbox_values.append(bool(review.getvar(widget.cget("variable"))))
                checkbox_labels.append(str(widget.cget("text") or ""))
        except Exception:
            pass
        try:
            if widget.winfo_class() == "Text":
                prompt_bodies.append(widget.get("1.0", "end"))
        except Exception:
            pass
    assert checkbox_values and any(checkbox_values)
    assert any("generated-code execution is unlocked" in label and value for label, value in zip(checkbox_labels, checkbox_values))
    assert any("Pause and wait for my input" in label and value for label, value in zip(checkbox_labels, checkbox_values))
    assert any(body.strip() == "Compare the third CV cycle." for body in prompt_bodies)
    assert not any(workflow._STRUCTURE_MARKER in body for body in prompt_bodies)
    review.destroy()
    browser.destroy()
root.destroy()
print("v240 SciLink/Edison/schema GUI smoke: PASS")
