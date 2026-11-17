"""Background bundle creation; all Tk interaction stays on the GUI thread."""
import queue,threading
from pathlib import Path
from tkinter import filedialog,messagebox

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
                    if not getattr(gui,'_estudio_closing',False):messagebox.showinfo('SciLink-ready bundle',f"Created:\n{value['bundle_dir']}\n\nReview metadata and prompt before submission.",parent=gui)
                else:
                    gui.set_status(str(value))
                    if not isinstance(value,InterruptedError) and not getattr(gui,'_estudio_closing',False):messagebox.showerror('Bundle creation',str(value),parent=gui)
                return
        except queue.Empty:pass
        gui.after(120,poll)
    gui._estudio_bundle_thread=threading.Thread(target=worker,name='estudio-hdf5-bundle',daemon=False)
    gui._estudio_bundle_thread.start();gui.after(120,poll)
