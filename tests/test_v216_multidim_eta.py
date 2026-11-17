from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _synthetic_xy(tmp_path: Path):
    records=[]
    idx=0
    for yi,y in enumerate([0.0,1.0,2.0]):
        for xi,x in enumerate([0.0,1.0,2.0,3.0]):
            idx += 1
            t=np.linspace(0,8,121)
            current=-(idx*1e-9)*(1-np.exp(-t/1.5))
            p=tmp_path/f'ca_point_{idx:03d}.csv'
            pd.DataFrame({'time_s':t,'current_A':current}).to_csv(p,index=False)
            records.append({'path':str(p),'technique_key':'CA','point':idx,'method_index':1,'cycle':1,'x_position':x,'y_position':y})
    return records


class _Browser:
    def __init__(self, root: Path, records):
        self._catalog_records=records
        self.base_name='synthetic_CA_XY'
        self.cache_root=root/'cache'
        self.h5_path=root/'bundle.h5'


def test_altair_plotly_and_hyperspectral_outputs(tmp_path, monkeypatch):
    import pytest
    pytest.importorskip("altair")
    from faradaiq import multidim_v216 as m
    records=_synthetic_xy(tmp_path)
    browser=_Browser(tmp_path,records)
    monkeypatch.setattr(m,'_selected_records',lambda _b:records)

    alt=m.make_altair_2d(browser,'steady_state')
    assert Path(alt['html']).exists()
    assert Path(alt['spec']).exists()
    assert alt['engine']=='Altair' and alt['points']==12
    spec=json.loads(Path(alt['spec']).read_text(encoding='utf-8'))
    assert '$schema' in spec and 'vega-lite' in spec['$schema']

    p3=m.make_plotly_3d(browser,'steady_state')
    assert Path(p3['html']).exists()
    assert p3['engine']=='Plotly' and p3['spatial'] is True

    hs=m.make_hyperspectral(browser)
    assert Path(hs['html']).exists()
    assert Path(hs['cube']).exists()
    assert Path(hs['metadata']).exists()
    assert hs['n_points']==12 and hs['n_bands']==70 and hs['coordinates_available'] is True
    cube=np.load(hs['cube'])
    assert cube['values'].shape==(12,70)


def test_eta_profiles_store_file_size_rows_and_learn(tmp_path, monkeypatch):
    from faradaiq import eta_v216 as e
    stats=tmp_path/'stats'; monkeypatch.setenv('FARADAIQ_RUNTIME_STATS_DIR',str(stats))
    data=tmp_path/'data'; data.mkdir()
    for i in range(4):
        (data/f'point_{i+1:03d}.csv').write_text('time_s,current_A\n'+'\n'.join(f'{j},{j*1e-9}' for j in range(120)),encoding='utf-8')
    profile=e.command_profile(['python','parse_CA.py',str(data)],cwd=tmp_path,index=1)
    assert profile['kind']=='CA'
    assert profile['file_count']==4
    assert profile['total_bytes']>0
    assert profile['row_count_sampled']>=480
    for sec in (8.0,9.0,10.0,11.0,12.0):
        e.record(profile,sec,returncode=0,detail='parse_CA.py')
    pred=e.predict(profile)
    assert 7.0 <= pred['seconds'] <= 13.0
    assert pred['confidence'] in {'medium','high'}
    rem=e.predict_remaining([profile,profile],0)
    text=e.format_eta(rem['seconds'],rem['uncertainty'],rem['confidence'])
    assert text.startswith('ETA ~') and 'confidence' in text
    assert e.summary_path().exists() and e.history_path().exists()


def test_multitechnique_hyperspectral_master_and_regular_xy(tmp_path, monkeypatch):
    from faradaiq import multidim_v216 as m
    records = _synthetic_xy(tmp_path)
    # Add a second compatible technique family with a log-frequency axis.
    for point in range(1, 7):
        freq = np.geomspace(1, 1e4, 48)
        zreal = 100 + point * 5 + 20 / np.sqrt(freq)
        p = tmp_path / f"eis_point_{point:03d}.csv"
        pd.DataFrame({"frequency_Hz": freq, "Zreal_ohm": zreal, "Zimag_ohm": -0.25 * zreal}).to_csv(p, index=False)
        records.append({
            "path": str(p), "technique_key": "EIS", "point": point,
            "method_index": 1, "cycle": 0,
            "x_position": float((point - 1) % 3), "y_position": float((point - 1) // 3),
        })
    browser = _Browser(tmp_path, records)
    monkeypatch.setattr(m, "_selected_records", lambda _b: records)
    result = m.make_hyperspectral(browser)
    assert result["engine"] == "Plotly multi-technique hyperspectral"
    assert result["group_count"] == 2
    assert Path(result["html"]).exists()
    assert Path(result["cube_index"]).exists()
    groups = result["groups"]
    assert {g["technique"] for g in groups} == {"CA", "EIS"}
    ca = next(g for g in groups if g["technique"] == "CA")
    assert ca["regular_xy_grid"] is True
    assert Path(ca["cube"]).exists() and Path(ca["metadata"]).exists()


def test_eta_shape_history_and_prediction_residuals(tmp_path, monkeypatch):
    from faradaiq import eta_v216 as e
    stats = tmp_path / "stats_shape"
    monkeypatch.setenv("FARADAIQ_RUNTIME_STATS_DIR", str(stats))
    data = tmp_path / "shape_data"
    data.mkdir()
    for i in range(12):
        (data / f"method01_point{i+1:03d}.csv").write_text(
            "time_s,current_A\n" + "\n".join(f"{j},{(i+1)*j*1e-12}" for j in range(250)),
            encoding="utf-8",
        )
    profile = e.command_profile(["python", "parse_CA.py", str(data)], cwd=tmp_path, index=1)
    assert profile["file_count_bucket"] == "5_16"
    assert profile["row_count_bucket"] in {"1k_10k", "10k_100k"}
    assert "files=" in profile["shape_key"] and profile["storage_kind"] == "local"
    for actual in (9.0, 10.0, 11.0, 12.0):
        e.record(profile, actual, returncode=0, detail="shape run", predicted_seconds=10.0)
    summary = json.loads(e.summary_path().read_text(encoding="utf-8"))
    group = summary["by_kind_shape"][profile["shape_key"]]
    assert group["success_count"] == 4 and group["failure_count"] == 0
    assert group["prediction_error_stats"]["count"] == 4
    pred = e.predict(profile)
    assert "kind + file/row/point shape" in pred["basis"]


def test_resumable_raw_h5_checkpoint_and_resume(tmp_path):
    import h5py
    from types import SimpleNamespace
    from faradaiq.parsers.heka_v209 import _write_raw_h5_resumable

    traces=[]
    arrays=[]
    for i in range(4):
        arr=np.linspace(i, i+1, 16)
        arrays.append(arr)
        traces.append(SimpleNamespace(
            n_points=len(arr), label=f'Trace {i+1}', y_unit='A', x_unit='s',
            x_interval=.1, x_start=0.0, group_index=1, group_title='G',
            series_index=1, series_title='S', sweep_index=i+1, sweep_title='W',
            trace_index_in_sweep=1, technique_assignment='CA',
        ))
    parser=SimpleNamespace(
        traces=traces, source_path=str(tmp_path/'source.dat'),
        read_trace_array=lambda i: arrays[i],
    )
    raw=tmp_path/'raw.h5'
    with h5py.File(raw,'w') as h:
        h.attrs['source_sha256']='abc'
        g=h.create_group('traces')
        g.create_dataset('trace_00001',data=arrays[0])
        g.create_dataset('trace_00002',data=arrays[1])
    warnings=[]
    result=_write_raw_h5_resumable(parser,raw,{'sha256':'abc'},warnings)
    assert not warnings
    assert result['resumed_traces']==2 and result['new_traces_written']==2
    assert Path(result['progress_path']).exists()
    with h5py.File(raw,'r') as h:
        assert bool(h.attrs['complete']) is True
        assert int(h.attrs['completed_traces'])==4
        assert len(h['traces'])==4


def test_parser_status_reports_cumulative_files_parsed():
    core=(ROOT/'faradaiq_studio_core.py').read_text(encoding='utf-8')
    assert 'total_profile_files = sum' in core
    assert 'files parsed {completed_files}/{total_profile_files}' in core
    assert 'files parsed {total_profile_files}/{total_profile_files}' in core
    assert 'current input: {files} file(s)' in core
