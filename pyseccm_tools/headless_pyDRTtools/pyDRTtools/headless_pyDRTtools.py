#!/usr/bin/env python
"""FaradaIQ v273 auto headless DRT runner.

DRT engine policy in v123:
  --engine auto      Default. Run the bundled v110-compatible pyDRTtools
                     Tikhonov engine with a SciPy nonnegative QP solver.
                     v118 executes selected run types in a clear stage order:
                     simple first, then bayesian, then BHT.
                     This avoids the Windows OSQP/cvxpy access-violation crash
                     and avoids the earlier experimental ridge/NNLS artifact.
  --engine legacy    Same as auto: bundled v110-compatible pyDRTtools math,
                     no PyQt import, SciPy QP first.
  --engine official  Force installed/official ciuccislab pyDRTtools bridge.
                     This is optional because it may not be installed locally.
  --engine stable    Force experimental FaradaIQ ridge/NNLS fallback only.
                     Disabled in auto mode.
"""
from __future__ import annotations
import subprocess, sys, os
from pathlib import Path

# v208 Windows console safety: force UTF-8 when available and make Unicode prints recoverable.
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ENGINE_ALIASES = {
    "auto": "auto",
    "legacy": "legacy",
    "safe": "legacy",
    "safe_legacy": "legacy",
    "v110": "legacy",
    "pydrttools": "legacy",
    "official": "official",
    "github": "official",
    "official_github": "official",
    "official_pydrttools": "official",
    "ciuccislab": "official",
    "current": "official",
    "stable": "stable",
    "experimental": "stable",
    "experimental_stable": "stable",
    "stable_fallback": "stable",
    "ridge": "stable",
    "nnls": "stable",
}

def _strip_engine_args(argv):
    out=[]; skip=False
    for arg in argv:
        if skip:
            skip=False; continue
        if arg == '--engine':
            skip=True; continue
        if str(arg).startswith('--engine='):
            continue
        out.append(arg)
    return out

def _extract_engine(argv):
    engine='auto'
    for i,arg in enumerate(argv):
        if arg == '--engine' and i+1 < len(argv):
            engine=argv[i+1]; break
        if str(arg).startswith('--engine='):
            engine=str(arg).split('=',1)[1]; break
    return ENGINE_ALIASES.get(str(engine).strip().lower(), str(engine).strip().lower())

def _script_path(name):
    return Path(__file__).resolve().parent / name

def _run_child(script_name, argv, label):
    script=_script_path(script_name)
    if not script.exists():
        print(f"ERROR: {label} script not found: {script}", flush=True); return 127
    cmd=[sys.executable, '-W', 'ignore::SyntaxWarning', '-u', str(script)] + _strip_engine_args(list(argv))
    print('='*72, flush=True)
    print(f"FaradaIQ DRT engine: {label}", flush=True)
    print('Command:', ' '.join(cmd), flush=True)
    print('='*72, flush=True)
    try:
        env=os.environ.copy(); env['PYTHONUNBUFFERED']='1'; env['PYTHONWARNINGS']='ignore::SyntaxWarning'
        return subprocess.call(cmd, cwd=str(script.parent), env=env)
    except Exception as exc:
        print(f"ERROR: failed to launch {label}: {exc}", flush=True); return 126

def _print_help_passthrough():
    print(__doc__ or 'FaradaIQ auto DRT runner')
    print('\nCommon usage:')
    print('  headless_pyDRTtools.py input_dir --run_types simple --plot_types eis drt --export_types drt')
    print('  headless_pyDRTtools.py input_dir --engine legacy')
    print('  headless_pyDRTtools.py input_dir --engine official')
    print('  headless_pyDRTtools.py input_dir --engine stable')

def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if not argv or '--help' in argv or '-h' in argv:
        _print_help_passthrough(); return 0
    engine=_extract_engine(argv)
    if engine not in {'auto','official','legacy','stable'}:
        print(f"WARNING: unknown DRT engine '{engine}', using auto.", flush=True); engine='auto'
    if engine in {'auto','legacy'}:
        rc=_run_child('headless_pyDRTtools_legacy_pyDRTtools.py', argv, 'v123 safe legacy pyDRTtools: staged simple->bayesian->BHT, v110 Tikhonov + SciPy nonnegative QP')
        if rc == 0:
            print('FaradaIQ DRT: v123 staged safe legacy pyDRTtools completed successfully.', flush=True)
        return rc
    if engine == 'official':
        return _run_child('headless_pyDRTtools_official_github.py', argv, 'official/installed ciuccislab pyDRTtools')
    if engine == 'stable':
        print('WARNING: using experimental FaradaIQ stable fallback. It is not recommended for scientific DRT on the HEKA test data because it can create monotonic/boundary tau artifacts.', flush=True)
        return _run_child('headless_pyDRTtools_stable_fallback.py', argv, 'EXPERIMENTAL FaradaIQ stable ridge/NNLS fallback')
    return 1

if __name__ == '__main__':
    raise SystemExit(main())
