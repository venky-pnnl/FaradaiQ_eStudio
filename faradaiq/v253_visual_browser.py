"""FaradaIQ Studio v273 visualization and Browser responsiveness fixes.

Fixes delivered by this patch:
- a fixed bottom frame slider that remains visible at every window size
- reliable contour/heatmap styling in every animation frame
- deterministic playback that redraws each spatial frame before advancing
- lightweight Browser tree selection so opening a branch never traverses all descendants
"""
from __future__ import annotations

import copy
import json
import math
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Sequence

VERSION = "v273"
_CORE: MutableMapping[str, Any] = {}


def _styled_frame_trace(trace: Any) -> Any:
    """Keep the complete visual contract of an animated Plotly trace.

    v247 intentionally stripped frame traces to minimize HTML size. That also
    removed contour coloring, line, marker mode, colorbar, and hover settings.
    Some Plotly/WebView combinations consequently redrew only the measured-point
    overlay during playback. A deep copy retains the proven trace type and all
    style properties while keeping frame data independent.
    """
    try:
        return copy.deepcopy(trace)
    except Exception:
        try:
            payload = trace.to_plotly_json()
            kind = str(payload.get("type") or "scatter").lower()
            import plotly.graph_objects as go
            cls = {
                "contour": go.Contour,
                "heatmap": go.Heatmap,
                "scatter": go.Scatter,
                "scattergl": go.Scattergl,
                "surface": go.Surface,
                "mesh3d": go.Mesh3d,
            }.get(kind, go.Scatter)
            return cls(**payload)
        except Exception:
            return trace


def _frame_control_payload(fig: Any) -> dict[str, Any]:
    frames = list(getattr(fig, "frames", ()) or ())
    names = [str(getattr(frame, "name", "") or f"frame_{i:04d}") for i, frame in enumerate(frames)]
    labels: list[str] = [""] * len(names)
    try:
        sliders = list(getattr(fig.layout, "sliders", ()) or ())
        if sliders:
            steps = list(getattr(sliders[0], "steps", ()) or ())
            for index, step in enumerate(steps[: len(labels)]):
                label = str(getattr(step, "label", "") or "").strip()
                labels[index] = label
    except Exception:
        pass
    for index, frame in enumerate(frames):
        if labels[index]:
            continue
        try:
            title = str(getattr(getattr(frame, "layout", None), "title", None).text or "")
        except Exception:
            title = ""
        labels[index] = title.rsplit("|", 1)[-1].strip() if title else f"Frame {index + 1}"
    return {
        "schema": "faradaiq-fixed-frame-controls-v253",
        "version": VERSION,
        "frame_names": names,
        "frame_labels": labels,
        "frame_duration_ms": 280,
    }


def _hide_native_playback(fig: Any) -> None:
    """Use one always-visible playback dock rather than off-screen Plotly controls."""
    try:
        for slider in list(getattr(fig.layout, "sliders", ()) or ()):
            slider.visible = False
    except Exception:
        pass
    try:
        keep = []
        for menu in list(getattr(fig.layout, "updatemenus", ()) or ()):
            labels = [str(getattr(button, "label", "") or "") for button in list(getattr(menu, "buttons", ()) or ())]
            if any("Play" in label or "Pause" in label for label in labels):
                continue
            keep.append(menu)
        fig.layout.updatemenus = tuple(keep)
    except Exception:
        pass


def _inject_fixed_frame_controls(path: Path, payload: Mapping[str, Any]) -> None:
    if VERSION == "v255":
        from faradaiq.v255_browser_color import _inject_frame_controls
        return _inject_frame_controls(path, payload)
    if not payload.get("frame_names"):
        return
    try:
        html = path.read_text(encoding="utf-8")
    except Exception:
        return
    if "faradaiq-frame-dock-v253" in html:
        return

    payload_json = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
    dock = r'''
<div id="faradaiq-frame-dock-v253" role="group" aria-label="Hyperspectral frame controls">
  <button id="faradaiq-frame-play-v253" type="button">▶ Play</button>
  <button id="faradaiq-frame-pause-v253" type="button">❚❚ Pause</button>
  <label id="faradaiq-frame-caption-v253" for="faradaiq-frame-range-v253">Frame</label>
  <input id="faradaiq-frame-range-v253" type="range" min="0" max="0" value="0" step="1" aria-label="Hyperspectral frame slider">
  <output id="faradaiq-frame-value-v253">1 / 1</output>
</div>
<style id="faradaiq-frame-style-v253">
html,body{min-height:100%;}
body{padding-bottom:72px!important;box-sizing:border-box;}
#faradaiq-frame-dock-v253{position:fixed;left:0;right:0;bottom:0;z-index:20000;display:grid;grid-template-columns:auto auto minmax(150px,auto) minmax(240px,1fr) auto;align-items:center;gap:8px;padding:9px 12px;border-top:1px solid #9ba9bc;background:rgba(247,249,252,.98);box-shadow:0 -2px 10px rgba(20,42,70,.18);color:#17365d;font:14px/1.25 Arial,sans-serif;box-sizing:border-box}
#faradaiq-frame-dock-v253 button{font:inherit;padding:6px 10px;border:1px solid #9ba9bc;border-radius:4px;background:#fff;color:#17365d;cursor:pointer;font-weight:600}
#faradaiq-frame-dock-v253 button:disabled{opacity:.55;cursor:default}
#faradaiq-frame-range-v253{width:100%;min-width:160px;accent-color:#315b8a}
#faradaiq-frame-caption-v253{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}
#faradaiq-frame-value-v253{white-space:nowrap;min-width:76px;text-align:right}
@media(max-width:760px){#faradaiq-frame-dock-v253{grid-template-columns:auto auto 1fr auto}#faradaiq-frame-caption-v253{grid-column:1/-1;grid-row:1}.plotly-graph-div{max-width:100%!important}}
</style>
'''
    script = r'''
<script id="faradaiq-frame-script-v253">
(function(){
  const payload=__PAYLOAD__;
  const names=Array.isArray(payload.frame_names)?payload.frame_names:[];
  const labels=Array.isArray(payload.frame_labels)?payload.frame_labels:[];
  const range=document.getElementById('faradaiq-frame-range-v253');
  const caption=document.getElementById('faradaiq-frame-caption-v253');
  const value=document.getElementById('faradaiq-frame-value-v253');
  const play=document.getElementById('faradaiq-frame-play-v253');
  const pause=document.getElementById('faradaiq-frame-pause-v253');
  let index=0, playing=false, token=0, busy=false;
  function graph(){return document.querySelector('.plotly-graph-div');}
  function finiteZ(trace){
    if(!trace || trace.z==null) return false;
    const stack=[trace.z];
    while(stack.length){const item=stack.pop();if(Array.isArray(item)){for(let i=0;i<item.length;i++)stack.push(item[i]);}else if(Number.isFinite(Number(item)))return true;}
    return false;
  }
  function sync(){
    index=Math.max(0,Math.min(names.length-1,Number(index)||0));
    range.value=String(index);
    caption.textContent=labels[index]||('Frame '+(index+1));
    value.textContent=(index+1)+' / '+names.length;
    play.disabled=playing||names.length<2;
    pause.disabled=!playing;
  }
  function ensureSpatialLayer(gd){
    try{
      const visibleContour=(gd.data||[]).some(t=>['contour','heatmap','surface','mesh3d'].includes(String(t.type||'').toLowerCase())&&finiteZ(t));
      if(!visibleContour){
        const frameList=(gd._transitionData&&gd._transitionData._frames)||[];
        const frame=frameList.find(f=>String(f.name||'')===String(names[index]||''));
        if(frame&&frame.data){
          const jobs=[];
          frame.data.forEach((t,i)=>{if(['contour','heatmap','surface','mesh3d'].includes(String(t.type||'').toLowerCase())&&finiteZ(t))jobs.push(Plotly.restyle(gd,{visible:true,z:[t.z]},[i]));});
          if(jobs.length) return Promise.all(jobs).then(()=>Plotly.redraw(gd));
        }
      }
      return Plotly.redraw(gd);
    }catch(_){return Promise.resolve();}
  }
  function show(i){
    const gd=graph();
    if(!gd||typeof Plotly==='undefined'||!names.length)return Promise.resolve();
    index=(Number(i)+names.length)%names.length;sync();busy=true;
    return Plotly.animate(gd,[names[index]],{mode:'immediate',frame:{duration:0,redraw:true},transition:{duration:0}})
      .then(()=>ensureSpatialLayer(gd)).catch(()=>{try{return Plotly.redraw(gd);}catch(_){}}).finally(()=>{busy=false;});
  }
  function stop(){playing=false;token++;sync();}
  function run(myToken){
    if(!playing||myToken!==token)return;
    const next=(index+1)%names.length;
    show(next).finally(()=>{if(playing&&myToken===token)setTimeout(()=>run(myToken),Math.max(80,Number(payload.frame_duration_ms)||280));});
  }
  function start(){
    const gd=graph();if(!gd||typeof Plotly==='undefined'){setTimeout(start,60);return;}
    range.max=String(Math.max(0,names.length-1));sync();show(0);
    range.addEventListener('input',()=>{stop();show(Number(range.value));});
    range.addEventListener('change',()=>{stop();show(Number(range.value));});
    play.addEventListener('click',()=>{if(names.length<2)return;playing=true;const my=++token;sync();run(my);});
    pause.addEventListener('click',stop);
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
</script>
'''.replace('__PAYLOAD__', payload_json)

    if "<body>" in html:
        html = html.replace("<body>", "<body>" + dock, 1)
    else:
        html = dock + html
    if "</body>" in html:
        html = html.replace("</body>", script + "</body>", 1)
    else:
        html += script
    path.write_text(html, encoding="utf-8")


def _install_visualization_fix() -> None:
    from faradaiq import multidim_v216 as md
    import faradaiq.v247_experience as experience

    experience._compact_frame_trace = _styled_frame_trace
    current_write = md._v216_write_figure
    if getattr(current_write, "_v253_fixed_frames", False):
        return

    def write_figure(fig: Any, out: Path, meta: Mapping[str, Any]) -> Any:
        payload = _frame_control_payload(fig)
        if payload.get("frame_names"):
            _hide_native_playback(fig)
            try:
                fig.update_layout(margin=dict(
                    l=max(70, int(getattr(fig.layout.margin, "l", 0) or 0)),
                    r=max(50, int(getattr(fig.layout.margin, "r", 0) or 0)),
                    b=max(75, min(95, int(getattr(fig.layout.margin, "b", 0) or 0))),
                    t=max(150, int(getattr(fig.layout.margin, "t", 0) or 0)),
                ))
            except Exception:
                pass
        enriched = dict(meta)
        enriched.update({
            "fixed_frame_slider": bool(payload.get("frame_names")),
            "contour_frames_preserve_style": True,
            "browser_tree_selection": "lightweight_branch_selection",
        })
        result = current_write(fig, out, enriched)
        try:
            _inject_fixed_frame_controls(Path(out), payload)
        except Exception:
            pass
        return result

    write_figure._v253_fixed_frames = True  # type: ignore[attr-defined]
    md._v216_write_figure = write_figure


def _direct_records(browser: Any, iids: Sequence[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    record_by_iid = getattr(browser, "_record_by_iid", {}) or {}
    path_by_iid = getattr(browser, "_path_by_iid", {}) or {}
    for iid in iids:
        rec = record_by_iid.get(iid)
        if rec is None and iid in path_by_iid:
            rec = {"path": str(path_by_iid[iid]), "technique_key": "OTHER", "plot_mode": "Auto"}
        if not rec:
            continue
        raw = str(rec.get("path") or "")
        try:
            key = str(Path(raw).resolve()).lower()
        except Exception:
            key = raw.lower()
        if key not in seen:
            rows.append(dict(rec));seen.add(key)
    return rows


def _branch_metadata(browser: Any, iids: Sequence[str], mode: str) -> str:
    labels: list[str] = []
    child_total = 0
    for iid in iids[:20]:
        try:
            labels.append(str(browser.tree.item(iid, "text") or "Selected branch"))
            child_total += len(browser.tree.get_children(iid))
        except Exception:
            continue
    lines = [
        f"Selected Browser branch(es): {len(iids)}",
        f"Plot mode: {mode}",
        "Branch opening is optimized and does not scan every descendant file.",
        "Select an individual table for immediate preview, or use Plot selected to process the complete selected branch.",
    ]
    if labels:
        lines.append("Selection: " + ", ".join(labels[:8]))
    if child_total:
        lines.append(f"Immediate child nodes: {child_total}")
    return "\n".join(lines)


def _read_cached(browser: Any, path: Path, max_rows: int = 1200) -> Any:
    try:
        key = (str(path.resolve()), int(path.stat().st_mtime_ns), int(max_rows))
    except Exception:
        key = (str(path), 0, int(max_rows))
    cache: OrderedDict[Any, Any] = getattr(browser, "_v253_preview_cache", OrderedDict())
    if key in cache:
        value = cache.pop(key);cache[key] = value;browser._v253_preview_cache = cache
        return value
    reader = getattr(browser, "_v209_core", {}).get("_v138_csv_read")
    value = reader(path, max_rows=max_rows) if callable(reader) else None
    cache[key] = value
    while len(cache) > 64:
        cache.popitem(last=False)
    browser._v253_preview_cache = cache
    return value


def _install_browser_tree_fix() -> None:
    from faradaiq import browser_ui_v209 as bu

    def on_tree_select(browser: Any, _event: Any = None) -> None:
        try:
            pending = getattr(browser, "_v253_selection_after", None)
            if pending is not None:
                browser.after_cancel(pending)
        except Exception:
            pass
        try:
            iids = list(browser.tree.selection() or [])
            direct = _direct_records(browser, iids)
            mode = bu._mode_for_selection(browser, iids, direct)
            bu._set_mode_defaults(browser, mode)
            text = bu._metadata_text(direct, mode) if direct else _branch_metadata(browser, iids, mode)
            try:
                browser.qa_text.configure(state="normal")
                browser.qa_text.delete("1.0", "end")
                browser.qa_text.insert("1.0", text)
                browser.qa_text.configure(state="disabled")
            except Exception:
                pass

            def commit() -> None:
                browser._v253_selection_after = None
                if len(direct) != 1:
                    return
                path = Path(str(direct[0].get("path") or ""))
                browser.selected_path = path
                if path.suffix.lower() in bu._MEDIA_EXTS:
                    browser.selected_df = None
                    bu._display_media(browser, [path], 0)
                    return
                try:
                    browser.selected_df = _read_cached(browser, path, 1200)
                except Exception:
                    browser.selected_df = None

            try:
                browser._v253_selection_after = browser.after(20, commit)
            except Exception:
                commit()
        except Exception as exc:
            try:
                browser.log(f"Browser selection update failed: {exc}")
            except Exception:
                pass

    bu._on_tree_select = on_tree_select
    bu._v253_lightweight_tree_selection = True

    Browser = _CORE.get("FaradaIQH5Browser")
    if Browser is None or getattr(Browser, "_v253_tree_bind", False):
        return
    previous_build = Browser._build_visible_ui_first

    def build(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = previous_build(self, *args, **kwargs)
        try:
            self.tree.unbind("<<TreeviewSelect>>")
            self.tree.bind("<<TreeviewSelect>>", lambda event, b=self: bu._on_tree_select(b, event))
        except Exception:
            pass
        return result

    Browser._build_visible_ui_first = build
    Browser._v253_tree_bind = True


def _normalize_titles() -> None:
    import re
    for cls_name in ("SessionReaderGUI", "FaradaIQH5Browser", "FaradaIQDataBrowser"):
        cls = _CORE.get(cls_name)
        if cls is None or getattr(cls, "_v253_title_patch", False):
            continue
        old_title = getattr(cls, "title", None)
        if not callable(old_title):
            continue
        def title(self: Any, text: Any = None, _old=old_title) -> Any:
            if text is None:
                return _old(self)
            return _old(self, re.sub(r"v\d+(?:\.\d+)?", VERSION, str(text)))
        cls.title = title
        cls.wm_title = title
        cls._v253_title_patch = True


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE
    _CORE = core
    from faradaiq import multidim_v216 as md
    import faradaiq.v247_experience as experience
    import faradaiq.v250_reliability as reliability
    import faradaiq.v253_llm_qa as llmqa
    for module in (md, experience, reliability, llmqa):
        try:
            module.VERSION = VERSION
        except Exception:
            pass
    _install_visualization_fix()
    _install_browser_tree_fix()
    _normalize_titles()
    core["FARADAIQ_FIXED_FRAME_SLIDER"] = True
    core["FARADAIQ_CONTOUR_PLAYBACK_STYLE_PRESERVED"] = True
    core["FARADAIQ_FAST_TREE_EXPANSION"] = True
