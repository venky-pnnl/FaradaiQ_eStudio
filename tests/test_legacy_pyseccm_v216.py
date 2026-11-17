from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

from faradaiq.legacy_pyseccm_v210 import commands_from_manifest, export_legacy


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def test_traditional_folders_and_manifest_routing(tmp_path: Path) -> None:
    structured = tmp_path / "structured"
    cv = structured / "processed" / "cv.csv"
    ca = structured / "processed" / "ca.csv"
    eis1 = structured / "processed" / "eis1.csv"
    eis2 = structured / "processed" / "eis2.csv"
    _write_csv(cv, ["time_s", "potential_V", "current_A"], [
        {"time_s": 0, "potential_V": -0.2, "current_A": -1e-9},
        {"time_s": 1, "potential_V": 0.2, "current_A": 2e-9},
    ])
    _write_csv(ca, ["time_s", "potential_V", "current_A"], [
        {"time_s": 0, "potential_V": 0.1, "current_A": 1e-9},
        {"time_s": 1, "potential_V": 0.1, "current_A": 2e-9},
    ])
    _write_csv(eis1, ["frequency_Hz", "Zreal_ohm", "Zimag_ohm"], [
        {"frequency_Hz": 1000, "Zreal_ohm": 10, "Zimag_ohm": -5},
        {"frequency_Hz": 100, "Zreal_ohm": 20, "Zimag_ohm": -10},
    ])
    _write_csv(eis2, ["frequency_Hz", "Zreal_ohm", "Zimag_ohm"], [
        {"frequency_Hz": 500, "Zreal_ohm": 11, "Zimag_ohm": -6},
        {"frequency_Hz": 50, "Zreal_ohm": 21, "Zimag_ohm": -11},
    ])
    records = [
        {"relative_csv_path": "processed/cv.csv", "heka_technique": "CV", "group_title": "CV-50mV", "point": 1, "cycle": 1},
        {"relative_csv_path": "processed/ca.csv", "heka_technique": "CA", "series_title": "CA", "point": 1},
        {"relative_csv_path": "processed/eis1.csv", "heka_technique": "EIS", "point": 1},
        {"relative_csv_path": "processed/eis2.csv", "heka_technique": "EIS", "point": 2},
    ]
    package_root = Path(__file__).resolve().parents[1]
    info = export_legacy(
        SimpleNamespace(source_path="fixture.dat"),
        {"root": str(structured), "manifest": str(structured / "fixture_heka_v211_manifest.json"), "records": records},
        tmp_path / "out",
        "fixture",
        package_root=package_root,
    )
    root = Path(info["root"])
    expected = {
        "data", "data_generated_from_HEKA", "DRT_input_csv", "data_by_point_sequence",
        "photos", "pyseccm_tools_scripts", "legacy_pyseccm_manifest_v216.json",
        "Run_all_traditional_pySECCM_parsers.bat",
    }
    assert expected.issubset({p.name for p in root.iterdir()})
    assert (root / "data" / "Figures").is_dir()
    assert (root / "data" / "Models").is_dir()
    manifest = json.loads((root / "legacy_pyseccm_manifest_v216.json").read_text(encoding="utf-8"))
    kinds = [x["kind"] for x in manifest["parser_commands"]]
    assert kinds.count("CV") == 1
    assert kinds.count("CA") == 1
    assert kinds.count("EIS") == 2  # two incompatible frequency grids are separated
    assert len(list((root / "DRT_input_csv").glob("*.csv"))) == 2

    commands, notes, detected = commands_from_manifest(root / "data", package_root / "pyseccm_tools", "python", include_drt=False)
    assert notes == []
    assert len(commands) == 4
    assert {Path(argv[1]).name for argv, _cwd in commands} == {"parse_CV.py", "parse_CA.py", "parse_EIS.py"}
    assert detected["cv"] and detected["ca"] and detected["eis"]


def test_missing_manifest_returns_none(tmp_path: Path) -> None:
    assert commands_from_manifest(tmp_path, tmp_path, "python") is None
