"""FaradaIQ Studio v300 Takedata parsing and companion-stage-coordinate recovery.

The parser treats an existing Takedata table as the authoritative spatial record
when it can be matched to the loaded HEKA run.  It also exposes the full Takedata
fields (Imon, Emon, X/Y/Z and time) for Browser/PowerPoint reporting.

No experiment-specific names or assumptions are encoded here.
"""
from __future__ import annotations

import csv
import math
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

VERSION = "v300"


def _finite(v: Any) -> float | None:
    try:
        x = float(v)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def _split(line: str) -> list[str]:
    s = str(line or "").strip()
    if not s:
        return []
    if "," in s:
        try:
            return [x.strip() for x in next(csv.reader([s], skipinitialspace=True))]
        except Exception:
            pass
    if "\t" in s:
        return [x.strip() for x in s.split("\t")]
    return [x.strip() for x in re.split(r"\s{2,}", s) if x.strip()]


def _unit_factor(header: str) -> float:
    h = str(header or "").lower().replace("μ", "µ")
    if "[nm]" in h or h.endswith("_nm"): return 1e-9
    if "[µm]" in h or "[um]" in h or h.endswith("_um"): return 1e-6
    if "[mm]" in h or h.endswith("_mm"): return 1e-3
    return 1.0


def _find_col(headers: Sequence[str], kind: str) -> int | None:
    norms=[_norm(h) for h in headers]
    synonyms={
        "sweep": ("sweep", "sweepno", "sweepnumber", "point", "pointindex", "positionindex"),
        "imon": ("imon2a", "imon", "currenta", "current", "ia"),
        "emon": ("emon2v", "emon", "potentialv", "potential", "voltagev", "voltage"),
        "x": ("xposm", "xpositionm", "xposition", "xcoordinate", "xcoord", "xm", "xum", "xmm"),
        "y": ("yposm", "ypositionm", "yposition", "ycoordinate", "ycoord", "ym", "yum", "ymm"),
        "z": ("zposm", "zpositionm", "zposition", "zcoordinate", "zcoord", "zm", "zum", "zmm"),
        "time": ("times", "elapsedtimes", "elapsedtime", "time"),
    }
    for wanted in synonyms.get(kind, ()):
        for i,n in enumerate(norms):
            if n == wanted:
                return i
    # Conservative contains fallback for vendor labels.
    token={"x":"xpos","y":"ypos","z":"zpos","imon":"imon","emon":"emon","time":"time","sweep":"sweep"}.get(kind)
    if token:
        for i,n in enumerate(norms):
            if token in n:
                return i
    return None


def _coord_close(a: Mapping[str,Any], b: Mapping[str,Any]) -> bool:
    vals=[]
    for k in ("x_m","y_m","z_m"):
        av,bv=_finite(a.get(k)),_finite(b.get(k))
        if av is None or bv is None:
            continue
        vals.append(abs(av-bv) <= max(5e-8, 2e-4*max(abs(av),abs(bv),1e-12)))
    return bool(vals) and all(vals)


def _collapse_adjacent_station_records(rows: list[dict[str,Any]]) -> list[dict[str,Any]]:
    """Collapse vendor duplicate station records without losing measured metadata.

    Some acquisitions write a pair of near-identical Takedata rows per scientific
    position.  We aggregate the pair to one station row and retain repeat_count.
    """
    if len(rows) < 2:
        return rows
    out=[]; i=0
    while i < len(rows):
        group=[rows[i]]; j=i+1
        # Deliberately cap the adjacent cluster: real repeated scan cycles are
        # handled separately below rather than merged into a single point.
        while j < len(rows) and len(group) < 3 and _coord_close(group[-1], rows[j]):
            group.append(rows[j]); j+=1
        if len(group) == 1:
            out.append(dict(group[0])); i+=1; continue
        base=dict(group[0])
        for key in ("imon_A","emon_V","x_m","y_m","z_m","time_s"):
            vv=[_finite(r.get(key)) for r in group]; vv=[v for v in vv if v is not None]
            if vv: base[key]=sum(vv)/len(vv)
        base["coordinate_repeat_count"]=sum(int(r.get("coordinate_repeat_count") or 1) for r in group)
        base["status"]="adjacent Takedata station records collapsed"
        out.append(base); i=j
    return out


def _sequence_repeats(rows: list[dict[str,Any]], period: int) -> bool:
    if period <= 0 or len(rows) % period:
        return False
    for i,row in enumerate(rows):
        if not _coord_close(row, rows[i % period]):
            return False
    return True


def _collapse_repeated_cycles(rows: list[dict[str,Any]]) -> list[dict[str,Any]]:
    n=len(rows)
    if n < 4:
        return rows
    for period in range(1, n//2 + 1):
        repeats=n//period
        if repeats < 2 or n % period:
            continue
        if _sequence_repeats(rows, period):
            base=[]
            for i in range(period):
                group=[rows[i+k*period] for k in range(repeats)]
                item=dict(group[0])
                for key in ("imon_A","emon_V","x_m","y_m","z_m","time_s"):
                    vv=[_finite(r.get(key)) for r in group]; vv=[v for v in vv if v is not None]
                    if vv: item[key]=sum(vv)/len(vv)
                item["coordinate_repeat_count"]=sum(int(r.get("coordinate_repeat_count") or 1) for r in group)
                item["sequence_repeat_count"]=repeats
                item["status"]=f"Takedata coordinate sequence repeated {repeats} times; collapsed to unique positions"
                base.append(item)
            return base
    return rows


def parse_takedata_records(path: str | Path) -> list[dict[str,Any]]:
    """Parse every numeric Takedata record without collapsing station repeats.

    This raw record view is used for acquisition diagnostics.  Spatial point
    mapping is produced separately by :func:`parse_takedata_table`, which
    collapses only demonstrable duplicate/repeated station records.
    """
    p=Path(path)
    try:
        if not p.is_file() or p.stat().st_size > 50_000_000:
            return []
        text=p.read_text(encoding="utf-8",errors="ignore")
    except Exception:
        try: text=p.read_text(encoding="windows-1252",errors="ignore")
        except Exception: return []
    header=None; idx={}; factors={}; out=[]; ordinal=0
    for line in text.splitlines():
        f=_split(line)
        if not f: continue
        xi,yi=_find_col(f,"x"),_find_col(f,"y")
        # HEKA exports may repeat the header between point blocks.  Each
        # encountered header refreshes column indexes and units.
        if xi is not None and yi is not None:
            header=f
            idx={k:_find_col(f,k) for k in ("sweep","imon","emon","x","y","z","time")}
            factors={k:(_unit_factor(f[i]) if i is not None and i<len(f) else 1.0) for k,i in idx.items()}
            continue
        if header is None or idx.get("x") is None or idx.get("y") is None:
            continue
        if max(idx["x"],idx["y"]) >= len(f):
            continue
        x,y=_finite(f[idx["x"]]),_finite(f[idx["y"]])
        if x is None or y is None: continue
        ordinal+=1
        def val(kind):
            i=idx.get(kind)
            return _finite(f[i]) if i is not None and i < len(f) else None
        sweep=val("sweep")
        z=val("z")
        out.append({
            "record_index": ordinal,
            "point_index": ordinal,
            "sweep": int(sweep) if sweep is not None else None,
            "imon_A": val("imon"),
            "emon_V": val("emon"),
            "x_m": x*factors.get("x",1.0),
            "y_m": y*factors.get("y",1.0),
            "z_m": (z*factors.get("z",1.0) if z is not None else None),
            "time_s": val("time"),
            "source_path": str(p),
            "source_name": p.name,
            "coordinate_repeat_count": 1,
            "status": "read directly from Takedata table",
        })
    # Distance for the raw acquisition-order trajectory.  This intentionally
    # preserves repeats so a user can audit stage motion and return cycles.
    cumulative=0.0; prev=None
    for r in out:
        x,y=_finite(r.get("x_m")),_finite(r.get("y_m"))
        if x is not None and y is not None:
            if prev is not None: cumulative += math.hypot(x-prev[0], y-prev[1])
            r["raw_trajectory_distance_m"]=cumulative; prev=(x,y)
        else: r["raw_trajectory_distance_m"]=None
    return out


def parse_takedata_table(path: str | Path) -> list[dict[str,Any]]:
    """Parse Takedata into one row per scientific spatial position.

    Raw records are retained by :func:`parse_takedata_records`; this station
    view collapses only near-identical adjacent records and exactly repeated
    coordinate cycles.  No coordinate is synthesized here.
    """
    raw=parse_takedata_records(path)
    if not raw:
        return []
    out=_collapse_adjacent_station_records([dict(r) for r in raw])
    out=_collapse_repeated_cycles(out)
    for i,r in enumerate(out,1): r["point_index"]=i
    cumulative=0.0; prev=None
    for r in out:
        x,y=_finite(r.get("x_m")),_finite(r.get("y_m"))
        if x is not None and y is not None:
            if prev is not None: cumulative += math.hypot(x-prev[0], y-prev[1])
            r["scan_distance_m"]=cumulative; prev=(x,y)
        else: r["scan_distance_m"]=None
    return out


def takedata_diagnostics(path: str | Path) -> dict[str,Any]:
    """Return deterministic parsing/geometry diagnostics for reporting."""
    raw=parse_takedata_records(path); stations=parse_takedata_table(path)
    def vals(rows,key):
        return [v for r in rows if (v:=_finite(r.get(key))) is not None]
    xs,ys,zs=vals(stations,"x_m"),vals(stations,"y_m"),vals(stations,"z_m")
    ts=vals(raw,"time_s"); em=vals(raw,"emon_V"); im=vals(raw,"imon_A")
    def span(v): return (max(v)-min(v)) if len(v)>=2 else 0.0
    xr,yr,zr=span(xs),span(ys),span(zs)
    scale=max(abs(xr),abs(yr),1e-30)
    if len(stations)<2: dominant="Point"
    elif abs(yr)<=0.02*scale: dominant="X"
    elif abs(xr)<=0.02*scale: dominant="Y"
    else: dominant="Distance"
    steps=[]
    for a,b in zip(stations,stations[1:]):
        ax,ay=_finite(a.get("x_m")),_finite(a.get("y_m")); bx,by=_finite(b.get("x_m")),_finite(b.get("y_m"))
        if None not in (ax,ay,bx,by): steps.append(math.hypot(bx-ax,by-ay))
    finite_steps=sorted(x for x in steps if math.isfinite(x))
    median_step=finite_steps[len(finite_steps)//2] if finite_steps else None
    time_monotonic=all(b>=a for a,b in zip(ts,ts[1:])) if len(ts)>=2 else True
    warnings=[]
    if len(raw)>len(stations): warnings.append(f"{len(raw)-len(stations)} repeated/duplicate Takedata record(s) collapsed for spatial point mapping; raw records remain available for acquisition diagnostics.")
    if not time_monotonic: warnings.append("Takedata time is not globally monotonic; this may reflect repeated blocks/cycles rather than a single continuous clock.")
    if dominant=="Distance" and len(stations)>=3: warnings.append("Both X and Y vary materially; cumulative XY distance is the default 1-D spatial axis.")
    return {
        "raw_record_count":len(raw), "station_count":len(stations), "dominant_axis":dominant,
        "x_span_m":xr, "y_span_m":yr, "z_span_m":zr, "median_step_m":median_step,
        "time_start_s":min(ts) if ts else None, "time_end_s":max(ts) if ts else None,
        "emon_min_V":min(em) if em else None, "emon_max_V":max(em) if em else None,
        "imon_min_A":min(im) if im else None, "imon_max_A":max(im) if im else None,
        "time_monotonic":time_monotonic, "warnings":warnings,
    }

def _source_stem(path: str | Path) -> str:
    p=Path(str(path)); return _norm(p.stem)


def find_companion_takedata(source_path: str | Path, *, expected_points: int | None = None) -> tuple[Path | None,list[dict[str,Any]]]:
    """Find the best physical Takedata table near a HEKA source file.

    Search is bounded to the source run's parent tree.  Exact source-stem affinity,
    scientific point-count agreement, and explicitly cleaned tables are preferred.
    """
    src=Path(str(source_path)).expanduser()
    try: src=src.resolve()
    except Exception: pass
    if not src.exists(): return None,[]
    stem=_source_stem(src)
    roots=[src.parent]
    # Common vendor export layout has a sibling data/ or data/<run>/ folder.
    for name in ("data","Data","DATA"):
        q=src.parent/name
        if q.exists(): roots.append(q)
    candidates=[]; seen=set()
    for root in roots:
        try:
            it=root.rglob("*takedata*.txt")
            for p in it:
                if not p.is_file(): continue
                key=os.path.normcase(str(p.resolve()))
                if key in seen: continue
                seen.add(key)
                # Do not search unrelated deep experiments.
                rel_depth=len(p.relative_to(root).parts)
                if rel_depth > 5: continue
                rows=parse_takedata_table(p)
                if not rows: continue
                pn=_norm(re.sub(r"(?i)_?takedata.*$","",p.stem))
                score=0
                if stem and pn:
                    if stem==pn: score+=300
                    elif stem in pn or pn in stem: score+=180
                if expected_points:
                    diff=abs(len(rows)-int(expected_points))
                    if diff==0: score+=160
                    elif diff==1: score+=50
                    else: score-=min(100,diff*3)
                low=p.name.lower()
                if any(t in low for t in ("edited","clean","cleaned","dedup")): score+=25
                if "synthetic" in low: score-=500
                candidates.append((score,p,rows))
        except Exception:
            continue
    if not candidates: return None,[]
    candidates.sort(key=lambda x:(-x[0],len(x[1].parts),x[1].name.lower()))
    score,p,rows=candidates[0]
    # Require at least some source affinity, or an exact point-count match within the same run directory.
    if score < 120:
        return None,[]
    return p,rows


def to_heka_rows(rows: Sequence[Mapping[str,Any]]) -> list[list[float]]:
    out=[]
    for r in rows:
        out.append([
            int(r.get("sweep") or 1),
            _finite(r.get("imon_A")) if _finite(r.get("imon_A")) is not None else float("nan"),
            _finite(r.get("emon_V")) if _finite(r.get("emon_V")) is not None else float("nan"),
            _finite(r.get("x_m")) if _finite(r.get("x_m")) is not None else float("nan"),
            _finite(r.get("y_m")) if _finite(r.get("y_m")) is not None else float("nan"),
            _finite(r.get("z_m")) if _finite(r.get("z_m")) is not None else float("nan"),
            _finite(r.get("time_s")) if _finite(r.get("time_s")) is not None else float("nan"),
        ])
    return out


def install(core: MutableMapping[str,Any]) -> None:
    """Install companion-Takedata recovery into the legacy HEKA export path."""
    if core.get("_FARADAIQ_V300_TAKEDATA_INSTALLED"):
        return
    current=core.get("_heka_v15_position_rows_from_dat")
    raw=core.get("_HEKA_V17_ORIGINAL_POSITION_ROWS_FROM_DAT") or current
    if not callable(current) or not callable(raw):
        return
    def position_rows(self):
        expected=max(len(getattr(self,"blocks",[]) or []), int((getattr(self,"scan_inference",{}) or {}).get("n_inferred_measurement_points") or 0)) or None
        p,parsed=find_companion_takedata(getattr(self,"source_path","") or "", expected_points=expected)
        if p is not None and parsed:
            rows=to_heka_rows(parsed)
            try:
                self.heka_v17_coordinate_mode="physical_companion_takedata"
                self.heka_v17_coordinate_note=f"Loaded {len(rows)} measured stage positions from companion Takedata table."
                self.heka_v17_coordinate_is_synthetic=False
                self.takedata_companion_path=str(p)
                self.takedata_full_rows=[dict(r) for r in parsed]
            except Exception: pass
            return rows, "physical_companion_takedata_table"
        return current(self)
    core["_heka_v15_position_rows_from_dat"]=position_rows
    core["_heka_v14_estimated_position_rows"]=position_rows
    core["_FARADAIQ_V300_TAKEDATA_INSTALLED"]=True
