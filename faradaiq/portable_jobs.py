"""Native macOS parser jobs preserve argument arrays and progress logs."""
import json,sys,subprocess
from pathlib import Path
from .readable_names import new_folder
ROOT=Path(__file__).resolve().parents[1]
def command_script(commands,progress_file=None,notes=None,title='Scientific parser jobs'):
    jobs=[]
    for row in commands:
        args,cwd=row[:2]
        label=str(row[2]) if len(row)>2 else Path(str(args[1] if len(args)>1 else args[0])).name
        jobs.append({'args':[str(a) for a in args],'cwd':str(cwd),'label':label})
    payload={'jobs':jobs,'progress':str(progress_file) if progress_file else None,'title':str(title),'notes':list(notes or [])}
    return '''#!/usr/bin/env python3
import json,sys,subprocess
from pathlib import Path
payload=json.loads('''+repr(json.dumps(payload))+''')
progress=Path(payload['progress']) if payload['progress'] else None
logs=(progress.parent if progress else Path.cwd())/'_faradaiq_parser_logs'
logs.mkdir(parents=True,exist_ok=True)
total=len(payload['jobs']);failed=False
def report(index,state,detail):
    text=f'{index} of {total} {state} {detail}'
    print(text,flush=True)
    if progress:
        progress.parent.mkdir(parents=True,exist_ok=True)
        progress.write_text(text,encoding='utf-8')
print(payload['title'],flush=True)
for note in payload['notes']:print(note,flush=True)
report(0,'STARTING','Preparing parser commands')
for index,job in enumerate(payload['jobs'],1):
    report(index,'RUNNING',job['label'])
    log=logs/f'parser_{index:03d}.log'
    try:
        with log.open('w',encoding='utf-8') as stream:
            stream.write(json.dumps(job,ensure_ascii=False)+'\\n');stream.flush()
            result=subprocess.run(job['args'],cwd=job['cwd'],stdout=stream,stderr=subprocess.STDOUT)
        code=result.returncode
    except Exception as exc:
        log.write_text(str(exc),encoding='utf-8');code=1
    if code:failed=True
    report(index,'FAILED' if code else 'DONE',f'rc={code} {job["label"]} -- see {log}')
report(total,'FINISHED_WITH_ERRORS' if failed else 'FINISHED','Inspect _faradaiq_parser_logs' if failed else 'All parser commands finished')
sys.exit(1 if failed else 0)
'''
def launch_script(text,cwd=None,prefix='parser'):
    if not text.startswith('#!/usr/bin/env python3'):raise RuntimeError('This legacy tool produced Windows batch text. Use the batch analysis workspace or the macOS optional-tool installer for this operation.')
    folder=new_folder(ROOT/'FaradaIQ_parser_jobs',prefix,reserve=48)
    path=folder/'run_parser.py';path.write_text(text,encoding='utf-8');path.chmod(0o755)
    working=Path(cwd or ROOT)
    if not working.is_dir():working=ROOT
    with (folder/'launcher.log').open('ab') as stream:subprocess.Popen([sys.executable,str(path)],cwd=working,stdout=stream,stderr=subprocess.STDOUT)
    return path
def install(core):
    if sys.platform!='darwin':return
    core['_v22_command_batch_text']=lambda commands,title='Scientific parser jobs':command_script(commands,title=title)
    core['_v27_auto_pyseccm_batch_text']=lambda commands,notes=None,title='Scientific parser jobs':command_script(commands,notes=notes,title=title)
    core['_V216_PREV_BATCH_WITH_PROGRESS']=command_script
    core['_v69_make_tools_batch']=command_script
    core['_v22_write_bat_and_launch']=launch_script
    core['_v69_write_and_launch_batch']=launch_script
    core['_v22_pip_install_batch_text']=lambda python_exe=None,pip_packages=None,title='Repair scientific dependencies':command_script([([str(python_exe or sys.executable),'-m','pip','install','-r',str(ROOT/'requirements_macos.txt')],ROOT)],title=title)
