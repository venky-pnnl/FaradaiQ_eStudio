"""Native Tk smoke for default-enabled v290 PowerPoint controls."""
from __future__ import annotations
import os
os.environ.setdefault("FARADAIQ_NO_SPLASH", "1")
from faradaiq._legacy import legacy_module


def _walk(root):
    out=[]; stack=[root]
    while stack:
        w=stack.pop(); out.append(w)
        try: stack.extend(w.winfo_children())
        except Exception: pass
    return out


def _text(widget):
    try: return str(widget.cget("text"))
    except Exception: return ""


def main():
    core=legacy_module(); gui=core.SessionReaderGUI(); gui.update_idletasks()
    try:
        controls=[w for w in _walk(gui) if _text(w)=="Create PowerPoint data summary"]
        assert len(controls)==1
        assert bool(gui.powerpoint_summary_var.get()) is True
        assert bool(gui.powerpoint_use_direct_llm_var.get()) is True
        assert str(gui.powerpoint_detail_level_var.get()).lower()=="comprehensive"
        from faradaiq import v233_batch_jobs
        defaults=v233_batch_jobs.default_export_settings()
        assert defaults["powerpoint_summary"] is True
        assert defaults["powerpoint_use_direct_llm"] is True
        assert str(defaults["powerpoint_detail_level"]).lower()=="comprehensive"
    finally:
        gui.destroy()
    print("v290 PowerPoint controls smoke: PASS")

if __name__ == "__main__": main()
