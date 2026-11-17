from pathlib import Path
from pptx import Presentation


def _takedata(path: Path):
    # Repeated header + adjacent duplicate station rows + real nontrivial X/Y.
    path.write_text(
        "Sweep #, Imon-2[A], Emon-2[V], X-pos[m], Y-pos[m], Z-pos[m], Time[s]\n"
        "1,1e-9,-0.50,0.001000,0.002000,0.000100,0\n"
        "1,1.2e-9,-0.49,0.00100001,0.00200001,0.000100,0.2\n"
        "Sweep #, Imon-2[A], Emon-2[V], X-pos[m], Y-pos[m], Z-pos[m], Time[s]\n"
        "1,2e-9,-0.48,0.001200,0.002500,0.000101,1\n"
        "1,2.2e-9,-0.47,0.00120001,0.00250001,0.000101,1.2\n"
        "1,3e-9,-0.46,0.001400,0.003000,0.000102,2\n",
        encoding="utf-8",
    )


def test_takedata_parser_preserves_raw_and_collapses_only_spatial_duplicates(tmp_path: Path):
    p=tmp_path/'generic_takedata.txt'; _takedata(p)
    from faradaiq.takedata_v302 import parse_takedata_records, parse_takedata_table, takedata_diagnostics
    raw=parse_takedata_records(p); stations=parse_takedata_table(p); d=takedata_diagnostics(p)
    assert len(raw)==5
    assert len(stations)==3
    assert abs(stations[0]['x_m']-0.001000005)<1e-10
    assert stations[-1]['y_m']==0.003
    assert d['raw_record_count']==5 and d['station_count']==3
    assert d['dominant_axis']=='Distance'
    assert d['median_step_m'] and d['median_step_m']>0


def test_companion_takedata_prefers_physical_table(tmp_path: Path):
    raw=tmp_path/'run_generic.dat'; raw.write_bytes(b'dummy')
    synth=tmp_path/'run_generic_takedata_1_SYNTHETIC_XYZ.txt'; _takedata(synth)
    physical=tmp_path/'run_generic_takedata_1_cleaned.txt'; _takedata(physical)
    from faradaiq.takedata_v302 import find_companion_takedata
    picked,rows=find_companion_takedata(raw, expected_points=3)
    assert picked==physical
    assert len(rows)==3


def test_powerpoint_light_theme_notes_and_plot_density(tmp_path: Path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('v302_reporting_fixture', Path(__file__).with_name('test_v302_reporting.py'))
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    analysis=mod._write_fixture(tmp_path)
    from faradaiq.v290_portable_browser import write_portable_browser
    import json
    rec=write_portable_browser(tmp_path,analysis_root=analysis,create_batch_zips=False)
    manifest=json.loads(Path(rec['manifest_path']).read_text(encoding='utf-8'))
    from faradaiq.v290_powerpoint_summary import write_powerpoint_summary
    ppt=write_powerpoint_summary(tmp_path,analysis_root=analysis,manifest=manifest,use_direct_llm=False,detail_level='compact')
    prs=Presentation(ppt['pptx_path'])
    assert ppt['speaker_notes_included'] is True
    assert ppt['max_scientific_plots_per_slide']==2
    for slide in prs.slides:
        assert 'Presenter notes' in slide.notes_slide.notes_text_frame.text
        # generated plots are pictures; no derived slide should have >2 plot pictures.
        title=' '.join(sh.text for sh in slide.shapes if getattr(sh,'has_text_frame',False))
        if any(k in title for k in ('OCP average','OCP stability','Ecorr','Icorr','Tafel slope','Corrosion rate','Takedata')):
            pics=sum(1 for sh in slide.shapes if sh.shape_type==13)  # MSO_SHAPE_TYPE.PICTURE
            assert pics<=2
    # Light scientific theme: title slide background is near white.
    fill=prs.slides[0].background.fill
    rgb=fill.fore_color.rgb
    assert tuple(rgb) >= (240,240,240)
