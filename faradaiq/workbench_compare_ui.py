"""Local plotting, processing and comparison controls for eStudio v341."""
from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
from tempfile import mkdtemp

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from . import workbench_curves as curves
from .workbench_audit import record_operation
from .workbench_curves import comparison_key as _id


_PREFIX = 'w340_compare_'
_LEGEND_FIELDS = ('file', 'instrument', 'sample', 'potential', 'cycle', 'tags')
_STYLES = {
    'Publication': {'template': 'plotly_white', 'colors': ['#26547c', '#cb4b36', '#14866d', '#80509a', '#b27710'],
                    'width': 2.4, 'font_size': 15, 'grid': True, 'legend_fields': ['instrument', 'sample', 'file']},
    'High contrast': {'template': 'plotly_white', 'colors': ['#1754a4', '#a92b17', '#04774f', '#582a8d', '#704b00'],
                      'width': 3.2, 'font_size': 17, 'grid': False, 'legend_fields': ['instrument', 'file']},
    'Dark': {'template': 'plotly_dark', 'colors': ['#71baf6', '#fb8a70', '#69d7af', '#e1a8f4', '#f6d07a'],
             'width': 2.5, 'font_size': 15, 'grid': True, 'legend_fields': ['instrument', 'file']},
}
_DASHES = ('solid', 'dash', 'dot', 'dashdot')
_CHART_CONFIG = {'displaylogo': False, 'edits': {'annotationPosition': True},
                 'toImageButtonOptions': {'format': 'png', 'filename': 'estudio-curves', 'scale': 2}}
_EXPORT_SCRIPT = """
const chart = document.getElementById('{plot_id}');
const button = document.createElement('button');
button.textContent = 'Download annotation positions';
button.style.cssText = 'margin: 0.5rem; padding: 0.45rem; font: 1rem sans-serif';
button.onclick = () => {
  const data = JSON.stringify(chart.layout.annotations || [], null, 2);
  const url = URL.createObjectURL(new Blob([data], {type: 'application/json'}));
  const a = document.createElement('a');
  a.href = url;
  a.download = 'annotation-positions.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
chart.before(button);
"""


def _key(name):
    return _PREFIX + name


def _source_key(data, path):
    handle = data.records.get(path, {}).get('handle_id')
    relative = (path.relative_to(data.analysis_root).as_posix()
                if data.metadata.get('project_id') and path.is_relative_to(data.analysis_root) else None)
    return curves.measurement_key(data.source, path, handle, relative)


def _source(data, path):
    return curves.source_details(path, data.records.get(path, {}))


def _independent_sources(sources):
    handles = {str(source['handle_id']) for source in sources if source.get('handle_id')}
    raw_paths = {str(path).partition('::')[0] for source in sources
                 if (path := source.get('original_table_path') or source.get('source_path'))}
    instruments = {str(source.get('instrument', '')).strip().casefold() for source in sources if source.get('instrument')}
    return len(handles) > 1 or len(raw_paths) > 1 or len(instruments) > 1


def _read(data, path):
    return data.read_table(path).rename(columns=str)


def _selected(data, path, fields=None):
    return curves.legend_label(path, data.records.get(path, {}), fields or ('instrument', 'file'))


def _picker_label(data, path):
    relative = path.relative_to(data.analysis_root) if path.is_relative_to(data.analysis_root) else path
    return f'{_selected(data, path)} — {relative.as_posix()}'


def _history(data, path, table):
    key = _key('history_' + _source_key(data, path))
    if key not in st.session_state:
        metadata = data.records.get(path, {})
        tags = curves.metadata_tags(metadata)
        recipe = curves.default_recipe(table)
        reference = next((metadata.get(field) or tags.get(field) for field in ('potential_reference', 'reference_electrode')
                          if metadata.get(field) or tags.get(field)), '')
        recipe = replace(recipe, source_reference=str(reference))
        st.session_state[key] = {'entries': [asdict(recipe)], 'cursor': 0}
    saved = st.session_state[key]
    return key, saved['entries'], saved['cursor']


def _update_recipe(data, path, workspace, key, history, cursor, *, recipe=None, step=0):
    entries, position = curves.update_history(history, cursor, recipe=asdict(recipe) if recipe else None, step=step)
    st.session_state[key] = {'entries': entries, 'cursor': position}
    st.session_state[_key(_source_key(data, path) + '_preview')] = 'Applied result'
    action = 'Undo curve processing' if step < 0 else 'Redo curve processing' if step > 0 else 'Apply curve processing'
    record_operation(workspace, action, inputs=[path], parameters={'recipe': entries[position], 'source': _source(data, path)})
    st.rerun()


def _float(container, label, name, value=None, *, help=None, format='%.6g'):
    return container.number_input(label, value=value, persist_state='session', key=_key(name), help=help, format=format)


def _interval(container, name, defaults=None):
    left, right = container.columns(2)
    lower = _float(left, 'Minimum x', name + '_lower', defaults[0] if defaults else None)
    upper = _float(right, 'Maximum x', name + '_upper', defaults[1] if defaults else None)
    if lower is None or upper is None:
        raise ValueError('Enter both x bounds for the enabled interval.')
    return lower, upper


def _row_numbers(text):
    values = set()
    for token in text.replace('\n', ',').split(','):
        part = token.strip()
        if not part:
            continue
        try:
            bounds = list(map(int, part.split('-')))
            if len(bounds) == 1 and bounds[0] > 0:
                values.add(bounds[0])
            elif len(bounds) == 2 and 0 < bounds[0] <= bounds[1] and bounds[1] - bounds[0] <= 100_000:
                values.update(range(bounds[0], bounds[1] + 1))
            else:
                raise ValueError
        except ValueError:
            raise ValueError('Use one-based row numbers or ascending ranges separated by commas, for example 3, 9-12.') from None
    return tuple(sorted(values))


def _recipe_widgets(table, recipe, name):
    columns = list(table.columns)
    with st.expander('Axes and measurement units', expanded=True):
        axes = st.columns(2)
        x = axes[0].selectbox('X column', columns, index=columns.index(recipe.x), persist_state='session', key=_key(name + '_x'))
        y = axes[1].selectbox('Y column', columns, index=columns.index(recipe.y), persist_state='session', key=_key(name + '_y'))
        x_unit = axes[0].text_input('X unit override', recipe.x_unit, persist_state='session', key=_key(name + '_x_unit'), placeholder='Unit in the header')
        y_unit = axes[1].text_input('Y unit override', recipe.y_unit, persist_state='session', key=_key(name + '_y_unit'), placeholder='Unit in the header')
        cycle_choices = ['', *columns]
        cycle_column = st.selectbox('Cycle column', cycle_choices, index=cycle_choices.index(recipe.cycle_column),
                                    format_func=lambda value: value or 'None', persist_state='session', key=_key(name + '_cycle_column'))
        cycle_values = table[cycle_column].dropna().astype(str).unique().tolist() if cycle_column else []
        cycles = st.multiselect('Cycles to include', cycle_values,
                                default=[cycle for cycle in recipe.cycles if cycle in cycle_values],
                                persist_state='session', key=_key(name + '_cycles')) if cycle_column else []
        source_reference = st.text_input('Potential reference as recorded', recipe.source_reference,
                                         persist_state='session', key=_key(name + '_source_ref'), placeholder='E.g. Ag/AgCl, SHE, RHE')
    with st.expander('Area normalization, reference conversion and iR correction'):
        labels = ['as recorded', 'current', 'current density']
        output = st.selectbox('Current output', labels, index=labels.index(recipe.current_output),
                              persist_state='session', key=_key(name + '_current_output'))
        area = _float(st, 'Electrode area (cm²)', name + '_area', recipe.area_cm2)
        target_reference = st.text_input('Convert to potential reference', recipe.target_reference,
                                         persist_state='session', key=_key(name + '_target_ref'), placeholder='Blank uses recorded reference')
        source_she, target_she, ph, temperature = recipe.source_she_v, recipe.target_she_v, recipe.ph, recipe.temperature_c
        if target_reference.strip() and target_reference.strip().casefold() != source_reference.strip().casefold():
            sources = {source_reference.strip().casefold(), target_reference.strip().casefold()}
            if source_reference.strip().casefold() not in {'she', 'rhe'}:
                source_she = _float(st, 'Recorded reference offset vs SHE (V)', name + '_source_she', source_she)
            if target_reference.strip().casefold() not in {'she', 'rhe'}:
                target_she = _float(st, 'Target reference offset vs SHE (V)', name + '_target_she', target_she)
            if 'rhe' in sources:
                pht = st.columns(2)
                ph = _float(pht[0], 'Measured pH', name + '_ph', ph)
                temperature = _float(pht[1], 'Measured temperature (°C)', name + '_temperature', temperature)
        resistance = _float(st, 'Uncompensated resistance (Ω)', name + '_resistance', recipe.resistance_ohm,
                            help='Leave blank to omit iR correction. Corrected potential equals measured potential minus measured current times resistance.')
        current_column, current_unit = recipe.current_column, recipe.current_unit
        if resistance is not None:
            labels = ['', *columns]
            current_column = st.selectbox('Measured current column', labels, index=labels.index(current_column),
                                          format_func=lambda value: value or 'Use current axis', persist_state='session', key=_key(name + '_correction_current'))
            if current_column:
                current_unit = st.text_input('Measured current unit override', current_unit, persist_state='session', key=_key(name + '_correction_unit'))
            else:
                current_unit = ''
    with st.expander('Row mask, baseline and smoothing'):
        rows = st.text_input('Mask acquisition rows', ', '.join(map(str, recipe.excluded_rows)),
                             persist_state='session', key=_key(name + '_excluded_rows'), placeholder='E.g. 1, 9-12')
        crop_enabled = st.checkbox('Crop processed values to an x interval', value=recipe.window is not None,
                                   persist_state='session', key=_key(name + '_crop_enabled'))
        baseline_names = ['none', 'Linear', 'Polynomial']
        baseline = st.selectbox('Baseline subtraction', baseline_names, index=baseline_names.index(recipe.baseline),
                                persist_state='session', key=_key(name + '_baseline'))
        baseline_degree = recipe.baseline_degree
        if baseline == 'Polynomial':
            baseline_degree = st.number_input('Polynomial degree', min_value=1, max_value=8, value=baseline_degree,
                                              persist_state='session', key=_key(name + '_baseline_degree'))
        smoothing_names = ['none', 'Moving average', 'Savitzky–Golay']
        smoothing = st.selectbox('Smoothing', smoothing_names, index=smoothing_names.index(recipe.smoothing),
                                 persist_state='session', key=_key(name + '_smoothing'))
        smoothing_window, smoothing_degree = recipe.smoothing_window, recipe.smoothing_degree
        if smoothing != 'none':
            smoothing_window = st.number_input('Smoothing window (samples)', min_value=3, value=smoothing_window, step=2,
                                               persist_state='session', key=_key(name + '_smooth_count'))
            st.caption('Smoothing follows acquisition order within each selected cycle.')
        if smoothing == 'Savitzky–Golay':
            smoothing_degree = st.number_input('Savitzky–Golay polynomial degree', min_value=0, value=smoothing_degree,
                                               persist_state='session', key=_key(name + '_smooth_degree'))
        # Render all interval widgets even while an enabled interval is incomplete.
        errors = []
        try:
            window = _interval(st, name + '_crop', recipe.window) if crop_enabled else None
        except ValueError as exc:
            errors.append(str(exc))
            window = None
        try:
            if baseline != 'none':
                st.caption('Baseline fitting interval in the corrected x axis unit.')
            baseline_window = _interval(st, name + '_baseline', recipe.baseline_window) if baseline != 'none' else None
        except ValueError as exc:
            errors.append(str(exc))
            baseline_window = None
        if errors:
            raise ValueError(' '.join(dict.fromkeys(errors)))
    return curves.Recipe(x=x, y=y, x_unit=x_unit.strip(), y_unit=y_unit.strip(), cycle_column=cycle_column,
                         cycles=tuple(cycles), excluded_rows=_row_numbers(rows), window=window,
                         current_output=output, area_cm2=area, source_reference=source_reference.strip(),
                         target_reference=target_reference.strip(), source_she_v=source_she, target_she_v=target_she,
                         ph=ph, temperature_c=temperature, resistance_ohm=resistance, current_column=current_column,
                         current_unit=current_unit.strip(), baseline=baseline, baseline_window=baseline_window,
                         baseline_degree=baseline_degree, smoothing=smoothing, smoothing_window=smoothing_window,
                         smoothing_degree=smoothing_degree)


def _recorded_recipe(recipe):
    return curves.Recipe(recipe.x, recipe.y, x_unit=recipe.x_unit, y_unit=recipe.y_unit,
                         cycle_column=recipe.cycle_column, source_reference=recipe.source_reference)


def _active_style():
    return st.session_state.get(_key('active_style'), _STYLES['Publication'])


def _trace(curve, label, style, index, *, legend=True):
    frame = curve.frame
    return go.Scatter(x=frame['x'], y=frame['y'], name=label, mode='lines+markers', showlegend=legend,
                      connectgaps=False, marker={'size': 4},
                      line={'color': style['colors'][index % len(style['colors'])], 'width': style['width'],
                            'dash': _DASHES[index % len(_DASHES)]},
                      customdata=frame['row'] if 'row' in frame else None,
                      hovertemplate='x %{x:.6g}<br>y %{y:.6g}' + ('<br>Row %{customdata}' if 'row' in frame else '') + '<extra>%{fullData.name}</extra>')


def figure_curves(items, style, layout='Overlay'):
    """Make overlaid, linked-x or independent side-by-side Plotly views."""
    if layout == 'Overlay':
        curves.require_compatible([curve for _, curve in items])
        figure = go.Figure([_trace(curve, label, style, index) for index, (label, curve) in enumerate(items)])
        figure.update_xaxes(title_text=items[0][1].x.title)
        figure.update_yaxes(title_text=items[0][1].y.title)
    else:
        linked = layout == 'Linked x-axis subplots'
        if linked:
            curves.require_compatible([curve for _, curve in items], y=False)
        count = len(items)
        columns = 1 if linked else min(3, count)
        rows = count if linked else (count + columns - 1) // columns
        figure = make_subplots(rows=rows, cols=columns, shared_xaxes=linked,
                               subplot_titles=[label for label, _ in items],
                               vertical_spacing=min(.12, .4 / rows))
        for index, (label, curve) in enumerate(items):
            row, column = index // columns + 1, index % columns + 1
            figure.add_trace(_trace(curve, label, style, index, legend=False), row=row, col=column)
            figure.update_xaxes(title_text=curve.x.title if not linked or row == rows else None, row=row, col=column)
            figure.update_yaxes(title_text=curve.y.title, row=row, col=column)
        figure.update_layout(height=max(370, (260 if linked else 340) * rows))
    _format_figure(figure, style)
    return figure


def _format_figure(figure, style):
    figure.update_layout(template=style['template'], font={'size': style['font_size']},
                         margin={'l': 38, 'r': 18, 't': 42, 'b': 35}, dragmode='zoom',
                         legend={'orientation': 'h', 'yanchor': 'bottom', 'y': 1.04})
    figure.update_xaxes(showgrid=style['grid'])
    figure.update_yaxes(showgrid=style['grid'])


def _summary_figure(result, curve, style):
    figure = go.Figure()
    color = style['colors'][0]
    figure.add_scatter(x=result['x'], y=result['ci_high'], mode='lines', showlegend=False, line={'width': 0},
                       hoverinfo='skip')
    figure.add_scatter(x=result['x'], y=result['ci_low'], mode='lines', name='Confidence interval',
                       fill='tonexty', fillcolor='rgba(68, 129, 181, .25)', line={'width': 0}, hoverinfo='skip')
    figure.add_scatter(x=result['x'], y=result['mean'], mode='lines+markers', name='Mean',
                       line={'color': color, 'width': style['width']}, marker={'size': 4},
                       error_y={'type': 'data', 'array': result['sd'], 'visible': True, 'color': color, 'thickness': 1})
    figure.update_xaxes(title=curve.x.title)
    figure.update_yaxes(title=curve.y.title)
    _format_figure(figure, style)
    return figure


def _selection_window(event):
    selection = event.get('selection', {})
    x = [point['x'] for point in selection.get('points', []) if 'x' in point and isinstance(point['x'], (int, float))]
    if not x:
        for box in selection.get('box', []):
            x.extend(value for value in box.get('x', []) if isinstance(value, (int, float)))
    if len(x) > 1 and np.isfinite(x).all() and min(x) < max(x):
        return min(x), max(x)
    return None


def _selection_rows(event):
    values = [point.get('customdata') for point in event.get('selection', {}).get('points', [])]
    return sorted({int(value) for value in values if isinstance(value, (int, float)) and np.isfinite(value)
                   and value >= 1 and value == int(value)})


def _pick_annotation(event, figure):
    points = event.get('selection', {}).get('points', [])
    if len(points) != 1:
        return None
    point = points[0]
    x, y, index = point.get('x'), point.get('y'), point.get('curve_number', 0)
    if not isinstance(index, int) or not 0 <= index < len(figure.data):
        return None
    if any(not isinstance(value, (float, int)) or not np.isfinite(value) for value in (x, y)):
        return None
    trace = figure.data[index]
    panel = int((trace.xaxis or 'x')[1:] or '1')
    return x, y, panel


def _stage_window(source_name, interval, workspace, sources):
    name = _key(source_name + '_crop')
    st.session_state[name + '_enabled'] = True
    st.session_state[name + '_lower'], st.session_state[name + '_upper'] = interval
    record_operation(workspace, 'Stage curve processing window', inputs=[source['table_path'] for source in sources],
                     parameters={'interval': interval})


def _stage_rows(source_name, rows, workspace, sources):
    key = _key(source_name + '_excluded_rows')
    try:
        existing = _row_numbers(st.session_state.get(key, ''))
    except ValueError as exc:
        st.session_state[_key(source_name + '_mask_error')] = str(exc)
        return
    combined = sorted(set(existing) | set(rows))
    st.session_state[key] = ', '.join(map(str, combined))
    record_operation(workspace, 'Stage curve outlier mask', inputs=[source['table_path'] for source in sources],
                     parameters={'excluded_rows': combined})


def _save_plot(figure, *, workspace, sources, options):
    parent = Path(workspace) / 'Workbench' / 'Compare' / 'figures'
    parent.mkdir(parents=True, exist_ok=True)
    folder = Path(mkdtemp(prefix='figure-', dir=parent))
    html, json_path = folder / 'figure.html', folder / 'figure.json'
    figure.write_html(html, include_plotlyjs=True, config=_CHART_CONFIG, post_script=_EXPORT_SCRIPT)
    json_path.write_text(figure.to_json(), encoding='utf-8')
    record_operation(workspace, 'Export annotated curve figure', inputs=[source['table_path'] for source in sources],
                     parameters={'sources': sources, **options}, outputs=[html, json_path])
    return str(html), str(json_path)


def _plot(figure, chart_name, *, workspace, sources, allow_window=True, panels=1, processing_source=None):
    name = _key('plot_' + chart_name)
    annotations = st.session_state.get(name + '_annotations', [])
    for annotation in annotations:
        figure.add_annotation(**annotation)
    bounds = st.session_state.get(name + '_bounds') if allow_window else None
    if bounds:
        figure.update_xaxes(range=bounds)
    options = {'key': name, 'config': _CHART_CONFIG, 'width': 'stretch', 'theme': None}
    selection = st.plotly_chart(figure, on_select='rerun', selection_mode=('box', 'lasso', 'points'), **options)
    if allow_window:
        with st.expander('Plot window'):
            st.caption('Use the box or lasso tool on the chart to select samples, or enter bounds directly.')
            picked = _selection_window(selection)
            if st.button('Use selected x interval', disabled=picked is None, key=name + '_use_selection'):
                st.session_state[name + '_minimum'], st.session_state[name + '_maximum'] = picked
            if st.button('Reset plot window', disabled=bounds is None, key=name + '_reset_window'):
                st.session_state.pop(name + '_bounds', None)
                st.session_state.pop(name + '_minimum', None)
                st.session_state.pop(name + '_maximum', None)
                record_operation(workspace, 'Reset curve plot window', inputs=[source['table_path'] for source in sources])
                st.rerun()
            a, b = st.columns(2)
            minimum = a.number_input('View minimum x', value=bounds[0] if bounds else None, format='%.6g', persist_state='session', key=name + '_minimum')
            maximum = b.number_input('View maximum x', value=bounds[1] if bounds else None, format='%.6g', persist_state='session', key=name + '_maximum')
            if st.button('Apply plot window', key=name + '_set_window'):
                if minimum is None or maximum is None or not np.isfinite([minimum, maximum]).all() or minimum >= maximum:
                    st.error('Enter finite bounds with minimum below maximum.')
                else:
                    st.session_state[name + '_bounds'] = [minimum, maximum]
                    record_operation(workspace, 'Set curve plot window', inputs=[source['table_path'] for source in sources],
                                     parameters={'bounds': [minimum, maximum]})
                    st.rerun()
            if processing_source and chart_name.endswith(('Processed preview', 'Applied result')):
                interval = picked or bounds
                st.button('Use selection or view window as processing crop', key=name + '_stage_window',
                          disabled=interval is None, on_click=_stage_window,
                          args=(processing_source, interval, workspace, sources))
    if processing_source:
        rows = _selection_rows(selection)
        st.button('Mask selected acquisition rows', key=name + '_mask_selection', disabled=not rows,
                  on_click=_stage_rows, args=(processing_source, rows, workspace, sources))
        error = st.session_state.pop(_key(processing_source + '_mask_error'), '')
        if error:
            st.error(error)
    with st.expander('Annotations and figure export'):
        point = _pick_annotation(selection, figure)
        if st.button('Use selected point coordinates', key=name + '_label_point', disabled=point is None):
            x, y, panel = point
            st.session_state[name + '_annotation_text'] = f'({x:.5g}, {y:.5g})'
            st.session_state[name + '_annotation_x'], st.session_state[name + '_annotation_y'] = float(x), float(y)
            if panels > 1:
                st.session_state[name + '_annotation_panel'] = panel
        if st.checkbox('Find local peaks', persist_state='session', key=name + '_peaks'):
            available = {i: trace for i, trace in enumerate(figure.data) if 'markers' in (trace.mode or '')}
            if available:
                i = st.selectbox('Peak series', list(available), format_func=lambda i: available[i].name,
                                 persist_state='session', key=name + '_peak_trace')
                kind = st.radio('Peak type', ['Maxima', 'Minima'], horizontal=True, persist_state='session', key=name + '_peak_kind')
                prominence = st.number_input('Minimum peak prominence in y units', min_value=0., value=None,
                                             format='%.6g', persist_state='session', key=name + '_peak_threshold')
                trace = available[i]
                indexes = curves.local_peak_points(trace.x, trace.y, maxima=kind == 'Maxima', prominence=prominence, interval=bounds)
                if indexes:
                    peak = st.selectbox('Peak coordinate', indexes,
                                        format_func=lambda row: f'({trace.x[row]:.5g}, {trace.y[row]:.5g})',
                                        persist_state='session', key=name + '_peak_index')
                    if st.button('Use peak coordinates', key=name + '_label_peak'):
                        x, y = float(trace.x[peak]), float(trace.y[peak])
                        label = 'Maximum' if kind == 'Maxima' else 'Minimum'
                        st.session_state[name + '_annotation_text'] = f'{label} ({x:.5g}, {y:.5g})'
                        st.session_state[name + '_annotation_x'], st.session_state[name + '_annotation_y'] = x, y
                        if panels > 1:
                            st.session_state[name + '_annotation_panel'] = int((trace.xaxis or 'x')[1:] or '1')
                else:
                    st.info('No local peaks meet the options in this view.')
        text = st.text_input('Annotation text', persist_state='session', key=name + '_annotation_text')
        a, b = st.columns(2)
        x = a.number_input('Annotation x', value=None, format='%.6g', persist_state='session', key=name + '_annotation_x')
        y = b.number_input('Annotation y', value=None, format='%.6g', persist_state='session', key=name + '_annotation_y')
        panel = st.selectbox('Annotation panel', range(1, panels + 1), persist_state='session', key=name + '_annotation_panel') if panels > 1 else 1
        controls = st.columns(3)
        if controls[0].button('Add annotation', key=name + '_add_annotation'):
            if not text.strip() or x is None or y is None or not np.isfinite([x, y]).all():
                st.error('Enter annotation text and finite x and y coordinates.')
            else:
                suffix = '' if panel == 1 else str(panel)
                annotation = {'text': text.strip(), 'x': x, 'y': y, 'xref': 'x' + suffix, 'yref': 'y' + suffix,
                              'showarrow': True, 'ax': 22, 'ay': -45, 'arrowhead': 2, 'bgcolor': '#ffffff',
                              'font': {'color': '#1b2632'}}
                st.session_state[name + '_annotations'] = [*annotations, annotation]
                record_operation(workspace, 'Add curve annotation', inputs=[source['table_path'] for source in sources],
                                 parameters=annotation)
                st.rerun()
        if controls[1].button('Remove last annotation', disabled=not annotations, key=name + '_remove_annotation'):
            st.session_state[name + '_annotations'] = annotations[:-1]
            record_operation(workspace, 'Remove curve annotation', inputs=[source['table_path'] for source in sources],
                             parameters=annotations[-1])
            st.rerun()
        st.caption('Drag annotation labels directly on the plot. The camera saves the current view as PNG. '
                   'The saved interactive HTML also lets you drag labels and download their updated positions.')
        if controls[2].button('Save interactive figure', key=name + '_save_figure'):
            try:
                st.session_state[name + '_figure'] = _save_plot(figure, workspace=workspace, sources=sources,
                                                               options={'style': _active_style(), 'window': bounds})
            except (ValueError, OSError) as exc:
                st.error(str(exc))
        files = st.session_state.get(name + '_figure', [])
        if len(files) == 2 and all(Path(path).is_file() for path in files):
            st.download_button('Download interactive HTML', Path(files[0]).read_bytes(), 'figure.html', 'text/html',
                               key=name + '_download_html', on_click='ignore')
            st.download_button('Download Plotly JSON', Path(files[1]).read_bytes(), 'figure.json', 'application/json',
                               key=name + '_download_json', on_click='ignore')


def _save_result(frame, workspace, sources, operation, settings, name, *, curve=None):
    key = _key('save_' + name + '_' + _id(settings))
    if st.button('Save derived CSV and settings', key=key):
        try:
            outputs = curves.write_derived(workspace, frame, operation=operation, settings=settings, sources=sources, curve=curve)
            record_operation(workspace, operation, inputs=[source['table_path'] for source in sources],
                             parameters={'settings': settings, 'sources': sources}, outputs=outputs)
            st.session_state[key + '_files'] = list(map(str, outputs))
        except (OSError, ValueError) as exc:
            st.error(str(exc))
    files = st.session_state.get(key + '_files', [])
    if len(files) == 2 and all(Path(path).is_file() for path in files):
        st.success(f'Saved {files[0]}')
        a, b = st.columns(2)
        a.download_button('Download derived CSV', Path(files[0]).read_bytes(), 'derived.csv', 'text/csv',
                          key=key + '_csv', on_click='ignore')
        b.download_button('Download settings JSON', Path(files[1]).read_bytes(), 'settings.json', 'application/json',
                          key=key + '_json', on_click='ignore')


def _processing(data, path, workspace):
    name = _source_key(data, path)
    table = _read(data, path)
    history_key, entries, cursor = _history(data, path, table)
    recipe = curves.recipe_from_dict(entries[cursor])
    try:
        draft = _recipe_widgets(table, recipe, name)
    except ValueError as exc:
        st.error(str(exc))
        draft = None
    raw = processed = preview = None
    if draft is not None:
        try:
            raw = curves.prepare_curve(table, _recorded_recipe(draft))
            preview = curves.process_curve(table, draft)
        except ValueError as exc:
            st.warning(str(exc))
    if raw is None and draft is None:
        try:
            raw = curves.prepare_curve(table, _recorded_recipe(recipe))
        except ValueError:
            pass
    try:
        processed = curves.process_curve(table, recipe)
    except ValueError:
        pass
    controls = st.columns(3)
    if controls[0].button('Apply processing', disabled=preview is None, key=_key(name + '_apply')):
        _update_recipe(data, path, workspace, history_key, entries, cursor, recipe=draft)
    if controls[1].button('Undo processing', disabled=cursor == 0, key=_key(name + '_undo')):
        _update_recipe(data, path, workspace, history_key, entries, cursor, step=-1)
    if controls[2].button('Redo processing', disabled=cursor + 1 == len(entries), key=_key(name + '_redo')):
        _update_recipe(data, path, workspace, history_key, entries, cursor, step=1)
    views = {'Raw': raw, 'Processed preview': preview, 'Applied result': processed, 'Raw and processed': raw and preview}
    mode = st.radio('Curve preview', list(views), horizontal=True, persist_state='session', key=_key(name + '_preview'))
    current = views[mode]
    if current is not None:
        if mode == 'Raw':
            st.caption('Recorded measurements are displayed in base units, before filters or corrections.')
        style = _active_style()
        title = _selected(data, path, style['legend_fields'])
        if mode == 'Raw and processed':
            figure = figure_curves([(f'{title} · Raw', raw), (f'{title} · Processed preview', preview)], style, 'Side by side')
            _plot(figure, name + '_' + mode, workspace=workspace, sources=[_source(data, path)],
                  allow_window=False, panels=2, processing_source=name)
        else:
            figure = figure_curves([(f'{title} · {mode}', current)], style)
            _plot(figure, name + '_' + mode, workspace=workspace, sources=[_source(data, path)], processing_source=name)
        with st.expander('Preview values'):
            st.dataframe(current.frame, hide_index=True)
    else:
        st.info('Choose valid measurement columns and settings to show this preview.')
    with st.expander('Applied processing recipe'):
        st.json(entries[cursor])
    if processed is not None:
        _save_result(processed.frame, workspace, [_source(data, path)], 'Save processed curve', entries[cursor],
                     name, curve=processed)
        if 'cycle' in processed.frame and processed.frame['cycle'].nunique() > 1:
            with st.expander('Average selected cycles'):
                st.caption('Choose the same sweep number in each included cycle.')
                sweep = st.number_input('Sweep number', min_value=1, value=1, persist_state='session', key=_key(name + '_average_sweep'))
                confidence = _confidence_widget(name + '_cycle_ci')
                def calculate():
                    return curves.average_cycles(processed, sweep, confidence)
                settings = {'recipe': entries[cursor], 'sweep': sweep, 'confidence': confidence}
                sources = [_source(data, path)]
                summary = _calculate(name + '_cycle_summary', 'Calculate cycle average', calculate, workspace, sources, settings)
                if summary is not None:
                    _plot(_summary_figure(summary, processed, _active_style()), name + '_cycle_mean',
                          workspace=workspace, sources=sources)
                    st.dataframe(summary, hide_index=True)
                    _save_result(summary, workspace, sources, 'Save cycle statistics', settings, name + '_cycle', curve=processed)


def _confidence_widget(name):
    return st.number_input('Confidence level (%)', min_value=1.0, max_value=99.9, value=95.0, step=.5,
                           persist_state='session', key=_key(name)) / 100


def _calculate(name, label, calculate, workspace, sources, settings):
    key = _key('calculation_' + name)
    fingerprint = _id([sources, settings])
    run = st.button(label, key=key + '_run')
    if not run and st.session_state.get(key) != fingerprint:
        return None
    try:
        result = calculate()
    except ValueError as exc:
        st.error(str(exc))
        return None
    if run:
        st.session_state[key] = fingerprint
        record_operation(workspace, label, inputs=[source['table_path'] for source in sources],
                         parameters={'settings': settings, 'sources': sources})
    return result


def _branch_choice(curve, name, label):
    branches = dict(curves.sweep_branches(curve))
    if len(branches) > 1:
        choice = st.selectbox(f'Sweep for {label}', ['', *branches], format_func=lambda branch: branch or 'Select a single sweep',
                              persist_state='session', key=_key('branch_' + name))
        return branches.get(choice), choice
    return curve, ''


def _comparison_data(data, paths, use_applied):
    items, recipes = {}, {}
    for path in paths:
        try:
            table = _read(data, path)
            _, history, cursor = _history(data, path, table)
            recipe = curves.recipe_from_dict(history[cursor])
            if not use_applied:
                recipe = _recorded_recipe(recipe)
            items[path] = curves.process_curve(table, recipe)
            recipes[str(path)] = asdict(recipe)
        except (ValueError, OSError, KeyError) as exc:
            st.error(f'{_selected(data, path)}: {exc}')
    return items, recipes


def _effect_comparison(data, paths, items, workspace, settings, name):
    labels = lambda path: _picker_label(data, path)
    control = st.multiselect('Control measurements', paths, format_func=labels, persist_state='session', key=_key(name + '_control'))
    treatment = st.multiselect('Treatment measurements', paths, format_func=labels, persist_state='session', key=_key(name + '_treatment'))
    paired = st.radio('Groups', ['Unpaired', 'Paired'], horizontal=True, persist_state='session', key=_key(name + '_pair_mode')) == 'Paired'
    pair_tag = ''
    if paired:
        tag_maps = {path: curves.metadata_tags(data.records.get(path, {})) for path in [*control, *treatment]}
        common_tags = sorted(set.intersection(*(set(tags) for tags in tag_maps.values()))) if tag_maps else []
        pair_tag = st.selectbox('Match pairs by', ['', *common_tags], format_func=lambda tag: tag or 'Selection order',
                                persist_state='session', key=_key(name + '_pair_tag'))
        if pair_tag:
            controls = {tag_maps[path][pair_tag]: path for path in control}
            treatments = {tag_maps[path][pair_tag]: path for path in treatment}
            if len(controls) != len(control) or len(treatments) != len(treatment) or set(controls) != set(treatments):
                raise ValueError('Paired groups require one unique measurement per shared value of the selected tag.')
            treatment = [treatments[tag_maps[path][pair_tag]] for path in control]
        else:
            st.caption('Select each group in matched order. The table shows the pairs used for inference.')
        if len(control) == len(treatment) and control:
            st.dataframe(pd.DataFrame({'Control': list(map(labels, control)), 'Treatment': list(map(labels, treatment))}), hide_index=True)
    metric = st.selectbox('One value per measurement', ['Mean y', 'Maximum y', 'Minimum y'], persist_state='session', key=_key(name + '_effect_metric'))
    interval = None
    if st.checkbox('Restrict effect to an x interval', persist_state='session', key=_key(name + '_effect_window')):
        try:
            interval = _interval(st, name + '_effect_interval')
        except ValueError as exc:
            st.info(str(exc))
            return
    confidence = _confidence_widget(name + '_effect_ci')
    selected = [*control, *treatment]
    sources = [_source(data, path) for path in selected]
    settings.update(control=list(map(str, control)), treatment=list(map(str, treatment)), paired=paired, pair_tag=pair_tag,
                    metric=metric, x_interval=interval, confidence=confidence)

    def calculate():
        if set(control) & set(treatment):
            raise ValueError('A measurement can belong to only one group.')
        curves.require_independent_acquisitions(sources)
        group = [items[path] for path in selected]
        curves.require_compatible(group, x=interval is not None, known_reference=_independent_sources(sources))
        summarize = {'Mean y': np.mean, 'Maximum y': np.max, 'Minimum y': np.min}[metric]
        values = []
        for path in selected:
            curve = items[path]
            if interval is not None:
                if curve.frame['x'].min() > interval[0] or curve.frame['x'].max() < interval[1]:
                    raise ValueError(f'{labels(path)} does not span the entire requested x interval.')
                curve = curves.window_curve(curve, interval)
            values.append(float(summarize(curve.frame['y'])))
        result = curves.treatment_effect(values[:len(control)], values[len(control):], paired=paired, confidence=confidence)
        return pd.DataFrame([{**result, 'unit': group[0].y.unit}])

    result = _calculate(name + '_effect', 'Calculate treatment effect', calculate, workspace, sources, settings)
    if result is not None:
        st.dataframe(result, hide_index=True)
        _save_result(result, workspace, sources, 'Save treatment effect', settings, name + '_effect')


def _comparisons(data, paths, workspace):
    if not paths:
        st.info('Select measurements to compare.')
        return
    name = _id([_source_key(data, path) for path in paths])
    input_mode = st.radio('Comparison inputs', ['Applied processing', 'As recorded'], horizontal=True,
                          persist_state='session', key=_key(name + '_comparison_inputs'))
    items, recipes = _comparison_data(data, paths, input_mode == 'Applied processing')
    if len(items) != len(paths):
        st.info('Set the columns, units and row masks for these measurements in Process one measurement.')
        return
    operation = st.radio('Comparison', ['Plot', 'Difference', 'Ratio', 'Replicate statistics', 'Control and treatment'],
                         horizontal=True, persist_state='session', key=_key(name + '_comparison_operation'))
    style = _active_style()
    labels = lambda path: _selected(data, path, style['legend_fields'])
    sources = [_source(data, path) for path in paths]
    settings = {'input_mode': input_mode, 'recipes': recipes, 'operation': operation}
    if operation == 'Plot':
        layout = st.radio('Plot layout', ['Overlay', 'Linked x-axis subplots', 'Side by side'], horizontal=True,
                          persist_state='session', key=_key(name + '_layout'))
        figure = figure_curves([(labels(path), items[path]) for path in paths], style, layout)
        _plot(figure, name + '_comparison_' + layout, workspace=workspace, sources=sources,
              allow_window=layout != 'Side by side', panels=len(paths) if layout != 'Overlay' else 1)
        return
    if operation == 'Control and treatment':
        _effect_comparison(data, paths, items, workspace, settings, name)
        return
    if operation == 'Replicate statistics':
        tags = {path: curves.metadata_tags(data.records.get(path, {})) for path in paths}
        tag_keys = sorted({key for values in tags.values() for key in values})
        if tag_keys:
            tag = st.selectbox('Replicate group tag', ['', *tag_keys], format_func=lambda value: value or 'All selected measurements',
                               persist_state='session', key=_key(name + '_replicate_tag'))
            if tag:
                values = sorted({values[tag] for values in tags.values() if tag in values})
                value = st.selectbox('Replicate group value', values, persist_state='session', key=_key(name + '_replicate_value_' + tag))
                paths = [path for path in paths if tags[path].get(tag) == value]
                settings['group_tag'] = {tag: value}
    if len(paths) < 2:
        st.info('Select at least two measurements.')
        return
    if operation in {'Difference', 'Ratio'}:
        st.caption('The result uses measured x points from the first curve inside the shared range.')
        fields = st.columns(2)
        first_label, second_label = ('Minuend', 'Subtrahend') if operation == 'Difference' else ('Numerator', 'Denominator')
        picker_label = lambda path: _picker_label(data, path)
        first = fields[0].selectbox(first_label, paths, format_func=picker_label, persist_state='session', key=_key(name + '_' + first_label))
        second = fields[1].selectbox(second_label, paths, index=1, format_func=picker_label, persist_state='session', key=_key(name + '_' + second_label))
        if first == second:
            raise ValueError('Choose two different measurements for arithmetic.')
        paths = [first, second]
    selected = [_branch_choice(items[path], _source_key(data, path) + '_' + name, _picker_label(data, path)) for path in paths]
    if any(part is None for part, _ in selected):
        st.info('Choose one sweep per reversing curve before interpolation.')
        return
    branches = [part for part, _ in selected]
    sources = [_source(data, path) for path in paths]
    settings.update(measurements=list(map(str, paths)), branches=[branch for _, branch in selected])
    if operation in {'Difference', 'Ratio'}:
        def calculate():
            curves.require_compatible(branches, known_reference=_independent_sources(sources))
            return curves.compare_curves(*branches, operation)
        result = _calculate(name + '_' + operation, f'Calculate {operation.lower()}', calculate,
                            workspace, sources, settings)
        if result is not None:
            figure = figure_curves([(operation, result)], style)
            _plot(figure, name + '_' + operation, workspace=workspace, sources=sources)
            st.dataframe(result.frame, hide_index=True)
            _save_result(result.frame, workspace, sources, f'Save {operation.lower()}', settings, name + operation, curve=result)
        return
    confidence = _confidence_widget(name + '_replicate_ci')
    settings['confidence'] = confidence
    def calculate():
        curves.require_independent_acquisitions(sources)
        curves.require_compatible(branches, known_reference=_independent_sources(sources))
        return curves.summarize_replicates(branches, confidence)
    result = _calculate(name + '_replicates', 'Calculate replicate statistics',
                        calculate, workspace, sources, settings)
    if result is not None:
        st.caption('Error bars show sample SD. The shaded region is the pointwise Student t confidence interval for the mean.')
        _plot(_summary_figure(result, branches[0], style), name + '_replicate_mean', workspace=workspace, sources=sources)
        st.dataframe(result, hide_index=True)
        _save_result(result, workspace, sources, 'Save replicate statistics', settings, name + '_replicates', curve=branches[0])


def _batch(data, workspace):
    names = curves.load_named(workspace, 'recipes')
    paths = list(data.tables)
    source = st.selectbox('Use applied settings from', paths, format_func=lambda path: _picker_label(data, path), persist_state='session', key=_key('batch_source'))
    _, entries, cursor = _history(data, source, _read(data, source))
    selected = st.selectbox('Batch recipe', ['', *names], format_func=lambda value: value or 'Use applied settings', persist_state='session', key=_key('batch_recipe'))
    if selected:
        record = names[selected]
        recipe = curves.recipe_from_dict(record['recipe'])
        origin = record.get('origin', {})
    else:
        recipe = curves.recipe_from_dict(entries[cursor])
        origin = _source(data, source)
    settings = asdict(recipe)
    with st.expander('Recipe settings and origin', expanded=True):
        st.json({'recipe': settings, 'origin': origin})
    name = st.text_input('Recipe name', persist_state='session', key=_key('batch_name'))
    if st.button('Save batch recipe', key=_key('batch_save')):
        output = curves.save_named(workspace, 'recipes', name, {'recipe': settings, 'origin': origin})
        record_operation(workspace, 'Save batch processing recipe', inputs=[origin.get('table_path', source)],
                         parameters={'recipe': settings, 'name': name, 'origin': origin}, outputs=[output])
        st.session_state[_key('batch_saved_message')] = f'Saved recipe {name.strip()}.'
        st.rerun()
    if _key('batch_saved_message') in st.session_state:
        st.success(st.session_state.pop(_key('batch_saved_message')))
    selected_paths = st.multiselect('Batch measurements', paths, default=[source],
                                    format_func=lambda path: _picker_label(data, path), persist_state='session', key=_key('batch_files'))
    sources = [_source(data, path) for path in selected_paths]
    fingerprint = _id([settings, sources])
    st.caption('Check that the column mappings, units, electrode area and reference corrections apply to every selected measurement.')
    reviewed = st.checkbox('I reviewed this recipe for the selected measurements', persist_state='session', key=_key('batch_review_' + fingerprint))
    if st.button('Run reviewed batch recipe', disabled=not reviewed or not selected_paths, key=_key('batch_run')):
        result = curves.run_batch(selected_paths, read_table=lambda path: _read(data, path), recipe=recipe,
                                  workspace=workspace, metadata=data.records)
        csv, json_path = curves.write_derived(workspace, result, operation='Batch curve processing status',
                                              settings={'recipe': settings, 'origin': origin}, sources=sources)
        files = [file for column in ('data_csv', 'settings_json') for file in result[column] if file]
        record_operation(workspace, 'Run reviewed batch processing recipe', inputs=selected_paths,
                         parameters={'recipe': settings, 'name': selected, 'origin': origin, 'sources': sources},
                         outputs=[csv, json_path, *files])
        st.session_state[_key('batch_last_result')] = [fingerprint, str(csv), str(json_path)]
    last = st.session_state.get(_key('batch_last_result'), [])
    if len(last) == 3 and last[0] == fingerprint and Path(last[1]).is_file():
        st.dataframe(pd.read_csv(last[1], keep_default_na=False), hide_index=True)
        st.download_button('Download batch status CSV', Path(last[1]).read_bytes(), 'batch-status.csv', 'text/csv',
                           key=_key('batch_download'), on_click='ignore')


def _plot_style(data, workspace):
    named = curves.load_named(workspace, 'styles')
    options = {**_STYLES, **named}
    choice = st.selectbox('Style preset', list(options), persist_state='session', key=_key('style_base'))
    base = options[choice]
    name = _id(choice)
    a, b = st.columns(2)
    size = a.slider('Font size', min_value=10, max_value=28, value=base['font_size'], persist_state='session', key=_key(name + '_style_size'))
    width = b.slider('Line width', min_value=.5, max_value=6., value=base['width'], step=.1, persist_state='session', key=_key(name + '_style_width'))
    grid = st.checkbox('Show grid', value=base['grid'], persist_state='session', key=_key(name + '_style_grid'))
    fields = st.multiselect('Legend fields', _LEGEND_FIELDS, default=base['legend_fields'], persist_state='session', key=_key(name + '_style_legend'))
    st.caption('Legend fields use the recorded metadata for each measurement.')
    style = {**base, 'font_size': size, 'width': width, 'grid': grid, 'legend_fields': fields}
    if st.button('Apply plot style', key=_key('style_apply')):
        st.session_state[_key('active_style')] = style
        record_operation(workspace, 'Apply curve plot style', parameters={'preset': choice, 'style': style})
        st.success('Plot style applied.')
    save_name = st.text_input('Preset name', persist_state='session', key=_key('style_name'))
    if st.button('Save style preset', key=_key('style_save')):
        output = curves.save_named(workspace, 'styles', save_name, style)
        record_operation(workspace, 'Save curve style preset', parameters={'name': save_name, 'style': style}, outputs=[output])
        st.session_state[_key('style_saved_message')] = f'Saved style {save_name.strip()}.'
        st.rerun()
    if _key('style_saved_message') in st.session_state:
        st.success(st.session_state.pop(_key('style_saved_message')))
    path = st.selectbox('Preview measurement', data.tables, format_func=lambda path: _picker_label(data, path), persist_state='session', key=_key('style_preview'))
    table = _read(data, path)
    _, entries, cursor = _history(data, path, table)
    curve = curves.process_curve(table, curves.recipe_from_dict(entries[cursor]))
    figure = figure_curves([(_selected(data, path, fields), curve)], style)
    st.plotly_chart(figure, width='stretch', key=_key('style_sample'), config=_CHART_CONFIG, theme=None)


def _csv_matrix(text):
    try:
        return [[float(value) for value in line.split(',')] for line in text.splitlines() if line.strip()]
    except ValueError:
        raise ValueError('Enter comma-separated numbers with one matrix row per line.') from None


def _uncertainty(workspace):
    st.caption('Enter one sensitivity row per output. Use the same input order in every sensitivity row and covariance axis.')
    j = st.text_area('Sensitivities', placeholder='1, 2', persist_state='session', key=_key('uncertainty_sensitivities'))
    cov = st.text_area('Input covariance', placeholder='0.04, 0.01\n0.01, 0.09', persist_state='session', key=_key('uncertainty_covariance'))
    settings = {'sensitivities': j, 'covariance': cov}

    def calculate():
        covariance, uncertainties = curves.propagate_uncertainty(_csv_matrix(j), _csv_matrix(cov))
        count = len(uncertainties)
        return pd.DataFrame({'output': np.arange(1, count + 1), 'standard_uncertainty': uncertainties,
                             **{f'covariance_output_{index + 1}': covariance[:, index] for index in range(count)}})

    result = _calculate('uncertainty', 'Propagate uncertainty', calculate, workspace, [], settings)
    if result is not None:
        st.dataframe(result, hide_index=True)
        _save_result(result, workspace, [], 'Save propagated uncertainty', settings, 'uncertainty')


def render(data, *, workspace: Path):
    """Render the comparison and processing route for native and merged BrowserData."""
    if not data.tables:
        st.info('Open at least one measurement table to process or compare curves.')
        return
    routes = ('Process one measurement', 'Compare measurements', 'Batch recipes', 'Plot style', 'Uncertainty')
    mode = st.radio('Compare and process', routes, horizontal=True, persist_state='session', key=_key('route'))
    source_scope = 'project' if data.metadata.get('project_id') else _id(str(data.source))
    try:
        if mode == 'Process one measurement':
            path = st.selectbox('Measurement to process', data.tables, format_func=lambda path: _picker_label(data, path),
                                persist_state='session', key=_key('process_source_' + source_scope))
            _processing(data, path, workspace)
        elif mode == 'Compare measurements':
            paths = st.multiselect('Measurements to compare', data.tables, default=list(data.tables[:2]),
                                   format_func=lambda path: _picker_label(data, path),
                                   persist_state='session', key=_key('comparison_files_' + source_scope))
            _comparisons(data, paths, workspace)
        elif mode == 'Batch recipes':
            _batch(data, workspace)
        elif mode == 'Plot style':
            _plot_style(data, workspace)
        else:
            _uncertainty(workspace)
    except (ValueError, OSError, KeyError, IndexError) as exc:
        st.error(str(exc))
