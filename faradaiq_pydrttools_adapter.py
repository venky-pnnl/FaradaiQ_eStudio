"""FaradaIQ -> official ciuccislab pyDRTtools GUI handoff.

The official pyDRTtools GUI imports one headerless three-column CSV/TXT spectrum:
frequency / Hz, Z' / ohm, Z'' / ohm.  This adapter normalizes the exact EIS
files selected in the FaradaIQ browser and writes a reproducible handoff folder.

pyDRTtools credit: Ciucci Lab, https://github.com/ciuccislab/pyDRTtools
License: MIT (third-party project; not maintained by FaradaIQ).
"""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import pandas as pd

from faradaiq_deareis_adapter import _extract_eis_frame


PYDRTTOOLS_CREDIT = (
    "pyDRTtools credit: Ciucci Lab. FaradaIQ prepares a selected-spectrum "
    "handoff and launches the separate official pyDRTtools GUI."
)


@dataclass
class PyDRTtoolsItem:
    source_file: str
    strict_csv: str
    preview_csv: str
    label: str
    n_points: int
    min_frequency_hz: Optional[float]
    max_frequency_hz: Optional[float]
    warnings: List[str]


@dataclass
class PyDRTtoolsHandoff:
    created: str
    output_dir: str
    strict_files: List[str]
    manifest: str
    readme: str
    warnings: List[str]
    items: List[PyDRTtoolsItem]


def _safe_name(text: object, default: str = "eis") -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text or default)).strip("._-")
    return value or default


def create_pydrttools_handoff(
    paths: Iterable[os.PathLike | str],
    output_root: os.PathLike | str,
    label: str = "FaradaIQ selected EIS",
) -> PyDRTtoolsHandoff:
    selected = [Path(p) for p in paths if str(p or "").strip()]
    if not selected:
        raise ValueError("No EIS files were supplied for pyDRTtools handoff.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = Path(output_root) / f"pydrttools_eis_handoff_{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    strict_files: List[str] = []
    items: List[PyDRTtoolsItem] = []
    warnings: List[str] = []

    for index, source in enumerate(selected, 1):
        try:
            frame, item_warnings = _extract_eis_frame(source)
            # Official EIS_object.from_file reads header=None and columns 0/1/2.
            # Preserve actual complex Z'' sign; pyDRTtools displays -Z'' itself.
            strict = frame[["frequency_Hz", "Zreal_ohm", "Zimag_ohm"]].copy()
            strict = strict.sort_values("frequency_Hz", ascending=False, kind="stable")
            safe = _safe_name(source.stem or f"eis_{index}")
            strict_path = outdir / f"{index:03d}_{safe}_for_pyDRTtools.csv"
            preview_path = outdir / f"{index:03d}_{safe}_preview.csv"
            strict.to_csv(strict_path, index=False, header=False)
            frame.to_csv(preview_path, index=False)
            strict_files.append(str(strict_path))
            items.append(
                PyDRTtoolsItem(
                    source_file=str(source),
                    strict_csv=str(strict_path),
                    preview_csv=str(preview_path),
                    label=source.stem or f"EIS {index}",
                    n_points=int(len(strict)),
                    min_frequency_hz=float(strict["frequency_Hz"].min()),
                    max_frequency_hz=float(strict["frequency_Hz"].max()),
                    warnings=list(item_warnings),
                )
            )
            warnings.extend(f"{source.name}: {message}" for message in item_warnings)
        except Exception as exc:
            warnings.append(f"Skipped {source}: {exc}")

    if not strict_files:
        raise ValueError(
            "No selected file could be converted to frequency/Zreal/Zimag data for pyDRTtools.\n"
            + "\n".join(warnings)
        )

    manifest_path = outdir / "faradaiq_pydrttools_handoff_manifest.json"
    readme_path = outdir / "README_pyDRTtools_handoff.txt"
    result = PyDRTtoolsHandoff(
        created=datetime.now().isoformat(timespec="seconds"),
        output_dir=str(outdir),
        strict_files=strict_files,
        manifest=str(manifest_path),
        readme=str(readme_path),
        warnings=warnings,
        items=items,
    )
    manifest_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    readme_path.write_text(
        "FaradaIQ -> pyDRTtools selected-EIS handoff\n"
        "===========================================\n\n"
        + PYDRTTOOLS_CREDIT
        + "\n\nThe *_for_pyDRTtools.csv files are headerless and contain exactly:\n"
        "  column 1: frequency / Hz\n"
        "  column 2: Z' / ohm\n"
        "  column 3: Z'' / ohm (actual complex sign)\n\n"
        "FaradaIQ opens one pyDRTtools GUI window per selected spectrum so every selected spectrum is already loaded.\n\n"
        "Files:\n"
        + "\n".join(f"- {Path(p).name}" for p in strict_files)
        + "\n\nOfficial project: https://github.com/ciuccislab/pyDRTtools\n",
        encoding="utf-8",
    )
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize FaradaIQ-selected EIS spectra for pyDRTtools.")
    parser.add_argument("paths", nargs="*", help="Selected EIS CSV/TXT files")
    parser.add_argument("--manifest", help="JSON file containing a 'files' list")
    parser.add_argument("--output-root", default=".")
    parser.add_argument("--label", default="FaradaIQ selected EIS")
    args = parser.parse_args(argv)
    paths = list(args.paths)
    if args.manifest:
        payload = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        paths.extend(str(p) for p in payload.get("files", []) if p)
    result = create_pydrttools_handoff(paths, args.output_root, label=args.label)
    print(json.dumps(asdict(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
