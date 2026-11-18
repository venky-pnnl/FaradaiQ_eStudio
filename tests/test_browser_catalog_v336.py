from io import BytesIO
import hashlib
import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from faradaiq.browser_catalog import catalog_entries
from faradaiq.browser_data import export_browser_bundle, open_data, stage_browser_uploads


class Uploaded(BytesIO):
    def __init__(self, name, content):
        super().__init__(content)
        self.name = name


def test_directory_upload_preserves_relative_paths_and_scope(tmp_path):
    members = [Uploaded('Copper/Point 1/EIS.csv', b'frequency_Hz,Zreal_ohm,Zimag_ohm\n100,5,-2\n'),
               Uploaded('Copper/Point 2/EIS.csv', b'frequency_Hz,Zreal_ohm,Zimag_ohm\n100,6,-3\n')]
    source = stage_browser_uploads(members, tmp_path / 'Uploads')
    assert source.name == 'Copper'
    result = open_data(source, tmp_path / 'Analysis')
    assert [p.relative_to(result.analysis_root).as_posix() for p in result.tables] == [
        'Point 1/EIS.csv', 'Point 2/EIS.csv']


@pytest.mark.parametrize('names', [('ok.csv', '../escape.csv'), ('ok.csv', 'OK.csv'),
                                  ('Point/ok.csv', 'point/another.csv'), ('ok.csv', 'PRN.csv')])
def test_upload_invalid_or_ambiguous_paths_do_not_leave_partial_files(tmp_path, names):
    with pytest.raises(ValueError):
        stage_browser_uploads([Uploaded(name, b'1,2\n') for name in names], tmp_path / 'Uploads')
    assert not list(tmp_path.rglob('*.csv'))


def bundle_fixture(source):
    first = np.array([[1000.0, np.nextafter(0.125, 1.0), -2.0], [100.0, 0.12345678901234567, -7.0]])
    with h5py.File(source, 'w') as h5:
        h5.attrs['schema'] = 'faradaiq-canonical-h5-bundle-v7'
        h5.create_dataset('manifest_json', data=json.dumps({
            'sample_name': 'Copper film', 'gui_metadata': {'session_id': 'S000018', 'instrument': 'ZPlot'},
            'supporting_json_metadata': {'lab_notes': {'electrolyte': 'CuSO4'}}}))
        tables = h5.create_group('tables')
        table = tables.create_dataset('item_00001_EIS', data=first)
        table.attrs['record_json'] = json.dumps({'technique': 'EIS', 'name': 'Impedance', 'point': 13,
                                                'cycle': 2, 'dc_potential_V': -0.12})
        table.attrs['columns_json'] = json.dumps(['Frequency', 'Real', 'Imaginary'])
        table.attrs['units_json'] = json.dumps({'Frequency': 'Hz', 'Real': 'ohm', 'Imaginary': 'ohm'})
        media = h5.create_group('media').create_dataset('asset0001', data=np.frombuffer(b'GIF89a', dtype='uint8'))
        media.attrs['record_json'] = json.dumps({'name': 'Cell photo.gif', 'role': 'photo sequence'})
        metadata = h5.create_group('metadata').create_dataset('note0001', data='{"sample":"Cu"}')
        metadata.attrs['record_json'] = json.dumps({'name': 'conditions.json'})
        source_record = h5.create_group('source_documents').create_dataset(
            'source0001', data=np.frombuffer(b'frequency,zr,zi\n100,1,-2\n', dtype='uint8'))
        source_record.attrs['filename'] = 'instrument.csv'
    return first


def test_hdf5_catalog_and_canonical_round_trip(tmp_path):
    source = tmp_path / 'Input.h5'
    expected = bundle_fixture(source)
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    data = open_data(source, tmp_path / 'Workspace')
    assert data.bundle_path == source
    assert data.metadata['instrument'] == 'ZPlot'
    assert data.metadata['session_id'] == 'S000018'
    assert data.bundle_manifest['supporting_json_metadata']['lab_notes']['electrolyte'] == 'CuSO4'
    assert np.array_equal(data.read_table(data.tables[0]).to_numpy(), expected)
    asset = next(asset for asset in data.assets if asset.kind == 'Media')
    assert asset.path.read_bytes() == b'GIF89a'
    rows = catalog_entries(data, query='ohm impedance')
    assert len(rows) == 1 and rows[0]['Point'] == 13 and rows[0]['Cycle'] == 2
    assert rows[0]['Potential (V)'] == -0.12 and rows[0]['Technique'] == 'EIS'
    assert catalog_entries(data, techniques=['CV']) == []

    restored = open_data(data.analysis_root, tmp_path / 'Workspace')
    assert restored.bundle_path == source
    assert len(restored.assets) == len(data.assets)
    receipt = export_browser_bundle(restored, tmp_path / 'Export', sample_name='Copper repeat',
                                    metadata={'operator': 'Test'})
    with h5py.File(receipt['h5_path'], 'r') as h5:
        assert np.array_equal(h5['tables/item_00001_EIS'][()], expected)
        assert h5['media/asset0001'][()].tobytes() == b'GIF89a'
        assert h5['metadata/note0001'][()].decode() == '{"sample":"Cu"}'
        assert h5['source_documents/source0001'][()].tobytes() == b'frequency,zr,zi\n100,1,-2\n'
        assert json.loads(h5['manifest_json'][()]) == json.loads(Path(receipt['json_path']).read_text())
        assert json.loads(h5['manifest_json'][()])['gui_metadata'] == {
            'session_id': 'S000018', 'instrument': 'ZPlot', 'operator': 'Test'}
    assert hashlib.sha256(source.read_bytes()).hexdigest() == checksum


def test_canonical_export_of_curated_tables_uses_shared_schema(tmp_path):
    source = tmp_path / 'Cu.csv'
    source.write_text('potential_V,current_A\n0.1,2.0\n0.2,3.0\n')
    dataset = open_data(source, tmp_path / 'Work')
    receipt = export_browser_bundle(dataset, tmp_path / 'Out', metadata={'session_id': 'S000018'})
    with h5py.File(receipt['h5_path'], 'r') as h5:
        assert list(h5['tables'].values())[0][()].tolist() == [[0.1, 2], [0.2, 3]]
        assert json.loads(h5['manifest_json'][()])['gui_metadata']['session_id'] == 'S000018'


def test_workbook_tables_can_build_canonical_and_scilink_bundles(tmp_path_factory):
    import pandas as pd
    from faradaiq.scilink_bundle import make_scilink_ready_bundle

    root = tmp_path_factory.mktemp('xlsx')
    source = root / 'CV.xlsx'
    pd.DataFrame({'Potential (V)': [0.1, 0.2], 'Current (mA)': [2.0, 3.0]}).to_excel(source, index=False)
    original = source.read_bytes()
    data = open_data(source, root / 'Work')
    assert data.read_table(data.tables[0])['Current (mA)'].tolist() == [2.0, 3.0]
    canonical = export_browser_bundle(data, root / 'Output')
    scilink = make_scilink_ready_bundle(data.analysis_root)
    for receipt in (canonical, scilink):
        with h5py.File(receipt['h5_path'], 'r') as h5:
            item = next(iter(h5['tables'].values()))
            assert item[()].tolist() == [[0.1, 2], [0.2, 3]]
            assert json.loads(item.attrs['columns_json']) == ['Potential (V)', 'Current (mA)']
    with h5py.File(scilink['h5_path'], 'r') as h5:
        assert next(iter(h5['source_documents'].values()))[()].tobytes() == original
    assert source.read_bytes() == original
