from pathlib import Path
import ast

ROOT=Path(__file__).resolve().parents[1]


def test_current_public_release_identity():
    assert (ROOT / "faradaiq_studio_v316.py").exists()
    assert (ROOT / "Start_FaradaIQ_Studio_v320.bat").exists()
    import faradaiq
    import faradaiq_studio_core as core
    assert faradaiq.__version__ == "0.316.0"
    assert core.APP_TITLE == "FaradaIQ Studio v316"
    assert core.FARADAIQ_PUBLIC_VERSION == "v316"
    assert 'version = "0.316.0"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_session_lowercase_creator_removed():
    text=(ROOT/'faradaiq_studio_core.py').read_text(encoding='utf-8')
    start=text.index('def _v177_add_session_buttons_to_launch_row')
    end=text.index('def _v177_apply_main_window_cleanup',start)
    block=text[start:end]
    assert "text='Save session'" not in block
    assert "text='Load session'" not in block
    assert '_v177_insert_main_session_buttons' in block


def test_drt_default_log_in_unified_renderer():
    text=(ROOT/'faradaiq'/'browser_ui_v209.py').read_text(encoding='utf-8')
    assert 'mode_l.strip() == "drt"' in text
    assert 'plot_xscale_var.set("Log")' in text


def test_lazy_media_decoder_does_not_preload_all_frames():
    text=(ROOT/'faradaiq'/'browser_ui_v209.py').read_text(encoding='utf-8')
    assert 'class _LazyMediaDecoder' in text
    assert 'press Play to animate' in text
    assert 'while len(self._cache) > 3' in text
    assert '_display_media(browser' in text and '_LazyMediaDecoder(path)' in text



def test_drt_log_default_and_user_override(tmp_path):
    import csv
    from types import SimpleNamespace
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from faradaiq.browser_ui_v209 import _load_curve, _render

    path=tmp_path/'point001_simple_drt_data.csv'
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['tau_s','gamma_ohm']); w.writeheader()
        for tau,gamma in [(1e-5,1.0),(1e-3,2.0),(1e-1,0.5)]: w.writerow({'tau_s':tau,'gamma_ohm':gamma})
    class V:
        def __init__(self,v): self.v=v
        def get(self): return self.v
        def set(self,v): self.v=v
    class P:
        def __init__(self):
            self.browser=SimpleNamespace(_catalog_by_path={str(path.resolve()).lower():{'technique_key':'DRT','method_index':1,'point':1,'plot_mode':'DRT'}},plot_mode_var=V('DRT'),plot_xscale_var=V('Linear'),plot_yscale_var=V('Linear'),plot_grid_var=V(True),plot_xmin_var=V(''),plot_xmax_var=V(''),plot_ymin_var=V(''),plot_ymax_var=V(''),legend_var=V('On'),eis_cmap_var=V('Blues'),eis_plot_style_var=V('Line + Points'),cv_view_var=V('I-V'),log=lambda *_a,**_k:None)
            self.figure=Figure(); self.ax=self.figure.add_subplot(111); self.canvas=SimpleNamespace(draw_idle=lambda:None); self.last_recipe={}; self.last_curves=[]
        def _get(self,n,d=None):
            v=getattr(self.browser,n,None); return v.get() if hasattr(v,'get') else d
        def _new_figure(self): self.figure.clear(); self.ax=self.figure.add_subplot(111)
        def _style_flags(self): return True,True
        def _apply_options(self,_has_cbar=False):
            self.ax.set_xscale('log' if str(self.browser.plot_xscale_var.get()).lower().startswith('log') else 'linear')
            self.ax.set_yscale('log' if str(self.browser.plot_yscale_var.get()).lower().startswith('log') else 'linear')
    panel=P(); curve=_load_curve(panel,path)
    assert _render(panel,[curve]) is True
    assert panel.ax.get_xscale()=='log'
    assert panel.ax.get_xlabel()=='τ / s'
    panel.browser.plot_xscale_var.set('Linear')
    assert _render(panel,[curve]) is True
    assert panel.ax.get_xscale()=='linear'


def test_interactive_media_selection_is_lazy(tmp_path):
    from PIL import Image
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.figure import Figure
    from types import SimpleNamespace
    from faradaiq.browser_ui_v209 import _display_media, _close_media_decoder
    frames=[Image.new('RGB',(600,400),(i*20%255,0,0)) for i in range(12)]
    gif=tmp_path/'large.gif'; frames[0].save(gif,save_all=True,append_images=frames[1:],duration=60,loop=0)
    class Panel:
        def __init__(self): self.figure=Figure(); self.ax=self.figure.add_subplot(111); self.canvas=SimpleNamespace(draw_idle=lambda:None)
        def _new_figure(self): self.figure.clear(); self.ax=self.figure.add_subplot(111)
    b=SimpleNamespace(_v206_pub_panel=Panel(),_ensure_plot_canvas=lambda:None,log=lambda *_a,**_k:None)
    _display_media(b,[gif],0)
    assert b._v211_media_playing is False
    assert b._v216_media_decoder.frame_count==12
    assert len(b._v216_media_decoder._cache)<=1
    _close_media_decoder(b)


def test_multidimensional_dependencies_declared():
    req=(ROOT/'requirements_all.txt').read_text(encoding='utf-8').lower()
    for token in ('altair>=6.0','plotly>=6.0','vl-convert-python','pywebview'):
        assert token in req


def test_legacy_optional_dependency_startup_prompt_disabled():
    text=(ROOT/'faradaiq_studio_core.py').read_text(encoding='utf-8')
    start=text.index('def _session_reader_gui_v22_init')
    end=text.index('SessionReaderGUI.__init__ = _session_reader_gui_v22_init', start)
    block=text[start:end]
    assert 'self.after(900, _prompt_once)' not in block
    assert '_faradaiq_optional_dependency_prompt_disabled' in block
