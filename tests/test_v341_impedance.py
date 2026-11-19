"""Complex EIS and DRT workbench numerical and browser coverage."""
from hashlib import sha256
from importlib.util import find_spec
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from faradaiq import browser_analysis
from faradaiq import workbench_impedance as w
from faradaiq.browser_data import BrowserData
from faradaiq.workbench_impedance_ui import _run


def _table(circuit, values, *, frequency=None, noise=0.0, seed=2):
    f = np.geomspace(0.1, 2e4, 81) if frequency is None else frequency
    angular_frequency = 2 * np.pi * f
    values = iter(values)
    z = np.full(len(f), next(values), dtype=complex)
    for branch in circuit.branches:
        conductance = 1 / next(values)
        if branch == "RC":
            z += 1 / (conductance + 1j * angular_frequency * next(values))
        else:
            coefficient, alpha = next(values), next(values)
            z += 1 / (conductance + coefficient * angular_frequency**alpha * np.exp(.5j * np.pi * alpha))
    if circuit.warburg:
        z += next(values) * (1 - 1j) / np.sqrt(2 * angular_frequency)
    rng = np.random.default_rng(seed)
    z += noise * (rng.normal(size=len(f)) + 1j * rng.normal(size=len(f)))
    return pd.DataFrame({"frequency_Hz": f, "Zreal_ohm": z.real, "Zimag_ohm": z.imag})


def _spectrum(name, circuit, values, **options):
    return w.spectrum_from_table(name, _table(circuit, values, **options))


def test_mask_normalizes_units_and_preserves_raw_source_rows():
    raw = pd.DataFrame({"freq/kHz": [.001, .003, .004, .005, .008, .015, .015, .020, np.nan],
                        "Re(Z)/kohm": [.005] * 9, "-Im(Z)/kohm": [.002] * 9})
    raw.index = [0, 1, 1, 2, 3, 4, 4, 5, 6]
    original = raw.copy(deep=True)
    with pytest.raises(ValueError, match="sign conversion"):
        w.spectrum_from_table("sample", raw)
    mask = w.FrequencyMask(3, 16, ((4, 7),), (6,))
    result = w.spectrum_from_table("sample", raw, mask, negate_imaginary=True)
    assert result.data.source_row.tolist() == [2, 5, 7]
    assert result.data.frequency_Hz.tolist() == [3, 8, 15]
    assert result.data.Zimag_ohm.tolist() == [-2, -2, -2]
    assert list(result.provenance.reason) == ["below minimum", "", "excluded range 1", "excluded range 1",
                                             "", "excluded source row", "", "above maximum",
                                             "invalid impedance or frequency"]
    pd.testing.assert_frame_equal(original, raw)
    with pytest.raises(ValueError, match="area-normalized"):
        w.spectrum_from_table("sample", raw.rename(columns={"Re(Z)/kohm": "Re(Z)/Ω cm²"}),
                              real="Re(Z)/Ω cm²", negate_imaginary=True)


@pytest.mark.parametrize("header", ["Zreal_ohm cm^-2", "Zreal_ohm/m", "Zreal [Ω·s]",
                                    "Zreal (1/ohm)", "Zreal Ohm/Ohm", "Zreal 1/ohm", "Zreal m/ohm"])
def test_impedance_rejects_compound_units(header):
    raw = pd.DataFrame({"frequency_Hz": [10., 100.], header: [10., 5.], "Zimag_ohm": [-3., -1.]})
    with pytest.raises(ValueError, match="area-normalized|Compound units"):
        w.spectrum_from_table("compound", raw, real=header)


@pytest.mark.parametrize(
    ("circuit", "true_values", "initial_values"),
    [(w.Circuit(("RC",)), (4., 61., .0017), (3.8, 55., .002)),
     (w.Circuit(("CPE",)), (3., 46., .0007, .83), (3.3, 39., .0008, .85)),
     (w.Circuit(("RC", "CPE"), True), (5., 41., 1e-4, 82., .0014, .76, 3.),
      (4., 35., 1.25e-4, 75., .0016, .7, 4.))],
)
def test_recovers_constructed_circuit_and_covariance(circuit, true_values, initial_values):
    raw = _table(circuit, true_values)
    values = dict(zip(circuit.parameters, true_values))
    predicted = w.circuit_response(circuit, raw.frequency_Hz, values)
    np.testing.assert_allclose(predicted, raw.Zreal_ohm + 1j * raw.Zimag_ohm)
    sample = _spectrum("synthetic", circuit, true_values, noise=.005)
    settings = {name: w.Parameter(initial=value) for name, value in zip(circuit.parameters, initial_values)}
    fit = w.fit_series([sample], circuit, settings)
    assert fit.score["jacobian_rank"] == len(circuit.parameters)
    assert fit.score["rmse_ohm"] < .007
    np.testing.assert_allclose(fit.parameters.value, true_values, rtol=.012)
    assert (fit.parameters.confidence_high >= fit.parameters.value).all()
    assert (fit.parameters.confidence_low <= fit.parameters.value).all()
    np.testing.assert_allclose(fit.covariance, fit.covariance.T)
    np.testing.assert_allclose(np.diag(fit.correlations), 1)
    assert fit.parameters.standard_error.gt(0).all()


def test_global_fit_shares_parameter_and_respects_fixed_values_and_bounds():
    circuit = w.Circuit()
    samples = [_spectrum("a", circuit, (4., 35., .002), noise=.01),
               _spectrum("b", circuit, (4., 72., .002), noise=.01, seed=20)]
    settings = {"Rs_ohm": w.Parameter(initial=3.5, lower=2., upper=5., shared=True),
                "R1_ohm": w.Parameter(lower=1., upper=100.),
                "C1_F": w.Parameter(initial=.002, fixed=True, shared=True)}
    fit = w.fit_series(samples, circuit, settings)
    assert fit.score["free_parameters"] == 3
    rs = fit.parameters.query("parameter == 'Rs_ohm'")
    assert rs.value.nunique() == 1
    assert rs.value.iloc[0] == pytest.approx(4, abs=.01)
    assert rs.uncertainty_label.nunique() == 1
    assert fit.parameters.query("parameter == 'C1_F'").standard_error.isna().all()
    np.testing.assert_allclose(fit.parameters.query("parameter == 'R1_ohm'").value, [35, 72], atol=.01)
    assert fit.covariance.shape == (3, 3)
    assert set(fit.residuals.spectrum) == {"a", "b"}
    with pytest.raises(ValueError, match="bounds"):
        w.fit_series(samples, circuit, {"R1_ohm": w.Parameter(initial=10, lower=20)})


def test_rank_deficiency_withholds_intervals_and_model_scores_rank_on_same_data():
    circuit = w.Circuit(("RC", "RC"))
    equal = (4., 30., .001, 30., .001)
    sample = _spectrum("sample", circuit, equal)
    fit = w.fit_series([sample], circuit, {name: w.Parameter(initial=value)
                                           for name, value in zip(circuit.parameters, equal)})
    assert fit.score["jacobian_rank"] < fit.score["free_parameters"]
    assert any("rank" in warning for warning in fit.warnings)
    assert fit.parameters.standard_error.isna().all()
    assert fit.covariance.isna().all().all()
    rc = w.Circuit()
    noisy = _spectrum("sample", rc, (4., 30., .001), noise=.02)
    scores, results = w.compare_models([noisy], [w.Circuit(()), rc, w.Circuit(("CPE",))])
    assert scores.iloc[0].circuit == rc.label
    assert scores.iloc[0].delta_aicc == 0
    assert scores.iloc[0].delta_bic == 0
    assert scores.observations.nunique() == 1
    assert len(results) == 3
    selected = scores.iloc[0]
    n = selected.observations
    k = selected.free_parameters + 1
    assert selected.aicc == pytest.approx(n * np.log(selected.rss_ohm2 / n) + 2*k + 2*k*(k+1)/(n-k-1))


def test_repeated_frequency_circuits_are_diagnostic_and_cannot_outrank_identifiable_model():
    raw = pd.DataFrame({"frequency_Hz": [100.] * 6, "Zreal_ohm": [10.] * 6, "Zimag_ohm": [-3.] * 6})
    spectrum = w.spectrum_from_table("replicates", raw)
    rs, rc, cpe = w.Circuit(()), w.Circuit(), w.Circuit(("CPE",))
    scores, fits = w.compare_models([spectrum], [rc, cpe, rs])
    rows = scores.set_index("circuit")
    assert scores.iloc[0]["circuit"] == rs.label
    assert rows.loc[rs.label, ["rank_aicc", "rank_bic"]].tolist() == [1, 1]
    for circuit, free in ((rc, 3), (cpe, 4)):
        row = rows.loc[circuit.label]
        assert row.status == "non-identifiable"
        assert (row.jacobian_rank, row.free_parameters) == (2, free)
        assert row.rss_ohm2 < rows.loc[rs.label, "rss_ohm2"]
        assert row[["rank_aicc", "rank_bic", "delta_aicc", "delta_bic"]].isna().all()
        fit = next(result for _, label, result in fits if label == circuit.label)
        assert fit.parameters.parameter_status.eq("non-identifiable").all()
        assert fit.parameters.value.isna().all()
        assert fit.parameters.diagnostic_value.notna().all()


def test_inadequate_frequency_data_and_active_parameter_bounds_have_diagnostics():
    circuit = w.Circuit()
    raw = _table(circuit, (5., 41., .001))
    sample = w.spectrum_from_table("bounded", raw)
    result = w.fit_series([sample], circuit, {"Rs_ohm": w.Parameter(initial=3.5, upper=4.)})
    assert result.parameters.iloc[0].value == pytest.approx(4.)
    assert any("bound" in warning for warning in result.warnings)
    assert result.parameters.standard_error.isna().all()
    inadequate = w.spectrum_from_table("inadequate", raw.iloc[:1])
    with pytest.raises(ValueError, match="at least 2 usable"):
        w.fit_series([inadequate], circuit)


def test_batch_lin_kk_retains_backend_failures_and_preparation_errors(monkeypatch):
    sample = _spectrum("good", w.Circuit(), (3., 10., .01))
    duplicate = _spectrum("exception", w.Circuit(), (42., 10., .01))
    unavailable = _spectrum("unavailable", w.Circuit(), (10., 10., .01))

    def validate(frame, *, max_m):
        assert max_m == 100
        high_frequency_resistance = frame.Zreal_ohm.iloc[-1]
        if high_frequency_resistance > 20:
            raise RuntimeError("singular fit")
        if high_frequency_resistance > 5:
            return {"available": False, "warning": "optional solver missing"}
        return {"available": True, "M": 3, "rmse": .001}

    monkeypatch.setattr(browser_analysis, "validate_impedance", validate)
    result = w.batch_lin_kk([sample, duplicate, unavailable], errors={"unreadable": "unknown units"})
    rows = result.set_index("spectrum")
    assert rows.loc["good", "M"] == 3
    assert rows.loc["exception", "error"] == "singular fit"
    assert rows.loc["unavailable", "error"] == "optional solver missing"
    assert rows.loc["unreadable", "error"] == "unknown units"


def test_lin_kk_rejects_two_distinct_frequencies_and_limits_native_model_size(monkeypatch):
    raw = pd.DataFrame({"frequency_Hz": [10., 100., 1_000.], "Zreal_ohm": [10., 5., 3.],
                        "Zimag_ohm": [-3., -1., -.1]})
    calls = []

    def native(frequency, impedance, *, max_m=100):
        calls.append((len(frequency), max_m))
        return {"available": True, "M": max_m, "rmse": .001}

    monkeypatch.setattr(browser_analysis.intelligence, "_lin_kk", native)
    two = w.spectrum_from_table("two", raw.iloc[:2])
    duplicates = w.spectrum_from_table("duplicates", pd.concat([raw.iloc[:2]] * 3))
    three = w.spectrum_from_table("three", raw)
    results = w.batch_lin_kk([two, duplicates, three]).set_index("spectrum")
    assert calls == [(3, 2)]
    assert results.loc[["two", "duplicates"], "status"].eq("error").all()
    assert results.loc[["two", "duplicates"], "error"].str.contains("three distinct").all()
    assert results.loc["three", ["status", "M", "max_M", "residual_degrees_of_freedom"]].tolist() == ["ok", 2, 2, 2]


def test_drt_inverse_lambda_tradeoff_integration_tracking_and_errors():
    circuit = w.Circuit()
    samples = [_spectrum("earlier", circuit, (3., 45., .001)),
               _spectrum("later", circuit, (3., 46., .0014))]
    duplicate = _spectrum("duplicate", circuit, (3., 45., .001),
                          frequency=np.r_[np.geomspace(.2, 2e4, 64), .2])
    low, high = 1e-5, 1e-2
    results, errors = w.explore_drt([*samples, duplicate], [low, high], points=61)
    assert errors.spectrum.tolist() == ["duplicate"]
    assert len(results) == 4
    assert results[0].diagnostics["rmse_ohm"] < results[1].diagnostics["rmse_ohm"]
    assert results[0].diagnostics["curvature_norm_ohm"] > results[1].diagnostics["curvature_norm_ohm"]
    assert results[0].diagnostics["integral_dln_tau_ohm"] == pytest.approx(45., rel=.025)
    assert results[0].diagnostics["Rs_ohm"] == pytest.approx(3., abs=.15)
    assert results[0].distribution.gamma_ohm.ge(0).all()
    partitions = w.drt_peaks(results[0].distribution)
    assert len(partitions) == 1
    assert partitions.tau_seconds.iloc[0] == pytest.approx(.045, rel=.25)
    assert partitions.area_dln_tau_ohm.sum() == pytest.approx(results[0].diagnostics["integral_dln_tau_ohm"])
    distributions = {item.diagnostics["spectrum"]: item.distribution for item in results[::-1]
                     if item.diagnostics["lambda"] == low}
    peaks = w.track_drt_peaks(distributions, conditions={"earlier": .2, "later": .3})
    assert peaks.spectrum.tolist() == ["earlier", "later"]
    assert peaks.track.tolist() == [1, 1]
    assert peaks.tau_seconds.iloc[1] > peaks.tau_seconds.iloc[0]


@pytest.mark.parametrize(
    ("axis", "header", "recorded", "normalized", "coordinate"),
    [("Potential (V)", "set_potential_mV", 100, .1, "potential_V"),
     ("Time (s)", "aging_time_min", 2, 120, "time_s"),
     ("Concentration (mol/L)", "concentration_mM", 8.2, .0082, "concentration_mol_L")],
)
def test_condition_maps_use_recorded_or_supplied_values(axis, header, recorded, normalized, coordinate):
    circuit = w.Circuit()
    raw = _table(circuit, (3., 45., .001))
    samples = [w.spectrum_from_table("A", raw), w.spectrum_from_table("B", raw)]
    conditions = {
        "A": w.recorded_condition(raw, {header: recorded}, axis),
        "B": w.recorded_condition(raw.assign(**{header: recorded}), {}, axis, supplied=3.),
    }
    results, _ = w.explore_drt(samples, [1e-4], points=41)
    eis, drt = w.condition_maps(samples, conditions, axis, distributions=results)
    assert eis[coordinate].unique() == pytest.approx(sorted([normalized, 3.]))
    assert drt[coordinate].unique() == pytest.approx(sorted([normalized, 3.]))
    assert set(eis.condition_source) == {f"record: {header}", "supplied"}
    assert eis.Z_magnitude_ohm.iloc[0] == pytest.approx(np.hypot(raw.Zreal_ohm.iloc[0], raw.Zimag_ohm.iloc[0]))
    with pytest.raises(ValueError, match="Supply"):
        w.condition_maps(samples, {"A": conditions["A"]}, axis)


def test_consecutive_condition_steps_preserve_original_row_positions():
    circuit = w.Circuit()
    raw = _table(circuit, (3., 45., .001)).iloc[:8]
    series = pd.concat([raw.assign(potential_V=0.1), raw.assign(potential_V=0.3), raw.assign(potential_V=0.1)])
    groups = w.condition_step_rows(series, "Potential (V)")
    assert [step.value for step, _ in groups] == [0.1, 0.3, 0.1]
    condition, positions = groups[1]
    result = w.spectrum_from_table("step", series.iloc[positions], source_rows=positions + 1)
    assert condition.source == "step column: potential_V"
    assert result.provenance.source_row.tolist() == list(range(9, 17))


def test_conditions_reject_conflicting_values_and_reference_bases():
    axis = "Potential (V)"
    raw = _table(w.Circuit(), (3., 45., .001))
    with pytest.raises(ValueError, match="Conflicting recorded Potential"):
        w.recorded_condition(raw, {"potential_V": .1, "metadata": {"set_potential_mV": 300}}, axis)
    with pytest.raises(ValueError, match="Conflicting recorded Potential"):
        w.recorded_condition(raw.assign(potential_V=.2), {"set_potential_mV": 300}, axis)
    with pytest.raises(ValueError, match="Conflicting reference bases"):
        w.recorded_condition(raw.assign(**{"Potential (V vs SHE)": .2}),
                             {"reference_electrode": "Ag/AgCl"}, axis)

    a = raw.assign(**{"Potential (VvsSHE)": .2})
    b = raw.assign(**{"Potential (mV vs Ag/AgCl (3 M KCl))": 200.})
    sample_a, sample_b = w.spectrum_from_table("a", a), w.spectrum_from_table("b", b)
    a_condition = w.recorded_condition(a, {}, axis)
    b_condition = w.recorded_condition(b, {"tags": {"potential_reference": "Ag/AgCl (3 M KCl)"}}, axis)
    assert (a_condition.basis, b_condition.basis) == ("SHE", "Ag/AgCl (3 M KCl)")
    assert b_condition.value == pytest.approx(a_condition.value)
    for other in (b_condition, w.Condition(.2, "unknown")):
        with pytest.raises(ValueError, match="mixed reference bases"):
            w.condition_maps([sample_a, sample_b], {"a": a_condition, "b": other}, axis)
    converted = w.recorded_condition(b, {}, axis, supplied=.41, supplied_basis="SHE")
    estimates, _ = w.explore_drt([sample_a, sample_b], [1e-4])
    eis, drt = w.condition_maps([sample_a, sample_b], {"a": a_condition, "b": converted},
                                axis, distributions=estimates)
    assert set(eis.condition_basis) == set(drt.condition_basis) == {"SHE"}
    assert eis.potential_V.unique().tolist() == [.2, .41]
    assert w.condition_step_rows(b, axis)[0][0].basis == b_condition.basis
    with pytest.raises(ValueError, match="Conflicting recorded Potential"):
        w.condition_step_rows(b.assign(**{"Potential (V vs SHE)": .2}), axis)


@pytest.mark.parametrize("condition_order", [False, True])
def test_drt_run_keeps_failed_acquisitions_and_breaks_peak_tracks(tmp_path, condition_order):
    raw = _table(w.Circuit(), (3., 45., .001))
    first, failed, last = (tmp_path / f"{name}.csv" for name in ("first", "failed", "last"))
    paths = (first, failed, last)
    raw.assign(potential_V=.1).to_csv(first, index=False)
    pd.concat([raw, raw.iloc[:1]]).assign(potential_V=.2).to_csv(failed, index=False)
    raw.assign(potential_V=.3).to_csv(last, index=False)
    data = BrowserData(tmp_path, tmp_path, paths, {path: {} for path in paths}, {})
    inputs = {"excluded_ranges": "", "minimum_hz": None, "maximum_hz": None,
              "columns": {str(path): {"excluded_rows": ""} for path in paths}}
    settings = {"lambdas": (1e-4,), "points": 61, "padding_decades": .5, "prominence_fraction": .05,
                "max_shift_decades": .5, "condition_order": condition_order}
    conditions = {"axis": "Potential (V)", "split": False, "supplied_conditions": {}} if condition_order else None
    folder = _run(data, tmp_path, paths, "drt", inputs, settings, conditions)
    tracks = pd.read_csv(folder / "drt_peak_tracks.csv")
    assert tracks.spectrum.tolist() == [path.name for path in paths]
    assert tracks.series_step.tolist() == tracks.selection_step.tolist() == [1, 2, 3]
    assert tracks.tracking_status.tolist() == ["tracked", "missing distribution", "tracked"]
    assert tracks.track.dropna().tolist() == [1, 2]
    assert pd.isna(tracks.tau_seconds.iloc[1])
    assert pd.read_csv(folder / "drt_errors.csv").spectrum.tolist() == [failed.name]


def _widget(widgets, label, index=0):
    return [widget for widget in widgets if widget.label == label][index]


def test_impedance_actions_write_auditable_results_and_survive_rerun(tmp_path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    first, second = input_dir / "EIS alpha.csv", input_dir / "EIS beta.csv"
    c = w.Circuit()
    _table(c, (2., 40., .003)).assign(potential_V=.1).to_csv(first, index=False)
    _table(c, (2., 55., .002)).assign(potential_V=.3).to_csv(second, index=False)
    app = AppTest.from_string(f"""
from pathlib import Path
import pandas as pd
import streamlit as st
from faradaiq.browser_data import BrowserData
from faradaiq.workbench_impedance_ui import render
paths = (Path({str(first)!r}), Path({str(second)!r}))
records = {{p: {{'technique': 'EIS', 'tags': ['batch=7'], 'original_table_path': f'native/{{p.name}}'}}
           for p in paths}}
data = BrowserData(paths[0].parent, paths[0].parent, paths, records, {{}})
if not st.checkbox('Leave workbench'):
    render(data, workspace=Path({str(tmp_path)!r}))
""", default_timeout=30).run()
    assert not app.exception
    _widget(app.button, "Add CPE branch").click().run()
    assert _widget(app.selectbox, "Parallel branch 2").value == "CPE"
    _widget(app.button, "Remove", 1).click().run()
    assert len([w for w in app.selectbox if "Parallel branch" in w.label]) == 1
    _widget(app.multiselect, "Impedance spectra").set_value([str(first), str(second)])
    _widget(app.multiselect, "Additional models to compare").set_value([w.Circuit(()).label])
    _widget(app.number_input, "Minimum frequency (Hz)").set_value(.25)
    _widget(app.radio, "Fit scope").set_value("Global fit")
    _widget(app.checkbox, "Shared").check()
    _widget(app.button, "Run circuit fits").click().run()
    assert not app.exception
    assert not app.error
    prefix = "w340_eis_" + sha256(str(input_dir).encode()).hexdigest()[:12] + "_"
    result_key = prefix + "fit_latest_circuits"
    folder = Path(app.session_state[result_key])
    receipt = json.loads((folder / "settings.json").read_text())
    assert receipt["input_settings"]["minimum_hz"] == .25
    assert receipt["analysis_settings"]["global_fit"] is True
    assert receipt["analysis_settings"]["models"][0]["parameters"]["Rs_ohm"]["shared"] is True
    assert all(spectrum["tags"] == ["batch=7"] for spectrum in receipt["spectra"])
    assert [spectrum["original_table_path"] for spectrum in receipt["spectra"]] == [
        f"native/{first.name}", f"native/{second.name}"]
    assert len(pd.read_csv(folder / "model_scores.csv")) == 2
    assert set(pd.read_csv(folder / "parameters.csv").spectrum) == {first.name, second.name}
    assert (~pd.read_csv(folder / "frequency_mask.csv").included).any()
    _widget(app.checkbox, "Leave workbench").check().run()
    _widget(app.checkbox, "Leave workbench").uncheck().run()
    assert _widget(app.number_input, "Minimum frequency (Hz)").value == .25
    assert Path(app.session_state[result_key]) == folder
    _widget(app.text_input, "DRT lambdas").set_value("0.0001, 0.001")
    _widget(app.button, "Run DRT explorer").click().run()
    assert not app.exception
    assert not app.error
    _widget(app.checkbox, "Include a DRT condition map").check().run()
    _widget(app.button, "Run condition maps").click().run()
    assert not app.exception
    assert not app.error
    _widget(app.button, "Run lin-KK batch").click().run()
    assert not app.exception
    assert not app.error
    history_path = tmp_path / "Workbench" / "analysis_history.jsonl"
    history = [json.loads(line) for line in history_path.read_text().splitlines()]
    assert [row["action"] for row in history] == [
        "Fit complex circuit models", "Explore DRT regularization", "Build impedance condition maps", "Run lin-KK batch"]
    for operation, scope, result in (("drt", "drt_", "drt_peak_tracks.csv"),
                                     ("maps", "maps_", "drt_condition_map.csv"),
                                     ("lin_kk", "kk_", "lin_kk.csv")):
        saved = Path(app.session_state[prefix + scope + "latest_" + operation])
        assert (saved / "settings.json").is_file()
        assert (saved / result).is_file()
    kk = pd.read_csv(Path(app.session_state[prefix + "kk_latest_lin_kk"]) / "lin_kk.csv")
    assert set(kk.status) == ({"ok"} if find_spec("impedance") is not None else {"unavailable"})
    if set(kk.status) == {"ok"}:
        assert kk.rmse.lt(.05).all()
    assert first.is_file() and second.is_file()
