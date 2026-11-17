"""FaradaIQ Studio v313 Mott-Schottky and capacitance regression support."""
from __future__ import annotations

import csv
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, MutableMapping, Sequence

import numpy as np

VERSION = "v313"
DEFAULT_OUTPUT_DIR = r"C:\Data\ANALYSIS\Faradaiq"

_RECORD_BYTES = 92
_HEADER_BYTES = 8

# VersaStudio Mott-Schottky binary layout inferred from paired .par/.par.psbd files.
# Big-endian 32-bit words. Real-valued electrochemical fields are stored as f32,
# while several indexes/status values are i32. The Z fields are validated by
# Z = (E_real + i E_imag)/(I_real + i I_imag).
_FIELD_OFFSETS = {
    "elapsed_us_i32": 8,
    "point_index_i32": 12,
    "step_status_i32": 20,
    "potential_V_f32": 24,
    "e_real_V_f32": 28,
    "e_imag_V_f32": 32,
    "z_real_ohm_f32": 36,
    "z_imag_ohm_f32": 40,
    "frequency_Hz_f32": 64,
    "i_dc_A_f32": 68,
    "i_real_A_f32": 72,
    "i_imag_A_f32": 76,
    "ac_amplitude_V_f32": 80,
}


def _to_float(v: Any) -> float | None:
    try:
        x = float(v)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def _is_mott_action(action: dict[str, Any]) -> bool:
    hay = " ".join(str(action.get(k, "")) for k in ("name", "Name", "technique")).lower()
    return "mott" in hay or "schott" in hay


def _mott_actions(parser: Any) -> list[dict[str, Any]]:
    meta = getattr(parser, "metadata", None) or {}
    parsed = list(meta.get("parsed_actions") or [])
    raw_by_id = {r.get("action_id"): r for r in (meta.get("actions") or []) if isinstance(r, dict)}
    out: list[dict[str, Any]] = []
    for a in parsed:
        if _is_mott_action(a):
            merged = dict(raw_by_id.get(a.get("action_id"), {}))
            merged.update(a)
            merged["technique"] = "MOTT_SCHOTTKY"
            a["technique"] = "MOTT_SCHOTTKY"
            out.append(merged)
    for raw in raw_by_id.values():
        if _is_mott_action(raw):
            raw["technique"] = "MOTT_SCHOTTKY"
            if not any(x.get("action_id") == raw.get("action_id") for x in out):
                out.append(dict(raw))
    return out


def _expected_frequency_count(action: dict[str, Any]) -> int | None:
    start = _to_float(action.get("start_freq_Hz") or action.get("Start Frequency (Hz)"))
    end = _to_float(action.get("end_freq_Hz") or action.get("End Frequency (Hz)"))
    ppd = _to_float(action.get("pts_per_decade") or action.get("Points Per Decade"))
    if start and end and start > 0 and end > 0 and ppd and ppd > 0:
        return int(round(abs(math.log10(start / end)) * ppd)) + 1
    n = _to_float(action.get("Number of Points") or action.get("number_of_points"))
    return int(n) if n and n > 0 else None


def _expected_step_count(action: dict[str, Any]) -> int | None:
    for key in ("Number of Steps", "Segments", "segments"):
        n = _to_float(action.get(key))
        if n and n > 0:
            return int(n)
    initial = _to_float(action.get("initial_V") or action.get("Initial Potential (V),Value"))
    final = _to_float(action.get("final_V") or action.get("Final Potential (V),Value"))
    step = _to_float(action.get("Step Height (mV)"))
    if initial is not None and final is not None and step:
        return int(round(abs((final - initial) / (step / 1000.0)))) + 1
    return None


def _read_be_f32(data: bytes, off: int) -> float:
    import struct
    return float(struct.unpack_from(">f", data, off)[0])


def _read_be_i32(data: bytes, off: int) -> int:
    import struct
    return int(struct.unpack_from(">i", data, off)[0])


def _decode_mott_binary(parser: Any, path: str | Path, action: dict[str, Any]) -> dict[str, Any] | None:
    path = Path(path)
    if not path.exists():
        return None
    data = path.read_bytes()
    n_freq = _expected_frequency_count(action)
    n_steps = _expected_step_count(action)
    if not n_freq or not n_steps:
        return None
    expected_rows = n_freq * n_steps
    if len(data) < _HEADER_BYTES + expected_rows * _RECORD_BYTES:
        return None

    rows: list[list[float]] = []
    valid_frequency_rows = 0
    for idx in range(expected_rows):
        off = _HEADER_BYTES + idx * _RECORD_BYTES
        f = _read_be_f32(data, off + _FIELD_OFFSETS["frequency_Hz_f32"])
        e = _read_be_f32(data, off + _FIELD_OFFSETS["potential_V_f32"])
        zr = _read_be_f32(data, off + _FIELD_OFFSETS["z_real_ohm_f32"])
        zi = _read_be_f32(data, off + _FIELD_OFFSETS["z_imag_ohm_f32"])
        ere = _read_be_f32(data, off + _FIELD_OFFSETS["e_real_V_f32"])
        eim = _read_be_f32(data, off + _FIELD_OFFSETS["e_imag_V_f32"])
        ire = _read_be_f32(data, off + _FIELD_OFFSETS["i_real_A_f32"])
        iim = _read_be_f32(data, off + _FIELD_OFFSETS["i_imag_A_f32"])
        idc = _read_be_f32(data, off + _FIELD_OFFSETS["i_dc_A_f32"])
        amp = _read_be_f32(data, off + _FIELD_OFFSETS["ac_amplitude_V_f32"])
        elapsed = _read_be_i32(data, off + _FIELD_OFFSETS["elapsed_us_i32"]) / 1e6
        point = _read_be_i32(data, off + _FIELD_OFFSETS["point_index_i32"])
        step_index = idx // n_freq + 1
        freq_index = idx % n_freq + 1
        if math.isfinite(f) and f > 0:
            valid_frequency_rows += 1
        zmag = math.hypot(zr, zi) if math.isfinite(zr) and math.isfinite(zi) else float("nan")
        phase = math.degrees(math.atan2(zi, zr)) if zmag and math.isfinite(zmag) else float("nan")
        den = zr * zr + zi * zi
        if den > 0 and f > 0:
            g = zr / den
            b = -zi / den
            omega = 2.0 * math.pi * f
            cp = abs(b) / omega
            cs = 1.0 / (omega * abs(zi)) if zi else float("nan")
            inv_cp2 = 1.0 / (cp * cp) if cp > 0 else float("nan")
            inv_cs2 = 1.0 / (cs * cs) if cs > 0 else float("nan")
            tan_delta = abs(zr / zi) if zi else float("nan")
        else:
            g = b = cp = cs = inv_cp2 = inv_cs2 = tan_delta = float("nan")
        rows.append([
            step_index, freq_index, point, elapsed, e, f, amp,
            ere, eim, ire, iim, idc, zr, zi, -zi, zmag, phase, -phase,
            g, b, cp, cs, inv_cp2, inv_cs2, tan_delta,
        ])

    if valid_frequency_rows < expected_rows * 0.95:
        return None
    arr = np.asarray(rows, dtype=float)
    # Basic consistency check: potential should be block-wise and frequency should repeat each block.
    potentials = arr[::n_freq, 4]
    freqs = arr[:n_freq, 5]
    return {
        "schema": "faradaiq-versastudio-mott-schottky-v313",
        "title": action.get("name") or action.get("Name") or "Mott-Schottky",
        "technique": "MOTT_SCHOTTKY",
        "action_id": action.get("action_id"),
        "source_file": getattr(parser, "source_path", None),
        "binary_data_file": str(path),
        "n_rows": int(arr.shape[0]),
        "n_potential_steps": int(n_steps),
        "n_frequencies": int(n_freq),
        "initial_potential_V": float(potentials[0]) if len(potentials) else action.get("initial_V"),
        "final_potential_V": float(potentials[-1]) if len(potentials) else action.get("final_V"),
        "frequency_min_Hz": float(np.nanmin(freqs)) if len(freqs) else action.get("end_freq_Hz"),
        "frequency_max_Hz": float(np.nanmax(freqs)) if len(freqs) else action.get("start_freq_Hz"),
        "ac_amplitude_V": float(np.nanmedian(arr[:, 6])) if arr.size else None,
        "columns": [
            "potential_step_index", "frequency_index", "vendor_point_index", "elapsed_time_s", "potential_V", "frequency_Hz", "ac_amplitude_V",
            "E_real_V", "E_imag_V", "I_real_A", "I_imag_A", "I_dc_A", "Zreal_ohm", "Zimag_ohm", "minus_Zimag_ohm", "Zmod_ohm", "phase_deg", "minus_phase_deg",
            "admittance_real_S", "admittance_imag_S", "capacitance_parallel_F", "capacitance_series_F", "inverse_capacitance_parallel_squared_F_minus2", "inverse_capacitance_series_squared_F_minus2", "loss_tangent_abs",
        ],
        "array": arr,
        "notes": [
            f"Decoded {expected_rows} Mott-Schottky impedance/capacitance rows from {n_steps} potential steps and {n_freq} frequencies.",
            "Capacitance columns are impedance-derived diagnostics. They are exported for Mott-Schottky plotting; flat-band/carrier-density fits require geometry, dielectric constant, and a user-selected linear window.",
            "For low-DOS materials, interpret measured total capacitance as a series combination of electrochemical double-layer capacitance and material/quantum capacitance when relevant.",
        ],
    }


def _attach_mott_decoding(parser: Any, decoded: dict[str, Any]) -> dict[str, Any]:
    actions = _mott_actions(parser)
    if not actions:
        return decoded
    decoded.setdefault("notes", [])
    decoded.setdefault("mott_schottky", [])
    if decoded.get("mott_schottky"):
        return decoded
    for action in actions:
        result = _decode_mott_binary(parser, getattr(parser, "psbd_path", ""), action)
        if result:
            decoded["mott_schottky"].append(result)
            decoded["notes"].extend(result.get("notes", []))
    if decoded.get("mott_schottky"):
        decoded["notes"].append("v313: Mott-Schottky was routed to capacitance analysis instead of generic EIS/DRT.")
    return decoded


def _write_csv(path: Path, columns: Sequence[str], rows: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(list(columns))
        for r in rows:
            out = []
            for x in r:
                try:
                    v = float(x)
                    out.append("" if not math.isfinite(v) else f"{v:.12g}")
                except Exception:
                    out.append(x)
            w.writerow(out)


def _selected_indices(vals: np.ndarray, targets: Sequence[float]) -> list[int]:
    out: list[int] = []
    clean = np.asarray(vals, dtype=float)
    for t in targets:
        if not len(clean):
            continue
        idx = int(np.nanargmin(np.abs(np.log10(np.maximum(clean, 1e-300)) - math.log10(t))))
        if idx not in out:
            out.append(idx)
    return out


def _plot_mott_figures(ms: dict[str, Any], fig_dir: Path, safe: str) -> list[str]:
    paths: list[str] = []
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return paths
    arr = ms["array"]
    nfreq = int(ms["n_frequencies"])
    nsteps = int(ms["n_potential_steps"])
    V = arr[:, 4].reshape(nsteps, nfreq)
    F = arr[:, 5].reshape(nsteps, nfreq)
    Zr = arr[:, 12].reshape(nsteps, nfreq)
    Zi = arr[:, 13].reshape(nsteps, nfreq)
    Zm = arr[:, 15].reshape(nsteps, nfreq)
    Cp = arr[:, 20].reshape(nsteps, nfreq)
    Cs = arr[:, 21].reshape(nsteps, nfreq)
    InvCp2 = arr[:, 22].reshape(nsteps, nfreq)
    freq_vec = F[0, :]
    pot_vec = V[:, 0]
    sel_f = _selected_indices(freq_vec, [1e6, 1e5, 1e4, 1e3, 1e2, 10, 1])
    sel_v = _selected_indices(np.abs(pot_vec - np.nanmedian(pot_vec)) + 1, [1, 1.1, 1.2])
    if not sel_v:
        sel_v = [0, nsteps // 2, nsteps - 1]

    def finish(name: str) -> None:
        p = fig_dir / f"{safe}_{name}.png"
        plt.tight_layout()
        plt.savefig(p, dpi=220)
        plt.close()
        paths.append(str(p))

    fig_dir.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(7.2, 4.8))
    for j in sel_f:
        plt.plot(pot_vec, InvCp2[:, j], marker=".", linewidth=1.0, label=f"{freq_vec[j]:.3g} Hz")
    plt.xlabel("Potential / V")
    plt.ylabel("1/Cp^2 / F^-2")
    plt.title("Mott-Schottky: inverse capacitance squared")
    plt.legend(fontsize=7)
    finish("mott_inverse_Cp2_vs_potential")

    plt.figure(figsize=(7.2, 4.8))
    for j in sel_f:
        plt.plot(pot_vec, Cp[:, j], marker=".", linewidth=1.0, label=f"Cp {freq_vec[j]:.3g} Hz")
    plt.yscale("log")
    plt.xlabel("Potential / V")
    plt.ylabel("Parallel capacitance / F")
    plt.title("Potential-dependent capacitance")
    plt.legend(fontsize=7)
    finish("capacitance_vs_potential")

    plt.figure(figsize=(7.2, 4.8))
    x, y = np.meshgrid(pot_vec, np.log10(freq_vec))
    plt.pcolormesh(x, y, np.log10(np.maximum(Cp.T, 1e-300)), shading="auto")
    plt.colorbar(label="log10(Cp / F)")
    plt.xlabel("Potential / V")
    plt.ylabel("log10(frequency / Hz)")
    plt.title("Capacitance map across potential and frequency")
    finish("capacitance_heatmap")

    plt.figure(figsize=(7.2, 4.8))
    plt.pcolormesh(x, y, np.log10(np.maximum(Zm.T, 1e-300)), shading="auto")
    plt.colorbar(label="log10(|Z| / ohm)")
    plt.xlabel("Potential / V")
    plt.ylabel("log10(frequency / Hz)")
    plt.title("Impedance magnitude map")
    finish("impedance_heatmap")

    plt.figure(figsize=(6.4, 5.2))
    for i in [0, nsteps // 2, nsteps - 1]:
        if 0 <= i < nsteps:
            plt.plot(Zr[i, :], -Zi[i, :], marker=".", linewidth=1.0, label=f"{pot_vec[i]:+.3f} V")
    plt.xlabel("Zreal / ohm")
    plt.ylabel("-Zimag / ohm")
    plt.title("Representative Nyquist traces")
    plt.legend(fontsize=7)
    finish("nyquist_representative_potentials")

    return paths


def _export_mott_outputs(parser: Any, output_dir: Path, base_name: str) -> dict[str, Any]:
    output_dir = Path(output_dir)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(base_name)).strip("._") or "mott_schottky"
    out_dir = output_dir / f"{safe}_Mott_Schottky"
    fig_dir = out_dir / "figures"
    entries: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    ms_list = list(((getattr(parser, "decoded", None) or {}).get("mott_schottky") or []))
    if not ms_list:
        return {"n_csv_files": 0, "csv_entries": [], "skipped_entries": []}
    for idx, ms in enumerate(ms_list, start=1):
        arr = ms["array"]
        cols = list(ms["columns"])
        raw_csv = out_dir / f"{safe}__mott_schottky_raw_grid.csv"
        _write_csv(raw_csv, cols, arr)
        entries.append({
            "relative_csv_path": str(raw_csv.relative_to(output_dir)).replace("\\", "/"),
            "measurement_index": idx,
            "curve_index": 1,
            "measurement_title": ms.get("title", "Mott-Schottky"),
            "curve_title": "Mott-Schottky impedance and capacitance grid",
            "method_id": "versastudio_mott_schottky",
            "technique": "MOTT_SCHOTTKY",
            "x_label": "Potential",
            "x_unit": "V",
            "y_label": "1/Cp^2",
            "y_unit": "F^-2",
            "n_points": int(ms.get("n_rows") or len(arr)),
        })
        # Long-form capacitance table restricted to columns useful for browser/ppt comparisons.
        keep_names = ["potential_step_index", "potential_V", "frequency_Hz", "capacitance_parallel_F", "capacitance_series_F", "inverse_capacitance_parallel_squared_F_minus2", "inverse_capacitance_series_squared_F_minus2", "Zreal_ohm", "Zimag_ohm", "minus_Zimag_ohm", "Zmod_ohm", "phase_deg", "loss_tangent_abs"]
        keep_idx = [cols.index(c) for c in keep_names if c in cols]
        cap_csv = out_dir / f"{safe}__mott_schottky_capacitance_summary.csv"
        _write_csv(cap_csv, [cols[i] for i in keep_idx], arr[:, keep_idx])
        entries.append({
            "relative_csv_path": str(cap_csv.relative_to(output_dir)).replace("\\", "/"),
            "measurement_index": idx,
            "curve_index": 2,
            "measurement_title": ms.get("title", "Mott-Schottky"),
            "curve_title": "Mott-Schottky capacitance summary",
            "method_id": "versastudio_mott_schottky_capacitance",
            "technique": "MOTT_SCHOTTKY",
            "x_label": "Potential",
            "x_unit": "V",
            "y_label": "Capacitance / inverse capacitance squared",
            "y_unit": "F or F^-2",
            "n_points": int(ms.get("n_rows") or len(arr)),
        })
        # Representative frequency slices for clean 1/C^2 vs V plotting.
        nfreq = int(ms["n_frequencies"])
        nsteps = int(ms["n_potential_steps"])
        grid = arr.reshape(nsteps, nfreq, arr.shape[1])
        freq_vec = grid[0, :, cols.index("frequency_Hz")]
        sel = _selected_indices(freq_vec, [1e6, 1e5, 1e4, 1e3, 1e2, 10, 1])
        slice_rows = []
        for j in sel:
            for i in range(nsteps):
                slice_rows.append([
                    grid[i, j, cols.index("potential_V")], grid[i, j, cols.index("frequency_Hz")],
                    grid[i, j, cols.index("capacitance_parallel_F")], grid[i, j, cols.index("capacitance_series_F")],
                    grid[i, j, cols.index("inverse_capacitance_parallel_squared_F_minus2")], grid[i, j, cols.index("inverse_capacitance_series_squared_F_minus2")],
                ])
        slice_csv = out_dir / f"{safe}__mott_schottky_selected_frequency_slices.csv"
        _write_csv(slice_csv, ["potential_V", "frequency_Hz", "capacitance_parallel_F", "capacitance_series_F", "inverse_capacitance_parallel_squared_F_minus2", "inverse_capacitance_series_squared_F_minus2"], np.asarray(slice_rows, dtype=float))
        entries.append({
            "relative_csv_path": str(slice_csv.relative_to(output_dir)).replace("\\", "/"),
            "measurement_index": idx,
            "curve_index": 3,
            "measurement_title": ms.get("title", "Mott-Schottky"),
            "curve_title": "Mott-Schottky selected frequency slices",
            "method_id": "versastudio_mott_schottky_frequency_slices",
            "technique": "MOTT_SCHOTTKY",
            "x_label": "Potential",
            "x_unit": "V",
            "y_label": "1/Cp^2",
            "y_unit": "F^-2",
            "n_points": len(slice_rows),
        })
        figures = _plot_mott_figures(ms, fig_dir, safe)
        overview = dict(ms)
        overview.pop("array", None)
        overview["figure_files"] = [str(Path(p).relative_to(output_dir)).replace("\\", "/") for p in figures]
        overview["paper_informed_context"] = {
            "capacitance_modeling_note": "The attached Nature paper motivates explicit treatment of potential-dependent material/quantum capacitance in low-DOS electrodes and total capacitance as a series combination with the electrochemical double layer.",
            "recommended_figures": ["1/Cp^2 vs potential", "capacitance vs potential", "capacitance heatmap vs potential/frequency", "impedance map", "representative Nyquist"],
        }
        overview_path = out_dir / f"{safe}__mott_schottky_analysis_overview.json"
        overview_path.write_text(json.dumps(overview, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"n_csv_files": len(entries), "csv_entries": entries, "skipped_entries": skipped, "mott_output_dir": str(out_dir)}


def install(core: MutableMapping[str, Any]) -> None:
    Parser = core.get("VersaStudioParser")
    if Parser is None or getattr(Parser, "_v313_mott_schottky_installed", False):
        return

    old_tech = Parser._technique_from_action_name
    old_parse = Parser._parse_psbd
    old_summary = Parser.summarize
    old_compact = Parser.build_compact_summary
    old_payload = Parser.native_numeric_payload
    old_iter = Parser.iter_primary_curve_exports
    old_export = getattr(Parser, "export_analysis_ready_outputs", None)

    def tech_from_action_v313(name: str) -> str:
        n = (name or "").strip().lower()
        if "mott" in n or "schott" in n:
            return "MOTT_SCHOTTKY"
        return old_tech(name)

    def parse_psbd_v313(self, path: str) -> dict[str, Any]:
        decoded = old_parse(self, path)
        return _attach_mott_decoding(self, decoded)

    def summarize_v313(self) -> dict[str, Any]:
        s = old_summary(self)
        for ms in (getattr(self, "decoded", {}) or {}).get("mott_schottky") or []:
            s.setdefault("measurement_rows", []).append({
                "index": len(s.get("measurement_rows", [])),
                "title": ms.get("title") or "Mott-Schottky",
                "method_id": "versastudio_mott_schottky",
                "technique": "MOTT_SCHOTTKY",
                "n_curves": 3,
                "n_eis_blocks": int(ms.get("n_potential_steps") or 0),
                "curve_titles": ["1/Cp^2 vs potential", "capacitance vs potential", "potential-frequency impedance grid"],
            })
        counts = {}
        for r in s.get("measurement_rows") or []:
            k = str(r.get("technique") or "")
            if k:
                counts[k] = counts.get(k, 0) + 1
        s["technique_counts"] = counts
        s["n_measurements"] = len(s.get("measurement_rows") or [])
        return s

    def compact_v313(self) -> dict[str, Any]:
        c = old_compact(self)
        rows = list(c.get("measurement_rows") or [])
        for ms in (getattr(self, "decoded", {}) or {}).get("mott_schottky") or []:
            rows.append({
                "title": ms.get("title") or "Mott-Schottky",
                "technique": "MOTT_SCHOTTKY",
                "n_scans": int(ms.get("n_potential_steps") or 0),
                "n_points": int(ms.get("n_rows") or 0),
                "freq_min_Hz": ms.get("frequency_min_Hz"),
                "freq_max_Hz": ms.get("frequency_max_Hz"),
                "potential_initial_V": ms.get("initial_potential_V"),
                "potential_final_V": ms.get("final_potential_V"),
                "ac_amplitude_V": ms.get("ac_amplitude_V"),
                "capacitance_analysis_ready": True,
            })
        c["measurement_rows"] = rows
        c["technique_counts"] = summarize_v313(self).get("technique_counts", c.get("technique_counts"))
        c.setdefault("decoded_counts", {})["n_mott_schottky_rows"] = int(sum((ms.get("n_rows") or 0) for ms in (getattr(self, "decoded", {}) or {}).get("mott_schottky") or []))
        return c

    def payload_v313(self) -> dict[str, Any]:
        p = old_payload(self)
        ms_payload = []
        for ms in (getattr(self, "decoded", {}) or {}).get("mott_schottky") or []:
            item = dict(ms)
            ms_payload.append(item)
        if ms_payload:
            p["mott_schottky"] = ms_payload
            p["format"] = "versastudio-native-v5-mott-schottky"
        return p

    def iter_primary_v313(self):
        yielded = False
        for x in old_iter(self):
            yielded = True
            yield x
        # Do not force dense 2D Mott data into two-column legacy curve exports;
        # dedicated export_analysis_ready_outputs writes full scientific tables.
        if not yielded:
            return

    def export_v313(self, output_dir: Path, base_name: str) -> dict[str, Any]:
        merged = {"n_csv_files": 0, "csv_entries": [], "skipped_entries": []}
        if callable(old_export):
            try:
                old = old_export(self, output_dir, base_name) or {}
                merged["n_csv_files"] += int(old.get("n_csv_files", 0))
                merged["csv_entries"].extend(old.get("csv_entries", []) or [])
                merged["skipped_entries"].extend(old.get("skipped_entries", []) or [])
            except Exception as exc:
                merged["skipped_entries"].append({"reason": f"legacy VersaStudio custom export failed: {exc}"})
        try:
            new = _export_mott_outputs(self, Path(output_dir), base_name) or {}
            merged["n_csv_files"] += int(new.get("n_csv_files", 0))
            merged["csv_entries"].extend(new.get("csv_entries", []) or [])
            merged["skipped_entries"].extend(new.get("skipped_entries", []) or [])
            if new.get("mott_output_dir"):
                merged["mott_output_dir"] = new.get("mott_output_dir")
        except Exception as exc:
            merged["skipped_entries"].append({"reason": f"Mott-Schottky export failed: {exc}"})
        return merged

    Parser._technique_from_action_name = staticmethod(tech_from_action_v313)
    Parser._parse_psbd = parse_psbd_v313
    Parser.summarize = summarize_v313
    Parser.build_compact_summary = compact_v313
    Parser.native_numeric_payload = payload_v313
    Parser.iter_primary_curve_exports = iter_primary_v313
    Parser.export_analysis_ready_outputs = export_v313
    Parser._v313_mott_schottky_installed = True

    core["FARADAIQ_DEFAULT_OUTPUT_DIR"] = os.environ.get("FARADAIQ_DEFAULT_OUTPUT_DIR", DEFAULT_OUTPUT_DIR)
    try:
        import faradaiq.v290_powerpoint_summary as ppt
        ppt.VERSION = VERSION
        ppt.APP_TITLE = f"FaradaIQ Studio {VERSION}"
        ppt.FRONT_SLIDE_FOOTER = f"Made by FaradaIQ Studio {VERSION}"
        ppt.TECHNIQUE_SCENARIOS["MOTT_SCHOTTKY"] = [
            ("Mott-Schottky capacitance", "Compare inverse capacitance squared versus potential across representative frequencies."),
            ("Frequency dispersion", "Inspect capacitance and impedance maps across the frequency-potential grid."),
            ("Screening and DOS context", "For low-DOS electrodes, discuss total capacitance as including material or quantum capacitance contributions when metadata supports it."),
        ]
        ppt.TECHNIQUE_SLIDE_RECIPES_V304["MOTT_SCHOTTKY"] = {
            "label": "Mott-Schottky / capacitance",
            "core": ["1/Cp^2 vs potential", "capacitance vs potential", "frequency-potential capacitance map"],
            "appendix": ["Nyquist/Bode checks", "frequency dispersion", "flat-band fit window sensitivity"],
        }
    except Exception:
        pass
    try:
        import faradaiq.v290_portable_browser as browser
        old_browser_tech = browser._technique_key
        old_plot_views = browser._plot_views
        if not getattr(browser, "_v313_mott_schottky_installed", False):
            def browser_tech_v313(value: Any, *, path: Any = "", columns: Sequence[Any] = (), role: Any = "") -> str:
                text = " ".join((str(value or ""), str(path or ""), str(role or ""))).upper()
                norms = {browser._normalize_column(c) for c in columns}
                has_potential = any(x in norms for x in ("potentialv", "potential", "ev"))
                has_cap = any("capacitance" in n or "c2" in n or "inversecapacitance" in n for n in norms)
                if ("MOTT" in text or "SCHOTTKY" in text or "FLAT BAND" in text) and (has_potential or has_cap):
                    return "MOTT_SCHOTTKY"
                return old_browser_tech(value, path=path, columns=columns, role=role)
            def plot_views_v313(technique: str, columns: Sequence[Any], numeric: Sequence[Any], *, path_hint: str = ""):
                if str(technique).upper() != "MOTT_SCHOTTKY":
                    return old_plot_views(technique, columns, numeric, path_hint=path_hint)
                views = []
                def add(id_, label, x, y, **kw):
                    if x is None or y is None:
                        return
                    views.append({"id": id_, "label": label, "x_index": x, "y_index": y, "x_column": str(columns[x]), "y_column": str(columns[y]), "x_scale": kw.get("x_scale", "linear"), "y_scale": kw.get("y_scale", "linear"), "plot_type": kw.get("plot_type", "curve"), "synthetic_x": False, "priority": kw.get("priority", 10), "description": kw.get("description", label)})
                find = browser._find_column
                p = find(columns, ["potential_V", "potential", "E(V)"])
                f = find(columns, ["frequency_Hz", "frequency", "freq_Hz"])
                invcp2 = find(columns, ["inverse_capacitance_parallel_squared_F_minus2", "inverse_capacitance_squared", "inv_c2", "1_c2"])
                cp = find(columns, ["capacitance_parallel_F", "capacitance_f", "capacitance"])
                cs = find(columns, ["capacitance_series_F", "series_capacitance"])
                zr = find(columns, ["Zreal_ohm", "zreal"])
                zim = find(columns, ["minus_Zimag_ohm", "minus_zimag", "-zimag", "Zimag_ohm"])
                add("mott_inverse_c2_potential", "Mott-Schottky: 1/Cp^2 vs potential", p, invcp2, y_scale="log", priority=1)
                add("capacitance_potential", "Capacitance vs potential", p, cp, y_scale="log", priority=2)
                add("series_capacitance_potential", "Series capacitance vs potential", p, cs, y_scale="log", priority=3)
                add("frequency_capacitance", "Frequency vs capacitance", f, cp, x_scale="log", y_scale="log", priority=4)
                add("frequency_inverse_c2", "Frequency vs 1/Cp^2", f, invcp2, x_scale="log", y_scale="log", priority=5)
                add("nyquist", "Nyquist: Zreal vs -Zimag", zr, zim, priority=8)
                return views or old_plot_views("SEMICONDUCTOR_ANALYSIS", columns, numeric, path_hint=path_hint)
            browser._technique_key = browser_tech_v313
            browser._plot_views = plot_views_v313
            try:
                browser.TECHNIQUE_REGISTRY["MOTT_SCHOTTKY"] = {"label": "Mott-Schottky / capacitance", "family": "Semiconductor analysis", "kind": "analysis"}
            except Exception:
                pass
            old_assign_roles = browser._assign_ui_roles
            def assign_roles_v313(datasets):
                old_assign_roles(datasets)
                for d in datasets:
                    if str(d.get("technique_key") or "").upper() == "MOTT_SCHOTTKY":
                        d["technique_label"] = "Mott-Schottky / capacitance"
                        d["family"] = "Semiconductor analysis"
                        d["data_kind"] = "analysis"
                        d["analysis_kind"] = "analysis"
                        d["ui_role"] = "analysis"
                        d["ui_role_reason"] = "Mott-Schottky capacitance analysis result"
                        d["ui_hidden_default"] = False
                        d["ui_hidden_from_compare"] = False
            browser._assign_ui_roles = assign_roles_v313
            browser._v313_mott_schottky_installed = True
    except Exception:
        pass
