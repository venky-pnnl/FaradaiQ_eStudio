from __future__ import annotations
from pathlib import Path
from pptx import Presentation


def _manifest_with_takedata():
    rows=[]
    raw=[]
    for i in range(5):
        row={
            'point_index':i+1,
            'x_position_um':float(i),
            'y_position_um':0.5*float(i),
            'z_position_um':0.1*float(i),
            'scan_distance_um':(i*(1.0**2+0.5**2)**0.5),
            'step_distance_um':0.0 if i==0 else 1.118,
            'delta_x_um':0.0 if i==0 else 1.0,
            'delta_y_um':0.0 if i==0 else 0.5,
        }
        rows.append(row)
        raw.append({**row,'record_index':i+1,'elapsed_from_start_s':float(i),'emon_V':-0.7+0.02*i,'imon_A':1e-10*(i+1)})
    datasets=[{'id':'td1','display_name':'Takedata QA','technique_key':'POSITION','technique_label':'Position / Takedata','family':'Spatial','data_kind':'raw','ui_role':'measurement','method_label':'Method 01','point':1,'columns':['point_index','x_position_um'],'preview':{'status':'plot_ready','rows':[[1,0],[2,1]],'columns':['point_index','x_position_um']},'plot_views':[{'id':'x_position','label':'X position','x_index':0,'y_index':1,'x_column':'point_index','y_column':'x_position_um','x_transform':'identity','y_transform':'identity','x_scale':'linear','y_scale':'linear','plot_type':'curve'}]}]
    matrix=[{'technique_key':'POSITION','technique_label':'Position / Takedata','family':'Spatial','view_id':'x_position','view_label':'X position','plot_type':'curve','x_column':'point_index','y_column':'x_position_um','dataset_ids':['td1'],'dataset_count':1,'unique_points':1,'point_min':1,'point_max':1,'methods':['Method 01'],'score':80}]
    techs=[{'technique_key':'POSITION','technique_label':'Position / Takedata','family':'Spatial','dataset_count':5,'point_count':5,'method_count':1,'analysis_count':0}]
    return {
        'schema':'faradaiq-portable-browser-v304','version':'v316','experiment_name':'Generic recipe QA',
        'datasets':datasets,'comparison':{'matrix':matrix,'recommended':matrix},
        'discovery':{'point_count':5,'measurement_count':5,'analysis_count':0,'media_count':0,'method_count':1,'techniques':techs,'recommended_views':[],'trend_cards':[],'unusual_points':[],'analysis_cards':[]},
        'primary_downloads':{},'short_names':{'enabled':False},
        'electrochemistry_results':{
            'ocp':[],'corrosion':[],
            'takedata':[{'rows':rows,'raw_rows':raw,'row_count':len(rows),'raw_record_count':len(raw),'default_axis':'Distance','diagnostics':{'dominant_axis':'Distance'},'coordinate_quality':{'label':'measured stage coordinates'},'warnings':[],
                'metrics':[{'id':'x_position_um','label':'X position','unit':'µm'},{'id':'y_position_um','label':'Y position','unit':'µm'},{'id':'z_position_um','label':'Z position','unit':'µm'},{'id':'step_distance_um','label':'Step distance','unit':'µm'}]}]
        }
    }


def test_v311_consolidates_takedata_without_relationship_debug_slides(tmp_path: Path):
    from faradaiq.v290_powerpoint_summary import write_powerpoint_summary
    r=write_powerpoint_summary(tmp_path,manifest=_manifest_with_takedata(),use_direct_llm=False,detail_level='comprehensive')
    from faradaiq import release_identity
    assert r['version']==release_identity.APP_VERSION
    assert r['presentation_theme']=='scientific_consolidated_light_v311'
    assert r['technique_specific_slide_recipes'] is True
    assert r['takedata_relationship_engine'] is False
    assert r['consolidated_results_per_technique'] is True
    assert r['professional_deck_mode'] is True
    assert r['presentation_plan']['professional_deck_mode'] is True
    prs=Presentation(r['pptx_path'])
    text='\n'.join(sh.text for sl in prs.slides for sh in sl.shapes if getattr(sh,'has_text_frame',False))
    assert 'Takedata coordinate geometry relationships' not in text
    assert 'Takedata electrochemical context relationships' not in text
    assert 'consolidated result' in text.lower()
    assert r['slide_count'] <= 6
    notes='\n'.join(sl.notes_slide.notes_text_frame.text for sl in prs.slides)
    assert 'Open with the slide title' not in notes
