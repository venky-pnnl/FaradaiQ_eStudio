"""Launch official pyDRTtools with FaradaIQ-normalized EIS spectra preloaded."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence


def _load_paths(paths: Sequence[str], manifest: str = "") -> List[Path]:
    out = [Path(p).resolve() for p in paths if str(p).strip()]
    if manifest:
        payload = json.loads(Path(manifest).read_text(encoding="utf-8"))
        out.extend(Path(p).resolve() for p in payload.get("strict_files", payload.get("files", [])) if p)
    deduped: List[Path] = []
    seen = set()
    for path in out:
        key = str(path).lower()
        if key not in seen:
            deduped.append(path)
            seen.add(key)
    return deduped


def launch(paths: Sequence[Path]) -> int:
    if not paths:
        raise ValueError("No pyDRTtools input spectra were supplied.")
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError("Missing pyDRTtools input file(s):\n" + "\n".join(missing))

    try:
        from PyQt5 import QtWidgets
        from pyDRTtools.GUI import GUI
        from pyDRTtools.runs import EIS_object
    except Exception as exc:
        raise RuntimeError(
            "Official pyDRTtools is not installed or could not import. Run "
            "install_official_pydrttools_optional.bat, then retry.\n\n" + str(exc)
        ) from exc

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    try:
        from faradaiq.v227_features import apply_qt_theme
        theme_name = apply_qt_theme(app)
    except Exception:
        theme_name = "High Contrast"
    windows = []
    failures = []
    for path in paths:
        try:
            window = GUI()
            window.data = EIS_object.from_file(str(path))
            # Apply the GUI's currently selected inductance policy and draw EIS.
            window.inductance_callback()
            try:
                window.setWindowTitle(f"pyDRTtools — {path.stem} — FaradaIQ {theme_name}")
                window.statusBar().showMessage(f"Loaded by FaradaIQ: {path}", 10000)
            except Exception:
                pass
            window.show()
            windows.append(window)
        except Exception as exc:
            failures.append(f"{path}: {exc}")

    if not windows:
        raise RuntimeError("pyDRTtools could not load any selected spectrum.\n" + "\n".join(failures))
    # Keep references alive for all simultaneously opened spectrum windows.
    app._faradaiq_pydrttools_windows = windows  # type: ignore[attr-defined]
    if failures:
        try:
            QtWidgets.QMessageBox.warning(
                windows[0],
                "Some EIS spectra were not loaded",
                "Loaded " + str(len(windows)) + " spectrum window(s).\n\n" + "\n".join(failures[:12]),
            )
        except Exception:
            pass
    return int(app.exec_())


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Open selected FaradaIQ EIS spectra already loaded in pyDRTtools.")
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--manifest", default="")
    args = parser.parse_args(argv)
    return launch(_load_paths(args.paths, args.manifest))


if __name__ == "__main__":
    raise SystemExit(main())
