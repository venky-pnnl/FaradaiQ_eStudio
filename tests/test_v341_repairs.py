from pathlib import Path
from datetime import datetime
import json,threading,subprocess,sys
import pytest,h5py
from faradaiq.readable_names import safe_component,new_folder,path_component
from faradaiq.scilink_bundle import make_scilink_ready_bundle,column_unit
ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('raw,want',[('CON','_CON'),('nul.csv','_nul.csv'),('COM1.txt','_COM1.txt'),('Project: Ni/Co?  .','Project_ Ni_Co_'),('Dy(NO₃)₃ μM','Dy(NO₃)₃ μM')])
def test_names(raw,want):assert safe_component(raw)==want

def test_readable_collision_and_long_name(tmp_path):
    label='CDOC_NiCo_transport_100mM';now=datetime(2026,11,17,9,30,45)
    first=new_folder(tmp_path,label,now=now);second=new_folder(tmp_path,label,now=now)
    assert first.name==label+'_2026-11-17_093045' and second.name==first.name+'_02'
    assert json.loads((first/'name_mapping.json').read_text())['original_label']==label
    name='ProjectNiCo_'+'measurement_'*15+'_sample17_cycle02.csv'
    out=path_component(tmp_path,name,reserve=50,max_chars=100)
    assert out.startswith('ProjectNiCo_') and out.endswith('_sample17_cycle02.csv') and len(out)<=100
    with pytest.raises(ValueError):path_component(tmp_path/('a'*210),'sample')
@pytest.mark.parametrize('label,want',[('potential_mV','mV'),('Ewe/V','V'),('I [mA]','mA'),('Zreal/Ohm','ohm'),('scan_rate/V/s','V/s'),('signal',None)])
def test_units(label,want):assert column_unit(label)==want

def fixture(root):
    source=root/'CV'/'point_01'/'cycle_01.csv';source.parent.mkdir(parents=True)
    source.write_text('potential_mV,current_mA,time_s\n100,2,0\n200,3,1\n')
    (root/'run_summary.json').write_text('{"electrolyte":"0.1 M NiCl2"}')
    return source

def test_handoff(tmp_path):
    source=fixture(tmp_path);original=source.read_bytes()
    receipt=make_scilink_ready_bundle(tmp_path,metadata={'session_id':'S12','sample':'NiCo'})
    assert source.read_bytes()==original and (tmp_path/'run_summary.json').exists()
    manifest=json.loads(Path(receipt['json_path']).read_text());ready=manifest['scilink_ready']
    assert not ready['approved'] and ready['analysis_only'] and ready['experimental_metadata']['sample']=='NiCo'
    with h5py.File(receipt['h5_path']) as h:
        ds=next(iter(h['tables'].values()));assert list(ds[0])==[100.,2.,0.]
        units=json.loads(ds.attrs['units_json']);assert units['potential_mV']=='mV' and units['current_mA']=='mA'
        assert json.loads(h['manifest_json'][()].decode())['scilink_ready']==ready
        assert any(bytes(ds[:])==original for ds in h['source_documents'].values())
        assert h.attrs['n_metadata']==len(h['metadata'])
    assert Path(receipt['skill_path']).read_bytes()==(ROOT/'skills/electrochemistry_skill.md').read_bytes()
    assert Path(receipt['request_path']).exists()

def test_parent_metadata(tmp_path):
    root=tmp_path/'sample_analysis_ready_csv';root.mkdir();fixture(root)
    outside=tmp_path/'sample_summary.json';outside.write_text('{"temperature_C":23}')
    receipt=make_scilink_ready_bundle(root)
    assert outside.exists() and 'temperature_C' in Path(receipt['json_path']).read_text()

def test_source_changes_rejected(tmp_path,monkeypatch):
    import faradaiq_canonical_bundle as c
    source=fixture(tmp_path);original=c.collect_records
    def collect(*args,**kwargs):
        result=original(*args,**kwargs);source.write_text(source.read_text()+'300,4,2\n');return result
    monkeypatch.setattr(c,'collect_records',collect)
    with pytest.raises(RuntimeError,match='changed'):make_scilink_ready_bundle(tmp_path)
    assert not list((tmp_path/'SciLink_ready').glob('*'))

def test_cancel(tmp_path):
    source=fixture(tmp_path);event=threading.Event()
    with pytest.raises(InterruptedError):make_scilink_ready_bundle(tmp_path,cancel_event=event,progress_callback=lambda msg:event.set())
    assert source.exists() and not list((tmp_path/'SciLink_ready').glob('*'))

def test_batch_folder_and_old_access(tmp_path):
    from faradaiq.v233_batch_jobs import BatchJob
    job=BatchJob(source_path='NiCo.csv',experiment_name='CDOC: NiCo transport',output_dir=str(tmp_path))
    folder=job.session_dir
    assert folder.name.startswith('CDOC_ NiCo transport_') and job.job_id not in folder.name
    assert BatchJob.from_dict(job.to_dict()).session_dir==folder
    old=BatchJob(source_path='old.csv',experiment_name='old',output_dir=str(tmp_path),job_id='FQJOB_old')
    legacy=tmp_path/'FaradaIQ_job_sessions'/'FQJOB_old';legacy.mkdir()
    assert old.session_dir==legacy

def test_brand_and_mac_syntax():
    from faradaiq.estudio_ui import brand_text,brand_window_title
    assert brand_text('FaradaIQ Studio v320')=='FaradaIQ eStudio'
    assert brand_window_title('FaradaIQ Studio v320 — Data')=='FaradaIQ eStudio v341 — Data'
    assert (ROOT/'assets/estudio.png').read_bytes()==(ROOT/'faradaiq_clean_icon.png').read_bytes()
    for p in list(ROOT.glob('*.sh'))+list(ROOT.glob('*.command')):subprocess.run(['bash','-n',str(p)],check=True)
    for filename in ['install.ps1','verify.ps1']:
        text=(ROOT/'scripts'/filename).read_text()
        assert "[string]$Root = ''" in text and '(Split-Path $PSScriptRoot -Parent)' not in text

def test_portable_parser(tmp_path):
    from faradaiq.portable_jobs import command_script
    output=tmp_path/'result with spaces.txt';progress=tmp_path/'progress.txt'
    code='import sys;from pathlib import Path;Path(sys.argv[1]).write_text(sys.argv[2])'
    argument='Ni/Co: 100mM "sample" $HOME'
    script=tmp_path/'driver.py';script.write_text(command_script([([sys.executable,'-c',code,str(output),argument],tmp_path,'CV analysis')],progress))
    result=subprocess.run([sys.executable,str(script)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert output.read_text()==argument and 'FINISHED All parser commands finished' in progress.read_text()
    script.write_text(command_script([([sys.executable,'-c','raise SystemExit(7)'],tmp_path,'bad fit')],progress))
    result=subprocess.run([sys.executable,str(script)],capture_output=True,text=True)
    assert result.returncode==1 and 'FINISHED_WITH_ERRORS' in progress.read_text()


def test_browser_forget_uses_only_active_geometry_manager():
    from faradaiq.simple_browser_v302 import _forget
    class Widget:
        def __init__(self,manager):self.manager=manager;self.calls=[]
        def winfo_manager(self):return self.manager
        def pack_forget(self):self.calls.append('pack')
        def grid_remove(self):self.calls.append('grid')
        def place_forget(self):self.calls.append('place')
    for manager in ['pack','grid','place','']:
        widget=Widget(manager);_forget(widget)
        assert widget.calls==([manager] if manager else [])
