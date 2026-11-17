import time
import tkinter as tk
from tkinter import ttk

from faradaiq.runtime_v217 import _install_persistent_status

root = tk.Tk()
root.geometry("760x260")
root.status_var = tk.StringVar(value="Ready")
root._faradaiq_pyseccm_status_var = tk.StringVar(value="")
root.browser_cache_status_var = tk.StringVar(value="")
_install_persistent_status(root, ttk, tk)

writes = []
root._v218_status_display_var.trace_add("write", lambda *_: writes.append(root._v218_status_display_var.get()))

# Simulate the common burst where the parser monitor and general status are
# written almost together.  The parser source must remain authoritative and the
# coalescer must avoid a rapid parser/general/parser flash.
root._faradaiq_pyseccm_status_var.set("pySECCM/DRT parser: command RUNNING 2/6 | files parsed 12/50")
root.status_var.set("Background task running")
for _ in range(8):
    root.update_idletasks(); root.update(); time.sleep(0.04)
assert "pySECCM/DRT parser" in root._v218_status_display_var.get()
assert len(writes) <= 3, writes
assert root._v218_status_prefix_label.cget("text") == "Status tracking:"
assert root._v218_status_progress.winfo_manager() == "grid"
assert str(root._v218_status_progress.cget("mode")) == "determinate"
assert 30.0 <= float(root._v218_status_progress.cget("value")) <= 35.0

# General status churn must not displace the still-active parser source.
for i in range(12):
    root.status_var.set(f"generic progress running {i}")
root.update_idletasks(); root.update(); time.sleep(0.18)
root.update_idletasks(); root.update()
assert "pySECCM/DRT parser" in root._v218_status_display_var.get()

# Resizing must keep the footer mapped without repeated placement/raising.
root.geometry("430x150")
for _ in range(5):
    root.update_idletasks(); root.update(); time.sleep(0.03)
assert root._v218_persistent_status_frame.winfo_ismapped()

# Terminal parser state allows the cache source to take over cleanly.
root._faradaiq_pyseccm_status_var.set("pySECCM/DRT parser: finished 6/6 command(s)")
root.browser_cache_status_var.set("Browser cache building 10/20")
for _ in range(6):
    root.update_idletasks(); root.update(); time.sleep(0.04)
assert "Browser cache building" in root._v218_status_display_var.get()
assert str(root._v218_status_progress.cget("mode")) == "determinate"
assert 49.0 <= float(root._v218_status_progress.cget("value")) <= 51.0

root.destroy()
print("GUI_V218_STATUS_NO_FLICKER_PASS")
