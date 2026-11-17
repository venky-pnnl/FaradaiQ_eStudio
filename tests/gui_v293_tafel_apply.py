from __future__ import annotations
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from faradaiq import ui_tabs_v217

class FakePanel:
    def __init__(self, browser):
        self.browser = browser
        self.last_curves = [{"path":"dummy.csv"}]
        self.render_calls = 0
    def render(self, curves, clear=True):
        self.render_calls += 1
        self.last_curves = list(curves)

class Browser:
    def __init__(self, root):
        self.root=root
        self.plot_mode_var=tk.StringVar(root, value="CV I-V")
        self.max_curves_var=tk.StringVar(root, value="24")
        self.plot_xscale_var=tk.StringVar(root, value="Linear")
        self.plot_yscale_var=tk.StringVar(root, value="Log")
        self.messages=[]
    def plot_selected(self, clear=True): pass
    def clear_plot(self): pass
    def open_axis_title_dialog(self): pass
    def export_current_comparison(self): pass
    def log(self,msg): self.messages.append(str(msg))

root=tk.Tk(); root.geometry("1000x500+10+10")
browser=Browser(root)
frame=ttk.Frame(root); frame.pack(fill="both", expand=True)
core={"FaradaIQPublicationPlotPanelV206":FakePanel}
ui_tabs_v217._build_classic(browser, frame, core, ttk, tk)
root.update_idletasks(); root.update()
assert browser._v206_pub_panel.render_calls == 0
browser.plot_mode_var.set("Tafel trace log|I| vs V")
browser.plot_mode.event_generate("<<ComboboxSelected>>")
root.update_idletasks(); root.update()
assert browser.plot_yscale_var.get() == "Linear"
assert browser._v206_pub_panel.render_calls == 1
root.destroy()
print("v293 Tafel mode immediate-apply GUI smoke: PASS")
