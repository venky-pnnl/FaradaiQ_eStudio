from pathlib import Path
import json
import hashlib
import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT=Path(__file__).resolve().parents[1]


def widget(widgets,label):
    return next(w for w in widgets if w.label==label)


def fixture_data(tmp_path):
    source=tmp_path/'PalmSens experiment';source.mkdir()
    (source/'scan.csv').write_text('potential_V,current_A,time_s\n0,0,0\n0.1,0.003,1\n0.2,0.001,2\n0.3,0,3\n0.2,-0.001,4\n0.1,-0.002,5\n0,0,6\n')
    return source


def test_all_advanced_workspaces_open_and_keep_source(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path/'Home'))
    source=fixture_data(tmp_path)
    before=(source/'scan.csv').read_bytes()
    app=AppTest.from_file(str(ROOT/'faradaiq/browser_app.py'),default_timeout=30).run()
    app.session_state['preferences']={'workspace':str(tmp_path/'Work')}
    widget(app.text_input,'Local file or folder').set_value(str(source))
    widget(app.button,'Open data').click().run()
    widget(app.button,'Add instruments or manage this session').click().run()
    assert widget(app.radio,'Workspace').value=='Workbench'
    areas=list(widget(app.selectbox,'Workbench area').options)
    assert len(areas)==6
    for area in areas:
        widget(app.selectbox,'Workbench area').set_value(area).run()
        assert not app.exception,(area,[e.message for e in app.exception])
    assert (source/'scan.csv').read_bytes()==before


def make_project(tmp_path):
    from faradaiq.browser_data import open_data
    from faradaiq.workbench_project import MultiInstrumentProject
    first=fixture_data(tmp_path)
    second=tmp_path/'BioLogic experiment';second.mkdir()
    (second/'scan.csv').write_text('potential_mV,current_mA,time_s\n0,0,0\n100,3,1\n200,1,2\n300,0,3\n200,-1,4\n100,-2,5\n0,0,6\n')
    project=MultiInstrumentProject(tmp_path/'Project','NiCo comparison')
    project.add_data(open_data(first,tmp_path/'Imports'),label='PalmSens',instrument='PalmSens4')
    project.add_data(open_data(second,tmp_path/'Imports'),label='BioLogic',instrument='BioLogic')
    project.save()
    return project,(first,second)


def test_multi_instrument_overlay_session_and_scilink_scope(tmp_path,monkeypatch):
    from faradaiq.browser_productivity import prepare_xy_series
    from faradaiq.scilink_bundle import make_scilink_ready_bundle
    from faradaiq.workbench_project import MultiInstrumentProject
    import h5py
    monkeypatch.setenv('HOME',str(tmp_path/'Home'))
    project,sources=make_project(tmp_path)
    data=project.combined_data()
    assert len(data.tables)==2
    assert {r['instrument'] for r in data.records.values()}=={'PalmSens4','BioLogic'}
    series,skipped=prepare_xy_series([(p,data.read_table(p)) for p in data.tables],'potential_V','current_A')
    assert not skipped and len(series)==2
    np.testing.assert_allclose(series[0][1],series[1][1])
    app=AppTest.from_file(str(ROOT/'faradaiq/browser_app.py'),default_timeout=30).run()
    app.session_state['preferences']={'workspace':str(tmp_path/'Work')}
    app.session_state['multi_project']=project
    app.session_state['dataset']=data
    widget(app.radio,'Workspace').set_value('Analysis').run()
    assert not app.exception
    widget(app.multiselect,'Overlay measurements').set_value([data.tables[1]]).run()
    widget(app.text_input,'Plot title').set_value('Two instruments').run()
    widget(app.button,'Quick save session').click().run()
    assert not app.exception and not app.error
    session=Path(app.session_state['session_file'])
    payload=json.loads(session.read_text())
    assert len(payload['browser_workspace']['state']['multi_project']['handles'])==2
    widget(app.radio,'Workspace').set_value('Workbench').run()
    widget(app.selectbox,'Workbench area').set_value('Compare and process').run()
    widget(app.button,'Quick save session').click().run()
    assert not app.exception and not app.error
    widget(app.radio,'Workspace').set_value('Data').run()
    widget(app.text_input,'Local session JSON path').set_value(str(session)).run()
    widget(app.button,'Open session').click().run()
    assert not app.exception and not app.error
    assert len(app.session_state['multi_project'].handles)==2
    widget(app.radio,'Workspace').set_value('Analysis').run()
    assert widget(app.text_input,'Plot title').value=='Two instruments'
    receipt=make_scilink_ready_bundle(data.analysis_root)
    with h5py.File(receipt['h5_path']) as h5:
        records=[json.loads(ds.attrs['record_json']) for ds in h5['tables'].values()]
        assert len(records)==2
        assert {r['instrument'] for r in records}=={'PalmSens4','BioLogic'}
    removed=project.handles[1]['id']
    project.remove_handle(removed)
    active=project.combined_data()
    receipt=make_scilink_ready_bundle(active.analysis_root)
    with h5py.File(receipt['h5_path']) as h5:
        assert len(h5['tables'])==1
    assert project.undo()
    assert len(project.combined_data().tables)==2


def test_saved_project_can_reopen_without_original_sources(tmp_path):
    from faradaiq.workbench_project import MultiInstrumentProject
    project,sources=make_project(tmp_path)
    payload=project.snapshot()
    for source in sources:source.rename(source.with_name(source.name+' moved'))
    restored=MultiInstrumentProject.load(payload)
    assert len(restored.combined_data().tables)==2


def test_native_palmsens_and_solartron_files_share_one_project(tmp_path):
    from faradaiq.workbench_project import MultiInstrumentProject
    session=tmp_path/'PalmSens.pssession'
    session.write_text(json.dumps({'Measurements':[{
        'Title':'NiCo CV', 'Method':'TECHNIQUE=CV\nSCAN_RATE=0.05', 'Curves':[{
            'Title':'I vs E',
            'XAxisDataArray':{'Description':'Potential','Unit':'V','DataValues':[0.1,0.2,0.3]},
            'YAxisDataArray':{'Description':'Current','Unit':'µA','DataValues':[2.0,3.0,4.0]}}]}]}))
    eis=tmp_path/'Solartron.z'
    eis.write_text('ZPlot\nExperiment Type: EIS\nEnd Comments\n1000 0.01 0 0 5 -2\n100 0.01 0 1 8 -7\n10 0.01 0 2 19 -6\n')
    originals={p:p.read_bytes() for p in (session,eis)}
    project=MultiInstrumentProject(tmp_path/'Mixed instruments')
    project.add_source(session,workspace=tmp_path/'Import',instrument='PalmSens4')
    project.add_source(eis,workspace=tmp_path/'Import',instrument='Solartron')
    data=project.combined_data()
    assert len(project.handles)==2
    assert {'PalmSens4','Solartron'}=={r['instrument'] for r in data.records.values()}
    from faradaiq.browser_analysis import measurement_roles
    roles=[measurement_roles(data.read_table(p)) for p in data.tables]
    assert any(row.get('potential') and row.get('current') for row in roles)
    assert any(row.get('frequency') and row.get('zreal') and row.get('zimag') for row in roles)
    for path,original in originals.items():assert path.read_bytes()==original
