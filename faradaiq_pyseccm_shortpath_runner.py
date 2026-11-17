#!/usr/bin/env python
"""Run a pySECCM parser through a short staging alias, then copy outputs back.

This avoids Windows cmd.exe failures like "The current directory is invalid" when
FaradaIQ export folders and generated filenames together exceed classic Windows
path limits.  Original folders are not renamed; the parser runs on a temporary
short alias and the parser outputs are copied/renamed back next to the original
source file.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


def _normal(p: str | Path) -> Path:
    return Path(p).expanduser().resolve()


def _win_path(p: Path) -> str:
    s = str(p)
    if os.name == "nt":
        s = os.path.abspath(s)
        if not s.startswith("\\\\?\\"):
            if s.startswith("\\\\"):
                # UNC path -> \\?\UNC\server\share\...
                s = "\\\\?\\UNC\\" + s.lstrip("\\")
            else:
                s = "\\\\?\\" + s
    return s


def _safe_name(text: str, max_len: int = 44) -> str:
    import re
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "file")).strip("_.")
    return (s[:max_len].strip("_.") or "file")


def suggest_short_name(src: Path, ordinal: int = 1) -> str:
    name = src.name
    low = name.lower()
    rate = None
    cycles = None
    import re
    m = re.search(r"cv\s*([0-9]+(?:\.[0-9]+)?)\s*mVperS", name, flags=re.I)
    if not m:
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*mV\s*per\s*s", name, flags=re.I)
    if m:
        rate = m.group(1).replace(".", "p")
    m = re.search(r"([0-9]+)\s*cycles?", name, flags=re.I)
    if m:
        cycles = m.group(1)
    h = hashlib.sha1(str(src).encode("utf-8", errors="ignore")).hexdigest()[:6]
    ext = src.suffix or ".asc"
    if "cv" in low:
        parts = ["CV", f"{ordinal:02d}"]
        if rate: parts.append(f"{rate}mVs")
        if cycles: parts.append(f"{cycles}cy")
        parts.append(h)
        return _safe_name("_".join(parts), 64) + ext
    return _safe_name(src.stem, 48) + "_" + h + ext


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_win_path(src), _win_path(dst))


def copy_tree_outputs(staging: Path, original_cwd: Path, short_base: str, orig_base: str, ext: str) -> dict:
    copied = {"groups": 0, "figures": 0, "other": 0}
    # Copy group tables back using the original long basename so downstream FaradaIQ
    # and pySECCM conventions still find <original>_Groups/<original>_000.asc.
    src_groups = staging / f"{short_base}_Groups"
    dst_groups = original_cwd / f"{orig_base}_Groups"
    if src_groups.exists():
        dst_groups.mkdir(parents=True, exist_ok=True)
        for f in sorted(src_groups.iterdir()):
            if not f.is_file():
                continue
            new_name = f.name.replace(short_base, orig_base, 1) if f.name.startswith(short_base) else f"{orig_base}_{f.name}"
            copy_file(f, dst_groups / new_name)
            copied["groups"] += 1
    # Copy plot figures/data back and rename to original basename.
    src_fig_root = staging / "Figures" / short_base
    dst_fig_root = original_cwd / "Figures" / orig_base
    if src_fig_root.exists():
        dst_fig_root.mkdir(parents=True, exist_ok=True)
        for f in sorted(src_fig_root.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(src_fig_root)
            new_parts = []
            for part in rel.parts:
                new_parts.append(part.replace(short_base, orig_base, 1) if part.startswith(short_base) else part)
            copy_file(f, dst_fig_root.joinpath(*new_parts))
            copied["figures"] += 1
    return copied


def append_alias_manifest(original_cwd: Path, row: dict) -> None:
    json_path = original_cwd / "faradaiq_parser_short_name_aliases.json"
    csv_path = original_cwd / "faradaiq_parser_short_name_aliases.csv"
    data = []
    try:
        if json_path.exists():
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                data = []
    except Exception:
        data = []
    data.append(row)
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        fields = ["time", "parser", "original_file", "position_file", "short_alias", "staging_dir", "returncode", "copied_groups", "copied_figures", "reason"]
        w = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FaradaIQ short-path runner for pySECCM parsers")
    ap.add_argument("--script", required=True, help="Parser script path, e.g. parse_CV.py")
    ap.add_argument("--input", required=True, help="Original primary input file path, e.g. CV/CA/CP file or EIS file")
    ap.add_argument("--pos-input", default="", help="Optional original position/takedata file for parse_EIS.py")
    ap.add_argument("--original-cwd", required=True, help="Original output/data folder")
    ap.add_argument("--short-name", default="", help="Short staged filename to use for the primary input")
    ap.add_argument("--pos-short-name", default="", help="Short staged filename for --pos-input")
    ap.add_argument("--reason", default="long Windows path")
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--", dest="extra", nargs="*")
    ns, unknown = ap.parse_known_args(argv)

    src = _normal(ns.input)
    original_cwd = _normal(ns.original_cwd)
    script = _normal(ns.script)
    pos_src = _normal(ns.pos_input) if str(ns.pos_input or "").strip() else None
    if not src.exists():
        print(f"[FaradaIQ shortpath runner] ERROR: source not found: {src}")
        return 2
    if pos_src is not None and not pos_src.exists():
        print(f"[FaradaIQ shortpath runner] ERROR: position/takedata source not found: {pos_src}")
        return 5
    if not original_cwd.exists():
        print(f"[FaradaIQ shortpath runner] ERROR: original folder not found: {original_cwd}")
        return 3
    if not script.exists():
        print(f"[FaradaIQ shortpath runner] ERROR: parser script not found: {script}")
        return 4

    short_name = ns.short_name or suggest_short_name(src)
    short_name = Path(short_name).name
    pos_short_name = Path(ns.pos_short_name).name if str(ns.pos_short_name or "").strip() else ("pos_" + hashlib.sha1(str(pos_src).encode("utf-8", errors="ignore")).hexdigest()[:6] + (pos_src.suffix if pos_src else ".txt"))
    token_seed = str(src) + str(pos_src or "") + str(datetime.now())
    token = hashlib.sha1(token_seed.encode("utf-8", errors="ignore")).hexdigest()[:10]
    staging = Path(tempfile.gettempdir()) / "FaradaIQ_parser_staging" / token
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / short_name
    staged_pos = staging / pos_short_name if pos_src is not None else None

    print("[FaradaIQ shortpath runner] Original primary file:", src)
    if pos_src is not None:
        print("[FaradaIQ shortpath runner] Original position/takedata file:", pos_src)
    print("[FaradaIQ shortpath runner] Suggested/accepted primary short alias:", short_name)
    if staged_pos is not None:
        print("[FaradaIQ shortpath runner] Position/takedata short alias:", pos_short_name)
    print("[FaradaIQ shortpath runner] Temporary parser folder:", staging)
    print("[FaradaIQ shortpath runner] Original output folder remains unchanged:", original_cwd)

    copy_file(src, staged)
    if pos_src is not None:
        copy_file(pos_src, staged_pos)
    # Provide style expected by pySECCM plotting.
    for style_src in [original_cwd / "base.mplstyle", script.parent / "base.mplstyle"]:
        if style_src.exists():
            copy_file(style_src, staging / "base.mplstyle")
            break
    if not (staging / "base.mplstyle").exists():
        (staging / "base.mplstyle").write_text("axes.grid: True\nfigure.dpi: 160\nsavefig.dpi: 160\n", encoding="utf-8")

    script_lower = script.name.lower()
    if script_lower == "parse_eis.py" and staged_pos is not None:
        cmd = [sys.executable, str(script), pos_short_name, short_name]
    else:
        cmd = [sys.executable, str(script), short_name]
    if ns.plot:
        cmd.append("--plot")
    # Preserve any unknown trailing parser options such as --n_grid 100.
    cmd.extend([x for x in unknown if x != "--"])
    print("[FaradaIQ shortpath runner] Running:", " ".join(cmd))
    # Stream parser output directly to this process so the parent BAT log captures
    # real pySECCM errors.  While the parser runs, periodically copy partial
    # *_Groups/Figures outputs back to the original folder so users can see
    # v178-style folders appearing during long EIS runs instead of waiting until
    # the very end.
    import time as _time
    proc = subprocess.Popen(cmd, cwd=str(staging))
    copied = {"groups": 0, "figures": 0, "other": 0}
    last_copy = 0.0
    while True:
        rc = proc.poll()
        now = _time.time()
        if rc is not None:
            break
        if now - last_copy >= 15.0:
            try:
                copied = copy_tree_outputs(staging, original_cwd, staged.stem, src.stem, src.suffix)
                print(f"[FaradaIQ shortpath runner] Interim copied outputs: groups={copied.get('groups',0)}, figures/data={copied.get('figures',0)}")
            except Exception as exc:
                print(f"[FaradaIQ shortpath runner] WARNING: interim copy failed: {exc}")
            last_copy = now
        _time.sleep(2.0)
    proc.returncode = proc.returncode if proc.returncode is not None else rc

    copied = copy_tree_outputs(staging, original_cwd, staged.stem, src.stem, src.suffix)
    # For EIS, parse_EIS.py also writes <pos>_str.txt from the staged position file.
    # Copy it back with the original takedata basename so v178-style folders remain self-contained.
    try:
        if staged_pos is not None:
            sp_base = staged_pos.with_suffix("")
            staged_str = staging / f"{sp_base.name}_str{staged_pos.suffix}"
            if staged_str.exists():
                pos_base = pos_src.with_suffix("").name if pos_src is not None else "takedata_1"
                copy_file(staged_str, original_cwd / f"{pos_base}_str{staged_pos.suffix}")
                copied["other"] = copied.get("other", 0) + 1
    except Exception as exc:
        print(f"[FaradaIQ shortpath runner] WARNING: could not copy staged position _str file: {exc}")
    row = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "parser": script.name,
        "original_file": str(src),
        "position_file": str(pos_src or ""),
        "short_alias": short_name,
        "staging_dir": str(staging),
        "returncode": proc.returncode,
        "copied_groups": copied.get("groups", 0),
        "copied_figures": copied.get("figures", 0),
        "reason": ns.reason,
    }
    try:
        append_alias_manifest(original_cwd, row)
    except Exception as exc:
        print(f"[FaradaIQ shortpath runner] WARNING: could not write alias manifest: {exc}")
    print(f"[FaradaIQ shortpath runner] Copied outputs back: groups={copied.get('groups',0)}, figures/data={copied.get('figures',0)}")
    return int(proc.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
