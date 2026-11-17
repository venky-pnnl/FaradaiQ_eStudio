import os, sys, time, tempfile
from pathlib import Path
os.environ.setdefault('MPLBACKEND', 'TkAgg')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import faradaiq_studio_core as core

core.messagebox.showinfo = lambda *a, **k: None
core.messagebox.showwarning = lambda *a, **k: None
core.messagebox.showerror = lambda *a, **k: None

with tempfile.TemporaryDirectory() as td:
    src = Path(td) / 'batch_source.pssession'; src.write_text('fixture', encoding='utf-8')
    gui = core.SessionReaderGUI()
    end = time.time() + 0.8
    while time.time() < end:
        gui.update(); time.sleep(.01)
    assert hasattr(gui, '_v229_batch_model')
    buttons=[]
    def walk(w):
        for c in w.winfo_children():
            try:
                if str(c.cget('text')) == 'Open jobs': buttons.append(c)
            except Exception: pass
            walk(c)
    walk(gui)
    assert len(buttons) == 1, len(buttons)
    jobs = gui._v229_batch_model.add_paths([src], open_window=False)
    assert len(jobs) == 1
    win = gui._v229_batch_model.open_window(select_job=jobs[0].job_id)
    gui.update_idletasks(); gui.update()
    assert win.tree.exists(jobs[0].job_id)
    assert win.name_var.get() == 'batch_source'
    win.name_var.set('Custom experiment')
    win.output_var.set(str(Path(td) / 'custom_output'))
    saved = win.apply_settings(show_message=False)
    assert saved.experiment_name == 'Custom experiment'
    assert Path(saved.session_json).exists()
    assert saved.export_settings['resume_completed_stages'] is True
    # Normal Load Session restoration also recreates the batch job.
    payload = gui._v229_batch_model._main_session_payload(saved, include_browser=False)
    gui._v229_batch_model.jobs.clear()
    core._v177_apply_full_session(gui, payload, open_browsers=False)
    assert gui._v229_batch_model.find(saved.job_id) is not None
    # Native drag/drop is installed when tkinterdnd2 is available.
    assert hasattr(gui, '_v229_drop_targets') or hasattr(gui, '_v229_drop_error')
    win.destroy(); gui.destroy()
print('GUI_V229_BATCH_QUEUE_PASS')
