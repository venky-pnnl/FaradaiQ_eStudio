from __future__ import annotations
import sys, tempfile, tkinter as tk
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import faradaiq_studio_core as core

class Root(tk.Tk):
    def __init__(self, folder):
        super().__init__(); self.withdraw(); self.output_var=tk.StringVar(value=str(folder)); self.file_var=tk.StringVar(value=str(folder)); self.base_name='demo'
    def set_status(self,*a,**k): pass

def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)

with tempfile.TemporaryDirectory() as tmp:
    root=Root(Path(tmp))
    review=core._v245_show_analysis_review(root,'scilink', prompt_override='EXACT USER PROMPT')
    review.withdraw(); review.update_idletasks(); review.update()
    tabs=[]; texts=[]; combos=[]; checks=[]; buttons=[]
    for w in walk(review):
        try:
            if w.winfo_class() in {'TNotebook','Notebook'}:
                tabs.extend(str(w.tab(i,'text')) for i in w.tabs())
        except Exception: pass
        try:
            if w.winfo_class()=='Text': texts.append(w.get('1.0','end-1c'))
        except Exception: pass
        try:
            if w.winfo_class()=='TCombobox': combos.append(w.get())
        except Exception: pass
        try:
            if w.winfo_class() in {'TCheckbutton','Checkbutton'}: checks.append(bool(review.getvar(w.cget('variable'))))
        except Exception: pass
        try:
            if w.winfo_class() in {'TButton','Button'}: buttons.append(str(w.cget('text')))
        except Exception: pass
    assert {'Prompt','Files to Share','Request JSON','Service Settings'}.issubset(set(tabs))
    assert any(t == 'EXACT USER PROMPT' for t in texts), texts
    assert 'Autonomous' in combos, combos
    assert not any(checks), checks
    assert {'Select visible','Clear visible','Select all','Clear all'}.issubset(set(buttons)), buttons
    assert any('selected_data_paths' in t and 'requested_filenames' in t for t in texts)
    assert not any('ASK SCILINK' in t or 'Required behavior' in t or 'Analysis requirements' in t for t in texts)
    review.destroy(); root.destroy()
print('v245 SciLink exact-prompt review GUI smoke: PASS')
