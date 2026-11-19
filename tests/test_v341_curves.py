"""Analytical fixtures and real workbench actions for v341 curve processing."""
from dataclasses import asdict, replace
import json

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from streamlit.testing.v1 import AppTest

from faradaiq import workbench_curves as curves
from faradaiq.workbench_audit import read_history
from faradaiq.workbench_compare_ui import _STYLES, _pick_annotation, _selection_rows, _selection_window, figure_curves


def test_area_reference_and_ir_transform_preserves_raw_data():
    table = pd.DataFrame({'Ewe/mV': [200, 225, 150], 'I/mA': [5, -10, 0]})
    measured = table.copy(deep=True)
    recipe = curves.Recipe('Ewe/mV', 'I/mA', current_output='current density', area_cm2=0.5,
                           source_reference='Ag/AgCl (3 M KCl)', source_she_v=0.21, target_reference='RHE',
                           ph=7, temperature_c=25, resistance_ohm=5)
    result = curves.process_curve(table, recipe)
    assert result.frame['y'].to_list() == pytest.approx([.01, -.02, 0])
    assert result.frame['x'].to_list() == pytest.approx(np.array([.385, .485, .36]) + .05915935 * 7)
    assert result.x.reference == 'RHE'
    assert result.y.unit == 'A/cm2'
    pd.testing.assert_frame_equal(table, measured)
    for change, message in [({'area_cm2': 0}, 'electrode area'), ({'area_cm2': None}, 'electrode area'),
                            ({'ph': None}, 'measured pH'), ({'temperature_c': None}, 'measured pH'),
                            ({'temperature_c': -274}, 'absolute zero'), ({'source_she_v': None}, 'versus SHE'),
                            ({'resistance_ohm': -1}, 'Uncompensated resistance')]:
        with pytest.raises(ValueError, match=message):
            curves.process_curve(table, replace(recipe, **change))


def test_density_units_current_conversion_and_recorded_reference():
    table = pd.DataFrame({'Potential (V vs SHE)': [0., 1.], 'current density [mA cm⁻²]': [3., -4.]})
    recipe = curves.Recipe(*table.columns, current_output='current', area_cm2=2., resistance_ohm=5.)
    curve = curves.process_curve(table, recipe)
    assert curve.x.reference == 'SHE'
    assert curve.frame['y'].to_list() == pytest.approx([.006, -.008])
    assert curve.frame['x'].to_list() == pytest.approx([-.03, 1.04])
    table = pd.DataFrame({'t/s': [0, 1], 'j (mA/mm²)': [3., 4.]})
    assert curves.process_curve(table, curves.Recipe(*table.columns)).frame['y'].tolist() == pytest.approx([.3, .4])
    for columns in [('t/s', 'Current'), ('Data_1', 'Current_A'), ('t/s', 'Current density [mA]')]:
        mislabeled = pd.DataFrame([[0., .5], [1., .6]], columns=columns)
        with pytest.raises(ValueError, match='unit'):
            curves.process_curve(mislabeled, curves.Recipe(*columns))
    assert curves.process_curve(mislabeled, curves.Recipe(*columns, y_unit='mA/cm2')).y.quantity == 'density'


def test_row_masks_saved_polynomial_baseline_smoothing_and_crop():
    x = np.arange(8, dtype=float)
    y = .5 * x**2 - 2 * x + 9
    y[4] += 100
    table = pd.DataFrame({'time_s': x, 'current_A': y + np.array([0, 0, 0, 0, 0, 1, 0, 0])})
    recipe = curves.Recipe(*table.columns, excluded_rows=(5,), baseline='Polynomial', baseline_degree=2,
                           baseline_window=(0., 3.), smoothing='Moving average', smoothing_window=3, window=(4., 7.))
    result = curves.process_curve(table, recipe)
    assert result.frame['x'].tolist() == [5, 6, 7]
    assert result.frame['row'].tolist() == [6, 7, 8]
    assert result.frame['baseline_y'].tolist() == pytest.approx((.5 * x**2 - 2 * x + 9)[5:])
    assert result.frame['y'].tolist() == pytest.approx([1 / 3, 1 / 3, 0.], abs=1e-12)
    assert curves.smooth_values(x**2, 'Savitzky–Golay', 5, 2) == pytest.approx(x**2, abs=1e-12)
    with pytest.raises(ValueError, match='odd'):
        curves.process_curve(table, replace(recipe, smoothing_window=4))
    with pytest.raises(ValueError, match='distinct'):
        curves.process_curve(table, replace(recipe, baseline_window=(0., 1.)))
    with pytest.raises(ValueError, match='outside'):
        curves.process_curve(table, replace(recipe, excluded_rows=(9,)))
    with pytest.raises(ValueError, match='no measurements'):
        curves.process_curve(table, replace(recipe, window=(9., 10.)))
    table.loc[4, 'current_A'] = np.nan
    with pytest.raises(ValueError, match=r'Mask rows.*\[5\]'):
        curves.process_curve(table, replace(recipe, excluded_rows=()))
    linear = curves.process_curve(pd.DataFrame({'t_s': x, 'I_A': 3 * x - 2}),
                                  curves.Recipe('t_s', 'I_A', baseline='Linear', baseline_window=(0., 5.)))
    assert linear.frame['y'].tolist() == pytest.approx([0.] * len(x), abs=1e-12)


def _curve(x, y, x_unit='V', y_unit='A', reference='SHE'):
    table = pd.DataFrame({'x': x, 'y': y})
    return curves.process_curve(table, curves.Recipe('x', 'y', x_unit, y_unit, source_reference=reference))


def test_align_difference_ratio_keeps_order_and_never_extrapolates():
    first = _curve([4, 3, 2, 1, 0], [9, 7, 5, 3, 1])
    second = _curve([1500, 2500, 3500], [2000, 3000, 4000], 'mV', 'mA')
    difference = curves.compare_curves(first, second, 'Difference')
    assert difference.frame['x'].tolist() == [3, 2]
    assert difference.frame['y'].tolist() == [3.5, 2.5]
    ratio = curves.compare_curves(first, second, 'Ratio')
    assert ratio.frame['y'].tolist() == [2., 2.]
    assert ratio.y.unit == '1'
    with pytest.raises(ValueError, match='shared|common'):
        curves.compare_curves(first, _curve([10, 12], [1, 2]), 'Difference')
    for incompatible in [_curve([1, 3], [1, 2], y_unit='mA/cm2'), _curve([1, 3], [1, 2], reference='RHE')]:
        with pytest.raises(ValueError, match='compatible'):
            curves.compare_curves(first, incompatible, 'Difference')
    with pytest.raises(ValueError, match='denominator is zero'):
        curves.compare_curves(first, _curve([1, 3], [-1, 1]), 'Ratio')
    with pytest.raises(ValueError, match='repeated'):
        curves.align_curves([first, _curve([1, 2, 2, 3], [1, 2, 3, 4])])


def test_cv_requires_sweep_choice_and_cycle_averaging_keeps_turns():
    table = pd.DataFrame({'Ewe_V': [0, 1, 2, 1, 0] * 2, 'I_A': [1, 3, 5, 7, 9, 3, 5, 7, 9, 11],
                          'cycle': [1] * 5 + [2] * 5})
    recipe = curves.Recipe('Ewe_V', 'I_A', cycle_column='cycle')
    curve = curves.process_curve(table, recipe)
    assert curve.frame['x'].tolist() == table['Ewe_V'].to_list()
    with pytest.raises(ValueError, match='cycle.*sweep'):
        curves.align_curves([curve])
    cycle = curves.process_curve(table, replace(recipe, cycles=('1',)))
    with pytest.raises(ValueError, match='sweep branch'):
        curves.align_curves([cycle])
    branches = [part for _, part in curves.sweep_branches(cycle)]
    assert [part.frame['row'].tolist() for part in branches] == [[1, 2, 3], [3, 4, 5]]
    first, second = (curves.average_cycles(curve, index) for index in (1, 2))
    assert first['x'].tolist() == [0, 1, 2]
    assert first['mean'].tolist() == [2, 4, 6]
    assert second['x'].tolist() == [2, 1, 0]
    assert second['mean'].tolist() == [6, 8, 10]
    assert first['sd'].tolist() == pytest.approx([np.sqrt(2)] * 3)
    with pytest.raises(ValueError, match='absent'):
        curves.average_cycles(curve, 3)


def test_replicates_and_paired_and_welch_match_analytic_results():
    replicates = [_curve([0, 1, 2], np.array([0, 1, 2]) + offset) for offset in [1, 2, 3]]
    result = curves.summarize_replicates(replicates)
    assert result['mean'].tolist() == [2, 3, 4]
    assert result['sd'].tolist() == [1., 1., 1.]
    assert result['n'].tolist() == [3, 3, 3]
    assert (result['ci_high'] - result['mean']).tolist() == pytest.approx([4.302652729 / np.sqrt(3)] * 3)
    with pytest.raises(ValueError, match='two independent'):
        curves.summarize_replicates(replicates[:1])
    control, treatment = [3, 4, 6, 10], [6, 6, 12, 11]
    paired = curves.treatment_effect(control, treatment, paired=True)
    welch = curves.treatment_effect(control, treatment)
    assert paired['difference'] == welch['difference'] == 3.
    assert paired['p_value'] == pytest.approx(stats.ttest_rel(treatment, control).pvalue)
    assert welch['p_value'] == pytest.approx(stats.ttest_ind(treatment, control, equal_var=False).pvalue)
    assert paired['ci_low'] < paired['difference'] < paired['ci_high']
    with pytest.raises(ValueError, match='same number'):
        curves.treatment_effect(control[:2], treatment, paired=True)
    degenerate = curves.treatment_effect([1, 2], [3, 4], paired=True)
    assert degenerate['p_value'] is None
    assert degenerate['difference'] == 2


def test_uncertainty_supports_correlation_and_rejects_invalid_covariance():
    covariance, errors = curves.propagate_uncertainty([[1, 2], [1, -1]], [[.04, .01], [.01, .09]])
    np.testing.assert_allclose(covariance, [[.44, -.13], [-.13, .11]])
    assert errors == pytest.approx(np.sqrt([.44, .11]))
    for supplied, match in [([[1., 2], [0., 1]], 'symmetric'), ([[1., 2], [2., 1]], 'semidefinite'),
                            ([[1.]], 'matching dimensions')]:
        with pytest.raises(ValueError, match=match):
            curves.propagate_uncertainty([1., 2.], supplied)


def test_plot_layouts_and_legend_metadata_and_selection():
    first = _curve([0, 1, 2], [4, 5, 6])
    other = _curve([0, 1, 2], [20, 30, 40], y_unit='ohm')
    unrelated = _curve([0, 1, 2], [4, 5, 6], x_unit='s')
    style = _STYLES['Dark']
    linked = figure_curves([('BioLogic', first), ('Gamry', other)], style, 'Linked x-axis subplots')
    assert linked.layout.xaxis.matches == 'x2'
    assert 'ohm' in linked.layout.yaxis2.title.text
    side = figure_curves([('BioLogic', first), ('Gamry', unrelated)], style, 'Side by side')
    assert side.layout.xaxis.matches is None
    assert side.layout.xaxis2.title.text == 'Time (s)'
    assert side.data[0].x.tolist() == [0., 1., 2.]
    assert _selection_window({'selection': {'points': [{'x': 7}, {'x': 2}, {'x': 3}]}}) == (2, 7)
    assert _selection_window({'selection': {'box': [{'x': [1, 3]}]}}) == (1, 3)
    event = {'selection': {'points': [{'x': 1, 'y': 5, 'curve_number': 1, 'customdata': 2}]}}
    assert _selection_rows(event) == [2]
    assert _pick_annotation(event, side) == (1, 5, 2)
    assert curves.local_peak_points([4, 3, 2, 1, 0], [1, 5, 1, 3, 2], prominence=2) == [1]
    assert curves.local_peak_points([4, 3, 2, 1, 0], [1, 5, 1, 3, 2], interval=(.5, 1.5)) == [3]
    record = {'instrument': 'PalmSens', 'display_name': 'my run', 'tags': ['batch=x', 'sample=gold', 'cycle=4'],
              'potential_V': 0.1}
    assert curves.legend_label('measurement.csv', record, ('instrument', 'sample', 'potential', 'cycle')) == 'PalmSens · gold · 0.1 · 4'
    assert curves.metadata_tags({'tags': {'Replicate': 1}}) == {'replicate': '1'}
    assert curves.legend_label('measurement.csv', {'tags': {'field': 'on'}}, ('tags',)) == 'field=on'


def test_named_presets_batch_outputs_and_reversible_recipe(tmp_path):
    source1, source2 = tmp_path / 'good.csv', tmp_path / 'bad.csv'
    tables = {source1: pd.DataFrame({'t_s': [0, 1], 'I_mA': [2, 4]}),
              source2: pd.DataFrame({'t_s': [0, 1], 'wrong': [2, 4]})}
    recipe = curves.Recipe('t_s', 'I_mA', current_output='current density', area_cm2=2.)
    root = tmp_path / 'workspace'
    settings = {'recipe': asdict(recipe), 'origin': {'instrument': 'Gamry'}}
    assert curves.save_named(root, 'recipes', 'Two cm²', settings).is_file()
    assert curves.recipe_from_dict(curves.load_named(root, 'recipes')['Two cm²']['recipe']) == recipe
    curves.save_named(root, 'styles', 'Night lab', _STYLES['Dark'])
    assert curves.load_named(root, 'styles')['Night lab'] == _STYLES['Dark']
    provenance = {source1: {'instrument': 'Gamry', 'source_path': '/experiment/disk.dta', 'handle_id': 'source1',
                           'original_table_path': '/experiment/original.csv'}}
    result = curves.run_batch(tables, read_table=tables.__getitem__, recipe=recipe, workspace=root, metadata=provenance)
    assert result['status'].tolist() == ['saved', 'failed']
    assert 'absent' in result['message'].iloc[1]
    export = pd.read_csv(result['data_csv'].iloc[0])
    assert export['y_current_density_A/cm2'].tolist() == [.001, .002]
    with open(result['settings_json'].iloc[0], encoding='utf-8') as stream:
        document = json.load(stream)
    assert document['sources'][0]['instrument'] == 'Gamry'
    assert document['sources'][0]['original_table_path'] == '/experiment/original.csv'
    recorded = asdict(replace(recipe, current_output='as recorded'))
    updated = asdict(recipe)
    history, position = curves.update_history([recorded], 0, recipe=updated)
    assert history[position] == updated
    history, position = curves.update_history(history, position, step=-1)
    assert history[position] == recorded
    history, position = curves.update_history(history, position, step=1)
    assert history[position] == updated
    history, position = curves.update_history(history, 0, recipe=asdict(replace(recipe, area_cm2=3.)))
    assert len(history) == 2 and history[position]['area_cm2'] == 3.


def _button(app, label):
    return next(button for button in app.button if button.label == label)


def _widget(app, kind, label):
    return next(widget for widget in getattr(app, kind) if widget.label == label)


def test_workbench_real_processing_save_undo_batch_and_export(tmp_path):
    script = f'''
from pathlib import Path
import pandas as pd
from faradaiq.browser_data import BrowserData
from faradaiq.workbench_compare_ui import render
root = Path({str(tmp_path)!r})
a, b = root / 'one.csv', root / 'two.csv'
for file, scale in ((a, 1), (b, 2)):
    pd.DataFrame({{'Time_s': [0, 1, 2, 3], 'Current_mA': [scale*n for n in [1, 2, 3, 4]]}}).to_csv(file, index=False)
data = BrowserData(root, root, (a, b), {{a: {{'instrument': 'Gamry', 'handle_id': 'a'}}, b: {{'instrument': 'BioLogic', 'handle_id': 'b'}}}}, {{}})
render(data, workspace=root)
'''
    app = AppTest.from_string(script, default_timeout=20).run()
    assert not app.exception
    _widget(app, 'selectbox', 'Current output').select('current density').run()
    _widget(app, 'number_input', 'Electrode area (cm²)').set_value(2.).run()
    _button(app, 'Apply processing').click().run()
    assert not app.exception
    assert _widget(app, 'radio', 'Curve preview').value == 'Applied result'
    _button(app, 'Save derived CSV and settings').click().run()
    saved = list((tmp_path / 'Workbench' / 'Compare' / 'results').glob('*/data.csv'))
    assert len(saved) == 1
    assert pd.read_csv(saved[0])['y_current_density_A/cm2'].tolist() == [.0005, .001, .0015, .002]
    _button(app, 'Undo processing').click().run()
    assert _button(app, 'Undo processing').disabled
    _button(app, 'Redo processing').click().run()
    assert _button(app, 'Redo processing').disabled
    app.number_input(key=next(w.key for w in app.number_input if w.label == 'View minimum x')).set_value(.5).run()
    _widget(app, 'number_input', 'View maximum x').set_value(2.5).run()
    _button(app, 'Apply plot window').click().run()
    _widget(app, 'text_input', 'Annotation text').set_value('Peak check').run()
    _widget(app, 'number_input', 'Annotation x').set_value(2.).run()
    _widget(app, 'number_input', 'Annotation y').set_value(.0015).run()
    _button(app, 'Add annotation').click().run()
    _button(app, 'Save interactive figure').click().run()
    assert not app.exception
    figures = list((tmp_path / 'Workbench' / 'Compare' / 'figures').glob('*/figure.json'))
    figure = json.loads(figures[0].read_text())
    assert figure['layout']['annotations'][0]['text'] == 'Peak check'
    assert figure['layout']['xaxis']['range'] == [.5, 2.5]
    assert 'Download annotation positions' in figures[0].with_suffix('.html').read_text()
    _button(app, 'Use selection or view window as processing crop').click().run()
    assert _widget(app, 'checkbox', 'Crop processed values to an x interval').value
    assert _widget(app, 'number_input', 'Minimum x').value == .5
    _widget(app, 'radio', 'Compare and process').set_value('Batch recipes').run()
    _widget(app, 'text_input', 'Recipe name').set_value('Density')
    _button(app, 'Save batch recipe').click().run()
    _widget(app, 'selectbox', 'Batch recipe').select('Density').run()
    _widget(app, 'multiselect', 'Batch measurements').set_value([tmp_path / 'one.csv', tmp_path / 'two.csv']).run()
    assert _button(app, 'Run reviewed batch recipe').disabled
    _widget(app, 'checkbox', 'I reviewed this recipe for the selected measurements').check().run()
    _button(app, 'Run reviewed batch recipe').click().run()
    assert not app.exception
    actions = [row['action'] for row in read_history(tmp_path)]
    for action in ('Apply curve processing', 'Undo curve processing', 'Redo curve processing', 'Set curve plot window',
                   'Export annotated curve figure', 'Stage curve processing window', 'Save batch processing recipe',
                   'Run reviewed batch processing recipe'):
        assert action in actions
    status = next(df.value for df in app.dataframe if 'status' in df.value)
    assert status['status'].tolist() == ['saved', 'saved']
    assert status['instrument'].tolist() == ['Gamry', 'BioLogic']
    _widget(app, 'radio', 'Compare and process').set_value('Compare measurements').run()
    _widget(app, 'radio', 'Comparison inputs').set_value('As recorded').run()
    _widget(app, 'radio', 'Comparison').set_value('Difference').run()
    _button(app, 'Calculate difference').click().run()
    assert app.dataframe[0].value['y'].tolist() == [-.001, -.002, -.003, -.004]
    _widget(app, 'radio', 'Comparison').set_value('Ratio').run()
    assert not app.dataframe
    _button(app, 'Calculate ratio').click().run()
    assert app.dataframe[0].value['y'].tolist() == [.5, .5, .5, .5]
    assert not app.exception


def _project_curves_app(project_file, workspace, *, replacement=None):
    return AppTest.from_string(f'''
from pathlib import Path
from unittest.mock import patch
import streamlit as st
from faradaiq import workbench_compare_ui, workbench_project_ui
from faradaiq.browser_app import _restore_session
from faradaiq.workbench_project import MultiInstrumentProject
from faradaiq.workbench_state import restore_settings
workspace = Path({str(workspace)!r})
if 'multi_project' not in st.session_state:
    replacement = {str(replacement) if replacement is not None else ''!r}
    if replacement:
        with patch('faradaiq.browser_app._remember'):
            _restore_session({str(project_file)!r}, replacement)
    else:
        project = MultiInstrumentProject.load({str(project_file)!r})
        st.session_state['multi_project'] = project
        restore_settings(st.session_state, project.state.get('workbench_settings'))
project = st.session_state['multi_project']
data = project.combined_data()
if st.radio('Workspace', ['Curves', 'Projects']) == 'Curves':
    workbench_compare_ui.render(data, workspace=workspace)
else:
    workbench_project_ui.render(data, workspace=workspace)
''', default_timeout=20).run()


def test_project_processing_and_choices_survive_portable_zip_and_relocated_sessions(tmp_path):
    from faradaiq.browser_data import save_session
    from faradaiq.workbench_project import MultiInstrumentProject

    project = MultiInstrumentProject(tmp_path / 'project')
    for folder, scale in [('first', 1), ('second', 4)]:
        raw = tmp_path / folder / 'scan.csv'
        raw.parent.mkdir()
        pd.DataFrame({'Time_s': [0, 1, 2], 'Current_mA': [scale, 2 * scale, 3 * scale]}).to_csv(raw, index=False)
        project.add_source(raw, instrument='PalmSens', workspace=tmp_path / 'stage')
    first, second = project.combined_data().tables
    app = _project_curves_app(project.save(), tmp_path)
    picker = _widget(app, 'selectbox', 'Measurement to process')
    assert picker.value == first and len(set(picker.options)) == 2
    picker.set_value(second).run()
    assert _widget(app, 'selectbox', 'Measurement to process').value == second
    _widget(app, 'selectbox', 'Current output').select('current density').run()
    _widget(app, 'number_input', 'Electrode area (cm²)').set_value(2.).run()
    _button(app, 'Apply processing').click().run()
    expected = [.002, .004, .006]
    np.testing.assert_allclose(app.dataframe[0].value['y'], expected)
    _widget(app, 'radio', 'Compare and process').set_value('Compare measurements').run()
    _widget(app, 'radio', 'Comparison inputs').set_value('As recorded').run()
    comparison = _widget(app, 'multiselect', 'Measurements to compare')
    assert len(set(comparison.options)) == 2
    comparison.set_value([second, first]).run()
    assert _widget(app, 'multiselect', 'Measurements to compare').value == [second, first]
    _widget(app, 'radio', 'Comparison').set_value('Difference').run()
    assert _widget(app, 'selectbox', 'Minuend').value == second
    assert _widget(app, 'selectbox', 'Subtrahend').value == first
    _widget(app, 'radio', 'Workspace').set_value('Projects').run()
    app.radio(key='w340_project_section').set_value('Projects and packages').run()
    zipped = tmp_path / 'full.zip'
    app.text_input(key='w340_project_portable_path').set_value(str(zipped)).run()
    app.button(key='w340_project_portable_export').click().run()
    relocated = tmp_path / 'relocated'
    app.text_input(key='w340_project_relocate_project').set_value(str(relocated)).run()
    app.button(key='w340_project_relocate_button').click().run()
    assert not app.exception and not app.error

    zip_project = MultiInstrumentProject.load(zipped, root=tmp_path / 'unpacked')
    zipped_project_file = zip_project.root / 'project.json'
    session = save_session(tmp_path / 'reviewed.faradaiq_session.json', source=zip_project.root,
                           analysis_root=zip_project.root / 'Measurements',
                           browser_state={**zip_project.state, 'multi_project': zip_project.snapshot()})
    session_root = tmp_path / 'session data relocated'
    zip_project.relocate({zip_project.root: session_root})
    for path, replacement in [(zipped_project_file, None), (relocated / 'project.json', None), (session, session_root)]:
        replay = _project_curves_app(path, tmp_path, replacement=replacement)
        a, b = replay.session_state['multi_project'].combined_data().tables
        assert a.is_relative_to(replacement or path.parent) and b.is_relative_to(replacement or path.parent)
        assert not replay.exception and not replay.error
        assert _widget(replay, 'multiselect', 'Measurements to compare').value == [b, a]
        assert _widget(replay, 'radio', 'Comparison').value == 'Difference'
        _widget(replay, 'radio', 'Compare and process').set_value('Process one measurement').run()
        assert _widget(replay, 'selectbox', 'Measurement to process').value == b
        assert _widget(replay, 'radio', 'Curve preview').value == 'Applied result'
        assert _widget(replay, 'selectbox', 'Current output').value == 'current density'
        np.testing.assert_allclose(replay.dataframe[0].value['y'], expected)
        results = tmp_path / 'Workbench' / 'Compare' / 'results'
        previous = set(results.glob('*/data.csv'))
        _button(replay, 'Save derived CSV and settings').click().run()
        [output] = set(results.glob('*/data.csv')) - previous
        np.testing.assert_allclose(pd.read_csv(output)['y_current_density_A/cm2'], expected)
        assert json.loads(output.with_name('settings.json').read_text())['sources'][0]['table_path'] == str(b)


def test_same_instrument_project_sources_require_a_reference_for_potential_arithmetic(tmp_path):
    from faradaiq.workbench_project import MultiInstrumentProject

    project = MultiInstrumentProject(tmp_path / 'project')
    for name, current in [('one', [1, 2]), ('two', [2, 4])]:
        raw = tmp_path / f'{name}.csv'
        pd.DataFrame({'potential_V': [0, .1], 'current_A': current}).to_csv(raw, index=False)
        project.add_source(raw, workspace=tmp_path / 'stage')
    app = _project_curves_app(project.save(), tmp_path)
    _widget(app, 'radio', 'Compare and process').set_value('Compare measurements').run()
    _widget(app, 'radio', 'Comparison').set_value('Difference').run()
    _button(app, 'Calculate difference').click().run()
    assert not app.dataframe
    assert any('potential reference labels' in error.value for error in app.error)
    _widget(app, 'radio', 'Compare and process').set_value('Process one measurement').run()
    for path in project.combined_data().tables:
        _widget(app, 'selectbox', 'Measurement to process').set_value(path).run()
        _widget(app, 'text_input', 'Potential reference as recorded').set_value('SHE').run()
        _button(app, 'Apply processing').click().run()
    _widget(app, 'radio', 'Compare and process').set_value('Compare measurements').run()
    _button(app, 'Calculate difference').click().run()
    assert not app.error
    assert app.dataframe[0].value['y'].tolist() == [-1., -2.]


def test_statistical_comparison_rejects_duplicate_views_of_one_acquisition(tmp_path):
    from faradaiq.workbench_project import MultiInstrumentProject

    project = MultiInstrumentProject(tmp_path / 'project')
    raw = tmp_path / 'one.csv'
    raw.write_text('Time_s,Current_mA\n0,1\n1,2\n')
    independent = tmp_path / 'independent.csv'
    independent.write_bytes(raw.read_bytes())
    for source, duplicate in [(raw, False), (raw, True), (independent, True)]:
        project.add_source(source, workspace=tmp_path / 'stage', allow_duplicate=duplicate)
    a, b, c = project.combined_data().tables
    app = _project_curves_app(project.save(), tmp_path)
    _widget(app, 'radio', 'Compare and process').set_value('Compare measurements').run()
    _widget(app, 'radio', 'Comparison').set_value('Replicate statistics').run()
    _button(app, 'Calculate replicate statistics').click().run()
    assert any('one measurement per original acquisition' in error.value for error in app.error)
    assert not app.dataframe
    _widget(app, 'radio', 'Comparison').set_value('Control and treatment').run()
    _widget(app, 'multiselect', 'Control measurements').set_value([a]).run()
    _widget(app, 'multiselect', 'Treatment measurements').set_value([b]).run()
    _button(app, 'Calculate treatment effect').click().run()
    assert any('one measurement per original acquisition' in error.value for error in app.error)
    _widget(app, 'multiselect', 'Measurements to compare').set_value([a, c]).run()
    _widget(app, 'radio', 'Comparison').set_value('Replicate statistics').run()
    _button(app, 'Calculate replicate statistics').click().run()
    assert not app.error and not app.exception
    assert app.dataframe[-1].value['n'].tolist() == [2, 2]
