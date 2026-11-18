import importlib.util,json,zipfile,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_builder',ROOT/'scripts/build_release.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

def test_release_filter_excludes_environments_state_and_archives():
    files=builder.payload_files()
    assert files
    assert not any(f.suffix=='.zip' or set(f.relative_to(ROOT).parts)&builder.EXCLUDED for f in files)
    assert not any(f.name in {'github.json','installation_complete.json','current.json'} for f in files)

def test_zip_payload_matches_checksums(tmp_path):
    dest=builder.build(tmp_path/'FaradaIQ_eStudio_v338_package.zip')
    with zipfile.ZipFile(dest) as z:
        names=z.namelist()
        assert 'Install_FaradaIQ_eStudio_v338.bat' in names
        assert not any(n.endswith('.zip') or '..' in Path(n).parts for n in names)
        lines=z.read('PACKAGE_MANIFEST_SHA256_v338.txt').decode().splitlines()
        assert len(lines)>100
        for line in lines:
            digest,name=line.split('  ',1)
            assert hashlib.sha256(z.read(name)).hexdigest()==digest,name
    expected=dest.with_name(dest.name+'.sha256').read_text().split()[0]
    assert hashlib.sha256(dest.read_bytes()).hexdigest()==expected

def test_required_skill_is_unchanged():
    expected='624e31ad3754c22df0958b4adf2dd8ffd4fa0932c87546fcc7c26b5b076df06a'
    assert hashlib.sha256((ROOT/'skills/electrochemistry_skill.md').read_bytes()).hexdigest()==expected


def test_runtime_keys_and_job_queues_are_excluded():
    created=[]
    try:
        for directory,name in [('FaradaIQ_export_jobs','qa_exclusion_probe.json'),('llm_env_helpers','qa_exclusion_probe.bat'),('FaradaIQ_runtime_settings','qa_exclusion_probe.json'),('.', 'analysis_tools_status_v338.json')]:
            path=ROOT/directory/name;path.parent.mkdir(exist_ok=True);path.write_text('private-runtime-probe');created.append(path)
        files=builder.payload_files()
        assert all(path not in files for path in created)
        # Upload receives the built ZIP; its source files come from the verified package manifest.
        uploader = (ROOT/'scripts/distribute.ps1').read_text()
        assert 'publish_payload.py' in uploader and "'--zip', $package" in uploader
        assert 'git add -A' not in (ROOT/'scripts/distribute.ps1').read_text()
    finally:
        for path in created:path.unlink(missing_ok=True)
