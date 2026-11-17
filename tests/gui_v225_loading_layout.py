import tempfile
import time
from pathlib import Path

import faradaiq_studio_core as core

errors = []
core.messagebox.askyesno = lambda *a, **k: False
core.messagebox.showinfo = lambda *a, **k: None
core.messagebox.showwarning = lambda *a, **k: None
core.messagebox.showerror = lambda *a, **k: errors.append((a, k))

orig_load = core.PSSessionCollection.load
orig_summarize = core.PSSessionCollection.summarize
orig_preview = core.SessionReaderGUI._display_structure_preview


def fake_load(self, path):
    time.sleep(0.80)
    self.source_path = path
    self.mode = "single"
    self.entries = [{"entry_name": Path(path).name, "virtual_source": path, "parser": object()}]


def fake_summarize(self):
    return {
        "source_file": self.source_path,
        "mode": "single",
        "n_measurements": 0,
        "technique_counts": {},
        "subfolder_list": [],
    }


core.PSSessionCollection.load = fake_load
core.PSSessionCollection.summarize = fake_summarize
core.SessionReaderGUI._display_structure_preview = lambda self, summary: None

try:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "responsive_load.pssession"
        path.write_text("fixture", encoding="utf-8")

        gui = core.SessionReaderGUI()
        end = time.time() + 0.9
        while time.time() < end:
            gui.update()
            time.sleep(0.01)

        gui.file_var.set(str(path))
        started = time.monotonic()
        gui.load_and_inspect()
        returned_in = time.monotonic() - started
        assert returned_in < 0.20, returned_in

        # Let the new-session banner rebuild and the background worker run.
        end = time.time() + 0.18
        while time.time() < end:
            gui.update()
            time.sleep(0.01)

        assert gui._v225_load_busy is True
        assert "Loading and inspecting:" in gui._faradaiq_load_status_var.get()
        assert "Loading and inspecting:" in gui._v218_status_display_var.get()
        assert gui._v218_persistent_status_frame.winfo_ismapped()

        footer = gui._v218_persistent_status_frame
        footer_children = list(footer.winfo_children())
        progressbars = [w for w in footer_children if w.winfo_class() == "TProgressbar"]
        assert len(progressbars) == 1, [w.winfo_class() for w in footer_children]
        assert progressbars[0] is gui._v218_status_progress
        assert progressbars[0].grid_info().get("column") == 1
        footer_texts = [str(w.cget("text") or "") for w in footer_children if "text" in w.keys()]
        assert "Status tracking:" in footer_texts, footer_texts
        assert "Details" in footer_texts, footer_texts

        frame = gui._v753_session_banner_frame
        texts = {}
        duplicate_status = []

        def walk(widget):
            for child in widget.winfo_children():
                try:
                    text = str(child.cget("text") or "")
                    textvar = str(child.cget("textvariable") or "")
                except Exception:
                    text = textvar = ""
                if text:
                    texts[text] = child
                if text == "Status:" or textvar == str(gui.status_var):
                    duplicate_status.append(child)
                walk(child)

        walk(frame)
        assert not duplicate_status, duplicate_status

        required = [
            "New Session",
            "Save Session",
            "Load Session",
            "Auto Save session",
            "Open tracking folder",
            "Copy ID",
        ]
        for text in required:
            assert text in texts, (text, list(texts))
            assert texts[text].winfo_ismapped(), text

        session_labels = [w for t, w in texts.items() if t.startswith("FaradaIQ working session:") or t.startswith("FaradaIQ launch session:")]
        tracking_labels = [w for t, w in texts.items() if t.startswith("Tracking only:")]
        assert session_labels and session_labels[0].winfo_ismapped()
        assert tracking_labels and tracking_labels[0].winfo_ismapped()
        tracking_info = tracking_labels[0].grid_info()
        assert int(tracking_info.get("row", -1)) == 1, tracking_info
        assert int(tracking_info.get("columnspan", 0)) >= 8, tracking_info
        assert tracking_labels[0].winfo_width() > 500, tracking_labels[0].winfo_width()

        end = time.time() + 3.0
        while time.time() < end and gui._v225_load_busy:
            gui.update()
            time.sleep(0.01)
        for _ in range(25):
            gui.update()
            time.sleep(0.01)

        assert gui._v225_load_busy is False
        assert gui.current_summary
        assert "Loaded" in gui._faradaiq_load_status_var.get(), gui._faradaiq_load_status_var.get()
        assert "Loaded" in gui._v218_status_display_var.get(), gui._v218_status_display_var.get()
        assert not errors, errors
        gui.destroy()
finally:
    core.PSSessionCollection.load = orig_load
    core.PSSessionCollection.summarize = orig_summarize
    core.SessionReaderGUI._display_structure_preview = orig_preview

print("GUI_V225_LOADING_LAYOUT_PASS")
