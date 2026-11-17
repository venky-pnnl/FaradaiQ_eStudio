import time
import tkinter as tk

import faradaiq_studio_core as core

core.messagebox.showinfo = lambda *a, **k: None
core.messagebox.showwarning = lambda *a, **k: None
core.messagebox.showerror = lambda *a, **k: None

root = core.SessionReaderGUI()
end = time.time() + 0.45
while time.time() < end:
    root.update_idletasks(); root.update(); time.sleep(0.02)

win, log = core._v63_analysis_progress_window(root, "FaradaIQ v273 theme smoke")
assert win is not None
log("Theme smoke test")
for _ in range(8):
    root.update_idletasks(); root.update(); time.sleep(0.03)

texts = []
def walk(w):
    if str(w.winfo_class()) == "Text":
        texts.append(w)
    for child in w.winfo_children():
        walk(child)
walk(win)
assert texts, "No progress Text/ScrolledText widget found"
for text in texts:
    assert str(text.cget("background")).lower() == "#000000", text.cget("background")
    assert str(text.cget("foreground")).lower() == "#ffffff", text.cget("foreground")
assert str(win.cget("background")).lower() == "#000000"

win.destroy(); root.destroy()
print("GUI_V222_PROGRESS_TEXT_THEME_PASS")
