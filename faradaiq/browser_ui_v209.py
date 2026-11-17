"""Unified browser tree and technique-aware plotting for FaradaIQ Studio v273.

Installed at runtime by faradaiq_studio_core. The canonical bundle catalog is
built first, then one deterministic tree is rendered for every importer.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .browser_catalog_v209 import PLOT_MODES, build_catalog, record_label, technique_spec

_TABLE_EXTS = {".csv", ".tsv", ".txt", ".asc", ".xlsx", ".xls"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".mpeg", ".mpg", ".m4v", ".webm"}
_MEDIA_EXTS = _IMAGE_EXTS | _VIDEO_EXTS
_INSTALLED_CORE: Dict[str, Any] = {}


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _ival(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _widget_walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from _widget_walk(child)


def _find_mode_combo(browser, ttk):
    target = str(browser.plot_mode_var)
    for w in _widget_walk(browser):
        try:
            if isinstance(w, ttk.Combobox) and str(w.cget("textvariable")) == target:
                return w
        except Exception:
            pass
    return None


def _method_point_leaf_label(rec: Mapping[str, Any], include_method: bool = False) -> str:
    tech = str(rec.get("technique_key") or "")
    bits: List[str] = []
    if rec.get("instrument") == "HEKA" and _ival(rec.get("group_index")):
        bits.append(f"G{_ival(rec.get('group_index')):02d}")
    if include_method:
        bits.append(f"M{_ival(rec.get('method_index'), 1):02d}")
    if _ival(rec.get("line")):
        bits.append(f"L{_ival(rec.get('line')):03d}")
    if _ival(rec.get("cycle")):
        bits.append(f"Cycle {_ival(rec.get('cycle')):02d}")
    elif _ival(rec.get("sweep")):
        bits.append(f"Sweep {_ival(rec.get('sweep')):03d}")
    if tech == "EIS":
        bits.append("Nyquist / Bode / phase")
    elif not bits:
        bits.append(Path(str(rec.get("path") or "table")).stem)
    return " · ".join(bits)


def _path_key(path: Any) -> str:
    try:
        return str(Path(path).resolve()).lower()
    except Exception:
        return str(path).lower()


def _tree_records(browser, iid: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def walk(node: str):
        rec = getattr(browser, "_record_by_iid", {}).get(node)
        if rec is None:
            legacy_path = getattr(browser, "_path_by_iid", {}).get(node)
            if legacy_path is not None:
                rec = {"path": str(legacy_path), "technique_key": "OTHER", "plot_mode": "Auto"}
        if rec:
            key = _path_key(rec.get("path"))
            if key not in seen:
                out.append(rec)
                seen.add(key)
        for child in browser.tree.get_children(node):
            walk(child)

    walk(iid)
    return out


def _mode_for_selection(browser, iids: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> str:
    for iid in iids:
        meta = getattr(browser, "_node_meta", {}).get(iid, {})
        if meta.get("view_mode"):
            return str(meta["view_mode"])
    techs = {str(r.get("technique_key") or "") for r in rows if r}
    if len(techs) == 1:
        return str(technique_spec(next(iter(techs))).get("mode") or "Auto")
    return "Auto"


def _set_mode_defaults(browser, mode: str) -> None:
    try:
        browser.plot_mode_var.set(mode)
    except Exception:
        pass
    ml = mode.lower()
    try:
        if any(x in ml for x in ("bode", "drt")):
            browser.plot_xscale_var.set("Log")
        elif "nyquist" in ml or "approach" in ml or "scan" in ml or "chrono" in ml or "potential vs time" in ml or "current vs time" in ml:
            browser.plot_xscale_var.set("Linear")
    except Exception:
        pass
    try:
        if "bode magnitude" in ml:
            browser.plot_yscale_var.set("Log")
        elif "bode phase" in ml or "nyquist" in ml:
            browser.plot_yscale_var.set("Linear")
    except Exception:
        pass
    try:
        if mode == "CV / LSV":
            browser.cv_view_var.set("I-V")
        elif "current vs time" in ml:
            browser.cv_view_var.set("I-t")
        elif "potential vs time" in ml:
            browser.cv_view_var.set("E-t")
    except Exception:
        pass


def _metadata_text(rows: Sequence[Mapping[str, Any]], mode: str) -> str:
    if not rows:
        return "No plottable table or media item is selected."
    techs = Counter(str(r.get("technique_label") or r.get("technique_key") or "Unknown") for r in rows)
    methods = sorted({(_ival(r.get("method_index"), 1), str(r.get("method_title") or "")) for r in rows})
    points = sorted({_ival(r.get("point")) for r in rows if _ival(r.get("point")) > 0})
    lines = [
        f"Selected items: {len(rows)}",
        "Technique: " + ", ".join(f"{k} ({v})" for k, v in techs.items()),
        f"Plot mode: {mode}",
        "Methods: " + ", ".join(f"M{i:02d}" + (f" {t}" if t else "") for i, t in methods[:12]),
        "Points: " + (", ".join(str(x) for x in points[:30]) if points else "not assigned"),
    ]
    if len(rows) == 1:
        r = rows[0]
        lines.extend([
            f"Source: {r.get('source_file') or Path(str(r.get('path'))).name}",
            f"Group: {_ival(r.get('group_index')):02d} {r.get('group_title') or ''}".rstrip(),
            f"Series: {_ival(r.get('series_index')):03d} {r.get('series_title') or ''}".rstrip(),
            f"Sweep/cycle: {_ival(r.get('sweep'))}/{_ival(r.get('cycle'))}",
            f"Columns: {', '.join(str(x) for x in r.get('columns') or [])}",
            f"Confidence: {r.get('confidence') if r.get('confidence') is not None else 'not reported'}",
        ])
        evidence = [str(x) for x in r.get("evidence") or []]
        if evidence:
            lines.append("Evidence:")
            lines.extend(f"  • {x}" for x in evidence[:20])
        default_plot = str(r.get("default_plot") or "").strip()
        if default_plot:
            lines.append(f"Importer default: {default_plot}")
    return "\n".join(lines)


def _on_tree_select(browser, _event=None) -> None:
    try:
        iids = list(browser.tree.selection())
        rows: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for iid in iids:
            for rec in _tree_records(browser, iid):
                key = _path_key(rec.get("path"))
                if key not in seen:
                    rows.append(rec); seen.add(key)
        mode = _mode_for_selection(browser, iids, rows)
        _set_mode_defaults(browser, mode)
        try:
            browser.qa_text.configure(state="normal")
            browser.qa_text.delete("1.0", "end")
            browser.qa_text.insert("1.0", _metadata_text(rows, mode))
            browser.qa_text.configure(state="disabled")
        except Exception:
            pass
        if len(rows) == 1:
            browser.selected_path = Path(rows[0]["path"])
            if browser.selected_path.suffix.lower() in _MEDIA_EXTS:
                browser.selected_df = None
                _display_media(browser, [Path(r["path"]) for r in rows], 0)
            else:
                try:
                    reader = browser._v209_core.get("_v138_csv_read")
                    browser.selected_df = reader(browser.selected_path, max_rows=2000) if callable(reader) else None
                except Exception:
                    browser.selected_df = None
        try:
            if rows:
                browser.log(f"Selection: {len(rows)} table(s), mode={mode}, technique(s)={sorted({r.get('technique_key') for r in rows})}")
        except Exception:
            pass
    except Exception as exc:
        try:
            browser.log(f"Selection metadata update failed: {exc}")
        except Exception:
            pass



class _LazyMediaDecoder:
    """Decode only the frame being displayed and keep a tiny LRU cache."""
    def __init__(self, path: Path, max_dim: int = 1280):
        self.path = Path(path)
        self.max_dim = int(max_dim)
        self.suffix = self.path.suffix.lower()
        self.kind = "image"
        self.frame_count = 1
        self.default_duration = 120
        self._cache: Dict[int, np.ndarray] = {}
        self._gif = None
        self._cap = None
        self._imageio = None
        if self.suffix == ".gif":
            from PIL import Image
            self._gif = Image.open(self.path)
            self.frame_count = max(1, int(getattr(self._gif, "n_frames", 1) or 1))
            self.default_duration = max(40, int(self._gif.info.get("duration", 120) or 120))
            self.kind = "animated GIF" if self.frame_count > 1 else "image"
        elif self.suffix in _VIDEO_EXTS:
            self.kind = "video"
            try:
                import cv2
                self._cap = cv2.VideoCapture(str(self.path))
                count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)
                self.frame_count = max(1, count)
                self.default_duration = max(25, int(1000.0 / fps)) if fps > 0 else 67
            except Exception:
                self._cap = None
                try:
                    import imageio.v3 as iio
                    self._imageio = iio
                except Exception:
                    self._imageio = None
        else:
            self.kind = "image"

    def _downscale(self, arr: np.ndarray) -> np.ndarray:
        arr = np.asarray(arr)
        if arr.ndim == 2:
            arr = np.repeat(arr[:, :, None], 3, axis=2)
        if arr.ndim == 3 and arr.shape[2] > 3:
            arr = arr[:, :, :3]
        h, w = arr.shape[:2]
        if max(h, w) <= self.max_dim:
            return arr.astype(np.uint8, copy=False)
        from PIL import Image
        im = Image.fromarray(arr.astype(np.uint8, copy=False))
        im.thumbnail((self.max_dim, self.max_dim))
        return np.asarray(im.convert("RGB"))

    def frame(self, index: int) -> Tuple[Optional[np.ndarray], int]:
        idx = max(0, int(index)) % max(1, self.frame_count)
        if idx in self._cache:
            return self._cache[idx], self.default_duration
        arr = None; duration = self.default_duration
        try:
            if self._gif is not None:
                self._gif.seek(idx)
                duration = max(40, int(self._gif.info.get("duration", self.default_duration) or self.default_duration))
                arr = np.asarray(self._gif.convert("RGB"))
            elif self._cap is not None:
                import cv2
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = self._cap.read()
                if ok:
                    arr = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            elif self._imageio is not None and self.kind == "video":
                arr = np.asarray(self._imageio.imread(str(self.path), index=idx))
            else:
                from PIL import Image
                with Image.open(self.path) as im:
                    arr = np.asarray(im.convert("RGB"))
        except Exception:
            arr = None
        if arr is not None:
            arr = self._downscale(arr)
            self._cache[idx] = arr
            # Keep only three decoded frames to avoid large-GIF/video memory spikes.
            while len(self._cache) > 3:
                self._cache.pop(next(iter(self._cache)))
        return arr, duration

    def close(self):
        try:
            if self._gif is not None: self._gif.close()
        except Exception: pass
        try:
            if self._cap is not None: self._cap.release()
        except Exception: pass
        self._cache.clear()


def _close_media_decoder(browser):
    dec = getattr(browser, "_v216_media_decoder", None)
    if dec is not None:
        try: dec.close()
        except Exception: pass
    browser._v216_media_decoder = None


def _load_media_frame(path: Path):
    """Compatibility helper used by tests/exporters; the interactive browser stays lazy."""
    dec = _LazyMediaDecoder(path)
    frames = []
    durations = []
    # Preserve the historical helper contract for modest media while the UI
    # itself uses _LazyMediaDecoder directly and never preloads all frames.
    limit = min(dec.frame_count, 500 if dec.kind == "animated GIF" else 1)
    for idx in range(limit):
        frame, duration = dec.frame(idx)
        if frame is None:
            break
        frames.append(frame); durations.append(duration)
    kind = dec.kind
    dec.close()
    return frames, durations, kind


def _cancel_media_animation(browser):
    token = getattr(browser, "_v211_media_after", None)
    if token:
        try: browser.after_cancel(token)
        except Exception: pass
    browser._v211_media_after = None


def _draw_media_frame(browser):
    dec = getattr(browser, "_v216_media_decoder", None)
    if dec is None: return
    idx = int(getattr(browser, "_v211_media_frame", 0)) % max(1, dec.frame_count)
    frame, duration = dec.frame(idx)
    browser._v216_media_current_duration = duration
    if frame is None: return
    try:
        ensure = getattr(browser, "_ensure_plot_canvas", None)
        if callable(ensure): ensure()
    except Exception: pass
    panel = getattr(browser, "_v206_pub_panel", None) or getattr(browser, "_v200_pub_panel", None) or getattr(browser, "publication_plot_panel", None) or getattr(browser, "plot_panel", None)
    if panel is None: return
    try: panel._new_figure()
    except Exception:
        try: panel.ax.clear()
        except Exception: return
    panel.ax.imshow(frame); panel.ax.axis("off")
    paths = getattr(browser, "_v211_media_paths", [Path("media")])
    path = paths[getattr(browser, "_v211_media_index", 0)]
    panel.ax.set_title(f"{dec.kind}: {Path(path).name} | frame {idx+1}/{max(1, dec.frame_count)}")
    try: panel.canvas.draw_idle()
    except Exception: pass


def _media_tick(browser):
    if not getattr(browser, "_v211_media_playing", False): return
    dec = getattr(browser, "_v216_media_decoder", None)
    if dec is None or dec.frame_count <= 1: return
    browser._v211_media_frame = (getattr(browser, "_v211_media_frame", 0) + 1) % dec.frame_count
    _draw_media_frame(browser)
    delay = int(getattr(browser, "_v216_media_current_duration", dec.default_duration) or dec.default_duration)
    browser._v211_media_after = browser.after(max(25, delay), lambda: _media_tick(browser))


def _display_media(browser, paths: Sequence[Path], index: int = 0):
    paths = [Path(p) for p in paths if Path(p).suffix.lower() in _MEDIA_EXTS and Path(p).exists()]
    if not paths: return
    _cancel_media_animation(browser); _close_media_decoder(browser)
    browser._v211_media_paths = paths
    browser._v211_media_index = max(0, min(int(index), len(paths)-1))
    path = paths[browser._v211_media_index]
    try:
        dec = _LazyMediaDecoder(path)
        browser._v216_media_decoder = dec
        browser._v211_media_kind = dec.kind
        browser._v211_media_frame = 0
        # Never auto-play large GIFs or videos. The first frame appears quickly;
        # playback begins only after the user presses Play.
        browser._v211_media_playing = False
        _draw_media_frame(browser)
        try: browser.log(f"Opened {dec.kind} lazily: {path.name} ({dec.frame_count} frame(s)); press Play to animate")
        except Exception: pass
    except Exception as exc:
        try: browser.log(f"Media preview failed for {path.name}: {exc}")
        except Exception: pass


def _media_prev(browser):
    paths = getattr(browser, "_v211_media_paths", [])
    if paths: _display_media(browser, paths, (getattr(browser, "_v211_media_index", 0)-1) % len(paths))


def _media_next(browser):
    paths = getattr(browser, "_v211_media_paths", [])
    if paths: _display_media(browser, paths, (getattr(browser, "_v211_media_index", 0)+1) % len(paths))


def _media_toggle(browser):
    dec = getattr(browser, "_v216_media_decoder", None)
    if dec is None or dec.frame_count <= 1: return
    browser._v211_media_playing = not bool(getattr(browser, "_v211_media_playing", False))
    if browser._v211_media_playing: _media_tick(browser)
    else: _cancel_media_animation(browser)


def _media_open_external(browser):
    import os, webbrowser
    paths = getattr(browser, "_v211_media_paths", [])
    if not paths: return
    path = Path(paths[getattr(browser, "_v211_media_index", 0) % len(paths)])
    try: os.startfile(str(path))
    except Exception:
        try: webbrowser.open(path.resolve().as_uri())
        except Exception: pass

def _refresh_tree(browser) -> None:
    core = getattr(browser, "_v209_core", None) or _INSTALLED_CORE
    fallback = core.get("_v209_collect_browser_records") or core.get("_v208_collect_browser_records") or core.get("_v206_collect_browser_records")
    try:
        catalog = build_catalog(
            browser.cache_root,
            bundle_root=(core.get("_v206_bundle_root")(browser) if callable(core.get("_v206_bundle_root")) else browser.cache_root),
            manifest=getattr(browser, "manifest", {}),
            base_name=getattr(browser, "base_name", ""),
            fallback_collector=fallback,
        )
    except Exception as exc:
        try:
            browser.log(f"Unified catalog build failed: {exc}")
        except Exception:
            pass
        catalog = {"records": [], "warnings": [str(exc)], "technique_counts": {}}

    browser._browser_catalog_v209 = catalog
    browser._catalog_records = list(catalog.get("records") or [])
    browser._catalog_by_path = {_path_key(r.get("path")): r for r in browser._catalog_records}
    browser._path_by_iid = {}
    browser._record_by_iid = {}
    browser._node_meta = {}
    browser.tree.delete(*browser.tree.get_children())

    def node(parent: str, label: str, kind: str = "", open_: bool = False, **meta) -> str:
        iid = browser.tree.insert(parent, "end", text=label, values=(kind, ""), open=open_)
        browser._node_meta[iid] = dict(meta)
        return iid

    def leaf(parent: str, label: str, rec: Dict[str, Any], view_mode: str = "") -> str:
        try:
            formatter = core.get("_v138_fmt_size")
            size = formatter(Path(rec["path"]).stat().st_size) if callable(formatter) else str(Path(rec["path"]).stat().st_size)
        except Exception:
            size = ""
        iid = browser.tree.insert(parent, "end", text=label, values=(rec.get("technique_key", "table"), size), open=False)
        browser._path_by_iid[iid] = Path(rec["path"])
        browser._record_by_iid[iid] = rec
        browser._node_meta[iid] = {"technique_key": rec.get("technique_key"), "view_mode": view_mode or rec.get("plot_mode")}
        return iid

    def add_rows_by_method(parent: str, rows: List[Dict[str, Any]], view_mode: str = "") -> None:
        methods = defaultdict(list)
        for r in rows:
            methods[_ival(r.get("method_index"), 1)].append(r)
        for mi in sorted(methods):
            mrows = methods[mi]
            label = str(mrows[0].get("method_label") or f"Method {mi:02d}")
            mn = node(parent, label, "method", open_=len(methods) <= 4, technique_key=mrows[0].get("technique_key"), view_mode=view_mode)
            points = defaultdict(list)
            for r in mrows:
                points[_ival(r.get("point"))].append(r)
            for pt in sorted(points, key=lambda x: (x <= 0, x)):
                prows = points[pt]
                plabel = f"Point {pt:03d}" if pt > 0 else "Initial / pre-scan setup"
                pn = node(mn, plabel, "point", open_=len(points) <= 3, technique_key=prows[0].get("technique_key"), view_mode=view_mode)
                for r in sorted(prows, key=lambda x: (_ival(x.get("line")), _ival(x.get("cycle")), _ival(x.get("sweep")), _ival(x.get("source_order")))):
                    leaf(pn, _method_point_leaf_label(r, include_method=False), r, view_mode)

    def add_rows_by_point(parent: str, rows: List[Dict[str, Any]], view_mode: str = "") -> None:
        points = defaultdict(list)
        for r in rows:
            points[_ival(r.get("point"))].append(r)
        for pt in sorted(points, key=lambda x: (x <= 0, x)):
            prows = points[pt]
            plabel = f"Point {pt:03d}" if pt > 0 else "Initial / pre-scan setup"
            pn = node(parent, plabel, "point", open_=len(points) <= 4, technique_key=prows[0].get("technique_key"), view_mode=view_mode)
            methods = defaultdict(list)
            for r in prows:
                methods[_ival(r.get("method_index"), 1)].append(r)
            for mi in sorted(methods):
                mrows = methods[mi]
                label = str(mrows[0].get("method_label") or f"Method {mi:02d}")
                mn = node(pn, label, "method", open_=len(methods) <= 3, technique_key=mrows[0].get("technique_key"), view_mode=view_mode)
                for r in sorted(mrows, key=lambda x: (_ival(x.get("line")), _ival(x.get("cycle")), _ival(x.get("sweep")), _ival(x.get("source_order")))):
                    leaf(mn, _method_point_leaf_label(r, include_method=False), r, view_mode)

    def add_rows_by_spatial_axis(parent: str, rows: List[Dict[str, Any]], axis: str, view_mode: str = "") -> int:
        """v294: show the same point hierarchy ordered/labeled by physical Takedata coordinate."""
        try:
            from .spatial_points_v296 import spatial_axis_value, format_coordinate
        except Exception:
            return 0
        points = defaultdict(list)
        for r in rows:
            pt = _ival(r.get("point"))
            if pt > 0:
                points[pt].append(r)
        ranked = []
        key_name = {"X": "x_position_m", "Y": "y_position_m", "Distance": "scan_distance_m"}.get(axis, "")
        for pt, prows in points.items():
            value = None
            for r in prows:
                value = spatial_axis_value(r, axis)
                if value is not None:
                    break
            if value is not None:
                ranked.append((float(value), pt, prows))
        ranked.sort(key=lambda item: (item[0], item[1]))
        for value, pt, prows in ranked:
            if axis == "Distance":
                coord = format_coordinate(value)
                plabel = f"Distance {coord} · Point {pt:03d}"
            else:
                coord = format_coordinate(value)
                plabel = f"{axis} {coord} · Point {pt:03d}"
            pn = node(parent, plabel, "spatial point", open_=len(ranked) <= 4, technique_key=prows[0].get("technique_key"), view_mode=view_mode, spatial_axis=axis, spatial_value_m=value)
            methods = defaultdict(list)
            for r in prows:
                methods[_ival(r.get("method_index"), 1)].append(r)
            for mi in sorted(methods):
                mrows = methods[mi]
                label = str(mrows[0].get("method_label") or f"Method {mi:02d}")
                mn = node(pn, label, "method", open_=len(methods) <= 3, technique_key=mrows[0].get("technique_key"), view_mode=view_mode)
                for r in sorted(mrows, key=lambda x: (_ival(x.get("line")), _ival(x.get("cycle")), _ival(x.get("sweep")), _ival(x.get("source_order")))):
                    leaf(mn, _method_point_leaf_label(r, include_method=False), r, view_mode)
        return len(ranked)

    def add_original_heka(parent: str, rows: List[Dict[str, Any]]) -> None:
        groups = defaultdict(list)
        for r in rows:
            groups[(_ival(r.get("group_index")), str(r.get("group_title") or "untitled"))].append(r)
        for (gi, gt), grows in sorted(groups.items(), key=lambda kv: kv[0][0]):
            gn = node(parent, f"Group {gi:02d} — {gt}", "HEKA Group", open_=len(groups) <= 2)
            series = defaultdict(list)
            for r in grows:
                series[(_ival(r.get("series_index")), str(r.get("series_title") or "aggregated Series"))].append(r)
            for (si, st), srows in sorted(series.items(), key=lambda kv: kv[0][0]):
                slabel = "Aggregated EIS spectrum" if si == 0 and srows[0].get("technique_key") == "EIS" else f"Series {si:03d} — {st}"
                sn = node(gn, slabel, "HEKA Series", open_=False)
                for r in sorted(srows, key=lambda x: (_ival(x.get("sweep")), _ival(x.get("cycle")), _ival(x.get("source_order")))):
                    leaf(sn, _method_point_leaf_label(r, include_method=True), r)

    records = list(browser._catalog_records)
    by_tech = defaultdict(list)
    for r in records:
        by_tech[str(r.get("technique_key") or "OTHER")].append(r)

    for tech in sorted(by_tech, key=lambda k: (int(technique_spec(k).get("order", 999)), str(technique_spec(k).get("label", k)).lower())):
        rows = by_tech[tech]
        spec = technique_spec(tech)
        default_mode = str(spec.get("mode") or "Auto")
        root = node("", f"{spec.get('label', tech)} ({len(rows)})", "technique", open_=len(by_tech) <= 5, technique_key=tech, view_mode=default_mode)

        if tech in {"PHOTO", "GIF", "VIDEO", "FIGURE"}:
            for r in sorted(rows, key=lambda x: (str(x.get("role") or ""), Path(str(x.get("path") or "")).name.lower())):
                leaf(root, Path(str(r.get("path") or "media")).name, r, "Media viewer")
            continue

        if tech == "POSITION":
            # Takedata is a point anchor, not an electrochemical technique.
            # Present one concise point list instead of duplicating it under
            # method, point, and original-hierarchy branches.
            for r in sorted(rows, key=lambda x: (_ival(x.get("point")), _ival(x.get("group_index")))):
                pt = _ival(r.get("point"))
                gt = str(r.get("group_title") or "NewPoint")
                gi = _ival(r.get("group_index"))
                if pt > 0:
                    try:
                        from .spatial_points_v296 import format_coordinate
                        x_txt = format_coordinate(r.get("x_position_m"))
                        y_txt = format_coordinate(r.get("y_position_m"))
                    except Exception:
                        x_txt = y_txt = ""
                    spatial = f" · X {x_txt} · Y {y_txt}" if x_txt and y_txt else ""
                    label = f"Point {pt:03d} — Group {gi:02d} {gt} (Takedata){spatial}"
                else:
                    label = f"Initial setup — Group {gi:02d} {gt}"
                leaf(root, label, r, "Numeric")
            continue

        # Technique-specific plot views retain one method/point grouping contract.
        view_modes: List[Tuple[str, str]] = []
        if tech == "EIS":
            view_modes = [
                ("Nyquist Z′ vs −Z″", "EIS Nyquist"),
                ("Nyquist with frequency gradient", "EIS Nyquist — frequency gradient"),
                ("Bode magnitude", "EIS Bode magnitude"),
                ("Bode phase", "EIS Bode phase"),
                ("Stored Zimag sign diagnostic", "EIS Nyquist — stored Zimag"),
            ]
            cols = {_norm(c) for r in rows for c in r.get("columns") or []}
            if any(c.startswith("yreal") or c.startswith("realy") for c in cols):
                view_modes.append(("Admittance Nyquist", "EIS Admittance Nyquist"))
        elif tech == "SHEAR_FORCE":
            view_modes = [("Amplitude / monitor signal", "Shear-force amplitude"), ("Phase", "Shear-force phase")]

        if view_modes:
            vr = node(root, "Technique plot views", "plot views", open_=True, technique_key=tech)
            for label, mode in view_modes:
                vn = node(vr, label, "plot mode", open_=False, technique_key=tech, view_mode=mode)
                add_rows_by_method(vn, rows, mode)

        mn = node(root, f"Grouped by method ({len({r.get('method_index') for r in rows})})", "grouping", open_=not bool(view_modes), technique_key=tech, view_mode=default_mode)
        add_rows_by_method(mn, rows, default_mode)
        pn = node(root, f"Grouped by point ({len({r.get('point') for r in rows if _ival(r.get('point')) > 0}) or 'unassigned'})", "grouping", open_=False, technique_key=tech, view_mode=default_mode)
        add_rows_by_point(pn, rows, default_mode)

        # v296: retain Grouped by point and add coordinate-aware point orderings
        # sourced from the physical HEKA Takedata coordinates propagated by the
        # unified catalog. This does not create or jitter coordinates.
        try:
            from .spatial_points_v296 import available_axes
            spatial_axes = [a for a in available_axes(rows) if a in {"X", "Y", "Distance"}]
        except Exception:
            spatial_axes = []
        if spatial_axes:
            synthetic = any(bool(r.get("spatial_coordinate_is_synthetic")) for r in rows if r.get("spatial_coordinate_available"))
            spatial_label = "Spatial point order · Takedata (compatibility coordinates)" if synthetic else "Spatial point order · HEKA Takedata"
            sr = node(root, spatial_label, "spatial grouping", open_=False, technique_key=tech, view_mode=default_mode)
            for axis in spatial_axes:
                count = len({r.get('point') for r in rows if _ival(r.get('point')) > 0 and r.get({"X":"x_position_m","Y":"y_position_m","Distance":"scan_distance_m"}[axis]) is not None})
                label = f"By {axis} coordinate ({count} points)" if axis in {"X", "Y"} else f"By cumulative scan distance ({count} points)"
                an = node(sr, label, "spatial axis", open_=False, technique_key=tech, view_mode=default_mode, spatial_axis=axis)
                add_rows_by_spatial_axis(an, rows, axis, default_mode)

        rate_order = {}
        for r in rows:
            rate = str(r.get("scan_rate") or "").strip()
            if rate:
                rate_order[rate] = min(rate_order.get(rate, 10**9), _ival(r.get("method_index"), 10**9))
        rates = sorted(rate_order, key=lambda rate: (rate_order[rate], rate))
        if tech in {"CV", "CV/LSV", "LSV", "FSCV"} and rates:
            rn = node(root, f"Grouped by scan rate ({len(rates)})", "grouping", open_=False, technique_key=tech, view_mode="CV / LSV")
            for rate in rates:
                rr = [r for r in rows if str(r.get("scan_rate") or "").strip() == rate]
                rnode = node(rn, rate, "scan rate", open_=False, technique_key=tech, view_mode="CV / LSV")
                add_rows_by_point(rnode, rr, "CV / LSV")

        if tech == "DRT":
            run_root = node(root, "Grouped by DRT run and engine", "grouping", open_=False, technique_key=tech, view_mode="DRT")
            runs = defaultdict(list)
            for r in rows:
                runs[(str(r.get("run") or "DRT results"), str(r.get("engine") or "DRT"))].append(r)
            for (run, eng), rrows in sorted(runs.items()):
                rr = node(run_root, run, "DRT run", open_=False, technique_key=tech, view_mode="DRT")
                ee = node(rr, eng, "DRT engine", open_=False, technique_key=tech, view_mode="DRT")
                add_rows_by_method(ee, rrows, "DRT")

        heka_rows = [r for r in rows if r.get("instrument") == "HEKA" or r.get("heka_v216") or r.get("heka_v211") or r.get("heka_v209")]
        if heka_rows:
            hn = node(root, "Original HEKA hierarchy", "hierarchy", open_=False, technique_key=tech, view_mode=default_mode)
            add_original_heka(hn, heka_rows)

    counts = Counter(str(r.get("technique_key") or "OTHER") for r in records)
    browser._v206_technique_counts = dict(counts)
    summary = " | ".join(f"{technique_spec(k).get('label', k)} {v}" for k, v in sorted(counts.items(), key=lambda kv: technique_spec(kv[0]).get("order", 999)))
    try:
        browser.summary_var.set(f"Unified canonical catalog | {len(records)} table(s) | {summary or 'no detected techniques'}")
    except Exception:
        pass
    try:
        browser.qa_text.configure(state="normal")
        browser.qa_text.delete("1.0", "end")
        lines = [
            "Unified browser catalog v216",
            f"Tables: {len(records)}",
            f"Detected categories: {len(counts)}",
        ]
        for k, n in sorted(counts.items(), key=lambda kv: technique_spec(kv[0]).get("order", 999)):
            lines.append(f"  • {technique_spec(k).get('label', k)}: {n} table(s), {catalog.get('method_counts', {}).get(k, 0)} method(s), {catalog.get('point_counts', {}).get(k, 0)} point(s)")
        if catalog.get("warnings"):
            lines.append("Warnings:")
            lines.extend(f"  • {x}" for x in catalog.get("warnings") or [])
        browser.qa_text.insert("1.0", "\n".join(lines))
        browser.qa_text.configure(state="disabled")
    except Exception:
        pass
    try:
        browser.log(f"Unified catalog rendered {len(records)} table(s) into {len(counts)} detected technique root(s). Empty categories hidden; method/point views retained for every parser.")
    except Exception:
        pass


def _column_map(df) -> Dict[str, Any]:
    cols = list(getattr(df, "columns", []))
    nmap = {_norm(c): c for c in cols}

    def pick(names: Sequence[str], contains: bool = True):
        for name in names:
            nn = _norm(name)
            if nn in nmap:
                return nmap[nn]
        if contains:
            for c in cols:
                cn = _norm(c)
                for name in names:
                    nn = _norm(name)
                    if len(nn) >= 3 and nn in cn:
                        return c
        return None

    return {
        "time": pick(["time_s", "time", "t_s", "elapsed_time_s"]),
        "potential": pick(["potential_v", "potential", "ewe_v", "ewe", "voltage_v", "voltage", "emon"]),
        "current": pick(["current_a", "current", "i_a", "imon", "current_na", "current_ua", "current_ma"]),
        "charge": pick(["charge_c", "charge", "q_c"]),
        "frequency": pick(["frequency_hz", "frequency", "freq_hz", "freq", "f2_hz", "f2"]),
        "zreal": pick(["zreal_ohm", "zreal", "real_z", "realz", "re_z", "re(z)"]),
        "zimag": pick(["zimag_ohm", "zimag", "imag_z", "imagz", "im_z", "im(z)"]),
        "neg_zimag": pick(["neg_zimag_ohm", "neg_zimag", "negative_zimag", "minus_zimag", "-zimag"]),
        "zmod": pick(["zmod_ohm", "zmod", "zabs_ohm", "zabs", "impedance_magnitude", "modulus"]),
        "phase": pick(["phase_deg", "phase", "phase_degree"]),
        "yreal": pick(["yreal_s", "yreal", "real_y", "re_y", "re(y)"]),
        "yimag": pick(["yimag_s", "yimag", "imag_y", "im_y", "im(y)"]),
        "ymod": pick(["ymod_s", "ymod", "yabs_s", "admittance_magnitude"]),
        "tau": pick(["tau_s", "tau", "timescale"]),
        "gamma": pick(["gamma_ohm", "gamma", "drt"]),
        "x": pick(["x_position", "x_coordinate", "x_um", "x_mm", "x_m"], contains=False),
        "y": pick(["y_position", "y_coordinate", "y_um", "y_mm", "y_m"], contains=False),
        "z": pick(["z_position", "z_coordinate", "distance", "distance_m", "height"], contains=False),
        "distance": pick(["distance", "distance_m", "distance_um", "separation", "tip_distance"], contains=False),
        "line": pick(["line_index", "line", "scan_line"], contains=False),
        "point": pick(["point_index", "point", "position_index", "pixel_index"], contains=False),
        "amplitude": pick(["amplitude", "amplitude_v", "sf_amplitude", "rms", "magnitude_v"], contains=False),
        "z_slope": pick(["z_slope", "zslope", "slope_z"], contains=False),
    }


def _numeric(df, col):
    if col is None:
        return np.asarray([], dtype=float)
    import pandas as pd
    return pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)


def _generic_numeric_columns(df, exclude: Sequence[Any] = ()) -> List[Any]:
    import pandas as pd
    ex = {str(x) for x in exclude if x is not None}
    out = []
    for c in df.columns:
        if str(c) in ex:
            continue
        arr = pd.to_numeric(df[c], errors="coerce")
        if int(arr.notna().sum()) >= 2:
            out.append(c)
    return out


def _legend_label(rec: Mapping[str, Any], path: Path) -> str:
    bits = []
    if rec:
        if _ival(rec.get("group_index")):
            bits.append(f"G{_ival(rec.get('group_index')):02d}")
        bits.append(f"M{_ival(rec.get('method_index'), 1):02d}")
        title = str(rec.get("method_title") or rec.get("series_title") or "").strip()
        if title:
            bits.append(title)
        if _ival(rec.get("point")):
            bits.append(f"P{_ival(rec.get('point')):03d}")
        if _ival(rec.get("line")):
            bits.append(f"L{_ival(rec.get('line')):03d}")
        if _ival(rec.get("cycle")):
            bits.append(f"C{_ival(rec.get('cycle')):02d}")
        elif _ival(rec.get("sweep")):
            bits.append(f"S{_ival(rec.get('sweep')):02d}")
    return " | ".join(bits) if bits else path.stem[:80]


def _load_curve(panel, path):
    import pandas as pd
    p = Path(path)
    core = getattr(panel.browser, "_v209_core", None) or _INSTALLED_CORE
    reader = core.get("_v138_csv_read")
    try:
        df = reader(p) if callable(reader) else pd.read_csv(p, sep=None, engine="python")
    except Exception:
        df = pd.read_csv(p, sep=None, engine="python")
    rec = getattr(panel.browser, "_catalog_by_path", {}).get(_path_key(p), {})
    tech = str(rec.get("technique_key") or "OTHER")
    try:
        from .potential_identity_v309 import enrich_record
        rec = enrich_record(rec, technique=tech, dataframe=df, path=p)
    except Exception:
        pass
    return {
        "path": p,
        "df": df,
        "tech": tech,
        "cols": _column_map(df),
        "record": rec,
        "label": _legend_label(rec, p),
    }


def _finite_xy(x, y):
    if len(x) != len(y):
        n = min(len(x), len(y)); x = x[:n]; y = y[:n]
    mask = np.isfinite(x) & np.isfinite(y)
    return x[mask], y[mask]


def _plot_line(panel, x, y, label: str, xlabel: str, ylabel: str, marker: Optional[str] = None):
    x, y = _finite_xy(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
    if len(x) < 2:
        return None
    line, = panel.ax.plot(x, y, linewidth=1.6, marker=marker if marker is not None else ("." if len(x) <= 350 else None), markersize=3.0, label=label)
    panel.ax.set_xlabel(xlabel); panel.ax.set_ylabel(ylabel)
    return line


def _warn_fallback(panel, curve, message: str):
    key = (str(curve.get("path")), message)
    seen = getattr(panel, "_v209_warning_keys", set())
    if key not in seen:
        seen.add(key); panel._v209_warning_keys = seen
        try:
            panel.browser.log(f"Plot warning for {Path(curve.get('path')).name}: {message}")
        except Exception:
            pass


def _plot_eis(panel, curve, mode_l: str):
    df = curve["df"]; label = curve["label"]
    cols = _column_map(df)
    zr = _numeric(df, cols.get("zreal")); zi = _numeric(df, cols.get("zimag")); nzi = _numeric(df, cols.get("neg_zimag"))
    freq = _numeric(df, cols.get("frequency"))
    zmod = _numeric(df, cols.get("zmod")); phase = _numeric(df, cols.get("phase"))
    if len(freq) == 0:
        freq = np.arange(max(len(zr), len(zi), len(nzi)), 0, -1, dtype=float)
        _warn_fallback(panel, curve, "No explicit frequency column; acquisition order is used as a diagnostic X axis.")

    if "bode magnitude" in mode_l:
        if len(zmod) == 0 and len(zr) and len(zi):
            n = min(len(zr), len(zi)); zmod = np.sqrt(zr[:n] ** 2 + zi[:n] ** 2)
        return _plot_line(panel, freq, zmod, label, "Frequency / Hz", "|Z| / Ω"), False
    if "bode phase" in mode_l:
        if len(phase) == 0 and len(zr) and len(zi):
            n = min(len(zr), len(zi)); phase = np.degrees(np.arctan2(zi[:n], zr[:n]))
        return _plot_line(panel, freq, phase, label, "Frequency / Hz", "Phase / degree"), False
    if "admittance" in mode_l:
        yr = _numeric(df, cols.get("yreal")); yi = _numeric(df, cols.get("yimag"))
        if len(yr) == 0 or len(yi) == 0:
            n = min(len(zr), len(zi))
            if n:
                z = zr[:n] + 1j * zi[:n]
                good = np.abs(z) > np.finfo(float).eps
                y = np.full(n, np.nan + 1j * np.nan); y[good] = 1.0 / z[good]
                yr, yi = y.real, y.imag
        return _plot_line(panel, yr, -yi, label, "Y′ / S", "−Y″ / S", marker="o"), False

    if len(zr) == 0:
        return None, False
    if "stored zimag" in mode_l:
        y = zi if len(zi) else -nzi
        ylabel = "Stored Zimag / Ω"
    else:
        y = nzi if len(nzi) else -zi
        ylabel = "−Z″ / Ω"
    x, y = _finite_xy(zr, y)
    if len(x) < 2:
        return None, False
    want_line, want_points = panel._style_flags() if hasattr(panel, "_style_flags") else (True, True)
    if any(tok in mode_l for tok in ("frequency gradient", "freq-color", "freq color", "gradient", "colour", "color")):
        from matplotlib.collections import LineCollection
        from matplotlib.colors import LogNorm, Normalize
        n = min(len(x), len(freq)); x = x[:n]; y = y[:n]; f = freq[:n]
        mask = np.isfinite(x) & np.isfinite(y) & np.isfinite(f)
        x, y, f = x[mask], y[mask], f[mask]
        if len(x) < 2:
            return None, False
        order = np.argsort(f)[::-1]; x, y, f = x[order], y[order], f[order]
        core = getattr(panel.browser, "_v209_core", None) or _INSTALLED_CORE
        cmap_fun = core.get("_v206_get_cmap")
        cmap = cmap_fun(panel.browser) if callable(cmap_fun) else None
        if np.nanmin(f) > 0 and np.nanmax(f) > np.nanmin(f):
            norm = LogNorm(vmin=float(np.nanmin(f)), vmax=float(np.nanmax(f)))
        else:
            norm = Normalize(vmin=float(np.nanmin(f)), vmax=float(np.nanmax(f) or 1.0))
        if want_line:
            pts = np.column_stack([x, y]).reshape(-1, 1, 2)
            seg = np.concatenate([pts[:-1], pts[1:]], axis=1)
            lc = LineCollection(seg, cmap=cmap, norm=norm, linewidths=1.8)
            lc.set_array((f[:-1] + f[1:]) / 2.0); panel.ax.add_collection(lc); panel.ax.autoscale_view()
        if want_points:
            panel.ax.scatter(x, y, c=f, cmap=cmap, norm=norm, s=24, label="_nolegend_")
        if getattr(panel, "_v209_colorbar_mappable", None) is None:
            from matplotlib.cm import ScalarMappable
            sm = ScalarMappable(norm=norm, cmap=cmap); sm.set_array([]); panel._v209_colorbar_mappable = sm; panel._v209_colorbar_label = "Frequency / Hz"
            try:
                cb = panel.figure.colorbar(sm, ax=panel.ax, pad=0.03)
                cb.set_label("Frequency / Hz")
                panel._v209_colorbar_added = True
            except Exception:
                panel._v209_colorbar_added = False
        from matplotlib.lines import Line2D
        handle = Line2D([0], [0], linewidth=1.7 if want_line else 0, marker="o" if want_points else None, label=label)
    else:
        handle, = panel.ax.plot(x, y, linestyle="-" if want_line else "None", marker="o" if want_points else None, linewidth=1.7, markersize=3.5, label=label)
    panel.ax.set_xlabel("Z′ / Ω"); panel.ax.set_ylabel(ylabel)
    return handle, bool(getattr(panel, "_v209_colorbar_mappable", None))


def _signal_axis_label(col: Any) -> str:
    n = _norm(col)
    if "current" in n or n in {"i", "ia", "imon"}:
        return "Current / A"
    if "potential" in n or "voltage" in n or n in {"e", "ewe", "emon"}:
        return "Potential / V"
    if "phase" in n:
        return "Phase / degree"
    if "charge" in n or n in {"q", "qc"}:
        return "Charge / C"
    if "slope" in n:
        return "Z slope"
    if "amplitude" in n or "magnitude" in n:
        return "Amplitude"
    return str(col or "Signal")


def _choose_signal(cols: Mapping[str, Any], preferred: Sequence[str]) -> Any:
    for key in preferred:
        if cols.get(key) is not None:
            return cols[key]
    return None


def _plot_xy(panel, curve, mode_l: str):
    df = curve["df"]; cols = curve["cols"]; tech = str(curve.get("tech") or "OTHER"); label = curve["label"]
    rec = curve.get("record") or {}
    xcol = ycol = None; xlabel = ylabel = ""

    if tech == "DRT" or "drt" == mode_l.strip():
        xcol, ycol = cols.get("tau"), cols.get("gamma"); xlabel, ylabel = "τ / s", "γ / Ω"
    elif tech in {"CV", "CV/LSV", "LSV", "FSCV", "IV/TAFEL", "DPV", "SWV", "NPV", "RDE_RRDE"}:
        view = str(panel._get("cv_view_var", "I-V") or "I-V").lower()
        if "current vs time" in mode_l or ("i-t" in view and mode_l in {"auto", "cv / lsv", "iv / tafel"}):
            xcol, ycol = cols.get("time"), cols.get("current"); xlabel, ylabel = "Time / s", "Current / A"
        elif "potential vs time" in mode_l or ("e-t" in view and mode_l in {"auto", "cv / lsv", "iv / tafel"}):
            xcol, ycol = cols.get("time"), cols.get("potential"); xlabel, ylabel = "Time / s", "Potential / V"
        elif "charge vs time" in mode_l or ("q-t" in view and mode_l in {"auto", "cv / lsv", "iv / tafel"}):
            xcol, ycol = cols.get("time"), cols.get("charge") or cols.get("current"); xlabel, ylabel = "Time / s", "Charge / C"
        else:
            xcol, ycol = cols.get("potential"), cols.get("current"); xlabel, ylabel = "Potential / V", "Current / A"
            if xcol is None:
                _warn_fallback(panel, curve, "No valid potential channel is present; CV/LSV current-versus-potential plot was not invented.")
    elif tech == "CA":
        view = str(panel._get("cv_view_var", "I-t") or "I-t").lower()
        if "charge" in mode_l or "q-t" in view:
            xcol, ycol = cols.get("time"), cols.get("charge") or cols.get("current"); xlabel, ylabel = "Time / s", "Charge / C"
        elif "potential" in mode_l or "e-t" in view:
            xcol, ycol = cols.get("time"), cols.get("potential") or cols.get("current"); xlabel, ylabel = "Time / s", "Potential / V"
        else:
            xcol, ycol = cols.get("time"), cols.get("current"); xlabel, ylabel = "Time / s", "Current / A"
    elif tech in {"CP", "OCP"}:
        view = str(panel._get("cv_view_var", "E-t") or "E-t").lower()
        if tech == "CP" and ("charge" in mode_l or "q-t" in view):
            xcol, ycol = cols.get("time"), cols.get("charge") or cols.get("potential"); xlabel, ylabel = "Time / s", "Charge / C"
        else:
            xcol, ycol = cols.get("time"), cols.get("potential"); xlabel, ylabel = "Time / s", "Potential / V"
    elif tech == "APPROACH":
        xcol = cols.get("distance") or cols.get("z")
        if xcol is None:
            xcol = cols.get("time"); xlabel = "Acquisition time / s"
            _warn_fallback(panel, curve, "No reliable Z/distance channel is stored; ordered acquisition time is shown instead of inventing distance.")
        else:
            xlabel = "Z / distance"
        ycol = _choose_signal(cols, ["current", "potential", "amplitude", "phase", "z_slope"]); ylabel = _signal_axis_label(ycol)
    elif tech == "DISTANCE_CAL":
        xcol = cols.get("distance") or cols.get("z") or cols.get("time")
        xlabel = "Z / distance" if (cols.get("distance") or cols.get("z")) else "Acquisition time / s"
        ycol = _choose_signal(cols, ["phase", "amplitude", "potential", "current", "z_slope"]); ylabel = "Calibration signal" if ycol else "Signal"
        if not (cols.get("distance") or cols.get("z")):
            _warn_fallback(panel, curve, "Distance calibration has no explicit distance channel; acquisition time is retained as the honest X axis.")
    elif tech == "SHEAR_FORCE":
        title = str(rec.get("series_title") or "").lower()
        raw_time = _numeric(df, cols.get("time"))
        frequency_like = "spectra" in title and len(raw_time) > 1 and np.nanmedian(raw_time) > 1e3
        xcol = cols.get("time")
        xlabel = "Frequency / Hz" if frequency_like else ("Z / distance" if cols.get("z") else "Acquisition time / s")
        if cols.get("z") and not frequency_like:
            xcol = cols.get("z")
        if "phase" in mode_l:
            ycol = cols.get("phase"); ylabel = "Shear-force phase / degree"
        else:
            ycol = cols.get("amplitude") or cols.get("potential") or cols.get("current") or cols.get("z_slope")
            ylabel = "Shear-force amplitude / monitor signal"
        if frequency_like:
            _warn_fallback(panel, curve, "HEKA SF_Spectra stores the frequency sweep in the exported X trace; it is plotted as frequency based on protocol/Series semantics.")
    elif tech in {"TIME_SCAN", "X_SCAN", "Y_SCAN", "Z_SCAN", "LINE_SCAN", "SKP", "SICM", "CHRONOCOULOMETRY", "ZRA", "CONDUCTIVITY", "EQCM", "NOISE"}:
        coordinate_key = {"X_SCAN": "x", "Y_SCAN": "y", "Z_SCAN": "z", "LINE_SCAN": "distance"}.get(tech)
        xcol = cols.get(coordinate_key) if coordinate_key else cols.get("time")
        if xcol is None:
            xcol = cols.get("time") or cols.get("point")
            _warn_fallback(panel, curve, f"No explicit {coordinate_key or 'time'} coordinate is stored; ordered acquisition axis is used.")
        xlabel = {"X_SCAN": "X position", "Y_SCAN": "Y position", "Z_SCAN": "Z position", "LINE_SCAN": "Distance / line coordinate"}.get(tech, "Time / s")
        ycol = _choose_signal(cols, ["current", "potential", "phase", "amplitude", "z_slope"]); ylabel = _signal_axis_label(ycol)
    elif tech in {"MAP_2D", "SCAN_3D"}:
        x = cols.get("x"); y = cols.get("y"); z = cols.get("z")
        signal = _choose_signal(cols, ["current", "potential", "phase", "amplitude", "z_slope"])
        if x is not None and y is not None and signal is not None:
            xv, yv, sv = _numeric(df, x), _numeric(df, y), _numeric(df, signal)
            n = min(len(xv), len(yv), len(sv)); mask = np.isfinite(xv[:n]) & np.isfinite(yv[:n]) & np.isfinite(sv[:n])
            if int(mask.sum()) >= 4:
                sc = panel.ax.scatter(xv[:n][mask], yv[:n][mask], c=sv[:n][mask], s=34)
                panel.ax.set_xlabel("X position"); panel.ax.set_ylabel("Y position")
                if getattr(panel, "_v209_colorbar_mappable", None) is None:
                    panel._v209_colorbar_mappable = sc; panel._v209_colorbar_label = str(signal)
                return sc
        if tech == "SCAN_3D" and x is not None and y is not None and z is not None:
            pass
        xcol = cols.get("time") or cols.get("point")
        ycol = signal
        xlabel = "Acquisition order / time"; ylabel = _signal_axis_label(signal)
        _warn_fallback(panel, curve, "Spatial coordinates are incomplete; an ordered trace is shown and no map coordinates are invented.")
    else:
        nums = _generic_numeric_columns(df)
        if len(nums) >= 2:
            xcol, ycol = nums[:2]; xlabel, ylabel = str(xcol), str(ycol)

    if xcol is None or ycol is None:
        _warn_fallback(panel, curve, f"No valid axis pair for {tech} in mode '{panel._get('plot_mode_var', 'Auto')}'.")
        return None
    return _plot_line(panel, _numeric(df, xcol), _numeric(df, ycol), label, xlabel, ylabel)


def _render(panel, curves, clear: bool = True):
    mode = str(panel._get("plot_mode_var", "Auto") or "Auto")
    mode_l = mode.lower()
    previous_mode = str((getattr(panel, "last_recipe", None) or {}).get("mode", ""))
    # Technique-appropriate defaults are applied only when entering a new EIS
    # view. Users can subsequently change Linear/Log without the renderer
    # overriding their explicit choice while the mode remains unchanged.
    if ("bode" in mode_l or mode_l.strip() == "drt") and previous_mode != mode:
        try:
            panel.browser.plot_xscale_var.set("Log")
        except Exception:
            pass
    elif "nyquist" in mode_l and "bode" in previous_mode.lower():
        try:
            panel.browser.plot_xscale_var.set("Linear")
        except Exception:
            pass
    panel._new_figure()
    panel._v209_colorbar_mappable = None; panel._v209_colorbar_label = ""; panel._v209_colorbar_added = False; panel._v209_warning_keys = set()
    handles = []; fixed = []
    prepared=[]
    for _i, raw in enumerate(list(curves or [])):
        try:
            c=dict(raw)
            if not c.get("record"):
                c=_load_curve(panel,c.get("path"))
            else:
                try:
                    from .potential_identity_v309 import enrich_record
                    c["record"] = enrich_record(c.get("record"), technique=str(c.get("tech") or ""), dataframe=c.get("df"), path=c.get("path"))
                except Exception: pass
            prepared.append(c)
        except Exception:
            prepared.append(dict(raw))
    try:
        from .potential_identity_v309 import legend_labels, potential_sort_key
        recs=[c.get("record") or {} for c in prepared]
        fallback=[c.get("label") or _legend_label(c.get("record") or {}, Path(c.get("path"))) for c in prepared]
        label_mode=str(panel._get("legend_label_var", "Auto") or "Auto")
        labels, legend_title=legend_labels(recs,label_mode,fallback)
        for c,lab in zip(prepared,labels): c["label"]=lab
        order_mode=str(panel._get("potential_order_var", "Potential") or "Potential")
        if order_mode == "Potential" and prepared and all(str(c.get("tech") or "").upper() in {"EIS","GEIS","DRT"} for c in prepared):
            prepared=sorted(enumerate(prepared), key=lambda t: potential_sort_key(t[1].get("record") or {}, t[0]))
            prepared=[c for _,c in prepared]
        else: legend_title = legend_title if 'legend_title' in locals() else "Method / point / cycle"
    except Exception:
        legend_title="Method / point / cycle"
    for raw in prepared:
        try:
            c = dict(raw)
            if not c.get("record"):
                c = _load_curve(panel, c.get("path"))
            if mode == "Auto" and c.get("record"):
                local_mode = str(c["record"].get("plot_mode") or mode).lower()
            else:
                local_mode = mode_l
            if c.get("tech") == "EIS":
                h, _ = _plot_eis(panel, c, local_mode)
            else:
                h = _plot_xy(panel, c, local_mode)
            if h is not None:
                handles.append(h); fixed.append(c)
        except Exception as exc:
            try:
                panel.browser.log(f"v216 plot skipped {Path(raw.get('path', '')).name}: {exc}")
            except Exception:
                pass

    all_tech = {str(c.get("tech") or "") for c in fixed}
    if all_tech == {"EIS"}:
        if "bode phase" in mode_l:
            title = "EIS Bode phase overlay"
        elif "bode magnitude" in mode_l:
            title = "EIS Bode magnitude overlay"
        elif "stored zimag" in mode_l:
            title = "EIS stored-Zimag sign diagnostic"
        elif "frequency gradient" in mode_l:
            title = "EIS Nyquist frequency-gradient overlay"
        else:
            title = "EIS Nyquist overlay"
    else:
        title = f"{mode} overlay" if mode != "Auto" else "FaradaIQ technique-aware overlay"
    panel.ax.set_title(title)
    if handles and str(panel._get("legend_var", "On")).lower() not in {"off", "false", "0", "no"}:
        fs = 7 if len(handles) <= 14 else (6 if len(handles) <= 30 else 5)
        try:
            panel.ax.legend(handles=handles, fontsize=fs, loc="best", frameon=True, framealpha=0.86, title=legend_title)
        except Exception:
            pass
    has_cbar = False
    if panel._v209_colorbar_mappable is not None:
        try:
            if not getattr(panel, "_v209_colorbar_added", False):
                cb = panel.figure.colorbar(panel._v209_colorbar_mappable, ax=panel.ax, pad=0.03)
                cb.set_label(panel._v209_colorbar_label)
                panel._v209_colorbar_added = True
            has_cbar = True
        except Exception:
            pass
    panel._apply_options(has_cbar)
    try:
        panel.canvas.draw_idle()
    except Exception:
        pass
    panel.last_curves = fixed
    panel.last_recipe = {
        "version": "v273",
        "schema": "faradaiq-plot-recipe-v216",
        "mode": mode,
        "palette": str(panel._get("eis_cmap_var", "Blues")),
        "eis_style": str(panel._get("eis_plot_style_var", "Line + Points")),
        "legend": str(panel._get("legend_var", "On")),
        "legend_labels": str(panel._get("legend_label_var", "Auto")),
        "order": str(panel._get("potential_order_var", "Potential")),
        "cv_trace_view": str(panel._get("cv_view_var", "I-V")),
        "xscale": str(panel._get("plot_xscale_var", "Linear")),
        "yscale": str(panel._get("plot_yscale_var", "Linear")),
        "grid": bool(panel._get("plot_grid_var", True)),
        "xlim": [panel._get("plot_xmin_var", ""), panel._get("plot_xmax_var", "")],
        "ylim": [panel._get("plot_ymin_var", ""), panel._get("plot_ymax_var", "")],
        "curves": [
            {
                "path": str(c.get("path")),
                "technique": c.get("tech"),
                "method": (c.get("record") or {}).get("method_index"),
                "point": (c.get("record") or {}).get("point"),
                "line": (c.get("record") or {}).get("line"),
                "cycle": (c.get("record") or {}).get("cycle"),
                "sweep": (c.get("record") or {}).get("sweep"),
            }
            for c in fixed
        ],
    }
    return True


def install(core: Dict[str, Any]) -> None:
    """Install the v216 unified catalog/tree/plot layer into the loaded core."""
    global _INSTALLED_CORE
    _INSTALLED_CORE = core
    Browser = core.get("FaradaIQH5Browser")
    Panel = core.get("FaradaIQPublicationPlotPanelV206") or core.get("FaradaIQPublicationPlotPanel")
    ttk = core.get("ttk")
    if Browser is None or Panel is None or ttk is None:
        raise RuntimeError("FaradaIQ browser classes are not available")
    if getattr(Browser, "_v209_unified_catalog_installed", False):
        return

    previous_build = Browser._build_visible_ui_first
    previous_plot_selected = Browser.plot_selected

    def build_ui(self):
        self._v209_core = core
        previous_build(self)
        try:
            controls = ttk.Frame(self)
            controls.pack(fill="x", padx=8, pady=(0, 6), before=self.winfo_children()[-1] if self.winfo_children() else None)
            ttk.Label(controls, text="Media browser").pack(side="left", padx=(0,8))
            ttk.Button(controls, text="◀ Previous", command=lambda: _media_prev(self)).pack(side="left", padx=2)
            ttk.Button(controls, text="Play / pause", command=lambda: _media_toggle(self)).pack(side="left", padx=2)
            ttk.Button(controls, text="Next ▶", command=lambda: _media_next(self)).pack(side="left", padx=2)
            ttk.Button(controls, text="Open externally", command=lambda: _media_open_external(self)).pack(side="left", padx=2)
        except Exception:
            pass
        try:
            combo = _find_mode_combo(self, ttk)
            if combo is not None:
                combo.configure(values=PLOT_MODES, width=31)
                self.plot_mode = combo
        except Exception:
            pass
        try:
            self.tree.unbind("<<TreeviewSelect>>")
            self.tree.bind("<<TreeviewSelect>>", lambda event: _on_tree_select(self, event))
        except Exception:
            pass
        try:
            self.title(f"FaradaIQ Studio v273 — unified data browser — {getattr(self, 'base_name', '')}")
        except Exception:
            pass
        try:
            self.bind("<Destroy>", lambda e, b=self: _close_media_decoder(b) if e.widget is b else None, add="+")
        except Exception:
            pass

    def selected_paths(self, plottable_only=True):
        out: List[Path] = []; seen: set[str] = set()
        for iid in self.tree.selection():
            for rec in _tree_records(self, iid):
                p = Path(rec["path"])
                if plottable_only and p.suffix.lower() not in (_TABLE_EXTS | _MEDIA_EXTS):
                    continue
                key = _path_key(p)
                if key not in seen:
                    seen.add(key); out.append(p)
        return out

    def preview(self):
        return _on_tree_select(self)

    def plot_selected(self, clear=True, add_view=True):
        paths=self.selected_paths(plottable_only=True)
        media=[p for p in paths if p.suffix.lower() in _MEDIA_EXTS]
        tables=[p for p in paths if p.suffix.lower() in _TABLE_EXTS]
        if media and not tables:
            _display_media(self, media, 0)
            try:
                add_view_fn=core.get("_v138_add_plot_view")
                if callable(add_view_fn) and add_view:
                    add_view_fn(self, f"Media viewer | {len(media)} item(s) | v216", media, mode="Media viewer", source="v211_media_browser")
            except Exception:
                pass
            return
        return previous_plot_selected(self, clear=clear, add_view=add_view)

    Browser._build_visible_ui_first = build_ui
    Browser.refresh_tree = _refresh_tree
    Browser.selected_paths = selected_paths
    Browser.preview_selected = preview
    Browser.plot_selected = plot_selected
    Browser._v209_unified_catalog_installed = True

    Panel._load_curve = _load_curve
    Panel._plot_eis = _plot_eis
    Panel._plot_xy = _plot_xy
    Panel.render = _render
    core["FaradaIQPublicationPlotPanel"] = Panel
