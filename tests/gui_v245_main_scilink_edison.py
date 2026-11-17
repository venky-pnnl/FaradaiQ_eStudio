from __future__ import annotations
import sys
from pathlib import Path as _BootstrapPath
sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[1]))
import os, time
os.environ['FARADAIQ_AI_LAUNCH_MODE']='scilink_edison'
os.environ['FARADAIQ_EDISON_ENABLED']='1'
os.environ['FARADAIQ_LLM_TOOLS_ENABLED']='1'
import faradaiq_studio_core as core

def walk(widget):
    yield widget
    for child in widget.winfo_children(): yield from walk(child)

gui=core.SessionReaderGUI(); gui.withdraw()
end=time.time()+5.8
while time.time()<end:
    gui.update_idletasks(); gui.update(); time.sleep(0.05)
texts=[]
for w in walk(gui):
    try:
        t=str(w.cget('text')).strip()
        if t: texts.append(t)
    except Exception: pass
assert 'Ask SciLink' in texts
assert 'Ask Edison Scientific' in texts
assert 'Generate LLM-guided prompt' in texts
assert 'AI Chat' in texts
gui.destroy()
print('v245 SciLink+Edison GUI smoke: PASS')
