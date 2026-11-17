from __future__ import annotations
import sys
from pathlib import Path as _BootstrapPath
sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[1]))
import os, tempfile, time, tkinter as tk
from pathlib import Path
os.environ['FARADAIQ_EDISON_ENABLED']='0'
import faradaiq_studio_core as core
import faradaiq.v244_workflow_cleanup as release

class Root(tk.Tk):
    def __init__(self):
        super().__init__(); self.withdraw(); self._faradaiq_theme_mode='dark'; self.base_name='v245_smoke'
    def set_status(self,*a,**k): pass

def walk(widget):
    yield widget
    for child in widget.winfo_children(): yield from walk(child)

root=Root()
with tempfile.TemporaryDirectory() as tmp:
    folder=Path(tmp)
    csv=folder/'CV'/'point_01'/'cycle_03.csv'; csv.parent.mkdir(parents=True); csv.write_text('Potential_V,Current_A\n0,0\n')
    js=folder/'v245_smoke_faradaiq_bundle.json'; js.write_text('{"techniques_detected":["CV"]}')
    browser=core.FaradaIQH5Browser(root, cache_info={'cache_root':str(folder),'manifest':{'techniques_detected':['CV / LSV'],'technique_counts':{'CV / LSV':1}}}, base_name='v245_smoke')
    browser.withdraw(); end=time.time()+5.2
    while time.time()<end:
        browser.update_idletasks(); browser.update(); time.sleep(0.05)
    texts=[]; tabs=[]
    for w in walk(browser):
        try:
            t=str(w.cget('text')).strip()
            if t: texts.append(t)
        except Exception: pass
        try:
            if w.winfo_class() in {'TNotebook','Notebook'}: tabs.extend(str(w.tab(i,'text')) for i in w.tabs())
        except Exception: pass
    assert browser.title().startswith('FaradaIQ Studio v273')
    assert 'Visual Data Schema' in tabs
    assert 'Ask SciLink' in texts
    assert 'Ask Edison Scientific' not in texts
    assert 'Ask AI' not in texts and 'Ask Local' not in texts
    assert 'Use LLM for Smart Suggestions' in texts
    browser.selected_path = csv
    review=release.show_analysis_review(browser,'scilink',prompt_override='Analyze all CV points and estimate diffusion coefficient.')
    review.withdraw(); review.update_idletasks(); review.update()
    rtabs=[]; checkbox=[]; checkbox_labels=[]; bodies=[]; buttons=[]
    for w in walk(review):
        try:
            if w.winfo_class() in {'TNotebook','Notebook'}: rtabs.extend(str(w.tab(i,'text')) for i in w.tabs())
        except Exception: pass
        try:
            if w.winfo_class() in {'TCheckbutton','Checkbutton'}:
                checkbox.append(bool(review.getvar(w.cget('variable')))); checkbox_labels.append(str(w.cget('text') or ''))
        except Exception: pass
        try:
            if w.winfo_class()=='Text': bodies.append(w.get('1.0','end-1c'))
        except Exception: pass
        try:
            if w.winfo_class() in {'TButton','Button'}: buttons.append(str(w.cget('text')))
        except Exception: pass
    assert {'Prompt','Files to Share','Request JSON','Service Settings'}.issubset(set(rtabs))
    assert checkbox and any(checkbox)
    assert any('Prepared experimental data bundle' in text for text in texts + bodies) or any('Prepared experimental data bundle' in str(w.cget('text') or '') for w in walk(review) if w.winfo_class() in {'TLabel','Label'})
    assert any('Compact SciLink data-structure JSON' in text for text in texts + bodies) or any('Compact SciLink data-structure JSON' in str(w.cget('text') or '') for w in walk(review) if w.winfo_class() in {'TLabel','Label'})
    assert sum(1 for value in checkbox if value) >= 5  # bundle, context, wait, literature, execution authorization
    assert any('generated-code execution is unlocked' in label and value for label, value in zip(checkbox_labels, checkbox))
    assert any('Pause and wait for my input' in label and value for label, value in zip(checkbox_labels, checkbox))
    assert {'Select visible','Clear visible','Select all','Clear all'}.issubset(set(buttons)), buttons
    assert any(b.strip() == 'Analyze all CV points and estimate diffusion coefficient.' for b in bodies), bodies
    assert any('selected_data_paths' in b and 'requested_filenames' in b for b in bodies)
    assert not any('ASK SCILINK' in b or 'Required behavior' in b or 'Analysis requirements' in b for b in bodies)
    assert not any('FARADAIQ DATA STRUCTURE (TEXT ONLY)' in b for b in bodies)
    review.destroy(); browser.destroy()
root.destroy()
print('v245 Browser/review GUI smoke: PASS')
