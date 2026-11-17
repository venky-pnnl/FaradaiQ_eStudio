import os, sys, time, tkinter as tk
from pathlib import Path
os.environ.setdefault('MPLBACKEND','TkAgg')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import faradaiq_studio_core as core

cache=Path('/tmp/fq228_gui_cache')
(cache/'CV_LSV'/'method_01'/'point_001'/'cycle_01').mkdir(parents=True,exist_ok=True)
p=cache/'CV_LSV'/'method_01'/'point_001'/'cycle_01'/'cv.csv'
p.write_text('potential_V,current_A\n0.5,1e-6\n0.0,0\n-0.5,-1e-6\n0,0\n0.5,1e-6\n',encoding='utf-8')
root=tk.Tk(); root.withdraw()
b=core.FaradaIQH5Browser(root,{'cache_root':str(cache),'manifest':{'base_name':'gui228'}},'gui228')
end=time.time()+2.5
while time.time()<end:
    root.update_idletasks(); root.update(); time.sleep(.03)
assert hasattr(b,'_v228_intelligence_tab')
assert hasattr(b,'_v228_index_status_var')
assert hasattr(b,'_v228_selection')
texts=[b._v226_side_notebook.tab(i,'text') for i in range(b._v226_side_notebook.index('end'))]
assert 'Intelligence' in texts,texts
assert 'Ready' in b._v228_index_status_var.get() or 'Indexing' in b._v228_index_status_var.get() or 'Preparing' in b._v228_index_status_var.get(),b._v228_index_status_var.get()
# command palette should open and be closable without disturbing browser state
core._v228_build_ai_context(b)
assert (b.analyzed_root/'Scientific_Intelligence_v237'/'AI_context'/'current_ai_context.json').exists()
# Ctrl+K binding / direct command palette
core._v228_generate_report
from faradaiq.v228_intelligence import open_command_palette
open_command_palette(b, core.__dict__)
root.update_idletasks(); root.update()
palettes=[w for w in b.winfo_children() if isinstance(w,tk.Toplevel) and 'command palette' in w.title().lower()]
assert palettes
for w in palettes: w.destroy()
b.destroy(); root.destroy()
print('GUI_V228_INTELLIGENCE_WORKSPACE_PASS')
