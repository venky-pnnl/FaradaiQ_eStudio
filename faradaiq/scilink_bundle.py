"""Reviewable HDF5 handoff with unchanged sources and explicit provenance."""
from __future__ import annotations
import json,hashlib,re,shutil
from datetime import datetime,timezone
from pathlib import Path
from .readable_names import new_folder

_UNITS={'V','mV','A','mA','uA','µA','μA','nA','s','ms','Hz','kHz','MHz','ohm','Ohm','Ω','kohm','kOhm','kΩ','MΩ','C','F','mF','uF','µF','K','deg','°','V/s','mV/s','A/cm2','mA/cm2'}
def column_unit(name):
    name=str(name)
    choices=[]
    if name.endswith((')',']')):choices+=re.findall(r'[\[(]([^\])]+)[\])]',name)
    choices+=[name.split('_')[-1]]
    choices+=[name.split('/',i)[-1] for i in range(1,len(name.split('/'))) ]
    for unit in choices:
        if unit in _UNITS:return 'ohm' if unit=='Ohm' else unit
    return None

def _check(event):
    if event is not None and event.is_set():raise InterruptedError('Bundle creation canceled.')
def _snapshot(path,event):
    before=path.stat();h=hashlib.sha256()
    with path.open('rb') as f:
        while True:
            _check(event);block=f.read(8*1024*1024)
            if not block:break
            h.update(block)
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise RuntimeError(f'Source changed while reading: {path}')
    return {'path':str(path),'name':path.name,'size':after.st_size,'mtime_ns':after.st_mtime_ns,'sha256':h.hexdigest()}

def make_scilink_ready_bundle(analysis_root,sample_name=None,metadata=None,progress_callback=None,cancel_event=None):
    import h5py
    import faradaiq_canonical_bundle as canonical
    metadata=canonical.safe_json(metadata or {})
    root=Path(analysis_root).resolve()
    if not root.is_dir():raise ValueError('Choose an exported analysis folder.')
    data_root,container,base,_=canonical.resolve_bundle_locations(root)
    sample_name=sample_name or base or root.name
    output=new_folder(root/'SciLink_ready',sample_name,reserve=64)
    try:
        files=[]
        allowed=canonical.TABLE_EXTS|canonical.MEDIA_EXTS|canonical.JSON_EXTS
        for p in data_root.rglob('*'):
            _check(cancel_event)
            if not p.is_file() or p.suffix.lower() not in allowed:continue
            if any(part.lower() in canonical.EXCLUDED_DIR_TOKENS for part in p.relative_to(data_root).parts):continue
            if '_faradaiq_bundle.' in p.name:continue
            files.append(p)
        if container!=data_root:files += [p for p in container.glob('*.json') if '_faradaiq_bundle.' not in p.name]
        snapshots={str(p):_snapshot(p,cancel_event) for p in sorted(set(files))}
        if progress_callback:progress_callback('Source snapshot recorded. Building HDF5…')
        receipt=canonical.build_canonical_bundle(root,base_name=base,gui_metadata=metadata or {},progress_callback=progress_callback,label='SciLink-ready',output_dir=output,preserve_sources=True,preserve_input_units=True,cancel_event=cancel_event,bundle_file_stem="experiment")
        for p,original in snapshots.items():
            if _snapshot(Path(p),cancel_event)!=original:raise RuntimeError(f'Source changed during bundle creation: {p}')
        skill=Path(__file__).resolve().parents[1]/'skills/electrochemistry_skill.md'
        skill_copy=output/skill.name;shutil.copy2(skill,skill_copy)
        ready={'schema':'faradaiq-scilink-ready-v1','created_utc':datetime.now(timezone.utc).isoformat(),'application':'FaradaIQ eStudio','version':'v331','sample_name':sample_name,'session_id':(metadata or {}).get('session_id',''),'experimental_metadata':metadata or {},'approved':False,'analysis_only':True,'submission':'not_submitted','data_format':'HDF5','source_files':list(snapshots.values()),'datasets':[],'analysis_skill':{'file':skill.name,'sha256':hashlib.sha256(skill.read_bytes()).hexdigest()},'metadata_policy':'Original column labels and values retained; explicit units only, unknown units null; source snapshots verified.'}
        with h5py.File(receipt['h5_path'],'r+') as h5:
            if not len(h5['tables']):raise ValueError('No curated numeric tables found. Export or select an analysis-ready data folder first.')
            for group in ('tables','media','metadata'):
                for name,ds in h5[group].items():
                    _check(cancel_event)
                    source=str(ds.attrs.get('source_path',''))
                    if source and source not in snapshots:raise RuntimeError(f'An input appeared after the source snapshot: {source}')
                    columns=json.loads(ds.attrs.get('columns_json','[]'))
                    units={col:column_unit(col) for col in columns}
                    ds.attrs['units_json']=json.dumps(units,ensure_ascii=False)
                    ready['datasets'].append({'path':ds.name,'shape':list(ds.shape),'columns':columns,'units':units,'technique':str(ds.attrs.get('technique','')),'source_path':source})
            originals=h5.create_group('source_documents')
            used={item['source_path'] for item in ready['datasets'] if item['path'].startswith('/tables/')}
            used.update(path for path in snapshots if Path(path).suffix.lower() in canonical.JSON_EXTS)
            for index,path in enumerate(sorted(used),1):
                _check(cancel_event)
                original=snapshots[path];document=Path(path)
                ds=originals.create_dataset(f'source_{index:05d}',shape=(original['size'],),dtype='uint8',compression='gzip',compression_opts=4)
                digest=hashlib.sha256();offset=0
                with document.open('rb') as stream:
                    while True:
                        _check(cancel_event)
                        raw=stream.read(8*1024*1024)
                        if not raw:break
                        if offset+len(raw)>original['size']:raise RuntimeError(f'Source changed during evidence copy: {path}')
                        import numpy as np
                        ds[offset:offset+len(raw)]=np.frombuffer(raw,dtype='uint8')
                        digest.update(raw);offset+=len(raw)
                if offset!=original['size'] or digest.hexdigest()!=original['sha256']:raise RuntimeError(f'Source changed during evidence copy: {path}')
                ds.attrs['source_path']=path;ds.attrs['filename']=document.name;ds.attrs['sha256']=original['sha256']
                ds.attrs['role']='exact original table or metadata bytes'
            ready['source_documents_group']='/source_documents'
            manifest=json.loads(h5['manifest_json'][()].decode())
            manifest.update(app='FaradaIQ eStudio',version='v331',scilink_ready=ready)
            manifest['h5_counts']['metadata']=len(h5['metadata'])+1
            manifest['metadata_count']=len(h5['metadata'])+1
            h5.attrs['n_metadata']=len(h5['metadata'])+1
            for key,value in {'app':'FaradaIQ eStudio','version':'v331','bundle_schema':ready['schema'],'sample_name':sample_name,'session_id':ready['session_id']}.items():h5.attrs[key]=value
            del h5['manifest_json'];h5.create_dataset('manifest_json',data=json.dumps(manifest,ensure_ascii=False),dtype=h5py.string_dtype())
            h5['metadata'].create_dataset('scilink_ready_json',data=json.dumps(ready,ensure_ascii=False),dtype=h5py.string_dtype())
        Path(receipt['json_path']).write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
        request=output/'scilink_request.json'
        request.write_text(json.dumps({'data_paths':[Path(receipt['h5_path']).name],'metadata_paths':[Path(receipt['json_path']).name],'skills':[skill.name],'prompt':'','approved':False,'analysis_only':True},indent=2),encoding='utf-8')
        _check(cancel_event)
        return dict(receipt,request_path=str(request),skill_path=str(skill_copy),ready=ready)
    except BaseException:
        shutil.rmtree(output,ignore_errors=True)
        raise
