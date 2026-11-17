"""FaradaIQ Studio v273 responsive Browser selection and frame color scaling.

This patch fixes three production issues:
- Auto rescale each frame now applies the active frame's exact finite range.
- Every fixed/manual/automatic color range shows useful intermediate colorbar ticks.
- Browser row selection returns immediately while CSV preview loading runs in a worker.
"""
from __future__ import annotations

import json
import math
import threading
import queue
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Sequence

VERSION = "v273"
_CORE: MutableMapping[str, Any] = {}


def _color_tick_values(lo: float, hi: float, count: int = 6) -> list[float]:
    """Return inclusive, evenly spaced colorbar ticks with stable endpoints."""
    try:
        lo_f, hi_f = float(lo), float(hi)
    except Exception:
        return []
    if not (math.isfinite(lo_f) and math.isfinite(hi_f)):
        return []
    if hi_f <= lo_f:
        pad = max(abs(lo_f), 1.0) * 1e-9
        lo_f, hi_f = lo_f - pad, hi_f + pad
    n = max(3, int(count))
    step = (hi_f - lo_f) / float(n - 1)
    return [lo_f + step * i for i in range(n)]


def _inject_color_controls(path: Path, payload: Mapping[str, Any]) -> None:
    """Inject deterministic per-frame range controls and six colorbar ticks."""
    try:
        html = path.read_text(encoding="utf-8")
    except Exception:
        return
    # The write pipeline can be called through more than one compatibility layer.
    if "faradaiq-color-scale-toolbar-v255" in html:
        return
    # Fresh v255 cache generation should not contain the prior toolbar, but make
    # replacement defensive for repaired/exported HTML files.
    if "faradaiq-color-scale-toolbar-v253" in html:
        start = html.find('<div id="faradaiq-color-scale-toolbar-v253"')
        end = html.find('</style>', start)
        if start >= 0 and end >= 0:
            html = html[:start] + html[end + len('</style>'):]
        script_start = html.find('<script id="faradaiq-color-scale-script-v253"')
        script_end = html.find('</script>', script_start)
        if script_start >= 0 and script_end >= 0:
            html = html[:script_start] + html[script_end + len('</script>'):]

    payload_json = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
    toolbar = '''
<div id="faradaiq-color-scale-toolbar-v255" role="group" aria-label="Contour color scale">
  <strong>Color scale</strong>
  <select id="faradaiq-color-mode-v255" aria-label="Color scale mode">
    <option value="auto">Auto rescale each frame</option>
    <option value="global">Fixed global min/max</option>
    <option value="manual">User-defined min/max</option>
  </select>
  <label>Min <input id="faradaiq-color-min-v255" type="number" step="any"></label>
  <label>Max <input id="faradaiq-color-max-v255" type="number" step="any"></label>
  <button id="faradaiq-color-apply-v255" type="button">Apply</button>
  <span id="faradaiq-color-status-v255"></span>
</div>
<style id="faradaiq-color-style-v255">
#faradaiq-color-scale-toolbar-v255{position:sticky;top:0;z-index:10000;display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 12px;border-bottom:1px solid #bcc7d6;background:#f7f9fc;color:#17365d;font:14px/1.3 Arial,sans-serif;box-sizing:border-box}
#faradaiq-color-scale-toolbar-v255 select,#faradaiq-color-scale-toolbar-v255 input,#faradaiq-color-scale-toolbar-v255 button{font:inherit;padding:5px 7px;border:1px solid #9ba9bc;border-radius:3px;background:white;color:#17365d}
#faradaiq-color-scale-toolbar-v255 input{width:120px}
#faradaiq-color-scale-toolbar-v255 button{cursor:pointer;font-weight:600}
#faradaiq-color-status-v255{color:#4b5f78}
</style>
'''
    script = r'''
<script id="faradaiq-color-scale-script-v255">
(function(){
  const payload=__PAYLOAD__;
  const modeEl=document.getElementById('faradaiq-color-mode-v255');
  const minEl=document.getElementById('faradaiq-color-min-v255');
  const maxEl=document.getElementById('faradaiq-color-max-v255');
  const applyEl=document.getElementById('faradaiq-color-apply-v255');
  const statusEl=document.getElementById('faradaiq-color-status-v255');
  const modeMap={'Auto per frame':'auto','Global fixed (all frames)':'global','User-defined min/max':'manual'};
  let currentMode=modeMap[payload.initial_mode]||'global';

  function graph(){return document.querySelector('.plotly-graph-div');}
  function finiteValues(obj){
    const values=[];
    function add(value){
      if(Array.isArray(value)){value.forEach(add);return;}
      const n=Number(value);if(Number.isFinite(n))values.push(n);
    }
    if(obj&&obj.z!=null)add(obj.z);
    if(obj&&obj.marker&&obj.marker.color!=null&&typeof obj.marker.color!=='string')add(obj.marker.color);
    return values;
  }
  function safeRange(values){
    if(!values.length)return null;
    let lo=Math.min.apply(null,values),hi=Math.max.apply(null,values);
    if(!(Number.isFinite(lo)&&Number.isFinite(hi)))return null;
    if(hi<=lo){const pad=Math.max(Math.abs(lo),1)*1e-9;lo-=pad;hi+=pad;}
    return [lo,hi];
  }
  function formatInput(n){
    if(!Number.isFinite(n))return '';
    const a=Math.abs(n);return (a!==0&&(a>=1e5||a<1e-3))?n.toExponential(7):String(Number(n.toPrecision(9)));
  }
  function formatTick(n){
    if(!Number.isFinite(n))return '';
    const a=Math.abs(n);
    const units=[[1e12,'T'],[1e9,'G'],[1e6,'M'],[1e3,'k']];
    for(const entry of units){if(a>=entry[0]){const v=n/entry[0];return String(Number(v.toPrecision(4)))+entry[1];}}
    if(a!==0&&a<1e-3)return n.toExponential(2);
    return String(Number(n.toPrecision(5)));
  }
  function ticks(range){
    const lo=Number(range[0]),hi=Number(range[1]),count=6,out=[];
    if(!(Number.isFinite(lo)&&Number.isFinite(hi)&&hi>lo))return {vals:[],text:[]};
    const step=(hi-lo)/(count-1);
    for(let i=0;i<count;i++)out.push(lo+step*i);
    return {vals:out,text:out.map(formatTick)};
  }
  function frameList(gd){try{return (gd._transitionData&&gd._transitionData._frames)||[];}catch(_){return [];}}
  function frameByName(gd,name){return frameList(gd).find(f=>String(f.name||'')===String(name||''));}
  function activeFrameName(gd){
    try{if(gd._faradaiqActiveFrameName)return String(gd._faradaiqActiveFrameName);}catch(_){}
    try{if(gd.dataset&&gd.dataset.faradaiqFrameName)return String(gd.dataset.faradaiqFrameName);}catch(_){}
    try{
      const active=(gd.layout&&gd.layout.sliders&&gd.layout.sliders[0]&&gd.layout.sliders[0].active);
      const frames=frameList(gd);if(Number.isInteger(active)&&frames[active])return String(frames[active].name||'');
    }catch(_){}
    return '';
  }
  function currentRange(gd){
    const values=[];(gd.data||[]).forEach(t=>values.push.apply(values,finiteValues(t)));
    return safeRange(values)||payload.initial_range||payload.global_range;
  }
  function activeFrameRange(gd){
    const name=activeFrameName(gd);
    const mapped=payload.frame_ranges&&payload.frame_ranges[name];
    if(mapped&&mapped.length===2&&Number(mapped[1])>Number(mapped[0]))return [Number(mapped[0]),Number(mapped[1])];
    const frame=frameByName(gd,name);
    if(frame){const values=[];(frame.data||[]).forEach(t=>values.push.apply(values,finiteValues(t)));const range=safeRange(values);if(range)return range;}
    return currentRange(gd);
  }
  function selectedRange(gd,mode){
    if(mode==='manual'){
      const lo=Number(minEl.value),hi=Number(maxEl.value);
      if(!(Number.isFinite(lo)&&Number.isFinite(hi)&&hi>lo))throw new Error('Enter a finite minimum smaller than the maximum.');
      return [lo,hi];
    }
    if(mode==='global')return [Number(payload.global_range[0]),Number(payload.global_range[1])];
    return activeFrameRange(gd);
  }
  function colorTrace(trace,range){
    if(!trace)return;
    const lo=range[0],hi=range[1],tk=ticks(range);
    const type=String(trace.type||'').toLowerCase();
    const hasZ=trace.z!=null||['contour','heatmap','surface','mesh3d'].includes(type);
    const hasMarker=trace.marker&&trace.marker.color!=null&&typeof trace.marker.color!=='string';
    if(hasZ){
      trace.zauto=false;trace.zmin=lo;trace.zmax=hi;
      trace.colorbar=trace.colorbar||{};trace.colorbar.tickmode='array';trace.colorbar.tickvals=tk.vals;trace.colorbar.ticktext=tk.text;trace.colorbar.nticks=tk.vals.length;
    }
    if(hasMarker){
      trace.marker.cauto=false;trace.marker.cmin=lo;trace.marker.cmax=hi;
      trace.marker.colorbar=trace.marker.colorbar||{};trace.marker.colorbar.tickmode='array';trace.marker.colorbar.tickvals=tk.vals;trace.marker.colorbar.ticktext=tk.text;trace.marker.colorbar.nticks=tk.vals.length;
    }
  }
  function mutateFrames(gd,mode,fixedRange){
    frameList(gd).forEach(frame=>{
      const name=String(frame.name||'');
      const range=mode==='auto'?(payload.frame_ranges[name]||null):fixedRange;
      if(range)(frame.data||[]).forEach(trace=>colorTrace(trace,[Number(range[0]),Number(range[1])]));
    });
  }
  function restyleCurrent(gd,range){
    const zIdx=[],markerIdx=[],tk=ticks(range);
    (gd.data||[]).forEach((trace,i)=>{
      const type=String(trace&&trace.type||'').toLowerCase();
      if(trace&&(trace.z!=null||['contour','heatmap','surface','mesh3d'].includes(type)))zIdx.push(i);
      if(trace&&trace.marker&&trace.marker.color!=null&&typeof trace.marker.color!=='string')markerIdx.push(i);
    });
    const jobs=[];
    if(zIdx.length)jobs.push(Plotly.restyle(gd,{
      'zauto':false,'zmin':range[0],'zmax':range[1],
      'colorbar.tickmode':zIdx.map(()=> 'array'),'colorbar.tickvals':zIdx.map(()=>tk.vals),'colorbar.ticktext':zIdx.map(()=>tk.text),'colorbar.nticks':zIdx.map(()=>tk.vals.length)
    },zIdx));
    if(markerIdx.length)jobs.push(Plotly.restyle(gd,{
      'marker.cauto':false,'marker.cmin':range[0],'marker.cmax':range[1],
      'marker.colorbar.tickmode':markerIdx.map(()=> 'array'),'marker.colorbar.tickvals':markerIdx.map(()=>tk.vals),'marker.colorbar.ticktext':markerIdx.map(()=>tk.text),'marker.colorbar.nticks':markerIdx.map(()=>tk.vals.length)
    },markerIdx));
    return Promise.all(jobs).then(()=>Plotly.redraw(gd));
  }
  function syncInputs(mode){
    const disabled=mode!=='manual';minEl.disabled=disabled;maxEl.disabled=disabled;
    if(mode==='global'){minEl.value=formatInput(payload.global_range[0]);maxEl.value=formatInput(payload.global_range[1]);}
    else if(mode==='manual'&&!minEl.value){minEl.value=formatInput(payload.manual_range[0]);maxEl.value=formatInput(payload.manual_range[1]);}
  }
  function apply(silent){
    const gd=graph();if(!gd||typeof Plotly==='undefined')return Promise.resolve();
    currentMode=modeEl.value;
    try{
      const range=selectedRange(gd,currentMode);
      mutateFrames(gd,currentMode,currentMode==='auto'?null:range);
      syncInputs(currentMode);
      try{localStorage.setItem('faradaiq-color-mode-v255',currentMode);}catch(_){}
      return restyleCurrent(gd,range).then(()=>{
        const prefix=currentMode==='auto'?'Active frame range: ':'Active range: ';
        statusEl.textContent=prefix+formatInput(range[0])+' to '+formatInput(range[1])+' • 6 colorbar ticks';
      });
    }catch(err){statusEl.textContent=String(err&&err.message||err);return Promise.resolve();}
  }
  function start(){
    const gd=graph();if(!gd||typeof Plotly==='undefined'){setTimeout(start,80);return;}
    try{const saved=localStorage.getItem('faradaiq-color-mode-v255');if(['auto','global','manual'].includes(saved))currentMode=saved;}catch(_){}
    modeEl.value=currentMode;
    minEl.value=formatInput(payload.manual_range[0]);maxEl.value=formatInput(payload.manual_range[1]);
    syncInputs(currentMode);apply(true);
    modeEl.addEventListener('change',()=>apply(false));
    applyEl.addEventListener('click',()=>apply(false));
    minEl.addEventListener('keydown',e=>{if(e.key==='Enter')apply(false);});
    maxEl.addEventListener('keydown',e=>{if(e.key==='Enter')apply(false);});
    gd.addEventListener('faradaiq_frame_changed',e=>{
      try{gd._faradaiqActiveFrameName=String(e.detail&&e.detail.name||'');}catch(_){}
      if(currentMode==='auto')setTimeout(()=>apply(true),0);
    });
    if(gd.on){
      gd.on('plotly_sliderchange',e=>{
        try{
          const step=e&&e.step;const args=step&&step.args;const name=Array.isArray(args)&&Array.isArray(args[0])?args[0][0]:'';
          if(name)gd._faradaiqActiveFrameName=String(name);
        }catch(_){}
        if(currentMode==='auto')setTimeout(()=>apply(true),0);
      });
      gd.on('plotly_animated',()=>{if(currentMode==='auto')setTimeout(()=>apply(true),0);});
    }
    const g=payload.global_range;statusEl.textContent='Global data range: '+formatInput(g[0])+' to '+formatInput(g[1])+' • 6 colorbar ticks';
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
</script>
'''.replace('__PAYLOAD__', payload_json)

    if '<body>' in html:
        html = html.replace('<body>', '<body>' + toolbar, 1)
    else:
        html = toolbar + html
    if '</body>' in html:
        html = html.replace('</body>', script + '</body>', 1)
    else:
        html += script
    path.write_text(html, encoding='utf-8')


def _inject_frame_controls(path: Path, payload: Mapping[str, Any]) -> None:
    """Inject the fixed dock and publish a frame-changed event after redraw."""
    if not payload.get("frame_names"):
        return
    try:
        html = path.read_text(encoding="utf-8")
    except Exception:
        return
    if "faradaiq-frame-dock-v255" in html:
        return
    payload_json = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
    dock = r'''
<div id="faradaiq-frame-dock-v255" role="group" aria-label="Hyperspectral frame controls">
  <button id="faradaiq-frame-play-v255" type="button">▶ Play</button>
  <button id="faradaiq-frame-pause-v255" type="button">❚❚ Pause</button>
  <label id="faradaiq-frame-caption-v255" for="faradaiq-frame-range-v255">Frame</label>
  <input id="faradaiq-frame-range-v255" type="range" min="0" max="0" value="0" step="1" aria-label="Hyperspectral frame slider">
  <output id="faradaiq-frame-value-v255">1 / 1</output>
</div>
<style id="faradaiq-frame-style-v255">
html,body{min-height:100%;}body{padding-bottom:72px!important;box-sizing:border-box;}
#faradaiq-frame-dock-v255{position:fixed;left:0;right:0;bottom:0;z-index:20000;display:grid;grid-template-columns:auto auto minmax(150px,auto) minmax(240px,1fr) auto;align-items:center;gap:8px;padding:9px 12px;border-top:1px solid #9ba9bc;background:rgba(247,249,252,.98);box-shadow:0 -2px 10px rgba(20,42,70,.18);color:#17365d;font:14px/1.25 Arial,sans-serif;box-sizing:border-box}
#faradaiq-frame-dock-v255 button{font:inherit;padding:6px 10px;border:1px solid #9ba9bc;border-radius:4px;background:#fff;color:#17365d;cursor:pointer;font-weight:600}
#faradaiq-frame-dock-v255 button:disabled{opacity:.55;cursor:default}#faradaiq-frame-range-v255{width:100%;min-width:160px;accent-color:#315b8a}
#faradaiq-frame-caption-v255{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}#faradaiq-frame-value-v255{white-space:nowrap;min-width:76px;text-align:right}
@media(max-width:760px){#faradaiq-frame-dock-v255{grid-template-columns:auto auto 1fr auto}#faradaiq-frame-caption-v255{grid-column:1/-1;grid-row:1}.plotly-graph-div{max-width:100%!important}}
</style>
'''
    script = r'''
<script id="faradaiq-frame-script-v255">
(function(){
  const payload=__PAYLOAD__,names=Array.isArray(payload.frame_names)?payload.frame_names:[],labels=Array.isArray(payload.frame_labels)?payload.frame_labels:[];
  const range=document.getElementById('faradaiq-frame-range-v255'),caption=document.getElementById('faradaiq-frame-caption-v255'),value=document.getElementById('faradaiq-frame-value-v255');
  const play=document.getElementById('faradaiq-frame-play-v255'),pause=document.getElementById('faradaiq-frame-pause-v255');
  let index=0,playing=false,token=0;
  function graph(){return document.querySelector('.plotly-graph-div');}
  function finiteZ(trace){if(!trace||trace.z==null)return false;const stack=[trace.z];while(stack.length){const item=stack.pop();if(Array.isArray(item)){for(let i=0;i<item.length;i++)stack.push(item[i]);}else if(Number.isFinite(Number(item)))return true;}return false;}
  function sync(){index=Math.max(0,Math.min(names.length-1,Number(index)||0));range.value=String(index);caption.textContent=labels[index]||('Frame '+(index+1));value.textContent=(index+1)+' / '+names.length;play.disabled=playing||names.length<2;pause.disabled=!playing;}
  function ensureSpatialLayer(gd){
    try{
      const visible=(gd.data||[]).some(t=>['contour','heatmap','surface','mesh3d'].includes(String(t.type||'').toLowerCase())&&finiteZ(t));
      if(!visible){
        const frames=(gd._transitionData&&gd._transitionData._frames)||[],frame=frames.find(f=>String(f.name||'')===String(names[index]||''));
        if(frame&&frame.data){const jobs=[];frame.data.forEach((t,i)=>{if(['contour','heatmap','surface','mesh3d'].includes(String(t.type||'').toLowerCase())&&finiteZ(t))jobs.push(Plotly.restyle(gd,{visible:true,z:[t.z]},[i]));});if(jobs.length)return Promise.all(jobs).then(()=>Plotly.redraw(gd));}
      }
      return Plotly.redraw(gd);
    }catch(_){return Promise.resolve();}
  }
  function announce(gd){
    const name=String(names[index]||'');
    try{gd._faradaiqActiveFrameName=name;if(gd.dataset)gd.dataset.faradaiqFrameName=name;}catch(_){}
    try{gd.dispatchEvent(new CustomEvent('faradaiq_frame_changed',{detail:{name:name,index:index,label:labels[index]||''}}));}catch(_){}
  }
  function show(i){
    const gd=graph();if(!gd||typeof Plotly==='undefined'||!names.length)return Promise.resolve();
    index=(Number(i)+names.length)%names.length;sync();
    return Plotly.animate(gd,[names[index]],{mode:'immediate',frame:{duration:0,redraw:true},transition:{duration:0}})
      .then(()=>ensureSpatialLayer(gd)).then(()=>announce(gd)).catch(()=>{try{return Plotly.redraw(gd).then(()=>announce(gd));}catch(_){announce(gd);}});
  }
  function stop(){playing=false;token++;sync();}
  function run(my){if(!playing||my!==token)return;const next=(index+1)%names.length;show(next).finally(()=>{if(playing&&my===token)setTimeout(()=>run(my),Math.max(80,Number(payload.frame_duration_ms)||280));});}
  function start(){
    const gd=graph();if(!gd||typeof Plotly==='undefined'){setTimeout(start,60);return;}
    range.max=String(Math.max(0,names.length-1));sync();show(0);
    range.addEventListener('input',()=>{stop();show(Number(range.value));});range.addEventListener('change',()=>{stop();show(Number(range.value));});
    play.addEventListener('click',()=>{if(names.length<2)return;playing=true;const my=++token;sync();run(my);});pause.addEventListener('click',stop);
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
</script>
'''.replace('__PAYLOAD__', payload_json)
    if '<body>' in html:
        html = html.replace('<body>', '<body>' + dock, 1)
    else:
        html = dock + html
    if '</body>' in html:
        html = html.replace('</body>', script + '</body>', 1)
    else:
        html += script
    path.write_text(html, encoding='utf-8')


def _branch_text(browser: Any, iids: Sequence[str], mode: str) -> str:
    labels: list[str] = []
    for iid in iids[:12]:
        try:
            labels.append(str(browser.tree.item(iid, "text") or "Selected branch"))
        except Exception:
            pass
    lines = [
        f"Selected Browser branch(es): {len(iids)}",
        f"Plot mode: {mode}",
        "Selection acknowledged immediately. Descendant tables are not scanned until Plot selected is requested.",
    ]
    if labels:
        lines.append("Selection: " + ", ".join(labels))
    return "\n".join(lines)


def _async_preview_reader(browser: Any, path: Path, max_rows: int) -> Any:
    """Read one table with a small thread-safe LRU cache."""
    try:
        key = (str(path.resolve()), int(path.stat().st_mtime_ns), int(max_rows))
    except Exception:
        key = (str(path), 0, int(max_rows))
    lock = getattr(browser, "_v255_preview_cache_lock", None)
    if lock is None:
        lock = threading.RLock()
        browser._v255_preview_cache_lock = lock
    with lock:
        cache: OrderedDict[Any, Any] = getattr(browser, "_v255_preview_cache", OrderedDict())
        if key in cache:
            value = cache.pop(key)
            cache[key] = value
            browser._v255_preview_cache = cache
            return value
    reader = getattr(browser, "_v209_core", {}).get("_v138_csv_read")
    value = reader(path, max_rows=max_rows) if callable(reader) else None
    with lock:
        cache = getattr(browser, "_v255_preview_cache", OrderedDict())
        cache[key] = value
        while len(cache) > 96:
            cache.popitem(last=False)
        browser._v255_preview_cache = cache
    return value


def _install_browser_selection() -> None:
    from faradaiq import browser_ui_v209 as bu
    from faradaiq import v253_visual_browser as v253

    def on_tree_select(browser: Any, _event: Any = None) -> None:
        try:
            iids = list(browser.tree.selection() or [])
            direct = v253._direct_records(browser, iids)
            mode = bu._mode_for_selection(browser, iids, direct)
            bu._set_mode_defaults(browser, mode)

            # v255 owns the final TreeviewSelect binding.  Synchronize the Analyze
            # panel from this authoritative callback so later binding replacement
            # cannot leave "Nothing selected" and disabled handoff buttons behind.
            def sync_analysis_context() -> None:
                refresh = getattr(browser, "_v226_refresh_analysis_context", None)
                if callable(refresh):
                    try:
                        refresh()
                    except Exception:
                        pass
            try:
                browser.after_idle(sync_analysis_context)
                browser.after(60, sync_analysis_context)
            except Exception:
                sync_analysis_context()

            generation = int(getattr(browser, "_v255_selection_generation", 0) or 0) + 1
            browser._v255_selection_generation = generation

            if direct:
                text = bu._metadata_text(direct, mode)
            else:
                text = _branch_text(browser, iids, mode)
            try:
                browser.qa_text.configure(state="normal")
                browser.qa_text.delete("1.0", "end")
                browser.qa_text.insert("1.0", text)
                if len(direct) == 1:
                    browser.qa_text.insert("end", "\n\nPreview: loading in background...")
                browser.qa_text.configure(state="disabled")
            except Exception:
                pass

            if len(direct) != 1:
                browser.selected_path = None
                browser.selected_df = None
                return

            path = Path(str(direct[0].get("path") or ""))
            browser.selected_path = path
            browser.selected_df = None
            if path.suffix.lower() in bu._MEDIA_EXTS:
                # Defer media setup so the native Treeview can repaint first.
                try:
                    browser.after(1, lambda b=browser, p=path, g=generation: bu._display_media(b, [p], 0) if getattr(b, "_v255_selection_generation", 0) == g else None)
                except Exception:
                    pass
                return

            results: queue.Queue[tuple[Any, Optional[BaseException]]] = queue.Queue(maxsize=1)

            def commit(value: Any, error: Optional[BaseException]) -> None:
                if int(getattr(browser, "_v255_selection_generation", 0) or 0) != generation:
                    return
                try:
                    if Path(str(getattr(browser, "selected_path", ""))) != path:
                        return
                except Exception:
                    return
                browser.selected_df = value if error is None else None
                try:
                    browser.qa_text.configure(state="normal")
                    current = browser.qa_text.get("1.0", "end-1c") if hasattr(browser.qa_text, "get") else text
                    current = current.replace("\n\nPreview: loading in background...", "")
                    suffix = "\n\nPreview: ready (cached for later selections)." if error is None else f"\n\nPreview unavailable: {error}"
                    browser.qa_text.delete("1.0", "end")
                    browser.qa_text.insert("1.0", current + suffix)
                    browser.qa_text.configure(state="disabled")
                except Exception:
                    pass

            def poll_result() -> None:
                if int(getattr(browser, "_v255_selection_generation", 0) or 0) != generation:
                    return
                try:
                    value, error = results.get_nowait()
                except queue.Empty:
                    try:
                        browser.after(15, poll_result)
                    except Exception:
                        pass
                    return
                commit(value, error)

            def worker() -> None:
                error: Optional[BaseException] = None
                value: Any = None
                try:
                    value = _async_preview_reader(browser, path, 1200)
                except BaseException as exc:  # preserve UI even for parser failures
                    error = exc
                try:
                    results.put_nowait((value, error))
                except queue.Full:
                    pass

            threading.Thread(target=worker, name="FaradaIQ-browser-preview-v255", daemon=True).start()
            try:
                browser.after(15, poll_result)
            except Exception:
                pass
        except Exception as exc:
            try:
                browser.log(f"Browser selection update failed: {exc}")
            except Exception:
                pass

    bu._on_tree_select = on_tree_select
    bu._v255_async_tree_selection = True
    Browser = _CORE.get("FaradaIQH5Browser")
    if Browser is None or getattr(Browser, "_v255_tree_bind", False):
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
    Browser._v255_tree_bind = True


def _install_visual_color() -> None:
    import faradaiq.v250_reliability as reliability
    import faradaiq.v253_visual_browser as visual
    import faradaiq.v254_contour_prerender as prerender

    reliability.VERSION = VERSION
    visual.VERSION = VERSION
    prerender.VERSION = VERSION
    reliability._inject_standalone_color_controls = _inject_color_controls
    visual._inject_fixed_frame_controls = _inject_frame_controls

    # Invalidate all earlier pre-rendered HTML so corrected JS is guaranteed to be
    # present. Unchanged v255 views are still reused from the new cache.
    prerender._CACHE_SCHEMA = "faradaiq-contour-prerender-cache-v255"
    prerender._CACHE_GENERATION = 255

    def cache_root(browser: Any, md: Any) -> Path:
        root = Path(md._view_dir(browser)) / "contour_prerender_cache_v255"
        root.mkdir(parents=True, exist_ok=True)
        return root

    prerender._cache_root = cache_root


def _normalize_titles() -> None:
    import re
    for cls_name in ("SessionReaderGUI", "FaradaIQH5Browser", "FaradaIQDataBrowser"):
        cls = _CORE.get(cls_name)
        if cls is None or getattr(cls, "_v255_title_patch", False):
            continue
        old = getattr(cls, "title", None)
        if not callable(old):
            continue

        def title(self: Any, text: Any = None, _old=old) -> Any:
            if text is None:
                return _old(self)
            return _old(self, re.sub(r"v\d+(?:\.\d+)?", VERSION, str(text)))

        cls.title = title
        cls.wm_title = title
        cls._v255_title_patch = True


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE
    _CORE = core
    modules = []
    try:
        from faradaiq import multidim_v216, v228_intelligence, v247_experience, v250_reliability, v253_visual_browser, v254_contour_prerender, v253_llm_qa, v251_context_scilink, v245_queue_scilink, v244_workflow_cleanup, v239_scilink_edison
        modules = [multidim_v216, v228_intelligence, v247_experience, v250_reliability, v253_visual_browser, v254_contour_prerender, v253_llm_qa, v251_context_scilink, v245_queue_scilink, v244_workflow_cleanup, v239_scilink_edison]
    except Exception:
        pass
    for module in modules:
        try:
            module.VERSION = VERSION
        except Exception:
            pass
    _install_visual_color()
    _install_browser_selection()
    _normalize_titles()
    core["FARADAIQ_AUTO_FRAME_RANGE_EXACT"] = True
    core["FARADAIQ_COLORBAR_INTERMEDIATE_TICKS"] = 6
    core["FARADAIQ_BROWSER_SELECTION_ASYNC"] = True
    core["FARADAIQ_CONTOUR_CACHE_GENERATION"] = 255
