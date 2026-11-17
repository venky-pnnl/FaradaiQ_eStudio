import json, os, sys, tempfile, tkinter as tk
from pathlib import Path
os.environ.setdefault('MPLBACKEND','TkAgg')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from faradaiq_studio_core import FaradaIQH5Browser
qa_root=Path(tempfile.gettempdir())/'faradaiq_gui_v216'; qa_root.mkdir(parents=True,exist_ok=True)
h5=qa_root/'generic_fixture.h5'
cache=qa_root/'browser_cache'
root=tk.Tk(); root.withdraw()
b=FaradaIQH5Browser(root, {'h5_path':str(h5),'cache_root':str(cache),'manifest':{'base_name':'generic_multidim_controls_v216'}}, 'generic_multidim_controls_v216')
root.update_idletasks(); root.update()
bar=getattr(b,'_v216_multidim_bar',None)
assert bar is not None

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
content=texts(bar)
for required in ['Color scale behavior','Spatial X/Y unit','Signal/color unit','Slider axis unit','Map style','Interpolation','Contour levels','Overlay measured points','Use background image','Background resize/settings…','Background is off by default']:
    assert any(required in x for x in content), (required, content)
assert b._v216_spatial_unit_var.get()=='µm'
assert b._v216_signal_unit_var.get()=='Auto'
assert b._v216_spectral_unit_var.get()=='Auto'
assert b._v216_render_mode_var.get()=='Contour + points'
assert b._v216_background_enabled_var.get() is False
assert b._v250_color_scale_mode_var.get() in ('Auto per frame','Global fixed (all frames)','User-defined min/max')
opts=b._v216_multidim_options()
assert opts['spatial_unit']=='µm' and opts['background_enabled'] is False
print(json.dumps({'title':b.title(),'spatial_unit':opts['spatial_unit'],'signal_unit':opts['signal_unit'],'spectral_unit':opts['spectral_unit'],'render_mode':opts['render_mode'],'background_enabled':opts['background_enabled'],'controls_found':len(content)}))
b.destroy(); root.destroy()
