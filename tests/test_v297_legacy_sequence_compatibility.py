from pathlib import Path
from types import SimpleNamespace

from faradaiq.spatial_points_v297 import _read_takedata_coordinate_file, auto_axis_for_records
from faradaiq.sequence_roles_v297 import assign_sequence_roles
import faradaiq_studio_core as core


def _write_repeated_station_takedata(path: Path, n_points: int = 6):
    lines=[]
    for p in range(n_points):
        x = p * 2e-4
        # two records at one position with nm-scale offset, then move 200 um
        for jitter in (0.0, 2e-9):
            lines.append('Sweep #, Imon-2[A], Emon-2[V], X-pos[m], Y-pos[m], Z-pos[m], Time[s]')
            lines.append(f'1, 1e-10, -0.4, {x+jitter:.12E}, 0.0, -1e-4, 0')
    path.write_text('\n'.join(lines), encoding='utf-8')


def test_adjacent_duplicate_takedata_rows_collapse_to_scientific_points(tmp_path):
    p=tmp_path/'legacy_linescan_takedata.txt'
    _write_repeated_station_takedata(p, 6)
    rows=_read_takedata_coordinate_file(p)
    assert len(rows)==6
    assert all(int(r.get('coordinate_repeat_count') or 1)==2 for r in rows)
    recs=[{'point':i+1,'x_position_m':r['x_m'],'y_position_m':r['y_m']} for i,r in enumerate(rows)]
    assert auto_axis_for_records(recs)=='X'


def test_pre_and_post_tafel_eis_are_kept_as_distinct_methods():
    rows=[
        {'point':1,'technique_key':'EIS','group_index':1,'series_index_in_group':1,'sweep':1,'source_order':1,'method_title':'EIS','method_signature':'EIS:base'},
        {'point':1,'technique_key':'IV/TAFEL','group_index':1,'series_index_in_group':2,'sweep':1,'source_order':2,'method_title':'Tafel','method_signature':'IV:tafel'},
        {'point':1,'technique_key':'EIS','group_index':1,'series_index_in_group':3,'sweep':1,'source_order':3,'method_title':'EIS','method_signature':'EIS:base'},
    ]
    out=assign_sequence_roles(rows)
    assert out['role_counts']['EIS:pre_tafel']==1
    assert out['role_counts']['EIS:post_tafel']==1
    assert rows[0]['sequence_role_label']=='Pre-Tafel EIS'
    assert rows[2]['sequence_role_label']=='Post-Tafel EIS'
    assert rows[0]['method_signature'] != rows[2]['method_signature']


def test_repeated_eis_without_tafel_anchor_is_not_guessed():
    rows=[
        {'point':1,'technique_key':'EIS','group_index':1,'series_index_in_group':1,'source_order':1,'method_signature':'EIS:base'},
        {'point':1,'technique_key':'EIS','group_index':1,'series_index_in_group':2,'source_order':2,'method_signature':'EIS:base'},
    ]
    out=assign_sequence_roles(rows)
    assert out['points_with_tafel_anchor']==0
    assert not any('sequence_role' in r for r in rows)


def test_partial_tafel_prefers_adjacent_exact_length_potential_trace():
    # Prelude potential 300, current 304, true potential 304, log(i) 304.
    # The current must pair with the following exact-length potential, not prelude.
    parser=core.HEKAPotmasterParser()
    parser.traces=[
        SimpleNamespace(label='Emon-2', n_points=300, eis_group_index=None, block_index0=0),
        SimpleNamespace(label='Imon-2', n_points=304, eis_group_index=None, block_index0=0),
        SimpleNamespace(label='Emon-2', n_points=304, eis_group_index=None, block_index0=0),
        SimpleNamespace(label='log(i)', n_points=304, eis_group_index=None, block_index0=0),
    ]
    parser._detect_cv_pairs()
    assert len(parser.cv_pairs)==1
    assert parser.cv_pairs[0]['current_trace_index']==1
    assert parser.cv_pairs[0]['potential_trace_index']==2
    assert parser.cv_pairs[0]['n_points']==304
