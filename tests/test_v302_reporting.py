from __future__ import annotations
import csv, json
from pathlib import Path
from pptx import Presentation


def _write_fixture(root: Path):
    analysis=root/'analysis'; analysis.mkdir()
    ocp=analysis/'OCP_Tail_Average_v294'/'ocp_final_generic'; ocp.mkdir(parents=True)
    rows=[]
    for i in range(1,5):
        rows.append({'point_index':i,'x_position_m':i*1e-6,'y_position_m':0,'scan_distance_m':(i-1)*1e-6,'x_position_um':i,'y_position_um':0,'scan_distance_um':i-1,'mean_ocp_V':-0.45+0.01*i,'std_ocp_V':0.001*i,'sem_ocp_V':0.0005*i,'tail_drift_V_per_s':1e-5*i,'final_time_percent':30,'window_basis':'elapsed_time'})
    with (ocp/'ocp_average_spatial.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    (ocp/'ocp_tail_handoff_manifest.json').write_text(json.dumps({'calculation':{'horizontal_axis_resolved':'X','window':'final 30% of elapsed time span'},'errors':[]}),encoding='utf-8')
    cor=analysis/'corrosion_metrics'; cor.mkdir()
    crows=[]
    for i in range(1,5):
        crows.append({'point_index':i,'x_position_m':i*1e-6,'y_position_m':0,'scan_distance_m':(i-1)*1e-6,'ecorr_V':-0.6+0.01*i,'icorr_A':1e-8*i,'icorr_A_cm2':1e-7*i,'cathodic_tafel_mV_dec':120+i,'anodic_tafel_mV_dec':90+i,'cathodic_r2':0.98,'anodic_r2':0.97,'corrosion_rate_mm_per_year':0.02*i,'zero_crossing_cluster_count':2 if i==3 else 1,'dip_count':2 if i==3 else 1,'ambiguity_status':'AMBIGUOUS' if i==3 else 'CLEAR','flags':['MULTIPLE_DIPS'] if i==3 else ['CLEAR']})
    payload={'schema':'faradaiq-corrosion-metrics-v298','horizontal_axis':'X','settings':{'surface_area_cm2':0.1,'equivalent_weight_g_per_equiv':27,'density_g_cm3':2.7},'rows':crows}
    (cor/'robust_corrosion_metrics_20260101_120000.json').write_text(json.dumps(payload),encoding='utf-8')
    with (cor/'robust_corrosion_metrics_20260101_120000.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(crows[0])); w.writeheader(); w.writerows(crows)
    take=analysis/'generic_takedata_1.txt'
    take.write_text("Sweep #, Imon-2[A], Emon-2[V], X-pos[m], Y-pos[m], Z-pos[m], Time[s]\n" + "\n".join([
        f"1, {1e-9*i}, {-0.5+0.01*i}, {i*1e-6}, {0.2e-6*i}, {5e-6}, {i*2.0}" for i in range(1,5)
    ]) + "\n", encoding='utf-8')
    return analysis


def test_shared_browser_powerpoint_electrochemistry_reporting(tmp_path: Path):
    analysis=_write_fixture(tmp_path)
    from faradaiq.report_results_v302 import collect_electrochemistry_results
    report=collect_electrochemistry_results(tmp_path,analysis)
    assert report['result_count']==3
    assert {a['id'] for a in report['ocp'][0]['axis_options']} >= {'Point','X','Y','Distance'}
    assert report['corrosion'][0]['status_counts']['AMBIGUOUS']==1
    assert {m['id'] for m in report['corrosion'][0]['metrics']} >= {'ecorr_V','icorr_A','cathodic_tafel_mV_dec','anodic_tafel_mV_dec','corrosion_rate_mm_per_year'}

    from faradaiq.v290_portable_browser import write_portable_browser
    receipt=write_portable_browser(tmp_path,analysis_root=analysis,create_batch_zips=False)
    manifest=json.loads(Path(receipt['manifest_path']).read_text(encoding='utf-8'))
    html=Path(receipt['html_path']).read_text(encoding='utf-8')
    assert manifest['electrochemistry_results']['result_count']==3
    assert 'Electrochem &amp; spatial' in html
    assert 'renderElectrochemistry' in html
    assert 'Corrosion rate / mm y⁻¹' in html

    from faradaiq.v290_powerpoint_summary import write_powerpoint_summary
    ppt=write_powerpoint_summary(tmp_path,analysis_root=analysis,manifest=manifest,use_direct_llm=False,detail_level='comprehensive')
    prs=Presentation(ppt['pptx_path'])
    all_text='\n'.join(sh.text for sl in prs.slides for sh in sl.shapes if getattr(sh,'has_text_frame',False))
    for required in ('FaradaIQ Consolidated Results Summary','Open-circuit potential (OCP): consolidated result','Corrosion, LPR, and polarization analysis: consolidated result'):
        assert required in all_text
    for legacy_debug in ('OCP average vs X coordinate','Takedata technical diagnostics','Corrosion fit quality and ambiguity'):
        assert legacy_debug not in all_text
    assert ppt['consolidated_results_per_technique'] is True
    assert ppt['professional_deck_mode'] is True
    assert ppt['speaker_notes_included'] is True
    assert ppt['scientific_story_planner'] is True
    assert ppt['presentation_theme']=='scientific_consolidated_light_v311'
    assert all('Presenter notes' in sl.notes_slide.notes_text_frame.text for sl in prs.slides)


def test_report_collector_does_not_invent_rate_without_material_inputs(tmp_path: Path):
    analysis=_write_fixture(tmp_path)
    path=next((analysis/'corrosion_metrics').glob('*.json'))
    payload=json.loads(path.read_text())
    for row in payload['rows']:
        row['icorr_A_cm2']=None; row['corrosion_rate_mm_per_year']=None
    payload['settings']={}
    path.write_text(json.dumps(payload))
    from faradaiq.report_results_v302 import collect_electrochemistry_results
    result=collect_electrochemistry_results(tmp_path,analysis)['corrosion'][0]
    metric_ids={m['id'] for m in result['metrics']}
    assert 'icorr_A_cm2' not in metric_ids
    assert 'corrosion_rate_mm_per_year' not in metric_ids
