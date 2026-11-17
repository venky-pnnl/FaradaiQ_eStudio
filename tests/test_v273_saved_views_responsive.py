from __future__ import annotations

from pathlib import Path

from faradaiq import ui_tabs_v226 as views_ui

ROOT = Path(__file__).resolve().parents[1]


class _FakeListbox:
    def __init__(self, selected=()):
        self._selected = tuple(selected)

    def curselection(self):
        return self._selected


class _FakeBrowser:
    def __init__(self, selected=(0,)):
        self.plot_view_list = _FakeListbox(selected)
        self.plot_views = [
            {
                "name": "CA current vs time",
                "mode": "CA: current vs time",
                "paths": ["a.csv", "b.csv", "c.csv"],
            }
        ]


def test_saved_view_single_click_is_selection_only():
    text = (ROOT / "faradaiq" / "ui_tabs_v226.py").read_text(encoding="utf-8")
    assert 'bind("<<ListboxSelect>>", select_only)' in text
    assert 'bind("<Double-Button-1>", open_selected)' in text
    assert 'bind("<Return>", open_selected)' in text
    assert 'text="Open selected view"' in text
    assert 'bind("<<ListboxSelect>>", lambda _e: browser.load_selected_plot_view())' not in text


def test_saved_view_summary_is_metadata_only():
    browser = _FakeBrowser()
    idx, summary = views_ui._selected_view_summary(browser)
    assert idx == 0
    assert "CA current vs time" in summary
    assert "3 source file(s)" in summary
    assert "Double-click" in summary


def test_saved_view_no_selection_explains_non_loading_click():
    browser = _FakeBrowser(selected=())
    idx, summary = views_ui._selected_view_summary(browser)
    assert idx is None
    assert "Single-click only selects" in summary
