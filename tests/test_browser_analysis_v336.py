"""Browser adapters use the desktop analysis engines and retain scientific units."""
from dataclasses import asdict
from pathlib import Path
from threading import Event

import numpy as np
import pandas as pd
import pytest

from faradaiq.browser_analysis import (
    analyze_mott_schottky,
    analyze_tafel,
    analyze_trace,
    eis_table,
    fit_circuit,
    run_drt,
    spatial_points,
    validate_impedance,
)
from faradaiq.v228_intelligence import _fit_circuit, _lin_kk


def impedance_frame():
    f = np.geomspace(10000, 0.5, 30)
    z = 5 + 1 / (1 / 40 + 2j * np.pi * f * 0.001)
    return pd.DataFrame({'frequency_Hz': f, 'Zreal_ohm': z.real, 'Zimag_ohm': z.imag}), f, z


def test_eis_fit_and_kk_match_native_results():
    frame, f, z = impedance_frame()
    prepared = eis_table(frame)
    assert asdict(fit_circuit(prepared)) == asdict(_fit_circuit('R-RC', f, z, ''))
    assert validate_impedance(prepared) == _lin_kk(f, z)


def test_explicit_impedance_units_and_nyquist_sign_preserve_source():
    raw = pd.DataFrame({'freq/kHz': [1.0, 2.0, 0.0], 'real (kΩ)': [0.001, 0.002, 0.003],
                        '-Im [Ω]': [3.0, 4.0, 5.0]})
    prepared = eis_table(raw, 'freq/kHz', 'real (kΩ)', '-Im [Ω]', negate_imaginary=True)
    assert prepared.to_numpy() == pytest.approx(np.array([[1000., 1., -3.], [2000., 2., -4.]]))
    assert raw.iloc[0].tolist() == [1., .001, 3.]
    with pytest.raises(ValueError, match='different columns'):
        eis_table(raw, 'freq/kHz', 'real (kΩ)', 'real (kΩ)')


def test_native_drt_produces_readable_csv_and_respects_precancel(tmp_path):
    frame, _, _ = impedance_frame()
    cancel = Event(); cancel.set()
    with pytest.raises(InterruptedError):
        run_drt(eis_table(frame), tmp_path, cancel)
    assert not list(tmp_path.iterdir())
    result = run_drt(eis_table(frame), tmp_path)
    assert result['figures'] and result['log_path'].is_file()
    assert result['drt_csvs'] and not pd.read_csv(result['drt_csvs'][0]).empty
    assert all(result['output_dir'] in path.parents for path in result['drt_csvs'])


def test_trace_analysis_preserves_units_and_cp_measured_charge():
    data = pd.DataFrame({'time_ms': range(0, 5001, 100), 'potential_mV': np.linspace(200, 220, 51),
                         'current_mA': [-2.] * 51})
    ca = analyze_trace(data, 'CA')
    assert ca['metrics']['integrated_charge_C'] == pytest.approx(-0.010)
    assert ca['transient']['charge_C'] == pytest.approx(-0.010)
    cp = analyze_trace(data, 'CP')
    assert cp['metrics']['potential_start_V'] == pytest.approx(0.200)
    assert cp['metrics']['integrated_charge_C'] == pytest.approx(-0.010)
    ocp = analyze_trace(data, 'OCP')
    assert ocp['tail']['mean_ocp_V'] == pytest.approx(0.2170, abs=0.00021)
    cv = analyze_trace(data, 'LSV')
    assert cv['metrics']['sweeps'][0]['charge_C'] == pytest.approx(-0.010)


def test_tafel_worker_retains_outputs_and_uses_requested_area(tmp_path):
    from faradaiq_tafel_adapter import TafelSettings
    potential = np.linspace(-0.22, 0.22, 101)
    current = 2e-6 * (np.exp(20 * potential) - np.exp(-20 * potential))
    raw = pd.DataFrame({'Potential (mV)': potential * 1000, 'Current (uA)': current * 1e6})
    results = analyze_tafel(raw, tmp_path, settings=TafelSettings(enable_bootstrap=False, surface_area_cm2=2.0))
    assert results[0].settings['surface_area_cm2'] == 2.0
    assert Path(results[0].normalized_csv).is_file()
    assert Path(results[0].figure_png).is_file()


def test_mott_rejects_incomplete_grids_and_calls_shared_diagnostics():
    frame, f, z = impedance_frame()
    first = frame.assign(potential_V=-0.1)
    second = frame.assign(potential_V=0.1)
    incomplete = pd.concat([first, second.iloc[:-1]], ignore_index=True)
    with pytest.raises(ValueError, match='same frequency grid'):
        analyze_mott_schottky(incomplete)
    result = analyze_mott_schottky(pd.concat([first, second], ignore_index=True))
    assert result['summary'] and result['mott']
    assert any(row['reason'] == 'insufficient_positive_capacitance' for row in result['mott'])


def test_spatial_does_not_manufacture_coordinates():
    frame = pd.DataFrame({'x_mm': [0., 0., 1., 1.], 'y_mm': [2., 2., 4., 4.], 'response': [2., 4., 6., 8.]})
    result = spatial_points(frame, signal='response')
    assert list(result['x_m']) == [0.0, 0.001]
    assert list(result['y_m']) == [0.002, 0.004]
    assert list(result['mean']) == [3., 7.]
    with pytest.raises(ValueError, match='X position'):
        spatial_points(frame.drop(columns='x_mm'), signal='response')


def test_native_cv_branches_support_internal_forward_and_reverse(tmp_path):
    from faradaiq.browser_analysis import analyze_cv_branches
    potential = np.r_[np.linspace(-0.4, 0.4, 51), np.linspace(.398, -.4, 50)]
    current = np.r_[np.exp(-((potential[:51] - .10) / .075) ** 2),
                    -np.exp(-((potential[51:] + .10) / .075) ** 2)] * 1e-6
    frame = pd.DataFrame({'potential_V': potential, 'current_A': current})
    result = analyze_cv_branches(frame, tmp_path, scan_rate_V_s=0.02)
    metrics = pd.read_csv(result['metrics_csv'])
    assert set(metrics['branch_key']) == {'forward', 'reverse'}
    assert metrics['scan_rate_V_s'].tolist() == [0.02, 0.02]
    assert metrics['charge_C'].notna().all()


def test_browser_calls_native_plotly_3d_for_selected_data(tmp_path):
    from faradaiq.browser_analysis import make_native_view
    from hashlib import sha256
    frame, _, _ = impedance_frame()
    path = tmp_path / 'eis.csv'
    frame.to_csv(path, index=False)
    digest = sha256(path.read_bytes()).hexdigest()
    output = make_native_view([{'path': str(path), 'technique_key': 'EIS', 'point': 1}],
                              tmp_path / 'results', mode='3D')
    assert output['curves'] == 1
    assert Path(output['html']).is_file()
    assert Path(output['html']).with_name('plotly.min.js').is_file()
    assert sha256(path.read_bytes()).hexdigest() == digest


def test_browser_drt_cancellation_removes_partial_outputs(tmp_path):
    frame, _, _ = impedance_frame()
    cancel = Event()
    with pytest.raises(InterruptedError):
        run_drt(eis_table(frame), tmp_path, cancel, progress_callback=lambda _: cancel.set())
    assert not list(tmp_path.iterdir())


def test_deareis_handoff_uses_native_normalization_without_optional_dependency(tmp_path):
    from faradaiq.browser_analysis import make_deareis_handoff
    frame, f, z = impedance_frame()
    result = make_deareis_handoff(eis_table(frame), tmp_path, make_project=False)
    normalized = pd.read_csv(result.normalized_files[0])
    assert normalized['frequency_Hz'].to_numpy() == pytest.approx(f)
    assert normalized['Zimag_ohm'].to_numpy() == pytest.approx(z.imag)
    assert Path(result.manifest).is_file()


def test_pysimple_cv_writes_native_result_metadata(tmp_path):
    from faradaiq.browser_analysis import analyze_pysimple_cv
    potential = np.r_[np.linspace(-0.4, 0.4, 51), np.linspace(.398, -.4, 50)]
    current = np.r_[np.exp(-((potential[:51] - .10) / .075) ** 2),
                    -np.exp(-((potential[51:] + .10) / .075) ** 2)] * 1e-6
    frame = pd.DataFrame({'potential_V': potential, 'current_A': current})
    result = analyze_pysimple_cv(frame, tmp_path, parameters={'scan_rate': 0.1})
    assert result['result']['scan_rate_V_s'] == 0.1
    assert 'PySimpleEChem' in result['result']['backend']
    assert Path(result['exports']['json']).is_file()
