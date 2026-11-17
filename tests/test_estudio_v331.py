from pathlib import Path

def test_public_identity():
    from faradaiq import release_identity as r
    assert r.APP_NAME == 'FaradaIQ eStudio'
    assert r.APP_VERSION == 'v331'
    assert r.INSTALL_TARGET == r'C:\FaradaIQ\eStudio\V331'

def test_branding_does_not_change_data_or_paths():
    from faradaiq.estudio_ui import brand_text
    assert brand_text('FaradaIQ Studio v320 — Browser') == 'FaradaIQ eStudio — Browser'
    assert brand_text(r'C:\Data\FaradaIQ Studio v320\sample.csv') == r'C:\Data\FaradaIQ Studio v320\sample.csv'
    assert brand_text('Sample v120') == 'Sample v120'

def test_release_entrypoints_and_distribution():
    root=Path(__file__).resolve().parents[1]
    for name in ['Start_FaradaIQ_eStudio_v331.bat','Install_FaradaIQ_eStudio_v331.bat','Install_or_Update.bat','Configure_GitHub.bat','Upload_to_GitHub.bat','assets/estudio.ico','release.json','scripts/distribute.ps1']:
        assert (root/name).is_file(),name

def test_active_export_schema_uses_current_release():
    import faradaiq_studio_core
    from faradaiq import v290_portable_browser as browser
    assert browser.SCHEMA == 'faradaiq-portable-browser-v331'
