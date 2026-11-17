from __future__ import annotations
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['FARADAIQ_AI_LAUNCH_MODE']='scilink_edison_llm'
os.environ['FARADAIQ_EDISON_ENABLED']='1'
os.environ['FARADAIQ_LLM_TOOLS_ENABLED']='1'
os.environ['FARADAIQ_SKIP_STARTUP_AI_SELECTOR']='1'
import faradaiq_studio_core as core

def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)

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
for label in ('LLM settings','Generate LLM-guided prompt','Ask SciLink','Ask Edison Scientific','AI Chat'):
    assert label in texts, (label, texts)
assert hasattr(gui, '_v250_resource_var')
assert 'CPU' in gui._v250_resource_var.get() and 'RAM' in gui._v250_resource_var.get()
gui.destroy()
print('v250 all-services GUI smoke: PASS')
