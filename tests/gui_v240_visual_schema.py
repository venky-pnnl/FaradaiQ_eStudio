from __future__ import annotations

import json
import tempfile
import time
import tkinter as tk
from pathlib import Path

import faradaiq_studio_core as core
import faradaiq.v240_visual_schema_catalog as release

root = tk.Tk()
root.withdraw()
root._faradaiq_theme_mode = "dark"
root.base_name = "v240_schema_smoke"
root.set_status = lambda *_args, **_kwargs: None

with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp) / ".browser_cache_from_h5"
    csv = folder / "CV_LSV" / "point_001" / "cycle_03" / "CV_point_001_cycle_03.csv"
    csv.parent.mkdir(parents=True)
    csv.write_text("Potential_V,Current_A\n0,0\n", encoding="utf-8")
    h5 = Path(tmp) / "v240_schema_smoke_faradaiq_bundle.h5"
    h5.write_bytes(b"test-h5")
    manifest = {"techniques_detected": ["CV / LSV"], "technique_counts": {"CV / LSV": 1}}
    (folder / "_bundle_source.json").write_text(json.dumps({
        "h5_path": str(h5), "h5_mtime": h5.stat().st_mtime,
        "n_tables": 1, "extract_policy_version": "v216", "manifest": manifest,
    }), encoding="utf-8")
    core._v240_build_catalog_cached(folder, base_name="v240_schema_smoke")

    browser = core.FaradaIQH5Browser(
        root,
        cache_info={"cache_root": str(folder), "manifest": manifest, "h5_path": str(h5)},
        base_name="v240_schema_smoke",
    )
    browser.geometry("1400x820+0+0")
    browser.deiconify()
    deadline = time.time() + 3.5
    while time.time() < deadline:
        browser.update_idletasks(); browser.update(); time.sleep(0.03)

    notebook = browser._v226_center_notebook
    labels = [str(notebook.tab(tab, "text")) for tab in notebook.tabs()]
    assert "Visual Data Schema" in labels
    for tab in notebook.tabs():
        if str(notebook.tab(tab, "text")) == "Visual Data Schema":
            notebook.select(tab)
            break
    deadline = time.time() + 1.8
    while time.time() < deadline:
        browser.update_idletasks(); browser.update(); time.sleep(0.03)

    canvas = browser._v240_schema_canvas
    assert browser.title().startswith("FaradaIQ Studio v273")
    assert getattr(browser, "_v240_visual_schema_added", False)
    assert canvas.winfo_width() > 400
    assert canvas.winfo_height() > 250
    assert len(canvas.find_all()) > 50
    assert len(browser._v240_schema_blocks) == 15
    assert 0.88 <= float(browser._v240_schema_scale) <= 0.92
    scroll = [float(value) for value in str(canvas.cget("scrollregion")).split()]
    assert scroll[2] > canvas.winfo_width()
    assert scroll[3] > canvas.winfo_height()
    release._fit_schema(browser)
    deadline = time.time() + 0.5
    while time.time() < deadline:
        browser.update_idletasks(); browser.update(); time.sleep(0.02)
    fit_scroll = [float(value) for value in str(canvas.cget("scrollregion")).split()]
    assert fit_scroll[2] <= canvas.winfo_width() + 8
    assert fit_scroll[3] <= canvas.winfo_height() + 15
    browser.destroy()

root.destroy()
print("v240 visual block schema GUI smoke: PASS")
