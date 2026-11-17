from __future__ import annotations

import sys
import tempfile
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import faradaiq_studio_core as core
from faradaiq import v239_scilink_edison as old
from faradaiq import v250_reliability as reliability
from faradaiq import v262_technique_scilink as review_mod


class Root(tk.Tk):
    def __init__(self, folder: Path):
        super().__init__()
        self.geometry("420x260+20+20")
        self.deiconify()
        self.output_var = tk.StringVar(value=str(folder))
        self.file_var = tk.StringVar(value=str(folder))
        self.base_name = "review_actions"
        self.gui = self

    def set_status(self, *args, **kwargs):
        pass


def verify_uncovered(review):
    review.deiconify()
    review.update_idletasks()
    review.update()
    start = review._faradaiq_scilink_start_button
    cancel = review._faradaiq_scilink_cancel_button
    bar = review._faradaiq_scilink_action_bar
    assert start.winfo_viewable(), "Start SciLink is not viewable"
    assert cancel.winfo_viewable(), "Cancel is not viewable"
    assert bar.winfo_viewable(), "Action bar is not viewable"
    assert start.cget("state") == "normal"
    assert cancel.cget("state") == "normal"
    sx = start.winfo_rootx() + max(1, start.winfo_width() // 2)
    sy = start.winfo_rooty() + max(1, start.winfo_height() // 2)
    cx = cancel.winfo_rootx() + max(1, cancel.winfo_width() // 2)
    cy = cancel.winfo_rooty() + max(1, cancel.winfo_height() // 2)
    assert review.winfo_containing(sx, sy) == start, "Start button is covered by another widget"
    assert review.winfo_containing(cx, cy) == cancel, "Cancel button is covered by another widget"
    return start, cancel


with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp)
    data = folder / "CA_point_001.csv"
    data.write_text(
        "time_s,current_A,potential_V\n0,-1e-6,-0.9\n1,-5e-7,-0.9\n2,-2e-7,-0.9\n",
        encoding="utf-8",
    )
    root = Root(folder)

    original_paths = review_mod._browser_selection_paths
    original_names = review_mod._selected_record_names
    original_desc = review_mod._experiment_description
    original_collect = old.collect_share_candidates
    original_write = review_mod.write_scilink_job
    original_launch = reliability._launch_scilink_transaction
    launched = []
    try:
        review_mod._browser_selection_paths = lambda _source: [data]
        review_mod._selected_record_names = lambda _source: ["Chronoamperometry (CA)", "Point 001"]
        review_mod._experiment_description = lambda _source: "10 mM CoCl2 CA test"
        old.collect_share_candidates = lambda _source, _cfg: []
        captured_jobs = []
        def fake_write(*args, **kwargs):
            captured_jobs.append(kwargs)
            return {
                "manifest": {"selected_data_paths": [str(data)]},
                "job_dir": str(folder / "job"),
            }
        review_mod.write_scilink_job = fake_write
        reliability._launch_scilink_transaction = lambda source, job, cfg: launched.append((source, job, cfg))

        review = core.show_analysis_review(root, "scilink", prompt_override="Analyze charge")
        start, _cancel = verify_uncovered(review)
        summary = review._faradaiq_scilink_selected_data_var.get()
        assert "Selected data: CA" in summary, summary
        assert "1 source file" in summary, summary
        assert "bundle + compact JSON checked" in summary, summary

        # The question gate must not disable or overwrite the selected mode.
        mode_combo = review._faradaiq_scilink_mode_combo
        mode_var = review._faradaiq_scilink_mode_var
        assert str(mode_combo.cget("state")) == "readonly"
        mode_combo.set("Auto-pilot")
        mode_combo.event_generate("<<ComboboxSelected>>")
        review.update_idletasks(); review.update()
        assert mode_var.get() == "Auto-pilot"
        assert str(mode_combo.cget("state")) == "readonly"

        # Prove the Start button command is connected and can be activated.
        start.invoke()
        root.update_idletasks()
        root.update()
        assert launched, "Start SciLink did not invoke the transaction launcher"
        assert captured_jobs and captured_jobs[0]["mode"] == "Auto-pilot"
        assert captured_jobs[0]["wait_for_user_questions"] is True
        assert not bool(review.winfo_exists())

        # Open a second review and prove Cancel is also active.
        review2 = core.show_analysis_review(root, "scilink", prompt_override="Analyze charge")
        _start2, cancel2 = verify_uncovered(review2)
        cancel2.invoke()
        root.update_idletasks()
        root.update()
        assert not bool(review2.winfo_exists())
    finally:
        review_mod._browser_selection_paths = original_paths
        review_mod._selected_record_names = original_names
        review_mod._experiment_description = original_desc
        old.collect_share_candidates = original_collect
        review_mod.write_scilink_job = original_write
        reliability._launch_scilink_transaction = original_launch
        try:
            root.destroy()
        except Exception:
            pass

print("v290 SciLink review action-bar and selection smoke: PASS")
