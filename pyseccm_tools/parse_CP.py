#!/usr/bin/env python3
"""FaradaIQ CP parser for HEKA chronopotentiometry exports."""
from __future__ import annotations
# Reuse the CA parser format; CP primarily emphasizes E vs t but the file contains I/E/Q.
import sys
from pathlib import Path

# Import sibling parser when copied as part of pyseccm_tools.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse_CA as ca


def main():
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument('cp_file')
    ap.add_argument('--plot', action='store_true')
    args=ap.parse_args()
    src=Path(args.cp_file).resolve()
    groups=ca.read_groups(src)
    figroot=src.parent/'Figures'/src.stem
    potential_dir=figroot/'Potential'
    current_dir=figroot/'Current'
    charge_dir=figroot/'Charge'
    all_rows=[]
    for idx,(name,h,rows0) in enumerate(groups):
        rows=ca.normalize_group(h,rows0)
        if not rows: continue
        stem=f'{src.stem}_{idx:03d}_{ca._safe(name)[:50]}'
        ca.write_csv(potential_dir/f'{stem}_time_potential_current_charge.csv', rows)
        if args.plot:
            ca.plot_xy(potential_dir/f'{stem}_E_vs_t.png', rows, 2, 'Potential (V)', f'{src.stem} potential vs time')
            ca.plot_xy(current_dir/f'{stem}_i_vs_t.png', rows, 1, 'Current (A)', f'{src.stem} current vs time')
            ca.plot_xy(charge_dir/f'{stem}_q_vs_t.png', rows, 3, 'Charge (C)', f'{src.stem} charge vs time')
        all_rows.append((idx,name,rows))
    if args.plot and all_rows:
        try:
            import matplotlib.pyplot as plt
            fig,ax=plt.subplots(figsize=(6.8,4.6), dpi=160)
            for idx,name,rows in all_rows:
                ax.plot([r[0] for r in rows], [r[2] for r in rows], linewidth=0.8, alpha=0.85)
            ax.set_xlabel('Time (s)'); ax.set_ylabel('Potential (V)'); ax.set_title(src.stem+' CP overlay')
            ax.grid(True, alpha=0.3); fig.tight_layout(); (figroot/'Overlays').mkdir(parents=True,exist_ok=True); fig.savefig(figroot/'Overlays'/(src.stem+'_E_vs_t_overlay.png')); plt.close(fig)
        except Exception as exc:
            (figroot/'Overlays').mkdir(parents=True,exist_ok=True); (figroot/'Overlays'/'overlay_error.txt').write_text(str(exc), encoding='utf-8')
    print(f'Parsed {len(all_rows)} CP group(s) from {src.name} into {figroot}')

if __name__=='__main__':
    main()
