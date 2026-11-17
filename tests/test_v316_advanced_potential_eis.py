import os
import numpy as np
from faradaiq.v316_advanced_potential_eis import analyze_mott_grid_advanced, _select_models, _kk_style_screen


def _spectrum(v=0.0):
    f=np.geomspace(1e6,1,61); w=2*np.pi*f
    Rs=320+5*v; L=5.5e-5; Q=1.4e-6*(1+0.15*v); n=0.92
    z=Rs+1j*w*L+1/(Q*(1j*w)**n)
    return f,z


def test_model_selection_prefers_identifiable_blocking_model():
    f,z=_spectrum()
    best,fits=_select_models(f,z)
    assert best['model'] in {'L-Rs-CPE','L-Rs-(R||CPE)','L-Rs-CPE-Wo'}
    assert 250 < best['Rs_ohm'] < 450
    assert 1e-5 < best['L_H'] < 2e-4
    assert best['rmse'] < 0.05


def test_kk_fallback_or_linkk_accepts_causal_spectrum():
    f,z=_spectrum()
    q=_kk_style_screen(f,z)
    assert q['kk_style_status'] in {'PASS','REVIEW'}
    assert q['kk_style_rmse'] < 0.10


def test_grid_analysis_gates_quantum_and_bad_mott_schottky(monkeypatch):
    for key in ('FARADAIQ_ELECTRODE_AREA_CM2','FARADAIQ_CDL_UF_CM2','FARADAIQ_ELECTRODE_CLASS','FARADAIQ_SEMICONDUCTOR_EPS_R'):
        monkeypatch.delenv(key, raising=False)
    vs=np.linspace(0.5,-0.35,17); rows=[]
    for i,v in enumerate(vs,1):
        f,z=_spectrum(v)
        for j,(ff,zz) in enumerate(zip(f,z),1): rows.append([i,j,v,ff,zz.real,zz.imag])
    arr=np.asarray(rows,float)
    r=analyze_mott_grid_advanced({'array':arr,'columns':['potential_step_index','frequency_index','potential_V','frequency_Hz','Zreal_ohm','Zimag_ohm'],'n_potential_steps':17,'n_frequencies':61})
    assert r['status']=='ok'
    assert r['interpretation']['quantum_capacitance_status']=='not_identifiable_from_EIS_alone'
    assert len(r['summary'])==17
    assert all(x['status'] in {'REJECT','PASS_CANDIDATE'} for x in r['mott'])


def test_advanced_export_includes_fit_diagnostics_and_scientific_summary(tmp_path):
    from faradaiq.v316_advanced_potential_eis import _export_advanced
    vs=np.linspace(0.5,-0.35,17); rows=[]
    for i,v in enumerate(vs,1):
        f,z=_spectrum(v)
        for j,(ff,zz) in enumerate(zip(f,z),1):
            rows.append([i,j,v,ff,zz.real,zz.imag])
    ms={
        'array':np.asarray(rows,float),
        'columns':['potential_step_index','frequency_index','potential_V','frequency_Hz','Zreal_ohm','Zimag_ohm'],
        'n_potential_steps':17,
        'n_frequencies':61,
        'title':'synthetic potential EIS',
    }
    class Parser:
        decoded={'mott_schottky':[ms]}
    out=_export_advanced(Parser(),tmp_path,'demo')
    adir=tmp_path/'demo_Advanced_Potential_EIS'
    fit=adir/'demo__fit_diagnostics_all_potentials.csv'
    summary=adir/'demo__advanced_scientific_summary.json'
    assert fit.is_file()
    assert summary.is_file()
    lines=fit.read_text(encoding='utf-8').splitlines()
    assert len(lines)==17*61+1
    payload=__import__('json').loads(summary.read_text(encoding='utf-8'))
    assert payload['potential_steps']==17
    assert payload['quantum_capacitance_claim']=='not_reported_without_independent_low_DOS_and_Cdl_context'
    assert 'model_counts' in payload
    assert 'conclusion_bullets' in payload and payload['conclusion_bullets']


def test_powerpoint_narrative_uses_advanced_summary_without_llm():
    from faradaiq.v316_advanced_potential_eis import _augment_ppt_narrative
    narrative={'executive_summary':[],'conclusions':[],'global_cautions':[],'technique_discussions':{},'presenter_notes':{},'slide_content':{}}
    summary={
        'potential_steps':85,
        'model_counts':{'L-Rs-CPE':80,'L-Rs-(R||CPE)':5},
        'median_Rs_ohm':326.0,
        'median_L_uH':56.7,
        'median_inductive_capacitive_crossover_Hz':24300.0,
        'finite_parallel_R_candidate_count':5,
        'finite_parallel_R_potential_min_V':-0.35,
        'finite_parallel_R_potential_max_V':-0.3095,
        'mott_schottky_status':'no_frequency_passed_strict_Mott_Schottky_checks',
        'pzc_status':'no_frequency_stable_PZC_assignment',
        'quantum_capacitance_claim':'not_reported_without_independent_low_DOS_and_Cdl_context',
        'conclusion_bullets':['80/85 spectra are best represented by a blocking distributed-capacitance model.'],
        'limitation_bullets':['Quantum capacitance is not separately identifiable from total EIS capacitance here.'],
    }
    out=_augment_ppt_narrative(narrative,summary)
    text=' '.join(out['conclusions']+out['global_cautions']+out['executive_summary'])
    assert '80/85' in text
    assert 'Quantum capacitance' in text
    assert 'Mott-Schottky' in text
    assert 'PZC' in text


def test_find_advanced_summary_reads_nested_analysis_json(tmp_path):
    from faradaiq.v316_advanced_potential_eis import _find_advanced_summary
    p=tmp_path/'analysis'/'nested'
    p.mkdir(parents=True)
    payload={'potential_steps':85,'model_counts':{'L-Rs-CPE':80}}
    (p/'x__advanced_scientific_summary.json').write_text(__import__('json').dumps(payload),encoding='utf-8')
    out=_find_advanced_summary(tmp_path)
    assert out['potential_steps']==85
    assert out['model_counts']['L-Rs-CPE']==80


def test_v316_prefers_pre_v315_exporter_to_avoid_duplicate_slow_modeling():
    from faradaiq.v316_advanced_potential_eis import _base_exporter_for_v316
    def old_base(*a,**k): return 'base'
    def v315_wrapper(*a,**k): return 'legacy-modeling'
    class Parser:
        export_analysis_ready_outputs=v315_wrapper
        _v315_export_base=old_base
    assert _base_exporter_for_v316(Parser) is old_base


def test_suppress_legacy_potential_eis_story_when_v316_summary_present():
    from faradaiq.v316_advanced_potential_eis import _suppress_legacy_potential_eis_story
    manifest={
        'v316_advanced_eis_summary':{'potential_steps':85},
        'technique_counts':{'POTENTIAL_EIS_MODELING':4,'EIS_VALIDATION':2},
        'discovery':{'techniques':[{'technique_key':'POTENTIAL_EIS_MODELING'},{'technique_key':'EIS_VALIDATION'}]},
        'comparison':{'matrix':[{'technique_key':'POTENTIAL_EIS_MODELING'},{'technique_key':'EIS_VALIDATION'}]},
    }
    out=_suppress_legacy_potential_eis_story(manifest)
    assert 'POTENTIAL_EIS_MODELING' not in out['technique_counts']
    assert all(x['technique_key']!='POTENTIAL_EIS_MODELING' for x in out['discovery']['techniques'])
    assert all(x['technique_key']!='POTENTIAL_EIS_MODELING' for x in out['comparison']['matrix'])


def test_powerpoint_slide_content_gets_dataset_specific_v316_takeaways():
    from faradaiq.v316_advanced_potential_eis import _augment_ppt_narrative
    narrative={'executive_summary':[],'conclusions':[],'global_cautions':[],'technique_discussions':{},'presenter_notes':{},'slide_content':{
        'technique-eis_circuit_model':{},'technique-eis_validation':{},'technique-eis_capacitance':{},'technique-mott_schottky_diagnostic':{},'technique-eis_fit_diagnostic':{},'technique-drt_peak_tracks':{}
    }}
    summary={'potential_steps':85,'model_counts':{'L-Rs-CPE':80,'L-Rs-(R||CPE)':5},'median_Rs_ohm':326.0,'median_L_uH':56.7,'median_inductive_capacitive_crossover_Hz':24300.0,'finite_parallel_R_candidate_count':5,'mott_schottky_status':'no_frequency_passed_strict_Mott_Schottky_checks','pzc_status':'no_frequency_stable_PZC_assignment','quantum_capacitance_claim':'not_reported_without_independent_low_DOS_and_Cdl_context','median_kk_normalized_rmse':0.020,'median_recommended_fit_normalized_rmse':0.087,'conclusion_bullets':['80/85 spectra are best represented by a blocking or near-blocking distributed-capacitance model after identifiability screening.'],'limitation_bullets':[]}
    out=_augment_ppt_narrative(narrative,summary)
    assert '80/85' in out['slide_content']['technique-eis_circuit_model']['takeaway']
    assert '0.020' in out['slide_content']['technique-eis_validation']['takeaway']
    assert 'not frequency-stable' in out['slide_content']['technique-eis_capacitance']['takeaway']
    assert 'do not support' in out['slide_content']['technique-mott_schottky_diagnostic']['takeaway']


def test_suppress_nonplot_drt_log_story_when_peak_tracks_present():
    from faradaiq.v316_advanced_potential_eis import _suppress_legacy_potential_eis_story
    manifest={
        'v316_advanced_eis_summary':{'potential_steps':85},
        'technique_counts':{'DRT_ANALYSIS':1,'DRT_PEAK_TRACKS':1},
        'discovery':{'techniques':[{'technique_key':'DRT_ANALYSIS'},{'technique_key':'DRT_PEAK_TRACKS'}]},
        'comparison':{'matrix':[]},
    }
    out=_suppress_legacy_potential_eis_story(manifest)
    assert 'DRT_ANALYSIS' not in out['technique_counts']
    assert all(x['technique_key']!='DRT_ANALYSIS' for x in out['discovery']['techniques'])


def test_advanced_export_includes_full_complex_capacitance_grid(tmp_path):
    from faradaiq.v316_advanced_potential_eis import _export_advanced
    vs=np.linspace(0.5,-0.35,17); rows=[]
    for i,v in enumerate(vs,1):
        f,z=_spectrum(v)
        for j,(ff,zz) in enumerate(zip(f,z),1):
            rows.append([i,j,v,ff,zz.real,zz.imag])
    ms={
        'array':np.asarray(rows,float),
        'columns':['potential_step_index','frequency_index','potential_V','frequency_Hz','Zreal_ohm','Zimag_ohm'],
        'n_potential_steps':17,
        'n_frequencies':61,
        'title':'synthetic potential EIS',
    }
    class Parser:
        decoded={'mott_schottky':[ms]}
    _export_advanced(Parser(),tmp_path,'demo')
    p=tmp_path/'demo_Advanced_Potential_EIS'/'demo__complex_capacitance_all_points.csv'
    assert p.is_file()
    lines=p.read_text(encoding='utf-8').splitlines()
    assert len(lines)==17*61+1
    assert 'C_storage_F' in lines[0]
    assert 'C_loss_F' in lines[0]


def test_circuit_parameter_trends_include_q_and_area_normalized_effective_capacitance(tmp_path):
    from faradaiq.v316_advanced_potential_eis import _export_advanced
    source=tmp_path/'source.par'
    source.write_text('Working Electrode Area (cm2)=1\nWorking Electrode Type=Unspecified\nReference Electrode=(unspecified) (0 Volts)\n',encoding='utf-8')
    vs=np.linspace(0.5,-0.35,17); rows=[]
    for i,v in enumerate(vs,1):
        f,z=_spectrum(v)
        for j,(ff,zz) in enumerate(zip(f,z),1): rows.append([i,j,v,ff,zz.real,zz.imag])
    ms={'array':np.asarray(rows,float),'columns':['potential_step_index','frequency_index','potential_V','frequency_Hz','Zreal_ohm','Zimag_ohm'],'n_potential_steps':17,'n_frequencies':61,'title':'synthetic potential EIS','source_file':str(source)}
    class Parser: decoded={'mott_schottky':[ms]}
    _export_advanced(Parser(),tmp_path,'demo')
    p=tmp_path/'demo_Advanced_Potential_EIS'/'demo__circuit_parameter_trends.csv'
    header=p.read_text(encoding='utf-8').splitlines()[0]
    assert 'Q1_F_s_n_minus1' in header
    assert 'Ceff1_uF_cm2_if_area' in header


def test_browser_prefers_area_normalized_effective_capacitance_when_available():
    import faradaiq.v290_portable_browser as b
    import faradaiq.v316_advanced_potential_eis as v316
    # Ensure the test exercises the v316 browser patch even in an isolated process.
    if hasattr(b,'_v316_advanced_potential_eis_installed'):
        delattr(b,'_v316_advanced_potential_eis_installed')
    v316.install({'VersaStudioParser':None})
    cols=['potential_V','Rs_ohm','L_H','R1_or_Rp_ohm','Q1_F_s_n_minus1','Ceff1_HsuMansfeld_F','Ceff1_uF_cm2_if_area','n1']
    views=b._plot_views('EIS_CIRCUIT_MODEL',cols,[],path_hint='')
    ceff=next(x for x in views if x['id']=='ceff_vs_potential')
    assert ceff['y_column']=='Ceff1_uF_cm2_if_area'


def test_powerpoint_circuit_notes_do_not_equate_cpe_effective_capacitance_with_cdl():
    from faradaiq.v316_advanced_potential_eis import _augment_ppt_narrative
    narrative={'executive_summary':[],'conclusions':[],'global_cautions':[],'technique_discussions':{},'presenter_notes':{},'slide_content':{'technique-eis_circuit_model':{}}}
    summary={'potential_steps':85,'model_counts':{'L-Rs-CPE':80,'L-Rs-(R||CPE)':5},'finite_parallel_R_candidate_count':5,'mott_schottky_status':'no_frequency_passed_strict_Mott_Schottky_checks','pzc_status':'no_frequency_stable_PZC_assignment','quantum_capacitance_claim':'not_reported_without_independent_low_DOS_and_Cdl_context','conclusion_bullets':[],'limitation_bullets':[]}
    out=_augment_ppt_narrative(narrative,summary)
    notes=out['slide_content']['technique-eis_circuit_model']['speaker_notes']
    assert 'not automatically Cdl' in notes


def test_v316_manifest_normalization_keeps_real_eis_and_drops_drt_input_duplicates():
    from faradaiq.v316_advanced_potential_eis import _normalize_v316_manifest_datasets
    manifest={
        'datasets':[
            {'id':'n','technique_key':'SEMICONDUCTOR_ANALYSIS','technique_label':'Mott-Schottky','physical_path':'x__Mott-Schottky_0.5000_V_Nyquist.csv'},
            {'id':'b','technique_key':'SEMICONDUCTOR_ANALYSIS','technique_label':'Mott-Schottky','physical_path':'x__Mott-Schottky_0.5000_V_Bode_mag.csv'},
            {'id':'d','technique_key':'SEMICONDUCTOR_ANALYSIS','technique_label':'Mott-Schottky','physical_path':'DRT_input_csv/x__Mott-Schottky_0.5000_V__freq_zreal_zimag.csv'},
            {'id':'o','technique_key':'SEMICONDUCTOR_ANALYSIS','technique_label':'Mott-Schottky','physical_path':'x__mott_schottky_analysis_overview.json'},
            {'id':'m','technique_key':'MOTT_SCHOTTKY_DIAGNOSTIC','technique_label':'Mott diag','physical_path':'x__mott_schottky_strict_diagnostics.csv'},
        ],
        'technique_counts':{'SEMICONDUCTOR_ANALYSIS':4,'MOTT_SCHOTTKY_DIAGNOSTIC':1},
    }
    out=_normalize_v316_manifest_datasets(manifest)
    ids={d['id'] for d in out['datasets']}
    assert ids=={'n','b','m'}
    by={d['id']:d for d in out['datasets']}
    assert by['n']['technique_key']=='EIS'
    assert by['b']['technique_key']=='EIS'
    assert out['technique_counts']['EIS']==2
    assert 'SEMICONDUCTOR_ANALYSIS' not in out['technique_counts']


def test_executive_cautions_are_compact_without_losing_full_scientific_cautions():
    from faradaiq.v316_advanced_potential_eis import _augment_ppt_narrative
    narrative={
        'executive_summary':[],
        'conclusions':[],
        'global_cautions':[
            'Automated summaries are evidence-bounded screening and presentation aids; mechanistic conclusions still require expert review.',
            'Equivalent-circuit elements are phenomenological unless supported by independent chemistry and perturbation evidence; a lower-AICc but unidentifiable model is not used for mechanistic parameters.',
            'Quantum capacitance is not separately identifiable from total EIS capacitance without an independent low-DOS electrode model and Cdl/DOS context.',
            'DRT peak positions and amplitudes support time-scale analysis but are not automatically assigned to a specific electrochemical mechanism.',
        ],
        'slide_content':{},
    }
    summary={
        'mott_schottky_status':'no_frequency_passed_strict_Mott_Schottky_checks',
        'pzc_status':'no_frequency_stable_PZC_assignment',
        'quantum_capacitance_claim':'not_reported_without_independent_low_DOS_and_Cdl_context',
        'conclusion_bullets':[],
        'limitation_bullets':[],
    }
    out=_augment_ppt_narrative(narrative,summary)
    # Keep all detailed cautions for notes/audit, but the executive slide must stay readable.
    assert len(out['global_cautions']) >= 6
    assert 1 <= len(out['executive_cautions']) <= 2
    assert max(map(len,out['executive_cautions'])) <= 135
    executive=' '.join(out['executive_cautions'])
    assert 'Mott-Schottky' in executive
    assert 'PZC' in executive
    assert 'Quantum capacitance' in executive


def test_executive_cross_technique_panel_is_compact_for_potential_eis_story():
    from faradaiq.v316_advanced_potential_eis import _augment_ppt_narrative
    narrative={
        'executive_summary':[], 'conclusions':[], 'global_cautions':[], 'slide_content':{},
        'cross_technique_observations':[
            'Cross-technique interpretation is limited to measurements with compatible point, method, and chronology coverage.',
            'The v304 planner uses technique-specific slide recipes and cross-technique spatial registration so coordinate, OCP, corrosion, impedance, spectral, image, and transient evidence can be presented consistently when compatible point mapping exists.',
        ],
    }
    summary={'mott_schottky_status':'candidate_regime_present','pzc_status':'frequency_stable_candidate','conclusion_bullets':[],'limitation_bullets':[]}
    out=_augment_ppt_narrative(narrative,summary)
    assert len(out['cross_technique_observations']) == 2
    assert len(out['executive_cross_technique']) == 1
    assert len(out['executive_cross_technique'][0]) <= 135
    assert 'compatible' in out['executive_cross_technique'][0].lower()
