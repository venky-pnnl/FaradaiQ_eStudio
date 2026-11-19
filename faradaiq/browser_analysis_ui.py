"""Compact measurement navigation and readable identities for the Analysis workspace."""
from __future__ import annotations

from pathlib import Path
from functools import partial
from hashlib import sha256
import re

import numpy as np
import pandas as pd
import streamlit as st


def is_support_record(data, path):
    record = data.records.get(path, {})
    declared = str(record.get('technique_key') or record.get('technique') or '').strip().upper()
    text = ' '.join((path.stem, str(record.get('series_title', '')), str(record.get('method_title', ''))))
    if declared in {'POSITION', 'METADATA'} or re.search(r'position[_ -]*takedata', text, re.I):
        return True
    return not declared and bool(re.search(r'(?:^|[/\\])position(?:[/\\]|$)', str(path), re.I))


def short_measurement_labels(data):
    """Short, unique captions; the full identity stays in provenance and source details."""
    rows = {}
    for path in data.tables:
        rec = data.records.get(path, {})
        name = str(rec.get('display_name') or rec.get('measurement_title') or rec.get('curve_title') or
                   rec.get('series_title') or path.stem)
        name = re.sub(r'[_]+', ' ', name).strip()
        if len(name) > 42:
            name = name[:21].rstrip() + '…' + name[-17:].lstrip()
        details = []
        if rec.get('instrument'):
            details.append(str(rec['instrument']))
        for key, caption in (('point', 'P'), ('point_index', 'P'), ('cycle', 'C'), ('sweep', 'S')):
            if rec.get(key) is not None and not any(part.startswith(caption + ' ') for part in details):
                details.append(f'{caption} {rec[key]}')
        for key in ('dc_potential_V', 'bias_potential_V'):
            if rec.get(key) is not None:
                try: details.append(f'{float(rec[key]):g} V')
                except (TypeError, ValueError): pass
                break
        rows[path] = name + (' · ' + ' · '.join(details) if details else '')
    counts = {}
    for label in rows.values(): counts[label] = counts.get(label, 0) + 1
    for index, path in enumerate(data.tables, 1):
        if counts[rows[path]] > 1:
            rows[path] += f' · #{index:03d}'
    return rows


def legend_labels(data, paths):
    short = short_measurement_labels(data)
    result = {}
    for path in paths:
        text = short[path]
        if len(text) > 34:
            # Keep the distinguishing source number/recorded conditions in view.
            text = text[:15].rstrip() + '…' + text[-16:].lstrip()
        result[path] = text
    # Truncation must not create ambiguous plotted identities.
    counts = {}
    for label in result.values(): counts[label] = counts.get(label, 0) + 1
    for index, path in enumerate(paths, 1):
        if counts[result[path]] > 1: result[path] += f' [{index}]'
    return result


def _choose_row(data, candidates, labels, key):
    selected = st.session_state.get(key, {}).get('selection', {}).get('rows', [])
    if selected and 0 <= selected[0] < len(candidates):
        path = candidates[selected[0]]
        st.session_state['table'] = path
        st.session_state['selected_table'] = labels[path]


def render_measurement_browser(data, labels):
    from .browser_productivity import measurement_label

    display = {p: measurement_label(data, p) for p in data.tables}
    short = short_measurement_labels(data)
    st.subheader('Measurements')
    query = st.text_input('Filter measurements', key='analysis_filter', placeholder='Search name, point, cycle…', label_visibility='collapsed')
    support = [p for p in data.tables if is_support_record(data, p)]
    show_support = st.checkbox('Include position / support records', value=len(support) == len(data.tables),
                               key='analysis_include_support', help='Position_Takedata and acquisition metadata remain available here.') if support else False
    terms = query.casefold().split()
    candidates = [p for p in data.tables if (show_support or p not in support)
                  and all(term in display[p].casefold() for term in terms)]
    if not candidates:
        st.info('No measurements match. Clear the search or include support records.')
        return None, [], display
    active_path = st.session_state.get('table')
    if active_path not in candidates:
        active_path = next((p for p in candidates if labels[p] == st.session_state.get('selected_table')), candidates[0])
    active = candidates.index(active_path)
    previous, following = st.columns(2)
    if previous.button('Previous measurement', icon=':material/chevron_left:', disabled=active == 0, help='Previous measurement', use_container_width=True):
        active -= 1
    if following.button('Next measurement', icon=':material/chevron_right:', disabled=active == len(candidates)-1, help='Next measurement', use_container_width=True):
        active += 1
    st.session_state['table'] = candidates[active]
    st.caption(f'{active + 1} of {len(candidates)} · {len(data.tables)} total')
    key = 'analysis-measurement-list-' + sha256(('\0'.join(map(str, candidates)) + f':{active}').encode()).hexdigest()[:16]
    rows = pd.DataFrame({'Measurement': [short[p] for p in candidates],
                         'Kind': ['Support' if p in support else str(data.records.get(p, {}).get('technique_key') or
                                  data.records.get(p, {}).get('technique') or '') for p in candidates]})
    st.dataframe(rows, hide_index=True, height=430, row_height=38, key=key,
                 selection_mode='single-row', selection_default={'selection': {'rows': [active]}},
                 on_select=partial(_choose_row, data, candidates, labels, key),
                 column_order=['Measurement'],
                 column_config={'Measurement': st.column_config.TextColumn(width=190)})
    with st.popover('Jump to measurement', use_container_width=True):
        path = st.selectbox('Active measurement', candidates, format_func=short.get, key='table')
    st.session_state['selected_table'] = labels[path]
    if support and not show_support:
        st.caption(f'{len(support)} position / support records hidden')
    return path, candidates, display


def render_measurement_summary(data, path, table, technique):
    short = short_measurement_labels(data)[path]
    st.subheader(short)
    record = data.records.get(path, {})
    parts = ['Position / support' if is_support_record(data,path) else technique, f'{len(table):,} rows']
    if record.get('instrument'): parts.append(str(record['instrument']))
    st.caption(' · '.join(parts))
    if is_support_record(data, path):
        st.info('Position / support record selected. Use an electrochemical measurement for current–potential analysis.')


def apply_analysis_style():
    st.html('''<style>
    [data-testid="stMainBlockContainer"] {padding-top:1.35rem; padding-bottom:1.5rem;}
    [data-testid="stSidebar"] {min-width:210px!important;max-width:245px!important;}
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {gap:.5rem;}
    [data-testid="stSidebar"] h1 {font-size:1.35rem;}
    .st-key-analysis-workspace h2 {font-size:1.5rem;padding:.1rem 0 .4rem;}
    .st-key-analysis-workspace h3 {font-size:1rem;line-height:1.35;padding:.15rem 0 .35rem;overflow-wrap:anywhere;}
    .st-key-analysis-workspace [data-testid="stVerticalBlock"] {gap:.55rem;}
    .st-key-analysis-measurements [data-testid="stButton"] button {font-size:.8rem;min-height:2rem;padding:.25rem .4rem;}
    .st-key-analysis-measurements [data-testid="stButton"] button p {font-size:0;}
    .st-key-analysis-measurements [data-testid="stButton"] button p::first-letter {font-size:0;}
    .st-key-analysis-controls [data-testid="stVerticalBlock"] {gap:.6rem;}
    .st-key-analysis-controls label p {font-size:.82rem;}
    .st-key-analysis-controls [data-testid="stExpander"] {background:transparent;}
    .st-key-analysis-canvas {min-width:0;}
    .st-key-analysis-workspace [data-testid="stColumn"] {min-width:0;}
    @media(max-width:1100px) {
      [data-testid="stSidebar"] {min-width:190px!important;max-width:210px!important;}
      .st-key-analysis-workspace [data-testid="stHorizontalBlock"] {flex-wrap:wrap;}
      .st-key-analysis-workspace [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {min-width:min(100%,260px);flex:1 1 300px;}
      .st-key-analysis-workspace [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {flex-grow:3;}
    }
    </style>''')
