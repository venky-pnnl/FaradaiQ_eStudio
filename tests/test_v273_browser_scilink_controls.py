from __future__ import annotations

import inspect
from pathlib import Path

from faradaiq import ui_tabs_v226
from faradaiq import v255_browser_color
from faradaiq import v262_technique_scilink as v273
import faradaiq_scilink_cli_job as runner


class _Tree:
    def __init__(self):
        self._selection = ("point",)
        self._items = {
            "point": {"text": "Point 001", "values": ("point", "")},
            "method": {"text": "Method 01 — ChronoPotStat", "values": ("method", "")},
            "ca": {"text": "Chronoamperometry (CA)", "values": ("technique", "170")},
        }
        self._parents = {"point": "method", "method": "ca", "ca": ""}

    def selection(self):
        return self._selection

    def item(self, iid):
        return self._items[iid]

    def parent(self, iid):
        return self._parents.get(iid, "")

    def get_children(self, iid):
        return ()


class _Browser:
    def __init__(self, path: Path | None = None):
        self.tree = _Tree()
        self.selected_path = path
        self._record_by_iid = {
            "point": {
                "path": str(path) if path else "CA_point_001_method_01_cycle_01.csv",
                "technique_key": "CA",
            }
        }

    def selected_paths(self, plottable_only=False):
        return [self.selected_path] if self.selected_path else []


def test_selection_context_uses_parent_technique_for_point_rows(tmp_path):
    p = tmp_path / "CA_point_001_method_01_cycle_01.csv"
    p.write_text("time_s,current_A\n0,1e-6\n", encoding="utf-8")
    kind, count = ui_tabs_v226._selected_context(_Browser(p))
    assert "CA" in kind
    assert count == 1


def test_browser_selection_resolver_accepts_h5_record_rows(tmp_path):
    p = tmp_path / "CA_point_001_method_01_cycle_01.csv"
    p.write_text("time_s,current_A\n0,1e-6\n", encoding="utf-8")
    assert v273._browser_selection_paths(_Browser(p)) == [p.resolve()]


def test_active_tree_handler_resynchronizes_analyze_panel():
    source = inspect.getsource(v255_browser_color._install_browser_selection)
    assert "_v226_refresh_analysis_context" in source
    assert "browser.after_idle(sync_analysis_context)" in source
    assert "browser.after(60, sync_analysis_context)" in source


def test_analyze_handoff_buttons_are_clickable_and_not_state_gated():
    source = inspect.getsource(ui_tabs_v226._build_analyze_panel)
    assert 'button.configure(state="normal", takefocus=True)' in source
    assert 'state="normal" if eis_ok else "disabled"' not in source
    assert 'state="normal" if ca_cp_ok else "disabled"' not in source


def test_review_mode_combobox_supports_all_modes_and_is_not_locked():
    source = inspect.getsource(v273.show_analysis_review)
    assert '("Co-pilot", "Auto-pilot", "Autonomous")' in source
    assert 'mode_combo.configure(state="readonly")' in source
    assert 'mode_combo.configure(state="disabled")' not in source
    assert 'mode_var.set("Co-pilot")' not in source
    assert 'win._faradaiq_scilink_mode_combo' in source


def test_cli_preserves_mode_independent_of_question_gate():
    assert runner._effective_scilink_mode({"scilink_mode": "Co-pilot", "scilink_wait_for_user_questions": True}) == "co-pilot"
    assert runner._effective_scilink_mode({"scilink_mode": "Auto-pilot", "scilink_wait_for_user_questions": True}) == "autopilot"
    assert runner._effective_scilink_mode({"scilink_mode": "Autonomous", "scilink_wait_for_user_questions": True}) == "autonomous"
    assert runner._effective_scilink_mode({"scilink_mode": "Autonomous", "scilink_wait_for_user_questions": False}) == "autonomous"
