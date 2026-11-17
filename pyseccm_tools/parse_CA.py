#!/usr/bin/env python3
"""FaradaIQ CA parser for HEKA ChronoPotStat exports.

Input: *_fullCA*.asc produced by FaradaIQ from HEKA DAT/PUL.
Output: v178-style Figures/<input-stem>/... folders with current/time, charge/time,
and potential/time CSV/PNG files. This is intentionally separate from the official
pySECCM CV/EIS parsers and does not change their behavior.
"""
from __future__ import annotations
import argparse, csv, math, re
from pathlib import Path


def _to_float(x):
    try:
        s=str(x).strip().strip('"')
        if not s:
            return float('nan')
        return float(s)
    except Exception:
        return float('nan')


def _safe(s):
    return re.sub(r'[^A-Za-z0-9._-]+','_',str(s)).strip('_.') or 'CA'


def read_groups(path: Path):
    groups=[]
    current_name='group'
    headers=None
    rows=[]
    with open(path, 'r', encoding='windows-1252', errors='replace', newline='') as f:
        for raw in f:
            line=raw.strip()
            if not line:
                if headers and rows:
                    groups.append((current_name, headers, rows))
                headers=None; rows=[]
                continue
            if line.lower().startswith('series') or line.lower().startswith('sweep'):
                current_name=_safe(line.split(',',1)[0])[:80]
                continue
            if line.startswith('"Index"') or line.startswith('Index'):
                headers=[h.strip().strip('"') for h in next(csv.reader([raw]))]
                rows=[]
                continue
            if headers:
                try:
                    vals=[v.strip().strip('"') for v in next(csv.reader([raw]))]
                except Exception:
                    vals=[]
                if vals:
                    rows.append(vals)
    if headers and rows:
        groups.append((current_name, headers, rows))
    return groups


def normalize_group(headers, rows):
    cols={h.lower().replace(' ',''): i for i,h in enumerate(headers)}
    def col(*names):
        for n in names:
            key=n.lower().replace(' ','')
            if key in cols:
                return cols[key]
        return None
    ti=col('Time[s]', 'time_s')
    ci=col('Imon-2[A]', 'current_A', 'I[A]')
    ei=col('Emon-2[V]', 'potential_V', 'E[V]')
    qi=col('Charge[C]', 'charge_C')
    # In FaradaIQ files there are two Time[s] columns; use the first.
    if ti is None:
        ti=1 if len(headers)>1 else None
    if ci is None:
        ci=2 if len(headers)>2 else None
    if ei is None:
        ei=4 if len(headers)>4 else None
    out=[]
    q=0.0
    prev_t=None; prev_i=None
    for r in rows:
        def get(i):
            return _to_float(r[i]) if i is not None and i < len(r) else float('nan')
        t=get(ti); cur=get(ci); pot=get(ei)
        if qi is not None:
            qv=get(qi)
        else:
            if prev_t is not None and math.isfinite(t) and math.isfinite(cur) and math.isfinite(prev_i):
                q += 0.5*(prev_i+cur)*(t-prev_t)
            qv=q
        if math.isfinite(t) and (math.isfinite(cur) or math.isfinite(pot)):
            out.append((t, cur, pot, qv))
            prev_t=t; prev_i=cur
    return out


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path,'w',encoding='utf-8',newline='') as f:
        w=csv.writer(f); w.writerow(['time_s','current_A','potential_V','charge_C'])
        for r in rows: w.writerow([f'{r[0]:.12G}', f'{r[1]:.12G}', f'{r[2]:.12G}', f'{r[3]:.12G}'])


def plot_xy(path, rows, yidx, ylabel, title):
    if not rows: return
    try:
        import matplotlib.pyplot as plt
        x=[r[0] for r in rows]; y=[r[yidx] for r in rows]
        fig,ax=plt.subplots(figsize=(6.2,4.2), dpi=160)
        ax.plot(x,y,linewidth=1.2)
        ax.set_xlabel('Time (s)'); ax.set_ylabel(ylabel); ax.set_title(title)
        ax.grid(True, alpha=0.3)
        fig.tight_layout(); path.parent.mkdir(parents=True, exist_ok=True); fig.savefig(path); plt.close(fig)
    except Exception as exc:
        path.with_suffix('.plot_error.txt').write_text(str(exc), encoding='utf-8')


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('ca_file')
    ap.add_argument('--plot', action='store_true')
    args=ap.parse_args()
    src=Path(args.ca_file).resolve()
    groups=read_groups(src)
    figroot=src.parent/'Figures'/src.stem
    current_dir=figroot/'Current'
    charge_dir=figroot/'Charge'
    potential_dir=figroot/'Potential'
    all_rows=[]
    for idx,(name,h,rows0) in enumerate(groups):
        rows=normalize_group(h,rows0)
        if not rows: continue
        stem=f'{src.stem}_{idx:03d}_{_safe(name)[:50]}'
        write_csv(current_dir/f'{stem}_time_current_charge_potential.csv', rows)
        if args.plot:
            plot_xy(current_dir/f'{stem}_i_vs_t.png', rows, 1, 'Current (A)', f'{src.stem} current vs time')
            plot_xy(charge_dir/f'{stem}_q_vs_t.png', rows, 3, 'Charge (C)', f'{src.stem} charge vs time')
            plot_xy(potential_dir/f'{stem}_E_vs_t.png', rows, 2, 'Potential (V)', f'{src.stem} potential vs time')
        all_rows.append((idx,name,rows))
    # simple overlay current plot
    if args.plot and all_rows:
        try:
            import matplotlib.pyplot as plt
            fig,ax=plt.subplots(figsize=(6.8,4.6), dpi=160)
            for idx,name,rows in all_rows:
                ax.plot([r[0] for r in rows], [r[1] for r in rows], linewidth=0.8, alpha=0.85)
            ax.set_xlabel('Time (s)'); ax.set_ylabel('Current (A)'); ax.set_title(src.stem+' CA overlay')
            ax.grid(True, alpha=0.3); fig.tight_layout(); (figroot/'Overlays').mkdir(parents=True,exist_ok=True); fig.savefig(figroot/'Overlays'/(src.stem+'_i_vs_t_overlay.png')); plt.close(fig)
        except Exception as exc:
            (figroot/'Overlays').mkdir(parents=True,exist_ok=True); (figroot/'Overlays'/'overlay_error.txt').write_text(str(exc), encoding='utf-8')
    print(f'Parsed {len(all_rows)} CA group(s) from {src.name} into {figroot}')

if __name__=='__main__':
    main()
