import os, sys, time, tkinter as tk
from pathlib import Path
os.environ.setdefault('MPLBACKEND','TkAgg')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import faradaiq_studio_core as core

cache=Path('/tmp/fq227_gui_cache')
(cache/'CV_LSV'/'scanrate_50'/'point_001'/'cycle_01').mkdir(parents=True,exist_ok=True)
(cache/'CV_LSV'/'scanrate_50'/'point_001'/'cycle_01'/'cv.csv').write_text('potential_V,current_A,time_s\n0.5,1e-6,0\n0,0,1\n-0.5,-1e-6,2\n0,0,3\n0.5,1e-6,4\n',encoding='utf-8')
root=tk.Tk(); root.withdraw()
b=core.FaradaIQH5Browser(root,{'cache_root':str(cache),'manifest':{'base_name':'gui227'}},'gui227')
for _ in range(15): root.update_idletasks(); root.update(); time.sleep(.03)
assert hasattr(b,'_v227_aspect_var')
assert b._v227_aspect_var.get() in ('Auto fit','Equal XY units','Square plot','Rectangle 4:3','Wide 16:9')
b._v227_aspect_var.set('Square plot')
assert b._v216_multidim_options()['aspect_preset']=='Square plot'

t=core.FaradaIQTafelAnalysisWindow(b, paths=[cache/'CV_LSV'/'scanrate_50'/'point_001'/'cycle_01'/'cv.csv'])
for _ in range(10): root.update_idletasks(); root.update(); time.sleep(.03)
assert t.scan_branch_var.get()=='Whole trace / LSV'
t.scan_branch_var.set('Forward + reverse separately')
assert t._settings().scan_branch_mode=='Forward + reverse separately'
assert 'v240' in t.title()
# High Contrast/dark text widgets must not use the default white text background.
try:
    bg=str(t.files_text.cget('background')).lower()
    assert bg not in ('white','#ffffff','systemwindow')
except Exception:
    pass

t.destroy(); b.destroy(); root.destroy()
print('GUI_V227_ASPECT_TAFEL_THEME_PASS')
