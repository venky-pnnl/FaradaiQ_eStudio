import os, sys, time, tempfile
from pathlib import Path
os.environ.setdefault('MPLBACKEND', 'TkAgg')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import faradaiq_studio_core as core
import faradaiq.v232_batch_jobs as batch_module

core.messagebox.showinfo = lambda *a, **k: None
core.messagebox.showwarning = lambda *a, **k: None
core.messagebox.showerror = lambda *a, **k: None


def widget_texts(root):
    texts=[]
    def walk(w):
        for child in w.winfo_children():
            try:
                text=str(child.cget('text'))
                if text: texts.append(text)
            except Exception:
                pass
            walk(child)
    walk(root)
    return texts


def assert_inside(child, parent, margin=4):
    parent.update_idletasks()
    left=parent.winfo_rootx(); top=parent.winfo_rooty()
    right=left+parent.winfo_width(); bottom=top+parent.winfo_height()
    cleft=child.winfo_rootx(); ctop=child.winfo_rooty()
    cright=cleft+child.winfo_width(); cbottom=ctop+child.winfo_height()
    assert cleft >= left-margin, (cleft,left)
    assert ctop >= top-margin, (ctop,top)
    assert cright <= right+margin, (cright,right)
    assert cbottom <= bottom+margin, (cbottom,bottom)


with tempfile.TemporaryDirectory() as td:
    src = Path(td) / 'batch_source.pssession'; src.write_text('fixture', encoding='utf-8')
    gui = core.SessionReaderGUI()
    end = time.time() + 1.9
    while time.time() < end:
        gui.update(); time.sleep(.01)
    assert hasattr(gui, '_v232_batch_model')
    assert hasattr(gui, '_v230_batch_model')
    texts=widget_texts(gui)
    for expected in ('Open jobs', 'Run all jobs', 'Run selected jobs', 'Stop jobs', 'Live transactions'):
        assert expected in texts, (expected, texts)
    assert 'Batch queue' not in texts
    assert 'Add selected to queue' not in texts

    details = gui._v218_status_details_button
    assert details.winfo_ismapped()
    assert_inside(details, gui)
    left, top, work_w, work_h = batch_module._usable_work_area(gui)
    assert gui.winfo_rootx() >= left-8
    assert gui.winfo_rooty() >= top-8
    assert gui.winfo_rootx()+gui.winfo_width() <= left+work_w+8
    assert gui.winfo_rooty()+gui.winfo_height() <= top+work_h+8

    job = gui._v232_batch_model.add_paths([src], open_window=False)[0]
    win = gui._v232_batch_model.open_window(select_job=job.job_id)
    end = time.time() + .5
    while time.time() < end:
        gui.update(); time.sleep(.01)
    texts=widget_texts(win.win)
    for expected in ('Run all jobs', 'Run selected jobs', 'Stop jobs', 'Live transactions', 'Apply & save settings', 'Duplicate job'):
        assert expected in texts, (expected, texts)
    assert 'Add selected to queue' not in texts
    assert 'Remove selected from queue' not in texts
    assert win.selected_actions.winfo_ismapped()
    assert_inside(win.selected_actions, win.win)
    for child in win.selected_actions.winfo_children():
        assert child.winfo_ismapped()
        assert_inside(child, win.win)

    left, top, work_w, work_h = batch_module._usable_work_area(win.win)
    assert win.win.winfo_rootx() >= left-8
    assert win.win.winfo_rooty() >= top-8
    assert win.win.winfo_rootx()+win.win.winfo_width() <= left+work_w+8
    assert win.win.winfo_rooty()+win.win.winfo_height() <= top+work_h+8

    job.status='Running'; job.stage='drt'; job.progress=42.35; job.progress_current=3; job.progress_total=7
    job.elapsed_seconds=15; job.eta_seconds=20; job.message='processed 42%'
    win.refresh(select_job=job.job_id)
    assert '42.4%' in str(win.tree.item(job.job_id, 'values')[-1])
    assert '3/7' in str(win.tree.item(job.job_id, 'values')[-1])

    tx = gui._v232_batch_model.open_transaction_window()
    gui._v232_batch_model.current_job_id = job.job_id
    gui._v232_batch_model._last_run_job_ids = {job.job_id}
    tx.append_log(job.job_id, '[12:00:00] processed 42%')
    tx.refresh(); gui.update_idletasks(); gui.update()
    assert tx.job_percent_var.get() == '42.4%'
    assert 'processed 42%' in tx.log_text.get('1.0', 'end')
    assert 'Live job transactions' in tx.win.title()
    tx.destroy(); gui._v232_batch_model.transaction_window = None

    original_runner = batch_module.run_batch_job
    def quick_runner(active_job, _core, progress, **_kwargs):
        progress('Running', 'export', 50.0, '1/2 live transaction')
        active_job.status = 'Completed'; active_job.stage = 'completed'; active_job.progress = 100.0
        progress('Completed', 'completed', 100.0, 'done')
        return active_job
    batch_module.run_batch_job = quick_runner
    job.status = 'Queued'; job.stage = 'queued'
    gui._v232_batch_model.run_queue([job.job_id])
    deadline = time.time() + 3
    while gui._v232_batch_model.running and time.time() < deadline:
        gui.update(); time.sleep(.02)
    gui.update_idletasks(); gui.update()
    assert gui._v232_batch_model.transaction_window is not None
    assert gui._v232_batch_model.transaction_window.winfo_exists()
    batch_module.run_batch_job = original_runner
    gui._v232_batch_model.transaction_window.destroy()
    win.destroy(); gui.destroy()
print('GUI_V232_BATCH_CONTROLS_PASS')
