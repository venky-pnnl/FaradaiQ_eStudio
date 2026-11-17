#!/usr/bin/env python3
"""Reference deterministic CV analyzer for FaradaIQ/SciLink.

This is not a generic curve-fitting implementation. It segments each CV by
sweep direction and extracts direct electrochemical metrics.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd


def _fill_zero_signs(values: np.ndarray) -> np.ndarray:
    out = values.astype(float).copy()
    last = 0.0
    for index in range(len(out)):
        if out[index] != 0:
            last = out[index]
        elif last != 0:
            out[index] = last
    last = 0.0
    for index in range(len(out) - 1, -1, -1):
        if out[index] != 0:
            last = out[index]
        elif last != 0:
            out[index] = last
    return out


def _mad(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    median = np.nanmedian(values)
    return float(1.4826 * np.nanmedian(np.abs(values - median)))


def _metadata_from_name(name: str) -> dict:
    match = re.search(
        r"point_(\d+)_scanrate_(\d+(?:\.\d+)?)mVs_cycle_(\d+)",
        name,
        flags=re.IGNORECASE,
    )
    if not match:
        return {"point": None, "scan_rate_mV_s": None, "cycle": None}
    return {
        "point": int(match.group(1)),
        "scan_rate_mV_s": float(match.group(2)),
        "cycle": int(match.group(3)),
    }


def analyze_file(path: Path) -> dict:
    frame = pd.read_csv(path)
    required = {"potential_V", "current_A"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{path.name}: missing required columns {missing}")

    potential = frame["potential_V"].to_numpy(dtype=float)
    current = frame["current_A"].to_numpy(dtype=float)
    valid = np.isfinite(potential) & np.isfinite(current)
    potential = potential[valid]
    current = current[valid]

    if len(potential) < 5:
        raise ValueError(f"{path.name}: too few valid points")

    signs = _fill_zero_signs(np.sign(np.diff(potential)))
    changes = (np.where(signs[1:] != signs[:-1])[0] + 1).tolist()
    bounds = [0] + changes + [len(potential)]

    meta = _metadata_from_name(path.name)
    segments = []

    for segment_index, (start, stop) in enumerate(
        zip(bounds[:-1], bounds[1:]), start=1
    ):
        e = potential[start:stop]
        i = current[start:stop]
        if len(e) < 5:
            continue

        direction = "positive-going" if e[-1] > e[0] else "negative-going"
        margin = max(1, int(round(0.01 * len(e))))
        inner_stop = len(e) - margin
        if inner_stop <= margin:
            margin = 0
            inner_stop = len(e)

        e_inner = e[margin:inner_stop]
        i_inner = i[margin:inner_stop]
        max_index = int(np.argmax(i_inner))
        min_index = int(np.argmin(i_inner))

        baseline_count = max(10, int(round(0.10 * len(e))))
        baseline_count = min(baseline_count, len(e))
        coefficients = np.polyfit(e[:baseline_count], i[:baseline_count], 1)
        baseline = np.polyval(coefficients, e)
        residual = i - baseline
        sigma = _mad(residual[:baseline_count])
        if not np.isfinite(sigma) or sigma <= 0:
            sigma = max(_mad(np.diff(i)), np.finfo(float).eps)

        signed_residual = residual if direction == "positive-going" else -residual
        onset_index = None
        threshold = 5.0 * sigma
        consecutive = 5
        for index in range(baseline_count, max(baseline_count, len(e) - consecutive)):
            if np.all(signed_residual[index:index + consecutive] > threshold):
                onset_index = index
                break

        charge = None
        scan_rate = meta["scan_rate_mV_s"]
        if scan_rate is not None and scan_rate > 0:
            scan_rate_V_s = scan_rate / 1000.0
            delta_t = np.abs(np.diff(e)) / scan_rate_V_s
            charge = float(np.sum(0.5 * (i[:-1] + i[1:]) * delta_t))

        segments.append(
            {
                "segment": segment_index,
                "direction": direction,
                "rows": int(len(e)),
                "E_start_V": float(e[0]),
                "E_end_V": float(e[-1]),
                "I_max_A": float(i_inner[max_index]),
                "E_at_I_max_V": float(e_inner[max_index]),
                "I_min_A": float(i_inner[min_index]),
                "E_at_I_min_V": float(e_inner[min_index]),
                "onset_V": None if onset_index is None else float(e[onset_index]),
                "onset_method": (
                    "first five consecutive points beyond initial linear "
                    "baseline by five robust standard deviations"
                ),
                "scan_rate_derived_charge_C": charge,
            }
        )

    return {
        "file": path.name,
        **meta,
        "rows": int(len(potential)),
        "sweep_count": len(segments),
        "segments": segments,
        "generic_curve_fitting_used": False,
        "spectral_peak_fitting_used": False,
    }


def analyze_directory(folder: Path) -> dict:
    files = sorted(folder.glob("*.csv"))
    results = [analyze_file(path) for path in files]

    scan_rates = sorted(
        {result["scan_rate_mV_s"] for result in results
         if result["scan_rate_mV_s"] is not None}
    )
    cycles_by_rate = {
        str(rate): sorted(
            result["cycle"] for result in results
            if result["scan_rate_mV_s"] == rate and result["cycle"] is not None
        )
        for rate in scan_rates
    }

    return {
        "schema": "faradaiq.deterministic_cv_analysis.v1",
        "route": "deterministic_electrochemical_cv_analysis",
        "generic_curve_fitting_used": False,
        "spectral_peak_fitting_used": False,
        "files_analyzed": len(results),
        "scan_rates_mV_s": scan_rates,
        "cycles_by_scan_rate": cycles_by_rate,
        "curve_metrics": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--output", type=Path, default=Path("deterministic_cv_analysis.json"))
    args = parser.parse_args()

    result = analyze_directory(args.folder)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
