from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
from zipfile import ZipFile

import h5py
import pytest

from faradaiq import browser_data
from faradaiq.browser_data import open_data, stage_browser_uploads
from faradaiq.scilink_bundle import make_scilink_ready_bundle


_SAMPLE = 'NiCo_foam_Batch-2026_polished_in_KOH_after_potential_hold_replicate_03_25mV_Tafel'


def _workspace(base, length):
    parent = base / 'Shared experiment results'
    return parent / ('Electrochemistry-' + 'x' * (length - len(str(parent)) - 18))


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(data):
    return json.loads(data.manifest_path.read_text(encoding='utf-8'))


@pytest.fixture
def stage_parent(tmp_path, monkeypatch):
    directory = tmp_path / 'native-stage'
    directory.mkdir()
    monkeypatch.setattr(browser_data, 'gettempdir', lambda: directory)
    return directory


def test_uploaded_palmsens_long_name_is_readable_after_stage_cleanup(tmp_path, stage_parent):
    raw = json.dumps({'Measurements': [{
        'Title': 'NiCo conditioning CV', 'Method': 'TECHNIQUE=CV\nSCAN_RATE=0.05', 'Curves': [{
            'Title': 'I vs E',
            'XAxisDataArray': {'Description': 'Potential', 'Unit': 'V', 'DataValues': [0.1, 0.2, 0.3]},
            'YAxisDataArray': {'Description': 'Current', 'Unit': 'µA', 'DataValues': [2.0, 3.0, 4.0]},
        }],
    }]}).encode('utf-16')
    upload = BytesIO(raw)
    upload.name = f'{_SAMPLE}.pssession'
    workspace = _workspace(tmp_path, 108)
    source = stage_browser_uploads([upload], workspace / 'Uploads')
    data = open_data(source, workspace / 'Imports', description='Electrode conditioned in KOH.')
    [table] = data.tables
    record = data.records[table]
    assert source.name == upload.name and source.read_bytes() == raw
    assert record['relative_source_path'] == record['source_filename'] == upload.name
    assert Path(record['source_path']) == source
    assert data.analysis_root.parent == workspace / 'Imports'
    assert data.analysis_root.name.startswith('NiCo')
    assert 'Tafel' in data.analysis_root.name
    assert _manifest(data)['browser_import']['original_label'] == _SAMPLE
    assert _manifest(data)['canonical_identity_base_name'] == _SAMPLE
    assert not list(stage_parent.iterdir())
    assert str(stage_parent) not in data.manifest_path.read_text(encoding='utf-8')
    assert data.read_table(table).to_dict('list') == {'Potential (V)': [0.1, 0.2, 0.3], 'Current (µA)': [2, 3, 4]}
    reopened = open_data(data.analysis_root, workspace / 'Imports')
    assert reopened.records == data.records
    assert reopened.metadata['experimental_description'] == 'Electrode conditioned in KOH.'
    assert all(browser_data._path_units(path) <= 235 for path in workspace.rglob('*'))
    handoff = make_scilink_ready_bundle(reopened.analysis_root, output_dir=workspace / 'SciLink')
    assert handoff['ready']['sample_name'] == _SAMPLE
    with h5py.File(handoff['h5_path'], 'r') as bundle:
        [stored] = bundle['tables'].values()
        assert stored[:].tolist() == [[0.1, 2.0], [0.2, 3.0], [0.3, 4.0]]
        assert json.loads(stored.attrs['units_json']) == {'Potential (V)': 'V', 'Current (µA)': 'µA'}


def test_long_solartron_import_rewrites_drt_paths_and_repeated_imports_do_not_overwrite(tmp_path, stage_parent):
    workspace = _workspace(tmp_path, 119)
    source = tmp_path / f'{_SAMPLE}_impedance.Z'
    source.write_text('ZPlot\nExperiment Type: EIS\nEnd Comments\n'
                      '1000 0.01 0 0 5 -2\n100 0.01 0 1 8 -7\n10 0.01 0 2 19 -6\n')
    digest = _hash(source)
    first = open_data(source, workspace)
    first_export = {path: _hash(path) for path in first.tables}
    second = open_data(source, workspace)
    assert first.analysis_root != second.analysis_root
    assert first.analysis_root.parent == second.analysis_root.parent == workspace
    assert first_export == {path: _hash(path) for path in first.tables}
    assert _hash(source) == digest
    assert not list(stage_parent.iterdir())
    for data in (first, second):
        manifest = _manifest(data)
        info = manifest['generic_drt_input']
        [csv] = map(Path, info['files'])
        assert csv in data.tables and 'DRT_input_csv' in csv.parts
        assert manifest['source_file'] == str(source)
        assert manifest['analysis_ready_root'] == str(data.analysis_root)
        drt = json.loads(Path(info['manifest_path']).read_text(encoding='utf-8'))
        assert data.analysis_root / drt['entries'][0]['files'][0]['relative_drt_path'] == csv
        assert manifest['browser_import']['generated_path_aliases']
        assert all(browser_data._path_units(path) <= 235 for path in data.analysis_root.rglob('*'))
        assert all(str(stage_parent) not in path.read_text(encoding='utf-8')
                   for path in data.analysis_root.rglob('*.json'))
        assert data.read_table(csv)['Zimag_ohm'].tolist() == [-2, -7, -6]
    handoff = make_scilink_ready_bundle(second.analysis_root, output_dir=workspace / 'SciLink')
    with h5py.File(handoff['h5_path'], 'r') as bundle:
        [spectrum] = [table for table in bundle['tables'].values()
                      if json.loads(table.attrs['columns_json']) == ['frequency_Hz', 'Zreal_ohm', 'Zimag_ohm']]
        assert spectrum[:].tolist() == [[1000, 5, -2], [100, 8, -7], [10, 19, -6]]


def _heka_payloads(*, dat_offset):
    data = struct.pack('<120f', *([1e-6] * 60), *([0.25] * 60))
    pulse = bytearray(256)
    for start, label, offset in ((0, b'Imon-1', dat_offset), (128, b'Emon-1', dat_offset + 240)):
        pulse[start + 12:start + 12 + len(label)] = label
        struct.pack_into('<III', pulse, start + 44, 1, offset, 60)
    return data, pulse


def test_heka_dat_and_archived_loose_companions_keep_full_identity(tmp_path, stage_parent):
    filename = f'{_SAMPLE}.dat'
    direct = tmp_path / filename
    data, pulse = _heka_payloads(dat_offset=256)
    header = bytearray(256)
    header[:4] = b'DAT2'
    struct.pack_into('<II8sII8s', header, 64, 256, len(data), b'.dat', 256 + len(data), len(pulse), b'.pul')
    direct.write_bytes(header + data + pulse)
    original = _hash(direct)
    workspace = _workspace(tmp_path, 95)
    upload = BytesIO(direct.read_bytes())
    upload.name = filename
    staged = stage_browser_uploads([upload], workspace / 'Uploads')
    bundled = open_data(staged, workspace / 'Imports')
    assert _hash(direct) == _hash(staged) == original
    assert _manifest(bundled)['source_file'] == str(staged)
    [combined] = [path for path in bundled.tables if path.name.endswith('heka_cv_combined.csv')]
    assert bundled.records[combined]['source_filename'] == filename
    traces = bundled.read_table(combined)
    assert len(traces) == 60
    assert traces['potential_V'].tolist() == [0.25] * 60
    assert traces['current_A'].tolist() == pytest.approx([1e-6] * 60)

    archive = tmp_path / 'NiCo loose acquisition.zip'
    data, pulse = _heka_payloads(dat_offset=0)
    companion = f'{_SAMPLE}.pul'
    with ZipFile(archive, 'w') as output:
        output.writestr(f'Run A/{filename}', data)
        output.writestr(f'Run A/{companion}', pulse)
    digest = _hash(archive)
    loose = open_data(archive, workspace)
    assert loose.source == archive and _hash(archive) == digest
    manifest = _manifest(loose)
    assert manifest['browser_import']['source_filename'] == archive.name
    [native] = list(loose.analysis_root.rglob(filename))
    assert native.read_bytes() == data
    assert native.with_name(companion).read_bytes() == pulse
    assert manifest['csv_entries']
    assert all(record['relative_source_path'] == filename and record['source_path'] == str(native)
               for record in manifest['csv_entries'])
    assert not list(stage_parent.iterdir())
    assert all(str(stage_parent) not in path.read_text(encoding='utf-8')
               for path in loose.analysis_root.rglob('*.json'))
    assert open_data(loose.analysis_root, workspace).tables == loose.tables


def test_overdeep_import_names_the_workspace_and_leaves_no_partial_files(tmp_path, stage_parent):
    workspace = _workspace(tmp_path, 208)
    source = tmp_path / 'EIS.z'
    source.write_text('ZPlot\nEnd Comments\n1000 0.01 0 0 5 -2\n100 0.01 0 1 8 -7\n10 0.01 0 2 19 -6\n')
    with pytest.raises(ValueError, match='Settings → Local output folder') as error:
        open_data(source, workspace)
    assert str(workspace) in str(error.value)
    assert not workspace.exists()
    assert not list(stage_parent.iterdir())


def test_uploaded_1_6mb_heka_tafel_under_long_workspace(tmp_path, stage_parent):
    """Exercise the screenshot's approximate upload size and readable Tafel name."""
    count = 200_000
    data = struct.pack(f'<{count}f', *([1e-6] * count)) + struct.pack(f'<{count}f', *([0.25] * count))
    pulse = bytearray(256)
    for start, label, offset in ((0, b'Imon-1', 256), (128, b'Emon-1', 256 + count * 4)):
        pulse[start + 12:start + 12 + len(label)] = label
        struct.pack_into('<III', pulse, start + 44, 1, offset, count)
    header = bytearray(256)
    header[:4] = b'DAT2'
    struct.pack_into('<II8sII8s', header, 64, 256, len(data), b'.dat', 256 + len(data), len(pulse), b'.pul')
    upload = BytesIO(header + data + pulse)
    upload.name = f'{_SAMPLE}.dat'
    workspace = _workspace(tmp_path, 108)
    source = stage_browser_uploads([upload], workspace / 'Uploads')
    digest = _hash(source)
    opened = open_data(source, workspace / 'Imports')
    assert _hash(source) == digest
    assert opened.analysis_root.name.startswith('NiCo')
    assert _manifest(opened)['canonical_identity_base_name'] == _SAMPLE
    [combined] = [path for path in opened.tables if path.name.endswith('heka_cv_combined.csv')]
    table = opened.read_table(combined)
    assert len(table) == count
    assert table['current_A'].iloc[[0, -1]].tolist() == pytest.approx([1e-6, 1e-6])
    assert table['potential_V'].iloc[[0, -1]].tolist() == [0.25, 0.25]
    assert not list(stage_parent.iterdir())
    assert all(browser_data._path_units(path) <= 235 for path in opened.analysis_root.rglob('*'))
    assert open_data(opened.analysis_root, workspace / 'Imports').records == opened.records
