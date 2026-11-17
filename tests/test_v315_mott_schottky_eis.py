from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from faradaiq.v314_mott_schottky_eis import _register_mott_as_eis


def _synthetic_mott():
    cols = [
        "potential_step_index", "frequency_index", "vendor_point_index", "elapsed_time_s",
        "potential_V", "frequency_Hz", "ac_amplitude_V", "E_real_V", "E_imag_V", "I_real_A",
        "I_imag_A", "I_dc_A", "Zreal_ohm", "Zimag_ohm", "minus_Zimag_ohm", "Zmod_ohm",
        "phase_deg", "minus_phase_deg", "admittance_real_S", "admittance_imag_S",
        "capacitance_parallel_F", "capacitance_series_F",
        "inverse_capacitance_parallel_squared_F_minus2", "inverse_capacitance_series_squared_F_minus2",
        "loss_tangent_abs",
    ]
    rows = []
    for step, v in enumerate([0.5, 0.4], start=1):
        for fi, f in enumerate([1e4, 1e3, 1e2, 1e1], start=1):
            zr = 10 + step + fi
            zi = -(30 + step + fi)
            zm = float(np.hypot(zr, zi))
            phase = float(np.degrees(np.arctan2(zi, zr)))
            row = [step, fi, fi, 0, v, f, 0.01, 0, 0, 0, 0, 0, zr, zi, -zi, zm, phase, -phase, 0, 0, 1e-6, 1e-6, 1e12, 1e12, 0.1]
            rows.append(row)
    return {
        "mott_schottky": [{
            "action_id": 1,
            "array": np.asarray(rows, dtype=float),
            "columns": cols,
            "n_potential_steps": 2,
            "n_frequencies": 4,
        }],
        "notes": [],
    }


def test_mott_grid_registers_each_potential_as_eis_spectrum():
    decoded = _register_mott_as_eis(_synthetic_mott())
    assert decoded["mott_schottky_eis_registered"] is True
    assert decoded["mott_schottky_eis_spectrum_count"] == 2
    assert len(decoded["eis_sweeps"]) == 2
    assert len(decoded["eis_sweep_metadata"]) == 2
    assert decoded["eis_sweeps"][0].shape == (4, 6)
    assert np.all(np.diff(decoded["eis_sweeps"][0][:, 0]) < 0)
    assert decoded["eis_sweep_metadata"][0]["dc_potential_V"] == 0.5
    assert decoded["eis_sweep_metadata"][1]["dc_potential_V"] == 0.4
    assert all(m["source"] == "MOTT_SCHOTTKY" for m in decoded["eis_sweep_metadata"])


def test_mott_registration_is_idempotent():
    decoded = _register_mott_as_eis(_synthetic_mott())
    decoded2 = _register_mott_as_eis(decoded)
    assert len(decoded2["eis_sweeps"]) == 2
    assert len(decoded2["eis_sweep_metadata"]) == 2
