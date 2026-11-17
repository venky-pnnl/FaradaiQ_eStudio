from pathlib import Path

def test_robust_corrosion_button_present_in_main_and_popout():
    text=(Path(__file__).resolve().parents[1]/'faradaiq'/'ui_tabs_v226.py').read_text(encoding='utf-8')
    assert text.count('Robust corrosion metrics · point/spatial') >= 2
    assert '_v298_open_corrosion_metrics' in text

def test_sequence_order_maps_eis_pre_tafel_and_after_ocp_post_tafel():
    from faradaiq.sequence_roles_v297 import assign_sequence_roles
    rows=[
        {'point':1,'technique_key':'OCP','group_index':2,'series_index_in_group':1,'method_title':'OCP','method_signature':'OCP:long'},
        {'point':1,'technique_key':'EIS','group_index':3,'series_index_in_group':1,'method_title':'EIS','method_signature':'EIS:base'},
        {'point':1,'technique_key':'OCP','group_index':4,'series_index_in_group':1,'method_title':'OCP','method_signature':'OCP:short'},
        {'point':1,'technique_key':'IV/TAFEL','group_index':5,'series_index_in_group':1,'method_title':'Tafel','method_signature':'IV:tafel'},
        {'point':1,'technique_key':'OCP','group_index':6,'series_index_in_group':1,'method_title':'OCP','method_signature':'OCP:after'},
    ]
    out=assign_sequence_roles(rows)
    assert rows[1]['sequence_role_label']=='Pre-Tafel EIS'
    assert rows[0]['sequence_role_label']=='Pre-Tafel OCP'
    assert rows[2]['sequence_role_label']=='Pre-Tafel OCP'
    assert rows[4]['sequence_role_label']=='Post-Tafel OCP'
    assert out['role_counts']['EIS:pre_tafel']==1
