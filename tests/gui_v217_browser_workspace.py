import os, time, tkinter as tk
from pathlib import Path
os.environ.setdefault("MPLBACKEND", "TkAgg")
import faradaiq_studio_core as core

cache=Path('/tmp/fq217_gui_cache')
(cache/'CV_LSV'/'scanrate_50'/'point_001'/'cycle_01').mkdir(parents=True,exist_ok=True)
(cache/'CV_LSV'/'scanrate_50'/'point_001'/'cycle_01'/'cv.csv').write_text('potential_V,current_A,time_s\n-0.5,-1e-6,0\n0,0,1\n0.5,1e-6,2\n')
root=tk.Tk(); root.withdraw()
b=core.FaradaIQH5Browser(root,{'cache_root':str(cache),'manifest':{'base_name':'gui217','technique_counts':{'CV / LSV':1}}},'gui217')
for _ in range(20): root.update_idletasks(); root.update(); time.sleep(.04)
assert b.title().startswith('FaradaIQ Studio v273')
nb=b._v217_center_notebook
assert [nb.tab(i,'text') for i in range(nb.index('end'))]==['Quick Plot','Spatial / Cube']
assert b.plot_holder.winfo_ismapped()
assert getattr(b,'_v206_pub_panel',None) is not None
assert b._v217_lock_xy_aspect_var.get() is False
# Verify popout tabs and synchronized map are created.
from faradaiq.ui_tabs_v226 import open_browser_popout
w=open_browser_popout(b,core.__dict__,core.ttk,core.tk)
for _ in range(8): root.update_idletasks(); root.update(); time.sleep(.03)
notebooks=[x for x in w.winfo_children() if x.winfo_class()=='TNotebook']
assert notebooks
labels=[notebooks[0].tab(i,'text') for i in range(notebooks[0].index('end'))]
assert labels==['Data','Visualize','Analyze','Activity']
visual=b._v226_popout_visual_notebook
assert [visual.tab(i,'text') for i in range(visual.index('end'))]==['Quick Plot','Spatial / Cube','Saved Views']
side=b._v226_side_notebook
assert [side.tab(i,'text') for i in range(side.index('end'))]==['Views','Analyze','Activity','Intelligence']
w.destroy(); b.destroy(); root.destroy()
print('GUI_V217_WORKSPACE_PASS')
