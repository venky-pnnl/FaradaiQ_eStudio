#!/usr/bin/env python
r"""Compact an existing FaradaIQ export folder.

Usage:
    python tools/compact_faradaiq_export.py C:\path\to\sample_analysis_ready_csv

This is mainly for old v118/v119 HEKA exports that contain files_snapshot,
data_generated_from_HEKA, DRT_input_csv_all, and *_AI_analysis_payload.npy mirrors.
It writes the v123 compact HDF5+JSON bundle and removes only known redundant caches.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _ensure_project_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compact a FaradaIQ analysis_ready_csv folder into v123 HDF5+JSON format.")
    parser.add_argument("analysis_root", help="Path to *_analysis_ready_csv folder or sample output folder")
    parser.add_argument("--base-name", default=None, help="Optional base name; inferred from folder name if omitted")
    parser.add_argument("--aggressive", action="store_true", help="Also remove old files_snapshot/context-pack timestamp mirrors where found")
    ns = parser.parse_args(argv)

    _ensure_project_root()
    import faradaiq_studio_core as core  # noqa: WPS433

    root = Path(ns.analysis_root).resolve()
    if not root.exists():
        raise SystemExit(f"Folder not found: {root}")
    # If the user passes the sample folder, choose its analysis_ready_csv child.
    if not root.name.endswith("_analysis_ready_csv"):
        children = sorted([p for p in root.glob("*_analysis_ready_csv") if p.is_dir()])
        if children:
            root = children[0]
    cleanup = core._v120_cleanup_redundant_export_files(root, aggressive=bool(ns.aggressive))
    info = core.make_ai_analysis_bundle(root, base_name=ns.base_name, label="manual_compact_cli", copy_files=False)
    result = {"analysis_root": str(root), "cleanup": cleanup, "bundle": info}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
