"""Build the complete release without runtime state or nested packages."""
from __future__ import annotations
import argparse,hashlib,json,re,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
EXCLUDED={'.git','.github_local','.faradaiq_venv','.faradaiq_venv_macos','.faradaiq_test_venv','.deareis_venv','__pycache__','.pytest_cache','dist','FaradaIQ_launch_sessions','FaradaIQ_parser_jobs','FaradaIQ_runtime_estimates','FaradaIQ_runtime_settings','FaradaIQ_export_jobs','llm_env_helpers','scilink_home','logs','qa','SciLink_ready','FaradaIQ eStudio.app','.estudio.iconset'}
EXCLUDED_FILES={'github.json','installation_complete.json','current.json','.DS_Store'}
PATTERNS=[re.compile(r'gh[pousr]_[A-Za-z0-9]{30,}'),re.compile(r'github_pat_[A-Za-z0-9_]{40,}'),re.compile(r'AKIA[A-Z0-9]{16}'),re.compile(r'sk-proj-[A-Za-z0-9_-]{40,}'),re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')]

def payload_files():
    result=[]
    for p in sorted(ROOT.rglob('*')):
        rel=p.relative_to(ROOT)
        if not p.is_file() or any(part in EXCLUDED or part.startswith('.deareis_venv') for part in rel.parts):continue
        if rel.parts[0]=='litellm_proxy' and p.name not in {'README_litellm_proxy.txt','requirements_litellm_proxy.txt','start_faradaiq_litellm_proxy.bat'}:continue
        if p.name in EXCLUDED_FILES or p.name.startswith(('.env','analysis_tools_status_v','.analysis-tools-')) or p.suffix.lower() in {'.zip','.whl','.pyc','.log','.pem','.key','.pfx'}:continue
        result.append(p)
    return result

def screen(files):
    failures=[]
    for p in files:
        if p.suffix.lower() not in {'.py','.pyw','.ps1','.bat','.json','.yml','.yaml','.toml','.txt','.md','.ini','.cfg','.sh','.command'}:continue
        content=p.read_text(encoding='utf-8',errors='replace')
        for pattern in PATTERNS:
            if pattern.search(content):failures.append(str(p.relative_to(ROOT)))
    if failures:raise RuntimeError('Possible credentials found; inspect these files before publishing: '+', '.join(sorted(set(failures))))

def build(output=None, *, from_manifest=False):
    meta=json.loads((ROOT/'release.json').read_text())
    manifest=ROOT/f"PACKAGE_MANIFEST_SHA256_{meta['version']}.txt"
    files=[f for f in payload_files() if not f.name.startswith('PACKAGE_MANIFEST_SHA256_')]
    if from_manifest:
        available = {path.relative_to(ROOT).as_posix(): path for path in files}
        listed = [line.split('  ', 1)[1] for line in manifest.read_text(encoding='utf-8').splitlines()]
        missing = sorted(set(listed) - available.keys())
        if missing:
            raise RuntimeError('Current package is missing files. Extract the full ZIP again: ' + ', '.join(missing))
        files = [available[name] for name in listed]
    screen(files)
    manifest.write_text(''.join(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+f.relative_to(ROOT).as_posix()+'\n' for f in files),encoding='utf-8')
    dest=Path(output) if output else ROOT/'dist'/meta['package_asset']
    dest.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        # Installer and update BAT files appear first at the root of the ZIP.
        for f in sorted(files+[manifest],key=lambda f:(f.suffix!='.bat',str(f))):
            rel=f.relative_to(ROOT).as_posix()
            info=zipfile.ZipInfo(rel,date_time=(2026,11,18,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=((0o100755 if f.suffix in {'.sh','.command'} else 0o100644)<<16)
            z.writestr(info,f.read_bytes())
    digest=hashlib.sha256(dest.read_bytes()).hexdigest()
    dest.with_name(dest.name+'.sha256').write_text(digest+'  '+dest.name+'\n',encoding='ascii')
    print(f'{dest}\nSHA256 {digest}\n{len(files)+1} files; {dest.stat().st_size:,} bytes')
    return dest

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--scan-only',action='store_true');parser.add_argument('--output');parser.add_argument('--list-files',action='store_true');parser.add_argument('--from-manifest',action='store_true',help='Use the existing release manifest as the payload inventory.')
    args=parser.parse_args()
    if args.list_files:
        files=payload_files();screen(files)
        print('\n'.join(f.relative_to(ROOT).as_posix() for f in files))
    elif args.scan_only:screen(payload_files());print('Source credential-pattern screening: PASS')
    else:build(args.output, from_manifest=args.from_manifest)
