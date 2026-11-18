"""Complete semantic decoders for the HEKA record versions supplied with FaradaIQ v273.

Supported exact HEKA ElProScan/PotMaster layouts:
* PGF stimulus tree v1000: record sizes [1144, 248, 400, 80]
* ONL online-analysis tree v11: record sizes [64, 2640, 168]
* AMP amplifier-state tree v1000: record sizes [80, 16, 560]
* SOL solution database v1000: record sizes [88, 160, 40]
* MRK marker tree v9: record sizes [8, 104]
* MTH fixed-length method log records used by the attached ElProScan EIS fixture

Every record is bounds checked and receives an offset, byte count, SHA-256, and raw
hex for reserved/vendor-extension bytes. Unknown enum values are never invented.
"""
from __future__ import annotations

import hashlib
import math
import re
import struct
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA = "faradaiq-heka-semantics-v210"
VERSION = "v273"

SEGMENT_CLASS = ["SegmentConstant", "SegmentRamp", "SegmentContinuous", "SegmentConstSine", "SegmentSquarewave", "SegmentChirpwave"]
INC_MODE = ["ModeInc", "ModeDec", "ModeIncInterleaved", "ModeDecInterleaved", "ModeAlternate", "ModeLogInc", "ModeLogDec", "ModeLogIncInterleaved", "ModeLogDecInterleaved", "ModeLogAlternate"]
EXT_TRIGGER = ["TrigNone", "TrigSeries", "TrigSweep", "TrigSweepNoLeak"]
AMPL_MODE = ["AnyAmplMode", "VCAmplMode", "CCAmplMode", "IDensityMode"]
AUTORANGE = ["AutoRangingOff", "AutoRangingPeak", "AutoRangingMean", "AutoRangingRelSeg"]
ADC_TYPE = ["AdcOff", "Analog", "Digitals", "Digital", "AdcVirtual"]
LEAK_STORE = ["LNone", "LStoreAvg", "LStoreEach", "LNoStore"]
LEAK_HOLD = ["Labs", "Lrel", "LabsLH", "LrelLH"]
BREAK = ["NoBreak", "BreakPos", "BreakNeg"]
SEG_STORE = ["SegNoStore", "SegStore", "SegStoreStart", "SegStoreEnd"]
FUNCTION_TYPES = [
    "SweepCountAbsc", "TimeAbsc", "TimerAbsc", "RealtimeAbsc", "SegAmplitude", "SegDuration", "ScanRateAbsc",
    "ExtremumMode", "MaximumMode", "MinimumMode", "MeanMode", "IntegralMode", "VarianceMode", "SlopeMode",
    "TimeToExtrMode", "AnodicChargeMode", "CathodChargeMode", "CSlowMode", "RSeriesMode", "UserParam1Mode",
    "UserParam2Mode", "LockInCMMode", "LockInGMMode", "LockInGSMode", "SeriesTime", "StimulusMode", "SegmentTimeAbs",
    "OpEquationMode", "ConstantMode", "OperatorPlusMode", "OperatorMinusMode", "OperatorMultMode", "OperatorDivMode",
    "OperatorAbsMode", "OperatorLogMode", "OperatorSqrMode", "OperatorInvMode", "OperatorInvLogMode", "OperatorInvSqrMode",
    "TraceMode", "QMode", "InvTraceMode", "InvQMode", "LnTraceMode", "LnQMode", "LogTraceMode", "LogQMode",
    "TraceXaxisMode", "FreqMode", "DensityMode", "HistoAmplMode", "HistoBinsMode", "OnlineIndex", "ExtrAmplMode",
    "SegmentTimeRel", "CellPotential", "SealResistance", "RsValue", "GLeak", "MConductance", "Temperature",
    "PipettePressure", "InternalSolution", "ExternalSolution", "DigitalIn", "OperatorBitInMode", "ReversalMode",
    "LockInPhase", "LockInFreq", "TotMeanMode", "DiffMode", "IntSolValue", "ExtSolValue", "OperatorAtanMode",
    "OperatorInvAtanMode", "TimeToMinMode", "TimeToMaxMode", "TimeToThreshold", "TraceEquationMode", "ThresholdAmpl",
    "XposMode", "YposMode", "ZposMode", "TraceCountMode", "AP_Baseline", "AP_MaximumAmpl", "AP_MaximumTime",
    "AP_MinimumAmpl", "AP_MinimumTime", "AP_RiseTime1Dur", "AP_RiseTime1Slope", "AP_RiseTime1Time", "AP_RiseTime2Dur",
    "AP_RiseTime2Slope", "AP_RiseTime2Time", "AP_Tau", "MatrixXindexMode", "MatrixYindexMode", "YatX_Mode",
    "ThresholdCount", "SECM_3Dx", "SECM_3Dy", "InterceptMode", "MinAmplMode", "MaxAmplMode", "TauMode", "ZeroOffset",
    "LockInAmpl", "UserParam3Mode", "UserParam4Mode", "UserParam5Mode", "UserParam6Mode", "UserParam7Mode",
    "UserParam8Mode", "UserParam9Mode", "UserParam10Mode",
]
TIC = ["TicLeft", "TicRight", "TicBoth"]
SCALE = ["ScaleFixed", "ScaleSeries", "ScaleSweeps"]
AXIS_LEVEL = ["Min", "Zero", "Max"]
AXIS_TYPE = ["ScaleLinear", "ScaleLog", "ScaleInverse", "ScaleSqrt", "ScaleSquare"]
MARKER = ["MarkerPoint", "MarkerPlus", "MarkerStar", "MarkerDiamond", "MarkerX", "MarkerSquare"]
NORMALIZE = ["NormalizeNone", "NormalizeMax", "NormalizeMinMax"]
CURSOR = ["Cursor_Segment", "Cursor_Trace"]
BASELINE = ["Baseline_Zero", "Baseline_Cursors", "Baseline_Auto"]
SEARCH = ["Search_All", "Search_Positive", "Search_Negative"]
AMP_TYPES = ["Epc7Ampl", "Epc8Ampl", "Epc9Ampl", "Epc10Ampl", "Epc10PlusAmpl"]
AD_BOARDS = ["ITC16Board", "ITC18Board", "LIH1600Board"]
AMP_MODES = ["TestMode", "VCMode", "CCMode", "NoMode"]
MARKER_KINDS = ["MarkerText", "MarkerComment", "MarkerSet", "MarkerClear"]


class HekaSemanticError(ValueError):
    pass


def _check(buf: bytes, off: int, size: int, name: str = "field") -> None:
    if off < 0 or size < 0 or off + size > len(buf):
        raise HekaSemanticError(f"Truncated {name}: need bytes {off}:{off+size}, record has {len(buf)}")


def _cstr(b: bytes, o: int, n: int) -> str:
    _check(b, o, n, "string")
    return b[o:o+n].split(b"\0", 1)[0].decode("latin-1", "replace").strip()


def _unpack(b: bytes, o: int, fmt: str, e: str = "<") -> Any:
    size = struct.calcsize(fmt)
    _check(b, o, size, fmt)
    return struct.unpack_from(e + fmt, b, o)[0]


def _i32(b: bytes, o: int, e: str = "<") -> int: return int(_unpack(b, o, "i", e))
def _u32(b: bytes, o: int, e: str = "<") -> int: return int(_unpack(b, o, "I", e))
def _i16(b: bytes, o: int, e: str = "<") -> int: return int(_unpack(b, o, "h", e))
def _u16(b: bytes, o: int, e: str = "<") -> int: return int(_unpack(b, o, "H", e))
def _f32(b: bytes, o: int, e: str = "<") -> float: return float(_unpack(b, o, "f", e))
def _f64(b: bytes, o: int, e: str = "<") -> float: return float(_unpack(b, o, "d", e))
def _u8(b: bytes, o: int) -> int: _check(b, o, 1); return int(b[o])
def _i8(b: bytes, o: int) -> int: return int(_unpack(b, o, "b", ""))
def _bool8(b: bytes, o: int) -> bool: return bool(_u8(b, o))
def _enum(values: Sequence[str], value: int, family: str = "enum") -> str:
    return values[value] if 0 <= value < len(values) else f"VendorExtension({family}={value})"


def _record_meta(record: bytes, offset: int, level: int, child_count: int) -> Dict[str, Any]:
    return {
        "record_offset": offset,
        "record_size": len(record),
        "tree_level": level,
        "child_count": child_count,
        "record_sha256": hashlib.sha256(record).hexdigest(),
    }


def parse_tree(data: bytes, expected_sizes: Optional[Sequence[int]] = None, section: str = "tree") -> Dict[str, Any]:
    if len(data) < 12:
        raise HekaSemanticError(f"{section} is too short for a HEKA tree header")
    if data[:4] == b"eerT":
        e = "<"
    elif data[:4] == b"Tree":
        e = ">"
    else:
        raise HekaSemanticError(f"{section} has unsupported tree magic {data[:4]!r}")
    levels = _i32(data, 4, e)
    if not 1 <= levels <= 16:
        raise HekaSemanticError(f"{section} declares invalid level count {levels}")
    header_size = 8 + 4 * levels
    _check(data, 0, header_size, f"{section} header")
    sizes = [_i32(data, 8 + 4 * k, e) for k in range(levels)]
    if any(s <= 0 or s > 4_000_000 for s in sizes):
        raise HekaSemanticError(f"{section} declares invalid record sizes {sizes}")
    if expected_sizes is not None and list(expected_sizes) != sizes:
        raise HekaSemanticError(f"{section} record sizes {sizes} do not match supported attached layout {list(expected_sizes)}")
    pos = header_size
    count_by_level = [0] * levels

    def walk(level: int) -> Dict[str, Any]:
        nonlocal pos
        if level >= levels:
            raise HekaSemanticError(f"{section} descends beyond declared level count")
        off = pos
        size = sizes[level]
        _check(data, off, size + 4, f"{section} level-{level} record")
        record = data[off:off+size]
        pos += size
        children = _i32(data, pos, e)
        pos += 4
        if children < 0 or children > 10_000_000:
            raise HekaSemanticError(f"{section} level-{level} record at {off} has invalid child count {children}")
        count_by_level[level] += 1
        node = {"level": level, "offset": off, "size": size, "child_count": children, "record": record, "children": []}
        if level == levels - 1 and children:
            raise HekaSemanticError(f"{section} leaf record at {off} unexpectedly has {children} children")
        for _ in range(children):
            node["children"].append(walk(level + 1))
        return node

    root = walk(0)
    if pos != len(data):
        raise HekaSemanticError(f"{section} semantic tree consumed {pos} of {len(data)} bytes; {len(data)-pos} unexplained bytes remain")
    return {
        "schema": SCHEMA,
        "section": section,
        "endian": "little" if e == "<" else "big",
        "endian_char": e,
        "tree_magic": data[:4].decode("latin-1"),
        "level_count": levels,
        "record_sizes": sizes,
        "record_counts": count_by_level,
        "header_size": header_size,
        "parsed_bytes": pos,
        "total_bytes": len(data),
        "root": root,
    }


def _decode_pgf_segment(r: bytes, e: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    code_class = _u8(r, 4); code_store = _u8(r, 5); code_vi = _u8(r, 6); code_di = _u8(r, 7)
    return {
        **meta, "mark": _i32(r, 0, e),
        "class_code": code_class, "class": _enum(SEGMENT_CLASS, code_class, "SegmentClass"),
        "store_kind_code": code_store, "store_kind": _enum(SEG_STORE, code_store, "StoreKind"),
        "voltage_increment_mode_code": code_vi, "voltage_increment_mode": _enum(INC_MODE, code_vi, "IncrementMode"),
        "duration_increment_mode_code": code_di, "duration_increment_mode": _enum(INC_MODE, code_di, "IncrementMode"),
        "voltage": _f64(r, 8, e), "voltage_source": _i32(r, 16, e),
        "delta_v_factor": _f64(r, 20, e), "delta_v_increment": _f64(r, 28, e),
        "duration_s": _f64(r, 36, e), "duration_source": _i32(r, 44, e),
        "delta_t_factor": _f64(r, 48, e), "delta_t_increment_s": _f64(r, 56, e),
        "filler1": _i32(r, 64, e), "crc": _u32(r, 68, e), "scan_rate": _f64(r, 72, e),
    }


def _decode_pgf_channel(r: bytes, e: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    sid = _u16(r, 76, e); cm = _u16(r, 78, e); break_code = _u8(r, 87)
    return {
        **meta, "mark": _i32(r, 0, e), "linked_channel": _i32(r, 4, e),
        "compression_factor": _i32(r, 8, e), "y_unit": _cstr(r, 12, 8),
        "adc_channel": _i16(r, 20, e), "adc_mode_code": _u8(r, 22), "adc_mode": _enum(ADC_TYPE, _u8(r, 22), "AdcType"),
        "do_write": _bool8(r, 23), "leak_store_code": _u8(r, 24), "leak_store": _enum(LEAK_STORE, _u8(r, 24), "LeakStore"),
        "amplifier_mode_code": _u8(r, 25), "amplifier_mode": _enum(AMPL_MODE, _u8(r, 25), "AmplifierMode"),
        "own_segment_time": _bool8(r, 26), "set_last_segment_vmemb": _bool8(r, 27),
        "dac_channel": _i16(r, 28, e), "dac_mode": _u8(r, 30), "has_lockin_square": _u8(r, 31),
        "relevant_x_segment": _i32(r, 32, e), "relevant_y_segment": _i32(r, 36, e), "dac_unit": _cstr(r, 40, 8),
        "holding": _f64(r, 48, e), "leak_holding": _f64(r, 56, e), "leak_size": _f64(r, 64, e),
        "leak_hold_mode_code": _u8(r, 72), "leak_hold_mode": _enum(LEAK_HOLD, _u8(r, 72), "LeakHoldMode"),
        "leak_alternate": _bool8(r, 73), "alternate_leak_averaging": _bool8(r, 74), "leak_pulse_on": _bool8(r, 75),
        "stim_to_dac_id": sid,
        "stim_to_dac_flags": {k: bool(sid & (1 << bit)) for bit, k in [(0, "use_stim_scale"), (1, "use_relative"), (2, "use_file_template"), (3, "use_for_lockin"), (4, "use_for_wavelength"), (5, "use_scaling"), (6, "use_for_chirp"), (7, "use_for_imaging"), (14, "reserved14"), (15, "reserved15")]},
        "compression_mode": cm, "compression_flags": {"store_real": bool(cm & 1), "use_mean": bool(cm & 2), "use_digital_filter": bool(cm & 4)},
        "compression_skip": _i32(r, 80, e), "dac_bit": _i16(r, 84, e), "has_lockin_sine": _bool8(r, 86),
        "break_mode_code": break_code, "break_mode": _enum(BREAK, break_code, "BreakMode"),
        "zero_segment": _i32(r, 88, e), "stim_sweep": _i32(r, 92, e),
        "sine_cycle": _f64(r, 96, e), "sine_amplitude": _f64(r, 104, e), "lockin_v_reversal": _f64(r, 112, e),
        "chirp_start_frequency_hz": _f64(r, 120, e), "chirp_end_frequency_hz": _f64(r, 128, e), "chirp_min_points": _f64(r, 136, e),
        "square_negative_amplitude": _f64(r, 144, e), "square_duration_factor": _f64(r, 152, e),
        "lockin_skip": _i32(r, 160, e), "photo_max_cycles": _i32(r, 164, e), "photo_segment_number": _i32(r, 168, e),
        "lockin_average_cycles": _i32(r, 172, e), "imaging_roi_number": _i32(r, 176, e), "chirp_skip": _i32(r, 180, e),
        "chirp_amplitude": _f64(r, 184, e), "photo_adapt": _u8(r, 192), "sine_kind": _u8(r, 193), "chirp_prechirp": _u8(r, 194),
        "sine_source": _u8(r, 195), "square_negative_source": _u8(r, 196), "square_positive_source": _u8(r, 197),
        "chirp_kind": _u8(r, 198), "chirp_source": _u8(r, 199), "dac_offset": _f64(r, 200, e), "adc_offset": _f64(r, 208, e),
        "trace_math_format": _u8(r, 216), "has_chirp": _bool8(r, 217), "square_kind": _u8(r, 218),
        "reserved_219_223_hex": r[219:224].hex(), "square_base_increment": _f64(r, 224, e), "square_cycle": _f64(r, 232, e),
        "square_positive_amplitude": _f64(r, 240, e), "compression_offset": _i32(r, 248, e), "photo_mode": _i32(r, 252, e),
        "break_level": _f64(r, 256, e), "trace_math": _cstr(r, 264, 128), "filler2": _i32(r, 392, e), "crc": _u32(r, 396, e),
    }


def _decode_pgf_stimulation(r: bytes, e: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **meta, "mark": _i32(r, 0, e), "entry_name": _cstr(r, 4, 32), "file_name": _cstr(r, 36, 32),
        "analysis_name": _cstr(r, 68, 32), "data_start_segment": _i32(r, 100, e), "data_start_time_s": _f64(r, 104, e),
        "sample_interval_s": _f64(r, 112, e), "sweep_interval_s": _f64(r, 120, e), "leak_delay_s": _f64(r, 128, e),
        "filter_factor": _f64(r, 136, e), "number_sweeps": _i32(r, 144, e), "number_leaks": _i32(r, 148, e),
        "number_averages": _i32(r, 152, e), "actual_adc_channels": _i32(r, 156, e), "actual_dac_channels": _i32(r, 160, e),
        "external_trigger_code": _u8(r, 164), "external_trigger": _enum(EXT_TRIGGER, _u8(r, 164), "ExternalTrigger"),
        "no_start_wait": _bool8(r, 165), "use_scan_rates": _bool8(r, 166), "no_continuous_acquisition": _bool8(r, 167),
        "has_lockin": _bool8(r, 168), "old_start_macro_kind": _i8(r, 169), "old_end_macro_kind": _bool8(r, 170),
        "auto_range_code": _u8(r, 171), "auto_range": _enum(AUTORANGE, _u8(r, 171), "AutoRange"),
        "break_next": _bool8(r, 172), "is_expanded": _bool8(r, 173), "leak_compensation_mode_hard": _bool8(r, 174),
        "has_chirp": _bool8(r, 175), "old_start_macro": _cstr(r, 176, 32), "old_end_macro": _cstr(r, 208, 32),
        "is_gap_free": _bool8(r, 240), "handled_externally": _bool8(r, 241), "filler1": _bool8(r, 242), "filler2": _bool8(r, 243),
        "crc": _u32(r, 244, e),
    }


def decode_pgf(data: bytes) -> Dict[str, Any]:
    tree = parse_tree(data, [1144, 248, 400, 80], "PGF")
    e = tree["endian_char"]; root_node = tree["root"]; r = root_node["record"]
    root_meta = _record_meta(r, root_node["offset"], 0, root_node["child_count"])
    out: Dict[str, Any] = {
        "schema": SCHEMA, "section": "PGF", "format_version": "v1000", "tree": {k: v for k, v in tree.items() if k not in {"root", "endian_char"}},
        "root": {
            **root_meta, "version": _i32(r, 0, e), "mark": _i32(r, 4, e), "version_name": _cstr(r, 8, 32),
            "max_samples": _i32(r, 40, e), "filler1": _i32(r, 44, e),
            "parameters": [_f64(r, 48 + 8*i, e) for i in range(10)],
            "parameter_text": [_cstr(r, 128 + 32*i, 32) for i in range(10)],
            "reserved_text": _cstr(r, 448, 128), "filler2": _i32(r, 576, e),
            "reserved2_hex": r[580:1140].hex(), "crc": _u32(r, 1140, e),
        },
        "stimulations": [], "warnings": [],
    }
    vendor_break_records = []
    for sidx, sn in enumerate(root_node["children"], 1):
        sr = sn["record"]; st = _decode_pgf_stimulation(sr, e, _record_meta(sr, sn["offset"], 1, sn["child_count"]))
        st["stimulation_index"] = sidx; st["channels"] = []
        for cidx, cn in enumerate(sn["children"], 1):
            cr = cn["record"]; ch = _decode_pgf_channel(cr, e, _record_meta(cr, cn["offset"], 2, cn["child_count"]))
            ch["channel_index"] = cidx; ch["segments"] = []
            if str(ch["break_mode"]).startswith("VendorExtension"):
                vendor_break_records.append({"stimulation_index": sidx, "stimulation_name": st.get("entry_name"), "channel_index": cidx, "code": ch.get("break_mode_code"), "record_offset": ch.get("record_offset")})
            for segidx, en in enumerate(cn["children"], 1):
                er = en["record"]; seg = _decode_pgf_segment(er, e, _record_meta(er, en["offset"], 3, en["child_count"]))
                seg["segment_index"] = segidx; ch["segments"].append(seg)
            st["channels"].append(ch)
        out["stimulations"].append(st)
    if vendor_break_records:
        out["warnings"].append({"code": "pgf_vendor_break_mode", "message": f"{len(vendor_break_records)} PGF channel record(s) use undocumented BreakMode code 3. FaradaIQ preserves the exact code and record bytes without inventing a semantic name.", "records": vendor_break_records})
    out["counts"] = {"stimulations": len(out["stimulations"]), "channels": sum(len(s["channels"]) for s in out["stimulations"]), "segments": sum(len(c["segments"]) for s in out["stimulations"] for c in s["channels"])}
    out["protocols"] = reconstruct_protocols(out)
    return out


def _scaling(r: bytes, e: str) -> Dict[str, Any]:
    return {"min": _f64(r, 0, e), "max": _f64(r, 8, e), "grid_factor": _f64(r, 16, e), "tic_length": _i16(r, 24, e), "tic_number": _i16(r, 26, e), "tic_direction_code": _u8(r, 28), "tic_direction": _enum(TIC, _u8(r, 28), "TicDirection"), "axis_level_code": _u8(r, 29), "axis_level": _enum(AXIS_LEVEL, _u8(r, 29), "AxisLevel"), "axis_type_code": _u8(r, 30), "axis_type": _enum(AXIS_TYPE, _u8(r, 30), "AxisType"), "scale_mode_code": _u8(r, 31), "scale_mode": _enum(SCALE, _u8(r, 31), "ScaleMode"), "no_unit": _bool8(r, 32), "obsolete": _bool8(r, 33), "zero_line": _bool8(r, 34), "grid": _bool8(r, 35), "nice": _bool8(r, 36), "label": _bool8(r, 37), "centered": _bool8(r, 38), "include_zero": _bool8(r, 39)}


def _entry(r: bytes, e: str) -> Dict[str, Any]:
    return {"x_wave": _i16(r, 0, e), "y_wave": _i16(r, 2, e), "marker_size": _i16(r, 4, e), "marker_color_rgb16": [_u16(r, 6, e), _u16(r, 8, e), _u16(r, 10, e)], "marker_kind_code": _u8(r, 12), "marker_kind": _enum(MARKER, _u8(r, 12), "MarkerKind"), "active": _bool8(r, 13), "line": _bool8(r, 14), "trace_color": _bool8(r, 15)}


def _graph(r: bytes, e: str, index: int) -> Dict[str, Any]:
    return {"graph_index": index, "active": _bool8(r, 0), "overlay": _bool8(r, 1), "wrap": _i8(r, 2), "overlay_sweeps": _bool8(r, 3), "normalize_code": _u8(r, 4), "normalize": _enum(NORMALIZE, _u8(r, 4), "Normalize"), "sort": _bool8(r, 5), "reserved_6": _u8(r, 6), "reserved_7": _u8(r, 7), "x_scaling": _scaling(r[8:48], e), "y_scaling": _scaling(r[48:88], e), "entries": [_entry(r[88+16*i:104+16*i], e) for i in range(4)]}


def decode_onl(data: bytes) -> Dict[str, Any]:
    tree = parse_tree(data, [64, 2640, 168], "ONL")
    e = tree["endian_char"]; root_node = tree["root"]; r = root_node["record"]
    out: Dict[str, Any] = {
        "schema": SCHEMA, "section": "ONL", "format_version": "v11", "tree": {k: v for k, v in tree.items() if k not in {"root", "endian_char"}},
        "root": {**_record_meta(r, root_node["offset"], 0, root_node["child_count"]), "version": _i32(r, 0, e), "mark": _i32(r, 4, e), "version_name": _cstr(r, 8, 32), "obsolete": _u8(r, 40), "max_traces": _i8(r, 41), "window_defined": _bool8(r, 42), "reserved1": _u8(r, 43), "crc": _u32(r, 44, e), "window_numbers": [_u8(r, 48+i) for i in range(12)], "reserved2": _i32(r, 60, e)},
        "methods": [], "warnings": [],
    }
    for midx, mn in enumerate(root_node["children"], 1):
        mr = mn["record"]
        m: Dict[str, Any] = {
            **_record_meta(mr, mn["offset"], 1, mn["child_count"]), "method_index": midx, "mark": _i32(mr, 0, e), "entry_name": _cstr(mr, 4, 32),
            "shared_x_window1": _bool8(mr, 36), "shared_x_window2": _bool8(mr, 37), "reserved1": _bool8(mr, 38), "reserved2": _bool8(mr, 39),
            "graphs": [_graph(mr[40+152*i:40+152*(i+1)], e, i+1) for i in range(12)], "reserved3": _i32(mr, 1864, e), "crc": _u32(mr, 1868, e),
            "headers": [_cstr(mr, 1872+32*i, 32) for i in range(12)], "last_x_min": [_f64(mr, 2256+8*i, e) for i in range(12)], "last_x_max": [_f64(mr, 2352+8*i, e) for i in range(12)], "last_y_min": [_f64(mr, 2448+8*i, e) for i in range(12)], "last_y_max": [_f64(mr, 2544+8*i, e) for i in range(12)], "functions": [],
        }
        for fidx, fn in enumerate(mn["children"], 1):
            fr = fn["record"]; code = _u8(fr, 82)
            f = {
                **_record_meta(fr, fn["offset"], 2, fn["child_count"]), "function_index": fidx, "mark": _i32(fr, 0, e), "name": _cstr(fr, 4, 32), "unit": _cstr(fr, 36, 8),
                "left_operand": _i16(fr, 44, e), "right_operand": _i16(fr, 46, e), "left_bound": _f64(fr, 48, e), "right_bound": _f64(fr, 56, e), "constant": _f64(fr, 64, e),
                "x_segment_offset": _i32(fr, 72, e), "y_segment_offset": _i32(fr, 76, e), "tc_enumerator": _i16(fr, 80, e),
                "function_code": code, "function": _enum(FUNCTION_TYPES, code, "FunctionType"), "do_notebook": _bool8(fr, 83), "no_fit": _bool8(fr, 84), "new_name": _bool8(fr, 85),
                "target_value": _i16(fr, 86, e), "cursor_kind_code": _u8(fr, 88), "cursor_kind": _enum(CURSOR, _u8(fr, 88), "CursorType"),
                "tc_kind1_raw": _u8(fr, 89), "tc_kind2_raw": _u8(fr, 90), "cursor_source": _u8(fr, 91), "crc": _u32(fr, 92, e), "equation": _cstr(fr, 96, 64),
                "baseline_mode_code": _u8(fr, 160), "baseline_mode": _enum(BASELINE, _u8(fr, 160), "BaselineType"),
                "search_direction_code": _u8(fr, 161), "search_direction": _enum(SEARCH, _u8(fr, 161), "SearchDirection"), "source_value": _i16(fr, 162, e), "cursor_anchor": _i16(fr, 164, e), "spare1": _i16(fr, 166, e),
            }
            if str(f["function"]).startswith("VendorExtension"):
                out["warnings"].append({"code": "onl_vendor_function", "message": f"Method {m['entry_name']!r}, function {fidx} uses unknown function code {code}; raw code preserved."})
            m["functions"].append(f)
        out["methods"].append(m)
    out["counts"] = {"methods": len(out["methods"]), "graphs": sum(len(m["graphs"]) for m in out["methods"]), "functions": sum(len(m["functions"]) for m in out["methods"])}
    return out


def _decode_lockin(r: bytes, e: str) -> Dict[str, Any]:
    return {"external_calibration_phase": _f64(r, 0, e), "external_calibration_attenuation": _f64(r, 8, e), "power_line_phase": _f64(r, 16, e), "power_line_phase_y1": _f64(r, 24, e), "power_line_phase_y2": _f64(r, 32, e), "used_phase_shift": _f64(r, 40, e), "used_attenuation": _f64(r, 48, e), "spare": _f64(r, 56, e), "external_calibration_valid": _bool8(r, 64), "power_line_phase_valid": _bool8(r, 65), "lockin_mode": _u8(r, 66), "calibration_mode": _u8(r, 67), "reserved_68_95_hex": r[68:96].hex()}


def _decode_amp_state(r: bytes, e: str) -> Dict[str, Any]:
    # Exact documented AmplifierState v1000 fields, byte offsets 0..399.
    doubles = [("current_gain", 8), ("f2_bandwidth", 16), ("f2_frequency", 24), ("rs_value", 32), ("rs_fraction", 40), ("g_leak", 48), ("c_fast_amp1", 56), ("c_fast_amp2", 64), ("c_fast_tau", 72), ("c_slow", 80), ("g_series", 88), ("vc_stim_dac_scale", 96), ("cc_stim_scale", 104), ("v_hold", 112), ("last_v_hold", 120), ("vp_offset", 128), ("v_liquid_junction", 136), ("cc_i_hold", 144), ("c_slow_stim_volts", 152), ("cc_track_v_hold", 160), ("timeout_c_slow", 168), ("search_delay", 176), ("m_conductance", 184), ("m_capacitance", 192), ("imon1_bandwidth", 264), ("stim_scale", 272), ("stim_filter_hz", 296), ("rs_tau", 304), ("dac_to_adc_delay", 312), ("input_filter_tau", 320), ("output_filter_tau", 328), ("vmon_factor", 337), ("vmon_offset", 360), ("cc_stim_dac_scale", 376), ("vmon_filter_bandwidth", 384), ("vmon_filter_frequency", 392)]
    out: Dict[str, Any] = {"state_version_hex": r[0:8].hex(), "serial_number": _cstr(r, 200, 8), "calibration_date": _cstr(r, 344, 16)}
    out.update({name: _f64(r, off, e) for name, off in doubles})
    for name, off in [("e9_boards", 208), ("c_slow_cycles", 210), ("imon_adc", 212), ("vmon_adc", 214), ("mux_adc", 216), ("test_dac", 218), ("stim_dac", 220), ("stim_dac_offset", 222), ("max_digital_bit", 224)]: out[name] = _i16(r, off, e)
    byte_names = ["has_cfast_high", "cfast_high", "has_bath_sense", "bath_sense", "has_f2_bypass", "f2_mode", "amplifier_kind_code", "is_epc9n", "ad_board_code", "board_version", "active_e9_board", "mode_code", "range_code", "f2_response", "rs_on", "c_slow_range", "cc_range", "cc_gain", "c_slow_to_test_dac", "stim_path", "cc_track_tau", "was_clipping", "repetitive_c_slow", "last_c_slow_range", "old1", "can_cc_fast", "can_low_cc_range", "can_high_cc_range", "can_cc_tracking", "has_vmon_path", "has_new_cc_mode"]
    for idx, name in enumerate(byte_names): out[name] = _u8(r, 226 + idx)
    out["selector"] = chr(_u8(r, 257)) if _u8(r, 257) else ""
    for name, off in [("hold_inverted",258),("auto_cfast",259),("auto_cslow",260),("has_vmon_x100",261),("test_dac_on",262),("q_mux_adc_on",263),("gain_code",280),("filter1_code",281),("stim_filter_on",282),("rs_slow",283),("old2",284),("cc_cfast_on",285),("cc_fast_speed",286),("f2_source",287),("test_range",288),("test_dac_path",289),("mux_channel",290),("mux_gain64",291),("vmon_x100",292),("is_quadro",293),("f1_mode",294),("old3",295),("eeprom_kind",368),("vref_x2",369),("has_vref_x2_and_f2_vmon",370),("spare1",371),("spare2",372),("spare3",373),("spare4",374),("spare5",375)]: out[name] = _u8(r, off)
    out["amplifier_kind"] = _enum(AMP_TYPES, out["amplifier_kind_code"], "AmplifierType")
    out["ad_board"] = _enum(AD_BOARDS, out["ad_board_code"], "ADBoardType")
    out["mode"] = _enum(AMP_MODES, out["mode_code"], "AmplifierMode")
    return out


def decode_amp(data: bytes) -> Dict[str, Any]:
    tree = parse_tree(data, [80, 16, 560], "AMP")
    e = tree["endian_char"]; rn = tree["root"]; r = rn["record"]
    out = {"schema": SCHEMA, "section": "AMP", "format_version": "v1000", "tree": {k:v for k,v in tree.items() if k not in {"root","endian_char"}}, "root": {**_record_meta(r,rn["offset"],0,rn["child_count"]), "version": _i32(r,0,e), "mark": _i32(r,4,e), "version_name": _cstr(r,8,32), "amplifier_name": _cstr(r,40,32), "amplifier_code": _u8(r,72), "amplifier": _enum(AMP_TYPES,_u8(r,72),"AmplifierType"), "ad_board_code": _u8(r,73), "ad_board": _enum(AD_BOARDS,_u8(r,73),"ADBoardType"), "creator": _u8(r,74), "filler1": _u8(r,75), "crc": _u32(r,76,e)}, "series": [], "warnings": []}
    for sidx, sn in enumerate(rn["children"],1):
        sr=sn["record"]; s={**_record_meta(sr,sn["offset"],1,sn["child_count"]),"series_index":sidx,"mark":_i32(sr,0,e),"series_count":_i32(sr,4,e),"filler1":_i32(sr,8,e),"crc":_u32(sr,12,e),"states":[]}
        for stidx, stn in enumerate(sn["children"],1):
            rr=stn["record"]; state={**_record_meta(rr,stn["offset"],2,stn["child_count"]),"state_index":stidx,"mark":_i32(rr,0,e),"state_count":_i32(rr,4,e),"state_version":_u8(rr,8),"filler_9_11_hex":rr[9:12].hex(),"filler4":_i32(rr,12,e),"lockin":_decode_lockin(rr[16:112],e),"amplifier_state":_decode_amp_state(rr[112:512],e),"internal_solution_index":_i32(rr,512,e),"external_solution_index":_i32(rr,516,e),"reserved_520_555_hex":rr[520:556].hex(),"crc":_u32(rr,556,e)}
            s["states"].append(state)
        out["series"].append(s)
    out["counts"]={"series":len(out["series"]),"states":sum(len(s["states"]) for s in out["series"])}
    return out


def decode_sol(data: bytes) -> Dict[str, Any]:
    tree=parse_tree(data,[88,160,40],"SOL"); e=tree["endian_char"]; rn=tree["root"]; r=rn["record"]
    out={"schema":SCHEMA,"section":"SOL","format_version":"v1000","tree":{k:v for k,v in tree.items() if k not in {"root","endian_char"}},"root":{**_record_meta(r,rn["offset"],0,rn["child_count"]),"version":_i32(r,0,e),"database_name":_cstr(r,4,80),"crc":_u32(r,84,e)},"solutions":[],"warnings":[]}
    for sidx,sn in enumerate(rn["children"],1):
        sr=sn["record"]; sol={**_record_meta(sr,sn["offset"],1,sn["child_count"]),"solution_index":sidx,"number":_i32(sr,0,e),"name":_cstr(sr,4,80),"numeric":_f32(sr,84,e),"numeric_name":_cstr(sr,88,30),"pH":_f32(sr,118,e),"pH_compound":_cstr(sr,122,30),"osmolality":_f32(sr,152,e),"crc":_u32(sr,156,e),"chemicals":[]}
        for cidx,cn in enumerate(sn["children"],1):
            cr=cn["record"]; sol["chemicals"].append({**_record_meta(cr,cn["offset"],2,cn["child_count"]),"chemical_index":cidx,"concentration":_f32(cr,0,e),"name":_cstr(cr,4,30),"spare1":_i16(cr,34,e),"crc":_u32(cr,36,e)})
        out["solutions"].append(sol)
    out["counts"]={"solutions":len(out["solutions"]),"chemicals":sum(len(s["chemicals"]) for s in out["solutions"])}
    return out


def decode_mrk(data: bytes) -> Dict[str, Any]:
    tree=parse_tree(data,[8,104],"MRK"); e=tree["endian_char"]; rn=tree["root"]; r=rn["record"]
    out={"schema":SCHEMA,"section":"MRK","format_version":"v9","tree":{k:v for k,v in tree.items() if k not in {"root","endian_char"}},"root":{**_record_meta(r,rn["offset"],0,rn["child_count"]),"version":_i32(r,0,e),"crc":_u32(r,4,e)},"markers":[],"warnings":[]}
    for idx,n in enumerate(rn["children"],1):
        rr=n["record"]; code=_u8(rr,92)
        out["markers"].append({**_record_meta(rr,n["offset"],1,n["child_count"]),"marker_index":idx,"time_s":_f64(rr,0,e),"text":_cstr(rr,8,80),"trace":_i32(rr,88,e),"kind_code":code,"kind":_enum(MARKER_KINDS,code,"MarkerKind"),"reserved_93_99_hex":rr[93:100].hex(),"crc":_u32(rr,100,e)})
    out["counts"]={"markers":len(out["markers"])}
    return out


_UNIT_FACTORS={"hz":1.0,"khz":1e3,"mhz":1e6,"ghz":1e9,"v":1.0,"mv":1e-3,"uv":1e-6,"a":1.0,"ma":1e-3,"ua":1e-6,"na":1e-9,"s":1.0,"ms":1e-3,"us":1e-6}
def _parse_method_value(text: str) -> Dict[str, Any]:
    raw=text.strip(); m=re.fullmatch(r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([A-Za-zµμΩ/%]*)",raw)
    if not m: return {"raw":raw,"value":None,"unit":""}
    val=float(m.group(1)); unit=m.group(2).replace("µ","u").replace("μ","u"); factor=_UNIT_FACTORS.get(unit.lower(),1.0)
    return {"raw":raw,"value":val,"unit":unit,"si_value":val*factor,"si_unit":{"khz":"Hz","mhz":"Hz","ghz":"Hz","mv":"V","uv":"V","ma":"A","ua":"A","na":"A","ms":"s","us":"s"}.get(unit.lower(),unit)}


def decode_mth(data: bytes) -> Dict[str, Any]:
    pos=0; records=[]; warnings=[]
    while pos < len(data):
        if pos+4>len(data): raise HekaSemanticError(f"MTH has truncated length prefix at byte {pos}")
        n=struct.unpack_from("<I",data,pos)[0]
        if n<=0 or n>1_000_000 or pos+4+n>len(data): raise HekaSemanticError(f"MTH record at {pos} has invalid payload length {n}")
        payload=data[pos+4:pos+4+n]; text=payload.split(b"\0",1)[0].decode("latin-1","replace").strip()
        name=text[:32].strip(); description=text[32:].strip() if len(text)>32 else ""
        timing_match=re.match(r"^\(\s*([-+]?\d+(?:\.\d+)?)s\s*\):\s*",description)
        elapsed_time_s=float(timing_match.group(1)) if timing_match else None
        parameter_text=description[timing_match.end():] if timing_match else description
        params={}
        for part in re.split(r",\s*",parameter_text):
            if "=" in part:
                k,v=part.split("=",1); params[k.strip()]=_parse_method_value(v.strip())
        records.append({"record_index":len(records)+1,"record_offset":pos,"payload_length":n,"record_size":n+4,"record_sha256":hashlib.sha256(data[pos:pos+4+n]).hexdigest(),"name":name,"description":description,"elapsed_time_s":elapsed_time_s,"parameter_text":parameter_text,"parameters":params,"raw_text":text,"unused_payload_hex":payload[len(text.encode('latin-1','replace')):].hex()})
        pos+=4+n
    return {"schema":SCHEMA,"section":"MTH","format_version":"attached-fixed-record","total_bytes":len(data),"parsed_bytes":pos,"records":records,"counts":{"records":len(records)},"warnings":warnings}


def _source_value(root: Dict[str, Any], source: int, base: float) -> Tuple[float,str]:
    # HEKA uses negative/no-source values for direct constants and positive parameter references.
    if 0 <= source < 10:
        params=root.get("parameters",[])
        if source < len(params): return float(params[source]),f"PGF parameter p{source+1}"
    return float(base),"record constant"


def _increment_value(base: float, delta_factor: float, delta_increment: float, mode_code: int, sweep0: int) -> float:
    if mode_code in (0,5): step=sweep0
    elif mode_code in (1,6): step=-sweep0
    elif mode_code in (2,7): step=(sweep0+1)//2 if sweep0%2 else 0
    elif mode_code in (3,8): step=-((sweep0+1)//2 if sweep0%2 else 0)
    elif mode_code in (4,9): step=((sweep0+1)//2)*(1 if sweep0%2 else -1)
    else: step=0
    if mode_code>=5:
        factor=delta_factor if delta_factor not in (0.0,1.0) else 10.0
        try:return base*(factor**step)
        except Exception:return base
    return base + step*delta_increment


def infer_protocol_technique(stim: Dict[str, Any]) -> Tuple[str,float,List[str]]:
    name=(str(stim.get("entry_name",''))+" "+str(stim.get("analysis_name",''))).lower(); evidence=[]; scores:Dict[str,float]={}
    channels=stim.get("channels",[]); classes=[seg.get("class") for c in channels for seg in c.get("segments",[])]
    scan_rates=[abs(float(seg.get("scan_rate") or 0)) for c in channels for seg in c.get("segments",[]) if math.isfinite(float(seg.get("scan_rate") or 0))]
    if "eis" in name or stim.get("has_lockin") or any(c.get("has_lockin_sine") or c.get("has_chirp") for c in channels): scores["EIS"]=0.98; evidence.append("PGF lock-in/chirp/EIS semantics")
    if "ocp" in name or "open circuit" in name: scores["OCP"]=0.98; evidence.append("PGF stimulus/analysis name identifies OCP")
    if "fscv" in name or "fastcv" in name or (scan_rates and max(scan_rates)>1.0): scores["FSCV"]=0.94; evidence.append("PGF fast-scan name or scan rate >1 V/s")
    if "cv" in name and "fscv" not in name and "fastcv" not in name: scores["CV"]=0.95; evidence.append("PGF stimulus/analysis name identifies CV")
    if any(x in name for x in ("lsv","orr","linear sweep","polarization","polarisation")): scores["LSV"]=0.95; evidence.append("PGF stimulus/analysis name identifies LSV/ORR")
    if any(x in name for x in ("tafel","corrosion","corrod")): scores["Tafel/corrosion"]=0.97; evidence.append("PGF stimulus/analysis name identifies corrosion/Tafel")
    if any(x in name for x in ("chronoamp"," ca","ca ","potentiostatic")): scores["CA"]=0.92; evidence.append("PGF stimulus/analysis name identifies CA")
    if any(x in name for x in ("chronopot"," cp","cp ","galvanostatic")): scores["CP"]=0.92; evidence.append("PGF stimulus/analysis name identifies CP")
    if not scores and "SegmentRamp" in classes: scores["LSV"]=0.55; evidence.append("PGF contains ramp segment but no explicit method label")
    if not scores and all(x=="SegmentConstant" for x in classes if x): scores["Generic pulse/hold"]=0.45; evidence.append("PGF contains constant segments")
    if not scores:return "Unknown HEKA",0.0,["No decisive PGF protocol evidence"]
    tech=max(scores,key=scores.get); return tech,scores[tech],evidence


def reconstruct_protocols(pgf: Dict[str, Any], max_sweeps: int = 10000) -> List[Dict[str, Any]]:
    root=pgf.get("root",{}); out=[]
    for stim in pgf.get("stimulations",[]):
        ns=max(0,min(int(stim.get("number_sweeps") or 0),max_sweeps)); tech,conf,evidence=infer_protocol_technique(stim)
        protocol={"stimulation_index":stim.get("stimulation_index"),"entry_name":stim.get("entry_name"),"analysis_name":stim.get("analysis_name"),"technique":tech,"confidence":conf,"evidence":evidence,"sample_interval_s":stim.get("sample_interval_s"),"sweep_interval_s":stim.get("sweep_interval_s"),"number_sweeps":ns,"channels":[]}
        for ch in stim.get("channels",[]):
            c={"channel_index":ch.get("channel_index"),"y_unit":ch.get("y_unit"),"dac_unit":ch.get("dac_unit"),"holding":ch.get("holding"),"amplifier_mode":ch.get("amplifier_mode"),"sweeps":[]}
            for sw0 in range(ns or 1):
                segs=[]; total=0.0
                for seg in ch.get("segments",[]):
                    v0,vsrc=_source_value(root,int(seg.get("voltage_source") or -1),float(seg.get("voltage") or 0.0)); t0,tsrc=_source_value(root,int(seg.get("duration_source") or -1),float(seg.get("duration_s") or 0.0))
                    v=_increment_value(v0,float(seg.get("delta_v_factor") or 0),float(seg.get("delta_v_increment") or 0),int(seg.get("voltage_increment_mode_code") or 0),sw0)
                    dur=_increment_value(t0,float(seg.get("delta_t_factor") or 0),float(seg.get("delta_t_increment_s") or 0),int(seg.get("duration_increment_mode_code") or 0),sw0)
                    segs.append({"segment_index":seg.get("segment_index"),"class":seg.get("class"),"store_kind":seg.get("store_kind"),"voltage":v,"voltage_source_semantics":vsrc,"duration_s":dur,"duration_source_semantics":tsrc,"scan_rate":seg.get("scan_rate"),"start_time_s":total,"end_time_s":total+max(0,dur)})
                    total+=max(0,dur)
                c["sweeps"].append({"sweep_index":sw0+1,"segments":segs,"total_duration_s":total})
            protocol["channels"].append(c)
        out.append(protocol)
    return out


def decode_section(ext: str, data: bytes) -> Dict[str, Any]:
    ext=ext.lower()
    if ext==".pgf": return decode_pgf(data)
    if ext==".onl": return decode_onl(data)
    if ext==".amp": return decode_amp(data)
    if ext==".sol": return decode_sol(data)
    if ext==".mrk": return decode_mrk(data)
    if ext==".mth": return decode_mth(data)
    raise HekaSemanticError(f"No complete semantic decoder for {ext}")


def _section_bytes(parser: Any, ext: str) -> Optional[bytes]:
    sb=getattr(parser,"section_bytes",{}) or {}
    if ext in sb:return bytes(sb[ext])
    sections=getattr(parser,"sections",{}) or {}; bundle=getattr(parser,"bundle_path",None)
    if ext in sections and bundle:
        sec=sections[ext]
        with Path(bundle).open("rb") as f:f.seek(int(sec.offset)); return f.read(int(sec.length))
    paths=getattr(parser,"support_paths",{}) or {}
    if ext in paths:return Path(paths[ext]).read_bytes()
    src=Path(str(getattr(parser,"source_path","") or ""))
    if src.exists() and src.suffix.lower()==ext:return src.read_bytes()
    return None


def decode_parser_sections(parser: Any) -> Dict[str, Any]:
    sections:Dict[str,Any]={}; warnings=[]; inventory=[]
    known=(".pgf",".onl",".amp",".sol",".mrk",".mth")
    available=set((getattr(parser,"sections",{}) or {}).keys())|set((getattr(parser,"support_paths",{}) or {}).keys())
    src=Path(str(getattr(parser,"source_path","") or ""))
    if src.exists():available.add(src.suffix.lower())
    for ext in sorted(available):
        data=_section_bytes(parser,ext)
        if data is None:continue
        item={"section":ext,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"semantic_status":"not_applicable" if ext in (".dat",".pul") else "preserved_raw"}
        if ext in known:
            try:
                decoded=decode_section(ext,data); sections[ext]=decoded; item["semantic_status"]="fully_decoded_attached_version"; item["format_version"]=decoded.get("format_version"); item["counts"]=decoded.get("counts",{})
                warnings.extend(decoded.get("warnings",[]))
            except Exception as exc:
                item["semantic_status"]="unsupported_or_malformed"; item["error"]=str(exc); warnings.append({"code":"semantic_section_decode_failed","section":ext,"message":str(exc)})
        inventory.append(item)
    return {"schema":SCHEMA,"parser_version":VERSION,"sections":sections,"inventory":inventory,"warnings":warnings}
