import time, tkinter as tk
from tkinter import ttk
from faradaiq.runtime_v217 import _install_persistent_status, _palette
root=tk.Tk(); root.geometry('700x250')
root.status_var=tk.StringVar(value='Ready')
root.set_status=lambda msg: root.status_var.set(msg)
_install_persistent_status(root,ttk,tk)
root.status_var.set('Browser catalog building 1/10')
for _ in range(15): root.update_idletasks(); root.update(); time.sleep(.08)
text=root._v217_status_display_var.get()
assert 'elapsed' in text and 'Browser catalog building' in text
root.geometry('420x160')
for _ in range(5): root.update_idletasks(); root.update(); time.sleep(.05)
assert root._v217_persistent_status_frame.winfo_ismapped()
for name in ('Dark','Light','Slate','Warm','Midnight','High Contrast'):
 assert _palette(name)['display']==name
root.destroy(); print('GUI_V217_STATUS_THEME_PASS')
