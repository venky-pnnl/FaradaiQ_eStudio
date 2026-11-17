from __future__ import annotations

import importlib
import sys

REQUIRED = {
    "pytest": "pytest",
    "altair": "altair",
    "plotly": "plotly",
    "vl_convert": "vl-convert-python",
    "numpy": "numpy",
    "pandas": "pandas",
    "scipy": "scipy",
    "matplotlib": "matplotlib",
    "h5py": "h5py",
    "PIL": "pillow",
    "requests": "requests",
}

failed = []
print("QA Python:", sys.executable)
print("Python version:", sys.version.replace("\n", " "))
for module_name, package_name in REQUIRED.items():
    try:
        module = importlib.import_module(module_name)
        version = getattr(module, "__version__", "available")
        print(f"[OK] {package_name}: {version}")
    except Exception as exc:
        failed.append((package_name, repr(exc)))
        print(f"[MISSING] {package_name}: {exc}")

if failed:
    raise SystemExit(4)
print("All FaradaIQ QA dependencies are available, including Altair.")
