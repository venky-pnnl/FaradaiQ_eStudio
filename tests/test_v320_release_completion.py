from pathlib import Path


def test_v320_release_identity_and_entrypoint():
    from faradaiq import release_identity
    root=Path(__file__).resolve().parents[1]
    assert release_identity.APP_VERSION == 'v320'
    assert release_identity.SEMVER == '0.320.0'
    assert release_identity.INSTALL_TARGET == r'C:\FaradaIQ\Studio\V320'
    app=(root/'faradaiq'/'app.py').read_text(encoding='utf-8',errors='replace')
    assert '_v320_main_final' in app
    assert app.index('_v320_main_final') < app.index('_v319_main_final')
    assert (root/'faradaiq'/'v320_release_integrity.py').is_file()
    assert (root/'faradaiq_studio_v320.py').is_file()


def test_v320_powerpoint_overlay_footer_is_release_dynamic():
    root=Path(__file__).resolve().parents[1]
    module=(root/'faradaiq'/'v290_powerpoint_summary.py').read_text(encoding='utf-8',errors='replace')
    assert 'FaradaIQ Studio {VERSION} · {experiment_name}' in module
    assert '_v307_safe_savefig(fig, out' in module


def test_v320_installation_artifacts_exist():
    root=Path(__file__).resolve().parents[1]
    for name in (
        'Install_FaradaIQ_Studio_v320.bat',
        'Start_FaradaIQ_Studio_v320.bat',
        '_ensure_faradaiq_venv_v320.ps1',
        '_verify_package_manifest_v320.ps1',
        'README_v320.md',
        'RELEASE_NOTES_v320.md',
    ):
        assert (root/name).is_file(), name
