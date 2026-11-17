"""eStudio presentation layer over the validated scientific engine.

Original controls remain alive: proxy buttons invoke their original command,
entry/check controls share Tcl variables. Scientific state and APIs are unchanged.
"""
from __future__ import annotations
import json
import os
import re
import sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = 'FaradaIQ eStudio'
TITLE = APP_NAME + ' v331'
TAGLINE = 'Electrochemical Intelligence for Analyze, Control and Discover'
AI_NOTICE = 'Scientific review required: AI-assisted analysis can be incorrect or unsupported. Verify data, units, model assumptions, fits, uncertainty, and cited sources before using results.' 
DARK = dict(bg='#0b1220',card='#131f31',field='#0e1828',fg='#e5edf8',muted='#9fb0c8',line='#293b53',accent='#39d4b5',ink='#09251f',select='#244567')
LIGHT = dict(bg='#edf2f7',card='#ffffff',field='#f5f8fc',fg='#162b44',muted='#506784',line='#ced8e5',accent='#087e70',ink='#ffffff',select='#d9edf2')

def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)

def brand_text(value):
    if not isinstance(value,str) or '/' in value or '\\' in value:
        return value
    return re.sub(r'FaradaIQ (?:eStudio|Studio)(?: [vV]\d+(?:\.\d+)*)?',APP_NAME,value)

def brand_window_title(value):
    return re.sub(r'FaradaIQ (?:eStudio|Studio)(?: [vV]\d+(?:\.\d+)*)?',TITLE,str(value))


def _text(w):
    try:return str(w.cget('text'))
    except tk.TclError:return ''

def _settings_path():
    base=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.config')))
    return base/'FaradaIQ'/'eStudio'/'ui.json'

def set_icon(window):
    path=ROOT/'assets'/'estudio.ico'
    if sys.platform=='win32':
        try:window.iconbitmap(str(path))
        except tk.TclError:pass
    try:
        window._estudio_icon=tk.PhotoImage(master=window,file=str(ROOT/'assets'/'estudio.png'))
        window.iconphoto(True,window._estudio_icon)
    except tk.TclError:pass

def apply_theme(root,mode='Dark'):
    p=LIGHT if mode=='Light' else DARK
    style=ttk.Style(root)
    if style.theme_use()!='clam':style.theme_use('clam')
    font=('Segoe UI',10) if sys.platform=='win32' else ('Helvetica',11)
    root.option_add('*Font',font)
    for name in ('TFrame','App.TFrame','Card.TFrame','Hero.TFrame','E.TFrame'):
        style.configure(name,background=p['bg'] if name in ('App.TFrame','E.TFrame') else p['card'])
    for name in ('TLabel','Card.TLabel','HeroSub.TLabel','Status.TLabel','E.TLabel'):
        style.configure(name,background=p['card'],foreground=p['fg'],font=font)
    for name in ('Muted.TLabel','HeroCredit.TLabel'):
        style.configure(name,background=p['card'],foreground=p['muted'],font=font)
    style.configure('HeroTitle.TLabel',background=p['card'],foreground=p['fg'],font=(font[0],18,'bold'))
    style.configure('Section.TLabel',background=p['card'],foreground=p['fg'],font=(font[0],11,'bold'))
    style.configure('E.Title.TLabel',background=p['bg'],foreground=p['fg'],font=(font[0],20,'bold'))
    style.configure('E.Subtitle.TLabel',background=p['bg'],foreground=p['muted'],font=font)
    style.configure('TButton',background=p['card'],foreground=p['fg'],bordercolor=p['line'],lightcolor=p['line'],darkcolor=p['line'],font=font,padding=(12,7),borderwidth=1)
    style.map('TButton',background=[('active',p['select'])],foreground=[('disabled',p['muted'])])
    for name in ('Accent.TButton','E.Primary.TButton'):
        style.configure(name,background=p['accent'],foreground=p['ink'],font=(font[0],10,'bold'),padding=(14,8))
        style.map(name,background=[('active','#29bca0'),('disabled',p['line'])],foreground=[('disabled',p['muted'])])
    style.configure('E.Stop.TButton',background='#a93449',foreground='white',padding=(16,8),font=(font[0],10,'bold'))
    style.map('E.Stop.TButton',background=[('active','#cb455d')])
    style.configure('E.Sidebar.TFrame', background=p['card'])
    style.configure('E.RailHeading.TLabel', background=p['card'], foreground=p['muted'], font=(font[0],9,'bold'))
    style.configure('E.RailStatus.TLabel', background=p['card'], foreground=p['muted'], font=(font[0],9))
    style.configure('E.Nav.TButton',background=p['card'],foreground=p['fg'],anchor='w',padding=(11,5),borderwidth=0)
    style.map('E.Nav.TButton',background=[('pressed',p['select']),('active',p['select'])])
    style.configure('E.Selected.TButton',background=p['select'],foreground=p['fg'],anchor='w',padding=(11,5),borderwidth=0,font=(font[0],10,'bold'))
    style.configure('E.RailAction.TButton', anchor='w', padding=(9,4))
    style.configure('E.RailPrimary.TButton', background=p['accent'], foreground=p['ink'],
                    anchor='w', padding=(9,6), font=(font[0],10,'bold'))
    style.map('E.RailPrimary.TButton',background=[('active',p['select'])],foreground=[('active',p['fg'])])
    for name in ('TEntry','TCombobox','TSpinbox'):
        style.configure(name,fieldbackground=p['field'],background=p['field'],foreground=p['fg'],insertcolor=p['fg'],bordercolor=p['line'],padding=7,font=font)
        style.map(name,fieldbackground=[('readonly',p['field'])],foreground=[('readonly',p['fg']),('disabled',p['muted'])],selectbackground=[('!disabled',p['select'])])
    for name in ('TCheckbutton','TRadiobutton'):
        style.configure(name,background=p['card'],foreground=p['fg'],font=font,padding=4)
        style.map(name,background=[('active',p['card'])],indicatorbackground=[('selected',p['accent']),('!selected',p['field'])])
    style.configure('TLabelframe',background=p['card'],bordercolor=p['line'],padding=10)
    style.configure('TLabelframe.Label',background=p['card'],foreground=p['muted'],font=(font[0],10,'bold'))
    style.configure('TNotebook',background=p['card'],borderwidth=0)
    style.configure('TNotebook.Tab',background=p['card'],foreground=p['muted'],padding=(16,9),font=font)
    style.map('TNotebook.Tab',background=[('selected',p['select'])],foreground=[('selected',p['fg'])])
    style.configure('Treeview',background=p['field'],fieldbackground=p['field'],foreground=p['fg'],rowheight=28,font=font,bordercolor=p['line'])
    style.configure('Treeview.Heading',background=p['card'],foreground=p['muted'],font=(font[0],10,'bold'))
    style.map('Treeview',background=[('selected',p['select'])],foreground=[('selected',p['fg'])])
    style.configure('E.Results.Treeview',rowheight=23)
    style.configure('E.Results.Vertical.TScrollbar',background=p['line'],troughcolor=p['field'],
                    bordercolor=p['line'],arrowcolor=p['muted'])
    style.configure('TSeparator',background=p['line'])
    style.configure('TPanedwindow',background=p['line'])
    style.configure('Horizontal.TProgressbar',background=p['accent'],troughcolor=p['field'],borderwidth=0)
    root.configure(background=p['bg'])
    for w in walk(root):
        cls=w.winfo_class()
        try:
            if cls in ('Text','Listbox'):
                w.configure(background=p['field'],foreground=p['fg'],selectbackground=p['select'],selectforeground=p['fg'],font=font,highlightthickness=0,borderwidth=0)
                if cls=='Text':w.configure(insertbackground=p['fg'],padx=10,pady=9)
            elif cls in ('Canvas','Frame'):
                w.configure(background=p['card'],highlightthickness=0)
            elif cls=='Label':w.configure(background=p['card'],foreground=p['fg'],font=font)
            if cls=='TLabel':
                label_style=str(w.cget('style'))
                if label_style.startswith('E.Rail'):
                    w.configure(font='',foreground=p['muted'],background=p['card'])
                else:
                    w.configure(font='',foreground=p['fg'],background=p['bg'] if label_style.startswith('E.') else p['card'])
            if cls=='TFrame' and not str(w.cget('style')).startswith('E.'):w.configure(style='Card.TFrame')
            if cls in ('Button','Checkbutton','Radiobutton','Entry','Menu'):w.configure(background=p['card'],foreground=p['fg'])
            if cls=='TLabel' and not str(w.cget('style')).startswith('E.'):
                if w.winfo_reqwidth()>800:w.configure(wraplength=760)
        except tk.TclError:pass
    return p


def arrange(gui,core):
    """Consolidate UI while retaining every original control and callback."""
    if sys.platform=='darwin':
        output=str(gui.output_dir_var.get())
        if re.match(r'^[A-Za-z]:[\\/]',output):gui.output_dir_var.set(str(Path.home()/'Data'/'ANALYSIS'/'FaradaIQ'/'eStudio'))
    from . import v250_reliability as services
    services._show_startup_selector(gui)
    from . import v244_workflow_cleanup as cleanup
    cleanup.compact_tools(gui)
    original=list(walk(gui))
    gui.estudio_original_controls=original
    gui.estudio_control_clones=[]
    gui.estudio_hidden_originals=[]
    # All old panels share this parent, so Tk allows the original preview pane
    # to be managed inside a descendant workspace using pack(in_=...).
    host=gui.desc_text
    while host.master is not gui:host=host.master
    panels=list(host.winfo_children())
    content=next(w for w in panels if w.winfo_class()=='TPanedwindow')
    controls=next(w for w in panels if any(_text(c)=='Input file / folder' for c in w.winfo_children()))
    tools=next(w for w in panels if w.winfo_class()=='TLabelframe')
    for w in panels:
        if w.winfo_manager()=='pack':w.pack_forget()
        elif w.winfo_manager()=='grid':w.grid_remove()
    for w in list(gui.winfo_children()):
        if w is not host and w.winfo_manager()=='place':w.place_forget()
    host.configure(padding=0)
    mode='Dark'
    try:
        settings=json.loads(_settings_path().read_text())
        if isinstance(settings,dict):mode=settings.get('theme','Dark')
    except (OSError,ValueError):pass
    gui.estudio_theme_var=tk.StringVar(master=gui,value=mode if mode in ('Dark','Light') else 'Dark')
    gui.estudio_shell=shell=ttk.Frame(host,style='E.TFrame')
    shell.pack(fill='both',expand=True)
    header=ttk.Frame(shell,style='E.TFrame',padding=(19,9,18,8));header.pack(fill='x')
    image=tk.PhotoImage(master=gui,file=str(ROOT/'assets'/'estudio.png'))
    factor=max(1,image.width()//46)
    gui._estudio_header_icon=image.subsample(factor,factor)
    ttk.Label(header,image=gui._estudio_header_icon,style='E.Subtitle.TLabel').pack(side='left',padx=(0,13))
    brand=ttk.Frame(header,style='E.TFrame');brand.pack(side='left')
    gui.estudio_brand_label=ttk.Label(brand,text=APP_NAME,style='E.Title.TLabel');gui.estudio_brand_label.pack(anchor='w')
    ttk.Label(brand,text=TAGLINE,style='E.Subtitle.TLabel').pack(anchor='w',pady=(3,0))
    stop_original=next(w for w in original if _text(w)=='Stop jobs')
    def stop_all():
        event=getattr(gui,'_estudio_bundle_cancel',None)
        if event is not None:event.set()
        stop_original.invoke()
    gui.estudio_stop=ttk.Button(header,text='STOP',style='E.Stop.TButton',command=stop_all)
    gui.estudio_stop.pack(side='right',padx=(14,0))
    gui.estudio_credits=ttk.Button(header,text='Credits',command=gui.show_about_credits)
    gui.estudio_credits.pack(side='right')
    ttk.Separator(shell).pack(fill='x')
    # Footer is managed before the expandable body, guaranteeing reserved space.
    footer=ttk.Frame(shell,padding=(18,9));footer.pack(side='bottom',fill='x')
    ttk.Label(footer,textvariable=gui.status_var,style='Muted.TLabel',wraplength=800).pack(side='left',fill='x',expand=True)
    resource=getattr(gui,'_v245_resource_var',None) or getattr(gui,'_v244_resource_var',None)
    if resource is not None:ttk.Label(footer,textvariable=resource,style='Muted.TLabel').pack(side='right',padx=10)
    pb=next((w for w in original if w.winfo_class()=='TProgressbar'),None)
    if pb:
        kw={k:pb.cget(k) for k in ('variable','maximum','mode','orient')}
        gui.estudio_progress=ttk.Progressbar(footer,length=120,**kw)
        gui.estudio_progress.pack(side='right',padx=12)
    body=ttk.Frame(shell,style='E.TFrame');body.pack(fill='both',expand=True)
    gui.estudio_sidebar=nav=ttk.Frame(body,style='E.Sidebar.TFrame',padding=(12,10,12,8))
    nav.pack(side='left',fill='y')
    ttk.Separator(body,orient='vertical').pack(side='left',fill='y')
    stage=ttk.Frame(body,style='E.TFrame',padding=(18,10,18,10));stage.pack(fill='both',expand=True)
    stage.rowconfigure(0,weight=1);stage.columnconfigure(0,weight=1)
    gui.estudio_pages={};gui.estudio_nav={}
    ttk.Label(nav,text='Workspace',style='E.RailHeading.TLabel').pack(anchor='w',padx=4,pady=(0,4))
    for name in ('Data','Analysis','SciLink','Reports','Settings'):
        page=ttk.Frame(stage,style='E.TFrame');page.grid(row=0,column=0,sticky='nsew')
        gui.estudio_pages[name]=page
        btn=ttk.Button(nav,text=name,style='E.Nav.TButton',command=lambda n=name:gui.estudio_show_page(n))
        btn.pack(fill='x',pady=1);gui.estudio_nav[name]=btn
    gui.estudio_active_page='Data'
    def show(name):
        for key,page in gui.estudio_pages.items():
            if key==name:page.grid()
            else:page.grid_remove()
        gui.estudio_pages[name].tkraise();gui.estudio_active_page=name
        if name=='Data':
            content.pack(in_=gui.estudio_pages['Data'],fill='both',expand=True,pady=(2,0));content.lift()
        else:content.pack_forget()
        for key,btn in gui.estudio_nav.items():btn.configure(style='E.Selected.TButton' if key==name else 'E.Nav.TButton')
    gui.estudio_show_page=show

    def heading(page,title,subtitle):
        ttk.Label(page,text=title,style='E.Title.TLabel').pack(anchor='w')
        ttk.Label(page,text=subtitle,style='E.Subtitle.TLabel',wraplength=1000).pack(anchor='w',pady=(5,16))
    def card(page,title):
        box=ttk.LabelFrame(page,text=title,padding=12);box.pack(fill='x',pady=(0,12));return box
    def clone(source,parent):
        cls=source.winfo_class()
        if cls=='TButton':
            w=ttk.Button(parent,text=brand_text(_text(source)),command=source.invoke)
            if 'Accent' in str(source.cget('style')):w.configure(style='E.Primary.TButton')
        elif cls=='TCheckbutton':w=ttk.Checkbutton(parent,text=_text(source),variable=source.cget('variable'),onvalue=source.cget('onvalue'),offvalue=source.cget('offvalue'),command=lambda s=source:s.tk.call(s.cget('command')) if s.cget('command') else None)
        elif cls in ('TEntry','TCombobox','TSpinbox'):
            kw=dict(textvariable=source.cget('textvariable'))
            if cls=='TCombobox':
                kw.update(values=source.cget('values'),state=source.cget('state'))
                w=ttk.Combobox(parent,**kw)
                w.bind('<<ComboboxSelected>>',lambda _e,s=source:s.event_generate('<<ComboboxSelected>>'))
            elif cls=='TSpinbox':w=ttk.Spinbox(parent,from_=source.cget('from'),to=source.cget('to'),width=6,**kw)
            else:w=ttk.Entry(parent,**kw)
            for event in ('<FocusOut>','<Return>'):
                w.bind(event,lambda _e,e=event,s=source:s.event_generate(e))
        elif cls=='TLabel':
            label=brand_text(_text(source))
            if label=='AI workflow':label='Services'
            if label.startswith('Startup mode:'):label='Scientific services: '+label.split(':',1)[1].split('. Edison remains')[0].strip()+'. Review prompts and selected files before submission.'
            kw=dict(text=label,style='Muted.TLabel',wraplength=850,justify='left')
            if source.cget('textvariable'):kw['textvariable']=source.cget('textvariable')
            w=ttk.Label(parent,**kw)
        elif cls=='TProgressbar':w=ttk.Progressbar(parent,variable=source.cget('variable'),maximum=source.cget('maximum'),mode=source.cget('mode'))
        else:
            w=ttk.Frame(parent)
            for ch in source.winfo_children():
                if not ch.winfo_manager():continue
                child=clone(ch,w)
                if ch.winfo_manager()=='grid':
                    info=ch.grid_info();child.grid(row=info['row'],column=info['column'],columnspan=info.get('columnspan',1),sticky='w',padx=4,pady=4)
                else:
                    info=ch.pack_info()
                    child.pack(side=info.get('side','top'),fill=info.get('fill','none'),expand=info.get('expand',False),padx=4,pady=3)
            return w
        gui.estudio_control_clones.append((source,w));return w
    gui._estudio_clone=clone
    browser=next(w for w in original if _text(w)=='Data browser + analysis')
    from .estudio_sidebar import ResultsNavigator

    ttk.Separator(nav).pack(fill='x',pady=(7,6))
    ttk.Label(nav,text='Session',style='E.RailHeading.TLabel').pack(anchor='w',padx=4)
    saved_session=tk.StringVar(master=gui,value='Working session')
    ttk.Label(nav,textvariable=saved_session,style='E.RailStatus.TLabel',width=27).pack(anchor='w',padx=4,pady=(0,3))
    session_row=ttk.Frame(nav,style='E.Sidebar.TFrame')
    session_row.pack(fill='x')
    for col, label in enumerate(('New Session','Save Session','Load Session')):
        source=next(w for w in original if _text(w)==label)
        button=clone(source,session_row)
        button.configure(style='E.RailAction.TButton')

        def run_session(action=label, original_button=source):
            result=original_button.invoke()
            if action=='New Session':
                ctx=getattr(gui,'_v219_working_session_context',{}) or {}
                saved_session.set(ctx.get('session_name') or 'Working session')
            elif result:
                name=Path(str(result)).name
                for suffix in ('.faradaiq_session.json','.faradaiq_main_session.json','.json'):
                    if name.endswith(suffix):
                        name=name.removesuffix(suffix)
                        break
                saved_session.set(name)

        button.configure(command=run_session)
        if col<2:
            session_row.columnconfigure(col,weight=1)
            button.grid(row=0,column=col,sticky='ew',padx=(0,3) if col==0 else (3,0))
        else:
            button.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(4,0))
    ttk.Checkbutton(nav,text='Auto-save session',variable=gui.auto_save_session_var,
                    command=lambda:core['_v177_toggle_autosave_session'](gui)).pack(anchor='w',padx=2)

    ttk.Separator(nav).pack(fill='x',pady=(5,6))
    ttk.Label(nav,text='Data Browser',style='E.RailHeading.TLabel').pack(anchor='w',padx=4,pady=(0,4))
    results_frame=ttk.Frame(nav,style='E.Sidebar.TFrame')
    navigator=ResultsNavigator(results_frame,gui,core,browser.invoke)
    gui.estudio_results_tree=navigator.tree
    ttk.Button(nav,text='Open Data Browser',style='E.RailPrimary.TButton',
               command=navigator.open_selected).pack(fill='x')
    ttk.Button(nav,text='Load Browser Session',style='E.RailAction.TButton',
               command=navigator.load_browser_session).pack(fill='x',pady=(4,0))
    ttk.Button(nav,text='Choose Folder',style='E.RailAction.TButton',
               command=navigator.choose_folder).pack(fill='x',pady=(4,0))
    scrollbar=ttk.Scrollbar(results_frame,orient='vertical',style='E.Results.Vertical.TScrollbar',
                            command=navigator.tree.yview)
    navigator.tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side='right',fill='y')
    navigator.tree.pack(side='left',fill='both',expand=True)
    results_frame.pack(fill='both',expand=True,pady=(6,0))

    data=gui.estudio_pages['Data']
    heading(data,'Experiment data','Select a file or folder to inspect. Open exported results from the Data Browser.')
    source_box=card(data,'EXPERIMENT')
    for i,(label,var,browse) in enumerate((('Input',gui.file_var,gui.browse_input_folder),('Output',gui.output_dir_var,gui.browse_output_dir))):
        ttk.Label(source_box,text=label).grid(row=i,column=0,sticky='w',padx=(0,12),pady=4)
        ttk.Entry(source_box,textvariable=var).grid(row=i,column=1,sticky='ew',pady=4)
        if i==0:
            ttk.Button(source_box,text='File',command=gui.browse_file).grid(row=i,column=2,padx=(10,4))
            ttk.Button(source_box,text='Folder',command=browse).grid(row=i,column=3,padx=4)
            ttk.Button(source_box,text='Inspect',command=gui.load_and_inspect,style='E.Primary.TButton').grid(row=i,column=4,padx=(4,0))
        else:
            ttk.Button(source_box,text='Choose',command=browse).grid(row=i,column=2,padx=(10,4))
            ttk.Button(source_box,text='Export',command=gui.export_outputs,style='E.Primary.TButton').grid(row=i,column=3,padx=4)
    source_box.columnconfigure(1,weight=1)
    data_actions=ttk.Frame(source_box);data_actions.grid(row=2,column=0,columnspan=5,sticky='ew',pady=(8,0))
    clone(next(w for w in original if _text(w)=='Open jobs'),data_actions).pack(side='left',padx=(0,6))
    from .estudio_bundle_ui import start_bundle
    gui.estudio_make_scilink_bundle=lambda:start_bundle(gui,core)
    ttk.Button(data_actions,text='Make SciLink-ready bundle',command=gui.estudio_make_scilink_bundle).pack(side='left',padx=6)
    # Original live text widgets: no copies, so loading/restore/popouts retain state.
    content.pack(in_=data,fill='both',expand=True,pady=(2,0))
    content.configure(height=320)
    analysis=gui.estudio_pages['Analysis']
    heading(analysis,'Analysis workspace','Deterministic tools, interactive plots, batch jobs, and canonical HDF5 bundles.')
    children=tools.winfo_children()
    for marker,title in (('Analysis tool','TECHNIQUES & TOOLS'),('Export jobs','BATCH PROCESSING'),('Rebuild H5 bundle','DATA BUNDLE')):
        src=next((w for w in list(controls.winfo_children())+children if any(_text(x)==marker for x in walk(w))),None)
        if src:
            box=card(analysis,title);clone(src,box).pack(fill='x')
    b=card(analysis,'VISUALIZE & COMPARE')
    ttk.Button(b,text='Open data browser + analysis',command=browser.invoke,style='E.Primary.TButton').pack(anchor='w')
    ttk.Label(b,text='Overlay curves • EIS and DRT • Mott–Schottky • Tafel • Multidimensional views • Plot popouts',style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=(12,2))
    scilink=gui.estudio_pages['SciLink']
    heading(scilink,'Scientific reasoning','Review the exact prompt and selected files before submitting an analysis request.')
    for marker,title in (('Ask SciLink','SCILINK & SERVICES'),('LLM status','CONNECTION STATUS')):
        src=next((w for w in children if any(_text(x)==marker for x in walk(w))),None)
        if src:
            box=card(scilink,title);clone(src,box).pack(fill='x')
    handoff=card(scilink,'DATA HANDOFF')
    ttk.Button(handoff,text='Make SciLink-ready bundle',command=gui.estudio_make_scilink_bundle,style='E.Primary.TButton').pack(anchor='w')
    ttk.Label(handoff,text=AI_NOTICE,wraplength=900,style='Muted.TLabel').pack(anchor='w',pady=(12,0))
    info=card(scilink,'EXPERIMENT CONTEXT')
    ttk.Label(info,text='Edit the experimental description in Data. SciLink review retains the existing file-selection, electrochemistry skill, job tracking, and report workflows.',wraplength=850,style='Muted.TLabel').pack(anchor='w')
    ttk.Button(info,text='Edit experimental description',command=lambda:show('Data')).pack(anchor='w',pady=(12,0))
    reports=gui.estudio_pages['Reports']
    heading(reports,'Reports & exports','Configure the portable data browser and scientific PowerPoint generated during export.')
    moved=set()
    for marker,title in (('Create portable HTML data browser','PORTABLE DATA BROWSER'),('Create PowerPoint data summary','POWERPOINT SUMMARY')):
        src=next((w for w in controls.winfo_children() if any(_text(x)==marker for x in walk(w))),None)
        if src:
            box=card(reports,title);clone(src,box).pack(fill='x');moved.add(src)
    box=card(reports,'EXPORT & REVIEW')
    ttk.Button(box,text='Export experiment',command=gui.export_outputs,style='E.Primary.TButton').pack(side='left',padx=(0,10))
    ttk.Button(box,text='Browse exported results',command=browser.invoke).pack(side='left')
    settings=gui.estudio_pages['Settings']
    heading(settings,'Settings','Application appearance, output options, services, and advanced utilities.')
    box=card(settings,'APPEARANCE')
    ttk.Label(box,text='Theme').pack(side='left',padx=(0,12))
    theme=ttk.Combobox(box,values=('Dark','Light'),textvariable=gui.estudio_theme_var,state='readonly',width=18);theme.pack(side='left')
    def change_theme(*args):
        apply_theme(gui,gui.estudio_theme_var.get())
        try:
            path=_settings_path();path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps({'theme':gui.estudio_theme_var.get()}))
        except OSError:pass
    gui.estudio_apply_theme=change_theme;theme.bind('<<ComboboxSelected>>',change_theme)
    box=card(settings,'SERVICES & HELP')
    for text in ('LLM settings','Help','About / credits'):
        src=next((w for w in original if _text(w)==text),None)
        if src:clone(src,box).pack(side='left',padx=(0,8))
    box=card(settings,'OUTPUT OPTIONS')
    for src in controls.winfo_children():
        if any('Use shortened names' in _text(x) for x in walk(src)):
            clone(src,box).pack(fill='x')
    # Preserve all lesser-used original actions in a compact, scrollable drawer.
    box=card(settings,'ADVANCED CONTROLS')
    ttk.Button(box,text='Open all original controls…',command=lambda:show_advanced(gui,original,clone,apply_theme)).pack(anchor='w')
    ttk.Label(box,text='Session tracking, parser dependencies, cache options, preview fonts, and other retained utilities.',style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=(8,0))
    show('Data');set_icon(gui);change_theme()
    gui.set_status('Ready. Choose an experiment to begin.')
    gui.geometry('1568x820');gui.minsize(1100,700)
    def synchronize():
        if not gui.winfo_exists():return
        for src,dst in gui.estudio_control_clones:
            try:
                if src.winfo_exists() and dst.winfo_exists() and isinstance(dst,(ttk.Button,ttk.Combobox)):
                    dst.state(['disabled'] if src.instate(['disabled']) else ['!disabled'])
            except (tk.TclError,AttributeError):pass
        source_progress=getattr(gui,'_faradaiq_pyseccm_progress',pb)
        if source_progress is not None and hasattr(gui,'estudio_progress'):
            try:gui.estudio_progress.configure(value=source_progress.cget('value'),maximum=source_progress.cget('maximum'),mode=source_progress.cget('mode'))
            except tk.TclError:pass
        gui._estudio_sync=gui.after(700,synchronize)
    gui._estudio_sync=gui.after(700,synchronize)
    for delay in (250,900,1900,3700,7200):
        gui.after(delay,lambda:apply_theme(gui,gui.estudio_theme_var.get()))


def show_advanced(gui,original,clone,theme):
    win=tk.Toplevel(gui);win.title(f'{TITLE} — Advanced controls');win.geometry('1120x700');set_icon(win)
    outer=ttk.Frame(win,padding=14);outer.pack(fill='both',expand=True)
    ttk.Label(outer,text='All retained controls',style='Section.TLabel').pack(anchor='w',pady=(0,12))
    canvas=tk.Canvas(outer,highlightthickness=0);bar=ttk.Scrollbar(outer,orient='vertical',command=canvas.yview)
    canvas.configure(yscrollcommand=bar.set);bar.pack(side='right',fill='y');canvas.pack(fill='both',expand=True)
    inside=ttk.Frame(canvas,padding=10);item=canvas.create_window(0,0,window=inside,anchor='nw')
    inside.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.bind('<Configure>',lambda e:canvas.itemconfigure(item,width=e.width))
    for src in original:
        if src.winfo_class() in ('TButton','TCheckbutton','TCombobox','TSpinbox') and _text(src)!='STOP':
            row=ttk.Frame(inside);row.pack(fill='x',pady=3)
            if src.winfo_class() in ('TCombobox','TSpinbox'):
                label=' / '.join(_text(x) for x in src.master.winfo_children() if x.winfo_class()=='TLabel' and _text(x))
                ttk.Label(row,text=label[:100] or 'Options',width=30,wraplength=260).pack(side='left')
            clone(src,row).pack(side='left')
    def wheel(e):canvas.yview_scroll(-1 if e.delta>0 or getattr(e,'num',0)==4 else 1,'units')
    win.bind('<MouseWheel>',wheel);win.bind('<Button-4>',wheel);win.bind('<Button-5>',wheel)
    theme(win,gui.estudio_theme_var.get())


def install(core):
    from .portable_jobs import install as install_portable_jobs
    install_portable_jobs(core)
    os.environ.setdefault('FARADAIQ_AI_LAUNCH_MODE','scilink_edison_llm')
    os.environ.setdefault('FARADAIQ_DIRECT_EDISON_ENABLED','1')
    os.environ.setdefault('FARADAIQ_EDISON_ENABLED','1')
    os.environ.setdefault('FARADAIQ_LLM_TOOLS_ENABLED','1')
    os.environ.setdefault('FARADAIQ_SCIENCE_MODE','scilink_edison_llm')
    # Existing normalizers resolve their globals on each call, including delayed
    # labels, dialogs, popouts, HTML/PPT metadata and tracking events.
    for name,module in list(sys.modules.items()):
        if not name.startswith('faradaiq'):continue
        for attr,value in dict(VERSION='v331',APP_VERSION='0.331.0',SEMVER='0.331.0',VERSION_NUMBER=327,APP_NAME='FaradaIQ eStudio',APP_TITLE=TITLE,INSTALL_TARGET=r'C:\FaradaIQ\eStudio\V331').items():
            if hasattr(module,attr):setattr(module,attr,'v331' if attr=='APP_VERSION' and name in ('faradaiq','faradaiq.release_identity') else value)
        if name=='faradaiq.v290_portable_browser':module.SCHEMA='faradaiq-portable-browser-v331'
        if hasattr(module,'normalize_release_text'):module.normalize_release_text=brand_text
        if hasattr(module,'FRONT_SLIDE_FOOTER'):module.FRONT_SLIDE_FOOTER='Made by '+TITLE
    for k,v in dict(APP_TITLE=TITLE,FARADAIQ_PUBLIC_VERSION='v331',FARADAIQ_APP_VERSION='v331',FARADAIQ_RELEASE_VERSION='v331',FARADAIQ_RELEASE_TITLE=TITLE,FARADAIQ_INSTALL_TARGET=r'C:\FaradaIQ\eStudio\V331').items():core[k]=v
    core['_v290_normalize_release_text']=brand_text
    old_theme=core.get('_v36_apply_theme')
    def themed(root,*args,**kwargs):
        result=old_theme(root,*args,**kwargs) if callable(old_theme) else None
        main=root.winfo_toplevel()
        try:owner=main if hasattr(main,'estudio_theme_var') else main.master
        except AttributeError:owner=None
        apply_theme(root,getattr(owner,'estudio_theme_var',None).get() if getattr(owner,'estudio_theme_var',None) is not None else 'Dark')
        return result
    core['_v36_apply_theme']=themed
    previous_banner=core.get('_v177_insert_main_session_buttons')
    def banner(gui,*args,**kwargs):
        if hasattr(gui,'estudio_shell'):return True
        return previous_banner(gui,*args,**kwargs) if callable(previous_banner) else None
    core['_v177_insert_main_session_buttons']=banner
    def public_title(window,base=TITLE):
        window.tk.call('wm','title',window._w,brand_window_title(base))
    for helper in ('_v753_apply_title','_v138_apply_title','_v208_apply_title','_v240_apply_title'):
        core[helper]=public_title
    for helper in ('_v753_add_session_banner','_v84_hide_legacy_status_labels','_v80_reapply_fonts','_v177_apply_main_window_cleanup','_v219_rebuild_session_banner','_v89_reset_session_banner','_v89_configure_ai_workflow_toolbar','_v105_configure_ai_workflow_toolbar','_v86_configure_ai_workflow_toolbar','_v84_configure_ai_workflow_toolbar'):
        previous_helper=core.get(helper)
        def guarded(window,*args,_previous=previous_helper,_helper=helper,**kwargs):
            if _helper=='_v80_reapply_fonts':
                owner=window.winfo_toplevel()
                mode=getattr(owner,'estudio_theme_var',None)
                apply_theme(window,mode.get() if mode is not None else 'Dark')
                return None
            if hasattr(window,'estudio_shell'):
                if _helper=='_v80_reapply_fonts':apply_theme(window,window.estudio_theme_var.get())
                if _helper=='_v219_rebuild_session_banner':core['_v219_refresh_session_banner_text'](window)
                return None
            return _previous(window,*args,**kwargs) if callable(_previous) else None
        core[helper]=guarded
    for module_name,function_name in (('faradaiq.v250_reliability','_configure_main_ai_toolbar'),('faradaiq.v239_scilink_edison','configure_main_ai_toolbar')):
        module=sys.modules.get(module_name)
        if module is not None:
            previous_toolbar=getattr(module,function_name)
            def toolbar(gui,*args,_previous=previous_toolbar,**kwargs):
                if hasattr(gui,'estudio_shell'):return None
                return _previous(gui,*args,**kwargs)
            setattr(module,function_name,toolbar)
    # Several historical modules retain aliases to toolbar rebuilders. Freeze
    # these entry points after consolidation so their widgets/callbacks survive.
    def protect(fn):
        def guarded(gui,*args,**kwargs):
            if hasattr(gui,'estudio_shell'):return None
            return fn(gui,*args,**kwargs)
        return guarded
    for key,fn in list(core.items()):
        if callable(fn) and ('configure_ai_workflow_toolbar' in key or 'configure_main_ai_toolbar' in key or 'CONFIG_AI_TOOLBAR' in key or 'configure_main_ai_toolbar' in getattr(fn,'__name__','')):core[key]=protect(fn)
    for name,module in list(sys.modules.items()):
        if name.startswith('faradaiq.'):
            for key in ('configure_main_ai_toolbar','_configure_main_ai_toolbar','_show_startup_selector'):
                fn=getattr(module,key,None)
                if callable(fn):setattr(module,key,protect(fn))
    core['_v83_set_windows_app_id']=lambda:__import__('ctypes').windll.shell32.SetCurrentProcessExplicitAppUserModelID('FaradaIQ.eStudio') if sys.platform=='win32' else None
    # Bypass historical class-specific title rewrites at the native WM boundary.
    def native_title(self,string=None):
        if string is None:return self.tk.call('wm','title',self._w)
        return self.tk.call('wm','title',self._w,brand_window_title(string))
    tk.Wm.title=tk.Wm.wm_title=native_title
    for class_name in ('SessionReaderGUI','FaradaIQH5Browser','FaradaIQDataBrowser'):
        klass=core.get(class_name)
        if klass is not None:klass.title=klass.wm_title=native_title
    cls=core['SessionReaderGUI']
    from .estudio_about import show_about
    cls.show_about_credits=show_about
    previous=cls.__init__
    def init(self,*args,**kwargs):
        previous(self,*args,**kwargs)
        arrange(self,core);self.title(TITLE)
    cls.__init__=init

    # Cancel pending Tcl timers before deleting their owning Python commands.
    # Raw Tcl cancellation leaves command ownership with the originating widget.
    def cancel_owned(window,all_timers=False):
        commands=set()
        for widget in walk(window):commands.update(getattr(widget,'_tclCommands',None) or [])
        for timer in window.tk.splitlist(window.tk.call('after','info')):
            try:
                script=window.tk.call('after','info',timer)[0]
                tokens=window.tk.splitlist(script)
                if all_timers or any(str(token) in commands for token in tokens):window.tk.call('after','cancel',timer)
            except tk.TclError:pass
    old_destroy=tk.Toplevel.destroy
    def destroy_top(window):
        cancel_owned(window)
        result=old_destroy(window)
        for timer in window.tk.splitlist(window.tk.call('after','info')):
            try:
                script=window.tk.call('after','info',timer)[0]
                tokens=window.tk.splitlist(script)
                if tokens and re.match(r'^\d+',str(tokens[0])) and not window.tk.call('info','commands',tokens[0]):window.tk.call('after','cancel',timer)
            except tk.TclError:pass
        return result
    tk.Toplevel.destroy=destroy_top
    previous_destroy=cls.destroy
    def final_destroy(window):
        if getattr(window,'_estudio_destroyed',False):return
        thread=getattr(window,'_estudio_bundle_thread',None)
        if thread is not None and thread.is_alive():
            window._estudio_closing=True
            window._estudio_bundle_cancel.set()
            window.set_status('Finishing bundle cancellation…')
            window.after(100,lambda:final_destroy(window))
            return
        window._estudio_destroyed=True
        cancel_owned(window,all_timers=True)
        return previous_destroy(window)
    cls.destroy=final_destroy
    old_top=tk.Toplevel.__init__
    def top(self,*args,**kwargs):
        old_top(self,*args,**kwargs)
        set_icon(self)
        def repaint():
            if not self.winfo_exists():return
            owner=self
            while owner is not None and not hasattr(owner,'estudio_theme_var'):owner=getattr(owner,'master',None)
            apply_theme(self,owner.estudio_theme_var.get() if owner is not None else 'Dark')
        for delay in (0,150,600,1600,4000):self.after(delay,repaint)
        self.bind('<Map>',lambda e:self.after_idle(repaint) if e.widget is self else None,add='+')
    tk.Toplevel.__init__=top
