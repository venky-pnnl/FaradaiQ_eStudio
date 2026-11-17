import os, sys, time, tempfile
from pathlib import Path
os.environ.setdefault('MPLBACKEND', 'TkAgg')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import faradaiq_studio_core as core
import faradaiq.v233_batch_jobs as batch

core.messagebox.showinfo = lambda *a, **k: None
core.messagebox.showwarning = lambda *a, **k: None
core.messagebox.showerror = lambda *a, **k: None


def texts(root):
    found=[]
    def walk(w):
        for child in w.winfo_children():
            try:
                value=str(child.cget('text'))
                if value: found.append(value)
            except Exception: pass
            walk(child)
    walk(root)
    return found

with tempfile.TemporaryDirectory() as td:
    root=Path(td)
    files=[]
    for i in range(3):
        p=root/f'job_{i}.dat'; p.write_text('fixture',encoding='utf-8'); files.append(p)
    gui=core.SessionReaderGUI()
    end=time.time()+1.4
    while time.time()<end:
        gui.update(); time.sleep(.01)
    model=gui._v233_batch_model
    jobs=model.add_paths(files,open_window=False)
    win=model.open_window(select_job=jobs[0].job_id)
    end=time.time()+.5
    while time.time()<end:
        gui.update(); time.sleep(.01)
    t=texts(win.win)
    for expected in ('Run all jobs','Run selected jobs','Stop jobs','Live transactions','Apply & save settings','Duplicate job','Remove selected','Clear completed','Load selected in main GUI','Restore session/browser','Open session folder','Run jobs in parallel'):
        assert expected in t,(expected,t)
    assert not hasattr(win,'progress')
    assert win.selected_actions.winfo_ismapped()

    win.parallel_enabled_var.set(True); win.parallel_workers_var.set(2); win._sync_parallel_settings()
    assert model.parallel_enabled and model.parallel_workers==2

    original=batch.run_batch_job
    def quick(job,_core,progress,**kwargs):
        progress('Running','inspection',20.0,'inspection 1/5')
        time.sleep(.08)
        progress('Running','export',70.0,'export 4/5')
        time.sleep(.08)
        job.status='Completed'; job.stage='completed'; job.progress=100.0
        progress('Completed','completed',100.0,'done')
        return job
    batch.run_batch_job=quick
    for job in jobs: job.status='Queued'; job.stage='queued'
    model.run_queue([j.job_id for j in jobs],parallel=True,max_workers=2)
    deadline=time.time()+4
    while model.running and time.time()<deadline:
        gui.update(); time.sleep(.02)
    gui.update_idletasks(); gui.update()
    tx=model.transaction_window
    assert tx is not None and tx.winfo_exists()
    assert len(tx.notebook.tabs())==3
    assert 'parallel' in tx.queue_var.get().lower()
    assert 'job_' in gui._v233_main_job_status_var.get().lower() or 'completed' in gui._v233_main_job_status_var.get().lower()
    batch.run_batch_job=original
    tx.destroy(); win.destroy(); gui.destroy()
print('GUI_V233_PARALLEL_JOBS_PASS')
