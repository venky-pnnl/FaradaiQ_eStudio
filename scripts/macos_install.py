#!/usr/bin/env python3
"""Install eStudio in a per-user directory using the selected Python runtime."""
from __future__ import annotations
import argparse,hashlib,json,os,shutil,subprocess,sys,venv
from pathlib import Path

from macos_shortcut import install_launchers
ROOT=Path(__file__).resolve().parents[1]

def verify(root):
    meta=json.loads((root/'release.json').read_text())
    for line in (root/f"PACKAGE_MANIFEST_SHA256_{meta['version']}.txt").read_text().splitlines():
        digest,relative=line.split('  ',1)
        path=(root/relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():raise ValueError('Unsafe or missing payload: '+relative)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Payload checksum mismatch: '+relative)
    return meta

def install(target_root=None,no_launch=False,plan=False):
    if not plan:
        print('Verifying the eStudio package.', flush=True)
    meta=verify(ROOT)
    target=(Path(target_root).expanduser() if target_root else Path.home()/'Applications'/'FaradaIQ eStudio')/f"V{meta['version_number']}"
    shortcut=Path.home()/'Desktop'/'Holaris Apps'/'FaradaIQ eStudio.app'
    if plan:
        print(f"Install {meta['app_name']} {meta['version']} -> {target}\nApplication shortcut -> {shortcut}");return
    if sys.platform!='darwin':raise RuntimeError('Run this installer on macOS. Use the Windows BAT on Windows.')
    if not (3,11)<=sys.version_info[:2]<(3,13):raise RuntimeError('Use Python 3.11 or 3.12 with Tk support (python.org macOS installer).')
    import tkinter
    test=tkinter.Tk();test.withdraw();test.update();test.destroy()
    print(f'Preparing eStudio files in {target}.', flush=True)
    # Validate manifest before copying. Build the venv in its final path.
    if ROOT.resolve()!=target.resolve():
        target.mkdir(parents=True,exist_ok=True)
        for line in (ROOT/f"PACKAGE_MANIFEST_SHA256_{meta['version']}.txt").read_text().splitlines():
            _,relative=line.split('  ',1);destination=target/relative
            destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/relative,destination)
        shutil.copy2(ROOT/f"PACKAGE_MANIFEST_SHA256_{meta['version']}.txt",target)
    verify(target)
    for entry in target.iterdir():
        if entry.suffix in {'.sh','.command'}:entry.chmod(0o755)
    print('Preparing the private eStudio Python environment.', flush=True)
    runtime=target/'.faradaiq_venv_macos';python=runtime/'bin/python'
    if not python.exists():venv.EnvBuilder(with_pip=True).create(runtime)
    subprocess.run([str(python),'-m','pip','install','--upgrade','pip','setuptools','wheel'],check=True)
    print('Installing eStudio Python dependencies. This may take a few minutes.', flush=True)
    subprocess.run([str(python),'-m','pip','install','-r',str(target/'requirements_macos.txt')],check=True)
    print('Checking the installed environment and application imports.', flush=True)
    subprocess.run([str(python),'-c','import tkinter,numpy,pandas,scipy,matplotlib,h5py; import faradaiq_studio_core'],cwd=target,check=True)
    archive=os.environ.get('FARADAIQ_SOURCE_PACKAGE_ZIP')
    if not archive:
        candidate=ROOT.parent/meta['package_asset']
        if candidate.is_file():archive=str(candidate)
        elif not archive:
            candidates=sorted(ROOT.parent.glob(Path(meta['package_asset']).stem+'*.zip'),key=lambda p:p.stat().st_mtime,reverse=True)
            if candidates:archive=str(candidates[0])
    if archive and Path(archive).is_file() and Path(archive).resolve()!=(target/meta['package_asset']).resolve():shutil.copy2(archive,target/meta['package_asset'])
    print('Creating and verifying the FaradaIQ eStudio Finder launchers.', flush=True)
    app=install_launchers(target,shortcut,meta)
    (target/'installation_complete.json').write_text(json.dumps({'platform':'macOS','version':meta['version'],'python':str(python),'shortcut':str(shortcut)},indent=2))
    print(f'Installed {meta["app_name"]} {meta["version"]}\n{app}')
    if not no_launch:subprocess.run(['/usr/bin/open',str(shortcut)],check=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--install-root');parser.add_argument('--no-launch',action='store_true');parser.add_argument('--plan',action='store_true');args=parser.parse_args()
    try:install(args.install_root,args.no_launch,args.plan)
    except Exception as exc:print('Installation failed: '+str(exc),file=sys.stderr);sys.exit(1)
