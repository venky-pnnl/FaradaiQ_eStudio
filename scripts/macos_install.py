#!/usr/bin/env python3
"""Install eStudio in a per-user directory using the selected Python runtime."""
from __future__ import annotations
import argparse,hashlib,json,os,shutil,subprocess,sys,venv
from pathlib import Path

from macos_shortcut import install_application_launchers, install_scilink_launcher
ROOT=Path(__file__).resolve().parents[1]

def verify(root):
    meta=json.loads((root/'release.json').read_text())
    for line in (root/f"PACKAGE_MANIFEST_SHA256_{meta['version']}.txt").read_text().splitlines():
        digest,relative=line.split('  ',1)
        path=(root/relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():raise ValueError('Unsafe or missing payload: '+relative)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Payload checksum mismatch: '+relative)
    return meta

def scilink_root():
    configured = os.environ.get('FARADAIQ_SCILINK_ROOT')
    return Path(configured).expanduser().resolve() if configured else Path.home() / 'Applications' / 'FaradaIQ SciLink'


def prepare_scilink(python, target):
    root = scilink_root()
    print(f'[3/3] Checking for a verified SciLink update in {root}.', flush=True)
    command = [str(python), '-m', 'faradaiq.managed_scilink']
    environment = {**os.environ, 'FARADAIQ_SCILINK_ROOT': str(root)}
    try:
        result = subprocess.run([*command, 'update'], cwd=target, env=environment)
        if result.returncode:
            print('SciLink update failed. A usable previous installation will be retained.', file=sys.stderr, flush=True)
    except OSError as error:
        print(f'SciLink setup could not run: {error}', file=sys.stderr, flush=True)
    if all((root / name).is_file() for name in ('bootstrap/bin/python', 'scilink_launch.py', 'active.json')):
        try:
            status = subprocess.run([*command, 'status'], cwd=target, env=environment,
                                    check=True, capture_output=True, text=True)
            if isinstance(json.loads(status.stdout), dict):
                return root
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            print(f'Could not verify the SciLink runtime: {error}', file=sys.stderr, flush=True)
    print('No verified managed SciLink installation is available. Run this installer again to retry SciLink setup.',
          file=sys.stderr, flush=True)
    return None


def check_core_runtime(python, target, version):
    subprocess.run([str(python), '-c', 'import tkinter,numpy,pandas,scipy,matplotlib,h5py; import faradaiq_studio_core'],
                   cwd=target, check=True)
    subprocess.run([str(python), str(target / f'faradaiq_browser_{version}.py'), '--check'],
                   cwd=target, check=True)


def prepare_analysis_tools(python, target):
    print('[2/3] Checking DearEIS and pyDRTtools.', flush=True)
    try:
        result = subprocess.run([str(python), str(target / 'scripts/setup_analysis_tools.py')], cwd=target)
    except OSError as error:
        print(f'Optional analysis setup could not run: {error}', file=sys.stderr, flush=True)
        return None
    if result.returncode:
        print('One or more optional analysis tools need attention. See the setup messages above.',
              file=sys.stderr, flush=True)
    return result.returncode


def install(target_root=None,no_launch=False,plan=False):
    if not plan:
        print('Verifying the eStudio package.', flush=True)
    meta=verify(ROOT)
    install_root=Path(target_root).expanduser().resolve() if target_root else Path.home()/'Applications'/'FaradaIQ eStudio'
    target=install_root/f"V{meta['version_number']}"
    shortcut=Path.home()/'Desktop'/'Holaris Apps'/'FaradaIQ eStudio.app'
    browser_shortcut=shortcut.with_name('FaradaIQ eStudio Browser.app')
    scilink_shortcut=shortcut.with_name('SciLink.app')
    if plan:
        print(f"Install {meta['app_name']} {meta['version']} -> {target}\n"
              'Setup order: eStudio core, DearEIS and pyDRTtools, managed SciLink, shortcuts and launch.\n'
              f"Desktop shortcut -> {shortcut}\nBrowser shortcut -> {browser_shortcut}\n"
              f"Managed SciLink -> {scilink_root()}\nSciLink shortcut -> {scilink_shortcut}")
        return
    if sys.platform!='darwin':raise RuntimeError('Run this installer on macOS. Use the Windows BAT on Windows.')
    if sys.version_info[:2]!=(3,12):raise RuntimeError('Use Python 3.12 with Tk support (python.org macOS installer).')
    import tkinter
    test=tkinter.Tk();test.withdraw();test.update();test.destroy()
    print(f'Preparing eStudio files in {target}.', flush=True)
    # Validate manifest before copying. Build the venv in its final path.
    manifest=ROOT/f"PACKAGE_MANIFEST_SHA256_{meta['version']}.txt"
    payload=[line.split('  ',1)[1] for line in manifest.read_text().splitlines()]
    if ROOT.resolve()!=target.resolve():
        target.mkdir(parents=True,exist_ok=True)
        for relative in payload:
            destination=target/relative
            destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/relative,destination)
        shutil.copy2(manifest,target)
    verify(target)
    for relative in payload:
        entry=target/relative
        if entry.suffix in {'.sh','.command'}:entry.chmod(0o755)
    print('[1/3] Checking the private eStudio runtime and core dependencies.', flush=True)
    runtime=target/'.faradaiq_venv_macos';python=runtime/'bin/python'
    marker = target / 'installation_complete.json'
    core_ready = False
    if marker.is_file() and python.exists():
        try:
            check_core_runtime(python, target, meta['version'])
            core_ready = True
        except (OSError, subprocess.CalledProcessError):
            print('Repairing the existing private eStudio environment.', flush=True)
    if not core_ready:
        if not python.exists():
            venv.EnvBuilder(with_pip=True).create(runtime)
        print('Installing eStudio Python dependencies. This may take a few minutes.', flush=True)
        subprocess.run([str(python),'-m','pip','install','--upgrade','pip','setuptools','wheel'],check=True)
        subprocess.run([str(python),'-m','pip','install','-r',str(target/'requirements_macos.txt')],check=True)
        check_core_runtime(python, target, meta['version'])
    archive=os.environ.get('FARADAIQ_SOURCE_PACKAGE_ZIP')
    if not archive:
        candidate=ROOT.parent/meta['package_asset']
        if candidate.is_file():archive=str(candidate)
        elif not archive:
            candidates=sorted(ROOT.parent.glob(Path(meta['package_asset']).stem+'*.zip'),key=lambda p:p.stat().st_mtime,reverse=True)
            if candidates:archive=str(candidates[0])
    if archive and Path(archive).is_file() and Path(archive).resolve()!=(target/meta['package_asset']).resolve():shutil.copy2(archive,target/meta['package_asset'])
    analysis_exit = prepare_analysis_tools(python, target)
    managed_scilink = prepare_scilink(python, target)
    print('Creating and verifying the FaradaIQ eStudio Finder launchers.', flush=True)
    app,browser_app=install_application_launchers(target,shortcut,meta)
    if managed_scilink is not None:
        try:
            scilink_app = install_scilink_launcher(managed_scilink, scilink_shortcut, meta, target / 'assets/scilink.icns')
        except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
            print(f'SciLink is available at {managed_scilink}, but its Finder shortcut could not be refreshed: {error}',
                  file=sys.stderr, flush=True)
            scilink_app = None
    else:
        scilink_app = None
    analysis_report = target / f"analysis_tools_status_{meta['version']}.json"
    marker.write_text(json.dumps({
        'platform': 'macOS', 'version': meta['version'], 'python': str(python),
        'shortcut': str(shortcut), 'browser_shortcut': str(browser_shortcut),
        'analysis_tools_exit_code': analysis_exit,
        'analysis_tools_report': str(analysis_report) if analysis_report.is_file() else None,
        'scilink_root': str(managed_scilink) if managed_scilink else None,
    }, indent=2))
    print(f'Installed {meta["app_name"]} {meta["version"]}\nDesktop: {app}\nBrowser: {browser_app}')
    if scilink_app is not None:
        print(f'SciLink: {scilink_app}')
    if not no_launch:
        for launcher in (shortcut, browser_shortcut):
            try:
                subprocess.run(['/usr/bin/open', str(launcher)], check=True)
            except (OSError, subprocess.CalledProcessError) as error:
                print(f'Could not open {launcher.name}: {error}', file=sys.stderr, flush=True)
        if managed_scilink is not None:
            try:
                subprocess.run([str(managed_scilink / 'bootstrap/bin/python'), str(managed_scilink / 'scilink_launch.py'),
                                '--root', str(managed_scilink)], cwd=managed_scilink, check=True)
            except (OSError, subprocess.CalledProcessError) as error:
                print(f'Could not open SciLink: {error}', file=sys.stderr, flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--install-root');parser.add_argument('--no-launch',action='store_true');parser.add_argument('--plan',action='store_true');args=parser.parse_args()
    try:install(args.install_root,args.no_launch,args.plan)
    except Exception as exc:print('Installation failed: '+str(exc),file=sys.stderr);sys.exit(1)
