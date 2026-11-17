from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faradaiq import ui_tabs_v226


class Browser:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.tree = ttk.Treeview(root); self.tree.pack()
        node = self.tree.insert('', 'end', text='CV / IV', values=('technique', ''))
        self.tree.selection_set(node)
        self.ai_compare_entry = ttk.Entry(root)
    def _open_pysimple(self): pass
    def suggest_comparisons(self): pass
    def ask_ai_comparisons(self): pass
    def apply_selected_suggestion(self): pass


root = tk.Tk(); root.geometry('900x900+20+20'); root.deiconify()
browser = Browser(root)
panel = ttk.Frame(root); panel.pack(fill='both', expand=True)
calls = []
core = {
    '_v138_open_deareis_with_selected': lambda *a, **k: None,
    '_v225_open_pydrttools_with_selected': lambda *a, **k: None,
    '_v130_open_tafel_window': lambda *a, **k: calls.append('analysis'),
    '_v291_show_selected_tafel_trace': lambda *a, **k: calls.append('trace'),
    '_v262_open_ca_cp_analysis': lambda *a, **k: None,
    '_v262_open_scilink_technique_prompt': lambda *a, **k: None,
    '_v291_open_ocp_tail_analysis': lambda *a, **k: None,
    '_v291_open_ocp_scilink_handoff': lambda *a, **k: None,
}
ui_tabs_v226._build_analyze_panel(browser, panel, core, ttk, tk)
root.update_idletasks(); root.update()
texts=[]
def walk(widget):
    for child in widget.winfo_children():
        try:
            value = child.cget('text')
            if value: texts.append(str(value))
        except Exception:
            pass
        walk(child)
walk(panel)
assert 'Tafel analysis' in texts
assert 'Tafel trace: log|I| vs V' not in texts
assert not hasattr(browser, '_v291_tafel_trace_btn')
root.destroy()
print('v293 Tafel Analyze cleanup smoke: PASS')
