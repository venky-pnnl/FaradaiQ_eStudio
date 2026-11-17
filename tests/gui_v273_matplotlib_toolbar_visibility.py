#!/usr/bin/env python
"""Real Tk smoke check for visible FaradaIQ Matplotlib plot controls."""
from __future__ import annotations

import tkinter as tk

import matplotlib
matplotlib.use("TkAgg", force=True)
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from faradaiq.v290_release_integrity import install


def main() -> int:
    install({})
    root = tk.Tk()
    root.title("FaradaIQ Studio v293 plot toolbar smoke")
    root.geometry("1000x620")

    figure = Figure(figsize=(8.5, 4.6), dpi=100)
    axis = figure.add_subplot(111)
    axis.plot([0, 1, 2], [0, 2, 1])
    canvas = FigureCanvasTkAgg(figure, master=root)
    canvas.get_tk_widget().pack(fill="both", expand=True)
    toolbar = NavigationToolbar2Tk(canvas, root, pack_toolbar=False)
    toolbar.update()
    toolbar.pack(fill="x")

    root.update_idletasks()
    root.update()

    buttons = list(getattr(toolbar, "_faradaiq_visible_buttons", []))
    labels = [str(button.cget("text")) for button in buttons]
    expected = {"Home", "Back", "Forward", "Pan", "Box zoom", "Save figure"}
    assert expected.issubset(set(labels)), labels
    assert toolbar.winfo_ismapped() == 1
    assert toolbar.winfo_height() >= 30
    assert str(toolbar.cget("background")).lower() not in {"black", "#000000", "#000"}
    for button in buttons:
        assert button.winfo_ismapped() == 1, button.cget("text")
        assert str(button.cget("foreground")).lower() not in {"black"} or str(button.cget("background")).lower() not in {"black", "#000000", "#000"}
        assert int(button.winfo_width()) > 20

    # Exercise non-dialog callbacks to ensure visible controls are functional.
    by_label = {str(button.cget("text")): button for button in buttons}
    by_label["Home"].invoke()
    by_label["Pan"].invoke()
    by_label["Pan"].invoke()
    by_label["Box zoom"].invoke()
    by_label["Box zoom"].invoke()

    root.destroy()
    print("v290 matplotlib toolbar smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
