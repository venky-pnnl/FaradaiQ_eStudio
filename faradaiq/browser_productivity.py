"""Recent work, measurement checks, and comparisons for the local workspace."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

import numpy as np
import pandas as pd

from .browser_analysis import _current_density_header, _scale, _UNITS, _NORMALIZE_UNITS, measurement_roles
from .release_identity import APP_VERSION


def recent_items(value):
    """Accept only bounded, path-based recent entries from preferences."""
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, dict) or item.get('kind') not in {'data', 'session'}:
            continue
        path = item.get('path')
        if not isinstance(path, str) or not path.strip():
            continue
        key = (item['kind'], path)
        if key in seen:
            continue
        seen.add(key)
        result.append({'kind': item['kind'], 'path': path, 'label': Path(path).name or path})
        if len(result) == 8:
            break
    return result


def remember_recent(preferences, path, kind):
    path = str(Path(path).expanduser().resolve())
    entries = [{'kind': kind, 'path': path}] + recent_items(preferences.get('recent'))
    return dict(preferences, recent=recent_items(entries))


def measurement_label(data, path):
    """Keep the folder identity and include recorded conditions, including zero bias."""
    label = path.relative_to(data.analysis_root).as_posix()
    record = data.records.get(path, {})
    if record.get('display_name'):
        label = f"{record['display_name']} [{label}]"
    fields = []
    for key, title in (('dc_potential_V', 'V'), ('bias_potential_V', 'V')):
        value = record.get(key)
        if value is not None:
            try:
                fields.append(f'{float(value):g} {title}')
            except (TypeError, ValueError):
                pass
            break
    for key, title in (('cycle', 'cycle'), ('point', 'point'), ('point_index', 'point')):
        if record.get(key) is not None and not any(v.startswith(title + ' ') for v in fields):
            fields.append(f'{title} {record[key]}')
    return label + (' · ' + ', '.join(fields) if fields else '')


def measurement_reference(data, path):
    """Find a declared source reference without inferring one from the instrument model."""
    record = data.records.get(path, {})
    candidates = [record, record.get('metadata')]
    if not record.get('handle_id'):
        candidates.append(data.metadata)
    handles = data.metadata.get('project_handles') or []
    if isinstance(handles, list):
        candidates.extend(h.get('metadata') for h in handles if isinstance(h, dict)
                          and h.get('id') == record.get('handle_id'))
    found = []
    keys = {'reference_electrode', 'potential_reference', 'reference', 'reference_scale'}
    for metadata in candidates:
        if not isinstance(metadata, dict): continue
        values = [metadata.get(key) for key in keys]
        tags = metadata.get('tags')
        if isinstance(tags, dict):
            values.extend(tags.get(key) for key in keys)
        elif isinstance(tags, (list, tuple)):
            for tag in tags:
                match = re.match(r'^\s*(reference_electrode|potential_reference|reference|reference_scale)\s*[:=]\s*(.+)$', str(tag), re.I)
                if match: values.append(match.group(2))
        for value in values:
            if isinstance(value, dict): value = value.get('name') or value.get('type')
            if isinstance(value, str) and value.strip() and value.strip().casefold() not in {'unknown','unspecified','none'}:
                found.append(value.strip())
    normalized = {re.sub(r'\s+', '', ref).casefold() for ref in found}
    if len(normalized) > 1:
        raise ValueError(f'{Path(path).name} has conflicting declared potential references: {", ".join(found)}')
    return found[0] if found else ''


def table_quality(table):
    """Inspect the full selected table without removing or changing any source rows."""
    roles = measurement_roles(table)
    rows = []
    for role, column in roles.items():
        if column is None or column not in table or any(r['Column'] == column for r in rows):
            continue
        values = pd.to_numeric(table[column], errors='coerce').to_numpy(dtype=float, na_value=np.nan)
        finite = values[np.isfinite(values)]
        rows.append({'Quantity': role, 'Column': str(column), 'Finite values': int(len(finite)),
                     'Missing / nonfinite': int(len(values) - len(finite)),
                     'Minimum': float(finite.min()) if len(finite) else None,
                     'Maximum': float(finite.max()) if len(finite) else None})
    issues = []
    if any(row['Missing / nonfinite'] for row in rows):
        issues.append('Some measurement values are missing, nonnumeric, or infinite. Analysis may exclude these rows.')
    if roles.get('frequency') is not None:
        values = pd.to_numeric(table[roles['frequency']], errors='coerce').to_numpy(dtype=float, na_value=np.nan)
        finite = values[np.isfinite(values)]
        invalid = int(np.sum(finite <= 0))
        repeated = int(len(finite) - len(np.unique(finite)))
        if invalid:
            issues.append(f'{invalid} nonpositive frequency value(s); EIS analysis excludes them.')
        if repeated:
            issues.append(f'{repeated} repeated frequency value(s). Select one spectrum before running DRT.')
    if roles.get('time') is not None:
        values = pd.to_numeric(table[roles['time']], errors='coerce').to_numpy(dtype=float, na_value=np.nan)
        finite = values[np.isfinite(values)]
        if np.any(np.diff(finite) < 0):
            issues.append('Time moves backward. Check whether this table contains multiple acquisitions before integrating charge.')
        repeated = int(len(finite) - len(np.unique(finite)))
        if repeated:
            issues.append(f'{repeated} repeated timestamp(s). Review before fitting transients.')
    return pd.DataFrame(rows), issues


def _explicit_unit(column, role):
    text = str(column).translate(_NORMALIZE_UNITS).strip()
    return next((t.replace('Ohm', 'ohm') for t in reversed(re.split(r'[\s_/\[\]()]+', text))
                 if t.replace('Ohm', 'ohm') in _UNITS.get(role, {})), None)


def _mapped_axis(frame, target, target_roles):
    if target in frame:
        return pd.to_numeric(frame[target], errors='coerce')
    role = next((r for r, c in target_roles.items() if c == target and r in _UNITS), None)
    column = measurement_roles(frame).get(role) if role else None
    if column is None:
        raise ValueError(f'no matching column for {target}')
    if role == 'current' and (_current_density_header(target) or _current_density_header(column)):
        raise ValueError('current and current density require an explicit electrode-area conversion')
    if _explicit_unit(target, role) is None or _explicit_unit(column, role) is None:
        raise ValueError(f'cannot verify units for {column} → {target}')
    values = pd.to_numeric(frame[column], errors='coerce') * (_scale(column, role) / _scale(target, role))
    if role == 'zimag':
        from .browser_plotting import negative_impedance_label
        if negative_impedance_label(column) != negative_impedance_label(target):
            values = -values
    return values


def prepare_xy_series(series, x, y):
    """Convert compatible overlay axes to the active table's explicit units."""
    if not series:
        return [], []
    target_roles = measurement_roles(series[0][1])
    prepared, skipped = [], []
    for path, frame in series:
        try:
            axes = {column: _mapped_axis(frame, column, target_roles) for column in dict.fromkeys((x, y))}
            prepared.append((path, pd.DataFrame(axes)))
        except ValueError as error:
            skipped.append(f'{Path(path).name}: {error}')
    return prepared, skipped


def compare_trace_metrics(data, paths, *, technique='Auto', cancel=None, progress=None):
    """Use existing scientific kernels, retaining a result or error for every selection."""
    from .browser_analysis import analyze_trace
    from .browser_plot_ui import measurement_technique
    supported = {'CV', 'LSV', 'FSCV', 'CA', 'CP', 'OCP', 'DPV', 'SWV', 'NPV', 'Stripping', 'Deposition'}
    rows = []
    for index, path in enumerate(paths, 1):
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Trace comparison canceled.')
        if progress:
            progress(f'{index}/{len(paths)} · {Path(path).name}')
        row = {'Measurement': path.relative_to(data.analysis_root).as_posix(), 'Source': str(data.source),
               'Version': APP_VERSION,
               'Calculated UTC': datetime.now(timezone.utc).isoformat()}
        try:
            table = data.read_table(path)
            roles = measurement_roles(table)
            if technique == 'Auto' and roles.get('frequency'):
                raise ValueError('Use EIS and DRT for impedance spectra.')
            selected = measurement_technique(data, path, roles) if technique == 'Auto' else technique
            row['Technique'] = selected
            if selected not in supported:
                raise ValueError('Choose a supported trace technique.')
            result = analyze_trace(table, selected)
            row['Status'] = result.get('status', 'complete')
            for section in ('metrics', 'tail'):
                for key, value in (result.get(section) or {}).items():
                    if value is None or isinstance(value, (str, int, float, bool, np.number)):
                        row[(('tail_' if section == 'tail' else '') + key)] = value
            # Keep the first forward/reverse sweeps useful in a compact comparison.
            for number, sweep in enumerate((result.get('metrics') or {}).get('sweeps', [])[:2], 1):
                for key in ('direction', 'current_min_A', 'potential_at_current_min_V',
                            'current_max_A', 'potential_at_current_max_V', 'onset_V', 'charge_C'):
                    row[f'sweep_{number}_{key}'] = sweep.get(key)
            warnings = result.get('warnings') or result.get('warning') or ''
            row['Detail'] = '; '.join(map(str, warnings)) if isinstance(warnings, list) else str(warnings)
            if result.get('required_columns'):
                row['Detail'] += ' Required columns: ' + ', '.join(result['required_columns'])
            if result.get('missing_metadata'):
                row['Detail'] += ' Missing metadata: ' + ', '.join(result['missing_metadata'])
            _, issues = table_quality(table)
            if issues:
                row['Detail'] = '; '.join(filter(None, [row['Detail'], *issues]))
                if row['Status'] == 'complete':
                    row['Status'] = 'review'
        except Exception as error:
            row.update(Status='unavailable', Detail=str(error))
        rows.append(row)
    return pd.DataFrame(rows)
