from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from faradaiq.ui_tabs_v226 import _build_views_panel


class BrowserStub:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.plot_views = [
            {
                "name": "CA current vs time",
                "mode": "CA: current vs time",
                "paths": [f"curve_{i:03d}.csv" for i in range(170)],
            }
        ]
        self.load_count = 0
        self.logs: list[str] = []

    def after(self, delay: int, callback):
        return self.root.after(delay, callback)

    def load_selected_plot_view(self):
        self.load_count += 1

    def log(self, message: str):
        self.logs.append(message)

    def rename_selected_view(self):
        return None

    def clear_selected_view(self):
        return None

    def clear_plot_view_list(self):
        return None

    def save_plotviews_dialog(self):
        return None

    def load_plotviews_dialog(self):
        return None

    def save_browser_session_dialog(self):
        return None

    def load_browser_session_dialog(self):
        return None


def main() -> int:
    root = tk.Tk()
    root.geometry("700x600")
    parent = ttk.Frame(root)
    parent.pack(fill="both", expand=True)
    browser = BrowserStub(root)
    _build_views_panel(browser, parent, ttk, tk)
    root.update_idletasks()

    browser.plot_view_list.selection_set(0)
    browser.plot_view_list.event_generate("<<ListboxSelect>>", when="tail")
    root.update()
    assert browser.load_count == 0, "single-click selection must not load the view"
    assert "170 source file(s)" in browser._v273_view_status_var.get()

    browser._v273_open_view_btn.invoke()
    deadline = time.time() + 1.0
    while browser.load_count == 0 and time.time() < deadline:
        root.update()
        time.sleep(0.01)
    assert browser.load_count == 1, "Open selected view must load exactly once"
    assert not browser._v273_view_open_busy
    root.destroy()
    print("saved-view responsiveness smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
