from __future__ import annotations

import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faradaiq import ui_tabs_v226


class Browser:
    def __init__(self, root: tk.Tk, data: Path):
        self.root = root
        self.tree = ttk.Treeview(root)
        self.tree.pack()
        tech = self.tree.insert("", "end", text="Chronoamperometry (CA)", values=("technique", "170"))
        method = self.tree.insert(tech, "end", text="Method 01 — ChronoPotStat", values=("method", ""))
        self.point = self.tree.insert(method, "end", text="Point 001", values=("point", ""))
        self.tree.item(tech, open=True); self.tree.item(method, open=True)
        self.tree.selection_set(self.point)
        self.selected_path = data
        self._record_by_iid = {self.point: {"path": str(data), "technique_key": "CA"}}
        self._path_by_iid = {self.point: data}
        self.ai_compare_entry = ttk.Entry(root)
        self.calls = []

    def selected_paths(self, plottable_only=False):
        return [self.selected_path]

    def _open_pysimple(self): self.calls.append("pysimple")
    def suggest_comparisons(self): self.calls.append("suggest")
    def ask_ai_comparisons(self): self.calls.append("ask")
    def apply_selected_suggestion(self): self.calls.append("apply")


with tempfile.TemporaryDirectory() as tmp:
    data = Path(tmp) / "CA_point_001_method_01_cycle_01.csv"
    data.write_text("time_s,current_A\n0,-1e-6\n", encoding="utf-8")
    root = tk.Tk(); root.geometry("760x760+20+20"); root.deiconify()
    browser = Browser(root, data)
    panel = ttk.Frame(root); panel.pack(fill="both", expand=True)
    calls = []
    core = {
        "_v138_open_deareis_with_selected": lambda *a, **k: calls.append("deareis"),
        "_v225_open_pydrttools_with_selected": lambda *a, **k: calls.append("pydrt"),
        "_v130_open_tafel_window": lambda *a, **k: calls.append("tafel"),
        "_v262_open_ca_cp_analysis": lambda *a, **k: calls.append("ca_cp"),
        "_v262_open_scilink_technique_prompt": lambda *a, **k: calls.append("scilink"),
    }
    ui_tabs_v226._build_analyze_panel(browser, panel, core, ttk, tk)
    browser.tree.event_generate("<<TreeviewSelect>>")
    root.update_idletasks(); root.update()
    browser._v226_refresh_analysis_context()
    root.update_idletasks(); root.update()

    summary = browser._v226_analysis_context_var.get()
    assert "CA" in summary, summary
    assert "1 file" in summary, summary
    for name in (
        "_v226_deareis_btn", "_v226_pydrt_btn", "_v226_pysimple_btn", "_v226_tafel_btn",
        "_v262_ca_cp_btn", "_v262_ca_cp_scilink_btn", "_v262_scilink_template_btn",
    ):
        button = getattr(browser, name)
        assert str(button.cget("state")) == "normal", f"{name} is not clickable"
        assert button.winfo_viewable(), f"{name} is not visible"

    browser._v262_ca_cp_btn.invoke()
    browser._v262_ca_cp_scilink_btn.invoke()
    browser._v262_scilink_template_btn.invoke()
    assert calls == ["ca_cp", "scilink", "scilink"], calls
    root.destroy()

print("v290 Browser Analyze selection and clickable handoff controls smoke: PASS")
