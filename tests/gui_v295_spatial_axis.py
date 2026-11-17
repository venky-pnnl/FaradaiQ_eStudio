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
        tech = self.tree.insert("", "end", text="OCP", values=("technique", "10"))
        self.point = self.tree.insert(tech, "end", text="Point 001", values=("point", ""))
        self.tree.selection_set(self.point)
        self.selected_path = data
        self._record_by_iid = {self.point: {"path": str(data), "technique_key": "OCP", "point": 1}}
        self._path_by_iid = {self.point: data}
        self.ai_compare_entry = ttk.Entry(root)

    def selected_paths(self, plottable_only=False): return [self.selected_path]
    def _open_pysimple(self): pass
    def suggest_comparisons(self): pass
    def ask_ai_comparisons(self): pass
    def apply_selected_suggestion(self): pass


with tempfile.TemporaryDirectory() as tmp:
    data = Path(tmp) / "OCP_point_001.csv"
    data.write_text("time_s,potential_V\n0,-0.6\n10,-0.5\n", encoding="utf-8")
    root = tk.Tk(); root.geometry("900x900+20+20"); root.deiconify()
    browser = Browser(root, data)
    panel = ttk.Frame(root); panel.pack(fill="both", expand=True)
    core = {
        "_v138_open_deareis_with_selected": lambda *a, **k: None,
        "_v225_open_pydrttools_with_selected": lambda *a, **k: None,
        "_v130_open_tafel_window": lambda *a, **k: None,
        "_v262_open_ca_cp_analysis": lambda *a, **k: None,
        "_v262_open_scilink_technique_prompt": lambda *a, **k: None,
        "_v291_open_ocp_tail_analysis": lambda *a, **k: None,
        "_v291_open_ocp_scilink_handoff": lambda *a, **k: None,
    }
    ui_tabs_v226._build_analyze_panel(browser, panel, core, ttk, tk)
    root.update_idletasks(); root.update()
    assert browser._v294_ocp_axis_var.get() == "Auto"
    browser._v294_ocp_axis_var.set("X")
    root.update_idletasks(); root.update()
    assert browser._v294_ocp_axis_var.get() == "X"
    browser._v294_ocp_axis_var.set("Distance")
    assert browser._v294_ocp_axis_var.get() == "Distance"
    root.destroy()

print("v296 OCP Point/X/Y/Distance axis control smoke: PASS")
