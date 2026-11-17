import json, tempfile, time, tkinter as tk
from pathlib import Path
import faradaiq_studio_core as core

work=Path(tempfile.mkdtemp(prefix='faradaiq_v216_cache_smoke_'))
(work/'cv_point001.csv').write_text('potential_V,current_A,time_s\n-0.1,-1e-6,0\n0.0,0,1\n0.1,1e-6,2\n',encoding='utf-8')
root=tk.Tk(); root.withdraw()
class G(tk.Frame):
    def __init__(self,master):
        super().__init__(master)
        self.auto_build_browser_cache_after_parser_var=tk.BooleanVar(master=master,value=True)
        self.browser_cache_status_var=tk.StringVar(master=master,value='waiting')
        self.status_var=tk.StringVar(master=master,value='')
        self._v216_post_cache_requested=True
        self._v216_post_cache_triggered=False
        self._v216_browser_cache_ready=False
    def set_status(self,text): self.status_var.set(str(text))
g=G(root)
orig_base=core._v27_current_base_name
orig_find=core._v55_find_existing_analysis_root
core._v27_current_base_name=lambda _g:'cache_smoke'
core._v55_find_existing_analysis_root=lambda _g,base_name=None:work
try:
    core._v216_trigger_post_export_browser_cache(g,'qa_after_export_and_parser_runs')
    deadline=time.time()+45
    while time.time()<deadline and not getattr(g,'_v216_browser_cache_ready',False):
        root.update(); time.sleep(0.02)
    assert g._v216_browser_cache_ready, g.browser_cache_status_var.get()
    info=g._v216_browser_cache_info
    assert Path(info['h5_path']).exists()
    assert Path(info['cache_root']).exists()
    assert 'ready to open' in g.browser_cache_status_var.get().lower()
    print(json.dumps({'ready':True,'status':g.browser_cache_status_var.get(),'h5':Path(info['h5_path']).name,'tables':info.get('n_tables'),'cache_root':str(info['cache_root'])}))
finally:
    core._v27_current_base_name=orig_base
    core._v55_find_existing_analysis_root=orig_find
    root.destroy()
