from __future__ import annotations
import sys
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import faradaiq_studio_core as core
import faradaiq.v239_scilink_edison as old

class Root(tk.Tk):
    def __init__(self, folder: Path):
        super().__init__()
        self.title('FaradaIQ Studio v293')
        self.geometry('800x600+10+10')
        self.output_var = tk.StringVar(value=str(folder))
        self.file_var = tk.StringVar(value=str(folder))
        self.base_name = 'demo'
        self.desc_text = tk.Text(self, height=3)
        self.desc_text.insert('1.0', '10 mM CoCl2 SECCM chronoamperometry line scan.')
    def set_status(self, *args, **kwargs):
        pass

class Browser(tk.Toplevel):
    def __init__(self, gui: Root, folder: Path, data_path: Path):
        super().__init__(gui)
        self.gui = gui
        self.analysis_root = folder
        self.cache_root = folder
        self.title('FaradaIQ Studio v293 — Data Browser')
        self.geometry('900x650+120+80')
        self.tree = ttk.Treeview(self)
        ca = self.tree.insert('', 'end', iid='ca', text='Chronoamperometry (CA)', values=('technique', '170'))
        method = self.tree.insert(ca, 'end', iid='method', text='Method 01 — ChronoPotStat', values=('method', ''))
        point = self.tree.insert(method, 'end', iid='point', text='Point 001', values=('point', ''))
        self.tree.selection_set(point)
        self.tree.pack(fill='both', expand=True)
        self.selected_path = data_path
        self._record_by_iid = {
            point: {'path': str(data_path), 'technique_key': 'CA', 'label': 'Point 001'}
        }
        self.last_plot_paths = [data_path]
    def selected_paths(self, plottable_only=False):
        return [self.selected_path]

with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp)
    data = folder / 'CA_point_001_method_01_cycle_01.csv'
    data.write_text('time_s,current_A\n0,2e-7\n1,-4.8e-8\n2,-1.2e-8\n', encoding='utf-8')
    root = Root(folder)
    browser = Browser(root, folder, data)
    root.update_idletasks(); root.update()
    browser.update_idletasks(); browser.update()

    previous = old.collect_share_candidates
    def forbidden(*args, **kwargs):
        raise AssertionError('Recursive candidate discovery must not run before the review window opens')
    old.collect_share_candidates = forbidden
    try:
        started = time.monotonic()
        review = core._v262_open_scilink_technique_prompt(browser, 'CA charge and transient metrics')
        elapsed = time.monotonic() - started
        review.update_idletasks(); review.update()
        assert elapsed < 1.5, elapsed
        assert review.winfo_exists()
        assert review._faradaiq_scilink_parent is browser
        assert str(review.transient()) == str(browser)
        assert 'v293' in review.title()
        assert 'CA' in review._faradaiq_scilink_selected_data_var.get()
        assert '1 source file' in review._faradaiq_scilink_selected_data_var.get()
        rows = review._faradaiq_scilink_file_rows
        assert len(rows) == 2, [r['item'] for r in rows]
        assert {r['item'].get('generated') for r in rows} == {
            'browser_selection_bundle', 'browser_selection_context_json'
        }
        assert all(bool(r['var'].get()) for r in rows)
        prompt = review._faradaiq_scilink_prompt_box.get('1.0', 'end-1c')
        assert 'chronoamperometry' in prompt.lower()
        assert 'time_s' in prompt and 'current_A' in prompt
        assert review._faradaiq_scilink_start_button.winfo_ismapped()
        assert review._faradaiq_scilink_cancel_button.winfo_ismapped()
        review.destroy()

        # The general Browser Ask SciLink route uses the same immediate, front-of-
        # Browser review but leaves the scientific prompt blank for the operator.
        general = core.show_analysis_review(browser, 'scilink')
        general.update_idletasks(); general.update()
        assert general._faradaiq_scilink_parent is browser
        assert general._faradaiq_scilink_prompt_box.get('1.0', 'end-1c') == ''
        assert len(general._faradaiq_scilink_file_rows) == 2
        general.destroy()
    finally:
        old.collect_share_candidates = previous
    browser.destroy(); root.destroy()
print('v290 SciLink front-of-browser immediate review smoke: PASS')
