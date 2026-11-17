import json, time
import faradaiq_studio_core as c

# GUI smoke must never wait on modal dialogs. v217 also disables the legacy
# optional-dependency prompt in production.
c.messagebox.askyesno=lambda *a,**k: False
c.messagebox.showinfo=lambda *a,**k: None
c.messagebox.showwarning=lambda *a,**k: None
c.messagebox.showerror=lambda *a,**k: None

g=c.SessionReaderGUI()
g.withdraw()
# allow all delayed cleanup callbacks
end=time.time()+3.5
while time.time()<end:
    g.update()
    time.sleep(0.02)
texts=[]
def walk(w):
    for ch in w.winfo_children():
        try:
            t=str(ch.cget('text') or '')
            if t: texts.append(t)
        except Exception: pass
        walk(ch)
walk(g)
counts={x:sum(1 for t in texts if t.strip().lower()==x) for x in ('new session','save session','load session','auto save session')}

frame=getattr(g,'_v753_session_banner_frame',None)
order={}
if frame is not None:
    for ch in frame.winfo_children():
        try:
            text=str(ch.cget('text') or '')
            info=ch.grid_info()
            if text in {'New Session','Save Session','Load Session','Auto Save session'} and info:
                order[text]=int(info.get('column',-1))
        except Exception: pass
print(json.dumps({'title':g.title(),'counts':counts,'status':g.status_var.get(),'cache_default':bool(g.auto_build_browser_cache_after_parser_var.get()),'cache_status':g.browser_cache_status_var.get(),'tagline':any('Electrochemical Intelligence for Analyze, Control and Discover' == t for t in texts),'order':order}))
assert counts=={'new session':1,'save session':1,'load session':1,'auto save session':1},counts
assert [order[x] for x in ('New Session','Save Session','Load Session','Auto Save session')]==[2,3,4,5],order
assert 'v240' in g.title(),g.title()
assert g.auto_build_browser_cache_after_parser_var.get() is True
assert getattr(g,'_v217_persistent_status_installed',False) is True
assert getattr(g,'_v217_theme_selector_installed',False) is True
assert any(t == 'After export + parser runs: automatically build browser cache' for t in texts)
assert any(t == 'Electrochemical Intelligence for Analyze, Control and Discover' for t in texts)
g.destroy()
