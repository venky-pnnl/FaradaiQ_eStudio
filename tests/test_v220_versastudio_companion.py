from pathlib import Path

import pytest

import faradaiq_studio_core as core


def _stub_parser():
    parser = core.VersaStudioParser()
    parser._parse_par_metadata = lambda path: {'par_path': path}
    parser._parse_psbd = lambda path: {'psbd_path': path}
    return parser


def test_standard_par_psbd_pair_is_accepted(tmp_path):
    par = tmp_path / 'scan.par'
    psbd = tmp_path / 'scan.psbd'
    par.write_text('method', encoding='utf-8')
    psbd.write_bytes(b'binary')

    parser = _stub_parser()
    result = parser.load(str(par))

    assert Path(parser.psbd_path) == psbd
    assert result['decoded']['psbd_path'] == str(psbd)


def test_legacy_par_dot_psbd_pair_is_accepted(tmp_path):
    par = tmp_path / 'scan.par'
    psbd = tmp_path / 'scan.par.psbd'
    par.write_text('method', encoding='utf-8')
    psbd.write_bytes(b'binary')

    parser = _stub_parser()
    parser.load(str(par))

    assert Path(parser.psbd_path) == psbd


def test_companion_lookup_has_case_insensitive_fallback(tmp_path):
    par = tmp_path / 'scan.par'
    psbd = tmp_path / 'SCAN.PSBD'
    par.write_text('method', encoding='utf-8')
    psbd.write_bytes(b'binary')

    resolved = core.VersaStudioParser._resolve_psbd_path(par)

    assert resolved == psbd


def test_missing_companion_error_lists_both_expected_names(tmp_path):
    par = tmp_path / 'scan.par'
    par.write_text('method', encoding='utf-8')

    with pytest.raises(RuntimeError) as exc:
        core.VersaStudioParser._resolve_psbd_path(par)

    message = str(exc.value)
    assert 'scan.psbd' in message
    assert 'scan.par.psbd' in message
