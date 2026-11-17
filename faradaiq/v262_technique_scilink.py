"""FaradaIQ Studio v290 interactive SciLink sessions and minimal Browser data handoff.

Production fixes:
- The hyperspectral range control can be dragged manually without Plotly redraws
  interrupting the pointer gesture. The selected frame is rendered on release,
  with previous/next buttons and keyboard navigation as fallbacks.
- Ask SciLink from the Browser creates one compact, job-local analysis dataset from the current Browser selection.
- The only context supplied with that dataset is the exact experiment description entered in Main Studio and a compact JSON structure description of the generated bundle.
- The SciLink CLI receives explicit --data and --metadata paths and the terminal remains interactive for follow-up questions.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import traceback
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

VERSION = "v292"
_CORE: MutableMapping[str, Any] = {}
_PREVIOUS_REVIEW: Any = None

_DATA_EXTENSIONS = {
    ".csv", ".tsv", ".txt", ".asc", ".dat", ".npy", ".npz", ".xlsx", ".xls",
}
_METADATA_EXTENSIONS = {".json", ".yaml", ".yml", ".xml"}
_SUPPORT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".pdf", ".html", ".md", ".docx"}


def _g(name: str, default: Any = None) -> Any:
    return _CORE.get(name, default)


def _gui_from(source: Any) -> Any:
    return getattr(source, "gui", source)


def _browser_from(source: Any) -> Any | None:
    try:
        import faradaiq.v239_scilink_edison as old
        resolved = old._browser_from(source)
        if resolved is not None:
            return resolved
    except Exception:
        pass
    return source if hasattr(source, "tree") else None


def _safe_path_key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except Exception:
        return os.path.normcase(str(path))


def _browser_selection_paths(source: Any) -> list[Path]:
    """Resolve the active Browser selection, including branch and H5-backed rows.

    The Browser has accumulated several tree implementations.  Some expose a
    direct ``_path_by_iid`` mapping, while technique/method/point branches are
    resolved only by ``selected_paths`` or by recursively walking record rows.
    Use every supported resolver so the Analyze panel, local handoffs, and the
    SciLink review always agree about the current selection.
    """
    browser = _browser_from(source)
    if browser is None:
        return []
    paths: list[Path] = []
    try:
        selected = getattr(browser, "selected_paths", None)
        if callable(selected):
            paths.extend(Path(p).expanduser().resolve() for p in (selected(plottable_only=False) or []))
    except Exception:
        pass
    try:
        raw = getattr(browser, "selected_path", None)
        if raw:
            paths.append(Path(raw).expanduser().resolve())
    except Exception:
        pass
    try:
        tree = getattr(browser, "tree", None)
        path_by_iid = getattr(browser, "_path_by_iid", {}) or {}
        records_by_iid = getattr(browser, "_record_by_iid", {}) or getattr(browser, "_records_by_iid", {}) or {}
        if tree is not None:
            selected_iids = list(tree.selection() or [])
            for iid in selected_iids:
                raw = path_by_iid.get(iid)
                if raw:
                    paths.append(Path(raw).expanduser().resolve())
                rec = records_by_iid.get(iid)
                if isinstance(rec, Mapping) and rec.get("path"):
                    paths.append(Path(str(rec["path"])).expanduser().resolve())
            # Branch rows often have no direct path.  The active Browser helper
            # recursively resolves every descendant record under the branch.
            try:
                from faradaiq.browser_ui_v209 import _tree_records
                for iid in selected_iids:
                    for rec in _tree_records(browser, iid):
                        raw = rec.get("path") if isinstance(rec, Mapping) else None
                        if raw:
                            paths.append(Path(str(raw)).expanduser().resolve())
            except Exception:
                pass
            collector = getattr(browser, "_collect_children_paths", None)
            if callable(collector):
                collected: list[Any] = []
                for iid in selected_iids:
                    try:
                        collector(iid, collected)
                    except Exception:
                        pass
                paths.extend(Path(p).expanduser().resolve() for p in collected)
    except Exception:
        pass
    # A Plot/view can be the active Browser selection even when the tree focus is
    # elsewhere.  Preserve that reviewed set as a final non-stale fallback.
    if not paths:
        try:
            paths.extend(Path(p).expanduser().resolve() for p in (getattr(browser, "last_plot_paths", None) or []))
        except Exception:
            pass
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            if not path.is_file():
                continue
            key = _safe_path_key(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        except Exception:
            continue
    return unique


def _selected_record_names(source: Any) -> list[str]:
    try:
        import faradaiq.v247_experience as v247
        return list(v247._selected_record_names(source))
    except Exception:
        return []


def _experiment_description(source: Any) -> str:
    """Return the operator-visible experiment description without auto-generated file paths.

    The Main Studio text widget can contain a FARADAIQ_AUTO_CONTEXT block with
    canonical H5/JSON names and the original experiment folder. That block caused
    SciLink to leave the reviewed job-local data folder and inspect unrelated
    parent files. The Browser handoff keeps only the operator-facing description
    that precedes the auto-context marker.
    """
    gui = _gui_from(source)
    text = ""
    for name in ("desc_text", "experiment_description_text", "description_text"):
        try:
            widget = getattr(gui, name)
            text = str(widget.get("1.0", "end-1c"))
            if text.strip():
                break
        except Exception:
            continue
    if not text.strip():
        for name in ("experiment_description", "description"):
            try:
                value = getattr(gui, name)
                if value is not None and str(value).strip():
                    text = str(value)
                    break
            except Exception:
                continue
    text = re.sub(
        r"(?is)\n?FARADAIQ_AUTO_CONTEXT_BEGIN.*?FARADAIQ_AUTO_CONTEXT_END\s*",
        "\n",
        text,
    )
    text = re.sub(r"(?is)\n?FARADAIQ_AUTO_CONTEXT_BEGIN.*$", "\n", text)
    clean_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if clean_lines and clean_lines[-1] != "":
                clean_lines.append("")
            continue
        if re.match(r"(?i)^(source|analysis/output folder|canonical h5|canonical json|browser catalog)\s*:", line):
            continue
        if re.search(r"(?:^[A-Za-z]:\\|^\\\\|/mnt/|/home/)", line):
            continue
        clean_lines.append(line)
    return "\n".join(clean_lines).strip()[:12000]


def _text_table_structure(path: Path) -> dict[str, Any]:
    """Inspect delimiter, headers, and row count without copying raw values into metadata."""
    info: dict[str, Any] = {"kind": "text_table", "columns": [], "row_count": None}
    try:
        sample = path.read_text(encoding="utf-8-sig", errors="replace")[:131072]
        lines = [line for line in sample.splitlines() if line.strip()]
        if not lines:
            return info
        first = lines[0]
        delimiter = "," if first.count(",") >= max(first.count("\t"), first.count(";")) else "\t" if first.count("\t") >= first.count(";") else ";"
        columns = [cell.strip() for cell in first.split(delimiter)]
        info["delimiter"] = "tab" if delimiter == "\t" else delimiter
        info["columns"] = columns[:200]
        # Count lines in a streaming pass. This is compact metadata only.
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            count = sum(1 for line in handle if line.strip())
        info["row_count"] = max(0, count - 1)
    except Exception as exc:
        info["inspection_warning"] = str(exc)[:300]
    return info


def _file_structure(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    base: dict[str, Any] = {
        "filename": path.name,
        "relative_path": f"data/{path.name}",
        "extension": suffix,
        "size_bytes": int(path.stat().st_size) if path.exists() else 0,
    }
    if suffix in {".csv", ".tsv", ".txt", ".asc", ".dat"}:
        base.update(_text_table_structure(path))
    elif suffix == ".npy":
        try:
            import numpy as np
            arr = np.load(path, mmap_mode="r", allow_pickle=False)
            base.update({"kind": "numpy_array", "shape": list(arr.shape), "dtype": str(arr.dtype)})
        except Exception as exc:
            base.update({"kind": "numpy_array", "inspection_warning": str(exc)[:300]})
    elif suffix == ".npz":
        try:
            import numpy as np
            with np.load(path, allow_pickle=False) as archive:
                base.update({
                    "kind": "numpy_archive",
                    "arrays": [{"name": key, "shape": list(archive[key].shape), "dtype": str(archive[key].dtype)} for key in list(archive.files)[:200]],
                })
        except Exception as exc:
            base.update({"kind": "numpy_archive", "inspection_warning": str(exc)[:300]})
    else:
        base["kind"] = "data_file"
    return base


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_column_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())



def _find_column(headers: Sequence[str], candidates: Sequence[str]) -> int | None:
    """Resolve a measurement column without letting one-letter aliases match index fields.

    Earlier releases allowed substring matching for aliases such as ``i`` and
    ``e``.  That made ``point_index`` look like current and caused CA handoffs
    to become ``time_s,point_index``.  Matching is now exact first, then uses
    only descriptive aliases of at least three characters.
    """
    raw_headers = [re.sub(r"\s+", "", str(value or "")).casefold() for value in headers]
    raw_aliases = [re.sub(r"\s+", "", str(value or "")).casefold() for value in candidates if str(value).strip()]
    for alias in raw_aliases:
        for index, value in enumerate(raw_headers):
            if value == alias:
                return index
    normalized = [_normalized_column_name(value) for value in headers]
    aliases = [_normalized_column_name(value) for value in candidates if str(value).strip()]
    for alias in aliases:
        for index, value in enumerate(normalized):
            if value == alias:
                return index
    descriptive = [alias for alias in aliases if len(alias) >= 3]
    for alias in descriptive:
        for index, value in enumerate(normalized):
            if value.startswith(alias) or value.endswith(alias):
                return index
    for alias in descriptive:
        for index, value in enumerate(normalized):
            if alias in value:
                return index
    return None



def _filename_series_metadata(path: Path) -> dict[str, Any]:
    text = path.stem
    out: dict[str, Any] = {}
    point = re.search(r"(?i)(?:^|_)point[_-]?(\d+)", text)
    if not point:
        point = re.search(r"(?i)(?:^|[_-])P0*(\d+)(?:[_-]|$)", text)
    cycle = re.search(r"(?i)(?:^|_)cycle[_-]?(\d+)", text)
    scan = re.search(r"(?i)(?:^|_)scanrate[_-]?([0-9]+(?:[p.]?[0-9]+)?)mvs", text)
    if point:
        out["point_index"] = int(point.group(1))
    if cycle:
        out["cycle_index"] = int(cycle.group(1))
    if scan:
        out["scan_rate_mV_s"] = float(scan.group(1).replace("p", "."))
    return out



def _unit_factor(header: str, quantity: str) -> float:
    raw = str(header or "").replace("μ", "µ")
    text = raw.lower().replace("ω", "ohm")
    compact = re.sub(r"\s+", "", text)
    if quantity == "current":
        if "pa" in compact: return 1e-12
        if "na" in compact: return 1e-9
        if "µa" in compact or "ua" in compact: return 1e-6
        if "ma" in compact: return 1e-3
    if quantity == "potential":
        if "mv" in compact: return 1e-3
        if "kv" in compact: return 1e3
    if quantity == "time":
        if "ns" in compact: return 1e-9
        if "µs" in compact or "us" in compact: return 1e-6
        if "ms" in compact: return 1e-3
        if "min" in compact: return 60.0
        if "hour" in compact or compact.endswith("hr") or "_hr" in compact: return 3600.0
    if quantity == "frequency":
        if re.search(r"MHz|mega\s*hertz", raw): return 1e6
        if re.search(r"kHz|kilo\s*hertz", raw, re.I): return 1e3
        if re.search(r"mHz|milli\s*hertz", raw): return 1e-3
    if quantity in {"impedance", "gamma"}:
        if re.search(r"M(?:Ω|Ohm|ohm)|mega\s*ohm", raw): return 1e6
        if re.search(r"k(?:Ω|Ohm|ohm)|kilo\s*ohm", raw, re.I): return 1e3
        if re.search(r"m(?:Ω|Ohm|ohm)|milli\s*ohm", raw): return 1e-3
    if quantity == "charge":
        if "mc" in compact: return 1e-3
        if "µc" in compact or "uc" in compact: return 1e-6
        if "nc" in compact: return 1e-9
    return 1.0




def _prepare_scilink_table(source: Path, destination: Path, techniques: Sequence[str]) -> dict[str, Any]:
    """Create a validated technique-specific table for SciLink.

    The prepared table contains only the independent variable and measured
    signal columns required by the selected technique.  Control columns such as
    point index, applied setpoint, cycle, and method remain in the provenance
    JSON and untouched ``source_data`` copy.  EIS keeps its required three-column
    complex-impedance contract.
    """
    technique_set = {str(value).upper() for value in techniques}
    suffix = source.suffix.lower()
    result: dict[str, Any] = {
        "source_filename": source.name,
        "generated_filename": destination.name,
        "short_name_applied": source.name != destination.name,
        "source_sha256": _sha256(source),
        "transformation": "exact_copy",
        "source_columns": [],
        "scilink_columns": [],
        "detected_techniques": sorted(technique_set),
    }
    if suffix not in {".csv", ".tsv", ".txt", ".asc", ".dat"}:
        shutil.copy2(source, destination)
        result["scilink_sha256"] = _sha256(destination)
        result.update(_filename_series_metadata(source))
        return result

    try:
        sample = source.read_text(encoding="utf-8-sig", errors="replace")[:65536]
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except Exception:
        dialect = csv.excel
        dialect.delimiter = ","

    with source.open("r", encoding="utf-8-sig", errors="replace", newline="") as src:
        reader = csv.reader(src, dialect)
        headers = [str(value).strip() for value in next(reader, [])]
        result["source_columns"] = headers

        t_index = _find_column(headers, (
            "time_s", "time", "t_s", "elapsed_s", "elapsed_time", "seconds", "time_ms", "time_us",
        ))
        p_index = _find_column(headers, (
            "potential_V", "potential", "voltage_V", "voltage", "Ewe/V", "E_V", "working_electrode_potential",
        ))
        i_index = _find_column(headers, (
            "current_A", "current", "I_A", "I/mA", "current_mA", "current_uA", "measured_current", "I",
        ))
        f_index = _find_column(headers, (
            "frequency_Hz", "frequency", "freq_Hz", "freq", "f_Hz", "Frequency/Hz",
        ))
        zr_index = _find_column(headers, (
            "z_real_ohm", "z_real", "zreal", "z_re", "real_impedance", "Zre/Ohm", "Z'",
        ))
        zi_index = _find_column(headers, (
            "z_imag_ohm", "z_imag", "zimag", "z_im", "imag_impedance", "Zim/Ohm", "Z''", "minus_zimag",
        ))
        tau_index = _find_column(headers, (
            "tau_s", "tau", "relaxation_time_s", "time_constant_s", "time_constant",
        ))
        gamma_index = _find_column(headers, (
            "gamma_ohm", "gamma", "drt_gamma", "distribution_amplitude", "gamma/ohm",
        ))
        charge_index = _find_column(headers, (
            "charge_C", "charge", "capacity_C", "capacity",
        ))

        # Infer a missing technique from the actual table structure rather than
        # relying only on filenames or Browser labels.
        if f_index is not None and zr_index is not None and zi_index is not None:
            technique_set.add("EIS")
        elif tau_index is not None and gamma_index is not None:
            technique_set.add("DRT")
        elif t_index is not None and i_index is not None and p_index is not None and not technique_set:
            # Current-vs-time is the safest default for a chrono table that also
            # carries an applied potential setpoint.
            technique_set.add("CA")
        elif t_index is not None and i_index is not None and not technique_set:
            technique_set.add("CA")
        elif t_index is not None and p_index is not None and not technique_set:
            technique_set.add("CP")
        elif p_index is not None and i_index is not None and not technique_set:
            technique_set.add("CV")

        result["detected_techniques"] = sorted(technique_set)

        specs: list[tuple[int, str, str]] = []
        contract = ""
        excluded: list[str] = []
        if technique_set & {"EIS", "GEIS"} and None not in (f_index, zr_index, zi_index):
            specs = [
                (int(f_index), "frequency_Hz", "frequency"),
                (int(zr_index), "z_real_ohm", "impedance"),
                (int(zi_index), "z_imag_ohm", "impedance"),
            ]
            contract = "complex_impedance"
        elif "DRT" in technique_set and None not in (tau_index, gamma_index):
            specs = [(int(tau_index), "tau_s", "time"), (int(gamma_index), "gamma_ohm", "gamma")]
            contract = "distribution_of_relaxation_times"
        elif technique_set & {"CV", "LSV", "DPV", "SWV", "NPV", "STRIPPING", "TAFEL", "IV"} and None not in (p_index, i_index):
            specs = [(int(p_index), "potential_V", "potential"), (int(i_index), "current_A", "current")]
            contract = "voltammetry"
            excluded = [headers[idx] for idx in (t_index, charge_index) if idx is not None]
        elif technique_set & {"CA", "DEPOSITION"} and None not in (t_index, i_index):
            specs = [(int(t_index), "time_s", "time"), (int(i_index), "current_A", "current")]
            contract = "chronoamperometry" if "CA" in technique_set else "deposition_current_transient"
            excluded = [headers[idx] for idx in (p_index,) if idx is not None]
        elif technique_set & {"CP", "GCD", "CHARGE_DISCHARGE"} and None not in (t_index, p_index):
            specs = [(int(t_index), "time_s", "time"), (int(p_index), "potential_V", "potential")]
            contract = "chronopotentiometry"
            excluded = [headers[idx] for idx in (i_index, charge_index) if idx is not None]
        elif "OCP" in technique_set and None not in (t_index, p_index):
            specs = [(int(t_index), "time_s", "time"), (int(p_index), "potential_V", "potential")]
            contract = "open_circuit_potential"
            excluded = [headers[idx] for idx in (i_index,) if idx is not None]
        else:
            # Generic numeric-curve fallback.  Never choose obvious index/control
            # columns as the measured signal.
            normalized = [_normalized_column_name(value) for value in headers]
            excluded_tokens = ("index", "point", "cycle", "method", "segment", "record", "row")
            candidate_indices = [idx for idx, value in enumerate(normalized) if not any(token in value for token in excluded_tokens)]
            independent = next((idx for idx in (t_index, p_index, f_index, tau_index) if idx is not None), None)
            dependent = next((idx for idx in (i_index, gamma_index, p_index, charge_index) if idx is not None and idx != independent), None)
            if independent is None and candidate_indices:
                independent = candidate_indices[0]
            if dependent is None:
                dependent = next((idx for idx in candidate_indices if idx != independent), None)
            if independent is not None and dependent is not None:
                specs = [
                    (int(independent), headers[int(independent)] or "x", "generic"),
                    (int(dependent), headers[int(dependent)] or "y", "generic"),
                ]
                contract = "generic_numeric_curve"

        if not specs:
            shutil.copy2(source, destination)
            result["inspection_warning"] = "No unambiguous technique-specific measurement columns were identified; the original table was copied unchanged."
        else:
            factors = [_unit_factor(headers[index], quantity) for index, _name, quantity in specs]
            with destination.open("w", encoding="utf-8", newline="") as dst:
                writer = csv.writer(dst, lineterminator="\n")
                writer.writerow([name for _index, name, _quantity in specs])
                rows = 0
                skipped_rows = 0
                for row in reader:
                    if any(index >= len(row) for index, _name, _quantity in specs):
                        skipped_rows += 1
                        continue
                    values: list[str] = []
                    valid = True
                    for (index, _name, _quantity), factor in zip(specs, factors):
                        raw = str(row[index]).strip()
                        try:
                            numeric = float(raw) * factor
                        except Exception:
                            valid = False
                            break
                        values.append(raw if factor == 1.0 else repr(numeric))
                    if not valid:
                        skipped_rows += 1
                        continue
                    writer.writerow(values)
                    rows += 1
            result.update({
                "transformation": "lossless_xy_projection" if contract == "voltammetry" else "lossless_technique_projection",
                "technique_contract": contract,
                "scilink_columns": [name for _index, name, _quantity in specs],
                "row_count": rows,
                "skipped_non_numeric_rows": skipped_rows,
                "unit_conversions": {name: factor for (_index, name, _quantity), factor in zip(specs, factors)},
                "excluded_control_columns": [value for value in excluded if value],
            })
            if contract == "complex_impedance":
                result["imaginary_sign_convention"] = "preserved exactly from source; no sign inversion performed"
    result["scilink_sha256"] = _sha256(destination)
    result.update(_filename_series_metadata(source))
    return result


def _spatial_step_mm(text: str) -> float | None:
    match = re.search(r"(?i)(\d+(?:[p.]\d+)?)\s*(?:um|µm)[_-]?step", text)
    if not match:
        match = re.search(r"(?i)(\d+(?:[p.]\d+)?)umstep", text)
    return float(match.group(1).replace("p", ".")) / 1000.0 if match else None


def _series_metadata(files: Sequence[Mapping[str, Any]], experiment_description: str, techniques: Sequence[str]) -> dict[str, Any] | None:
    scan_values = {item["filename"]: item.get("scan_rate_mV_s") for item in files if item.get("scan_rate_mV_s") is not None}
    if scan_values:
        return {"variable": "scan_rate_mV_s", "unit": "mV/s", "values": scan_values}
    points = {item["filename"]: item.get("point_index") for item in files if item.get("point_index") is not None}
    if len(points) > 1:
        search_text = experiment_description + " " + " ".join(str(item.get("filename") or "") for item in files[:20])
        step = _spatial_step_mm(search_text)
        if step is not None:
            first = min(int(v) for v in points.values())
            values = {name: (int(point) - first) * step for name, point in points.items()}
            return {"variable": "position_mm", "unit": "mm", "values": values, "start_point_index": first, "step_mm": step, "source": "point index and experiment-name step"}
        return {"variable": "point_index", "unit": "index", "values": {name: int(point) for name, point in points.items()}, "source": "filename metadata"}
    return None



def _technique_contracts(techniques: Sequence[str]) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    tech = {str(value).upper() for value in techniques}
    if tech & {"CV", "LSV", "DPV", "SWV", "NPV", "STRIPPING", "TAFEL", "IV"}:
        contracts.append({"techniques": sorted(tech & {"CV", "LSV", "DPV", "SWV", "NPV", "STRIPPING", "TAFEL", "IV"}), "columns": ["potential_V", "current_A"], "shape": "N x 2", "x_role": "applied or measured potential", "y_role": "measured current"})
    if tech & {"CA", "DEPOSITION"}:
        contracts.append({"techniques": sorted(tech & {"CA", "DEPOSITION"}), "columns": ["time_s", "current_A"], "shape": "N x 2", "x_role": "time", "y_role": "measured current", "excluded_control_columns": ["point_index", "potential_V", "method", "cycle"]})
    if tech & {"CP", "GCD", "CHARGE_DISCHARGE"}:
        contracts.append({"techniques": sorted(tech & {"CP", "GCD", "CHARGE_DISCHARGE"}), "columns": ["time_s", "potential_V"], "shape": "N x 2", "x_role": "time", "y_role": "measured potential", "excluded_control_columns": ["point_index", "current_A", "method", "cycle"]})
    if "OCP" in tech:
        contracts.append({"techniques": ["OCP"], "columns": ["time_s", "potential_V"], "shape": "N x 2", "x_role": "time", "y_role": "open-circuit potential"})
    if "DRT" in tech:
        contracts.append({"techniques": ["DRT"], "columns": ["tau_s", "gamma_ohm"], "shape": "N x 2", "x_role": "relaxation time", "y_role": "DRT amplitude"})
    if tech & {"EIS", "GEIS"}:
        contracts.append({"techniques": sorted(tech & {"EIS", "GEIS"}), "columns": ["frequency_Hz", "z_real_ohm", "z_imag_ohm"], "shape": "N x 3", "imaginary_sign_convention": "preserved from source"})
    for contract in contracts:
        contract.update({
            "route": "technique_specific_electrochemistry_analysis",
            "generic_curve_fitting_allowed": False,
            "curve_fitting_agent_required": False,
            "spectral_peak_fitting_allowed": False,
            "best_of_n_allowed": False,
            "adaptive_refit_allowed": False,
            "generic_r2_gate_allowed": False,
            "deterministic_analysis_required": True,
        })
    return contracts



def _write_scilink_context(
    job_dir: Path,
    copied_paths: Sequence[Path],
    record_names: Sequence[str],
    experiment_description: str,
    file_provenance: Sequence[Mapping[str, Any]],
) -> Path:
    """Write the sole compact SciLink-visible context JSON."""
    techniques = _infer_techniques(copied_paths, record_names)
    for item in file_provenance:
        for technique in list(item.get("detected_techniques") or []):
            key = str(technique).upper()
            if key and key not in techniques:
                techniques.append(key)
    files: list[dict[str, Any]] = []
    for index, path in enumerate(copied_paths):
        item = _file_structure(path)
        item["relative_path"] = path.name
        if index < len(file_provenance):
            item["provenance"] = dict(file_provenance[index])
        item.update(_filename_series_metadata(path))
        files.append(item)
    series_metadata = _series_metadata(files, experiment_description, techniques)
    payload = {
        "schema": "faradaiq-scilink-context-v262",
        "application": "FaradaIQ Studio",
        "version": VERSION,
        "experiment_description": experiment_description,
        "series_metadata": series_metadata,
        "working_directory": ".",
        "data_access": {"scope": "current job data folder only", "parent_directory_search": False, "metadata_file": "scilink_bundle_context.json"},
        "data_structure": {
            "selection_scope": "current Browser selection only",
            "selected_record_names": list(record_names),
            "detected_techniques": techniques,
            "file_count": len(copied_paths),
            "files": files,
            "series_variable": series_metadata,
            "technique_curve_contracts": _technique_contracts(techniques),
            "curve_agent_matrix_contract": (_technique_contracts(techniques)[0] if _technique_contracts(techniques) else None),
            "curve_agent_matrix_contract_legacy_note": "Legacy shape/column contract only; it does not authorize CurveFittingAgent or generic fitting.",
            "electrochemistry_routing_policy": {
                "route": "technique_specific_electrochemistry_analysis",
                "generic_curve_fitting_allowed": False,
                "curve_fitting_agent_required": False,
                "spectral_peak_fitting_allowed": False,
                "best_of_n_allowed": False,
                "adaptive_refit_allowed": False,
                "generic_r2_gate_allowed": False,
                "deterministic_analysis_required": True,
            },
        },
        "context_policy": "Only the operator-facing Main Studio description and compact structure of this job-local data folder are included. Absolute parent paths, canonical manifests, raw values, and hidden scientific prompts are excluded.",
    }
    path = job_dir / "data" / "scilink_bundle_context.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.resolve()


def _bundle_candidate(
    source: Any,
    source_paths: Sequence[Path] | None = None,
    *,
    record_names_override: Sequence[str] | None = None,
    display_label: str | None = None,
) -> dict[str, Any] | None:
    paths = [Path(p).expanduser().resolve() for p in (source_paths if source_paths is not None else _browser_selection_paths(source))]
    paths = [p for p in paths if p.is_file()]
    if not paths:
        return None
    total = 0
    for path in paths:
        try:
            total += int(path.stat().st_size)
        except Exception:
            pass
    return {
        "path": "",
        "role": "Prepared experimental data bundle",
        "source_group": "Current Browser selection bundle",
        "generated": "browser_selection_bundle",
        "selected": True,
        "size_bytes": total,
        "bundle_source_paths": [str(p) for p in paths],
        "bundle_record_names": list(record_names_override) if record_names_override is not None else None,
        "display_path": (
            str(display_label).strip() if str(display_label or "").strip() else
            f"Automatically prepare {len(paths)} selected file(s) using the detected technique contract, "
            "copy untouched originals to source_data, and create scilink_ready_browser_selection.zip."
        ),
    }


def _context_candidate(source: Any, source_paths: Sequence[Path] | None = None) -> dict[str, Any] | None:
    paths = list(source_paths) if source_paths is not None else _browser_selection_paths(source)
    if not paths:
        return None
    return {
        "path": "",
        "role": "Compact SciLink data-structure JSON",
        "source_group": "Current Browser selection context",
        "generated": "browser_selection_context_json",
        "selected": True,
        "size_bytes": 0,
        "display_path": (
            "Generate scilink_bundle_context.json with the Main Studio experiment description, detected technique, "
            "canonical column roles, units, series metadata, filenames, row counts, and hashes. Raw values and parent paths are excluded."
        ),
    }




def _review_candidates(
    source: Any,
    candidates: Sequence[Mapping[str, Any]],
    *,
    bundle_source_paths: Sequence[Path] | None = None,
    bundle_record_names: Sequence[str] | None = None,
    bundle_display_label: str | None = None,
) -> list[dict[str, Any]]:
    """Show and preselect both generated handoff artifacts.

    The prepared data bundle and compact context JSON are separate, visible
    review items.  Every unrelated file remains unchecked.
    """
    bundle = _bundle_candidate(
        source, bundle_source_paths, record_names_override=bundle_record_names, display_label=bundle_display_label
    )
    context = _context_candidate(source, bundle_source_paths)
    out: list[dict[str, Any]] = []
    if bundle is not None:
        out.append(bundle)
    if context is not None:
        out.append(context)
    for raw in candidates:
        item = dict(raw)
        if bundle is not None and str(item.get("source_group") or "").strip().lower() == "current selection":
            continue
        item["selected"] = False
        out.append(item)
    return out



def _candidate_role(item: Mapping[str, Any]) -> str:
    return str(item.get("role") or "Data file").strip() or "Data file"


def _candidate_group(item: Mapping[str, Any]) -> str:
    return str(item.get("source_group") or item.get("group") or "Other").strip() or "Other"



def _candidate_extension(item: Mapping[str, Any]) -> str:
    generated = str(item.get("generated") or "")
    if generated == "browser_selection_bundle":
        return ".zip + prepared data"
    if generated == "browser_selection_context_json":
        return ".json"
    raw = str(item.get("path") or item.get("display_path") or "")
    return Path(raw).suffix.lower() or "(generated)"



def _unique_destination(folder: Path, index: int, source: Path) -> Path:
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source.stem).strip("._-")[:90] or "data"
    suffix = source.suffix.lower()
    candidate = folder / f"{index:04d}_{safe_stem}{suffix}"
    serial = 2
    while candidate.exists():
        candidate = folder / f"{index:04d}_{safe_stem}_{serial}{suffix}"
        serial += 1
    return candidate


def _copy_file(source: Path, destination_folder: Path, index: int) -> Path:
    destination_folder.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination(destination_folder, index, source)
    shutil.copy2(source, destination)
    return destination.resolve()


def _classify_item(item: Mapping[str, Any], path: Path) -> str:
    role = _candidate_role(item).lower()
    group = _candidate_group(item).lower()
    suffix = path.suffix.lower()
    if suffix in _METADATA_EXTENSIONS or "metadata" in role or "manifest" in role or "metadata" in group:
        return "metadata"
    if suffix in _SUPPORT_EXTENSIONS or any(token in role or token in group for token in ("literature", "figure", "visualization", "support", "reference", "additional")):
        return "support"
    return "data"



def _infer_techniques(paths: Sequence[Path], names: Sequence[str]) -> list[str]:
    text = " ".join([*(p.name for p in paths), *names]).upper()
    mapping = [
        ("DRT", (r"(?<![A-Z0-9])DRT(?![A-Z0-9])", r"DISTRIBUTION OF RELAXATION")),
        ("GEIS", (r"(?<![A-Z0-9])GEIS(?![A-Z0-9])", r"GALVANOSTATIC EIS")),
        ("EIS", (r"(?<![A-Z0-9])EIS(?![A-Z0-9])", r"ELECTROCHEMICAL IMPEDANCE", r"IMPEDANCE SPECTRO")),
        ("OCP", (r"(?<![A-Z0-9])OCP(?![A-Z0-9])", r"OPEN[ -]?CIRCUIT")),
        ("CA", (r"(?<![A-Z0-9])CA(?![A-Z0-9])", r"CHRONOAMPER", r"CHRONOPOTSTAT", r"CURRENT VS TIME")),
        ("DEPOSITION", (r"(?<![A-Z0-9])DEPOSITION(?![A-Z0-9])", r"ELECTRODEPOSITION")),
        ("CP", (r"(?<![A-Z0-9])CP(?![A-Z0-9])", r"CHRONOPOTENTIO", r"POTENTIAL VS TIME")),
        ("GCD", (r"(?<![A-Z0-9])GCD(?![A-Z0-9])", r"GALVANOSTATIC CHARGE", r"CHARGE[ -]?DISCHARGE")),
        ("CV", (r"(?<![A-Z0-9])CV(?![A-Z0-9])", r"CYCLIC VOLTAM")),
        ("LSV", (r"(?<![A-Z0-9])LSV(?![A-Z0-9])", r"LINEAR SWEEP")),
        ("DPV", (r"(?<![A-Z0-9])DPV(?![A-Z0-9])", r"DIFFERENTIAL PULSE")),
        ("SWV", (r"(?<![A-Z0-9])SWV(?![A-Z0-9])", r"SQUARE WAVE")),
        ("NPV", (r"(?<![A-Z0-9])NPV(?![A-Z0-9])", r"NORMAL PULSE")),
        ("STRIPPING", (r"STRIPPING",)),
        ("TAFEL", (r"TAFEL",)),
        ("IV", (r"(?<![A-Z0-9])I[ _-]?V(?![A-Z0-9])", r"CURRENT[ -]?VOLTAGE")),
    ]
    techniques: list[str] = []
    for key, patterns in mapping:
        if any(re.search(pattern, text) for pattern in patterns):
            techniques.append(key)
    return techniques



def _write_bundle_zip(
    job_dir: Path,
    data_paths: Sequence[Path],
    metadata_paths: Sequence[Path],
    support_paths: Sequence[Path],
    source_paths: Sequence[Path] = (),
) -> Path:
    zip_path = job_dir / "scilink_ready_browser_selection.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in data_paths:
            zf.write(path, f"data/{path.name}")
        for path in metadata_paths:
            if path.parent.name == "data":
                zf.write(path, f"data/{path.name}")
            else:
                zf.write(path, f"metadata/{path.name}")
        for path in support_paths:
            zf.write(path, f"supporting/{path.name}")
        for path in source_paths:
            zf.write(path, f"source_data/{path.name}")
    return zip_path.resolve()



def _materialize_selected_files(
    source: Any,
    selected: Sequence[Mapping[str, Any]],
    job_dir: Path,
    *,
    include_short_name_details: bool = False,
    initial_name_aliases: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[str], list[str], list[str], Path | None] | tuple[list[str], list[str], list[str], Path | None, dict[str, Any], Path | None]:
    """Create the reviewed, job-local SciLink dataset and context artifacts."""
    data_dir = job_dir / "data"
    source_dir = job_dir / "source_data"
    support_dir = job_dir / "supporting"
    data_dir.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)
    support_dir.mkdir(parents=True, exist_ok=True)
    data_paths: list[Path] = []
    source_paths: list[Path] = []
    support_paths: list[Path] = []
    provenance: list[dict[str, Any]] = []
    counters = {"data": 0, "support": 0}
    record_names = _selected_record_names(source)
    for _item in selected:
        if str(_item.get("generated") or "").strip().lower() == "browser_selection_bundle" and _item.get("bundle_record_names") is not None:
            record_names = [str(value) for value in list(_item.get("bundle_record_names") or []) if str(value).strip()]
            break
    generated_review_items_present = any(
        str(item.get("generated") or "").strip().lower() in {"browser_selection_bundle", "browser_selection_context_json"}
        for item in selected
    )
    bundle_selected = any(str(item.get("generated") or "").strip().lower() == "browser_selection_bundle" for item in selected) or not generated_review_items_present
    context_selected = (
        any(str(item.get("generated") or "").strip().lower() == "browser_selection_context_json" for item in selected)
        or bundle_selected
        or not generated_review_items_present
    )

    def add_data(path: Path) -> None:
        counters["data"] += 1
        original_copy = _copy_file(path, source_dir, counters["data"])
        source_paths.append(original_copy)
        destination = _unique_destination(data_dir, counters["data"], path)
        file_techniques = _infer_techniques([path], record_names)
        info = _prepare_scilink_table(path, destination, file_techniques)
        data_paths.append(destination.resolve())
        provenance.append(info)

    def add_support(path: Path) -> None:
        counters["support"] += 1
        support_paths.append(_copy_file(path, support_dir, counters["support"]))

    for item in selected:
        generated = str(item.get("generated") or "").strip().lower()
        if generated == "browser_selection_bundle":
            for raw in list(item.get("bundle_source_paths") or []):
                path = Path(str(raw)).expanduser().resolve()
                if path.is_file() and path.suffix.lower() not in _METADATA_EXTENSIONS:
                    add_data(path)
            continue
        if generated in {"browser_selection_context_json", "session_metadata"}:
            continue
        raw = str(item.get("path") or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            continue
        kind = _classify_item(item, path)
        if kind == "metadata":
            # Large or arbitrary metadata files are intentionally not forwarded.
            # FaradaIQ generates one compact, sanitized context JSON from the
            # reviewed data selection and Main Studio experiment description.
            continue
        add_data(path) if kind == "data" else add_support(path)

    metadata_paths: list[Path] = []
    if data_paths and context_selected:
        context_path = _write_scilink_context(
            job_dir, data_paths, record_names, _experiment_description(source), provenance
        )
        metadata_paths.append(context_path)

    # v273: every SciLink handoff explicitly records original and generated
    # filenames. The map is also included in the ZIP so SciLink never has to
    # infer whether a short filename represents a different source file.
    try:
        from faradaiq.v290_short_names import alias_entry, policy_payload, write_policy_json
        name_aliases = [dict(row) for row in initial_name_aliases if row]
        for info in provenance:
            row = alias_entry(
                info.get("source_filename", ""),
                info.get("generated_filename", ""),
                kind="scilink_prepared_data",
                context="data",
            )
            if row:
                name_aliases.append(row)
        for source_path in source_paths:
            # The prefixed source_data name is intentionally explicit even when
            # the source stem itself did not require hashing.
            source_original = next((x.get("source_filename") for x in provenance if str(source_path.name).endswith(str(x.get("source_filename", "")))), "")
            row = alias_entry(source_original or source_path.name, source_path.name, kind="scilink_source_copy", context="source_data")
            if row:
                name_aliases.append(row)
        short_name_policy = policy_payload(aliases=name_aliases, scope="scilink_handoff")
        short_name_map_path = write_policy_json(
            job_dir, name_aliases, filename="short_name_map.json", scope="scilink_handoff"
        )
        if metadata_paths:
            try:
                context_payload = json.loads(metadata_paths[0].read_text(encoding="utf-8"))
                context_payload["short_name_policy"] = short_name_policy
                context_payload["short_name_map_filename"] = short_name_map_path.name
                context_payload["filename_interpretation"] = (
                    "Generated filenames may be shortened. Use short_name_policy.name_aliases "
                    "or short_name_map.json in the bundle to recover original source names."
                )
                metadata_paths[0].write_text(
                    json.dumps(context_payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                pass
    except Exception:
        name_aliases = []
        short_name_policy = {"enabled": False, "name_aliases": []}
        short_name_map_path = None

    if data_paths:
        validation = {
            "schema": "faradaiq-scilink-data-preflight-v262",
            "version": VERSION,
            "status": "ready" if metadata_paths else "ready_without_generated_context",
            "working_directory": str(data_dir.resolve()),
            "data_file_count": len(data_paths),
            "bundle_selected_in_review": bundle_selected,
            "context_json_selected_in_review": context_selected,
            "metadata_file": str(metadata_paths[0]) if metadata_paths else "",
            "all_data_files_exist": all(path.is_file() and path.stat().st_size > 0 for path in data_paths),
            "parent_paths_exposed_to_scilink": False,
            "short_name_policy": short_name_policy,
            "short_name_map_path": str(short_name_map_path) if short_name_map_path else "",
            "files": [
                {
                    "filename": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                    "provenance": provenance[index],
                }
                for index, path in enumerate(data_paths)
            ],
        }
        (job_dir / "scilink_data_preflight.json").write_text(
            json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    zip_metadata_paths = list(metadata_paths)
    if short_name_map_path is not None:
        zip_metadata_paths.append(short_name_map_path)
    bundle_zip = _write_bundle_zip(
        job_dir, data_paths, zip_metadata_paths, support_paths, source_paths
    ) if bundle_selected and (data_paths or support_paths) else None
    legacy_result = (
        [str(path) for path in data_paths],
        [str(path) for path in support_paths],
        [str(path) for path in metadata_paths],
        bundle_zip,
    )
    if include_short_name_details:
        return (*legacy_result, short_name_policy, short_name_map_path)
    return legacy_result



def write_scilink_job(
    source: Any,
    exact_prompt: str,
    selected: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
    *,
    mode: str = "autonomous",
    requested_filenames: Sequence[str] | None = None,
    unsafe_execution_ok: bool | None = None,
    literature_analysis_enabled: bool | None = None,
    labric_cloud_context_enabled: bool | None = None,
    wait_for_user_questions: bool | None = None,
    deterministic_electrochemistry_enabled: bool | None = None,
) -> dict[str, Any]:
    """Create one self-contained SciLink job while preserving the exact prompt."""
    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_id = f"scilink_{stamp}_{uuid.uuid4().hex[:6]}"
    base = old._safe_name(old._base_name(_gui_from(source)))
    original_job_folder = f"{base}_{job_id}"
    initial_name_aliases: list[dict[str, Any]] = []
    try:
        from faradaiq.v290_short_names import MAX_SUBFOLDER_CHARS, alias_entry, is_enabled, short_component
        generated_job_folder = (
            short_component(original_job_folder, max_chars=MAX_SUBFOLDER_CHARS, default="scilink_job")
            if is_enabled() else original_job_folder
        )
        folder_alias = alias_entry(original_job_folder, generated_job_folder, kind="scilink_job_folder", context="job_root")
        if folder_alias:
            initial_name_aliases.append(folder_alias)
    except Exception:
        generated_job_folder = original_job_folder
    job_dir = old._job_root(source) / generated_job_folder
    job_dir.mkdir(parents=True, exist_ok=False)

    prompt_path = job_dir / "analysis_prompt.md"
    prompt_path.write_text(str(exact_prompt), encoding="utf-8")

    data_paths, supporting_paths, metadata_paths, bundle_zip, short_name_policy, short_name_map_path = _materialize_selected_files(
        source, selected, job_dir, include_short_name_details=True, initial_name_aliases=initial_name_aliases
    )
    request = {
        "selected_data_paths": data_paths,
        "supporting_file_paths": supporting_paths,
        "metadata_paths": metadata_paths,
        "output_folder": str(job_dir.resolve()),
        "selected_record_names": _selected_record_names(source),
        "requested_filenames": [str(value).strip() for value in (requested_filenames or []) if str(value).strip()],
    }
    request_path = job_dir / "scilink_request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")

    settings = dict(cleanup._service_settings(cfg))
    # v262 always unlocks generated-code execution for the launched SciLink
    # child process. The optional argument remains for backward compatibility
    # but cannot disable the process-local override.
    authorized_unsafe_execution = True
    literature_enabled = bool(
        cfg.get("scilink_literature_analysis_enabled", True)
        if literature_analysis_enabled is None else literature_analysis_enabled
    )
    labric_cloud_enabled = bool(
        cfg.get("scilink_labric_cloud_context_enabled", cfg.get("labric_mcp_enabled", False))
        if labric_cloud_context_enabled is None else labric_cloud_context_enabled
    )
    wait_for_user = bool(
        cfg.get("scilink_wait_for_user_questions", True)
        if wait_for_user_questions is None else wait_for_user_questions
    )
    raw_mode = str(mode or "co-pilot").strip().lower().replace("_", "-")
    if raw_mode.startswith("co"):
        requested_mode = "co-pilot"
    elif raw_mode in {"auto-pilot", "autopilot"} or raw_mode.startswith("auto-p"):
        requested_mode = "autopilot"
    else:
        requested_mode = "autonomous"
    # Interaction policy and SciLink autonomy are independent controls.  The
    # question gate can remain enabled in Auto-pilot or Autonomous mode without
    # silently rewriting the operator's reviewed mode back to Co-pilot.
    effective_mode = requested_mode
    settings.update({
        "scilink_mode": effective_mode,
        "scilink_requested_mode": requested_mode,
        "scilink_wait_for_user_questions": wait_for_user,
        "scilink_human_feedback_required": wait_for_user,
        "scilink_auto_continue_after_question": not wait_for_user,
        "inject_faradaiq_skill_prompt": False,
        "handoff_contract": "exact_user_prompt_plus_job_local_request_json",
        "job_local_data_bundle": bool(data_paths),
        "scilink_unsafe_execution_ok": True,
        "unsafe_execution_authorization_scope": "current SciLink CLI subprocess only",
        "scilink_literature_analysis_enabled": literature_enabled,
        "scilink_labric_cloud_context_enabled": labric_cloud_enabled,
        "labric_mcp_enabled": labric_cloud_enabled,
        "labric_mcp_transport": "stdio",
        "labric_mcp_server_name": "labric",
        "labric_mcp_package_local": True,
        "edison_enabled": True,
        "direct_edison_enabled": bool(settings.get("direct_edison_enabled", False)),
        "unsafe_execution_environment_variable": "UNSAFE_EXECUTION_OK",
        "scilink_auto_repair_dependencies": True,
        "scilink_curvefit_data_contract": "job-local validated x/y tables",
        "short_generated_names_enabled": bool(short_name_policy.get("enabled", False)),
        "short_name_map_path": str(short_name_map_path) if short_name_map_path else "",
        "short_name_mapping_required_for_interpretation": True,
        "deterministic_electrochemistry_enabled": bool(deterministic_electrochemistry_enabled),
        "electrochemistry_analysis_mode": "optional_deterministic" if bool(deterministic_electrochemistry_enabled) else "standard_scilink",
    })
    settings_path = job_dir / "service_settings.json"
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_path = Path(metadata_paths[0]) if metadata_paths else None

    return {
        "job_id": job_id,
        "job_dir": job_dir,
        "prompt_path": prompt_path,
        "manifest_path": request_path,
        "request_path": request_path,
        "settings_path": settings_path,
        "manifest": request,
        "settings": settings,
        "bundle_zip": bundle_zip,
        "bundle_summary_path": summary_path,
        "short_name_map_path": short_name_map_path,
        "short_name_policy": short_name_policy,
    }



def _preview_request(source: Any, selected: Sequence[Mapping[str, Any]], requested: Sequence[str]) -> dict[str, Any]:
    data: list[str] = []
    support: list[str] = []
    metadata: list[str] = []
    bundles: list[str] = []
    for item in selected:
        generated = str(item.get("generated") or "").strip().lower()
        if generated == "browser_selection_bundle":
            bundles.append("<new SciLink job>/scilink_ready_browser_selection.zip")
            for index, raw in enumerate(list(item.get("bundle_source_paths") or []), start=1):
                path = Path(str(raw))
                safe = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._-")[:90] or "data"
                data.append(f"<new SciLink job>/data/{index:04d}_{safe}{path.suffix.lower()}")
            continue
        if generated == "browser_selection_context_json":
            metadata.append("<new SciLink job>/data/scilink_bundle_context.json")
            continue
        if generated == "session_metadata":
            continue
        raw = str(item.get("path") or "").strip()
        if not raw:
            continue
        path = Path(raw)
        kind = _classify_item(item, path)
        target = f"<new SciLink job>/{'data' if kind == 'data' else 'supporting'}/{path.name}"
        (data if kind == "data" else support).append(target)
    return {
        "selected_bundle_paths": bundles,
        "selected_data_paths": data,
        "supporting_file_paths": support,
        "metadata_paths": metadata,
        "output_folder": "<new SciLink job folder created after Start>",
        "selected_record_names": _selected_record_names(source),
        "requested_filenames": [str(value).strip() for value in requested if str(value).strip()],
    }




_TECHNIQUE_PROMPTS: dict[str, str] = {
    "General quantitative analysis": "Inspect the selected data structure and measurement quality first. Perform only analyses supported by the supplied columns and metadata. Report assumptions, units, uncertainty, failed or excluded records, and reproducible output files.",
    "EIS quality, circuit, and DRT analysis": "For the selected EIS data, verify frequency ordering, sign convention, duplicates, non-finite values, inductive artifacts, frequency coverage, and Kramers-Kronig consistency where supported. Compare physically plausible equivalent-circuit models without overfitting, report parameter uncertainties and residuals, and calculate or compare DRT features consistently across the selected records.",
    "DRT peak and spatial analysis": "Analyze the selected DRT curves consistently. Extract peak time constants, characteristic frequencies, amplitudes, integrated areas, widths, uncertainties, and fit quality. Compare the features across points or positions, identify gradients and outliers, and distinguish plausible heterogeneity from noise, incomplete frequency coverage, or fitting artifacts.",
    "CV/LSV transport and kinetics": "Analyze the selected CV or LSV curves using the supplied potential_V and current_A columns. Extract peak potentials, currents, peak separation, onset metrics, cycle stability, and scan-rate trends. Estimate diffusion coefficient and heterogeneous electron-transfer rate only when the required geometry, concentration, temperature, and scan-rate metadata are available; state the equation, assumptions, units, uncertainty, and applicability limits.",
    "Pulse and stripping voltammetry": "Analyze the selected DPV, SWV, NPV, or stripping-voltammetry curves using potential_V and current_A. Apply a stated baseline method, identify and quantify statistically supported peaks, report peak potential, height, area, width, detection limits where calibration data exist, replicate precision, interferences, and excluded records. Do not infer concentration without an applicable calibration or standard-addition model.",
    "Tafel and polarization analysis": "Analyze the selected polarization or Tafel data using potential_V and current_A. Identify cathodic and anodic branches, state the selected linear windows, fit log10 absolute current only where the assumptions are defensible, report slopes, exchange-current or corrosion-current estimates, intercept uncertainty, residuals, and sensitivity to the fit window. Do not fit through current zero or mix transport-limited and kinetic regions.",
    "CA charge and transient metrics": "Analyze the selected chronoamperometry curves using time_s as the independent variable and current_A as the measured signal. Integrate signed, cathodic, anodic, and absolute charge; extract initial, peak, steady-state, and decay metrics; compare points or positions; test Cottrell and exponential/relaxation behavior; report fit windows, residuals, uncertainty, outliers, and where each model is not applicable.",
    "CA Scharifker-Hills nucleation analysis": "For the selected electrodeposition chronoamperometry transients, calculate charge and baseline-corrected current, identify whether a resolved interior current maximum exists, and compare the dimensionless Scharifker-Hills instantaneous and progressive three-dimensional diffusion-controlled nucleation curves only when their assumptions are met. Report t_max, I_max, dimensionless-fit errors, classification confidence, spatial trends, and explicit not-applicable reasons. Also compare Cottrell and exponential alternatives without forcing a nucleation interpretation.",
    "CP transition-time and stability analysis": "Analyze the selected chronopotentiometry curves using time_s and potential_V. Extract initial and final potential, IR step, drift, transition time, derivative extrema, plateaus, oscillation/noise, and spatial or cycle trends. Calculate charge or capacity only when the applied current is supplied in metadata or source columns, and state all assumptions and units.",
    "Galvanostatic charge-discharge analysis": "Analyze the selected GCD or charge-discharge traces using time_s and potential_V. Separate charge and discharge segments, calculate transition times, voltage plateaus, IR drop, coulombic efficiency, capacity, energy, and power only when current, mass, area, and voltage conventions are available. Report cycle retention, uncertainties, outliers, and any normalization assumptions.",
    "OCP stability and drift analysis": "Analyze the selected open-circuit-potential traces for equilibration time, drift rate, noise, jumps, outliers, and stability across points or cycles. Use robust statistics, preserve the acquisition order, and report uncertainty and excluded intervals.",
    "SECCM spatial comparison": "Treat the selected files as a spatial SECCM series using the explicit series_metadata supplied by FaradaIQ. Calculate technique-appropriate metrics per point, make ranked tables and spatial trends, compare defined experimental regions, and distinguish genuine spatial changes from contact, meniscus, geometry, drift, and noise artifacts.",
}


_V290_PROMPT_BASE = (
    "Use the attached electrochemistry skill, preserve cycles, sweeps, stages, points, units, and provenance, "
    "start with direct electrochemical observables, and do not invent missing metadata. "
)
_TECHNIQUE_PROMPTS.update({
    "General quantitative analysis": _V290_PROMPT_BASE + "Identify the technique and use fitting, simulation, generated code, literature, or alternative SciLink tools when scientifically useful. Separate measured, calculated, fitted, and interpreted results.",
    "EIS quality, circuit, and DRT analysis": _V290_PROMPT_BASE + "Analyze EIS or GEIS with both real and imaginary impedance, validate signs and frequency order, compare physically justified circuits and residuals, and run DRT only when linked impedance data support it.",
    "DRT peak and spatial analysis": _V290_PROMPT_BASE + "Analyze DRT only with linked EIS or GEIS, report regularization and reconstruction residuals, and treat process assignments as hypotheses unless independently supported.",
    "CV/LSV transport and kinetics": _V290_PROMPT_BASE + "For CV or LSV, preserve sweep direction and cycles, extract direct peaks, onsets, charge, hysteresis, stability, and scan-rate trends; estimate transport or kinetics only when required metadata and assumptions are available.",
    "Pulse and stripping voltammetry": _V290_PROMPT_BASE + "For DPV, SWV, NPV, or stripping data, apply pulse-appropriate baselines, quantify supported peaks and charge, and report repeatability, calibration limits, and deposition history where available.",
    "Tafel and polarization analysis": _V290_PROMPT_BASE + "For Tafel or polarization data, select defensible kinetic windows, preserve current sign, report iR treatment, slopes, uncertainty, and residuals, and avoid mixing kinetic and transport-limited regions.",
    "CA charge and transient metrics": _V290_PROMPT_BASE + "For CA, quantify current transients and charge first, then assess Cottrell, relaxation, nucleation, or growth models only when their assumptions and metadata are satisfied.",
    "CA Scharifker-Hills nucleation analysis": _V290_PROMPT_BASE + "For electrodeposition CA, calculate direct transient metrics and charge first, then evaluate Scharifker-Hills, Scharifker-Mostany, Heerman-Tarallo, Bewick-Fleischmann-Thirsk, Cottrell, or Avrami models only when eligible; report non-uniqueness and residuals.",
    "CP transition-time and stability analysis": _V290_PROMPT_BASE + "For CP, quantify potential transients, transition times, stability, resistance-related features, and transport interpretation using the applied-current metadata and stated assumptions.",
    "Galvanostatic charge-discharge analysis": _V290_PROMPT_BASE + "For GCD, preserve charge and discharge segments and calculate capacity, coulombic efficiency, energy, power, and retention only when current and normalization metadata are available.",
    "OCP stability and drift analysis": _V290_PROMPT_BASE + "For OCP, quantify stability, drift, noise, offset, settling, discontinuities, and acquisition-order effects before considering any relaxation model.",
    "SECCM spatial comparison": _V290_PROMPT_BASE + "For SECCM or spatial electrochemistry, retain exact structured point identity, coordinates only when recorded, acquisition order, and each local technique while comparing spatial metrics and drift.",
})


def _technique_template_names(source: Any) -> list[str]:
    paths = _browser_selection_paths(source)
    techniques = set(_infer_techniques(paths, _selected_record_names(source)))
    names = ["General quantitative analysis"]
    if techniques & {"EIS", "GEIS"}: names.append("EIS quality, circuit, and DRT analysis")
    if "DRT" in techniques: names.append("DRT peak and spatial analysis")
    if techniques & {"CV", "LSV", "IV"}: names.append("CV/LSV transport and kinetics")
    if techniques & {"DPV", "SWV", "NPV", "STRIPPING"}: names.append("Pulse and stripping voltammetry")
    if "TAFEL" in techniques: names.append("Tafel and polarization analysis")
    if techniques & {"CA", "DEPOSITION"}:
        names.extend(["CA charge and transient metrics", "CA Scharifker-Hills nucleation analysis"])
    if "CP" in techniques: names.append("CP transition-time and stability analysis")
    if "GCD" in techniques: names.append("Galvanostatic charge-discharge analysis")
    if "OCP" in techniques: names.append("OCP stability and drift analysis")
    if len(paths) > 1: names.append("SECCM spatial comparison")
    return list(dict.fromkeys(names))


def technique_prompt(name: str) -> str:
    return _TECHNIQUE_PROMPTS.get(str(name), _TECHNIQUE_PROMPTS["General quantitative analysis"])


def open_scilink_with_technique_prompt(source: Any, template_name: str | None = None) -> Any:
    # Synchronize the Analyze-panel summary immediately before opening the review
    # window.  This prevents a stale ``Nothing selected`` label when the Browser
    # tree selection changed while a preview worker was still completing.
    browser = _browser_from(source)
    try:
        refresh = getattr(browser, "_v226_refresh_analysis_context", None)
        if callable(refresh):
            refresh()
    except Exception:
        pass
    names = _technique_template_names(source)
    chosen = template_name if template_name in _TECHNIQUE_PROMPTS else (names[1] if len(names) > 1 else names[0])
    return show_analysis_review(source, "scilink", prompt_override=technique_prompt(chosen))

def show_analysis_review(
    source: Any,
    service: str,
    prompt_override: str | None = None,
    *,
    bundle_source_paths: Sequence[Path] | None = None,
    bundle_record_names: Sequence[str] | None = None,
    bundle_display_label: str | None = None,
) -> Any:
    """Review SciLink with the prepared bundle and compact JSON selected; delegate Edison unchanged."""
    service_name = "SciLink" if service == "scilink" else "Edison Scientific"
    if service != "scilink":
        return _PREVIOUS_REVIEW(source, service, prompt_override=prompt_override)

    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup
    import faradaiq.v250_reliability as reliability

    tk, ttk, ScrolledText = _g("tk"), _g("ttk"), _g("ScrolledText")
    messagebox = _g("messagebox")
    filedialog = _g("filedialog")
    gui = _gui_from(source)
    cfg = old.load_sharing_settings()
    # Open the review window immediately.  Recursive discovery of every analysis
    # output previously ran on the Tk event thread before Toplevel creation, so
    # the click appeared to do nothing for large experiments.  The two required
    # generated handoff items are enough to start a correct SciLink job; optional
    # supporting files can be added explicitly from the Files to Share tab.
    if bundle_source_paths is None and bundle_record_names is None and bundle_display_label is None:
        # Keep the proven default review path unchanged: exactly the two required
        # generated items are created immediately, before any recursive discovery.
        candidates = _review_candidates(source, [])
    else:
        candidates = _review_candidates(
            source, [],
            bundle_source_paths=bundle_source_paths,
            bundle_record_names=bundle_record_names,
            bundle_display_label=bundle_display_label,
        )
    initial_prompt = "" if prompt_override is None else str(prompt_override)
    bundle_present = any(item.get("generated") == "browser_selection_bundle" for item in candidates)
    context_present = any(item.get("generated") == "browser_selection_context_json" for item in candidates)

    browser_parent = _browser_from(source)
    parent = browser_parent if browser_parent is not None else gui
    try:
        if not bool(parent.winfo_exists()):
            parent = gui
    except Exception:
        parent = gui
    win = tk.Toplevel(parent)
    win.title(f"FaradaIQ Studio {VERSION} - Review SciLink")
    win.geometry("1280x900")
    win.minsize(980, 680)
    try:
        win.transient(parent)
    except Exception:
        pass

    def _show_review_in_front() -> None:
        try:
            win.deiconify()
            win.lift()
            win.focus_force()
            win.attributes("-topmost", True)
            win.after(450, lambda: win.attributes("-topmost", False) if win.winfo_exists() else None)
        except Exception:
            pass

    try:
        win.after_idle(_show_review_in_front)
        win.after(100, _show_review_in_front)
    except Exception:
        pass
    win.grid_rowconfigure(0, weight=1)
    win.grid_columnconfigure(0, weight=1)

    # Use a strict grid with a dedicated action row.  Earlier pack-based layouts
    # could leave a themed Notebook or prompt pane over the visible toolbar on
    # some Windows scaling/theme combinations.  The controls looked enabled but
    # mouse events landed on the overlapping pane.  This row is now structurally
    # separate, raised after idle, and uses native Tk buttons for reliable clicks.
    outer = ttk.Frame(win, style="Card.TFrame", padding=(12, 12))
    outer.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
    outer.grid_columnconfigure(0, weight=1)
    outer.grid_rowconfigure(4, weight=1)

    ttk.Label(outer, text="Review SciLink request", style="Section.TLabel").grid(
        row=0, column=0, sticky="w"
    )
    header_text = (
        ((str(bundle_display_label).strip() + " will create a technique-specific prepared-data bundle and a compact data-structure JSON. ")
         if str(bundle_display_label or "").strip() else
         "The current Browser selection will create a technique-specific prepared-data bundle and a compact data-structure JSON. ")
        + "Both generated items are selected automatically. Enter or edit the prompt, review them, then start the job. Other files remain unchecked."
        if bundle_present and context_present else
        "No Browser data file is currently selected. Return to the Browser and select a point, method, group, or data file before starting SciLink."
    )
    ttk.Label(outer, text=header_text, style="Muted.TLabel", wraplength=1180).grid(
        row=1, column=0, sticky="ew", pady=(2, 4)
    )

    selection_paths = [Path(p).expanduser().resolve() for p in bundle_source_paths] if bundle_source_paths is not None else _browser_selection_paths(source)
    selection_names = [str(v) for v in bundle_record_names] if bundle_record_names is not None else _selected_record_names(source)
    selection_techniques = _infer_techniques(selection_paths, selection_names)
    selection_count = len(selection_paths)
    technique_label = ", ".join(selection_techniques) or "Not yet identified"
    if selection_count:
        selected_summary = (
            f"Selected data: {technique_label} • {selection_count} source file{'s' if selection_count != 1 else ''} • "
            "prepared bundle + compact JSON checked\n"
            "Electrochemistry skill: Applied • Analysis mode: Standard SciLink • SciLink tools: Available • "
            f"Detected technique: {technique_label} • SciLink status: Awaiting approval"
        )
    elif bundle_present and context_present:
        selected_summary = (
            "Selected data: Browser selection resolved through the prepared bundle • bundle + compact JSON checked\n"
            "Electrochemistry skill: Applied • Analysis mode: Standard SciLink • SciLink tools: Available • "
            "SciLink status: Awaiting approval"
        )
    else:
        selected_summary = (
            "Selected data: Nothing selected\nElectrochemistry skill: Applied • Analysis mode: Standard SciLink • "
            "SciLink tools: Available • SciLink status: Awaiting selection"
        )
    selected_data_var = tk.StringVar(value=selected_summary)
    ttk.Label(outer, textvariable=selected_data_var, style="Card.TLabel", wraplength=1180).grid(
        row=2, column=0, sticky="ew", pady=(0, 6)
    )

    status_var = tk.StringVar(
        value=(
            "Nothing starts until you approve. SciLink waits for your answers, and generated-code execution is unlocked only for this launched process."
        )
    )
    top_actions = ttk.Frame(outer, style="Card.TFrame", padding=(2, 2))
    top_actions.grid(row=3, column=0, sticky="ew", pady=(0, 8))
    top_actions.grid_columnconfigure(0, weight=1)
    status_label = ttk.Label(top_actions, textvariable=status_var, style="Muted.TLabel", wraplength=820)
    status_label.grid(row=0, column=0, sticky="ew", padx=(10, 10), pady=8)

    # Commands use lambdas so the locally defined start() function is resolved at
    # click time.  Tk does not dispatch user events until this builder returns.
    top_start_button = tk.Button(
        top_actions,
        text="Start SciLink",
        command=lambda: start(),
        takefocus=True,
        padx=18,
        pady=6,
        cursor="hand2",
        default="active",
    )
    top_start_button.grid(row=0, column=1, sticky="e", padx=(0, 6), pady=6)
    top_cancel_button = tk.Button(
        top_actions,
        text="Cancel",
        command=win.destroy,
        takefocus=True,
        padx=14,
        pady=6,
        cursor="hand2",
    )
    top_cancel_button.grid(row=0, column=2, sticky="e", padx=(0, 8), pady=6)

    tabs = ttk.Notebook(outer)
    tabs.grid(row=4, column=0, sticky="nsew")
    prompt_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    files_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    request_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    settings_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    tabs.add(prompt_tab, text="Prompt")
    tabs.add(files_tab, text="Files to Share")
    tabs.add(request_tab, text="Request JSON")
    tabs.add(settings_tab, text="Service Settings")

    template_names = _technique_template_names(source)
    template_frame = ttk.LabelFrame(prompt_tab, text="Technique prompt templates", padding=(8, 6))
    template_frame.pack(fill="x", pady=(0, 8))
    template_top = ttk.Frame(template_frame, style="Card.TFrame")
    template_top.pack(fill="x")
    detected = ", ".join(_infer_techniques(selection_paths, selection_names)) or "not identified"
    ttk.Label(template_top, text=f"Detected selection: {detected}", style="Muted.TLabel").pack(side="left")
    template_name_var = tk.StringVar(value=template_names[1] if len(template_names) > 1 else template_names[0])
    template_combo = ttk.Combobox(template_top, textvariable=template_name_var, values=template_names, state="readonly", width=48)
    template_combo.pack(side="right")
    template_editor = ScrolledText(template_frame, wrap="word", height=6)
    template_editor.pack(fill="x", pady=(5, 5))
    template_editor.insert("1.0", technique_prompt(template_name_var.get()))

    def load_template(*_args: Any) -> None:
        template_editor.delete("1.0", "end")
        template_editor.insert("1.0", technique_prompt(template_name_var.get()))

    def append_template() -> None:
        text = template_editor.get("1.0", "end-1c").strip()
        if not text:
            return
        existing = prompt_box.get("1.0", "end-1c")
        prompt_box.insert("end", ("\n\n" if existing.strip() else "") + text)
        prompt_box.see("end")
        prompt_box.focus_set()

    template_combo.bind("<<ComboboxSelected>>", load_template)
    ttk.Button(template_top, text="Append editable template to prompt", command=append_template).pack(side="right", padx=(6, 8))
    ttk.Label(template_frame, text="Templates are never added silently. Edit the text above, then append it so the complete submitted prompt remains visible and reviewable.", style="Muted.TLabel", wraplength=1120).pack(anchor="w")

    prompt_box = ScrolledText(prompt_tab, wrap="word")
    prompt_box.pack(fill="both", expand=True)
    if initial_prompt:
        prompt_box.insert("1.0", initial_prompt)
    try:
        prompt_box.focus_set()
    except Exception:
        pass

    search_var = tk.StringVar(value="")
    role_var = tk.StringVar(value="All roles")
    group_var = tk.StringVar(value="All groups")
    type_var = tk.StringVar(value="All types")
    selected_only_var = tk.BooleanVar(value=False)
    selected_initial = sum(1 for item in candidates if item.get("selected"))
    summary_var = tk.StringVar(value=f"Selected {selected_initial} of {len(candidates)} item(s).")

    toolbar = ttk.Frame(files_tab, style="Card.TFrame")
    toolbar.pack(fill="x", pady=(0, 6))
    ttk.Label(toolbar, text="Search", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 4))
    ttk.Entry(toolbar, textvariable=search_var, width=32).grid(row=0, column=1, sticky="ew", padx=(0, 6))
    roles = sorted({_candidate_role(item) for item in candidates})
    groups = sorted({_candidate_group(item) for item in candidates})
    types = sorted({_candidate_extension(item) for item in candidates})
    role_combo = ttk.Combobox(toolbar, textvariable=role_var, values=["All roles", *roles], state="readonly", width=24)
    role_combo.grid(row=0, column=2, padx=3)
    group_combo = ttk.Combobox(toolbar, textvariable=group_var, values=["All groups", *groups], state="readonly", width=25)
    group_combo.grid(row=0, column=3, padx=3)
    type_combo = ttk.Combobox(toolbar, textvariable=type_var, values=["All types", *types], state="readonly", width=18)
    type_combo.grid(row=0, column=4, padx=3)
    ttk.Checkbutton(toolbar, text="Selected only", variable=selected_only_var).grid(row=0, column=5, padx=(6, 3))
    toolbar.columnconfigure(1, weight=1)

    actions = ttk.Frame(files_tab, style="Card.TFrame")
    actions.pack(fill="x", pady=(0, 5))
    ttk.Label(actions, textvariable=summary_var, style="Muted.TLabel").pack(side="left", fill="x", expand=True)

    canvas = tk.Canvas(files_tab, highlightthickness=0, borderwidth=0)
    ybar = ttk.Scrollbar(files_tab, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=ybar.set)
    canvas.pack(side="left", fill="both", expand=True)
    ybar.pack(side="right", fill="y")
    inner = ttk.Frame(canvas, style="Card.TFrame")
    inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(inner_id, width=e.width))

    file_rows: list[dict[str, Any]] = []

    def _refresh_filter_choices() -> None:
        role_combo.configure(values=["All roles", *sorted({_candidate_role(entry["item"]) for entry in file_rows})])
        group_combo.configure(values=["All groups", *sorted({_candidate_group(entry["item"]) for entry in file_rows})])
        type_combo.configure(values=["All types", *sorted({_candidate_extension(entry["item"]) for entry in file_rows})])

    def _add_candidate_row(item: Mapping[str, Any], *, selected: bool | None = None) -> None:
        normalized = dict(item)
        if selected is not None:
            normalized["selected"] = bool(selected)
        key = (str(normalized.get("generated") or ""), os.path.normcase(str(normalized.get("path") or "")))
        for entry in file_rows:
            existing = entry["item"]
            existing_key = (str(existing.get("generated") or ""), os.path.normcase(str(existing.get("path") or "")))
            if key == existing_key:
                if selected:
                    entry["var"].set(True)
                return
        var = tk.BooleanVar(value=bool(normalized.get("selected")))
        row = ttk.Frame(inner, style="Card.TFrame", padding=(2, 3))
        row.grid(row=len(file_rows), column=0, sticky="ew")
        row.columnconfigure(2, weight=1)
        display = normalized.get("display_path") or normalized.get("path") or "(generated only if checked)"
        ttk.Checkbutton(row, variable=var).grid(row=0, column=0, sticky="nw", padx=(0, 4))
        ttk.Label(row, text=_candidate_role(normalized), style="Card.TLabel", width=31).grid(row=0, column=1, sticky="nw", padx=(0, 8))
        ttk.Label(row, text=str(display), style="Muted.TLabel", wraplength=720).grid(row=0, column=2, sticky="nw")
        ttk.Label(row, text=old._format_size(normalized.get("size_bytes")), style="Muted.TLabel", width=10).grid(row=0, column=3, sticky="ne", padx=(8, 0))
        file_rows.append({"item": normalized, "var": var, "row": row, "visible": True})
        try:
            var.trace_add("write", lambda *_args: refresh_summary())
        except Exception:
            pass
        _refresh_filter_choices()

    for item in candidates:
        _add_candidate_row(item)
    inner.columnconfigure(0, weight=1)

    def add_supporting_files() -> None:
        if filedialog is None:
            return
        paths = filedialog.askopenfilenames(
            parent=win,
            title="Add supporting files to this SciLink request",
            filetypes=[
                ("Scientific and document files", "*.csv *.tsv *.txt *.asc *.dat *.npy *.npz *.xlsx *.xls *.json *.yaml *.yml *.xml *.png *.jpg *.jpeg *.svg *.pdf *.html *.md *.docx"),
                ("All files", "*.*"),
            ],
        )
        for raw in paths:
            path = Path(str(raw)).expanduser()
            try:
                resolved = path.resolve()
                size = int(resolved.stat().st_size)
            except Exception:
                continue
            _add_candidate_row({
                "path": str(resolved),
                "role": "User-selected supporting file",
                "source_group": "Added in SciLink review",
                "selected": True,
                "size_bytes": size,
                "display_path": str(resolved),
            }, selected=True)
        apply_filter()

    ttk.Button(actions, text="Add supporting files…", command=add_supporting_files).pack(side="right", padx=(6, 0))

    wait_for_user_var = tk.BooleanVar(value=bool(cfg.get("scilink_wait_for_user_questions", True)))
    stored_mode = str(cfg.get("scilink_mode") or "co-pilot").strip().lower().replace("_", "-")
    if stored_mode.startswith("co"):
        initial_mode = "Co-pilot"
    elif stored_mode in {"auto-pilot", "autopilot"} or stored_mode.startswith("auto-p"):
        initial_mode = "Auto-pilot"
    else:
        initial_mode = "Autonomous"
    mode_var = tk.StringVar(value=initial_mode)
    mode_help_var = tk.StringVar(value="")
    mode_row = ttk.Frame(settings_tab, style="Card.TFrame")
    mode_row.pack(fill="x", pady=(0, 8))
    ttk.Label(mode_row, text="SciLink mode", style="Card.TLabel").pack(side="left")
    mode_combo = ttk.Combobox(
        mode_row,
        textvariable=mode_var,
        values=("Co-pilot", "Auto-pilot", "Autonomous"),
        state="readonly",
        width=18,
    )
    mode_combo.pack(side="left", padx=(8, 0))
    ttk.Label(mode_row, textvariable=mode_help_var, style="Muted.TLabel", wraplength=760).pack(side="left", padx=(8, 0), fill="x", expand=True)
    interaction_box = ttk.LabelFrame(settings_tab, text="Interactive questions", padding=(10, 8))
    interaction_box.pack(fill="x", pady=(0, 8))
    ttk.Checkbutton(
        interaction_box,
        text="Pause and wait for my input when SciLink asks a question",
        variable=wait_for_user_var,
    ).pack(anchor="w")
    ttk.Label(
        interaction_box,
        text="Checked by default. SciLink keeps the reviewed Co-pilot, Auto-pilot, or Autonomous mode, but explicit questions pause for your terminal input instead of silently rerunning the sample.",
        style="Muted.TLabel", wraplength=1080,
    ).pack(anchor="w", fill="x", pady=(2, 0))

    def sync_interaction_mode(*_args: Any) -> None:
        descriptions = {
            "Co-pilot": "Human leads and reviews agent choices.",
            "Auto-pilot": "SciLink leads but pauses at review or missing-information gates.",
            "Autonomous": "SciLink runs the workflow automatically; explicit questions still wait when the question gate is checked.",
        }
        suffix = " Questions wait for your terminal input." if wait_for_user_var.get() else " SciLink may continue using available defaults."
        mode_help_var.set(descriptions.get(mode_var.get(), descriptions["Co-pilot"]) + suffix)
        # Never disable or overwrite the operator's reviewed autonomy choice.
        mode_combo.configure(state="readonly")

    try:
        wait_for_user_var.trace_add("write", sync_interaction_mode)
        mode_var.trace_add("write", sync_interaction_mode)
    except Exception:
        pass
    mode_combo.bind("<<ComboboxSelected>>", sync_interaction_mode, add="+")
    sync_interaction_mode()
    deterministic_electrochemistry_var = tk.BooleanVar(value=False)
    deterministic_box = ttk.LabelFrame(settings_tab, text="Electrochemistry analysis", padding=(10, 8))
    deterministic_box.pack(fill="x", pady=(0, 8))
    deterministic_check = ttk.Checkbutton(
        deterministic_box,
        text="Deterministic electrochemistry",
        variable=deterministic_electrochemistry_var,
    )
    deterministic_check.pack(anchor="w")
    try:
        deterministic_check.configure(cursor="hand2")
    except Exception:
        pass
    try:
        tooltip_fn = _g("_v103_add_tooltip") or _g("_v66_add_tooltip")
        if callable(tooltip_fn):
            tooltip_fn(deterministic_check, "Precalculate technique-specific electrochemical metrics before SciLink analysis.")
    except Exception:
        pass
    ttk.Label(
        deterministic_box,
        text="Precalculate technique-specific electrochemical metrics before SciLink analysis.",
        style="Muted.TLabel", wraplength=1080,
    ).pack(anchor="w", fill="x", pady=(2, 0))

    def refresh_analysis_mode_status(*_args: Any) -> None:
        if deterministic_electrochemistry_var.get():
            mode_text = "Electrochemistry skill: Applied • Analysis mode: Deterministic electrochemistry • Deterministic preprocessing: Runs before SciLink"
        else:
            mode_text = "Electrochemistry skill: Applied • Analysis mode: Standard SciLink • SciLink tools: Available"
        prefix = selected_data_var.get().split("\n", 1)[0]
        selected_data_var.set(prefix + "\n" + mode_text + f" • Detected technique: {technique_label} • SciLink status: Awaiting approval")

    try:
        deterministic_electrochemistry_var.trace_add("write", refresh_analysis_mode_status)
    except Exception:
        pass

    literature_analysis_var = tk.BooleanVar(value=bool(cfg.get("scilink_literature_analysis_enabled", True)))
    literature_box = ttk.LabelFrame(settings_tab, text="SciLink literature analysis", padding=(10, 8))
    literature_box.pack(fill="x", pady=(0, 8))
    ttk.Checkbutton(
        literature_box,
        text="Ask SciLink to perform literature analysis and novelty assessment",
        variable=literature_analysis_var,
    ).pack(anchor="w")
    ttk.Label(
        literature_box,
        text="Checked by default. Edison/FutureHouse remains available inside SciLink in every launch mode. Uncheck this only when the current SciLink job should not use literature tools.",
        style="Muted.TLabel", wraplength=1080,
    ).pack(anchor="w", fill="x", pady=(2, 0))

    labric_cloud_var = tk.BooleanVar(value=bool(cfg.get("scilink_labric_cloud_context_enabled", cfg.get("labric_mcp_enabled", False))))
    labric_box = ttk.LabelFrame(settings_tab, text="LabRIC MCP cloud context", padding=(10, 8))
    labric_box.pack(fill="x", pady=(0, 8))
    ttk.Checkbutton(
        labric_box,
        text="Use LabRIC MCP cloud context for cross-instrument comparison, prior-data analysis, and experiment planning",
        variable=labric_cloud_var,
    ).pack(anchor="w")
    ttk.Label(
        labric_box,
        text="When checked, FaradaIQ connects the package-local LabRIC stdio MCP bridge before SciLink begins. SciLink can query only the LabRIC data authorized by your API key. Configure and test the endpoint and key in LLM Settings → Data Sharing.",
        style="Muted.TLabel", wraplength=1080,
    ).pack(anchor="w", fill="x", pady=(2, 0))

    unsafe_execution_var = tk.BooleanVar(value=True)
    # Keep the Tcl variable alive for the lifetime of the review window so the
    # disabled always-on checkbox remains visibly checked and introspectable.
    win._v262_unsafe_execution_var = unsafe_execution_var
    auth_box = ttk.LabelFrame(settings_tab, text="Generated-code execution", padding=(10, 8))
    auth_box.pack(fill="x", pady=(0, 8))
    ttk.Checkbutton(
        auth_box,
        text="SciLink CLI generated-code execution is unlocked",
        variable=unsafe_execution_var, state="disabled",
    ).pack(anchor="w")
    ttk.Label(
        auth_box,
        text="FaradaIQ always sets UNSAFE_EXECUTION_OK=true only in this SciLink CLI subprocess. The exact scientific prompt and global Windows environment are not modified.",
        style="Muted.TLabel", wraplength=1080,
    ).pack(anchor="w", fill="x", pady=(2, 0))
    ttk.Label(settings_tab, text="Requested filenames (optional, one per line)", style="Card.TLabel").pack(anchor="w")
    filenames_box = ScrolledText(settings_tab, wrap="none", height=6)
    filenames_box.pack(fill="x", pady=(2, 8))
    details_box = ScrolledText(settings_tab, wrap="word", height=20)
    details_box.pack(fill="both", expand=True)
    details = cleanup._service_details_text("scilink", cfg)
    details += (
        "\n\nBrowser bundle behavior\n"
        "- The currently selected Browser files are copied into the new job's data folder.\n"
        "- A compact metadata index is generated; the large canonical JSON is not auto-selected.\n"
        "- SciLink starts with its working directory set to the job-local data folder.\n"
        "- The scientific prompt remains exactly the text visible in the Prompt tab.\n"
        "- Co-pilot mode pauses for operator answers instead of automatically rerunning after a question.\n"
        "- Completed SciLink HTML/HTM reports open automatically in the default browser."
    )
    details_box.insert("1.0", details)
    details_box.configure(state="disabled")

    request_box = ScrolledText(request_tab, wrap="word")
    request_box.pack(fill="both", expand=True)
    request_box.configure(state="disabled")

    def requested_names() -> list[str]:
        return [line.strip() for line in filenames_box.get("1.0", "end-1c").splitlines() if line.strip()]

    def visible_rows() -> list[dict[str, Any]]:
        return [entry for entry in file_rows if entry.get("visible")]

    def selected_items() -> list[dict[str, Any]]:
        return [dict(entry["item"]) for entry in file_rows if entry["var"].get()]

    def refresh_summary(*_args: Any) -> None:
        selected = [entry for entry in file_rows if entry["var"].get()]
        total = sum(int(entry["item"].get("size_bytes") or 0) for entry in selected)
        auto_bundle = any(entry["item"].get("generated") == "browser_selection_bundle" for entry in selected)
        auto_context = any(entry["item"].get("generated") == "browser_selection_context_json" for entry in selected)
        suffix = " Prepared bundle and context JSON are selected." if auto_bundle and auto_context else " Prepared bundle is selected." if auto_bundle else " Context JSON is selected." if auto_context else ""
        summary_var.set(
            f"Selected {len(selected)} of {len(file_rows)} item(s), {old._format_size(total)}. "
            f"Visible after filter: {len(visible_rows())}.{suffix}"
        )
        request_box.configure(state="normal")
        request_box.delete("1.0", "end")
        request_box.insert("1.0", json.dumps(_preview_request(source, selected_items(), requested_names()), ensure_ascii=False, indent=2))
        request_box.configure(state="disabled")

    def apply_filter(*_args: Any) -> None:
        query = search_var.get().strip().lower()
        role_filter = role_var.get()
        group_filter = group_var.get()
        type_filter = type_var.get()
        selected_only = bool(selected_only_var.get())
        for entry in file_rows:
            item = entry["item"]
            haystack = " ".join(str(item.get(key) or "") for key in ("role", "source_group", "path", "display_path", "name", "label", "generated")).lower()
            match = (
                (not query or query in haystack)
                and (role_filter == "All roles" or _candidate_role(item) == role_filter)
                and (group_filter == "All groups" or _candidate_group(item) == group_filter)
                and (type_filter == "All types" or _candidate_extension(item) == type_filter)
                and (not selected_only or bool(entry["var"].get()))
            )
            entry["visible"] = bool(match)
            entry["row"].grid() if match else entry["row"].grid_remove()
        refresh_summary()

    def set_rows(rows: Sequence[dict[str, Any]], value: bool) -> None:
        for entry in rows:
            entry["var"].set(value)
        apply_filter()

    ttk.Button(actions, text="Select visible", command=lambda: set_rows(visible_rows(), True)).pack(side="right", padx=(4, 0))
    ttk.Button(actions, text="Clear visible", command=lambda: set_rows(visible_rows(), False)).pack(side="right", padx=(4, 0))
    ttk.Button(actions, text="Select all", command=lambda: set_rows(file_rows, True)).pack(side="right", padx=(4, 0))
    ttk.Button(actions, text="Clear all", command=lambda: set_rows(file_rows, False)).pack(side="right", padx=(4, 0))
    for var in (search_var, role_var, group_var, type_var, selected_only_var):
        try:
            var.trace_add("write", apply_filter)
        except Exception:
            pass
    for entry in file_rows:
        try:
            entry["var"].trace_add("write", refresh_summary)
        except Exception:
            pass
    filenames_box.bind("<KeyRelease>", refresh_summary)
    apply_filter()

    def start() -> None:
        exact_prompt = prompt_box.get("1.0", "end-1c")
        if not exact_prompt.strip():
            messagebox.showwarning("Prompt required", "Enter the SciLink prompt before starting.", parent=win)
            return
        selected = selected_items()
        preview = _preview_request(source, selected, requested_names())
        if not preview.get("selected_data_paths"):
            messagebox.showwarning(
                "Experimental data required",
                "Select at least one Browser data file or keep the automatically generated current-selection data bundle checked before starting SciLink.",
                parent=win,
            )
            tabs.select(files_tab)
            return
        if not preview.get("metadata_paths"):
            messagebox.showwarning(
                "SciLink context JSON required",
                "Keep the automatically generated compact SciLink data-structure JSON checked. It supplies the experiment description, technique contract, units, and series metadata without exposing the large canonical manifest.",
                parent=win,
            )
            tabs.select(files_tab)
            return
        status_var.set("Creating the job-local SciLink data bundle...")
        try:
            job = write_scilink_job(
                source,
                exact_prompt,
                selected,
                cfg,
                mode=mode_var.get(),
                requested_filenames=requested_names(),
                unsafe_execution_ok=True,
                literature_analysis_enabled=bool(literature_analysis_var.get()),
                labric_cloud_context_enabled=bool(labric_cloud_var.get()),
                wait_for_user_questions=bool(wait_for_user_var.get()),
                deterministic_electrochemistry_enabled=bool(deterministic_electrochemistry_var.get()),
            )
            if not job["manifest"].get("selected_data_paths"):
                raise RuntimeError("The reviewed SciLink bundle did not produce any readable experimental data files.")
            win.destroy()
            reliability._launch_scilink_transaction(source, job, cfg)
        except Exception as exc:
            messagebox.showerror("Could not start SciLink", f"{exc}\n\n{traceback.format_exc()}", parent=win)

    top_start_button.configure(command=start)

    def _raise_action_bar() -> None:
        try:
            top_actions.lift()
            top_start_button.lift()
            top_cancel_button.lift()
        except Exception:
            pass

    try:
        win.bind("<Control-Return>", lambda _event: start(), add="+")
        win.bind("<Escape>", lambda _event: win.destroy(), add="+")
        top_start_button.bind("<Return>", lambda _event: start(), add="+")
        top_cancel_button.bind("<Return>", lambda _event: win.destroy(), add="+")
        win.after_idle(_raise_action_bar)
        win.after(150, _raise_action_bar)
    except Exception:
        pass

    # Stable names support UI automation and make the fixed top action bar and
    # synchronized selection summary easy to verify without depending on
    # theme-specific widget paths.
    win._faradaiq_scilink_action_bar = top_actions
    win._faradaiq_scilink_start_button = top_start_button
    win._faradaiq_scilink_cancel_button = top_cancel_button
    win._faradaiq_scilink_status_var = status_var
    win._faradaiq_scilink_selected_data_var = selected_data_var
    win._faradaiq_scilink_mode_var = mode_var
    win._faradaiq_scilink_mode_combo = mode_combo
    win._faradaiq_scilink_wait_for_user_var = wait_for_user_var
    win._faradaiq_scilink_deterministic_electrochemistry_var = deterministic_electrochemistry_var
    win._faradaiq_scilink_deterministic_electrochemistry_check = deterministic_check
    win._faradaiq_scilink_parent = parent
    win._faradaiq_scilink_prompt_box = prompt_box
    win._faradaiq_scilink_file_rows = file_rows
    win._faradaiq_scilink_add_supporting_files = add_supporting_files
    return win


def _inject_frame_controls(path: Path, payload: Mapping[str, Any]) -> None:
    """Inject a drag-safe manual frame slider and deterministic playback controls."""
    if not payload.get("frame_names"):
        return
    try:
        html = path.read_text(encoding="utf-8")
    except Exception:
        return
    if "faradaiq-frame-dock-v262" in html:
        return
    # Remove the immediately preceding fixed dock so there is only one control surface.
    if "faradaiq-frame-dock-v255" in html:
        start = html.find('<div id="faradaiq-frame-dock-v255"')
        end = html.find('</style>', start)
        if start >= 0 and end >= 0:
            html = html[:start] + html[end + len('</style>'):]
        script_start = html.find('<script id="faradaiq-frame-script-v255"')
        script_end = html.find('</script>', script_start)
        if script_start >= 0 and script_end >= 0:
            html = html[:script_start] + html[script_end + len('</script>'):]

    payload_json = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
    dock = r'''
<div id="faradaiq-frame-dock-v262" role="group" aria-label="Hyperspectral frame controls">
  <button id="faradaiq-frame-play-v262" type="button">▶ Play</button>
  <button id="faradaiq-frame-pause-v262" type="button">❚❚ Pause</button>
  <button id="faradaiq-frame-prev-v262" type="button" title="Previous frame">◀ Previous</button>
  <label id="faradaiq-frame-caption-v262" for="faradaiq-frame-range-v262">Frame</label>
  <input id="faradaiq-frame-range-v262" type="range" min="0" max="0" value="0" step="1" aria-label="Drag to choose hyperspectral frame">
  <button id="faradaiq-frame-next-v262" type="button" title="Next frame">Next ▶</button>
  <output id="faradaiq-frame-value-v262">1 / 1</output>
</div>
<style id="faradaiq-frame-style-v262">
html,body{min-height:100%;}body{padding-bottom:76px!important;box-sizing:border-box;}
#faradaiq-frame-dock-v262{position:fixed;left:0;right:0;bottom:0;z-index:2147483000;display:grid;grid-template-columns:auto auto auto minmax(150px,auto) minmax(280px,1fr) auto auto;align-items:center;gap:8px;padding:9px 12px;border-top:1px solid #9ba9bc;background:rgba(247,249,252,.99);box-shadow:0 -2px 10px rgba(20,42,70,.18);color:#17365d;font:14px/1.25 Arial,sans-serif;box-sizing:border-box;pointer-events:auto}
#faradaiq-frame-dock-v262 button{font:inherit;padding:6px 10px;border:1px solid #9ba9bc;border-radius:4px;background:#fff;color:#17365d;cursor:pointer;font-weight:600;pointer-events:auto}
#faradaiq-frame-dock-v262 button:disabled{opacity:.55;cursor:default}
#faradaiq-frame-range-v262{display:block;width:100%;min-width:220px;height:28px;accent-color:#315b8a;cursor:grab;pointer-events:auto;touch-action:none;position:relative;z-index:2147483001}
#faradaiq-frame-range-v262:active{cursor:grabbing}
#faradaiq-frame-caption-v262{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}
#faradaiq-frame-value-v262{white-space:nowrap;min-width:76px;text-align:right}
@media(max-width:900px){#faradaiq-frame-dock-v262{grid-template-columns:auto auto auto 1fr auto auto}#faradaiq-frame-caption-v262{grid-column:1/-1;grid-row:1}.plotly-graph-div{max-width:100%!important}}
</style>
'''
    script = r'''
<script id="faradaiq-frame-script-v262">
(function(){
  const payload=__PAYLOAD__,names=Array.isArray(payload.frame_names)?payload.frame_names:[],labels=Array.isArray(payload.frame_labels)?payload.frame_labels:[];
  const range=document.getElementById('faradaiq-frame-range-v262'),caption=document.getElementById('faradaiq-frame-caption-v262'),value=document.getElementById('faradaiq-frame-value-v262');
  const play=document.getElementById('faradaiq-frame-play-v262'),pause=document.getElementById('faradaiq-frame-pause-v262');
  const prev=document.getElementById('faradaiq-frame-prev-v262'),next=document.getElementById('faradaiq-frame-next-v262');
  let index=0,playing=false,token=0,dragging=false,lastRendered=-1;
  function graph(){return document.querySelector('.plotly-graph-div');}
  function finiteZ(trace){if(!trace||trace.z==null)return false;const stack=[trace.z];while(stack.length){const item=stack.pop();if(Array.isArray(item)){for(let i=0;i<item.length;i++)stack.push(item[i]);}else if(Number.isFinite(Number(item)))return true;}return false;}
  function clamp(i){return Math.max(0,Math.min(names.length-1,Number(i)||0));}
  function sync(rendered){index=clamp(index);range.value=String(index);caption.textContent=labels[index]||('Frame '+(index+1));value.textContent=(index+1)+' / '+names.length+(dragging?' • release to render':'');play.disabled=playing||names.length<2;pause.disabled=!playing;prev.disabled=names.length<2;next.disabled=names.length<2;if(rendered)lastRendered=index;}
  function ensureSpatialLayer(gd){
    try{
      const visible=(gd.data||[]).some(t=>['contour','heatmap','surface','mesh3d'].includes(String(t.type||'').toLowerCase())&&finiteZ(t));
      if(!visible){
        const frames=(gd._transitionData&&gd._transitionData._frames)||[],frame=frames.find(f=>String(f.name||'')===String(names[index]||''));
        if(frame&&frame.data){const jobs=[];frame.data.forEach((t,i)=>{if(['contour','heatmap','surface','mesh3d'].includes(String(t.type||'').toLowerCase())&&finiteZ(t))jobs.push(Plotly.restyle(gd,{visible:true,z:[t.z]},[i]));});if(jobs.length)return Promise.all(jobs).then(()=>Plotly.redraw(gd));}
      }
      return Plotly.redraw(gd);
    }catch(_){return Promise.resolve();}
  }
  function announce(gd){const name=String(names[index]||'');try{gd._faradaiqActiveFrameName=name;if(gd.dataset)gd.dataset.faradaiqFrameName=name;}catch(_){}try{gd.dispatchEvent(new CustomEvent('faradaiq_frame_changed',{detail:{name:name,index:index,label:labels[index]||''}}));}catch(_){}}
  function show(i){
    const gd=graph();if(!gd||typeof Plotly==='undefined'||!names.length)return Promise.resolve();
    index=clamp(i);dragging=false;sync(false);
    return Plotly.animate(gd,[names[index]],{mode:'immediate',frame:{duration:0,redraw:true},transition:{duration:0}})
      .then(()=>ensureSpatialLayer(gd)).then(()=>{sync(true);announce(gd);})
      .catch(()=>{try{return Plotly.redraw(gd).then(()=>{sync(true);announce(gd);});}catch(_){sync(true);announce(gd);}});
  }
  function stop(){playing=false;token++;sync(false);}
  function run(my){if(!playing||my!==token)return;const target=(index+1)%names.length;show(target).finally(()=>{if(playing&&my===token)setTimeout(()=>run(my),Math.max(80,Number(payload.frame_duration_ms)||280));});}
  function beginDrag(){stop();dragging=true;index=clamp(range.value);sync(false);}
  function previewDrag(){dragging=true;index=clamp(range.value);sync(false);}
  function commitDrag(){const target=clamp(range.value);dragging=false;if(target===lastRendered){index=target;sync(true);return;}show(target);}
  function start(){
    const gd=graph();if(!gd||typeof Plotly==='undefined'){setTimeout(start,60);return;}
    range.max=String(Math.max(0,names.length-1));sync(false);show(0);
    range.addEventListener('pointerdown',beginDrag);range.addEventListener('mousedown',beginDrag);range.addEventListener('touchstart',beginDrag,{passive:true});
    range.addEventListener('input',previewDrag);
    range.addEventListener('change',commitDrag);range.addEventListener('pointerup',commitDrag);range.addEventListener('mouseup',commitDrag);range.addEventListener('touchend',commitDrag);
    range.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowDown','ArrowRight','ArrowUp','Home','End','PageUp','PageDown'].includes(e.key)){stop();setTimeout(()=>show(Number(range.value)),0);}});
    prev.addEventListener('click',()=>{stop();show(index-1);});next.addEventListener('click',()=>{stop();show(index+1);});
    play.addEventListener('click',()=>{if(names.length<2)return;playing=true;const my=++token;sync(false);run(my);});pause.addEventListener('click',stop);
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
</script>
'''.replace('__PAYLOAD__', payload_json)
    if '<body>' in html:
        html = html.replace('<body>', '<body>' + dock, 1)
    else:
        html = dock + html
    if '</body>' in html:
        html = html.replace('</body>', script + '</body>', 1)
    else:
        html += script
    path.write_text(html, encoding="utf-8")


def _install_visual_patch() -> None:
    import faradaiq.v253_visual_browser as visual
    import faradaiq.v254_contour_prerender as prerender

    visual.VERSION = VERSION
    prerender.VERSION = VERSION
    visual._inject_fixed_frame_controls = _inject_frame_controls
    prerender._CACHE_SCHEMA = "faradaiq-contour-prerender-cache-v262"
    prerender._CACHE_GENERATION = 258

    def cache_root(browser: Any, md: Any) -> Path:
        root = Path(md._view_dir(browser)) / "contour_prerender_cache_v262"
        root.mkdir(parents=True, exist_ok=True)
        return root

    prerender._cache_root = cache_root


def _install_review_patch() -> None:
    global _PREVIOUS_REVIEW
    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup
    import faradaiq.v245_queue_scilink as minimal
    import faradaiq.v250_reliability as reliability

    if _PREVIOUS_REVIEW is None:
        _PREVIOUS_REVIEW = reliability.show_analysis_review
    reliability.show_analysis_review = show_analysis_review
    old.show_analysis_review = show_analysis_review
    cleanup.show_analysis_review = show_analysis_review
    minimal.show_analysis_review = show_analysis_review
    minimal._write_minimal_scilink_job = write_scilink_job
    _CORE["show_analysis_review"] = show_analysis_review
    _CORE["FARADAIQ_SCILINK_BROWSER_BUNDLE_AUTO_SELECTED"] = True
    _CORE["FARADAIQ_SCILINK_JOB_LOCAL_DATA"] = True
    _CORE["FARADAIQ_SCILINK_UNSAFE_EXECUTION_SESSION_OVERRIDE"] = True
    _CORE["FARADAIQ_SCILINK_UNSAFE_EXECUTION_ALWAYS_ON"] = True
    _CORE["FARADAIQ_SCILINK_LITERATURE_CHECKBOX"] = True
    _CORE["FARADAIQ_SCILINK_LABRIC_CLOUD_CONTEXT_CHECKBOX"] = True
    _CORE["FARADAIQ_SCILINK_INTERACTIVE_SESSION"] = True


def _normalize_titles() -> None:
    for cls_name in ("SessionReaderGUI", "FaradaIQH5Browser", "FaradaIQDataBrowser"):
        cls = _g(cls_name)
        if cls is None or getattr(cls, "_v262_title_patch", False):
            continue
        old_title = getattr(cls, "title", None)
        if not callable(old_title):
            continue

        def title(self: Any, text: Any = None, _old=old_title) -> Any:
            if text is None:
                return _old(self)
            return _old(self, re.sub(r"v\d+(?:\.\d+)?", VERSION, str(text)))

        cls.title = title
        cls.wm_title = title
        cls._v262_title_patch = True


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE
    _CORE = core
    modules = []
    try:
        from faradaiq import (
            multidim_v216, v228_intelligence, v239_scilink_edison, v240_visual_schema_catalog, v244_workflow_cleanup,
            v245_queue_scilink, v247_experience, v250_reliability, v251_context_scilink,
            v253_llm_qa, v253_visual_browser, v254_contour_prerender, v255_browser_color,
        )
        modules = [
            multidim_v216, v228_intelligence, v239_scilink_edison, v240_visual_schema_catalog, v244_workflow_cleanup,
            v245_queue_scilink, v247_experience, v250_reliability, v251_context_scilink,
            v253_llm_qa, v253_visual_browser, v254_contour_prerender, v255_browser_color,
        ]
    except Exception:
        pass
    for module in modules:
        try:
            module.VERSION = VERSION
        except Exception:
            pass
    _install_visual_patch()
    _install_review_patch()
    try:
        from faradaiq.ca_cp_analysis_v262 import install as _install_ca_cp
        _install_ca_cp(core)
    except Exception as exc:
        try:
            print(f"[FaradaIQ v291] CA/CP analysis patch warning: {exc}")
        except Exception:
            pass
    core["_v262_open_scilink_technique_prompt"] = open_scilink_with_technique_prompt
    core["_v262_scilink_technique_prompt"] = technique_prompt
    _normalize_titles()

    # Older startup/catalog callbacks can run after the GUI is created. Point
    # every active title helper at the current release so a delayed v240
    # callback cannot overwrite the v262 window title.
    def _apply_v262_title(window: Any, base: str = "FaradaIQ Studio v291") -> None:
        value = re.sub(r"v\d+(?:\.\d+)?", VERSION, str(base or "FaradaIQ Studio v291"))
        try:
            window.tk.call("wm", "title", window._w, value)
        except Exception:
            try:
                window.title(value)
            except Exception:
                pass

    core["_v240_apply_title"] = _apply_v262_title
    core["_v753_apply_title"] = _apply_v262_title
    core["FARADAIQ_MANUAL_FRAME_SLIDER"] = True
    core["FARADAIQ_MANUAL_FRAME_RENDER_ON_RELEASE"] = True
    core["FARADAIQ_CONTOUR_CACHE_GENERATION"] = 258
    core["FARADAIQ_SCILINK_CONTEXT_POLICY"] = "sanitized_operator_description_plus_job_local_validated_data_structure"
    core["FARADAIQ_SCILINK_CURVE_DATA_CONTRACT"] = "technique-specific: potential/current for voltammetry, pulse, stripping, and Tafel methods; time/current for CA and deposition; time/potential for CP/OCP/GCD; frequency/Zreal/Zimag for EIS/GEIS; tau/gamma for DRT"
    core["FARADAIQ_SCILINK_DEFAULT_REVIEW_ITEMS"] = "prepared data bundle plus compact context JSON, both checked"
    core["FARADAIQ_SCILINK_DEPENDENCY_AUTO_REPAIR"] = True
    core["FARADAIQ_SCILINK_EDISON_AVAILABLE_ALL_MODES"] = True
    core["FARADAIQ_SCILINK_LITERATURE_ANALYSIS_DEFAULT"] = True
    core["FARADAIQ_SCILINK_LABRIC_MCP_PACKAGE_LOCAL"] = True
    core["FARADAIQ_SCILINK_WAIT_FOR_USER_DEFAULT"] = True
    core["FARADAIQ_SCILINK_AUTO_OPEN_HTML_REPORTS"] = True

