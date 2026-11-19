from pathlib import Path
import json
import hashlib
from streamlit.testing.v1 import AppTest
from faradaiq.browser_data import open_data
from faradaiq.workbench_project import MultiInstrumentProject


def project(tmp_path, name, value):
    source=tmp_path/f'{name}.csv'
    source.write_text(f'time_s,current_A\n0,{value}\n1,{value}\n2,{value}\n')
    p=MultiInstrumentProject(tmp_path/name,name)
    p.add_data(open_data(source,tmp_path/'Imports'),instrument=name)
    p.save()
    return p


def harness(tmp_path,monkeypatch):
    from faradaiq import browser_app
    monkeypatch.setattr(browser_app,'PREFERENCES',tmp_path/'preferences.json')
    return AppTest.from_string('''
import streamlit as st
from faradaiq.browser_app import _sync_project, _save_current_session
_sync_project()
if st.button('Save'):
    st.session_state['saved'] = str(_save_current_session())
''',default_timeout=20).run()


def test_project_switch_never_overwrites_previous_session_or_context(tmp_path,monkeypatch):
    a,b=project(tmp_path,'A',1),project(tmp_path,'B',2)
    foreign=tmp_path/'B_session.json';foreign.write_text('prior saved bytes')
    original=foreign.read_bytes()
    a.state={'experiment_metadata':{'sample_name':'A sample'}}
    app=harness(tmp_path,monkeypatch)
    app.session_state['preferences']={'workspace':str(tmp_path/'Outputs')}
    app.session_state['dataset']=b.combined_data()
    app.session_state['multi_project']=a
    app.session_state['project_changed']=True
    app.session_state['project_restore_state']=True
    app.session_state['session_file']=str(foreign)
    app.session_state['restored_session']={'foreign':'B'}
    app.session_state['metadata']={'sample_name':'B sample'}
    app.session_state['w340_foreign_setting']=9
    app.session_state['Canonical HDF5']={'old':'B'}
    app.run()
    assert not app.exception
    assert 'session_file' not in app.session_state
    assert 'w340_foreign_setting' not in app.session_state
    assert 'Canonical HDF5' not in app.session_state
    assert app.session_state['metadata']['sample_name']=='A sample'
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state['saved']!=str(foreign)
    assert foreign.read_bytes()==original


def test_empty_project_sync_persists_empty_main_file(tmp_path,monkeypatch):
    p=project(tmp_path,'Only',1)
    before=p.combined_data()
    p.remove_handle(p.handles[0]['id'])
    app=harness(tmp_path,monkeypatch)
    app.session_state['dataset']=before
    app.session_state['multi_project']=p
    app.session_state['project_changed']=True
    app.session_state['project_restore_state']=True
    app.session_state['Trace comparison']={'old':True}
    app.run()
    assert not app.exception
    assert 'dataset' not in app.session_state
    assert 'project_restore_state' not in app.session_state
    assert 'Trace comparison' not in app.session_state
    assert json.loads((p.root/'project.json').read_text())['handles']==[]


def test_failed_provisional_project_does_not_replace_visible_raw_session(tmp_path,monkeypatch):
    source=tmp_path/'raw.csv';source.write_text('time_s,current_A\n0,1\n1,2\n')
    data=open_data(source,tmp_path/'Imports')
    empty=MultiInstrumentProject(tmp_path/'Empty')
    app=harness(tmp_path,monkeypatch)
    app.session_state['preferences']={'workspace':str(tmp_path/'Outputs')}
    app.session_state['dataset']=data
    app.session_state['metadata']={}
    app.session_state['multi_project']=empty
    app.button[0].click().run()
    assert not app.exception
    payload=json.loads(Path(app.session_state['saved']).read_text())
    assert payload['browser_workspace']['state']['multi_project'] is None
    assert payload['browser_workspace']['source']==str(source)
