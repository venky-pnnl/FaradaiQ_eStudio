"""FaradaIQ Studio v291 raw Tafel trace view.

Provides a technique-aware, non-fitting visualization of measured current as
log10(|I|/A) versus measured potential in volts.  Acquisition order is
preserved so forward/reverse CV branches remain visible as traces.  This view
is intentionally separate from the governed Tafel fitting workflow.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

VERSION = "v292"
MODE_LABEL = "Tafel trace log|I| vs V"


def prepare_tafel_trace(
    df: pd.DataFrame,
    potential_col: Optional[str] = None,
    current_col: Optional[str] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Return a unit-normalized raw Tafel trace in acquisition order.

    The transformation is only ``log10(abs(I_A))``.  It performs no Eeq/OCP
    subtraction, current-density normalization, iR correction, smoothing,
    fitting, branch deletion, interpolation, or point reordering.
    """
    import faradaiq_tafel_adapter as tafel

    if potential_col is None or current_col is None:
        detected_potential, detected_current, warnings = tafel._detect_columns(df)
        potential_col = potential_col or detected_potential
        current_col = current_col or detected_current
    else:
        warnings = []

    p_raw = pd.to_numeric(df[potential_col], errors="coerce").to_numpy(dtype=float)
    i_raw = pd.to_numeric(df[current_col], errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(p_raw) & np.isfinite(i_raw)
    p_raw = p_raw[finite]
    i_raw = i_raw[finite]
    source_index = np.arange(len(df), dtype=int)[finite]
    if len(p_raw) < 2:
        raise ValueError("Need at least two finite potential/current samples for a Tafel trace.")

    p_scale = float(tafel._unit_scale_from_name(str(potential_col), "potential", p_raw))
    i_scale = float(tafel._unit_scale_from_name(str(current_col), "current", i_raw))
    potential_v = p_raw * p_scale
    current_a = i_raw * i_scale

    nonzero = np.isfinite(current_a) & (np.abs(current_a) > 0.0)
    zero_count = int(np.count_nonzero(~nonzero))
    potential_v = potential_v[nonzero]
    current_a = current_a[nonzero]
    source_index = source_index[nonzero]
    if len(potential_v) < 2:
        raise ValueError("Too few non-zero current samples for log10(|I|) Tafel trace.")

    out = pd.DataFrame(
        {
            "acquisition_index": source_index,
            "potential_V": potential_v,
            "current_A": current_a,
            "log10_abs_current_A": np.log10(np.abs(current_a)),
            "current_sign": np.where(current_a > 0, "anodic", "cathodic"),
        }
    )
    meta: Dict[str, Any] = {
        "mode": MODE_LABEL,
        "potential_column": str(potential_col),
        "current_column": str(current_col),
        "potential_scale_to_V": p_scale,
        "current_scale_to_A": i_scale,
        "finite_input_samples": int(len(p_raw)),
        "plotted_samples": int(len(out)),
        "zero_current_samples_omitted": zero_count,
        "preserves_acquisition_order": True,
        "transformation": "log10(abs(current_A))",
        "warnings": list(warnings),
    }
    return out, meta


def plot_tafel_trace(ax: Any, trace: pd.DataFrame, label: str = "Measured trace") -> Any:
    """Plot one raw Tafel trace while preserving acquisition order."""
    x = trace["potential_V"].to_numpy(float)
    y = trace["log10_abs_current_A"].to_numpy(float)
    line, = ax.plot(
        x,
        y,
        linewidth=1.5,
        marker="." if len(trace) <= 450 else None,
        markersize=3.0,
        label=label,
    )
    ax.set_xlabel("Potential / V")
    ax.set_ylabel("log₁₀(|I| / A)")
    return line


def _selected_paths(browser: Any, core: Mapping[str, Any]) -> list[Path]:
    selector = core.get("_v130_selected_iv_lsv_paths")
    if callable(selector):
        try:
            return [Path(p) for p in selector(browser, generous=True)]
        except Exception:
            pass
    try:
        return [Path(p) for p in browser.selected_paths(plottable_only=True)]
    except Exception:
        return []


def show_selected_tafel_trace(browser: Any, core: Mapping[str, Any]) -> list[Path]:
    """Filter selected data to IV/CV/LSV and render the raw log-current trace."""
    paths = _selected_paths(browser, core)
    panel = getattr(browser, "_v206_pub_panel", None) or getattr(browser, "_v200_pub_panel", None)
    if panel is None:
        raise RuntimeError("Publication plot panel is not available.")

    try:
        maxn = max(1, int(float(browser.max_curves_var.get())))
    except Exception:
        maxn = 24

    curves = []
    accepted: list[Path] = []
    for path in paths:
        if len(curves) >= maxn:
            break
        try:
            curve = panel._load_curve(path)
            prepare_tafel_trace(curve["df"])
            curves.append(curve)
            accepted.append(path)
        except Exception as exc:
            try:
                browser.log(f"Tafel trace skipped {path.name}: {exc}")
            except Exception:
                pass

    if not curves:
        raise ValueError("No selected CV/LSV/IV files contain usable potential/current data for a Tafel trace.")

    try:
        browser.plot_mode_var.set(MODE_LABEL)
    except Exception:
        pass
    # The ordinate is already logarithmically transformed. Keep both axes on
    # linear display scales so a stale Browser log-axis preference cannot hide
    # negative log10 current values.
    for attr in ("plot_xscale_var", "plot_yscale_var"):
        try:
            getattr(browser, attr).set("Linear")
        except Exception:
            pass
    panel.render(curves, clear=True)
    try:
        browser.log(
            f"Tafel trace rendered for {len(curves)} selected CV/LSV/IV file(s): "
            "log10(|I|/A) vs measured potential; acquisition order preserved; no fit applied."
        )
    except Exception:
        pass
    return accepted


def install(core: Dict[str, Any]) -> None:
    """Install the v291 Tafel trace renderer and selected-data action."""
    panel_cls = core.get("FaradaIQPublicationPlotPanelV206") or core.get("FaradaIQPublicationPlotPanel")
    if panel_cls is not None and not getattr(panel_cls, "_v291_tafel_trace_patch", False):
        previous = panel_cls._plot_xy

        def plot_xy(self: Any, curve: Mapping[str, Any], mode_l: str):
            mode_text = str(mode_l or "").lower()
            if "tafel" in mode_text and "log" in mode_text:
                try:
                    trace, meta = prepare_tafel_trace(curve["df"])
                    handle = plot_tafel_trace(self.ax, trace, str(curve.get("label") or Path(curve.get("path", "")).stem))
                    curve["tafel_trace_meta"] = meta
                    return handle
                except Exception as exc:
                    try:
                        self.browser.log(f"Tafel trace fallback for {Path(curve.get('path','')).name}: {exc}")
                    except Exception:
                        pass
            return previous(self, curve, mode_l)

        panel_cls._plot_xy = plot_xy
        panel_cls._v291_tafel_trace_patch = True

    # The current publication panel renderer calls the module-level helper
    # directly, so patch that helper as well.  This keeps behavior identical for
    # already-instantiated and subsequently-created panels.
    previous_helper = core.get("_v206b_plot_xy")
    if callable(previous_helper) and not core.get("_v291_tafel_trace_helper_installed"):
        def helper(self: Any, curve: Mapping[str, Any], mode_l: str):
            mode_text = str(mode_l or "").lower()
            if "tafel" in mode_text and "log" in mode_text:
                trace, meta = prepare_tafel_trace(curve["df"])
                handle = plot_tafel_trace(self.ax, trace, str(curve.get("label") or Path(curve.get("path", "")).stem))
                try:
                    curve["tafel_trace_meta"] = meta
                except Exception:
                    pass
                return handle
            return previous_helper(self, curve, mode_l)
        core["_v206b_plot_xy"] = helper
        # Replace the function in the legacy module globals used by _v206b_render.
        core["_v291_tafel_trace_helper_installed"] = True

    def open_selected(browser: Any):
        try:
            return show_selected_tafel_trace(browser, core)
        except Exception as exc:
            messagebox = core.get("messagebox")
            if messagebox is not None:
                try:
                    messagebox.showinfo("Tafel trace", str(exc), parent=browser)
                    return []
                except Exception:
                    pass
            raise

    core["_v291_show_selected_tafel_trace"] = open_selected
    core["_v291_prepare_tafel_trace"] = prepare_tafel_trace
    core["_v291_tafel_trace_mode"] = MODE_LABEL
