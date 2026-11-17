"""FaradaIQ Studio v273 structured HEKA DAT2/PUL support.

This module augments the historical HEKAPotmasterParser without replacing its
validated raw trace decoder. It reconstructs the complete Root -> Group ->
Series -> Sweep -> Trace PUL tree, attaches hierarchy metadata to decoded
traces, classifies each Series from multiple evidence sources, assembles EIS
spectra at the HEKA Group level, and writes stable, reloadable exports.

The implementation deliberately treats optional HEKA sections as optional.
Malformed records are reported in warnings with hierarchy context and do not
abort unrelated Series.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import struct
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from . import heka_semantics_v209 as _sem

PARSER_SCHEMA = "faradaiq-heka-structured-v216"
PARSER_VERSION = "v237"


def _cstr(raw: bytes) -> str:
    return bytes(raw).split(b"\x00", 1)[0].decode("latin-1", errors="replace").strip()


def _safe_name(value: Any, limit: int = 80) -> str:
    out = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unnamed")).strip("_.")
    return (out or "unnamed")[:limit]


def _finite(value: Any) -> Optional[float]:
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def _json_safe(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return str(type(value).__name__)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        v = float(value)
        return v if math.isfinite(v) else None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v, depth + 1) for v in value]
    return str(value)


def _sha256(path: Optional[Path]) -> str:
    if not path or not path.exists() or not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _unit_factor(unit: str) -> Tuple[float, str]:
    """Return SI multiplier and normalized unit without guessing unknown units."""
    u = str(unit or "").strip().replace("µ", "u").replace("μ", "u")
    table = {
        "a": (1.0, "A"), "ma": (1e-3, "A"), "ua": (1e-6, "A"), "na": (1e-9, "A"), "pa": (1e-12, "A"),
        "v": (1.0, "V"), "mv": (1e-3, "V"), "uv": (1e-6, "V"),
        "s": (1.0, "s"), "ms": (1e-3, "s"), "us": (1e-6, "s"),
        "ohm": (1.0, "ohm"), "kohm": (1e3, "ohm"), "mohm": (1e6, "ohm"),
        "hz": (1.0, "Hz"), "khz": (1e3, "Hz"),
        "m": (1.0, "m"), "mm": (1e-3, "m"), "um": (1e-6, "m"), "nm": (1e-9, "m"),
        "deg": (1.0, "deg"), "degree": (1.0, "deg"), "degrees": (1.0, "deg"),
        "s_": (1.0, "S"), "siemens": (1.0, "S"),
    }
    key = u.lower().replace("ω", "ohm").replace(" ", "")
    if key == "s" and u == "S":
        return 1.0, "S"
    return table.get(key, (1.0, u or "unknown"))


def _trace_values_si(parser: Any, trace_index: int, max_points: Optional[int] = None) -> Tuple[np.ndarray, str]:
    tr = parser.traces[int(trace_index)]
    values = np.asarray(parser.read_trace_array(int(trace_index), max_points=max_points), dtype=float)
    factor, unit = _unit_factor(getattr(tr, "y_unit", ""))
    if factor != 1.0:
        values = values * factor
    return values, unit


def _time_axis(parser: Any, trace_index: int, n: int) -> np.ndarray:
    tr = parser.traces[int(trace_index)]
    dt = _finite(getattr(tr, "x_interval", None))
    start = _finite(getattr(tr, "x_start", None))
    if dt is None or dt <= 0:
        dt = 1.0
    if start is None:
        start = _finite(getattr(tr, "time_offset", None)) or 0.0
    factor, unit = _unit_factor(getattr(tr, "x_unit", "s"))
    if unit == "s":
        dt *= factor
        start *= factor
    return start + np.arange(int(n), dtype=float) * dt


def _trace_x_axis(parser: Any, trace_index: int, n: int) -> Tuple[np.ndarray, str]:
    """Return the stored HEKA X axis in normalized SI units.

    HEKA uses the Trace X unit semantically. For example, TipDown approach
    curves in the generic multi-point HEKA fixture store X in metres, not seconds. Treating that
    axis as time creates the near-vertical artifact seen in v210.
    """
    tr = parser.traces[int(trace_index)]
    step = _finite(getattr(tr, "x_interval", None))
    start = _finite(getattr(tr, "x_start", None))
    if step is None or step == 0:
        step = 1.0
    if start is None:
        start = 0.0
    factor, unit = _unit_factor(getattr(tr, "x_unit", ""))
    if unit in {"s", "m"}:
        step *= factor
        start *= factor
    return start + np.arange(int(n), dtype=float) * step, unit


def _normalized_label(label: str) -> str:
    s = str(label or "").strip().lower()
    s = re.sub(r"[_-]\d+$", "", s)
    s = s.replace(" ", "")
    return s


def _channel_role(trace: Any) -> str:
    label = _normalized_label(getattr(trace, "label", ""))
    unit = _unit_factor(getattr(trace, "y_unit", ""))[1]
    if re.search(r"real\(?z\)?", label): return "zreal"
    if re.search(r"imag\(?z\)?", label): return "zimag"
    if "imp|z|" in label or "|z|" in label or "zmod" in label: return "zabs"
    if "phase" in label: return "phase"
    if re.search(r"real\(?y\)?", label): return "yreal"
    if re.search(r"imag\(?y\)?", label): return "yimag"
    if "admit" in label or "|y|" in label: return "yabs"
    if "freq" in label or unit == "Hz": return "frequency"
    # HEKA monitor labels are stronger evidence than occasionally inconsistent stored units.
    if label.startswith("imon") or "current" in label: return "current"
    if label.startswith("emon") or "potential" in label or "voltage" in label: return "potential"
    if unit == "A": return "current"
    if unit == "V": return "potential"
    if "z_pos" in label or label in {"z", "zpos", "height", "distance"}: return "z_position"
    if "z_slope" in label or "zslope" in label: return "z_slope"
    if "sf-ampl" in label or "shear" in label and "amp" in label: return "shear_amplitude"
    if "sf-phase" in label or "shear" in label and "phase" in label: return "shear_phase"
    if label in {"x", "xpos", "x_pos"} or "x_pos" in label: return "x_position"
    if label in {"y", "ypos", "y_pos"} or "y_pos" in label: return "y_position"
    return "signal"


@dataclass
class HekaWarning:
    code: str
    message: str
    file: str = ""
    group: str = ""
    series: str = ""
    sweep: str = ""
    trace: str = ""


class HekaHierarchyError(RuntimeError):
    pass


def parse_hierarchy(parser: Any) -> Dict[str, Any]:
    """Parse the PUL tree with sizes read from the file and strict bounds checks."""
    pul = bytes(getattr(parser, "pul", b"") or b"")
    warnings: List[HekaWarning] = []
    source = str(getattr(parser, "source_path", "") or "")
    if len(pul) < 16:
        raise HekaHierarchyError("PUL section is too short to contain a tree header")
    if pul[:4] == b"eerT":
        endian = "<"
    elif pul[:4] == b"Tree":
        endian = ">"
    else:
        raise HekaHierarchyError(f"Unsupported PUL tree magic {pul[:4]!r}")
    try:
        n_levels = struct.unpack_from(endian + "i", pul, 4)[0]
    except Exception as exc:
        raise HekaHierarchyError(f"Cannot read PUL level count: {exc}") from exc
    if not 1 <= n_levels <= 16:
        raise HekaHierarchyError(f"Invalid PUL hierarchy depth {n_levels}")
    header_end = 8 + 4 * n_levels
    if header_end > len(pul):
        raise HekaHierarchyError("Truncated PUL level-size table")
    level_sizes = list(struct.unpack_from(endian + ("i" * n_levels), pul, 8))
    if any(s <= 0 or s > 2_000_000 for s in level_sizes):
        raise HekaHierarchyError(f"Invalid PUL record sizes {level_sizes}")

    level_names = ["Root", "Group", "Series", "Sweep", "Trace"]
    level_names += [f"Level {i}" for i in range(len(level_names), n_levels)]
    pos = header_end
    level_counts = [0] * n_levels
    trace_by_offset = {int(getattr(t, "rec_offset", -1)): i for i, t in enumerate(getattr(parser, "traces", []))}
    flat_nodes: List[Dict[str, Any]] = []

    def walk(level: int, parents: List[Dict[str, Any]]) -> Dict[str, Any]:
        nonlocal pos
        if level >= n_levels:
            raise HekaHierarchyError("PUL tree descends beyond declared level count")
        rec_size = int(level_sizes[level])
        rec_offset = pos
        rec_end = rec_offset + rec_size
        if rec_end + 4 > len(pul):
            raise HekaHierarchyError(f"Truncated {level_names[level]} record at PUL offset {rec_offset}")
        rec = pul[rec_offset:rec_end]
        pos = rec_end
        child_count = struct.unpack_from(endian + "i", pul, pos)[0]
        pos += 4
        if child_count < 0 or child_count > 10_000_000:
            raise HekaHierarchyError(f"Invalid child count {child_count} at PUL offset {rec_offset}")
        level_counts[level] += 1
        index_global = level_counts[level]
        title = _cstr(rec[4:36]) if rec_size >= 36 else ""
        if level == 0 and not title and rec_size >= 64:
            title = _cstr(rec[8:40])
        node: Dict[str, Any] = {
            "level": level,
            "kind": level_names[level],
            "index_global": index_global,
            "record_offset": rec_offset,
            "record_size": rec_size,
            "child_count": int(child_count),
            "title": title,
            "record_sha256": hashlib.sha256(rec).hexdigest(),
            "children": [],
        }
        if parents:
            same_kind = [c for c in parents[-1].get("children", []) if c.get("kind") == level_names[level]]
            node["index_in_parent"] = len(same_kind) + 1
        else:
            node["index_in_parent"] = 1
        if level == 0 and rec_size >= 32:
            node["bundle_version_text"] = _cstr(rec[8:40])
        elif level == 1 and rec_size >= 128:
            node.update({
                "group_text": _cstr(rec[36:116]),
                "experiment_number": struct.unpack_from(endian + "i", rec, 116)[0],
                "group_count": struct.unpack_from(endian + "i", rec, 120)[0],
                "crc": struct.unpack_from(endian + "I", rec, 124)[0],
            })
        elif level == 2 and rec_size >= 1728:
            node.update({
                "comment": _cstr(rec[36:116]),
                "series_count": struct.unpack_from(endian + "i", rec, 116)[0],
                "number_sweeps": struct.unpack_from(endian + "i", rec, 120)[0],
                "amplifier_state_flag": struct.unpack_from(endian + "i", rec, 124)[0],
                "amplifier_state_reference": struct.unpack_from(endian + "i", rec, 128)[0],
                "method_tag": struct.unpack_from(endian + "i", rec, 132)[0],
                "acquisition_time_raw": struct.unpack_from(endian + "d", rec, 136)[0],
                "page_width": struct.unpack_from(endian + "d", rec, 144)[0],
                "method_name": _cstr(rec[312:344]),
                "username": _cstr(rec[872:952]),
                "crc": struct.unpack_from(endian + "I", rec, 1116)[0],
                "user_parameters": list(struct.unpack_from(endian + "4d", rec, 1120)),
                "scan_parameters_raw_hex": rec[1312:1408].hex(),
                "record_reserved_tail_hex": rec[1408:1728].hex(),
            })
        elif level == 3 and rec_size >= 352:
            node.update({
                "aux_data_file_offset": struct.unpack_from(endian + "i", rec, 36)[0],
                "stimulation_index": struct.unpack_from(endian + "i", rec, 40)[0],
                "sweep_count": struct.unpack_from(endian + "i", rec, 44)[0],
                "acquisition_time_raw": struct.unpack_from(endian + "d", rec, 48)[0],
                "timer_s": struct.unpack_from(endian + "d", rec, 56)[0],
                "user_parameters": list(struct.unpack_from(endian + "2d", rec, 64)),
                "pipette_pressure": struct.unpack_from(endian + "d", rec, 80)[0],
                "rms_noise": struct.unpack_from(endian + "d", rec, 88)[0],
                "temperature": struct.unpack_from(endian + "d", rec, 96)[0],
                "internal_solution_index": struct.unpack_from(endian + "i", rec, 104)[0],
                "external_solution_index": struct.unpack_from(endian + "i", rec, 108)[0],
                "digital_in": struct.unpack_from(endian + "H", rec, 112)[0],
                "sweep_kind": struct.unpack_from(endian + "H", rec, 114)[0],
                "digital_out": struct.unpack_from(endian + "H", rec, 116)[0],
                "markers": list(struct.unpack_from(endian + "4d", rec, 120)),
                "crc": struct.unpack_from(endian + "I", rec, 156)[0],
                "holding_values": list(struct.unpack_from(endian + "16d", rec, 160)),
                "extended_user_parameters": list(struct.unpack_from(endian + "8d", rec, 288)),
            })
        if level == n_levels - 1:
            ti = trace_by_offset.get(rec_offset)
            node["trace_index"] = ti
            if ti is None:
                warnings.append(HekaWarning("trace_record_not_decoded", "Trace tree record could not be matched to a decoded numeric trace.", source, trace=title))
            else:
                tr = parser.traces[ti]
                node.update({
                    "title": getattr(tr, "label", title),
                    "channel_name": getattr(tr, "label", title),
                    "physical_unit": getattr(tr, "y_unit", ""),
                    "x_unit": getattr(tr, "x_unit", ""),
                    "x_increment": _finite(getattr(tr, "x_interval", None)),
                    "x_start": _finite(getattr(tr, "x_start", None)),
                    "sample_count": int(getattr(tr, "n_points", 0) or 0),
                    "data_offset": int(getattr(tr, "old_offset", 0) or 0),
                    "data_format": int(getattr(tr, "data_format", -1)),
                    "data_scaler": _finite(getattr(tr, "data_scaler", None)),
                    "zero_data": _finite(getattr(tr, "zero_data", None)),
                    "data_endian": getattr(tr, "data_endian", ""),
                    "recording_role": _channel_role(tr),
                })
        flat_nodes.append(node)
        if level + 1 < n_levels:
            for _ in range(child_count):
                child = walk(level + 1, parents + [node])
                node["children"].append(child)
        elif child_count:
            warnings.append(HekaWarning("trace_has_children", f"Trace-level record unexpectedly declares {child_count} children.", source, trace=title))
        return node

    root = walk(0, [])
    if pos != len(pul):
        warnings.append(HekaWarning("pul_trailing_bytes", f"PUL tree ended at {pos}, leaving {len(pul)-pos} trailing bytes preserved as an unsupported tail.", source))

    # Attach hierarchy context to the historical trace objects.
    def attach(node: Dict[str, Any], context: Dict[str, Any]) -> None:
        ctx = dict(context)
        kind = node["kind"]
        if kind in {"Group", "Series", "Sweep"}:
            key = kind.lower()
            ctx[f"{key}_index"] = node.get("index_global")
            ctx[f"{key}_index_in_parent"] = node.get("index_in_parent")
            ctx[f"{key}_title"] = node.get("title") or ""
        if kind == "Trace" and node.get("trace_index") is not None:
            tr = parser.traces[int(node["trace_index"])]
            for k, v in ctx.items():
                setattr(tr, k, v)
            setattr(tr, "trace_index_in_sweep", int(node.get("index_in_parent") or 1))
        for child in node.get("children", []):
            attach(child, ctx)
    attach(root, {})

    return {
        "schema": PARSER_SCHEMA,
        "parser_version": PARSER_VERSION,
        "tree_magic": pul[:4].decode("latin-1", errors="replace"),
        "tree_endian": "little" if endian == "<" else "big",
        "level_count": n_levels,
        "record_sizes": level_sizes,
        "level_names": level_names[:n_levels],
        "level_counts": {level_names[i]: level_counts[i] for i in range(n_levels)},
        "parsed_bytes": pos,
        "pul_bytes": len(pul),
        "root": root,
        "warnings": [asdict(w) for w in warnings],
    }


def _iter_nodes(node: Dict[str, Any], kind: Optional[str] = None) -> Iterable[Dict[str, Any]]:
    if kind is None or node.get("kind") == kind:
        yield node
    for ch in node.get("children", []):
        yield from _iter_nodes(ch, kind)


def _trace_indices(node: Dict[str, Any]) -> List[int]:
    out = []
    for tr in _iter_nodes(node, "Trace"):
        if tr.get("trace_index") is not None:
            out.append(int(tr["trace_index"]))
    return out


def _series_signal_shape(parser: Any, series_node: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for sweep in [c for c in series_node.get("children", []) if c.get("kind") == "Sweep"]:
        tis = _trace_indices(sweep)
        pot = next((ti for ti in tis if _channel_role(parser.traces[ti]) == "potential"), None)
        cur = next((ti for ti in tis if _channel_role(parser.traces[ti]) == "current"), None)
        if pot is None or cur is None:
            continue
        try:
            n = min(int(parser.traces[pot].n_points), int(parser.traces[cur].n_points), 500_000)
            e, eu = _trace_values_si(parser, pot, max_points=n)
            i, iu = _trace_values_si(parser, cur, max_points=n)
            m = min(len(e), len(i))
            if m < 8:
                continue
            e = e[:m]
            finite = e[np.isfinite(e)]
            if finite.size < 8:
                continue
            erange = float(np.nanmax(finite) - np.nanmin(finite))
            closure = float(abs(finite[-1] - finite[0])) / max(erange, np.finfo(float).eps)
            d = np.diff(finite)
            d = d[np.isfinite(d)]
            turns = int(np.count_nonzero(np.diff(np.sign(d[np.abs(d) > max(erange * 1e-9, 1e-15)])))) if d.size else 0
            result = {
                "paired_sweep_count": result.get("paired_sweep_count", 0) + 1,
                "potential_range_V": erange,
                "potential_closure_fraction": closure,
                "potential_turn_count": turns,
                "potential_start_V": float(finite[0]),
                "potential_end_V": float(finite[-1]),
            }
        except Exception as exc:
            result["shape_warning"] = str(exc)
    return result


def _estimate_frequency(parser: Any, trace_index: int) -> Optional[float]:
    tr = parser.traces[int(trace_index)]
    dt = _finite(getattr(tr, "x_interval", None))
    if dt is None or dt <= 0:
        return None
    try:
        arr, _ = _trace_values_si(parser, trace_index, max_points=min(int(tr.n_points), 131072))
    except Exception:
        return None
    arr = np.asarray(arr, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 16:
        return None
    arr = arr - float(np.mean(arr))
    scale = float(np.max(np.abs(arr)))
    if not math.isfinite(scale) or scale <= 0:
        return None
    # FFT is robust for the HEKA excitation traces and avoids counting noisy crossings.
    window = np.hanning(arr.size)
    spec = np.abs(np.fft.rfft(arr * window))
    if spec.size <= 1:
        return None
    spec[0] = 0
    k = int(np.argmax(spec))
    freq = float(np.fft.rfftfreq(arr.size, d=dt)[k])
    if not math.isfinite(freq) or freq <= 0:
        return None
    return freq


def classify_series(parser: Any, hierarchy: Dict[str, Any]) -> List[Dict[str, Any]]:
    series_nodes = list(_iter_nodes(hierarchy["root"], "Series"))
    all_series_names = " ".join(str(s.get("title") or "") for s in series_nodes).lower()
    all_group_names = " ".join(str(g.get("title") or "") for g in _iter_nodes(hierarchy["root"], "Group")).lower()
    global_modality = ""
    if "sf matrix" in all_group_names or ("tipdown" in all_series_names and "orr" in all_series_names):
        global_modality = "SECCM"
    elif any(x in all_series_names for x in ("3d_scan", "2d_xx", "2d_yy")):
        global_modality = "SECM"
    elif "sicm" in all_series_names or "sicm" in all_group_names:
        global_modality = "SICM"
    elif "skp" in all_series_names or "kelvin" in all_series_names:
        global_modality = "SKP"

    out: List[Dict[str, Any]] = []
    point_counters: Dict[Tuple[int, str], int] = defaultdict(int)
    for s in series_nodes:
        title = str(s.get("title") or "")
        low = title.lower().replace(" ", "")
        trace_indices = _trace_indices(s)
        roles = [_channel_role(parser.traces[ti]) for ti in trace_indices]
        role_counts = Counter(roles)
        labels = [str(parser.traces[ti].label) for ti in trace_indices]
        group_idx = 0
        group_title = ""
        if trace_indices:
            tr0 = parser.traces[trace_indices[0]]
            group_idx = int(getattr(tr0, "group_index", 0) or 0)
            group_title = str(getattr(tr0, "group_title", "") or "")
        evidence: List[str] = []
        scores: Dict[str, float] = defaultdict(float)

        processed_eis = role_counts["zreal"] > 0 and role_counts["zimag"] > 0
        if processed_eis:
            scores["EIS"] += 0.90; evidence.append("processed Real(Z) and Imag(Z) channels are present")
        if "eis" in low:
            scores["EIS"] += 0.12; evidence.append(f"Series title contains EIS: {title}")
        if any(x in low for x in ("fastcv", "fscv")):
            scores["FSCV"] += 0.90; evidence.append(f"Series title identifies fast-scan CV: {title}")
        if "cv" in low and role_counts["current"] and role_counts["potential"]:
            scores["CV"] += 0.72; evidence.append("CV title plus current and potential channels")
        lsv_title = ("lsv" in low or "polarization" in low or "polarisation" in low or bool(re.search(r"\borr\d*\b", title.lower())))
        if lsv_title and role_counts["current"] and role_counts["potential"]:
            scores["LSV"] += 0.72; evidence.append("LSV/ORR/polarization title plus current and potential channels")
        if any(x in low for x in ("tafel", "corrosion", "corrod")) and role_counts["current"] and role_counts["potential"]:
            scores["Tafel/corrosion"] += 0.86; evidence.append("Tafel/corrosion title plus current and potential channels")
        if ("skp" in low or "kelvin" in low or global_modality == "SKP") and role_counts["potential"]:
            scores["Scanning Kelvin probe"] += 0.88; evidence.append("SKP/Kelvin context plus potential channel")
        if any(x in low for x in ("ocp", "opencircuit")):
            scores["OCP"] += 0.90; evidence.append(f"Series title identifies OCP: {title}")
        if any(x in low for x in ("chronoamp", "ca", "potentiostatic")) and role_counts["current"]:
            scores["CA"] += 0.75; evidence.append("chronoamperometric/potentiostatic title and current channel")
        if any(x in low for x in ("chronopot", "cp", "galvanostatic")) and role_counts["potential"]:
            scores["CP"] += 0.75; evidence.append("chronopotentiometric/galvanostatic title and potential channel")
        if "tipdown" in low or "approach" in low:
            scores["Approach curve"] += 0.92; evidence.append(f"Series title identifies approach/tip-down: {title}")
        if "pullup" in low or "retract" in low:
            scores["Approach curve"] += 0.88; evidence.append(f"Series title identifies retract/pull-up branch: {title}")
        if "dist_calib" in low or "distancecal" in low:
            scores["Distance calibration"] += 0.95; evidence.append(f"Series title identifies distance calibration: {title}")
        if "sf_spectra" in low or role_counts["shear_amplitude"] or role_counts["shear_phase"]:
            scores["Shear-force"] += 0.93; evidence.append("shear-force amplitude/phase evidence")
        if "c.d.sf" in low or "constantdistance" in low:
            scores["Shear-force"] += 0.90; evidence.append("constant-distance shear-force Series title")
        if "2d_time" in low:
            scores["Time scan"] += 0.94; evidence.append("2D_Time Series title")
        if "2d_xx" in low or re.search(r"(^|_)xscan", low):
            scores["X scan"] += 0.94; evidence.append("X-position scan Series title")
        if "2d_yy" in low or re.search(r"(^|_)yscan", low):
            scores["Y scan"] += 0.94; evidence.append("Y-position scan Series title")
        if "2d_z" in low or re.search(r"(^|_)zscan", low):
            scores["Z scan"] += 0.94; evidence.append("Z-position scan Series title")
        if "3d_scan" in low or "3dscan" in low:
            scores["3D scan"] += 0.96; evidence.append("3D scan Series title")
        if "matrix" in low:
            scores["2D matrix scan"] += 0.80; evidence.append("matrix Series title")
        if "linescan" in low or "line_scan" in low:
            scores["Line scan"] += 0.90; evidence.append("line-scan Series title")
        if "takedata" in low:
            scores["Position/Takedata"] += 0.98
            evidence.append("HEKA Takedata Series marks a spatial/acquisition point and is not an electrochemical technique")

        shape = {}
        if role_counts["current"] and role_counts["potential"] and not processed_eis:
            shape = _series_signal_shape(parser, s)
            closure = _finite(shape.get("potential_closure_fraction"))
            turns = int(shape.get("potential_turn_count", 0) or 0)
            if closure is not None:
                if closure <= 0.20 and turns >= 1:
                    scores["CV"] += 0.28; evidence.append(f"potential waveform returns near its start and reverses (closure={closure:.3g})")
                elif closure >= 0.60 and turns <= 1:
                    scores["LSV"] += 0.25; evidence.append(f"potential waveform is predominantly one-way (closure={closure:.3g})")
        if not scores:
            if role_counts["current"] and not role_counts["potential"]:
                scores["CA"] = 0.48; evidence.append("current-versus-time channel present without a paired potential channel")
            elif role_counts["potential"] and not role_counts["current"]:
                scores["Unknown HEKA"] = 0.30; evidence.append("potential-only Series lacks explicit OCP/open-circuit evidence")
            else:
                scores["Unknown HEKA"] = 0.20; evidence.append("no rule had sufficient technique-specific evidence")

        technique, raw_score = max(scores.items(), key=lambda kv: kv[1])
        confidence = max(0.0, min(1.0, raw_score))
        modality = global_modality
        if technique == "Shear-force":
            modality = modality or "Shear-force distance regulation"
        if "sf matrix" in group_title.lower():
            modality = "SECCM"
        point_key = (group_idx, technique)
        point_counters[point_key] += 1
        point_index = group_idx if technique == "EIS" and group_idx else point_counters[point_key]
        if modality in {"SECCM", "SECM", "SICM"} and technique in {"CV", "LSV", "FSCV", "CA", "CP", "OCP"}:
            # Repeated Series are spatial points in acquisition order when explicit coordinates are unavailable.
            point_index = point_counters[point_key]
        sweep_count = sum(1 for c in s.get("children", []) if c.get("kind") == "Sweep")
        record = {
            "group_index": group_idx,
            "group_title": group_title,
            "series_index": int(s.get("index_global") or 0),
            "series_index_in_group": int(s.get("index_in_parent") or 0),
            "series_title": title,
            "sweep_count": sweep_count,
            "trace_count": len(trace_indices),
            "trace_indices": trace_indices,
            "channel_labels": labels,
            "channel_roles": dict(role_counts),
            "technique": technique,
            "confidence": round(confidence, 4),
            "evidence": evidence,
            "modality": modality,
            "point_index": int(point_index or 0),
            "signal_shape": shape,
        }
        s["technique_assignment"] = {k: record[k] for k in ("technique", "confidence", "evidence", "modality", "point_index")}
        for ti in trace_indices:
            tr = parser.traces[ti]
            tr.technique_assignment = technique
            tr.technique_confidence = confidence
            tr.technique_evidence = list(evidence)
            tr.modality = modality
            tr.point_index = int(point_index or 0)
        out.append(record)
    return out


def _first_by_role(parser: Any, trace_indices: Sequence[int], role: str) -> Optional[int]:
    return next((int(ti) for ti in trace_indices if _channel_role(parser.traces[int(ti)]) == role), None)



def _scan_rate_from_text(*values: Any) -> Optional[float]:
    """Return an explicit scan rate in mV/s from HEKA Group/Series/protocol text."""
    text = " ".join(str(v or "") for v in values)
    patterns = (
        r"(?i)(?:CV|LSV|scan(?:rate)?)?[_\- ]*([0-9]+(?:\.[0-9]+)?)\s*(mV|V)\s*/?\s*s?\b",
        r"(?i)\b([0-9]+(?:\.[0-9]+)?)\s*(mV|V)\b",
    )
    for pattern in patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        value = float(m.group(1))
        unit = m.group(2).lower()
        return value * (1000.0 if unit == "v" else 1.0)
    return None


def _assign_acquisition_points(parser: Any, classifications: List[Dict[str, Any]]) -> None:
    """Assign every Series to acquisition points delimited by Takedata anchors.

    HEKA files use two layouts that must both work:

    1. multi-point HEKA scans place one Takedata anchor in each NewPoint Group and put
       CV/EIS methods in following Groups.
    2. Some CA/SECCM files keep many TipDown → Takedata → ChronoPotStat blocks
       inside one Group. Group-only counting collapses every CA trace to point 1.

    This routine therefore uses the full (Group, Series) acquisition order. A
    TipDown/approach Series immediately before a same-Group Takedata anchor is
    assigned to the upcoming point; scientific measurements after an anchor
    remain with that point until the next anchor. Records before the first
    anchor remain setup point 0.
    """
    rows = sorted(classifications, key=lambda r: (int(r.get("group_index") or 0), int(r.get("series_index") or 0)))
    anchors = [r for r in rows if r.get("technique") == "Position/Takedata"]
    if not anchors:
        anchors = [r for r in rows if "newpoint" in str(r.get("group_title") or "").lower()]
    anchor_keys = [(int(r.get("group_index") or 0), int(r.get("series_index") or 0)) for r in anchors]
    anchor_point = {key: i + 1 for i, key in enumerate(anchor_keys)}
    anchors_by_group: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
    for key, point in anchor_point.items():
        anchors_by_group[key[0]].append((key[1], point))
    for gi in anchors_by_group:
        anchors_by_group[gi].sort()

    first_anchor = anchor_keys[0] if anchor_keys else None
    for rec in rows:
        gi = int(rec.get("group_index") or 0)
        si = int(rec.get("series_index") or 0)
        key = (gi, si)
        point = None
        same_group = anchors_by_group.get(gi, [])
        if key in anchor_point:
            point = anchor_point[key]
        elif same_group:
            previous = [item for item in same_group if item[0] < si]
            following = [item for item in same_group if item[0] > si]
            # TipDown belongs to the point it is approaching, which is the next
            # same-Group Takedata anchor. Other methods belong to the preceding
            # anchor until the next Takedata record appears.
            if rec.get("technique") == "Approach curve" and following:
                point = following[0][1]
            elif previous:
                point = previous[-1][1]
            elif following:
                point = following[0][1]
        if point is None and anchor_keys:
            prior = [anchor_point[a] for a in anchor_keys if a <= key]
            point = prior[-1] if prior else 0
        if point is None:
            point = max(1, int(rec.get("point_index") or 1))

        rec["point_index"] = int(point)
        rec["initial_setup"] = bool(point == 0)
        if rec.get("technique") == "Position/Takedata":
            rec["evidence"] = list(rec.get("evidence") or []) + [f"acquisition point anchor P{int(point):03d}"]
        rate = _scan_rate_from_text(rec.get("group_title"), rec.get("series_title"), rec.get("pgf_stimulation_name"), rec.get("series_method_name"))
        if rate is not None and rec.get("technique") in {"CV", "LSV", "FSCV", "Tafel/corrosion"}:
            rec["scan_rate_mV_s"] = float(rate)
            rec["scan_rate_label"] = f"{rate:g} mV/s"
        for ti in rec.get("trace_indices", []):
            tr = parser.traces[int(ti)]
            tr.point_index = int(point)
            if rate is not None:
                tr.scan_rate_mV_s = float(rate)


def build_eis_spectra(parser: Any, classifications: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_group: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for rec in classifications:
        if rec.get("technique") == "EIS":
            by_group[int(rec.get("group_index") or 0)].append(dict(rec))
    spectra: List[Dict[str, Any]] = []
    for group_idx, series_recs in sorted(by_group.items()):
        point = int(next((r.get("point_index") for r in series_recs if int(r.get("point_index") or 0) > 0), 0) or 0)
        rows = []
        group_title = series_recs[0].get("group_title", "") if series_recs else ""
        for rec in sorted(series_recs, key=lambda x: int(x.get("series_index_in_group") or 0)):
            tis = list(rec.get("trace_indices") or [])
            values: Dict[str, Any] = {}
            for role, col in (("zreal", "Zreal_ohm"), ("zimag", "Zimag_stored_ohm"), ("zabs", "Zabs_ohm"), ("phase", "phase_deg"),
                              ("yreal", "Yreal_S"), ("yimag", "Yimag_S"), ("yabs", "Yabs_S"),
                              ("potential", "bias_potential_V"), ("current", "monitor_current_A")):
                ti = _first_by_role(parser, tis, role)
                if ti is None:
                    values[col] = None
                    continue
                try:
                    arr, _ = _trace_values_si(parser, ti, max_points=512)
                    finite = arr[np.isfinite(arr)]
                    values[col] = float(np.nanmedian(finite)) if finite.size else None
                except Exception:
                    values[col] = None
            freq_ti = _first_by_role(parser, tis, "frequency")
            freq = _finite(rec.get("protocol_frequency_Hz"))
            frequency_source = "PGF chSine_Cycle period" if freq is not None else ""
            if freq_ti is not None and freq is None:
                try:
                    a, _ = _trace_values_si(parser, freq_ti, max_points=64)
                    a = a[np.isfinite(a)]
                    freq = float(np.nanmedian(a)) if a.size else None
                    if freq is not None: frequency_source = "stored frequency channel"
                except Exception:
                    freq = None
            if freq is None:
                excitation = _first_by_role(parser, tis, "potential") or _first_by_role(parser, tis, "current")
                if excitation is not None:
                    freq = _estimate_frequency(parser, excitation)
                    if freq is not None: frequency_source = "FFT estimate from excitation trace"
            zim = _finite(values.get("Zimag_stored_ohm"))
            row = {
                "frequency_Hz": freq,
                "frequency_source": frequency_source,
                "Zreal_ohm": _finite(values.get("Zreal_ohm")),
                "Zimag_ohm": zim,
                "neg_Zimag_ohm": -zim if zim is not None else None,
                "Zmod_ohm": _finite(values.get("Zabs_ohm")),
                "phase_deg": _finite(values.get("phase_deg")),
                "Yreal_S": _finite(values.get("Yreal_S")),
                "Yimag_S": _finite(values.get("Yimag_S")),
                "Yabs_S": _finite(values.get("Yabs_S")),
                "bias_potential_V": _finite(values.get("bias_potential_V")),
                "monitor_current_A": _finite(values.get("monitor_current_A")),
                "series_index": int(rec.get("series_index") or 0),
                "series_index_in_group": int(rec.get("series_index_in_group") or 0),
                "series_title": rec.get("series_title", ""),
            }
            rows.append(row)
        rows.sort(key=lambda r: (float("inf") if r.get("frequency_Hz") is None else float(r["frequency_Hz"])))
        valid = [r for r in rows if r.get("frequency_Hz") and r.get("Zreal_ohm") is not None and r.get("Zimag_ohm") is not None]
        warnings = []
        if len(valid) < len(rows):
            warnings.append(f"{len(rows)-len(valid)} Series lacked a complete frequency/Zreal/Zimag triplet")
        spectra.append({
            "point_index": point,
            "group_index": group_idx,
            "group_title": group_title,
            "series_count": len(series_recs),
            "valid_point_count": len(valid),
            "rows": rows,
            "warnings": warnings,
            "stored_sign_convention": "Zimag_ohm is preserved exactly as stored; neg_Zimag_ohm is provided for the conventional Nyquist display.",
        })
    return spectra


def _support_only_summary(parser: Any, semantics: Dict[str, Any]) -> Dict[str, Any]:
    sections = semantics.get("sections", {})
    counts: Dict[str, int] = {}
    rows: List[Dict[str, Any]] = []
    if ".pgf" in sections:
        pgf = sections[".pgf"]
        for protocol in pgf.get("protocols", []):
            tech = str(protocol.get("technique") or "Unknown HEKA")
            counts[tech] = counts.get(tech, 0) + 1
            rows.append({"section": "PGF", "index": protocol.get("stimulation_index"), "name": protocol.get("entry_name"), "analysis_name": protocol.get("analysis_name"), "technique": tech, "confidence": protocol.get("confidence"), "evidence": protocol.get("evidence")})
    if ".onl" in sections:
        for method in sections[".onl"].get("methods", []):
            rows.append({"section": "ONL", "index": method.get("method_index"), "name": method.get("entry_name"), "function_count": len(method.get("functions", [])), "active_graph_count": sum(1 for g in method.get("graphs", []) if g.get("active"))})
    warnings = list(semantics.get("warnings", []))
    return {
        "schema": PARSER_SCHEMA, "parser_version": PARSER_VERSION, "source_file": str(getattr(parser, "source_path", "") or ""),
        "bundle_version": "support-file", "hierarchy": {"level_counts": {}, "root": None}, "series_classifications": [], "eis_spectra": [],
        "technique_counts": counts, "modalities": {}, "warnings": warnings, "unsupported_sections": [], "semantic_sections": semantics,
        "support_rows": rows,
        "summary": {"groups": 0, "series": 0, "sweeps": 0, "traces": 0, "detected_techniques": sorted(counts), "cv_series": counts.get("CV", 0)+counts.get("FSCV", 0), "lsv_series": counts.get("LSV", 0), "eis_spectra": 0, "scan_points": 0, "warnings": len(warnings), "unsupported_sections": 0, "semantic_sections": len(sections)},
    }


def _augment_classifications_with_semantics(parser: Any, hierarchy: Dict[str, Any], classifications: List[Dict[str, Any]], semantics: Dict[str, Any]) -> None:
    sections = semantics.get("sections", {})
    pgf = sections.get(".pgf", {})
    onl = sections.get(".onl", {})
    stimuli = {int(x.get("stimulation_index") or 0): x for x in pgf.get("stimulations", [])}
    protocols = {int(x.get("stimulation_index") or 0): x for x in pgf.get("protocols", [])}
    methods_by_name = {str(x.get("entry_name") or "").strip().lower(): x for x in onl.get("methods", []) if str(x.get("entry_name") or "").strip()}
    series_nodes = {int(x.get("index_global") or 0): x for x in _iter_nodes(hierarchy["root"], "Series")}
    for rec in classifications:
        node = series_nodes.get(int(rec.get("series_index") or 0))
        sweeps = [x for x in (node or {}).get("children", []) if x.get("kind") == "Sweep"]
        stim_indices = sorted({int(x.get("stimulation_index") or 0) for x in sweeps if int(x.get("stimulation_index") or 0) > 0})
        rec["stimulation_indices"] = stim_indices
        rec["series_method_name"] = (node or {}).get("method_name", "")
        if not stim_indices:
            continue
        stim_idx = stim_indices[0]
        stim = stimuli.get(stim_idx)
        protocol = protocols.get(stim_idx)
        if stim:
            rec["pgf_stimulation_index"] = stim_idx
            rec["pgf_stimulation_name"] = stim.get("entry_name")
            rec["pgf_analysis_name"] = stim.get("analysis_name")
            rec["stimulus_definition"] = {
                "sample_interval_s": stim.get("sample_interval_s"), "sweep_interval_s": stim.get("sweep_interval_s"),
                "number_sweeps": stim.get("number_sweeps"), "filter_factor": stim.get("filter_factor"),
                "has_lockin": stim.get("has_lockin"), "has_chirp": stim.get("has_chirp"),
                "external_trigger": stim.get("external_trigger"), "auto_range": stim.get("auto_range"),
            }
            # The attached EIS PGF stores the sine period in chSine_Cycle.
            periods = [float(c.get("sine_cycle")) for c in stim.get("channels", []) if c.get("has_lockin_sine") and _finite(c.get("sine_cycle")) and float(c.get("sine_cycle")) > 0]
            if periods:
                rec["protocol_frequency_Hz"] = 1.0 / periods[0]
            analysis_name = str(stim.get("analysis_name") or "").strip().lower()
            method = methods_by_name.get(analysis_name)
            if method:
                rec["onl_method_index"] = method.get("method_index")
                rec["onl_method_name"] = method.get("entry_name")
                rec["onl_functions"] = [{"name": f.get("name"), "unit": f.get("unit"), "function": f.get("function"), "equation": f.get("equation")} for f in method.get("functions", [])]
                rec["evidence"].append(f"ONL method {method.get('entry_name')!r} linked through PGF analysis name")
        if protocol:
            rec["protocol_technique"] = protocol.get("technique")
            rec["protocol_confidence"] = protocol.get("confidence")
            rec["protocol_evidence"] = protocol.get("evidence")
            ptech = str(protocol.get("technique") or "Unknown HEKA")
            pconf = float(protocol.get("confidence") or 0)
            for ev in protocol.get("evidence", []):
                rec["evidence"].append(f"PGF: {ev}")
            # Explicit PGF semantics supersede a weaker generic trace/title guess, but spatial
            # regulation/scan categories remain primary when the Series is itself a scan action.
            spatial_primary = rec.get("technique") in {"Approach curve", "Distance calibration", "Shear-force", "Time scan", "X scan", "Y scan", "Z scan", "2D matrix scan", "3D scan", "Line scan", "Scanning Kelvin probe", "Position/Takedata"}
            if ptech not in {"Unknown HEKA", "Generic pulse/hold"} and pconf >= 0.90 and (not spatial_primary or rec.get("technique") == "Unknown HEKA"):
                rec["technique"] = ptech
                rec["confidence"] = round(max(float(rec.get("confidence") or 0), pconf), 4)
        for ti in rec.get("trace_indices", []):
            tr = parser.traces[int(ti)]
            tr.technique_assignment = rec.get("technique")
            tr.technique_confidence = rec.get("confidence")
            tr.technique_evidence = list(rec.get("evidence") or [])


def analyze(parser: Any) -> Dict[str, Any]:
    semantics = _sem.decode_parser_sections(parser)
    if not bytes(getattr(parser, "pul", b"") or b""):
        result = _support_only_summary(parser, semantics)
        parser.heka_v209 = result
        parser.heka_v208 = result  # backward session compatibility only
        parser.metadata = dict(getattr(parser, "metadata", {}) or {})
        parser.metadata.update({"heka_parser_schema": PARSER_SCHEMA, "heka_parser_version": PARSER_VERSION, "semantic_sections": semantics.get("inventory", []), "warnings": result.get("warnings", [])})
        return result

    hierarchy = parse_hierarchy(parser)
    classifications = classify_series(parser, hierarchy)
    _augment_classifications_with_semantics(parser, hierarchy, classifications, semantics)
    _assign_acquisition_points(parser, classifications)
    spectra = build_eis_spectra(parser, classifications)
    warnings = list(hierarchy.get("warnings") or []) + list(semantics.get("warnings") or [])
    for spectrum in spectra:
        for msg in spectrum.get("warnings", []):
            warnings.append(asdict(HekaWarning("incomplete_eis_point", msg, str(getattr(parser, "source_path", "")), group=str(spectrum.get("group_title") or spectrum.get("group_index")))))
    counts = Counter(str(r.get("technique") or "Unknown HEKA") for r in classifications)
    modalities = Counter(str(r.get("modality") or "") for r in classifications if r.get("modality"))
    unsupported = []
    for item in semantics.get("inventory", []):
        if item.get("semantic_status") in {"preserved_raw", "unsupported_or_malformed"} and item.get("section") not in {".dat", ".pul"}:
            unsupported.append(dict(item))
    result = {
        "schema": PARSER_SCHEMA, "parser_version": PARSER_VERSION,
        "source_file": str(getattr(parser, "source_path", "") or ""),
        "bundle_version": hierarchy.get("root", {}).get("bundle_version_text", ""),
        "hierarchy": hierarchy, "series_classifications": classifications, "eis_spectra": spectra,
        "technique_counts": dict(counts), "modalities": dict(modalities), "warnings": warnings,
        "unsupported_sections": unsupported, "semantic_sections": semantics,
        "summary": {
            "groups": int(hierarchy.get("level_counts", {}).get("Group", 0)),
            "series": int(hierarchy.get("level_counts", {}).get("Series", 0)),
            "sweeps": int(hierarchy.get("level_counts", {}).get("Sweep", 0)),
            "traces": int(hierarchy.get("level_counts", {}).get("Trace", 0)),
            "detected_techniques": sorted(counts),
            "cv_series": int(counts.get("CV", 0) + counts.get("FSCV", 0)),
            "lsv_series": int(counts.get("LSV", 0)), "eis_spectra": len(spectra),
            "scan_points": max([int(r.get("point_index") or 0) for r in classifications] or [0]),
            "warnings": len(warnings), "unsupported_sections": len(unsupported),
            "semantic_sections": len(semantics.get("sections", {})),
            "pgf_stimulations": int(semantics.get("sections", {}).get(".pgf", {}).get("counts", {}).get("stimulations", 0)),
            "onl_methods": int(semantics.get("sections", {}).get(".onl", {}).get("counts", {}).get("methods", 0)),
            "onl_functions": int(semantics.get("sections", {}).get(".onl", {}).get("counts", {}).get("functions", 0)),
        },
    }
    parser.heka_v209 = result
    parser.heka_v208 = result  # old sessions and downstream code can still resolve the result
    parser.heka_hierarchy = hierarchy
    parser.heka_series_classifications = classifications
    parser.heka_eis_spectra = spectra
    parser.metadata = dict(getattr(parser, "metadata", {}) or {})
    parser.metadata.update({
        "heka_parser_schema": PARSER_SCHEMA, "heka_parser_version": PARSER_VERSION,
        "bundle_version": result.get("bundle_version"), "hierarchy_counts": hierarchy.get("level_counts"),
        "technique_counts": dict(counts), "modalities": dict(modalities), "warnings": warnings,
        "unsupported_sections": unsupported, "semantic_sections": semantics.get("inventory", []),
    })
    return result

def compact_summary(parser: Any) -> Dict[str, Any]:
    result = getattr(parser, "heka_v209", None) or analyze(parser)
    rows = []
    for item in result.get("support_rows", []):
        rows.append({
            "index": int(item.get("index") or 0),
            "title": f"{item.get('section')} — {item.get('name') or 'unnamed'}",
            "method_id": item.get("name") or item.get("section") or "HEKA support record",
            "technique": item.get("technique") or ("Online analysis" if item.get("section") == "ONL" else "HEKA support"),
            "n_curves": int(item.get("function_count") or 0),
            "status": f"confidence={float(item.get('confidence') or 0):.2f}" if item.get("confidence") is not None else "complete semantic record",
            "details_text": "; ".join(item.get("evidence") or []) if isinstance(item.get("evidence"), list) else str(item),
        })
    for rec in result.get("series_classifications", []):
        rows.append({
            "index": int(rec.get("series_index") or 0),
            "title": f"Group {int(rec.get('group_index') or 0):02d} — {rec.get('group_title') or 'untitled'} / Series {int(rec.get('series_index_in_group') or 0):03d} — {rec.get('series_title') or 'untitled'}",
            "method_id": rec.get("series_title") or "HEKA Series",
            "technique": rec.get("technique") or "Unknown HEKA",
            "n_curves": int(rec.get("trace_count") or 0),
            "status": f"confidence={float(rec.get('confidence') or 0):.2f}",
            "details_text": "; ".join(rec.get("evidence") or []),
            "group_index": rec.get("group_index"),
            "series_index": rec.get("series_index"),
            "point_index": rec.get("point_index"),
            "modality": rec.get("modality"),
        })
    return {
        "source_file": result.get("source_file"),
        "vendor": "HEKA ElProScan/PotMaster/PATCHMASTER",
        "mode": getattr(parser, "mode", "unknown"),
        "n_measurements": len(rows),
        "technique_counts": result.get("technique_counts", {}),
        "measurement_rows": rows,
        "notes": list(getattr(parser, "notes", []) or []),
        "heka_metadata": _json_safe(getattr(parser, "metadata", {})),
        "heka_v216_summary": result.get("summary", {}),
        "heka_semantic_sections": result.get("semantic_sections", {}).get("inventory", []),
        "heka_parser_warnings": result.get("warnings", []),
    }


def _write_csv(path: Path, rows: Sequence[Dict[str, Any]], fieldnames: Optional[Sequence[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    if fieldnames is None:
        names: List[str] = []
        for row in rows:
            for key in row:
                if key not in names:
                    names.append(key)
        fieldnames = names or ["status"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: _json_safe(row.get(k)) for k in fieldnames})


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(obj), indent=2, ensure_ascii=False), encoding="utf-8")




def _write_raw_h5_resumable(parser: Any, raw_h5: Path, source_meta: Dict[str, Any], warnings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Write the raw-trace H5 with checkpoints and safe resume support.

    Large multi-technique HEKA files can contain thousands of traces.  v216
    flushes every small batch and records throughput/ETA statistics so an
    interrupted export can resume instead of restarting from trace 1.
    """
    import h5py
    total = len(getattr(parser, "traces", []) or [])
    progress_path = raw_h5.with_name(raw_h5.stem + "_progress_v216.json")
    source_sha = str(source_meta.get("sha256") or "")
    started = time.time()
    resumed = 0
    created = 0

    def reset_if_incompatible() -> None:
        nonlocal resumed
        if not raw_h5.exists():
            return
        try:
            with h5py.File(raw_h5, "r") as h5:
                same = str(h5.attrs.get("source_sha256", "")) == source_sha
                has_group = "traces" in h5
                if same and has_group:
                    resumed = len(h5["traces"].keys())
                    return
        except Exception:
            pass
        try:
            raw_h5.unlink()
        except Exception:
            backup = raw_h5.with_suffix(raw_h5.suffix + ".incompatible")
            try:
                if backup.exists(): backup.unlink()
                raw_h5.replace(backup)
            except Exception:
                pass
        resumed = 0

    reset_if_incompatible()
    with h5py.File(raw_h5, "a", libver="latest") as h5:
        h5.attrs["schema"] = PARSER_SCHEMA
        h5.attrs["source_file"] = str(getattr(parser, "source_path", ""))
        h5.attrs["source_sha256"] = source_sha
        h5.attrs["total_traces"] = int(total)
        h5.attrs["complete"] = False
        grp = h5.require_group("traces")
        valid_existing = 0
        for ti, tr in enumerate(getattr(parser, "traces", [])):
            name = f"trace_{ti+1:05d}"
            expected = int(getattr(tr, "n_points", 0) or 0)
            if name in grp:
                try:
                    ds = grp[name]
                    if expected <= 0 or int(ds.shape[0]) == expected:
                        valid_existing += 1
                        continue
                except Exception:
                    pass
                try: del grp[name]
                except Exception: pass
            try:
                arr = np.asarray(parser.read_trace_array(ti), dtype=np.float64)
                ds = grp.create_dataset(name, data=arr, compression="lzf", shuffle=True)
                for key, val in {
                    "label": getattr(tr, "label", ""), "unit": getattr(tr, "y_unit", ""), "x_unit": getattr(tr, "x_unit", ""),
                    "x_increment": getattr(tr, "x_interval", np.nan), "x_start": getattr(tr, "x_start", np.nan),
                    "group_index": getattr(tr, "group_index", 0), "group_title": getattr(tr, "group_title", ""),
                    "series_index": getattr(tr, "series_index", 0), "series_title": getattr(tr, "series_title", ""),
                    "sweep_index": getattr(tr, "sweep_index", 0), "sweep_title": getattr(tr, "sweep_title", ""),
                    "trace_index_in_sweep": getattr(tr, "trace_index_in_sweep", 0), "technique": getattr(tr, "technique_assignment", "Unknown HEKA"),
                }.items():
                    try: ds.attrs[key] = val if val is not None else ""
                    except Exception: ds.attrs[key] = str(val)
                created += 1
            except Exception as exc:
                warnings.append(asdict(HekaWarning("raw_trace_export_failed", str(exc), str(getattr(parser, "source_path", "")), trace=str(getattr(tr, "label", ti+1)))))
            done = valid_existing + created
            if done % 25 == 0 or done == total:
                elapsed = max(0.001, time.time() - started)
                rate = created / elapsed if created else 0.0
                remaining = max(0, total - done)
                eta = remaining / rate if rate > 0 else None
                h5.attrs["completed_traces"] = int(done)
                h5.attrs["last_checkpoint_utc"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                h5.flush()
                _write_json(progress_path, {
                    "schema": "faradaiq-heka-raw-h5-progress-v216",
                    "source_sha256": source_sha, "total_traces": total,
                    "completed_traces": done, "resumed_traces": valid_existing,
                    "new_traces_written": created, "elapsed_seconds": elapsed,
                    "traces_per_second": rate, "eta_seconds": eta,
                    "output_bytes": raw_h5.stat().st_size if raw_h5.exists() else 0,
                    "status": "running",
                })
        done = len(grp.keys())
        h5.attrs["completed_traces"] = int(done)
        h5.attrs["complete"] = bool(done == total)
        h5.attrs["completed_utc"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        h5.flush()
    elapsed = max(0.001, time.time() - started)
    payload = {
        "schema": "faradaiq-heka-raw-h5-progress-v216",
        "source_sha256": source_sha, "total_traces": total,
        "completed_traces": total, "resumed_traces": valid_existing,
        "new_traces_written": created, "elapsed_seconds": elapsed,
        "traces_per_second": created / elapsed if created else 0.0,
        "eta_seconds": 0.0, "output_bytes": raw_h5.stat().st_size,
        "status": "complete",
    }
    _write_json(progress_path, payload)
    return {**payload, "progress_path": str(progress_path)}

def _sweep_pairs(parser: Any, series_rec: Dict[str, Any], hierarchy: Dict[str, Any]) -> Iterable[Tuple[Dict[str, Any], Optional[int], Optional[int]]]:
    target = next((s for s in _iter_nodes(hierarchy["root"], "Series") if int(s.get("index_global") or 0) == int(series_rec.get("series_index") or 0)), None)
    if not target:
        return
    for sweep in [c for c in target.get("children", []) if c.get("kind") == "Sweep"]:
        tis = _trace_indices(sweep)
        yield sweep, _first_by_role(parser, tis, "potential"), _first_by_role(parser, tis, "current")


def export_structured(parser: Any, output_dir: Path, base_name: str) -> Dict[str, Any]:
    result = getattr(parser, "heka_v209", None) or analyze(parser)
    base = _safe_name(base_name, 120)
    root = Path(output_dir) / f"{base}_HEKA_v216"
    root.mkdir(parents=True, exist_ok=True)
    processed = root / "processed"
    records: List[Dict[str, Any]] = []
    files: List[str] = []
    warnings = list(result.get("warnings") or [])

    # v216: preserve decoded Takedata/ElProScan coordinates in every processed
    # technique record.  The sequence model is built from the same DAT/PUL
    # hierarchy and short Takedata records used by the traditional exporter.
    point_coordinates: Dict[int, Dict[str, Any]] = {}
    try:
        seq_builder = getattr(parser, "build_sequence_model", None)
        sequence_model = seq_builder() if callable(seq_builder) else None
        if not isinstance(sequence_model, dict):
            sequence_model = {}
        for point_row in sequence_model.get("points", []) or []:
            point_index = int(point_row.get("point_index") or 0)
            td = point_row.get("takedata") or {}
            if point_index <= 0:
                continue
            source = str(td.get("source") or sequence_model.get("takedata_source") or "HEKA Takedata")
            synthetic = "synthetic" in source.lower() or "no_heka_takedata_records" in source.lower()
            x = _finite(td.get("x_m")); y = _finite(td.get("y_m")); z = _finite(td.get("z_m"))
            if synthetic:
                # Synthetic X/Y values are retained only in the traditional
                # pySECCM compatibility export. The canonical browser uses the
                # ordered point index and never presents those values as measured.
                x = y = z = None
            finite_xy = x is not None and y is not None
            status = "decoded physical X/Y/Z from HEKA Takedata" if finite_xy else "ordered point only; physical X/Y coordinates unavailable"
            point_coordinates[point_index] = {
                "x_position": x, "y_position": y, "z_position": z,
                "coordinate_status": status, "coordinate_source": source,
            }
    except Exception as exc:
        warnings.append(asdict(HekaWarning("coordinate_decode_failed", f"Could not attach Takedata coordinates: {exc}", str(getattr(parser, "source_path", "")))))

    def point_coordinate(point_index: int) -> Dict[str, Any]:
        return dict(point_coordinates.get(int(point_index or 0)) or {
            "x_position": None, "y_position": None, "z_position": None,
            "coordinate_status": "ordered point only; physical X/Y coordinates unavailable",
            "coordinate_source": "",
        })

    source_path = None
    try:
        p = Path(str(getattr(parser, "source_path", "")))
        if p.exists(): source_path = p
    except Exception:
        source_path = None
    source_meta = {
        "original_source_filename": source_path.name if source_path else str(getattr(parser, "source_path", "")),
        "original_source_path": str(source_path or getattr(parser, "source_path", "")),
        "sha256": _sha256(source_path),
        "size_bytes": source_path.stat().st_size if source_path and source_path.exists() else None,
        "bundle_version": result.get("bundle_version"),
        "parser_schema": PARSER_SCHEMA,
        "parser_version": PARSER_VERSION,
    }
    _write_json(root / "source_and_checksum.json", source_meta); files.append(str(root / "source_and_checksum.json"))
    _write_json(root / "heka_hierarchy.json", result.get("hierarchy")); files.append(str(root / "heka_hierarchy.json"))
    _write_json(root / "heka_metadata.json", getattr(parser, "metadata", {})); files.append(str(root / "heka_metadata.json"))
    _write_json(root / "heka_parser_warnings.json", warnings); files.append(str(root / "heka_parser_warnings.json"))
    _write_json(root / "heka_unsupported_sections.json", result.get("unsupported_sections", [])); files.append(str(root / "heka_unsupported_sections.json"))

    # Complete semantic exports for the supported HEKA section versions.
    semantic_root = root / "semantic_sections"
    semantic = result.get("semantic_sections", {}) or {}
    _write_json(semantic_root / "semantic_inventory.json", semantic.get("inventory", [])); files.append(str(semantic_root / "semantic_inventory.json"))
    for ext, decoded in sorted((semantic.get("sections", {}) or {}).items()):
        tag = str(ext).lstrip(".").lower()
        section_json = semantic_root / f"{tag}_complete_semantics.json"
        _write_json(section_json, decoded); files.append(str(section_json))
        if ext == ".pgf":
            stim_rows=[]; channel_rows=[]; segment_rows=[]; protocol_rows=[]
            for stim in decoded.get("stimulations", []):
                stim_rows.append({k:v for k,v in stim.items() if k not in {"channels"}})
                for ch in stim.get("channels", []):
                    channel_rows.append({"stimulation_index":stim.get("stimulation_index"),"stimulation_name":stim.get("entry_name"), **{k:v for k,v in ch.items() if k not in {"segments"}}})
                    for seg in ch.get("segments", []):
                        segment_rows.append({"stimulation_index":stim.get("stimulation_index"),"stimulation_name":stim.get("entry_name"),"channel_index":ch.get("channel_index"), **seg})
            for protocol in decoded.get("protocols", []):
                for ch in protocol.get("channels", []):
                    for sweep in ch.get("sweeps", []):
                        for seg in sweep.get("segments", []):
                            protocol_rows.append({"stimulation_index":protocol.get("stimulation_index"),"entry_name":protocol.get("entry_name"),"analysis_name":protocol.get("analysis_name"),"technique":protocol.get("technique"),"confidence":protocol.get("confidence"),"channel_index":ch.get("channel_index"),"sweep_index":sweep.get("sweep_index"), **seg})
            for name,rows in (("pgf_stimulations.csv",stim_rows),("pgf_channels.csv",channel_rows),("pgf_segments.csv",segment_rows),("pgf_reconstructed_protocols.csv",protocol_rows)):
                q=semantic_root/name; _write_csv(q,rows); files.append(str(q))
        elif ext == ".onl":
            method_rows=[]; function_rows=[]; graph_rows=[]; graph_entry_rows=[]
            for method in decoded.get("methods", []):
                method_rows.append({k:v for k,v in method.items() if k not in {"graphs","functions","headers","last_x_min","last_x_max","last_y_min","last_y_max"}})
                for fn in method.get("functions", []): function_rows.append({"method_index":method.get("method_index"),"method_name":method.get("entry_name"), **fn})
                for graph in method.get("graphs", []):
                    graph_rows.append({"method_index":method.get("method_index"),"method_name":method.get("entry_name"), **{k:v for k,v in graph.items() if k not in {"entries","x_scaling","y_scaling"}}, **{f"x_{k}":v for k,v in graph.get("x_scaling",{}).items()}, **{f"y_{k}":v for k,v in graph.get("y_scaling",{}).items()}})
                    for ei,entry in enumerate(graph.get("entries", []),1): graph_entry_rows.append({"method_index":method.get("method_index"),"method_name":method.get("entry_name"),"graph_index":graph.get("graph_index"),"entry_index":ei,**entry})
            for name,rows in (("onl_methods.csv",method_rows),("onl_functions.csv",function_rows),("onl_graphs.csv",graph_rows),("onl_graph_entries.csv",graph_entry_rows)):
                q=semantic_root/name; _write_csv(q,rows); files.append(str(q))

    # Preserve embedded HEKA metadata/support sections byte-for-byte. The large
    # sampled .dat payload is represented by the decoded raw-trace HDF5 and the
    # original source checksum; .pul and optional sections are exported here.
    raw_section_dir = root / "raw_embedded_sections"
    raw_section_inventory = []
    for ext, sec in sorted((getattr(parser, "sections", {}) or {}).items()):
        if str(ext).lower() == ".dat":
            continue
        try:
            data = (getattr(parser, "section_bytes", {}) or {}).get(ext)
            if data is None and source_path and source_path.exists():
                with source_path.open("rb") as fh:
                    fh.seek(int(getattr(sec, "offset", 0)))
                    data = fh.read(int(getattr(sec, "length", 0)))
            data = bytes(data or b"")
            expected = int(getattr(sec, "length", len(data)) or len(data))
            if len(data) != expected:
                raise IOError(f"expected {expected} bytes, read {len(data)}")
            raw_section_dir.mkdir(parents=True, exist_ok=True)
            safe_ext = re.sub(r"[^a-z0-9]+", "_", str(ext).lower()).strip("_") or "section"
            out_section = raw_section_dir / f"embedded_{safe_ext}.bin"
            out_section.write_bytes(data)
            files.append(str(out_section))
            raw_section_inventory.append({"section": ext, "offset": int(getattr(sec, "offset", 0)), "length": expected, "sha256": hashlib.sha256(data).hexdigest(), "relative_path": out_section.relative_to(root).as_posix()})
        except Exception as exc:
            warnings.append(asdict(HekaWarning("raw_section_export_failed", f"{ext}: {exc}", str(getattr(parser, "source_path", "")))))
    _write_json(root / "heka_raw_section_inventory.json", raw_section_inventory); files.append(str(root / "heka_raw_section_inventory.json"))

    evidence_rows = []
    for rec in result.get("series_classifications", []):
        evidence_rows.append({
            "group_index": rec.get("group_index"), "group_title": rec.get("group_title"),
            "series_index": rec.get("series_index"), "series_index_in_group": rec.get("series_index_in_group"),
            "series_title": rec.get("series_title"), "technique": rec.get("technique"),
            "modality": rec.get("modality"), "point_index": rec.get("point_index"),
            "confidence": rec.get("confidence"), "evidence": " | ".join(rec.get("evidence") or []),
            "channel_labels": " | ".join(rec.get("channel_labels") or []),
            "pgf_stimulation_index": rec.get("pgf_stimulation_index"), "pgf_stimulation_name": rec.get("pgf_stimulation_name"),
            "pgf_analysis_name": rec.get("pgf_analysis_name"), "protocol_technique": rec.get("protocol_technique"),
            "protocol_frequency_Hz": rec.get("protocol_frequency_Hz"), "onl_method_name": rec.get("onl_method_name"),
        })
    _write_csv(root / "heka_technique_evidence.csv", evidence_rows); files.append(str(root / "heka_technique_evidence.csv"))
    _write_json(root / "heka_technique_evidence.json", result.get("series_classifications", [])); files.append(str(root / "heka_technique_evidence.json"))

    trace_rows = []
    for ti, tr in enumerate(getattr(parser, "traces", [])):
        trace_rows.append({
            "trace_index": ti + 1,
            "group_index": getattr(tr, "group_index", None), "group_title": getattr(tr, "group_title", ""),
            "series_index": getattr(tr, "series_index", None), "series_index_in_group": getattr(tr, "series_index_in_parent", None), "series_title": getattr(tr, "series_title", ""),
            "sweep_index": getattr(tr, "sweep_index", None), "sweep_index_in_series": getattr(tr, "sweep_index_in_parent", None), "sweep_title": getattr(tr, "sweep_title", ""),
            "trace_index_in_sweep": getattr(tr, "trace_index_in_sweep", None), "trace_title": getattr(tr, "label", ""),
            "channel_role": _channel_role(tr), "physical_unit": getattr(tr, "y_unit", ""), "x_unit": getattr(tr, "x_unit", ""),
            "x_increment": getattr(tr, "x_interval", None), "x_start": getattr(tr, "x_start", None), "sample_count": getattr(tr, "n_points", None),
            "data_offset": getattr(tr, "old_offset", None), "data_format": getattr(tr, "data_format", None), "data_scaler": getattr(tr, "data_scaler", None),
            "acquisition_time": getattr(tr, "acquisition_time", ""), "holding_potential_V": getattr(tr, "holding_potential", ""),
            "stimulus_definition": getattr(tr, "stimulus_definition", ""), "amplifier_range": getattr(tr, "amplifier_range", ""),
            "filter_information": getattr(tr, "filter_information", ""), "recording_mode": getattr(tr, "recording_mode", ""),
            "spatial_x": getattr(tr, "spatial_x", ""), "spatial_y": getattr(tr, "spatial_y", ""), "spatial_z": getattr(tr, "spatial_z", ""),
            "scan_direction": getattr(tr, "scan_direction", ""), "line_number": getattr(tr, "line_number", ""),
            "cycle_number": getattr(tr, "cycle_number", getattr(tr, "sweep_index_in_parent", "")),
            "technique": getattr(tr, "technique_assignment", "Unknown HEKA"),
            "technique_confidence": getattr(tr, "technique_confidence", None), "point_index": getattr(tr, "point_index", None),
        })
    _write_csv(root / "heka_raw_trace_index.csv", trace_rows); files.append(str(root / "heka_raw_trace_index.csv"))

    # One spectrum per HEKA Group. Individual Series remain represented as frequency rows.
    for spectrum in result.get("eis_spectra", []):
        gi = int(spectrum.get("group_index") or 0); pt = int(spectrum.get("point_index") or 0)
        coord = point_coordinate(pt)
        gname = _safe_name(spectrum.get("group_title") or f"group_{gi:03d}")
        path = processed / f"group_{gi:03d}_{gname}" / "EIS" / f"point_{pt:03d}" / f"EIS_group_{gi:03d}_point_{pt:03d}_Nyquist_Bode.csv"
        eis_rows = []
        for source_row in spectrum.get("rows") or []:
            row = dict(source_row)
            row.update({
                "point_index": pt,
                "x_position_m": coord.get("x_position"),
                "y_position_m": coord.get("y_position"),
                "z_position_m": coord.get("z_position"),
            })
            eis_rows.append(row)
        _write_csv(path, eis_rows, fieldnames=["frequency_Hz","frequency_source","Zreal_ohm","Zimag_ohm","neg_Zimag_ohm","Zmod_ohm","phase_deg","Yreal_S","Yimag_S","Yabs_S","bias_potential_V","monitor_current_A","series_index","series_index_in_group","series_title","point_index","x_position_m","y_position_m","z_position_m"])
        files.append(str(path))
        records.append({
            "relative_csv_path": path.relative_to(root).as_posix(), "technique": "EIS", "role": "HEKA processed impedance spectrum",
            "point": pt, "method": 1, "group_index": gi, "group_title": spectrum.get("group_title", ""),
            "series_index": 0, "series_title": "EIS frequency Series aggregated by Group", "sweep_index": 0,
            "modality": next((r.get("modality") for r in result.get("series_classifications", []) if int(r.get("group_index") or 0) == gi and r.get("technique") == "EIS"), ""),
            "confidence": 1.0, "evidence": ["Real(Z)/Imag(Z) processed channels aggregated across EIS Series", "frequency reconstructed from excitation traces when not stored"],
            "x_label": "frequency", "x_unit": "Hz", "y_label": "impedance", "y_unit": "ohm",
            "x_position": coord.get("x_position"), "y_position": coord.get("y_position"), "z_position": coord.get("z_position"),
            "coordinate_status": coord.get("coordinate_status"), "coordinate_source": coord.get("coordinate_source"),
            "default_plot": "Nyquist: Zreal_ohm versus neg_Zimag_ohm", "available_views": ["Nyquist", "Bode magnitude", "Bode phase", "admittance", "raw excitation"],
            "heka_v216": True, "heka_v211": True, "heka_v210": True, "heka_v209": True, "canonical_key": f"HEKA:EIS:group{gi:03d}",
        })

    cycle_rows = []
    spatial_rows = []
    for rec in result.get("series_classifications", []):
        tech = str(rec.get("technique") or "Unknown HEKA")
        if tech == "EIS":
            continue
        gi = int(rec.get("group_index") or 0); si = int(rec.get("series_index_in_group") or 0); point = int(rec.get("point_index") or 0)
        gname = _safe_name(rec.get("group_title") or f"group_{gi:03d}")
        sname = _safe_name(rec.get("series_title") or f"series_{si:03d}")
        modality = str(rec.get("modality") or "")
        coord = point_coordinate(point)
        spatial_rows.append({
            "group_index": gi, "group_title": rec.get("group_title"), "point_index": point,
            "series_index_in_group": si, "series_title": rec.get("series_title"),
            "technique": tech, "modality": modality,
            "x_m": coord.get("x_position"), "y_m": coord.get("y_position"), "z_m": coord.get("z_position"),
            "coordinate_status": coord.get("coordinate_status"), "coordinate_source": coord.get("coordinate_source"),
        })
        sweep_num = 0
        wrote_any = False
        for sweep, pot_ti, cur_ti in _sweep_pairs(parser, rec, result["hierarchy"]):
            sweep_num += 1
            cycle = sweep_num
            tis = _trace_indices(sweep)
            sweep_title = str(sweep.get("title") or "")
            sub = processed / f"group_{gi:03d}_{gname}" / _safe_name(tech) / f"point_{point:03d}" / f"series_{si:03d}_{sname}"
            path = sub / f"sweep_{sweep_num:03d}_cycle_{cycle:03d}_{_safe_name(tech)}.csv"
            rows: List[Dict[str, Any]] = []
            default_plot = ""
            x_label = "time"; x_unit = "s"; y_label = "signal"; y_unit = ""
            try:
                if tech in {"CV", "LSV", "FSCV", "Tafel/corrosion"} and pot_ti is not None and cur_ti is not None:
                    e, _ = _trace_values_si(parser, pot_ti)
                    i, _ = _trace_values_si(parser, cur_ti)
                    m = min(len(e), len(i)); t = _time_axis(parser, pot_ti, m)
                    rows = [{
                        "time_s": float(t[k]), "potential_V": float(e[k]), "current_A": float(i[k]),
                        "cycle_index": cycle, "point_index": point, "line_index": None,
                        "x_position_m": coord.get("x_position"), "y_position_m": coord.get("y_position"), "z_position_m": coord.get("z_position"),
                    } for k in range(m)]
                    default_plot = "current_A versus potential_V"; x_label = "potential"; x_unit = "V"; y_label = "current"; y_unit = "A"
                    cycle_rows.append({"group_index": gi, "series_index_in_group": si, "series_title": rec.get("series_title"), "sweep_index": sweep_num, "cycle_index": cycle, "point_index": point, "technique": tech, "potential_trace_index": pot_ti + 1, "current_trace_index": cur_ti + 1}) if tech in {"CV", "LSV", "FSCV"} else None
                elif tech in {"CA", "OCP", "CP", "Approach curve", "Distance calibration", "Shear-force", "Time scan", "X scan", "Y scan", "Z scan", "2D matrix scan", "3D scan", "Line scan", "Scanning Kelvin probe", "Position/Takedata", "Unknown HEKA"}:
                    arrays = []
                    n = None
                    for ti in tis:
                        arr, unit = _trace_values_si(parser, ti)
                        n = len(arr) if n is None else min(n, len(arr))
                        arrays.append((ti, arr, unit, _channel_role(parser.traces[ti])))
                    n = int(n or 0)
                    if n:
                        x_values, x_axis_unit = _trace_x_axis(parser, tis[0], n)
                        if x_axis_unit == "m" and tech in {"Approach curve", "Distance calibration", "Z scan", "Line scan", "3D scan", "2D matrix scan"}:
                            x_key = "distance_m" if tech in {"Approach curve", "Distance calibration", "Line scan"} else "z_m"
                            x_label = "distance" if x_key == "distance_m" else "Z position"
                            x_unit = "m"
                        else:
                            x_key = "time_s"
                            x_label = "time"
                            x_unit = "s" if x_axis_unit in {"", "unknown", "s"} else x_axis_unit
                        for k in range(n):
                            row = {
                                x_key: float(x_values[k]), "point_index": point,
                                "x_position_m": coord.get("x_position"), "y_position_m": coord.get("y_position"), "z_position_m": coord.get("z_position"),
                            }
                            for ti, arr, unit, role in arrays:
                                name = role if role != "signal" else _safe_name(parser.traces[ti].label, 40)
                                if name in row: name = f"{name}_{ti+1}"
                                row[name] = float(arr[k])
                            rows.append(row)
                        if tech == "CA": default_plot = "current versus time"; y_label = "current"; y_unit = "A"
                        elif tech in {"CP", "OCP"}: default_plot = "potential versus time"; y_label = "potential"; y_unit = "V"
                        elif tech == "Approach curve":
                            default_plot = "signal versus distance" if x_key == "distance_m" else "signal versus acquisition time"
                            y_label = "current" if any(role == "current" for _ti, _arr, _unit, role in arrays) else "signal"
                            y_unit = "A" if y_label == "current" else ""
                        else: default_plot = "signal versus coordinate/time using the stored HEKA X unit"
                if rows:
                    _write_csv(path, rows)
                    files.append(str(path)); wrote_any = True
                    records.append({
                        "relative_csv_path": path.relative_to(root).as_posix(),
                        "technique": "CV / LSV" if tech in {"CV", "FSCV"} else ("LSV" if tech == "LSV" else ("IV / Tafel" if tech == "Tafel/corrosion" else ("Chronoamperometry / CA" if tech == "CA" else ("Chronopotentiometry / CP" if tech == "CP" else ("OCP/OCV" if tech == "OCP" else tech))))),
                        "heka_technique": tech, "role": "HEKA processed Series/Sweep table",
                        "point": point, "initial_setup": bool(rec.get("initial_setup")), "cycle": cycle if tech in {"CV", "LSV", "FSCV"} else 0, "method": si,
                        "scan_rate_mV_s": rec.get("scan_rate_mV_s"), "scan_rate": rec.get("scan_rate_label") or (f"{float(rec.get('scan_rate_mV_s')):g} mV/s" if rec.get("scan_rate_mV_s") is not None else ""),
                        "group_index": gi, "group_title": rec.get("group_title", ""), "series_index": int(rec.get("series_index") or 0), "series_index_in_group": si,
                        "series_title": rec.get("series_title", ""), "sweep_index": sweep_num, "sweep_title": sweep_title,
                        "modality": modality, "confidence": rec.get("confidence"), "evidence": rec.get("evidence"),
                        "x_label": x_label, "x_unit": x_unit, "y_label": y_label, "y_unit": y_unit,
                        "x_position": coord.get("x_position"), "y_position": coord.get("y_position"), "z_position": coord.get("z_position"),
                        "coordinate_status": coord.get("coordinate_status"), "coordinate_source": coord.get("coordinate_source"),
                        "default_plot": default_plot, "available_axes": ["potential", "time", "current", "cycle index", "point index", "line index", "spatial coordinate"],
                        "heka_v216": True, "heka_v211": True, "heka_v210": True, "heka_v209": True, "canonical_key": f"HEKA:{tech}:g{gi:03d}:s{si:03d}:w{sweep_num:03d}",
                    })
            except Exception as exc:
                warnings.append(asdict(HekaWarning("processed_export_failed", str(exc), str(getattr(parser, "source_path", "")), group=rec.get("group_title", ""), series=rec.get("series_title", ""), sweep=str(sweep_num))))
        if not wrote_any:
            warnings.append(asdict(HekaWarning("no_processed_table", "Series metadata was preserved but no reliable processed table could be constructed.", str(getattr(parser, "source_path", "")), group=rec.get("group_title", ""), series=rec.get("series_title", ""))))

    _write_csv(root / "heka_cv_cycle_assignments.csv", cycle_rows); files.append(str(root / "heka_cv_cycle_assignments.csv"))
    _write_csv(root / "heka_spatial_point_table.csv", spatial_rows); files.append(str(root / "heka_spatial_point_table.csv"))
    _write_json(root / "heka_parser_warnings.json", warnings)

    # Raw traces in one HDF5 file. v216 writes checkpoints, a progress/ETA
    # sidecar, and resumes a compatible incomplete file after interruption.
    raw_h5 = root / f"{base}_heka_raw_traces.h5"
    raw_h5_stats: Dict[str, Any] = {}
    try:
        raw_h5_stats = _write_raw_h5_resumable(parser, raw_h5, source_meta, warnings)
        files.append(str(raw_h5))
        if raw_h5_stats.get("progress_path"):
            files.append(str(raw_h5_stats["progress_path"]))
    except Exception as exc:
        warnings.append(asdict(HekaWarning("raw_hdf5_unavailable", f"Raw HDF5 export was not written: {exc}", str(getattr(parser, "source_path", "")))))

    manifest = {
        "schema": PARSER_SCHEMA,
        "version": PARSER_VERSION,
        "source": source_meta,
        "summary": result.get("summary", {}),
        "technique_counts": result.get("technique_counts", {}),
        "modalities": result.get("modalities", {}),
        "hierarchy_counts": result.get("hierarchy", {}).get("level_counts", {}),
        "records": records,
        "warnings": warnings,
        "unsupported_sections": result.get("unsupported_sections", []),
        "raw_embedded_sections": raw_section_inventory,
        "semantic_sections": semantic.get("inventory", []),
        "raw_h5_export": raw_h5_stats,
        "reload_contract": "FaradaIQ canonical builder reads records.relative_csv_path and hierarchy metadata from this manifest.",
    }
    manifest_path = root / f"{base}_heka_v216_manifest.json"
    _write_json(manifest_path, manifest); files.append(str(manifest_path))
    return {
        "schema": PARSER_SCHEMA,
        "root": str(root),
        "manifest": str(manifest_path),
        "files": files,
        "records": records,
        "warnings": warnings,
        "raw_h5": str(raw_h5) if raw_h5.exists() else "",
    }
