import time
import tkinter as tk
from tkinter import ttk

import faradaiq_studio_core as core

# Keep the smoke run non-interactive.
core.messagebox.askyesno = lambda *a, **k: False
core.messagebox.showinfo = lambda *a, **k: None
core.messagebox.showwarning = lambda *a, **k: None
core.messagebox.showerror = lambda *a, **k: None

gui = core.SessionReaderGUI()

# The root must not be mapped while the legacy wrappers are still building.
assert gui.state() == "withdrawn", gui.state()
assert getattr(gui, "_faradaiq_theme_mode", "") == "high contrast"
assert gui.theme_mode_var.get() == "High Contrast"
assert str(gui.cget("background")).lower() == "#000000"

reveal_observations = []
original_deiconify = gui.deiconify

def observed_deiconify(*args, **kwargs):
    style = ttk.Style(gui)
    reveal_observations.append(
        {
            "mode": getattr(gui, "_faradaiq_theme_mode", ""),
            "selector": gui.theme_mode_var.get(),
            "root_bg": str(gui.cget("background")).lower(),
            "frame_bg": str(style.lookup("TFrame", "background")).lower(),
        }
    )
    return original_deiconify(*args, **kwargs)

gui.deiconify = observed_deiconify

end = time.time() + 0.8
while time.time() < end:
    gui.update()
    time.sleep(0.02)

assert len(reveal_observations) == 1, reveal_observations
first = reveal_observations[0]
assert first["mode"] == "high contrast", first
assert first["selector"] == "High Contrast", first
assert first["root_bg"] == "#000000", first
assert first["frame_bg"] in {"#000000", "#050505"}, first
assert gui.state() == "normal", gui.state()
assert getattr(gui, "_v222_startup_revealed", False) is True
assert getattr(gui, "_v217_theme_selector", None) is not None
assert gui._v217_theme_selector.get() == "High Contrast"

gui.destroy()
print("GUI_V222_DEFAULT_THEME_NO_STARTUP_FLASH_PASS")
