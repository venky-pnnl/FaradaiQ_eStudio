from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BAT = (ROOT / 'Install_FaradaIQ_Studio_v320.bat').read_text(encoding='utf-8', errors='replace')
VERIFIER_PATH = ROOT / '_verify_package_manifest_v320.ps1'
VERIFIER = VERIFIER_PATH.read_text(encoding='utf-8', errors='replace') if VERIFIER_PATH.exists() else ''


def test_installer_searches_normal_extract_all_ancestry_for_complete_package():
    assert r'%SOURCE_DIR%\..\..\..\%PACKAGE_ARCHIVE_NAME%' in BAT
    assert r'%SOURCE_DIR%\..\..\..\..\%PACKAGE_ARCHIVE_NAME%' in BAT


def test_installer_accepts_explicit_complete_package_archive_from_wrapper():
    assert 'FARADAIQ_SOURCE_PACKAGE_ZIP' in BAT
    assert 'if defined FARADAIQ_SOURCE_PACKAGE_ZIP' in BAT


def test_installer_uses_only_the_single_complete_package_name():
    assert 'PACKAGE_ARCHIVE_NAME=FaradaIQ_Studio_v320_package.zip' in BAT
    assert 'FaradaIQ_Studio_v320_installation.zip' not in BAT


def test_installer_failure_is_visible_and_logged():
    assert 'INSTALLATION FAILED' in BAT
    assert "Read-Host 'Press Enter to close this installer window'" in BAT
    assert r'FaradaIQ_Studio_v320_install.log' in BAT


def test_elevation_waits_for_child_completion():
    assert 'Start-Process' in BAT
    assert '-Wait' in BAT
    assert '-PassThru' in BAT


def test_extracted_payload_integrity_is_checked_before_bootstrap():
    assert 'PACKAGE_MANIFEST_SHA256_v320.txt' in BAT
    assert '_verify_package_manifest_v320.ps1' in BAT
    assert 'Get-FileHash -Algorithm SHA256' in VERIFIER
    assert 'PAYLOAD HASH MISMATCH' in VERIFIER
    assert BAT.index('_verify_package_manifest_v320.ps1') < BAT.index('--install-only')
