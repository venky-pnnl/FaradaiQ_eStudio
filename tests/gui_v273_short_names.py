"""Native Tk smoke for the default-enabled v290 short-name controls."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("FARADAIQ_SKIP_STARTUP_AI_SELECTOR", "1")


def _walk(widget):
    yield widget
    try:
        for child in widget.winfo_children():
            yield from _walk(child)
    except Exception:
        return


def _text(widget) -> str:
    try:
        return str(widget.cget("text"))
    except Exception:
        return ""


def main() -> int:
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="faradaiq_v273_short_gui_") as tmp:
        os.chdir(tmp)
        try:
            import faradaiq_studio_core as core

            gui = core.SessionReaderGUI()
            gui.update_idletasks()
            main_controls = [
                widget for widget in _walk(gui)
                if _text(widget) == "Use shortened names for generated folders and files"
            ]
            assert len(main_controls) == 1
            assert bool(gui.short_names_var.get()) is True

            window = gui._v233_batch_model.open_window()
            window.update_idletasks()
            job_controls = [
                widget for widget in _walk(window.win)
                if "Use shortened generated names" in _text(widget)
            ]
            assert len(job_controls) == 1
            assert bool(window.short_names_var.get()) is True
            assert window.export_vars["short_names"] is window.short_names_var
            assert bool(job_controls[0].winfo_ismapped()) is True

            window.win.destroy()
            gui.destroy()
        finally:
            os.chdir(previous)
    print("v290 short-name controls smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
