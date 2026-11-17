from pathlib import Path
import pytest
import faradaiq_studio_core as core


def _stub_decode(monkeypatch):
    monkeypatch.setattr(core.VersaStudioParser, '_parse_par_metadata', lambda self, path: {'path': path})
    monkeypatch.setattr(core.VersaStudioParser, '_parse_psbd', lambda self, path: {'dc_segments': [], 'eis_sweeps': [], 'psbd': path})


def test_folder_skips_orphan_par_and_loads_valid_pairs(tmp_path, monkeypatch):
    _stub_decode(monkeypatch)
    good = tmp_path / 'valid_scan.par'
    good.write_text('method', encoding='utf-8')
    (tmp_path / 'valid_scan.par.psbd').write_bytes(b'binary')
    orphan = tmp_path / 'orphan_scan.par'
    orphan.write_text('method', encoding='utf-8')

    c = core.PSSessionCollection().load(str(tmp_path))
    assert len(c.entries) == 1
    assert c.entries[0]['entry_name'] == 'valid_scan.par'
    assert len(c.skipped_entries) == 1
    assert c.skipped_entries[0]['relative_path'] == 'orphan_scan.par'
    assert 'no unambiguous' in c.skipped_entries[0]['reason']


def test_folder_does_not_fuzzy_pair_orphan_to_copy_binary(tmp_path, monkeypatch):
    _stub_decode(monkeypatch)
    orphan = tmp_path / 'scan.par'
    orphan.write_text('template', encoding='utf-8')
    copied = tmp_path / 'scan - Copy.par'
    copied.write_text('acquired', encoding='utf-8')
    (tmp_path / 'scan - Copy.par.psbd').write_bytes(b'binary')

    c = core.PSSessionCollection().load(str(tmp_path))
    assert len(c.entries) == 1
    assert c.entries[0]['entry_name'] == 'scan - Copy.par'
    assert len(c.skipped_entries) == 1
    assert c.skipped_entries[0]['relative_path'] == 'scan.par'


def test_single_orphan_par_remains_fail_fast(tmp_path):
    orphan = tmp_path / 'orphan.par'
    orphan.write_text('method', encoding='utf-8')
    with pytest.raises(RuntimeError, match='Matching VersaStudio binary data file not found'):
        core.PSSessionCollection().load(str(orphan))


def test_folder_with_only_orphans_reports_clear_failure(tmp_path):
    (tmp_path / 'orphan.par').write_text('method', encoding='utf-8')
    with pytest.raises(RuntimeError, match='No loadable experiments remained'):
        core.PSSessionCollection().load(str(tmp_path))
