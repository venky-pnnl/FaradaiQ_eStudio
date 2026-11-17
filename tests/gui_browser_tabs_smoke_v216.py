import json, os, sys, tempfile, tkinter as tk
from pathlib import Path
os.environ.setdefault('MPLBACKEND','TkAgg')
from faradaiq_studio_core import FaradaIQH5Browser
qa_root=Path(tempfile.gettempdir())/'faradaiq_gui_v216'; qa_root.mkdir(parents=True,exist_ok=True)
h5=qa_root/'generic_fixture.h5'
cache=qa_root/'browser_cache'
root=tk.Tk(); root.withdraw()
b=FaradaIQH5Browser(root, {'h5_path':str(h5),'cache_root':str(cache),'manifest':{'base_name':'generic_tabs_v216'}}, 'generic_tabs_v216')
root.update_idletasks(); root.update()
nb=getattr(b,'_v216_center_notebook',None)
assert nb is not None
labels=[nb.tab(i,'text') for i in range(nb.index('end'))]
assert labels==['Quick Plot','Spatial / Cube'],labels
classic=getattr(b,'_v216_classic_tab'); multi=getattr(b,'_v216_multidim_tab')

def texts(w):
    out=[]; stack=[w]
    while stack:
        x=stack.pop()
        try:
            t=x.cget('text')
            if t: out.append(str(t))
        except Exception: pass
        try: stack.extend(x.winfo_children())
        except Exception: pass
    return out
ct=[]
for w in classic.pack_slaves(): ct += texts(w)
mt=[]
for w in multi.pack_slaves(): mt += texts(w)
assert any('Plot/overlay selected' in x for x in ct),ct[:40]
assert any('Spatial, 3D, and hyperspectral visualization' in x for x in mt),mt[:80]
assert any('Choose a view, then refine only the settings that matter.' in x for x in mt),mt[:80]
assert any('Off = free rectangular zoom' in x for x in mt),mt[:80]
assert any('physical probe footprint' in x for x in mt),mt[:80]

classic_slaves=classic.pack_slaves(); multi_slaves=multi.pack_slaves()
assert classic_slaves and multi_slaves
nb.select(classic); root.update_idletasks(); root.update()
classic_mapped=any(w.winfo_ismapped() for w in classic_slaves)
multi_mapped_in_classic=any(w.winfo_ismapped() for w in multi_slaves)
nb.select(multi); root.update_idletasks(); root.update()
classic_mapped_in_multi=any(w.winfo_ismapped() for w in classic_slaves)
multi_mapped=any(w.winfo_ismapped() for w in multi_slaves)
assert classic_mapped and not multi_mapped_in_classic
assert multi_mapped and not classic_mapped_in_multi
print(json.dumps({'title':b.title(),'tabs':labels,'classic_controls':len(ct),'multidim_controls':len(mt),'classic_has_plot':any('Plot/overlay selected' in x for x in ct),'multi_has_task_layout':any('Spatial, 3D, and hyperspectral visualization' in x for x in mt),'tab_visibility_passed':classic_mapped and multi_mapped and not classic_mapped_in_multi and not multi_mapped_in_classic}))
b.destroy(); root.destroy()
