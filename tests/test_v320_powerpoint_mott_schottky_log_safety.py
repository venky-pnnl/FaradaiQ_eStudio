from __future__ import annotations

from pathlib import Path
import csv
import math


def _write_csv(path: Path, header: list[str], rows: list[list[float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)


def _manifest(root: Path) -> dict:
    datasets=[]
    for idx,pot in enumerate([0.50, 0.10, -0.35], 1):
        base=f'sample__m{idx:03d}__Mott-Schottky_{pot:.4f}_V'
        freqs=[1e5,1e4,1e3,1e2,1e1,1]
        ny=root/'analysis'/f'{base}_Nyquist.csv'
        bm=root/'analysis'/f'{base}_Bode_mag.csv'
        bp=root/'analysis'/f'{base}_Bode_phase.csv'
        nyrows=[]; bmrows=[]; bprows=[]
        for j,freq in enumerate(freqs):
            zr=200+50*j+80*(0.5-pot); zi=220/(j+1)+15*(0.5-pot)
            nyrows.append([zr,zi]); bmrows.append([freq,math.hypot(zr,zi)]); bprows.append([freq,-math.degrees(math.atan2(zi,zr))])
        _write_csv(ny,['z_real_ohm','neg_z_imag_ohm'],nyrows)
        _write_csv(bm,['frequency_Hz','z_mag_ohm'],bmrows)
        _write_csv(bp,['frequency_Hz','phase_deg'],bprows)
        for kind,path in [('Nyquist',ny),('Bode_mag',bm),('Bode_phase',bp)]:
            datasets.append({'technique_key':'EIS','display_name':f'{base}_{kind}','physical_path':str(path.relative_to(root)),'set_potential_V':pot})
        drt=root/'analysis'/'DRT'/f'{base}_simple_DRT.csv'
        tau=[1e-6,1e-5,1e-4,1e-3,1e-2,1e-1,1]
        gamma=[20+10*i+5*pot for i in range(len(tau))]
        _write_csv(drt,['tau_s','gamma_ohm'],[[a,b] for a,b in zip(tau,gamma)])
        datasets.append({'technique_key':'DRT','display_name':f'{base} DRT distribution','physical_path':str(drt.relative_to(root)),'set_potential_V':pot})
    ms=root/'analysis'/'sample__mott_schottky_capacitance_summary.csv'
    _write_csv(ms,['potential_V','capacitance_F'],[[0.5,1e-5],[0.1,1.1e-5],[-0.35,1.2e-5]])
    datasets.append({'technique_key':'MOTT_SCHOTTKY','display_name':'Mott-Schottky capacitance summary','physical_path':str(ms.relative_to(root))})
    return {'experiment_name':'Mott-Schottky log-safety QA','datasets':datasets}


def test_v320_mott_schottky_overlay_rendering_is_safe_on_mathtext_hostile_backend(tmp_path, monkeypatch):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.figure import Figure
    from matplotlib.ticker import LogFormatter
    from faradaiq import v290_powerpoint_summary as ppt

    manifest=_manifest(tmp_path)
    assets=tmp_path/'assets'; assets.mkdir()
    selected=ppt._v319_selected_potentials(tmp_path,manifest,limit=3)
    original=Figure.savefig

    def hostile_savefig(self, *args, **kwargs):
        for ax in self.axes:
            if ax.get_xscale() == 'log' and isinstance(ax.xaxis.get_major_formatter(), LogFormatter):
                raise RuntimeError("ParseException: Expected end of text, found '$' in mathdefault log tick")
            if ax.get_yscale() == 'log' and isinstance(ax.yaxis.get_major_formatter(), LogFormatter):
                raise RuntimeError("ParseException: Expected end of text, found '$' in mathdefault log tick")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Figure,'savefig',hostile_savefig)

    assert ppt._v319_plot_bode_overlay(tmp_path,manifest,assets,selected)
    assert ppt._v319_plot_drt_overlay(tmp_path,manifest,assets,selected)
    assert ppt._v319_plot_drt_map(tmp_path,manifest,assets)
