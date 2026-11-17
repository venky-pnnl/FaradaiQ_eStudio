from __future__ import annotations

from pathlib import Path

from faradaiq import ui_tabs_v226
from faradaiq import v262_technique_scilink as v273


class _Tree:
    def __init__(self):
        self._selection = ["node-ca"]

    def selection(self):
        return tuple(self._selection)

    def item(self, iid):
        assert iid == "node-ca"
        return {"text": "Chronoamperometry (CA)", "values": ("technique", "170 files")}


class _Browser:
    def __init__(self, path: Path | None = None):
        self.tree = _Tree()
        self.selected_path = str(path) if path else None

    def selected_paths(self, plottable_only=False):
        return []


def test_analyze_panel_uses_tree_selection_when_selected_paths_is_empty(tmp_path):
    browser = _Browser()
    kind, count = ui_tabs_v226._selected_context(browser)
    assert kind == "CA"
    assert count == 1


def test_scilink_review_has_non_overlapping_fixed_action_row_and_selection_summary():
    import inspect

    source = inspect.getsource(v273.show_analysis_review)
    assert 'outer.grid_rowconfigure(4, weight=1)' in source
    assert 'top_actions = ttk.Frame' in source
    assert 'top_actions.grid(row=3' in source
    assert 'tabs.grid(row=4' in source
    assert 'top_start_button = tk.Button' in source
    assert 'top_cancel_button = tk.Button' in source
    assert 'win._faradaiq_scilink_selected_data_var' in source
    assert 'win.after_idle(_raise_action_bar)' in source
    assert 'win.bind("<Control-Return>"' in source


def test_open_scilink_refreshes_browser_selection_summary_before_review():
    import inspect

    source = inspect.getsource(v273.open_scilink_with_technique_prompt)
    assert '_v226_refresh_analysis_context' in source
    assert 'refresh()' in source
