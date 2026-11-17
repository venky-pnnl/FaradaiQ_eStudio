import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "TkAgg")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import faradaiq_studio_core as core
import faradaiq_tafel_adapter as tafel

core.messagebox.showinfo = lambda *a, **k: None
core.messagebox.showwarning = lambda *a, **k: None
core.messagebox.showerror = lambda *a, **k: None
core.messagebox.askyesno = lambda *a, **k: True


def widget_texts(root):
    out = []
    def walk(widget):
        for child in widget.winfo_children():
            try:
                text = str(child.cget("text"))
                if text:
                    out.append(text)
            except Exception:
                pass
            walk(child)
    walk(root)
    return out

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    path = tafel.generate_dummy_dataset(root, noise_std_dec=0.01)
    gui = core.SessionReaderGUI()
    deadline = time.time() + 1.2
    while time.time() < deadline:
        gui.update(); time.sleep(0.01)
    win = core.FaradaIQTafelAnalysisWindow(gui, paths=[path])
    deadline = time.time() + 0.7
    while time.time() < deadline:
        gui.update(); time.sleep(0.01)
    texts = widget_texts(win)
    for expected in ("Home", "Back", "Forward", "Pan", "Box zoom", "Fit view", "Save figure", "Run Tafel analysis", "Cancel analysis"):
        assert expected in texts, (expected, texts)
    # Native toolbar exists only as a callback provider and is not packed.
    assert win._nav is not None
    assert not win._nav.winfo_ismapped()
    win.fit_mode_var.set("Fast")
    win.bootstrap_var.set(False)
    win._run_selected()
    assert win._process is not None
    # Repeated Tk updates while the worker is active prove that the event loop is not blocked.
    responsive_ticks = 0
    deadline = time.time() + 25
    while win._process is not None and time.time() < deadline:
        gui.update()
        responsive_ticks += 1
        time.sleep(0.02)
    gui.update(); gui.update_idletasks()
    assert responsive_ticks >= 10
    assert win._process is None
    assert float(win.progress_var.get()) == 100.0
    assert len(win.results.get_children()) > 0
    assert "Completed" in win.status_var.get()
    win.destroy(); gui.destroy()
print("GUI_V234_TAFEL_RESPONSIVE_PASS")
