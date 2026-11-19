"""Analytical recovery and browser tests for the v341 electrochemical workbench."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from faradaiq import workbench_science as ws


def _cv(anodic_peak=0.13, cathodic_peak=-0.12, amplitude=1e-4):
    ea = np.unique(np.r_[np.linspace(-0.4, 0.4, 101), anodic_peak])
    ec = np.unique(np.r_[np.linspace(-0.4, 0.4, 100), cathodic_peak])[::-1]
    e = np.r_[ea, ec[1:]]
    i = np.r_[amplitude * np.exp(-((ea - anodic_peak) / 0.045) ** 2),
              -amplitude * np.exp(-((ec[1:] - cathodic_peak) / 0.045) ** 2)]
    return pd.DataFrame({"Ewe/mV": e * 1000, "<I>/mA": i * 1000})


def _record(value, frame):
    return (str(value), frame, value, None, {"potential_reference": "RHE"})


def test_37_scan_rate_regression_keeps_cathodic_sign_and_audits_peaks():
    rates = np.array([0.01, 0.04, 0.09, 0.16])
    records = [_record(rate, _cv(amplitude=2e-4 * np.sqrt(rate))) for rate in rates]
    result = ws.scan_rate_series(records, polarity="cathodic", direction=-1)
    assert result.metrics["slope"] == pytest.approx(-2e-4)
    assert result.metrics["intercept"] == pytest.approx(0)
    assert list(result.tables["Peak observations"].sweep) == [2] * len(rates)
    with pytest.raises(ValueError, match="distinct"):
        ws.scan_rate_series([records[0]] * 3, polarity="anodic")


def test_38_track_both_cv_branches_without_fabricating_boundary_peaks():
    frame = _cv()
    result = ws.cv_peak_tracking(frame, polarity="cathodic", window=(-0.20, -0.065))
    peaks = result.tables["Peaks"]
    assert pd.isna(peaks.peak_potential_V.iloc[0])
    assert peaks.peak_potential_V.iloc[1] == pytest.approx(-0.12)
    assert peaks.peak_current_A.iloc[1] == pytest.approx(-1e-4)
    with pytest.raises(ValueError, match="ambiguous current units"):
        ws.cv_peak_tracking(frame.rename(columns={"<I>/mA": "current"}), polarity="anodic")


def test_39_interpolate_signed_cv_charge_and_include_reverse_turn():
    e = [0, 1, 2, 1, 0]
    data = pd.DataFrame({"potential_V": e, "current_mA": [-2, -1, 1, -1, -2], "time_ms": np.arange(5) * 1000})
    result = ws.integrate_cv_charge(data, branch=2, window=(1, 2))
    assert result.metrics["net_charge_C"] == pytest.approx(0)
    assert result.metrics["anodic_charge_C"] == pytest.approx(0.00025)
    assert result.metrics["cathodic_charge_C"] == pytest.approx(-0.00025)
    result = ws.integrate_cv_charge(data, branch=1, window=(0.25, 0.75), scan_rate_V_s=0.25)
    assert result.metrics["net_charge_C"] == pytest.approx(-0.003)
    with pytest.raises(ValueError, match="within"):
        ws.integrate_cv_charge(data, window=(-1, 1))


def test_39_measured_charge_includes_each_endpoint_dwell_once():
    frame = pd.DataFrame({"potential_V": [0, 0, 1, 2, 2, 1, 0], "current_A": 1, "time_s": np.arange(7)})
    increasing = ws.integrate_cv_charge(frame, branch=1, window=(0, 2))
    decreasing = ws.integrate_cv_charge(frame, branch=2, window=(0, 2))
    assert increasing.metrics["net_charge_C"] == 4
    assert decreasing.metrics["net_charge_C"] == 2
    assert increasing.tables["Integration points"].time_s.tolist() == [0, 1, 2, 3, 4]
    assert decreasing.tables["Integration points"].time_s.tolist() == [4, 5, 6]
    assert sum(result.metrics["net_charge_C"] for result in (increasing, decreasing)) == 6

    at_lower_hold = ws.integrate_cv_charge(frame, branch=1, window=(0, 0.5))
    at_upper_hold = ws.integrate_cv_charge(frame, branch=1, window=(0.5, 2))
    reverse_clipped = ws.integrate_cv_charge(frame, branch=2, window=(0, 1.5))
    assert at_lower_hold.tables["Integration points"].time_s.tolist() == [0, 1, 1.5]
    assert at_upper_hold.tables["Integration points"].time_s.tolist() == [1.5, 2, 3, 4]
    assert reverse_clipped.tables["Integration points"].time_s.tolist() == [4.5, 5, 6]


@pytest.mark.parametrize("current", ["current_A/kg", "current_mA/s", "Current (mA s⁻¹)", "charge_mA h",
                                    "charge_mA", "dI/dt (A)", "Current [A/V]", "current_A_per_g", "I (mA cm⁻²)"])
def test_39_rejects_compound_and_wrong_quantity_columns(current):
    frame = pd.DataFrame({"potential_V": [0, 1, 2], current: [1, 2, 3], "time_s": [0, 1, 2]})
    with pytest.raises(ValueError, match="ambiguous current units|contains current density"):
        ws.integrate_cv_charge(frame, window=(0, 2), columns={"current": current})


@pytest.mark.parametrize(("current", "scale"), [("current_A", 1), ("<I>/mA", 1e-3), ("I/mA (mean)", 1e-3),
                                                ("Current (µA)", 1e-6), ("I [pA]", 1e-12)])
def test_39_native_current_and_annotated_potential_use_exact_prefixes(current, scale):
    potential = "Potential (mV vs Ag/AgCl (3 M KCl))"
    frame = pd.DataFrame({potential: [0, 1000, 2000], current: [1, 2, 3], "t/ms": [0, 1000, 2000]})
    result = ws.integrate_cv_charge(frame, window=(0, 2))
    assert result.metrics["net_charge_C"] == pytest.approx(4 * scale, rel=1e-12, abs=0)
    assert ws.potential_reference_sources(frame, None, {}) == {"column": "Ag/AgCl (3 M KCl)"}


def test_40_cdl_recovers_half_current_difference_slope_with_background():
    e = [0, 0.1, 0.2, 0.3, 0.4, 0.3, 0.2, 0.1, 0]
    capacitance = 2e-5
    rates = [0.02, 0.05, 0.11, 0.2]
    records = []
    for rate in rates:
        i = np.r_[np.repeat(4e-7 + capacitance * rate, 5), np.repeat(3e-7 - capacitance * rate, 4)]
        records.append(_record(rate, pd.DataFrame({"potential_V": e, "current_A": i})))
    result = ws.double_layer_capacitance(records, potential_V=0.15)
    assert result.metrics["Cdl_F"] == pytest.approx(capacitance)
    assert result.metrics["intercept"] == pytest.approx(5e-8)
    with pytest.raises(ValueError, match="inside both"):
        ws.double_layer_capacitance(records, potential_V=0.5)


def test_41_ecsa_uses_supplied_matched_reference():
    result = ws.electrochemical_surface_area(signal=4e-4, reference_per_cm2=2e-4, method="charge", geometric_area_cm2=0.5)
    assert result.metrics["ECSA_cm2"] == 2
    assert result.metrics["roughness_factor"] == 4
    with pytest.raises(ValueError, match="reference"):
        ws.electrochemical_surface_area(signal=1.0, reference_per_cm2=None, method="capacitance")


def test_42_randles_sevcik_recovers_known_diffusion():
    d, t, n, area, concentration = 7.0e-6, 307.0, 2, 0.071, 1.2e-6
    slope = 0.4463 * n * ws.F * area * concentration * np.sqrt(n * ws.F * d / (ws.R * t))
    records = [_record(v, _cv(amplitude=slope * np.sqrt(v))) for v in (.01, .04, .09, .16)]
    kwargs = dict(polarity="anodic", area_cm2=area, concentration_mol_cm3=concentration, electrons=n, temperature_K=t)
    assert ws.randles_sevcik(records, **kwargs).metrics["diffusion_cm2_s"] == pytest.approx(d)
    with pytest.raises(ValueError, match="integer"):
        ws.randles_sevcik(records, **(kwargs | {"electrons": 1.4}))


def test_42_laviron_recovers_model_and_rejects_low_overpotentials():
    n, temperature, alpha, ks, e0 = 1, 285.0, 0.45, 0.04, 0.01
    factor = ws.R * temperature / (n * ws.F)
    a, c = factor / (1 - alpha), factor / alpha
    rates = [0.08, 0.18, 0.4, 0.85]
    records = [_record(v, _cv(e0 + a * np.log(v / (ks * a)), e0 - c * np.log(v / (ks * c)))) for v in rates]
    result = ws.laviron(records, electrons=n, temperature_K=temperature, formal_potential_V=e0)
    assert result.metrics["alpha_mean"] == pytest.approx(alpha)
    assert result.metrics["ks_geometric_mean_s_minus1"] == pytest.approx(ks)
    with pytest.raises(ValueError, match="2RT"):
        ws.laviron(records, electrons=n, temperature_K=temperature, formal_potential_V=0.25)


def test_43_ca_model_comparison_and_step_origin():
    elapsed = np.linspace(0.1, 10, 80)
    time = elapsed + 2
    cottrell = 7e-6 - 3e-5 / np.sqrt(elapsed)
    frame = pd.DataFrame({"time_s": time, "current_A": cottrell})
    result = ws.chronoamperometry_fit(frame, interval_s=(time[0], time[-1]), step_time_s=2)
    fits = result.tables["Model comparison"].set_index("model")
    assert fits.loc["Cottrell + offset", "cottrell_coefficient_A_sqrt_s"] == pytest.approx(-3e-5)
    assert fits.loc["Cottrell + offset", "current_offset_A"] == pytest.approx(7e-6)
    assert fits.loc["Cottrell + offset", "AICc"] < fits.loc["Exponential + offset", "AICc"]
    exp = 4e-6 + 5e-5 * np.exp(-elapsed / 1.2)
    result = ws.chronoamperometry_fit(frame.assign(current_A=exp), interval_s=(time[0], time[-1]), step_time_s=2)
    assert result.tables["Model comparison"].set_index("model").loc["Exponential + offset", "tau_s"] == pytest.approx(1.2)
    with pytest.raises(ValueError, match="positive time"):
        ws.chronoamperometry_fit(frame, interval_s=(time[0], time[-1]), step_time_s=3)


def test_44_progressive_nucleation_and_failures_are_diagnostic():
    time = np.linspace(0, 3, 151)
    inst, prog = ws.nucleation_curves(time)
    assert inst[0] == prog[0] == 0
    assert prog[50] == pytest.approx(1, abs=2e-4)
    # Start from the dimensional growth relation J = K*(1 - exp(-k*t**2))/sqrt(t).
    shape = np.zeros_like(time)
    shape[1:] = (1 - np.exp(-2.33665 * time[1:] ** 2)) / np.sqrt(time[1:])
    current = 2e-6 - 3e-5 * shape
    frame = pd.DataFrame({"time_s": time, "current_A": current})
    result = ws.scharifker_hills(frame, interval_s=(0, 3), step_time_s=0, polarity="cathodic", background_A=2e-6)
    assert result.metrics["peak_elapsed_s"] == pytest.approx(1.0)
    assert result.metrics["progressive_rmse"] < 1e-4
    assert result.metrics["instantaneous_rmse"] > 0.1
    with pytest.raises(ValueError, match="peak"):
        ws.scharifker_hills(frame, interval_s=(0, 0.8), step_time_s=0, polarity="cathodic", background_A=2e-6)


def test_45_cp_resolves_interior_transition_only():
    t = np.linspace(0, 6, 121)
    e = 0.3 + 0.2 * np.tanh((t - 3) / 0.17)
    frame = pd.DataFrame({"time_s": t, "potential_V": e})
    result = ws.chronopotentiometry_transition(frame, interval_s=(1, 5), threshold_V_s=0.1)
    assert result.metrics["transition_time_s"] == pytest.approx(3)
    assert result.metrics["transition_dE_dt_V_s"] > 1.0
    absent = ws.chronopotentiometry_transition(frame, interval_s=(1, 5), threshold_V_s=2)
    assert absent.metrics["transition_time_s"] is None


def test_46_ocp_exact_drift_and_detrended_noise():
    time = np.arange(0, 51, 10)
    noise = np.array([0, 1, -1, -1, 1, 0]) * 1e-5
    frame = pd.DataFrame({"time_s": time, "potential_mV": 1000 * (0.4 - 2e-5 * time + noise)})
    result = ws.ocp_stability(frame, interval_s=(0, 50), maximum_drift_mV_min=2, maximum_noise_mV=0.02)
    assert result.metrics["drift_mV_per_min"] == pytest.approx(-1.2)
    assert result.metrics["detrended_noise_sd_mV"] == pytest.approx(0.01)
    assert result.metrics["status"] == "within selected limits"
    flat = ws.ocp_stability(frame.assign(potential_mV=400.0), interval_s=(0, 50))
    assert flat.metrics["drift_mV_per_min"] == flat.metrics["detrended_noise_sd_mV"] == 0
    with pytest.raises(ValueError, match="both"):
        ws.ocp_stability(frame, interval_s=(0, 50), maximum_noise_mV=0.02)


def test_47_binary_deposition_and_efficiency_charge_balance():
    frame = pd.DataFrame({"time_s": [0, 2, 4, 6], "current_A": [-1] * 4})
    components = [("Cu", 63.546, 2, 0.75), ("Ag", 107.868, 1, 0.25)]
    result = ws.deposition(frame, interval_s=(0.5, 4.5), components=components, efficiency_percent=75)
    table = result.tables["Estimated deposit composition"]
    assert result.metrics["net_charge_C"] == -4
    assert result.metrics["estimated_deposit_mass_g"] == pytest.approx(
        (63.546 * 0.75 + 107.868 * 0.25) * 3 / (1.75 * ws.F))
    assert table.mass_fraction.sum() == pytest.approx(1)
    with pytest.raises(ValueError, match="sum"):
        ws.deposition(frame, interval_s=(0.5, 4.5), components=components[:1], efficiency_percent=75)


def test_48_faradaic_efficiency_respects_product_electron_counts_units_and_denominator():
    frame = pd.DataFrame({"time_s": [0, 1, 2, 3], "current_A": [-1, -1, 1, -1]})
    products = [("H2", 1e6 * 0.5 / (2 * ws.F), "µmol", 2, None),
                ("CO", 28.01 * 1e3 * 0.3 / (2 * ws.F), "mg", 2, 28.01)]
    result = ws.faradaic_efficiency(frame, interval_s=(0, 3), products=products, polarity="cathodic")
    assert result.metrics["reaction_charge_C"] == 1
    assert result.tables["Product efficiencies"].faradaic_efficiency_percent.tolist() == pytest.approx([50, 30])
    same = ws.faradaic_efficiency(frame, interval_s=(0, 3), products=products, polarity="cathodic",
                                  charge_basis="selected polarity")
    assert same.metrics["reaction_charge_C"] == 1.5
    with pytest.raises(ValueError, match="greater than zero"):
        ws.faradaic_efficiency(frame, interval_s=(0, 3), products=products, polarity="anodic")


def test_49_tafel_compares_named_windows_and_reports_failed_windows():
    e = np.linspace(-0.3, 0.3, 121)
    j0 = 2e-6
    i = 2 * np.sign(e) * j0 * 10 ** (np.abs(e) / 0.08)
    frame = pd.DataFrame({"potential_V": e, "current_A": i})
    result = ws.tafel_window_sensitivity(frame, branch="cathodic", windows_V=[(-0.22, -0.06), (-0.18, -0.08), (-0.4, -0.35)],
                                         area_cm2=2, equilibrium_V=0)
    table = result.tables["Windows"]
    assert table.tafel_slope_mV_dec.iloc[:2].to_list() == pytest.approx([-80, -80])
    assert table.extrapolated_j0_A_cm2.iloc[0] == pytest.approx(j0)
    assert table.status.iloc[-1] == "insufficient distinct overpotentials"
    with pytest.raises(ValueError, match="overpotential sign"):
        ws.tafel_window_sensitivity(frame, branch="cathodic", windows_V=[(0.01, 0.02), (-0.2, -0.1)], area_cm2=2, equilibrium_V=0)


def test_50_corrosion_series_matches_native_estimates_and_provenance():
    e = np.linspace(-0.36, 0.36, 211)
    e0 = 0.015
    frame = pd.DataFrame({"potential_V": e, "current_A": 2e-6 * (np.exp(26 * (e - e0)) - np.exp(-26 * (e - e0)))})
    result = ws.corrosion_series([("first", frame, None, {"source_path": "first.csv", "potential_reference": "RHE"}),
                                  ("second", frame, None, {"source_path": "second.csv", "tags": ["reference_electrode=rhe"]})],
                                 area_cm2=0.5, equivalent_weight_g_per_equiv=28, density_g_cm3=7.8)
    table = result.tables["Corrosion metrics"]
    assert table.source_filename.to_list() == ["first.csv", "second.csv"]
    assert table.ecorr_V.to_numpy() == pytest.approx([e0, e0], abs=0.002)
    assert (table.corrosion_rate_mm_per_year > 0).all()
    assert result.metrics["potential_reference"] == "RHE"
    with pytest.raises(ValueError, match="together"):
        ws.corrosion_series([("one", frame, None, {})], area_cm2=1, density_g_cm3=7)


def test_62_frequency_resolved_mott_schottky_preserves_rejected_windows():
    f = np.geomspace(1000, 1, 7)
    z = 3 + 1 / (1 / 40 + 2j * np.pi * f * 0.001)
    grid = pd.concat([pd.DataFrame({"potential_V": potential, "frequency_Hz": f, "Zreal_ohm": z.real, "Zimag_ohm": z.imag})
                      for potential in (-0.1, 0.2)], ignore_index=True)
    result = ws.mott_schottky_frequencies(grid)
    table = result.tables["Frequency diagnostics"]
    assert set(table.frequency_Hz) == set(f)
    assert table.status.eq("REJECT").all()
    assert table.flatband_potential_V.isna().all()
    with pytest.raises(ValueError, match="same frequency grid"):
        ws.mott_schottky_frequencies(grid.iloc[:-1])


def test_62_mott_recovers_candidate_intercept_and_fit_window(monkeypatch):
    from faradaiq.v317_advanced_potential_eis import CAP_COLUMNS

    bias = np.linspace(-0.15, 0.4, 17)
    frequencies = [1, 10, 100, 1000, 10000, 100000]
    flatband, slope = -0.22, 1e10
    cap_rows, grid = [], []
    for index, potential in enumerate(bias):
        capacitance = 1 / np.sqrt((potential - flatband) * slope)
        for frequency in frequencies:
            cap_rows.append((index, potential, frequency, capacitance, 0, 0, None, None, None))
            z = 4 + 1 / (1 / 300 + 2j * np.pi * frequency * capacitance)
            grid.append((potential, frequency, z.real, z.imag))
    assert len(cap_rows[0]) == len(CAP_COLUMNS)
    table = pd.DataFrame(grid, columns=["potential_V", "frequency_Hz", "Zreal_ohm", "Zimag_ohm"])
    # Isolate the workbench's frequency reporting and the desktop window kernel
    # from per-bias equivalent-circuit optimization.
    monkeypatch.setattr(ws.browser, "analyze_mott_schottky",
                        lambda *_a, **_kw: {"status": "ok", "cap_all": cap_rows, "summary": []})
    result = ws.mott_schottky_frequencies(table, frequencies_Hz=[10, 10000])
    rows = result.tables["Frequency diagnostics"]
    assert rows.frequency_Hz.tolist() == [10, 10000]
    assert rows.status.eq("PASS_CANDIDATE").all()
    assert rows.slope_F_minus2_per_V.tolist() == pytest.approx([slope, slope])
    assert rows.flatband_potential_V.tolist() == pytest.approx([flatband, flatband])
    assert result.tables["Frequency points"].fitted_inverse_C_squared_F_minus2.notna().all()
    with pytest.raises(ValueError, match="exactly"):
        ws.mott_schottky_frequencies(table, frequencies_Hz=[400])


@pytest.mark.parametrize(("sample_count", "span"), [(181, 0.8), (182, 0.8), (201, 0.8), (201, 0.18)])
def test_62_dense_bias_windows_recover_known_flatband(sample_count, span):
    from faradaiq.v317_advanced_potential_eis import _mott_diagnostic

    potential = np.linspace(0, span, sample_count)
    flatband, slope, frequency = -0.1, 1e8, 1000
    capacitance = 1 / np.sqrt(slope * (potential - flatband))
    result = _mott_diagnostic(potential, capacitance, frequency)
    assert result["status"] == "PASS_CANDIDATE"
    assert result["frequency_Hz"] == frequency
    assert result["slope_F_minus2_per_V"] == pytest.approx(slope)
    assert result["flatband_potential_V"] == pytest.approx(flatband)
    assert result["window_V_max"] - result["window_V_min"] >= 0.15


def test_63_arrhenius_celsius_to_kelvin_and_activation_energy_sign():
    k = np.array([281., 297., 311., 326.])
    y = 1e7 * np.exp(-24000 / (ws.R * k))
    result = ws.arrhenius(k - 273.15, y, unit="°C", rate_unit="s⁻¹")
    assert result.metrics["apparent_activation_energy_kJ_mol"] == pytest.approx(24)
    assert result.metrics["prefactor_in_rate_unit"] == pytest.approx(1e7)
    with pytest.raises(ValueError, match="Kelvin"):
        ws.arrhenius([-1, 10, 40], [1, 2, 3], rate_unit="A")


def test_64_levich_and_koutecky_levich_use_omega_and_material_constants():
    area, c, d, viscosity, n, kinetic = 0.23, 1.2e-6, 1.8e-5, 0.011, 4, 5e-4
    rpm = np.array([100, 225, 400, 625, 900])
    omega = 2 * np.pi * rpm / 60
    b = 0.62 * n * ws.F * area * c * d ** (2 / 3) * viscosity ** (-1 / 6)
    limit = b * np.sqrt(omega)
    actual = 1 / (1 / limit + 1 / kinetic)
    settings = dict(area_cm2=area, concentration_mol_cm3=c, diffusion_cm2_s=d,
                    kinematic_viscosity_cm2_s=viscosity, potential_V=0.16, polarity="cathodic", reference_electrons=n)

    def series(current):
        return [_record(speed, pd.DataFrame({"potential_V": [0.0, 0.1, 0.2, 0.3], "current_A": -i}))
                for speed, i in zip(rpm, current)]

    simple = ws.rotating_disk_series(series(limit), method="Levich", **settings)
    coupled = ws.rotating_disk_series(series(actual), method="Koutecky–Levich", **settings)
    assert simple.metrics["apparent_electrons"] == pytest.approx(4)
    assert coupled.metrics["apparent_electrons"] == pytest.approx(4)
    assert coupled.metrics["kinetic_current_magnitude_A"] == pytest.approx(kinetic)
    assert coupled.tables["Rotation series"].omega_rad_s.tolist() == pytest.approx(omega)
    assert ws.rotating_disk_series(series(limit), method="Koutecky–Levich", **settings).metrics["kinetic_current_magnitude_A"] is None
    with pytest.raises(ValueError, match="polarity"):
        ws.rotating_disk_series(series(-limit), method="Levich", **settings)


@pytest.mark.parametrize(("function", "parameters"), [
    (ws.scan_rate_series, {"polarity": "anodic"}),
    (ws.double_layer_capacitance, {"potential_V": 0.16}),
    (ws.randles_sevcik, {"polarity": "anodic", "area_cm2": 1, "concentration_mol_cm3": 0.01, "electrons": 1, "temperature_K": 300}),
    (ws.laviron, {"electrons": 1, "temperature_K": 300, "formal_potential_V": 0}),
    (ws.corrosion_series, {}),
    (ws.rotating_disk_series, {"method": "Levich", "area_cm2": 1, "concentration_mol_cm3": 0.01,
                               "diffusion_cm2_s": 1e-6, "kinematic_viscosity_cm2_s": 0.01, "potential_V": 0.16,
                               "polarity": "cathodic"}),
])
def test_potential_series_reject_mixed_and_unrecorded_reference_scales(function, parameters):
    metadata = ({"potential_reference": "RHE"}, {"tags": {"reference_electrode": "Ag/AgCl"}}, {})
    records = [(str(i), _cv(), i, None, identity) for i, identity in enumerate(metadata, 1)]
    if function is ws.corrosion_series:
        records = [(name, table, columns, identity) for name, table, _, columns, identity in records]
    with pytest.raises(ValueError, match="Conflicting potential references.*RHE.*Ag/AgCl"):
        function(records, **parameters)
    with pytest.raises(ValueError, match="unknown potential reference"):
        function(records[::2] + records[:1], **parameters)
    with pytest.raises(ValueError, match="Enter a reviewed potential offset"):
        function(records, common_potential_reference="RHE", **parameters)


def test_scan_rate_series_accepts_reviewed_common_scale_for_unknown_references():
    records = [(str(rate), _cv(amplitude=1e-4 * np.sqrt(rate)), rate, None) for rate in (.01, .04, .09)]
    result = ws.scan_rate_series(records, polarity="anodic", common_potential_reference="SHE")
    assert result.metrics["potential_reference"] == "SHE"
    assert result.tables["Peak observations"].peak_potential_V.tolist() == pytest.approx([0.13] * 3)


def test_reference_sources_include_single_bundle_metadata_and_selected_column(tmp_path):
    from faradaiq.browser_data import BrowserData
    from faradaiq.workbench_science_ui import _measurement_identity

    path = tmp_path / "measurement.csv"
    identity = {"metadata": {"reference_electrode": "SHE"}}
    data = BrowserData(path, tmp_path, (path,), {path: identity}, {"potential_reference": "SHE"},
                       bundle_manifest={"experiment_metadata": {"tags": ["potential_reference=she"]}})
    frame = _cv().rename(columns={"Ewe/mV": "Ewe/mV vs SHE"})
    sources = ws.potential_reference_sources(frame, None, _measurement_identity(data, path))
    assert sources == {"column": "SHE", "metadata.reference_electrode": "SHE",
                       "source_metadata.potential_reference": "SHE",
                       "bundle_manifest.experiment_metadata.tags.potential_reference": "she"}


def test_rotation_ui_reconciles_reference_tags_and_audits_applied_offsets(tmp_path):
    from streamlit.testing.v1 import AppTest
    from faradaiq.workbench_audit import read_history

    paths = [tmp_path / f"run_{i}.csv" for i in range(3)]
    biases = np.array([0.0, 0.1, 0.2, 0.3])
    speeds = [100, 225, 400]
    for path, rpm, offset in zip(paths, speeds, [0, 0.11, 0]):
        pd.DataFrame({"potential_V": biases - offset,
                      "current_A": -(1 + 2 * biases) * np.sqrt(rpm) * 1e-6}).to_csv(path, index=False)
    originals = [path.read_bytes() for path in paths]
    script = f"""
from pathlib import Path
from faradaiq.browser_data import BrowserData
from faradaiq.workbench_science_ui import render
folder = Path({str(tmp_path)!r})
paths = tuple(folder / f"run_{{i}}.csv" for i in range(3))
records = {{paths[0]: {{"handle_id": "first", "rotation_rpm": 100}},
            paths[1]: {{"tags": ["reference_electrode=Ag/AgCl"], "rotation_rpm": 225}},
            paths[2]: {{"reference_electrode": "rhe", "rotation_rpm": 400}}}}
metadata = {{"project_handles": [{{"id": "first", "metadata": {{"potential_reference": "RHE"}}}},
                                 {{"id": "unselected", "metadata": {{"potential_reference": "SHE"}}}}]}}
render(BrowserData(paths[0], folder, paths, records, metadata), workspace=folder)
"""
    app = AppTest.from_string(script).run()
    app.selectbox[0].select(64).run()
    app.multiselect[0].set_value(paths).run()
    values = {"Common potential (V)": "0.16", "Disk area (cm²)": "1",
              "Bulk concentration (mol/cm³)": "1e-6", "Diffusion coefficient (cm²/s)": "1e-5",
              "Kinematic viscosity (cm²/s)": "0.01"}
    for widget in app.text_input:
        if widget.label in values:
            widget.set_value(values[widget.label])
    app.button[0].click().run()
    assert not app.exception
    assert "Conflicting potential references" in app.error[0].value
    assert not read_history(tmp_path)

    reference = next(widget for widget in app.text_input if widget.label == "Common potential reference")
    reference.set_value("RHE").run()
    app.button[0].click().run()
    assert "Enter a reviewed potential offset" in app.error[0].value
    assert not read_history(tmp_path)

    offsets = [widget for widget in app.text_input if widget.label == "Potential offset to common reference (V)"]
    offsets[1].set_value("0.11")
    app.button[0].click().run()
    assert not app.exception and not app.error
    audit = read_history(tmp_path)
    assert len(audit) == 1
    assert audit[0]["parameters"]["resolved_potential_reference"] == "RHE"
    files = {Path(path).stem: Path(path) for path in audit[0]["outputs"]}
    methods = json.loads(files["methods"].read_text(encoding="utf-8"))
    assert methods["metrics"]["potential_reference"] == "RHE"
    assert methods["parameters"]["measurements"][0]["potential_reference_sources"] == {"project_handle.metadata.potential_reference": "RHE"}
    assert methods["parameters"]["measurements"][1]["potential_reference_sources"] == {"tags.reference_electrode": "Ag/AgCl"}
    assert [row["potential_offset_V"] for row in methods["parameters"]["measurements"]] == [0, 0.11, 0]
    achieved = pd.read_csv(files["rotation_series"])
    assert achieved.signed_current_A.tolist() == pytest.approx(-1.32 * np.sqrt(speeds) * 1e-6)
    assert [path.read_bytes() for path in paths] == originals


def test_science_ui_reaches_all_methods_persists_result_and_writes_audit(tmp_path):
    from streamlit.testing.v1 import AppTest
    from faradaiq.workbench_audit import read_history
    from faradaiq.workbench_science_ui import TOOLS

    source = tmp_path / "sample_cv.csv"
    _cv().to_csv(source, index=False)
    script = f"""
from pathlib import Path
import pandas as pd
from faradaiq.browser_data import BrowserData
from faradaiq.workbench_science_ui import render
folder = Path({str(tmp_path)!r})
source = folder / "sample_cv.csv"
data = BrowserData(source, folder, (source,), {{source: {{"instrument": "Test instrument"}}}}, {{}})
render(data, workspace=folder)
"""
    app = AppTest.from_string(script).run()
    assert not app.exception
    assert set(app.selectbox[0].options) == set(TOOLS.values())
    app.selectbox[0].select(38).run()
    assert not app.exception
    assert app.button[0].label == "Calculate"
    app.button[0].click().run()
    assert not app.exception and not app.error
    assert len(app.dataframe) == 2
    audit = read_history(tmp_path)
    assert audit[0]["parameters"]["feature"] == 38
    assert all(Path(path).is_file() for path in audit[0]["outputs"])
    app.selectbox[0].select(39).run()
    assert not app.exception
    app.selectbox[0].select(38).run()
    assert not app.exception
    assert len(app.dataframe) == 2
    assert len(read_history(tmp_path)) == 1


def test_science_ui_configures_every_tool_for_canonical_tables(tmp_path):
    from streamlit.testing.v1 import AppTest
    from faradaiq.workbench_science_ui import TOOLS

    script = f"""
from pathlib import Path
import pandas as pd
from faradaiq.workbench_science_ui import render
folder = Path({str(tmp_path)!r})
class Fixture:
    source = analysis_root = folder
    tables = tuple(folder / f"{{n}}.csv" for n in range(3))
    records = dict.fromkeys(tables, {{"instrument": "Test instrument"}})
    def read_table(self, path):
        return pd.DataFrame({{"time_s": [0, 1, 2], "potential_V": [0, 1, 2], "current_A": [1, 3, 1],
                             "frequency_Hz": [1000, 100, 10], "Zreal_ohm": [1, 2, 3], "Zimag_ohm": [-1, -2, -3]}})
data = Fixture()
render(data, workspace=data.source)
"""
    app = AppTest.from_string(script).run()
    for feature in TOOLS:
        app.selectbox[0].select(feature).run()
        if not app.button:
            app.multiselect[0].set_value([tmp_path / f"{n}.csv" for n in range(3)]).run()
        assert not app.exception and not app.error, feature
        assert app.button[0].label == "Calculate", feature
