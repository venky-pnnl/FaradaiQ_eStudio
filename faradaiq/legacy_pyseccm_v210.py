from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np

SCHEMA = "faradaiq-heka-legacy-pyseccm-v216"


def _safe(value: Any, limit: int = 96) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("_.")
    return (text or "item")[:limit]


def _num(value: Any, default: float = float("nan")) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _json_default(value: Any):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(type(value).__name__)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")


def _write_repeated_table(path: Path, headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="windows-1252", newline="") as f:
        f.write("\n")
        header = ", ".join(f"{str(c):>13}" for c in headers)
        for row in rows:
            f.write(header + "\n")
            vals: List[str] = []
            for value in row:
                if isinstance(value, (int, np.integer)) and abs(int(value)) < 10_000_000:
                    vals.append(f"{int(value):7d}")
                else:
                    try:
                        vals.append(f"{float(value):13.9E}")
                    except Exception:
                        vals.append(str(value))
            f.write(", ".join(vals) + "\n")


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))


def _method_label(rec: Mapping[str, Any]) -> str:
    tech = str(rec.get("heka_technique") or rec.get("technique") or "HEKA")
    group = str(rec.get("group_title") or "").strip()
    series = str(rec.get("series_title") or "").strip()
    protocol = str(rec.get("protocol_name") or rec.get("pgf_stimulation_name") or "").strip()
    # Group titles commonly carry the actual scan-rate/method identity (CV-100mV, E-2534).
    if tech in {"CV", "LSV", "FSCV", "Tafel/corrosion"} and group:
        return group
    if protocol:
        return protocol
    if series and series.lower() not in {"eis frequency series aggregated by group", "eis"}:
        return series
    return tech


def _point(rec: Mapping[str, Any]) -> int:
    # Preserve an explicit point 0 for initial/pre-scan setup records. Only
    # fall back to Group index when no point field exists at all.
    for key in ("point", "point_index"):
        if key not in rec or rec.get(key) in (None, ""):
            continue
        try:
            value = int(float(rec.get(key)))
            if value >= 0:
                return value
        except Exception:
            pass
    try:
        value = int(float(rec.get("group_index")))
        if value > 0:
            return value
    except Exception:
        pass
    return 1


def _record_path(structured_root: Path, rec: Mapping[str, Any]) -> Path:
    return structured_root / str(rec.get("relative_csv_path") or "")


def _prepare_dirs(output_dir: Path, base: str, package_root: Path | None = None) -> Dict[str, Path]:
    root = output_dir / f"{base}_for_plotting_analysis"
    dirs = {
        "root": root,
        "data": root / "data",
        "generated": root / "data_generated_from_HEKA",
        "drt": root / "DRT_input_csv",
        "figures": root / "data" / "Figures",
        "sequence": root / "data_by_point_sequence",
        "photos": root / "photos",
        "scripts": root / "pyseccm_tools_scripts",
    }
    root.mkdir(parents=True, exist_ok=True)
    # A fresh export must not leave stale parser inputs or stale parser result folders.
    for key in ("data", "generated", "drt", "sequence"):
        p = dirs[key]
        if p.exists():
            shutil.rmtree(p)
        p.mkdir(parents=True, exist_ok=True)
    dirs["figures"].mkdir(parents=True, exist_ok=True)
    dirs["photos"].mkdir(parents=True, exist_ok=True)
    if package_root:
        src = package_root / "pyseccm_tools"
        if src.exists():
            if dirs["scripts"].exists():
                shutil.rmtree(dirs["scripts"])
            shutil.copytree(src, dirs["scripts"], ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (dirs["data"] / "base.mplstyle").write_text(
        "font.size: 8\naxes.labelsize: 8\naxes.titlesize: 9\nlegend.fontsize: 7\nfigure.dpi: 160\nsavefig.dpi: 300\n",
        encoding="utf-8",
    )
    return dirs


def _extract_coordinates(structured_root: Path, records: Sequence[Mapping[str, Any]]) -> Tuple[Dict[int, Tuple[float, float, float]], str, str]:
    points = sorted({_point(r) for r in records}) or [1]
    coords: Dict[int, Tuple[float, float, float]] = {}
    spatial = structured_root / "heka_spatial_point_table.csv"
    if spatial.exists():
        try:
            for row in _read_rows(spatial):
                pt = int(float(row.get("point_index") or 0))
                x, y, z = _num(row.get("x")), _num(row.get("y")), _num(row.get("z"))
                if pt > 0 and all(math.isfinite(v) for v in (x, y, z)):
                    coords[pt] = (x, y, z)
        except Exception:
            pass
    if all(pt in coords for pt in points):
        return coords, "real_HEKA_XYZ", "All exported points contain finite HEKA X/Y/Z coordinates."
    # Legacy pySECCM mapping scripts require coordinates. Use an explicitly marked ordered fallback.
    spacing = 1e-6
    for idx, pt in enumerate(points):
        coords.setdefault(pt, (idx * spacing, 0.0, 0.0))
    return coords, "SYNTHETIC_XYZ_ordered_point_line", "HEKA coordinates were incomplete; an explicitly marked 1 µm ordered X line was generated only for legacy pySECCM compatibility."


def _position_rows(points: Sequence[int], coords: Mapping[int, Tuple[float, float, float]]) -> List[List[Any]]:
    rows = []
    for idx, pt in enumerate(points):
        x, y, z = coords[pt]
        rows.append([1, 0.0, 0.0, x, y, z, float(idx)])
    return rows


def _write_position(path: Path, points: Sequence[int], coords: Mapping[int, Tuple[float, float, float]]) -> None:
    _write_repeated_table(path, ["Sweep #", "Imon-2[A]", "Emon-2[V]", "X-pos[m]", "Y-pos[m]", "Z-pos[m]", "Time[s]"], _position_rows(points, coords))


def _write_voltammetry_file(path: Path, items: Sequence[Tuple[Mapping[str, Any], Path]]) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    groups = rows_total = 0
    with path.open("w", encoding="windows-1252", newline="") as f:
        for rec, table in sorted(items, key=lambda x: (_point(x[0]), int(x[0].get("cycle") or 0), int(x[0].get("sweep_index") or 0))):
            rows = _read_rows(table)
            if not rows:
                continue
            point = _point(rec)
            cycle = int(rec.get("cycle") or rec.get("sweep_index") or 1)
            label = _safe(_method_label(rec), 64)
            f.write(f"Series_{label}_point{point:03d}\n")
            f.write(f"Sweep_{label}_point{point:03d}_cycle{cycle:03d},  0.000000000E+00, 00:00:00.000, 1970/01/01 00:00:00.000, \"Generated from HEKA DAT2 by FaradaIQ v273\"\n")
            f.write('"Index", "Time[s]"       , "Imon-2[A]"     , "Time[s]"       , "Emon-2[V]"     \n')
            wrote = 0
            for k, row in enumerate(rows):
                t = _num(row.get("time_s"), float(k))
                cur = _num(row.get("current_A"))
                pot = _num(row.get("potential_V"))
                if not (math.isfinite(cur) and math.isfinite(pot)):
                    continue
                f.write(f"{k:7d}, {t:16.9E}, {cur:16.9E}, {t:16.9E}, {pot:16.9E}\n")
                wrote += 1
            if wrote:
                f.write("\n")
                groups += 1
                rows_total += wrote
    return {"n_groups": groups, "n_rows": rows_total}


def _charge(time_s: Sequence[float], current_a: Sequence[float]) -> np.ndarray:
    t = np.asarray(time_s, dtype=float); i = np.asarray(current_a, dtype=float)
    n = min(len(t), len(i)); q = np.zeros(n, dtype=float)
    if n > 1:
        q[1:] = np.cumsum(0.5 * (i[:-1] + i[1:]) * np.diff(t))
    return q


def _write_chrono_file(path: Path, items: Sequence[Tuple[Mapping[str, Any], Path]], kind: str) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    groups = rows_total = 0
    with path.open("w", encoding="windows-1252", newline="") as f:
        for rec, table in sorted(items, key=lambda x: (_point(x[0]), int(x[0].get("sweep_index") or 0))):
            rows = _read_rows(table)
            point = _point(rec); label = _safe(_method_label(rec), 64)
            vals = []
            for k, row in enumerate(rows):
                t = _num(row.get("time_s"), float(k))
                cur = _num(row.get("current") if "current" in row else row.get("current_A"))
                pot = _num(row.get("potential") if "potential" in row else row.get("potential_V"))
                if math.isfinite(t) and (math.isfinite(cur) or math.isfinite(pot)):
                    vals.append((t, cur, pot))
            if not vals:
                continue
            q = _charge([v[0] for v in vals], [v[1] if math.isfinite(v[1]) else 0.0 for v in vals])
            f.write(f"Series_{kind}_{label}_point{point:03d}\n")
            f.write(f"Sweep_{kind}_{label}_point{point:03d},  0.000000000E+00, 00:00:00.000, 1970/01/01 00:00:00.000, \"Generated from HEKA DAT2 by FaradaIQ v273\"\n")
            f.write('"Index", "Time[s]"       , "Imon-2[A]"     , "Time[s]"       , "Emon-2[V]"     , "Charge[C]"     , "Point"\n')
            for k, (t, cur, pot) in enumerate(vals):
                f.write(f"{k:7d}, {t:16.9E}, {cur:16.9E}, {t:16.9E}, {pot:16.9E}, {float(q[k]):16.9E}, {point:7d}\n")
            f.write("\n"); groups += 1; rows_total += len(vals)
    return {"n_groups": groups, "n_rows": rows_total}


def _write_ocp(path: Path, items: Sequence[Tuple[Mapping[str, Any], Path]]) -> Tuple[Dict[str, Any], Dict[int, float]]:
    info = _write_chrono_file(path, items, "OCP")
    averages: Dict[int, List[float]] = defaultdict(list)
    for rec, table in items:
        for row in _read_rows(table):
            pot = _num(row.get("potential") if "potential" in row else row.get("potential_V"))
            if math.isfinite(pot): averages[_point(rec)].append(pot)
    return info, {pt: float(np.mean(vals)) for pt, vals in averages.items() if vals}


def _frequency_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    vals = [round(_num(r.get("frequency_Hz")), 9) for r in rows if math.isfinite(_num(r.get("frequency_Hz")))]
    raw = json.dumps(vals, separators=(",", ":"))
    return hashlib.sha1(raw.encode("ascii", errors="ignore")).hexdigest()[:12]


def _eis_legacy_row(row: Mapping[str, Any]) -> List[Any] | None:
    freq = _num(row.get("frequency_Hz")); zr = _num(row.get("Zreal_ohm")); zi = _num(row.get("Zimag_ohm"))
    if not all(math.isfinite(v) for v in (freq, zr, zi)) or freq <= 0:
        return None
    zmod = _num(row.get("Zmod_ohm"), math.hypot(zr, zi))
    phase = _num(row.get("phase_deg"), math.degrees(math.atan2(zi, zr)))
    den = zr * zr + zi * zi
    yr, yi = ((zr / den, -zi / den) if den > 0 else (float("nan"), float("nan")))
    yabs = math.hypot(yr, yi) if math.isfinite(yr) and math.isfinite(yi) else float("nan")
    return [1, freq, math.log10(freq), yabs, math.log10(abs(yabs)) if math.isfinite(yabs) and yabs != 0 else float("nan"), yr, yi, phase, zr, zi, zmod, math.log10(abs(zmod)) if zmod else float("nan")]


def _write_eis_file(path: Path, spectra: Sequence[Tuple[Mapping[str, Any], Path]]) -> Dict[str, Any]:
    rows: List[List[Any]] = []
    points: List[int] = []
    for rec, table in sorted(spectra, key=lambda x: _point(x[0])):
        one = []
        for row in _read_rows(table):
            converted = _eis_legacy_row(row)
            if converted is not None: one.append(converted)
        if one:
            rows.extend(one); points.append(_point(rec))
    _write_repeated_table(path, ["Sweep #", "f2[Hz]", "log(f)", "AdmitY[S]", "log(AdmitY)", "RealY[S]", "ImagY[S]", "Phase[deg]", "RealZ[Ohm]", "ImagZ[Ohm]", "ImpZ[Ohm]", "log(ImpZ)"], rows)
    return {"n_rows": len(rows), "n_spectra": len(points), "points": points}


def _copy_generated_to_data(generated: Path, data: Path) -> None:
    for p in generated.iterdir():
        if p.is_file(): shutil.copy2(p, data / p.name)


def _write_manual_runner(root: Path, entries: Sequence[Mapping[str, Any]]) -> None:
    lines = ["@echo off", "setlocal", "cd /d \"%~dp0data\"", "set PYTHON_EXE=python", "set TOOLS=%~dp0pyseccm_tools_scripts", "echo Running FaradaIQ v273 traditional pySECCM parser inputs...", ""]
    for entry in entries:
        script = entry.get("script"); args = entry.get("args", [])
        if not script: continue
        quoted = " ".join(f'"{a}"' for a in args)
        lines.append(f'"%PYTHON_EXE%" "%TOOLS%\\{script}" {quoted}')
        lines.append("if errorlevel 1 echo FAILED: " + str(script) + " " + " ".join(map(str, args)))
    lines += ["", "echo Finished.", "pause", "endlocal"]
    (root / "Run_all_traditional_pySECCM_parsers.bat").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def _copy_media_sidecars(parser: Any, photos_dir: Path, source_dir: Path | None = None) -> Dict[str, Any]:
    image_exts={".png",".jpg",".jpeg",".tif",".tiff",".bmp",".gif",".webp"}
    video_exts={".mp4",".avi",".mov",".mkv",".wmv",".mpeg",".mpg",".m4v",".webm"}
    files=[]
    for attr in ("media_files","image_files","video_files"):
        files.extend(Path(x) for x in (getattr(parser,attr,[]) or []))
    if source_dir and source_dir.exists():
        files.extend(p for p in source_dir.rglob("*") if p.is_file() and p.suffix.lower() in image_exts|video_exts)
    rows=[]; seen=set(); photos_dir.mkdir(parents=True,exist_ok=True)
    for src in sorted(files,key=lambda p:p.name.lower()):
        try:
            src=src.resolve(); key=str(src).lower()
            if key in seen or not src.exists(): continue
            seen.add(key); dst=photos_dir/src.name
            if src != dst.resolve(): shutil.copy2(src,dst)
            rows.append({"name":dst.name,"source_path":str(src),"relative_path":dst.name,"media_type":"video" if dst.suffix.lower() in video_exts else ("gif" if dst.suffix.lower()==".gif" else "image"),"size_bytes":dst.stat().st_size})
        except Exception as exc:
            rows.append({"name":getattr(src,"name",str(src)),"error":str(exc)})
    stills=[photos_dir/r["name"] for r in rows if r.get("media_type")=="image" and (photos_dir/r["name"]).exists()]
    gif_path=None
    if len(stills) >= 2:
        try:
            from PIL import Image
            ims=[]
            for q in stills[:240]:
                with Image.open(q) as im:
                    frame=im.convert("RGB")
                    frame.thumbnail((720,720))
                    canvas=Image.new("RGB",(720,720),(0,0,0))
                    canvas.paste(frame,((720-frame.width)//2,(720-frame.height)//2))
                    ims.append(canvas.quantize(colors=128))
            if ims:
                gif_path=photos_dir/"faradaiq_photo_sequence.gif"
                ims[0].save(gif_path,save_all=True,append_images=ims[1:],duration=250,loop=0,optimize=True,disposal=2)
                rows.append({"name":gif_path.name,"relative_path":gif_path.name,"media_type":"gif","size_bytes":gif_path.stat().st_size,"generated":True})
        except Exception as exc:
            rows.append({"name":"faradaiq_photo_sequence.gif","error":str(exc)})
    manifest_path=photos_dir/"media_manifest_v216.json"
    _write_json(manifest_path,{"schema":"faradaiq-media-manifest-v216","count":len(rows),"files":rows})
    return {"count":len(rows),"manifest":str(manifest_path),"generated_gif":str(gif_path) if gif_path else ""}


def export_legacy(parser: Any, structured_info: Mapping[str, Any], output_dir: Path, base_name: str, package_root: Path | None = None) -> Dict[str, Any]:
    output_dir = Path(output_dir); base = _safe(base_name, 120)
    structured_root = Path(str(structured_info.get("root") or output_dir))
    records = [r for r in structured_info.get("records", []) if r.get("relative_csv_path")]
    dirs = _prepare_dirs(output_dir, base, package_root)
    source_dir = None
    try:
        sp = Path(str(getattr(parser, "source_path", "")))
        source_dir = sp.parent if sp.is_file() else sp
    except Exception:
        source_dir = None
    media_info = _copy_media_sidecars(parser, dirs["photos"], source_dir)
    coords, coordinate_mode, coordinate_note = _extract_coordinates(structured_root, records)
    all_points = sorted({pt for r in records if (pt := _point(r)) > 0}) or [1]
    pos_name = f"{base}_takedata_1{'_SYNTHETIC_XYZ' if 'SYNTHETIC' in coordinate_mode else ''}.txt"
    pos_path = dirs["generated"] / pos_name
    _write_position(pos_path, all_points, coords)

    manifest_entries: List[Dict[str, Any]] = []
    generated: List[Dict[str, Any]] = [{"kind": "position/takedata", "filename": pos_name, "points": all_points, "coordinate_mode": coordinate_mode}]

    by_tech_method: Dict[Tuple[str, str], List[Tuple[Mapping[str, Any], Path]]] = defaultdict(list)
    for rec in records:
        p = _record_path(structured_root, rec)
        if not p.exists(): continue
        tech = str(rec.get("heka_technique") or rec.get("technique") or "Unknown HEKA")
        by_tech_method[(tech, _method_label(rec))].append((rec, p))

    def _method_order(item):
        (tech_label, method_label), method_items = item
        first_group = min((int(float(r.get("group_index") or 10**9)) for r, _p in method_items), default=10**9)
        first_series = min((int(float(r.get("series_index") or 10**9)) for r, _p in method_items), default=10**9)
        return (str(tech_label), first_group, first_series, str(method_label))

    # CV/LSV/FSCV all use the traditional parse_CV input grammar.
    method_no = 0
    for (tech, label), items in sorted(by_tech_method.items(), key=_method_order):
        if tech not in {"CV", "LSV", "FSCV"}: continue
        method_no += 1
        name = f"{base}_CV_{_safe(tech)}_{_safe(label,48)}_method{method_no:02d}.asc"
        info = _write_voltammetry_file(dirs["generated"] / name, items)
        if info["n_groups"]:
            generated.append({"kind": "CV", "filename": name, "technique": tech, "method_label": label, **info})
            manifest_entries.append({"kind": "CV", "script": "parse_CV.py", "args": [name, "--plot"], "input_files": [name], "method_label": label})

    # EIS spectra are grouped only when their exact frequency vectors match, so parse_EIS receives rectangular groups.
    eis_groups: Dict[str, List[Tuple[Mapping[str, Any], Path]]] = defaultdict(list)
    for (tech, label), items in by_tech_method.items():
        if tech != "EIS": continue
        for rec, p in items:
            eis_groups[_frequency_signature(_read_rows(p))].append((rec, p))
    for idx, (signature, items) in enumerate(sorted(eis_groups.items()), 1):
        eis_name = f"{base}_fullEIS_6.txt" if idx == 1 else f"{base}_fullEIS_method{idx}.txt"
        info = _write_eis_file(dirs["generated"] / eis_name, items)
        if not info["n_spectra"]: continue
        pts = info["points"]
        eis_pos_name = f"{base}_takedata_EIS_method{idx:02d}{'_SYNTHETIC_XYZ' if 'SYNTHETIC' in coordinate_mode else ''}.txt"
        _write_position(dirs["generated"] / eis_pos_name, pts, coords)
        generated.extend([
            {"kind": "EIS", "filename": eis_name, "frequency_signature": signature, **info},
            {"kind": "position/takedata", "filename": eis_pos_name, "purpose": f"EIS method {idx}", "points": pts},
        ])
        manifest_entries.append({"kind": "EIS", "script": "parse_EIS.py", "args": [eis_pos_name, eis_name, "--plot", "--n_grid", "100"], "input_files": [eis_pos_name, eis_name], "frequency_signature": signature})
        # DRT remains one file per spatial spectrum.
        for rec, p in items:
            rows = _read_rows(p); out_rows = []
            for row in rows:
                fr, zr, zi = _num(row.get("frequency_Hz")), _num(row.get("Zreal_ohm")), _num(row.get("Zimag_ohm"))
                if all(math.isfinite(v) for v in (fr, zr, zi)) and fr > 0: out_rows.append((fr, zr, zi))
            if out_rows:
                pt = _point(rec)
                drt_name = f"{base}__method{idx:02d}_position{pt:03d}__freq_zreal_zimag.csv"
                with (dirs["drt"] / drt_name).open("w", encoding="utf-8", newline="") as f:
                    w = csv.writer(f); w.writerow(["frequency_Hz", "Zreal_ohm", "Zimag_ohm"]); w.writerows(out_rows)

    # CA, CP, OCP.
    for kind in ("CA", "CP"):
        local = 0
        for (tech, label), items in sorted(by_tech_method.items(), key=_method_order):
            if tech != kind: continue
            local += 1
            name = f"{base}_full{kind}_9.asc" if local == 1 else f"{base}_full{kind}_method{local}.asc"
            info = _write_chrono_file(dirs["generated"] / name, items, kind)
            if info["n_groups"]:
                generated.append({"kind": kind, "filename": name, "method_label": label, **info})
                manifest_entries.append({"kind": kind, "script": f"parse_{kind}.py", "args": [name, "--plot"], "input_files": [name], "method_label": label})

    ocp_no = 0
    for (tech, label), items in sorted(by_tech_method.items(), key=_method_order):
        if tech != "OCP": continue
        ocp_no += 1
        suffix = "8" if ocp_no == 1 else f"method{ocp_no}"
        full_name = f"{base}_OCP_from_HEKA_{suffix}.asc"; avg_name = f"{base}_avgOCP_from_HEKA_{suffix}.txt"
        info, averages = _write_ocp(dirs["generated"] / full_name, items)
        pts = sorted(averages) or sorted({_point(r) for r, _ in items})
        _write_repeated_table(dirs["generated"] / avg_name, ["Sweep #", "OCP-value[V]"], [[1, averages.get(pt, float("nan"))] for pt in pts])
        pos_local = f"{base}_takedata_OCP_method{ocp_no:02d}{'_SYNTHETIC_XYZ' if 'SYNTHETIC' in coordinate_mode else ''}.txt"
        _write_position(dirs["generated"] / pos_local, pts, coords)
        generated.extend([{"kind": "OCP", "filename": full_name, **info}, {"kind": "OCP-average", "filename": avg_name}, {"kind": "position/takedata", "filename": pos_local, "purpose": f"OCP method {ocp_no}"}])
        manifest_entries.append({"kind": "OCP", "script": "parse_OCP.py", "args": [pos_local, avg_name, full_name, "--plot", "--n_grid", "100"], "input_files": [pos_local, avg_name, full_name], "method_label": label})

    # Tafel/corrosion uses parse_IV with a generated companion summary.
    iv_no = 0
    for (tech, label), items in sorted(by_tech_method.items(), key=_method_order):
        if tech != "Tafel/corrosion": continue
        iv_no += 1
        iv_name = f"{base}_IV_Tafel_from_HEKA_method{iv_no}.asc"
        info = _write_voltammetry_file(dirs["generated"] / iv_name, items)
        pts = sorted({_point(r) for r, _ in items})
        corr_name = f"{base}_Ecorr_Icorr_from_HEKA_method{iv_no}.txt"
        corr_rows = []
        for pt in pts:
            item = next((x for x in items if _point(x[0]) == pt), None)
            e = i = float("nan")
            if item:
                rows = _read_rows(item[1])
                if rows:
                    finite = [(abs(_num(r.get("current_A"), float("inf"))), _num(r.get("potential_V")), _num(r.get("current_A"))) for r in rows]
                    finite = [r for r in finite if all(math.isfinite(v) for v in r)]
                    if finite: _, e, i = min(finite, key=lambda x: x[0])
            z = coords[pt][2]
            corr_rows.append([1, e, i, z, float("nan")])
        _write_repeated_table(dirs["generated"] / corr_name, ["Sweep #", "E_corr[V]", "I_corr[A]", "Z-pos[m]", "Rp"], corr_rows)
        pos_local = f"{base}_takedata_IV_method{iv_no:02d}{'_SYNTHETIC_XYZ' if 'SYNTHETIC' in coordinate_mode else ''}.txt"
        _write_position(dirs["generated"] / pos_local, pts, coords)
        generated.extend([{"kind": "TAFEL-IV", "filename": iv_name, **info}, {"kind": "TAFEL-corr", "filename": corr_name}, {"kind": "position/takedata", "filename": pos_local, "purpose": f"IV method {iv_no}"}])
        manifest_entries.append({"kind": "IV", "script": "parse_IV.py", "args": [pos_local, corr_name, iv_name, "--plot", "--n_grid", "100"], "input_files": [pos_local, corr_name, iv_name], "method_label": label})

    _copy_generated_to_data(dirs["generated"], dirs["data"])
    # Put a copy of the DRT folder path into the parser manifest; the integrated launcher adds the DRT command.
    manifest = {
        "schema": SCHEMA,
        "created": datetime.now().isoformat(timespec="seconds"),
        "source_file": str(getattr(parser, "source_path", "")),
        "structured_manifest": str(structured_info.get("manifest") or ""),
        "structured_root": str(structured_root),
        "plotting_root": str(dirs["root"]),
        "data_folder": str(dirs["data"]),
        "generated_folder": str(dirs["generated"]),
        "drt_input_folder": str(dirs["drt"]),
        "coordinate_mode": coordinate_mode,
        "coordinate_note": coordinate_note,
        "media": media_info,
        "generated_files": generated,
        "parser_commands": manifest_entries,
        "traditional_folders": ["data", "data/Models", "data/Figures", "DRT_input_csv", "data_generated_from_HEKA", "data_by_point_sequence", "photos", "pyseccm_tools_scripts"],
        "notes": [
            "Structured HEKA decoding remains authoritative; these are additional compatibility inputs for the traditional pySECCM parser scripts.",
            "Each parser command has explicit companion files, preventing the first takedata file from being incorrectly reused for every EIS/OCP/IV method.",
        ],
    }
    (dirs["data"] / "Models").mkdir(parents=True, exist_ok=True)
    manifest_path = dirs["root"] / "legacy_pyseccm_manifest_v216.json"
    _write_json(manifest_path, manifest)
    _write_manual_runner(dirs["root"], manifest_entries)
    return {
        "root": str(dirs["root"]), "main_data": str(dirs["data"]), "manifest": str(manifest_path),
        "n_generated_files": len(generated), "n_parser_commands": len(manifest_entries),
        "n_drt_files": len(list(dirs["drt"].glob("*.csv"))), "n_media_files": media_info.get("count", 0), "media": media_info, "generated_files": generated,
        "parser_commands": manifest_entries, "coordinate_mode": coordinate_mode,
        "files": [str(manifest_path), str(dirs["root"] / "Run_all_traditional_pySECCM_parsers.bat"), str(dirs["data"]), str(dirs["drt"])],
    }


def commands_from_manifest(data_dir: Path, script_root: Path, python_exe: str, include_drt: bool = True):
    data_dir = Path(data_dir); root = data_dir.parent
    manifest_path = root / "legacy_pyseccm_manifest_v216.json"
    if not manifest_path.exists():
        legacy = root / "legacy_pyseccm_manifest_v211.json"
        if legacy.exists():
            manifest_path = legacy
        else:
            return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    commands = []; notes = []
    detected: Dict[str, List[Path]] = {"cv": [], "eis": [], "pos": [], "iv": [], "corr": [], "ocp": [], "avg_ocp": [], "ca": [], "cp": [], "drt_dir": []}
    kind_key = {"CV": "cv", "EIS": "eis", "IV": "iv", "OCP": "ocp", "CA": "ca", "CP": "cp"}
    for entry in manifest.get("parser_commands", []):
        script = script_root / str(entry.get("script") or "")
        args = [str(a) for a in entry.get("args", [])]
        if not script.exists():
            notes.append(f"Skipped {entry.get('kind')}: parser script missing: {script}")
            continue
        missing = [name for name in entry.get("input_files", []) if not (data_dir / name).exists()]
        if missing:
            notes.append(f"Skipped {entry.get('kind')}: missing generated input(s): {', '.join(missing)}")
            continue
        commands.append(([str(python_exe), str(script), *args], data_dir))
        key = kind_key.get(str(entry.get("kind")))
        if key:
            for name in entry.get("input_files", []):
                p = data_dir / name
                if p.exists() and p not in detected[key]: detected[key].append(p)
        for name in entry.get("input_files", []):
            p = data_dir / name; low = name.lower()
            if "takedata" in low and p not in detected["pos"]: detected["pos"].append(p)
            elif "ecorr" in low and p not in detected["corr"]: detected["corr"].append(p)
            elif "avgocp" in low and p not in detected["avg_ocp"]: detected["avg_ocp"].append(p)
    drt = root / "DRT_input_csv"
    if include_drt and drt.exists() and any(drt.glob("*.csv")):
        detected["drt_dir"].append(drt)
        candidates = [script_root / "headless_pyDRTtools" / "pyDRTtools" / "headless_pyDRTtools.py", script_root / "headless_pyDRTtools.py"]
        drt_script = next((p for p in candidates if p.exists()), None)
        if drt_script:
            commands.append(([str(python_exe), str(drt_script), str(drt), "--run_types", "simple", "--plot_types", "eis", "drt", "--export_types", "drt"], drt_script.parent))
        else:
            notes.append(f"DRT inputs exist but headless_pyDRTtools.py was not found under {script_root}")
    return commands, notes, detected
