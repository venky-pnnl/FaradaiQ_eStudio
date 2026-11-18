from __future__ import annotations

import hashlib
import json
from zipfile import ZipFile

import h5py
import pytest

from faradaiq.browser_data import classify_source, load_session, open_data, save_session


def _data(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('frequency_Hz,Zreal_ohm,Zimag_ohm\n1000,5,-2\n100,8,-7\n10,19,-6\n')
    return path


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_curated_directory_and_single_file_stay_scoped(tmp_path):
    source = _data(tmp_path / 'Experiment' / 'scan.csv')
    _data(source.with_name('unselected.csv'))
    before = _sha(source)
    workspace = tmp_path / 'Workspace'
    single = open_data(source, workspace)
    assert classify_source(source) == 'tables'
    assert [path.name for path in single.tables] == ['scan.csv']
    assert single.read_table(single.tables[0])['frequency_Hz'].tolist() == [1000, 100, 10]
    assert single.analysis_root.is_relative_to(workspace)
    assert len(open_data(source.parent, workspace).tables) == 2
    assert _sha(source) == before


def test_existing_manifest_headerless_curve_keeps_first_row_and_units(tmp_path):
    data_root = tmp_path / 'sample_analysis_ready_csv'
    data_root.mkdir()
    source = data_root / 'cycle.csv'
    source.write_text('0.1,10\n0.2,20\n0.3,30\n')
    entry = {'relative_csv_path': source.name, 'measurement_index': 2,
             'technique': 'CV', 'x_label': 'Potential', 'x_unit': 'V',
             'y_label': 'Current', 'y_unit': 'µA'}
    (tmp_path / 'sample_analysis_ready_manifest.json').write_text(json.dumps({'csv_entries': [entry]}))
    imported = open_data(data_root, tmp_path / 'Workspace')
    frame = imported.read_table(source)
    assert list(frame.columns) == ['Potential (V)', 'Current (µA)']
    assert frame.iloc[0].tolist() == [0.1, 10.0]
    assert imported.records[source]['measurement_index'] == 2


def test_vendor_solartron_uses_desktop_collection_and_export(tmp_path):
    from faradaiq.parsers.pssession import PSSessionCollection
    from faradaiq.scilink_bundle import make_scilink_ready_bundle

    native = tmp_path / 'Impedance.Z'
    native.write_text('ZPlot\nExperiment Type: EIS\n'
                      'End Comments\n1000 0.01 0 0 5 -2\n100 0.01 0 1 8 -7\n10 0.01 0 2 19 -6\n')
    before = _sha(native)
    expected = PSSessionCollection().load(str(native)).entries[0]['parser'].data['array']
    data = open_data(native, tmp_path / 'Workspace')
    assert classify_source(native) == 'instrument'
    assert data.metadata['n_measurements'] == 1
    eis = data.read_table(next(path for path in data.tables if 'DRT_input_csv' in path.parts))
    eis = eis.sort_values('frequency_Hz')
    assert eis['frequency_Hz'].tolist() == expected[:, 0].tolist()
    assert eis['Zimag_ohm'].tolist() == expected[:, 2].tolist()
    handoff = make_scilink_ready_bundle(data.analysis_root)
    with h5py.File(handoff['h5_path'], 'r') as h5:
        complete_spectra = [table for table in h5['tables'].values()
                            if json.loads(table.attrs['columns_json']) == ['frequency_Hz', 'Zreal_ohm', 'Zimag_ohm']]
        assert len(complete_spectra) == 1
        assert sorted(complete_spectra[0][()].tolist()) == sorted(expected[:, :3].tolist())
    assert _sha(native) == before


def test_zip_accepts_curated_folders_and_rejects_parent_paths(tmp_path):
    source = tmp_path / 'curated.zip'
    with ZipFile(source, 'w') as zipped:
        zipped.writestr('Sample/trace.csv', 'time_s,current_A\n0,1\n1,3\n')
    workspace = tmp_path / 'Workspace'
    data = open_data(source, workspace)
    assert data.read_table(data.tables[0])['current_A'].tolist() == [1, 3]
    unsafe = tmp_path / 'invalid.zip'
    with ZipFile(unsafe, 'w') as zipped:
        zipped.writestr('../escape.csv', 'time_s,current_A\n0,1\n1,3\n')
    with pytest.raises(ValueError, match='unsafe ZIP member'):
        open_data(unsafe, workspace)
    assert not (tmp_path / 'escape.csv').exists()


def test_hdf5_materialization_preserves_manifest_and_numeric_values(tmp_path):
    source = tmp_path / 'Canonical.h5'
    with h5py.File(source, 'w') as h5:
        h5.create_dataset('manifest_json', data=json.dumps({'experiment': 'sweep'}))
        item = h5.create_group('tables').create_dataset('dataset_1', data=[[10, 5, -2], [100, 8, -7]])
        item.attrs['columns_json'] = json.dumps(['frequency_Hz', 'Zreal_ohm', 'Zimag_ohm'])
        item.attrs['record_json'] = json.dumps({'technique': 'EIS', 'eis_method': 3, 'name': 'EIS spectrum'})
    before = _sha(source)
    data = open_data(source, tmp_path / 'Workspace')
    assert data.metadata['experiment'] == 'sweep'
    assert data.records[data.tables[0]]['eis_method'] == 3
    assert data.read_table(data.tables[0])['Zimag_ohm'].tolist() == [-2, -7]
    assert not (tmp_path / '.browser_cache_from_h5').exists()
    assert _sha(source) == before


def test_desktop_session_round_trip_keeps_native_settings(tmp_path):
    source = _data(tmp_path / 'Scan' / 'scan.csv')
    native = tmp_path / 'native.faradaiq_main_session.json'
    native.write_text(json.dumps({'schema': 'faradaiq_main_session_v199',
        'main_vars': {'file_var': str(source), 'theme_mode_var': 'Dark'},
        'main_texts': {'experimental_description': 'Original context'},
        'open_browser_sessions': [{'vars': {'plot_log_var': True}}],
        'current_analysis_root': str(source.parent)}))
    previous = load_session(native)
    new_path = save_session(tmp_path / 'working.json', source=source, analysis_root=source.parent,
        metadata={'sample': 'Copper'}, browser_state={'active_tool': 'DRT'}, previous=previous)
    restored = load_session(new_path)
    assert restored['schema'] == previous['schema']
    assert restored['main_vars']['theme_mode_var'] == 'Dark'
    assert restored['main_texts']['experimental_description'] == 'Original context'
    assert restored['open_browser_sessions'] == previous['open_browser_sessions']
    assert restored['browser_workspace']['metadata']['sample'] == 'Copper'
    assert restored['browser_workspace']['state']['active_tool'] == 'DRT'
    save_session(new_path, source=source, analysis_root=source.parent,
                 browser_state={'active_tool': 'EIS'}, previous=restored)
    assert load_session(new_path)['browser_workspace']['state']['active_tool'] == 'EIS'


def test_scilink_bundle_keeps_every_headerless_native_point(tmp_path):
    from faradaiq.scilink_bundle import make_scilink_ready_bundle

    root = tmp_path / 'scan_analysis_ready_csv'
    root.mkdir()
    curve = root / 'scan_CV.csv'
    curve.write_text('0.1,11\n0.2,22\n0.3,33\n')
    record = {'relative_csv_path': curve.name, 'technique': 'CV',
              'x_label': 'Potential', 'x_unit': 'V', 'y_label': 'Current', 'y_unit': 'µA'}
    (tmp_path / 'scan_analysis_ready_manifest.json').write_text(json.dumps({'csv_entries': [record]}))
    before = _sha(curve)
    receipt = make_scilink_ready_bundle(root)
    with h5py.File(receipt['h5_path'], 'r') as h5:
        table = next(iter(h5['tables'].values()))
        assert table[:, 1].tolist() == [11, 22, 33]
        assert json.loads(table.attrs['columns_json']) == ['Potential (V)', 'Current (µA)']
        assert json.loads(table.attrs['units_json']) == {'Potential (V)': 'V', 'Current (µA)': 'µA'}
    assert _sha(curve) == before


def test_native_palmsens_zip_keeps_measurement_order(tmp_path):
    source = tmp_path / 'Session.zip'
    def measurement(title, current):
        return {'Title': title, 'Method': 'TECHNIQUE=CV\nSCAN_RATE=0.05', 'Curves': [{
            'Title': 'I vs E',
            'XAxisDataArray': {'Description': 'Potential', 'Unit': 'V', 'DataValues': [0.1, 0.2]},
            'YAxisDataArray': {'Description': 'Current', 'Unit': 'µA', 'DataValues': current},
        }]}
    session = {'Measurements': [measurement('first', [3.0, 4.0]), measurement('second', [5.0, 6.0])]}
    with ZipFile(source, 'w') as archive:
        archive.writestr('Run/trace.pssession', json.dumps(session).encode('utf-16'))
    imported = open_data(source, tmp_path / 'Workspace')
    assert [imported.records[p]['measurement_title'] for p in imported.tables] == ['first', 'second']
    for expected, path in zip(([3.0, 4.0], [5.0, 6.0]), imported.tables):
        assert imported.read_table(path)['Current (µA)'].tolist() == expected


def test_numpy_input_never_unpickles_objects(tmp_path):
    import numpy as np

    source = tmp_path / 'raw.npy'
    np.save(source, {'instrument_object': 2}, allow_pickle=True)
    with pytest.raises(ValueError, match='Export its tables as CSV or canonical HDF5'):
        open_data(source, tmp_path / 'Workspace')
    numeric = tmp_path / 'numeric.npz'
    np.savez(numeric, traces=np.array([[1.0, 5.0], [2.0, -7.0]]))
    opened = open_data(numeric, tmp_path / 'Workspace')
    assert opened.read_table(opened.tables[0])['col_2'].tolist() == [5.0, -7.0]


def test_biologic_ascii_header_and_comma_decimals_survive_handoff(tmp_path_factory):
    from faradaiq.scilink_bundle import make_scilink_ready_bundle

    tmp_path = tmp_path_factory.mktemp('mpt')
    source = tmp_path / 'CV_biologic.mpt'
    source.write_bytes(b'EC-Lab ASCII FILE\r\nNb header lines : 4\r\nSample: Copper\r\n'
                       b'Ewe/V\t<I>/mA\r\n0,05\t1,1\r\n0,1\t2,2\r\n')
    data = open_data(source, tmp_path / 'Workspace')
    assert data.read_table(data.tables[0])['<I>/mA'].tolist() == [1.1, 2.2]
    handoff = make_scilink_ready_bundle(data.analysis_root)
    with h5py.File(handoff['h5_path'], 'r') as h5:
        table = next(iter(h5['tables'].values()))
        assert table[:, 1].tolist() == [1.1, 2.2]
        assert json.loads(table.attrs['columns_json']) == ['Ewe/V', '<I>/mA']


@pytest.mark.parametrize('name', ['metadata.json', 'measurement.csv'])
def test_session_save_will_not_replace_unrelated_data_files(tmp_path, name):
    source = _data(tmp_path / 'Experiment' / 'scan.csv')
    target = source.parent / name
    original = '{"schema":"instrument-metadata","sample":"Copper"}' if name.endswith('.json') else 'x,y\n1,2\n'
    target.write_text(original)
    with pytest.raises((ValueError, FileExistsError), match='session'):
        save_session(target, source=source.parent, analysis_root=source.parent)
    assert target.read_text() == original
