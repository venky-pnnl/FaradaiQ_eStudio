"""Unit-aware curve processing and comparison for the local workbench."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
from tempfile import NamedTemporaryFile, mkdtemp

import numpy as np
import pandas as pd
from scipy import stats
from scipy.constants import R as _R_GAS, physical_constants
from scipy.signal import find_peaks, savgol_filter

from .browser_analysis import _UNITS, _current_density_header, measurement_roles


_UNIT_TRANSLATION = str.maketrans({'µ': 'u', 'μ': 'u', 'Ω': 'ohm', '−': '-', '⁻': '-', '²': '2', '°': 'deg'})
_BASE_UNITS = {'potential': 'V', 'current': 'A', 'density': 'A/cm2', 'time': 's',
               'frequency': 'Hz', 'impedance': 'ohm', 'length': 'm', 'charge': 'C',
               'capacitance': 'F', 'temperature': 'degC', 'count': 'counts', 'arbitrary': 'a.u.',
               'percentage': '%', 'dimensionless': '1'}
_UNIT_SCALES = {quantity: _UNITS[role] for quantity, role in (
    ('potential', 'potential'), ('current', 'current'), ('time', 'time'), ('frequency', 'frequency'),
    ('impedance', 'zreal'), ('length', 'x'))}
_UNIT_SCALES.update({
    'density': {f'{current}/{area}2': scale / area_scale for current, scale in _UNITS['current'].items()
                for area, area_scale in [('cm', 1.), ('mm', .01), ('m', 1e4)]},
    'charge': {'C': 1., 'mC': 1e-3, 'uC': 1e-6, 'nC': 1e-9},
    'capacitance': {'F': 1., 'mF': 1e-3, 'uF': 1e-6, 'nF': 1e-9, 'pF': 1e-12},
    'temperature': {'degC': 1.}, 'count': {'counts': 1., 'count': 1.}, 'arbitrary': {'a.u.': 1., 'au': 1.},
    'percentage': {'%': 1.}, 'dimensionless': {'1': 1.},
})
_DENSITY = re.compile(r'(?<![a-zA-Z])([munpf]?A)(?:\s*[/]\s*(cm|mm|m)\^?2|[\s_*]*(cm|mm|m)\^?-2)(?![a-zA-Z0-9])')
_FARADAY = physical_constants['Faraday constant'][0]


@dataclass(frozen=True)
class Axis:
    quantity: str
    unit: str
    label: str
    reference: str = ''

    @property
    def signature(self):
        return self.quantity, self.unit, self.reference.strip().casefold()

    @property
    def title(self):
        reference = f' vs {self.reference}' if self.reference else ''
        return f'{self.label} ({self.unit}{reference})'


@dataclass(frozen=True)
class Curve:
    frame: pd.DataFrame
    x: Axis
    y: Axis


@dataclass(frozen=True)
class Recipe:
    x: str
    y: str
    x_unit: str = ''
    y_unit: str = ''
    cycle_column: str = ''
    cycles: tuple[str, ...] = ()
    excluded_rows: tuple[int, ...] = ()
    window: tuple[float, float] | None = None
    current_output: str = 'as recorded'
    area_cm2: float | None = None
    source_reference: str = ''
    target_reference: str = ''
    source_she_v: float | None = None
    target_she_v: float | None = None
    ph: float | None = None
    temperature_c: float | None = None
    resistance_ohm: float | None = None
    current_column: str = ''
    current_unit: str = ''
    baseline: str = 'none'
    baseline_window: tuple[float, float] | None = None
    baseline_degree: int = 2
    smoothing: str = 'none'
    smoothing_window: int = 5
    smoothing_degree: int = 2


def recipe_from_dict(values):
    """Read persisted settings with JSON arrays restored to immutable sequences."""
    allowed = {field.name for field in fields(Recipe)}
    if set(values) - allowed:
        raise ValueError('The recipe contains unsupported settings.')
    values = dict(values)
    for key in ('cycles', 'excluded_rows', 'window', 'baseline_window'):
        if values.get(key) is not None:
            values[key] = tuple(values[key])
    return Recipe(**values)


def _axis(column, specified=''):
    header = str(column).translate(_UNIT_TRANSLATION).strip().replace('Ohm', 'ohm')
    if specified:
        unit_text = str(specified).translate(_UNIT_TRANSLATION).strip().replace('Ohm', 'ohm')
        density = _DENSITY.fullmatch(unit_text)
        tokens = [unit_text]
    else:
        density = _DENSITY.search(header)
        measurement = re.split(r'\s+vs\.?\s+', header, maxsplit=1, flags=re.I)[0].strip()
        last_token = re.split(r'[\s_/:\[\]()]+', measurement.rstrip('])'))[-1]
        tokens = [last_token] if last_token != '1' or re.search(r'\[1\]|\(1\)', measurement) else []
    if density:
        tokens = [f'{density[1]}/{density[2] or density[3]}2']
    elif not specified and _current_density_header(header):
        raise ValueError(f'{column!r} requires an explicit current density unit such as mA/cm2.')
    # Source metadata can override an absent or ambiguous header unit explicitly.
    for quantity, units in _UNIT_SCALES.items():
        for token in tokens:
            if token in units:
                label = {'potential': 'Potential', 'current': 'Current', 'density': 'Current density',
                         'time': 'Time', 'frequency': 'Frequency', 'charge': 'Charge',
                         'capacitance': 'Capacitance', 'temperature': 'Temperature'}.get(quantity)
                if label is None:
                    label = re.sub(r'[\s_:/\[(]+' + re.escape(token) + r'[\])]*$', '', header).strip()
                return Axis(quantity, _BASE_UNITS[quantity], label or header), units[token]
    raise ValueError(f'{column!r} has no recognized unit. Enter its measured unit explicitly.')


def _values(table, column, specified):
    if column not in table.columns:
        raise ValueError(f'Column {column!r} is absent from this table.')
    axis, scale = _axis(column, specified)
    values = pd.to_numeric(table[column], errors='coerce').to_numpy(dtype=float, na_value=np.nan)
    return axis, values * scale


def _area(area):
    if area is None or not np.isfinite(area) or area <= 0:
        raise ValueError('Enter a finite electrode area greater than 0 cm² to convert current and current density.')
    return area


def reference_potential(reference, she_offset=None, *, ph=None, temperature_c=None):
    """Reference electrode potential in V vs SHE under the supplied conditions."""
    if reference.strip().casefold() == 'she':
        return 0.
    if reference.strip().casefold() == 'rhe':
        if ph is None or not np.isfinite(ph) or temperature_c is None or not np.isfinite(temperature_c):
            raise ValueError('Conversion involving RHE requires measured pH and temperature in °C.')
        if temperature_c <= -273.15:
            raise ValueError('RHE temperature must be above absolute zero.')
        return -np.log(10.) * _R_GAS * (temperature_c + 273.15) / _FARADAY * ph
    if not reference.strip() or she_offset is None or not np.isfinite(she_offset):
        raise ValueError('Name the reference electrode and supply its potential versus SHE in V.')
    return float(she_offset)


def mask_rows(frame, rows):
    """Mark exclusions by original, one-based acquisition row number."""
    if any(isinstance(row, bool) or not isinstance(row, (int, np.integer)) for row in rows):
        raise ValueError('Excluded row numbers must be integers.')
    absent = sorted(set(rows).difference(frame['row']))
    if absent:
        raise ValueError(f'Excluded row numbers outside this table: {absent[:10]}.')
    return frame.loc[~frame['row'].isin(rows)].copy()


def window_curve(curve, interval):
    lower, upper = interval
    if not np.isfinite([lower, upper]).all() or lower >= upper:
        raise ValueError('Enter finite plot bounds with minimum below maximum.')
    frame = curve.frame.loc[curve.frame['x'].between(lower, upper)].copy()
    if frame.empty:
        raise ValueError('The selected x interval contains no measurements.')
    return Curve(frame, curve.x, curve.y)


def local_peak_points(x, y, *, maxima=True, prominence=None, interval=None):
    """Find measured local extrema in acquisition order, without spanning missing samples."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if x.ndim != 1 or x.shape != y.shape:
        raise ValueError('Peak labels require matching x and y curves.')
    valid = np.flatnonzero(np.isfinite(x) & np.isfinite(y))
    blocks = np.split(valid, np.flatnonzero(np.diff(valid) != 1) + 1)
    signal = y if maxima else -y
    indexes = [int(block[index]) for block in blocks for index in find_peaks(signal[block], prominence=prominence)[0]]
    return [index for index in indexes if interval is None or interval[0] <= x[index] <= interval[1]]


def _cycle_blocks(frame):
    if 'cycle' not in frame:
        return [(None, frame)]
    groups = frame['cycle'].ne(frame['cycle'].shift()).fillna(True).cumsum()
    return [(block['cycle'].iloc[0], block) for _, block in frame.groupby(groups, sort=False)]


def select_cycles(curve, cycles):
    if not cycles:
        return curve
    if 'cycle' not in curve.frame:
        raise ValueError('Choose a cycle column before selecting cycles.')
    absent = sorted(set(cycles) - set(curve.frame['cycle']))
    if absent:
        raise ValueError(f'Cycles missing from this table: {", ".join(absent)}.')
    return Curve(curve.frame[curve.frame['cycle'].isin(cycles)].copy(), curve.x, curve.y)


def baseline_values(x, y, interval, degree):
    """Fit the supplied baseline interval using a scaled Vandermonde system."""
    if interval is None or len(interval) != 2 or not np.isfinite(interval).all() or interval[0] >= interval[1]:
        raise ValueError('Supply an increasing, finite x interval for baseline fitting.')
    if not 1 <= degree <= 8 or not isinstance(degree, (int, np.integer)):
        raise ValueError('Polynomial baseline degree must be an integer from 1 through 8.')
    selected = (x >= interval[0]) & (x <= interval[1])
    x_fit, y_fit = x[selected], y[selected]
    if len(np.unique(x_fit)) <= degree:
        raise ValueError(f'The baseline interval requires at least {degree + 1} distinct x values per cycle.')
    center = x_fit.mean()
    scale = np.ptp(x_fit)
    design = np.polynomial.polynomial.polyvander((x_fit - center) / scale, degree)
    coefficients, _, rank, _ = np.linalg.lstsq(design, y_fit, rcond=None)
    if rank != degree + 1:
        raise ValueError('The baseline fit is rank-deficient in the selected interval.')
    values = np.polynomial.polynomial.polyval((x - center) / scale, coefficients)
    if not np.isfinite(values).all():
        raise ValueError('The baseline prediction exceeds the numeric range of this data.')
    return values


def smooth_values(y, method, window, degree=2):
    """Smooth consecutive acquisition samples, using smaller moving windows at edges."""
    if not isinstance(window, (int, np.integer)) or window < 3 or window % 2 != 1 or window > len(y):
        raise ValueError('The smoothing window must be odd, at least 3, and no longer than each selected cycle.')
    if method == 'Moving average':
        return pd.Series(y).rolling(window, center=True, min_periods=1).mean().to_numpy()
    if method == 'Savitzky–Golay':
        if not isinstance(degree, (int, np.integer)) or degree < 0 or degree >= window:
            raise ValueError('Savitzky–Golay degree must be lower than the smoothing window.')
        return savgol_filter(y, window_length=window, polyorder=degree, mode='interp')
    raise ValueError(f'Unknown smoothing method: {method}.')


def prepare_curve(table, recipe):
    """Build a numeric curve in canonical units, retaining its one-based acquisition rows."""
    if not table.columns.is_unique or recipe.x == recipe.y:
        raise ValueError('Choose two distinct columns from a table with unique column names.')
    x, xv = _values(table, recipe.x, recipe.x_unit)
    y, yv = _values(table, recipe.y, recipe.y_unit)
    frame = pd.DataFrame({'row': np.arange(1, len(table) + 1), 'x': xv, 'y': yv})
    if recipe.cycle_column:
        if recipe.cycle_column not in table:
            raise ValueError(f'Cycle column {recipe.cycle_column!r} is absent from this table.')
        frame['cycle'] = table[recipe.cycle_column].astype('string').array
    def label_reference(axis, column):
        if axis.quantity != 'potential':
            return axis
        explicit = re.search(r'\s+vs\.?\s+(.+)$', column, re.I)
        reference = recipe.source_reference.strip() or (explicit[1].rstrip('])').strip() if explicit else '')
        return Axis(axis.quantity, axis.unit, axis.label, reference)
    x, y = label_reference(x, recipe.x), label_reference(y, recipe.y)
    return Curve(frame, x, y)


def process_curve(table, recipe):
    """Apply masks, cycle selection, potential corrections, area, baseline and smoothing."""
    raw = prepare_curve(table, recipe)
    frame = mask_rows(raw.frame, recipe.excluded_rows)
    curve = select_cycles(Curve(frame, raw.x, raw.y), recipe.cycles)
    frame = curve.frame.copy()
    if frame.empty:
        raise ValueError('No measurements remain after applying row and cycle selections.')
    invalid = ~np.isfinite(frame[['x', 'y']].to_numpy()).all(axis=1)
    if 'cycle' in frame:
        invalid |= frame['cycle'].isna().to_numpy()
    if invalid.any():
        rows = frame.loc[invalid, 'row'].tolist()
        raise ValueError(f'Mask rows with missing or nonnumeric values before processing: {rows[:10]}.')

    axes = {'x': curve.x, 'y': curve.y}
    if recipe.target_reference or recipe.resistance_ohm is not None:
        if not any(axis.quantity == 'potential' for axis in axes.values()):
            raise ValueError('Potential corrections require a potential axis.')
        correction = 0.
        if recipe.resistance_ohm is not None:
            resistance = recipe.resistance_ohm
            if not np.isfinite(resistance) or resistance < 0:
                raise ValueError('Uncompensated resistance must be finite and at least 0 ohm.')
            selected = recipe.current_column or next((getattr(recipe, name) for name, axis in axes.items()
                                                       if axis.quantity in {'current', 'density'}), '')
            if not selected:
                raise ValueError('Select the measured current column for iR correction.')
            unit = recipe.current_unit
            if not unit and selected in {recipe.x, recipe.y}:
                unit = recipe.x_unit if selected == recipe.x else recipe.y_unit
            current_axis, current = _values(table, selected, unit)
            if current_axis.quantity not in {'current', 'density'}:
                raise ValueError('iR correction requires a current or current density column.')
            if current_axis.quantity == 'density':
                current *= _area(recipe.area_cm2)
            measured = current[frame['row'].to_numpy() - 1]
            if not np.isfinite(measured).all():
                raise ValueError('Mask rows with missing measured current before applying iR correction.')
            correction = correction - measured * resistance
        for name, axis in axes.items():
            if axis.quantity == 'potential':
                shift = correction
                if recipe.target_reference and recipe.target_reference.strip().casefold() != axis.reference.casefold():
                    settings = {'ph': recipe.ph, 'temperature_c': recipe.temperature_c}
                    shift = (shift + reference_potential(axis.reference, recipe.source_she_v, **settings)
                             - reference_potential(recipe.target_reference, recipe.target_she_v, **settings))
                frame[name] += shift
                axes[name] = Axis(axis.quantity, axis.unit, axis.label, recipe.target_reference.strip() or axis.reference)

    if recipe.current_output not in {'as recorded', 'current', 'current density'}:
        raise ValueError('Select current, current density, or as recorded.')
    if recipe.current_output != 'as recorded':
        if not any(axis.quantity in {'current', 'density'} for axis in axes.values()):
            raise ValueError('Current conversion requires a current or current density axis.')
        for name, axis in axes.items():
            if recipe.current_output == 'current density' and axis.quantity == 'current':
                frame[name] /= _area(recipe.area_cm2)
                axes[name] = Axis('density', 'A/cm2', 'Current density')
            if recipe.current_output == 'current' and axis.quantity == 'density':
                frame[name] *= _area(recipe.area_cm2)
                axes[name] = Axis('current', 'A', 'Current')

    curve = Curve(frame, axes['x'], axes['y'])
    if not np.isfinite(frame[['x', 'y']].to_numpy()).all():
        raise ValueError('Curve correction exceeds the numeric range of the measurement.')
    if recipe.baseline not in {'none', 'Linear', 'Polynomial'}:
        raise ValueError('Choose a supported baseline method.')
    for _, block in _cycle_blocks(frame):
        y = block['y'].to_numpy()
        if recipe.baseline != 'none':
            degree = 1 if recipe.baseline == 'Linear' else recipe.baseline_degree
            baseline = baseline_values(block['x'].to_numpy(), y, recipe.baseline_window, degree)
            frame.loc[block.index, 'baseline_y'] = baseline
            y = y - baseline
        if recipe.smoothing != 'none':
            y = smooth_values(y, recipe.smoothing, recipe.smoothing_window, recipe.smoothing_degree)
        frame.loc[block.index, 'y'] = y
    if not np.isfinite(frame[['x', 'y']].to_numpy()).all():
        raise ValueError('Curve correction exceeds the numeric range of the measurement.')
    return window_curve(curve, recipe.window) if recipe.window is not None else curve


def sweep_branches(curve):
    """Split one continuous cycle into monotonic sweeps, sharing each reversal row."""
    blocks = _cycle_blocks(curve.frame)
    branches = []
    for cycle, frame in blocks:
        x = frame['x'].to_numpy()
        if len(x) < 2 or not np.isfinite(x).all():
            continue
        signs = np.sign(np.diff(x))
        nonzero = np.flatnonzero(signs)
        if not len(nonzero):
            continue
        start, direction = 0, signs[nonzero[0]]
        sweeps = []
        for index in nonzero:
            if signs[index] != direction:
                sweeps.append((start, index + 1, direction))
                start, direction = index, signs[index]
        sweeps.append((start, len(frame), direction))
        prefix = f'Cycle {cycle} · ' if cycle is not None else ''
        for position, (start, end, sign) in enumerate(sweeps, 1):
            direction_text = 'increasing x' if sign > 0 else 'decreasing x'
            part = frame.iloc[start:end].copy()
            first, last = part['row'].iloc[[0, -1]]
            label = f'{prefix}Sweep {position} ({direction_text}, rows {first}–{last})'
            branches.append((label, Curve(part, curve.x, curve.y)))
    return branches


def _monotonic(curve):
    if len(_cycle_blocks(curve.frame)) > 1:
        raise ValueError('Select a single cycle and sweep branch before interpolation.')
    x, y = curve.frame[['x', 'y']].to_numpy().T
    if len(x) < 2 or not np.isfinite([x, y]).all():
        raise ValueError('Interpolation requires at least two finite measurements per curve.')
    dx = np.diff(x)
    if not ((dx > 0).all() or (dx < 0).all()):
        if (dx > 0).any() and (dx < 0).any():
            raise ValueError('Select one sweep branch before interpolating a reversing curve.')
        raise ValueError('Interpolation requires unique x values. Mask repeated x rows explicitly.')
    return (x, y) if dx[0] > 0 else (x[::-1], y[::-1])


def require_compatible(curves, *, x=True, y=True, known_reference=False):
    if not curves:
        raise ValueError('Select curves to compare.')
    if ((x and len({curve.x.signature for curve in curves}) != 1)
            or (y and len({curve.y.signature for curve in curves}) != 1)):
        raise ValueError('Selected curves require compatible quantities, units and potential reference labels.')
    if known_reference:
        axes = [axis for curve in curves for enabled, axis in ((x, curve.x), (y, curve.y)) if enabled]
        if any(axis.quantity == 'potential' and not axis.reference for axis in axes):
            raise ValueError('Numerical comparison across independent sources requires compatible potential reference labels.')


def align_curves(curves):
    """Use measured x points from the first curve inside the common domain only."""
    require_compatible(curves)
    samples = [_monotonic(curve) for curve in curves]
    lower = max(x[0] for x, _ in samples)
    upper = min(x[-1] for x, _ in samples)
    first = curves[0].frame['x'].to_numpy()
    grid = first[(first >= lower) & (first <= upper)]
    if len(grid) < 2:
        raise ValueError('At least two measured x points from the first curve must lie in the common domain.')
    return grid, np.asarray([np.interp(grid, x, y) for x, y in samples])


def compare_curves(first, second, operation):
    x, (a, b) = align_curves([first, second])
    if operation == 'Difference':
        result, axis = a - b, first.y
    elif operation == 'Ratio':
        if np.any(b == 0):
            raise ValueError('The denominator is zero within the common domain. Choose a different interval or comparison.')
        result, axis = a / b, Axis('dimensionless', '1', 'Ratio')
    else:
        raise ValueError('Choose Difference or Ratio.')
    if not np.isfinite(result).all():
        raise ValueError('The comparison exceeds the numeric range of the measurement.')
    return Curve(pd.DataFrame({'x': x, 'y': result}), first.x, axis)


def _confidence(confidence):
    if not 0 < confidence < 1:
        raise ValueError('Confidence level must be strictly between 0 and 1.')


def summarize_replicates(curves, confidence=.95):
    """Compute pointwise sample SD and Student t confidence bounds for the mean."""
    _confidence(confidence)
    if len(curves) < 2:
        raise ValueError('Select at least two independent replicate curves.')
    x, values = align_curves(curves)
    n = len(values)
    mean = np.mean(values, axis=0)
    sd = np.std(values, axis=0, ddof=1)
    margin = stats.t.ppf((1 + confidence) / 2, n - 1) * sd / np.sqrt(n)
    return pd.DataFrame({'x': x, 'mean': mean, 'sd': sd, 'ci_low': mean - margin, 'ci_high': mean + margin, 'n': n})


def average_cycles(curve, sweep, confidence=.95):
    """Summarize the same ordinal sweep from each explicitly selected cycle."""
    blocks = _cycle_blocks(curve.frame)
    if len(blocks) < 2 or any(cycle is None for cycle, _ in blocks):
        raise ValueError('Select a cycle column and at least two cycles before averaging.')
    if len({cycle for cycle, _ in blocks}) != len(blocks):
        raise ValueError('Cycle identifiers must each span one contiguous acquisition block.')
    matched = []
    directions = set()
    for cycle, frame in blocks:
        branches = sweep_branches(Curve(frame, curve.x, curve.y))
        if not isinstance(sweep, int) or not 1 <= sweep <= len(branches):
            raise ValueError(f'Sweep {sweep} is absent from cycle {cycle!r}.')
        part = branches[sweep - 1][1]
        directions.add(np.sign(part.frame['x'].iloc[-1] - part.frame['x'].iloc[0]))
        matched.append(part)
    if len(directions) != 1:
        raise ValueError('Select sweep branches with the same direction across cycles.')
    return summarize_replicates(matched, confidence)


def treatment_effect(control, treatment, *, paired=False, confidence=.95):
    """Return treatment-minus-control mean difference and paired or Welch t inference."""
    _confidence(confidence)
    control, treatment = (np.asarray(values, dtype=float) for values in (control, treatment))
    if any(values.ndim != 1 or len(values) < 2 or not np.isfinite(values).all()
           for values in (control, treatment)):
        raise ValueError('Each group requires at least two finite, independent observations.')
    if paired and len(control) != len(treatment):
        raise ValueError('Paired groups must contain the same number of explicitly matched observations.')
    difference = float(treatment.mean() - control.mean())
    if paired:
        delta = treatment - control
        se = float(stats.sem(delta))
        df = len(delta) - 1
    else:
        variances = np.array([control.var(ddof=1) / len(control), treatment.var(ddof=1) / len(treatment)])
        se = float(np.sqrt(variances.sum()))
        weights = variances / variances.sum() if se else np.zeros(2)
        df = float(1 / np.sum(weights**2 / [len(control) - 1, len(treatment) - 1])) if se else np.inf
    if not np.isfinite([difference, se]).all():
        raise ValueError('Group statistics exceed the numeric range of the supplied measurements.')
    if se:
        p = float(2 * stats.t.sf(abs(difference / se), df))
        margin = float(stats.t.ppf((1 + confidence) / 2, df) * se)
    else:
        p, margin = None, 0.
    return {'method': 'Paired t' if paired else 'Welch t', 'n_control': len(control), 'n_treatment': len(treatment),
            'control_mean': float(control.mean()), 'treatment_mean': float(treatment.mean()),
            'difference': difference, 'standard_error': se, 'confidence': confidence,
            'ci_low': difference - margin, 'ci_high': difference + margin, 'p_value': p,
            'inference_note': 'Zero estimated standard error leaves the t p-value undefined.' if se == 0 else ''}


def propagate_uncertainty(sensitivities, covariance):
    """Compute J Σ Jᵀ and its standard uncertainties for one or more outputs."""
    if np.ndim(sensitivities) == 0:
        raise ValueError('Enter one sensitivity vector per output.')
    jacobian = np.atleast_2d(np.asarray(sensitivities, dtype=float))
    cov = np.asarray(covariance, dtype=float)
    if (jacobian.ndim != 2 or not all(jacobian.shape) or cov.shape != (jacobian.shape[1], jacobian.shape[1])
            or not np.isfinite(jacobian).all() or not np.isfinite(cov).all()):
        raise ValueError('Supply finite sensitivities and a square covariance matrix with matching dimensions.')
    tolerance = np.max(np.abs(cov)) * np.finfo(float).eps * 100 * len(cov)
    if not np.allclose(cov, cov.T, rtol=0, atol=tolerance):
        raise ValueError('The input covariance matrix must be symmetric.')
    cov = (cov + cov.T) / 2
    if np.linalg.eigvalsh(cov).min() < -tolerance:
        raise ValueError('The input covariance matrix must be positive semidefinite.')
    result = jacobian @ cov @ jacobian.T
    if not np.isfinite(result).all():
        raise ValueError('Propagated covariance exceeds the numeric range of the supplied values.')
    return result, np.sqrt(np.maximum(result.diagonal(), 0))


def metadata_tags(metadata):
    """Read structured metadata tags from either a mapping or key=value strings."""
    tags = metadata.get('tags', ())
    if isinstance(tags, Mapping):
        return {str(key).strip().casefold(): str(value).strip() for key, value in tags.items()
                if str(key).strip() and value is not None and str(value).strip()}
    if isinstance(tags, str):
        tags = tags.split(',')
    pairs = (str(tag).partition('=') for tag in (tags or ()))
    return {key.strip().casefold(): value.strip() for key, separator, value in pairs
            if separator and key.strip() and value.strip()}


def legend_label(path, metadata, fields):
    """Build a figure label from explicitly selected source metadata."""
    aliases = {'instrument': ('instrument', 'instrument_name', 'source_instrument'),
               'sample': ('sample', 'sample_name', 'specimen'),
               'potential': ('potential', 'potential_V', 'bias_V'),
               'cycle': ('cycle', 'cycle_index'), 'tags': ('tags',),
               'file': ('display_name', 'title')}
    values = []
    tags = metadata_tags(metadata)
    for field in fields:
        value = next((metadata[key] for key in aliases.get(field, ())
                      if key in metadata and metadata[key] is not None and metadata[key] != ''), None)
        if value is None and field == 'file':
            value = Path(path).stem
        if value is None:
            value = next((tags[key] for key in aliases.get(field, ()) if key in tags), None)
        if isinstance(value, Mapping):
            value = ', '.join(f'{key}={item}' for key, item in value.items())
        if isinstance(value, (list, tuple, set)):
            value = ', '.join(map(str, value))
        if value is not None and value != '':
            values.append(str(value))
    return ' · '.join(values) or Path(path).stem


def comparison_key(value):
    return sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def measurement_key(source, path, handle_id=None, relative_path=None):
    """Use the catalog identity for a project measurement and file identity otherwise."""
    if handle_id and relative_path is not None:
        return comparison_key([handle_id, relative_path])
    return comparison_key([str(source), str(path), handle_id])


def update_history(history, cursor, *, recipe=None, step=0):
    """Advance a per-source processing history or move its current position."""
    history = list(history)
    if recipe is not None and recipe != history[cursor]:
        history = [*history[:cursor + 1], recipe]
        return history, cursor + 1
    return history, max(0, min(cursor + step, len(history) - 1))


def _atomic_json(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(contents, stream, indent=2, ensure_ascii=False, allow_nan=False, default=str)
            stream.write('\n')
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def save_named(workspace, kind, name, settings):
    """Save a named style or batch recipe as JSON in the current workspace."""
    if kind not in {'styles', 'recipes'}:
        raise ValueError('Choose a style or recipe collection.')
    name = name.strip()
    if not name:
        raise ValueError('Enter a name before saving.')
    key = sha256(name.encode('utf-8')).hexdigest()[:24]
    return _atomic_json(Path(workspace) / 'Workbench' / 'Compare' / kind / f'{key}.json',
                        {'name': name, 'settings': settings})


def load_named(workspace, kind):
    if kind not in {'styles', 'recipes'}:
        raise ValueError('Choose a style or recipe collection.')
    folder = Path(workspace) / 'Workbench' / 'Compare' / kind
    items = {}
    for path in sorted(folder.glob('*.json')):
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(document['name'], str) and isinstance(document['settings'], dict):
                items[document['name']] = document['settings']
        except (ValueError, TypeError, KeyError, OSError):
            continue
    return dict(sorted(items.items()))


def write_derived(workspace, frame, *, operation, settings, sources, curve=None):
    """Write a unique CSV with synchronized settings and original source provenance."""
    parent = Path(workspace) / 'Workbench' / 'Compare' / 'results'
    parent.mkdir(parents=True, exist_ok=True)
    folder = Path(mkdtemp(prefix='curve-', dir=parent))
    csv, settings_path = folder / 'data.csv', folder / 'settings.json'
    document = {'operation': operation, 'settings': settings, 'sources': sources}
    if curve is not None:
        document['axes'] = {'x': asdict(curve.x), 'y': asdict(curve.y)}
        names = {'x': _export_column('x', curve.x), 'y': _export_column('y', curve.y),
                 'baseline_y': f'baseline_{_export_column("y", curve.y)}',
                 **{name: f'{name}_{_export_column("y", curve.y)}' for name in ('mean', 'sd', 'ci_low', 'ci_high')}}
        frame = frame.rename(columns=names)
    try:
        frame.to_csv(csv, index=False)
        _atomic_json(settings_path, document)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return csv, settings_path


def _export_column(axis_name, axis):
    label = axis.label if axis.quantity not in {'impedance', 'length'} else axis_name
    return f'{axis_name}_{label.lower().replace(" ", "_")}_{axis.unit}'


def source_details(path, metadata):
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return {'table_path': str(path), **{key: metadata.get(key) for key in
            ('source_path', 'original_table_path', 'instrument', 'handle_id', 'display_name', 'tags',
             'duplicate_source_handle_id') if key in metadata}}


def require_independent_acquisitions(sources):
    """Reject multiple representations of one original measurement in inference."""
    seen = set()
    for source in sources:
        acquisition = str(source.get('original_table_path') or source['table_path'])
        if acquisition in seen:
            raise ValueError('Select one measurement per original acquisition for statistical inference.')
        seen.add(acquisition)


def run_batch(sources, *, read_table, recipe, workspace, metadata):
    """Run a reviewed recipe on each source, returning independent success and failure rows."""
    statuses = []
    for path in sources:
        provenance = source_details(path, metadata.get(path, {}))
        row = {**provenance, 'status': 'failed', 'data_csv': '', 'settings_json': '', 'message': ''}
        try:
            curve = process_curve(read_table(path), recipe)
            csv, settings = write_derived(workspace, curve.frame, operation='Batch curve processing',
                                          settings=asdict(recipe), sources=[provenance], curve=curve)
            row.update(status='saved', data_csv=str(csv), settings_json=str(settings))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            row['message'] = str(exc)
        statuses.append(row)
    return pd.DataFrame(statuses)


def default_recipe(table):
    """Prefer native electrochemical axes while keeping all columns available for selection."""
    roles = measurement_roles(table)
    columns = list(map(str, table.columns))
    if len(columns) < 2:
        raise ValueError('Curve processing requires at least two table columns.')
    x = roles.get('potential') or roles.get('time') or roles.get('frequency') or columns[0]
    y = roles.get('current') or roles.get('zreal') or roles.get('potential')
    if y is None or x == y:
        y = next(column for column in columns if column != x)
    cycle = next((column for column in columns if re.fullmatch(r'cycle(?:[ _-]+(?:number|index|id))?', column, re.I)), '')
    return Recipe(str(x), str(y), cycle_column=cycle)
