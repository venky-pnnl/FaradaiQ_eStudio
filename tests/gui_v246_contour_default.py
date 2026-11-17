from __future__ import annotations
import os, sys, tempfile, time, tkinter as tk
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory() as tmp:
    os.environ['FARADAIQ_RUNTIME_SETTINGS_DIR'] = tmp
    os.environ['FARADAIQ_EDISON_ENABLED'] = '0'
    import faradaiq_studio_core as core
    root=tk.Tk(); root.withdraw()
    browser=core.FaradaIQH5Browser(root, cache_info={'cache_root':tmp,'manifest':{'base_name':'v246_contour','techniques_detected':['DRT'],'technique_counts':{'DRT':24}}}, base_name='v246_contour')
    browser.withdraw()
    end=time.time()+2.5
    while time.time()<end:
        browser.update_idletasks(); browser.update(); time.sleep(0.03)
    assert browser.title().startswith('FaradaIQ Studio v273'), browser.title()
    assert browser._v216_render_mode_var.get() == 'Contour + points', browser._v216_render_mode_var.get()
    opts=browser._v216_multidim_options()
    assert opts['render_mode']=='Contour + points'
    assert opts['interpolation']=='Linear'
    browser.destroy(); root.destroy()
print('v246 contour-default GUI smoke: PASS')
