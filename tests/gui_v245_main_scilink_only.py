from __future__ import annotations
import sys
from pathlib import Path as _BootstrapPath
sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[1]))
import os, time
os.environ['FARADAIQ_AI_LAUNCH_MODE']='scilink'
os.environ['FARADAIQ_EDISON_ENABLED']='0'
os.environ['FARADAIQ_LLM_TOOLS_ENABLED']='1'
import faradaiq_studio_core as core

def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)

gui=core.SessionReaderGUI(); gui.withdraw()
end=time.time()+5.8
while time.time()<end:
    gui.update_idletasks(); gui.update(); time.sleep(0.05)
texts=[]; buttons={}
for w in walk(gui):
    try:
        t=str(w.cget('text')).strip()
        if t:
            texts.append(t)
            buttons.setdefault(t,w)
    except Exception: pass
assert gui.title().startswith('FaradaIQ Studio v273'), gui.title()
for label in ('LLM settings','Ask SciLink'):
    assert label in texts, label
assert 'Generate LLM-guided prompt' in texts
assert 'AI Chat' in texts
assert 'Ask Edison Scientific' not in texts
assert 'Analysis tool' in texts
assert 'Auto parser/DRT after export' in texts
assert hasattr(gui,'_v245_resource_var')
assert 'CPU' in gui._v245_resource_var.get() and 'RAM' in gui._v245_resource_var.get()
gui.destroy()
print('v245 main SciLink-only GUI smoke: PASS')
