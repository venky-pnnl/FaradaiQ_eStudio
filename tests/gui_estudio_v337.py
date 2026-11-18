"""Live GUI contract; no network calls or scientific service submissions."""
import os,time,sys,tempfile
from tkinter import filedialog, messagebox, ttk
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['FARADAIQ_SKIP_STARTUP_AI_SELECTOR']='1'
os.environ['FARADAIQ_AI_LAUNCH_MODE']='scilink_edison_llm'
temp_settings=tempfile.TemporaryDirectory(prefix='estudio-qa-')
os.environ['LOCALAPPDATA']=temp_settings.name
os.environ['FARADAIQ_LAUNCH_ROOT']=str(Path(temp_settings.name)/'launch')
settings=Path(temp_settings.name)/'FaradaIQ'/'eStudio'/'ui.json'
settings.parent.mkdir(parents=True);settings.write_text('[]')
import faradaiq_studio_core as core
from faradaiq.estudio_ui import walk

def pump(g,seconds=.15):
    g.after(max(1, round(seconds*1000)), g.quit)
    g.mainloop()

def wait_for(gui, condition):
    deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        pump(gui,.05)
        result=condition()
        if result:
            return result
    raise AssertionError('Timed out waiting for the GUI')

g=core.SessionReaderGUI(); errors=[]
g.report_callback_exception=lambda *args:errors.append(str(args))
g.tk.createcommand('bgerror',lambda msg:errors.append(msg))
pump(g,9)
assert g.title().startswith('FaradaIQ eStudio v337'),g.title()
assert str(g.estudio_brand_label.cget('text'))=='FaradaIQ eStudio'
assert set(g.estudio_pages)=={'Data','Analysis','SciLink','Reports','Settings'}
assert g.desc_text.winfo_viewable()
assert g.desc_text.winfo_height()>80,g.desc_text.winfo_height()
assert g.estudio_stop.winfo_viewable()
assert g.estudio_credits.winfo_viewable()
sidebar = g.estudio_sidebar
assert sidebar.winfo_viewable()
assert sidebar.winfo_rootx() < g.estudio_pages['Data'].winfo_rootx()
actions = {str(w.cget('text')): w for w in walk(sidebar) if w.winfo_class() == 'TButton'}
for name in ('New Session', 'Save Session', 'Load Session', 'Open Data Browser', 'Choose Folder', 'Load Browser Session'):
    assert name in actions and actions[name].winfo_viewable(), name
assert g.estudio_results_tree.winfo_viewable()
g._faradaiq_pyseccm_progress.configure(maximum=10,value=4)
pump(g,1)
assert hasattr(g,'estudio_progress'),'footer has no tracked progress'
assert float(g.estudio_progress.cget('value'))==4
assert float(g.estudio_progress.cget('maximum'))==10
originals={str(w):w for w in g.estudio_original_controls}
for name in g.estudio_pages:
    g.estudio_show_page(name);pump(g)
    assert g.estudio_pages[name].winfo_viewable(),name
    assert g.estudio_stop.winfo_viewable()
    assert g.estudio_credits.winfo_viewable()
    assert actions['Load Session'].winfo_viewable()
    assert actions['Open Data Browser'].winfo_viewable()
g.estudio_show_page('Analysis');pump(g)
assert any(w.winfo_viewable() and w.winfo_class()=='TLabel' and str(w.cget('text'))=='Analysis tool' for w in walk(g.estudio_pages['Analysis']))
g.estudio_show_page('SciLink');pump(g)
scilink_actions={str(w.cget('text')):w for w in walk(g.estudio_pages['SciLink']) if w.winfo_class()=='TButton'}
assert {'Update SciLink','Open SciLink','Make SciLink-ready bundle'} <= scilink_actions.keys()
from faradaiq import managed_scilink
runtime_receipt={'version':'0.0.80','commit':'9dff180ddd7eb05dec73fecad44605c291d993688','changed':True}
with patch.object(managed_scilink,'current',return_value=runtime_receipt), \
     patch.object(managed_scilink,'update',return_value=runtime_receipt) as install_scilink, \
     patch.object(managed_scilink,'launch',return_value='http://127.0.0.1:8815') as launch_scilink:
    scilink_actions['Update SciLink'].invoke()
    wait_for(g,lambda:not g._estudio_scilink_busy)
    install_scilink.assert_called_once()
    launch_scilink.assert_called_once_with(no_open=False)
    assert '0.0.80' in g.status_var.get() and '9dff180ddd7e' in g.status_var.get()
    scilink_actions['Open SciLink'].invoke()
    wait_for(g,lambda:not g._estudio_scilink_busy)
    assert launch_scilink.call_count==2 and install_scilink.call_count==1
g.estudio_stop.invoke();pump(g)
assert len(g.estudio_control_clones)>20
for original,clone in g.estudio_control_clones:
    assert original.winfo_exists(), (str(original),str(clone),str(clone.cget("text")) if "text" in clone.keys() else "")
    if original.winfo_class() in ('TEntry','TCombobox','TCheckbutton','TSpinbox'):
        key='variable' if original.winfo_class()=='TCheckbutton' else 'textvariable'
        assert str(original.cget(key))==str(clone.cget(key)),(original,clone,key)
g.estudio_show_page('Data');g.geometry('1200x740');pump(g)
assert g.estudio_stop.winfo_rootx()+g.estudio_stop.winfo_width()<=g.winfo_rootx()+g.winfo_width()
assert g.estudio_results_tree.winfo_height() > 75
g.geometry('1100x700');pump(g)
assert g.estudio_results_tree.winfo_height() > 50
assert all(actions[name].winfo_viewable() for name in ('Load Session','Load Browser Session'))
g.geometry('1200x740')
g.estudio_theme_var.set('Light');g.estudio_apply_theme();pump(g)
g.estudio_theme_var.set('Dark');g.estudio_apply_theme();pump(g)
# Verify the unified popup still uses the original settings callback.
settings=next(w for w in walk(g.estudio_pages['Settings']) if w.winfo_class()=='TButton' and str(w.cget('text'))=='LLM settings')
settings.invoke();pump(g,.5)
tops=[w for w in g.winfo_children() if w.winfo_class()=='Toplevel']
assert tops,'settings did not open'
assert all('FaradaIQ Studio' not in w.title() for w in tops)
for w in tops:w.destroy()
pump(g,2)
g.estudio_credits.invoke();pump(g,.5)
about=g._estudio_about_window
assert 'v337' in about.title()
assert g.show_about_credits() is about
assert any(isinstance(w, ttk.Label) and w.cget('text') == 'FaradaIQ eStudio was designed by Venkateshkumar Prabhakaran.' for w in walk(about))
tree=next(w for w in walk(about) if w.winfo_class()=='Treeview')
projects={tree.item(item,'text'):item for item in tree.get_children()}
assert {'pySECCM tools','pyDRTtools','DearEIS','pyimpspec','PySimpleEChem','SciLink','Edison Scientific'} <= set(projects)
assert {'Benjamin A. Helfrecht (PNNL)', 'Original SECCM parser design and accompanying plotting tools'} <= {str(w.cget('text')) for w in walk(about) if isinstance(w, ttk.Label)}
assert 'LabRIC' not in projects
tree.selection_set(projects['pyDRTtools'])
pump(g)
assert any('Francesco Ciucci' in str(w.cget('text')) for w in walk(about) if w.winfo_class()=='TLabel')
buttons={w.cget('text'):w for w in walk(about) if w.winfo_class()=='TButton'}
with patch('faradaiq.estudio_about.webbrowser.open') as launched:
    buttons['Open project'].invoke()
    launched.assert_called_once_with('https://github.com/ciuccislab/pyDRTtools')
with patch('faradaiq.estudio_about.webbrowser.open') as launched:
    buttons['Integration setup'].invoke()
    assert launched.call_args.args[0].endswith('/docs/homepage.html#configure-integrations')
about.destroy();pump(g,2)
fixture=Path(temp_settings.name)/'NiCo_transport_analysis_ready_csv'
curve=fixture/'CV'/'point01'/'cycle01.csv';curve.parent.mkdir(parents=True)
curve.write_text('potential_V,current_A,time_s\n0.1,0.00002,0\n0.2,0.00003,1\n')
session_path=Path(temp_settings.name)/'NiCo.faradaiq_session.json'
actions['New Session'].invoke()
assert getattr(g, '_v219_working_session_context')['session_name']
g.file_var.set(str(curve))
g.output_dir_var.set(str(fixture))
with patch.object(filedialog, 'asksaveasfilename', return_value=str(session_path)):
    actions['Save Session'].invoke()
assert session_path.is_file()
assert f'Saved full FaradaIQ session: {session_path.name}' in g.status_var.get()
g.file_var.set('')
with patch.object(filedialog, 'askopenfilename', return_value=str(session_path)), patch.object(messagebox, 'showinfo'):
    actions['Load Session'].invoke()
assert g.file_var.get() == str(curve)

from faradaiq_canonical_bundle import build_canonical_bundle
bundle_path=Path(build_canonical_bundle(fixture, base_name='NiCo_transport')['h5_path'])
with patch.object(filedialog, 'askdirectory', return_value=str(bundle_path.parent)):
    actions['Choose Folder'].invoke()
assert g.output_dir_var.get() == str(fixture)
tree=g.estudio_results_tree
root_item=tree.get_children()[0]
bundle_item=wait_for(g, lambda: next((item for item in tree.get_children(root_item)
                                    if tree.item(item,'text') == bundle_path.name), None))
tree.selection_set(bundle_item)
actions['Open Data Browser'].invoke()
browser=wait_for(g, lambda: next((w for w in g.winfo_children() if isinstance(w, core.FaradaIQH5Browser)), None))
assert browser.h5_path == bundle_path
wait_for(g, lambda: browser.tree.get_children())
browser.plot_title_var.set('Stored NiCo view')
browser_session_path=Path(temp_settings.name)/'NiCo_browser_session.json'
with patch.object(filedialog, 'asksaveasfilename', return_value=str(browser_session_path)):
    browser.save_browser_session_dialog()
assert browser_session_path.is_file()
browser.destroy();pump(g)
with patch.object(filedialog, 'askopenfilename', return_value=str(browser_session_path)):
    actions['Load Browser Session'].invoke()
browser=wait_for(g, lambda: next((w for w in g.winfo_children() if isinstance(w, core.FaradaIQH5Browser)), None))
assert browser.h5_path == bundle_path
assert browser.plot_title_var.get() == 'Stored NiCo view'
browser.destroy();pump(g)

browser=core.FaradaIQH5Browser(g,cache_info={'cache_root':str(fixture),'manifest':{'techniques_detected':['CV / LSV'],'technique_counts':{'CV / LSV':1}}},base_name='NiCo_transport')
pump(g,4.5)
assert browser.tree.get_children()
texts=[str(w.cget('text')) for w in walk(browser) if 'text' in w.keys()]
assert any('SciLink' in t for t in texts)
assert any('Edison' in t for t in texts)
assert any('Smart' in t or 'Suggestions' in t for t in texts)
assert 'v337' in browser.title()
browser.destroy();pump(g,2)
resolver=core._v64_analysis_root_for_browser;showinfo=messagebox.showinfo
core._v64_analysis_root_for_browser=lambda gui:(fixture,'NiCo_transport')
messagebox.showinfo=lambda *args,**kwargs:None
try:
    g.estudio_make_scilink_bundle()
    deadline=time.monotonic()+15
    while getattr(g,'_estudio_bundle_busy',False) and time.monotonic()<deadline:pump(g,.1)
    assert not g._estudio_bundle_busy
    assert Path(g._estudio_last_bundle['h5_path']).exists() and curve.exists()
finally:core._v64_analysis_root_for_browser=resolver;messagebox.showinfo=showinfo
g._faradaiq_pyseccm_progress.configure(maximum=100,value=0)
pump(g,1)
g.geometry('1568x820');g.estudio_show_page('Data');pump(g)
if os.environ.get('ESTUDIO_SCREENSHOT'):
    from PIL import ImageGrab
    ImageGrab.grab(xdisplay=os.environ.get('DISPLAY')).save(os.environ['ESTUDIO_SCREENSHOT'])
assert not errors,errors
import threading
cancel=threading.Event();worker=threading.Thread(target=lambda:cancel.wait(2))
g._estudio_bundle_thread=worker;g._estudio_bundle_cancel=cancel;worker.start()
g.destroy();assert cancel.is_set();worker.join(2)
end=time.monotonic()+2
while not getattr(g,'_estudio_destroyed',False) and time.monotonic()<end:g.update();time.sleep(.025)
assert g._estudio_destroyed and not worker.is_alive()
assert not errors,errors
temp_settings.cleanup()
print('eStudio v337 GUI smoke: PASS',flush=True)
sys.exit(0)
