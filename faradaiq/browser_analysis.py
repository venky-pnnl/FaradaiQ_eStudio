"""DataFrame entry points for the existing eStudio analysis engines."""
from __future__ import annotations

from collections.abc import Mapping
import csv
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
from tempfile import TemporaryDirectory, mkdtemp

import numpy as np
import pandas as pd

from . import v228_intelligence as intelligence
from . import v290_deterministic_electrochemistry as deterministic
from .drt.runner import build_headless_drt_command, headless_drt_script
from .multidim_v216 import _column_roles
from .release_identity import APP_VERSION

EIS_COLUMNS = ('frequency_Hz', 'Zreal_ohm', 'Zimag_ohm')
CIRCUITS = ('R', 'R-RC', 'R-CPE', 'R-RC-W')
DRT_MIN_BAYESIAN_SAMPLES = 502

_UNITS = {
    'potential': {'V': 1.0, 'mV': 1e-3, 'uV': 1e-6},
    'current': {'A': 1.0, 'mA': 1e-3, 'uA': 1e-6, 'nA': 1e-9, 'pA': 1e-12, 'fA': 1e-15},
    'time': {'s': 1.0, 'ms': 1e-3, 'us': 1e-6, 'min': 60.0, 'h': 3600.0},
    'frequency': {'Hz': 1.0, 'mHz': 1e-3, 'kHz': 1e3, 'MHz': 1e6},
    'zreal': {'ohm': 1.0, 'mohm': 1e-3, 'kohm': 1e3, 'Mohm': 1e6},
    'x': {'m': 1.0, 'cm': 1e-2, 'mm': 1e-3, 'um': 1e-6, 'nm': 1e-9},
}
_UNITS['zimag'] = _UNITS['zreal']
_UNITS['y'] = _UNITS['z'] = _UNITS['x']
_ROLE_LABELS = {'time': 'Time', 'potential': 'Potential', 'current': 'Current', 'frequency': 'Frequency',
                'zreal': 'Real impedance', 'zimag': 'Imaginary impedance', 'x': 'X position',
                'y': 'Y position', 'z': 'Z position', 'signal': 'Signal'}
_NORMALIZE_UNITS = str.maketrans({'µ': 'u', 'μ': 'u', 'Ω': 'ohm', '−': '-', '⁻': '-', '²': '2'})


def _current_density_header(column):
    name = str(column).translate(_NORMALIZE_UNITS).casefold()
    return (bool(re.search(r'current[\s_\-]*density|^j(?:\b|_)', name)) or
            bool(re.search(r'a(?:\s|\(|_|\[|\.)*(?:/\s*(?:c|m)?m\s*\^?\s*2|(?:c|m)?m\s*\^?\s*-\s*2|(?:c|m)?m2)', name)))


def measurement_roles(table):
    """Recognize legacy and native short instrument headers in addition to canonical labels."""
    roles = _column_roles(table)
    header_roles = {
        'current': r'^\s*<?i>?\s*[/(_\[]\s*[munpf]?a(?:\b|_)',
        'potential': r'^\s*<?e(?:we)?>?\s*[/(_\[]\s*[mu]?v(?:\b|_)',
        'time': r'^\s*t\s*[/(_\[]\s*(?:m?s|min|h)(?:\b|_)',
    }
    for role, pattern in header_roles.items():
        if roles.get(role) is None or role == 'current' and _current_density_header(roles[role]):
            roles[role] = next((column for column in table.columns if re.match(
                pattern, str(column).translate(_NORMALIZE_UNITS), flags=re.IGNORECASE)
                and (role != 'current' or not _current_density_header(column))), roles.get(role))
    if roles.get('current') is None:
        roles['current'] = next((column for column in table.columns if _current_density_header(column)), None)
    return roles


def _scale(column, role):
    if role == 'current' and _current_density_header(column):
        raise ValueError(f'{column!r} contains current density. Convert it to measured current using the electrode area before analysis.')
    name = str(column).translate(_NORMALIZE_UNITS).strip()
    units = _UNITS.get(role, {})
    for token in reversed(re.split(r'[\s_/\[\]()]+', name)):
        token = token.replace('Ohm', 'ohm')
        if token in units:
            return units[token]
    return 1.0


def _roles_to_frame(table, columns, aliases, required):
    """Make a numeric copy using the selected columns and their explicit units."""
    if not table.columns.is_unique:
        raise ValueError('Select a table with unique column names.')
    detected = measurement_roles(table)
    pairs = []
    for role, name in aliases.items():
        column = columns.get(role)
        if column is None:
            column = detected.get(role)
        if column not in table:
            if role in required:
                raise ValueError(f'{_ROLE_LABELS[role]} column could not be identified. Select it explicitly.')
            continue
        pairs.append((role, name, column))
    if len({column for _, _, column in pairs}) != len(pairs):
        raise ValueError('Select different columns for each measurement quantity.')
    values = {name: pd.to_numeric(table[column], errors='coerce').to_numpy(dtype=float, na_value=np.nan)
              * _scale(column, role) for role, name, column in pairs}
    return pd.DataFrame(values, index=table.index)


def eis_table(table, frequency=None, real=None, imaginary=None, *, negate_imaginary=False):
    """Prepare one EIS spectrum in Hz and ohms, preserving acquisition order."""
    roles = dict(zip(('frequency', 'zreal', 'zimag'), EIS_COLUMNS))
    data = _roles_to_frame(table, {'frequency': frequency, 'zreal': real, 'zimag': imaginary}, roles, roles)
    if negate_imaginary:
        data['Zimag_ohm'] *= -1
    valid = np.isfinite(data.to_numpy()).all(axis=1) & data['frequency_Hz'].gt(0)
    result = data.loc[valid].reset_index(drop=True)
    result.attrs['excluded_rows'] = len(data) - len(result)
    if len(result) < 2:
        raise ValueError('EIS requires at least two finite impedance values at positive frequencies.')
    return result


def _impedance(data):
    numeric = data.loc[:, list(EIS_COLUMNS)].to_numpy(dtype=float)
    frequency, real, imaginary = numeric.T
    if len(frequency) < 2 or not np.isfinite(numeric).all() or (frequency <= 0).any():
        raise ValueError('Prepare valid positive-frequency EIS data before running analysis.')
    return frequency, real + 1j * imaginary


def fit_circuit(eis, circuit='R-RC', source=''):
    """Return the same fit diagnostics and parameters as native eStudio."""
    if circuit not in CIRCUITS:
        raise ValueError(f'Unsupported circuit: {circuit}')
    frequency, impedance = _impedance(eis)
    return intelligence._fit_circuit(circuit, frequency, impedance, source)


def validate_impedance(eis):
    """Run native eStudio's optional impedance.py lin-KK validation."""
    return intelligence._lin_kk(*_impedance(eis))


def circuit_trace(eis, result):
    """Evaluate the native fitted circuit at the measured frequencies."""
    frequency, _ = _impedance(eis)
    if not result.success:
        raise ValueError(result.warning or 'No successful circuit fit.')
    p = result.parameters
    if result.model == 'R':
        parameters = np.log([p['Rs_ohm']])
    elif result.model == 'R-CPE':
        alpha = np.clip(p['alpha'], np.finfo(float).eps, 1 - np.finfo(float).eps)
        parameters = np.r_[np.log([p['Rs_ohm'], p['Rct_ohm'], p['Q']]), np.log(alpha / (1 - alpha))]
    else:
        values = [p['Rs_ohm'], p['Rct_ohm'], p['C_F']]
        if result.model == 'R-RC-W':
            values.append(p['Warburg_sigma'])
        parameters = np.log(values)
    fitted = intelligence._complex_model(result.model, frequency, parameters)
    return pd.DataFrame({'frequency_Hz': frequency, 'Zreal_fit_ohm': fitted.real, 'Zimag_fit_ohm': fitted.imag})


def _canceled(event):
    return event is not None and event.is_set()


def _stop_process(process):
    if os.name == 'nt':
        try:
            subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'], check=False,
                           capture_output=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=8)


def _export_drt_tables(sources, target):
    """Extract numeric distributions from native exports with L/R preambles."""
    columns = {'tau': 'tau_s', 'gamma': 'gamma_ohm', 'MAP': 'gamma_map_ohm', 'Mean': 'gamma_mean_ohm',
               'Upperbound': 'gamma_upper_ohm', 'Lowerbound': 'gamma_lower_ohm',
               'gamma_Re': 'gamma_real_ohm', 'gamma_Im': 'gamma_imaginary_ohm'}
    target.mkdir()
    tables = []
    for source in sources:
        with source.open(newline='', encoding='utf-8-sig') as stream:
            header_row = next((index for index, row in enumerate(csv.reader(stream))
                               if row and row[0].strip().casefold() in {'tau', 'tau_s'}), None)
        if header_row is None:
            continue
        table = pd.read_csv(source, skiprows=header_row).rename(columns=columns)
        if table.empty or 'tau_s' not in table or len(table.columns) < 2:
            continue
        path = target / source.name
        table.to_csv(path, index=False)
        tables.append(path)
    return tables


def run_drt(eis, output_dir, cancel_event=None, *, progress_callback=None, engine='legacy', run_types=('simple',),
            plot_types=('eis', 'drt'), export_types=('drt',), rbf_type='gaussian', derivative=1,
            data='combined', inductance='without', regularization=1e-3,
            shape_control='fwhm', coefficient=0.5, sample_number=2000):
    """Run one spectrum through the bundled DRT worker and retain its downloadable outputs."""
    frequency, _ = _impedance(eis)
    if len(frequency) < 6 or len(np.unique(frequency)) != len(frequency):
        raise ValueError('DRT requires at least six unique positive frequencies with one impedance pair each.')
    run_types = tuple(run_types)
    if not run_types or not set(run_types).issubset({'simple', 'bayesian', 'bht'}):
        raise ValueError('Choose one or more supported DRT methods.')
    if 'bayesian' in run_types and sample_number < DRT_MIN_BAYESIAN_SAMPLES:
        raise ValueError(f'Bayesian DRT requires at least {DRT_MIN_BAYESIAN_SAMPLES} samples. The native sampler discards 501 burn-in samples.')
    if _canceled(cancel_event):
        raise InterruptedError('DRT canceled.')
    parent = Path(output_dir).expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    run = Path(mkdtemp(prefix='DRT_', dir=parent))
    inputs = run / 'input'
    results = run / 'results'
    log = run / 'drt_console.log'
    process_options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    try:
        inputs.mkdir()
        eis.to_csv(inputs / 'impedance.csv', index=False, columns=list(EIS_COLUMNS))
        command = build_headless_drt_command(
            inputs, engine=engine, run_types=run_types, plot_types=plot_types, export_types=export_types,
            extra_args=['--output_dir', str(results), '--rbf_type', rbf_type, '--derivative', str(derivative),
                        '--data', data, '--inductance', inductance, '--regularization', str(regularization),
                        '--shape_control', shape_control, '--coefficient', str(coefficient),
                        '--sample_number', str(sample_number)],
        )
        with log.open('w', encoding='utf-8') as writer, log.open(encoding='utf-8', errors='replace') as reader:
            with subprocess.Popen(command, cwd=headless_drt_script().parent,
                                  env={**os.environ, 'MPLBACKEND': 'Agg'}, stdout=writer,
                                  stderr=subprocess.STDOUT, **process_options) as process:
                try:
                    while True:
                        if _canceled(cancel_event):
                            raise InterruptedError('DRT canceled.')
                        try:
                            process.wait(timeout=0.25)
                        except subprocess.TimeoutExpired:
                            pass
                        messages = reader.readlines()
                        if progress_callback:
                            for message in messages:
                                progress_callback(message.strip())
                        if process.poll() is not None:
                            break
                finally:
                    if process.poll() is None:
                        _stop_process(process)
        exports = sorted(results.rglob('*.csv'))
        if _canceled(cancel_event):
            raise InterruptedError('DRT canceled.')
        native_drt_csvs = [path for path in exports if path.stem.endswith('_DRT')]
        drt_csvs = _export_drt_tables(native_drt_csvs, run / 'tables')
        summary_path = next(results.rglob('DRT_run_summary.csv'), None)
        settings_path = next(results.rglob('DRT_run_settings.json'), None)
        completed = {name for name in run_types
                     if any(path.stem.lower().endswith(f'_{name}_drt') for path in drt_csvs)}
        failures = {name: 'No DRT output was produced.' for name in run_types if name not in completed}
        if summary_path:
            for stage in pd.read_csv(summary_path, keep_default_na=False).to_dict('records'):
                if stage['status'] != 'ok':
                    failures[stage['run_type']] = stage['error'] or 'The DRT worker reported a failure.'
                    completed.discard(stage['run_type'])
        if process.returncode or not completed:
            raise RuntimeError(f'DRT did not produce a complete result. See the worker log at {log}.')
        drt_csvs = [path for path in drt_csvs if any(path.stem.lower().endswith(f'_{mode}_drt') for mode in completed)]
        return {'output_dir': run, 'drt_csvs': drt_csvs, 'native_drt_csvs': native_drt_csvs, 'csvs': exports,
                'figures': sorted(results.rglob('*.png')), 'log_path': log, 'settings_path': settings_path,
                'summary_path': summary_path, 'completed_modes': sorted(completed), 'failed_modes': failures}
    except InterruptedError:
        shutil.rmtree(run, ignore_errors=True)
        raise
    except BaseException:
        shutil.rmtree(results, ignore_errors=True)
        shutil.rmtree(inputs, ignore_errors=True)
        shutil.rmtree(run / 'tables', ignore_errors=True)
        raise


def _trace_frame(table, *, potential=None, current=None, time=None, required, include_current=True):
    aliases = {'potential': 'potential_V', 'current': 'current_A', 'time': 'time_s'}
    if not include_current:
        aliases.pop('current')
    return _roles_to_frame(table, {'potential': potential, 'current': current, 'time': time}, aliases, required)


def analyze_trace(table, technique, *, potential=None, current=None, time=None, identity=None,
                  final_time_percent=None, applied_current_A=None):
    """Analyze trace data with the native electrochemistry kernels in scientific SI units."""
    from .ca_cp_analysis_v262 import analyze_file as native_transient
    from .ocp_handoff_v291 import DEFAULT_FINAL_TIME_PERCENT, _tail_metrics

    technique = str(technique).strip().upper()
    identity = dict(identity or {})
    analysis = {
        'CV': deterministic.analyze_cv_lsv,
        'FSCV': deterministic.analyze_cv_lsv,
        'LSV': deterministic.analyze_cv_lsv,
        'CA': deterministic.analyze_ca,
        'DEPOSITION': deterministic.analyze_ca,
        'DPV': deterministic.analyze_pulse,
        'SWV': deterministic.analyze_pulse,
        'NPV': deterministic.analyze_pulse,
        'STRIPPING': deterministic.analyze_stripping,
    }
    if technique in {'CA', 'DEPOSITION'}:
        required = ('time', 'current')
    elif technique in {'OCP', 'CP'}:
        required = ('time', 'potential')
    elif technique in analysis:
        required = ('potential', 'current')
    else:
        raise ValueError(f'Unsupported trace technique: {technique}')
    normalized = _trace_frame(table, potential=potential, current=current, time=time, required=required,
                              include_current=technique != 'OCP')
    columns = list(normalized.columns)
    values = normalized.to_numpy()
    if technique == 'OCP':
        result = deterministic.analyze_ocp(columns, values, identity)
        result['tail'] = _tail_metrics(normalized['time_s'].to_numpy(), normalized['potential_V'].to_numpy(),
                                       DEFAULT_FINAL_TIME_PERCENT if final_time_percent is None else final_time_percent)
        return result
    if technique == 'CP':
        # The explicit CP route applies the desktop CP kernel even when current is also recorded.
        # Use one sample at each time for the derivative when timestamps are duplicated.
        ordered = normalized.sort_values('time_s', kind='stable')
        unique = ordered.drop_duplicates('time_s', keep='last')
        result = deterministic.analyze_cp(columns, unique.to_numpy(), identity)
        if 'current_A' in ordered:
            valid = ordered.loc[np.isfinite(ordered[['time_s', 'current_A']]).all(axis=1)]
            result.setdefault('metrics', {})['integrated_charge_C'] = deterministic._trapz(
                valid['current_A'].to_numpy(), valid['time_s'].to_numpy())
            result['metrics']['charge_source'] = 'measured current'
        elif applied_current_A is not None:
            measured_time = ordered.loc[np.isfinite(ordered['time_s']), 'time_s'].to_numpy()
            applied = float(applied_current_A)
            if not np.isfinite(applied):
                raise ValueError('Applied current must be finite and expressed in amperes.')
            result.setdefault('metrics', {})['integrated_charge_C'] = deterministic._trapz(
                np.full_like(measured_time, applied), measured_time)
            result['metrics']['charge_source'] = 'user-supplied constant current'
        return result
    function = analysis[technique]
    if technique == 'STRIPPING':
        return function(columns, values, identity)
    result = function(columns, values, identity, 'CV' if technique == 'FSCV' else technique)
    if technique in {'CA', 'DEPOSITION'} and result.get('status') == 'complete':
        transient = normalized[['time_s', 'current_A']].replace([np.inf, -np.inf], np.nan).dropna()
        if len(transient) >= 3:
            with TemporaryDirectory(prefix='estudio_ca_') as scratch:
                source = Path(scratch) / 'chronoamperometry.csv'
                transient.to_csv(source, index=False)
                result['transient'] = asdict(native_transient(source))
    return result


def _normalized_iv(table, potential, current):
    normalized = _roles_to_frame(table, {'potential': potential, 'current': current},
                                 {'potential': 'potential_V', 'current': 'current_A'}, ('potential', 'current'))
    return normalized.loc[np.isfinite(normalized.to_numpy()).all(axis=1)]


def _write_iv(table, target, potential, current):
    _normalized_iv(table, potential, current).to_csv(target, index=False)


def analyze_tafel(table, output_dir, *, settings=None, potential=None, current=None):
    """Use the desktop Tafel branch, stable-window and Butler–Volmer implementations."""
    from faradaiq_tafel_adapter import TafelSettings, analyze_file_variants

    if isinstance(settings, Mapping):
        settings = TafelSettings(**settings)
    settings = settings or TafelSettings()
    cath_min, cath_max = settings.manual_cathodic_range_V
    anod_min, anod_max = settings.manual_anodic_range_V
    if not cath_min < cath_max <= 0 or not 0 <= anod_min < anod_max:
        raise ValueError('Enter increasing negative cathodic and positive anodic overpotential ranges.')
    if settings.auto_min_abs_eta_V >= settings.auto_max_abs_eta_V:
        raise ValueError('The automatic maximum |η| must be greater than the minimum |η|.')
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    run = Path(mkdtemp(prefix='Tafel_', dir=root))
    source = run / 'trace.csv'
    try:
        _write_iv(table, source, potential, current)
        outputs = analyze_file_variants(source, run, settings)
        if settings.eeq_V is not None:
            for result in outputs:
                summary_path = Path(result.summary_json)
                summary = json.loads(summary_path.read_text(encoding='utf-8'))
                summary['nearest_measured_potential_V'] = result.eeq_V
                summary['eeq_V'] = result.eeq_V = float(settings.eeq_V)
                intelligence._atomic_json(summary_path, summary)
        return outputs
    except BaseException:
        shutil.rmtree(run, ignore_errors=True)
        raise


def analyze_corrosion(table, *, settings=None, potential=None, current=None, identity=None, output_dir=None):
    """Apply eStudio's Ecorr selection and corrosion metrics to a selected trace."""
    from .corrosion_metrics_v298 import CorrosionSettings, analyze_trace as native_corrosion

    if isinstance(settings, Mapping):
        settings = CorrosionSettings(**settings)
    settings = settings or CorrosionSettings()
    with TemporaryDirectory(prefix='estudio_corrosion_') as scratch:
        source = Path(scratch) / 'trace.csv'
        _write_iv(table, source, potential, current)
        result = native_corrosion(source, settings, identity)
    result['source_path'] = str((identity or {}).get('source_path', ''))
    result['source_filename'] = Path(result['source_path']).name
    if output_dir is not None:
        root = Path(output_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        run = Path(mkdtemp(prefix='Corrosion_', dir=root))
        try:
            csv_path = run / 'corrosion_metrics.csv'
            dip_path = run / 'corrosion_dip_candidates.csv'
            json_path = run / 'corrosion_metrics.json'
            scalars = {key: value for key, value in result.items() if not isinstance(value, (list, dict, tuple))}
            pd.DataFrame([scalars]).to_csv(csv_path, index=False)
            pd.DataFrame(result['dip_candidates']).to_csv(dip_path, index=False)
            intelligence._atomic_json(json_path, {'schema': 'faradaiq-corrosion-metrics-v298', 'version': APP_VERSION,
                                                    'settings': asdict(settings), 'rows': [result]})
            result['artifacts'] = {'corrosion_metrics_csv': csv_path, 'corrosion_dip_candidates_csv': dip_path,
                                   'corrosion_metrics_json': json_path}
        except BaseException:
            shutil.rmtree(run, ignore_errors=True)
            raise
    return result


def analyze_mott_schottky(table, *, potential=None, frequency=None, real=None, imaginary=None, negate_imaginary=False,
                          output_dir=None):
    """Run native strict potential-frequency EIS and Mott–Schottky diagnostics."""
    from .v317_advanced_potential_eis import analyze_mott_grid_advanced

    roles = {'potential': 'potential_V', **dict(zip(('frequency', 'zreal', 'zimag'), EIS_COLUMNS))}
    selected = {'potential': potential, 'frequency': frequency, 'zreal': real, 'zimag': imaginary}
    numeric = _roles_to_frame(table, selected, roles, roles)
    values = numeric.to_numpy()
    if not len(values) or not np.isfinite(values).all() or numeric['frequency_Hz'].le(0).any():
        raise ValueError('Potential-frequency grids require finite potential/impedance and positive frequencies.')
    if negate_imaginary:
        numeric['Zimag_ohm'] *= -1
    groups = [rows.sort_values('frequency_Hz', ascending=False) for _, rows in numeric.groupby('potential_V', sort=False)]
    if len(groups) < 2:
        raise ValueError('A potential-frequency grid must include at least two potential steps.')
    reference = groups[0]['frequency_Hz'].to_numpy()
    if len(reference) < 6 or len(np.unique(reference)) != len(reference):
        raise ValueError('Each potential step needs at least six unique frequencies.')
    if any(len(group) != len(reference) or not np.array_equal(group['frequency_Hz'], reference) for group in groups[1:]):
        raise ValueError('Every potential step must contain the same frequency grid.')
    result = analyze_mott_grid_advanced({'array': pd.concat(groups, ignore_index=True).to_numpy(),
                                         'columns': list(roles.values()), 'n_potential_steps': len(groups),
                                         'n_frequencies': len(reference)})
    if output_dir is not None and result.get('status') == 'ok':
        result['artifacts'] = _export_mott_schottky(result, output_dir)
    return result


def _export_mott_schottky(result, output_dir):
    from . import v317_advanced_potential_eis as native

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    run = Path(mkdtemp(prefix='Mott_Schottky_', dir=root))
    tables = {'fit_diagnostics': ('summary', native.SUMMARY_COLUMNS),
              'model_candidates': ('candidates', native.CANDIDATE_COLUMNS),
              'capacitance': ('cap_all', native.CAP_COLUMNS),
              'capacitance_minimum': ('pzc', native.PZC_COLUMNS),
              'mott_schottky_diagnostics': ('mott', native.MOTT_COLUMNS),
              'measured_and_fitted_eis': ('fit_rows', native.FIT_COLUMNS)}
    artifacts = {}
    try:
        for stem, (key, columns) in tables.items():
            path = run / f'{stem}.csv'
            pd.DataFrame(result[key], columns=columns).to_csv(path, index=False)
            artifacts[f'{stem}_csv'] = path
        interpretation_path = run / 'interpretation.json'
        intelligence._atomic_json(interpretation_path, {'version': APP_VERSION, **result['interpretation']})
        artifacts['interpretation_json'] = interpretation_path
        return artifacts
    except BaseException:
        shutil.rmtree(run, ignore_errors=True)
        raise


def spatial_points(table, *, x=None, y=None, z=None, signal=None):
    """Summarize repeated measurements at observed locations using native curve metrics."""
    from .multidim_v216 import Curve, _metrics

    aliases = {'x': 'x_m', 'y': 'y_m', 'z': 'z_m', 'signal': 'signal'}
    numeric = _roles_to_frame(table, {'x': x, 'y': y, 'z': z, 'signal': signal}, aliases, ('x', 'y', 'signal'))
    coordinates = [name for name in ('x_m', 'y_m', 'z_m') if name in numeric]
    valid = numeric.loc[np.isfinite(numeric.to_numpy()).all(axis=1)]
    rows = []
    for location, group in valid.groupby(coordinates, sort=False):
        values = group['signal'].to_numpy()
        # Only summary statistics are retained. Row indices are not a spatial or time axis.
        curve = Curve(Path(''), {}, 'repeat', 'signal', np.arange(len(group)), values, len(rows) + 1, 1, 0, 0)
        metrics = _metrics(curve)
        metrics.pop('integral', None)
        rows.append({**dict(zip(coordinates, location)), 'n_samples': len(group), **metrics})
    return pd.DataFrame(rows)


class _RecordSelection:
    """Supplies the selected catalog leaves to the native analysis helpers."""

    def __init__(self, records):
        self.records = {str(index): record for index, record in enumerate(records)}

    def selection(self):
        return list(self.records)

    def get_children(self, _item):
        return ()


def _native_workspace(records, run, label, positions=()):
    from types import SimpleNamespace

    selection = _RecordSelection(records)
    return SimpleNamespace(analyzed_root=run, cache_root=run / 'data', base_name=label,
                           tree=selection, _record_by_iid=selection.records,
                           _catalog_records=[*records, *positions])


def analyze_cv_branches(table, output_dir, *, potential=None, current=None, scan_rate_V_s=None, identity=None):
    """Run native branch-specific linear-baseline peak metrics and charge exports."""
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    run = Path(mkdtemp(prefix='CV_', dir=root))
    try:
        source = run / 'voltammogram.csv'
        _write_iv(table, source, potential, current)
        record = {**(identity or {}), 'path': str(source), 'technique_key': 'CV'}
        if scan_rate_V_s is not None:
            scan_rate_V_s = float(scan_rate_V_s)
            if not np.isfinite(scan_rate_V_s) or scan_rate_V_s <= 0:
                raise ValueError('Scan rate must be positive and expressed in volts per second.')
            record['scan_rate'] = f'{scan_rate_V_s:.12f} V/s'
        result = intelligence.analyze_cv(_native_workspace([record], run, 'voltammogram'))
        return result
    except BaseException:
        shutil.rmtree(run, ignore_errors=True)
        raise


def make_native_view(records, output_dir, *, mode, metric='Auto', options=None, positions=(), label='Measurements'):
    """Generate the desktop visualizations from selected exported table records."""
    from . import multidim_v216 as multidim

    generators = {'2D': multidim.make_altair_2d, '3D': multidim.make_plotly_3d,
                  'Hyperspectral': multidim.make_hyperspectral}
    if mode not in generators:
        raise ValueError(f'Unsupported native visualization: {mode}')
    if not records:
        raise ValueError('Select one or more exported measurement records.')
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    run = Path(mkdtemp(prefix='View_', dir=root))
    try:
        browser = _native_workspace(records, run, label, positions)
        return generators[mode](browser, metric, options)
    except BaseException:
        shutil.rmtree(run, ignore_errors=True)
        raise


def pysimple_defaults(table, *, potential=None, current=None):
    """Suggest PySimpleEChem controls using the same normalized samples as the fit."""
    from faradaiq_pysimpleechem_cv_adapter import default_params_for

    frame = _normalized_iv(table, potential, current)
    if len(frame) < 3:
        raise ValueError('PySimpleEChem requires at least three finite potential/current samples.')
    return default_params_for(frame['potential_V'].to_numpy(), frame['current_A'].to_numpy())


def analyze_pysimple_cv(table, output_dir, *, parameters=None, potential=None, current=None):
    """Apply the bundled PySimpleEChem two-peak and two-baseline workflow."""
    from faradaiq_pysimpleechem_cv_adapter import analyze_cv_files, compact_result, write_results_bundle

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    run = Path(mkdtemp(prefix='PySimpleEChem_', dir=root))
    try:
        source = run / 'voltammogram.csv'
        _write_iv(table, source, potential, current)
        bundle = analyze_cv_files([source], parameters)
        if not bundle['results']:
            raise ValueError(bundle['errors'][0]['error'])
        result = bundle['results'][0]
        exports = write_results_bundle(bundle, run)
        voltage = result['E']
        trace = pd.DataFrame({'sample_index': np.arange(len(voltage)), 'potential_V': voltage,
                              'current_density_A_cm2': result['I']})
        for peak in (1, 2):
            coefficients = result[f'peak_{peak}']['baseline_poly_coef']
            trace[f'baseline_{peak}_A_cm2'] = np.polynomial.polynomial.polyval(voltage, coefficients)
        trace_path = run / 'pysimpleechem_cv_trace.csv'
        trace.to_csv(trace_path, index=False)
        exports['trace_csv'] = str(trace_path)
        return {'result': compact_result(result), 'trace': trace, 'exports': exports}
    except BaseException:
        shutil.rmtree(run, ignore_errors=True)
        raise


def make_deareis_handoff(eis, output_dir, *, make_project=True):
    """Write the native normalized spectrum and an optional DearEIS project locally."""
    import json
    from faradaiq_deareis_adapter import create_deareis_handoff

    _impedance(eis)
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    run = Path(mkdtemp(prefix='DearEIS_', dir=root))
    try:
        source = run / 'impedance.csv'
        eis.to_csv(source, index=False, columns=list(EIS_COLUMNS))
        package = Path(__file__).resolve().parents[1]
        relative_python = ('Scripts', 'python.exe') if os.name == 'nt' else ('bin', 'python')
        optional_python = package.joinpath('.deareis_venv', *relative_python)
        separate_runtime = make_project and optional_python.is_file()
        result = create_deareis_handoff([source], run, make_project=make_project and not separate_runtime)
        if separate_runtime:
            project = Path(result.output_dir) / 'FaradaIQ_DearEIS_project.json'
            command = [str(optional_python), str(package / 'faradaiq_deareis_project_cli.py'),
                       '--out', str(project), *result.normalized_files]
            try:
                subprocess.run(command, check=True, timeout=30, capture_output=True, text=True)
                if not project.is_file():
                    raise RuntimeError('DearEIS did not create the project file.')
                result.project_json = str(project)
                result.deareis_available = True
                Path(result.readme).write_text(
                    'Open the DearEIS project JSON in the optional DearEIS application.\n'
                    f'Project: {project.name}\n', encoding='utf-8')
            except (OSError, subprocess.SubprocessError, RuntimeError) as error:
                result.warnings.append(f'DearEIS project is unavailable. The normalized CSV was retained. {error}')
            Path(result.manifest).write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding='utf-8')
        return result
    except BaseException:
        shutil.rmtree(run, ignore_errors=True)
        raise
