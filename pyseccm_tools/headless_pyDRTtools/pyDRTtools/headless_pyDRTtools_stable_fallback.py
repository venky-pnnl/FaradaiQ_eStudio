#!/usr/bin/env python
"""FaradaIQ stable headless DRT runner.

This wrapper keeps the historical pyDRTtools command-line interface, but defaults to a
pure NumPy/SciPy ridge/NNLS DRT engine. It avoids the PyQt/cvxpy/OSQP stack that can
produce Windows access-violation exits (for example 0xC0000005 / -1073741819) in some
Python/Qt/Solver combinations.

Original pyDRTtools-based runner is preserved as headless_pyDRTtools_legacy_pyDRTtools.py
and can be invoked with --engine legacy_pyDRTtools if needed.
"""

import os
import sys
import csv
import glob
import json
import math
import argparse
import subprocess
import uuid
import hashlib
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Force a non-GUI Matplotlib backend before importing pyplot. This is important for
# headless batch runs from a Windows command window.
import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt

try:
    from scipy.optimize import nnls
except Exception as exc:  # pragma: no cover
    nnls = None
    _SCIPY_IMPORT_ERROR = exc
else:
    _SCIPY_IMPORT_ERROR = None


def _safe_name(text, fallback="DRT_run"):
    import re
    text = str(text or "").strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_.")
    return text or fallback


def _unique_run_id(user_run_id=None, settings_seed=None):
    if user_run_id:
        return _safe_name(user_run_id, fallback="DRT_run")
    now = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    digest = hashlib.sha1(str(settings_seed or uuid.uuid4().hex).encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"DRT_{now}_{digest}"


def _make_unique_dir(parent_dir, run_id):
    parent_dir = os.path.abspath(parent_dir)
    os.makedirs(parent_dir, exist_ok=True)
    base = _safe_name(run_id, fallback="DRT_run")
    out = os.path.join(parent_dir, base)
    if not os.path.exists(out):
        os.makedirs(out, exist_ok=False)
        return out, base
    for i in range(2, 10000):
        candidate = os.path.join(parent_dir, f"{base}_{i:03d}")
        if not os.path.exists(candidate):
            os.makedirs(candidate, exist_ok=False)
            return candidate, f"{base}_{i:03d}"
    raise RuntimeError(f"Could not create a unique DRT output folder under {parent_dir}")


def _write_settings(run_dir, settings):
    os.makedirs(run_dir, exist_ok=True)
    json_path = os.path.join(run_dir, "DRT_run_settings.json")
    txt_path = os.path.join(run_dir, "DRT_run_settings.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, sort_keys=True, default=str)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("FaradaIQ stable DRT run settings\n")
        f.write("=" * 72 + "\n")
        for k in sorted(settings.keys()):
            v = settings[k]
            if isinstance(v, (list, tuple)):
                v = ", ".join(str(x) for x in v)
            f.write(f"{k}: {v}\n")
    csv_path = os.path.join(run_dir, "DRT_input_files_manifest.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["index", "input_file"])
        for i, p in enumerate(settings.get("input_files", []), start=1):
            writer.writerow([i, p])
    return json_path, txt_path


def _coerce_numeric_frame(path):
    """Read common EIS CSV variants and return freq, zreal, zimag arrays."""
    # Try a normal headered file first.
    last_exc = None
    for header in (0, None):
        try:
            df = pd.read_csv(path, header=header)
        except Exception as exc:
            last_exc = exc
            continue
        if df.empty or df.shape[1] < 3:
            continue
        # Drop entirely empty columns and convert likely numeric columns.
        df = df.dropna(axis=1, how="all")
        numeric_cols = []
        for col in df.columns:
            vals = pd.to_numeric(df[col], errors="coerce")
            if vals.notna().sum() >= 3:
                numeric_cols.append((col, vals))
        if len(numeric_cols) < 3:
            continue

        def score_name(col, keywords):
            name = str(col).lower()
            return max([1 if k in name else 0 for k in keywords] or [0])

        # Identify by header names when available.
        cols = [c for c, _ in numeric_cols]
        freq_candidates = [c for c in cols if score_name(c, ["freq", "frequency", "hz", "f/"]) > 0]
        real_candidates = [c for c in cols if any(k in str(c).lower() for k in ["zreal", "z_real", "z'", "re", "real", "zprime", "z_prime"])]
        imag_candidates = [c for c in cols if any(k in str(c).lower() for k in ["zimag", "z_imag", "z''", "im", "imag", "zdouble", "z_double", "-z"])]

        selected = []
        if freq_candidates and real_candidates and imag_candidates:
            for c in [freq_candidates[0], real_candidates[0], imag_candidates[0]]:
                if c not in selected:
                    selected.append(c)
        if len(selected) < 3:
            selected = [c for c, _ in numeric_cols[:3]]

        arr = []
        for c in selected[:3]:
            arr.append(pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float))
        mat = np.vstack(arr).T
        mat = mat[np.all(np.isfinite(mat), axis=1)]
        if mat.shape[0] < 3:
            continue
        freq, zre, zim = mat[:, 0], mat[:, 1], mat[:, 2]
        good = np.isfinite(freq) & np.isfinite(zre) & np.isfinite(zim) & (freq > 0)
        freq, zre, zim = freq[good], zre[good], zim[good]
        if len(freq) < 3:
            continue
        # Sort by descending frequency for EIS convention but use arrays consistently.
        order = np.argsort(freq)[::-1]
        return freq[order], zre[order], zim[order], {"columns_used": [str(c) for c in selected[:3]], "header": header}
    if last_exc:
        raise last_exc
    raise ValueError(f"Could not identify at least three numeric EIS columns in {path}")


def _second_difference_matrix(n):
    if n < 3:
        return np.zeros((0, n), dtype=float)
    D = np.zeros((n - 2, n), dtype=float)
    for i in range(n - 2):
        D[i, i] = 1.0
        D[i, i + 1] = -2.0
        D[i, i + 2] = 1.0
    return D


def _stable_drt_fit(freq, zre, zim, regularization=1e-3, n_tau=None, data_mode="combined"):
    if nnls is None:
        raise RuntimeError(f"scipy.optimize.nnls could not be imported: {_SCIPY_IMPORT_ERROR}")
    freq = np.asarray(freq, dtype=float)
    zre = np.asarray(zre, dtype=float)
    zim = np.asarray(zim, dtype=float)
    good = np.isfinite(freq) & np.isfinite(zre) & np.isfinite(zim) & (freq > 0)
    freq, zre, zim = freq[good], zre[good], zim[good]
    if len(freq) < 3:
        raise ValueError("Need at least three finite EIS points for DRT fitting.")

    # Robust R_inf estimate from the high-frequency real-Z region.
    hf_n = max(1, min(3, len(freq) // 4))
    hf_idx = np.argsort(freq)[-hf_n:]
    r_inf = float(np.nanmedian(zre[hf_idx]))

    fmin = float(np.nanmin(freq))
    fmax = float(np.nanmax(freq))
    tau_min = 1.0 / (2.0 * np.pi * fmax) / 10.0
    tau_max = 1.0 / (2.0 * np.pi * fmin) * 10.0
    if not np.isfinite(tau_min) or not np.isfinite(tau_max) or tau_min <= 0 or tau_max <= tau_min:
        tau_min, tau_max = 1e-6, 1e3
    if n_tau is None:
        n_tau = int(max(60, min(240, len(freq) * 8)))
    tau = np.logspace(np.log10(tau_min), np.log10(tau_max), n_tau)
    omega = 2.0 * np.pi * freq
    wt = omega[:, None] * tau[None, :]
    dln_tau = float(np.mean(np.diff(np.log(tau)))) if len(tau) > 1 else 1.0
    A_re = (1.0 / (1.0 + wt ** 2)) * dln_tau
    A_im = (-wt / (1.0 + wt ** 2)) * dln_tau

    rows = []
    y = []
    mode = str(data_mode or "combined").lower()
    if mode in {"combined", "re-im", "both"}:
        rows.extend([A_re, A_im])
        y.extend([zre - r_inf, zim])
    elif mode in {"re", "real", "re data"}:
        rows.append(A_re)
        y.append(zre - r_inf)
    elif mode in {"im", "imag", "imaginary", "im data"}:
        rows.append(A_im)
        y.append(zim)
    else:
        rows.extend([A_re, A_im])
        y.extend([zre - r_inf, zim])
    A = np.vstack(rows)
    b = np.concatenate(y)

    scale = float(np.nanmedian(np.abs(b))) or 1.0
    lam = max(float(regularization or 0.0), 0.0)
    if lam > 0 and n_tau >= 3:
        D2 = _second_difference_matrix(n_tau)
        A_aug = np.vstack([A / scale, math.sqrt(lam) * D2])
        b_aug = np.concatenate([b / scale, np.zeros(D2.shape[0])])
    else:
        A_aug = A / scale
        b_aug = b / scale

    gamma, residual_norm = nnls(A_aug, b_aug, maxiter=max(3 * n_tau, 1000))
    z_fit_re = r_inf + A_re @ gamma
    z_fit_im = A_im @ gamma
    residual_re = zre - z_fit_re
    residual_im = zim - z_fit_im
    rmse_re = float(np.sqrt(np.mean(residual_re ** 2)))
    rmse_im = float(np.sqrt(np.mean(residual_im ** 2)))
    return {
        "freq": freq,
        "zre": zre,
        "zim": zim,
        "tau": tau,
        "gamma": gamma,
        "r_inf": r_inf,
        "z_fit_re": z_fit_re,
        "z_fit_im": z_fit_im,
        "residual_re": residual_re,
        "residual_im": residual_im,
        "rmse_re": rmse_re,
        "rmse_im": rmse_im,
        "regularization": lam,
        "n_tau": n_tau,
        "residual_norm": float(residual_norm),
    }


def _save_eis_plot(result, prefix):
    fig, ax = plt.subplots(figsize=(6.2, 5.0), dpi=150)
    ax.plot(result["zre"], -result["zim"], "o", ms=4, label="Data")
    ax.plot(result["z_fit_re"], -result["z_fit_im"], "-", lw=1.8, label="DRT fit")
    ax.set_xlabel("Z' (Ω)")
    ax.set_ylabel("-Z'' (Ω)")
    ax.set_title("Nyquist / DRT fit")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(f"{prefix}_eis_data.png", bbox_inches="tight")
    plt.close(fig)
    pd.DataFrame({
        "freq_Hz": result["freq"],
        "Z_real_ohm": result["zre"],
        "Z_imag_ohm": result["zim"],
        "Z_fit_real_ohm": result["z_fit_re"],
        "Z_fit_imag_ohm": result["z_fit_im"],
        "residual_real_ohm": result["residual_re"],
        "residual_imag_ohm": result["residual_im"],
    }).to_csv(f"{prefix}_eis_data.csv", index=False)


def _save_drt_plot(result, prefix):
    fig, ax = plt.subplots(figsize=(6.2, 4.6), dpi=150)
    ax.semilogx(result["tau"], result["gamma"], "-", lw=1.8)
    ax.set_xlabel("τ (s)")
    ax.set_ylabel("γ(τ) (Ω)")
    ax.set_title("Stable ridge/NNLS DRT")
    ax.grid(True, which="both", alpha=0.25)
    fig.tight_layout()
    fig.savefig(f"{prefix}_drt_data.png", bbox_inches="tight")
    plt.close(fig)
    pd.DataFrame({"tau_s": result["tau"], "gamma_ohm": result["gamma"]}).to_csv(f"{prefix}_drt_data.csv", index=False)


def _save_magnitude_plot(result, prefix):
    z = result["zre"] + 1j * result["zim"]
    zfit = result["z_fit_re"] + 1j * result["z_fit_im"]
    fig, ax = plt.subplots(figsize=(6.2, 4.6), dpi=150)
    ax.loglog(result["freq"], np.abs(z), "o", ms=4, label="Data")
    ax.loglog(result["freq"], np.abs(zfit), "-", lw=1.8, label="DRT fit")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("|Z| (Ω)")
    ax.set_title("Bode magnitude")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(f"{prefix}_magnitude.png", bbox_inches="tight")
    plt.close(fig)
    pd.DataFrame({
        "freq_Hz": result["freq"],
        "Z_abs_ohm": np.abs(z),
        "Z_fit_abs_ohm": np.abs(zfit),
    }).to_csv(f"{prefix}_magnitude.csv", index=False)


def _save_phase_plot(result, prefix):
    z = result["zre"] + 1j * result["zim"]
    zfit = result["z_fit_re"] + 1j * result["z_fit_im"]
    fig, ax = plt.subplots(figsize=(6.2, 4.6), dpi=150)
    ax.semilogx(result["freq"], np.angle(z, deg=True), "o", ms=4, label="Data")
    ax.semilogx(result["freq"], np.angle(zfit, deg=True), "-", lw=1.8, label="DRT fit")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Phase (deg)")
    ax.set_title("Bode phase")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(f"{prefix}_phase.png", bbox_inches="tight")
    plt.close(fig)
    pd.DataFrame({
        "freq_Hz": result["freq"],
        "phase_deg": np.angle(z, deg=True),
        "phase_fit_deg": np.angle(zfit, deg=True),
    }).to_csv(f"{prefix}_phase.csv", index=False)


def _export_drt_csv(result, path):
    with open(path, "w", newline="", encoding="utf-8") as save_file:
        writer = csv.writer(save_file)
        writer.writerow(["L", 0.0])
        writer.writerow(["R", result["r_inf"]])
        writer.writerow(["tau", "gamma"])
        for t, g in zip(result["tau"], result["gamma"]):
            writer.writerow([t, g])


def _export_eis_csv(result, path):
    pd.DataFrame({
        "freq": result["freq"],
        "mu_Z_re": result["z_fit_re"],
        "mu_Z_im": result["z_fit_im"],
        "Z_re_res": result["residual_re"],
        "Z_im_res": result["residual_im"],
    }).to_csv(path, index=False)


def _run_legacy_pyDRTtools(argv):
    here = Path(__file__).resolve().parent
    legacy = here / "headless_pyDRTtools_legacy_pyDRTtools.py"
    if not legacy.exists():
        raise FileNotFoundError(f"Legacy pyDRTtools runner not found: {legacy}")
    cmd = [sys.executable, str(legacy)] + argv
    print("Launching legacy pyDRTtools runner:", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(here))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Support legacy opt-in before parsing with the stable parser.
    if "--engine" in argv:
        idx = argv.index("--engine")
        if idx + 1 < len(argv) and str(argv[idx + 1]).lower() in {"legacy", "legacy_pydrttools", "pydrttools", "pyDRTtools".lower()}:
            argv2 = argv[:idx] + argv[idx + 2:]
            return _run_legacy_pyDRTtools(argv2)

    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("input_dir", type=str, help="Directory containing DRT-compatible CSV files: frequency, Z real, Z imag.")
    parser.add_argument("--plot_types", type=str, nargs="+", default=["eis"], choices=["eis", "magnitude", "phase", "re", "im", "re_residual", "im_residual", "drt", "score"], help="Plots to generate.")
    parser.add_argument("--run_types", type=str, nargs="+", default=["simple"], choices=["simple", "bayesian", "bht"], help="Accepted for compatibility. Stable engine performs a ridge/NNLS DRT for each requested type.")
    parser.add_argument("--export_types", type=str, nargs="+", default=["drt"], choices=["drt", "eis"], help="CSV exports to write.")
    parser.add_argument("--rbf_type", type=str, default="gaussian", choices=["gaussian", "c0", "c2", "c4", "c6", "inverse_quadratic", "inverse_quadric", "cauchy"], help="Accepted for compatibility; stable engine uses log-tau kernel discretization.")
    parser.add_argument("--data", type=str, choices=["re", "im", "combined"], default="combined", help="Data component used for fitting.")
    parser.add_argument("--derivative", type=int, choices=[1, 2], default=1, help="Accepted for compatibility. Stable engine uses second-difference smoothing.")
    parser.add_argument("--inductance", type=str, choices=["with", "without", "discard"], default="without", help="Accepted for compatibility. Stable engine does not fit inductance.")
    parser.add_argument("--regularization", type=float, default=1e-3, help="Tikhonov smoothing strength for stable DRT.")
    parser.add_argument("--shape_control", type=str, choices=["fwhm", "shape"], default="fwhm", help="Accepted for compatibility.")
    parser.add_argument("--coefficient", type=float, default=0.5, help="Accepted for compatibility.")
    parser.add_argument("--sample_number", type=int, default=2000, help="Accepted for compatibility.")
    parser.add_argument("--run_id", type=str, default=None, help="Optional output subfolder name.")
    parser.add_argument("--output_dir", type=str, default=None, help="Optional parent output folder. Default is <input_dir>/DRT.")
    parser.add_argument("--engine", type=str, default="stable", choices=["stable", "legacy_pyDRTtools", "legacy", "pydrttools"], help="DRT engine. Stable avoids Windows Qt/cvxpy access-violation crashes.")
    parser.add_argument("--max_files", type=int, default=0, help="Optional limit for quick testing; 0 means all files.")
    args = parser.parse_args(argv)

    if str(args.engine).lower() in {"legacy", "legacy_pydrttools", "pydrttools"}:
        argv2 = [x for x in argv]
        # Remove --engine plus value for legacy argparse.
        if "--engine" in argv2:
            idx = argv2.index("--engine")
            del argv2[idx:idx + 2]
        return _run_legacy_pyDRTtools(argv2)

    input_dir = os.path.abspath(args.input_dir)
    input_files = sorted(glob.glob(os.path.join(input_dir, "*.csv")))
    if args.max_files and args.max_files > 0:
        input_files = input_files[:args.max_files]
    if not input_files:
        raise FileNotFoundError(f"No CSV files were found in DRT input directory: {input_dir}")

    settings_seed = {
        "input_dir": input_dir,
        "engine": "stable_ridge_nnls",
        "run_types": list(args.run_types),
        "plot_types": list(args.plot_types),
        "export_types": list(args.export_types),
        "rbf_type": args.rbf_type,
        "data": args.data,
        "derivative": args.derivative,
        "inductance": args.inductance,
        "regularization": args.regularization,
        "shape_control": args.shape_control,
        "coefficient": args.coefficient,
        "sample_number": args.sample_number,
    }
    run_id = _unique_run_id(args.run_id, settings_seed=settings_seed)
    drt_parent = os.path.abspath(args.output_dir) if args.output_dir else os.path.join(input_dir, "DRT")
    run_dir, final_run_id = _make_unique_dir(drt_parent, run_id)
    settings = dict(settings_seed)
    settings.update({
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "run_id_requested": args.run_id,
        "run_id": final_run_id,
        "output_parent_dir": drt_parent,
        "output_run_dir": run_dir,
        "input_files": [os.path.abspath(p) for p in input_files],
        "python_executable": sys.executable,
        "script_path": os.path.abspath(__file__),
        "cwd": os.getcwd(),
        "note": "Stable FaradaIQ ridge/NNLS DRT engine. Use --engine legacy_pyDRTtools to try the bundled original pyDRTtools runner.",
    })
    _write_settings(run_dir, settings)
    print(f"DRT output run folder: {run_dir}")
    print(f"DRT settings saved: {os.path.join(run_dir, 'DRT_run_settings.json')}")
    print("Using FaradaIQ stable ridge/NNLS DRT engine (no PyQt/cvxpy GUI stack).")

    summary_rows = []
    errors = []
    for input_file in input_files:
        file_name = os.path.basename(input_file)
        file_prefix = os.path.splitext(file_name)[0]
        try:
            freq, zre, zim, meta = _coerce_numeric_frame(input_file)
            result = _stable_drt_fit(freq, zre, zim, regularization=args.regularization, data_mode=args.data)
            for run_type in args.run_types:
                # Keep the historical filename convention with the run type. Stable engine uses
                # the same computation for simple/bayesian/bht compatibility requests.
                output_prefix = os.path.join(run_dir, f"{file_prefix}_{run_type}")
                if "eis" in args.plot_types:
                    _save_eis_plot(result, output_prefix)
                if "drt" in args.plot_types:
                    _save_drt_plot(result, output_prefix)
                if "magnitude" in args.plot_types:
                    _save_magnitude_plot(result, output_prefix)
                if "phase" in args.plot_types:
                    _save_phase_plot(result, output_prefix)
                if "drt" in args.export_types:
                    _export_drt_csv(result, os.path.join(run_dir, f"{file_prefix}_{run_type}_DRT.csv"))
                if "eis" in args.export_types:
                    _export_eis_csv(result, os.path.join(run_dir, f"{file_prefix}_{run_type}_EIS.csv"))
                # Always write compact fit metrics for provenance.
                with open(os.path.join(run_dir, f"{file_prefix}_{run_type}_stable_DRT_fit_summary.json"), "w", encoding="utf-8") as f:
                    json.dump({
                        "input_file": os.path.abspath(input_file),
                        "engine": "stable_ridge_nnls",
                        "run_type_label": run_type,
                        "columns_used": meta.get("columns_used"),
                        "n_points": int(len(result["freq"])),
                        "n_tau": int(len(result["tau"])),
                        "r_inf_ohm": result["r_inf"],
                        "rmse_re_ohm": result["rmse_re"],
                        "rmse_im_ohm": result["rmse_im"],
                        "regularization": result["regularization"],
                        "frequency_min_Hz": float(np.min(result["freq"])),
                        "frequency_max_Hz": float(np.max(result["freq"])),
                    }, f, indent=2)
            summary_rows.append({
                "input_file": input_file,
                "status": "ok",
                "n_points": len(result["freq"]),
                "r_inf_ohm": result["r_inf"],
                "rmse_re_ohm": result["rmse_re"],
                "rmse_im_ohm": result["rmse_im"],
            })
        except Exception as exc:
            msg = f"WARNING: stable DRT failed for {file_name}: {exc}"
            print(msg)
            errors.append({"input_file": input_file, "error": str(exc)})
            summary_rows.append({"input_file": input_file, "status": "failed", "error": str(exc)})
            continue

    pd.DataFrame(summary_rows).to_csv(os.path.join(run_dir, "stable_DRT_batch_summary.csv"), index=False)
    with open(os.path.join(run_dir, "stable_DRT_batch_summary.json"), "w", encoding="utf-8") as f:
        json.dump({
            "run_id": final_run_id,
            "run_dir": run_dir,
            "n_input_files": len(input_files),
            "n_success": sum(1 for r in summary_rows if r.get("status") == "ok"),
            "n_failed": len(errors),
            "errors": errors,
        }, f, indent=2)
    print(f"Finished stable DRT run_id={final_run_id}")
    print(f"All DRT outputs saved under: {run_dir}")
    if errors:
        print(f"Completed with {len(errors)} file-level warning(s); see stable_DRT_batch_summary.json.")
    return 0 if len(errors) < len(input_files) else 2


if __name__ == "__main__":
    raise SystemExit(main())
