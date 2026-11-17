from __future__ import annotations

import csv
import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass
class SpectrumInput:
    source_group: Path
    source_set: str
    group_index: int
    frequency_hz: np.ndarray
    zreal_ohm: np.ndarray
    zimag_ohm: np.ndarray
    set_potential_V: float | None = None
    potential_label: str = ""
    potential_mode: str = "unknown"


def _safe(text: str, fallback: str = "eis") -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text or "")).strip("_.")
    return out or fallback


def _natural_key(path: Path):
    parts = re.split(r"(\d+)", path.name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def _find_column(columns: Iterable[str], predicates) -> str | None:
    for col in columns:
        low = str(col).strip().lower().replace(" ", "")
        if any(pred(low) for pred in predicates):
            return str(col)
    return None


def _read_spectrum(path: Path, source_set: str, group_index: int) -> SpectrumInput | None:
    try:
        df = pd.read_csv(path, encoding="windows-1252")
    except Exception:
        try:
            df = pd.read_csv(path)
        except Exception:
            return None
    if df.empty:
        return None
    cols = list(df.columns)
    fcol = _find_column(cols, [
        lambda s: s in {"frequency_hz", "frequency", "freq", "f2[hz]", "f[hz]"},
        lambda s: "freq" in s and ("hz" in s or s == "freq"),
        lambda s: s.startswith("f2[") and "hz" in s,
    ])
    zrcol = _find_column(cols, [
        lambda s: s in {"zreal_ohm", "realz[ohm]", "real(z)[ohm]", "zre", "zreal"},
        lambda s: "realz" in s and "ohm" in s,
        lambda s: "real(z)" in s and "ohm" in s,
    ])
    zicol = _find_column(cols, [
        lambda s: s in {"zimag_ohm", "imagz[ohm]", "imag(z)[ohm]", "zim", "zimag"},
        lambda s: "imagz" in s and "ohm" in s,
        lambda s: "imag(z)" in s and "ohm" in s,
    ])
    if not (fcol and zrcol and zicol):
        return None
    f = pd.to_numeric(df[fcol], errors="coerce").to_numpy(float)
    zr = pd.to_numeric(df[zrcol], errors="coerce").to_numpy(float)
    zi = pd.to_numeric(df[zicol], errors="coerce").to_numpy(float)
    mask = np.isfinite(f) & np.isfinite(zr) & np.isfinite(zi) & (f > 0)
    f, zr, zi = f[mask], zr[mask], zi[mask]
    if len(f) < 4 or len(np.unique(np.round(np.log10(f), 9))) < 3:
        return None
    # DRT receives one acquisition per file. Keep the vendor/parser ordering when
    # monotonic; otherwise sort high-to-low by frequency for a stable spectrum.
    d = np.diff(f)
    if not (np.all(d <= 0) or np.all(d >= 0)):
        order = np.argsort(f)[::-1]
        f, zr, zi = f[order], zr[order], zi[order]
    elif np.all(d >= 0):
        f, zr, zi = f[::-1], zr[::-1], zi[::-1]
    try:
        from .potential_identity_v309 import resolve_potential_identity
        ident = resolve_potential_identity(technique="EIS", dataframe=df, path=path)
    except Exception:
        ident = None
    return SpectrumInput(path, source_set, group_index, f, zr, zi,
        getattr(ident, "set_potential_V", None), getattr(ident, "potential_label", ""), getattr(ident, "potential_mode", "unknown"))


def discover_parsed_eis_spectra(data_dir: Path) -> list[SpectrumInput]:
    data_dir = Path(data_dir)
    spectra: list[SpectrumInput] = []
    for group_dir in sorted([p for p in data_dir.glob("*_Groups") if p.is_dir()], key=_natural_key):
        source_set = group_dir.name[:-7] if group_dir.name.lower().endswith("_groups") else group_dir.name
        files = sorted([p for p in group_dir.iterdir() if p.is_file() and p.suffix.lower() in {".txt", ".csv", ".asc"}], key=_natural_key)
        for idx, path in enumerate(files, 1):
            spec = _read_spectrum(path, source_set, idx)
            if spec is not None:
                spectra.append(spec)
    return spectra


def _existing_input_csvs(drt_dir: Path) -> list[Path]:
    return sorted([p for p in Path(drt_dir).glob("*.csv") if p.is_file() and "freq_zreal_zimag" in p.name.lower()], key=_natural_key)


def rebuild_drt_inputs_from_parsed_groups(data_dirs: Iterable[Path], *, clear_stale_outputs: bool = True) -> dict:
    """Replace concatenated/reconstructed DRT inputs with one CSV per parsed EIS acquisition.

    parse_EIS.py already separates repeated frequency sweeps into ``*_Groups``.  Those
    group files are the strongest acquisition-boundary evidence available after parsing,
    so DRT should consume them one-by-one rather than collapsing all EIS measurements at
    one physical point into one or two long files.
    """
    result = {
        "version": "v309",
        "data_dirs_checked": 0,
        "drt_dirs_rebuilt": 0,
        "spectra_written": 0,
        "old_inputs_removed": 0,
        "stale_drt_output_dirs_removed": 0,
        "details": [],
    }
    for data_dir in [Path(p) for p in data_dirs]:
        result["data_dirs_checked"] += 1
        spectra = discover_parsed_eis_spectra(data_dir)
        if not spectra:
            continue
        drt_dir = data_dir.parent / "DRT_input_csv"
        drt_dir.mkdir(parents=True, exist_ok=True)
        old = _existing_input_csvs(drt_dir)
        old_rows = {}
        for p in old:
            try:
                with p.open("r", encoding="utf-8", errors="replace") as fh:
                    old_rows[p.name] = max(0, sum(1 for _ in fh) - 1)
            except Exception:
                old_rows[p.name] = None
        for p in old:
            try:
                p.unlink()
                result["old_inputs_removed"] += 1
            except Exception:
                pass

        if clear_stale_outputs:
            stale = drt_dir / "DRT"
            if stale.exists() and stale.is_dir():
                try:
                    shutil.rmtree(stale)
                    result["stale_drt_output_dirs_removed"] += 1
                except Exception:
                    pass

        written = []
        source_counts = {}
        for spec in spectra:
            source_counts[spec.source_set] = source_counts.get(spec.source_set, 0) + 1
            stem = _safe(spec.source_set, "eis")
            fname = f"{stem}__spectrum{spec.group_index:03d}__freq_zreal_zimag.csv"
            out = drt_dir / fname
            with out.open("w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["frequency_Hz", "Zreal_ohm", "Zimag_ohm"])
                for f, zr, zi in zip(spec.frequency_hz, spec.zreal_ohm, spec.zimag_ohm):
                    w.writerow([f"{float(f):.12g}", f"{float(zr):.12g}", f"{float(zi):.12g}"])
            written.append({
                "file": out.name,
                "source_group": spec.source_group.name,
                "source_set": spec.source_set,
                "spectrum_index": spec.group_index,
                "n_frequency_points": int(len(spec.frequency_hz)),
                "frequency_max_Hz": float(np.nanmax(spec.frequency_hz)),
                "frequency_min_Hz": float(np.nanmin(spec.frequency_hz)),
                "source_eis_dataset_id": f"{stem}::spectrum{spec.group_index:03d}",
                "source_eis_file": spec.source_group.name,
                "set_potential_V": spec.set_potential_V,
                "potential_label": spec.potential_label,
                "potential_mode": spec.potential_mode,
            })
        manifest = {
            "schema": "faradaiq-drt-input-spectrum-manifest-v309",
            "policy": "one parsed EIS acquisition per DRT input CSV; physical point identity does not collapse repeated EIS acquisitions",
            "source": "parse_EIS repeated-frequency *_Groups outputs",
            "old_inputs_replaced": old_rows,
            "source_sets": source_counts,
            "n_spectra": len(written),
            "spectra": written,
        }
        (drt_dir / "DRT_input_spectrum_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        result["drt_dirs_rebuilt"] += 1
        result["spectra_written"] += len(written)
        result["details"].append({
            "drt_dir": str(drt_dir),
            "n_spectra": len(written),
            "old_input_count": len(old),
            "source_sets": source_counts,
        })
    return result


def propagate_drt_potential_metadata(input_dir: Path) -> dict:
    """Attach source-EIS potential identity to completed DRT outputs without changing the solve."""
    input_dir = Path(input_dir)
    manifest_path = input_dir / "DRT_input_spectrum_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {"updated": 0, "missing": 0}
    output_root = input_dir / "DRT"
    updated=0; missing=0; children=[]
    for item in manifest.get("spectra") or []:
        inp = str(item.get("file") or "")
        expected = f"{Path(inp).stem}_simple_drt_data.csv"
        matches=list(output_root.rglob(expected)) if output_root.exists() else []
        if not matches:
            missing += 1; continue
        out=matches[0]
        try:
            df=pd.read_csv(out)
            df["source_eis"] = str(item.get("source_eis_file") or item.get("source_group") or "")
            df["source_eis_dataset_id"] = str(item.get("source_eis_dataset_id") or "")
            df["set_potential_V"] = item.get("set_potential_V")
            df["potential_label"] = str(item.get("potential_label") or "")
            df["potential_mode"] = str(item.get("potential_mode") or "unknown")
            df.to_csv(out,index=False)
            sidecar=out.with_suffix(out.suffix + ".metadata.json")
            child={
                "source_eis_dataset_id": item.get("source_eis_dataset_id"),
                "source_eis_file": item.get("source_eis_file") or item.get("source_group"),
                "source_drt_input": inp,
                "drt_result": out.name,
                "set_potential_V": item.get("set_potential_V"),
                "potential_label": item.get("potential_label") or "",
                "potential_mode": item.get("potential_mode") or "unknown",
            }
            sidecar.write_text(json.dumps(child,indent=2,ensure_ascii=False),encoding="utf-8")
            children.append(child); updated += 1
        except Exception:
            missing += 1
    run_manifest=output_root / "DRT_run_potential_manifest.json"
    if output_root.exists():
        run_manifest.write_text(json.dumps({"schema":"faradaiq-drt-potential-linkage-v309","spectra":children},indent=2,ensure_ascii=False),encoding="utf-8")
    return {"updated":updated,"missing":missing,"manifest":str(run_manifest) if output_root.exists() else ""}


def validate_drt_outputs(input_dir: Path) -> dict:
    input_dir = Path(input_dir)
    inputs = sorted([p for p in input_dir.glob("*.csv") if p.is_file() and "freq_zreal_zimag" in p.name.lower()], key=_natural_key)
    output_root = input_dir / "DRT"
    outputs = list(output_root.rglob("*_simple_drt_data.csv")) if output_root.exists() else []
    output_names = {p.name.lower() for p in outputs}
    missing = []
    for inp in inputs:
        expected = f"{inp.stem}_simple_drt_data.csv".lower()
        if expected not in output_names:
            missing.append(inp.name)
    propagated = propagate_drt_potential_metadata(input_dir)
    return {
        "expected": len(inputs),
        "completed": len(inputs) - len(missing),
        "missing": missing,
        "output_files_found": len(outputs),
        "complete": bool(inputs) and not missing,
        "potential_metadata": propagated,
    }
