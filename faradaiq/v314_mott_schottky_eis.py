"""FaradaIQ Studio v314: expose Mott-Schottky frequency sweeps to the normal EIS/DRT pipeline.

v313 correctly decoded VersaStudio Mott-Schottky .par/.psbd grids, but kept the
frequency sweeps only in the capacitance-analysis branch.  v314 keeps that branch
and additionally registers every potential step as a conventional EIS spectrum so
existing generic EIS export, DRT, Browser, Direct-LLM, and PowerPoint machinery can
operate without special-case scientific solvers.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, MutableMapping

import numpy as np

VERSION = "v314"
DEFAULT_OUTPUT_DIR = r"C:\Data\ANALYSIS\Faradaiq"


def _safe_potential_token(v: float) -> str:
    sign = "p" if v >= 0 else "m"
    return f"{sign}{abs(v):.4f}".replace(".", "p")


def _register_mott_as_eis(decoded: dict[str, Any]) -> dict[str, Any]:
    """Register each Mott-Schottky potential block as one standard EIS sweep.

    The native VersaStudio EIS representation used elsewhere in FaradaIQ is
    Nx6 = frequency, Zreal, Zimag, |Z|, phase, AC amplitude.  Mott-Schottky data
    are already decoded by v313 into a potential x frequency grid containing the
    same physical observables.  Re-exposing those rows through ``eis_sweeps`` is
    therefore lossless and allows the established generic EIS + DRT pipeline to
    run on the exact same measured points.
    """
    ms_list = list(decoded.get("mott_schottky") or [])
    if not ms_list:
        return decoded

    existing = list(decoded.get("eis_sweeps") or [])
    existing_meta = list(decoded.get("eis_sweep_metadata") or [])
    # Avoid duplicate registration on repeated load/wrapper calls.
    if decoded.get("mott_schottky_eis_registered"):
        return decoded

    added_sweeps: list[np.ndarray] = []
    added_meta: list[dict[str, Any]] = []
    for ms_idx, ms in enumerate(ms_list, start=1):
        arr = np.asarray(ms.get("array"), dtype=float)
        cols = list(ms.get("columns") or [])
        nsteps = int(ms.get("n_potential_steps") or 0)
        nfreq = int(ms.get("n_frequencies") or 0)
        if arr.ndim != 2 or nsteps <= 0 or nfreq <= 0 or len(arr) != nsteps * nfreq:
            continue
        required = ["potential_V", "frequency_Hz", "Zreal_ohm", "Zimag_ohm", "Zmod_ohm", "phase_deg", "ac_amplitude_V"]
        if not all(c in cols for c in required):
            continue
        idx = {c: cols.index(c) for c in required}
        grid = arr.reshape(nsteps, nfreq, arr.shape[1])
        for step_i in range(nsteps):
            block = grid[step_i]
            v = float(np.nanmedian(block[:, idx["potential_V"]]))
            f = block[:, idx["frequency_Hz"]]
            zr = block[:, idx["Zreal_ohm"]]
            zi = block[:, idx["Zimag_ohm"]]
            zm = block[:, idx["Zmod_ohm"]]
            ph = block[:, idx["phase_deg"]]
            amp = block[:, idx["ac_amplitude_V"]]
            mask = np.isfinite(f) & np.isfinite(zr) & np.isfinite(zi) & (f > 0)
            if int(mask.sum()) < 4:
                continue
            sw = np.column_stack([f[mask], zr[mask], zi[mask], zm[mask], ph[mask], amp[mask]])
            # Standard FaradaIQ EIS convention is descending frequency.
            order = np.argsort(sw[:, 0])[::-1]
            sw = sw[order]
            added_sweeps.append(sw)
            added_meta.append({
                "action_id": ms.get("action_id"),
                "title": "Mott-Schottky EIS",
                "technique": "EIS",
                "dc_potential_V": v,
                "label": f"Mott-Schottky {v:+.4f} V",
                "start_freq_Hz": float(np.nanmax(sw[:, 0])),
                "end_freq_Hz": float(np.nanmin(sw[:, 0])),
                "pts_per_decade": None,
                "ac_amplitude_mV": float(np.nanmedian(sw[:, 5]) * 1000.0),
                "source": "MOTT_SCHOTTKY",
                "mott_grid_index": ms_idx,
                "potential_step_index": step_i + 1,
                "potential_token": _safe_potential_token(v),
            })

    if added_sweeps:
        decoded["eis_sweeps"] = existing + added_sweeps
        decoded["eis_sweep_metadata"] = existing_meta + added_meta
        decoded["mott_schottky_eis_registered"] = True
        decoded["mott_schottky_eis_spectrum_count"] = len(added_sweeps)
        decoded.setdefault("notes", []).append(
            f"v314: registered {len(added_sweeps)} Mott-Schottky potential-step frequency sweeps as standard EIS spectra; normal EIS export and DRT remain enabled in addition to capacitance analysis."
        )
    return decoded


def _write_potential_manifest_for_generic_drt(collection: Any, analysis_root: Path) -> dict[str, Any]:
    """Write v309-compatible DRT linkage manifests with set-potential identity."""
    analysis_root = Path(analysis_root)
    written = 0
    spectra = 0
    for entry_idx, entry in enumerate(getattr(collection, "entries", []) or [], start=1):
        if not isinstance(entry, dict):
            continue
        parser = entry.get("parser")
        decoded = getattr(parser, "decoded", None) or {}
        if not decoded.get("mott_schottky_eis_registered"):
            continue
        meta = entry.get("path_metadata", {}) or {}
        stem = Path(meta.get("basename", f"entry_{entry_idx}")).stem
        rel_dir = Path(*meta.get("subfolder_names", [])) if meta.get("subfolder_names") else Path()
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_.") or f"entry_{entry_idx}"
        root = analysis_root / rel_dir / f"{safe_stem}_for_plotting_analysis"
        drt_dir = root / "DRT_input_csv"
        if not drt_dir.is_dir():
            continue
        csvs = sorted([p for p in drt_dir.glob("*.csv") if "freq_zreal_zimag" in p.name.lower()])
        pairs = list(parser._paired_eis_sweeps()) if hasattr(parser, "_paired_eis_sweeps") else []
        mott_pairs = [(m, a) for m, a in pairs if str(m.get("source") or "").upper() == "MOTT_SCHOTTKY"]
        if not mott_pairs:
            continue
        csv_by_name = {p.name.lower(): p for p in csvs}
        items = []
        for i, pair in enumerate(mott_pairs, start=1):
            m, arr = pair
            label = re.sub(r"[^A-Za-z0-9._-]+", "_", str(m.get("label") or f"Mott-Schottky_{i:03d}")).strip("_.")
            expected_name = f"{safe_stem}__{label}__freq_zreal_zimag.csv"
            p = csv_by_name.get(expected_name.lower())
            if p is None:
                # Fall back to a label match without guessing potential identity.
                candidates = [q for q in csvs if label.lower() in q.name.lower()]
                p = candidates[0] if len(candidates) == 1 else None
            if p is None:
                continue
            v = m.get("dc_potential_V")
            items.append({
                "file": p.name,
                "source_group": "Mott-Schottky",
                "source_set": safe_stem,
                "spectrum_index": int(m.get("potential_step_index") or i),
                "n_frequency_points": int(len(arr)) if isinstance(arr, np.ndarray) else 0,
                "frequency_max_Hz": float(np.nanmax(arr[:, 0])) if isinstance(arr, np.ndarray) and len(arr) else None,
                "frequency_min_Hz": float(np.nanmin(arr[:, 0])) if isinstance(arr, np.ndarray) and len(arr) else None,
                "source_eis_dataset_id": f"{safe_stem}::mott_step{int(m.get('potential_step_index') or i):03d}",
                "source_eis_file": Path(str(getattr(parser, "source_path", stem))).name,
                "set_potential_V": float(v) if isinstance(v, (int, float)) and math.isfinite(float(v)) else None,
                "potential_label": m.get("label") or "",
                "potential_mode": "set_potential",
            })
        if not items:
            continue
        manifest = {
            "schema": "faradaiq-drt-input-spectrum-manifest-v314",
            "policy": "one Mott-Schottky potential step per EIS/DRT input CSV; capacitance analysis and EIS/DRT are additive",
            "source": "VersaStudio Mott-Schottky decoded potential-frequency grid",
            "source_sets": {safe_stem: len(items)},
            "n_spectra": len(items),
            "spectra": items,
        }
        (drt_dir / "DRT_input_spectrum_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        written += 1
        spectra += len(items)
    return {"manifests_written": written, "spectra_linked": spectra}


def install(core: MutableMapping[str, Any]) -> None:
    Parser = core.get("VersaStudioParser")
    if Parser is None or getattr(Parser, "_v314_mott_eis_installed", False):
        return

    previous_parse = Parser._parse_psbd

    def parse_psbd_v314(self, path: str) -> dict[str, Any]:
        decoded = previous_parse(self, path)
        return _register_mott_as_eis(decoded)

    Parser._parse_psbd = parse_psbd_v314
    Parser._v314_mott_eis_installed = True

    # Wrap the generic DRT exporter.  It now receives the registered EIS sweeps;
    # after it writes its normal files, add potential identity for DRT/Browser/PPT.
    previous_generic = core.get("_v29_export_generic_drt_for_collection")
    if callable(previous_generic) and not getattr(previous_generic, "_v314_mott_wrapper", False):
        def generic_drt_v314(collection, analysis_root, base_name):
            result = previous_generic(collection, analysis_root, base_name)
            try:
                linkage = _write_potential_manifest_for_generic_drt(collection, Path(analysis_root))
                if isinstance(result, dict):
                    result["mott_schottky_potential_linkage"] = linkage
            except Exception as exc:
                if isinstance(result, dict):
                    result["mott_schottky_potential_linkage_error"] = str(exc)
            return result
        generic_drt_v314._v314_mott_wrapper = True
        core["_v29_export_generic_drt_for_collection"] = generic_drt_v314

    # Extend the existing v313 PowerPoint Mott recipe so EIS + DRT are explicitly
    # part of the main scientific story, not hidden as an appendix-only check.
    try:
        import faradaiq.v290_powerpoint_summary as ppt
        ppt.VERSION = VERSION
        ppt.APP_TITLE = f"FaradaIQ Studio {VERSION}"
        ppt.FRONT_SLIDE_FOOTER = f"Made by FaradaIQ Studio {VERSION}"
        ppt.TECHNIQUE_SCENARIOS["MOTT_SCHOTTKY"] = [
            ("Mott-Schottky capacitance", "Compare inverse capacitance squared versus potential across representative frequencies."),
            ("Potential-resolved EIS", "Treat each Mott-Schottky potential step as its measured EIS spectrum and compare Nyquist/Bode response across potential."),
            ("DRT across potential", "Use the normal DRT pipeline on each validated potential-step spectrum and compare relaxation processes as a function of bias."),
            ("Frequency dispersion", "Inspect capacitance and impedance maps across the frequency-potential grid."),
            ("Screening and DOS context", "For low-DOS electrodes, discuss total capacitance as including electrochemical double-layer and material/quantum-capacitance contributions when supported by metadata."),
        ]
        ppt.TECHNIQUE_SLIDE_RECIPES_V304["MOTT_SCHOTTKY"] = {
            "label": "Mott-Schottky / capacitance",
            "core": ["1/Cp^2 vs potential", "capacitance vs potential", "potential-resolved Nyquist/Bode", "DRT across potential", "frequency-potential capacitance map"],
            "appendix": ["series/parallel capacitance comparison", "frequency dispersion", "flat-band fit window sensitivity"],
        }
    except Exception:
        pass

    core["FARADAIQ_DEFAULT_OUTPUT_DIR"] = DEFAULT_OUTPUT_DIR
