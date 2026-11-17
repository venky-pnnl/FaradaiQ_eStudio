"""Shared potential identity contract for EIS/GEIS/DRT reporting.

Potential resolution is deliberately conservative. Structured metadata/table values win;
filename parsing is a technique-aware fallback and conflicting values resolve to unknown.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import math, re

VERSION = "v311"

@dataclass(frozen=True)
class PotentialIdentity:
    set_potential_V: float | None = None
    potential_label: str = ""
    potential_mode: str = "unknown"
    source: str = "unknown"
    ambiguous: bool = False
    def as_dict(self) -> dict[str, Any]: return asdict(self)

_FIXED_KEYS = (
    "set_potential_v","bias_potential_v","holding_potential_v","applied_potential_v",
    "working_electrode_potential_v","dc_potential_v","eis_potential_v","potential_v",
    "setpotentialv","biaspotentialv","holdingpotentialv","appliedpotentialv",
)
_OCP_MODE_KEYS = ("potential_mode","mode","eis_mode","control_mode","acquisition_mode")
_OCV_KEYS = ("ocp_v","open_circuit_potential_v","measured_ocp_v","ocv_v")
_ALLOWED_TECH = {"EIS","GEIS","DRT"}


def _norm_key(x: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(x or "").strip().lower()).strip("_")

def _finite_float(v: Any) -> float | None:
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except Exception: return None

def _format_v(v: float, decimals: int | None = None) -> str:
    if abs(v) < 0.0005: v = 0.0
    if decimals is None:
        av=abs(v)
        decimals = 3 if av < 0.1 and abs(v*100-round(v*100)) > 1e-8 else 2
        if abs(v*10-round(v*10)) < 1e-8: decimals=1
    s=f"{v:.{decimals}f}"
    if decimals > 1: s=s.rstrip("0").rstrip(".")
    if s == "-0": s="0"
    return f"{s} V"

def _mapping_candidates(m: Mapping[str, Any] | None) -> tuple[list[tuple[float,str]], bool, float | None]:
    fixed=[]; is_ocp=False; ocp_value=None
    if not isinstance(m, Mapping): return fixed,is_ocp,ocp_value
    flat={_norm_key(k):v for k,v in m.items()}
    # inspect common nested metadata containers too
    for nk in ("metadata","acquisition_metadata","method_metadata","series_metadata","settings","experiment"):
        child=m.get(nk)
        if isinstance(child, Mapping):
            for k,v in child.items(): flat.setdefault(_norm_key(k),v)
    for key in _OCP_MODE_KEYS:
        v=flat.get(key)
        if v is not None and re.search(r"\b(ocp|open\s*circuit|ocv)\b", str(v), re.I): is_ocp=True
    for k,v in flat.items():
        if k in _OCV_KEYS:
            fv=_finite_float(v)
            if fv is not None: ocp_value=fv
        if k in _FIXED_KEYS:
            fv=_finite_float(v)
            if fv is not None: fixed.append((fv, f"metadata:{k}"))
    # Explicit label can preserve OCP semantics from prior canonicalization.
    pl=str(flat.get("potential_label") or "")
    pm=str(flat.get("potential_mode") or "").lower()
    if pm == "ocp" or pl.upper().startswith("OCP"): is_ocp=True
    return fixed,is_ocp,ocp_value

def _table_candidates(columns: Sequence[Any] | None, rows: Sequence[Sequence[Any]] | None) -> tuple[list[tuple[float,str]], bool, float | None]:
    if not columns or not rows: return [],False,None
    idx={_norm_key(c):i for i,c in enumerate(columns)}
    fixed=[]; is_ocp=False; ocp=None
    for k in _OCP_MODE_KEYS:
        if k in idx:
            vals=[str(r[idx[k]]) for r in rows[:100] if idx[k] < len(r)]
            if any(re.search(r"\b(ocp|open\s*circuit|ocv)\b", v, re.I) for v in vals): is_ocp=True
    for k in _OCV_KEYS:
        if k in idx:
            vals=[_finite_float(r[idx[k]]) for r in rows[:100] if idx[k] < len(r)]
            vals=[v for v in vals if v is not None]
            if vals and max(vals)-min(vals) <= max(1e-6,1e-4*max(1,abs(sum(vals)/len(vals)))): ocp=sum(vals)/len(vals)
    for k in _FIXED_KEYS:
        if k in idx:
            vals=[_finite_float(r[idx[k]]) for r in rows[:200] if idx[k] < len(r)]
            vals=[v for v in vals if v is not None]
            if vals and max(vals)-min(vals) <= max(1e-6,1e-4*max(1,abs(sum(vals)/len(vals)))):
                fixed.append((sum(vals)/len(vals), f"table:{k}"))
    return fixed,is_ocp,ocp

def _df_candidates(df: Any) -> tuple[list[tuple[float,str]], bool, float | None]:
    try:
        cols=list(df.columns)
        rows=df.head(200).values.tolist()
        return _table_candidates(cols, rows)
    except Exception: return [],False,None

def _filename_candidate(path: str | Path | None, technique: str) -> list[tuple[float,str]]:
    if not path or str(technique or "").upper() not in _ALLOWED_TECH: return []
    text=Path(str(path)).stem
    # Remove contexts known NOT to represent DC set potential.
    scrub=re.sub(r"(?i)(?:scan[_ -]?rate|amplitude|ac[_ -]?(?:amp|amplitude)?)[_ :=-]*[+-]?\d+(?:[._]?(?:pt)?\d+)?\s*m?v(?:\s*/\s*s)?", " ", text)
    scrub=re.sub(r"(?i)[+-]?\d+(?:\.\d+)?\s*mV\s*/\s*s", " ", scrub)
    candidates=[]
    # V tokens incl. 0pt05V and -1.0_V. Require explicit V unit.
    pat=r"(?<![A-Za-z0-9])([+-]?\d+(?:(?:pt|\.)\d+)?)\s*_?\s*(m?V)(?![A-Za-z0-9])(?!\s*/\s*s)"
    for m in re.finditer(pat, scrub, re.I):
        raw=m.group(1).lower().replace("pt","."); unit=m.group(2).lower()
        try: val=float(raw) / (1000.0 if unit == "mv" else 1.0)
        except Exception: continue
        # A bare small mV token is risky unless potential/bias/hold context is nearby.
        around=scrub[max(0,m.start()-28):min(len(scrub),m.end()+28)].lower()
        has_dc_context = bool(re.search(r"potential|bias|hold|dc|eis|geis|ocp|set", around))
        if unit == "mv" and not has_dc_context: continue
        # v311: reject date/sample-name tokens and physically implausible filename-only voltages unless explicit DC context is nearby.
        if unit == "v" and abs(val) > 10 and not has_dc_context: continue
        candidates.append((val,"filename"))
    return candidates

def _coalesce(candidates: Iterable[tuple[float,str]]) -> tuple[float | None,str,bool]:
    vals=list(candidates)
    if not vals: return None,"unknown",False
    ref=vals[0][0]
    if any(abs(v-ref) > max(1e-5,1e-4*max(1,abs(ref),abs(v))) for v,_ in vals[1:]): return None,"conflict",True
    return sum(v for v,_ in vals)/len(vals), vals[0][1], False

def resolve_potential_identity(*, technique: str, metadata: Mapping[str,Any] | None=None,
                               dataframe: Any=None, columns: Sequence[Any] | None=None,
                               rows: Sequence[Sequence[Any]] | None=None, path: str | Path | None=None) -> PotentialIdentity:
    tech=str(technique or "").upper()
    if tech not in _ALLOWED_TECH: return PotentialIdentity()
    mc, mo, mov = _mapping_candidates(metadata)
    tc,to,tov = _df_candidates(dataframe) if dataframe is not None else _table_candidates(columns,rows)
    is_ocp=mo or to
    ocp_value=mov if mov is not None else tov
    # Structured sources are authoritative as a tier; disagreement => unknown.
    structured=mc+tc
    val,src,amb=_coalesce(structured)
    if amb: return PotentialIdentity(ambiguous=True, source="structured_conflict")
    if is_ocp:
        actual=ocp_value if ocp_value is not None else val
        return PotentialIdentity(actual, "OCP" if actual is None else f"OCP ({_format_v(actual)})", "ocp", src if actual is not None else "metadata")
    if val is not None: return PotentialIdentity(val,_format_v(val),"fixed",src)
    fc=_filename_candidate(path,tech)
    val,src,amb=_coalesce(fc)
    if amb: return PotentialIdentity(ambiguous=True,source="filename_conflict")
    _path_text = Path(str(path)).stem if path else ""
    if re.search(r"(?i)(?:^|[_ .-])(ocp|open[_ .-]*circuit|ocv)(?:$|[_ .-])", _path_text):
        return PotentialIdentity(val, "OCP" if val is None else f"OCP ({_format_v(val)})", "ocp", src if val is not None else "filename")
    if val is not None: return PotentialIdentity(val,_format_v(val),"fixed",src)
    return PotentialIdentity()

def enrich_record(record: Mapping[str,Any] | None, *, technique: str, dataframe: Any=None,
                  columns: Sequence[Any] | None=None, rows: Sequence[Sequence[Any]] | None=None,
                  path: str | Path | None=None) -> dict[str,Any]:
    r=dict(record or {}); md=dict(r.get("metadata") or {}) if isinstance(r.get("metadata"),Mapping) else {}
    merged=dict(md); merged.update({k:v for k,v in r.items() if k not in {"metadata","preview"}})
    ident=resolve_potential_identity(technique=technique,metadata=merged,dataframe=dataframe,columns=columns,rows=rows,path=path)
    vals=ident.as_dict()
    r.update({k:vals[k] for k in ("set_potential_V","potential_label","potential_mode")})
    md.update(vals); r["metadata"]=md
    return r

def _point(rec: Mapping[str,Any]) -> str:
    try: return f"P{int(rec.get('point')):03d}" if rec.get('point') is not None else ""
    except Exception: return ""

def _method(rec: Mapping[str,Any]) -> str:
    try: return f"M{int(rec.get('method_index')):02d}" if rec.get('method_index') is not None else str(rec.get("method_label") or "")
    except Exception: return str(rec.get("method_label") or "")

def legend_labels(records: Sequence[Mapping[str,Any]], mode: str="Auto", fallback_labels: Sequence[str] | None=None) -> tuple[list[str],str]:
    mode=str(mode or "Auto"); fall=list(fallback_labels or [""]*len(records)); ids=[str(r.get("potential_label") or "") for r in records]
    have=all(ids) and bool(ids)
    if mode == "Existing method labels" or not have:
        return [fall[i] or _method(r) or Path(str(r.get('physical_path') or 'dataset')).stem for i,r in enumerate(records)], "Method / point / cycle"
    def unique(xs): return len(set(xs))==len(xs)
    if mode == "Potential": labels=ids; title="Potential"
    elif mode == "Point + Potential": labels=[" · ".join(x for x in (_point(r),ids[i]) if x) for i,r in enumerate(records)]; title="Point · Potential"
    elif mode == "Method + Point + Potential": labels=[" · ".join(x for x in (_method(r),_point(r),ids[i]) if x) for i,r in enumerate(records)]; title="Method · Point · Potential"
    elif mode == "Filename": labels=[Path(str(r.get("physical_path") or r.get("path") or fall[i] or "dataset")).stem for i,r in enumerate(records)]; title="Filename"
    else:
        labels=ids; title="Potential"
        if not unique(labels): labels=[" · ".join(x for x in (_point(r),ids[i]) if x) for i,r in enumerate(records)]; title="Point · Potential"
        if not unique(labels): labels=[" · ".join(x for x in (_method(r),_point(r),ids[i]) if x) for i,r in enumerate(records)]; title="Method · Point · Potential"
        if not unique(labels):
            labels=[" · ".join(x for x in (labels[i], f"C{int(r.get('cycle')):02d}" if r.get('cycle') is not None else (f"S{int(r.get('sweep')):02d}" if r.get('sweep') is not None else "")) if x) for i,r in enumerate(records)]
            title += " · Cycle" if any(r.get('cycle') is not None for r in records) else " · Sweep"
    return labels,title

def potential_sort_key(rec: Mapping[str,Any], acquisition_index: int=0):
    mode=str(rec.get("potential_mode") or "unknown")
    v=_finite_float(rec.get("set_potential_V"))
    if mode=="fixed" and v is not None: return (0,v,acquisition_index)
    if mode=="ocp": return (1, v if v is not None else 0.0, acquisition_index)
    return (2,0.0,acquisition_index)
