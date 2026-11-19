"""Exercise workflows that previously needed manual reselection or silent skips."""
from pathlib import Path
from threading import Event
import json
import time

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from faradaiq.browser_data import open_data
from faradaiq.browser_productivity import (
    compare_trace_metrics, measurement_label, prepare_xy_series, recent_items,
    remember_recent, table_quality,
)

ROOT = Path(__file__).resolve().parents[1]


def test_overlay_converts_units_and_impedance_sign_without_changing_inputs(tmp_path):
    first = pd.DataFrame({'potential_V': [.1, .2], 'current_A': [.001, .002]})
    second = pd.DataFrame({'Ewe/mV': [100., 200.], 'I/mA': [1., 2.]})
    original = second.copy(deep=True)
    series, skipped = prepare_xy_series([(tmp_path/'one.csv', first), (tmp_path/'two.csv', second)],
                                         'potential_V', 'current_A')
    assert not skipped
    assert len(series) == 2
    np.testing.assert_allclose(series[1][1], first)
    pd.testing.assert_frame_equal(second, original)
    positive = pd.DataFrame({'Zreal_ohm': [1., 2.], 'neg_zimag_ohm': [3., 4.]})
    signed = pd.DataFrame({'Zreal_kohm': [.001, .002], 'Zimag_kohm': [-.003, -.004]})
    series, skipped = prepare_xy_series([(tmp_path/'a', positive), (tmp_path/'b', signed)],
                                         'Zreal_ohm', 'neg_zimag_ohm')
    assert not skipped
    np.testing.assert_allclose(series[1][1], positive)


@pytest.mark.parametrize('overlay', [
    {'Potential': [.1, .2], 'Current': [1., 2.]},
    {'potential_V': [.1, .2], 'current_density_mA_cm2': [1., 2.]},
    {'time_s': [1., 2.], 'potential_V': [.1, .2]},
])
def test_incompatible_overlays_are_explained(tmp_path, overlay):
    frame = pd.DataFrame({'potential_V': [.1, .2], 'current_A': [.001, .002]})
    series, skipped = prepare_xy_series([(tmp_path/'active.csv', frame), (tmp_path/'other.csv', pd.DataFrame(overlay))],
                                         'potential_V', 'current_A')
    assert len(series) == 1
    assert len(skipped) == 1 and 'other.csv' in skipped[0]


def test_quality_reports_invalid_and_repeated_values_without_mutation():
    frame = pd.DataFrame({'frequency_Hz': [100, 100, 0, -1], 'Zreal_ohm': [1, 2, np.nan, 4],
                          'Zimag_ohm': [-1, -2, -3, np.inf], 'time_s': [0, 1, 1, .5]})
    before = frame.copy(deep=True)
    checks, issues = table_quality(frame)
    assert checks['Missing / nonfinite'].sum() == 2
    text = ' '.join(issues)
    assert all(word in text for word in ('nonpositive', 'repeated frequency', 'backward', 'repeated timestamp'))
    pd.testing.assert_frame_equal(frame, before)


def test_recent_work_is_bounded_deduplicated_and_keeps_preferences(tmp_path):
    preferences = {'workspace': 'somewhere', 'scilink_url': 'http://127.0.0.1:8810'}
    for index in range(12):
        preferences = remember_recent(preferences, tmp_path/f'data{index}', 'data')
    preferences = remember_recent(preferences, tmp_path/'data5', 'data')
    assert len(preferences['recent']) == 8
    assert preferences['recent'][0]['path'] == str(tmp_path/'data5')
    assert len({item['path'] for item in preferences['recent']}) == 8
    assert preferences['workspace'] == 'somewhere'
    assert recent_items([None, {}, {'kind': 'data', 'path': 5}]) == []


def test_cv_comparison_includes_sweep_extrema_in_SI_units(tmp_path):
    source = tmp_path/'CV.csv'
    source.write_text('potential_mV,current_uA,time_s\n0,0,0\n100,3,1\n200,1,2\n300,0,3\n200,-1,4\n100,-2,5\n0,0,6\n')
    data = open_data(source, tmp_path/'Work')
    result = compare_trace_metrics(data, data.tables)
    assert result.loc[0, 'sweep_1_current_max_A'] == pytest.approx(3e-6)
    assert result.loc[0, 'sweep_1_potential_at_current_max_V'] == pytest.approx(.1)
    assert result.loc[0, 'sweep_2_current_min_A'] == pytest.approx(-2e-6)


def test_metrics_comparison_keeps_charge_units_errors_and_cancel(tmp_path):
    folder = tmp_path/'experiment'
    folder.mkdir()
    ca = folder/'CA.csv'
    ca.write_text('time_ms,current_mA\n0,2\n1000,2\n2000,2\n3000,2\n')
    eis = folder/'EIS.csv'
    eis.write_text('frequency_Hz,Zreal_ohm,Zimag_ohm\n100,3,-2\n10,4,-1\n')
    originals = {p: p.read_bytes() for p in (ca, eis)}
    data = open_data(folder, tmp_path/'Work')
    result = compare_trace_metrics(data, data.tables).set_index('Measurement')
    assert result.loc['CA.csv', 'Status'] == 'complete'
    assert result.loc['CA.csv', 'integrated_charge_C'] == pytest.approx(.006)
    assert result.loc['EIS.csv', 'Status'] == 'unavailable'
    assert 'EIS' in result.loc['EIS.csv', 'Detail']
    for path, original in originals.items():
        assert path.read_bytes() == original
    cancel = Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        compare_trace_metrics(data, data.tables, cancel=cancel)
    data.records[ca].update(dc_potential_V=0.0, cycle=0)
    assert '0 V' in measurement_label(data, ca) and 'cycle 0' in measurement_label(data, ca)


def widget(widgets, label):
    return next(w for w in widgets if w.label == label)


def app_with_data(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path/'home'))
    source = tmp_path/'Cu series'
    source.mkdir()
    for name, header, values in (
        ('CA_A', 'time_s,current_A', '0,0.002\n1,0.002\n2,0.002\n3,0.002'),
        ('CA_mA', 'time_ms,current_mA', '0,2\n1000,2\n2000,2\n3000,2'),
    ):
        (source/f'{name}.csv').write_text(header+'\n'+values+'\n')
    app = AppTest.from_file(str(ROOT/'faradaiq/browser_app.py'), default_timeout=30).run()
    app.session_state['preferences'] = {'workspace': str(tmp_path/'Work')}
    widget(app.text_input, 'Local file or folder').set_value(str(source))
    widget(app.button, 'Open data').click().run()
    assert not app.exception and not app.error
    return app, source


def test_browser_catalog_navigation_quicksave_restore_and_reopen(tmp_path, monkeypatch):
    app, source = app_with_data(tmp_path, monkeypatch)
    widget(app.button, 'Use this measurement in Analysis').click().run()
    assert widget(app.radio, 'Workspace').value == 'Analysis'
    assert not app.exception
    assert widget(app.button, 'Previous measurement').disabled
    widget(app.button, 'Next measurement').click().run()
    assert widget(app.selectbox, 'Active measurement').value.name == 'CA_mA.csv'
    widget(app.text_input, 'Filter measurements').set_value('CA_A').run()
    assert widget(app.selectbox, 'Active measurement').value.name == 'CA_A.csv'
    widget(app.text_input, 'Plot title').set_value('Saved copper comparison').run()
    widget(app.multiselect, 'Overlay measurements').set_value([source/'CA_mA.csv']).run()
    widget(app.button, 'Quick save session').click().run()
    assert not app.exception and not app.error
    session = Path(app.session_state['session_file'])
    assert session.is_file() and session.is_relative_to(tmp_path/'Work'/'Sessions')
    payload = json.loads(session.read_text())
    assert payload['browser_workspace']['state']['plot_recipe']['title'] == 'Saved copper comparison'
    widget(app.text_input, 'Plot title').set_value('Unsaved change').run()
    widget(app.radio, 'Workspace').set_value('Data').run()
    widget(app.text_input, 'Local session JSON path').set_value(str(session)).run()
    widget(app.button, 'Open session').click().run()
    assert not app.exception and not app.error
    widget(app.radio, 'Workspace').set_value('Analysis').run()
    assert widget(app.text_input, 'Plot title').value == 'Saved copper comparison'
    widget(app.button, 'Reset plot defaults').click().run()
    assert widget(app.text_input, 'Plot title').value == ''
    widget(app.button, 'Quick save session').click().run()
    assert app.session_state['session_file'] == str(session)
    assert len(list((tmp_path/'Work'/'Sessions').glob('*.json'))) == 1
    second = AppTest.from_file(str(ROOT/'faradaiq/browser_app.py'), default_timeout=30).run()
    assert widget(second.selectbox, 'Recent data and sessions').options
    widget(second.button, 'Reopen recent work').click().run()
    assert not second.exception and not second.error
    assert second.session_state['session_file'] == str(session)


def test_browser_select_filtered_metrics_and_download(tmp_path, monkeypatch):
    app, source = app_with_data(tmp_path, monkeypatch)
    widget(app.button, 'Analyze loaded data').click().run()
    widget(app.button, 'Select filtered measurements').click().run()
    assert len(widget(app.multiselect, 'Measurements for metrics').value) == 2
    widget(app.button, 'Calculate comparison').click().run()
    deadline = time.monotonic() + 20
    while 'job' in app.session_state and time.monotonic() < deadline:
        time.sleep(.1)
        app.run()
    assert not app.exception and not app.error
    result = app.session_state['Trace comparison']['table']
    assert result['Status'].tolist() == ['complete', 'complete']
    assert result['integrated_charge_C'].to_numpy() == pytest.approx([.006, .006])


def test_moved_session_uses_replacement_data(tmp_path, monkeypatch):
    app, source = app_with_data(tmp_path, monkeypatch)
    widget(app.button, 'Quick save session').click().run()
    session = app.session_state['session_file']
    moved = source.with_name('Moved Cu series')
    source.rename(moved)
    widget(app.text_input, 'Local session JSON path').set_value(session).run()
    widget(app.text_input, 'Replacement data path (if moved)').set_value(str(moved)).run()
    widget(app.button, 'Open session').click().run()
    assert not app.exception and not app.error
    assert app.session_state['dataset'].source == moved
