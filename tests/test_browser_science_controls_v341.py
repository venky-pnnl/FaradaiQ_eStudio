"""Scientific UI settings reach the existing analysis kernels and exports."""
from pathlib import Path
import json

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from faradaiq import browser_analysis as analysis


def widget(items, label):
    return next(item for item in items if item.label == label)


def polarization():
    voltage = np.linspace(-0.24, 0.26, 126)
    current = 3e-6 * (np.exp(20 * (voltage - .01)) - np.exp(-20 * (voltage - .01)))
    return pd.DataFrame({'potential_mV': voltage * 1000, 'current_uA': current * 1e6})


def test_tafel_controls_apply_working_units_and_manual_range(tmp_path):
    app = AppTest.from_string('''
import streamlit as st
from faradaiq import browser_science_controls as controls
st.session_state['parameters'] = controls.tafel_settings()
''').run()
    assert not app.exception
    widget(app.number_input, 'Electrode area (cm²)').set_value(2.5)
    widget(app.number_input, 'Equilibrium potential Eeq (V)').set_value(0.01)
    widget(app.number_input, 'Anodic η minimum (V)').set_value(0.080)
    widget(app.number_input, 'Anodic η maximum (V)').set_value(0.140)
    widget(app.checkbox, 'Bootstrap slope and j₀ uncertainty').uncheck().run()
    result = analysis.analyze_tafel(polarization(), tmp_path, settings=app.session_state['parameters'])[0]
    assert result.eeq_V == pytest.approx(0.01)
    summary = json.loads(Path(result.summary_json).read_text())
    assert summary['eeq_V'] == pytest.approx(0.01)
    assert abs(summary['nearest_measured_potential_V'] - 0.01) == pytest.approx(0.002)
    assert result.settings['surface_area_cm2'] == 2.5
    manual = next(row for row in result.comparison_rows if 'manual' in row['method'].lower() and row['branch'] == 'anodic')
    assert .080 - 1e-10 <= manual['eta_min_V'] < manual['eta_max_V'] <= .140 + 1e-10
    assert Path(result.comparison_csv).is_file()


def test_corrosion_settings_and_downloadable_results(tmp_path):
    app = AppTest.from_string('''
import streamlit as st
from faradaiq import browser_science_controls as controls
st.session_state['parameters'] = controls.corrosion_settings()
''').run()
    assert not app.exception
    widget(app.number_input, 'Corrosion electrode area (cm²)').set_value(1.7)
    widget(app.number_input, 'Equivalent weight (g/equiv)').set_value(27.9)
    widget(app.number_input, 'Material density (g/cm³)').set_value(7.86).run()
    result = analysis.analyze_corrosion(polarization(), settings=app.session_state['parameters'], output_dir=tmp_path)
    metrics = pd.read_csv(result['artifacts']['corrosion_metrics_csv'])
    assert metrics.loc[0, 'surface_area_cm2'] == 1.7
    snapshot = json.loads(Path(result['artifacts']['corrosion_metrics_json']).read_text())
    assert snapshot['settings']['equivalent_weight_g_per_equiv'] == 27.9
    assert snapshot['settings']['density_g_cm3'] == 7.86


def test_pysimple_controls_derive_native_defaults_and_save_the_adjusted_trace(tmp_path):
    app = AppTest.from_string('''
import numpy as np
import pandas as pd
import streamlit as st
from faradaiq import browser_science_controls as controls
p = np.linspace(-200, 240, 101)
table = pd.DataFrame({'potential_mV': p, 'current_uA': np.exp(-((p - 60) / 35)**2)})
st.session_state['data'] = table
st.session_state['parameters'] = controls.pysimple_settings(table)
''').run()
    assert not app.exception
    widget(app.number_input, 'PySimpleEChem scan rate (V/s)').set_value(0.030)
    widget(app.selectbox, 'Peak 1 mode').set_value('exact')
    widget(app.number_input, 'Peak 1 sample').set_value(58).run()
    data, parameters = app.session_state['data'], app.session_state['parameters']
    out = analysis.analyze_pysimple_cv(data, tmp_path, parameters=parameters)
    assert out['result']['scan_rate_V_s'] == 0.030
    assert out['result']['peak_1']['peak_idx'] == 58
    assert out['result']['peak_1']['peak_E_V'] == pytest.approx(data['potential_mV'].iloc[58] / 1000)
    trace = pd.read_csv(out['exports']['trace_csv'])
    assert list(trace.columns)[:3] == ['sample_index', 'potential_V', 'current_density_A_cm2']
    assert trace['baseline_1_A_cm2'].notna().all()


def test_drt_controls_reach_native_job_and_retain_settings(tmp_path):
    app = AppTest.from_string('''
import streamlit as st
from faradaiq import browser_science_controls as controls
st.session_state['parameters'] = controls.drt_settings()
''').run()
    assert not app.exception
    widget(app.number_input, 'Regularization').set_value(0.002)
    widget(app.selectbox, 'Derivative order').set_value(2)
    widget(app.multiselect, 'DRT figures').set_value(['drt', 're_residual']).run()
    f = np.geomspace(10000, .5, 30)
    z = 5 + 1 / (1 / 40 + 2j * np.pi * f * .001)
    frame = pd.DataFrame({'frequency_Hz': f, 'Zreal_ohm': z.real, 'Zimag_ohm': z.imag})
    receipt = analysis.run_drt(frame, tmp_path, **app.session_state['parameters'])
    settings = json.loads(receipt['settings_path'].read_text())
    assert settings['regularization'] == pytest.approx(.002)
    assert settings['derivative'] == 2
    assert {'simple'} == set(receipt['completed_modes'])
    assert any('re_residual' in figure.stem for figure in receipt['figures'])


def test_mott_workbook_csv_names_and_column_units(tmp_path):
    f = np.geomspace(10000, .5, 30)
    z = 5 + 1 / (1 / 40 + 2j * np.pi * f * .001)
    frame = pd.DataFrame({'frequency_Hz': f, 'Zreal_ohm': z.real, 'Zimag_ohm': z.imag})
    grid = pd.concat([frame.assign(potential_V=-.1), frame.assign(potential_V=.1)])
    result = analysis.analyze_mott_schottky(grid, output_dir=tmp_path)
    capacitance = pd.read_csv(result['artifacts']['capacitance_csv'])
    assert set(capacitance['potential_V']) == {-.1, .1}
    assert 'C_storage_F' in capacitance
    assert Path(result['artifacts']['interpretation_json']).is_file()


@pytest.mark.parametrize('header', ['I/mA', '<I>/mA', 'I (mA)', 'I/mA (mean)'])
def test_native_instrument_current_headers_scale_to_amperes(header):
    trace = pd.DataFrame({'time_s': [0., 1., 2., 3.], header: [-2., -2., -2., -2.]})
    assert analysis.measurement_roles(trace)['current'] == header
    result = analysis.analyze_trace(trace, 'CA')
    assert result['metrics']['integrated_charge_C'] == pytest.approx(-.006)


@pytest.mark.parametrize('header', ['current_mA/cm2', 'I (mA cm⁻²)', 'Current density / A cm^-2', 'j_mA_cm2'])
def test_charge_fails_closed_for_current_density(header):
    trace = pd.DataFrame({'time_s': [0., 1., 2., 3.], 'potential_V': [0., .1, .2, .3], header: [-2., -2., -2., -2.]})
    with pytest.raises(ValueError, match='contains current density'):
        analysis.analyze_trace(trace, 'CA')
    assert analysis.analyze_trace(trace, 'OCP')['status'] == 'complete'


def test_measured_current_takes_priority_over_density():
    frame = pd.DataFrame({'time_s': [0., 1., 2.], 'current_density_mA_cm2': [-1., -2., -3.],
                          '<I>/mA': [-2., -2., -2.]})
    assert analysis.measurement_roles(frame)['current'] == '<I>/mA'
    assert analysis.analyze_trace(frame, 'CA')['metrics']['integrated_charge_C'] == pytest.approx(-.004)


def test_native_bht_outputs_score_and_drt_with_current_scipy(tmp_path):
    from threading import Event, Timer

    f = np.geomspace(10000., 0.5, 15)
    z = 5 + 1 / (1 / 40 + 2j * np.pi * f * .001)
    table = pd.DataFrame({'frequency_Hz': f, 'Zreal_ohm': z.real, 'Zimag_ohm': z.imag})
    cancel = Event()
    watchdog = Timer(45, cancel.set)
    retries = 0

    def check_progress(message):
        nonlocal retries
        if 'Try Another Inital Condition' in message:
            retries += 1
        if retries > 16:
            cancel.set()

    watchdog.start()
    try:
        receipt = analysis.run_drt(table, tmp_path, cancel, run_types=('bht',), plot_types=('drt', 'score'),
                                   progress_callback=check_progress)
    finally:
        watchdog.cancel()
    assert receipt['completed_modes'] == ['bht']
    assert not receipt['failed_modes']
    assert any('_score' in path.stem.lower() for path in receipt['figures'])
    distribution = pd.read_csv(receipt['drt_csvs'][0])
    assert list(distribution.columns) == ['tau_s', 'gamma_real_ohm', 'gamma_imaginary_ohm']
    assert not distribution.empty
