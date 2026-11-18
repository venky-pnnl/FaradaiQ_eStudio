"""Local browser controls for the existing scientific analysis engines."""
from __future__ import annotations

import streamlit as st


def cv_scan_rate(*, key='cv_branch'):
    return st.number_input('Scan rate (V/s)', min_value=0.000000001, value=None, format='%.6f', key=f'{key}:rate',
                           help='Entering a scan rate enables the branch-specific charge calculation.')


def tafel_settings(*, key='tafel'):
    from faradaiq_tafel_adapter import TafelSettings

    defaults = TafelSettings()
    material, fit = st.columns(2)
    with material:
        area = st.number_input('Electrode area (cm²)', min_value=0.000001, value=defaults.surface_area_cm2,
                               format='%.6g', key=f'{key}:area')
        equilibrium = st.number_input('Equilibrium potential Eeq (V)', value=None, format='%.6f',
                                      key=f'{key}:eeq', help='Leave blank to estimate Eeq from the minimum absolute current.')
        temperature = st.number_input('Temperature (K)', min_value=1.0, value=defaults.temperature_K,
                                      key=f'{key}:temperature')
    with fit:
        electrons = st.number_input('Electrons transferred', min_value=1, value=defaults.n_electrons,
                                    key=f'{key}:electrons')
        resistance = st.number_input('Uncompensated resistance (Ω)', min_value=0.0,
                                     value=defaults.uncompensated_resistance_ohm, key=f'{key}:ru')
        branch = st.selectbox('Scan branch', ('Whole trace / LSV', 'Forward scan only', 'Reverse scan only',
                                             'Forward + reverse separately'), key=f'{key}:branch')
    with st.expander('Fit ranges and confidence'):
        st.caption('Enter overpotential η relative to Eeq. Cathodic values are negative and anodic values are positive.')
        left, right = st.columns(2)
        with left:
            cath_min = st.number_input('Cathodic η minimum (V)', max_value=0.0, value=defaults.manual_cathodic_range_V[0],
                                       format='%.4f', key=f'{key}:cath_min')
            cath_max = st.number_input('Cathodic η maximum (V)', max_value=0.0, value=defaults.manual_cathodic_range_V[1],
                                       format='%.4f', key=f'{key}:cath_max')
            minimum_abs_eta = st.number_input('Automatic minimum |η| (V)', min_value=0.0,
                                              value=defaults.auto_min_abs_eta_V, format='%.4f', key=f'{key}:eta_min')
            minimum_window = st.number_input('Minimum automatic window span (V)', min_value=0.0001,
                                             value=defaults.auto_min_window_V, format='%.4f', key=f'{key}:span')
        with right:
            anod_min = st.number_input('Anodic η minimum (V)', min_value=0.0, value=defaults.manual_anodic_range_V[0],
                                       format='%.4f', key=f'{key}:anod_min')
            anod_max = st.number_input('Anodic η maximum (V)', min_value=0.0, value=defaults.manual_anodic_range_V[1],
                                       format='%.4f', key=f'{key}:anod_max')
            maximum_abs_eta = st.number_input('Automatic maximum |η| (V)', min_value=0.0001,
                                              value=defaults.auto_max_abs_eta_V, format='%.4f', key=f'{key}:eta_max')
            minimum_r2 = st.number_input('Minimum automatic-window R²', min_value=0.0, max_value=1.0,
                                         value=defaults.auto_min_r2, format='%.4f', key=f'{key}:r2')
        window, optimization = st.columns(2)
        with window:
            fitting_window = st.number_input('Butler–Volmer total η window (mV)', min_value=0.1,
                                             value=defaults.fitting_window_mV, key=f'{key}:bv_window')
            minimum_points = st.number_input('Minimum points per automatic window', min_value=3,
                                             value=defaults.auto_min_points, key=f'{key}:minimum_points')
        with optimization:
            mode = st.selectbox('Optimization mode', ('Fast', 'Balanced', 'Thorough'), index=1, key=f'{key}:mode')
            bootstrap_samples = st.number_input('Bootstrap samples', min_value=10,
                                                value=defaults.bootstrap_samples, step=10, key=f'{key}:bootstrap_samples')
        bootstrap = st.checkbox('Bootstrap slope and j₀ uncertainty', value=defaults.enable_bootstrap,
                                key=f'{key}:bootstrap')
        adjust = st.checkbox('Subtract current at supplied Eeq', value=defaults.current_adjust, key=f'{key}:adjust')
    scan_windows = {'Fast': (60.0, 120.0, 180.0), 'Balanced': defaults.eta_scan_windows_mV,
                    'Thorough': (*defaults.eta_scan_windows_mV, 300.0)}
    return TafelSettings(surface_area_cm2=area, n_electrons=electrons, temperature_K=temperature, eeq_V=equilibrium,
                         current_adjust=adjust, fitting_window_mV=fitting_window, eta_scan_windows_mV=scan_windows[mode],
                         manual_cathodic_range_V=(cath_min, cath_max), manual_anodic_range_V=(anod_min, anod_max),
                         auto_min_points=minimum_points, auto_min_abs_eta_V=minimum_abs_eta,
                         auto_max_abs_eta_V=maximum_abs_eta, auto_min_window_V=minimum_window,
                         auto_min_r2=minimum_r2, enable_bootstrap=bootstrap, bootstrap_samples=bootstrap_samples,
                         uncompensated_resistance_ohm=resistance, optimizer_mode=mode, scan_branch_mode=branch)


def corrosion_settings(*, key='corrosion'):
    from .corrosion_metrics_v298 import CorrosionSettings

    defaults = CorrosionSettings()
    area, equivalent, density = st.columns(3)
    surface_area = area.number_input('Corrosion electrode area (cm²)', min_value=0.000001, value=None,
                                     format='%.6g', key=f'{key}:area')
    weight = equivalent.number_input('Equivalent weight (g/equiv)', min_value=0.000001, value=None,
                                     format='%.6g', key=f'{key}:weight')
    mass_density = density.number_input('Material density (g/cm³)', min_value=0.000001, value=None,
                                        format='%.6g', key=f'{key}:density')
    st.caption('Corrosion current density requires electrode area. Corrosion rate also requires equivalent weight and density.')
    with st.expander('Corrosion-potential selection'):
        left, right = st.columns(2)
        crossing = left.number_input('Zero-crossing cluster tolerance (mV)', min_value=0.0,
                                     value=defaults.crossing_cluster_mV, key=f'{key}:crossing')
        separation = right.number_input('Minimum dip separation (mV)', min_value=0.0,
                                        value=defaults.minimum_dip_separation_mV, key=f'{key}:separation')
        prominence = left.number_input('Minimum dip prominence (decades)', min_value=0.0,
                                       value=defaults.minimum_dip_prominence_dec, key=f'{key}:prominence')
        offset = right.number_input('Maximum Tafel intersection offset (mV)', min_value=0.0,
                                    value=defaults.fit_max_seed_offset_mV, key=f'{key}:offset')
    return CorrosionSettings(surface_area_cm2=surface_area, equivalent_weight_g_per_equiv=weight,
                             density_g_cm3=mass_density, crossing_cluster_mV=crossing,
                             minimum_dip_separation_mV=separation, minimum_dip_prominence_dec=prominence,
                             fit_max_seed_offset_mV=offset)


def pysimple_settings(table, *, potential=None, current=None, key='pysimple'):
    from faradaiq_pysimpleechem_cv_adapter import REF_POTENTIALS_V
    from .browser_analysis import pysimple_defaults

    try:
        defaults = pysimple_defaults(table, potential=potential, current=current)
    except ValueError as error:
        st.info(str(error))
        return {}
    max_sample = defaults.trim_end
    st.caption('Sample indices start at zero and follow acquisition order after rows with missing potential or current are removed.')
    fingerprint = f'{key}:{potential}:{current}:{max_sample}'
    setup, reference = st.columns(2)
    with setup:
        area = st.number_input('PySimpleEChem electrode area (cm²)', min_value=0.000001, value=defaults.elec_area,
                               format='%.6g', key=f'{fingerprint}:area')
        rate = st.number_input('PySimpleEChem scan rate (V/s)', min_value=0.0, value=defaults.scan_rate, format='%.6f',
                               key=f'{fingerprint}:rate', help='Leave at zero when the scan rate is unknown.')
        resistance = st.number_input('PySimpleEChem iR compensation (Ω)', value=defaults.ir_compensation,
                                     key=f'{fingerprint}:ru', help='The adapter applies E + I × R.')
    with reference:
        reference_from = st.selectbox('Recorded potential reference', tuple(REF_POTENTIALS_V), key=f'{fingerprint}:ref_from')
        reference_to = st.selectbox('Display potential reference', tuple(REF_POTENTIALS_V), key=f'{fingerprint}:ref_to')
    with st.expander('Sample ranges, baselines, and peak selection', expanded=True):
        trim = st.slider('Trace sample range', min_value=0, max_value=max_sample, value=(0, max_sample),
                         key=f'{fingerprint}:trim')
        radius = st.number_input('Peak search radius (samples)', min_value=0, max_value=max_sample,
                                value=min(max_sample, defaults.peak_range), key=f'{fingerprint}:radius')
        peak_columns = st.columns(2)
        parameters = {'trim_start': trim[0], 'trim_end': trim[1], 'peak_range': radius, 'scan_rate': rate,
                      'elec_area': area, 'ir_compensation': resistance, 'ref_from': reference_from, 'ref_to': reference_to}
        for peak, block in enumerate(peak_columns, start=1):
            with block:
                label = f'Peak {peak}'
                modes = ('max', 'min', 'exact', '2nd derivative')
                parameters[f'peak_mode_{peak}'] = st.selectbox(f'{label} mode', modes, index=peak - 1, key=f'{fingerprint}:mode{peak}')
                parameters[f'peak_pos_{peak}'] = st.number_input(f'{label} sample', min_value=0, max_value=max_sample,
                                                                  value=getattr(defaults, f'peak_pos_{peak}'),
                                                                  key=f'{fingerprint}:sample{peak}')
                baseline = tuple(min(max_sample, getattr(defaults, f'baseline_{end}_{peak}')) for end in ('start', 'end'))
                if baseline[0] == baseline[1]:
                    baseline = (max(0, baseline[0] - 2), min(max_sample, baseline[1] + 2))
                start, end = st.slider(f'{label} baseline samples', min_value=0, max_value=max_sample, value=baseline,
                                       key=f'{fingerprint}:baseline{peak}')
                parameters[f'baseline_start_{peak}'] = start
                parameters[f'baseline_end_{peak}'] = end
    return parameters


def drt_settings(*, key='drt'):
    from .browser_analysis import DRT_MIN_BAYESIAN_SAMPLES

    with st.expander('DRT settings'):
        left, right = st.columns(2)
        with left:
            run_types = st.multiselect('DRT methods', ('simple', 'bayesian', 'bht'), default=['simple'],
                                       key=f'{key}:methods', format_func={'simple': 'Simple (Tikhonov)',
                                                                         'bayesian': 'Bayesian',
                                                                         'bht': 'Bayesian Hilbert transform'}.__getitem__)
            regularization = st.number_input('Regularization', min_value=1e-10, value=1e-3,
                                              format='%.3e', key=f'{key}:lambda')
            derivative = st.selectbox('Derivative order', (1, 2), key=f'{key}:derivative')
            basis = st.selectbox('Basis function', ('gaussian', 'c0', 'c2', 'c4', 'c6', 'inverse_quadratic',
                                                    'inverse_quadric', 'cauchy'), key=f'{key}:rbf')
        with right:
            data = st.selectbox('Impedance component', ('combined', 're', 'im'), key=f'{key}:data')
            inductance = st.selectbox('Inductance', ('without', 'with', 'discard'), key=f'{key}:inductance')
            shape = st.selectbox('Basis shape control', ('fwhm', 'shape'), key=f'{key}:shape')
            coefficient = st.number_input('Basis shape coefficient', min_value=0.000001, value=0.5,
                                          key=f'{key}:coefficient')
        samples = st.number_input('Bayesian samples', min_value=DRT_MIN_BAYESIAN_SAMPLES, value=2000, step=100,
                                  key=f'{key}:samples', help='The native sampler discards the first 501 samples as burn-in.')
        figures = st.multiselect('DRT figures', ('eis', 'magnitude', 'phase', 're', 'im', 're_residual', 'im_residual',
                                                'drt', 'score'), default=['eis', 'drt'], key=f'{key}:figures',
                                 help='The score plot is generated only for a Bayesian Hilbert transform run.')
        save_eis = st.checkbox('Also export fitted and measured EIS', key=f'{key}:eis_csv')
    return {'run_types': tuple(run_types or ['simple']), 'plot_types': tuple(figures or ['drt']),
            'export_types': ('drt', 'eis') if save_eis else ('drt',), 'rbf_type': basis, 'derivative': derivative,
            'data': data, 'inductance': inductance, 'regularization': regularization, 'shape_control': shape,
            'coefficient': coefficient, 'sample_number': samples}
