"""Native Tk smoke for the default-enabled v290 portable browser controls."""
from __future__ import annotations
import os, tempfile
from pathlib import Path
os.environ.setdefault("FARADAIQ_SKIP_STARTUP_AI_SELECTOR", "1")

def _walk(widget):
    yield widget
    try:
        for child in widget.winfo_children():
            yield from _walk(child)
    except Exception:
        return

def _text(widget):
    try: return str(widget.cget("text"))
    except Exception: return ""

def main() -> int:
    old = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="faradaiq_v290_portable_gui_") as tmp:
        os.chdir(tmp)
        try:
            import faradaiq_studio_core as core
            from faradaiq import v233_batch_jobs
            assert core.FARADAIQ_PUBLIC_VERSION == "v293"
            gui = core.SessionReaderGUI(); gui.update_idletasks()
            controls = [w for w in _walk(gui) if _text(w) == "Create portable HTML data browser"]
            assert len(controls) == 1 and bool(gui.portable_html_browser_var.get()) is True
            window = gui._v233_batch_model.open_window(); window.update_idletasks()
            job_controls = [w for w in _walk(window.win) if _text(w) == "Create portable HTML data browser"]
            assert len(job_controls) == 1 and bool(window.portable_html_browser_var.get()) is True
            assert v233_batch_jobs.default_export_settings()["portable_html_browser"] is True
            window.win.destroy(); gui.destroy()
        finally:
            os.chdir(old)
    print("v290 portable-browser controls smoke: PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
