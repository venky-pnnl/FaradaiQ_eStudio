from __future__ import annotations

from pathlib import Path
import csv
import json
import math
import zipfile


def _write_csv(path: Path, header: list[str], rows: list[list[float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _synthetic_potential_eis_root(root: Path) -> dict:
    datasets = []
    potentials = [0.50, 0.30, 0.10, -0.10, -0.35]
    freqs = [1e6, 1e5, 1e4, 1e3, 1e2, 1e1, 1]
    for idx, pot in enumerate(potentials, 1):
        base = f"sample__m{idx:03d}__Mott-Schottky_{pot:.4f}_V"
        ny = root / 'analysis' / f'{base}_Nyquist.csv'
        bm = root / 'analysis' / f'{base}_Bode_mag.csv'
        bp = root / 'analysis' / f'{base}_Bode_phase.csv'
        ny_rows=[]; bm_rows=[]; bp_rows=[]
        for j, f in enumerate(freqs):
            zr = 300.0 + 40.0*j + 120.0*(0.5-pot)
            zi = max(5.0, 260.0/(j+1) + 25.0*(0.5-pot))
            mag = math.hypot(zr, zi)
            phase = -math.degrees(math.atan2(zi, zr))
            ny_rows.append([zr, zi]); bm_rows.append([f, mag]); bp_rows.append([f, phase])
        _write_csv(ny, ['z_real_ohm','neg_z_imag_ohm'], ny_rows)
        _write_csv(bm, ['frequency_Hz','z_mag_ohm'], bm_rows)
        _write_csv(bp, ['frequency_Hz','phase_deg'], bp_rows)
        for kind, p in [('Nyquist', ny), ('Bode_mag', bm), ('Bode_phase', bp)]:
            datasets.append({'technique_key':'EIS','display_name':f'{base}_{kind}','physical_path':str(p.relative_to(root)),'set_potential_V':pot})
        drt = root / 'analysis' / 'DRT' / f'{base}_simple_DRT.csv'
        tau = [1e-6,1e-5,1e-4,1e-3,1e-2,1e-1,1]
        gamma = [max(0.0, 80*math.exp(-((math.log10(t)+2.8-0.8*pot)**2)/0.55)) for t in tau]
        _write_csv(drt, ['tau_s','gamma_ohm'], list(map(list, zip(tau,gamma))))
        datasets.append({'technique_key':'DRT','display_name':f'{base} DRT distribution','physical_path':str(drt.relative_to(root)),'set_potential_V':pot})
    # trigger MT-specific presentation mode
    ms = root / 'analysis' / 'sample__mott_schottky_capacitance_summary.csv'
    _write_csv(ms, ['potential_V','capacitance_F'], [[p,1e-5+1e-6*i] for i,p in enumerate(potentials)])
    datasets.append({'technique_key':'MOTT_SCHOTTKY','display_name':'Mott-Schottky capacitance summary','physical_path':str(ms.relative_to(root))})
    return {'experiment_name':'synthetic potential-dependent EIS', 'datasets':datasets}


def test_v319_overlay_figure_helpers_create_nyquist_bode_drt_and_map(tmp_path):
    from faradaiq import v290_powerpoint_summary as ppt
    manifest = _synthetic_potential_eis_root(tmp_path)
    assets = tmp_path / 'ppt_summary_assets'; assets.mkdir()
    selected = ppt._v319_selected_potentials(tmp_path, manifest, limit=5)
    assert len(selected) == 5
    assert ppt._v319_plot_nyquist_overlay(tmp_path, manifest, assets, selected)
    assert ppt._v319_plot_bode_overlay(tmp_path, manifest, assets, selected)
    assert ppt._v319_plot_drt_overlay(tmp_path, manifest, assets, selected)
    assert ppt._v319_plot_drt_map(tmp_path, manifest, assets)
    for name in ('v319_nyquist_overlay.png','v319_bode_overlay.png','v319_drt_overlay.png','v319_drt_map.png'):
        p = assets / name
        assert p.exists() and p.stat().st_size > 1000


def test_v319_summary_ppt_appends_overlay_and_drt_map_slides(tmp_path, monkeypatch):
    from faradaiq import v290_powerpoint_summary as ppt
    from pptx import Presentation
    manifest = _synthetic_potential_eis_root(tmp_path)
    # Create a minimal preexisting deterministic deck so this test focuses on the v319 overlay appendix.
    Presentation().save(tmp_path / ppt.PPTX_NAME)
    receipt = {'slide_count':0,'slide_index':[]}
    out = ppt._v319_append_overlay_eis_drt_slides(tmp_path, manifest, receipt)
    titles = [str(x.get('title') or '') for x in out.get('slide_index') or []]
    assert out.get('v319_overlay_eis_drt_slides') is True
    assert titles == [
        'Potential-resolved EIS overlay: Nyquist',
        'Potential-resolved EIS overlays: Bode magnitude and phase',
        'Potential-resolved DRT overlay',
        'DRT potential map and dominant relaxation trend',
    ]
    pptx = tmp_path / ppt.PPTX_NAME
    with zipfile.ZipFile(pptx) as z:
        text = '\n'.join(z.read(n).decode('utf-8', errors='ignore') for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml'))
    for title in titles:
        assert title in text


def test_v320_public_release_identity_and_entrypoint():
    from faradaiq import release_identity as rid
    root = Path(__file__).resolve().parents[1]
    assert rid.APP_VERSION == 'v320'
    assert rid.SEMVER == '0.320.0'
    assert rid.INSTALL_TARGET == r'C:\FaradaIQ\Studio\V320'
    app = (root / 'faradaiq' / 'app.py').read_text(encoding='utf-8', errors='replace')
    assert '_v320_main_final' in app
    assert app.index('_v320_main_final') < app.index('_v319_main_final')

def test_v319_drt_overlay_discovers_completed_drt_files_when_browser_manifest_omits_them(tmp_path):
    from faradaiq import v290_powerpoint_summary as ppt
    manifest = _synthetic_potential_eis_root(tmp_path)
    manifest['datasets'] = [d for d in manifest['datasets'] if d.get('technique_key') != 'DRT']
    assets = tmp_path / 'ppt_summary_assets'; assets.mkdir()
    selected = ppt._v319_selected_potentials(tmp_path, manifest, limit=5)
    curves = ppt._v319_curve_datasets(tmp_path, manifest, technique='DRT')
    assert len(curves) == 5
    assert ppt._v319_plot_drt_overlay(tmp_path, manifest, assets, selected)
    assert ppt._v319_plot_drt_map(tmp_path, manifest, assets)

def test_v319_compacts_technique_coverage_table_to_avoid_slide_overflow(tmp_path):
    from faradaiq import v290_powerpoint_summary as ppt
    from pptx import Presentation
    from pptx.util import Inches
    prs=Presentation(); slide=prs.slides.add_slide(prs.slide_layouts[6])
    tb=slide.shapes.add_textbox(Inches(0.6),Inches(0.3),Inches(8),Inches(0.5)); tb.text='Technique coverage and data readiness'
    table_shape=slide.shapes.add_table(9,5,Inches(0.72),Inches(1.55),Inches(11.9),Inches(4.75))
    table=table_shape.table
    long=['Technique','EIS','Mott-Schottky / capacitance','Measured vs fitted EIS and residual diagnostics','EIS validity and fit quality','Potential-dependent EIS circuit modeling','Complex capacitance and apparent PZC','Mott-Schottky validity diagnostics','Potential-resolved DRT peak tracks']
    for i,text in enumerate(long): table.cell(i,0).text=text
    assert table.columns[0].width/Inches(1) < 3.0
    changed=ppt._v319_compact_coverage_slide(prs)
    assert changed is True
    assert table.columns[0].width/Inches(1) >= 3.9
    assert sum(r.height for r in table.rows)/Inches(1) <= 5.3
