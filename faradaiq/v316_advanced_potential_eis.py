"""FaradaIQ Studio v316 advanced potential-dependent EIS analysis.

Scientific goals
----------------
* validate each spectrum before mechanistic fitting;
* explicitly model the high-frequency inductive lead response when present;
* compare blocking, charge-transfer, finite-diffusion and two-relaxation circuits;
* report model evidence, parameter identifiability and boundary/conditioning flags;
* calculate complex capacitance and capacitance dispersion directly from impedance;
* estimate an *apparent* capacitance-minimum/PZC track for GC/ionic-liquid style data;
* gate Mott-Schottky flat-band/carrier-density interpretation behind strict checks;
* summarize DRT peak tracks after pyDRTtools has completed;
* never label a fitted term as quantum capacitance without independent physical inputs.

This layer is additive. v314/v315 standard EIS, DRT and Mott-Schottky exports remain available.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

import numpy as np

VERSION = "v316"
DEFAULT_OUTPUT_DIR = r"C:\Data\ANALYSIS\Faradaiq"

# Published/explicit thresholds are intentionally surfaced in the overview JSON.
QUALITY = {
    "kk_style_rmse_pass": 0.05,
    "kk_style_rmse_review": 0.10,
    "fit_rmse_strong": 0.05,
    "fit_rmse_review": 0.10,
    "delta_aicc_strong": 6.0,
    "jacobian_condition_review": 1e10,
    "mott_r2_min": 0.98,
    "mott_min_window_span_V": 0.15,
    "mott_vfb_margin_V": 0.20,
    "apparent_pzc_stability_std_V": 0.05,
}


def _finite(x: Any) -> float | None:
    try:
        v = float(x)
    except Exception:
        return None
    return v if math.isfinite(v) else None


def _safe(text: Any, default: str = "advanced_potential_eis") -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or default)).strip("._") or default


def _source_context(ms: Mapping[str,Any]) -> dict[str,Any]:
    out: dict[str,Any]={}
    src=ms.get("source_file")
    try:
        if src and Path(str(src)).exists():
            text=Path(str(src)).read_text(encoding="utf-8",errors="ignore")
            m=re.search(r"Working Electrode Area \(cm2\)\s*=\s*([-+0-9.eE]+)",text,re.I)
            if m: out["electrode_area_cm2"]=_finite(m.group(1))
            m=re.search(r"Working Electrode Type\s*=\s*([^\r\n]+)",text,re.I)
            if m and m.group(1).strip() and m.group(1).strip().lower()!="unspecified": out["electrode_class"]=m.group(1).strip()
            m=re.search(r"Reference Electrode\s*=\s*([^\r\n]+)",text,re.I)
            if m: out["reference_electrode"]=m.group(1).strip()
    except Exception:
        pass
    return out


def _write_csv(path: Path, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(list(columns))
        for row in rows:
            out = []
            for x in row:
                if x is None:
                    out.append("")
                elif isinstance(x, (list, tuple, dict)):
                    out.append(json.dumps(x, ensure_ascii=False))
                else:
                    try:
                        v = float(x)
                        out.append("" if not math.isfinite(v) else f"{v:.12g}")
                    except Exception:
                        out.append(str(x))
            w.writerow(out)


def _n_from_raw(x: float) -> float:
    return 0.30 + 0.70 / (1.0 + math.exp(-float(np.clip(x, -50, 50))))


def _raw_from_n(n: float) -> float:
    y = min(max((float(n) - 0.30) / 0.70, 1e-8), 1 - 1e-8)
    return math.log(y / (1 - y))


def _cpe_z(q: float, n: float, w: np.ndarray) -> np.ndarray:
    return 1.0 / (q * np.power(1j * w, n))


def _rq_z(r: float, q: float, n: float, w: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 / r + q * np.power(1j * w, n))


def _wo_z(rw: float, tau: float, w: np.ndarray) -> np.ndarray:
    # finite-length blocking/open Warburg: Rw*coth(sqrt(jw*tau))/sqrt(jw*tau)
    s = np.sqrt(1j * w * tau)
    small = np.abs(s) < 1e-15
    s = np.where(small, 1e-15 + 0j, s)
    return rw / (s * np.tanh(s))


def _decode_params(model: str, p: np.ndarray) -> dict[str, float]:
    e = lambda x: float(np.exp(np.clip(x, -60, 60)))
    if model == "L-Rs-CPE":
        return {"Rs_ohm": e(p[0]), "L_H": e(p[1]), "Q1_F_s_n_minus1": e(p[2]), "n1": _n_from_raw(p[3])}
    if model == "L-Rs-(R||CPE)":
        return {"Rs_ohm": e(p[0]), "L_H": e(p[1]), "R1_ohm": e(p[2]), "Q1_F_s_n_minus1": e(p[3]), "n1": _n_from_raw(p[4])}
    if model == "L-Rs-CPE-Wo":
        return {"Rs_ohm": e(p[0]), "L_H": e(p[1]), "Q1_F_s_n_minus1": e(p[2]), "n1": _n_from_raw(p[3]), "Rw_ohm": e(p[4]), "tau_diff_s": e(p[5])}
    return {"Rs_ohm": e(p[0]), "L_H": e(p[1]), "R1_ohm": e(p[2]), "Q1_F_s_n_minus1": e(p[3]), "n1": _n_from_raw(p[4]), "R2_ohm": e(p[5]), "Q2_F_s_n_minus1": e(p[6]), "n2": _n_from_raw(p[7])}


def _model_z(model: str, p: np.ndarray, w: np.ndarray) -> np.ndarray:
    d = _decode_params(model, p)
    z = d["Rs_ohm"] + 1j * w * d["L_H"]
    if model == "L-Rs-CPE":
        return z + _cpe_z(d["Q1_F_s_n_minus1"], d["n1"], w)
    if model == "L-Rs-(R||CPE)":
        return z + _rq_z(d["R1_ohm"], d["Q1_F_s_n_minus1"], d["n1"], w)
    if model == "L-Rs-CPE-Wo":
        return z + _cpe_z(d["Q1_F_s_n_minus1"], d["n1"], w) + _wo_z(d["Rw_ohm"], d["tau_diff_s"], w)
    return z + _rq_z(d["R1_ohm"], d["Q1_F_s_n_minus1"], d["n1"], w) + _rq_z(d["R2_ohm"], d["Q2_F_s_n_minus1"], d["n2"], w)


def _initial(model: str, f: np.ndarray, z: np.ndarray) -> np.ndarray:
    w = 2 * np.pi * f
    rs = max(float(np.nanpercentile(z.real, 5)), 1e-3)
    pos = z.imag > 0
    L = float(np.nanmedian(z.imag[pos] / w[pos])) if np.any(pos) else 1e-8
    L = max(abs(L), 1e-10)
    low = int(np.argmin(f))
    q = 1.0 / max(abs(z[low]) * w[low], 1e-12)
    q = min(max(q, 1e-12), 1e1)
    rspan = max(float(np.nanmax(z.real) - rs), 10.0)
    if model == "L-Rs-CPE":
        return np.array([math.log(rs), math.log(L), math.log(q), _raw_from_n(0.88)])
    if model == "L-Rs-(R||CPE)":
        return np.array([math.log(rs), math.log(L), math.log(max(rspan * 10, 100.0)), math.log(q), _raw_from_n(0.88)])
    if model == "L-Rs-CPE-Wo":
        return np.array([math.log(rs), math.log(L), math.log(q), _raw_from_n(0.90), math.log(max(rspan * 0.2, 10.0)), math.log(0.5)])
    return np.array([math.log(rs), math.log(L), math.log(max(rspan * 0.3, 10.0)), math.log(q * 3), _raw_from_n(0.92), math.log(max(rspan, 100.0)), math.log(max(q / 5, 1e-12)), _raw_from_n(0.72)])


def _weighted_residual(zm: np.ndarray, zo: np.ndarray) -> np.ndarray:
    scale = np.maximum(np.abs(zo), max(float(np.nanmedian(np.abs(zo))) * 0.03, 1e-12))
    r = (zm - zo) / scale
    return np.r_[r.real, r.imag]


def _covariance_diagnostics(res: Any) -> tuple[float | None, float | None]:
    try:
        j = np.asarray(res.jac, float)
        cond = float(np.linalg.cond(j.T @ j))
        n, k = j.shape
        if n <= k:
            return cond, None
        rss = float(np.sum(np.asarray(res.fun, float) ** 2))
        cov = np.linalg.pinv(j.T @ j) * rss / max(n - k, 1)
        se = np.sqrt(np.maximum(np.diag(cov), 0))
        # For log-parameters the transformed-space SE is an approximate relative SE.
        max_rel_proxy = float(np.nanmax(se)) if len(se) else None
        return cond, max_rel_proxy
    except Exception:
        return None, None


def _parameter_bounds(model: str) -> tuple[np.ndarray, np.ndarray]:
    # Wide but finite physical/numerical bounds. These prevent a lower-AIC circuit
    # from winning solely by sending a resistance or time constant to infinity.
    lr=(math.log(1e-3), math.log(1e10)); ll=(math.log(1e-12), math.log(1.0)); lq=(math.log(1e-14), math.log(1.0)); lw=(math.log(1e-3), math.log(1e10)); lt=(math.log(1e-8), math.log(1e8)); nb=(-8.0,8.0)
    if model == "L-Rs-CPE":
        lo=[lr[0],ll[0],lq[0],nb[0]]; hi=[lr[1],ll[1],lq[1],nb[1]]
    elif model == "L-Rs-(R||CPE)":
        lo=[lr[0],ll[0],lr[0],lq[0],nb[0]]; hi=[lr[1],ll[1],lr[1],lq[1],nb[1]]
    elif model == "L-Rs-CPE-Wo":
        lo=[lr[0],ll[0],lq[0],nb[0],lw[0],lt[0]]; hi=[lr[1],ll[1],lq[1],nb[1],lw[1],lt[1]]
    else:
        lo=[lr[0],ll[0],lr[0],lq[0],nb[0],lr[0],lq[0],nb[0]]; hi=[lr[1],ll[1],lr[1],lq[1],nb[1],lr[1],lq[1],nb[1]]
    return np.asarray(lo,float),np.asarray(hi,float)


def _fit_candidate(f: np.ndarray, z: np.ndarray, model: str, warm: np.ndarray | None = None) -> dict[str, Any]:
    try:
        from scipy.optimize import least_squares
    except Exception as exc:
        return {"model": model, "status": "scipy_unavailable", "message": str(exc)}
    order = np.argsort(f)[::-1]
    f = np.asarray(f[order], float); z = np.asarray(z[order], complex); w = 2 * np.pi * f
    x0 = np.asarray(warm, float) if warm is not None and len(warm) == len(_initial(model, f, z)) else _initial(model, f, z)
    try:
        def fun(p: np.ndarray) -> np.ndarray:
            return _weighted_residual(_model_z(model, p, w), z)
        lo,hi=_parameter_bounds(model); x0=np.clip(x0,lo+1e-8,hi-1e-8)
        res = least_squares(fun, x0, bounds=(lo,hi), max_nfev=2200, x_scale="jac", loss="linear")
        rr = np.asarray(res.fun, float)
        rss = float(np.sum(rr * rr)); nobs = int(rr.size); k = int(len(res.x))
        rmse = float(np.sqrt(np.mean(rr * rr)))
        aic = nobs * math.log(max(rss / max(nobs, 1), 1e-300)) + 2 * k
        aicc = aic + (2 * k * (k + 1)) / max(nobs - k - 1, 1)
        bic = nobs * math.log(max(rss / max(nobs, 1), 1e-300)) + k * math.log(max(nobs, 2))
        d = _decode_params(model, res.x)
        cond, rel = _covariance_diagnostics(res)
        flags: list[str] = []
        if cond is not None and cond > QUALITY["jacobian_condition_review"]: flags.append("ill_conditioned")
        if rel is not None and rel > 1.0: flags.append("large_parameter_uncertainty")
        if d.get("n1", 0.5) > 0.995 or d.get("n1", 0.5) < 0.305: flags.append("n1_near_boundary")
        if "n2" in d and (d["n2"] > 0.995 or d["n2"] < 0.305): flags.append("n2_near_boundary")
        if d.get("R1_ohm", 0) > 1e9: flags.append("R1_effectively_open")
        if d.get("R2_ohm", 0) > 1e9: flags.append("R2_effectively_open")
        out = {"model": model, "status": "ok" if res.success else "not_converged", "rmse": rmse, "aicc": float(aicc), "bic": float(bic), "jacobian_condition": cond, "max_transformed_se_proxy": rel, "flags": flags, "optimizer_message": str(res.message), "_p": res.x.tolist()}
        out.update(d)
        if "R1_ohm" in d:
            q=d.get("Q1_F_s_n_minus1"); n=d.get("n1"); r=d.get("R1_ohm")
            try:
                out["tau1_s"] = float((r*q)**(1/n)); out["f1_Hz"] = 1/(2*np.pi*out["tau1_s"])
                out["Ceff1_HsuMansfeld_F"] = float((q*(r**(1-n)))**(1/n))
            except Exception: pass
        if "R2_ohm" in d:
            q=d.get("Q2_F_s_n_minus1"); n=d.get("n2"); r=d.get("R2_ohm")
            try:
                out["tau2_s"] = float((r*q)**(1/n)); out["f2_Hz"] = 1/(2*np.pi*out["tau2_s"])
                out["Ceff2_HsuMansfeld_F"] = float((q*(r**(1-n)))**(1/n))
            except Exception: pass
        return out
    except Exception as exc:
        return {"model": model, "status": "failed", "message": str(exc)}


def _kk_style_screen(f: np.ndarray, z: np.ndarray) -> dict[str, Any]:
    """Kramers-Kronig validation with an official implementation when available.

    The installed FaradaIQ environment includes ``impedance`` in requirements_all;
    its Lin-KK result is preferred. A local causal-basis screen is a fallback only
    and is labelled as such, never as a certified Lin-KK test.
    """
    f=np.asarray(f,float); z=np.asarray(z,complex)
    order=np.argsort(f)[::-1]; f=f[order]; z=z[order]; w=2*np.pi*f
    try:
        from impedance.validation import linKK
        result=linKK(f,z,c=0.85,max_M=100,fit_type="complex",add_cap=True)
        M,mu,zfit,res_r,res_i=result[:5]
        rr=np.r_[np.asarray(res_r,float),np.asarray(res_i,float)]
        # impedance.py residuals are normalized residuals.
        rmse=float(np.sqrt(np.mean(rr*rr)))
        status="PASS" if rmse<=QUALITY["kk_style_rmse_pass"] else ("REVIEW" if rmse<=QUALITY["kk_style_rmse_review"] else "FAIL")
        return {"kk_method":"impedance.py Lin-KK","kk_available":True,"kk_style_rmse":rmse,"kk_style_status":status,"linKK_M":int(M),"linKK_mu":float(mu)}
    except Exception as exc:
        official_error=str(exc)
    # Fallback: joint Re/Im fit to a signed Voigt basis. No ridge is imposed,
    # because a fixed ridge strongly biases spectra with a large blocking tail.
    taumin=1/(2*np.pi*np.nanmax(f))*0.1; taumax=1/(2*np.pi*np.nanmin(f))*10
    taus=np.geomspace(taumin,taumax,24)
    cols=[np.ones_like(w,dtype=complex),1j*w,1/(1j*w)]
    cols.extend([1/(1+1j*w*t) for t in taus])
    A=np.column_stack(cols)
    scale=np.maximum(np.abs(z),max(float(np.nanmedian(np.abs(z)))*0.03,1e-12))
    Ar=np.vstack([(A/scale[:,None]).real,(A/scale[:,None]).imag]); b=np.r_[(z/scale).real,(z/scale).imag]
    x=np.linalg.lstsq(Ar,b,rcond=1e-12)[0]; zh=A@x
    rr=(zh-z)/scale; rmse=float(np.sqrt(np.mean(np.r_[rr.real,rr.imag]**2)))
    status="PASS" if rmse<=QUALITY["kk_style_rmse_pass"] else ("REVIEW" if rmse<=QUALITY["kk_style_rmse_review"] else "FAIL")
    return {"kk_method":"causal Voigt fallback (not certified Lin-KK)","kk_available":False,"kk_warning":official_error,"kk_style_rmse":rmse,"kk_style_status":status,"voigt_terms":len(taus)}

def _select_models(f: np.ndarray, z: np.ndarray) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Rank circuits statistically, then select the simplest identifiable supported model.

    AICc is retained for transparency, but an unidentifiable lower-AIC model is not
    allowed to donate mechanistic parameters to the final trend plots.
    """
    models=("L-Rs-CPE","L-Rs-(R||CPE)","L-Rs-CPE-Wo","L-Rs-(R1||CPE1)-(R2||CPE2)")
    fits=[_fit_candidate(f,z,m) for m in models]
    good=[x for x in fits if x.get("status") in {"ok","not_converged"} and _finite(x.get("aicc")) is not None]
    if not good: return fits[0],fits
    good.sort(key=lambda x: float(x["aicc"]))
    amin=float(good[0]["aicc"]); weights=[]
    for g in good:
        da=float(g["aicc"])-amin; g["delta_aicc"]=da; weights.append(math.exp(-0.5*min(da,140)))
    sw=sum(weights) or 1
    for g,wgt in zip(good,weights): g["akaike_weight"]=wgt/sw
    stat_best=good[0]
    # Identifiability/physical screening. Boundary-hit and effectively-open finite-R
    # models are kept in the candidate table but cannot be selected for parameter trends.
    def usable(g: Mapping[str,Any]) -> bool:
        flags=set(g.get("flags") or [])
        cond=_finite(g.get("jacobian_condition")); rel=_finite(g.get("max_transformed_se_proxy")); rmse=_finite(g.get("rmse"))
        if rmse is None or rmse>QUALITY["fit_rmse_review"]: return False
        if cond is None or cond>QUALITY["jacobian_condition_review"]: return False
        if rel is not None and rel>1.0: return False
        if any(x in flags for x in {"n1_near_boundary","n2_near_boundary","R1_effectively_open","R2_effectively_open","ill_conditioned","large_parameter_uncertainty"}): return False
        return True
    interpretable=[g for g in good if usable(g)]
    selected=min(interpretable,key=lambda x:float(x["aicc"])) if interpretable else stat_best
    second=good[1] if len(good)>1 else None
    selected=selected.copy()
    selected["statistical_best_model"]=stat_best.get("model")
    selected["statistical_best_aicc"]=stat_best.get("aicc")
    selected["statistical_best_identifiable"]=usable(stat_best)
    selected["selected_by"]="lowest_AICc_among_identifiable_models" if interpretable else "statistical_best_no_identifiable_candidate"
    selected["delta_aicc_to_statistical_best"]=float(selected["aicc"])-float(stat_best["aicc"])
    selected["delta_aicc_to_second"]=(float(second["aicc"])-float(stat_best["aicc"])) if second else None
    flags=list(selected.get("flags") or [])
    if selected.get("rmse",1)>QUALITY["fit_rmse_review"]: flags.append("poor_fit")
    elif selected.get("rmse",1)>QUALITY["fit_rmse_strong"]: flags.append("fit_review")
    if selected.get("model") != stat_best.get("model"):
        flags.append("lower_AICc_model_rejected_for_identifiability")
    elif second and selected.get("delta_aicc_to_second",0)<2: flags.append("model_nonunique")
    elif second and selected.get("delta_aicc_to_second",0)<QUALITY["delta_aicc_strong"]: flags.append("model_evidence_moderate")
    selected["flags"]=sorted(set(flags))
    selected["support_status"]="SUPPORTED" if not [x for x in flags if x not in {"lower_AICc_model_rejected_for_identifiability"}] and selected.get("rmse",1)<=QUALITY["fit_rmse_strong"] else ("REVIEW" if selected.get("rmse",1)<=QUALITY["fit_rmse_review"] else "UNSUPPORTED")
    return selected,fits

def _capacitance_from_z(f: np.ndarray, z: np.ndarray, rs: float, L: float) -> tuple[np.ndarray,np.ndarray,np.ndarray]:
    w=2*np.pi*np.asarray(f,float)
    zi=np.asarray(z,complex)-float(rs)-1j*w*float(L)
    tiny=np.abs(zi)<1e-30; zi=np.where(tiny,1e-30+0j,zi)
    y=1/zi
    c_storage=np.imag(y)/w
    c_loss=np.real(y)/w
    return c_storage,c_loss,np.abs(c_loss/np.where(np.abs(c_storage)>1e-30,c_storage,np.nan))


def _nearest_frequency_rows(freqs: np.ndarray) -> list[float]:
    targets=np.asarray([1,3.16,10,31.6,100,316,1e3,3.16e3,1e4,3.16e4,1e5],float)
    out=[]
    for t in targets:
        v=float(freqs[np.argmin(np.abs(np.log10(freqs)-math.log10(t)))])
        if not any(abs(math.log10(v)-math.log10(x))<1e-7 for x in out): out.append(v)
    return out


def _quadratic_minimum(x: np.ndarray, y: np.ndarray) -> tuple[float|None,float|None,str]:
    ok=np.isfinite(x)&np.isfinite(y); x=x[ok]; y=y[ok]
    if len(x)<7:return None,None,"insufficient_points"
    order=np.argsort(x); x=x[order]; y=y[order]
    # light moving median to reduce point noise without inventing a new capacitance curve
    ys=np.array([np.nanmedian(y[max(0,i-1):min(len(y),i+2)]) for i in range(len(y))])
    i=int(np.nanargmin(ys))
    if i<2 or i>len(x)-3:return float(x[i]),float(y[i]),"edge_minimum_review"
    sl=slice(i-2,i+3)
    p=np.polyfit(x[sl],ys[sl],2)
    if p[0]<=0:return float(x[i]),float(y[i]),"nonconvex_review"
    xv=float(-p[1]/(2*p[0])); yv=float(np.polyval(p,xv))
    if xv<x[0] or xv>x[-1]:return float(x[i]),float(y[i]),"quadratic_outside_range_review"
    return xv,yv,"candidate_capacitance_minimum"


def _mott_diagnostic(pot: np.ndarray, cap: np.ndarray, freq: float) -> dict[str,Any]:
    ok=np.isfinite(pot)&np.isfinite(cap)&(cap>0); x=np.asarray(pot[ok],float); c=np.asarray(cap[ok],float)
    if len(x)<15:return {"frequency_Hz":freq,"status":"REJECT","reason":"insufficient_positive_capacitance"}
    order=np.argsort(x); x=x[order]; y=1/(c[order]**2)
    best=None; min_n=max(12,int(math.ceil(len(x)*0.22)))
    for n in range(min_n,min(len(x),40)+1):
        for s in range(len(x)-n+1):
            xs=x[s:s+n]; ys=y[s:s+n]; span=float(xs[-1]-xs[0])
            if span<QUALITY["mott_min_window_span_V"]: continue
            p=np.polyfit(xs,ys,1); yh=np.polyval(p,xs)
            ssr=float(np.sum((ys-yh)**2)); sst=float(np.sum((ys-np.mean(ys))**2)); r2=1-ssr/sst if sst>0 else 0
            score=r2+0.001*n
            if best is None or score>best[0]: best=(score,r2,p[0],p[1],xs[0],xs[-1],n)
    if best is None:return {"frequency_Hz":freq,"status":"REJECT","reason":"no_window_with_required_span"}
    _,r2,slope,intercept,vmin,vmax,n=best; vfb=(-intercept/slope) if slope else np.nan
    reasons=[]
    if r2<QUALITY["mott_r2_min"]: reasons.append("R2_below_threshold")
    if not (float(np.min(x))-QUALITY["mott_vfb_margin_V"] <= vfb <= float(np.max(x))+QUALITY["mott_vfb_margin_V"]): reasons.append("flatband_far_outside_measured_range")
    status="PASS_CANDIDATE" if not reasons else "REJECT"
    return {"frequency_Hz":freq,"status":status,"reason":";".join(reasons) if reasons else "candidate_only_requires_semiconductor_context_area_epsilon","window_V_min":vmin,"window_V_max":vmax,"n_points":n,"slope_F_minus2_per_V":slope,"intercept_F_minus2":intercept,"r_squared":r2,"flatband_potential_V":vfb,"slope_type":"n-type" if slope>0 else "p-type"}


def analyze_mott_grid_advanced(ms: Mapping[str,Any]) -> dict[str,Any]:
    arr=np.asarray(ms.get("array"),float); cols=list(ms.get("columns") or []); ns=int(ms.get("n_potential_steps") or 0); nf=int(ms.get("n_frequencies") or 0)
    needed={"potential_V","frequency_Hz","Zreal_ohm","Zimag_ohm"}
    if arr.ndim!=2 or ns<=0 or nf<=0 or len(arr)!=ns*nf or not needed.issubset(cols): return {"status":"invalid_grid"}
    grid=arr.reshape(ns,nf,arr.shape[1]); ci={c:cols.index(c) for c in needed}
    srcctx=_source_context(ms)
    area=_finite(os.environ.get("FARADAIQ_ELECTRODE_AREA_CM2")) or _finite(srcctx.get("electrode_area_cm2")); eps=_finite(os.environ.get("FARADAIQ_SEMICONDUCTOR_EPS_R")); cdl_u=_finite(os.environ.get("FARADAIQ_CDL_UF_CM2")); electrode_class=str(os.environ.get("FARADAIQ_ELECTRODE_CLASS") or srcctx.get("electrode_class") or "unspecified")
    quantum_supported=bool(area and cdl_u and re.search(r"graphene|monolayer|2d|low[- ]dos",electrode_class,re.I))
    summary=[]; candidates=[]; cap_long=[]; cap_all=[]; pzc=[]; mott=[]; fit_rows=[]
    per_step_caps=[]
    for si,block in enumerate(grid,1):
        v=float(np.nanmedian(block[:,ci["potential_V"]])); f=np.asarray(block[:,ci["frequency_Hz"]],float); z=np.asarray(block[:,ci["Zreal_ohm"]],float)+1j*np.asarray(block[:,ci["Zimag_ohm"]],float)
        ok=np.isfinite(f)&np.isfinite(z.real)&np.isfinite(z.imag)&(f>0); f=f[ok]; z=z[ok]
        kk=_kk_style_screen(f,z); best,fits=_select_models(f,z)
        rs=float(best.get("Rs_ohm") or kk.get("Rs_voigt_ohm") or np.nanmin(z.real)); L=float(best.get("L_H") or kk.get("L_voigt_H") or 0)
        cs,cl,td=_capacitance_from_z(f,z,rs,L)
        for j,ff in enumerate(f):
            c_area=(float(cs[j])/area*1e6) if area else None
            cl_area=(float(cl[j])/area*1e6) if area else None
            cq_area=None
            if quantum_supported and c_area is not None and cdl_u and c_area>0 and c_area < cdl_u:
                denom=(1.0/c_area)-(1.0/cdl_u)
                if denom>0: cq_area=1.0/denom
            cap_all.append([si,v,float(ff),float(cs[j]),float(cl[j]),float(td[j]) if np.isfinite(td[j]) else None,c_area,cl_area,cq_area])
        selected=_nearest_frequency_rows(f)
        cmap={}
        for sf in selected:
            j=int(np.argmin(np.abs(np.log10(f)-math.log10(sf)))); cmap[float(f[j])]=(float(cs[j]),float(cl[j]),float(td[j]) if np.isfinite(td[j]) else None)
            c_area=(float(cs[j])/area*1e6) if area else None
            cl_area=(float(cl[j])/area*1e6) if area else None
            cq_area=None
            if quantum_supported and c_area is not None and cdl_u and c_area>0 and c_area < cdl_u:
                denom=(1.0/c_area)-(1.0/cdl_u)
                if denom>0: cq_area=1.0/denom
            cap_long.append([si,v,float(f[j]),float(cs[j]),float(cl[j]),float(td[j]) if np.isfinite(td[j]) else None,c_area,cl_area,cq_area])
        per_step_caps.append((v,cmap))
        flags=list(best.get("flags") or [])
        if kk["kk_style_status"]=="FAIL": flags.append("KK_style_consistency_fail")
        elif kk["kk_style_status"]=="REVIEW": flags.append("KK_style_consistency_review")
        support="SUPPORTED" if not flags and best.get("support_status")=="SUPPORTED" else ("REVIEW" if best.get("support_status")!="UNSUPPORTED" else "UNSUPPORTED")
        # First inductive-to-capacitive zero crossing when descending frequency.
        cross=None
        oo=np.argsort(f)[::-1]; ff=f[oo]; zz=z.imag[oo]
        for jj in range(len(ff)-1):
            if zz[jj]>=0 and zz[jj+1]<0:
                x1,x2=np.log10(ff[jj]),np.log10(ff[jj+1]); y1,y2=zz[jj],zz[jj+1]
                cross=float(10**(x1+(0-y1)*(x2-x1)/(y2-y1))) if y2!=y1 else float(ff[jj+1]); break
        if best.get("model") in {"L-Rs-CPE","L-Rs-CPE-Wo"} or "R1_effectively_open" in flags:
            regime="blocking_or_near-blocking; finite Rct not identified"
        elif best.get("R1_ohm") is not None:
            regime="finite parallel resistance fitted; assign Rct only with independent faradaic context"
        else:
            regime="phenomenological interface model"
        summary.append([si,v,kk.get("kk_method"),kk.get("kk_available"),kk.get("kk_style_status"),kk.get("kk_style_rmse"),int(np.sum(z.imag>0)),cross,best.get("model"),best.get("statistical_best_model"),best.get("selected_by"),best.get("delta_aicc_to_statistical_best"),best.get("rmse"),best.get("aicc"),best.get("bic"),best.get("delta_aicc_to_second"),best.get("akaike_weight"),best.get("jacobian_condition"),best.get("max_transformed_se_proxy"),support,regime,";".join(sorted(set(flags))),best.get("Rs_ohm"),best.get("L_H"),best.get("R1_ohm"),best.get("Q1_F_s_n_minus1"),best.get("n1"),best.get("Ceff1_HsuMansfeld_F"),best.get("tau1_s"),best.get("f1_Hz"),best.get("R2_ohm"),best.get("Q2_F_s_n_minus1"),best.get("n2"),best.get("Ceff2_HsuMansfeld_F"),best.get("tau2_s"),best.get("f2_Hz"),best.get("Rw_ohm"),best.get("tau_diff_s")])
        # Preserve measured-vs-fitted points for every bias.  This is the audit trail
        # behind Nyquist/Bode fit overlays and residual inspection; the chosen model is
        # the identifiable/recommended model, not merely the lowest-AICc candidate.
        try:
            pp=np.asarray(best.get("_p"),float); wfit=2*np.pi*f
            zfit=_model_z(str(best.get("model")),pp,wfit)
            scale=np.maximum(np.abs(z),max(float(np.nanmedian(np.abs(z)))*0.03,1e-12))
            resid=(zfit-z)/scale
            for ff,zz,zp,rr in zip(f,z,zfit,resid):
                fit_rows.append([si,v,float(ff),float(zz.real),float(zz.imag),float(-zz.imag),float(zp.real),float(zp.imag),float(-zp.imag),float(abs(zz)),float(abs(zp)),float(np.degrees(np.angle(zz))),float(np.degrees(np.angle(zp))),float(rr.real),float(rr.imag),best.get("model"),support])
        except Exception:
            pass
        for fit in fits:
            candidates.append([si,v,fit.get("model"),fit.get("status"),fit.get("rmse"),fit.get("aicc"),fit.get("bic"),fit.get("delta_aicc"),fit.get("akaike_weight"),fit.get("jacobian_condition"),fit.get("max_transformed_se_proxy"),";".join(fit.get("flags") or []),fit.get("Rs_ohm"),fit.get("L_H"),fit.get("R1_ohm"),fit.get("Q1_F_s_n_minus1"),fit.get("n1"),fit.get("R2_ohm"),fit.get("Q2_F_s_n_minus1"),fit.get("n2"),fit.get("Rw_ohm"),fit.get("tau_diff_s")])
    # Potential-dependent capacitance minimum / apparent PZC by selected frequency.
    all_freq=sorted({fr for _,cm in per_step_caps for fr in cm})
    for fr in all_freq:
        vv=[]; cc=[]
        for v,cm in per_step_caps:
            if fr in cm: vv.append(v); cc.append(cm[fr][0])
        xv,yv,status=_quadratic_minimum(np.asarray(vv,float),np.asarray(cc,float))
        pzc.append([fr,xv,yv,area and yv/area*1e6 if yv is not None else None,status,"apparent capacitance minimum; PZC assignment requires interface-specific validation"])
        mott.append(_mott_diagnostic(np.asarray(vv,float),np.asarray(cc,float),fr))
    # Cross-frequency M-S consistency: passing one isolated frequency is not enough.
    pass_rows=[r for r in mott if r.get("status")=="PASS_CANDIDATE"]
    if pass_rows:
        signs=[1 if r.get("slope_F_minus2_per_V",0)>0 else -1 for r in pass_rows]
        coherent=max(signs.count(1),signs.count(-1))/len(signs) >= 0.8
        for r in pass_rows:
            if not coherent:
                r["status"]="REJECT"; r["reason"]="slope_sign_not_consistent_across_frequency"
    interior=[float(row[1]) for row in pzc if row[4]=="candidate_capacitance_minimum" and row[1] is not None]
    pzc_consensus=None; pzc_std=None
    if len(interior)>=3:
        pzc_consensus=float(np.median(interior)); pzc_std=float(np.std(interior,ddof=1)) if len(interior)>1 else 0.0
    pzc_supported=bool(pzc_consensus is not None and pzc_std is not None and pzc_std<=QUALITY["apparent_pzc_stability_std_V"])
    interpretation={
        "electrode_class":electrode_class,"electrode_area_cm2":area,"reference_electrode":srcctx.get("reference_electrode"),"assumed_Cdl_uF_cm2":cdl_u,"semiconductor_eps_r":eps,
        "quantum_capacitance_status":"conditional_series_decomposition_enabled" if quantum_supported else "not_identifiable_from_EIS_alone",
        "quantum_capacitance_reason":"Requires low-DOS electrode context plus independent area and Cdl/DOS model; total interfacial capacitance alone cannot uniquely separate Cdl and Cq.",
        "mott_schottky_status":"candidate_regime_present" if any(r.get("status")=="PASS_CANDIDATE" for r in mott) else "no_frequency_passed_strict_Mott_Schottky_checks",
        "pzc_status":"frequency_stable_candidate" if pzc_supported else "no_frequency_stable_PZC_assignment",
        "apparent_pzc_consensus_V":pzc_consensus,
        "apparent_pzc_std_V":pzc_std,
    }
    return {"status":"ok","summary":summary,"candidates":candidates,"cap_long":cap_long,"cap_all":cap_all,"pzc":pzc,"mott":mott,"fit_rows":fit_rows,"interpretation":interpretation}


SUMMARY_COLUMNS=["potential_step_index","potential_V","kk_method","certified_linKK_available","kk_style_status","kk_style_rmse","n_inductive_Zimag_positive_points","inductive_to_capacitive_crossover_Hz","recommended_model","statistical_best_model","model_selection_basis","delta_aicc_recommended_vs_statistical_best","fit_rmse","aicc","bic","delta_aicc_to_second","akaike_weight","jacobian_condition","max_transformed_se_proxy","support_status","interface_regime","quality_flags","Rs_ohm","L_H","R1_or_Rp_ohm","Q1_F_s_n_minus1","n1","Ceff1_HsuMansfeld_F","tau1_s","f1_Hz","R2_ohm","Q2_F_s_n_minus1","n2","Ceff2_HsuMansfeld_F","tau2_s","f2_Hz","Rw_ohm","tau_diff_s"]
CANDIDATE_COLUMNS=["potential_step_index","potential_V","model","fit_status","fit_rmse","aicc","bic","delta_aicc","akaike_weight","jacobian_condition","max_transformed_se_proxy","quality_flags","Rs_ohm","L_H","R1_ohm","Q1_F_s_n_minus1","n1","R2_ohm","Q2_F_s_n_minus1","n2","Rw_ohm","tau_diff_s"]
CAP_COLUMNS=["potential_step_index","potential_V","frequency_Hz","C_storage_F","C_loss_F","loss_tangent_abs","C_storage_uF_cm2_if_area","C_loss_uF_cm2_if_area","conditional_Cq_uF_cm2_if_low_DOS_and_Cdl_supplied"]
PZC_COLUMNS=["frequency_Hz","apparent_capacitance_minimum_potential_V","minimum_C_storage_F","minimum_C_storage_uF_cm2_if_area","status","interpretation_boundary"]
MOTT_COLUMNS=["frequency_Hz","status","reason","window_V_min","window_V_max","n_points","slope_F_minus2_per_V","intercept_F_minus2","r_squared","flatband_potential_V","slope_type"]
VALIDATION_COMPACT_COLUMNS=["potential_V","kk_method","certified_linKK_available","kk_style_status","kk_style_rmse","n_inductive_Zimag_positive_points","inductive_to_capacitive_crossover_Hz","recommended_model","statistical_best_model","fit_rmse","delta_aicc_recommended_vs_statistical_best","support_status"]
PARAMETER_TREND_COLUMNS=["potential_V","Rs_ohm","L_H","R1_or_Rp_ohm","Q1_F_s_n_minus1","Ceff1_HsuMansfeld_F","Ceff1_uF_cm2_if_area","n1","tau1_s","f1_Hz","Rw_ohm","tau_diff_s","recommended_model","support_status","interface_regime"]
FIT_COLUMNS=["potential_step_index","potential_V","frequency_Hz","Zreal_measured_ohm","Zimag_measured_ohm","minus_Zimag_measured_ohm","Zreal_fit_ohm","Zimag_fit_ohm","minus_Zimag_fit_ohm","Zmod_measured_ohm","Zmod_fit_ohm","phase_measured_deg","phase_fit_deg","normalized_residual_real","normalized_residual_imag","recommended_model","support_status"]


def _counts(values: Sequence[Any]) -> dict[str,int]:
    out: dict[str,int]={}
    for x in values:
        k=str(x or "unspecified"); out[k]=out.get(k,0)+1
    return out


def _median(rows: Sequence[Mapping[str,Any]], key: str, scale: float=1.0) -> float | None:
    vals=[float(v)*scale for r in rows if (v:=_finite(r.get(key))) is not None]
    return float(np.median(vals)) if vals else None


def _scientific_summary(r: Mapping[str,Any]) -> dict[str,Any]:
    rows=[dict(zip(SUMMARY_COLUMNS,row)) for row in (r.get("summary") or [])]
    interp=dict(r.get("interpretation") or {})
    finite=[x for x in rows if _finite(x.get("R1_or_Rp_ohm")) is not None and str(x.get("recommended_model"))=="L-Rs-(R||CPE)"]
    pots=[float(x["potential_V"]) for x in finite if _finite(x.get("potential_V")) is not None]
    model_counts=_counts([x.get("recommended_model") for x in rows])
    n=len(rows)
    blocking=sum(model_counts.get(k,0) for k in ("L-Rs-CPE","L-Rs-CPE-Wo"))
    finite_n=len(finite)
    rs=_median(rows,"Rs_ohm"); lu=_median(rows,"L_H",1e6); fc=_median(rows,"inductive_to_capacitive_crossover_Hz")
    mott_status=str(interp.get("mott_schottky_status") or "not_evaluated")
    pzc_status=str(interp.get("pzc_status") or "not_evaluated")
    conclusions=[]
    if n:
        conclusions.append(f"{blocking}/{n} spectra are best represented by a blocking or near-blocking distributed-capacitance model after identifiability screening.")
    if rs is not None and lu is not None:
        msg=f"Median series resistance is {rs:.3g} ohm and fitted series inductance is {lu:.3g} uH"
        if fc is not None: msg+=f", with an inductive-to-capacitive crossover near {fc/1e3:.3g} kHz"
        conclusions.append(msg+".")
    if finite_n:
        conclusions.append(f"A finite parallel-resistance candidate is identifiable at {finite_n}/{n} potentials; it is reported as polarization/interface resistance unless independent faradaic evidence supports an Rct assignment.")
    if mott_status!="candidate_regime_present": conclusions.append("Strict Mott-Schottky checks do not support reporting a flat-band potential or carrier density from this dataset.")
    if pzc_status!="frequency_stable_candidate": conclusions.append("The capacitance minimum is not frequency-stable enough for a defensible PZC assignment.")
    limitations=[
        "Equivalent-circuit elements are phenomenological unless supported by independent chemistry and perturbation evidence; a lower-AICc but unidentifiable model is not used for mechanistic parameters.",
        "Quantum capacitance is not separately identifiable from total EIS capacitance without an independent low-DOS electrode model and Cdl/DOS context.",
        "DRT peak positions and amplitudes support time-scale analysis but are not automatically assigned to a specific electrochemical mechanism.",
    ]
    return {
        "schema":"faradaiq-advanced-potential-eis-summary-v316",
        "potential_steps":n,
        "model_counts":model_counts,
        "statistical_best_model_counts":_counts([x.get("statistical_best_model") for x in rows]),
        "support_counts":_counts([x.get("support_status") for x in rows]),
        "kk_status_counts":_counts([x.get("kk_style_status") for x in rows]),
        "median_kk_normalized_rmse":_median(rows,"kk_style_rmse"),
        "median_recommended_fit_normalized_rmse":_median(rows,"fit_rmse"),
        "median_Rs_ohm":rs,
        "median_L_uH":lu,
        "median_inductive_capacitive_crossover_Hz":fc,
        "finite_parallel_R_candidate_count":finite_n,
        "finite_parallel_R_potential_min_V":min(pots) if pots else None,
        "finite_parallel_R_potential_max_V":max(pots) if pots else None,
        "finite_parallel_R_median_ohm":_median(finite,"R1_or_Rp_ohm"),
        "mott_schottky_status":mott_status,
        "pzc_status":pzc_status,
        "quantum_capacitance_claim":"not_reported_without_independent_low_DOS_and_Cdl_context",
        "electrode_area_cm2":interp.get("electrode_area_cm2"),
        "electrode_class":interp.get("electrode_class"),
        "reference_electrode":interp.get("reference_electrode"),
        "conclusion_bullets":conclusions,
        "limitation_bullets":limitations,
    }


def _augment_ppt_narrative(narrative: Mapping[str,Any], summary: Mapping[str,Any]) -> dict[str,Any]:
    out=dict(narrative or {})
    for key in ("executive_summary","conclusions","global_cautions"):
        out[key]=list(out.get(key) or [])
    conclusions=list(summary.get("conclusion_bullets") or [])
    cautions=list(summary.get("limitation_bullets") or [])
    # Always surface rejected high-risk interpretations in the deterministic narrative.
    mott=str(summary.get("mott_schottky_status") or "")
    pzc=str(summary.get("pzc_status") or "")
    if mott and mott!="candidate_regime_present": cautions.append("Mott-Schottky: strict cross-frequency checks do not support a reportable flat-band potential or carrier density.")
    if pzc and pzc!="frequency_stable_candidate": cautions.append("PZC: no frequency-stable capacitance minimum supports a defensible assignment.")
    if summary.get("quantum_capacitance_claim"):
        cautions.append("Quantum capacitance is not separately reported without independent low-DOS electrode and Cdl/DOS context.")
    def extend_unique(key: str, vals: Sequence[str]) -> None:
        seen={str(x) for x in out[key]}
        for x in vals:
            t=str(x).strip()
            if t and t not in seen: out[key].append(t); seen.add(t)
    extend_unique("executive_summary",conclusions[:3])
    extend_unique("conclusions",conclusions)
    extend_unique("global_cautions",cautions)
    # Keep the executive slide readable while retaining the complete caution set
    # in global_cautions for speaker notes, audit, and downstream reporting.
    executive_cautions: list[str] = []
    if mott and mott != "candidate_regime_present" or pzc and pzc != "frequency_stable_candidate":
        executive_cautions.append("Mott-Schottky and PZC are not reportable: cross-frequency validity/stability checks fail.")
    if summary.get("quantum_capacitance_claim"):
        executive_cautions.append("Quantum capacitance is not separable without independent low-DOS electrode and Cdl/DOS context.")
    if not executive_cautions:
        executive_cautions.append("Mechanistic assignments require fit identifiability plus independent electrochemical evidence.")
    out["executive_cautions"] = executive_cautions[:2]
    out["executive_cross_technique"] = [
        "Cross-technique claims require compatible point/condition mapping, acquisition context, and chronology."
    ]
    td=dict(out.get("technique_discussions") or {})
    td["EIS_CIRCUIT_MODEL"]={"summary":" ".join(conclusions[:3]),"cautions":cautions[:2]}
    td["MOTT_SCHOTTKY_DIAGNOSTIC"]={"summary":"Strict validity checks gate all flat-band/carrier-density claims.","cautions":[x for x in cautions if "Mott-Schottky" in x]}
    td["EIS_CAPACITANCE"]={"summary":"Complex capacitance and capacitance-minimum trends are reported with frequency dependence.","cautions":[x for x in cautions if "PZC" in x or "Quantum capacitance" in x]}
    out["technique_discussions"]=td
    sc=dict(out.get("slide_content") or {})
    n=int(summary.get("potential_steps") or 0); models=dict(summary.get("model_counts") or {})
    blocking=int(models.get("L-Rs-CPE",0))+int(models.get("L-Rs-CPE-Wo",0)); finite=int(summary.get("finite_parallel_R_candidate_count") or 0)
    rs=_finite(summary.get("median_Rs_ohm")); lu=_finite(summary.get("median_L_uH")); fc=_finite(summary.get("median_inductive_capacitive_crossover_Hz")); kkr=_finite(summary.get("median_kk_normalized_rmse")); frm=_finite(summary.get("median_recommended_fit_normalized_rmse"))
    rs_txt=f"{rs:.3g} ohm" if rs is not None else "not reportable"
    lu_txt=f"{lu:.3g} uH" if lu is not None else "not reportable"
    def put(sid: str, takeaway: str, notes: str) -> None:
        rec=dict(sc.get(sid) or {}); rec["takeaway"]=takeaway; rec["speaker_notes"]=notes; sc[sid]=rec
    put("technique-eis_circuit_model",
        f"{blocking}/{n} spectra favor a blocking/near-blocking distributed-capacitance model; finite parallel resistance is identifiable at only {finite}/{n} biases." if n else "Identifiability screening favors blocking/near-blocking behavior over indiscriminate finite-R assignment.",
        f"The recommended-model trend is selected after fit quality, parameter conditioning, uncertainty, and boundary checks. The statistical best AICc model can still be rejected when its parameters are not identifiable. Median Rs is {rs_txt} and median series L is {lu_txt}"+(f", with crossover near {fc/1e3:.3g} kHz" if fc else "")+". A finite parallel resistance is not automatically labeled Rct without independent evidence for a faradaic process. CPE-derived effective capacitance is an interfacial capacitance proxy and is not automatically Cdl without a validated physical interface model.")
    put("technique-eis_validation",
        f"Causal-consistency screening is strong (median normalized residual {kkr:.3f}) while the recommended circuit fit retains a median normalized complex residual of {frm:.3f}." if kkr is not None and frm is not None else "Validity and fit residuals are evaluated before mechanistic parameter reporting.",
        "Inspect Kramers-Kronig/Lin-KK status, complex residuals, high-frequency inductance, parameter conditioning, and model evidence together. Passing a consistency test supports the impedance data as a causal/stable response but does not identify a unique electrochemical mechanism.")
    put("technique-eis_fit_diagnostic",
        "Measured-versus-fitted spectra and complex residuals are retained for every potential, so parameter trends can be audited against the original EIS shape.",
        "Use the fit overlays to look for systematic frequency-localized residuals rather than judging a circuit only by a single scalar error. Residual structure, parameter bounds, and conditioning determine whether a circuit is suitable for parameter reporting.")
    put("technique-eis_capacitance",
        "The capacitance minimum is not frequency-stable enough for a defensible PZC assignment." if str(summary.get("pzc_status"))!="frequency_stable_candidate" else "A frequency-stable capacitance-minimum candidate is present and should be checked against interface-specific PZC evidence.",
        "Storage and loss capacitance are calculated from the Rs/L-corrected interfacial admittance and compared across potential and frequency. Frequency dispersion is retained explicitly. Quantum capacitance is not separated from the measured interfacial capacitance without an independent low-DOS material model and Cdl/DOS context.")
    put("technique-mott_schottky_diagnostic",
        "Strict cross-frequency Mott-Schottky checks do not support a reportable flat-band potential or carrier density." if str(summary.get("mott_schottky_status"))!="candidate_regime_present" else "A candidate Mott-Schottky regime is present but still requires semiconductor context, area, dielectric constant, and cross-frequency validation.",
        "Linear-window R², voltage span, slope consistency, and intercept plausibility are checked before semiconductor quantities are reported. A visually linear 1/C² segment alone is insufficient when the capacitance includes double-layer, dispersive, or low-DOS contributions.")
    put("technique-mott_schottky",
        "Potential-dependent impedance is retained as full EIS; capacitance/Mott-Schottky transforms are treated as derived diagnostics rather than a replacement for the spectra.",
        "Interpret the potential-frequency grid together with Nyquist/Bode, complex capacitance, DRT, and strict Mott-Schottky validity diagnostics. This prevents a frequency slice from being over-interpreted when the underlying interface is dispersive or blocking.")
    put("technique-drt_peak_tracks",
        "DRT tracks relaxation time and amplitude versus potential, but peak identity is not assigned to a mechanism without agreement with EIS model support and independent chemistry.",
        "Use DRT as a non-parametric cross-check on the number and movement of relaxation processes. Compare peak timescales with supported circuit time constants, while treating changes in peak rank or amplitude as descriptive rather than automatic Rct, adsorption, or diffusion assignments.")
    out["slide_content"]=sc
    return out


def _export_advanced(parser: Any, output_dir: Path, base_name: str) -> dict[str,Any]:
    output_dir=Path(output_dir); safe=_safe(base_name); out=output_dir/f"{safe}_Advanced_Potential_EIS"; entries=[]; notes=[]
    for mi,ms in enumerate(((getattr(parser,"decoded",None) or {}).get("mott_schottky") or []),1):
        r=analyze_mott_grid_advanced(ms)
        if r.get("status")!="ok": notes.append(str(r.get("status"))); continue
        sdict=[dict(zip(SUMMARY_COLUMNS,row)) for row in r["summary"]]
        area_cm2=_finite((r.get("interpretation") or {}).get("electrode_area_cm2"))
        for d in sdict:
            ceff=_finite(d.get("Ceff1_HsuMansfeld_F"))
            d["Ceff1_uF_cm2_if_area"]=(ceff/area_cm2*1e6) if (ceff is not None and area_cm2) else None
        validation_rows=[[d.get(c) for c in VALIDATION_COMPACT_COLUMNS] for d in sdict]
        parameter_rows=[[d.get(c) for c in PARAMETER_TREND_COLUMNS] for d in sdict]
        files=[
            ("validation_model_selection",VALIDATION_COMPACT_COLUMNS,validation_rows,"EIS_VALIDATION","Spectrum validity + model evidence","potential_V","kk_style_rmse"),
            ("circuit_parameter_trends",PARAMETER_TREND_COLUMNS,parameter_rows,"EIS_CIRCUIT_MODEL","Identifiable circuit parameters vs potential","potential_V","Rs_ohm"),
            ("validation_model_selection_full",SUMMARY_COLUMNS,r["summary"],"EIS_VALIDATION","Full validation/model-selection audit table","potential_V","fit_rmse"),
            ("candidate_circuits",CANDIDATE_COLUMNS,r["candidates"],"EIS_CIRCUIT_MODEL","All candidate circuit fits","potential_V","fit_rmse"),
            ("complex_capacitance_selected_frequencies",CAP_COLUMNS,r["cap_long"],"EIS_CAPACITANCE","Complex capacitance across potential and frequency","potential_V","C_storage_F"),
            ("apparent_pzc_vs_frequency",PZC_COLUMNS,r["pzc"],"EIS_CAPACITANCE","Apparent capacitance-minimum potential vs frequency","frequency_Hz","apparent_capacitance_minimum_potential_V"),
            ("mott_schottky_strict_diagnostics",MOTT_COLUMNS,[[x.get(c) for c in MOTT_COLUMNS] for x in r["mott"]],"MOTT_SCHOTTKY_DIAGNOSTIC","Strict Mott-Schottky validity diagnostics","frequency_Hz","r_squared"),
        ]
        for stem,cols,rows,tech,title,xlab,ylab in files:
            p=out/f"{safe}__{stem}.csv"; _write_csv(p,cols,rows)
            entries.append({"relative_csv_path":str(p.relative_to(output_dir)).replace("\\","/"),"measurement_index":mi,"curve_index":len(entries)+1,"measurement_title":ms.get("title","Potential-dependent EIS"),"curve_title":title,"method_id":"v316_advanced_potential_eis","technique":tech,"x_label":xlab,"x_unit":"V" if "potential" in xlab.lower() else "Hz","y_label":ylab,"y_unit":"model-dependent","n_points":len(rows)})
        # Separate capacitance-frequency slices so Browser/PPT can overlay clean C(V) curves.
        cap=np.asarray(r["cap_long"],object)
        if len(cap):
            freqs=sorted({float(x[2]) for x in r["cap_long"]})
            for k,fr in enumerate(freqs,1):
                rows=[x for x in r["cap_long"] if abs(math.log10(float(x[2]))-math.log10(fr))<1e-8]
                p=out/f"{safe}__capacitance_{fr:.6g}_Hz.csv"; _write_csv(p,CAP_COLUMNS,rows)
                entries.append({"relative_csv_path":str(p.relative_to(output_dir)).replace("\\","/"),"measurement_index":mi,"curve_index":100+k,"measurement_title":f"Complex capacitance {fr:.6g} Hz","curve_title":f"C' and C'' vs potential at {fr:.6g} Hz","method_id":"v316_complex_capacitance","technique":"EIS_CAPACITANCE","x_label":"Potential","x_unit":"V","y_label":"Capacitance","y_unit":"F","n_points":len(rows)})
        cap_all_path=out/f"{safe}__complex_capacitance_all_points.csv"
        _write_csv(cap_all_path,CAP_COLUMNS,r.get("cap_all") or [])
        fit_path=out/f"{safe}__fit_diagnostics_all_potentials.csv"
        _write_csv(fit_path,FIT_COLUMNS,r.get("fit_rows") or [])
        entries.append({"relative_csv_path":str(fit_path.relative_to(output_dir)).replace("\\","/"),"measurement_index":mi,"curve_index":900,"measurement_title":ms.get("title","Potential-dependent EIS"),"curve_title":"Measured vs recommended-model fit at every potential","method_id":"v316_fit_diagnostics","technique":"EIS_FIT_DIAGNOSTIC","x_label":"Frequency","x_unit":"Hz","y_label":"Impedance / residual","y_unit":"ohm / normalized","n_points":len(r.get("fit_rows") or [])})
        scientific=_scientific_summary(r)
        (out/f"{safe}__advanced_scientific_summary.json").write_text(json.dumps(scientific,indent=2,ensure_ascii=False),encoding="utf-8")
        ov={"schema":"faradaiq-advanced-potential-eis-v316","quality_thresholds":QUALITY,"interpretation":r["interpretation"],"scientific_summary":scientific,"literature_guidance":[
            {"doi":"10.1016/j.electacta.2025.146892","use":"KK validation, CNLS statistical evaluation, inductive-effect and DRT caution"},
            {"doi":"10.1016/j.electacta.2014.01.034","use":"robust linear KK / overfitting logic"},
            {"doi":"10.1016/j.joule.2024.05.008","use":"DRT as model-support tool, not automatic mechanism assignment"},
            {"doi":"10.1021/acsenergylett.1c01245","use":"Mott-Schottky sanity checks and requirement to isolate space-charge capacitance"},
            {"doi":"10.1039/C0CP00170H","use":"differential capacitance vs potential at GC/Pt/Au ionic-liquid interfaces and capacitance-minimum/PZC context"},
            {"doi":"10.1016/j.coelec.2017.01.006","use":"frequency-dependent double-layer capacitance / capacitance dispersion"},
            {"doi":"10.1021/jacs.9b07134","use":"potential-dependent ionic-liquid double-layer charging on graphene"},
            {"source":"attached Nature 2026 paper","use":"for low-DOS graphene, total capacitance is Cq and Cdl in series; Cq requires DOS/material context and is not generically identifiable from total EIS capacitance"},
        ]}
        (out/f"{safe}__advanced_interpretation.json").write_text(json.dumps(ov,indent=2,ensure_ascii=False),encoding="utf-8")
    return {"n_csv_files":len(entries),"csv_entries":entries,"skipped_entries":[],"advanced_potential_eis_dir":str(out),"notes":notes}


def _normalize_v316_manifest_datasets(manifest: MutableMapping[str,Any]) -> MutableMapping[str,Any]:
    """Remove pipeline-input duplicates and repair old Mott-Schottky EIS classification.

    Historical filename-driven classification can call the ordinary Nyquist/Bode
    exports ``SEMICONDUCTOR_ANALYSIS`` simply because their filenames contain
    ``Mott-Schottky``. DRT_input_csv is also a pipeline staging directory, not a
    second scientific dataset family. Once strict v316 diagnostics exist, keep
    only the genuine EIS curves and the strict diagnostic result.
    """
    datasets=list(manifest.get("datasets") or [])
    has_strict=any(str((d or {}).get("technique_key") or "").upper()=="MOTT_SCHOTTKY_DIAGNOSTIC" for d in datasets if isinstance(d,Mapping))
    kept=[]
    for item in datasets:
        if not isinstance(item,MutableMapping):
            kept.append(item); continue
        path=str(item.get("physical_path") or item.get("relative_path") or item.get("path") or "").replace("\\","/")
        low=path.lower()
        if "/drt_input_csv/" in "/"+low.lstrip("/") or low.startswith("drt_input_csv/"):
            continue
        key=str(item.get("technique_key") or "").upper()
        if key=="SEMICONDUCTOR_ANALYSIS" and re.search(r"mott[-_ ]schottky.*(?:nyquist|bode[_ -]?(?:mag|phase))\.csv$",low,re.I):
            item=dict(item); item["technique_key"]="EIS"; item["technique_label"]="EIS"; item["family"]="Impedance"
            kept.append(item); continue
        if has_strict and key=="SEMICONDUCTOR_ANALYSIS":
            continue
        kept.append(item)
    manifest["datasets"]=kept
    counts={}
    for d in kept:
        if not isinstance(d,Mapping): continue
        k=str(d.get("technique_key") or "OTHER").upper(); counts[k]=counts.get(k,0)+1
    manifest["technique_counts"]=counts
    return manifest


def _suppress_legacy_potential_eis_story(manifest: MutableMapping[str,Any]) -> MutableMapping[str,Any]:
    if not isinstance(manifest.get("v316_advanced_eis_summary"),Mapping): return manifest
    counts=manifest.get("technique_counts")
    suppress={"POTENTIAL_EIS_MODELING"}
    if isinstance(counts,Mapping) and int(counts.get("DRT_PEAK_TRACKS",0) or 0)>0:
        # The legacy DRT_ANALYSIS card can be a progress-log text dataset with no
        # scientific plot.  The v316 peak-track tables are the actual DRT result.
        suppress.add("DRT_ANALYSIS")
    if isinstance(counts,MutableMapping):
        for key in suppress: counts.pop(key,None)
    disc=manifest.get("discovery")
    if isinstance(disc,MutableMapping) and isinstance(disc.get("techniques"),list):
        disc["techniques"]=[x for x in disc["techniques"] if str((x or {}).get("technique_key") or "").upper() not in suppress]
    comp=manifest.get("comparison")
    if isinstance(comp,MutableMapping):
        for key in ("matrix","recommended"):
            if isinstance(comp.get(key),list):
                comp[key]=[x for x in comp[key] if str((x or {}).get("technique_key") or "").upper() not in suppress]
    return manifest


def _find_advanced_summary(experiment_root: str|Path) -> dict[str,Any]:
    root=Path(experiment_root)
    if not root.exists(): return {}
    for p in sorted(root.rglob("*__advanced_scientific_summary.json")):
        try:
            obj=json.loads(p.read_text(encoding="utf-8"))
            if isinstance(obj,dict) and int(obj.get("potential_steps") or 0)>0:
                return obj
        except Exception:
            continue
    return {}


def _read_csv_numeric(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open("r",encoding="utf-8-sig",errors="ignore",newline="") as f:
        r=csv.reader(f); rows=list(r)
    return (rows[0] if rows else []),(rows[1:] if len(rows)>1 else [])


def augment_drt_tracks(experiment_root: str|Path) -> None:
    """Create potential-resolved DRT peak tracks after the normal DRT job has run."""
    root=Path(experiment_root)
    if not root.exists(): return
    fns=[p for p in root.rglob("*_simple_drt_data.csv") if not p.name.endswith(".metadata.json")]
    if len(fns)<3:return
    spectra=[]
    try:
        from scipy.signal import find_peaks
    except Exception:
        return
    for p in fns:
        try:
            h,rows=_read_csv_numeric(p); idx={c:i for i,c in enumerate(h)}
            if "out_tau_vec" not in idx or "gamma" not in idx: continue
            tau=np.array([float(r[idx["out_tau_vec"]]) for r in rows],float); gam=np.array([float(r[idx["gamma"]]) for r in rows],float)
            if "set_potential_V" in idx: v=float(rows[0][idx["set_potential_V"]])
            else:
                m=re.search(r"Mott-Schottky_([+-]?\d+\.\d+)_V",p.name); v=float(m.group(1)) if m else np.nan
            if not np.isfinite(v) or not np.any(np.isfinite(gam)):continue
            g=np.where(np.isfinite(gam),gam,0); prom=max(float(np.nanmax(g))*0.04,1e-12)
            peaks,props=find_peaks(g,prominence=prom)
            pk=sorted([(float(tau[i]),float(g[i]),float(props["prominences"][j])) for j,i in enumerate(peaks)],key=lambda x:x[1],reverse=True)[:4]
            area=float(np.trapz(np.maximum(g,0),np.log(tau))) if np.all(tau>0) else np.nan
            spectra.append((v,pk,area))
        except Exception: continue
    if len(spectra)<3:return
    spectra.sort(key=lambda x:x[0]); tracks=[]
    # rank by amplitude independently; stable enough for visualization, never mechanism assignment.
    maxrank=max((len(x[1]) for x in spectra),default=0)
    out=root/"Advanced_Potential_EIS_DRT"; out.mkdir(parents=True,exist_ok=True)
    for rank in range(min(maxrank,4)):
        rows=[]
        for v,pk,area in spectra:
            if rank<len(pk):
                tau,g,prom=pk[rank]; rows.append([v,rank+1,tau,1/(2*np.pi*tau),g,prom,area])
        if rows:
            _write_csv(out/f"DRT_peak_track_{rank+1}.csv",["potential_V","peak_rank_by_amplitude","tau_s","characteristic_frequency_Hz","gamma_peak_ohm","prominence_ohm","integrated_positive_gamma_dln_tau_ohm"],rows)
    _write_csv(out/"DRT_integrated_resistance_vs_potential.csv",["potential_V","integrated_positive_gamma_dln_tau_ohm"],[[v,a] for v,_,a in spectra])
    (out/"DRT_INTERPRETATION_BOUNDARY_v316.txt").write_text("Peak rank/position changes with potential are descriptive. Assign electrochemical mechanisms only after cross-checking EIS model support, potential trends, perturbation validity, and independent chemistry.\n",encoding="utf-8")


def _base_exporter_for_v316(Parser: Any):
    """Bypass the v315 exploratory fitter; v316 supersedes it with a faster, gated analysis."""
    return getattr(Parser,"_v315_export_base",None) or getattr(Parser,"export_analysis_ready_outputs",None)


def install(core: MutableMapping[str,Any]) -> None:
    Parser=core.get("VersaStudioParser")
    if Parser is not None and not getattr(Parser,"_v316_advanced_potential_eis_installed",False):
        old=_base_exporter_for_v316(Parser)
        def export_v316(self,output_dir:Path,base_name:str):
            merged={"n_csv_files":0,"csv_entries":[],"skipped_entries":[]}
            if callable(old):
                prev=old(self,output_dir,base_name) or {}; merged["n_csv_files"]+=int(prev.get("n_csv_files",0)); merged["csv_entries"].extend(prev.get("csv_entries",[]) or []); merged["skipped_entries"].extend(prev.get("skipped_entries",[]) or [])
                for k,v in prev.items():
                    if k not in merged: merged[k]=v
            try:
                new=_export_advanced(self,Path(output_dir),base_name); merged["n_csv_files"]+=int(new.get("n_csv_files",0)); merged["csv_entries"].extend(new.get("csv_entries",[]) or []); merged["skipped_entries"].extend(new.get("skipped_entries",[]) or []); merged["advanced_potential_eis_dir"]=new.get("advanced_potential_eis_dir")
            except Exception as exc:
                merged["skipped_entries"].append({"reason":f"v316 advanced potential EIS failed: {exc}"})
            return merged
        Parser.export_analysis_ready_outputs=export_v316; Parser._v316_advanced_potential_eis_installed=True

    # Browser technique recognition and scientifically useful parameter views.
    try:
        import faradaiq.v290_portable_browser as b
        if not getattr(b,"_v316_advanced_potential_eis_installed",False):
            oldtech=b._technique_key; oldplot=b._plot_views; oldbuild=b.build_manifest
            def tech(value:Any,*,path:Any="",columns:Sequence[Any]=(),role:Any=""):
                t=" ".join(str(x or "") for x in (value,path,role)).upper(); norms={b._normalize_column(c) for c in columns}
                if "MOTT_SCHOTTKY_DIAGNOSTIC" in t or {"flatbandpotentialv","rsquared"}.issubset(norms): return "MOTT_SCHOTTKY_DIAGNOSTIC"
                if "EIS_FIT_DIAGNOSTIC" in t or {"zrealmeasuredohm","zrealfitohm"}.issubset(norms): return "EIS_FIT_DIAGNOSTIC"
                if "EIS_VALIDATION" in t or "kkstylermse" in norms: return "EIS_VALIDATION"
                if "EIS_CIRCUIT_MODEL" in t or "CIRCUIT_PARAMETER_TRENDS" in t or "jacobiancondition" in norms or ({"rsohm","lh"}.issubset(norms) and ("ceff1hsumansfeldf" in norms or "r1orrpohm" in norms)): return "EIS_CIRCUIT_MODEL"
                if "EIS_CAPACITANCE" in t or "cstoragef" in norms or "apparentcapacitanceminimumpotentialv" in norms: return "EIS_CAPACITANCE"
                if "DRT_PEAK_TRACK" in t or "gammapeakohm" in norms: return "DRT_PEAK_TRACKS"
                return oldtech(value,path=path,columns=columns,role=role)
            def plot(technique:str,columns:Sequence[Any],numeric:Sequence[Any],*,path_hint:str=""):
                key=str(technique).upper(); find=b._find_column; views=[]
                def add(id_,lab,xnames,ynames,ys="linear",prio=1):
                    x=find(columns,xnames); y=find(columns,ynames)
                    if x is not None and y is not None: views.append({"id":id_,"label":lab,"x_index":x,"y_index":y,"x_column":str(columns[x]),"y_column":str(columns[y]),"x_scale":"log" if "frequency" in str(columns[x]).lower() else "linear","y_scale":ys,"plot_type":"curve","synthetic_x":False,"priority":prio,"description":lab})
                if key=="EIS_FIT_DIAGNOSTIC":
                    add("nyquist_fit_overlay","Measured and fitted Nyquist components",["Zreal_measured_ohm"],["minus_Zimag_measured_ohm"],"linear",1); add("fit_residual_real","Normalized real residual vs frequency",["frequency_Hz"],["normalized_residual_real"],"linear",2); add("fit_residual_imag","Normalized imaginary residual vs frequency",["frequency_Hz"],["normalized_residual_imag"],"linear",3)
                elif key=="EIS_VALIDATION":
                    add("kk_vs_potential","KK-style consistency residual vs potential",["potential_V"],["kk_style_rmse"],"linear",1); add("fit_error_vs_potential","Best-fit residual vs potential",["potential_V"],["fit_rmse"],"linear",2); add("inductance_vs_potential","Series inductance vs potential",["potential_V"],["L_H"],"log",3)
                elif key=="EIS_CIRCUIT_MODEL":
                    add("rp_vs_potential","Interfacial/polarization resistance vs potential",["potential_V"],["R1_or_Rp_ohm","R1_ohm"],"log",1); add("ceff_vs_potential","Effective CPE capacitance vs potential",["potential_V"],["Ceff1_uF_cm2_if_area","Ceff1_HsuMansfeld_F"],"log",2); add("n_vs_potential","CPE exponent vs potential",["potential_V"],["n1"],"linear",3); add("rs_vs_potential","Series resistance vs potential",["potential_V"],["Rs_ohm"],"linear",4)
                elif key=="EIS_CAPACITANCE":
                    add("storage_capacitance","Storage capacitance vs potential",["potential_V"],["C_storage_F"],"log",1); add("loss_capacitance","Loss capacitance vs potential",["potential_V"],["C_loss_F"],"log",2); add("pzc_vs_frequency","Apparent capacitance-minimum potential vs frequency",["frequency_Hz"],["apparent_capacitance_minimum_potential_V"],"linear",3)
                elif key=="MOTT_SCHOTTKY_DIAGNOSTIC":
                    add("ms_r2_vs_frequency","Mott-Schottky linearity R² vs frequency",["frequency_Hz"],["r_squared"],"linear",1); add("vfb_vs_frequency","Candidate flat-band potential vs frequency",["frequency_Hz"],["flatband_potential_V"],"linear",2)
                elif key=="DRT_PEAK_TRACKS":
                    add("tau_vs_potential","DRT relaxation time vs potential",["potential_V"],["tau_s"],"log",1); add("gamma_vs_potential","DRT peak amplitude vs potential",["potential_V"],["gamma_peak_ohm"],"log",2)
                if views:return views
                return oldplot(technique,columns,numeric,path_hint=path_hint)
            def build_manifest_v316(experiment_root,*args,**kwargs):
                try: augment_drt_tracks(experiment_root)
                except Exception: pass
                manifest=oldbuild(experiment_root,*args,**kwargs)
                try:
                    _normalize_v316_manifest_datasets(manifest)
                    datasets=[dict(d) for d in (manifest.get("datasets") or []) if isinstance(d,Mapping)]
                    b._assign_ui_roles(datasets)
                    manifest["datasets"]=datasets
                    manifest["comparison"]=b._comparison_summary(datasets)
                    manifest["discovery"]=b._discovery_summary(datasets,manifest["comparison"])
                except Exception:
                    pass
                try:
                    advanced_summary=_find_advanced_summary(experiment_root)
                    if advanced_summary:
                        manifest["v316_advanced_eis_summary"]=advanced_summary
                except Exception:
                    pass
                try:
                    disc=manifest.setdefault("discovery",{}); rows=list(disc.get("techniques") or [])
                    counts=manifest.get("technique_counts") or {}; present={str(x.get("technique_key") or "").upper() for x in rows if isinstance(x,Mapping)}
                    # Analysis-only CSVs are intentionally supporting tables in the base Browser,
                    # so they may not receive comparison views. Add deterministic scientific views
                    # for the v316 advanced result families so Browser and PowerPoint show them.
                    advanced_keys={"EIS_FIT_DIAGNOSTIC","EIS_VALIDATION","EIS_CIRCUIT_MODEL","EIS_CAPACITANCE","MOTT_SCHOTTKY_DIAGNOSTIC","DRT_PEAK_TRACKS"}
                    labels={
                        "EIS_FIT_DIAGNOSTIC":"Measured vs fitted EIS and residual diagnostics",
                        "EIS_VALIDATION":"EIS validity and fit quality",
                        "EIS_CIRCUIT_MODEL":"Potential-dependent EIS circuit modeling",
                        "EIS_CAPACITANCE":"Complex capacitance and apparent PZC",
                        "MOTT_SCHOTTKY_DIAGNOSTIC":"Mott-Schottky validity diagnostics",
                        "DRT_PEAK_TRACKS":"Potential-resolved DRT peak tracks",
                    }
                    matrix=list((manifest.setdefault("comparison",{})).get("matrix") or [])
                    existing={(str(v.get("technique_key") or ""),str(v.get("view_id") or ""),tuple(v.get("dataset_ids") or [])) for v in matrix if isinstance(v,Mapping)}
                    for ds in manifest.get("datasets") or []:
                        if not isinstance(ds,MutableMapping): continue
                        key=str(ds.get("technique_key") or "").upper()
                        if key not in advanced_keys: continue
                        views=plot(key,ds.get("columns") or [],[],path_hint=str(ds.get("physical_path") or ""))
                        if views: ds["plot_views"]=views
                        for vw in views[:4]:
                            sig=(key,str(vw.get("id") or ""),(str(ds.get("id")),))
                            if sig in existing: continue
                            matrix.append({"technique_key":key,"technique_label":labels.get(key,ds.get("technique_label") or key),"family":"EIS analysis","view_id":vw.get("id"),"view_label":vw.get("label"),"plot_type":vw.get("plot_type","curve"),"x_column":vw.get("x_column"),"y_column":vw.get("y_column"),"description":vw.get("description",""),"score":90-int(vw.get("priority") or 1),"dataset_ids":[ds.get("id")],"dataset_count":1,"unique_points":0,"point_min":None,"point_max":None,"methods":[ds.get("method_label") or "v316 advanced EIS"],"recommendation_score":95-int(vw.get("priority") or 1)})
                            existing.add(sig)
                    manifest["comparison"]["matrix"]=matrix
                    # Legacy v315 called every Mott-Schottky-derived table a semiconductor result.
                    # Once strict v316 diagnostics exist, suppress that misleading primary story card.
                    if int(counts.get("MOTT_SCHOTTKY_DIAGNOSTIC",0) or 0)>0:
                        rows=[x for x in rows if str(x.get("technique_key") or "").upper()!="SEMICONDUCTOR_ANALYSIS"]
                        present.discard("SEMICONDUCTOR_ANALYSIS")
                    for key,label in labels.items():
                        n=int(counts.get(key,0) or 0)
                        if n>0 and key not in present:
                            rows.append({"technique_key":key,"technique_label":label,"family":"EIS analysis","data_kind":"analysis","dataset_count":n,"point_count":0,"method_count":1,"analysis_count":n})
                            present.add(key)
                    disc["techniques"]=rows
                except Exception:
                    pass
                try:
                    _suppress_legacy_potential_eis_story(manifest)
                except Exception:
                    pass
                return manifest
            b._technique_key=tech; b._plot_views=plot; b.build_manifest=build_manifest_v316
            for k,label in [("EIS_FIT_DIAGNOSTIC","Measured vs fitted EIS and residual diagnostics"),("EIS_VALIDATION","EIS validity and fit quality"),("EIS_CIRCUIT_MODEL","Potential-dependent EIS circuit modeling"),("EIS_CAPACITANCE","Complex capacitance and apparent PZC"),("MOTT_SCHOTTKY_DIAGNOSTIC","Mott-Schottky validity diagnostics"),("DRT_PEAK_TRACKS","Potential-resolved DRT peak tracks")]:
                try:b.TECHNIQUE_REGISTRY[k]={"label":label,"family":"EIS analysis","kind":"analysis"}
                except Exception:pass
            b._v316_advanced_potential_eis_installed=True
    except Exception:
        pass

    try:
        import faradaiq.v290_powerpoint_summary as ppt
        ppt.VERSION=VERSION; ppt.APP_TITLE=f"FaradaIQ Studio {VERSION}"; ppt.FRONT_SLIDE_FOOTER=f"Made by FaradaIQ Studio {VERSION}"
        ppt.TECHNIQUE_SCENARIOS.update({
            "EIS_FIT_DIAGNOSTIC":[("Fit overlays","Compare measured and fitted Nyquist/Bode components at each potential."),("Residual audit","Inspect normalized complex residuals versus frequency and flag systematic misfit before parameter interpretation.")],
            "EIS_VALIDATION":[("Data validity","Show KK-style consistency, fit residuals, high-frequency inductive response and review flags before mechanistic claims."),("Model evidence","Distinguish strong AICc support from model non-uniqueness and ill-conditioned parameters.")],
            "EIS_CIRCUIT_MODEL":[("Circuit parameters","Plot only supported Rs, L, resistance, CPE/effective capacitance and time constants versus potential."),("Interpretation boundary","A good circuit fit is phenomenological evidence, not unique proof of mechanism.")],
            "EIS_CAPACITANCE":[("Complex capacitance","Use C' and C'' from admittance after Rs/L correction; show frequency dispersion and capacitance-minimum potential."),("PZC","Call the minimum apparent PZC only when stable across frequency and interface context supports the assignment.")],
            "MOTT_SCHOTTKY_DIAGNOSTIC":[("Validity first","Show R², potential window, frequency dispersion and flat-band plausibility before reporting semiconductor quantities."),("Reject unsupported values","Do not report carrier density or flat-band potential when strict checks fail or area/epsilon are absent.")],
            "DRT_PEAK_TRACKS":[("DRT map summary","Track peak tau, amplitude and integrated relaxation resistance versus potential."),("Cross-validation","Use DRT to guide circuit complexity and compare relaxation times, without automatic process assignment.")],
        })
        for k,label,core_slides in [
            ("EIS_FIT_DIAGNOSTIC","Measured vs fitted EIS and residual diagnostics",["measured/fitted Nyquist or Bode overlay","normalized residuals vs frequency"]),
            ("EIS_VALIDATION","EIS validity and fit quality",["KK-style residual and high-frequency artifact status","fit residual/model evidence vs potential"]),
            ("EIS_CIRCUIT_MODEL","Potential-dependent EIS circuit parameters",["Rs/Rp/Ceff/n vs potential","model support and uncertainty flags"]),
            ("EIS_CAPACITANCE","Complex capacitance and apparent PZC",["C' and C'' vs potential at selected frequencies","apparent capacitance-minimum potential vs frequency"]),
            ("MOTT_SCHOTTKY_DIAGNOSTIC","Mott-Schottky validity diagnostics",["R² and accepted/rejected windows vs frequency","flat-band estimates only where physically plausible"]),
            ("DRT_PEAK_TRACKS","Potential-resolved DRT relaxation tracks",["tau and gamma vs potential","DRT resistance vs fitted polarization resistance"]),
        ]:
            ppt.TECHNIQUE_SLIDE_RECIPES_V304[k]={"label":label,"core":core_slides,"appendix":["source tables, fit flags and assumptions"]}
        if not getattr(ppt,"_v316_advanced_narrative_installed",False):
            old_context=ppt._v304_compact_manifest_context
            old_fallback=ppt._v310_fallback_narrative
            def context_v316(manifest,plan):
                ctx=old_context(manifest,plan)
                summary=manifest.get("v316_advanced_eis_summary") if isinstance(manifest,Mapping) else None
                if isinstance(summary,Mapping) and summary:
                    ctx["advanced_potential_eis_summary"]=dict(summary)
                return ctx
            def fallback_v316(manifest,plan):
                narrative=old_fallback(manifest,plan)
                summary=manifest.get("v316_advanced_eis_summary") if isinstance(manifest,Mapping) else None
                return _augment_ppt_narrative(narrative,summary) if isinstance(summary,Mapping) and summary else narrative
            ppt._v304_compact_manifest_context=context_v316
            ppt._v310_fallback_narrative=fallback_v316
            ppt._v316_advanced_narrative_installed=True
    except Exception:
        pass
    core["FARADAIQ_DEFAULT_OUTPUT_DIR"]=DEFAULT_OUTPUT_DIR
