"""Background bundle creation and local SciLink handoff in Tk."""
import queue,threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog,messagebox,ttk
import webbrowser

from .scilink_browser_handoff import (
    DEFAULT_SCILINK_URL,
    SCILINK_STREAMLIT_COMMAND,
    normalize_scilink_url,
    open_local_folder,
    streamlit_analysis_prompt,
    streamlit_handoff_files,
)


def show_streamlit_handoff(gui, receipt=None):
    """Open the most recent handoff or pick one from an earlier session."""
    from . import managed_scilink
    from .scilink_runtime_ui import runtime_status, start_desktop_scilink

    current = getattr(gui, '_estudio_streamlit_handoff_window', None)
    if current is not None and current.winfo_exists():
        if receipt is None:
            current.lift()
            return

    def pick_bundle():
        return filedialog.askopenfilename(
            parent=gui, title='Select a SciLink-ready HDF5 bundle',
            filetypes=[('HDF5 bundle', ('*.h5', '*.hdf5'))],
        )

    selected = receipt or getattr(gui, '_estudio_last_bundle', None) or pick_bundle()
    if not selected:
        return
    try:
        files = streamlit_handoff_files(selected)
        suggested_prompt = streamlit_analysis_prompt(selected)
    except (OSError, ValueError) as exc:
        messagebox.showerror('SciLink Streamlit handoff', str(exc), parent=gui)
        return

    if current is not None and current.winfo_exists():
        current.destroy()
    window = tk.Toplevel(gui)
    gui._estudio_streamlit_handoff_window = window
    window.title('SciLink Streamlit handoff')
    window.transient(gui)
    window.minsize(650, 440)
    panel = ttk.Frame(window, padding=20)
    panel.pack(fill='both', expand=True)
    panel.columnconfigure(1, weight=1)
    panel.rowconfigure(7, weight=1)

    status = tk.StringVar(value='Bundle prepared locally. Review the files and prompt before submitting an analysis.')
    installed = managed_scilink.current()
    launch_url = managed_scilink.running_url() if installed else None
    previous_url = getattr(gui, '_estudio_scilink_streamlit_address', DEFAULT_SCILINK_URL)
    preferred = (launch_url or DEFAULT_SCILINK_URL) if installed else previous_url
    address = tk.StringVar(value=preferred)

    def copy(text, label):
        window.clipboard_clear()
        window.clipboard_append(text)
        status.set(f'{label} copied.')

    def open_scilink():
        try:
            url = normalize_scilink_url(address.get())
            if managed_scilink.current() and url in {launch_url, DEFAULT_SCILINK_URL, managed_scilink.running_url()}:
                def show_progress(message):
                    if window.winfo_exists():
                        status.set(message)

                def completed(result):
                    if window.winfo_exists():
                        address.set(result['url'])
                        status.set(runtime_status(result))

                if start_desktop_scilink(gui, on_progress=show_progress, on_done=completed, on_error=show_progress):
                    status.set('Starting SciLink Streamlit…')
                else:
                    status.set('SciLink installation or startup is already in progress.')
                return
            if not webbrowser.open(url, new=2):
                raise OSError(f'Open {url} in your browser.')
        except (OSError, ValueError) as exc:
            status.set(str(exc))
            return
        gui._estudio_scilink_streamlit_address = url
        status.set('Opened your browser. Start SciLink in its terminal if the page is unavailable.')

    def open_bundle_folder():
        try:
            open_local_folder(files['Data'].parent)
        except OSError as exc:
            status.set(f'Could not open the folder. {exc}')

    def choose_another():
        new_bundle = pick_bundle()
        if new_bundle:
            show_streamlit_handoff(gui, new_bundle)

    ttk.Label(panel, text='Open SciLink and choose Analyze', style='Section.TLabel').grid(
        row=0, column=0, columnspan=3, sticky='w', pady=(0,8))
    ttk.Label(panel, text='Streamlit address').grid(row=1, column=0, sticky='w', padx=(0,8))
    ttk.Entry(panel, textvariable=address).grid(row=1, column=1, sticky='ew')
    ttk.Button(panel, text='Open SciLink', command=open_scilink).grid(row=1, column=2, padx=(8,0), sticky='ew')
    startup = 'The default address opens managed SciLink. Enter another local address to connect to an existing instance.' if installed else f'Install with Update SciLink in eStudio, or start a separate instance using {SCILINK_STREAMLIT_COMMAND}.'
    ttk.Label(panel, text=startup, wraplength=620).grid(
        row=2, column=0, columnspan=3, sticky='w', pady=(2,12))

    for row, (role, label) in enumerate((('Data','Data file(s)'), ('Metadata','Metadata (optional)'),
                                         ('Skill','Upload Skills (optional)')), start=3):
        path = str(files[role])
        ttk.Label(panel, text=label).grid(row=row, column=0, sticky='w', padx=(0,8), pady=3)
        entry = ttk.Entry(panel)
        entry.insert(0, path)
        entry.configure(state='readonly')
        entry.grid(row=row, column=1, sticky='ew')
        ttk.Button(panel, text='Copy path', command=lambda value=path, name=role:copy(value, name+' path')).grid(
            row=row, column=2, padx=(8,0), sticky='ew')

    ttk.Label(panel, text='Suggested scope · Review and edit before sending').grid(
        row=6, column=0, columnspan=3, sticky='w', pady=(12,3))
    style = ttk.Style(window)
    text_color = style.lookup('TEntry', 'foreground')
    prompt = tk.Text(panel, height=5, wrap='word', padx=8, pady=6,
                     background=style.lookup('TEntry', 'fieldbackground') or 'white',
                     foreground=text_color or 'black', insertbackground=text_color or 'black')
    prompt.insert('1.0', suggested_prompt)
    prompt.grid(row=7, column=0, columnspan=3, sticky='nsew')
    actions = ttk.Frame(panel)
    actions.grid(row=8, column=0, columnspan=3, sticky='ew', pady=(10,4))
    ttk.Button(actions, text='Copy prompt', command=lambda:copy(prompt.get('1.0','end-1c'), 'Prompt')).pack(side='left')
    ttk.Button(actions, text='Open bundle folder', command=open_bundle_folder).pack(side='left', padx=5)
    ttk.Button(actions, text='Choose another bundle', command=choose_another).pack(side='left', padx=5)
    ttk.Button(actions, text='Close', command=window.destroy).pack(side='right')
    ttk.Label(panel, textvariable=status, wraplength=620, style='Muted.TLabel').grid(
        row=9, column=0, columnspan=3, sticky='w', pady=(4,0))

def start_bundle(gui,core):
    if getattr(gui,'_estudio_bundle_busy',False):
        gui.set_status('A SciLink-ready bundle is already being prepared.');return
    resolver=core.get('_v64_analysis_root_for_browser')
    try:root=resolver(gui) if resolver else None
    except Exception:root=None
    if isinstance(root,(tuple,list)):root=root[0] if root else None
    if not root or not Path(str(root)).is_dir():root=filedialog.askdirectory(parent=gui,title='Choose exported analysis folder')
    if not root:return
    try:metadata=core['_v130_gui_metadata_snapshot'](gui)
    except Exception:metadata={}
    if not isinstance(metadata,dict):metadata={'context':str(metadata)}
    try:
        context=getattr(gui,'_v219_working_session_context',None) or core['_v753_launch_context']()
        metadata.update(session_id=context.get('session_id',''),session_name=context.get('session_name',''))
    except Exception:pass
    gui._estudio_bundle_busy=True
    gui._estudio_bundle_cancel=cancel=threading.Event();events=queue.Queue()
    gui.set_status('Preparing SciLink-ready HDF5 bundle…')
    def worker():
        from .scilink_bundle import make_scilink_ready_bundle
        try:events.put(('done',make_scilink_ready_bundle(root,metadata=metadata,progress_callback=lambda msg:events.put(('progress',msg)),cancel_event=cancel)))
        except Exception as exc:events.put(('error',exc))
    def poll():
        try:
            while True:
                kind,value=events.get_nowait()
                if kind=='progress':gui.set_status(str(value));continue
                gui._estudio_bundle_busy=False
                if kind=='done':
                    gui._estudio_last_bundle=value;gui.set_status('SciLink-ready HDF5 bundle created.')
                    if not getattr(gui,'_estudio_closing',False):show_streamlit_handoff(gui,value)
                else:
                    gui.set_status(str(value))
                    if not isinstance(value,InterruptedError) and not getattr(gui,'_estudio_closing',False):messagebox.showerror('Bundle creation',str(value),parent=gui)
                return
        except queue.Empty:pass
        gui.after(120,poll)
    gui._estudio_bundle_thread=threading.Thread(target=worker,name='estudio-hdf5-bundle',daemon=False)
    gui._estudio_bundle_thread.start();gui.after(120,poll)
