#!/usr/bin/env python
from __future__ import annotations
import hashlib, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
errors=[]; checks=0

def check(cond,msg):
    global checks
    checks += 1
    if not cond: errors.append(msg)

def text(rel):
    p=ROOT/rel; check(p.exists(),f'missing {rel}')
    return p.read_text(encoding='utf-8',errors='replace') if p.exists() else ''

rid=text('faradaiq/release_identity.py')
check('APP_VERSION = "v320"' in rid,'release identity not v320')
check('SEMVER = "0.320.0"' in rid,'semver not 0.320.0')
check(r'INSTALL_TARGET = r"C:\FaradaIQ\Studio\V320"' in rid,'install target not V320')
check('PACKAGE_NAME = "FaradaIQ_Studio_v320_package"' in rid,'package name not v320')
init=text('faradaiq/__init__.py'); check('v320' in init and '0.320.0' in init,'package init not v320')
app=text('faradaiq/app.py'); check('_v320_main_final' in app and '_v319_main_final' in app and app.index('_v320_main_final') < app.index('_v319_main_final'),'app does not prefer v320')
core=text('faradaiq_studio_core.py')
check('v320_release_integrity' in core,'core missing v320 release integrity')
check('_v320_main_final' in core,'core missing v320 entrypoint')
check(r'C:\Data\ANALYSIS\Faradaiq' in core or r'C:\\Data\\ANALYSIS\\Faradaiq' in core,'default analysis root changed')
rel=text('faradaiq/v320_release_integrity.py')
check('VERSION="v320"' in rel and 'V320' in rel,'v320 release-integrity identity missing')
check('faradaiq-portable-browser-v320' in rel,'v320 Browser schema missing')
ppt=text('faradaiq/v290_powerpoint_summary.py')
for phrase in (
    'Potential-resolved EIS overlay: Nyquist',
    'Potential-resolved EIS overlays: Bode magnitude and phase',
    'Potential-resolved DRT overlay',
    'DRT potential map and dominant relaxation trend',
    'v319_overlay_eis_drt_slides',
    'v319_nyquist_overlay.png',
    'v319_drt_map.png',
    'completed pyDRTtools distribution',
    '_v319_compact_coverage_slide',
    '_v307_disable_mathtext_for_figure',
    '_v307_safe_savefig',
): check(phrase in ppt,f'PowerPoint v320 contract missing {phrase}')
browser=text('faradaiq/v290_portable_browser.py'); check('faradaiq-portable-browser-v320' in browser,'browser standalone schema not v320')
canonical=text('faradaiq_canonical_bundle.py'); check('VERSION = "v320"' in canonical and 'FARADAIQ_PUBLIC_VERSION = "v320"' in canonical,'canonical standalone identity not v320')

bat=text('Install_FaradaIQ_Studio_v320.bat')
check('PACKAGE_ARCHIVE_NAME=FaradaIQ_Studio_v320_package.zip' in bat,'installer package name wrong')
check('PACKAGE_MANIFEST_SHA256_v320.txt' in bat,'installer manifest name wrong')
check('_verify_package_manifest_v320.ps1' in bat,'installer verifier wrong')
check('FaradaIQ_Studio_v320_installation.zip' not in bat,'installer references second installation zip')
check(r'C:\FaradaIQ\Studio\V320' in bat,'installer target not V320')

required=[
 'README_v320.md','RELEASE_NOTES_v320.md','MODIFIED_FILES_v320.md','POWERPOINT_PRESENTATION_QA_v320.md',
 'POWERPOINT_RECIPE_QA_v320.md','POTENTIAL_EIS_MODELING_QA_v320.md','COMPATIBILITY_QA_v320.md','INSTALLER_QA_v320.md',
 'KNOWN_LIMITATIONS_v320.md','LITERATURE_GUIDANCE_v320.md','RELEASE_VERSION_POLICY_v320.md','README_FIRST_INSTALL_v320.txt',
 'CHANGELOG_v320.txt','Run_FaradaIQ_QA_v320.bat','Start_FaradaIQ_Studio_v320.bat','Start_FaradaIQ_Studio_v320.command',
 'Start_FaradaIQ_Studio_v320.sh','faradaiq_studio_v320.py','_verify_package_manifest_v320.ps1','_run_faradaiq_qa_groups_v320.py',
 '_ensure_faradaiq_venv_v320.ps1','_qa_force_exit_plugin_v320.py','_run_llm_gui_smokes_v320.py','_audit_release_version_v320.py',
 'faradaiq/v320_release_integrity.py',
]
for r in required: check((ROOT/r).exists(),f'missing v320 release artifact {r}')

stale=[]
for p in ROOT.iterdir():
    if p.is_file() and any(v in p.name.lower() for v in ('v316','v317','v318','v319')):
        stale.append(p.name)
check(not stale,'stale top-level prior-release artifacts remain: '+', '.join(stale[:25]))

# Release payload must never carry generated launch/session state or stale prior-release regression artifacts.
for forbidden_dir in ('FaradaIQ_launch_sessions','regression_v316'):
    check(not (ROOT/forbidden_dir).exists(), f'generated/stale release directory present: {forbidden_dir}')

bad=[]
for p in ROOT.rglob('*'):
    relp=p.relative_to(ROOT).as_posix()
    if any(part in {'__pycache__','.pytest_cache'} for part in p.parts): bad.append(relp)
    elif p.suffix=='.pyc': bad.append(relp)
    elif p.suffix=='.zip': bad.append(relp)
check(not bad,'forbidden generated/nested files found: '+', '.join(bad[:25]))

manifest=ROOT/'PACKAGE_MANIFEST_SHA256_v320.txt'; check(manifest.exists(),'package manifest missing')
if manifest.exists():
    seen=0
    for line in manifest.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'): continue
        parts=line.split('  ',1)
        if len(parts)!=2:
            errors.append('malformed manifest line'); continue
        expected,relp=parts; target=ROOT/relp
        if not target.exists(): errors.append(f'manifest file missing: {relp}'); continue
        actual=hashlib.sha256(target.read_bytes()).hexdigest()
        if actual!=expected: errors.append(f'manifest hash mismatch: {relp}')
        seen+=1
    check(seen>400,f'manifest too small: {seen}')

if errors:
    print(f'FAIL {len(errors)} issue(s) across {checks} checks')
    for e in errors: print(' -',e)
    sys.exit(1)
print(f'PASS {checks} release-audit checks')
