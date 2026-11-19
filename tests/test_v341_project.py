import json
import time
from zipfile import ZipFile

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from faradaiq.browser_analysis import eis_table, measurement_roles
from faradaiq.browser_data import open_data
from faradaiq.workbench_audit import read_history
from faradaiq.workbench_import import ImportProfile
from faradaiq.workbench_project import DuplicateSourceError, MultiInstrumentProject, search_projects


def write_trace(path, offset=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'potential_V,current_A\n0,{offset + 1}\n0.15,{offset + 2}\n0.25,{offset + 3}\n', encoding='utf-8')
    return path


def test_project_materializes_independent_readable_tables_with_units_and_provenance(tmp_path):
    p1 = write_trace(tmp_path / 'one' / 'scan.csv')
    p2 = write_trace(tmp_path / 'two' / 'scan.csv', 10)
    before = p1.read_bytes(), p2.read_bytes()
    staging = tmp_path / 'stage'
    project = MultiInstrumentProject(tmp_path / 'project')
    id1 = project.add_source(p1, workspace=staging, label='Scan', instrument='PalmSens', tags='blank, cycle')
    id2 = project.add_data(open_data(p2, staging), label='Scan', instrument='BioLogic')
    data = project.combined_data()
    assert data.source == project.root
    assert data.analysis_root == project.root / 'Measurements'
    assert [path.parent.name for path in data.tables] == ['Scan_D001', 'Scan_D002']
    assert [path.name for path in data.tables] == ['0001_scan.csv', '0001_scan.csv']
    assert [data.records[path]['instrument'] for path in data.tables] == ['PalmSens', 'BioLogic']
    assert [handle['id'] for handle in data.metadata['project_handles']] == [id1, id2]
    assert data.metadata['project_handles'][0]['tags'] == ['blank', 'cycle']
    data.metadata['project_handles'][0]['tags'].append('local')
    assert project.handles[0]['tags'] == ['blank', 'cycle']
    for path, source, identifier in zip(data.tables, (p1, p2), (id1, id2)):
        assert path.is_file()
        assert data.records[path]['handle_id'] == identifier
        assert data.records[path]['source_path'] == data.records[path]['original_table_path'] == str(source)
        assert measurement_roles(data.read_table(path))['current'] == 'current_A'
    np.testing.assert_allclose(data.read_table(data.tables[0])['current_A'], [1, 2, 3])
    np.testing.assert_allclose(data.read_table(data.tables[1])['current_A'], [11, 12, 13])
    assert len(json.loads(data.manifest_path.read_text())['csv_entries']) == 2
    assert before == (p1.read_bytes(), p2.read_bytes())
    duplicate = tmp_path / 'copied.csv'
    duplicate.write_bytes(before[0])
    with pytest.raises(DuplicateSourceError) as exc:
        project.add_source(duplicate, workspace=staging)
    assert exc.value.handle_id == id1
    replicate = project.add_source(duplicate, workspace=staging, instrument='Solartron', allow_duplicate=True)
    assert replicate != id1
    assert project.handles[-1]['duplicate_of'] == id1
    records = project.combined_data().records
    assert {record['instrument'] for record in records.values()} == {'PalmSens', 'BioLogic', 'Solartron'}


def test_import_profile_persists_canonical_eis_units_metadata_and_sample_values(tmp_path):
    raw = tmp_path / 'instrument.csv'
    raw.write_text('freq,in_phase,minus_im,comment\n5,0.5,0.4,first\n1,0.7,0.1,second\n', encoding='utf-8')
    profile = ImportProfile('EIS export', dict(frequency='freq', zreal='in_phase', zimag='minus_im'),
                            dict(frequency='kHz', zreal='kohm', zimag='kohm'), 'EIS', True)
    project = MultiInstrumentProject(tmp_path / 'project')
    project.add_source(raw, workspace=tmp_path / 'staging', profile=profile)
    data = project.combined_data()
    [path] = data.tables
    record = data.records[path]
    assert record['original_columns'] == ['freq', 'in_phase', 'minus_im', 'comment']
    assert record['units'] == {'frequency_Hz': 'Hz', 'Zreal_ohm': 'ohm', 'Zimag_ohm': 'ohm'}
    assert record['technique'] == 'EIS'
    table = data.read_table(path)
    assert table['comment'].tolist() == ['first', 'second']
    np.testing.assert_allclose(eis_table(table).to_numpy(), [[5000, 500, -400], [1000, 700, -100]])


def test_project_recovers_edits_and_reversible_comparison_state_without_raw_sources(tmp_path):
    source = write_trace(tmp_path / 'raw' / 'sample.csv')
    project = MultiInstrumentProject(tmp_path / 'project', 'Comparison A')
    handle = project.add_source(source, workspace=tmp_path / 'stage', instrument='BioLogic', tags='EIS, EIS')
    [table] = project.combined_data().tables
    project.save()
    project.update_handle(handle, label='Electrode 2', tags=['reference'], annotations='Nitrogen atmosphere')
    project.update_measurement(table, 'Coating scan', ['oxide', 'Oxide', 'review'], 'Before calibration')
    comparison = {'active_table': table.relative_to(project.root / 'Measurements').as_posix(),
                  'plot_recipe': {'curves': [{'style': 'dashed'}]}}
    project.update_state(comparison)
    source.unlink()
    restored = MultiInstrumentProject.load(project.checkpoint_path)
    assert restored.state == comparison
    assert restored.history[-1]['action'] == 'Save comparison settings'
    assert restored.search('oxide nitrogen coating')[0]['source'] == 'Electrode 2'
    assert restored.combined_data().records[table]['tags'] == ['oxide', 'review']
    assert restored.undo()
    assert restored.state == {}
    assert restored.undo()
    assert restored.combined_data().records[table]['display_name'] == 'sample'
    assert restored.redo() and restored.redo()
    assert restored.state == comparison
    restored.remove_handle(handle)
    assert not table.exists()
    assert not restored.combined_data().tables
    restored = MultiInstrumentProject.load(restored.checkpoint_path)
    assert restored.undo()
    assert restored.combined_data().tables == (table,)
    assert source.exists() is False


def test_project_search_relocation_and_selected_zip(tmp_path):
    raw = tmp_path / 'raw'
    p1 = write_trace(raw / 'one.csv')
    p2 = write_trace(raw / 'two.csv', 15)
    workspace = tmp_path / 'work'
    project = MultiInstrumentProject(workspace / 'Project A', 'Titration B')
    selected = project.add_source(p1, workspace=tmp_path / 'stage', label='Cold run', instrument='PalmSens')
    project.add_source(p2, workspace=tmp_path / 'stage', label='Hot run', instrument='VersaStudio')
    other = MultiInstrumentProject(workspace / 'Project C', 'Titration C')
    other.add_source(p1, workspace=tmp_path / 'stage', instrument='BioLogic', tags='control')
    other.save()
    assert {row['project'] for row in search_projects(workspace, 'titration')} == {'Titration B', 'Titration C'}
    assert [row['instrument'] for row in search_projects(workspace, 'control biologic')] == ['BioLogic']
    previous_table = project.combined_data().tables[0]
    project.update_state({'workbench_settings': {'w340_table': {'__estudio_path__': str(previous_table)}}})
    raw.rename(tmp_path / 'relocated raw')
    new_root = tmp_path / 'Project B relocated'
    project.relocate({project.root: new_root, raw: tmp_path / 'relocated raw'})
    assert project.root == new_root
    assert project.handles[0]['source_path'] == str(tmp_path / 'relocated raw' / 'one.csv')
    assert project.state['workbench_settings']['w340_table']['__estudio_path__'] == str(project.combined_data().tables[0])
    assert MultiInstrumentProject.load(project.save()).root == new_root
    packaged = project.export_portable(tmp_path / 'selected.zip', selected=[selected])
    with ZipFile(packaged) as archive:
        members = archive.namelist()
        assert len(members) == 2
        assert members[1].startswith('Measurements/Cold run_D001/')
        assert selected not in members[1]
    opened = MultiInstrumentProject.load(packaged, root=tmp_path / 'restored')
    [data] = opened.combined_data().tables
    assert data.name == '0001_one.csv' and opened.handles[0]['id'] == selected
    assert not opened.can_undo and opened.state == {}
    assert [handle['id'] for handle in opened.combined_data().metadata['project_handles']] == [selected]
    all_zip = project.export_portable(tmp_path / 'all.zip')
    opened_all = MultiInstrumentProject.load(all_zip)
    assert opened_all.state['workbench_settings']['w340_table']['__estudio_path__'] == str(opened_all.combined_data().tables[0])


def test_legacy_absolute_curve_settings_migrate_on_portable_reopen(tmp_path):
    from faradaiq.workbench_curves import comparison_key, measurement_key
    from faradaiq.workbench_state import encode

    project = MultiInstrumentProject(tmp_path / 'project')
    handles = [project.add_source(write_trace(tmp_path / f'run{i}.csv', i), workspace=tmp_path / 'stage')
               for i in range(2)]
    data = project.combined_data()
    first, second = data.tables
    scope = comparison_key(str(data.source))
    old_keys = [measurement_key(data.source, path, handle) for path, handle in zip(data.tables, handles)]
    new_keys = [measurement_key(data.source, path, handle, path.relative_to(data.analysis_root).as_posix())
                for path, handle in zip(data.tables, handles)]
    history = {'cursor': 1, 'entries': [{'current_output': 'as recorded'}, {'current_output': 'current density', 'area_cm2': 2}]}
    project.update_state({'workbench_settings': {
        f'w340_compare_history_{old_keys[1]}': history,
        f'w340_compare_process_source_{scope}': encode(second),
        f'w340_compare_comparison_files_{scope}': encode([second, first]),
        f'w340_compare_{comparison_key(old_keys[::-1])}_comparison_operation': 'Difference',
    }})
    restored = MultiInstrumentProject.load(project.export_portable(tmp_path / 'legacy.zip'))
    a, b = restored.combined_data().tables
    settings = restored.state['workbench_settings']
    assert settings == {
        f'w340_compare_history_{new_keys[1]}': history,
        'w340_compare_process_source_project': encode(b),
        'w340_compare_comparison_files_project': encode([b, a]),
        f'w340_compare_{comparison_key(new_keys[::-1])}_comparison_operation': 'Difference',
    }


def test_portable_refuses_unexpected_files_and_path_traversal(tmp_path):
    project = MultiInstrumentProject(tmp_path / 'project')
    project.add_source(write_trace(tmp_path / 'sample.csv'), workspace=tmp_path / 'stage')
    for path, extra in ((tmp_path / 'traversal.zip', '../escape.txt'), (tmp_path / 'extras.zip', 'extra.txt')):
        project.export_portable(path)
        with ZipFile(path, 'a') as archive:
            archive.writestr(extra, 'unexpected')
        destination = tmp_path / path.stem
        with pytest.raises(ValueError, match='Unsafe|unsafe|match'):
            MultiInstrumentProject.load(path, root=destination)
        assert not destination.exists()
    assert not (tmp_path / 'escape.txt').exists()


def test_project_ui_guided_preview_background_import_and_organizer(tmp_path):
    source = write_trace(tmp_path / 'record.csv')
    workspace = tmp_path / 'workspace'
    app = AppTest.from_string(f'''
from pathlib import Path
import streamlit as st
from faradaiq.browser_app import _take_job_result
from faradaiq.workbench_project_ui import render
_take_job_result()
project = st.session_state.get('multi_project')
render(project.combined_data() if project else None, workspace=Path({str(workspace)!r}))
''', default_timeout=20).run()
    assert not app.exception
    app.text_area(key='w340_project_source_paths').set_value(str(source)).run()
    app.button(key='w340_project_preview').click().run()
    assert not app.exception
    assert len(app.dataframe) == 1
    assert app.dataframe[0].value['current_A'].tolist() == [1, 2, 3]
    app.button(key='w340_project_import_sources').click().run()
    if 'job' in app.session_state:
        app.session_state['job'].future.result(timeout=10)
    app.run()
    assert not app.exception
    project = app.session_state['multi_project']
    assert len(project.handles) == 1
    app.radio(key='w340_project_section').set_value('Organize').run()
    assert not app.exception
    name_input = next(widget for widget in app.text_input if widget.label == 'Measurement name')
    name_input.set_value('Sample preconditioned')
    next(widget for widget in app.button if widget.label == 'Update measurement').click().run()
    assert not app.exception
    assert project.search('preconditioned')[0]['measurement'] == 'Sample preconditioned'
    app.button(key='w340_project_undo').click().run()
    assert not app.exception
    assert project.search('record')[0]['measurement'] == 'record'
    assert {'Diagnose project import', 'Project import queue', 'Edit project measurement', 'Undo project edit'} <= {
        row['action'] for row in read_history(workspace)
    }


def test_project_ui_auto_imports_stable_watch_files(tmp_path):
    from faradaiq.workbench_import import StableFolderWatch

    folder = tmp_path / 'acquisition'
    write_trace(folder / 'new.csv')
    workspace = tmp_path / 'workspace'
    app = AppTest.from_string(f'''
from pathlib import Path
from faradaiq.browser_app import _take_job_result
from faradaiq.workbench_project_ui import render
_take_job_result()
render(None, workspace=Path({str(workspace)!r}))
''', default_timeout=20).run()
    app.radio(key='w340_project_section').set_value('Watch folder').run()
    app.text_input(key='w340_project_watch_folder').set_value(str(folder))
    app.number_input(key='w340_project_watch_seconds').set_value(1.0)
    watcher = StableFolderWatch(folder, stable_for=1)
    assert watcher.poll(now=time.monotonic() - 2)[0].status == 'Waiting'
    app.session_state['workbench_project_watch'] = watcher
    app.checkbox(key='w340_project_watch_enabled').set_value(True).run()
    assert not app.exception
    if 'job' in app.session_state:
        app.session_state['job'].future.result(timeout=10)
    app.run()
    assert not app.exception
    project = app.session_state['multi_project']
    assert [handle['label'] for handle in project.handles] == ['new']
    assert watcher.poll()[0].status == 'Handled'


def _current_source_app(source, workspace):
    return AppTest.from_string(f'''
from pathlib import Path
import streamlit as st
from faradaiq.browser_app import _take_job_result
from faradaiq.browser_data import open_data
from faradaiq.workbench_project_ui import render
workspace = Path({str(workspace)!r})
if 'dataset' not in st.session_state:
    st.session_state['dataset'] = open_data({str(source)!r}, workspace)
_take_job_result()
project = st.session_state.get('multi_project')
render(project.combined_data() if project else st.session_state['dataset'], workspace=workspace)
''', default_timeout=20).run()


def test_add_open_data_applies_reviewed_profile_and_blocks_incomplete_mapping(tmp_path):
    source = tmp_path / 'sample.csv'
    source.write_text('u,i\n100,10\n200,20\n', encoding='utf-8')
    profile = ImportProfile('Read millivolts and milliamps', {'potential': 'u', 'current': 'i'},
                            {'potential': 'mV', 'current': 'mA'}, 'CV')
    invalid = ImportProfile('Wrong source', {'potential': 'missing'}, {'potential': 'mV'})
    profile_path = profile.save(tmp_path / 'profile.json')
    invalid_path = invalid.save(tmp_path / 'invalid.json')
    app = _current_source_app(source, tmp_path / 'work')
    app.text_area(key='w340_project_source_paths').set_value(str(source)).run()
    app.button(key='w340_project_preview').click().run()
    app.radio(key='w340_project_mapping_mode').set_value('Map columns and units').run()
    assert app.button(key='w340_project_add_current').disabled
    app.radio(key='w340_project_mapping_mode').set_value('Load JSON profile').run()
    app.text_input(key='w340_project_profile_path').set_value(str(invalid_path)).run()
    assert app.button(key='w340_project_add_current').disabled
    assert any('missing columns' in error.value for error in app.error)
    app.text_input(key='w340_project_profile_path').set_value(str(profile_path)).run()
    assert not app.button(key='w340_project_add_current').disabled
    preview = app.dataframe[-1].value
    app.button(key='w340_project_add_current').click().run()
    assert not app.exception
    project = app.session_state['multi_project']
    assert project.handles[0]['profile'] == profile.snapshot()
    data = project.combined_data()
    [table] = data.tables
    assert data.records[table]['technique'] == 'CV'
    for column, values in [('potential_V', [.1, .2]), ('current_A', [.01, .02])]:
        np.testing.assert_allclose(preview[column], values)
        np.testing.assert_allclose(data.read_table(table)[column], values)
    assert source.read_text() == 'u,i\n100,10\n200,20\n'


def test_failed_first_queue_import_keeps_ordinary_dataset_active(tmp_path):
    source = write_trace(tmp_path / 'record.csv')
    app = _current_source_app(source, tmp_path / 'work')
    ordinary = app.session_state['dataset']
    app.text_area(key='w340_project_source_paths').set_value(str(tmp_path / 'missing.csv')).run()
    app.button(key='w340_project_import_sources').click().run()
    if 'job' in app.session_state:
        app.session_state['job'].future.result(timeout=10)
    app.run()
    assert not app.exception
    assert 'multi_project' not in app.session_state
    assert app.session_state['dataset'] is ordinary and len(ordinary.tables) == 1
    assert app.dataframe[-1].value['status'].tolist() == ['Failed']


def test_same_named_project_sources_round_trip_in_organizer_and_package_picker(tmp_path):
    project = MultiInstrumentProject(tmp_path / 'project')
    stage = tmp_path / 'stage'
    first = project.add_source(write_trace(tmp_path / 'first' / 'scan.csv'), workspace=stage, instrument='PalmSens')
    second = project.add_source(write_trace(tmp_path / 'second' / 'scan.csv', 20), workspace=stage, instrument='PalmSens')
    project_path = project.save()
    app = AppTest.from_string(f'''
from pathlib import Path
import streamlit as st
from faradaiq.workbench_project import MultiInstrumentProject
from faradaiq.workbench_project_ui import render
if 'multi_project' not in st.session_state:
    st.session_state['multi_project'] = MultiInstrumentProject.load({str(project_path)!r})
project = st.session_state['multi_project']
render(project.combined_data(), workspace=Path({str(tmp_path)!r}))
''', default_timeout=20).run()
    app.radio(key='w340_project_section').set_value('Organize').run()
    source = app.selectbox(key='w340_project_organize_source')
    assert source.value == first and len(set(source.options)) == 2
    source.set_value(second).run()
    assert app.selectbox(key='w340_project_organize_source').value == second
    app.radio(key='w340_project_section').set_value('Projects and packages').run()
    picker = next(widget for widget in app.multiselect if widget.label == 'Sources to package')
    assert len(set(picker.options)) == 2
    picker.set_value([second]).run()
    destination = tmp_path / 'second.zip'
    app.text_input(key='w340_project_portable_path').set_value(str(destination)).run()
    app.button(key='w340_project_portable_export').click().run()
    assert not app.exception
    data = MultiInstrumentProject.load(destination).combined_data()
    [table] = data.tables
    assert data.records[table]['handle_id'] == second
    assert data.read_table(table)['current_A'].tolist() == [21, 22, 23]
