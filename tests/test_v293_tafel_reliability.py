from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

import faradaiq_tafel_adapter as tafel
from faradaiq import tafel_worker_v237 as worker


def test_linregress_skips_identical_x_values_without_exception():
    x = np.ones(11, dtype=float) * -7.0
    y = np.linspace(-0.2, -0.1, 11)
    assert tafel._linregress(x, y) is None


def test_local_slope_diagnostics_skips_flat_log_current_windows():
    df = pd.DataFrame({
        'eta_V': np.linspace(-0.2, -0.01, 15),
        'log10_abs_j_A_cm2': np.full(15, -7.0),
    })
    out = tafel.local_slope_diagnostics(df, tafel.TafelSettings(local_slope_window_points=11))
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_worker_continues_after_one_source_file_failure(tmp_path, monkeypatch):
    good1 = tmp_path / 'Point_001' / 'sweep_001_cycle_001_Tafel_corrosion.csv'
    bad = tmp_path / 'Point_005' / 'sweep_001_cycle_001_Tafel_corrosion.csv'
    good2 = tmp_path / 'Point_010' / 'sweep_001_cycle_001_Tafel_corrosion.csv'
    for path in (good1, bad, good2):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('E_V,current_A\n-0.1,1e-8\n0.1,1e-7\n', encoding='utf-8')

    @dataclass
    class Result:
        source_file: str
        output_dir: str
        comparison_rows: list

    def fake_analyze(path, output_root, settings):
        if path.parent.name == 'Point_005':
            raise ValueError('degenerate local regression window')
        return [Result(str(path), str(tmp_path / ('out_' + path.parent.name)), [{'method': 'dummy'}])]

    monkeypatch.setattr(tafel, 'analyze_file_variants', fake_analyze)
    result_json = tmp_path / 'result.json'
    job = tmp_path / 'job.json'
    job.write_text(json.dumps({
        'paths': [str(good1), str(bad), str(good2)],
        'output_root': str(tmp_path / 'out'),
        'result_json': str(result_json),
        'settings': {},
    }), encoding='utf-8')

    assert worker.run(job) == 0
    payload = json.loads(result_json.read_text(encoding='utf-8'))
    assert payload['successful_file_count'] == 2
    assert payload['failed_file_count'] == 1
    assert len(payload['results']) == 2
    assert payload['failures'][0]['source'].endswith('Point_005/sweep_001_cycle_001_Tafel_corrosion.csv')


def test_analyze_panel_no_longer_contains_redundant_tafel_trace_button():
    root = Path(__file__).resolve().parents[1]
    text = (root / 'faradaiq' / 'ui_tabs_v226.py').read_text(encoding='utf-8')
    assert 'Tafel trace: log|I| vs V' not in text
    assert 'text="Tafel analysis"' in text
