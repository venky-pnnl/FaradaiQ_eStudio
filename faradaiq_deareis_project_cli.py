#!/usr/bin/env python
"""Create a native DearEIS project JSON from FaradaIQ-normalized EIS CSV files.

This script is executed with the optional .deareis_venv Python so it uses the
installed DearEIS version to write a project file that DearEIS can reopen.  It is
kept separate from FaradaIQ's main Python environment to avoid dependency
conflicts.
"""
from __future__ import annotations
import argparse, csv, json, sys
from pathlib import Path
import numpy as np


def read_eis_csv(path: Path):
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        raise ValueError(f'No rows in {path}')
    def pick(names):
        low = {k.lower().replace(' ','').replace('_',''): k for k in rows[0].keys()}
        for n in names:
            key = n.lower().replace(' ','').replace('_','')
            if key in low:
                return low[key]
        for k0, orig in low.items():
            if any(n.lower().replace(' ','').replace('_','') in k0 for n in names):
                return orig
        raise KeyError(f'Could not find column {names} in {path.name}')
    fc = pick(['frequency_Hz','frequency','freq','f_Hz'])
    rc = pick(['Zreal_ohm','Zreal','Re(Z)','RealZ'])
    ic = pick(['Zimag_ohm','Zimag','Im(Z)','ImagZ'])
    fvals=[]; zvals=[]
    for r in rows:
        try:
            fr = float(str(r[fc]).replace(',',''))
            zr = float(str(r[rc]).replace(',',''))
            zi = float(str(r[ic]).replace(',',''))
        except Exception:
            continue
        if np.isfinite(fr) and np.isfinite(zr) and np.isfinite(zi) and fr > 0:
            fvals.append(fr); zvals.append(complex(zr, zi))
    if len(fvals) < 2:
        raise ValueError(f'Fewer than two valid EIS points in {path}')
    return np.asarray(fvals, dtype=float), np.asarray(zvals, dtype=complex)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--label', default='FaradaIQ DearEIS handoff')
    ap.add_argument('csv_files', nargs='+')
    ns = ap.parse_args(argv)
    import deareis  # type: ignore
    project = deareis.Project()
    try: project.set_label(ns.label)
    except Exception: pass
    try: project.set_notes('Created by FaradaIQ Studio from normalized EIS CSV files. DearEIS is a separate third-party EIS analysis application; credit to the DearEIS developers / Ville Yrjana.')
    except Exception: pass
    added=[]
    for fn in ns.csv_files:
        path = Path(fn).resolve()
        f, z = read_eis_csv(path)
        ds = deareis.DataSet(frequencies=f, impedances=z, path=str(path), label=path.stem)
        project.add_data_set(ds)
        added.append(str(path))
    out = Path(ns.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    project.save(str(out))
    sidecar = out.with_suffix('.created_by_faradaiq.json')
    sidecar.write_text(json.dumps({'project': str(out), 'added_csv_files': added}, indent=2), encoding='utf-8')
    print(str(out))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
