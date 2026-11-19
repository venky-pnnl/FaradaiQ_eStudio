"""Exercise the Analysis redesign with real UI state and exact exported signals."""
from io import BytesIO
from pathlib import Path
import json

import h5py
import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from faradaiq.browser_analysis_ui import is_support_record, short_measurement_labels, legend_labels
from faradaiq.browser_data import open_data
from faradaiq.browser_plotting import build_xy_figure, displayed_plot_csv, displayed_plot_hdf5

ROOT=Path(__file__).resolve().parents[1]


def widget(items,label):return next(item for item in items if item.label==label)


@pytest.mark.parametrize('scale',[1e-9,1e-12,1e-15])
def test_small_signal_autoscale_preserves_exact_export(scale):
    path=Path('tiny.csv')
    frame=pd.DataFrame({'potential_V':[-.1,0,.1], 'current_A':np.array([1,2,3])*scale})
    fig=build_xy_figure([(path,frame)],'potential_V','current_A')
    lower,upper=fig.layout.yaxis.range
    assert 0 < lower < scale and 3*scale < upper < 4*scale
    np.testing.assert_array_equal(fig.data[0].y,frame.current_A)
    result=pd.read_csv(BytesIO(displayed_plot_csv(fig)),float_precision='round_trip')
    np.testing.assert_array_equal(result.y,frame.current_A)
    with h5py.File(BytesIO(displayed_plot_hdf5(fig))) as h5:
        np.testing.assert_array_equal(h5['curves/trace_001'][:,1],frame.current_A)


def test_constant_and_log_ranges_are_finite_and_do_not_fabricate_signal():
    f=pd.DataFrame({'x':[1,10,100], 'y':[3e-12]*3})
    fig=build_xy_figure([(Path('constant.csv'),f)],'x','y',log_x=True,log_y=True)
    assert all(np.isfinite(fig.layout.yaxis.range))
    assert fig.layout.yaxis.range[0] < np.log10(3e-12) < fig.layout.yaxis.range[1]
    assert list(fig.data[0].y)==[3e-12]*3
    zero=build_xy_figure([(Path('zero.csv'),f.assign(y=0.))],'x','y')
    assert list(zero.data[0].y)==[0.,0.,0.]


def test_short_unique_labels_and_full_export_provenance(tmp_path):
    source=tmp_path/'Project with long readable source identity'
    for name in ('Instrument A','Instrument B'):
        folder=source/name/'group_005_NewPoint'/'point_002'
        folder.mkdir(parents=True)
        (folder/'sweep_001_cycle_001_really_long_measurement_name_for_comparison.csv').write_text('potential_V,current_A\n0,1e-12\n.1,2e-12\n')
    data=open_data(source,tmp_path/'Work')
    short=short_measurement_labels(data)
    assert len(set(short.values()))==2
    legends=legend_labels(data,list(data.tables))
    assert len(set(legends.values()))==2 and max(map(len,legends.values()))<=60
    path=data.tables[1]
    full=path.relative_to(data.analysis_root).as_posix()
    fig=build_xy_figure([(path,data.read_table(path))],'potential_V','current_A',names=legends)
    fig.data[0].meta={'measurement':full}
    csv=pd.read_csv(BytesIO(displayed_plot_csv(fig)))
    assert set(csv.measurement)=={full}
    with h5py.File(BytesIO(displayed_plot_hdf5(fig))) as h5:
        assert h5['curves/trace_001'].attrs['measurement']==full


def app_with_data(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path/'home'))
    source=tmp_path/'HEKA experiment'
    source.mkdir()
    (source/'Position_Takedata.csv').write_text('potential_V,current_A\n.02,0\n.021,0\n')
    (source/'CV_sample_1.csv').write_text('potential_V,current_A\n-.1,1e-12\n0,2e-12\n.1,3e-12\n')
    (source/'CV_sample_2.csv').write_text('potential_mV,current_nA\n-100,.001\n0,.002\n100,.003\n')
    app=AppTest.from_file(str(ROOT/'faradaiq/browser_app.py'),default_timeout=30).run()
    app.session_state['preferences']={'workspace':str(tmp_path/'Work')}
    widget(app.text_input,'Local file or folder').set_value(str(source))
    widget(app.button,'Open data').click().run()
    widget(app.radio,'Workspace').set_value('Analysis').run()
    assert not app.exception
    return app,source


def test_browser_support_filter_focus_navigation_and_exact_scilink_scope(tmp_path,monkeypatch):
    app,source=app_with_data(tmp_path,monkeypatch)
    assert widget(app.selectbox,'Active measurement').value.name=='CV_sample_1.csv'
    assert 'Position_Takedata.csv' not in ' '.join(widget(app.selectbox,'Active measurement').options)
    assert app.get('plotly_chart')
    # The live element tree must keep controls beside the chart, rather than
    # stacking every control above it as the old page did.
    columns=app.get('column')
    assert any(col.get('plotly_chart') and not any(w.label=='X column' for w in col.get('selectbox')) for col in columns)
    assert any(not col.get('plotly_chart') and any(w.label=='X column' for w in col.get('selectbox')) for col in columns)
    fig=app.session_state['displayed_plot']['figure']
    assert fig.layout.height==560 and fig.layout.yaxis.range[1]<4e-12
    widget(app.multiselect,'Overlay measurements').set_value([source/'CV_sample_2.csv']).run()
    assert not app.error and not app.exception
    fig=app.session_state['displayed_plot']['figure']
    assert len(fig.data)==2
    np.testing.assert_allclose(fig.data[0].y,fig.data[1].y,rtol=1e-14,atol=0)
    for trace in fig.data:assert trace.meta['measurement'].endswith('.csv')
    widget(app.text_input,'Plot title').set_value('Saved nanoamp comparison').run()
    widget(app.toggle,'Focus plot').set_value(True).run()
    assert not app.exception
    assert app.session_state['plot_recipe']['title']=='Saved nanoamp comparison'
    assert len(app.session_state['displayed_plot']['figure'].data)==2
    widget(app.toggle,'Focus plot').set_value(False).run()
    assert not app.exception
    assert widget(app.selectbox,'Active measurement').value.name=='CV_sample_1.csv'
    widget(app.button,'Next measurement').click().run()
    assert widget(app.selectbox,'Active measurement').value.name=='CV_sample_2.csv'
    widget(app.checkbox,'Include position / support records').check().run()
    widget(app.selectbox,'Active measurement').set_value(source/'Position_Takedata.csv').run()
    assert any('Position / support record selected' in item.value for item in app.info)
    assert any('Constant recorded signal' in item.value for item in app.caption)
    # Explicitly fit a user view, then reset only the plot settings.
    widget(app.button,'Fit to data').click().run()
    assert app.session_state[f'plot:{source/"Position_Takedata.csv"}:view-revision']==1
    widget(app.button,'Reset plot defaults').click().run()
    assert not app.exception


def test_support_detection_preserves_explicit_cv_in_position_parent(tmp_path):
    raw=tmp_path/'Position'/'CV.csv';raw.parent.mkdir()
    raw.write_text('potential_V,current_A\n0,1\n1,2\n')
    data=open_data(raw.parent,tmp_path/'Work')
    data.records[raw]['technique']='CV'
    assert not is_support_record(data,raw)


def test_recorded_units_are_displayed_but_unknown_units_remain_unknown(tmp_path):
    from faradaiq.browser_plot_ui import _axis_caption
    raw=tmp_path/'bare.csv';raw.write_text('potential,current\n0,1e-9\n.1,2e-9\n')
    data=open_data(raw,tmp_path/'Work');[path]=data.tables
    assert _axis_caption(data,path,'current')=='current'
    data.records[path].update(units={'current':'A','potential':'V'})
    assert _axis_caption(data,path,'current')=='current (A)'
    assert _axis_caption(data,path,'potential')=='potential (V)'
