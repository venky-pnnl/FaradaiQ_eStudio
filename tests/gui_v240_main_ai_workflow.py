from __future__ import annotations
import time
import faradaiq_studio_core as core


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


gui = core.SessionReaderGUI()
gui.withdraw()
end = time.time() + 1.3
while time.time() < end:
    gui.update_idletasks(); gui.update(); time.sleep(0.05)
texts=[]
llm_button=None
for widget in walk(gui):
    try:
        t=str(widget.cget('text')).strip()
        if t: texts.append(t)
        if t=='LLM settings': llm_button=widget
    except Exception: pass
assert gui.title().startswith('FaradaIQ Studio v293')
for label in ('LLM settings','Generate LLM-guided prompt','Ask SciLink','Ask Edison Scientific','AI Chat'):
    assert label in texts, label
for forbidden in ('Build LLM-guided AI bundle','Build default AI bundle','Send AI bundle to Edison'):
    assert forbidden not in texts, forbidden
assert llm_button is not None
llm_button.invoke()
gui.update_idletasks(); gui.update()
settings=[w for w in gui.winfo_children() if w.winfo_class()=='Toplevel']
assert settings
win=settings[-1]; win.withdraw(); win.update_idletasks(); win.update()
tabs=[]; st=[]
for widget in walk(win):
    try:
        t=str(widget.cget('text')).strip()
        if t: st.append(t)
    except Exception: pass
    try:
        if widget.winfo_class() in {'TNotebook','Notebook'}:
            tabs.extend(str(widget.tab(tab,'text')) for tab in widget.tabs())
    except Exception: pass
assert {'Providers and Services','Data Sharing'}.issubset(set(tabs))
assert 'Data Sharing — SciLink / Edison only' in st
assert 'Apply / save sharing settings' in st
win.destroy(); gui.destroy()
print('v290 main AI workflow GUI smoke: PASS')
