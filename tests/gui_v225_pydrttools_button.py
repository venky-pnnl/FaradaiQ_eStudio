import os
import time
import tkinter as tk
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "TkAgg")
import faradaiq_studio_core as core

cache = Path("/tmp/fq224_pydrttools_gui_cache")
eis_dir = cache / "EIS" / "method_01" / "point_001"
eis_dir.mkdir(parents=True, exist_ok=True)
path = eis_dir / "point_001_EIS_Nyquist.csv"
path.write_text(
    "frequency_Hz,Zreal_ohm,Zimag_ohm\n1000,10,-1\n100,11,-2\n10,12,-3\n",
    encoding="utf-8",
)

root = tk.Tk()
root.withdraw()
browser = core.FaradaIQH5Browser(
    root,
    {
        "cache_root": str(cache),
        "manifest": {"base_name": "pydrt_button", "technique_counts": {"EIS": 1}},
    },
    "pydrt_button",
)
for _ in range(30):
    root.update_idletasks()
    root.update()
    time.sleep(0.03)

button_texts = []
def walk(widget):
    for child in widget.winfo_children():
        try:
            if str(child.winfo_class()) in {"TButton", "Button"}:
                button_texts.append(str(child.cget("text")))
        except Exception:
            pass
        walk(child)

walk(browser)
assert "Open selected EIS in pyDRTtools" in button_texts, button_texts
assert "Open native DearEIS GUI" not in button_texts, button_texts

browser.destroy()
root.destroy()
print("GUI_V225_PYDRTTOOLS_BUTTON_PASS")
