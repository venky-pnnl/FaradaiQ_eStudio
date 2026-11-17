import tempfile
import time
from pathlib import Path

import faradaiq_studio_core as core

errors = []
core.messagebox.askyesno = lambda *a, **k: False
core.messagebox.showinfo = lambda *a, **k: None
core.messagebox.showwarning = lambda *a, **k: None
core.messagebox.showerror = lambda *a, **k: errors.append((a, k))

core.VersaStudioParser._parse_par_metadata = lambda self, path: {
    'application_version': 'test',
    'actions': [],
}
core.VersaStudioParser._parse_psbd = lambda self, path: {
    'dc_segments': [],
    'eis_sweeps': [],
    'notes': ['GUI companion-resolution smoke fixture'],
}

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    par = root / 'named_scan.par'
    psbd = root / 'named_scan.psbd'
    par.write_text('fixture', encoding='utf-8')
    psbd.write_bytes(b'fixture')

    gui = core.SessionReaderGUI()
    end = time.time() + 0.5
    while time.time() < end:
        gui.update()
        time.sleep(0.01)
    gui.file_var.set(str(par))
    gui.load_and_inspect()
    for _ in range(20):
        gui.update()
        time.sleep(0.01)

    assert not errors, errors
    assert gui.current_summary, gui.current_summary
    assert gui.current_summary['binary_data_file'] == str(psbd)
    ctx = getattr(gui, '_v219_working_session_context', {})
    assert ctx.get('session_name') == 'named_scan'
    assert ctx.get('app_version') == 'v240'
    gui.destroy()

print('GUI_V222_VERSASTUDIO_LOAD_PASS')
