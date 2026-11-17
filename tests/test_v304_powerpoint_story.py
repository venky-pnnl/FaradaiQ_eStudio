from __future__ import annotations
from pathlib import Path
from pptx import Presentation


def _dataset(did,tech,point,view_id,label,x,y,rows):
    return {
        'id':did,'display_name':did,'technique_key':tech,'technique_label':tech,
        'family':'QA','data_kind':'raw','ui_role':'measurement','method_label':'Method 01',
        'point':point,'cycle':1,'sweep':1,'columns':[x,y],
        'preview':{'status':'plot_ready','rows':rows,'columns':[x,y]},
        'plot_views':[{'id':view_id,'label':label,'x_index':0,'y_index':1,'x_column':x,'y_column':y,'x_transform':'identity','y_transform':'identity','x_scale':'linear','y_scale':'linear','plot_type':'curve'}],
        'is_media':False,
    }


def _manifest():
    datasets=[]; matrix=[]; techs=[]
    specs=[
        ('OCP','time_potential','Time vs potential','time_s','potential',[[-1,-.8],[0,-.7],[10,-.6],[20,-.55]]),
        ('OCP','sqrt_time_potential','t¹ᐟ² vs potential','time_s','potential',[[-1,-.8],[0,-.7],[10,-.6],[20,-.55]]),
        ('OCP','metric_potential','potential vs point_index','point_index','potential',[[1,-.8],[1,-.7],[1,-.6]]),
        ('TAFEL','tafel_current','Tafel: potential vs log10|I|','potential_V','current_A',[[-.8,-9],[-.7,-10],[-.6,-9]]),
        ('APPROACH','distance_current','Distance vs current','distance_m','current',[[0,1e-9],[1e-6,2e-9],[2e-6,3e-9]]),
    ]
    for tech,vid,label,x,y,rows in specs:
        ids=[]
        for p in (1,2):
            did=f'{tech}_{vid}_{p}'; datasets.append(_dataset(did,tech,p,vid,label,x,y,rows)); ids.append(did)
        matrix.append({'technique_key':tech,'technique_label':tech,'family':'QA','view_id':vid,'view_label':label,'plot_type':'curve','x_column':x,'y_column':y,'dataset_ids':ids,'dataset_count':2,'unique_points':2,'point_min':1,'point_max':2,'methods':['Method 01'],'description':'QA scientific comparison.','score':100 if vid in {'time_potential','tafel_current','distance_current'} else 60})
    for tech in ('OCP','TAFEL','APPROACH'):
        techs.append({'technique_key':tech,'technique_label':tech,'family':'QA','dataset_count':2,'point_count':2,'method_count':1,'analysis_count':0})
    return {'schema':'faradaiq-portable-browser-v304','version':'v316','experiment_name':'Generic QA experiment','datasets':datasets,'comparison':{'matrix':matrix,'recommended':matrix[:3]},'discovery':{'point_count':2,'measurement_count':6,'analysis_count':0,'media_count':0,'method_count':1,'techniques':techs,'recommended_views':matrix[:3],'trend_cards':[],'unusual_points':[],'analysis_cards':[]},'primary_downloads':{},'short_names':{'enabled':False},'electrochemistry_results':{'ocp':[],'corrosion':[],'takedata':[]}}


def test_story_planner_suppresses_semantically_weak_views(tmp_path: Path):
    from faradaiq.v290_powerpoint_summary import write_powerpoint_summary
    r=write_powerpoint_summary(tmp_path,manifest=_manifest(),use_direct_llm=False,detail_level='standard')
    prs=Presentation(r['pptx_path'])
    all_text='\n'.join(sh.text for sl in prs.slides for sh in sl.shapes if getattr(sh,'has_text_frame',False))
    assert 't¹ᐟ² vs potential' not in all_text
    assert 'potential vs point_index' not in all_text
    assert 'OCP: consolidated result' in all_text
    assert 'TAFEL: consolidated result' in all_text
    assert 'APPROACH: consolidated result' in all_text
    assert r['scientific_story_planner'] is True
    assert r['suppressed_semantically_invalid_views'] >= 2
    assert r['presentation_theme']=='scientific_consolidated_light_v311'
    assert r['consolidated_results_per_technique'] is True
    assert r['professional_deck_mode'] is True


def test_speaker_notes_are_paragraphs_not_generic_instructions(tmp_path: Path):
    from faradaiq.v290_powerpoint_summary import write_powerpoint_summary
    r=write_powerpoint_summary(tmp_path,manifest=_manifest(),use_direct_llm=False,detail_level='compact')
    prs=Presentation(r['pptx_path'])
    for sl in prs.slides:
        text=sl.notes_slide.notes_text_frame.text
        assert 'Scientific discussion' in text
        assert 'Open with the slide title' not in text
        assert len(text) >= 180
    assert r['speaker_notes_style'].startswith('scientific_paragraphs')


def test_standard_is_main_story_with_small_appendix(tmp_path: Path):
    from faradaiq.v290_powerpoint_summary import write_powerpoint_summary
    r=write_powerpoint_summary(tmp_path,manifest=_manifest(),use_direct_llm=False,detail_level='standard')
    assert r['main_slide_count'] < 15
    assert r['appendix_slide_count'] <= 3
    assert r['slide_count']==r['main_slide_count']+r['appendix_slide_count']

def test_llm_slide_specific_notes_are_used(tmp_path: Path, monkeypatch):
    import faradaiq.v290_powerpoint_summary as pptmod
    def fake(prompt):
        return ({
            'executive_summary':['Evidence-grounded LLM summary.'],
            'cross_technique_observations':['Compatible measurements show a testable relationship.'],
            'conclusions':['LLM conclusion constrained to supplied evidence.'],
            'global_cautions':['Do not overinterpret.'],
            'slide_content':{
                'technique-ocp':{
                    'takeaway':'The OCP traces relax toward less negative potential while remaining separated.',
                    'speaker_notes':'The OCP traces show a shared relaxation direction over the recorded interval, while the two measurements remain offset. This is a direct observation from the plotted data rather than a mechanistic claim.\n\nBecause the traces continue to evolve, the presenter should distinguish operational late-time behavior from proof of equilibrium and use any available tail-drift metric to support a stronger stability statement.'
                }
            },
            'technique_discussions':{}
        }, {'status':'completed','provider':{'name':'QA'}})
    monkeypatch.setattr(pptmod,'_call_direct_llm',fake)
    r=pptmod.write_powerpoint_summary(tmp_path,manifest=_manifest(),use_direct_llm=True,detail_level='compact')
    prs=Presentation(r['pptx_path'])
    notes='\n'.join(sl.notes_slide.notes_text_frame.text for sl in prs.slides)
    assert 'shared relaxation direction' in notes
    assert r['direct_llm_used'] is True
