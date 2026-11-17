from __future__ import annotations
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['FARADAIQ_AI_LAUNCH_MODE']='scilink_edison'
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
tops=[w for w in walk(gui) if w.winfo_class()=='Toplevel' and 'AI services for this launch' in w.title()]
assert not tops, [w.title() for w in tops]
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
print('v250 terminal-selected GUI visibility smoke: PASS')
