"""Explicit electrochemical calculations for the v341 workbench."""

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
import re

import numpy as np
import pandas as pd
from scipy.constants import Avogadro, R, elementary_charge
from scipy.integrate import cumulative_trapezoid
from scipy.optimize import least_squares
from scipy.stats import linregress

from . import browser_analysis as browser
from .workbench_curves import metadata_tags

F = Avogadro * elementary_charge


@dataclass
class ScienceResult:
    tables: dict[str, pd.DataFrame]
    charts: tuple[tuple[str, str, tuple[str, ...], str | None], ...] = ()
    notes: tuple[str, ...] = ()
    metrics: dict = field(default_factory=dict)


def _number(value, label):
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"Enter a finite value for {label}.") from None
    if not math.isfinite(value):
        raise ValueError(f"Enter a finite value for {label}.")
    return value


def _positive(value, label):
    value = _number(value, label)
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return value


def _electrons(value):
    value = _positive(value, "Electron count")
    if not value.is_integer():
        raise ValueError("Electron count must be a positive integer.")
    return int(value)


def _window(bounds, label="Window"):
    try:
        lower, upper = bounds
    except (TypeError, ValueError):
        raise ValueError(f"Enter the lower and upper limits for {label.lower()}.") from None
    lower, upper = _number(lower, f"{label} lower limit"), _number(upper, f"{label} upper limit")
    if not lower < upper:
        raise ValueError(f"{label} limits must increase.")
    return lower, upper


_ROLE_COLUMNS = {
    "potential": "potential_V", "current": "current_A", "time": "time_s",
    "frequency": "frequency_Hz", "zreal": "Zreal_ohm", "zimag": "Zimag_ohm",
}

_QUANTITY_NAMES = {
    "potential": r"<?e(?:we|ce|oc|mon)?>?|ocp|(?:(?:applied|measured|bias|dc|holding|working electrode|open circuit) )?potential|voltage|e dc",
    "current": r"<?i(?:we|mon)?>?|amps?|amperes?|(?:(?:measured|total|working electrode|applied|monitor|average|mean|dc) )?current",
    "time": r"t|time|elapsed time|acquisition time|seconds",
    "frequency": r"f|freq|frequency|(?:ac|protocol) frequency",
    "zreal": r"zreal|z real|real z|real impedance|rez|re\s*\(\s*z\s*\)|z'",
    "zimag": r"(?:[+-]|(?:neg|negative|minus) )?(?:zimag|z imag|imag z|imaginary impedance|imz|im\s*\(\s*z\s*\)|z'')",
}
_UNIT_SUFFIX = re.compile(r"(.+?)(?:[\s_/]+([^\s_/\[\]()]+)|\s*\[([^\[\]]+)\]|\s*\(([^()]+)\))$")
_REFERENCE_SUFFIX = re.compile(r"[\s_]+(\(\s*)?vs\.?[\s_]+", flags=re.IGNORECASE)
_REFERENCE_FIELDS = ("potential_reference", "reference_electrode")


def _reference_name(value):
    name = " ".join(value.split()) if isinstance(value, str) else ""
    if name.casefold() in ("unknown", "none", "n/a", "na", "unspecified", "not specified", "not recorded", "-"):
        return ""
    return name


def _potential_header(column):
    name = str(column).strip()
    match = _REFERENCE_SUFFIX.search(name)
    if match is None:
        return name, ""
    quantity, reference = name[:match.start()].rstrip(), name[match.end():].strip()
    for opening, closing in (("(", ")"), ("[", "]")):
        if reference.endswith(closing) and quantity.count(opening) > quantity.count(closing):
            quantity += closing
            reference = reference[:-1].strip()
            break
    else:
        if match[1] and reference.endswith(")"):
            reference = reference[:-1].strip()
    return quantity, _reference_name(reference)


def _measurement_scale(column, role):
    name = _potential_header(column)[0] if role == "potential" else str(column).strip()
    name = name.translate(browser._NORMALIZE_UNITS).replace("Ohm", "ohm")
    # Some native instrument exports describe an averaged channel after its unit.
    name = re.sub(r"\s+\((?:mean|average)\)$", "", name, flags=re.IGNORECASE)
    match = _UNIT_SUFFIX.fullmatch(name)
    if match:
        quantity = re.sub(r"[\s_]+", " ", match[1].strip()).casefold()
        unit = next(part.strip() for part in match.groups()[1:] if part is not None)
        units = browser._UNITS[role]
        if re.fullmatch(_QUANTITY_NAMES[role], quantity) and unit in units:
            return units[unit]
    raise ValueError(f"{column!r} has ambiguous {role} units. Select a {role} measurement with a supported simple unit.")


def _measurement_column(table, column, role, detected):
    if column is not None:
        return column
    candidate = detected.get(role)
    for possible in (candidate, *table.columns):
        try:
            _measurement_scale(possible, role)
        except ValueError:
            continue
        return possible
    return candidate


def _normalized(table, columns, required):
    """Use the existing browser scales, but require an explicit supported unit."""
    if not table.columns.is_unique:
        raise ValueError("Select a table with unique column names.")
    detected = browser.measurement_roles(table)
    selected = {role: _measurement_column(table, (columns or {}).get(role), role, detected) for role in required}
    chosen = [column for column in selected.values() if column is not None]
    if len(set(chosen)) != len(chosen):
        raise ValueError("Select different columns for each measurement quantity.")
    values = {}
    for role, column in selected.items():
        if column not in table:
            raise ValueError(f"Select the {role} column with an explicit unit.")
        if role == "current" and browser._current_density_header(column):
            raise ValueError(f"{column!r} contains current density. Supply measured total current.")
        scale = _measurement_scale(column, role)
        values[_ROLE_COLUMNS[role]] = pd.to_numeric(table[column], errors="coerce").to_numpy(dtype=float, na_value=np.nan) * scale
    out = pd.DataFrame(values)
    if not np.isfinite(out.to_numpy()).all():
        raise ValueError("Selected columns contain missing or nonfinite samples. Clean or split the trace first.")
    return out


def potential_reference_sources(table, columns, identity):
    """Read declarations on the selected potential column, metadata and tags."""
    column = _measurement_column(table, (columns or {}).get("potential"), "potential", browser.measurement_roles(table))
    references = {"column": _potential_header(column)[1]}
    contexts = {"": identity, "metadata.": identity.get("metadata")} | identity.get("_potential_reference_context", {})
    for prefix, metadata in contexts.items():
        if isinstance(metadata, Mapping):
            tags = metadata_tags(metadata)
            for field in _REFERENCE_FIELDS:
                references[f"{prefix}{field}"] = _reference_name(metadata.get(field))
                references[f"{prefix}tags.{field}"] = _reference_name(tags.get(field))
    return {source: reference for source, reference in references.items() if reference}


def _potential_series(records, common_reference, offsets):
    """Validate reference scales before producing normalized copies of the traces."""
    target = _reference_name(common_reference)
    if common_reference and not target:
        raise ValueError("Enter a named common potential reference.")
    if offsets is None:
        offsets = [None] * len(records)
    if len(offsets) != len(records):
        raise ValueError("Supply one potential offset or blank entry per measurement.")
    if any(offset is not None for offset in offsets) and not target:
        raise ValueError("Declare the common potential reference for the supplied offsets.")

    selected, known = [], {}
    for name, table, axis_value, columns, *metadata in records:
        identity = (metadata[0] or {}) if metadata else {}
        declarations = potential_reference_sources(table, columns, identity)
        for reference in declarations.values():
            known.setdefault(reference.casefold(), reference)
        selected.append((name, table, axis_value, columns, identity, declarations))

    if not target:
        if len(known) > 1:
            raise ValueError("Conflicting potential references (" + ", ".join(known.values()) +
                             "). Declare a common reference and provide reviewed offsets for differing sources.")
        if len(selected) > 1 and any(not declarations for *_, declarations in selected):
            raise ValueError("At least one measurement has an unknown potential reference. Declare the common potential reference before comparing.")
        target = next(iter(known.values()), "")

    normalized = []
    for (name, table, axis_value, columns, identity, declarations), offset in zip(selected, offsets):
        if offset is None and any(reference.casefold() != target.casefold() for reference in declarations.values()):
            refs = ", ".join(dict.fromkeys(declarations.values()))
            raise ValueError(f"{name}: recorded potential reference ({refs}) differs from {target}. Enter a reviewed potential offset.")
        frame = _normalized(table, columns, ("potential", "current"))
        if offset is not None:
            frame["potential_V"] += _number(offset, f"Potential offset for {name} (V)")
            if not np.isfinite(frame.potential_V).all():
                raise ValueError(f"{name}: potential values after applying the offset must be finite.")
        normalized.append((name, frame, axis_value, identity))
    return normalized, target or None


def _timed(table, columns, signal):
    frame = _normalized(table, columns, ("time", signal))
    if len(frame) < 2 or np.any(np.diff(frame.time_s) <= 0):
        raise ValueError("Time must contain at least two strictly increasing samples in acquisition order.")
    return frame


def _fit(x, y, *, minimum=3):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        raise ValueError("Regression requires finite inputs.")
    if len(np.unique(x)) < minimum or np.ptp(x) <= np.finfo(float).eps * np.max(np.abs(x)) * 16:
        raise ValueError(f"Regression requires at least {minimum} distinct independent values.")
    if np.all(y == y[0]):
        return {"slope": 0.0, "intercept": float(y[0]), "r_squared": None, "slope_standard_error": 0.0,
                "rmse": 0.0, "points": len(x)}, y.copy(), np.zeros_like(y)
    fit = linregress(x, y)
    predicted = fit.intercept + fit.slope * x
    residual = y - predicted
    if not np.isfinite([fit.slope, fit.intercept, fit.stderr]).all() or not np.isfinite(residual).all():
        raise ValueError("The regression is numerically unresolved at the supplied scale.")
    return {"slope": float(fit.slope), "intercept": float(fit.intercept),
            "r_squared": float(fit.rvalue ** 2),
            "slope_standard_error": float(fit.stderr), "rmse": float(np.sqrt(np.mean(residual ** 2))),
            "points": len(x)}, predicted, residual


def _varying(y, label):
    y = np.asarray(y, dtype=float)
    if np.ptp(y) <= np.finfo(float).eps * np.max(np.abs(y)) * 16:
        raise ValueError(f"Identifying the requested parameter requires more variation in {label.lower()}.")


def _regression(x, y, x_name, y_name, *, notes=(), minimum=3):
    stats, predicted, residual = _fit(x, y, minimum=minimum)
    frame = pd.DataFrame({x_name: x, y_name: y, "fitted": predicted, "residual": residual}).sort_values(x_name)
    return ScienceResult({"Regression": pd.DataFrame([stats]), "Points and residuals": frame},
                         (("Points and residuals", x_name, (y_name, "fitted"), None),
                          ("Points and residuals", x_name, ("residual",), None)), notes, stats)


def _branches(frame):
    potential = frame.potential_V.to_numpy()
    if len(potential) < 3:
        raise ValueError("A voltammogram requires at least three samples.")
    step = np.diff(potential)
    direction = pd.Series(np.sign(step)).replace(0, np.nan).ffill().bfill().to_numpy()
    if not np.isfinite(direction).all():
        raise ValueError("Applied potential is constant. Select a voltammogram.")
    turns = np.r_[0, np.flatnonzero(direction[1:] != direction[:-1]) + 1, len(potential) - 1]
    return [(index, int(direction[start]), frame.iloc[start:stop + 1].reset_index(drop=True))
            for index, (start, stop) in enumerate(zip(turns[:-1], turns[1:]), 1)]


def _slice_potential(frame, branch, direction=None):
    branches = _branches(frame)
    if direction is not None:
        if direction not in (-1, 1):
            raise ValueError("Sweep direction must be +1 or −1.")
        branches = [item for item in branches if item[1] == direction]
    if not 1 <= branch <= len(branches):
        raise ValueError(f"Sweep {branch} is unavailable. Found {len(branches)} matching sweeps.")
    return branches[branch - 1]


def _peak(frame, polarity, bounds):
    if polarity not in ("anodic", "cathodic"):
        raise ValueError("Select anodic or cathodic peak polarity.")
    selected = frame
    if bounds is not None:
        lo, hi = _window(bounds, "Peak potential window")
        selected = frame.loc[frame.potential_V.between(lo, hi)]
    sign = 1 if polarity == "anodic" else -1
    current = selected.current_A.to_numpy() * sign
    if len(current) < 3:
        raise ValueError("Peak detection requires at least three samples in the selected sweep and window.")
    _varying(current, "Peak current")
    peaks = np.flatnonzero(current == np.max(current))
    if len(peaks) != 1 or peaks[0] in (0, len(current) - 1) or current[peaks[0]] <= 0:
        raise ValueError("No unique interior peak with the selected polarity. Review the sweep and potential window.")
    point = selected.iloc[peaks[0]]
    return float(point.potential_V), float(point.current_A)


def cv_peak_tracking(table, *, polarity, window=None, columns=None):
    if polarity not in ("both", "anodic", "cathodic"):
        raise ValueError("Select anodic, cathodic, or both peak polarities.")
    if window is not None:
        window = _window(window, "Peak potential window")
    frame = _normalized(table, columns, ("potential", "current"))
    polarities = ("anodic", "cathodic") if polarity == "both" else (polarity,)
    rows, traces = [], []
    for sweep, direction, segment in _branches(frame):
        direction_name = "increasing" if direction > 0 else "decreasing"
        trace_name = f"{sweep} ({direction_name})"
        for sign in polarities:
            row = {"sweep": sweep, "direction": direction_name, "polarity": sign}
            try:
                row["peak_potential_V"], row["peak_current_A"] = _peak(segment, sign, window)
                row["status"] = "peak measured"
            except ValueError as exc:
                row.update(peak_potential_V=None, peak_current_A=None, status=str(exc))
            rows.append(row)
        traces.append(segment.assign(sweep=trace_name))
    peaks = pd.DataFrame(rows)
    return ScienceResult({"Peaks": peaks, "Sweeps": pd.concat(traces, ignore_index=True)},
                         (("Sweeps", "potential_V", ("current_A",), "sweep"),
                          ("Peaks", "sweep", ("peak_potential_V",), "polarity"),
                          ("Peaks", "sweep", ("peak_current_A",), "polarity")),
                         ("Peaks are sampled signed extrema within each sweep. Select a window to isolate a feature. No baseline is subtracted.",))


def _series_peaks(records, *, polarity, branch, direction, window):
    if len(records) < 3:
        raise ValueError("Select at least three voltammograms with distinct scan rates.")
    rows, traces = [], []
    for name, frame, rate, _ in records:
        rate = _positive(rate, f"Scan rate for {name} (V/s)")
        sweep, _, segment = _slice_potential(frame, branch, direction)
        try:
            peak_e, peak_i = _peak(segment, polarity, window)
        except ValueError as exc:
            raise ValueError(f"{name}: {exc}") from exc
        rows.append(dict(measurement=name, scan_rate_V_s=rate, sweep=sweep,
                         peak_potential_V=peak_e, peak_current_A=peak_i, peak_current_magnitude_A=abs(peak_i)))
        traces.append(segment.assign(measurement=name))
    return pd.DataFrame(rows), pd.concat(traces, ignore_index=True)


def scan_rate_series(records, *, polarity, branch=1, direction=None, window=None, transform="sqrt",
                     common_potential_reference=None, potential_offsets_V=None):
    normalized, reference = _potential_series(records, common_potential_reference, potential_offsets_V)
    peaks, traces = _series_peaks(normalized, polarity=polarity, branch=branch, direction=direction, window=window)
    peaks = peaks.sort_values("scan_rate_V_s", kind="stable")
    rate = peaks.scan_rate_V_s.to_numpy()
    if transform == "sqrt":
        x, name = np.sqrt(rate), "sqrt_scan_rate_sqrt_V_per_s"
    elif transform == "linear":
        x, name = rate, "scan_rate_V_s"
    elif transform == "log10":
        x, name = np.log10(rate), "log10_scan_rate_re_V_s"
    else:
        raise ValueError("Select linear, square-root or log10 scan rate.")
    result = _regression(x, peaks.peak_current_A, name, "signed_peak_current_A",
                         notes=("Current regression retains the selected peak sign. Mechanistic interpretation requires independent evidence.",))
    result.tables.update({"Peak observations": peaks, "CV overlays": traces})
    result.charts += (("Peak observations", "scan_rate_V_s", ("peak_potential_V",), None),
                      ("CV overlays", "potential_V", ("current_A",), "measurement"))
    result.metrics["potential_reference"] = reference
    return result


def _interpolate_window(x, y, bounds, label):
    lo, hi = _window(bounds, label)
    if x[0] > lo or x[-1] < hi:
        raise ValueError(f"{label} must lie within the sampled range.")
    inside = (x > lo) & (x < hi)
    xx = np.r_[lo, x[inside], hi]
    return xx, np.interp(xx, x, y)


def _current_at_potential(frame, bias, name):
    ordered = frame.sort_values("potential_V", kind="stable")
    voltage = ordered.potential_V.to_numpy()
    if not voltage[0] <= bias <= voltage[-1]:
        raise ValueError(f"{name}: the selected potential lies outside the chosen sweep.")
    before = np.searchsorted(voltage, bias, side="left")
    after = np.searchsorted(voltage, bias, side="right")
    required = [bias] if after > before else [voltage[before - 1], voltage[after]]
    observed = ordered.loc[ordered.potential_V.isin(required)]
    if observed.groupby("potential_V").current_A.nunique().gt(1).any():
        raise ValueError(f"{name}: current at a potential boundary is ambiguous because samples repeat.")
    return float(np.interp(bias, observed.potential_V, observed.current_A))


def _potential_window(frame, bounds):
    """Clip in acquisition order and interpolate each traversed window edge."""
    if len(frame) < 2:
        raise ValueError("A charge or current window needs a potential sweep.")
    voltage = frame.potential_V.to_numpy()
    lo, hi = _window(bounds, "Potential window")
    if lo < np.min(voltage) or hi > np.max(voltage):
        raise ValueError("Potential window must lie within the selected sweep.")
    increasing = voltage[-1] > voltage[0]
    sign = 1 if increasing else -1
    key = voltage * sign
    left, right = (lo, hi) if increasing else (-hi, -lo)
    start = np.searchsorted(key, left, side="left")
    stop = np.searchsorted(key, right, side="right")

    def crossing(target, index):
        weight = (target - key[index - 1]) / (key[index] - key[index - 1])
        row = frame.iloc[index - 1] + weight * (frame.iloc[index] - frame.iloc[index - 1])
        row["potential_V"] = sign * target
        return row.to_frame().T

    points = []
    if key[start] != left:
        points.append(crossing(left, start))
    points.append(frame.iloc[start:stop])
    if key[stop - 1] != right:
        points.append(crossing(right, stop))
    return pd.concat(points, ignore_index=True)


def _charges(time, current):
    time, current = np.asarray(time), np.asarray(current)
    cross = np.flatnonzero(np.sign(current[:-1]) * np.sign(current[1:]) < 0)
    insert_t = time[cross] + np.diff(time)[cross] * (-current[cross] / np.diff(current)[cross])
    order = np.argsort(np.r_[time, insert_t], kind="stable")
    t = np.r_[time, insert_t][order]
    i = np.r_[current, np.zeros(len(cross))][order]
    anodic = float(cumulative_trapezoid(np.maximum(i, 0), t, initial=0)[-1])
    cathodic = float(cumulative_trapezoid(np.minimum(i, 0), t, initial=0)[-1])
    return {"net_charge_C": anodic + cathodic, "anodic_charge_C": anodic, "cathodic_charge_C": cathodic}


def _charge_result(frame, source, notes=()):
    time, current = frame.time_s.to_numpy(), frame.current_A.to_numpy()
    summary = _charges(time, current) | {"duration_s": float(time[-1] - time[0]), "time_source": source}
    frame = frame.assign(cumulative_signed_charge_C=cumulative_trapezoid(current, time, initial=0))
    return ScienceResult({"Charge": pd.DataFrame([summary]), "Integration points": frame},
                         (("Integration points", "time_s", ("current_A",), None),
                          ("Integration points", "time_s", ("cumulative_signed_charge_C",), None)), notes, summary)


def integrate_cv_charge(table, *, window, branch=1, direction=None, scan_rate_V_s=None, columns=None):
    required = ("potential", "current", "time") if scan_rate_V_s is None else ("potential", "current")
    frame = _normalized(table, columns, required)
    if "time_s" in frame and np.any(np.diff(frame.time_s) <= 0):
        raise ValueError("Use strictly increasing measured time or provide a constant scan rate (V/s).")
    sweep, _, selected = _slice_potential(frame, branch, direction)
    sliced = _potential_window(selected, window)
    source = "measured"
    if scan_rate_V_s is not None:
        rate = _positive(scan_rate_V_s, "Scan rate (V/s)")
        sliced = sliced.assign(time_s=np.r_[0.0, np.cumsum(np.abs(np.diff(sliced.potential_V))) / rate])
        source = "constant scan rate"
    result = _charge_result(sliced, source, ("Window edges are interpolated. Current retains its measured sign.",))
    result.tables["Charge"]["sweep"] = sweep
    return result


def double_layer_capacitance(records, *, potential_V, increasing_sweep=1, decreasing_sweep=1,
                             common_potential_reference=None, potential_offsets_V=None):
    bias = _number(potential_V, "Nonfaradaic potential (V)")
    normalized, reference = _potential_series(records, common_potential_reference, potential_offsets_V)
    rows = []
    for name, frame, rate, _ in normalized:
        rate = _positive(rate, f"Scan rate for {name} (V/s)")
        currents = []
        for branch, direction in ((increasing_sweep, 1), (decreasing_sweep, -1)):
            _, _, segment = _slice_potential(frame, branch, direction)
            if not segment.potential_V.min() < bias < segment.potential_V.max():
                raise ValueError(f"{name}: nonfaradaic potential must lie inside both selected sweeps.")
            currents.append(_current_at_potential(segment, bias, name))
        rows.append((name, rate, *currents, (currents[0] - currents[1]) / 2))
    observations = pd.DataFrame(rows, columns=["measurement", "scan_rate_V_s", "increasing_current_A",
                                                "decreasing_current_A", "half_current_difference_A"])
    if len(observations) < 3:
        raise ValueError("Select at least three measurements at distinct scan rates.")
    _varying(observations.half_current_difference_A, "Half-current difference")
    result = _regression(observations.scan_rate_V_s, observations.half_current_difference_A,
                         "scan_rate_V_s", "half_current_difference_A",
                         notes=("The selected bias must be nonfaradaic in both sweep directions. The free intercept diagnoses background asymmetry.",))
    result.metrics["Cdl_F"] = _positive(result.metrics["slope"], "Fitted double-layer capacitance")
    result.metrics["potential_reference"] = reference
    result.tables["Regression"]["Cdl_F"] = result.metrics["Cdl_F"]
    result.tables["Observations"] = observations
    return result


def electrochemical_surface_area(*, signal, reference_per_cm2, method, geometric_area_cm2=None):
    signal = _positive(signal, "Measured signal magnitude")
    reference = _positive(reference_per_cm2, "Material-specific reference per cm²")
    if method not in ("capacitance", "charge"):
        raise ValueError("Use capacitance or charge with a matched material-specific reference.")
    metrics = {"method": method, "ECSA_cm2": signal / reference}
    unit = "F" if method == "capacitance" else "C"
    metrics |= {f"signal_{unit}": signal, f"reference_{unit}_cm2": reference}
    if geometric_area_cm2 is not None:
        metrics["roughness_factor"] = metrics["ECSA_cm2"] / _positive(geometric_area_cm2, "Geometric area (cm²)")
    return ScienceResult({"Surface area": pd.DataFrame([metrics])},
                         notes=("The selected reference must match the electrode material, surface process, conditions and measurement units.",),
                         metrics=metrics)


def randles_sevcik(records, *, polarity, area_cm2, concentration_mol_cm3, electrons, temperature_K,
                   branch=1, direction=None, window=None, common_potential_reference=None, potential_offsets_V=None):
    n = _electrons(electrons)
    area = _positive(area_cm2, "Electrode area (cm²)")
    concentration = _positive(concentration_mol_cm3, "Bulk concentration (mol/cm³)")
    temperature = _positive(temperature_K, "Temperature (K)")
    normalized, reference = _potential_series(records, common_potential_reference, potential_offsets_V)
    peaks, _ = _series_peaks(normalized, polarity=polarity, branch=branch, direction=direction, window=window)
    x = np.sqrt(peaks.scan_rate_V_s)
    _varying(peaks.peak_current_magnitude_A, "Peak current magnitude")
    result = _regression(x, peaks.peak_current_magnitude_A, "sqrt_scan_rate_sqrt_V_per_s", "peak_current_magnitude_A",
                         notes=("Reversible Randles–Ševčík assumes planar semi-infinite diffusion, a reversible redox couple, known area and bulk concentration, and corrected background. The fitted intercept is retained.",))
    slope = _positive(result.metrics["slope"], "Peak current versus square-root scan-rate slope")
    coefficient = 0.4463 * n * F * area * concentration * np.sqrt(n * F / (R * temperature))
    result.metrics["diffusion_cm2_s"] = (slope / coefficient) ** 2
    result.metrics["potential_reference"] = reference
    result.tables["Regression"]["diffusion_cm2_s"] = result.metrics["diffusion_cm2_s"]
    result.tables["Peak observations"] = peaks
    return result


def laviron(records, *, electrons, temperature_K, formal_potential_V, anodic_window=None, cathodic_window=None,
            increasing_sweep=1, decreasing_sweep=1, common_potential_reference=None, potential_offsets_V=None):
    n = _electrons(electrons)
    temperature = _positive(temperature_K, "Temperature (K)")
    e0 = _number(formal_potential_V, "Formal potential (V)")
    normalized, reference = _potential_series(records, common_potential_reference, potential_offsets_V)
    anodic, _ = _series_peaks(normalized, polarity="anodic", branch=increasing_sweep, direction=1, window=anodic_window)
    cathodic, _ = _series_peaks(normalized, polarity="cathodic", branch=decreasing_sweep, direction=-1, window=cathodic_window)
    rate = anodic.scan_rate_V_s.to_numpy()
    ea, ec = anodic.peak_potential_V.to_numpy(), cathodic.peak_potential_V.to_numpy()
    thermal = R * temperature / (n * F)
    if np.any(ea - e0 < 2 * thermal) or np.any(e0 - ec < 2 * thermal):
        raise ValueError("Select high-rate scans with both peaks at least 2RT/nF from the supplied formal potential.")
    if np.any(ea - ec < 0.2 / n):
        raise ValueError("Select scans with anodic–cathodic peak separation of at least 0.200 V/n for the high-rate approximation.")
    x = np.log(rate)
    a, pa, ra = _fit(x, ea)
    c, pc, rc = _fit(x, ec)
    aa = 1 - thermal / _positive(a["slope"], "Anodic slope")
    ac = thermal / _positive(-c["slope"], "Magnitude of the cathodic slope")
    if not (0 < aa < 1 and 0 < ac < 1) or abs(aa - ac) > 0.15:
        raise ValueError("Laviron slopes do not identify a consistent transfer coefficient between zero and one.")
    ka = math.exp((e0 - a["intercept"]) / a["slope"]) / a["slope"]
    kc = math.exp((e0 - c["intercept"]) / c["slope"]) / -c["slope"]
    if min(ka, kc) <= 0 or not np.isfinite([ka, kc]).all() or max(ka, kc) / min(ka, kc) > 3:
        raise ValueError("The two high-rate branches give inconsistent rate constants for the supplied formal potential.")
    rows = pd.DataFrame([a | {"branch": "anodic", "alpha": aa, "ks_s_minus1": ka},
                         c | {"branch": "cathodic", "alpha": ac, "ks_s_minus1": kc}])
    points = pd.DataFrame({"measurement": anodic.measurement, "ln_scan_rate_re_V_s": x, "anodic_peak_V": ea,
                           "cathodic_peak_V": ec, "anodic_fit_V": pa, "cathodic_fit_V": pc,
                           "anodic_residual_V": ra, "cathodic_residual_V": rc}).sort_values("ln_scan_rate_re_V_s")
    return ScienceResult({"Branch estimates": rows, "Peak potentials and residuals": points},
                         (("Peak potentials and residuals", "ln_scan_rate_re_V_s",
                           ("anodic_peak_V", "anodic_fit_V", "cathodic_peak_V", "cathodic_fit_V"), None),),
                         ("High-rate diffusionless Laviron analysis for a surface-confined couple with a supplied formal potential. Review branch agreement, fit residuals and uncompensated resistance.",),
                         {"alpha_mean": (aa + ac) / 2, "ks_geometric_mean_s_minus1": math.sqrt(ka * kc),
                          "potential_reference": reference})


def _time_selection(frame, bounds, *, minimum, label):
    lo, hi = _window(bounds, label)
    if lo < frame.time_s.iloc[0] or hi > frame.time_s.iloc[-1]:
        raise ValueError(f"{label} must lie within the sampled time range.")
    selected = frame.loc[frame.time_s.between(lo, hi)].copy()
    if len(selected) < minimum:
        raise ValueError(f"{label} needs at least {minimum} measured samples.")
    return selected


def _exponential(t, current):
    span = np.ptp(t)
    scale = np.ptp(current)
    baseline = float(np.median(current))
    y = (current - baseline) / scale
    x = (t - t[0]) / span
    min_tau = float(np.median(np.diff(t))) / (2 * span)
    max_tau = 5.0

    def prediction(p):
        amplitude, ln_tau, offset = p
        return offset + amplitude * np.exp(-x / np.exp(ln_tau))

    fits = [least_squares(lambda p: prediction(p) - y, [y[0] - y[-1], np.log(tau), y[-1]],
                         bounds=([-np.inf, np.log(min_tau), -np.inf], [np.inf, np.log(max_tau), np.inf]))
            for tau in (max(min_tau * 1.01, 0.05), 0.25, 1.0)]
    best = min(fits, key=lambda fit: fit.cost)
    amplitude, ln_tau, offset = best.x
    singular = np.linalg.svd(best.jac, compute_uv=False)
    if (not best.success or np.any(best.active_mask) or abs(amplitude) < 1e-8
            or singular[-1] <= singular[0] * 1e-7):
        raise ValueError("The exponential time constant is unresolved over this fit interval.")
    tau = float(np.exp(ln_tau) * span)
    return prediction(best.x) * scale + baseline, {
        "amplitude_A_at_fit_start": float(amplitude * scale), "tau_s": tau,
        "current_offset_A": float(offset * scale + baseline), "fit_start_elapsed_s": float(t[0]),
    }


def chronoamperometry_fit(table, *, interval_s, step_time_s, models=("Cottrell + offset", "Exponential + offset"),
                          columns=None):
    frame = _timed(table, columns, "current")
    chosen = tuple(dict.fromkeys(models))
    available = ("Cottrell + offset", "Exponential + offset")
    if not chosen or any(model not in available for model in chosen):
        raise ValueError("Select Cottrell + offset, exponential + offset, or both models.")
    selected = _time_selection(frame, interval_s, minimum=6, label="CA fit interval")
    origin = _number(step_time_s, "Potential step time (s)")
    t = selected.time_s.to_numpy() - origin
    current = selected.current_A.to_numpy()
    _varying(current, "Transient current")
    if np.any(t <= 0):
        raise ValueError("The fit interval must contain only positive time after the potential step.")
    details = selected.assign(elapsed_since_step_s=t)
    rows = []
    for model in chosen:
        parameters = 2 if model == available[0] else 3
        try:
            if model == available[0]:
                stats, predicted, _ = _fit(1 / np.sqrt(t), current)
                if abs(stats["slope"]) <= np.finfo(float).eps * np.max(np.abs(current)) / np.ptp(1 / np.sqrt(t)) * 16:
                    raise ValueError("The Cottrell coefficient is unresolved for this interval.")
                values = {"cottrell_coefficient_A_sqrt_s": stats["slope"], "current_offset_A": stats["intercept"]}
            else:
                predicted, values = _exponential(t, current)
        except ValueError as exc:
            rows.append({"model": model, "status": str(exc)})
            continue
        residual = current - predicted
        label = "cottrell" if model == available[0] else "exponential"
        details[f"{label}_fit_A"] = predicted
        details[f"{label}_residual_A"] = residual
        n = len(t)
        # Residual variance is an estimated parameter in the Gaussian comparison.
        k = parameters + 1
        mse = float(np.mean(residual ** 2))
        floor = float(np.finfo(float).eps * np.max(np.abs(current))) ** 2
        aicc = n * math.log(max(mse, floor, np.finfo(float).tiny)) + 2 * k + (2 * k * (k + 1)) / (n - k - 1)
        rows.append({"model": model, "status": "estimated", "n_points": n, "rmse_A": math.sqrt(mse),
                     "AICc": aicc, "time_min_s": t[0] + origin, "time_max_s": t[-1] + origin, **values})
    accepted = [row for row in rows if row["status"] == "estimated"]
    if not accepted:
        raise ValueError(" ".join(row["status"] for row in rows))
    if len(accepted) > 1:
        best = min(row["AICc"] for row in accepted)
        for row in accepted:
            row["delta_AICc"] = row["AICc"] - best
    fits = tuple(name for name in details.columns if name.endswith("_fit_A"))
    residuals = tuple(name for name in details.columns if name.endswith("_residual_A"))
    return ScienceResult({"Model comparison": pd.DataFrame(rows), "Fits and residuals": details},
                         (("Fits and residuals", "time_s", ("current_A", *fits), None),
                          ("Fits and residuals", "time_s", residuals, None)),
                         ("Models use the same selected samples. Cottrell assumes planar semi-infinite diffusion. The exponential is a descriptive one-time-constant relaxation. Compare residuals and AICc alongside electrochemical context.",))


def nucleation_curves(relative_time):
    x = np.asarray(relative_time, dtype=float)
    if not np.isfinite(x).all() or (x < 0).any():
        raise ValueError("Normalized nucleation time must be finite and nonnegative.")
    inst = np.divide(1.9542 * np.expm1(-1.2564 * x) ** 2, x, out=np.zeros_like(x), where=x > 0)
    prog = np.divide(1.2254 * np.expm1(-2.3367 * x**2) ** 2, x, out=np.zeros_like(x), where=x > 0)
    return inst, prog


def scharifker_hills(table, *, interval_s, step_time_s, polarity, background_A=0, columns=None):
    frame = _timed(table, columns, "current")
    selected = _time_selection(frame, interval_s, minimum=9, label="Nucleation interval")
    origin = _number(step_time_s, "Potential step time (s)")
    fallback = _number(background_A, "Constant background (A)")
    if polarity not in ("anodic", "cathodic"):
        raise ValueError("Select the measured nucleation current polarity.")
    sign = 1 if polarity == "anodic" else -1
    t = selected.time_s.to_numpy() - origin
    current = sign * (selected.current_A.to_numpy() - fallback)
    if np.any(t < 0) or np.any(current < 0):
        raise ValueError("Select nonnegative elapsed time and a background-corrected interval with the declared polarity.")
    index = int(np.argmax(current))
    if current[index] <= 0 or index < 3 or index > len(t) - 4 or np.count_nonzero(current == current[index]) != 1:
        raise ValueError("The nucleation window must include a unique positive peak with at least three points on each side.")
    if current[0] > 0.9 * current[index] or current[-1] > 0.9 * current[index]:
        raise ValueError("The interval must resolve the nucleation rise and decay on both sides of the peak.")
    x = t / t[index]
    y = (current / current[index]) ** 2
    inst, prog = nucleation_curves(x)
    rms_inst, rms_prog = (float(np.sqrt(np.mean((y - model) ** 2))) for model in (inst, prog))
    metrics = {"peak_elapsed_s": float(t[index]), "peak_background_corrected_current_A": sign * float(current[index]),
               "instantaneous_rmse": rms_inst, "progressive_rmse": rms_prog}
    details = pd.DataFrame({"time_over_peak_time": x, "squared_current_ratio": y,
                            "instantaneous": inst, "progressive": prog,
                            "instantaneous_residual": y - inst, "progressive_residual": y - prog})
    return ScienceResult({"Nucleation diagnostics": pd.DataFrame([metrics]), "Normalized transient": details},
                         (("Normalized transient", "time_over_peak_time",
                           ("squared_current_ratio", "instantaneous", "progressive"), None),),
                         ("Scharifker–Hills limiting models describe three-dimensional diffusion-controlled growth. RMSE measures shape agreement over the selected interval. Mechanism assignment requires independent evidence.",), metrics)


def chronopotentiometry_transition(table, *, interval_s, threshold_V_s, columns=None):
    frame = _timed(table, columns, "potential")
    selected = _time_selection(frame, interval_s, minimum=5, label="CP transition interval")
    threshold = _positive(threshold_V_s, "Minimum transition |dE/dt| (V/s)")
    derivatives = np.gradient(selected.potential_V.to_numpy(), selected.time_s.to_numpy())
    magnitudes = np.abs(derivatives)
    index = int(np.argmax(magnitudes))
    interior = 0 < index < len(selected) - 1
    unique = np.count_nonzero(np.isclose(magnitudes, magnitudes[index], rtol=1e-8, atol=0)) == 1
    found = interior and unique and magnitudes[index] >= threshold
    row = {"status": "candidate" if found else "no resolved interior transition",
           "transition_time_s": float(selected.time_s.iloc[index]) if found else None,
           "transition_potential_V": float(selected.potential_V.iloc[index]) if found else None,
           "transition_dE_dt_V_s": float(derivatives[index]) if found else None,
           "threshold_V_s": threshold, "points": len(selected)}
    selected["dE_dt_V_s"] = derivatives
    return ScienceResult({"Transition": pd.DataFrame([row]), "Potential and derivative": selected},
                         (("Potential and derivative", "time_s", ("potential_V",), None),
                          ("Potential and derivative", "time_s", ("dE_dt_V_s",), None)),
                         ("The candidate is the unique interior maximum of |dE/dt| above the supplied threshold. Select the transition interval after the current-step switching transient.",), row)


def ocp_stability(table, *, interval_s, maximum_drift_mV_min=None, maximum_noise_mV=None, columns=None):
    frame = _timed(table, columns, "potential")
    selected = _time_selection(frame, interval_s, minimum=3, label="OCP stability interval")
    t = selected.time_s.to_numpy() - selected.time_s.iloc[0]
    potential = selected.potential_V.to_numpy()
    fit, prediction, residual = _fit(t, potential)
    drift = 60_000 * fit["slope"]
    noise = 1000 * float(np.sqrt(np.sum(residual**2) / (len(t) - 2)))
    row = {"mean_OCP_V": float(np.mean(potential)), "net_drift_V": float(potential[-1] - potential[0]),
           "drift_mV_per_min": drift, "detrended_noise_sd_mV": noise, "raw_sd_mV": float(np.std(potential, ddof=1) * 1000),
           "start_s": float(selected.time_s.iloc[0]), "end_s": float(selected.time_s.iloc[-1]), "points": len(t)}
    if maximum_drift_mV_min is not None and maximum_noise_mV is not None:
        row["maximum_drift_mV_min"] = _positive(maximum_drift_mV_min, "Maximum absolute drift (mV/min)")
        row["maximum_noise_mV"] = _positive(maximum_noise_mV, "Maximum detrended noise (mV)")
        row["status"] = "within selected limits" if abs(drift) <= row["maximum_drift_mV_min"] and noise <= row["maximum_noise_mV"] else "outside selected limits"
    elif maximum_drift_mV_min is not None or maximum_noise_mV is not None:
        raise ValueError("Supply both stability limits or leave both blank.")
    selected = selected.assign(fitted_drift_V=prediction, detrended_residual_V=residual)
    return ScienceResult({"Stability": pd.DataFrame([row]), "Potential and residuals": selected},
                         (("Potential and residuals", "time_s", ("potential_V", "fitted_drift_V"), None),
                          ("Potential and residuals", "time_s", ("detrended_residual_V",), None)),
                         ("Noise is residual standard deviation after linear detrending, with n − 2 degrees of freedom.",), row)


def _current_window(table, columns, interval_s):
    frame = _timed(table, columns, "current")
    time, current = _interpolate_window(frame.time_s.to_numpy(), frame.current_A.to_numpy(),
                                        interval_s, "Current integration interval")
    return pd.DataFrame({"time_s": time, "current_A": current})


def _reaction_charge(summary, polarity, basis):
    if polarity not in ("anodic", "cathodic"):
        raise ValueError("Select anodic or cathodic reaction polarity.")
    if basis == "net":
        charge = summary["net_charge_C"] * (1 if polarity == "anodic" else -1)
    elif basis == "selected polarity":
        charge = abs(summary[f"{polarity}_charge_C"])
    else:
        raise ValueError("Select signed net charge or charge in the selected polarity.")
    return _positive(charge, f"Integrated {polarity} reaction charge (C)")


def deposition(table, *, interval_s, components, efficiency_percent, polarity="cathodic", charge_basis="selected polarity",
               columns=None):
    frame = _current_window(table, columns, interval_s)
    result = _charge_result(frame, "measured")
    charge = _reaction_charge(result.metrics, polarity, charge_basis)
    efficiency = _positive(efficiency_percent, "Assumed deposition efficiency (%)") / 100
    if efficiency > 1:
        raise ValueError("Assumed deposition efficiency must be at most 100%.")
    if not components:
        raise ValueError("Enter at least one deposit component with a molar mass, electron count and mole fraction.")
    rows = []
    for name, mass, n, fraction in components:
        rows.append((name.strip(), _positive(mass, "Molar mass (g/mol)"), _electrons(n),
                     _positive(fraction, "Deposit mole fraction")))
    fractions = np.array([row[3] for row in rows])
    if not all(row[0] for row in rows) or len({row[0] for row in rows}) != len(rows):
        raise ValueError("Deposit components need distinct nonempty names.")
    if not math.isclose(float(fractions.sum()), 1, abs_tol=1e-6):
        raise ValueError("Deposit mole fractions must sum to 1.")
    n_total = charge * efficiency / (F * sum(n * fraction for _, _, n, fraction in rows))
    detail = pd.DataFrame(rows, columns=["component", "molar_mass_g_mol", "electrons_per_formula_unit", "mole_fraction"])
    detail["moles"] = detail.mole_fraction * n_total
    detail["mass_g"] = detail.moles * detail.molar_mass_g_mol
    mass = float(detail.mass_g.sum())
    detail["mass_fraction"] = detail.mass_g / mass
    result.tables["Estimated deposit composition"] = detail
    result.metrics.update(reaction_charge_C=charge, charge_basis=charge_basis, efficiency_percent=efficiency * 100,
                          estimated_deposit_mass_g=mass)
    result.tables["Charge"] = pd.DataFrame([result.metrics])
    result.notes = ("Estimated Faradaic mass uses the supplied composition and efficiency. Verify composition with an independent measurement.",)
    return result


def faradaic_efficiency(table, *, interval_s, products, polarity, charge_basis="net", columns=None):
    frame = _current_window(table, columns, interval_s)
    result = _charge_result(frame, "measured")
    charge = _reaction_charge(result.metrics, polarity, charge_basis)
    rows = []
    for name, amount, unit, n, mass in products:
        label = str(name).strip()
        amount = _number(amount, f"Amount for {label}")
        if amount < 0:
            raise ValueError("Product amounts must be nonnegative.")
        if unit == "mol":
            moles = amount
        elif unit == "µmol":
            moles = amount * 1e-6
        elif unit == "mg":
            moles = amount * 1e-3 / _positive(mass, f"Molar mass for {label} (g/mol)")
        else:
            raise ValueError("Product amount units must be mol, µmol or mg.")
        electrons = _electrons(n)
        q_product = electrons * moles * F
        rows.append({"product": label, "amount": amount, "input_unit": unit, "moles": moles,
                     "electrons_per_molecule": electrons, "equivalent_charge_C": q_product,
                     "faradaic_efficiency_percent": 100 * q_product / charge})
    names = [row["product"] for row in rows]
    if not names or not all(names) or len(set(names)) != len(names):
        raise ValueError("Enter at least one product with a distinct nonempty name.")
    details = pd.DataFrame(rows)
    total = float(details.faradaic_efficiency_percent.sum())
    status = "review charge balance: sum exceeds 100%" if total > 100 * (1 + 1e-10) else "calculated"
    result.metrics.update(reaction_charge_C=charge, charge_basis=charge_basis,
                          summed_faradaic_efficiency_percent=total, status=status)
    result.tables["Charge"] = pd.DataFrame([result.metrics])
    result.tables["Product efficiencies"] = details
    result.notes = ("Use product amounts measured over the same interval as the integrated charge. Electron counts and product recoveries must match the declared reactions.",)
    return result


def tafel_window_sensitivity(table, *, branch, windows_V, area_cm2, equilibrium_V, resistance_ohm=0, columns=None):
    from faradaiq_tafel_adapter import TafelSettings, classical_manual_fits

    if branch not in ("anodic", "cathodic"):
        raise ValueError("Select anodic or cathodic Tafel branch.")
    bounds_list = [_window(bounds, f"Tafel window {index}") for index, bounds in enumerate(windows_V, 1)]
    if len(set(bounds_list)) < 2:
        raise ValueError("Enter at least two distinct Tafel windows to compare.")
    e0 = _number(equilibrium_V, "Equilibrium potential (V)")
    area = _positive(area_cm2, "Geometric area (cm²)")
    resistance = _number(resistance_ohm, "Uncompensated resistance (Ω)")
    if resistance < 0:
        raise ValueError("Uncompensated resistance cannot be negative.")
    frame = _normalized(table, columns, ("potential", "current"))
    eta = frame.potential_V - frame.current_A * resistance - e0
    j = frame.current_A / area
    wanted = (j > 0) if branch == "anodic" else (j < 0)
    normalized = pd.DataFrame({"eta_V": eta[wanted], "log10_abs_j_A_cm2": np.log10(np.abs(j[wanted]))})
    normalized = normalized.sort_values("eta_V")
    rows, lines = [], []
    for index, (lo, hi) in enumerate(bounds_list, 1):
        if (branch == "anodic" and lo <= 0) or (branch == "cathodic" and hi >= 0):
            raise ValueError("Tafel windows must have the corresponding overpotential sign throughout.")
        display = f"{index}: {lo:g} to {hi:g} V"
        chosen = normalized.loc[normalized.eta_V.between(lo, hi)].copy()
        row = {"window": display, "requested_eta_min_V": lo, "requested_eta_max_V": hi,
               "samples": len(chosen), "status": "insufficient distinct overpotentials"}
        if chosen.eta_V.nunique() >= 3:
            field_name = f"manual_{branch}_range_V"
            settings = TafelSettings(**{field_name: (lo, hi)}, surface_area_cm2=area, current_adjust=False)
            fit = next(item for item in classical_manual_fits(normalized, settings) if item.branch == branch)
            slope = fit.slope_dec_per_V
            if slope is not None and slope * (1 if branch == "anodic" else -1) > 1e-12:
                predicted = slope * chosen.eta_V + fit.intercept_log10_j0
                lines.append(chosen.assign(window=display, fitted_log10_abs_j_A_cm2=predicted,
                                            residual_log10_j=chosen.log10_abs_j_A_cm2 - predicted))
                row.update(status="estimated", tafel_slope_mV_dec=fit.tafel_slope_mV_dec, r_squared=fit.r2,
                           fit_eta_min_V=fit.eta_min_V, fit_eta_max_V=fit.eta_max_V,
                           extrapolated_j0_A_cm2=fit.j0_A_cm2)
            else:
                row["status"] = fit.warning or "slope sign does not match selected branch"
        rows.append(row)
    tables = {"Windows": pd.DataFrame(rows)}
    charts = ()
    if lines:
        tables["Fits and residuals"] = pd.concat(lines, ignore_index=True)
        charts = (("Fits and residuals", "eta_V",
                   ("log10_abs_j_A_cm2", "fitted_log10_abs_j_A_cm2"), "window"),)
    return ScienceResult(tables, charts, ("The desktop classical Tafel kernel fits each requested window. The slope retains its branch sign and the j₀ estimate is an extrapolation.",))


def corrosion_series(records, *, area_cm2=None, equivalent_weight_g_per_equiv=None, density_g_cm3=None,
                     common_potential_reference=None, potential_offsets_V=None):
    if not records:
        raise ValueError("Select at least one polarization trace.")
    supply = (equivalent_weight_g_per_equiv is not None, density_g_cm3 is not None)
    if any(supply) and (not all(supply) or area_cm2 is None):
        raise ValueError("Corrosion rate requires area, equivalent weight and density together.")
    settings = {}
    for name, value, units in (("surface_area_cm2", area_cm2, "Geometric area (cm²)"),
                               ("equivalent_weight_g_per_equiv", equivalent_weight_g_per_equiv, "Equivalent weight (g/equiv)"),
                               ("density_g_cm3", density_g_cm3, "Density (g/cm³)")):
        if value is not None:
            settings[name] = _positive(value, units)
    potential_records = [(name, table, None, columns, identity) for name, table, columns, identity in records]
    normalized, reference = _potential_series(potential_records, common_potential_reference, potential_offsets_V)
    rows, dips, traces = [], [], []
    for name, frame, _, identity in normalized:
        result = browser.analyze_corrosion(frame, settings=settings, identity=identity)
        scalars = {key: value for key, value in result.items() if not isinstance(value, (list, dict))}
        rows.append({"measurement": name, **scalars, "flags": ", ".join(result["flags"])})
        traces.append(frame.assign(measurement=name))
        dips.extend({"measurement": name, **dip} for dip in result["dip_candidates"])
    return ScienceResult({"Corrosion metrics": pd.DataFrame(rows), "Dip candidates": pd.DataFrame(dips),
                          "Polarization overlays": pd.concat(traces, ignore_index=True)},
                         (("Polarization overlays", "potential_V", ("current_A",), "measurement"),
                          ("Corrosion metrics", "measurement", ("ecorr_V",), None),
                          ("Corrosion metrics", "measurement", ("icorr_A",), None)),
                         ("The desktop corrosion kernel preserves ambiguity flags and may leave Ecorr or Icorr blank. Shared material constants apply to every selected trace.",),
                         {"potential_reference": reference})


def mott_schottky_frequencies(table, *, negate_imaginary=False, frequencies_Hz=None, columns=None):
    from .v317_advanced_potential_eis import CAP_COLUMNS, MOTT_COLUMNS, SUMMARY_COLUMNS, _mott_diagnostic

    normalized = _normalized(table, columns, ("potential", "frequency", "zreal", "zimag"))
    observed = set(normalized.frequency_Hz)
    if frequencies_Hz is None:
        chosen = sorted(observed)
    else:
        chosen = sorted({_positive(f, "Frequency (Hz)") for f in frequencies_Hz})
        if not chosen or not set(chosen) <= observed:
            raise ValueError("Requested frequencies must be present exactly in the measured grid.")
    raw = browser.analyze_mott_schottky(normalized, negate_imaginary=negate_imaginary)
    if raw.get("status") != "ok":
        raise ValueError("The supplied grid could not produce potential-frequency diagnostics.")
    cap = pd.DataFrame(raw["cap_all"], columns=CAP_COLUMNS)
    diagnostic_rows = [_mott_diagnostic(group.potential_V.to_numpy(), group.C_storage_F.to_numpy(), frequency)
                       for frequency, group in cap.groupby("frequency_Hz", sort=True)]
    candidates = [row for row in diagnostic_rows if row["status"] == "PASS_CANDIDATE"]
    if candidates:
        positive = sum(row["slope_F_minus2_per_V"] > 0 for row in candidates)
        if max(positive, len(candidates) - positive) / len(candidates) < 0.8:
            for row in candidates:
                row.update(status="REJECT", reason="slope_sign_not_consistent_across_frequency")
    diagnostic_rows = [row for row in diagnostic_rows if row["frequency_Hz"] in chosen]
    report_rows = []
    for row in diagnostic_rows:
        retained = dict(row)
        if row["status"] != "PASS_CANDIDATE":
            retained.update(flatband_potential_V=None, slope_type=None)
        report_rows.append(retained)
    diagnostics = pd.DataFrame(report_rows, columns=MOTT_COLUMNS)
    points = cap.loc[cap.frequency_Hz.isin(chosen)].sort_values(["frequency_Hz", "potential_V"]).copy()
    positive_cap = points.C_storage_F > 0
    points.loc[positive_cap, "inverse_C_squared_F_minus2"] = 1 / points.loc[positive_cap, "C_storage_F"] ** 2
    points["frequency_group"] = points.frequency_Hz.map(lambda f: f"{f:.12g} Hz")
    points["fitted_inverse_C_squared_F_minus2"] = np.nan
    for row in report_rows:
        if row["status"] == "PASS_CANDIDATE":
            selected = (points.frequency_Hz.eq(row["frequency_Hz"]) & positive_cap
                        & points.potential_V.between(row["window_V_min"], row["window_V_max"]))
            points.loc[selected, "fitted_inverse_C_squared_F_minus2"] = (
                row["intercept_F_minus2"] + row["slope_F_minus2_per_V"] * points.loc[selected, "potential_V"])
    tables = {"Frequency diagnostics": diagnostics, "Frequency points": points,
              "EIS fit diagnostics": pd.DataFrame(raw["summary"], columns=SUMMARY_COLUMNS)}
    return ScienceResult(tables, (("Frequency points", "potential_V",
                                  ("inverse_C_squared_F_minus2", "fitted_inverse_C_squared_F_minus2"),
                                  "frequency_group"),),
                         ("The desktop potential-EIS engine computes corrected storage capacitance and strict candidate windows. Candidate intercepts require semiconductor context and reference validation.",))


def arrhenius(temperatures, positive_rates, *, unit="K", rate_unit, labels=None):
    if unit not in ("K", "°C"):
        raise ValueError("Temperature unit must be K or °C.")
    if not str(rate_unit).strip():
        raise ValueError("Enter the common unit of the kinetic observable.")
    t = np.array([_number(value, "Temperature") for value in temperatures], dtype=float)
    if unit == "°C":
        t += 273.15
    if (t <= 0).any():
        raise ValueError("Absolute temperatures must be greater than zero Kelvin.")
    rates = np.array([_positive(value, "Kinetic observable") for value in positive_rates])
    if len(t) != len(rates) or len(t) < 3 or labels is not None and len(labels) != len(t):
        raise ValueError("Enter at least three matched temperatures and kinetic observables.")
    logarithm = np.log(rates)
    _varying(logarithm, "Log kinetic observable")
    result = _regression(1 / t, logarithm, "inverse_temperature_K_minus1", "ln_rate_re_entered_unit",
                         notes=("Uses ln(observable) = ln(prefactor) − Ea/(RT). Use comparable, positive kinetic observables in the same units and regime. The activation energy retains its apparent sign.",))
    result.metrics["apparent_activation_energy_kJ_mol"] = -result.metrics["slope"] * R / 1000
    prefactor = float(np.exp(result.metrics["intercept"])) if result.metrics["intercept"] < math.log(np.finfo(float).max) else None
    result.metrics.update(rate_unit=str(rate_unit).strip(), prefactor_in_rate_unit=prefactor)
    result.tables["Regression"] = pd.DataFrame([result.metrics])
    result.tables["Temperature series"] = pd.DataFrame({"measurement": labels if labels is not None else list(range(1, len(t) + 1)),
                                                        "temperature_K": t, "rate_in_entered_unit": rates})
    return result


def rotating_disk_series(records, *, method, area_cm2, concentration_mol_cm3, diffusion_cm2_s,
                         kinematic_viscosity_cm2_s, potential_V, polarity, branch=1, direction=None,
                         reference_electrons=None, common_potential_reference=None, potential_offsets_V=None):
    if method not in ("Levich", "Koutecky–Levich"):
        raise ValueError("Select Levich or Koutecky–Levich.")
    if polarity not in ("anodic", "cathodic"):
        raise ValueError("Select a common current polarity.")
    if len(records) < 3:
        raise ValueError("Select at least three measurements at distinct positive rotation speeds.")
    area = _positive(area_cm2, "Disk area (cm²)")
    concentration = _positive(concentration_mol_cm3, "Bulk concentration (mol/cm³)")
    diffusion = _positive(diffusion_cm2_s, "Diffusion coefficient (cm²/s)")
    viscosity = _positive(kinematic_viscosity_cm2_s, "Kinematic viscosity (cm²/s)")
    bias = _number(potential_V, "Common potential (V)")
    normalized, reference = _potential_series(records, common_potential_reference, potential_offsets_V)
    b_per_electron = 0.62 * F * area * concentration * diffusion ** (2 / 3) * viscosity ** (-1 / 6)
    rows = []
    for name, frame, rpm, _ in normalized:
        rpm = _positive(rpm, f"Rotation speed for {name} (rpm)")
        _, _, selected = _slice_potential(frame, branch, direction)
        current = _current_at_potential(selected, bias, name)
        if current * (1 if polarity == "anodic" else -1) <= 0:
            raise ValueError(f"{name}: current at the selected potential does not have the declared polarity.")
        rows.append((name, rpm, 2 * np.pi * rpm / 60, current))
    observations = pd.DataFrame(rows, columns=["measurement", "rotation_rpm", "omega_rad_s", "signed_current_A"])
    omega_sqrt = np.sqrt(observations.omega_rad_s.to_numpy())
    current = np.abs(observations.signed_current_A.to_numpy())
    _varying(current, "Rotating-disk current")
    if method == "Levich":
        result = _regression(omega_sqrt, current, "sqrt_omega_rad_per_s", "current_magnitude_A",
                             notes=("Levich assumes rotating-disk transport-limited current at the selected common potential. The free intercept is a diagnostic.",))
        b = _positive(result.metrics["slope"], "Levich slope")
    else:
        result = _regression(1 / omega_sqrt, 1 / current, "inverse_sqrt_omega", "inverse_current_magnitude_A_minus1",
                             notes=("Koutecky–Levich assumes inverse currents add for kinetic and transport contributions at a common potential. A finite kinetic current requires an identifiable positive intercept.",))
        b = 1 / _positive(result.metrics["slope"], "Koutecky–Levich slope")
        intercept = result.metrics["intercept"]
        x = 1 / omega_sqrt
        n = len(x)
        intercept_se = result.metrics["rmse"] * np.sqrt(n / (n - 2) * (1 / n + np.mean(x) ** 2 / np.sum((x - np.mean(x)) ** 2)))
        threshold = max(2 * intercept_se, np.finfo(float).eps * np.max(1 / current) * 16)
        result.metrics["kinetic_current_magnitude_A"] = 1 / intercept if intercept > threshold else None
        result.metrics["kinetic_current_status"] = "estimated" if intercept > threshold else "positive intercept unresolved"
    result.metrics.update(method=method, apparent_electrons=b / b_per_electron, fitted_levich_B=b, potential_V=bias,
                          potential_reference=reference)
    if reference_electrons is not None:
        result.metrics["reference_levich_B"] = _electrons(reference_electrons) * b_per_electron
    result.tables["Regression"] = pd.DataFrame([result.metrics])
    result.tables["Rotation series"] = observations
    return result
