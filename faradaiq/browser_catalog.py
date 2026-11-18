"""Search and inspect local eStudio records using the desktop catalog's metadata rules."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .browser_catalog_v209 import _normalize_record
from .browser_data import BrowserData
from .scilink_bundle import column_unit


def _table_rows(data: BrowserData) -> list[dict]:
    rows = []
    for index, path in enumerate(data.tables, 1):
        record = data.records.get(path, {})
        native = _normalize_record(path, record, data.bundle_manifest, index)
        recorded_units = record.get('units')
        if isinstance(recorded_units, dict):
            units = [value for value in recorded_units.values() if value]
        else:
            units = [record.get('x_unit'), record.get('y_unit'),
                     *(column_unit(column) for column in native['columns'])]
        name = path.relative_to(data.analysis_root).as_posix()
        rows.append({
            'Table': name,
            'Measurement': str(record.get('measurement_title') or record.get('curve_title')
                               or record.get('series_title') or record.get('name') or path.stem),
            'Technique': native['technique_key'],
            'Method': str(record.get('method_id') or record.get('eis_method') or record.get('method_entry')
                          or native['method_title'] or ''),
            'Point': native['point'] if native['point'] or 'point' in record or 'point_index' in record else None,
            'Cycle': native['cycle'] if native['cycle'] or 'cycle' in record else None,
            'Potential (V)': record.get('dc_potential_V', record.get('bias_potential_V')),
            'Scan rate (mV/s)': native['scan_rate_mV_s'],
            'Rows': record.get('n_points') or record.get('n_rows'),
            'Units': ', '.join(dict.fromkeys(str(unit) for unit in units if unit)),
            'Columns': ', '.join(native['columns']),
            'Source': str(record.get('relative_source_path') or record.get('source_path')
                          or native['source_file'] or data.source),
        })
    return rows


def _filter_rows(rows: list[dict], query: str, techniques: Iterable[str]) -> list[dict]:
    selected = set(techniques)
    terms = query.casefold().split()
    return [record for record in rows
            if (not selected or record['Technique'] in selected)
            and all(term in ' '.join(str(value).casefold() for value in record.values()) for term in terms)]


def catalog_entries(data: BrowserData, *, query: str = '', techniques: Iterable[str] = ()) -> list[dict]:
    """Report normalized experiment records in acquisition order."""
    return _filter_rows(_table_rows(data), query, techniques)


def render_catalog(data: BrowserData) -> None:
    """Render the local catalog, scientific metadata, and embedded bundle assets."""
    import pandas as pd
    import streamlit as st

    with st.expander('Search loaded measurements', expanded=True):
        st.code(str(data.analysis_root), language=None)
        stored = st.session_state.get('measurement_catalog')
        if stored is not None and stored[0] is data:
            rows = stored[1]
        else:
            rows = _table_rows(data)
            st.session_state['measurement_catalog'] = (data, rows)
        search, filter_column = st.columns(2)
        query = search.text_input('Search measurements', placeholder='Measurement, point, unit, or source file')
        techniques = filter_column.multiselect('Filter by technique', sorted({row['Technique'] for row in rows}))
        matches = _filter_rows(rows, query, techniques)
        st.caption(f'{len(matches)} of {len(rows)} measurements')
        if matches:
            frame = pd.DataFrame(matches)
            for numeric in ('Point', 'Cycle', 'Potential (V)', 'Scan rate (mV/s)', 'Rows'):
                frame[numeric] = pd.to_numeric(frame[numeric], errors='coerce')
            st.dataframe(frame, hide_index=True)
            choices = {record['Table']: record for record in matches}
            choice = st.selectbox('Measurement for analysis', list(choices))
            if st.button('Use this measurement in Analysis'):
                st.session_state['selected_table'] = choice
                st.session_state.pop('table', None)
                st.success(f'Selected {Path(choice).name}. Open Analysis to continue.')
        else:
            st.info('No measurements match the current filters.')
    if data.bundle_manifest or data.metadata:
        with st.expander('Source metadata'):
            st.json(data.bundle_manifest or data.metadata, expanded=False)
    if data.assets:
        with st.expander('Bundle media and documents'):
            kind = st.selectbox('File type', sorted({asset.kind for asset in data.assets}))
            choices = [asset for asset in data.assets if asset.kind == kind]
            asset = st.selectbox('File', choices, format_func=lambda item: item.title)
            st.code(str(asset.path), language=None)
            if asset.hdf5_dataset:
                st.caption(asset.hdf5_dataset)
            if asset.path.stat().st_size <= 50 * 1024 * 1024:
                st.download_button('Download file', asset.path.read_bytes(), file_name=Path(asset.title).name,
                                   key=f'catalog-asset:{asset.path}')
                if st.button('Preview file'):
                    suffix = asset.path.suffix.lower()
                    if suffix in {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tif', '.tiff', '.webp'}:
                        st.image(str(asset.path))
                    elif suffix in {'.mp4', '.webm'}:
                        st.video(str(asset.path))
                    elif suffix in {'.json', '.jsonl', '.txt', '.md', '.csv', '.tsv', '.log'}:
                        with asset.path.open(encoding='utf-8-sig', errors='replace') as stream:
                            st.code(stream.read(10000), language='json' if suffix == '.json' else None)
