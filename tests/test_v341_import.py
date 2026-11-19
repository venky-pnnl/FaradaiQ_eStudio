import struct
import threading

import numpy as np
import pandas as pd
import pytest

from faradaiq.workbench_import import (ImportProfile, StableFolderWatch, diagnose_source, preview_source,
                                       sha256_source, source_signature)
from faradaiq.workbench_project import ImportQueue, ImportRequest, MultiInstrumentProject


def test_guided_preview_detects_truncated_transfer_and_exposes_acquisition_columns(tmp_path):
    work = tmp_path / 'work'
    source = tmp_path / 'sample.csv'
    source.write_text('time_s,Ewe/V,<I>/mA\n0,0.1,0.3\n1,0.2,0.4\n', encoding='utf-8')
    result = preview_source(source, work, rows=1)
    assert result.table_count == 1
    assert result.suggested_roles == {'time': 'time_s', 'potential': 'Ewe/V', 'current': '<I>/mA'}
    pd.testing.assert_frame_equal(result.samples['sample.csv'], pd.DataFrame({'time_s': [0], 'Ewe/V': [.1], '<I>/mA': [.3]}))
    assert not work.exists()
    source.write_text('time_s,Ewe/V,<I>/mA\n0,0.1,0.3\n1,0.2', encoding='utf-8')
    incomplete = preview_source(source, work)
    assert not incomplete.samples
    assert 'row 3 has 2 fields. Expected 3' in incomplete.diagnosis.problems[0]
    curated = tmp_path / 'curated'
    curated.mkdir()
    (curated / 'sample.csv').write_bytes(source.read_bytes())
    assert 'row 3 has 2 fields. Expected 3' in diagnose_source(curated, full=True).problems[0]


def test_companion_and_binary_diagnostics(tmp_path):
    method = tmp_path / 'Test.par'
    method.write_bytes(b'method settings')
    assert 'matching VersaStudio' in diagnose_source(method).problems[0]
    binary = method.with_suffix('.par.psbd')
    binary.write_bytes(b'acquisition bytes')
    result = diagnose_source(method)
    assert result.instrument == 'VersaStudio'
    assert result.companions == (binary,)
    assert not result.problems
    assert sha256_source(method) == sha256_source(binary)
    previously = sha256_source(method)
    binary.write_bytes(b'different acquisition bytes')
    assert previously != sha256_source(method)
    raw = tmp_path / 'heka.dat'
    raw.write_bytes(b'\0ab')
    assert 'Missing .pul' in diagnose_source(raw).problems[0]
    header = bytearray(256)
    header[:4] = b'DAT2'
    struct.pack_into('<II8s', header, 64, 256, 20, b'.dat')
    raw.write_bytes(header + b'few')
    assert 'missing bytes from the HEKA .dat section' in diagnose_source(raw).problems[0]
    archive = tmp_path / 'unfinished.zip'
    archive.write_bytes(b'PK\x03\x04')
    assert 'ZIP directory' in diagnose_source(archive).problems[0]


def test_profile_conversion_reuse_and_inconsistent_units(tmp_path):
    role_columns = {'frequency': 'f', 'zreal': 'real', 'zimag': 'imag', 'potential': 'bias',
                    'time': 't', 'current': 'i', 'x': 'distance'}
    units = {'frequency': 'kHz', 'zreal': 'kΩ', 'zimag': 'Ω', 'potential': 'mV',
             'time': 'ms', 'current': 'µA', 'x': 'mm'}
    profile = ImportProfile('Vendor template', role_columns, units, 'EIS', True)
    file = profile.save(tmp_path / 'converter.json')
    replay = ImportProfile.load(file)
    assert replay == profile
    with pytest.raises(FileExistsError):
        profile.save(file)
    table = pd.DataFrame({'f': [10, 1], 'real': [0.1, 0.5], 'imag': [200, 500], 'bias': [200, 400],
                          't': [50, 100], 'i': [4, 8], 'distance': [10, 20], 'point': [1, 2]})
    mapped = replay.apply(table)
    assert mapped['point'].tolist() == [1, 2]
    np.testing.assert_allclose(mapped.iloc[0, 1:].to_numpy(), [10_000, 100, -200, .2, .05, 4e-6, .01])
    assert 'bias' in table and 'potential_V' not in table
    with pytest.raises(ValueError, match='incompatible'):
        ImportProfile('Broken', {'potential': 'bias'}, {'potential': 'A'})
    with pytest.raises(ValueError, match='missing columns'):
        profile.apply(table.drop(columns='f'))
    with pytest.raises(ValueError, match='positive'):
        profile.apply(table.assign(f=[0, 1]))
    with pytest.raises(ValueError, match='finite numeric'):
        profile.apply(table.assign(i=['bad', 5]))


def test_watch_waits_for_stability_and_companions_and_detects_change_again(tmp_path):
    folder = tmp_path / 'acquisition'
    folder.mkdir()
    source = folder / 'source.csv'
    source.write_text('x,y\n0,1\n', encoding='utf-8')
    watch = StableFolderWatch(folder, stable_for=2)
    assert watch.poll(now=5)[0].status == 'Waiting'
    source.write_text('x,y\n0,1\n1,2\n', encoding='utf-8')
    assert watch.poll(now=6)[0].status == 'Waiting'
    assert watch.poll(now=7)[0].status == 'Waiting'
    stable = watch.poll(now=8)[0]
    assert stable.status == 'Ready'
    watch.mark_handled(source, stable.signature)
    assert watch.poll(now=10)[0].status == 'Handled'
    method = folder / 'scan.par'
    method.write_bytes(b'method')
    assert {item.source.name: item.status for item in watch.poll(now=11)}['scan.par'] == 'Waiting'
    assert {item.source.name: item.status for item in watch.poll(now=14)}['scan.par'] == 'Incomplete'
    method.with_suffix('.psbd').write_bytes(b'binary')
    assert {item.source.name: item.status for item in watch.poll(now=15)} == {'scan.par': 'Waiting', 'source.csv': 'Handled'}
    assert {item.source.name: item.status for item in watch.poll(now=18)}['scan.par'] == 'Ready'
    signature_before_change = source_signature(source)
    source.write_text('x,y\n0,2\n1,3\n2,4\n', encoding='utf-8')
    waiting = {item.source.name: item for item in watch.poll(now=19)}['source.csv']
    assert waiting.status == 'Waiting'
    assert waiting.signature != signature_before_change


def test_queue_partial_failure_duplicate_and_cancel_between_files(tmp_path):
    sources = tmp_path / 'sources'
    sources.mkdir()
    good = sources / 'good.csv'
    good.write_text('potential_V,current_A\n0,2\n1,5\n', encoding='utf-8')
    clone = sources / 'clone.csv'
    clone.write_bytes(good.read_bytes())
    incomplete = sources / 'partial.csv'
    incomplete.touch()
    next_file = sources / 'later.csv'
    next_file.write_text('time_s,current_A\n0,2\n1,5\n', encoding='utf-8')
    project = MultiInstrumentProject(tmp_path / 'project')
    cancel = threading.Event()
    requests = [ImportRequest(path) for path in (incomplete, good, clone, next_file)]
    queue = ImportQueue(project, requests, tmp_path / 'staging')

    def report(message):
        if 'clone' in message:
            cancel.set()

    handles = queue.run(cancel, report)
    assert not project.handles
    assert [row['status'] for row in queue.status()] == ['Incomplete', 'Prepared', 'Prepared', 'Canceled']
    assert queue.apply(handles) == 1
    assert [row['status'] for row in queue.status()] == ['Incomplete', 'Imported', 'Duplicate', 'Canceled']
    assert project.combined_data().tables[0].parent.name == 'good_D001'
    assert [path.name for path in (project.root / 'Sources').iterdir()] == ['good_D001']


def test_import_rechecks_watch_signature(tmp_path):
    source = tmp_path / 'scan.csv'
    source.write_text('time_s,current_A\n0,2\n1,5\n', encoding='utf-8')
    project = MultiInstrumentProject(tmp_path / 'project')
    request = ImportRequest(source, signature=source_signature(source))
    source.write_text('time_s,current_A\n0,2\n1,5\n2,7\n', encoding='utf-8')
    queue = ImportQueue(project, [request], tmp_path / 'staging')
    assert queue.run(threading.Event(), lambda _: None) == []
    [row] = queue.status()
    assert row['status'] == 'Incomplete'
    assert 'changed after the watch' in row['detail']
