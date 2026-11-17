#!/usr/bin/env python
"""Print a lightweight inventory of the FaradaIQ core script."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "faradaiq_studio_core.py"

def main() -> int:
    tree = ast.parse(LEGACY.read_text(encoding="utf-8", errors="ignore"))
    classes = [(n.name, n.lineno) for n in tree.body if isinstance(n, ast.ClassDef)]
    funcs = [(n.name, n.lineno) for n in tree.body if isinstance(n, ast.FunctionDef)]
    print(f"Core script: {LEGACY}")
    print(f"Classes: {len(classes)}")
    for name, line in classes:
        print(f"  class {name:<40} line {line}")
    print(f"Functions: {len(funcs)}")
    for name, line in funcs:
        print(f"  def   {name:<40} line {line}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
