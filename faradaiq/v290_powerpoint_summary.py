"""Professional technique-aware PowerPoint summary generation for FaradaIQ Studio v302.

The deck is generated beside the portable HTML browser.  Scientific plots and
coverage are deterministic.  An optional direct LLM pass receives only a compact
text/JSON summary and contributes clearly labeled discussion text; raw experiment
files and credentials are never embedded in the request.
"""
from __future__ import annotations

import json
import math
import os
import re
import textwrap
import threading
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

VERSION = "v302"
VERSION_NUMBER = 302
APP_TITLE = f"FaradaIQ eStudio {VERSION}"
PPTX_NAME = "FaradaIQ_Data_Summary.pptx"
PPT_MANIFEST_NAME = "faradaiq_powerpoint_summary.json"
PPT_ASSET_DIR = "ppt_summary_assets"
PPT_LLM_CONTEXT = "ppt_llm_context.json"
PPT_LLM_PROMPT = "ppt_llm_prompt.md"
PPT_LLM_RESPONSE = "ppt_llm_response.json"
DEFAULT_ENABLED = True
DEFAULT_USE_DIRECT_LLM = True
DEFAULT_DETAIL_LEVEL = "comprehensive"
FRONT_SLIDE_FOOTER = f"Made by FaradaIQ eStudio {VERSION}"
_CORE: MutableMapping[str, Any] = {}

# The catalog is intentionally phrased in scientific language.  Availability is
# decided from actual columns/views; no unavailable comparison is plotted.
TECHNIQUE_SCENARIOS: dict[str, list[tuple[str, str]]] = {
    "FSCV": [("Fast-scan voltammetry overlays", "Compare rapid cyclic responses across points, cycles, and conditions."), ("Peak and background response", "Compare peak position/amplitude and background-subtracted response only when supplied."), ("Cycle stability", "Review repeatability and drift across repeated fast scans.")],
    "IV": [("Current–voltage overlays", "Compare polarization response across points and conditions."), ("Onset and differential response", "Compare onset, slope, plateau, or conductance-like trends only when represented in the data."), ("Hysteresis/repeatability", "Compare forward/reverse or repeated branches when present.")],
    "LPR": [("Near-Ecorr polarization overlays", "Compare linear polarization response around the measured corrosion potential."), ("Polarization resistance", "Compare Rp only where the fitted window and units are documented."), ("Corrosion linkage", "Relate LPR-derived quantities to corrosion outputs only when the required assumptions and metadata are present.")],
    "CV": [("Potential–current overlays", "Compare points, conditions, scan rates, and cycles."), ("Peak response trends", "Compare anodic/cathodic peak current and potential."), ("Integrated response", "Compare signed or absolute current–potential area when available."), ("Cycle evolution", "Compare cycle-to-cycle drift, hysteresis, and repeatability.")],
    "CV_LSV": [("Potential–current overlays", "Compare points, methods, and conditions."), ("Onset and limiting response", "Compare onset, peak, plateau, or limiting current metrics."), ("Cycle or branch comparison", "Compare forward/reverse or repeated scans when present.")],
    "LSV": [("Potential–current overlays", "Compare polarization response across points and conditions."), ("Onset/half-wave/limiting current", "Compare extracted voltammetric metrics when available."), ("Tafel-compatible branch", "Review only a justified kinetic region.")],
    "DPV": [("Pulse peak overlays", "Compare peak height, area, and potential."), ("Calibration or concentration trend", "Compare response against ordered metadata when present.")],
    "SWV": [("Forward/reverse/net response", "Compare available square-wave components."), ("Peak metrics", "Compare peak position, amplitude, and width.")],
    "NPV": [("Pulse response overlays", "Compare current against potential or pulse index."), ("Peak/plateau metrics", "Compare response magnitude and position.")],
    "STRIPPING": [("Stripping peak overlays", "Compare peak position, area, and amplitude."), ("Calibration/condition trend", "Compare extracted metrics against point or condition.")],
    "CA": [("Current–time overlays", "Compare transient shape, magnitude, duration, and stability."), ("Charge–time and final charge", "Compare integrated charge when available."), ("Cottrell and Anson diagnostics", "Use only when assumptions and time windows are appropriate."), ("Nucleation/growth comparisons", "Compare normalized transients or fitted model parameters when present."), ("Current at fixed times", "Compare point-wise response at common time coordinates.")],
    "DEPOSITION": [("Deposition transient overlays", "Compare current, charge, induction time, and steady response."), ("Nucleation/growth models", "Compare model fits and residuals when available."), ("Composition/morphology linkage", "Connect electrochemical metrics with characterization outputs when present.")],
    "CP": [("Potential–time overlays", "Compare transition time, plateau, drift, and stability."), ("Transition-time trend", "Compare Sand-type or empirical transition metrics."), ("Current and charge context", "Compare applied current and accumulated charge when available.")],
    "GCD": [("Voltage–capacity/time overlays", "Compare charge/discharge profiles and polarization."), ("Capacity retention", "Compare capacity versus cycle or point."), ("Coulombic/energy efficiency", "Compare efficiency metrics when available."), ("Rate performance", "Compare capacities across applied current or C-rate.")],
    "OCP": [("Potential–time overlays", "Compare equilibration, drift, and stability."), ("Final/stable potential", "Compare robust endpoint values across points or conditions.")],
    "ZRA": [("Current–time overlays", "Compare galvanic current, sign changes, and stability."), ("Integrated charge", "Compare cumulative galvanic transfer when available.")],
    "GITT": [("Pulse/rest potential profiles", "Compare steps, relaxation, and hysteresis."), ("Diffusion-related metrics", "Compare fitted diffusion outputs only where assumptions are documented."), ("State-of-charge trends", "Compare metrics against capacity or step index.")],
    "PITT": [("Current transient overlays", "Compare pulse response and relaxation."), ("Diffusion-related metrics", "Compare fitted outputs only where assumptions are documented.")],
    "EIS_ANALYSIS": [("EIS parameter trends", "Compare fitted or derived impedance parameters across points and conditions."), ("Residual and validity diagnostics", "Compare fit quality, residual structure, and model sensitivity."), ("Model/representation comparison", "Compare models or derived representations only when present and scientifically justified.")],
    "EIS": [("Nyquist overlays", "Compare impedance arcs, intercepts, tails, and fitted curves."), ("Bode magnitude and phase", "Compare frequency-dependent response and characteristic transitions."), ("Fit parameters", "Compare circuit/physics-based parameters across points and conditions."), ("Residuals and validity", "Compare fit residuals, sensitivity, and validation metrics when present."), ("Derived admittance/capacitance", "Compare derived representations when scientifically appropriate.")],
    "GEIS": [("Nyquist/Bode overlays", "Compare galvanostatic impedance spectra."), ("Fit parameters and residuals", "Compare fitted parameters and diagnostics.")],
    "DRT": [("DRT overlays versus tau", "Compare relaxation families and amplitudes."), ("DRT versus characteristic frequency", "Compare the same peaks in frequency space."), ("Peak position/amplitude/area", "Compare extracted peak metrics across points."), ("Boundary and regularization sensitivity", "Report frequency limits and inversion settings when available.")],
    "DRT_ANALYSIS": [("Peak metric trends", "Compare peak tau/frequency, amplitude, and area."), ("Model and regularization diagnostics", "Compare settings, residuals, and sensitivity outputs.")],
    "TAFEL": [("Tafel overlays", "Compare kinetic branches using consistent sign and units."), ("Slope and exchange current", "Compare fitted slope, intercept, exchange current, and fit quality."), ("Branch/window sensitivity", "Compare anodic/cathodic windows and residuals.")],
    "TAFEL_ANALYSIS": [("Fit parameter comparison", "Compare slopes, exchange currents, windows, and diagnostics."), ("Residual and sensitivity review", "Compare quality and robustness metrics.")],
    "RDE_ANALYSIS": [("Levich/Koutecky–Levich parameters", "Compare slopes, intercepts, and derived kinetic/transport quantities only when supplied."), ("Rotation-rate diagnostics", "Check consistency across rotation rates and fitted regions."), ("Selectivity/kinetic trends", "Compare derived quantities across points or conditions with assumptions stated.")],
    "RDE": [("Current–potential by rotation", "Compare hydrodynamic voltammograms."), ("Levich/Koutecky–Levich", "Compare slopes, intercepts, and inferred kinetic quantities."), ("Limiting current trend", "Compare response against square root of rotation rate.")],
    "RRDE": [("Disk/ring overlays", "Compare disk and ring response."), ("Collection efficiency/selectivity", "Compare derived product selectivity when available."), ("Levich/Koutecky–Levich", "Compare transport and kinetic trends.")],
    "EQCM_ANALYSIS": [("Mass/charge coupling metrics", "Compare derived EQCM coupling quantities with units and assumptions preserved."), ("Fit/calibration diagnostics", "Compare calibration, baseline, and residual information when available."), ("Condition trends", "Compare derived mass-coupled metrics across points, cycles, or conditions.")],
    "EQCM": [("Mass and current versus time/potential", "Compare coupled electrochemical and mass response."), ("Mass/charge ratio", "Compare apparent molar mass or coupling metrics."), ("Hysteresis and reversibility", "Compare forward/reverse or cycle behavior.")],
    "CONDUCTIVITY": [("Conductivity versus condition", "Compare temperature, concentration, time, or point."), ("Transport-model trends", "Compare fitted activation or transport parameters when present.")],
    "APPROACH": [("Distance–current overlays", "Compare approach/retract shape and contact/feedback behavior."), ("Characteristic distance/current", "Compare extracted response metrics across points."), ("Approach quality", "Flag incomplete, flat, saturated, or noisy curves.")],
    "POSITION": [("Position/motion traces", "Compare X, Y, Z, current, or potential against time."), ("Acquisition sequence", "Review spatial ordering and point linkage."), ("Drift and repeatability", "Compare repeated positions or motion stability.")],
    "SPATIAL_MAP": [("2D spatial maps", "Map a selected metric over X/Y positions."), ("Line profiles", "Compare ordered spatial trends."), ("Point-linked curves and images", "Open the underlying data and media for selected locations.")],
    "PROFILE": [("Ordered line profile", "Compare metric versus distance, position, or point index."), ("Region comparison", "Compare user-defined spatial regions when metadata exist.")],
    "VOLTAMMETRY_ANALYSIS": [("Peak/onset/area metrics", "Compare derived voltammetry quantities."), ("Scan-rate relationships", "Compare peak response and separation against scan rate when present.")],
    "TRANSIENT_ANALYSIS": [("Charge and endpoint metrics", "Compare integrated and endpoint quantities."), ("Model parameter trends", "Compare Cottrell, exponential, nucleation, or transition parameters."), ("Fit quality", "Compare residual and applicability diagnostics.")],
    "NUCLEATION_ANALYSIS": [("Model-fit comparison", "Compare instantaneous/progressive or alternative model fits."), ("Nucleation/growth parameters", "Compare induction times and fitted parameters."), ("Residuals", "Compare model adequacy and sensitivity.")],
    "KINETICS_ANALYSIS": [("Kinetic parameter trends", "Compare rate constants, diffusion coefficients, and related outputs."), ("Method consistency", "Compare estimates from different valid methods.")],
    "BATTERY_ANALYSIS": [("Capacity and efficiency", "Compare capacity, retention, and efficiencies."), ("Rate/cycle trends", "Compare performance versus cycle or applied rate."), ("Energy/power", "Compare energy and power metrics when available.")],
    "CORROSION_ANALYSIS": [("Corrosion metrics", "Compare Ecorr, Icorr, polarization resistance, and rates."), ("Fit quality and windows", "Compare branch selection and residuals.")],
    "SEMICONDUCTOR_ANALYSIS": [("Mott–Schottky trends", "Compare slope, flat-band potential, and carrier-density-related outputs."), ("Frequency dependence", "Compare derived quantities across frequency when available.")],
    "ECSA_ANALYSIS": [("ECSA-related metrics", "Compare double-layer capacitance, roughness, or active area."), ("Normalization impact", "Compare geometric and ECSA-normalized response.")],
    "TRANSPORT_ANALYSIS": [("Transport metrics", "Compare diffusion, conductivity, mobility, or transfer quantities."), ("Condition dependence", "Compare against temperature, concentration, position, or time.")],
    "STATISTICS": [("Replicate distributions", "Compare central tendency, spread, and confidence intervals."), ("Outliers and quality flags", "Review robust flags without treating them as conclusions."), ("Group significance", "Compare group tests only when assumptions and replicate counts are adequate.")],
    "SUMMARY": [("Derived metric trends", "Compare meaningful scientific numeric outputs across point, condition, or row."), ("Analysis table review", "Preserve labels and provenance for unfamiliar analysis acronyms such as EAL, LSB, AFL, or future methods."), ("Cross-analysis linkage", "Connect compatible metrics to parent measurements where identifiers exist.")],
    "EAL": [("EAL analysis comparisons", "Preserve the source-defined EAL meaning and compare its numeric outputs by point, condition, and method."), ("Parent-data linkage", "Connect EAL outputs to source measurements when identifiers are present.")],
    "LSB": [("LSB analysis comparisons", "Preserve the source-defined LSB meaning and compare its numeric outputs."), ("Parent-data linkage", "Connect LSB outputs to source measurements when identifiers are present.")],
    "AFL": [("AFL analysis comparisons", "Preserve the source-defined AFL meaning and compare its numeric outputs."), ("Parent-data linkage", "Connect AFL outputs to source measurements when identifiers are present.")],
}


def _clean_text(value: Any, max_chars: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_chars]


def _finite(value: Any) -> float | None:
    try:
        v=float(value)
    except Exception:
        return None
    return v if math.isfinite(v) else None




def _promote_named_analysis_acronyms(manifest: MutableMapping[str, Any]) -> bool:
    """Preserve source-defined EAL/LSB/AFL analysis sections when present.

    The acronyms are not expanded.  Promotion only changes presentation
    grouping for datasets already classified as analysis/summary data.
    """
    changed=False
    for dataset in manifest.get("datasets") or []:
        if not isinstance(dataset,MutableMapping): continue
        current=str(dataset.get("technique_key") or "")
        if current not in {"SUMMARY","OTHER","STATISTICS"}: continue
        role=str(dataset.get("ui_role") or dataset.get("data_kind") or "").lower()
        if role not in {"analysis","summary","data"}: continue
        hay=" ".join(str(dataset.get(k) or "") for k in ("display_name","physical_path","original_scientific_path","analysis_kind","method_label","role"))
        for acronym in ("EAL","LSB","AFL"):
            if re.search(rf"(?<![A-Za-z0-9]){acronym}(?![A-Za-z0-9])",hay,re.I):
                dataset["technique_key"]=acronym; dataset["technique_label"]=f"{acronym} analysis"; dataset["family"]="Derived analysis"; dataset["data_kind"]="analysis"; dataset["ui_role"]="analysis"; dataset["ui_hidden_default"]=False; dataset["ui_hidden_from_compare"]=False
                changed=True; break
    return changed


def _compact_manifest_context(manifest: Mapping[str, Any]) -> dict[str, Any]:
    discovery = dict(manifest.get("discovery") or {})
    comparison = dict(manifest.get("comparison") or {})
    techniques = list(discovery.get("techniques") or [])
    recommended = list(discovery.get("recommended_views") or comparison.get("recommended") or [])
    trends = list(discovery.get("trend_cards") or [])
    unusual = list(discovery.get("unusual_points") or [])
    analyses = list(discovery.get("analysis_cards") or [])
    return {
        "schema": "faradaiq-ppt-llm-context-v302",
        "experiment_name": manifest.get("experiment_name"),
        "created": manifest.get("created"),
        "coverage": manifest.get("coverage"),
        "discovery_counts": {k: discovery.get(k) for k in ("point_count", "point_min", "point_max", "method_count", "cycle_count", "sweep_count", "measurement_count", "analysis_count", "media_count")},
        "techniques": techniques,
        "recommended_comparisons": [{k: r.get(k) for k in ("technique_key", "technique_label", "family", "view_id", "view_label", "dataset_count", "unique_points", "point_min", "point_max", "methods", "description", "score")} for r in recommended[:24]],
        "potential_series": [{"id":d.get("id"),"technique":d.get("technique_key"),"point":d.get("point"),"set_potential_V":d.get("set_potential_V"),"potential_label":d.get("potential_label"),"potential_mode":d.get("potential_mode")} for d in (manifest.get("datasets") or []) if d.get("potential_label")],
        "trend_cards": trends[:24],
        "unusual_points": unusual[:24],
        "analysis_cards": analyses[:36],
        "electrochemistry_results": manifest.get("electrochemistry_results") or {},
        "short_names": manifest.get("short_names"),
        "note": "This context contains compact manifest summaries and derived metrics only. It does not contain raw experimental files.",
    }


def _detected_technique_keys(context: Mapping[str, Any]) -> list[str]:
    """Every technique_key actually present in this experiment (lower_snake_case)."""
    seen: list[str] = []
    for row in list(context.get("techniques") or []):
        key = str((row or {}).get("technique_key") or "").strip()
        if key and key.upper() not in {k.upper() for k in seen}:
            seen.append(key.lower())
    return seen


def _llm_prompt(context: Mapping[str, Any]) -> str:
    detected = _detected_technique_keys(context)
    # Always keep the legacy buckets so older slide-routing code (and the
    # portable Browser's report layer, which reads the same JSON) still finds
    # them even on experiments that don't happen to include those techniques.
    legacy = ["opening", "ocp", "corrosion", "takedata", "eis", "cv_lsv"]
    presenter_keys = list(dict.fromkeys(legacy + detected + ["conclusions"]))
    scenario_hint_lines = []
    for key in detected:
        scenarios = TECHNIQUE_SCENARIOS.get(key.upper())
        if scenarios:
            hint = "; ".join(f"{name} — {desc}" for name, desc in scenarios[:3])
            scenario_hint_lines.append(f"- {key}: {hint}")
    scenario_hints = "\n".join(scenario_hint_lines) or "- (use the technique labels and comparison views supplied in the JSON)"
    presenter_schema = ",\n".join(
        f'    "{key}": ["what to emphasize on {key.replace("_", " ")} slides/panels, if present"]'
        for key in presenter_keys
    )
    return """You are an electrochemist and scientific presentation editor. Write evidence-grounded discussion text that is reused in two places: the auto-generated FaradaIQ PowerPoint deck (at most two scientific plots per slide, presentation-ready phrasing) and the shared portable HTML Browser report (denser, interactive, read on-screen rather than presented). Write bullets that work in both contexts rather than assuming a live audience.

Rules:
1. Use only the supplied JSON. Do not invent experimental conditions, values, mechanisms, or conclusions.
2. Separate direct observations from interpretation and from recommended checks.
3. Treat robust outlier flags as review prompts, not proof.
4. Preserve unfamiliar analysis acronyms exactly (for example EAL, LSB, AFL) and do not expand them unless the JSON explicitly defines them.
5. Mention limitations when comparison coverage, replicate count, metadata, or analysis assumptions are incomplete.
6. Cover every technique actually present in "techniques" below -- do not limit discussion to OCP/corrosion/EIS/CV just because those are common; an experiment with, for example, GCD, DRT, or RDE data deserves the same depth.
7. Return strict JSON only, with this schema:
{
  "executive_summary": ["..."],
  "cross_technique_observations": ["..."],
  "technique_discussions": {
    "TECHNIQUE_KEY": {
      "observations": ["..."],
      "interpretations": ["..."],
      "limitations": ["..."],
      "recommended_checks": ["..."]
    }
  },
  "conclusions": ["..."],
  "global_cautions": ["..."],
  "presenter_notes": {
""" + presenter_schema + """
  }
}
Keep each visible bullet concise and presentation-ready. Presenter notes may be longer, conversational, and useful to a scientist presenting the deck or reading the Browser report. Cite points, view names, or analysis labels when available.

Include a "technique_discussions" entry and a matching "presenter_notes" bucket (same lower_snake_case key) for every technique_key listed in "techniques" below, even ones without hardcoded slide layouts. Reference vocabulary for the comparisons available per technique (use only where the JSON actually shows the corresponding data):
""" + scenario_hints + """

FARADAIQ_CONTEXT_JSON
""" + json.dumps(context, ensure_ascii=False, indent=2)


def _extract_json_object(text: str) -> dict[str, Any]:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _call_direct_llm(prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use the active Direct LLM provider. No experiment file is uploaded."""
    call = _CORE.get("_v37_call_best_llm") or _CORE.get("_v68_call_best_llm")
    if callable(call):
        answer, provider = call([
            {"role": "system", "content": "Return strict JSON only. Never fabricate scientific information."},
            {"role": "user", "content": prompt},
        ], max_tokens=5000, temperature=0.15)
        parsed = _extract_json_object(str(answer or ""))
        return parsed, {"status": "completed" if parsed else "invalid_json", "provider": {k: provider.get(k) for k in ("name", "kind", "model", "base_url", "source") if isinstance(provider, Mapping)}, "raw_answer": str(answer or "")}
    # Standalone fallback for non-GUI use: OpenAI-compatible environment only.
    import requests
    provider = os.environ.get("FARADAIQ_LLM_PROVIDER", "Ollama")
    model = os.environ.get("FARADAIQ_LLM_MODEL") or os.environ.get("OLLAMA_MODEL") or "gpt-oss:120b-cloud"
    base = (os.environ.get("FARADAIQ_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1").rstrip("/")
    if not base.endswith("/v1"): base += "/v1"
    key = os.environ.get("OLLAMA_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("LITELLM_KEY") or "ollama"
    timeout = int(float(os.environ.get("FARADAIQ_LLM_TIMEOUT_SECONDS", "3600")))
    response = requests.post(base + "/chat/completions", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json={"model": model, "messages": [{"role": "system", "content": "Return strict JSON only. Never fabricate scientific information."}, {"role": "user", "content": prompt}], "temperature": 0.15, "max_tokens": 5000}, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    answer = (((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    parsed = _extract_json_object(str(answer))
    return parsed, {"status": "completed" if parsed else "invalid_json", "provider": {"name": provider, "model": model, "base_url": base}, "raw_answer": str(answer)}


def _fallback_discussion(manifest: Mapping[str, Any]) -> dict[str, Any]:
    discovery = dict(manifest.get("discovery") or {})
    discussions: dict[str, Any] = {}
    for row in discovery.get("techniques") or []:
        key = str(row.get("technique_key") or "OTHER")
        discussions[key] = {
            "observations": [f"{row.get('dataset_count', 0)} datasets cover {row.get('point_count', 0)} points and {row.get('method_count', 0)} methods/groups."],
            "interpretations": ["Interpretation should be based on the technique-specific plots and experimental metadata shown in this deck."],
            "limitations": ["The deterministic summary does not infer mechanism without supporting metadata and validation."],
            "recommended_checks": ["Review unusual points, source tables, units, and replicate coverage before drawing conclusions."],
        }
    trends = list(discovery.get("trend_cards") or [])
    executive = [f"The experiment contains {discovery.get('measurement_count', 0)} measurement datasets, {discovery.get('analysis_count', 0)} analysis results, and {discovery.get('point_count', 0)} points/locations."]
    if trends:
        executive.extend([f"{t.get('technique_label')}: {t.get('summary')} for {t.get('metric_label', 'the selected metric').lower()}." for t in trends[:3]])
    presenter_notes = {"opening": ["Start by orienting the audience to the measurement coverage, point count, and derived-result workflow."], "ocp": ["Explain the final-time-window average and use the stability diagnostics to avoid overclaiming equilibrium."], "corrosion": ["Emphasize that robust corrosion metrics separate clear fits from review or ambiguous points and never invent missing rates."], "takedata": ["Use Takedata slides to establish spatial provenance before discussing trends versus position."], "conclusions": ["Close by separating directly observed trends from interpretation and follow-up checks."]}
    # Give every detected technique (not just OCP/corrosion/Takedata) its own
    # presenter-notes bucket, keyed the same way as the LLM-generated path, so
    # the deterministic fallback and the shared portable Browser report stay
    # technique-aware even without a live LLM call.
    for key in discussions:
        low_key = str(key).lower()
        if low_key in presenter_notes:
            continue
        presenter_notes[low_key] = [f"Present {low_key.replace('_', ' ')} results by walking through the available comparison views, then state limitations from replicate coverage, fit quality, or metadata gaps before interpreting."]
    return {"executive_summary": executive, "cross_technique_observations": [], "technique_discussions": discussions, "conclusions": ["Use the comparison slides to identify reproducible trends and then verify them against raw data, metadata, and analysis assumptions."], "global_cautions": ["Automated summaries are screening aids and do not replace expert review."], "presenter_notes": presenter_notes}


def _nice(value: Any, unit: str = "") -> str:
    try:
        x = float(value)
        if not math.isfinite(x): return "n/a"
        ax = abs(x)
        text = f"{x:.4g}" if ax == 0 or (1e-3 <= ax < 1e4) else f"{x:.3e}"
        return f"{text} {unit}".strip()
    except Exception:
        return _clean_text(value, 80) or "n/a"


def _safe_filename(value: str, max_len: int = 70) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "plot")).strip("._") or "plot"
    return text[:max_len]



def _v307_plain_number(value: Any) -> str:
    """Return a PowerPoint/matplotlib-safe numeric label with no mathtext.

    Some Windows matplotlib/mathtext combinations fail while rendering log-axis
    labels such as ``$\\mathdefault{10^{3}}$``.  PowerPoint summary plots are
    presentation images, so plain Unicode/ASCII labels are safer than mathtext.
    """
    try:
        x = float(value)
    except Exception:
        return _clean_text(value, 24)
    if not math.isfinite(x):
        return ""
    ax = abs(x)
    if ax == 0:
        return "0"
    if 1e-3 <= ax < 1e4:
        return f"{x:g}"
    return f"{x:.1e}".replace("e+0", "e+").replace("e-0", "e-")


def _v307_plain_log_tick(value: Any, pos: Any = None) -> str:
    try:
        x = float(value)
    except Exception:
        return ""
    if not math.isfinite(x) or x <= 0:
        return ""
    exp = round(math.log10(x))
    if abs(x - (10 ** exp)) <= max(1e-12, abs(x) * 1e-9):
        return f"1e{exp}" if exp else "1"
    return _v307_plain_number(x)


def _v307_disable_mathtext_for_figure(fig: Any) -> None:
    """Make all plot text safe for PowerPoint summary rendering.

    This intentionally avoids mathtext tick labels and disables parse-math on
    figure text objects when supported.  It prevents one problematic formatter
    from aborting the entire PPT job.
    """
    try:
        import matplotlib
        matplotlib.rcParams["text.usetex"] = False
        matplotlib.rcParams["axes.formatter.use_mathtext"] = False
        from matplotlib import ticker as mticker
        from matplotlib.text import Text
    except Exception:
        mticker = None
        Text = None
    try:
        for ax in getattr(fig, "axes", []) or []:
            if mticker is not None:
                try:
                    if str(ax.get_xscale()).lower() == "log":
                        ax.xaxis.set_major_formatter(mticker.FuncFormatter(_v307_plain_log_tick))
                        ax.xaxis.set_minor_formatter(mticker.NullFormatter())
                    else:
                        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, p: _v307_plain_number(v)))
                except Exception:
                    pass
                try:
                    if str(ax.get_yscale()).lower() == "log":
                        ax.yaxis.set_major_formatter(mticker.FuncFormatter(_v307_plain_log_tick))
                        ax.yaxis.set_minor_formatter(mticker.NullFormatter())
                    else:
                        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, p: _v307_plain_number(v)))
                except Exception:
                    pass
            try:
                ax.xaxis.offsetText.set_visible(False)
                ax.yaxis.offsetText.set_visible(False)
            except Exception:
                pass
        if Text is not None:
            for text in fig.findobj(match=Text):
                try:
                    text.set_parse_math(False)
                except Exception:
                    pass
    except Exception:
        pass


def _v307_safe_savefig(fig: Any, out_path: Path, **kwargs: Any) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _v307_disable_mathtext_for_figure(fig)
    try:
        fig.savefig(out_path, **kwargs)
    except Exception as exc:
        msg = str(exc)
        if "ParseException" in msg or "mathdefault" in msg or "Expected end of text" in msg:
            _v307_disable_mathtext_for_figure(fig)
            fig.canvas.draw_idle()
            fig.savefig(out_path, **kwargs)
        else:
            raise

def _apply_scientific_plot_style(ax: Any, *, title_size: float = 20, label_size: float = 17, tick_size: float = 14, legend_size: float = 12) -> None:
    """Projector-safe matplotlib styling for presentation images."""
    try:
        ax.title.set_fontsize(title_size)
        ax.title.set_fontweight("bold")
        ax.xaxis.label.set_size(label_size)
        ax.yaxis.label.set_size(label_size)
        ax.tick_params(axis="both", which="major", labelsize=tick_size, width=1.0)
        ax.tick_params(axis="both", which="minor", labelsize=max(9, tick_size-2), width=0.8)
        ax.grid(True, alpha=0.20, linewidth=0.7)
        for spine in ax.spines.values():
            spine.set_linewidth(0.9)
        leg = ax.get_legend()
        if leg is not None:
            for text in leg.get_texts():
                text.set_fontsize(legend_size)
    except Exception:
        pass


def _add_reading_note(record: dict[str, Any], *items: Any) -> None:
    notes = record.setdefault("speaker_notes", [])
    for item in items:
        txt = str(item or "").strip()
        if txt and txt not in notes:
            notes.append(txt)


def _dataset_view_values(dataset: Mapping[str, Any], view_id: str) -> tuple[list[float], list[float], Mapping[str, Any] | None]:
    view = next((v for v in dataset.get("plot_views") or [] if str(v.get("id")) == str(view_id)), None)
    if not view:
        return [], [], None
    try:
        from . import v290_portable_browser as browser
        xs, ys = browser._dataset_view_values(dataset, view)
        return list(xs), list(ys), view
    except Exception:
        return [], [], view


def _plot_comparison(manifest: Mapping[str, Any], view_row: Mapping[str, Any], out_path: Path) -> dict[str, Any]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    by_id = {str(d.get("id")): d for d in manifest.get("datasets") or []}
    curves = []
    for did in view_row.get("dataset_ids") or []:
        d = by_id.get(str(did))
        if not d: continue
        xs, ys, view = _dataset_view_values(d, str(view_row.get("view_id")))
        if len(xs) < 2 or len(ys) < 2: continue
        n = min(len(xs), len(ys))
        curves.append((d, np.asarray(xs[:n], dtype=float), np.asarray(ys[:n], dtype=float), view or {}))
    if not curves:
        return {"created": False, "curve_count": 0}
    fig, ax = plt.subplots(figsize=(13.8, 7.2), dpi=180)
    ncurves = len(curves)
    # v309: shared potential identity, scientific ordering, and labels.
    try:
        from .potential_identity_v309 import legend_labels, potential_sort_key
        if curves and all(str(c[0].get("technique_key") or "").upper() in {"EIS","GEIS","DRT"} for c in curves):
            curves = [c for _,c in sorted(enumerate(curves), key=lambda t: potential_sort_key(t[1][0], t[0]))]
        _labels, _legend_title = legend_labels([c[0] for c in curves], "Auto", [str(c[0].get("method_label") or c[0].get("display_name") or "") for c in curves])
    except Exception:
        _labels = []
        _legend_title = "Method / point / cycle"
    cmap = plt.get_cmap("viridis")
    for idx, (d, x, y, view) in enumerate(curves):
        finite = np.isfinite(x) & np.isfinite(y)
        if not finite.any(): continue
        label = _labels[idx] if idx < len(_labels) and _labels[idx] else (f"P{int(d.get('point')):03d}" if d.get("point") is not None else _clean_text(d.get("method_label") or d.get("display_name"), 28))
        ax.plot(x[finite], y[finite], linewidth=1.75 if ncurves < 30 else 1.05, alpha=0.82 if ncurves < 30 else 0.45, color=cmap(idx / max(1, ncurves - 1)), label=label)
    xcol, ycol = str(view_row.get("x_column") or "x"), str(view_row.get("y_column") or "y")
    ax.set_xlabel(xcol.replace("_", " "))
    ax.set_ylabel(ycol.replace("_", " "))
    ax.set_title(f"{view_row.get('technique_label')} · {view_row.get('view_label')}", loc="left", fontsize=18, weight="bold")
    _apply_scientific_plot_style(ax)
    if str(view_row.get("technique_key")) in {"EIS", "GEIS"} and str(view_row.get("view_id")) == "nyquist":
        ax.set_aspect("equal", adjustable="datalim")
    if any(str((c[3] or {}).get("x_scale")) == "log" for c in curves) or "frequency" in xcol.lower() or "tau" in xcol.lower():
        positives = [x[x > 0] for _, x, _, _ in curves if (x > 0).any()]
        if positives: ax.set_xscale("log")
    if ncurves <= 16:
        ax.legend(fontsize=10, ncol=2, frameon=False, title=_legend_title if _legend_title else None)
    elif all(d.get("point") is not None for d, *_ in curves):
        import matplotlib as mpl
        pts = [float(d.get("point")) for d, *_ in curves]
        norm = mpl.colors.Normalize(vmin=min(pts), vmax=max(pts))
        sm = mpl.cm.ScalarMappable(norm=norm, cmap=cmap); sm.set_array([])
        fig.colorbar(sm, ax=ax, pad=0.015, label="Point")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _v307_safe_savefig(fig, out_path, bbox_inches="tight")
    plt.close(fig)
    return {"created": True, "curve_count": ncurves, "path": str(out_path)}


def _plot_trend(trend: Mapping[str, Any], out_path: Path) -> dict[str, Any]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    vals = list(trend.get("values") or [])
    if len(vals) < 2: return {"created": False}
    x = [v.get("point") for v in vals]; y = [v.get("value") for v in vals]
    fig, ax = plt.subplots(figsize=(12.4, 6.2), dpi=180)
    ax.plot(x, y, marker="o", linewidth=2.2, markersize=6)
    ax.set_xlabel("Point / ordered location")
    ax.set_ylabel(f"{trend.get('metric_label')} {trend.get('unit') or ''}".strip())
    ax.set_title(f"{trend.get('technique_label')} · {trend.get('metric_label')} trend", loc="left", fontsize=18, weight="bold")
    _apply_scientific_plot_style(ax)
    fig.tight_layout(); out_path.parent.mkdir(parents=True, exist_ok=True); _v307_safe_savefig(fig, out_path, bbox_inches="tight"); plt.close(fig)
    return {"created": True, "path": str(out_path)}



def _derived_axis_value(row: Mapping[str, Any], axis: str) -> float | None:
    key = {"X":"x_position_um", "Y":"y_position_um", "Distance":"scan_distance_um", "Point":"point_index"}.get(str(axis), "point_index")
    try:
        value=float(row.get(key))
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _derived_axis_label(axis: str) -> str:
    return {"X":"X coordinate / µm", "Y":"Y coordinate / µm", "Distance":"Cumulative distance / µm", "Point":"Point"}.get(str(axis), "Point")


def _plot_derived_metric(result_set: Mapping[str, Any], metric: Mapping[str, Any], axis: str, out_path: Path) -> dict[str, Any]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    values=[]
    for row in result_set.get("rows") or []:
        if not isinstance(row, Mapping): continue
        x=_derived_axis_value(row, axis)
        try: y=float(row.get(str(metric.get("id"))))
        except Exception: y=float("nan")
        if x is None or not math.isfinite(y): continue
        values.append((x,y,row))
    if not values:
        return {"created":False,"count":0}
    values.sort(key=lambda v:v[0])
    fig,ax=plt.subplots(figsize=(12.4,6.0),dpi=180)
    x=np.asarray([v[0] for v in values],dtype=float); y=np.asarray([v[1] for v in values],dtype=float)
    ax.plot(x,y,marker="o",linewidth=2.2,markersize=6)
    # Highlight non-clear corrosion points without changing the reported metric.
    bad=[v for v in values if str(v[2].get("ambiguity_status") or "").upper() not in {"","CLEAR"}]
    if bad:
        ax.scatter([v[0] for v in bad],[v[1] for v in bad],marker="x",s=78,linewidths=2.0,label="Review / ambiguous")
        ax.legend(frameon=False,fontsize=11)
    ax.set_xlabel(_derived_axis_label(axis))
    label=str(metric.get("label") or metric.get("id") or "Metric"); unit=str(metric.get("unit") or "")
    ax.set_ylabel(f"{label} / {unit}" if unit else label)
    ax.set_title(f"{label} vs {_derived_axis_label(axis).replace(' / µm','')}",loc="left",fontsize=18,weight="bold")
    _apply_scientific_plot_style(ax); fig.tight_layout(); out_path.parent.mkdir(parents=True,exist_ok=True); _v307_safe_savefig(fig,out_path,bbox_inches="tight"); plt.close(fig)
    return {"created":True,"count":len(values),"path":str(out_path)}



def _plot_takedata_xy(result_set: Mapping[str, Any], out_path: Path) -> dict[str, Any]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    vals=[]
    for row in result_set.get("rows") or []:
        try:
            x=float(row.get("x_position_um")); y=float(row.get("y_position_um")); p=int(row.get("point_index") or len(vals)+1)
        except Exception:
            continue
        if math.isfinite(x) and math.isfinite(y): vals.append((x,y,p))
    if len(vals)<2: return {"created":False,"count":len(vals)}
    fig,ax=plt.subplots(figsize=(12.4,6.4),dpi=180)
    x=np.asarray([v[0] for v in vals]); y=np.asarray([v[1] for v in vals])
    ax.plot(x,y,marker="o",linewidth=2.0,markersize=6)
    ax.scatter([x[0]],[y[0]],marker="s",s=90,label=f"Start · P{vals[0][2]}")
    ax.scatter([x[-1]],[y[-1]],marker="^",s=95,label=f"End · P{vals[-1][2]}")
    ax.set_xlabel("X coordinate / µm"); ax.set_ylabel("Y coordinate / µm")
    ax.set_title("Measured Takedata stage trajectory",loc="left",fontsize=18,weight="bold")
    _apply_scientific_plot_style(ax); ax.legend(frameon=False,fontsize=11)
    # Preserve the physical aspect when both dimensions vary materially.
    if np.ptp(x)>0 and np.ptp(y)>0: ax.set_aspect("equal",adjustable="datalim")
    fig.tight_layout(); out_path.parent.mkdir(parents=True,exist_ok=True); _v307_safe_savefig(fig,out_path,bbox_inches="tight"); plt.close(fig)
    return {"created":True,"count":len(vals),"path":str(out_path)}


def _plot_takedata_raw_metric(result_set: Mapping[str, Any], y_key: str, label: str, unit: str, out_path: Path, *, use_time: bool=False) -> dict[str, Any]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    vals=[]
    for row in result_set.get("raw_rows") or []:
        try:
            y=float(row.get(y_key))
        except Exception:
            continue
        if not math.isfinite(y): continue
        x_key="elapsed_from_start_s" if use_time else "record_index"
        try: x=float(row.get(x_key))
        except Exception: continue
        if math.isfinite(x): vals.append((x,y))
    if len(vals)<2: return {"created":False,"count":len(vals)}
    fig,ax=plt.subplots(figsize=(12.4,6.0),dpi=180)
    ax.plot([v[0] for v in vals],[v[1] for v in vals],linewidth=2.0)
    ax.set_xlabel("Elapsed time / s" if use_time else "Takedata acquisition record")
    ax.set_ylabel(f"{label} / {unit}" if unit else label)
    ax.set_title(f"{label} vs {'elapsed time' if use_time else 'acquisition record'}",loc="left",fontsize=18,weight="bold")
    _apply_scientific_plot_style(ax); fig.tight_layout(); out_path.parent.mkdir(parents=True,exist_ok=True); _v307_safe_savefig(fig,out_path,bbox_inches="tight"); plt.close(fig)
    return {"created":True,"count":len(vals),"path":str(out_path)}

def _find_media_paths(root: Path, manifest: Mapping[str, Any], limit: int = 12) -> list[tuple[Path, str]]:
    rows = []
    for d in manifest.get("datasets") or []:
        if not d.get("is_media"): continue
        rel = d.get("portable_preview_path") or d.get("physical_href") or d.get("physical_path")
        if not rel: continue
        p = root / str(rel)
        if p.is_file(): rows.append((p, _clean_text(d.get("display_name"), 80)))
        if len(rows) >= limit: break
    return rows


def _presentation_imports():
    from pptx import Presentation
    from pptx.enum.text import PP_ALIGN, MSO_AUTO_SIZE
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt
    return Presentation, PP_ALIGN, MSO_AUTO_SIZE, MSO_SHAPE, RGBColor, Inches, Pt


def _build_presentation(root: Path, manifest: Mapping[str, Any], narrative: Mapping[str, Any], assets: Path, detail_level: str) -> tuple[Any, list[dict[str, Any]]]:
    Presentation, PP_ALIGN, MSO_AUTO_SIZE, MSO_SHAPE, RGBColor, Inches, Pt = _presentation_imports()
    prs = Presentation(); prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    # v302 presentation theme: restrained, light, scientific, and projector-safe with large text.
    NAVY = RGBColor(255, 255, 255)      # slide background
    PANEL = RGBColor(246, 248, 250)     # soft scientific panel
    BLUE = RGBColor(20, 67, 104)        # primary accent
    CYAN = RGBColor(0, 101, 128)        # secondary accent
    WHITE = RGBColor(24, 36, 48)        # primary text (historical variable name)
    MUTED = RGBColor(82, 94, 107)       # secondary text
    GREEN = RGBColor(42, 111, 78)
    AMBER = RGBColor(150, 91, 14)
    TRUE_WHITE = RGBColor(255, 255, 255)
    slide_records: list[dict[str, Any]] = []

    def bg(slide):
        fill = slide.background.fill; fill.solid(); fill.fore_color.rgb = NAVY

    def _trim_for_box(text: Any, max_chars: int) -> str:
        txt = _clean_text(text, max_chars + 80)
        if len(txt) <= max_chars:
            return txt
        return txt[:max(12, max_chars - 1)].rstrip() + "…"

    def _estimate_font_pt(text: str, w_in: float, h_in: float, requested: float, min_pt: float = 9.5) -> float:
        # Conservative layout estimate so generated decks do not rely only on
        # PowerPoint's renderer.  The final text frame also uses auto-size.
        txt = str(text or "")
        lines = max(1, txt.count("\n") + 1)
        chars = max(1, len(txt))
        chars_per_line = max(8.0, w_in * 12.0)
        estimated_lines = max(lines, math.ceil(chars / chars_per_line))
        available_lines = max(1.0, (h_in * 72.0) / (requested * 1.24))
        if estimated_lines <= available_lines:
            return requested
        scaled = requested * available_lines / float(estimated_lines)
        return max(min_pt, min(requested, scaled))

    def _fit_textframe(shape: Any, base_pt: float = 12, min_pt: float = 9.5) -> None:
        try:
            tf = shape.text_frame
        except Exception:
            return
        try:
            tf.word_wrap = True
            tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        except Exception:
            pass
        try:
            w_in = float(shape.width) / 914400.0
            h_in = float(shape.height) / 914400.0
        except Exception:
            w_in, h_in = 3.0, 1.0
        text = "\n".join(p.text for p in tf.paragraphs)
        pt = _estimate_font_pt(text, w_in, h_in, float(base_pt), min_pt)
        for par in tf.paragraphs:
            try:
                par.font.name = "Aptos"
                par.font.size = Pt(pt)
            except Exception:
                pass
            for run in getattr(par, "runs", []):
                try:
                    run.font.name = "Aptos"
                    run.font.size = Pt(pt)
                except Exception:
                    pass

    def textbox(slide, x, y, w, h, text, size=18, color=WHITE, bold=False, align=None, margin=0.05):
        # All text boxes are created with PowerPoint auto-fit enabled and a
        # conservative pre-shrink estimate.  This prevents text from escaping
        # chart notes, footers, title boxes, and media captions on systems with
        # different font metrics.
        box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = box.text_frame
        tf.clear()
        tf.margin_left = tf.margin_right = Inches(margin)
        tf.margin_top = tf.margin_bottom = Inches(margin)
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        safe_text = _trim_for_box(text, max(36, int(w * h * 95)))
        p = tf.paragraphs[0]
        p.text = str(safe_text)
        p.font.name = "Aptos"
        p.font.size = Pt(_estimate_font_pt(p.text, w, h, float(size), 10.0))
        p.font.bold = bold
        p.font.color.rgb = color
        if align is not None:
            p.alignment = align
        _fit_textframe(box, size, 6.5)
        return box

    def title(slide, heading, sub=""):
        textbox(slide, 0.55, 0.28, 12.2, 0.54, heading, 30, WHITE, True)
        if sub: textbox(slide, 0.58, 0.88, 12.0, 0.34, sub, 14, MUTED)
        line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.55), Inches(1.27), Inches(12.2), Inches(0.025)); line.fill.solid(); line.fill.fore_color.rgb = BLUE; line.line.fill.background()

    def footer(slide, idx):
        # The title slide carries an explicit authorship footer. Remaining
        # slides retain the compact version + experiment provenance footer.
        footer_text = (
            FRONT_SLIDE_FOOTER
            if idx == 1
            else f"FaradaIQ eStudio {VERSION} · {manifest.get('experiment_name')}"
        )
        textbox(slide, 0.55, 7.12, 11.6, 0.25, footer_text, 9.5, MUTED)
        textbox(slide, 12.2, 7.12, 0.55, 0.25, str(idx), 9.5, MUTED, align=PP_ALIGN.RIGHT)

    def bullet_box(slide, x, y, w, h, heading, bullets, accent=BLUE, max_bullets=7):
        rect = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        rect.fill.solid(); rect.fill.fore_color.rgb = PANEL; rect.line.color.rgb = accent
        textbox(slide, x+0.18, y+0.12, w-0.36, 0.40, heading, 16, WHITE, True)
        box = slide.shapes.add_textbox(Inches(x+0.18), Inches(y+0.55), Inches(w-0.36), Inches(h-0.72))
        tf = box.text_frame
        tf.clear(); tf.word_wrap=True; tf.auto_size=MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        tf.margin_left=tf.margin_right=Inches(0.02); tf.margin_top=tf.margin_bottom=Inches(0.02)
        raw = [_clean_text(b, 420) for b in bullets if _clean_text(b, 420)]
        if not raw:
            raw=["No additional evidence-grounded statement is available."]
        shown = raw[:max_bullets]
        if len(raw) > max_bullets and shown:
            shown[-1] = f"{_trim_for_box(shown[-1], 90)}; plus {len(raw)-max_bullets} more item(s) in the summary JSON."
        # Estimate per-bullet character budget from the actual box area.
        per_bullet_cap = max(55, min(180, int((w * max(0.6, h - 0.72) * 78) / max(1, len(shown)))))
        clean = [_trim_for_box(b, per_bullet_cap) for b in shown]
        base_pt = 14.0 if len(clean) <= 4 else 12.3 if len(clean) <= 7 else 11.0
        for i,b in enumerate(clean):
            p=tf.paragraphs[0] if i==0 else tf.add_paragraph()
            p.text="• "+b
            p.level=0
            p.font.name="Aptos"
            p.font.size=Pt(base_pt)
            p.font.color.rgb=WHITE
            p.space_after=Pt(3 if len(clean) > 6 else 5)
        _fit_textframe(box, base_pt, 10.0)
        return rect

    def _fit_table(table: Any, base_pt: float = 11.0, min_pt: float = 9.0) -> None:
        for ri, row in enumerate(table.rows):
            for ci, cell in enumerate(row.cells):
                try:
                    tf = cell.text_frame
                    tf.word_wrap = True
                    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
                    tf.margin_left = tf.margin_right = Inches(0.02)
                    tf.margin_top = tf.margin_bottom = Inches(0.01)
                    text = "\n".join(p.text for p in tf.paragraphs)
                    w_in = float(table.columns[ci].width) / 914400.0
                    h_in = float(row.height) / 914400.0 if row.height else 0.34
                    pt = _estimate_font_pt(text, w_in, h_in, base_pt if ri else min(10.0, base_pt+0.8), min_pt)
                    for p in tf.paragraphs:
                        p.font.name = "Aptos"
                        p.font.size = Pt(pt)
                        p.font.color.rgb = WHITE
                except Exception:
                    pass

    def _enforce_text_containment(prs_obj: Any) -> None:
        for sl in prs_obj.slides:
            for shp in sl.shapes:
                try:
                    if getattr(shp, "has_text_frame", False):
                        _fit_textframe(shp, 12.5, 10.0)
                except Exception:
                    pass
                try:
                    if getattr(shp, "has_table", False):
                        _fit_table(shp.table, 10.5, 9.0)
                except Exception:
                    pass

    def add_slide(name, sub="", speaker_notes: Sequence[str] | None = None):
        slide=prs.slides.add_slide(blank); bg(slide); title(slide,name,sub)
        slide_records.append({"index":len(prs.slides),"title":name,"subtitle":sub,"speaker_notes":[str(x) for x in (speaker_notes or []) if str(x).strip()]})
        return slide

    def _speaker_note_text(name: str, sub: str = "", extra: Sequence[str] | None = None) -> str:
        low=str(name or "").lower()
        notes_obj = narrative.get("presenter_notes") if isinstance(narrative.get("presenter_notes"), Mapping) else {}
        lines=[f"Open with the slide title: {name}."]
        if sub: lines.append(f"Frame the audience with this context: {sub}.")

        def add_bucket(key: str) -> None:
            for item in (notes_obj.get(key) if isinstance(notes_obj, Mapping) else []) or []:
                txt=str(item or "").strip()
                if txt and txt not in lines: lines.append(txt)

        if "ocp" in low:
            add_bucket("ocp")
            lines += ["Explain that the reported OCP is a deterministic final-time-window average, not proof of equilibrium by itself.", "Use the SD/SEM/tail-drift diagnostics to discuss whether the selected final window is stable enough for comparison."]
        elif "corrosion" in low or "tafel" in low or "ecorr" in low or "icorr" in low or "fit r²" in low:
            add_bucket("corrosion")
            lines += ["State Ecorr, Icorr, current density, or corrosion rate only for points where the required inputs and fit quality are valid.", "Call out REVIEW, AMBIGUOUS, and NO CLEAR ECORR points explicitly; multiple crossing families or significant dips are a caution flag, not a result to average away."]
        elif "takedata" in low or "spatial" in low or "position" in low:
            add_bucket("takedata")
            lines += ["Use Takedata coordinates as the spatial reference and distinguish raw acquisition chronology from collapsed spatial stations.", "Point out dominant scan axis, XY trajectory, Z variation, timing, and repeated station records before interpreting downstream electrochemical trends."]
        elif "eis" in low or "nyquist" in low or "bode" in low:
            add_bucket("eis")
            lines += ["Discuss EIS trends in terms of comparable frequency coverage, arc/intercept behavior, and whether fits/residuals support the interpretation."]
        elif "cv" in low or "lsv" in low or "voltam" in low:
            add_bucket("cv_lsv")
            lines += ["For voltammetry, separate observed curve-shape changes from kinetic interpretation unless a justified branch/window is shown."]
        elif "conclusion" in low or "next check" in low:
            add_bucket("conclusions")
            lines += ["Close by separating direct observations from interpretation and from follow-up checks."]
        else:
            # Generic technique routing: match the slide title against every
            # technique actually detected in this experiment (not just the
            # hardcoded OCP/corrosion/Takedata/EIS/CV_LSV buckets above), so
            # techniques like CA, CP, GCD, DRT, RDE, etc. get their own
            # presenter-notes bucket instead of silently falling back to the
            # generic "opening" guidance.
            matched_key = None
            for key in (notes_obj.keys() if isinstance(notes_obj, Mapping) else []):
                k = str(key)
                if k in {"opening", "conclusions"}:
                    continue
                label = k.replace("_", " ")
                if k.lower() in low or label in low:
                    matched_key = k
                    break
            if matched_key:
                add_bucket(matched_key)
                lines += [f"Describe the {matched_key.replace('_', ' ')} comparisons shown here, separating direct observations from interpretation.", "Note any coverage, replicate, or fit-quality limitations before drawing conclusions."]
            else:
                add_bucket("opening")
                lines += ["Describe the dominant visual trend first, then mention exceptions and data-quality limits."]

        try:
            for key, disc in (narrative.get("technique_discussions") or {}).items():
                if str(key).lower() in low and isinstance(disc, Mapping):
                    for section in ("observations", "interpretations", "limitations", "recommended_checks"):
                        for txt in list(disc.get(section) or [])[:2]:
                            t=str(txt or "").strip()
                            if t and t not in lines: lines.append(t)
        except Exception:
            pass
        for item in extra or []:
            txt=str(item or "").strip()
            if txt and txt not in lines: lines.append(txt)
        final=[]
        for x in lines:
            if x not in final:
                final.append(x)
        return "Presenter notes\n" + "\n".join(f"• {x}" for x in final[:12])

    def _set_speaker_notes(slide: Any, name: str, sub: str = "", extra: Sequence[str] | None = None) -> None:
        try:
            tf=slide.notes_slide.notes_text_frame
            tf.clear(); tf.text=_speaker_note_text(name,sub,extra)
        except Exception:
            pass

    # Title slide
    s=prs.slides.add_slide(blank); bg(s)
    textbox(s,0.7,1.18,11.9,0.78,"FaradaIQ Scientific Data Summary",38,WHITE,True)
    textbox(s,0.72,2.04,11.8,0.58,_clean_text(manifest.get("experiment_name"),150),24,CYAN,True)
    textbox(s,0.72,2.82,10.9,0.58,"Presentation-ready technique-aware comparisons, derived metrics, spatial trends, and evidence-grounded discussion",18,MUTED)
    d=manifest.get("discovery") or {}
    stats=[("Points",d.get("point_count",0)),("Measurements",d.get("measurement_count",0)),("Analyses",d.get("analysis_count",0)),("Media",d.get("media_count",0))]
    for i,(lab,val) in enumerate(stats):
        x=0.75+i*3.05; rect=s.shapes.add_shape(MSO_SHAPE.RECTANGLE,Inches(x),Inches(4.0),Inches(2.65),Inches(1.05)); rect.fill.solid(); rect.fill.fore_color.rgb=PANEL; rect.line.color.rgb=BLUE
        textbox(s,x+0.15,4.12,2.35,0.42,str(val),28,WHITE,True,PP_ALIGN.CENTER); textbox(s,x+0.15,4.62,2.35,0.30,lab,12,MUTED,align=PP_ALIGN.CENTER)
    textbox(s,0.72,6.4,11.8,0.3,f"Generated {datetime.now().isoformat(timespec='minutes')} · Direct LLM discussion: {'included' if narrative.get('_llm_used') else 'deterministic fallback'}",10,MUTED)
    slide_records.append({"index":1,"title":"Title","subtitle":_clean_text(manifest.get("experiment_name"),150)})

    # Executive summary
    s=add_slide("Executive summary","Observations and discussion are constrained to the generated FaradaIQ manifest and derived metrics.")
    bullet_box(s,0.65,1.42,6.0,4.95,"Evidence-grounded summary",narrative.get("executive_summary") or [],BLUE,8)
    bullet_box(s,6.82,1.42,5.85,2.35,"Cross-technique observations",narrative.get("cross_technique_observations") or ["Review the technique sections for compatible cross-point and cross-method patterns."],GREEN,5)
    bullet_box(s,6.82,3.92,5.85,2.45,"Global cautions",narrative.get("global_cautions") or [],AMBER,5)

    # Coverage
    s=add_slide("Experiment and data coverage","Scientific measurements and analyses are separated from parser/provenance support files.")
    tech=list(d.get("techniques") or [])
    table=s.shapes.add_table(len(tech)+1,6,Inches(0.65),Inches(1.42),Inches(12.0),Inches(min(5.6,0.36*(len(tech)+1)))).table
    headers=["Technique / analysis","Family","Datasets","Points","Methods","Analyses"]
    for c,h in enumerate(headers): table.cell(0,c).text=h
    for r,row in enumerate(tech,1):
        vals=[row.get("technique_label"),row.get("family"),row.get("dataset_count"),row.get("point_count"),row.get("method_count"),row.get("analysis_count")]
        for c,v in enumerate(vals): table.cell(r,c).text=str(v or 0)
    for row in table.rows:
        for cell in row.cells:
            cell.fill.solid(); cell.fill.fore_color.rgb=PANEL; cell.text_frame.paragraphs[0].font.size=Pt(11.5); cell.text_frame.paragraphs[0].font.color.rgb=WHITE
    for cell in table.rows[0].cells: cell.fill.fore_color.rgb=RGBColor(24,76,111); cell.text_frame.paragraphs[0].font.bold=True; cell.text_frame.paragraphs[0].font.color.rgb=TRUE_WHITE

    # Recommended comparisons
    recs=list(d.get("recommended_views") or (manifest.get("comparison") or {}).get("recommended") or [])
    s=add_slide("Recommended scientific comparisons","Prioritized from actual columns, point coverage, method coverage, and technique semantics.")
    y=1.42
    for i,r in enumerate(recs[:8],1):
        rect=s.shapes.add_shape(MSO_SHAPE.RECTANGLE,Inches(0.65 if i<=4 else 6.73),Inches(y if i<=4 else 1.42+(i-5)*1.28),Inches(5.9),Inches(1.08)); rect.fill.solid(); rect.fill.fore_color.rgb=PANEL; rect.line.color.rgb=BLUE
        yy=y if i<=4 else 1.42+(i-5)*1.28
        textbox(s,(0.85 if i<=4 else 6.93),yy+0.12,5.45,0.25,f"{r.get('view_label')} · {r.get('technique_label')}",12,WHITE,True)
        textbox(s,(0.85 if i<=4 else 6.93),yy+0.43,5.45,0.42,f"{r.get('dataset_count',0)} datasets · {r.get('unique_points',0)} points · {len(r.get('methods') or [])} methods. {_clean_text(r.get('description'),110)}",9.5,MUTED)
        if i<=4: y+=1.28

    # Trend summary slides
    for t_index,trend in enumerate((d.get("trend_cards") or [])[:10],1):
        img=assets/f"trend_{t_index:02d}_{_safe_filename(str(trend.get('technique_key')))}.png"; plot=_plot_trend(trend,img)
        s=add_slide(f"Trend · {trend.get('technique_label')}",f"{trend.get('metric_label')} across {trend.get('point_count')} points")
        if plot.get("created"): s.shapes.add_picture(str(img),Inches(0.72),Inches(1.43),width=Inches(11.9),height=Inches(5.28))
        bullets=[trend.get("summary"),f"Minimum: Point {trend.get('min_point')} ({_nice(trend.get('min_value'),trend.get('unit') or '')})",f"Maximum: Point {trend.get('max_point')} ({_nice(trend.get('max_value'),trend.get('unit') or '')})",f"Start to end: {_nice(trend.get('start_value'),trend.get('unit') or '')} → {_nice(trend.get('end_value'),trend.get('unit') or '')}"]
        _add_reading_note(slide_records[-1], *bullets)

    # v300 technique-aware OCP/corrosion/Takedata report layer shared with the portable Browser.
    # Presentation rule: no more than two scientific plots per slide.  This is
    # intentionally less dense than the Browser so the generated deck is ready
    # to present without squeezing labels or legends.
    electro = manifest.get("electrochemistry_results") if isinstance(manifest.get("electrochemistry_results"), Mapping) else {}

    for oi, result_set in enumerate(electro.get("ocp") or [], 1):
        axes=[str(a.get("id")) for a in result_set.get("axis_options") or [] if str(a.get("id")) in {"Point","X","Y","Distance"}]
        axes=list(dict.fromkeys(axes)) or ["Point"]
        metric={"id":"mean_ocp_V","label":"Average OCP","unit":"V"}
        for ai,axis in enumerate(axes,1):
            axis_note={"Point":"Acquisition order is preserved.","X":"X values come from saved Takedata provenance.","Y":"Y values come from saved Takedata provenance.","Distance":"Distance is cumulative XY stage travel from saved Takedata coordinates."}.get(axis,"")
            s=add_slide(
                f"OCP average vs {_derived_axis_label(axis).replace(' / µm','')}",
                f"Final {result_set.get('final_time_percent') or 'selected'}% of elapsed time · {result_set.get('row_count',0)} trace(s)",
                speaker_notes=[
                    f"Window: final {result_set.get('final_time_percent') or 'selected'}% of elapsed time.",
                    axis_note,
                    "Error bars/variability come from the selected final-time window where available.",
                    "The final-window mean is a deterministic reduction and is not by itself proof of electrochemical equilibrium.",
                ],
            )
            img=assets/f"ocp_{oi:02d}_{ai:02d}_{axis.lower()}.png"; made=_plot_derived_metric(result_set,metric,axis,img)
            if made.get("created"):
                s.shapes.add_picture(str(img),Inches(0.72),Inches(1.40),width=Inches(11.9),height=Inches(5.34))
            else:
                bullet_box(s,0.85,1.65,11.6,4.75,"Plot unavailable",[f"No finite values are available on the {_derived_axis_label(axis)} axis."],AMBER,3)

        diag_metrics=[m for m in result_set.get("metrics") or [] if str(m.get("id")) in {"std_ocp_V","sem_ocp_V","tail_drift_V_per_s"}]
        if diag_metrics:
            axis=str(result_set.get("default_axis") or "Point")
            if axis not in axes: axis=axes[0]
            for chunk_i in range(0,len(diag_metrics),2):
                chunk=diag_metrics[chunk_i:chunk_i+2]
                s=add_slide("OCP stability diagnostics", f"Final-time-window diagnostics · horizontal axis: {_derived_axis_label(axis)}")
                positions=[(0.65,1.48),(6.75,1.48)]
                for mi,m in enumerate(chunk):
                    img=assets/f"ocp_{oi:02d}_diag_{chunk_i+mi+1}.png"; made=_plot_derived_metric(result_set,m,axis,img); x,y=positions[mi]
                    if made.get("created"): s.shapes.add_picture(str(img),Inches(x),Inches(y),width=Inches(5.9),height=Inches(4.95))
                if len(chunk)==1 and made.get("created"):
                    # One diagnostic gets the full slide width; explanatory
                    # guidance belongs in Presenter View rather than squeezing the plot.
                    try:
                        pic=s.shapes[-1]; pic.left=Inches(0.72); pic.top=Inches(1.48); pic.width=Inches(11.9); pic.height=Inches(4.95)
                    except Exception:
                        pass
                    slide_records[-1]["speaker_notes"] += [
                        "Use SD/SEM to communicate within-window variability and tail drift to identify continued potential evolution.",
                        "These diagnostics support stability assessment but do not independently establish equilibrium.",
                    ]

    for ci, result_set in enumerate(electro.get("corrosion") or [], 1):
        axes=[str(a.get("id")) for a in result_set.get("axis_options") or []]
        primary_axis=str(result_set.get("default_axis") or "Point")
        if primary_axis not in axes: primary_axis=axes[0] if axes else "Point"
        metric_by_id={str(m.get("id")):m for m in result_set.get("metrics") or []}
        report_ids=["ecorr_V","icorr_A","icorr_A_cm2","cathodic_tafel_mV_dec","anodic_tafel_mV_dec","cathodic_r2","anodic_r2","corrosion_rate_mm_per_year"]
        for mid in report_ids:
            m=metric_by_id.get(mid)
            if not m: continue
            plot_axes=["Point"]
            if primary_axis!="Point": plot_axes.append(primary_axis)
            gate_note = (
                "Penetration rate is reported only when valid area, equivalent weight, and density were supplied." if mid=="corrosion_rate_mm_per_year" else
                "Current density is reported only when a valid electrode/wetted area was supplied." if mid=="icorr_A_cm2" else
                "Review/ambiguous points remain visible and must not be interpreted as equivalent to CLEAR fits."
            )
            for axis in plot_axes:
                axis_label = _derived_axis_label(axis).replace(" / µm", "")
                s=add_slide(f"{m.get('label') or mid} vs {axis_label}", f"Robust corrosion screening · {result_set.get('row_count',0)} polarization point(s)", speaker_notes=[gate_note, f"Horizontal axis: {axis_label}."])
                img=assets/f"corrosion_{ci:02d}_{mid}_{axis.lower()}.png"; made=_plot_derived_metric(result_set,m,axis,img)
                if made.get("created"):
                    s.shapes.add_picture(str(img),Inches(0.72),Inches(1.43),width=Inches(11.9),height=Inches(5.28))
                else:
                    bullet_box(s,0.85,1.65,11.6,4.75,"Plot unavailable",[f"No finite values are available for {m.get('label') or mid} on the {axis_label} axis."],AMBER,3)
                if axis == "Point" and primary_axis == "Point":
                    slide_records[-1]["speaker_notes"].append("Point order is shown because no independent spatial axis is available.")

        review=list(result_set.get("review_rows") or [])
        counts=result_set.get("status_counts") or {}
        s=add_slide("Corrosion result quality and ambiguity",f"CLEAR {counts.get('CLEAR',0)} · REVIEW {counts.get('REVIEW',0)} · AMBIGUOUS {counts.get('AMBIGUOUS',0)} · NO CLEAR ECORR {counts.get('NO_CLEAR_ECORR',0)}")
        if review:
            chunk=review[:13]
            table=s.shapes.add_table(len(chunk)+1,5,Inches(0.65),Inches(1.45),Inches(12.0),Inches(min(5.15,0.36*(len(chunk)+1)))).table
            for c,h in enumerate(["Point","Status","Flags","Crossing families","Significant dips"]): table.cell(0,c).text=h
            for r,row in enumerate(chunk,1):
                vals=[row.get("point_index"),row.get("status"),", ".join(map(str,row.get("flags") or [])),row.get("zero_crossing_cluster_count"),row.get("dip_count")]
                for c,v in enumerate(vals): table.cell(r,c).text=_clean_text(v,90)
            for rr in table.rows:
                for cell in rr.cells: cell.fill.solid(); cell.fill.fore_color.rgb=PANEL; cell.text_frame.paragraphs[0].font.size=Pt(10.5); cell.text_frame.paragraphs[0].font.color.rgb=WHITE
            for cell in table.rows[0].cells: cell.fill.fore_color.rgb=RGBColor(109,75,25); cell.text_frame.paragraphs[0].font.bold=True; cell.text_frame.paragraphs[0].font.color.rgb=TRUE_WHITE
        else:
            bullet_box(s,0.9,1.65,11.4,4.7,"Quality summary",[
                "All reported points are CLEAR under the robust screening rules.",
                "Multiple zero-crossing families and significant low-current dips are reported explicitly when detected.",
                "Missing area/material constants remain blank and are never invented for current density or penetration-rate calculations.",
            ],GREEN,6)

    # Takedata is shown explicitly so the spatial coordinate assignment is
    # auditable before OCP/Tafel/EIS spatial trends are interpreted.
    for ti, result_set in enumerate(electro.get("takedata") or [], 1):
        diag=result_set.get("diagnostics") if isinstance(result_set.get("diagnostics"),Mapping) else {}
        quality=str(result_set.get("coordinate_quality") or "coordinate provenance retained")
        xy=assets/f"takedata_{ti:02d}_xy.png"; xy_made=_plot_takedata_xy(result_set,xy)
        s=add_slide("Takedata spatial trajectory", f"{result_set.get('row_count',0)} spatial station(s) · {quality}")
        if xy_made.get("created"): s.shapes.add_picture(str(xy),Inches(0.72),Inches(1.43),width=Inches(11.9),height=Inches(5.28))
        bullets=[
            f"Dominant 1-D axis: {diag.get('dominant_axis') or result_set.get('default_axis') or 'Point'}.",
            f"Raw Takedata records: {diag.get('raw_record_count',result_set.get('raw_record_count',0))}; spatial stations: {diag.get('station_count',result_set.get('row_count',0))}.",
            f"X span: {_nice((_finite(diag.get('x_span_m')) or 0)*1e6,'µm')}; Y span: {_nice((_finite(diag.get('y_span_m')) or 0)*1e6,'µm')}; Z span: {_nice((_finite(diag.get('z_span_m')) or 0)*1e6,'µm')}.",
        ]
        if _finite(diag.get("median_step_m")) is not None: bullets.append(f"Median XY station step: {_nice(float(diag.get('median_step_m'))*1e6,'µm')}.")
        bullets += list(result_set.get("warnings") or [])[:2]
        _add_reading_note(slide_records[-1], *bullets)

        m_by={str(m.get("id")):m for m in result_set.get("metrics") or []}
        coord_pairs=[("x_position_um","y_position_um"),("z_position_um","step_distance_um"),("delta_x_um","delta_y_um")]
        for pi,pair in enumerate(coord_pairs,1):
            metrics=[m_by[k] for k in pair if k in m_by]
            if not metrics: continue
            for mi,m in enumerate(metrics[:2]):
                s=add_slide(f"Takedata coordinate diagnostics · {m.get('label') or m.get('id')}", "Measured station values vs acquisition point", speaker_notes=["These are the coordinates used to register point-derived electrochemical metrics.", "Inspect unexpected jumps or nonuniform spacing before interpreting a spatial electrochemical trend."])
                img=assets/f"takedata_{ti:02d}_coord_{pi}_{mi}.png"; made=_plot_derived_metric(result_set,m,"Point",img)
                if made.get("created"): s.shapes.add_picture(str(img),Inches(0.72),Inches(1.43),width=Inches(11.9),height=Inches(5.28))

        raw_specs=[("emon_V","Takedata Emon","V",False),("imon_A","Takedata Imon","A",False),("x_position_um","Stage X","µm",True),("y_position_um","Stage Y","µm",True),("z_position_um","Stage Z","µm",True)]
        made_specs=[]
        for ri,(key,label,unit,use_time) in enumerate(raw_specs,1):
            img=assets/f"takedata_{ti:02d}_raw_{ri}.png"; made=_plot_takedata_raw_metric(result_set,key,label,unit,img,use_time=use_time)
            if made.get("created"): made_specs.append((img,label))
        for img,label in made_specs:
            s=add_slide(f"Takedata acquisition diagnostics · {label}", "Raw acquisition-order Takedata fields retained separately from collapsed spatial stations", speaker_notes=["Raw Takedata records are shown without collapsing acquisition chronology.", "Spatial station collapsing is used only for point-coordinate registration and is recorded in provenance."])
            s.shapes.add_picture(str(img),Inches(0.72),Inches(1.43),width=Inches(11.9),height=Inches(5.28))

    # Technique sections with scenario coverage and every user-facing comparison view.
    matrix=list((manifest.get("comparison") or {}).get("matrix") or [])
    by_tech=defaultdict(list)
    for row in matrix: by_tech[str(row.get("technique_key") or "OTHER")].append(row)
    discussion_map=dict(narrative.get("technique_discussions") or {})
    detected=[str(r.get("technique_key")) for r in tech]
    max_views_per_tech = 999 if detail_level == "comprehensive" else (5 if detail_level == "standard" else 2)
    for key in detected:
        row=next((r for r in tech if str(r.get("technique_key"))==key),{})
        views=sorted(by_tech.get(key,[]),key=lambda r:(-int(r.get("score") or 0),str(r.get("view_label"))))
        scenarios=TECHNIQUE_SCENARIOS.get(key,TECHNIQUE_SCENARIOS.get("SUMMARY",[]))
        s=add_slide(f"{row.get('technique_label') or key} · comparison plan",f"{row.get('dataset_count',0)} datasets · {row.get('point_count',0)} points · {row.get('method_count',0)} methods/groups")
        available=[str(v.get("view_label")) for v in views]
        bullet_box(s,0.65,1.42,6.1,4.95,"Technique-aware comparison scenarios",[f"{name}: {desc}" for name,desc in scenarios],BLUE,9)
        bullet_box(s,6.92,1.42,5.75,2.35,"Available in this experiment",available[:8] or ["No valid multi-dataset comparison view was found from the available columns."],GREEN,8)
        disc=discussion_map.get(key) if isinstance(discussion_map.get(key),Mapping) else {}
        combined=(list(disc.get("observations") or [])+list(disc.get("interpretations") or [])+list(disc.get("limitations") or [])+list(disc.get("recommended_checks") or []))
        bullet_box(s,6.92,3.92,5.75,2.45,"Discussion and checks",combined,AMBER,7)
        for vi,v in enumerate(views[:max_views_per_tech],1):
            img=assets/f"comparison_{_safe_filename(key)}_{vi:03d}_{_safe_filename(str(v.get('view_id')))}.png"; result=_plot_comparison(manifest,v,img)
            s=add_slide(f"{v.get('technique_label')} · {v.get('view_label')}",f"{v.get('dataset_count',0)} compatible datasets · {v.get('unique_points',0)} points · {len(v.get('methods') or [])} methods/groups")
            notes=[_clean_text(v.get("description"),220) or "Compare compatible datasets using the scientifically appropriate axes.",f"Axes: {v.get('x_column')} vs {v.get('y_column')}",f"Coverage: {v.get('dataset_count',0)} datasets; points {v.get('point_min')}–{v.get('point_max')}"]
            if v.get("methods"): notes.append("Methods/groups: "+", ".join(map(str,(v.get("methods") or [])[:5])))
            _add_reading_note(slide_records[-1], *notes)
            if result.get("created"):
                s.shapes.add_picture(str(img),Inches(0.72),Inches(1.43),width=Inches(11.9),height=Inches(5.28))
            else:
                bullet_box(s,0.85,1.65,11.6,4.75,"Plot unavailable",["The view is indexed, but a valid numeric preview could not be rendered. Open the linked data in the HTML browser."],AMBER,3)

    # Analysis inventory
    analyses=list(d.get("analysis_cards") or [])
    if analyses:
        for start in range(0,len(analyses),8):
            s=add_slide("Analysis outputs",f"Derived results {start+1}–{min(start+8,len(analyses))} of {len(analyses)}")
            chunk=analyses[start:start+8]
            table=s.shapes.add_table(len(chunk)+1,5,Inches(0.6),Inches(1.4),Inches(12.1),Inches(min(5.7,0.42*(len(chunk)+1)))).table
            for c,h in enumerate(["Analysis","Family","Point","Method/group","Views"]): table.cell(0,c).text=h
            for r,a in enumerate(chunk,1):
                vals=[a.get("display_name"),a.get("family"),a.get("point") if a.get("point") is not None else "—",a.get("method_label") or "—",a.get("view_count")]
                for c,v in enumerate(vals): table.cell(r,c).text=_clean_text(v,80)
            for rr in table.rows:
                for cell in rr.cells: cell.fill.solid(); cell.fill.fore_color.rgb=PANEL; cell.text_frame.paragraphs[0].font.size=Pt(11); cell.text_frame.paragraphs[0].font.color.rgb=WHITE
            for cell in table.rows[0].cells: cell.fill.fore_color.rgb=RGBColor(24,76,111); cell.text_frame.paragraphs[0].font.bold=True; cell.text_frame.paragraphs[0].font.color.rgb=TRUE_WHITE

    # Media montage
    media=_find_media_paths(root,manifest,12)
    if media:
        s=add_slide("Photos and generated figures","Representative portable previews; use the HTML gallery for all media and downloads.")
        for idx,(p,label) in enumerate(media):
            col=idx%4; rowi=idx//4; x=0.55+col*3.17; y=1.35+rowi*1.8
            try: s.shapes.add_picture(str(p),Inches(x),Inches(y),width=Inches(2.85),height=Inches(1.38))
            except Exception: pass
            textbox(s,x,y+1.4,2.85,0.3,label,8.3,MUTED,align=PP_ALIGN.CENTER)

    # Unusual points
    unusual=list(d.get("unusual_points") or [])
    if unusual:
        s=add_slide("Potentially unusual points","Robust screening flags for review; not scientific conclusions.")
        table=s.shapes.add_table(min(12,len(unusual))+1,6,Inches(0.65),Inches(1.42),Inches(12.0),Inches(5.3)).table
        headers=["Technique","View","Metric","Point","Value","Robust z"]
        for c,h in enumerate(headers): table.cell(0,c).text=h
        for r,u in enumerate(unusual[:12],1):
            vals=[u.get("technique_label"),u.get("view_label"),u.get("metric_label"),u.get("point"),_nice(u.get("value"),u.get("unit") or ""),_nice(u.get("robust_z"))]
            for c,v in enumerate(vals): table.cell(r,c).text=_clean_text(v,70)
        for rr in table.rows:
            for cell in rr.cells: cell.fill.solid(); cell.fill.fore_color.rgb=PANEL; cell.text_frame.paragraphs[0].font.size=Pt(11.5); cell.text_frame.paragraphs[0].font.color.rgb=WHITE
        for cell in table.rows[0].cells: cell.fill.fore_color.rgb=RGBColor(109,75,25); cell.text_frame.paragraphs[0].font.bold=True; cell.text_frame.paragraphs[0].font.color.rgb=TRUE_WHITE

    # Conclusions
    s=add_slide("Conclusions and next checks","Observations, interpretations, and recommendations remain separated.")
    bullet_box(s,0.65,1.42,6.0,4.95,"Conclusions",narrative.get("conclusions") or [],GREEN,8)
    global_checks=[]
    for key,disc in discussion_map.items():
        if isinstance(disc,Mapping): global_checks.extend([f"{key}: {x}" for x in disc.get("recommended_checks") or []])
    bullet_box(s,6.82,1.42,5.85,4.95,"Recommended verification",global_checks[:10] or ["Review source data, units, metadata, analysis assumptions, residuals, and replicate coverage."],BLUE,10)

    # Provenance / portability
    s=add_slide("Provenance, portability, and interpretation boundaries","The PowerPoint is a review aid generated beside the portable HTML browser.")
    bullets=[f"Source manifest schema: {manifest.get('schema')}",f"Portable manifest: {manifest.get('manifest_file')}",f"Canonical H5: {(manifest.get('primary_downloads') or {}).get('canonical_h5') or 'not listed'}",f"Canonical JSON: {(manifest.get('primary_downloads') or {}).get('canonical_json') or 'not listed'}",f"Short names enabled: {bool((manifest.get('short_names') or {}).get('enabled'))}","Direct LLM receives only a compact JSON summary, never raw experiment files.","LLM-generated statements are labeled discussion and must be reviewed against plots and source data.","Unfamiliar analysis acronyms are preserved without invented expansions."]
    bullet_box(s,0.8,1.55,11.75,4.9,"Record and limitations",bullets,BLUE,10)

    _enforce_text_containment(prs)
    for idx,slide in enumerate(prs.slides,1):
        footer(slide,idx)
        rec = slide_records[idx-1] if idx-1 < len(slide_records) else {"title":f"Slide {idx}","subtitle":""}
        _set_speaker_notes(slide, str(rec.get("title") or f"Slide {idx}"), str(rec.get("subtitle") or ""), rec.get("speaker_notes") or [])
    _enforce_text_containment(prs)
    return prs, slide_records



def _v311_best_views_for_technique(manifest: Mapping[str, Any], key: str, limit: int = 2) -> list[Mapping[str, Any]]:
    matrix=[v for v in ((manifest.get("comparison") or {}).get("matrix") or []) if isinstance(v, Mapping)]
    views=[v for v in matrix if str(v.get("technique_key") or "").upper()==str(key or "").upper() and _v303_view_is_semantically_valid(manifest, v)]
    views=sorted(views, key=_v303_view_rank)
    # Suppress row-index technical/debug views unless nothing else exists.
    professional=[v for v in views if " vs row" not in str(v.get("view_label") or "").lower() and not str(v.get("view_id") or "").lower().endswith("_row")]
    chosen=professional[:limit] or views[:limit]
    return [dict(v) for v in chosen]

def _v311_detected_rows(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows=[dict(r) for r in ((manifest.get("discovery") or {}).get("techniques") or []) if isinstance(r, Mapping)]
    present={str(r.get("technique_key") or "").upper() for r in rows}
    er=manifest.get("electrochemistry_results") or {}
    if isinstance(er, Mapping):
        if er.get("ocp") and "OCP" not in present:
            rows.append({"technique_key":"OCP","technique_label":"Open-circuit potential (OCP)","family":"Electrochemistry","dataset_count":len(er.get("ocp") or []),"point_count":0,"method_count":0,"analysis_count":0})
            present.add("OCP")
        if er.get("corrosion") and "CORROSION_ANALYSIS" not in present:
            rows.append({"technique_key":"CORROSION_ANALYSIS","technique_label":"Corrosion, LPR, and polarization analysis","family":"Polarization analysis","dataset_count":0,"point_count":0,"method_count":0,"analysis_count":len(er.get("corrosion") or [])})
            present.add("CORROSION_ANALYSIS")
        if er.get("takedata") and "POSITION" not in present:
            rows.append({"technique_key":"POSITION","technique_label":"Position / motion trace","family":"Spatial","dataset_count":len(er.get("takedata") or []),"point_count":0,"method_count":0,"analysis_count":0})
            present.add("POSITION")
    def rank(r):
        key=str(r.get("technique_key") or "OTHER").upper()
        family=str(r.get("family") or "")
        is_position=1 if key in {"POSITION","SPATIAL_MAP"} else 0
        return (is_position, -int(r.get("analysis_count") or 0), -int(r.get("dataset_count") or 0), family, str(r.get("technique_label") or key))
    return sorted(rows, key=rank)

def _v311_technique_summary_bullets(manifest: Mapping[str, Any], row: Mapping[str, Any], views: Sequence[Mapping[str, Any]]) -> list[str]:
    key=str(row.get("technique_key") or "OTHER").upper(); bullets=[]
    if key in {"EIS","GEIS"}: bullets.append("Use Nyquist/Bode response as the consolidated impedance result; keep potential identity and frequency coverage explicit.")
    elif key=="DRT": bullets.append("Report DRT only as source-linked relaxation information; peak metrics require exported peak parameters before stronger conclusions.")
    elif key in {"CV","CV_LSV","LSV","IV","RDE","RRDE"}: bullets.append("Use potential-current overlays as the primary result; scan rate and branch context are not DC set-potential labels.")
    elif key=="APPROACH": bullets.append("Use the approach trace as contact/probe-context evidence rather than a separate mechanistic conclusion.")
    elif key in {"POSITION","SPATIAL_MAP"}: bullets.append("Position/motion data are treated as provenance unless measured coordinates vary enough to support spatial interpretation.")
    elif key in {"CA","CP","GCD","GITT","PITT"}: bullets.append("Use time/capacity profiles as the consolidated transient/cycling result; derived metrics are summarized only when available.")
    else: bullets.append("Summarize the highest-value compatible comparison view; avoid file-inventory or parser-diagnostic slides.")
    if views:
        bullets.append("Selected view" + ("s" if len(views)>1 else "") + ": " + "; ".join(_clean_text(v.get("view_label") or v.get("view_id"),70) for v in views[:2]))
    if int(row.get("analysis_count") or 0): bullets.append(f"Includes {int(row.get('analysis_count') or 0)} derived analysis result(s) linked to this technique family.")
    return bullets[:4]

def _v311_plan(manifest: Mapping[str, Any], detail_level: str) -> dict[str, Any]:
    d=dict(manifest.get("discovery") or {})
    matrix=[v for v in ((manifest.get("comparison") or {}).get("matrix") or []) if isinstance(v, Mapping)]
    valid=[v for v in matrix if _v303_view_is_semantically_valid(manifest, v)]
    main=[]; appendix=[]
    def add(target, sid, kind, title, **kw): target.append({"id":sid,"kind":kind,"title":title,**kw})
    add(main,"title","title","Scientific data summary")
    add(main,"executive","executive","Consolidated scientific overview")
    add(main,"coverage","coverage","Technique coverage and data readiness")
    # One professional consolidated result slide per actual scientific technique.
    for row in _v311_detected_rows(manifest):
        key=str(row.get("technique_key") or "OTHER").upper()
        # Do not make separate slides for empty/non-varying position placeholders.
        if key in {"POSITION","SPATIAL_MAP"} and not int(row.get("dataset_count") or 0) > 3:
            continue
        views=_v311_best_views_for_technique(manifest,key,limit=2)
        if not views and key not in {"DRT","DRT_ANALYSIS","OCP","CORROSION_ANALYSIS"}:
            continue
        label=str(row.get("technique_label") or key)
        add(main,f"technique-{key.lower()}","technique_consolidated",f"{label}: consolidated result",technique_key=key,technique_label=label,views=views,dataset_count=row.get("dataset_count",0),point_count=row.get("point_count",0),analysis_count=row.get("analysis_count",0),source_summary=_v311_technique_summary_bullets(manifest,row,views))
    # Keep provenance concise and only at the end. No automatic technical appendix by default.
    add(main,"conclusions","conclusions","Key findings, limitations, and next checks")
    if str(detail_level or "").lower() in {"audit","appendix"}:
        add(appendix,"appendix-provenance","provenance","Provenance and interpretation boundaries")
    detected=[str(r.get("technique_key") or "UNKNOWN").upper() for r in _v311_detected_rows(manifest)]
    recipes={k:TECHNIQUE_SLIDE_RECIPES_V304.get(k,TECHNIQUE_SLIDE_RECIPES_V304["UNKNOWN"]) for k in detected}
    return {"schema":"faradaiq-presentation-plan-v311","main":main,"appendix":appendix,"valid_view_count":len(valid),"suppressed_view_count":len(matrix)-len(valid),"consolidated_results_per_technique":True,"professional_deck_mode":True,"technique_recipe_registry_version":"v311-consolidated","technique_slide_recipes":recipes}

def _v311_llm_prompt(context: Mapping[str, Any]) -> str:
    prompt=_v310_llm_prompt(context)
    guard="""

V311 PROFESSIONAL CONSOLIDATION POLICY
- Do not create or request file-inventory, parser-diagnostic, recipe-registry, row-index, or unavailable-plot narrative unless explicitly present as an audit appendix.
- Treat the deck as an executive scientific result package: one consolidated result per detected technique, plus overview and conclusions.
- Keep presenter notes useful but concise. Do not repeat the same EIS/CV paragraph for every derived axis or appendix view.
- If coordinates are synthetic compatibility placeholders, mention this once as a limitation; do not create separate spatial relationship claims.
- Never interpret date-like or sample-name tokens as electrochemical set potentials.
"""
    return prompt + guard

def write_powerpoint_summary(experiment_root: str | Path, *, analysis_root: str | Path | None = None, experiment_name: str | None = None, manifest: Mapping[str, Any] | None = None, use_direct_llm: bool = True, detail_level: str = DEFAULT_DETAIL_LEVEL) -> dict[str, Any]:
    root=Path(experiment_root).expanduser().resolve(); root.mkdir(parents=True,exist_ok=True)
    assets=root/PPT_ASSET_DIR; assets.mkdir(parents=True,exist_ok=True)
    if manifest is None:
        from .v290_portable_browser import build_manifest
        manifest=build_manifest(root,analysis_root=analysis_root,experiment_name=experiment_name,create_batch_zips=False)
    manifest=dict(manifest)
    promoted=_promote_named_analysis_acronyms(manifest)
    # Older portable manifests can be upgraded in memory so the PowerPoint
    # still receives the current relevance, comparison, trend, and discovery
    # model.  The source manifest on disk is not modified.
    if promoted or not manifest.get("discovery") or not (manifest.get("comparison") or {}).get("matrix"):
        try:
            from . import v290_portable_browser as browser
            datasets=[dict(d) for d in (manifest.get("datasets") or [])]
            browser._assign_ui_roles(datasets)
            comparison=browser._comparison_summary(datasets)
            discovery=browser._discovery_summary(datasets,comparison)
            manifest["datasets"]=datasets; manifest["comparison"]=comparison; manifest["discovery"]=discovery
        except Exception:
            pass
    context=_compact_manifest_context(manifest); (assets/PPT_LLM_CONTEXT).write_text(json.dumps(context,ensure_ascii=False,indent=2),encoding="utf-8")
    prompt=_llm_prompt(context); (assets/PPT_LLM_PROMPT).write_text(prompt,encoding="utf-8")
    narrative=_fallback_discussion(manifest); llm_status={"status":"not_requested","experimental_file_upload":False,"compact_context_only":True}
    if use_direct_llm:
        try:
            parsed,llm_status=_call_direct_llm(prompt)
            if parsed:
                narrative=parsed; narrative["_llm_used"]=True
            else:
                narrative["_llm_used"]=False
        except Exception as exc:
            llm_status={"status":"failed","error":str(exc),"traceback":traceback.format_exc(limit=8),"experimental_file_upload":False,"compact_context_only":True}
            narrative["_llm_used"]=False
    else:
        narrative["_llm_used"]=False
    (assets/PPT_LLM_RESPONSE).write_text(json.dumps({"narrative":narrative,"transaction":{k:v for k,v in llm_status.items() if k!="raw_answer"}},ensure_ascii=False,indent=2),encoding="utf-8")
    if llm_status.get("raw_answer"):
        (assets/"ppt_llm_response_raw.txt").write_text(str(llm_status.get("raw_answer")),encoding="utf-8")
    prs,slides=_build_presentation(root,manifest,narrative,assets,str(detail_level or DEFAULT_DETAIL_LEVEL).lower())
    ppt_path=root/PPTX_NAME; prs.save(ppt_path)
    receipt={"schema":"faradaiq-powerpoint-summary-v302","version":VERSION,"created":datetime.now().isoformat(timespec="seconds"),"experiment_name":manifest.get("experiment_name"),"pptx_path":str(ppt_path),"pptx_name":ppt_path.name,"slide_count":len(slides),"detail_level":detail_level,"technique_aware":True,"all_available_scientific_comparison_views":str(detail_level).lower()=="comprehensive","direct_llm_requested":bool(use_direct_llm),"direct_llm_used":bool(narrative.get("_llm_used")),"direct_llm_status":{k:v for k,v in llm_status.items() if k!="raw_answer"},"compact_context_path":str(assets/PPT_LLM_CONTEXT),"prompt_path":str(assets/PPT_LLM_PROMPT),"response_path":str(assets/PPT_LLM_RESPONSE),"raw_experimental_files_sent_to_llm":False,"front_slide_footer":FRONT_SLIDE_FOOTER,"presentation_theme":"professional_light_scientific_v302","speaker_notes_included":True,"max_scientific_plots_per_slide":2,"consolidated_results_per_technique":True,"professional_deck_mode":True,"slide_index":slides}
    (root/PPT_MANIFEST_NAME).write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
    return receipt


def _find_widget_by_text(root: Any, needle: str) -> Any | None:
    target=needle.strip().lower(); stack=[root]
    while stack:
        w=stack.pop()
        try:
            if target in str(w.cget("text")).strip().lower(): return w
        except Exception: pass
        try: stack.extend(w.winfo_children())
        except Exception: pass
    return None


def _patch_batch_jobs(core: MutableMapping[str, Any]) -> None:
    import importlib
    for name in ("faradaiq.v233_batch_jobs","faradaiq.v232_batch_jobs"):
        try: jobs=importlib.import_module(name)
        except Exception: continue
        old_defaults=jobs.default_export_settings
        if not getattr(old_defaults,"_faradaiq_v290_ppt",False):
            def defaults(_old=old_defaults):
                v=dict(_old()); v.setdefault("powerpoint_summary",True); v.setdefault("powerpoint_use_direct_llm",True); v.setdefault("powerpoint_detail_level","comprehensive"); return v
            defaults._faradaiq_v290_ppt=True; jobs.default_export_settings=defaults
        old_init=jobs.BatchJob.__init__
        if not getattr(old_init,"_faradaiq_v290_ppt",False):
            def init(self,*a,_old=old_init,_jobs=jobs,**k): _old(self,*a,**k); merged=_jobs.default_export_settings(); merged.update(getattr(self,"export_settings",{}) or {}); self.export_settings=merged
            init._faradaiq_v290_ppt=True; jobs.BatchJob.__init__=init
        old_build=jobs.BatchQueueWindow._build
        if not getattr(old_build,"_faradaiq_v290_ppt",False):
            def build(self,_old=old_build):
                result=_old(self); tk,ttk=self.tk,self.ttk
                self.powerpoint_summary_var=tk.BooleanVar(master=self.win,value=True); self.powerpoint_use_direct_llm_var=tk.BooleanVar(master=self.win,value=True); self.powerpoint_detail_level_var=tk.StringVar(master=self.win,value="Comprehensive")
                self.export_vars["powerpoint_summary"]=self.powerpoint_summary_var; self.export_vars["powerpoint_use_direct_llm"]=self.powerpoint_use_direct_llm_var
                box=_find_widget_by_text(self.win,"Export and parser settings")
                if box is not None:
                    try: row=max(10,int(box.grid_size()[1]))
                    except Exception: row=10
                    ttk.Checkbutton(box,text="Create PowerPoint data summary",variable=self.powerpoint_summary_var).grid(row=row,column=0,columnspan=2,sticky="w",pady=(5,1))
                    ttk.Checkbutton(box,text="Use Direct LLM scientific presenter notes",variable=self.powerpoint_use_direct_llm_var).grid(row=row+1,column=0,columnspan=2,sticky="w",padx=(22,0))
                    ttk.Label(box,text="PowerPoint detail",style="Muted.TLabel").grid(row=row+2,column=0,sticky="w",padx=(22,6)); ttk.Combobox(box,textvariable=self.powerpoint_detail_level_var,values=("Comprehensive","Standard","Compact"),state="readonly",width=16).grid(row=row+2,column=1,sticky="w")
                    ttk.Label(box,text="Default ON. Saved beside the HTML. Comprehensive includes every valid scientific comparison view; Direct LLM receives compact summary text only.",style="Muted.TLabel",wraplength=620).grid(row=row+3,column=0,columnspan=2,sticky="w",padx=(22,8),pady=(0,3))
                return result
            build._faradaiq_v290_ppt=True; jobs.BatchQueueWindow._build=build
        Window=jobs.BatchQueueWindow
        old_load=Window._load_editor
        if not getattr(old_load,"_faradaiq_v290_ppt",False):
            def load_editor(self,job,_old=old_load,_jobs=jobs):
                result=_old(self,job)
                try:
                    settings=_jobs.default_export_settings(); settings.update(getattr(job,"export_settings",{}) or {})
                    if hasattr(self,"powerpoint_detail_level_var"): self.powerpoint_detail_level_var.set(str(settings.get("powerpoint_detail_level") or "comprehensive").title())
                except Exception: pass
                return result
            load_editor._faradaiq_v290_ppt=True; Window._load_editor=load_editor
        old_apply=Window.apply_settings
        if not getattr(old_apply,"_faradaiq_v290_ppt",False):
            def apply_settings(self,*a,_old=old_apply,**k):
                job=_old(self,*a,**k)
                if job is not None:
                    try:
                        job.export_settings["powerpoint_detail_level"]=str(self.powerpoint_detail_level_var.get() or "Comprehensive").lower()
                        job.touch(); self.model.write_job_session(job,include_browser=bool(job.outputs.get("browser_cache"))); self.model.autosave_queue()
                    except Exception: pass
                return job
            apply_settings._faradaiq_v290_ppt=True; Window.apply_settings=apply_settings
        old_run=jobs.run_batch_job
        if not getattr(old_run,"_faradaiq_v290_ppt",False):
            def run(job,*a,_old=old_run,**k):
                result=_old(job,*a,**k)
                if bool((job.export_settings or {}).get("powerpoint_summary",True)):
                    progress=a[1] if len(a)>1 and callable(a[1]) else k.get("progress")
                    try:
                        job.status,job.stage,job.progress,job.message,job.finished_at="Running","powerpoint_summary",100.0,"Building technique-aware PowerPoint summary and Direct LLM discussion.",""
                        if callable(progress): progress("Running","powerpoint_summary",100.0,job.message)
                    except Exception: pass
                    out=Path(str(job.outputs.get("output_dir") or job.output_dir)).resolve(); analysis=job.outputs.get("analysis_ready_root")
                    try:
                        receipt=write_powerpoint_summary(out,analysis_root=analysis,experiment_name=job.experiment_name,use_direct_llm=bool((job.export_settings or {}).get("powerpoint_use_direct_llm",True)),detail_level=str((job.export_settings or {}).get("powerpoint_detail_level","comprehensive")).lower())
                        job.outputs["powerpoint_summary"]=receipt; job.message=f"Job completed. PowerPoint: {Path(receipt['pptx_path']).name}"
                    except Exception as exc:
                        job.outputs["powerpoint_summary_error"]=str(exc); job.message=f"Job completed, but PowerPoint generation failed: {exc}"
                    job.status,job.stage,job.progress,job.finished_at="Completed","completed",100.0,datetime.now().isoformat(timespec="seconds")
                    try:
                        if callable(progress): progress("Completed","completed",100.0,job.message)
                        job.touch()
                    except Exception: pass
                return result
            run._faradaiq_v290_ppt=True; jobs.run_batch_job=run
    try:
        from faradaiq import v233_batch_jobs as active
        for key in ("_v233_default_export_settings","_v232_default_export_settings","_v230_default_export_settings","_v229_default_export_settings"): core[key]=active.default_export_settings
        for key in ("_v233_run_batch_job","_v232_run_batch_job","_v230_run_batch_job","_v229_run_batch_job"): core[key]=active.run_batch_job
    except Exception: pass


def _patch_main_gui(core: MutableMapping[str, Any]) -> None:
    Session,tk,ttk=core.get("SessionReaderGUI"),core.get("tk"),core.get("ttk")
    if Session is None or tk is None or ttk is None: return
    old_build=Session._build_ui
    if not getattr(old_build,"_faradaiq_v290_ppt",False):
        def build(self,*a,**k):
            if not hasattr(self,"powerpoint_summary_var"): self.powerpoint_summary_var=tk.BooleanVar(master=self,value=True); self.powerpoint_use_direct_llm_var=tk.BooleanVar(master=self,value=True); self.powerpoint_detail_level_var=tk.StringVar(master=self,value="Comprehensive")
            result=old_build(self,*a,**k); html_label=_find_widget_by_text(self,"Create portable HTML data browser"); frame=getattr(html_label,"master",None); top=getattr(frame,"master",None) if frame is not None else None
            if top is not None:
                try: row=max(6,int(top.grid_size()[1]))
                except Exception: row=6
                f=ttk.Frame(top); f.grid(row=row,column=0,columnspan=3,sticky="ew",pady=(1,2)); ttk.Checkbutton(f,text="Create PowerPoint data summary",variable=self.powerpoint_summary_var).pack(side="left"); ttk.Checkbutton(f,text="Use Direct LLM scientific notes",variable=self.powerpoint_use_direct_llm_var).pack(side="left",padx=(10,0)); ttk.Label(f,text="Detail",style="Muted.TLabel").pack(side="left",padx=(12,4)); ttk.Combobox(f,textvariable=self.powerpoint_detail_level_var,values=("Comprehensive","Standard","Compact"),state="readonly",width=14).pack(side="left")
                ttk.Label(top,text="Default ON. The PPTX is saved beside FaradaIQ_Data_Browser.html. Standard builds a scientific main story; Comprehensive adds a technical appendix. Direct LLM receives compact summary JSON only.",style="Muted.TLabel",wraplength=1050).grid(row=row+1,column=0,columnspan=3,sticky="w",padx=(22,0),pady=(0,2))
            return result
        build._faradaiq_v290_ppt=True; Session._build_ui=build
    old_export=Session.export_outputs
    if not getattr(old_export,"_faradaiq_v290_ppt",False):
        def export(self,*a,**k):
            result=old_export(self,*a,**k)
            try:
                if not bool(self.powerpoint_summary_var.get()): return result
                source=Path(str(getattr(self.collection,"source_path","") or "experiment")); base=source.stem if source.is_file() else source.name or "experiment"; out=Path(str(self.output_dir_var.get() or ".")).resolve(); analysis=out/f"{base}_analysis_ready_csv"
                if not analysis.is_dir(): return result
                def worker():
                    try:
                        receipt=write_powerpoint_summary(out,analysis_root=analysis,experiment_name=base,use_direct_llm=bool(self.powerpoint_use_direct_llm_var.get()),detail_level=str(self.powerpoint_detail_level_var.get() or "Comprehensive").lower())
                        try: self.after(0,lambda:self.set_status(f"PowerPoint summary created: {Path(receipt['pptx_path']).name}"))
                        except Exception: pass
                    except Exception as exc:
                        try: self.after(0,lambda text=f"PowerPoint generation failed: {exc}":self.set_status(text))
                        except Exception: pass
                threading.Thread(target=worker,name="FaradaIQPowerPointSummary",daemon=True).start()
            except Exception: pass
            return result
        export._faradaiq_v290_ppt=True; Session.export_outputs=export


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE; _CORE=core
    core["FARADAIQ_PUBLIC_VERSION"]=VERSION; core["FARADAIQ_APP_VERSION"]=VERSION; core["APP_TITLE"]=APP_TITLE
    core["_v290_write_powerpoint_summary"]=write_powerpoint_summary
    collector=core.get("_v177_collect_full_session")
    if callable(collector) and not getattr(collector,"_faradaiq_v290_ppt",False):
        def collect(gui):
            payload=collector(gui)
            if isinstance(payload,dict): payload["powerpoint_summary"]={"enabled":bool(getattr(getattr(gui,"powerpoint_summary_var",None),"get",lambda:True)()),"use_direct_llm":bool(getattr(getattr(gui,"powerpoint_use_direct_llm_var",None),"get",lambda:True)()),"detail_level":str(getattr(getattr(gui,"powerpoint_detail_level_var",None),"get",lambda:"Comprehensive")()),"default_enabled":True,"pptx_name":PPTX_NAME,"same_folder_as_html":True,"technique_aware":True,"raw_files_sent_to_llm":False}
            return payload
        collect._faradaiq_v290_ppt=True; core["_v177_collect_full_session"]=collect
    applier=core.get("_v177_apply_full_session")
    if callable(applier) and not getattr(applier,"_faradaiq_v290_ppt",False):
        def apply(gui,payload,*a,**k):
            result=applier(gui,payload,*a,**k)
            try:
                s=payload.get("powerpoint_summary",{}) if isinstance(payload,dict) else {}
                if hasattr(gui,"powerpoint_summary_var") and "enabled" in s: gui.powerpoint_summary_var.set(bool(s.get("enabled")))
                if hasattr(gui,"powerpoint_use_direct_llm_var") and "use_direct_llm" in s: gui.powerpoint_use_direct_llm_var.set(bool(s.get("use_direct_llm")))
                if hasattr(gui,"powerpoint_detail_level_var") and s.get("detail_level"): gui.powerpoint_detail_level_var.set(str(s.get("detail_level")))
            except Exception: pass
            return result
        apply._faradaiq_v290_ppt=True; core["_v177_apply_full_session"]=apply
    _patch_batch_jobs(core); _patch_main_gui(core)

# ---- FaradaIQ Studio v291 public release identity ----
# Keep the historical implementation filename/schema while reporting the active application release.
VERSION = "v302"
VERSION_NUMBER = 302
APP_TITLE = f"FaradaIQ eStudio {VERSION}"
FRONT_SLIDE_FOOTER = f"Made by FaradaIQ eStudio {VERSION}"
# ---- End FaradaIQ Studio v291 public release identity ----
# ---- FaradaIQ Studio v303 scientific-story PowerPoint engine ----
# This override keeps the historical module name for compatibility while
# replacing the v302 export-first deck builder with a story-first planner.
VERSION = "v303"
VERSION_NUMBER = 303
APP_TITLE = f"FaradaIQ eStudio {VERSION}"
FRONT_SLIDE_FOOTER = f"Made by FaradaIQ eStudio {VERSION}"
DEFAULT_DETAIL_LEVEL = "comprehensive"


def _v303_dataset_lookup(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(d.get("id")): d for d in (manifest.get("datasets") or []) if isinstance(d, Mapping)}


def _v303_view_is_semantically_valid(manifest: Mapping[str, Any], view: Mapping[str, Any]) -> bool:
    """Reject plots that are numerically plottable but scientifically misleading."""
    key = str(view.get("technique_key") or "").upper()
    vid = str(view.get("view_id") or "").lower()
    xcol = str(view.get("x_column") or "").lower()
    ycol = str(view.get("y_column") or "").lower()
    label = str(view.get("view_label") or "").lower()

    # sqrt(time) is a technique-specific transient diagnostic, not a generic OCP view.
    if key == "OCP" and ("sqrt" in vid or "t¹" in label or "square-root" in label):
        return False
    # Avoid completely non-informative constant plots in the main deck and
    # detect the specific raw-trace-vs-point failure mode by inspecting x data.
    lookup = _v303_dataset_lookup(manifest)
    finite_y = []
    point_x_spans = []
    for did in list(view.get("dataset_ids") or [])[:12]:
        ds = lookup.get(str(did))
        if not ds:
            continue
        try:
            xs, ys, _ = _dataset_view_values(ds, str(view.get("view_id") or ""))
            fx=[float(v) for v in xs if math.isfinite(float(v))]
            fy=[float(v) for v in ys if math.isfinite(float(v))]
            finite_y.extend(fy)
            if fx: point_x_spans.append(max(fx)-min(fx))
        except Exception:
            continue
    if xcol in {"point_index", "point", "position_index"} and point_x_spans:
        # If every dataset has a scalar/constant point x-coordinate but contains
        # multiple y samples, a curve overlay would become a vertical line.
        if max(abs(v) for v in point_x_spans) <= 1e-12:
            return False
    if len(finite_y) >= 3:
        span = max(finite_y) - min(finite_y)
        scale = max(1e-30, max(abs(v) for v in finite_y))
        if abs(span) <= scale * 1e-12:
            return False
    return True


def _v303_view_rank(view: Mapping[str, Any]) -> tuple[int, int, str]:
    key = str(view.get("technique_key") or "").upper()
    vid = str(view.get("view_id") or "").lower()
    preferred = {
        "EIS": ["nyquist", "bode", "phase"],
        "GEIS": ["nyquist", "bode"],
        "DRT": ["drt_tau", "drt", "tau"],
        "OCP": ["time_potential"],
        "TAFEL": ["tafel_current", "tafel"],
        "CV": ["potential_current", "cv"],
        "CV_LSV": ["potential_current", "iv"],
        "LSV": ["potential_current", "iv"],
        "CA": ["time_current", "current_time"],
        "CP": ["time_potential", "potential_time"],
        "APPROACH": ["distance_current"],
        "GCD": ["capacity", "voltage"],
        "RAMAN": ["spectrum", "raman"],
        "IR": ["spectrum", "absorbance"],
        "FTIR": ["spectrum", "absorbance", "transmittance"],
        "UVVIS": ["spectrum", "absorbance", "transmittance"],
        "XRD": ["pattern", "intensity"],
        "XPS": ["spectrum", "binding"],
        "HYPERSPECTRAL": ["spectrum", "map", "component"],
    }
    priority = 999
    for i, token in enumerate(preferred.get(key, [])):
        if token in vid:
            priority = i
            break
    return (priority, -int(view.get("score") or 0), str(view.get("view_label") or ""))


def _v303_plan(manifest: Mapping[str, Any], detail_level: str) -> dict[str, Any]:
    d = dict(manifest.get("discovery") or {})
    matrix = [v for v in ((manifest.get("comparison") or {}).get("matrix") or []) if isinstance(v, Mapping)]
    valid = [v for v in matrix if _v303_view_is_semantically_valid(manifest, v)]
    by_tech: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for v in valid:
        by_tech[str(v.get("technique_key") or "OTHER")].append(v)
    for key in by_tech:
        by_tech[key] = sorted(by_tech[key], key=_v303_view_rank)

    electro = manifest.get("electrochemistry_results") if isinstance(manifest.get("electrochemistry_results"), Mapping) else {}
    main: list[dict[str, Any]] = []
    appendix: list[dict[str, Any]] = []
    def add(target, sid, kind, title, **kw):
        target.append({"id":sid,"kind":kind,"title":title,**kw})

    add(main,"title","title","Scientific data summary")
    add(main,"executive","executive","Scientific overview")
    add(main,"coverage","coverage","Experiment architecture and data coverage")
    if electro.get("takedata"):
        add(main,"spatial-provenance","takedata_summary","Spatial acquisition and coordinate provenance", result_index=0)

    # Derived electrochemistry gets priority over generic plot views.
    for i, rs in enumerate(electro.get("ocp") or []):
        add(main,f"ocp-derived-{i}","ocp_derived","OCP stability and spatial trend", result_index=i)
    for i, rs in enumerate(electro.get("corrosion") or []):
        add(main,f"corrosion-derived-{i}","corrosion_derived","Robust corrosion metrics", result_index=i)
        if rs.get("review_rows"):
            add(main,f"corrosion-quality-{i}","corrosion_quality","Corrosion fit quality and ambiguity", result_index=i)

    detected = [str(r.get("technique_key") or "OTHER") for r in (d.get("techniques") or [])]
    def hero_title(key: str, v: Mapping[str, Any]) -> str:
        vid=str(v.get("view_id") or "").lower(); label=str(v.get("view_label") or "")
        if key=="OCP" and "time_potential" in vid: return "Open-circuit potential relaxation across positions"
        if key=="TAFEL" and "tafel" in vid: return "Polarization behavior in Tafel coordinates"
        if key=="APPROACH" and "distance_current" in vid: return "Approach-curve response across positions"
        if key in {"EIS","GEIS"} and "nyquist" in vid: return "Impedance response across conditions"
        if key in {"EIS","GEIS"} and "bode" in vid: return "Frequency-dependent impedance response"
        if key=="DRT": return "Distribution of relaxation times"
        if key in {"CV","CV_LSV","LSV"}: return "Voltammetric response across conditions"
        if key in {"RAMAN","IR","FTIR","UVVIS","XPS"}: return f"{v.get('technique_label') or key} spectral comparison"
        if key=="XRD": return "Diffraction pattern comparison"
        if key=="HYPERSPECTRAL": return "Hyperspectral response and spatial structure"
        return f"{v.get('technique_label') or key} · {label}"
    for key in detected:
        views = by_tech.get(key, [])
        if not views:
            continue
        # Position/motion and generic spatial-map views support provenance; they do not compete with primary science in the main deck.
        if key in {"POSITION","SPATIAL_MAP"}:
            for j,v in enumerate(views): add(appendix,f"appendix-view-{key}-{j}","comparison",hero_title(key,v),view=dict(v),technique_key=key)
            continue
        # Main deck: one hero view for most techniques; EIS/DRT/spectroscopy can earn a second complementary view.
        limit = 2 if key in {"EIS","GEIS","DRT","HYPERSPECTRAL","RAMAN","IR","FTIR","UVVIS","XRD","XPS"} else 1
        for j, v in enumerate(views[:limit]):
            add(main,f"hero-{key}-{j}","comparison",hero_title(key,v), view=dict(v), technique_key=key)
        for j, v in enumerate(views[limit:]):
            add(appendix,f"appendix-view-{key}-{j}","comparison",hero_title(key,v), view=dict(v), technique_key=key)

    if d.get("media_count"):
        add(main,"media","media","Representative experimental context and media")
    if d.get("unusual_points"):
        add(main,"unusual","unusual","Data-quality and review flags")
    add(main,"conclusions","conclusions","Key findings, limitations, and next checks")

    if str(detail_level).lower() == "comprehensive":
        # The appendix preserves all scientifically valid derived views without crowding the main story.
        for i, rs in enumerate(electro.get("ocp") or []):
            axes=[str(a.get("id")) for a in rs.get("axis_options") or [] if str(a.get("id")) in {"Point","X","Y","Distance"}] or ["Point"]
            for axis in list(dict.fromkeys(axes)):
                add(appendix,f"appendix-ocp-{i}-{axis}","derived_metric",f"OCP average vs {_derived_axis_label(axis).replace(' / µm','')}",result_kind="ocp",result_index=i,metric_id="mean_ocp_V",axis=axis)
            for mid in ("std_ocp_V","sem_ocp_V","tail_drift_V_per_s"):
                if any(str(m.get("id"))==mid for m in rs.get("metrics") or []):
                    add(appendix,f"appendix-ocp-{i}-{mid}","derived_metric",f"OCP stability · {mid.replace('_',' ')}",result_kind="ocp",result_index=i,metric_id=mid,axis=str(rs.get("default_axis") or axes[0]))
        for i, rs in enumerate(electro.get("corrosion") or []):
            axes=["Point"]
            primary=str(rs.get("default_axis") or "Point")
            if primary!="Point": axes.append(primary)
            for m in rs.get("metrics") or []:
                mid=str(m.get("id") or "")
                if mid not in {"ecorr_V","icorr_A","icorr_A_cm2","cathodic_tafel_mV_dec","anodic_tafel_mV_dec","cathodic_r2","anodic_r2","corrosion_rate_mm_per_year"}: continue
                for axis in axes:
                    add(appendix,f"appendix-corrosion-{i}-{mid}-{axis}","derived_metric",f"{m.get('label') or mid} vs {_derived_axis_label(axis).replace(' / µm','')}",result_kind="corrosion",result_index=i,metric_id=mid,axis=axis)
        # Detailed Takedata diagnostics live in the appendix only, and constant fields are suppressed.
        for i, rs in enumerate(electro.get("takedata") or []):
            add(appendix,f"appendix-takedata-{i}","takedata_details","Takedata technical diagnostics", result_index=i)
        if d.get("analysis_cards"):
            add(appendix,"appendix-analysis","analysis_inventory","Derived analysis inventory")
        add(appendix,"appendix-provenance","provenance","Provenance and interpretation boundaries")
    elif str(detail_level).lower() == "standard":
        add(appendix,"appendix-provenance","provenance","Provenance and interpretation boundaries")
    return {"schema":"faradaiq-presentation-plan-v303","main":main,"appendix":appendix,"valid_view_count":len(valid),"suppressed_view_count":len(matrix)-len(valid)}


def _v303_compact_manifest_context(manifest: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    base = _compact_manifest_context(manifest)
    base["schema"] = "faradaiq-ppt-llm-context-v303"
    electro=manifest.get("electrochemistry_results") if isinstance(manifest.get("electrochemistry_results"), Mapping) else {}
    compact_electro={"ocp":[],"corrosion":[],"takedata":[]}
    for kind in compact_electro:
        for rs in electro.get(kind) or []:
            if not isinstance(rs, Mapping): continue
            summary={k:rs.get(k) for k in ("id","kind","title","default_axis","row_count","raw_record_count","final_time_percent","status_counts","coordinate_quality","diagnostics","warnings")}
            summary["metrics"]=[{k:m.get(k) for k in ("id","label","unit")} for m in (rs.get("metrics") or []) if isinstance(m, Mapping)]
            rows=list(rs.get("rows") or [])
            metric_stats={}
            for m in summary["metrics"]:
                mid=str(m.get("id") or ""); vals=[_finite(r.get(mid)) for r in rows if isinstance(r, Mapping)]; vals=[v for v in vals if v is not None]
                if vals: metric_stats[mid]={"min":min(vals),"max":max(vals),"first":vals[0],"last":vals[-1],"n":len(vals)}
            summary["metric_stats"]=metric_stats
            summary["review_rows"]=list(rs.get("review_rows") or [])[:12]
            compact_electro[kind].append(summary)
    base["electrochemistry_results"] = compact_electro
    base["presentation_plan"] = [
        {"id":s.get("id"),"kind":s.get("kind"),"title":s.get("title"),"technique_key":s.get("technique_key"),
         "view_label":(s.get("view") or {}).get("view_label") if isinstance(s.get("view"), Mapping) else None,
         "description":(s.get("view") or {}).get("description") if isinstance(s.get("view"), Mapping) else None}
        for s in list(plan.get("main") or [])
    ]
    base["presentation_policy"] = {
        "main_story_first": True,
        "technical_appendix": True,
        "one_scientific_message_per_slide": True,
        "semantically_invalid_views_suppressed": True,
        "speaker_notes_are_scientific_paragraphs": True,
        "raw_files_sent_to_llm": False,
    }
    return base


def _v303_llm_prompt(context: Mapping[str, Any]) -> str:
    return """You are the scientific presentation director for FaradaIQ. Build an evidence-bounded scientific narrative for the planned slides.

Use ONLY the supplied JSON. Never invent experimental conditions, numerical values, mechanisms, materials, causality, or measurements. The deterministic planner has already decided which plots are scientifically valid. Your job is to explain the evidence clearly and help a scientist present it.

For every planned main slide, write:
- takeaway: one short, evidence-grounded sentence suitable as a slide subtitle or callout.
- speaker_notes: 1–3 coherent scientific paragraphs, normally 90–180 words total. Discuss what is shown, what stands out, what it may mean, what limitation matters, and how it connects to the next scientific question. These must be natural presentation paragraphs, NOT generic instructions such as 'open with the slide title'.

Rules:
1. Separate observation from interpretation and from recommended checks.
2. If equilibrium, kinetics, mechanism, fit validity, or spatial provenance is uncertain, say so explicitly.
3. Robust outlier/ambiguity flags are review prompts, not proof.
4. For OCP, distinguish a final-window operational mean from proof of equilibrium.
5. For Tafel/corrosion, never report current density or penetration rate unless the JSON contains the required valid inputs/results; explicitly discuss multiple crossings/dips or boundary minima when present.
6. For EIS/DRT, respect frequency coverage, model/regularization assumptions, residuals, and fit validity.
7. For spectroscopy/imaging/unknown techniques, describe only features actually represented in the supplied evidence.
8. Do not repeat the same paragraph across slides.
9. Return strict JSON only.

Schema:
{
  "executive_summary": ["..."],
  "cross_technique_observations": ["..."],
  "conclusions": ["..."],
  "global_cautions": ["..."],
  "slide_content": {
    "SLIDE_ID": {
      "takeaway": "one concise evidence-grounded sentence",
      "speaker_notes": "one to three polished scientific paragraphs"
    }
  },
  "technique_discussions": {
    "TECHNIQUE_KEY": {
      "observations": ["..."],
      "interpretations": ["..."],
      "limitations": ["..."],
      "recommended_checks": ["..."]
    }
  }
}

FARADAIQ_CONTEXT_JSON
""" + json.dumps(context, ensure_ascii=False, indent=2)


def _v303_fallback_narrative(manifest: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    d = dict(manifest.get("discovery") or {})
    name = _clean_text(manifest.get("experiment_name"), 140) or "this experiment"
    summary = [f"The dataset contains {d.get('measurement_count',0)} measurement datasets across {d.get('point_count',0)} spatial or acquisition points, with {d.get('analysis_count',0)} derived analysis results."]
    tech_labels = [str(r.get("technique_label") or r.get("technique_key")) for r in (d.get("techniques") or [])]
    if tech_labels:
        summary.append("The presentation prioritizes the scientific story across " + ", ".join(tech_labels[:6]) + (" and additional techniques." if len(tech_labels)>6 else "."))
    slide_content: dict[str, Any] = {}
    for idx, s in enumerate(plan.get("main") or []):
        sid, kind, title = str(s.get("id")), str(s.get("kind")), str(s.get("title"))
        takeaway = "This slide establishes the evidence needed for the next scientific comparison."
        p1 = f"This slide summarizes {title.lower()} for {name}. The plotted quantities and counts are taken directly from the FaradaIQ manifest and derived-result tables; no experimental condition or mechanism is inferred beyond those recorded data."
        p2 = "The presentation should emphasize the dominant reproducible pattern first, then identify exceptions, uncertainty, incomplete coverage, or analysis assumptions that could change the interpretation."
        if kind == "title":
            takeaway = "A concise overview of the experiment, measurement coverage, and derived scientific results."
            p1 = f"This presentation summarizes the measurement and analysis coverage available for {name}. The main deck is organized around scientific questions rather than file inventory, while detailed diagnostics are moved to the appendix when requested."
            p2 = "The goal is to connect spatial or experimental context to technique-specific behavior and then to derived metrics, while keeping provenance and uncertainty visible throughout the discussion."
        elif kind == "executive":
            takeaway = summary[0]
            p1 = "The executive summary is intentionally limited to findings supported by the generated manifest, valid comparison views, and derived metrics. It should be used to orient the audience to the breadth of the experiment before moving into technique-specific evidence."
            p2 = "Any apparent cross-technique relationship should be treated as a hypothesis for verification unless the corresponding measurements share compatible points, chronology, and experimental context."
        elif kind == "coverage":
            takeaway = f"{d.get('measurement_count',0)} measurements and {d.get('analysis_count',0)} analyses are organized into a technique-aware scientific workflow."
            p1 = "This slide establishes what was actually measured and analyzed. Measurement counts, point coverage, and method coverage define which comparisons are scientifically defensible and which apparent gaps simply reflect missing or unmatched acquisition coverage."
            p2 = "This coverage map also prevents later slides from implying a spatial or cross-technique comparison where the underlying measurements were not acquired at compatible locations or conditions."
        elif kind == "takedata_summary":
            takeaway = "Spatial coordinates are treated as provenance first, then used to register point-derived electrochemical metrics."
            p1 = "The spatial-acquisition slide shows how measurement points are registered to the available Takedata coordinates. Physical coordinates are preferred; compatibility or synthetic coordinates must be labeled as such and should not be interpreted as independent stage metrology."
            p2 = "The main scientific role of this slide is to establish the coordinate system and acquisition order before discussing OCP, corrosion, impedance, spectroscopy, or other quantities versus position."
        elif kind == "ocp_derived":
            takeaway = "Final-window OCP is an operational stability metric; tail drift and variability determine how strongly it can be interpreted."
            p1 = "The OCP summary reduces each time trace using the selected final fraction of elapsed time and retains variability and tail-drift diagnostics. A stable final-window mean supports point-to-point comparison, but it does not by itself prove thermodynamic equilibrium."
            p2 = "When the mean shifts between positions, that spatial change should be discussed together with the within-window scatter and continuing drift so that a large apparent difference is not overinterpreted if the underlying trace remains unstable."
        elif kind == "corrosion_derived":
            takeaway = "Robust corrosion metrics are reported only where branch fits and required material inputs support them."
            p1 = "This slide summarizes corrosion quantities derived from polarization data, including Ecorr, Icorr and branch slopes where valid. The robust workflow separates clear fits from review or ambiguous cases rather than forcing every point into a single corrosion state."
            p2 = "Current density requires a valid area, and penetration corrosion rate additionally requires equivalent weight and density. Multiple zero-crossing families, significant dips, boundary minima, or weak branch fits should be highlighted as scientific uncertainty rather than averaged away."
        elif kind == "corrosion_quality":
            takeaway = "Ambiguity is treated as a result-quality finding, not hidden by a single forced fit."
            p1 = "The quality slide identifies points where the polarization curve does not support a unique, robust corrosion interpretation. Multiple crossing families, multiple significant low-current dips, or a minimum at the scan boundary can indicate that Ecorr and Icorr are not uniquely constrained by the acquired sweep."
            p2 = "These flags do not establish a mechanism. They indicate where the raw curve, branch selection, scan range, and experimental stability should be reviewed before quantitative corrosion values are compared."
        elif kind == "comparison":
            v = s.get("view") or {}
            key = str(s.get("technique_key") or "")
            takeaway = _clean_text(v.get("description"), 160) or f"The {key} comparison highlights the dominant response across compatible measurements."
            p1 = f"This slide compares {v.get('view_label') or 'the selected scientific view'} across the compatible {key or 'measurement'} datasets. The axes and transformations are chosen by the technique-aware view definition rather than by generic column availability."
            p2 = "The scientific discussion should focus on reproducible curve-shape, magnitude, position, frequency, or spatial differences that are visible across compatible datasets. Interpretation beyond those observations requires the relevant experimental metadata, model assumptions, and quality diagnostics."
        elif kind == "media":
            takeaway = "Representative media provide experimental context for the quantitative measurements."
            p1 = "The representative images are included to connect the numerical datasets with the physical experiment and measurement geometry. Image interpretation should remain descriptive unless scale, registration, segmentation, or other quantitative image-analysis metadata are available."
            p2 = "When a visible feature is spatially linked to an electrochemical or spectroscopic result, that relationship should be presented as a correlation unless the experimental design supports a stronger causal interpretation."
        elif kind == "unusual":
            takeaway = "Screening flags identify measurements that deserve review before they drive the scientific conclusion."
            p1 = "The flagged points are generated by robust screening and should be treated as review priorities, not automatic outliers to discard. A point can be scientifically important precisely because it differs from the dominant population."
            p2 = "Before excluding or mechanistically interpreting a flagged value, the presenter should verify the raw trace, acquisition metadata, units, fit residuals, and any nearby spatial or chronological measurements."
        elif kind == "conclusions":
            takeaway = "The strongest conclusions are those supported by consistent trends, valid derived metrics, and compatible spatial or experimental context."
            p1 = "The closing slide should distinguish direct observations from interpretation. Reproducible trends across compatible points and techniques can motivate a scientific hypothesis, while ambiguous fits, incomplete spatial registration, or missing material constants should remain explicit limitations."
            p2 = "The recommended next checks should target the specific uncertainty that limits the current interpretation, such as additional spatial coverage, repeat measurements, fit validation, or review of a flagged raw trace."
        slide_content[sid] = {"takeaway":takeaway,"speaker_notes":p1+"\n\n"+p2}
    return {
        "executive_summary": summary,
        "cross_technique_observations": ["Cross-technique interpretation is limited to measurements with compatible point, method, and chronology coverage."],
        "conclusions": ["Prioritize reproducible technique-specific and spatial trends, then verify ambiguous or incomplete results against raw data and metadata."],
        "global_cautions": ["Automated summaries are evidence-bounded screening and presentation aids; mechanistic conclusions still require expert review."],
        "slide_content": slide_content,
        "technique_discussions": {},
        "_llm_used": False,
    }


def _v303_build_presentation(root: Path, manifest: Mapping[str, Any], narrative: Mapping[str, Any], assets: Path, detail_level: str, plan: Mapping[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    Presentation, PP_ALIGN, MSO_AUTO_SIZE, MSO_SHAPE, RGBColor, Inches, Pt = _presentation_imports()
    prs = Presentation(); prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5); blank=prs.slide_layouts[6]
    BG=RGBColor(249,250,252); INK=RGBColor(20,31,44); MUTED=RGBColor(82,94,107); NAVY=RGBColor(20,67,104); TEAL=RGBColor(0,101,128); PANEL=RGBColor(239,243,247); GREEN=RGBColor(44,111,78); AMBER=RGBColor(150,91,14); WHITE=RGBColor(255,255,255)
    slide_records=[]; slide_content=narrative.get("slide_content") if isinstance(narrative.get("slide_content"), Mapping) else {}

    def bg(sl):
        f=sl.background.fill; f.solid(); f.fore_color.rgb=BG
    def textbox(sl,x,y,w,h,text,size=20,color=INK,bold=False,align=None):
        b=sl.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h)); tf=b.text_frame; tf.clear(); tf.word_wrap=True; tf.auto_size=MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        tf.margin_left=tf.margin_right=Inches(.02); tf.margin_top=tf.margin_bottom=Inches(.01)
        p=tf.paragraphs[0]; p.text=_clean_text(text, 1100); p.font.name="Aptos"; p.font.size=Pt(size); p.font.bold=bold; p.font.color.rgb=color
        if align is not None: p.alignment=align
        return b
    def title(sl, heading, takeaway=""):
        textbox(sl,.62,.28,12.05,.50,heading,31,INK,True)
        if takeaway: textbox(sl,.65,.86,12.0,.42,takeaway,16,TEAL,False)
        line=sl.shapes.add_shape(MSO_SHAPE.RECTANGLE,Inches(.62),Inches(1.29),Inches(12.05),Inches(.025)); line.fill.solid(); line.fill.fore_color.rgb=NAVY; line.line.fill.background()
    def footer(sl, idx):
        textbox(sl,.62,7.12,11.7,.22,(FRONT_SLIDE_FOOTER if idx==1 else f"FaradaIQ eStudio {VERSION}"),9,MUTED)
        textbox(sl,12.25,7.12,.45,.22,str(idx),9,MUTED,align=PP_ALIGN.RIGHT)
    def note_for(sid, fallback=""):
        rec=slide_content.get(sid) if isinstance(slide_content, Mapping) else None
        if isinstance(rec, Mapping) and str(rec.get("speaker_notes") or "").strip(): return str(rec.get("speaker_notes"))
        return fallback or "This slide summarizes evidence available in the FaradaIQ manifest and derived results. Interpretation should be checked against the underlying data and analysis assumptions."
    def takeaway_for(sid, fallback=""):
        rec=slide_content.get(sid) if isinstance(slide_content, Mapping) else None
        return _clean_text(rec.get("takeaway"),220) if isinstance(rec, Mapping) and rec.get("takeaway") else fallback
    def add_slide(spec, takeaway="", note=""):
        sl=prs.slides.add_slide(blank); bg(sl); title(sl,str(spec.get("title") or "Scientific result"),takeaway_for(str(spec.get("id")),takeaway)); slide_records.append({"index":len(prs.slides),"id":spec.get("id"),"title":spec.get("title"),"kind":spec.get("kind"),"speaker_notes":note_for(str(spec.get("id")),note)}); return sl
    def set_notes(sl, text):
        try:
            tf=sl.notes_slide.notes_text_frame; tf.clear(); tf.text="Presenter notes\n\nScientific discussion\n\n"+str(text or "").strip()
        except Exception: pass
    def panel(sl,x,y,w,h,heading,items,accent=NAVY):
        r=sl.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,Inches(x),Inches(y),Inches(w),Inches(h)); r.fill.solid(); r.fill.fore_color.rgb=PANEL; r.line.color.rgb=accent
        textbox(sl,x+.18,y+.14,w-.36,.34,heading,18,INK,True)
        yy=y+.63
        for item in list(items or [])[:5]:
            textbox(sl,x+.22,yy,w-.44,.62,"• "+_clean_text(item,190),16,INK); yy+=.68
    def add_picture_fit(sl, path, x=.72,y=1.45,w=11.9,h=5.28):
        try: sl.shapes.add_picture(str(path),Inches(x),Inches(y),width=Inches(w),height=Inches(h)); return True
        except Exception: return False
    def table_style(table, font=15):
        for ri,row in enumerate(table.rows):
            for cell in row.cells:
                cell.fill.solid(); cell.fill.fore_color.rgb=(NAVY if ri==0 else WHITE)
                for p in cell.text_frame.paragraphs:
                    p.font.name="Aptos"; p.font.size=Pt(font if ri else font); p.font.bold=(ri==0); p.font.color.rgb=(WHITE if ri==0 else INK)
                cell.text_frame.word_wrap=True; cell.text_frame.auto_size=MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    d=manifest.get("discovery") or {}; electro=manifest.get("electrochemistry_results") if isinstance(manifest.get("electrochemistry_results"), Mapping) else {}

    all_specs=list(plan.get("main") or [])
    if plan.get("appendix"):
        all_specs += [{"id":"appendix-divider","kind":"section","title":"Technical appendix"}] + list(plan.get("appendix") or [])

    for spec in all_specs:
        sid=str(spec.get("id")); kind=str(spec.get("kind")); title_text=str(spec.get("title"))
        if kind=="title":
            sl=prs.slides.add_slide(blank); bg(sl)
            textbox(sl,.78,1.18,11.8,.72,"FaradaIQ Consolidated Results Summary",38,INK,True)
            textbox(sl,.80,2.02,11.6,.48,_clean_text(manifest.get("experiment_name"),170),24,TEAL,True)
            textbox(sl,.80,2.72,11.3,.55,"Technique-aware consolidated results, evidence boundaries, and next checks",19,MUTED)
            stats=[("Points",d.get("point_count",0)),("Measurements",d.get("measurement_count",0)),("Analyses",d.get("analysis_count",0)),("Media",d.get("media_count",0))]
            for i,(lab,val) in enumerate(stats):
                x=.8+i*3.05; r=sl.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,Inches(x),Inches(4.0),Inches(2.65),Inches(1.02)); r.fill.solid(); r.fill.fore_color.rgb=PANEL; r.line.color.rgb=NAVY
                textbox(sl,x+.12,4.13,2.4,.38,str(val),28,INK,True,PP_ALIGN.CENTER); textbox(sl,x+.12,4.58,2.4,.28,lab,13,MUTED,False,PP_ALIGN.CENTER)
            slide_records.append({"index":len(prs.slides),"id":sid,"title":"Scientific data summary","kind":kind,"speaker_notes":note_for(sid)})
            continue
        if kind=="section":
            sl=prs.slides.add_slide(blank); bg(sl); textbox(sl,.9,2.5,11.5,.65,title_text,36,INK,True,PP_ALIGN.CENTER); textbox(sl,1.4,3.4,10.5,.55,"Detailed diagnostics, secondary views, inventory, and provenance",19,MUTED,False,PP_ALIGN.CENTER)
            slide_records.append({"index":len(prs.slides),"id":sid,"title":title_text,"kind":kind,"speaker_notes":"The following slides provide technical detail and provenance supporting the main scientific story. They are intended for questions, review, and audit rather than the core presentation narrative."}); continue
        if kind=="executive":
            sl=add_slide(spec,"The deck is organized as one consolidated scientific result per detected technique.")
            panel(sl,.68,1.55,6.0,4.95,"Evidence-grounded summary",narrative.get("executive_summary") or [f"{d.get('measurement_count',0)} measurements across {d.get('point_count',0)} points."],NAVY)
            panel(sl,6.88,1.55,5.8,2.35,"Cross-technique perspective",narrative.get("executive_cross_technique") or narrative.get("cross_technique_observations") or ["Cross-technique claims require compatible spatial and experimental context."],GREEN)
            panel(sl,6.88,4.08,5.8,2.42,"Interpretation boundaries",narrative.get("executive_cautions") or narrative.get("global_cautions") or ["Derived metrics and LLM discussion remain evidence-bounded and require expert review."],AMBER)
        elif kind=="coverage":
            sl=add_slide(spec,f"{d.get('measurement_count',0)} measurements · {d.get('analysis_count',0)} analyses · {d.get('point_count',0)} points")
            tech=list(d.get("techniques") or [])[:8]
            table=sl.shapes.add_table(len(tech)+1,5,Inches(.72),Inches(1.55),Inches(11.9),Inches(min(4.75,.55*(len(tech)+1)))).table
            for c,h in enumerate(["Technique","Family","Datasets","Points","Analyses"]): table.cell(0,c).text=h
            for r,row in enumerate(tech,1):
                vals=[row.get("technique_label"),row.get("family"),row.get("dataset_count"),row.get("point_count"),row.get("analysis_count")]
                for c,v in enumerate(vals): table.cell(r,c).text=str(v if v is not None else "—")
            table_style(table,15)
        elif kind=="takedata_summary":
            rs=(electro.get("takedata") or [])[int(spec.get("result_index") or 0)]
            sl=add_slide(spec,"Coordinates are shown as provenance before downstream spatial interpretation.")
            img=assets/f"v303_takedata_summary_{spec.get('result_index',0)}.png"; made=_plot_takedata_xy(rs,img)
            if made.get("created"): add_picture_fit(sl,img,.68,1.55,8.1,4.95)
            diag=rs.get("diagnostics") if isinstance(rs.get("diagnostics"), Mapping) else {}
            qraw=rs.get("coordinate_quality")
            quality_label=(qraw.get("label") or qraw.get("kind")) if isinstance(qraw, Mapping) else str(qraw or "recorded Takedata")
            bullets=[f"Spatial stations: {rs.get('row_count',0)}; raw records: {rs.get('raw_record_count',0)}.",f"Dominant axis: {diag.get('dominant_axis') or rs.get('default_axis') or 'Point'}.",f"Coordinate provenance: {quality_label}."]
            if rs.get("warnings"): bullets.append(_clean_text((rs.get("warnings") or [""])[0],180))
            panel(sl,9.0,1.55,3.65,4.95,"Spatial provenance",bullets,TEAL)
        elif kind=="takedata_relationship_grid":
            rs=(electro.get("takedata") or [])[int(spec.get("result_index") or 0)]
            relationships=list(spec.get("relationships") or [])
            role=str(spec.get("recipe_role") or "relationships")
            sl=add_slide(spec,"Technique-specific Takedata relationships are selected from coordinate, monitor, station, and chronology fields.")
            img=assets/f"v304_takedata_relationship_{_safe_filename(str(spec.get('id')))}.png"
            made=_plot_takedata_relationship_grid(rs,relationships,img,title=str(spec.get("plot_title") or title_text))
            if made.get("created"):
                add_picture_fit(sl,img,.62,1.43,8.35,5.22)
            else:
                panel(sl,.78,1.65,8.05,4.7,"Relationship plots unavailable",["The requested Takedata field pairs were not present with enough finite values in this dataset."],AMBER)
            diag=rs.get("diagnostics") if isinstance(rs.get("diagnostics"), Mapping) else {}
            qraw=rs.get("coordinate_quality")
            quality_label=(qraw.get("label") or qraw.get("kind")) if isinstance(qraw, Mapping) else str(qraw or "recorded Takedata")
            shown=made.get("shown",0); skipped=made.get("skipped",0)
            bullets=[f"Relationship family: {role}.",f"Shown relationships: {shown}; suppressed/unavailable: {skipped}.",f"Dominant spatial axis: {diag.get('dominant_axis') or rs.get('default_axis') or 'Point'}.",f"Coordinate provenance: {quality_label}."]
            if made.get("constant_relationships"):
                bullets.append("Constant or near-constant fields are labeled rather than over-interpreted.")
            panel(sl,9.12,1.55,3.35,4.95,"How to read this slide",bullets,TEAL)
        elif kind=="technique_recipe_matrix":
            sl=add_slide(spec,"Technique-specific slide recipes define what belongs in the main story and what moves to the appendix.")
            rows=[]
            for key,rec in sorted((TECHNIQUE_SLIDE_RECIPES_V304 or {}).items()):
                if key in {"UNKNOWN","SPATIAL","TAKEDATA"}: continue
                slides=rec.get("core") or []
                rows.append((rec.get("label") or key, "; ".join(slides[:3])))
            rows=rows[:9]
            table=sl.shapes.add_table(len(rows)+1,2,Inches(.72),Inches(1.55),Inches(11.9),Inches(min(5.0,.52*(len(rows)+1)))).table
            table.cell(0,0).text="Technique family"; table.cell(0,1).text="Core slide logic"
            for r,(lab,logic) in enumerate(rows,1):
                table.cell(r,0).text=_clean_text(lab,65); table.cell(r,1).text=_clean_text(logic,170)
            table_style(table,13)
        elif kind=="ocp_derived":
            rs=(electro.get("ocp") or [])[int(spec.get("result_index") or 0)]; axis=str(rs.get("default_axis") or "Point"); metric={"id":"mean_ocp_V","label":"Average OCP","unit":"V"}
            sl=add_slide(spec,f"Final {rs.get('final_time_percent') or 'selected'}% of elapsed time · spatial axis: {_derived_axis_label(axis).replace(' / µm','')}")
            img=assets/f"v303_ocp_{spec.get('result_index',0)}.png"; made=_plot_derived_metric(rs,metric,axis,img)
            if made.get("created"): add_picture_fit(sl,img)
        elif kind=="corrosion_derived":
            rs=(electro.get("corrosion") or [])[int(spec.get("result_index") or 0)]; axis=str(rs.get("default_axis") or "Point"); mby={str(m.get("id")):m for m in rs.get("metrics") or []}
            mids=[m for m in ("ecorr_V","icorr_A","icorr_A_cm2","corrosion_rate_mm_per_year") if m in mby][:2]
            sl=add_slide(spec,"Ecorr/Icorr and rate metrics are shown only where robust fits and required inputs support them.")
            positions=[(.68,1.55,5.85,4.95),(6.80,1.55,5.85,4.95)]
            for j,mid in enumerate(mids):
                img=assets/f"v303_corrosion_{spec.get('result_index',0)}_{mid}.png"; made=_plot_derived_metric(rs,mby[mid],axis,img)
                if made.get("created"): add_picture_fit(sl,img,*positions[j])
            if not mids: panel(sl,.8,1.7,11.7,4.6,"No robust scalar corrosion metrics available",["Review the polarization curves and analysis settings before reporting quantitative corrosion values."],AMBER)
        elif kind=="corrosion_quality":
            rs=(electro.get("corrosion") or [])[int(spec.get("result_index") or 0)]; review=list(rs.get("review_rows") or [])[:10]; counts=rs.get("status_counts") or {}
            sl=add_slide(spec,f"CLEAR {counts.get('CLEAR',0)} · REVIEW {counts.get('REVIEW',0)} · AMBIGUOUS {counts.get('AMBIGUOUS',0)} · NO CLEAR ECORR {counts.get('NO_CLEAR_ECORR',0)}")
            if review:
                table=sl.shapes.add_table(len(review)+1,5,Inches(.72),Inches(1.55),Inches(11.9),Inches(min(4.95,.47*(len(review)+1)))).table
                for c,h in enumerate(["Point","Status","Flags","Crossing families","Significant dips"]): table.cell(0,c).text=h
                for r,row in enumerate(review,1):
                    vals=[row.get("point_index"),row.get("status"),", ".join(map(str,row.get("flags") or [])),row.get("zero_crossing_cluster_count"),row.get("dip_count")]
                    for c,v in enumerate(vals): table.cell(r,c).text=_clean_text(v,100)
                table_style(table,14)
            else: panel(sl,.8,1.7,11.7,4.6,"Quality summary",["No review/ambiguous rows were reported by the robust corrosion screening."],GREEN)
        elif kind=="technique_consolidated":
            views=[v for v in (spec.get("views") or []) if isinstance(v, Mapping)]
            tech_label=_clean_text(spec.get("technique_label") or spec.get("technique_key") or "Technique", 90)
            count=int(spec.get("dataset_count") or 0); points=int(spec.get("point_count") or 0); analyses=int(spec.get("analysis_count") or 0)
            sl=add_slide(spec, f"Consolidated {tech_label} result · {count} dataset(s), {points} point(s), {analyses} analysis result(s).")
            created=[]
            positions=[(.62,1.45,5.86,4.82),(6.78,1.45,5.86,4.82)] if len(views)>=2 else [(.82,1.45,7.75,4.82)]
            for j,v in enumerate(views[:2]):
                img=assets/f"v311_consolidated_{_safe_filename(sid)}_{j}.png"; made=_plot_comparison(manifest,v,img)
                if made.get("created"):
                    add_picture_fit(sl,img,*positions[j]); created.append(v)
            bullets=[]
            if views:
                bullets.append("Representative view" + ("s" if len(created)>1 else "") + ": " + "; ".join(_clean_text(v.get("view_label") or v.get("view_id"),55) for v in views[:2]))
            if spec.get("source_summary"):
                bullets.extend(list(spec.get("source_summary") or [])[:3])
            if spec.get("warnings"):
                bullets.extend(list(spec.get("warnings") or [])[:2])
            if not bullets:
                bullets=["No finite, presentation-ready plot was available; inspect this technique in the Browser for source-level details."]
            side_x=9.05 if len(created)<2 else .82
            side_y=1.55 if len(created)<2 else 6.27
            side_w=3.42 if len(created)<2 else 11.65
            side_h=4.75 if len(created)<2 else .62
            if created:
                if len(created)<2: panel(sl,side_x,side_y,side_w,side_h,"Technique summary",bullets,TEAL)
                else: textbox(sl,side_x,side_y,side_w,side_h," • ".join(_clean_text(x,160) for x in bullets[:3]),13,MUTED)
            else:
                panel(sl,.85,1.7,11.6,4.6,"Consolidated result unavailable",bullets,AMBER)
        elif kind=="comparison":
            v=spec.get("view") or {}; sl=add_slide(spec,_clean_text(v.get("description"),210) or f"Technique-aware comparison across {v.get('dataset_count',0)} compatible datasets.")
            img=assets/f"v303_view_{_safe_filename(sid)}.png"; made=_plot_comparison(manifest,v,img)
            if made.get("created"): add_picture_fit(sl,img)
            else: panel(sl,.85,1.7,11.6,4.6,"Plot unavailable",["The view passed scientific gating but a finite numeric preview could not be rendered. Review the source data in the Browser."],AMBER)
        elif kind=="media":
            sl=add_slide(spec,"Representative media are used for experimental context, not as a substitute for quantitative analysis.")
            media=_find_media_paths(root,manifest,4)
            for idx,(p,label) in enumerate(media):
                col=idx%2; row=idx//2; x=.75+col*6.15; y=1.55+row*2.55
                try: sl.shapes.add_picture(str(p),Inches(x),Inches(y),width=Inches(5.65),height=Inches(2.05)); textbox(sl,x,y+2.08,5.65,.28,label,12,MUTED,False,PP_ALIGN.CENTER)
                except Exception: pass
        elif kind=="unusual":
            unusual=list(d.get("unusual_points") or [])[:10]; sl=add_slide(spec,"Robust flags identify review priorities; they are not automatic exclusions.")
            table=sl.shapes.add_table(len(unusual)+1,6,Inches(.72),Inches(1.55),Inches(11.9),Inches(min(4.95,.47*(len(unusual)+1)))).table
            for c,h in enumerate(["Technique","View","Metric","Point","Value","Robust z"]): table.cell(0,c).text=h
            for r,u in enumerate(unusual,1):
                vals=[u.get("technique_label"),u.get("view_label"),u.get("metric_label"),u.get("point"),_nice(u.get("value"),u.get("unit") or ""),_nice(u.get("robust_z"))]
                for c,v in enumerate(vals): table.cell(r,c).text=_clean_text(v,80)
            table_style(table,14)
        elif kind=="conclusions":
            sl=add_slide(spec,"Direct observations, interpretation, and verification are kept separate.")
            panel(sl,.68,1.55,6.0,4.95,"Key findings",narrative.get("conclusions") or ["Use reproducible technique-specific and spatial trends as the basis for conclusions."],GREEN)
            checks=[]
            for disc in (narrative.get("technique_discussions") or {}).values():
                if isinstance(disc, Mapping): checks.extend(disc.get("recommended_checks") or [])
            panel(sl,6.88,1.55,5.8,4.95,"Next verification",checks[:5] or ["Review flagged raw traces, fit assumptions, spatial registration, and replicate coverage before mechanistic interpretation."],NAVY)
        elif kind=="derived_metric":
            rkind=str(spec.get("result_kind") or ""); idx=int(spec.get("result_index") or 0); mid=str(spec.get("metric_id") or ""); axis=str(spec.get("axis") or "Point")
            sets=electro.get(rkind) or []; rs=sets[idx] if idx < len(sets) else {}
            mby={str(m.get("id")):m for m in rs.get("metrics") or []}; metric=mby.get(mid)
            if mid=="mean_ocp_V" and metric is None: metric={"id":"mean_ocp_V","label":"Average OCP","unit":"V"}
            sl=add_slide(spec,f"Technical appendix · {_derived_axis_label(axis)}")
            if metric:
                img=assets/f"v303_append_metric_{_safe_filename(sid)}.png"; made=_plot_derived_metric(rs,metric,axis,img)
                if made.get("created"): add_picture_fit(sl,img)
                else: panel(sl,.8,1.7,11.7,4.6,"Plot unavailable",["No finite values are available for this derived metric/axis combination."],AMBER)
            else: panel(sl,.8,1.7,11.7,4.6,"Metric unavailable",["The requested derived metric is not present in the current result set."],AMBER)
        elif kind=="takedata_details":
            rs=(electro.get("takedata") or [])[int(spec.get("result_index") or 0)]; sl=add_slide(spec,"Only varying or provenance-relevant Takedata diagnostics are shown.")
            mby={str(m.get("id")):m for m in rs.get("metrics") or []}; varying=[]
            for mid in ("x_position_um","y_position_um","z_position_um","step_distance_um","delta_x_um","delta_y_um","emon_V","imon_A"):
                m=mby.get(mid)
                if not m: continue
                vals=[_finite(r.get(mid)) for r in rs.get("rows") or []]; vals=[v for v in vals if v is not None]
                if len(vals)>1 and (max(vals)-min(vals))>max(1e-15,max(abs(v) for v in vals)*1e-12): varying.append(m)
            if varying:
                for j,m in enumerate(varying[:2]):
                    img=assets/f"v303_append_takedata_{spec.get('result_index',0)}_{j}.png"; made=_plot_derived_metric(rs,m,"Point",img); x=.68+6.12*j
                    if made.get("created"): add_picture_fit(sl,img,x,1.55,5.85,4.95)
            else: panel(sl,.8,1.7,11.7,4.6,"No varying Takedata diagnostic fields",["Constant coordinate/monitor channels are omitted because they do not add scientific information."],TEAL)
        elif kind=="analysis_inventory":
            analyses=list(d.get("analysis_cards") or [])[:10]; sl=add_slide(spec,"Detailed derived outputs are retained for audit and follow-up analysis.")
            table=sl.shapes.add_table(len(analyses)+1,4,Inches(.72),Inches(1.55),Inches(11.9),Inches(min(4.95,.47*(len(analyses)+1)))).table
            for c,h in enumerate(["Analysis","Family","Point","Method/group"]): table.cell(0,c).text=h
            for r,a in enumerate(analyses,1):
                vals=[a.get("display_name"),a.get("family"),a.get("point") if a.get("point") is not None else "—",a.get("method_label") or "—"]
                for c,v in enumerate(vals): table.cell(r,c).text=_clean_text(v,100)
            table_style(table,14)
        elif kind=="provenance":
            sl=add_slide(spec,"The deck is a scientific review aid generated from manifest-level evidence and derived results.")
            bullets=[f"Source manifest schema: {manifest.get('schema')}","Raw experimental files are not sent to the Direct LLM presentation discussion.","Semantically invalid or non-informative plots are suppressed from the main deck.","The technical appendix retains secondary valid views when Comprehensive detail is selected.","All LLM discussion remains evidence-bounded and should be reviewed against the plots and source data."]
            panel(sl,.82,1.65,11.65,4.7,"Provenance and interpretation boundaries",bullets,NAVY)
        else:
            sl=add_slide(spec); panel(sl,.82,1.65,11.65,4.7,"Summary",["No dedicated layout is available for this planned slide type."],AMBER)

    for idx,sl in enumerate(prs.slides,1):
        footer(sl,idx)
        rec=slide_records[idx-1] if idx-1<len(slide_records) else {"speaker_notes":""}
        set_notes(sl,rec.get("speaker_notes") or "")
    return prs, slide_records



def _v311_best_views_for_technique(manifest: Mapping[str, Any], key: str, limit: int = 2) -> list[Mapping[str, Any]]:
    matrix=[v for v in ((manifest.get("comparison") or {}).get("matrix") or []) if isinstance(v, Mapping)]
    views=[v for v in matrix if str(v.get("technique_key") or "").upper()==str(key or "").upper() and _v303_view_is_semantically_valid(manifest, v)]
    views=sorted(views, key=_v303_view_rank)
    # Suppress row-index technical/debug views unless nothing else exists.
    professional=[v for v in views if " vs row" not in str(v.get("view_label") or "").lower() and not str(v.get("view_id") or "").lower().endswith("_row")]
    chosen=professional[:limit] or views[:limit]
    return [dict(v) for v in chosen]

def _v311_detected_rows(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows=[dict(r) for r in ((manifest.get("discovery") or {}).get("techniques") or []) if isinstance(r, Mapping)]
    present={str(r.get("technique_key") or "").upper() for r in rows}
    er=manifest.get("electrochemistry_results") or {}
    if isinstance(er, Mapping):
        if er.get("ocp") and "OCP" not in present:
            rows.append({"technique_key":"OCP","technique_label":"Open-circuit potential (OCP)","family":"Electrochemistry","dataset_count":len(er.get("ocp") or []),"point_count":0,"method_count":0,"analysis_count":0})
            present.add("OCP")
        if er.get("corrosion") and "CORROSION_ANALYSIS" not in present:
            rows.append({"technique_key":"CORROSION_ANALYSIS","technique_label":"Corrosion, LPR, and polarization analysis","family":"Polarization analysis","dataset_count":0,"point_count":0,"method_count":0,"analysis_count":len(er.get("corrosion") or [])})
            present.add("CORROSION_ANALYSIS")
        if er.get("takedata") and "POSITION" not in present:
            rows.append({"technique_key":"POSITION","technique_label":"Position / motion trace","family":"Spatial","dataset_count":len(er.get("takedata") or []),"point_count":0,"method_count":0,"analysis_count":0})
            present.add("POSITION")
    def rank(r):
        key=str(r.get("technique_key") or "OTHER").upper()
        family=str(r.get("family") or "")
        is_position=1 if key in {"POSITION","SPATIAL_MAP"} else 0
        return (is_position, -int(r.get("analysis_count") or 0), -int(r.get("dataset_count") or 0), family, str(r.get("technique_label") or key))
    return sorted(rows, key=rank)

def _v311_technique_summary_bullets(manifest: Mapping[str, Any], row: Mapping[str, Any], views: Sequence[Mapping[str, Any]]) -> list[str]:
    key=str(row.get("technique_key") or "OTHER").upper(); bullets=[]
    if key in {"EIS","GEIS"}: bullets.append("Use Nyquist/Bode response as the consolidated impedance result; keep potential identity and frequency coverage explicit.")
    elif key=="DRT": bullets.append("Report DRT only as source-linked relaxation information; peak metrics require exported peak parameters before stronger conclusions.")
    elif key in {"CV","CV_LSV","LSV","IV","RDE","RRDE"}: bullets.append("Use potential-current overlays as the primary result; scan rate and branch context are not DC set-potential labels.")
    elif key=="APPROACH": bullets.append("Use the approach trace as contact/probe-context evidence rather than a separate mechanistic conclusion.")
    elif key in {"POSITION","SPATIAL_MAP"}: bullets.append("Position/motion data are treated as provenance unless measured coordinates vary enough to support spatial interpretation.")
    elif key in {"CA","CP","GCD","GITT","PITT"}: bullets.append("Use time/capacity profiles as the consolidated transient/cycling result; derived metrics are summarized only when available.")
    else: bullets.append("Summarize the highest-value compatible comparison view; avoid file-inventory or parser-diagnostic slides.")
    if views:
        bullets.append("Selected view" + ("s" if len(views)>1 else "") + ": " + "; ".join(_clean_text(v.get("view_label") or v.get("view_id"),70) for v in views[:2]))
    if int(row.get("analysis_count") or 0): bullets.append(f"Includes {int(row.get('analysis_count') or 0)} derived analysis result(s) linked to this technique family.")
    return bullets[:4]

def _v311_plan(manifest: Mapping[str, Any], detail_level: str) -> dict[str, Any]:
    d=dict(manifest.get("discovery") or {})
    matrix=[v for v in ((manifest.get("comparison") or {}).get("matrix") or []) if isinstance(v, Mapping)]
    valid=[v for v in matrix if _v303_view_is_semantically_valid(manifest, v)]
    main=[]; appendix=[]
    def add(target, sid, kind, title, **kw): target.append({"id":sid,"kind":kind,"title":title,**kw})
    add(main,"title","title","Scientific data summary")
    add(main,"executive","executive","Consolidated scientific overview")
    add(main,"coverage","coverage","Technique coverage and data readiness")
    # One professional consolidated result slide per actual scientific technique.
    for row in _v311_detected_rows(manifest):
        key=str(row.get("technique_key") or "OTHER").upper()
        # Do not make separate slides for empty/non-varying position placeholders.
        if key in {"POSITION","SPATIAL_MAP"} and not int(row.get("dataset_count") or 0) > 3:
            continue
        views=_v311_best_views_for_technique(manifest,key,limit=2)
        if not views and key not in {"DRT","DRT_ANALYSIS","OCP","CORROSION_ANALYSIS"}:
            continue
        label=str(row.get("technique_label") or key)
        add(main,f"technique-{key.lower()}","technique_consolidated",f"{label}: consolidated result",technique_key=key,technique_label=label,views=views,dataset_count=row.get("dataset_count",0),point_count=row.get("point_count",0),analysis_count=row.get("analysis_count",0),source_summary=_v311_technique_summary_bullets(manifest,row,views))
    # Keep provenance concise and only at the end. No automatic technical appendix by default.
    add(main,"conclusions","conclusions","Key findings, limitations, and next checks")
    if str(detail_level or "").lower() in {"audit","appendix"}:
        add(appendix,"appendix-provenance","provenance","Provenance and interpretation boundaries")
    detected=[str(r.get("technique_key") or "UNKNOWN").upper() for r in _v311_detected_rows(manifest)]
    recipes={k:TECHNIQUE_SLIDE_RECIPES_V304.get(k,TECHNIQUE_SLIDE_RECIPES_V304["UNKNOWN"]) for k in detected}
    return {"schema":"faradaiq-presentation-plan-v311","main":main,"appendix":appendix,"valid_view_count":len(valid),"suppressed_view_count":len(matrix)-len(valid),"consolidated_results_per_technique":True,"professional_deck_mode":True,"technique_recipe_registry_version":"v311-consolidated","technique_slide_recipes":recipes}

def _v311_llm_prompt(context: Mapping[str, Any]) -> str:
    prompt=_v310_llm_prompt(context)
    guard="""

V311 PROFESSIONAL CONSOLIDATION POLICY
- Do not create or request file-inventory, parser-diagnostic, recipe-registry, row-index, or unavailable-plot narrative unless explicitly present as an audit appendix.
- Treat the deck as an executive scientific result package: one consolidated result per detected technique, plus overview and conclusions.
- Keep presenter notes useful but concise. Do not repeat the same EIS/CV paragraph for every derived axis or appendix view.
- If coordinates are synthetic compatibility placeholders, mention this once as a limitation; do not create separate spatial relationship claims.
- Never interpret date-like or sample-name tokens as electrochemical set potentials.
"""
    return prompt + guard

def write_powerpoint_summary(experiment_root: str | Path, *, analysis_root: str | Path | None = None, experiment_name: str | None = None, manifest: Mapping[str, Any] | None = None, use_direct_llm: bool = True, detail_level: str = DEFAULT_DETAIL_LEVEL) -> dict[str, Any]:
    root=Path(experiment_root).expanduser().resolve(); root.mkdir(parents=True,exist_ok=True); assets=root/PPT_ASSET_DIR; assets.mkdir(parents=True,exist_ok=True)
    if manifest is None:
        from .v290_portable_browser import build_manifest
        manifest=build_manifest(root,analysis_root=analysis_root,experiment_name=experiment_name,create_batch_zips=False)
    manifest=dict(manifest); promoted=_promote_named_analysis_acronyms(manifest)
    if promoted or not manifest.get("discovery") or not (manifest.get("comparison") or {}).get("matrix"):
        try:
            from . import v290_portable_browser as browser
            datasets=[dict(d) for d in (manifest.get("datasets") or [])]; browser._assign_ui_roles(datasets); comparison=browser._comparison_summary(datasets); discovery=browser._discovery_summary(datasets,comparison); manifest["datasets"]=datasets; manifest["comparison"]=comparison; manifest["discovery"]=discovery
        except Exception: pass
    level=str(detail_level or DEFAULT_DETAIL_LEVEL).lower(); plan=_v303_plan(manifest,level)
    context=_v303_compact_manifest_context(manifest,plan); (assets/PPT_LLM_CONTEXT).write_text(json.dumps(context,ensure_ascii=False,indent=2),encoding="utf-8")
    prompt=_v303_llm_prompt(context); (assets/PPT_LLM_PROMPT).write_text(prompt,encoding="utf-8")
    narrative=_v303_fallback_narrative(manifest,plan); llm_status={"status":"not_requested","experimental_file_upload":False,"compact_context_only":True}
    if use_direct_llm:
        try:
            parsed,llm_status=_call_direct_llm(prompt)
            if parsed:
                # Fill any omitted slide notes deterministically rather than leaving blanks.
                fallback=_v303_fallback_narrative(manifest,plan)
                parsed.setdefault("slide_content",{})
                for sid,rec in (fallback.get("slide_content") or {}).items(): parsed["slide_content"].setdefault(sid,rec)
                for key in ("executive_summary","cross_technique_observations","conclusions","global_cautions","technique_discussions"):
                    if not parsed.get(key): parsed[key]=fallback.get(key)
                narrative=parsed; narrative["_llm_used"]=True
            else: narrative["_llm_used"]=False
        except Exception as exc:
            llm_status={"status":"failed","error":str(exc),"traceback":traceback.format_exc(limit=8),"experimental_file_upload":False,"compact_context_only":True}; narrative["_llm_used"]=False
    (assets/PPT_LLM_RESPONSE).write_text(json.dumps({"narrative":narrative,"transaction":{k:v for k,v in llm_status.items() if k!="raw_answer"},"plan":plan},ensure_ascii=False,indent=2),encoding="utf-8")
    if llm_status.get("raw_answer"): (assets/"ppt_llm_response_raw.txt").write_text(str(llm_status.get("raw_answer")),encoding="utf-8")
    prs,slides=_v303_build_presentation(root,manifest,narrative,assets,level,plan); ppt_path=root/PPTX_NAME; prs.save(ppt_path)
    main_count=len(plan.get("main") or []); appendix_count=(1+len(plan.get("appendix") or [])) if plan.get("appendix") else 0
    receipt={"schema":"faradaiq-powerpoint-summary-v303","version":VERSION,"created":datetime.now().isoformat(timespec="seconds"),"experiment_name":manifest.get("experiment_name"),"pptx_path":str(ppt_path),"pptx_name":ppt_path.name,"slide_count":len(slides),"main_slide_count":main_count,"appendix_slide_count":appendix_count,"detail_level":level,"technique_aware":True,"scientific_story_planner":True,"suppressed_semantically_invalid_views":plan.get("suppressed_view_count",0),"all_available_scientific_comparison_views":False,"direct_llm_requested":bool(use_direct_llm),"direct_llm_used":bool(narrative.get("_llm_used")),"direct_llm_status":{k:v for k,v in llm_status.items() if k!="raw_answer"},"compact_context_path":str(assets/PPT_LLM_CONTEXT),"prompt_path":str(assets/PPT_LLM_PROMPT),"response_path":str(assets/PPT_LLM_RESPONSE),"raw_experimental_files_sent_to_llm":False,"front_slide_footer":FRONT_SLIDE_FOOTER,"presentation_theme":"scientific_story_light_v303","speaker_notes_included":True,"speaker_notes_style":"scientific_paragraphs","max_scientific_plots_per_slide":2,"consolidated_results_per_technique":True,"professional_deck_mode":True,"slide_index":slides,"presentation_plan":plan}
    (root/PPT_MANIFEST_NAME).write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8"); return receipt
# ---- End FaradaIQ Studio v303 scientific-story PowerPoint engine ----


# ---- FaradaIQ Studio v304 technique-specific PowerPoint recipe engine ----
# v304 keeps the v303 story-first deck but adds a deterministic recipe registry
# so each technique family has a scientifically appropriate set of core and
# appendix slide candidates.  The Direct LLM may discuss only evidence exposed
# by this planner; it cannot invent measurements or derived metrics.
_V303_PLAN_FOR_V304 = _v303_plan
_V303_WRITE_FOR_V304 = write_powerpoint_summary

VERSION = "v314"
VERSION_NUMBER = 311
APP_TITLE = f"FaradaIQ eStudio {VERSION}"
FRONT_SLIDE_FOOTER = f"Made by FaradaIQ eStudio {VERSION}"
DEFAULT_DETAIL_LEVEL = "consolidated"

TECHNIQUE_SLIDE_RECIPES_V304 = {
    "TAKEDATA": {"label":"Takedata / position / motion", "core":["XY/XZ/YZ coordinate geometry", "X/Y/Z/time/point station diagnostics", "coordinate–Emon/Imon context"], "appendix":["all valid coordinate and monitor pairings", "raw acquisition chronology", "duplicate/repeated station audit"]},
    "POSITION": {"label":"Position / motion trace", "core":["motion/position vs time", "spatial ordering", "drift/repeatability"], "appendix":["axis-specific raw traces", "station spacing diagnostics"]},
    "SPATIAL_MAP": {"label":"Spatial map / point comparison", "core":["XY acquisition map", "primary metric map/profile", "point-linked raw curves/images"], "appendix":["secondary metrics", "spatial correlation matrix"]},
    "OCP": {"label":"Open-circuit potential", "core":["representative OCP time traces", "final-window average vs Point/X/Y/Distance", "SD/SEM/tail-drift stability"], "appendix":["all spatial axes", "raw trace overlays", "stability diagnostics"]},
    "TAFEL": {"label":"Tafel / polarization", "core":["raw polarization curves", "log10(|I|) vs potential", "Ecorr/Icorr/slopes and ambiguity"], "appendix":["branch fit windows", "residuals", "candidate crossings/dips"]},
    "CV": {"label":"Cyclic voltammetry", "core":["potential-current overlays", "peak current/potential", "cycle evolution / integrated charge"], "appendix":["individual cycles", "hysteresis", "scan-rate trends"]},
    "CV_LSV": {"label":"CV / LSV / IV", "core":["potential-current overlays", "onset/characteristic potential", "spatial/condition trends"], "appendix":["branch diagnostics", "integrated response"]},
    "LSV": {"label":"Linear sweep voltammetry", "core":["potential-current overlays", "onset/limiting current", "spatial/condition trends"], "appendix":["kinetic branch checks", "baseline sensitivity"]},
    "DPV": {"label":"Differential pulse voltammetry", "core":["pulse voltammograms", "peak potential/height/area", "calibration or spatial trend"], "appendix":["FWHM", "background quality"]},
    "SWV": {"label":"Square-wave voltammetry", "core":["forward/reverse/net current", "peak positions/amplitudes", "calibration/spatial trends"], "appendix":["component currents", "peak width diagnostics"]},
    "NPV": {"label":"Normal pulse voltammetry", "core":["pulse response", "plateau/peak magnitude", "potential-dependent response"], "appendix":["pulse-index evolution", "background checks"]},
    "STRIPPING": {"label":"Stripping voltammetry", "core":["stripping overlays", "peak position/amplitude/area", "calibration/spatial dependence"], "appendix":["background quality", "peak deconvolution"]},
    "CA": {"label":"Chronoamperometry", "core":["current vs time", "charge / endpoint metrics", "early-time vs long-time behavior"], "appendix":["Cottrell diagnostic when valid", "nucleation/growth fits"]},
    "CP": {"label":"Chronopotentiometry", "core":["potential vs time", "transition time/plateau", "charge and drift context"], "appendix":["Sand-type diagnostics when valid", "transition uncertainty"]},
    "DEPOSITION": {"label":"Electrodeposition", "core":["deposition transient", "integrated charge", "nucleation/induction behavior"], "appendix":["growth model fits", "morphology linkage"]},
    "GCD": {"label":"Galvanostatic cycling", "core":["voltage vs time/capacity", "capacity and efficiency", "retention/rate capability"], "appendix":["cycle-by-cycle diagnostics", "polarization evolution"]},
    "GITT": {"label":"GITT", "core":["pulse/rest profile", "relaxation and ΔEs/ΔEt", "diffusion vs SOC when valid"], "appendix":["pulse zooms", "assumption checks"]},
    "PITT": {"label":"PITT", "core":["current transients", "pulse comparison", "diffusion-related trends when valid"], "appendix":["fit residuals", "state dependence"]},
    "ZRA": {"label":"Zero-resistance ammetry", "core":["galvanic current vs time", "cumulative charge", "sign transitions/outliers"], "appendix":["event windows", "stability statistics"]},
    "EIS": {"label":"Electrochemical impedance", "core":["Nyquist", "Bode magnitude/phase", "fit/DRT parameters vs Point/X/Y/Distance"], "appendix":["residuals", "equivalent-circuit parameters", "frequency-window sensitivity"]},
    "GEIS": {"label":"Galvanostatic impedance", "core":["Nyquist", "Bode magnitude/phase", "galvanostatic excitation metadata"], "appendix":["fit residuals", "parameter trends"]},
    "DRT": {"label":"Distribution of relaxation times", "core":["gamma vs tau/frequency", "peak decomposition", "peak position/intensity trends"], "appendix":["regularization sensitivity", "frequency-window checks"]},
    "RDE": {"label":"Rotating disk electrode", "core":["rotation-rate voltammograms", "Levich", "Koutecky-Levich"], "appendix":["kinetic current", "mass-transport checks"]},
    "RRDE": {"label":"Rotating ring-disk electrode", "core":["disk/ring response", "collection efficiency", "selectivity/product trend"], "appendix":["rotation dependence", "ring background"]},
    "EQCM": {"label":"EQCM", "core":["current and mass vs time/potential", "mass/charge ratio", "cycle hysteresis"], "appendix":["apparent molar mass", "baseline drift"]},
    "CONDUCTIVITY": {"label":"Conductivity", "core":["conductivity trend", "temperature/concentration dependence", "transport parameter"], "appendix":["Arrhenius fits", "cell constant checks"]},
    "APPROACH": {"label":"Approach curve / SECCM", "core":["current vs distance", "normalized approach behavior", "characteristic metric vs X/Y/Distance"], "appendix":["contact/feedback diagnostics", "curve quality flags"]},
    "MOTT_SCHOTTKY": {"label":"Mott-Schottky", "core":["1/C² vs potential", "linear-region fit", "flat-band / slope-derived quantities"], "appendix":["frequency dependence", "fit window sensitivity"]},
    "ECSA": {"label":"ECSA", "core":["Cdl extraction", "ECSA/roughness trend", "normalization effects"], "appendix":["current-window relationships", "confidence intervals"]},
    "TRANSPORT": {"label":"Transport analysis", "core":["diffusion/conductivity/mobility metric", "state/space/time trend", "model fit"], "appendix":["assumption checks", "uncertainty"]},
    "RAMAN": {"label":"Raman spectroscopy", "core":["representative spectra", "peak position/intensity/area trends", "maps or components"], "appendix":["baseline/fitting diagnostics", "individual spectra"]},
    "IR": {"label":"IR spectroscopy", "core":["representative spectra", "band assignments/areas", "spatial/condition trends"], "appendix":["baseline correction", "peak-fit details"]},
    "FTIR": {"label":"FTIR spectroscopy", "core":["representative spectra", "band assignments/areas", "condition/spatial trends"], "appendix":["database match confidence", "fit residuals"]},
    "UVVIS": {"label":"UV-visible spectroscopy", "core":["absorbance/transmittance spectra", "band metrics", "calibration or spatial trends"], "appendix":["baseline and saturation checks", "derivative spectra"]},
    "XRD": {"label":"X-ray diffraction", "core":["diffraction pattern", "peak positions/intensities", "phase/texture metrics"], "appendix":["background subtraction", "peak fits"]},
    "XPS": {"label":"XPS", "core":["survey/high-resolution spectra", "chemical-state fits", "atomic ratios"], "appendix":["fit residuals", "background/model sensitivity"]},
    "HYPERSPECTRAL": {"label":"Hyperspectral imaging", "core":["representative spectra", "integrated-band/component maps", "pixel/region comparison"], "appendix":["PCA/component diagnostics", "ROI statistics"]},
    "IMAGE": {"label":"Imaging / microscopy", "core":["representative image", "annotated measurement context", "image-derived metric when available"], "appendix":["full gallery", "image quality/provenance"]},
    "UNKNOWN": {"label":"Unknown/future technique", "core":["data archetype overview", "representative raw view", "derived scalar/spatial trend if valid"], "appendix":["all valid nonmisleading views", "provenance"]},
}

TAKEDATA_GEOMETRY_RELATIONSHIPS_V304 = [("X","Y"),("X","Z"),("Y","Z"),("Point","Distance")]
TAKEDATA_ELECTROCHEM_RELATIONSHIPS_V304 = [("X","Imon"),("Y","Imon"),("Z","Imon"),("Distance","Imon"),("X","Emon"),("Y","Emon"),("Distance","Emon"),("Time","X"),("Time","Y"),("Time","Z")]

_FIELD_CANDIDATES_V304 = {
    "Point": ["point_index","point","station_index","record_index"],
    "Distance": ["scan_distance_um","distance_um","cumulative_distance_um","step_distance_um"],
    "Time": ["elapsed_from_start_s","time_s","elapsed_s","record_index"],
    "X": ["x_position_um","stage_x_um","x_um","x","x_m"],
    "Y": ["y_position_um","stage_y_um","y_um","y","y_m"],
    "Z": ["z_position_um","stage_z_um","z_um","z","z_m"],
    "Emon": ["emon_V","Emon_V","takedata_emon_V","emon","potential_V","potential"],
    "Imon": ["imon_A","Imon_A","takedata_imon_A","imon","current_A","current"],
}
_UNIT_LABEL_V304 = {"Point":"Point", "Distance":"Distance / µm", "Time":"Elapsed time / s", "X":"X / µm", "Y":"Y / µm", "Z":"Z / µm", "Emon":"Emon / V", "Imon":"Imon / A"}


def _v304_field_values(rows: Sequence[Mapping[str, Any]], field: str) -> list[float]:
    keys=_FIELD_CANDIDATES_V304.get(field,[field])
    vals=[]
    for i,row in enumerate(rows or []):
        got=None
        for key in keys:
            if key in row and row.get(key) not in (None,""):
                got=row.get(key); break
        if got is None and field=="Point": got=i+1
        try:
            v=float(got)
            # raw Takedata may carry metres; convert likely metre-scale XYZ to µm.
            if field in {"X","Y","Z","Distance"} and key in {"x_m","y_m","z_m","distance_m"}:
                v*=1e6
            if math.isfinite(v): vals.append(v)
            else: vals.append(float("nan"))
        except Exception:
            vals.append(float("nan"))
    return vals


def _v304_choose_rows_for_relationship(result_set: Mapping[str, Any], fx: str, fy: str) -> list[Mapping[str, Any]]:
    rows=list(result_set.get("raw_rows") or []) if (fx in {"Emon","Imon","Time"} or fy in {"Emon","Imon","Time"}) else []
    if not rows: rows=list(result_set.get("rows") or [])
    if not rows and result_set.get("raw_rows"): rows=list(result_set.get("raw_rows") or [])
    return [r for r in rows if isinstance(r, Mapping)]


def _v304_pair_values(result_set: Mapping[str, Any], fx: str, fy: str) -> tuple[list[float], list[float], int]:
    rows=_v304_choose_rows_for_relationship(result_set,fx,fy)
    xs=_v304_field_values(rows,fx); ys=_v304_field_values(rows,fy)
    paired=[]
    for x,y in zip(xs,ys):
        if math.isfinite(x) and math.isfinite(y): paired.append((x,y))
    if not paired: return [],[],len(rows)
    return [p[0] for p in paired],[p[1] for p in paired],len(rows)


def _plot_takedata_relationship_grid(result_set: Mapping[str, Any], relationships: Sequence[Sequence[str]], out_path: Path, *, title: str = "Takedata relationships") -> dict[str, Any]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    pairs=[]; skipped=0; constants=[]
    for rel in relationships or []:
        if len(rel)!=2: continue
        fx,fy=str(rel[0]),str(rel[1])
        xs,ys,nrows=_v304_pair_values(result_set,fx,fy)
        if len(xs)<2:
            skipped+=1; continue
        xspan=max(xs)-min(xs); yspan=max(ys)-min(ys)
        scale=max(1.0,max(abs(v) for v in xs+ys))
        const=(abs(xspan)<=scale*1e-12 or abs(yspan)<=scale*1e-12)
        if const: constants.append(f"{fx}–{fy}")
        pairs.append((fx,fy,xs,ys,const))
        if len(pairs)>=4: break
    if not pairs: return {"created":False,"shown":0,"skipped":skipped,"constant_relationships":constants}
    n=len(pairs); cols=2 if n>1 else 1; rows=int(math.ceil(n/cols))
    fig,axs=plt.subplots(rows,cols,figsize=(12.4,6.2),dpi=180,squeeze=False)
    for ax in axs.ravel(): ax.set_visible(False)
    for ax,(fx,fy,xs,ys,const) in zip(axs.ravel(),pairs):
        ax.set_visible(True)
        ax.plot(xs,ys,marker="o",linewidth=2.0,markersize=5)
        if len(xs)>=2:
            ax.scatter([xs[0]],[ys[0]],marker="s",s=55,label="start")
            ax.scatter([xs[-1]],[ys[-1]],marker="^",s=58,label="end")
        ax.set_xlabel(_UNIT_LABEL_V304.get(fx,fx)); ax.set_ylabel(_UNIT_LABEL_V304.get(fy,fy))
        ax.set_title(f"{fx} vs {fy}" + (" · constant field" if const else ""),loc="left",fontsize=15,weight="bold")
        _apply_scientific_plot_style(ax)
        if len(xs)>=2: ax.legend(frameon=False,fontsize=9,loc="best")
    fig.suptitle(_clean_text(title,100),fontsize=18,weight="bold",x=0.01,ha="left")
    fig.tight_layout(rect=(0,0,1,.95)); out_path.parent.mkdir(parents=True,exist_ok=True); _v307_safe_savefig(fig,out_path,bbox_inches="tight"); plt.close(fig)
    return {"created":True,"shown":len(pairs),"skipped":skipped,"constant_relationships":constants,"path":str(out_path)}


def _v304_insert_after(main: list[dict[str, Any]], predicate, items: list[dict[str, Any]]) -> None:
    if not items: return
    idx=None
    for i,spec in enumerate(main):
        if predicate(spec): idx=i+1
    if idx is None:
        for i,spec in enumerate(main):
            if spec.get("kind")=="coverage": idx=i+1; break
    if idx is None: idx=min(3,len(main))
    main[idx:idx]=items


def _v304_plan(manifest: Mapping[str, Any], detail_level: str) -> dict[str, Any]:
    plan=_V303_PLAN_FOR_V304(manifest,detail_level)
    electro=manifest.get("electrochemistry_results") if isinstance(manifest.get("electrochemistry_results"), Mapping) else {}
    main=list(plan.get("main") or []); appendix=list(plan.get("appendix") or [])
    inserted=[]
    for i,rs in enumerate(electro.get("takedata") or []):
        inserted.append({"id":f"takedata-geometry-relationships-{i}","kind":"takedata_relationship_grid","title":"Takedata coordinate geometry relationships","result_index":i,"relationships":TAKEDATA_GEOMETRY_RELATIONSHIPS_V304,"recipe_role":"coordinate geometry","plot_title":"Coordinate geometry: X–Y, X–Z, Y–Z, and scan distance"})
        inserted.append({"id":f"takedata-monitor-relationships-{i}","kind":"takedata_relationship_grid","title":"Takedata electrochemical context relationships","result_index":i,"relationships":TAKEDATA_ELECTROCHEM_RELATIONSHIPS_V304,"recipe_role":"coordinate–monitor context","plot_title":"Takedata coordinate/monitor relationships"})
    _v304_insert_after(main,lambda s: s.get("kind")=="takedata_summary",inserted)
    if str(detail_level).lower()=="comprehensive":
        appendix.append({"id":"appendix-technique-recipe-matrix","kind":"technique_recipe_matrix","title":"Technique-specific slide recipe registry"})
    detected=[str(r.get("technique_key") or "UNKNOWN").upper() for r in ((manifest.get("discovery") or {}).get("techniques") or [])]
    recipes={k:TECHNIQUE_SLIDE_RECIPES_V304.get(k,TECHNIQUE_SLIDE_RECIPES_V304["UNKNOWN"]) for k in detected}
    plan.update({"main":main,"appendix":appendix,"technique_recipe_registry_version":"v311-consolidated","technique_slide_recipes":recipes,"takedata_relationship_recipes":{"coordinate_geometry":TAKEDATA_GEOMETRY_RELATIONSHIPS_V304,"coordinate_monitor_context":TAKEDATA_ELECTROCHEM_RELATIONSHIPS_V304}})
    return plan


def _v304_fallback_narrative(manifest: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    narrative=_v303_fallback_narrative(manifest,plan)
    sc=narrative.setdefault("slide_content",{})
    for spec in list(plan.get("main") or [])+list(plan.get("appendix") or []):
        sid=str(spec.get("id")); kind=str(spec.get("kind"))
        if sid in sc: continue
        if kind=="takedata_relationship_grid":
            role=str(spec.get("recipe_role") or "Takedata relationship")
            sc[sid]={"takeaway":"Takedata relationships provide the coordinate backbone for interpreting point-derived electrochemical metrics.","speaker_notes":f"This slide applies the v304 Takedata recipe for {role}. The purpose is to inspect how spatial coordinates, acquisition order, and monitor channels relate before any downstream trend is interpreted as a real spatial effect. Coordinate relationships such as X–Y, X–Z, and Y–Z establish the scan geometry, while coordinate–Emon and coordinate–Imon views help identify whether the acquisition record itself carries electrochemical context or artifacts. Constant fields should be treated as provenance, not as correlations. If compatibility coordinates rather than measured stage positions are shown, that provenance remains part of the interpretation boundary."}
        elif kind=="technique_recipe_matrix":
            sc[sid]={"takeaway":"Each recognized technique is now planned with a technique-specific scientific slide recipe.","speaker_notes":"This appendix slide documents the recipe-based planning layer used by the v304 PowerPoint engine. Instead of generating every numerically possible plot, FaradaIQ first classifies the technique and data archetype, then selects a core set of slides that fits the scientific measurement. Detailed or lower-priority relationships are kept in the appendix, while the main deck remains focused on defensible scientific messages. This makes the presentation strategy portable across electrochemistry, spectroscopy, imaging, spatial maps, transients, and future unknown data types."}
    narrative.setdefault("cross_technique_observations",[]).append("The v304 planner uses technique-specific slide recipes and cross-technique spatial registration so coordinate, OCP, corrosion, impedance, spectral, image, and transient evidence can be presented consistently when compatible point mapping exists.")
    return narrative


def _v304_compact_manifest_context(manifest: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    context=_v303_compact_manifest_context(manifest,plan)
    context["technique_recipe_registry_version"]="v304"
    context["technique_slide_recipes"]={k:{"label":v.get("label"),"core":v.get("core"),"appendix":v.get("appendix")} for k,v in (plan.get("technique_slide_recipes") or {}).items()}
    context["takedata_relationship_recipes"]={"coordinate_geometry":TAKEDATA_GEOMETRY_RELATIONSHIPS_V304,"coordinate_monitor_context":TAKEDATA_ELECTROCHEM_RELATIONSHIPS_V304}
    context["presentation_policy"]="For each recognized technique, choose core slides from the technique-specific recipe; move valid but secondary relationships to the appendix; suppress misleading raw-trace-vs-point plots and semantically invalid transforms."
    return context


def _v310_detected_technique_keys(context: Mapping[str, Any]) -> list[str]:
    """Return every detected technique key as stable lower_snake_case names."""
    out: list[str] = []
    seen: set[str] = set()
    for row in list(context.get("techniques") or []):
        key = str((row or {}).get("technique_key") or "").strip()
        if not key:
            continue
        low = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_") or "unknown"
        if low not in seen:
            seen.add(low); out.append(low)
    return out


def _v310_presenter_keys(context: Mapping[str, Any]) -> list[str]:
    legacy = ["opening", "ocp", "corrosion", "takedata", "eis", "cv_lsv"]
    return list(dict.fromkeys(legacy + _v310_detected_technique_keys(context) + ["conclusions"]))


def _v310_llm_prompt(context: Mapping[str, Any]) -> str:
    """Active v310 prompt shared by the PowerPoint/Browser narrative artifacts."""
    detected = _v310_detected_technique_keys(context)
    presenter_keys = _v310_presenter_keys(context)
    hint_lines: list[str] = []
    for key in detected:
        scenarios = TECHNIQUE_SCENARIOS.get(key.upper()) or TECHNIQUE_SCENARIOS.get("SUMMARY", [])
        hint = "; ".join(f"{name} — {desc}" for name, desc in scenarios[:4])
        hint_lines.append(f"- {key}: {hint}")
    hints = "\n".join(hint_lines) or "- No technique-specific comparison vocabulary is available; use only labels and views supplied in the JSON."
    presenter_schema = ",\n".join(
        f'    "{key}": ["evidence-grounded presenter/browser narrative for {key.replace("_", " ")}"]'
        for key in presenter_keys
    )
    return """You are the scientific presentation director for FaradaIQ. Build one evidence-bounded narrative that is reused by the generated PowerPoint and the portable Browser report.

Use ONLY the supplied JSON. Never invent materials, experimental conditions, numerical values, mechanisms, causality, measurements, peaks, fits, or derived quantities. The deterministic planner has already decided which plots are scientifically valid.

For every planned main slide, write:
- takeaway: one concise evidence-grounded sentence suitable as a slide subtitle.
- speaker_notes: 1–3 coherent scientific paragraphs, normally 90–180 words total. Explain what is shown, what stands out, what may be interpreted, what limitation matters, and what scientifically useful check follows. Do not write generic presentation instructions.

Rules:
1. Separate direct observation, interpretation, and recommended checks.
2. Cover EVERY technique actually present in the techniques list, including less-common techniques such as GCD, DRT, RDE/RRDE, EQCM, GITT/PITT, conductivity, spatial/SECCM outputs, and derived analysis families.
3. Preserve unfamiliar acronyms exactly and never expand them unless the context defines them.
4. Treat robust outlier/ambiguity flags as review prompts, not proof.
5. For OCP, distinguish a final-window operational mean from proof of equilibrium.
6. For Tafel/corrosion/LPR, never invent current density, penetration rate, exchange current, or polarization resistance when required inputs/results are absent.
7. For EIS/GEIS/DRT, respect frequency coverage, potential identity, model/regularization assumptions, residuals, and fit validity. If a potential series is supplied, discuss the ordered potential dependence without inventing a mechanistic cause.
8. For voltammetry, never confuse scan rate or AC amplitude with DC set potential.
9. For spectroscopy, imaging, spatial, motion, battery, transport, or unfamiliar techniques, discuss only features and comparisons actually represented in the supplied evidence.
10. Cross-technique claims require compatible point, condition, chronology, or explicit source linkage.
11. Do not repeat the same paragraph across slides.
12. Return strict JSON only.

Schema:
{
  "executive_summary": ["..."],
  "cross_technique_observations": ["..."],
  "conclusions": ["..."],
  "global_cautions": ["..."],
  "slide_content": {
    "SLIDE_ID": {
      "takeaway": "one concise evidence-grounded sentence",
      "speaker_notes": "one to three polished scientific paragraphs"
    }
  },
  "technique_discussions": {
    "TECHNIQUE_KEY": {
      "observations": ["..."],
      "interpretations": ["..."],
      "limitations": ["..."],
      "recommended_checks": ["..."]
    }
  },
  "presenter_notes": {
""" + presenter_schema + """
  }
}

Include a technique_discussions entry and matching presenter_notes bucket using the same lower_snake_case key for every detected technique. Keep the legacy presenter_notes keys even when that technique is absent for backward compatibility.

Technique-specific comparison vocabulary. Use these only when the corresponding evidence exists in the JSON:
""" + hints + """

FARADAIQ_CONTEXT_JSON
""" + json.dumps(context, ensure_ascii=False, indent=2)


def _v310_fallback_narrative(manifest: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministic no-LLM narrative with the same technique buckets as v310 LLM output."""
    narrative = _v304_fallback_narrative(manifest, plan)
    context = _v304_compact_manifest_context(manifest, plan)
    detected = _v310_detected_technique_keys(context)
    discussions = narrative.setdefault("technique_discussions", {})
    presenter = narrative.setdefault("presenter_notes", {})
    presenter.setdefault("opening", ["Orient the reader to measurement coverage, technique coverage, point count, and derived-result provenance before interpreting trends."])
    presenter.setdefault("ocp", ["Treat OCP endpoint values as operational stability metrics rather than automatic proof of equilibrium. Compare the direction and magnitude of relaxation across compatible measurements, then use late-time drift, variability, duration, and replicate coverage to decide how strongly a stable-potential interpretation is supported."])
    presenter.setdefault("corrosion", ["Discuss corrosion or polarization quantities only where the corresponding fit, units, branch selection, and required material inputs are available. Compare fitted trends together with residual or ambiguity diagnostics, and treat competing crossings, boundary solutions, or incomplete branches as review findings rather than forcing a single mechanistic conclusion."])
    presenter.setdefault("takedata", ["Use Takedata and position information to establish spatial and acquisition provenance before interpreting point-wise electrochemical trends. Confirm which coordinates are measured, how points were ordered, and whether monitor channels changed with position; constant or compatibility coordinates should be presented as provenance rather than treated as scientific correlations."])
    presenter.setdefault("eis", ["Compare Nyquist and Bode behavior using the same curve identity, with acquisition potential and frequency coverage kept explicit. Describe systematic changes in intercepts, arcs, phase, magnitude, or fitted parameters only when visible or supplied, and keep model choice, residuals, frequency limits, and parameter identifiability separate from any proposed physical mechanism."])
    presenter.setdefault("cv_lsv", ["Compare voltammetric shape across compatible points, conditions, branches, and cycles, keeping scan rate and potential reference context explicit. Discuss onset, peak, plateau, limiting-current, hysteresis, or integrated-response trends only when those quantities are represented by the data, and distinguish repeatability from interpretation of the underlying reaction mechanism."])
    presenter.setdefault("conclusions", ["Close by separating the most reproducible observations from interpretation, limitations, and the most useful verification steps. Give priority to trends supported by compatible measurements and derived diagnostics, state where metadata or replicate coverage limit confidence, and identify the specific raw-data, fit, or cross-technique check that would most strengthen the next conclusion."])
    tech_rows = {str((r or {}).get("technique_key") or "").lower(): r for r in ((manifest.get("discovery") or {}).get("techniques") or [])}
    for key in detected:
        row = tech_rows.get(key, {})
        label = _clean_text((row or {}).get("technique_label") or key.replace("_", " ").upper(), 120)
        scenarios = TECHNIQUE_SCENARIOS.get(key.upper()) or TECHNIQUE_SCENARIOS.get("SUMMARY", [])
        available = [name for name, _ in scenarios[:3]]
        discussions.setdefault(key, {
            "observations": [f"{label} is represented in the experiment and should be compared using only the compatible views and derived metrics present in the manifest."],
            "interpretations": ["Interpretation is intentionally limited to trends directly supported by the plotted/derived evidence."],
            "limitations": ["Replicate coverage, metadata completeness, fit/model assumptions, and point/condition linkage should be checked before stronger conclusions."],
            "recommended_checks": ([f"Review {', '.join(available)} where available and verify the underlying source data/provenance."] if available else ["Review the available comparison views and verify the underlying source data/provenance."]),
        })
        presenter.setdefault(key, [f"Present the {label} evidence using only its available comparison views and derived metrics. State the direct observation first, identify the relevant point/condition/sequence context, then discuss the most important limitation, fit/model assumption, or provenance boundary. Finish with a concrete verification step, and do not infer quantities, mechanisms, or causal relationships that are not represented in the manifest."])
    return narrative


def _v310_slide_presenter_bucket(spec: Mapping[str, Any]) -> str:
    kind = str(spec.get("kind") or "").lower()
    if kind in {"title", "executive", "coverage", "section", "provenance"}:
        return "opening"
    if kind == "conclusions":
        return "conclusions"
    if kind.startswith("takedata"):
        return "takedata"
    rkind = str(spec.get("result_kind") or "").lower()
    if rkind == "ocp":
        return "ocp"
    if rkind == "corrosion":
        return "corrosion"
    key = re.sub(r"[^a-z0-9]+", "_", str(spec.get("technique_key") or "").lower()).strip("_")
    if key in {"cv", "lsv", "cv_lsv"}:
        return key or "cv_lsv"
    return key or "opening"


def _v310_route_presenter_notes(narrative: MutableMapping[str, Any], plan: Mapping[str, Any]) -> None:
    """Route technique presenter notes unless an LLM supplied slide-specific notes."""
    notes = narrative.get("presenter_notes") if isinstance(narrative.get("presenter_notes"), Mapping) else {}
    slide_content = narrative.setdefault("slide_content", {})
    if not isinstance(slide_content, MutableMapping):
        return
    explicit_llm_ids = set(str(x) for x in (narrative.get("_llm_slide_ids") or []))
    specs = list(plan.get("main") or []) + list(plan.get("appendix") or [])
    for spec in specs:
        sid = str(spec.get("id") or "")
        if not sid:
            continue
        rec = slide_content.setdefault(sid, {})
        if not isinstance(rec, MutableMapping):
            continue
        # Never overwrite a true slide-specific LLM narrative. For deterministic
        # output, upgrade actual technique comparison slides with the dynamic
        # technique bucket, but preserve richer recipe-specific fallback notes
        # for structural, Takedata, OCP/corrosion, and provenance slides.
        existing = str(rec.get("speaker_notes") or "").strip()
        if sid in explicit_llm_ids and existing:
            continue
        is_technique_slide = bool(str(spec.get("technique_key") or "").strip())
        if existing and not is_technique_slide:
            continue
        bucket = _v310_slide_presenter_bucket(spec)
        choices = notes.get(bucket) if isinstance(notes, Mapping) else None
        if not choices and bucket in {"cv", "lsv"} and isinstance(notes, Mapping):
            choices = notes.get("cv_lsv")
        # Only use opening as a fallback for non-technique structural slides; do
        # not make an unknown scientific technique sound like a generic title slide.
        if not choices and bucket == "opening" and isinstance(notes, Mapping):
            choices = notes.get("opening")
        if isinstance(choices, str):
            text = choices
        elif isinstance(choices, Sequence) and not isinstance(choices, (str, bytes)):
            text = " ".join(_clean_text(x, 700) for x in choices if str(x or "").strip())
        else:
            text = ""
        if text:
            rec["speaker_notes"] = text


def _v310_merge_narrative(parsed: MutableMapping[str, Any], fallback: Mapping[str, Any]) -> None:
    """Fill omitted dynamic buckets without overwriting valid LLM content."""
    original_slide_content = parsed.get("slide_content") if isinstance(parsed.get("slide_content"), Mapping) else {}
    parsed["_llm_slide_ids"] = [str(sid) for sid, rec in original_slide_content.items() if isinstance(rec, Mapping) and str(rec.get("speaker_notes") or "").strip()]
    parsed.setdefault("slide_content", {})
    for sid, rec in (fallback.get("slide_content") or {}).items():
        parsed["slide_content"].setdefault(sid, rec)
    for key in ("executive_summary", "cross_technique_observations", "conclusions", "global_cautions"):
        if not parsed.get(key):
            parsed[key] = fallback.get(key)
    for map_key in ("technique_discussions", "presenter_notes"):
        dst = parsed.setdefault(map_key, {})
        src = fallback.get(map_key) or {}
        if isinstance(dst, MutableMapping) and isinstance(src, Mapping):
            for k, v in src.items():
                dst.setdefault(k, v)



def _v311_best_views_for_technique(manifest: Mapping[str, Any], key: str, limit: int = 2) -> list[Mapping[str, Any]]:
    matrix=[v for v in ((manifest.get("comparison") or {}).get("matrix") or []) if isinstance(v, Mapping)]
    views=[v for v in matrix if str(v.get("technique_key") or "").upper()==str(key or "").upper() and _v303_view_is_semantically_valid(manifest, v)]
    views=sorted(views, key=_v303_view_rank)
    # Suppress row-index technical/debug views unless nothing else exists.
    professional=[v for v in views if " vs row" not in str(v.get("view_label") or "").lower() and not str(v.get("view_id") or "").lower().endswith("_row")]
    chosen=professional[:limit] or views[:limit]
    return [dict(v) for v in chosen]

def _v311_detected_rows(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows=[dict(r) for r in ((manifest.get("discovery") or {}).get("techniques") or []) if isinstance(r, Mapping)]
    present={str(r.get("technique_key") or "").upper() for r in rows}
    er=manifest.get("electrochemistry_results") or {}
    if isinstance(er, Mapping):
        if er.get("ocp") and "OCP" not in present:
            rows.append({"technique_key":"OCP","technique_label":"Open-circuit potential (OCP)","family":"Electrochemistry","dataset_count":len(er.get("ocp") or []),"point_count":0,"method_count":0,"analysis_count":0})
            present.add("OCP")
        if er.get("corrosion") and "CORROSION_ANALYSIS" not in present:
            rows.append({"technique_key":"CORROSION_ANALYSIS","technique_label":"Corrosion, LPR, and polarization analysis","family":"Polarization analysis","dataset_count":0,"point_count":0,"method_count":0,"analysis_count":len(er.get("corrosion") or [])})
            present.add("CORROSION_ANALYSIS")
        if er.get("takedata") and "POSITION" not in present:
            rows.append({"technique_key":"POSITION","technique_label":"Position / motion trace","family":"Spatial","dataset_count":len(er.get("takedata") or []),"point_count":0,"method_count":0,"analysis_count":0})
            present.add("POSITION")
    def rank(r):
        key=str(r.get("technique_key") or "OTHER").upper()
        family=str(r.get("family") or "")
        is_position=1 if key in {"POSITION","SPATIAL_MAP"} else 0
        return (is_position, -int(r.get("analysis_count") or 0), -int(r.get("dataset_count") or 0), family, str(r.get("technique_label") or key))
    return sorted(rows, key=rank)

def _v311_technique_summary_bullets(manifest: Mapping[str, Any], row: Mapping[str, Any], views: Sequence[Mapping[str, Any]]) -> list[str]:
    key=str(row.get("technique_key") or "OTHER").upper(); bullets=[]
    if key in {"EIS","GEIS"}: bullets.append("Use Nyquist/Bode response as the consolidated impedance result; keep potential identity and frequency coverage explicit.")
    elif key=="DRT": bullets.append("Report DRT only as source-linked relaxation information; peak metrics require exported peak parameters before stronger conclusions.")
    elif key in {"CV","CV_LSV","LSV","IV","RDE","RRDE"}: bullets.append("Use potential-current overlays as the primary result; scan rate and branch context are not DC set-potential labels.")
    elif key=="APPROACH": bullets.append("Use the approach trace as contact/probe-context evidence rather than a separate mechanistic conclusion.")
    elif key in {"POSITION","SPATIAL_MAP"}: bullets.append("Position/motion data are treated as provenance unless measured coordinates vary enough to support spatial interpretation.")
    elif key in {"CA","CP","GCD","GITT","PITT"}: bullets.append("Use time/capacity profiles as the consolidated transient/cycling result; derived metrics are summarized only when available.")
    else: bullets.append("Summarize the highest-value compatible comparison view; avoid file-inventory or parser-diagnostic slides.")
    if views:
        bullets.append("Selected view" + ("s" if len(views)>1 else "") + ": " + "; ".join(_clean_text(v.get("view_label") or v.get("view_id"),70) for v in views[:2]))
    if int(row.get("analysis_count") or 0): bullets.append(f"Includes {int(row.get('analysis_count') or 0)} derived analysis result(s) linked to this technique family.")
    return bullets[:4]

def _v311_plan(manifest: Mapping[str, Any], detail_level: str) -> dict[str, Any]:
    d=dict(manifest.get("discovery") or {})
    matrix=[v for v in ((manifest.get("comparison") or {}).get("matrix") or []) if isinstance(v, Mapping)]
    valid=[v for v in matrix if _v303_view_is_semantically_valid(manifest, v)]
    main=[]; appendix=[]
    def add(target, sid, kind, title, **kw): target.append({"id":sid,"kind":kind,"title":title,**kw})
    add(main,"title","title","Scientific data summary")
    add(main,"executive","executive","Consolidated scientific overview")
    add(main,"coverage","coverage","Technique coverage and data readiness")
    # One professional consolidated result slide per actual scientific technique.
    for row in _v311_detected_rows(manifest):
        key=str(row.get("technique_key") or "OTHER").upper()
        # Do not make separate slides for empty/non-varying position placeholders.
        if key in {"POSITION","SPATIAL_MAP"} and not int(row.get("dataset_count") or 0) > 3:
            continue
        views=_v311_best_views_for_technique(manifest,key,limit=2)
        if not views and key not in {"DRT","DRT_ANALYSIS","OCP","CORROSION_ANALYSIS"}:
            continue
        label=str(row.get("technique_label") or key)
        add(main,f"technique-{key.lower()}","technique_consolidated",f"{label}: consolidated result",technique_key=key,technique_label=label,views=views,dataset_count=row.get("dataset_count",0),point_count=row.get("point_count",0),analysis_count=row.get("analysis_count",0),source_summary=_v311_technique_summary_bullets(manifest,row,views))
    # Keep provenance concise and only at the end. No automatic technical appendix by default.
    add(main,"conclusions","conclusions","Key findings, limitations, and next checks")
    if str(detail_level or "").lower() in {"audit","appendix"}:
        add(appendix,"appendix-provenance","provenance","Provenance and interpretation boundaries")
    detected=[str(r.get("technique_key") or "UNKNOWN").upper() for r in _v311_detected_rows(manifest)]
    recipes={k:TECHNIQUE_SLIDE_RECIPES_V304.get(k,TECHNIQUE_SLIDE_RECIPES_V304["UNKNOWN"]) for k in detected}
    return {"schema":"faradaiq-presentation-plan-v311","main":main,"appendix":appendix,"valid_view_count":len(valid),"suppressed_view_count":len(matrix)-len(valid),"consolidated_results_per_technique":True,"professional_deck_mode":True,"technique_recipe_registry_version":"v311-consolidated","technique_slide_recipes":recipes}

def _v311_llm_prompt(context: Mapping[str, Any]) -> str:
    prompt=_v310_llm_prompt(context)
    guard="""

V311 PROFESSIONAL CONSOLIDATION POLICY
- Do not create or request file-inventory, parser-diagnostic, recipe-registry, row-index, or unavailable-plot narrative unless explicitly present as an audit appendix.
- Treat the deck as an executive scientific result package: one consolidated result per detected technique, plus overview and conclusions.
- Keep presenter notes useful but concise. Do not repeat the same EIS/CV paragraph for every derived axis or appendix view.
- If coordinates are synthetic compatibility placeholders, mention this once as a limitation; do not create separate spatial relationship claims.
- Never interpret date-like or sample-name tokens as electrochemical set potentials.
"""
    return prompt + guard

def write_powerpoint_summary(experiment_root: str | Path, *, analysis_root: str | Path | None = None, experiment_name: str | None = None, manifest: Mapping[str, Any] | None = None, use_direct_llm: bool = True, detail_level: str = DEFAULT_DETAIL_LEVEL) -> dict[str, Any]:
    root=Path(experiment_root).expanduser().resolve(); root.mkdir(parents=True,exist_ok=True); assets=root/PPT_ASSET_DIR; assets.mkdir(parents=True,exist_ok=True)
    if manifest is None:
        from .v290_portable_browser import build_manifest
        manifest=build_manifest(root,analysis_root=analysis_root,experiment_name=experiment_name,create_batch_zips=False)
    manifest=dict(manifest); promoted=_promote_named_analysis_acronyms(manifest)
    if promoted or not manifest.get("discovery") or not (manifest.get("comparison") or {}).get("matrix"):
        try:
            from . import v290_portable_browser as browser
            datasets=[dict(d) for d in (manifest.get("datasets") or [])]; browser._assign_ui_roles(datasets); comparison=browser._comparison_summary(datasets); discovery=browser._discovery_summary(datasets,comparison); manifest["datasets"]=datasets; manifest["comparison"]=comparison; manifest["discovery"]=discovery
        except Exception: pass
    level=str(detail_level or DEFAULT_DETAIL_LEVEL).lower(); plan=_v311_plan(manifest,level)
    context=_v304_compact_manifest_context(manifest,plan); (assets/PPT_LLM_CONTEXT).write_text(json.dumps(context,ensure_ascii=False,indent=2),encoding="utf-8")
    prompt=_v311_llm_prompt(context); (assets/PPT_LLM_PROMPT).write_text(prompt,encoding="utf-8")
    narrative=_v310_fallback_narrative(manifest,plan); llm_status={"status":"not_requested","experimental_file_upload":False,"compact_context_only":True}
    if use_direct_llm:
        try:
            parsed,llm_status=_call_direct_llm(prompt)
            if parsed:
                fallback=_v310_fallback_narrative(manifest,plan)
                _v310_merge_narrative(parsed, fallback)
                narrative=parsed; narrative["_llm_used"]=True
            else: narrative["_llm_used"]=False
        except Exception as exc:
            llm_status={"status":"failed","error":str(exc),"traceback":traceback.format_exc(limit=8),"experimental_file_upload":False,"compact_context_only":True}; narrative["_llm_used"]=False
    _v310_route_presenter_notes(narrative, plan)
    (assets/PPT_LLM_RESPONSE).write_text(json.dumps({"narrative":narrative,"transaction":{k:v for k,v in llm_status.items() if k!="raw_answer"},"plan":plan},ensure_ascii=False,indent=2),encoding="utf-8")
    if llm_status.get("raw_answer"): (assets/"ppt_llm_response_raw.txt").write_text(str(llm_status.get("raw_answer")),encoding="utf-8")
    prs,slides=_v303_build_presentation(root,manifest,narrative,assets,level,plan); ppt_path=root/PPTX_NAME; prs.save(ppt_path)
    main_count=len(plan.get("main") or []); appendix_count=(1+len(plan.get("appendix") or [])) if plan.get("appendix") else 0
    receipt={"schema":"faradaiq-powerpoint-summary-v311","version":VERSION,"created":datetime.now().isoformat(timespec="seconds"),"experiment_name":manifest.get("experiment_name"),"pptx_path":str(ppt_path),"pptx_name":ppt_path.name,"slide_count":len(slides),"main_slide_count":main_count,"appendix_slide_count":appendix_count,"detail_level":level,"technique_aware":True,"scientific_story_planner":True,"technique_specific_slide_recipes":True,"technique_recipe_registry_version":"v311-consolidated","takedata_relationship_engine":False,"suppressed_semantically_invalid_views":plan.get("suppressed_view_count",0),"all_available_scientific_comparison_views":False,"direct_llm_requested":bool(use_direct_llm),"direct_llm_used":bool(narrative.get("_llm_used")),"direct_llm_status":{k:v for k,v in llm_status.items() if k!="raw_answer"},"compact_context_path":str(assets/PPT_LLM_CONTEXT),"prompt_path":str(assets/PPT_LLM_PROMPT),"response_path":str(assets/PPT_LLM_RESPONSE),"raw_experimental_files_sent_to_llm":False,"front_slide_footer":FRONT_SLIDE_FOOTER,"presentation_theme":"scientific_consolidated_light_v311","speaker_notes_included":True,"speaker_notes_style":"scientific_paragraphs_concise_with_dynamic_technique_routing","dynamic_presenter_note_buckets":True,"max_scientific_plots_per_slide":2,"consolidated_results_per_technique":True,"professional_deck_mode":True,"slide_index":slides,"presentation_plan":plan}
    (root/PPT_MANIFEST_NAME).write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8"); return receipt

# Let legacy imports that call the old planner name still receive v304 recipes.
_v303_plan = _v304_plan
# ---- End FaradaIQ Studio v304 technique-specific PowerPoint recipe engine ----

# ---- FaradaIQ Studio v317 standalone public release identity ----
# Keep the historical presentation/schema implementation identifiers above,
# but ensure direct imports in a fresh process display the current public release.
VERSION = "v317"
APP_VERSION = "0.317.0"
FRONT_SLIDE_FOOTER = "Made by FaradaIQ Studio v317"
# ---- End FaradaIQ Studio v317 standalone public release identity ----

# ---- FaradaIQ Studio v317 call-time public identity guard ----
_V317_PREV_WRITE_POWERPOINT_SUMMARY = write_powerpoint_summary

def write_powerpoint_summary(*args, **kwargs):
    global VERSION, APP_VERSION, APP_TITLE, FRONT_SLIDE_FOOTER
    try:
        from . import release_identity as _release_identity
        VERSION = str(_release_identity.APP_VERSION)
        APP_VERSION = str(_release_identity.SEMVER)
        APP_TITLE = str(_release_identity.APP_TITLE)
        FRONT_SLIDE_FOOTER = f"Made by FaradaIQ eStudio {VERSION}"
    except Exception:
        VERSION = "v317"
        APP_VERSION = "0.317.0"
        APP_TITLE = "FaradaIQ Studio v317"
        FRONT_SLIDE_FOOTER = "Made by FaradaIQ Studio v317"
    return _V317_PREV_WRITE_POWERPOINT_SUMMARY(*args, **kwargs)
# ---- End FaradaIQ Studio v317 call-time public identity guard ----

# ---- FaradaIQ Studio v318 summary PowerPoint EIS/DRT completeness guard ----
# Potential-dependent Mott-Schottky measurements are also ordinary EIS spectra.
# V318 appends deterministic, standard EIS and DRT figure slides to the summary
# deck whenever those datasets are present, even when MT-specific analysis is
# already shown by the consolidated v317 slide planner.
VERSION = "v318"
APP_VERSION = "0.318.0"
APP_TITLE = "FaradaIQ Studio v318"
FRONT_SLIDE_FOOTER = "Made by FaradaIQ Studio v318"


def _v318_dataset_path(root: Path, ds: Mapping[str, Any]) -> Path | None:
    for k in ("physical_path", "processed_source_path", "original_scientific_path"):
        raw = str(ds.get(k) or "").strip()
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = root / raw
        if p.exists() and p.is_file():
            return p
    return None


def _v318_read_numeric_csv(path: Path) -> tuple[list[str], list[list[float]]]:
    import csv as _csv
    rows: list[list[str]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = _csv.reader(f)
        for row in reader:
            if row and any(str(x).strip() for x in row):
                rows.append(row)
    if not rows:
        return [], []
    def is_float(x: Any) -> bool:
        try:
            v = float(str(x).strip())
            return math.isfinite(v)
        except Exception:
            return False
    has_header = not all(is_float(x) for x in rows[0][: min(3, len(rows[0]))])
    headers = [str(x).strip() or f"column_{i+1}" for i, x in enumerate(rows[0])] if has_header else [f"column_{i+1}" for i in range(len(rows[0]))]
    data_rows = rows[1:] if has_header else rows
    out: list[list[float]] = []
    for row in data_rows:
        vals: list[float] = []
        ok = False
        for x in row:
            try:
                v = float(str(x).strip())
            except Exception:
                v = float("nan")
            if math.isfinite(v):
                ok = True
            vals.append(v)
        if ok:
            out.append(vals)
    return headers, out


def _v318_potential_label(ds: Mapping[str, Any], path: Path | None = None) -> str:
    v = _finite(ds.get("set_potential_V"))
    if v is None and path is not None:
        m = re.search(r"(?:^|_)([-+]?[0-9]+(?:\.[0-9]+)?)_V(?:_|$)", path.name)
        if m:
            try:
                v = float(m.group(1))
            except Exception:
                v = None
    return f"{v:+.3f} V" if v is not None else _clean_text(ds.get("potential_label") or "spectrum", 24)


def _v318_pick_spectra(root: Path, manifest: Mapping[str, Any], token: str, limit: int = 5) -> list[tuple[Mapping[str, Any], Path]]:
    candidates: list[tuple[float, Mapping[str, Any], Path]] = []
    tok = token.lower()
    for ds in manifest.get("datasets") or []:
        if not isinstance(ds, Mapping):
            continue
        if str(ds.get("technique_key") or "").upper() != "EIS":
            continue
        hay = " ".join(str(ds.get(k) or "") for k in ("display_name", "physical_path", "processed_source_path")).lower()
        if tok not in hay:
            continue
        p = _v318_dataset_path(root, ds)
        if not p:
            continue
        pot = _finite(ds.get("set_potential_V"))
        if pot is None:
            m = re.search(r"(?:^|_)([-+]?[0-9]+(?:\.[0-9]+)?)_V(?:_|$)", p.name)
            if m:
                try: pot = float(m.group(1))
                except Exception: pot = None
        candidates.append((float(pot) if pot is not None else float(len(candidates)), ds, p))
    candidates.sort(key=lambda x: x[0], reverse=True)
    if len(candidates) <= limit:
        return [(d, p) for _, d, p in candidates]
    idxs = sorted(set(round(i * (len(candidates) - 1) / max(1, limit - 1)) for i in range(limit)))
    return [(candidates[i][1], candidates[i][2]) for i in idxs]


def _v318_plot_standard_eis(root: Path, manifest: Mapping[str, Any], assets: Path, plot_kind: str) -> Path | None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return None
    token = "Nyquist" if plot_kind == "nyquist" else "Bode_mag" if plot_kind == "bode_mag" else "Bode_phase"
    chosen = _v318_pick_spectra(root, manifest, token, limit=5)
    if not chosen:
        return None
    out = assets / f"v318_standard_eis_{plot_kind}.png"
    plt.figure(figsize=(7.4, 4.25), dpi=170)
    plotted = 0
    for ds, p in chosen:
        _, rows = _v318_read_numeric_csv(p)
        xy = [(r[0], r[1]) for r in rows if len(r) >= 2 and math.isfinite(r[0]) and math.isfinite(r[1])]
        if len(xy) < 3:
            continue
        x = [a for a, _ in xy]
        y = [b for _, b in xy]
        label = _v318_potential_label(ds, p)
        plt.plot(x, y, linewidth=1.45, label=label)
        plotted += 1
    if not plotted:
        plt.close(); return None
    if plot_kind == "nyquist":
        plt.xlabel("Z' / ohm")
        plt.ylabel("-Z'' / ohm")
        plt.title("Representative Nyquist spectra")
    elif plot_kind == "bode_mag":
        plt.xscale("log"); plt.yscale("log")
        plt.xlabel("Frequency / Hz")
        plt.ylabel("|Z| / ohm")
        plt.title("Representative Bode magnitude")
    else:
        plt.xscale("log")
        plt.xlabel("Frequency / Hz")
        plt.ylabel("Phase / degree")
        plt.title("Representative Bode phase")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=7, loc="best")
    plt.tight_layout()
    plt.savefig(out)
    plt.close()
    return out if out.exists() else None


def _v318_pick_drt_distribution_datasets(root: Path, manifest: Mapping[str, Any], limit: int = 5) -> list[tuple[Mapping[str, Any], Path]]:
    candidates: list[tuple[float, Mapping[str, Any], Path]] = []
    for ds in manifest.get("datasets") or []:
        if not isinstance(ds, Mapping):
            continue
        key = str(ds.get("technique_key") or "").upper()
        hay = " ".join(str(ds.get(k) or "") for k in ("display_name", "physical_path", "processed_source_path")).lower()
        if key != "DRT" and "simple_drt" not in hay and "drt distribution" not in hay:
            continue
        p = _v318_dataset_path(root, ds)
        if not p:
            continue
        pot = _finite(ds.get("set_potential_V"))
        if pot is None:
            m = re.search(r"(?:^|_)([-+]?[0-9]+(?:\.[0-9]+)?)_V(?:_|$)", p.name)
            if m:
                try: pot = float(m.group(1))
                except Exception: pot = None
        candidates.append((float(pot) if pot is not None else float(len(candidates)), ds, p))
    candidates.sort(key=lambda x: x[0], reverse=True)
    if len(candidates) <= limit:
        return [(d, p) for _, d, p in candidates]
    idxs = sorted(set(round(i * (len(candidates) - 1) / max(1, limit - 1)) for i in range(limit)))
    return [(candidates[i][1], candidates[i][2]) for i in idxs]


def _v318_column_index(headers: Sequence[str], rows: Sequence[Sequence[float]], tokens: Sequence[str], fallback: int) -> int:
    low = [str(h or "").lower() for h in headers]
    for tok in tokens:
        for i, h in enumerate(low):
            if tok in h:
                return i
    return fallback


def _v318_plot_drt_overlay(root: Path, manifest: Mapping[str, Any], assets: Path) -> Path | None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return None
    chosen = _v318_pick_drt_distribution_datasets(root, manifest, limit=5)
    out = assets / "v318_standard_drt_overlay.png"
    if chosen:
        plt.figure(figsize=(7.4, 4.25), dpi=170)
        plotted = 0
        for ds, p in chosen:
            headers, rows = _v318_read_numeric_csv(p)
            if not rows:
                continue
            xi = _v318_column_index(headers, rows, ["tau", "time"], 0)
            yi = _v318_column_index(headers, rows, ["gamma", "resistance", "distribution"], 1 if len(rows[0]) > 1 else 0)
            pts = [(r[xi], r[yi]) for r in rows if len(r) > max(xi, yi) and math.isfinite(r[xi]) and math.isfinite(r[yi]) and r[xi] > 0]
            if len(pts) < 3:
                continue
            plt.plot([x for x, _ in pts], [y for _, y in pts], linewidth=1.35, label=_v318_potential_label(ds, p))
            plotted += 1
        if plotted:
            plt.xscale("log")
            plt.xlabel("Relaxation time τ / s")
            plt.ylabel("γ(τ) / ohm")
            plt.title("Representative DRT distributions")
            plt.grid(True, alpha=0.25)
            plt.legend(fontsize=7, loc="best")
            plt.tight_layout(); plt.savefig(out); plt.close()
            return out if out.exists() else None
        plt.close()
    # Fallback to v317 peak tracks when full DRT distributions are not in the manifest.
    tracks=[]
    for ds in manifest.get("datasets") or []:
        if isinstance(ds, Mapping) and str(ds.get("technique_key") or "").upper()=="DRT_PEAK_TRACKS":
            p=_v318_dataset_path(root, ds)
            if p: tracks.append((ds,p))
    if not tracks:
        return None
    plt.figure(figsize=(7.4, 4.25), dpi=170)
    plotted=0
    for ds,p in tracks[:3]:
        headers, rows = _v318_read_numeric_csv(p)
        if not rows: continue
        xi = _v318_column_index(headers, rows, ["potential"], 0)
        yi = _v318_column_index(headers, rows, ["tau_s", "tau"], 2 if len(rows[0])>2 else 1)
        pts=[(r[xi], r[yi]) for r in rows if len(r)>max(xi,yi) and math.isfinite(r[xi]) and math.isfinite(r[yi]) and r[yi]>0]
        if len(pts)<3: continue
        plt.plot([x for x,_ in pts],[y for _,y in pts],linewidth=1.45,label=_clean_text(ds.get('display_name') or 'DRT peak track',24))
        plotted+=1
    if not plotted:
        plt.close(); return None
    plt.yscale("log")
    plt.xlabel("Potential / V")
    plt.ylabel("Peak τ / s")
    plt.title("DRT peak relaxation time versus potential")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=7, loc="best")
    plt.tight_layout(); plt.savefig(out); plt.close()
    return out if out.exists() else None


def _v318_manifest_has_eis(manifest: Mapping[str, Any]) -> bool:
    return any(isinstance(ds, Mapping) and str(ds.get("technique_key") or "").upper()=="EIS" for ds in manifest.get("datasets") or [])


def _v318_manifest_has_mt(manifest: Mapping[str, Any]) -> bool:
    return any(isinstance(ds, Mapping) and str(ds.get("technique_key") or "").upper() in {"MOTT_SCHOTTKY","MOTT_SCHOTTKY_DIAGNOSTIC","EIS_CAPACITANCE"} for ds in manifest.get("datasets") or [])


def _v318_add_footer_text(slide: Any, idx: int, experiment_name: str) -> None:
    try:
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN
        tb = slide.shapes.add_textbox(Inches(0.55), Inches(7.12), Inches(11.6), Inches(0.25))
        p = tb.text_frame.paragraphs[0]
        p.text = f"FaradaIQ Studio v318 · {experiment_name}"
        p.font.name = "Aptos"; p.font.size = Pt(9.5); p.font.color.rgb = RGBColor(82,94,107)
        tb2 = slide.shapes.add_textbox(Inches(12.2), Inches(7.12), Inches(0.55), Inches(0.25))
        p2 = tb2.text_frame.paragraphs[0]
        p2.text = str(idx); p2.alignment = PP_ALIGN.RIGHT
        p2.font.name = "Aptos"; p2.font.size = Pt(9.5); p2.font.color.rgb = RGBColor(82,94,107)
    except Exception:
        pass


def _v318_append_standard_eis_drt_slides(root: Path, manifest: Mapping[str, Any], receipt: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    # v319 supersedes these representative-only slides with consistent-potential
    # EIS/DRT overlays plus a DRT potential map. Keep v318 behavior only when
    # replaying the v318 public release.
    if str(VERSION).lower() != "v318":
        receipt["v318_forced_standard_eis_drt_slides"] = False
        receipt["v318_forced_standard_eis_drt_superseded_by"] = str(VERSION)
        return receipt
    # Only apply to potential-dependent/Mott-Schottky EIS packages. Generic EIS decks retain legacy behavior.
    if not (_v318_manifest_has_eis(manifest) and _v318_manifest_has_mt(manifest)):
        receipt["v318_forced_standard_eis_drt_slides"] = False
        return receipt
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN
    except Exception:
        receipt["v318_forced_standard_eis_drt_slides"] = False
        receipt["v318_forced_standard_eis_drt_error"] = "python-pptx unavailable"
        return receipt
    assets = root / PPT_ASSET_DIR
    assets.mkdir(parents=True, exist_ok=True)
    ppt_path = root / PPTX_NAME
    if not ppt_path.exists():
        return receipt
    image_paths = {
        "nyquist": _v318_plot_standard_eis(root, manifest, assets, "nyquist"),
        "bode_mag": _v318_plot_standard_eis(root, manifest, assets, "bode_mag"),
        "bode_phase": _v318_plot_standard_eis(root, manifest, assets, "bode_phase"),
        "drt": _v318_plot_drt_overlay(root, manifest, assets),
    }
    if not any(image_paths.values()):
        receipt["v318_forced_standard_eis_drt_slides"] = False
        receipt["v318_forced_standard_eis_drt_error"] = "no standard EIS/DRT source files available"
        return receipt
    prs = Presentation(str(ppt_path))
    blank = prs.slide_layouts[6]
    WHITE = RGBColor(255,255,255); TEXT = RGBColor(24,36,48); MUTED = RGBColor(82,94,107); BLUE = RGBColor(20,67,104)
    def add_title(slide, title, sub):
        slide.background.fill.solid(); slide.background.fill.fore_color.rgb = WHITE
        tb=slide.shapes.add_textbox(Inches(0.55),Inches(0.28),Inches(12.2),Inches(0.54)); p=tb.text_frame.paragraphs[0]; p.text=title; p.font.name="Aptos"; p.font.size=Pt(28); p.font.bold=True; p.font.color.rgb=TEXT
        tb=slide.shapes.add_textbox(Inches(0.58),Inches(0.88),Inches(12.0),Inches(0.34)); p=tb.text_frame.paragraphs[0]; p.text=sub; p.font.name="Aptos"; p.font.size=Pt(13); p.font.color.rgb=MUTED
        ln=slide.shapes.add_shape(1, Inches(0.55), Inches(1.27), Inches(12.2), Inches(0.025)); ln.fill.solid(); ln.fill.fore_color.rgb=BLUE; ln.line.fill.background()
    def add_note(slide, text):
        tb=slide.shapes.add_textbox(Inches(0.78),Inches(6.55),Inches(11.8),Inches(0.38)); p=tb.text_frame.paragraphs[0]; p.text=text; p.font.name="Aptos"; p.font.size=Pt(12); p.font.color.rgb=MUTED; p.alignment=PP_ALIGN.CENTER
    slide_records = list(receipt.get("slide_index") or [])
    experiment = _clean_text(manifest.get("experiment_name") or "experiment", 120)
    added=[]
    if image_paths.get("nyquist") or image_paths.get("bode_mag"):
        sl=prs.slides.add_slide(blank); add_title(sl,"Usual EIS figures: Nyquist and Bode magnitude","Standard impedance views are shown in addition to Mott-Schottky/MT-specific analysis.")
        if image_paths.get("nyquist"): sl.shapes.add_picture(str(image_paths["nyquist"]), Inches(0.70), Inches(1.52), width=Inches(5.82), height=Inches(4.78))
        if image_paths.get("bode_mag"): sl.shapes.add_picture(str(image_paths["bode_mag"]), Inches(6.82), Inches(1.52), width=Inches(5.82), height=Inches(4.78))
        add_note(sl,"These are representative spectra selected across the potential range; use the Browser for all 85 bias-resolved spectra.")
        _v318_add_footer_text(sl,len(prs.slides),experiment)
        added.append({"index":len(prs.slides),"title":"Usual EIS figures: Nyquist and Bode magnitude","subtitle":"Forced v318 standard EIS figure slide"})
    if image_paths.get("bode_phase"):
        sl=prs.slides.add_slide(blank); add_title(sl,"Usual EIS figures: Bode phase","Phase dispersion and inductive/capacitive transitions remain visible alongside MT-specific capacitance plots.")
        sl.shapes.add_picture(str(image_paths["bode_phase"]), Inches(1.25), Inches(1.52), width=Inches(10.85), height=Inches(4.9))
        add_note(sl,"Do not replace phase-frequency behavior with a single capacitance or Mott-Schottky transform.")
        _v318_add_footer_text(sl,len(prs.slides),experiment)
        added.append({"index":len(prs.slides),"title":"Usual EIS figures: Bode phase","subtitle":"Forced v318 standard EIS figure slide"})
    if image_paths.get("drt"):
        sl=prs.slides.add_slide(blank); add_title(sl,"DRT figures: relaxation behavior versus potential","DRT evidence is included as its own impedance-analysis view, not hidden inside MT-specific analysis.")
        sl.shapes.add_picture(str(image_paths["drt"]), Inches(1.25), Inches(1.52), width=Inches(10.85), height=Inches(4.9))
        add_note(sl,"DRT peak movement is descriptive unless supported by circuit identifiability and independent electrochemical evidence.")
        _v318_add_footer_text(sl,len(prs.slides),experiment)
        added.append({"index":len(prs.slides),"title":"DRT figures: relaxation behavior versus potential","subtitle":"Forced v318 DRT figure slide"})
    if added:
        prs.save(str(ppt_path))
        slide_records.extend(added)
        receipt["slide_count"] = len(prs.slides)
        receipt["slide_index"] = slide_records
        receipt["v318_forced_standard_eis_drt_slides"] = True
        receipt["v318_forced_standard_eis_drt_slide_count"] = len(added)
        receipt["v318_standard_eis_nyquist_figure"] = str(image_paths.get("nyquist") or "")
        receipt["v318_standard_eis_bode_magnitude_figure"] = str(image_paths.get("bode_mag") or "")
        receipt["v318_standard_eis_bode_phase_figure"] = str(image_paths.get("bode_phase") or "")
        receipt["v318_standard_drt_figure"] = str(image_paths.get("drt") or "")
    return receipt


_V318_PREV_WRITE_POWERPOINT_SUMMARY = write_powerpoint_summary

def write_powerpoint_summary(*args, **kwargs):
    global VERSION, APP_VERSION, APP_TITLE, FRONT_SLIDE_FOOTER
    try:
        from . import release_identity as _release_identity
        VERSION = str(_release_identity.APP_VERSION)
        APP_VERSION = str(_release_identity.SEMVER)
        APP_TITLE = str(_release_identity.APP_TITLE)
        FRONT_SLIDE_FOOTER = f"Made by FaradaIQ eStudio {VERSION}"
    except Exception:
        VERSION = "v318"; APP_VERSION = "0.318.0"; APP_TITLE = "FaradaIQ Studio v318"; FRONT_SLIDE_FOOTER = "Made by FaradaIQ Studio v318"
    receipt = _V318_PREV_WRITE_POWERPOINT_SUMMARY(*args, **kwargs)
    try:
        root = Path(args[0] if args else kwargs.get("experiment_root")).expanduser().resolve()
        manifest = kwargs.get("manifest")
        if manifest is None:
            mf = root / "faradaiq_portable_browser_manifest.json"
            if mf.exists():
                manifest = json.loads(mf.read_text(encoding="utf-8"))
        if isinstance(manifest, Mapping):
            receipt = dict(receipt)
            receipt = _v318_append_standard_eis_drt_slides(root, manifest, receipt)
            (root / PPT_MANIFEST_NAME).write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        try:
            receipt = dict(receipt)
            receipt["v318_forced_standard_eis_drt_slides"] = False
            receipt["v318_forced_standard_eis_drt_error"] = str(exc)
            root = Path(args[0] if args else kwargs.get("experiment_root")).expanduser().resolve()
            (root / PPT_MANIFEST_NAME).write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return receipt
# ---- End FaradaIQ Studio v318 summary PowerPoint EIS/DRT completeness guard ----

# ---- FaradaIQ Studio v320 potential-resolved EIS/DRT overlay PowerPoint release ----
VERSION = "v320"
APP_VERSION = "0.320.0"
APP_TITLE = "FaradaIQ Studio v320"
FRONT_SLIDE_FOOTER = "Made by FaradaIQ Studio v320"


def _v319_potential(ds: Mapping[str, Any], path: Path | None = None) -> float | None:
    v = _finite(ds.get("set_potential_V"))
    if v is not None:
        return float(v)
    if path is not None:
        m = re.search(r"(?:^|_)([-+]?[0-9]+(?:\.[0-9]+)?)_V(?:_|$)", path.name)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                pass
    text = " ".join(str(ds.get(k) or "") for k in ("display_name", "potential_label"))
    m = re.search(r"([-+]?[0-9]+(?:\.[0-9]+)?)\s*V\b", text, re.I)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            pass
    return None


def _v319_selected_potentials(root: Path, manifest: Mapping[str, Any], limit: int = 9) -> list[float]:
    values: list[float] = []
    for ds in manifest.get("datasets") or []:
        if not isinstance(ds, Mapping) or str(ds.get("technique_key") or "").upper() != "EIS":
            continue
        hay = " ".join(str(ds.get(k) or "") for k in ("display_name", "physical_path", "processed_source_path")).lower()
        if "nyquist" not in hay:
            continue
        p = _v318_dataset_path(root, ds)
        v = _v319_potential(ds, p)
        if v is not None and math.isfinite(v):
            values.append(float(v))
    uniq = sorted({round(v, 8) for v in values}, reverse=True)
    if len(uniq) <= max(1, limit):
        return [float(v) for v in uniq]
    idxs = sorted(set(round(i * (len(uniq) - 1) / max(1, limit - 1)) for i in range(limit)))
    return [float(uniq[i]) for i in idxs]


def _v319_curve_datasets(root: Path, manifest: Mapping[str, Any], *, technique: str, token: str = "") -> list[tuple[float, Mapping[str, Any], Path]]:
    out: list[tuple[float, Mapping[str, Any], Path]] = []
    tok = token.lower().strip()
    for ds in manifest.get("datasets") or []:
        if not isinstance(ds, Mapping):
            continue
        key = str(ds.get("technique_key") or "").upper()
        hay = " ".join(str(ds.get(k) or "") for k in ("display_name", "physical_path", "processed_source_path")).lower()
        if technique == "DRT":
            if key != "DRT" and "simple_drt" not in hay and "drt distribution" not in hay:
                continue
        elif key != technique.upper():
            continue
        if tok and tok not in hay:
            continue
        p = _v318_dataset_path(root, ds)
        if not p:
            continue
        pot = _v319_potential(ds, p)
        if pot is None or not math.isfinite(pot):
            continue
        out.append((float(pot), ds, p))
    # Some older Browser manifests omit the completed pyDRTtools distribution
    # tables even though the production result tree contains them.  Discover
    # completed outputs directly as a fallback, never raw DRT_input staging CSVs.
    if technique == "DRT" and not out:
        seen: set[float] = set()
        patterns = ("*_simple_drt_data.csv", "*_simple_DRT.csv", "*_bayesian_DRT.csv", "*_bht_DRT.csv")
        for pattern in patterns:
            for p in root.rglob(pattern):
                low = p.as_posix().lower()
                if "/.browser_cache_from_h5/" in low:
                    continue
                faux: dict[str, Any] = {"technique_key":"DRT", "display_name":p.stem, "physical_path":str(p)}
                pot = _v319_potential(faux, p)
                if pot is None or not math.isfinite(pot):
                    continue
                key = round(float(pot), 6)
                if key in seen:
                    continue
                seen.add(key)
                out.append((float(pot), faux, p))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def _v319_selected_match(pot: float, selected: Sequence[float], tol: float = 5e-4) -> int | None:
    if not selected:
        return None
    diffs = [abs(float(pot) - float(v)) for v in selected]
    idx = min(range(len(diffs)), key=diffs.__getitem__)
    return idx if diffs[idx] <= tol else None


def _v319_plot_nyquist_overlay(root: Path, manifest: Mapping[str, Any], assets: Path, selected: Sequence[float]) -> Path | None:
    try:
        import numpy as np
        import matplotlib.pyplot as plt
    except Exception:
        return None
    curves = _v319_curve_datasets(root, manifest, technique="EIS", token="nyquist")
    if not curves:
        return None
    out = assets / "v319_nyquist_overlay.png"
    fig, ax = plt.subplots(figsize=(9.4, 5.4), dpi=180)
    for pot, ds, p in curves:
        _, rows = _v318_read_numeric_csv(p)
        pts = [(r[0], r[1]) for r in rows if len(r) >= 2 and math.isfinite(r[0]) and math.isfinite(r[1])]
        if len(pts) >= 3:
            ax.plot([x for x, _ in pts], [y for _, y in pts], linewidth=0.65, alpha=0.16, color="0.52", zorder=1)
    cmap = plt.get_cmap("viridis")
    colors = cmap(np.linspace(0.08, 0.92, max(1, len(selected))))
    plotted = 0
    for pot, ds, p in curves:
        si = _v319_selected_match(pot, selected)
        if si is None:
            continue
        _, rows = _v318_read_numeric_csv(p)
        pts = [(r[0], r[1]) for r in rows if len(r) >= 2 and math.isfinite(r[0]) and math.isfinite(r[1])]
        if len(pts) < 3:
            continue
        ax.plot([x for x, _ in pts], [y for _, y in pts], linewidth=2.0, color=colors[si], label=f"{pot:+.3f} V", zorder=3)
        plotted += 1
    if not plotted:
        plt.close(fig); return None
    ax.set_xlabel("Z' / Ω")
    ax.set_ylabel("−Z'' / Ω")
    ax.set_title(f"Nyquist overlay across potential · {len(curves)} spectra")
    ax.grid(True, alpha=0.22)
    ax.legend(fontsize=8, ncol=3, loc="best", frameon=False)
    ax.text(0.01, 0.02, "Gray: all spectra   Colored: representative potentials", transform=ax.transAxes, fontsize=8.5, color="0.35")
    _v307_disable_mathtext_for_figure(fig)
    fig.tight_layout(); _v307_safe_savefig(fig, out, bbox_inches="tight"); plt.close(fig)
    return out if out.exists() else None


def _v319_plot_bode_overlay(root: Path, manifest: Mapping[str, Any], assets: Path, selected: Sequence[float]) -> Path | None:
    try:
        import numpy as np
        import matplotlib.pyplot as plt
    except Exception:
        return None
    mag = _v319_curve_datasets(root, manifest, technique="EIS", token="bode_mag")
    phase = _v319_curve_datasets(root, manifest, technique="EIS", token="bode_phase")
    if not mag and not phase:
        return None
    out = assets / "v319_bode_overlay.png"
    fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.9), dpi=180)
    cmap = plt.get_cmap("viridis")
    colors = cmap(np.linspace(0.08, 0.92, max(1, len(selected))))
    for ax, curves, title, ylabel, logy in (
        (axes[0], mag, "Bode magnitude overlay", "|Z| / Ω", True),
        (axes[1], phase, "Bode phase overlay", "Phase / degree", False),
    ):
        for pot, ds, p in curves:
            _, rows = _v318_read_numeric_csv(p)
            pts = [(r[0], r[1]) for r in rows if len(r) >= 2 and math.isfinite(r[0]) and math.isfinite(r[1]) and r[0] > 0]
            if len(pts) >= 3:
                ax.plot([x for x, _ in pts], [y for _, y in pts], linewidth=0.6, alpha=0.14, color="0.55", zorder=1)
        for pot, ds, p in curves:
            si = _v319_selected_match(pot, selected)
            if si is None:
                continue
            _, rows = _v318_read_numeric_csv(p)
            pts = [(r[0], r[1]) for r in rows if len(r) >= 2 and math.isfinite(r[0]) and math.isfinite(r[1]) and r[0] > 0]
            if len(pts) >= 3:
                ax.plot([x for x, _ in pts], [y for _, y in pts], linewidth=1.85, color=colors[si], label=f"{pot:+.3f} V", zorder=3)
        ax.set_xscale("log")
        if logy:
            ax.set_yscale("log")
        ax.set_xlabel("Frequency / Hz")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(True, alpha=0.22)
    if selected:
        axes[1].legend(fontsize=7.5, ncol=2, loc="best", frameon=False)
    fig.suptitle(f"Potential-resolved Bode overlays · {max(len(mag), len(phase))} spectra", fontsize=13.5)
    _v307_disable_mathtext_for_figure(fig)
    fig.tight_layout(rect=(0, 0, 1, 0.95)); _v307_safe_savefig(fig, out, bbox_inches="tight"); plt.close(fig)
    return out if out.exists() else None


def _v319_plot_drt_overlay(root: Path, manifest: Mapping[str, Any], assets: Path, selected: Sequence[float]) -> Path | None:
    try:
        import numpy as np
        import matplotlib.pyplot as plt
    except Exception:
        return None
    curves = _v319_curve_datasets(root, manifest, technique="DRT")
    if not curves:
        return None
    out = assets / "v319_drt_overlay.png"
    fig, ax = plt.subplots(figsize=(9.4, 5.4), dpi=180)
    for pot, ds, p in curves:
        headers, rows = _v318_read_numeric_csv(p)
        if not rows:
            continue
        xi = _v318_column_index(headers, rows, ["tau", "time"], 0)
        yi = _v318_column_index(headers, rows, ["gamma", "distribution", "resistance"], 1 if len(rows[0]) > 1 else 0)
        pts = [(r[xi], r[yi]) for r in rows if len(r) > max(xi, yi) and math.isfinite(r[xi]) and math.isfinite(r[yi]) and r[xi] > 0]
        if len(pts) >= 3:
            ax.plot([x for x, _ in pts], [y for _, y in pts], linewidth=0.65, alpha=0.14, color="0.52", zorder=1)
    cmap = plt.get_cmap("viridis")
    colors = cmap(np.linspace(0.08, 0.92, max(1, len(selected))))
    plotted = 0
    for pot, ds, p in curves:
        si = _v319_selected_match(pot, selected)
        if si is None:
            continue
        headers, rows = _v318_read_numeric_csv(p)
        if not rows:
            continue
        xi = _v318_column_index(headers, rows, ["tau", "time"], 0)
        yi = _v318_column_index(headers, rows, ["gamma", "distribution", "resistance"], 1 if len(rows[0]) > 1 else 0)
        pts = [(r[xi], r[yi]) for r in rows if len(r) > max(xi, yi) and math.isfinite(r[xi]) and math.isfinite(r[yi]) and r[xi] > 0]
        if len(pts) < 3:
            continue
        ax.plot([x for x, _ in pts], [y for _, y in pts], linewidth=2.0, color=colors[si], label=f"{pot:+.3f} V", zorder=3)
        plotted += 1
    if not plotted:
        plt.close(fig); return None
    ax.set_xscale("log")
    ax.set_xlabel("Relaxation time τ / s")
    ax.set_ylabel("γ(τ) / Ω")
    ax.set_title(f"DRT overlay across potential · {len(curves)} solved spectra")
    ax.grid(True, alpha=0.22)
    ax.legend(fontsize=8, ncol=3, loc="best", frameon=False)
    ax.text(0.01, 0.02, "Gray: all DRTs   Colored: same representative potentials as EIS overlays", transform=ax.transAxes, fontsize=8.5, color="0.35")
    _v307_disable_mathtext_for_figure(fig)
    fig.tight_layout(); _v307_safe_savefig(fig, out, bbox_inches="tight"); plt.close(fig)
    return out if out.exists() else None


def _v319_plot_drt_map(root: Path, manifest: Mapping[str, Any], assets: Path) -> Path | None:
    try:
        import numpy as np
        import matplotlib.pyplot as plt
    except Exception:
        return None
    curves = _v319_curve_datasets(root, manifest, technique="DRT")
    parsed: list[tuple[float, Any, Any]] = []
    for pot, ds, p in curves:
        headers, rows = _v318_read_numeric_csv(p)
        if not rows:
            continue
        xi = _v318_column_index(headers, rows, ["tau", "time"], 0)
        yi = _v318_column_index(headers, rows, ["gamma", "distribution", "resistance"], 1 if len(rows[0]) > 1 else 0)
        pts = sorted((r[xi], r[yi]) for r in rows if len(r) > max(xi, yi) and math.isfinite(r[xi]) and math.isfinite(r[yi]) and r[xi] > 0)
        if len(pts) >= 4:
            parsed.append((pot, np.asarray([x for x, _ in pts], dtype=float), np.asarray([y for _, y in pts], dtype=float)))
    if len(parsed) < 3:
        return None
    parsed.sort(key=lambda x: x[0])
    lo = min(float(np.log10(t).min()) for _, t, _ in parsed)
    hi = max(float(np.log10(t).max()) for _, t, _ in parsed)
    log_grid = np.linspace(lo, hi, 180)
    tau_grid = np.power(10.0, log_grid)
    matrix=[]; pots=[]; peak_tau=[]; peak_gamma=[]
    for pot, tau, gamma in parsed:
        order=np.argsort(tau); tau=tau[order]; gamma=gamma[order]
        vals=np.interp(log_grid, np.log10(tau), gamma)
        matrix.append(vals); pots.append(pot)
        pi=int(np.argmax(np.abs(vals)))
        peak_tau.append(tau_grid[pi]); peak_gamma.append(vals[pi])
    arr=np.asarray(matrix, dtype=float)
    out = assets / "v319_drt_map.png"
    fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.9), dpi=180, gridspec_kw={"width_ratios":[1.55,1]})
    finite=arr[np.isfinite(arr)]
    if finite.size:
        vmax=float(np.nanpercentile(np.abs(finite), 98))
        vmax=max(vmax,1e-12); vmin=-vmax if float(np.nanmin(finite)) < 0 else 0.0
    else:
        vmin,vmax=0.0,1.0
    mesh=axes[0].pcolormesh(tau_grid, np.asarray(pots), arr, shading="auto", cmap="viridis", vmin=vmin, vmax=vmax)
    axes[0].set_xscale("log"); axes[0].set_xlabel("Relaxation time τ / s"); axes[0].set_ylabel("Potential / V"); axes[0].set_title("DRT intensity map")
    cb=fig.colorbar(mesh, ax=axes[0], pad=0.02); cb.set_label("γ(τ) / Ω")
    sc=axes[1].scatter(pots, peak_tau, c=np.abs(peak_gamma), cmap="viridis", s=42, edgecolors="none")
    axes[1].plot(pots, peak_tau, linewidth=0.8, alpha=0.45, color="0.45")
    axes[1].set_yscale("log"); axes[1].set_xlabel("Potential / V"); axes[1].set_ylabel("Dominant τ / s"); axes[1].set_title("Dominant relaxation trend")
    axes[1].grid(True, alpha=0.22); cb2=fig.colorbar(sc, ax=axes[1], pad=0.02); cb2.set_label("|peak γ| / Ω")
    fig.suptitle(f"DRT evolution across potential · {len(parsed)} spectra", fontsize=13.5)
    _v307_disable_mathtext_for_figure(fig)
    fig.tight_layout(rect=(0,0,1,0.95)); _v307_safe_savefig(fig, out, bbox_inches="tight"); plt.close(fig)
    return out if out.exists() else None


def _v319_add_footer_text(slide: Any, idx: int, experiment_name: str) -> None:
    try:
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN
        tb = slide.shapes.add_textbox(Inches(0.55), Inches(7.12), Inches(11.6), Inches(0.25))
        p = tb.text_frame.paragraphs[0]; p.text = f"FaradaIQ eStudio {VERSION} · {experiment_name}"
        p.font.name="Aptos"; p.font.size=Pt(9.5); p.font.color.rgb=RGBColor(82,94,107)
        tb2=slide.shapes.add_textbox(Inches(12.2), Inches(7.12), Inches(0.55), Inches(0.25))
        p2=tb2.text_frame.paragraphs[0]; p2.text=str(idx); p2.alignment=PP_ALIGN.RIGHT
        p2.font.name="Aptos"; p2.font.size=Pt(9.5); p2.font.color.rgb=RGBColor(82,94,107)
    except Exception:
        pass


def _v319_compact_coverage_slide(prs: Any) -> bool:
    """Compact the technique-coverage table so long analysis names stay on-canvas."""
    try:
        from pptx.util import Inches, Pt
    except Exception:
        return False
    changed=False
    for slide in prs.slides:
        texts=[]
        for sh in slide.shapes:
            try:
                if hasattr(sh, "text") and sh.text:
                    texts.append(str(sh.text))
            except Exception:
                pass
        if not any("Technique coverage and data readiness" in t for t in texts):
            continue
        for sh in slide.shapes:
            if not getattr(sh, "has_table", False):
                continue
            table=sh.table
            if len(table.columns) >= 5:
                widths=[4.0,3.0,1.65,1.55,1.70]
                for col,w in zip(table.columns,widths):
                    col.width=Inches(w)
                sh.width=Inches(sum(widths))
            nrows=len(table.rows)
            if nrows:
                header_h=0.46
                data_h=min(0.59, max(0.50, (5.18-header_h)/max(1,nrows-1)))
                table.rows[0].height=Inches(header_h)
                for row in list(table.rows)[1:]:
                    row.height=Inches(data_h)
                sh.height=Inches(header_h + data_h*max(0,nrows-1))
            for ri,row in enumerate(table.rows):
                for cell in row.cells:
                    try:
                        cell.margin_top=Inches(0.02); cell.margin_bottom=Inches(0.02)
                        cell.margin_left=Inches(0.08); cell.margin_right=Inches(0.05)
                    except Exception:
                        pass
                    for para in cell.text_frame.paragraphs:
                        if para.runs:
                            for run in para.runs:
                                run.font.size=Pt(10.5 if ri else 11.0)
                        else:
                            para.font.size=Pt(10.5 if ri else 11.0)
            changed=True
    return changed


def _v319_append_overlay_eis_drt_slides(root: Path, manifest: Mapping[str, Any], receipt: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    if not (_v318_manifest_has_eis(manifest) and _v318_manifest_has_mt(manifest)):
        receipt["v319_overlay_eis_drt_slides"] = False
        return receipt
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN
    except Exception:
        receipt["v319_overlay_eis_drt_slides"] = False
        receipt["v319_overlay_eis_drt_error"] = "python-pptx unavailable"
        return receipt
    ppt_path=root/PPTX_NAME
    if not ppt_path.exists():
        return receipt
    assets=root/PPT_ASSET_DIR; assets.mkdir(parents=True, exist_ok=True)
    selected=_v319_selected_potentials(root, manifest, limit=9)
    figs={
        "nyquist":_v319_plot_nyquist_overlay(root, manifest, assets, selected),
        "bode":_v319_plot_bode_overlay(root, manifest, assets, selected),
        "drt":_v319_plot_drt_overlay(root, manifest, assets, selected),
        "drt_map":_v319_plot_drt_map(root, manifest, assets),
    }
    if not any(figs.values()):
        receipt["v319_overlay_eis_drt_slides"] = False
        receipt["v319_overlay_eis_drt_error"] = "no overlay-compatible EIS/DRT source files available"
        return receipt
    prs=Presentation(str(ppt_path)); _v319_compact_coverage_slide(prs); blank=prs.slide_layouts[6]
    WHITE=RGBColor(255,255,255); TEXT=RGBColor(24,36,48); MUTED=RGBColor(82,94,107); BLUE=RGBColor(20,67,104)
    experiment=_clean_text(manifest.get("experiment_name") or "experiment",120)
    def add_title(slide,title,sub):
        slide.background.fill.solid(); slide.background.fill.fore_color.rgb=WHITE
        tb=slide.shapes.add_textbox(Inches(0.55),Inches(0.28),Inches(12.2),Inches(0.54)); p=tb.text_frame.paragraphs[0]; p.text=title; p.font.name="Aptos"; p.font.size=Pt(27); p.font.bold=True; p.font.color.rgb=TEXT
        tb=slide.shapes.add_textbox(Inches(0.58),Inches(0.86),Inches(12.0),Inches(0.36)); p=tb.text_frame.paragraphs[0]; p.text=sub; p.font.name="Aptos"; p.font.size=Pt(12.5); p.font.color.rgb=MUTED
        ln=slide.shapes.add_shape(1,Inches(0.55),Inches(1.27),Inches(12.2),Inches(0.025)); ln.fill.solid(); ln.fill.fore_color.rgb=BLUE; ln.line.fill.background()
    def add_note(slide,text):
        tb=slide.shapes.add_textbox(Inches(0.75),Inches(6.55),Inches(11.85),Inches(0.38)); p=tb.text_frame.paragraphs[0]; p.text=text; p.font.name="Aptos"; p.font.size=Pt(11.5); p.font.color.rgb=MUTED; p.alignment=PP_ALIGN.CENTER
    records=list(receipt.get("slide_index") or []); added=[]
    specs=[
        ("nyquist","Potential-resolved EIS overlay: Nyquist","All measured bias spectra are retained in gray; representative potentials are highlighted consistently across EIS and DRT.","Overlay preserves the full potential evolution while keeping representative curves readable."),
        ("bode","Potential-resolved EIS overlays: Bode magnitude and phase","Magnitude and phase use the same highlighted potentials as the Nyquist and DRT overlays.","Read frequency-dependent changes together with Nyquist, circuit-fit validity, and MT-specific capacitance analysis."),
        ("drt","Potential-resolved DRT overlay","All solved DRT distributions are shown in context, with the same representative bias values highlighted.","Peak motion and amplitude are descriptive unless supported by circuit identifiability and independent electrochemical evidence."),
        ("drt_map","DRT potential map and dominant relaxation trend","The full DRT stack is summarized as a potential–relaxation-time map plus the dominant relaxation trajectory.","Use the map to identify transitions and relaxation-family motion; do not assign mechanism from DRT alone."),
    ]
    for key,title,sub,note in specs:
        path=figs.get(key)
        if not path: continue
        sl=prs.slides.add_slide(blank); add_title(sl,title,sub)
        sl.shapes.add_picture(str(path), Inches(0.78), Inches(1.48), width=Inches(11.78), height=Inches(4.85))
        add_note(sl,note); _v319_add_footer_text(sl,len(prs.slides),experiment)
        rec={"index":len(prs.slides),"title":title,"subtitle":f"{VERSION} potential-resolved overlay figure slide"}; records.append(rec); added.append(rec)
    if added:
        prs.save(str(ppt_path)); receipt["slide_count"]=len(prs.slides); receipt["slide_index"]=records
        receipt["v319_overlay_eis_drt_slides"]=True; receipt["v319_overlay_eis_drt_slide_count"]=len(added)
        receipt["v319_overlay_selected_potentials_V"]=[float(x) for x in selected]
        for key,path in figs.items(): receipt[f"v319_{key}_figure"]=str(path or "")
    else:
        receipt["v319_overlay_eis_drt_slides"]=False
    return receipt


_V319_PREV_WRITE_POWERPOINT_SUMMARY = write_powerpoint_summary

def write_powerpoint_summary(*args, **kwargs):
    global VERSION, APP_VERSION, APP_TITLE, FRONT_SLIDE_FOOTER
    try:
        from . import release_identity as _release_identity
        VERSION=str(_release_identity.APP_VERSION); APP_VERSION=str(_release_identity.SEMVER); APP_TITLE=str(_release_identity.APP_TITLE); FRONT_SLIDE_FOOTER=f"Made by FaradaIQ eStudio {VERSION}"
    except Exception:
        VERSION="v320"; APP_VERSION="0.320.0"; APP_TITLE="FaradaIQ Studio v320"; FRONT_SLIDE_FOOTER="Made by FaradaIQ Studio v320"
    receipt=_V319_PREV_WRITE_POWERPOINT_SUMMARY(*args, **kwargs)
    try:
        root=Path(args[0] if args else kwargs.get("experiment_root")).expanduser().resolve()
        manifest=kwargs.get("manifest")
        if manifest is None:
            mf=root/"faradaiq_portable_browser_manifest.json"
            if mf.exists(): manifest=json.loads(mf.read_text(encoding="utf-8"))
        if isinstance(manifest, Mapping):
            receipt=dict(receipt); receipt=_v319_append_overlay_eis_drt_slides(root, manifest, receipt)
            (root/PPT_MANIFEST_NAME).write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
    except Exception as exc:
        try:
            receipt=dict(receipt); receipt["v319_overlay_eis_drt_slides"]=False; receipt["v319_overlay_eis_drt_error"]=str(exc)
            root=Path(args[0] if args else kwargs.get("experiment_root")).expanduser().resolve(); (root/PPT_MANIFEST_NAME).write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
        except Exception: pass
    return receipt
# ---- End FaradaIQ Studio v320 potential-resolved EIS/DRT overlay PowerPoint release ----
