import os, time, shutil, tkinter as tk
from pathlib import Path
os.environ.setdefault('MPLBACKEND','TkAgg')
import faradaiq_studio_core as core

cache=Path('/tmp/fq301_gui_cache'); shutil.rmtree(cache,ignore_errors=True)
(cache/'OCP'/'method_01'/'point_001').mkdir(parents=True,exist_ok=True)
(cache/'OCP'/'method_01'/'point_001'/'ocp.csv').write_text('time_s,potential_V\n0,-0.5\n1,-0.49\n2,-0.48\n')
(cache/'IV_Tafel'/'method_01'/'point_001').mkdir(parents=True,exist_ok=True)
(cache/'IV_Tafel'/'method_01'/'point_001'/'tafel.csv').write_text('potential_V,current_A\n-0.7,-1e-7\n-0.6,-1e-8\n-0.5,1e-8\n-0.4,1e-7\n')
root=tk.Tk(); root.withdraw()
b=core.FaradaIQH5Browser(root,{'cache_root':str(cache),'manifest':{'base_name':'simple','technique_counts':{'OCP':1,'IV/Tafel':1}}},'simple')
for _ in range(100): root.update_idletasks(); root.update(); time.sleep(.025)
assert getattr(b,'_v302_simplified',False)
assert 'v316' in b.title()
assert 'tree' in str(b.tree.cget('show'))
assert bool(b._v302_auto_preview_var.get()) is True
nb=b._v226_center_notebook
assert {'View','Map / 3D'} <= {nb.tab(t,'text') for t in nb.tabs()}
schema=[t for t in nb.tabs() if nb.tab(t,'text')=='Visual Data Schema']
if schema: assert nb.tab(schema[0],'state')=='hidden'
assert not b._v226_side_notebook.winfo_ismapped()
# Handoff strip is retained internally but hidden from the simplified default.
bar=getattr(b,'_v239_handoff_bar',None)
if bar is not None: assert not bar.winfo_ismapped()
# OCP top-level selection gives one OCP context and triggers automatic preview.
for iid in b.tree.get_children(''):
    txt=b.tree.item(iid,'text').lower()
    if 'ocp' in txt or 'open-circuit' in txt:
        b.tree.selection_set(iid); break
for _ in range(35): root.update_idletasks(); root.update(); time.sleep(.025)
assert b._v302_context_var.get().startswith('OCP ·')
assert getattr(b,'_figure',None) is not None
# Advanced functions can be restored without rebuilding the Browser.
b._v302_show_advanced('Analyze')
for _ in range(5): root.update_idletasks(); root.update(); time.sleep(.02)
assert b._v226_side_notebook.winfo_ismapped()
b._v302_toggle_advanced(); root.update()
assert not b._v226_side_notebook.winfo_ismapped()
if schema:
    b._v302_show_schema(); root.update(); assert nb.tab(schema[0],'state')=='normal'
b.destroy(); root.destroy(); print('GUI_V302_SIMPLE_BROWSER_PASS')
