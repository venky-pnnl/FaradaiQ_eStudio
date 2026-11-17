from __future__ import annotations

import numpy as np

from faradaiq.numpy_compat import trapezoid
from faradaiq import v290_electrochemistry_routing as route


def test_trapezoid_uses_numpy_1x_trapz_fallback(monkeypatch):
    """The managed runtime pins NumPy 1.26.4, which has trapz but no trapezoid."""
    monkeypatch.delattr(np, "trapezoid", raising=False)
    x = np.array([0.0, 1.0, 2.0])
    y = np.array([0.0, 1.0, 2.0])
    assert float(trapezoid(y, x=x)) == 2.0


def test_cv_deterministic_route_completes_without_numpy_trapezoid(monkeypatch):
    monkeypatch.delattr(np, "trapezoid", raising=False)
    e_forward = np.linspace(-0.5, 0.5, 101)
    e_reverse = np.linspace(0.5, -0.5, 101)[1:]
    potential = np.concatenate([e_forward, e_reverse])
    current = 2.0e-5 * potential
    result = route.analyze_electrochemical_record({
        "technique": "CV",
        "columns": ["potential_V", "current_A"],
        "array": np.column_stack([potential, current]),
        "identity": {"point_index": 1, "cycle": 1},
        "record_json": {},
        "source_path": "cv.csv",
        "dataset_path": None,
    }, dummy=False, linked_eis_geis=False)[0]
    assert result["status"] == "complete"
    assert result["route"] == "deterministic_electrochemical_cv_analysis"
    assert result["curve_fitting_agent_used"] is False
    assert result["spectral_peak_fitting_used"] is False
    assert result["metrics"]["sweep_count"] >= 2


def test_manual_fallback_works_when_both_numpy_names_are_absent(monkeypatch):
    monkeypatch.delattr(np, "trapezoid", raising=False)
    monkeypatch.delattr(np, "trapz", raising=False)
    x = np.array([0.0, 1.0, 3.0])
    y = np.array([0.0, 2.0, 2.0])
    assert float(trapezoid(y, x=x)) == 5.0
