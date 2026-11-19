"""Circuit fitting, lin-KK batches, and discretized DRT for EIS series."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re

import numpy as np
import pandas as pd
from scipy.optimize import least_squares, linear_sum_assignment, nnls
from scipy.signal import find_peaks
from scipy.stats import t

from . import browser_analysis as analysis
from .numpy_compat import trapezoid


@dataclass(frozen=True)
class FrequencyMask:
    minimum_hz: float | None = None
    maximum_hz: float | None = None
    excluded_ranges_hz: tuple[tuple[float, float], ...] = ()
    excluded_rows: tuple[int, ...] = ()


@dataclass(frozen=True)
class Spectrum:
    name: str
    data: pd.DataFrame
    provenance: pd.DataFrame


@dataclass(frozen=True)
class Circuit:
    branches: tuple[str, ...] = ("RC",)
    warburg: bool = False

    def __post_init__(self):
        if any(branch not in {"RC", "CPE"} for branch in self.branches):
            raise ValueError("Choose RC or CPE for each parallel branch.")

    @property
    def label(self):
        return " + ".join(("Rs", *(f"(R{i} ∥ {kind})" for i, kind in enumerate(self.branches, 1)),
                           *(("W",) if self.warburg else ())))

    @property
    def parameters(self):
        names = ["Rs_ohm"]
        for i, branch in enumerate(self.branches, 1):
            names.append(f"R{i}_ohm")
            names.extend((f"C{i}_F",) if branch == "RC" else (f"Q{i}", f"alpha{i}"))
        if self.warburg:
            names.append("Warburg_sigma_ohm_s^-0.5")
        return tuple(names)

    @property
    def native(self):
        return {((), False): "R", (("RC",), False): "R-RC",
                (("CPE",), False): "R-CPE", (("RC",), True): "R-RC-W"}.get((self.branches, self.warburg))


PRESET_CIRCUITS = (Circuit(()), Circuit(("RC",)), Circuit(("CPE",)), Circuit(("RC",), True),
                   Circuit(("RC", "RC")), Circuit(("RC", "CPE")))
DRT_METHOD = (
    "Nonnegative series resistance and RC distribution on an equally spaced natural-log tau grid. "
    "The complex fit gives real and imaginary components equal absolute weight. Trapezoidal "
    "quadrature integrates gamma with respect to ln(tau). Lambda weights the squared second "
    "derivative of gamma with respect to ln(tau). The model has no inductive or separate "
    "diffusion term. Frequency coverage, regularization and the grid affect peak locations and areas."
)


@dataclass(frozen=True)
class Parameter:
    initial: float | None = None
    lower: float | None = None
    upper: float | None = None
    fixed: bool = False
    shared: bool = False


@dataclass(frozen=True)
class CircuitFit:
    score: dict
    parameters: pd.DataFrame
    residuals: pd.DataFrame
    covariance: pd.DataFrame
    correlations: pd.DataFrame
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class DRTResult:
    distribution: pd.DataFrame
    residuals: pd.DataFrame
    diagnostics: dict


def _header_unit(column, role):
    """Read an explicit simple unit at the end of a measurement header."""
    token_text = str(column).translate(analysis._NORMALIZE_UNITS).replace("Ohm", "ohm").strip()
    if role in {"zreal", "zimag"} and re.search(r"(?<![A-Za-z0-9])[cmu]?m\s*\^?\s*[+-]?\s*2(?![A-Za-z0-9])",
                                               token_text):
        raise ValueError(f"{column!r} appears area-normalized. Provide impedance in ohms.")
    units = analysis._UNITS[role]
    symbols = "|".join(map(re.escape, units))
    match = re.search(rf"(?P<separator>[^\S\n]|[_/\[(])\s*(?P<unit>{symbols})(?![A-Za-z0-9])", token_text)
    if match:
        closing = {"[": "]", "(": ")"}.get(match["separator"], "")
        prefix = token_text[:match.start()].strip()
        # With a slash separator, a preceding standalone unit or number is
        # part of a compound expression, such as "Zreal m/ohm" or "Zreal 1/ohm".
        numerator = (match["separator"] == "/" and
                     re.search(r"(?:^|[\s_\[(])(?:1|[munpck]?m|[munp]?s|[munp]?A|[mu]?V)$", prefix))
        if not numerator and token_text[match.end():].strip() == closing:
            return units[match["unit"]]
    raise ValueError(f"{column!r} needs an explicit supported {role} unit ({', '.join(units)}). "
                     "Compound units are unsupported.")


def negative_imaginary_label(column):
    compact = re.sub(r"[\s_]+", "", str(column).replace("−", "-").casefold())
    return compact.startswith(("-im", "-zi", "-z''", "minusim", "minusz", "negativeim", "negativez", "negzimag"))


def spectrum_from_table(name, table, mask=FrequencyMask(), *, frequency=None, real=None,
                        imaginary=None, negate_imaginary=False, source_rows=None):
    """Normalize declared columns and retain inclusion and exclusion for every source row."""
    for bound in (mask.minimum_hz, mask.maximum_hz):
        if bound is not None and (not np.isfinite(bound) or bound <= 0):
            raise ValueError("Frequency bounds must be finite and positive.")
    if mask.minimum_hz is not None and mask.maximum_hz is not None and mask.minimum_hz > mask.maximum_hz:
        raise ValueError("The minimum frequency exceeds the maximum frequency.")
    for low, high in mask.excluded_ranges_hz:
        if not np.isfinite([low, high]).all() or low <= 0 or low > high:
            raise ValueError("Each excluded frequency range needs positive bounds in ascending order.")
    if not table.columns.is_unique:
        raise ValueError("Select a table with unique column names.")
    roles = analysis.measurement_roles(table)
    columns = tuple(observed if observed is not None else roles.get(role)
                    for observed, role in zip((frequency, real, imaginary), ("frequency", "zreal", "zimag")))
    scales = []
    for column, role in zip(columns, ("frequency", "zreal", "zimag")):
        if column is None or column not in table:
            raise ValueError(f"Could not identify the {role} column. Select it explicitly.")
        scales.append(_header_unit(column, role))
    if negative_imaginary_label(columns[2]) and not negate_imaginary:
        raise ValueError(f"{columns[2]!r} is labeled as negative imaginary impedance. Enable the sign conversion.")
    if len(set(columns)) != len(columns):
        raise ValueError("Select a different column for each impedance quantity.")
    numeric = np.column_stack([pd.to_numeric(table[column], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
                               * scale for column, scale in zip(columns, scales)])
    valid = np.isfinite(numeric).all(axis=1) & (numeric[:, 0] > 0)
    source_rows = np.arange(1, len(table) + 1) if source_rows is None else np.asarray(source_rows, dtype=int)
    if len(source_rows) != len(table):
        raise ValueError("Source row identifiers must match the input table.")
    reasons = np.full(len(table), "invalid impedance or frequency", dtype=object)
    reasons[valid] = ""
    if mask.minimum_hz is not None:
        reasons[valid & (numeric[:, 0] < mask.minimum_hz)] = "below minimum"
    if mask.maximum_hz is not None:
        reasons[valid & (numeric[:, 0] > mask.maximum_hz)] = "above maximum"
    for index, (low, high) in enumerate(mask.excluded_ranges_hz, 1):
        reasons[(reasons == "") & (numeric[:, 0] >= low) & (numeric[:, 0] <= high)] = f"excluded range {index}"
    reasons[(reasons == "") & np.isin(source_rows, mask.excluded_rows)] = "excluded source row"
    included = reasons == ""
    provenance = pd.DataFrame({"spectrum": name, "source_row": source_rows,
                               "frequency_Hz": numeric[:, 0], "included": included, "reason": reasons})
    selected = pd.DataFrame(numeric[included], columns=analysis.EIS_COLUMNS)
    if negate_imaginary:
        selected["Zimag_ohm"] *= -1
    selected.insert(0, "source_row", source_rows[included])
    return Spectrum(str(name), selected, provenance)


def _measurements(spectrum, *, unique=False, minimum=2):
    if len(spectrum.data) < minimum:
        raise ValueError(f"{spectrum.name} needs at least {minimum} usable rows after masking.")
    f, real, imag = spectrum.data.loc[:, list(analysis.EIS_COLUMNS)].to_numpy(dtype=float).T
    if not np.isfinite([f, real, imag]).all() or np.any(f <= 0):
        raise ValueError(f"{spectrum.name} contains a nonfinite measurement or nonpositive frequency.")
    if unique and len(np.unique(f)) != len(f):
        raise ValueError(f"{spectrum.name} needs distinct frequencies. Exclude duplicate frequencies before analysis.")
    return f, real + 1j * imag


def _circuit_response(circuit, frequency, values):
    """Return complex impedance and its derivatives in transformed coordinates."""
    jw = 2j * np.pi * frequency
    response = np.full(frequency.size, values[0], dtype=complex)
    derivatives = [response.copy()]
    index = 1
    for kind in circuit.branches:
        resistance, coefficient = values[index:index + 2]
        alpha = values[index + 2] if kind == "CPE" else 1.0
        admittance = resistance * coefficient * jw ** alpha
        denominator = 1.0 + admittance
        branch = resistance / denominator
        response += branch
        derivatives.extend((branch / denominator, -branch * admittance / denominator))
        index += 2
        if kind == "CPE":
            derivatives.append(derivatives[-1] * np.log(jw))
            index += 1
    if circuit.warburg:
        warburg = values[index] / np.sqrt(jw)
        response += warburg
        derivatives.append(warburg)
    return response, np.column_stack(derivatives)


def circuit_response(circuit, frequency, parameters):
    """Evaluate a constructed circuit at positive frequencies in Hz."""
    frequency = np.asarray(frequency, dtype=float)
    if frequency.ndim != 1 or not np.isfinite(frequency).all() or np.any(frequency <= 0):
        raise ValueError("Supply finite positive frequencies in Hz.")
    values = [float(parameters[name]) for name in circuit.parameters]
    for name, value in zip(circuit.parameters, values):
        low, high = _physical_limits(name)
        if not np.isfinite(value) or value < low or value > high:
            raise ValueError(f"{name} is outside its supported physical limits.")
    return _circuit_response(circuit, frequency, values)[0]


def _physical_limits(name):
    return (0.0, 1.0) if name.startswith("alpha") else (0.0, np.inf)


def _default_parameters(circuit, frequency, impedance):
    high = np.argmax(frequency)
    rs = max(float(impedance.real[high]), 1e-6)
    spread = max(float(np.ptp(impedance.real)), rs * 0.1, 1e-6)
    characteristic = float(frequency[np.argmin(impedance.imag)])
    if np.all(impedance.imag >= 0):
        characteristic = float(np.exp(np.mean(np.log(frequency))))
    values = [rs]
    branch_count = len(circuit.branches)
    centers = ([characteristic] if branch_count == 1 else
               np.geomspace(np.min(frequency), np.max(frequency), max(branch_count, 1) + 2)[1:-1][::-1])
    for branch, center in zip(circuit.branches, centers):
        resistance = spread / branch_count
        values.append(resistance)
        if branch == "RC":
            values.append(1 / (2 * np.pi * center * resistance))
        else:
            values.extend((1 / ((2 * np.pi * center) ** 0.85 * resistance), 0.85))
    if circuit.warburg:
        values.append(max(float(-impedance.imag[np.argmin(frequency)]) * np.sqrt(4 * np.pi * min(frequency)), 1e-6))
    return np.asarray(values, dtype=float)


def _native_seed(circuit, spectrum, fallback):
    if circuit.native is None or len(spectrum.data) < 6:
        return fallback
    result = analysis.fit_circuit(spectrum.data, circuit.native, source=spectrum.name)
    names = {"R": ("Rs_ohm",), "R-RC": ("Rs_ohm", "Rct_ohm", "C_F"),
             "R-CPE": ("Rs_ohm", "Rct_ohm", "Q", "alpha"),
             "R-RC-W": ("Rs_ohm", "Rct_ohm", "C_F", "Warburg_sigma")}[circuit.native]
    values = np.array([result.parameters[name] for name in names]) if result.success else fallback
    return values if np.isfinite(values).all() and np.all(values > 1e-18) else fallback


def _parameter_values(name, spec, seed):
    physical_low, physical_high = _physical_limits(name)
    low = physical_low if spec.lower is None else float(spec.lower)
    high = physical_high if spec.upper is None else float(spec.upper)
    if np.isnan([low, high]).any() or not physical_low <= low < high <= physical_high:
        raise ValueError(f"{name} has invalid bounds. Use increasing bounds within its physical range.")
    if spec.fixed and spec.initial is None:
        raise ValueError(f"Enter a value for fixed parameter {name}.")
    guess = float(spec.initial) if spec.initial is not None else float(np.clip(seed, low, high))
    if not np.isfinite(guess) or not low <= guess <= high:
        raise ValueError(f"{name} needs a finite initial value inside its bounds.")
    if not name.startswith("alpha") and not spec.fixed:
        if high <= 0 or (guess <= 0 and spec.initial is not None):
            raise ValueError(f"{name} must start above zero when it is fitted.")
        guess = max(guess, np.nextafter(0.0, 1.0))
    if name.startswith(("C", "Q")) and spec.fixed and guess == 0:
        raise ValueError(f"Use a positive fixed value for {name}.")
    if name.startswith("R") and name != "Rs_ohm" and spec.fixed and guess == 0:
        raise ValueError(f"Use a positive fixed value for {name}.")
    return guess, low, high


def _stack(complex_values):
    return np.concatenate((complex_values.real, complex_values.imag), axis=0)


def _residual_table(spectrum, frequency, measured, fitted):
    residual = fitted - measured
    return pd.DataFrame({"spectrum": spectrum.name, "source_row": spectrum.data["source_row"].to_numpy(),
                         "frequency_Hz": frequency, "Zreal_measured_ohm": measured.real,
                         "Zimag_measured_ohm": measured.imag, "Zreal_fit_ohm": fitted.real,
                         "Zimag_fit_ohm": fitted.imag, "residual_real_ohm": residual.real,
                         "residual_imag_ohm": residual.imag, "residual_magnitude_ohm": abs(residual)})


def _diagnostics(jacobian, residual, active, confidence, factors):
    observations, count = jacobian.shape
    warnings = []
    covariance = np.full((count, count), np.nan)
    correlation = covariance.copy()
    errors = np.full(count, np.nan)
    if not count:
        return 0, np.nan, covariance, correlation, errors, 0.0, warnings
    _, singular, vectors = np.linalg.svd(jacobian, full_matrices=False)
    limit = singular[0] * 1e-8
    rank = int(np.count_nonzero(singular > limit))
    condition = float(singular[0] / singular[-1]) if singular[-1] else np.inf
    if rank < count:
        warnings.append(f"Jacobian rank {rank} of {count}. Individual uncertainty intervals are unavailable.")
    if np.any(active):
        warnings.append("At least one parameter is at a bound. Interior covariance and intervals are unavailable.")
    critical = float(t.ppf((1 + confidence) / 2, observations - count))
    if not warnings:
        physical_vectors = factors[:, None] * vectors.T
        covariance = ((physical_vectors / singular**2) @ physical_vectors.T
                      * np.dot(residual, residual) / (observations - count))
        errors = np.sqrt(np.maximum(0.0, covariance.diagonal()))
        denominator = np.outer(errors, errors)
        np.divide(covariance, denominator, out=correlation, where=denominator > 0)
        if np.any(np.triu(np.abs(correlation) > 0.95, k=1)):
            warnings.append("A parameter pair has absolute correlation above 0.95.")
    return rank, condition, covariance, correlation, errors, critical, warnings


def fit_series(spectra, circuit, parameters=None, *, confidence=0.95):
    """Fit independent and shared circuit coefficients in one least-squares problem.

    Use one spectrum for an independent fit. Fit residual components in ohms under
    a single unknown error variance, with equal absolute weighting.
    """
    spectra = tuple(spectra)
    if not spectra or len({s.name for s in spectra}) != len(spectra):
        raise ValueError("Select one or more spectra with distinct identifiers.")
    if not 0 < confidence < 1:
        raise ValueError("Confidence must be between zero and one.")
    specs = parameters or {}
    names = circuit.parameters
    if set(specs) - set(names):
        raise ValueError("Parameter settings contain a name outside the selected circuit.")
    specs = [specs.get(name, Parameter()) for name in names]
    measured = [_measurements(spectrum) for spectrum in spectra]
    local_seeds = np.array([_default_parameters(circuit, frequency, impedance) for frequency, impedance in measured])
    if all(spec.initial is None for spec in specs):
        local_seeds = np.array([_native_seed(circuit, spectrum, seed) for spectrum, seed in zip(spectra, local_seeds)])
    shared_seeds = np.median(local_seeds, axis=0)
    start = []
    lower = []
    upper = []
    labels = []
    is_log = []
    groups = {}
    locations = np.full((len(spectra), len(names)), -1, dtype=int)
    fixed_values = np.zeros(locations.shape, dtype=float)
    for spec_index, spectrum in enumerate(spectra):
        for name_index, (name, spec) in enumerate(zip(names, specs)):
            scope = "shared" if spec.shared else spectrum.name
            seed = shared_seeds[name_index] if spec.shared else local_seeds[spec_index, name_index]
            value, low, high = _parameter_values(name, spec, seed)
            if spec.fixed:
                fixed_values[spec_index, name_index] = value
                continue
            group = (None if spec.shared else spec_index, name)
            if group not in groups:
                groups[group] = len(start)
                logarithmic = not name.startswith("alpha")
                is_log.append(logarithmic)
                start.append(np.log(value) if logarithmic else value)
                lower.append(np.log(low) if logarithmic and low else -np.inf if logarithmic else low)
                upper.append(np.log(high) if logarithmic else high)
                labels.append(f"shared / {name}" if spec.shared else f"local / {scope} / {name}")
            locations[spec_index, name_index] = groups[group]

    observation_count = 2 * sum(len(frequency) for frequency, _ in measured)
    variable_count = len(start)
    if observation_count <= variable_count:
        raise ValueError("There are too few retained complex observations for the number of free parameters.")
    log_indices = np.asarray(is_log, dtype=bool)
    varied = locations >= 0
    scale = max(float(np.median(np.concatenate([abs(z) for _, z in measured]))), 1e-12)

    def unpack(x):
        original = np.array(x, dtype=float)
        original[log_indices] = np.exp(original[log_indices])
        values = fixed_values.copy()
        values[varied] = original[locations[varied]]
        return values, original

    def evaluate(x, *, jacobian=False):
        values, _ = unpack(x)
        output = []
        for index, ((frequency, impedance), p) in enumerate(zip(measured, values)):
            predicted, derivatives = _circuit_response(circuit, frequency, p)
            if jacobian:
                jac = np.zeros((frequency.size, variable_count), dtype=complex)
                jac[:, locations[index, varied[index]]] = derivatives[:, varied[index]]
                output.append(_stack(jac) / scale)
            else:
                output.append(_stack(predicted - impedance) / scale)
        return np.concatenate(output, axis=0)

    if variable_count:
        fit = least_squares(evaluate, start, jac=lambda x: evaluate(x, jacobian=True), bounds=(lower, upper),
                            x_scale="jac", max_nfev=3000)
        if not fit.success:
            raise ValueError(f"Circuit optimization failed: {fit.message}")
        values, original = unpack(fit.x)
        factors = np.ones(variable_count)
        factors[log_indices] = original[log_indices]
        rank, condition, covariance, correlation, errors, critical, warnings = _diagnostics(
            fit.jac, fit.fun, fit.active_mask, confidence, factors)
    else:
        values = fixed_values
        rank, condition, covariance, correlation, errors, critical, warnings = _diagnostics(
            np.empty((observation_count, 0)), np.empty(observation_count), [], confidence, [])
    residual_tables = []
    results = []
    identifiable = rank == variable_count
    for index, (spectrum, (frequency, impedance), local) in enumerate(zip(spectra, measured, values)):
        predicted, _ = _circuit_response(circuit, frequency, local)
        residual_tables.append(_residual_table(spectrum, frequency, impedance, predicted))
        for name_index, (name, spec, value) in enumerate(zip(names, specs, local)):
            j = locations[index, name_index]
            error = np.nan if spec.fixed else errors[j]
            reportable = spec.fixed or identifiable
            parameter_status = "fixed" if spec.fixed else ("estimated" if identifiable else "non-identifiable")
            results.append({"spectrum": spectrum.name, "parameter": name,
                            "value": value if reportable else np.nan,
                            "diagnostic_value": np.nan if reportable else value, "parameter_status": parameter_status,
                            "shared": spec.shared, "fixed": spec.fixed, "standard_error": error,
                            "confidence_low": value - critical * error, "confidence_high": value + critical * error,
                            "uncertainty_label": "" if spec.fixed else labels[j]})
    residuals = pd.concat(residual_tables, ignore_index=True)
    rss = float(np.square(residuals["residual_magnitude_ohm"]).sum())
    likelihood_count = variable_count + 1  # Includes estimated residual variance.
    n = observation_count
    # A machine-precision floor keeps criteria finite for exactly reproduced data.
    variance_floor = (np.finfo(float).eps * scale) ** 2
    deviance = n * np.log(max(rss / n, variance_floor))
    aic = float(deviance + 2 * likelihood_count)
    aicc = float(aic + 2 * likelihood_count * (likelihood_count + 1) / (n - likelihood_count - 1)
                 ) if n > likelihood_count + 1 else np.nan
    if not np.isfinite(aicc):
        warnings.append("There are too few observations for the AICc correction.")
    if len(set(circuit.branches)) < len(circuit.branches):
        warnings.append("Identical branch types can exchange labels. Parameter estimates refer to this local solution.")
    score = {"circuit": circuit.label, "observations": n, "free_parameters": variable_count,
             "residual_degrees_of_freedom": n - variable_count,
             "rss_ohm2": rss, "rmse_ohm": float(np.sqrt(rss / n)), "aic": aic, "aicc": aicc,
             "bic": float(deviance + likelihood_count * np.log(n)), "jacobian_rank": rank,
             "jacobian_condition": condition, "identifiable": identifiable,
             "status": ("non-identifiable" if not identifiable else "warning" if warnings else "ok")}
    return CircuitFit(score, pd.DataFrame(results), residuals,
                      pd.DataFrame(covariance, index=labels, columns=labels),
                      pd.DataFrame(correlation, index=labels, columns=labels), tuple(warnings))


def compare_models(spectra, circuits, *, global_fit=False, settings=None, confidence=0.95):
    """Retain each candidate's diagnostics or error and rank within comparable observations."""
    spectra, circuits = tuple(spectra), tuple(dict.fromkeys(circuits))
    if not spectra or not circuits:
        raise ValueError("Select at least one spectrum and one circuit.")
    results = []
    scores = []
    groups = [(0, "Global", spectra)] if global_fit else [
        (i, spectrum.name, (spectrum,)) for i, spectrum in enumerate(spectra)]
    for group_id, group, members in groups:
        for circuit in circuits:
            try:
                result = fit_series(members, circuit, (settings or {}).get(circuit), confidence=confidence)
            except (ValueError, RuntimeError, np.linalg.LinAlgError) as error:
                scores.append({"group": group, "circuit": circuit.label, "error": str(error), "status": "error",
                               "identifiable": False, "aicc": np.nan, "bic": np.nan, "group_id": group_id})
                continue
            scores.append({"group": group, **result.score, "warning": " | ".join(result.warnings),
                           "fit_id": len(results) + 1,
                           "group_id": group_id})
            results.append((group, circuit.label, result))
    scores = pd.DataFrame(scores)
    for criterion in ("aicc", "bic"):
        eligible = scores[criterion].where(scores["identifiable"])
        grouped = eligible.groupby(scores["group_id"])
        scores[f"delta_{criterion}"] = eligible - grouped.transform("min")
        scores[f"rank_{criterion}"] = grouped.rank(method="min").astype("Int64")
    return scores.sort_values(["group_id", "rank_aicc", "rank_bic"], kind="stable").drop(columns="group_id"), results


def batch_lin_kk(spectra, *, errors=None):
    """Run the existing optional lin-KK backend one spectrum at a time."""
    rows = [{"spectrum": name, "status": "error", "error": str(error)} for name, error in (errors or {}).items()]
    for spectrum in spectra:
        try:
            frequencies, _ = _measurements(spectrum, minimum=0)
            distinct = len(np.unique(frequencies))
            if distinct < 3:
                raise ValueError("lin-KK requires at least three distinct valid frequencies after masking.")
            # M RC amplitudes, a series resistance, and a series capacitive term.
            max_m = min(100, 2 * distinct - 4)
            result = analysis.validate_impedance(spectrum.data, max_m=max_m)
            # Older impedance.py builds embed NumPy scalar reprs in the circuit
            # expression. Preserve that backend's numerical path on NumPy 2.
            if ("name 'np' is not defined" in result.get("warning", "")
                    and np.lib.NumpyVersion(np.__version__) >= "2.0.0"):
                with np.printoptions(legacy="1.25"):
                    result = analysis.validate_impedance(spectrum.data, max_m=max_m)
            available = result.get("available")
            rows.append({"spectrum": spectrum.name, "distinct_frequencies": distinct, "max_M": max_m, **result,
                         "residual_degrees_of_freedom": 2 * distinct - result["M"] - 2 if available else None,
                         "status": "ok" if available else "unavailable",
                         "error": "" if available else result.get("warning", "lin-KK unavailable.")})
        except Exception as error:
            rows.append({"spectrum": spectrum.name, "status": "error", "error": str(error)})
    return pd.DataFrame(rows)


def drt_grid(spectra, *, points=61, padding_decades=0.5):
    """Build a common logarithmic relaxation-time grid across spectra."""
    if not 5 <= points <= 250 or not np.isfinite(padding_decades) or not 0 <= padding_decades <= 4:
        raise ValueError("Choose 5 to 250 grid points and 0 to 4 padding decades.")
    frequencies = [_measurements(spectrum, unique=True, minimum=6)[0] for spectrum in spectra]
    if not frequencies:
        raise ValueError("Select one or more usable EIS spectra.")
    low = -np.log10(2 * np.pi * max(np.max(f) for f in frequencies)) - padding_decades
    high = -np.log10(2 * np.pi * min(np.min(f) for f in frequencies)) + padding_decades
    return np.logspace(low, high, num=points)


def _tau_grid(tau):
    tau = np.asarray(tau, dtype=float)
    if tau.ndim != 1 or tau.size < 5 or not np.isfinite(tau).all() or np.any(tau <= 0):
        raise ValueError("Supply at least five finite, positive relaxation times.")
    steps = np.diff(np.log(tau))
    if steps[0] <= 0 or not np.allclose(steps, steps[0], rtol=1e-7, atol=0):
        raise ValueError("Relaxation times must increase at equal intervals of ln(tau).")
    weights = np.full(tau.size, steps[0])
    weights[[0, -1]] /= 2
    return tau, steps[0], weights


def invert_drt(spectrum, tau_seconds, regularization):
    """Solve the nonnegative, curvature-regularized complex RC inverse problem."""
    if not np.isfinite(regularization) or regularization <= 0:
        raise ValueError("DRT lambda must be finite and strictly positive.")
    frequency, impedance = _measurements(spectrum, unique=True, minimum=6)
    tau, step, weights = _tau_grid(tau_seconds)
    response = weights / (1 + 2j * np.pi * frequency[:, None] * tau)
    response = np.column_stack((np.ones(frequency.size), response))
    curvature = np.diff(np.eye(tau.size), n=2, axis=0) / step**1.5
    penalty = np.column_stack((np.zeros(tau.size - 2), curvature))
    design = np.vstack((_stack(response), np.sqrt(regularization) * penalty))
    target = np.concatenate((_stack(impedance), np.zeros(penalty.shape[0])))
    values, _ = nnls(design, target, maxiter=30 * design.shape[1])
    distribution = pd.DataFrame({"spectrum": spectrum.name, "lambda": regularization,
                                 "tau_seconds": tau, "gamma_ohm": values[1:]})
    residuals = _residual_table(spectrum, frequency, impedance, response @ values)
    residual_norm = float(np.linalg.norm(residuals.residual_magnitude_ohm))
    diagnostics = {"spectrum": spectrum.name, "lambda": regularization, "Rs_ohm": float(values[0]),
                   "residual_norm_ohm": residual_norm, "rmse_ohm": residual_norm / np.sqrt(2 * frequency.size),
                   "curvature_norm_ohm": float(np.linalg.norm(penalty @ values)),
                   "integral_dln_tau_ohm": float(weights @ values[1:]),
                   "positive_imaginary_rows": int(np.count_nonzero(impedance.imag > 0))}
    return DRTResult(distribution, residuals, diagnostics)


def explore_drt(spectra, lambdas, *, points=61, padding_decades=0.5):
    """Invert a series on a common tau grid with an independent result per lambda."""
    lambdas = tuple(dict.fromkeys(lambdas))
    if not lambdas or not np.isfinite(lambdas).all() or min(lambdas) <= 0:
        raise ValueError("Enter one or more finite positive DRT lambda values.")
    eligible = []
    errors = []
    for spectrum in spectra:
        try:
            _measurements(spectrum, unique=True, minimum=6)
            eligible.append(spectrum)
        except ValueError as error:
            errors.append({"spectrum": spectrum.name, "error": str(error)})
    if not eligible:
        return [], pd.DataFrame(errors)
    tau = drt_grid(eligible, points=points, padding_decades=padding_decades)
    results = []
    for spectrum in eligible:
        for value in lambdas:
            try:
                results.append(invert_drt(spectrum, tau, value))
            except (ValueError, RuntimeError, np.linalg.LinAlgError) as error:
                errors.append({"spectrum": spectrum.name, "lambda": value, "error": str(error)})
    return results, pd.DataFrame(errors)


def drt_peaks(distribution, *, prominence_fraction=0.05):
    """Find interior peaks and integrate basins divided at intervening minima."""
    if not 0 < prominence_fraction <= 1:
        raise ValueError("Peak prominence fraction must be above zero and at most one.")
    tau, _, _ = _tau_grid(distribution["tau_seconds"])
    gamma = distribution["gamma_ohm"].to_numpy(dtype=float)
    if not np.isfinite(gamma).all() or np.any(gamma < 0):
        raise ValueError("Peak tracking requires finite, nonnegative gamma values.")
    peaks, properties = find_peaks(gamma, prominence=prominence_fraction * gamma.max())
    edges = [0, *(i + np.argmin(gamma[i:j + 1]) for i, j in zip(peaks, peaks[1:])), len(gamma) - 1]
    log_tau = np.log(tau)
    cols = ["tau_seconds", "gamma_ohm", "prominence_ohm", "lower_tau_seconds", "upper_tau_seconds",
            "area_dln_tau_ohm"]
    rows = [(tau[peak], gamma[peak], prominence, tau[low], tau[high],
             trapezoid(gamma[low:high + 1], log_tau[low:high + 1]))
            for peak, prominence, low, high in zip(peaks, properties["prominences"], edges, edges[1:])]
    return pd.DataFrame(rows, columns=cols)


def track_drt_peaks(distributions, *, series_order=None, conditions=None, prominence_fraction=0.05,
                    max_shift_decades=0.5):
    """Link peaks between adjacent series members by one-to-one log-tau proximity."""
    if not np.isfinite(max_shift_decades) or max_shift_decades <= 0:
        raise ValueError("The maximum tracking shift must be finite and positive.")
    selected = list(distributions) if series_order is None else list(series_order)
    if len(selected) != len(set(selected)) or set(distributions) - set(selected):
        raise ValueError("Peak tracking needs a distinct series position for every spectrum.")
    selection_steps = {name: step for step, name in enumerate(selected, 1)}
    if conditions is not None:
        if any(name not in conditions or not np.isfinite(conditions[name]) for name in selected):
            raise ValueError("Peak tracking needs a finite condition for each selected spectrum, including failed acquisitions.")
        ordered = sorted(selected, key=lambda name: conditions[name])
    else:
        ordered = selected
    next_track = 1
    prior_times, prior_tracks = np.array([]), np.array([], dtype=int)
    frames = []
    for step, name in enumerate(ordered, 1):
        distribution = distributions.get(name)
        peaks = (drt_peaks(distribution, prominence_fraction=prominence_fraction)
                 if distribution is not None else pd.DataFrame())
        log_time = np.log10(peaks.tau_seconds.to_numpy()) if len(peaks) else np.array([])
        shifts = np.abs(prior_times[:, None] - log_time[None, :])
        tracks = np.zeros(len(peaks), dtype=int)
        if shifts.size:
            # Out-of-gate links get a cost above any all-valid assignment.
            threshold = max_shift_decades * (sum(shifts.shape) + 1)
            left, right = linear_sum_assignment(np.where(shifts <= max_shift_decades, shifts, threshold))
            valid = shifts[left, right] <= max_shift_decades
            tracks[right[valid]] = prior_tracks[left[valid]]
        new = tracks == 0
        tracks[new] = np.arange(next_track, next_track + np.count_nonzero(new))
        next_track += np.count_nonzero(new)
        markers = peaks if len(peaks) else pd.DataFrame(index=[0])
        markers.insert(0, "spectrum", name)
        markers.insert(1, "series_step", step)
        markers.insert(2, "selection_step", selection_steps[name])
        markers.insert(3, "track", tracks if len(tracks) else np.nan)
        markers.insert(4, "tracking_status", "missing distribution" if distribution is None else
                       "tracked" if len(peaks) else "no peaks")
        if conditions is not None:
            markers["condition"] = conditions[name]
        frames.append(markers)
        prior_times, prior_tracks = log_time, tracks
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


CONDITION_AXES = {
    "Potential (V)": ("potential_V", {"V": 1.0, "mV": 1e-3, "uV": 1e-6},
                      ("set_potential", "bias", "dc_potential", "potential", "ewe", "voltage")),
    "Time (s)": ("time_s", {"s": 1.0, "ms": 1e-3, "us": 1e-6, "min": 60.0, "h": 3600.0},
                 ("condition_time", "hold_time", "aging_time", "elapsed_time", "elapsed", "time", "t")),
    "Concentration (mol/L)": ("concentration_mol_L", {"M": 1.0, "mM": 1e-3, "uM": 1e-6, "nM": 1e-9,
                                                       "mol/L": 1.0, "mmol/L": 1e-3, "umol/L": 1e-6,
                                                       "nmol/L": 1e-9},
                              ("concentration", "molarity", "c")),
}


@dataclass(frozen=True)
class Condition:
    value: float
    source: str
    basis: str | None = None


_REFERENCE_FIELDS = {
    "Potential (V)": ("potential_reference", "reference_electrode"),
    "Time (s)": ("time_reference", "time_origin"),
}


def _basis(value):
    if value is None:
        return None
    label = re.sub(r"\s+", " ", str(value).strip())
    return re.sub(r"\s*/\s*", "/", label) or None


def _basis_key(basis):
    return basis.casefold() if basis else None


def _condition_columns(columns, axis):
    _, units, aliases = CONDITION_AXES[axis]
    names = "|".join(re.escape(alias).replace("_", r"[\s_]+") for alias in aliases)
    symbols = "|".join(re.escape(unit).replace("/", "[/_]") for unit in sorted(units, key=len, reverse=True))
    pattern = rf"^<?(?i:{names})>?(?P<separator>[\s_/\[(<]+)(?P<unit>{symbols})(?P<suffix>.*)$"
    qualifier = {"Potential (V)": r"(?:vs\.?[\s_]*|(?:versus|relative[\s_]+to)[\s_]+)(.+)",
                 "Time (s)": r"(?:since|from|relative[\s_]+to)[\s_]+(.+)"}.get(axis)
    for column in columns:
        label = str(column).translate(analysis._NORMALIZE_UNITS).strip()
        match = re.fullmatch(pattern, label)
        if not match:
            continue
        reference = None
        suffix = match["suffix"].strip()
        opener = match["separator"].rstrip()[-1:]
        closing = {"[": "]", "(": ")", "<": ">"}.get(opener)
        if closing and suffix.startswith(closing):
            suffix = suffix[1:]
        elif closing and suffix.endswith(closing):
            suffix = suffix[:-1]
        suffix = suffix.strip(" _")
        if suffix:
            basis_match = qualifier and re.fullmatch(qualifier, suffix, re.IGNORECASE)
            if not basis_match:
                continue
            reference = _basis(basis_match[1].replace("_", " "))
        yield column, units[match["unit"].replace("_", "/")], reference


def _valid_condition(axis, value):
    return np.isfinite(value) and (axis != "Concentration (mol/L)" or value >= 0)


def _condition_records(record):
    return tuple(item for item in (record, record.get("metadata"), record.get("tags")) if isinstance(item, Mapping))


def _condition_basis(axis, reference, records):
    recorded = (_basis(record.get(key)) for record in records for key in _REFERENCE_FIELDS.get(axis, ()))
    bases = {_basis_key(label): label for label in (reference, *recorded) if label is not None}
    if len(bases) > 1:
        raise ValueError(f"Conflicting reference bases for {axis} ({', '.join(bases.values())}). "
                         "Verify the source metadata or supply a value in a common reference basis.")
    return next(iter(bases.values()), None)


def common_condition_basis(conditions):
    """Check that numeric coordinates for an axis share a recorded reference basis."""
    bases = {_basis_key(condition.basis) for condition in conditions}
    if len(bases) > 1:
        raise ValueError("Selected conditions use mixed reference bases. Convert values to a common basis before comparison.")


def recorded_condition(table, record, axis, *, supplied=None, supplied_basis=None):
    """Use an explicit value or a unit-bearing scalar recorded for the spectrum."""
    if supplied is not None:
        value = float(supplied)
        if not _valid_condition(axis, value):
            raise ValueError(f"{axis} requires a finite value within its physical range.")
        return Condition(value, "supplied", _basis(supplied_basis))
    records = _condition_records(record)
    candidates = []
    for metadata in records:
        for column, scale, basis in _condition_columns(metadata, axis):
            try:
                value = float(metadata[column]) * scale
            except (TypeError, ValueError):
                continue
            if _valid_condition(axis, value):
                candidates.append(Condition(value, f"record: {column}", _condition_basis(axis, basis, records)))
    for column, scale, basis in _condition_columns(table.columns, axis):
        values = pd.to_numeric(table[column], errors="coerce").to_numpy(dtype=float, na_value=np.nan) * scale
        if not len(values) or not np.isfinite(values).all():
            continue
        if np.allclose(values, values[0], rtol=1e-9, atol=1e-12) and _valid_condition(axis, values[0]):
            candidates.append(Condition(float(values[0]), f"constant column: {column}",
                                        _condition_basis(axis, basis, records)))
    if not candidates:
        raise ValueError(f"No unambiguous recorded {axis} value. Supply the spectrum condition explicitly.")
    first = candidates[0]
    if any(_basis_key(item.basis) != _basis_key(first.basis)
           or not np.isclose(item.value, first.value, rtol=1e-9, atol=1e-12) for item in candidates[1:]):
        sources = ", ".join(f"{item.source} ({item.value:g}{', ' + item.basis if item.basis else ''})"
                            for item in candidates)
        raise ValueError(f"Conflicting recorded {axis} values or reference bases in {sources}. "
                         "Supply the spectrum condition explicitly.")
    return first


def condition_step_rows(table, axis, column=None, *, record=None):
    """Group a stepped table by an explicit unit-bearing condition column."""
    entries = list(_condition_columns(table.columns, axis))
    if column is not None:
        entries = [entry for entry in entries if entry[0] == column]
    if not entries:
        raise ValueError(f"Select a table with an explicit {axis} column for step grouping.")
    records = _condition_records(record or {})
    series = []
    for name, scale, reference in entries:
        values = pd.to_numeric(table[name], errors="coerce").to_numpy(dtype=float, na_value=np.nan) * scale
        if not len(values) or any(not _valid_condition(axis, value) for value in values):
            raise ValueError(f"{name} contains missing or invalid conditions.")
        series.append((name, values, _condition_basis(axis, reference, records)))
    column, values, basis = series[0]
    if any(_basis_key(other_basis) != _basis_key(basis) or not np.allclose(other, values, rtol=1e-9, atol=1e-12)
           for _, other, other_basis in series[1:]):
        raise ValueError(f"Conflicting recorded {axis} step columns. Select a single verified condition column.")
    # Group contiguous steps so an identical condition encountered later is a separate acquisition.
    boundaries = np.r_[0, np.flatnonzero(~np.isclose(values[:-1], values[1:], rtol=1e-9, atol=1e-12)) + 1,
                       len(values)]
    return [(Condition(float(values[start]), f"step column: {column}", basis), np.arange(start, stop))
            for start, stop in zip(boundaries[:-1], boundaries[1:])]


def condition_maps(spectra, conditions, axis, *, distributions=()):
    """Create measured EIS and optional DRT maps with recorded sample coordinates."""
    coordinate = CONDITION_AXES[axis][0]
    spectra = tuple(spectra)
    if not spectra:
        raise ValueError("Select one or more spectra for the condition map.")
    names = {s.name for s in spectra}
    missing = names - set(conditions)
    if missing:
        raise ValueError(f"Supply {axis} for: {', '.join(sorted(missing))}.")
    common_condition_basis(conditions[name] for name in names)
    rows = []
    for spectrum in spectra:
        condition = conditions[spectrum.name]
        if not _valid_condition(axis, condition.value):
            raise ValueError(f"{spectrum.name} has an invalid {axis} condition.")
        frequency, z = _measurements(spectrum)
        rows.append(pd.DataFrame({"spectrum": spectrum.name, coordinate: condition.value,
                                  "condition_source": condition.source, "condition_basis": condition.basis,
                                  "source_row": spectrum.data["source_row"],
                                  "frequency_Hz": frequency, "Zreal_ohm": z.real, "Zimag_ohm": z.imag,
                                  "Z_magnitude_ohm": abs(z), "phase_deg": np.angle(z, deg=True)}))
    drt_rows = []
    for result in distributions:
        name = result.diagnostics["spectrum"]
        if name in names:
            condition = conditions[name]
            drt_rows.append(result.distribution.assign(**{coordinate: condition.value,
                                                           "condition_source": condition.source,
                                                           "condition_basis": condition.basis}))
    eis_map = pd.concat(rows, ignore_index=True).sort_values(coordinate, kind="stable")
    drt_map = (pd.concat(drt_rows, ignore_index=True).sort_values(coordinate, kind="stable")
               if drt_rows else pd.DataFrame())
    return eis_map, drt_map
