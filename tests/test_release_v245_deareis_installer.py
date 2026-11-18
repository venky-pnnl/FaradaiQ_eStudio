from pathlib import Path
import sys

import _install_deareis_optional as installer

ROOT = Path(__file__).resolve().parents[1]


def test_deareis_requirement_is_reproducibly_pinned():
    req = (ROOT / "requirements_deareis_optional.txt").read_text(encoding="utf-8")
    assert "deareis==5.1.3" in req
    assert "deareis>=" not in req


def test_deareis_installer_never_self_upgrades_pip():
    bat = (ROOT / "install_deareis_optional.bat").read_text(encoding="utf-8")
    helper = (ROOT / "_install_deareis_optional.py").read_text(encoding="utf-8")
    combined = bat + "\n" + helper
    forbidden = (
        "pip install --upgrade \"pip",
        "pip install --upgrade 'pip",
        "ensurepip --upgrade",
    )
    for token in forbidden:
        assert token not in combined
    assert '"ensurepip", "--default-pip"' in helper
    for token in ("--disable-pip-version-check", "--no-input", "--no-cache-dir", "--prefer-binary"):
        assert token in helper


def test_deareis_installer_uses_transactional_rebuild_and_rollback():
    bat = (ROOT / "install_deareis_optional.bat").read_text(encoding="utf-8")
    helper = (ROOT / "_install_deareis_optional.py").read_text(encoding="utf-8")
    assert '_install_deareis_optional.py' in bat
    assert '.deareis_venv.__backup_v292' in helper
    assert 'env_dir.rename(backup)' in helper
    assert 'backup.rename(env_dir)' in helper
    assert 'Previous DearEIS environment restored.' in helper
    assert 'DearEIS installation and verification succeeded.' in helper


def test_deareis_installer_reuses_base_python_and_prefers_stable_versions():
    helper = (ROOT / "_install_deareis_optional.py").read_text(encoding="utf-8")
    assert "sys._base_executable" in helper
    assert "PREFERRED_WINDOWS = ((3, 12), (3, 13), (3, 11), (3, 14))" in helper
    assert "Python.Python.3.12" in helper


def test_release_excludes_optional_env_and_backup(tmp_path, monkeypatch):
    from scripts import build_release

    monkeypatch.setattr(build_release, "ROOT", tmp_path)
    for folder in (".deareis_venv", ".deareis_venv.__backup_v292"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "sentinel").touch()
    (tmp_path / "install_deareis_optional.bat").touch()
    assert build_release.payload_files() == [tmp_path / "install_deareis_optional.bat"]


def test_transactional_installer_rebuilds_a_broken_environment(tmp_path):
    root = tmp_path / "studio"
    old_env = root / ".deareis_venv"
    old_env.mkdir(parents=True)
    (old_env / "old_environment_marker.txt").write_text("broken", encoding="utf-8")
    req = root / "requirements_test.txt"
    req.write_text("# empty test requirements\n", encoding="utf-8")
    log = root / "install.log"

    code = installer.transactional_install(
        root=root,
        requirements=req,
        verify_module="pip",
        seed_command=[sys.executable],
        log_path=log,
    )

    assert code == 0
    assert installer.env_python(root / ".deareis_venv").exists()
    assert not (root / ".deareis_venv.__backup_v292").exists()
    assert not (root / ".deareis_venv" / "old_environment_marker.txt").exists()
    assert "verification succeeded" in log.read_text(encoding="utf-8").lower()


def test_transactional_installer_restores_old_environment_on_install_failure(tmp_path, monkeypatch):
    root = tmp_path / "studio"
    old_env = root / ".deareis_venv"
    old_env.mkdir(parents=True)
    marker = old_env / "old_environment_marker.txt"
    marker.write_text("preserve me", encoding="utf-8")
    req = root / "requirements_test.txt"
    req.write_text("# empty test requirements\n", encoding="utf-8")
    log = root / "install.log"

    original_run = installer.InstallLog.run

    def fail_install(self, command, env=None):
        parts = [str(x) for x in command]
        if "install" in parts and "-r" in parts:
            self.write("Simulated pip install failure")
            return 1
        return original_run(self, command, env=env)

    monkeypatch.setattr(installer.InstallLog, "run", fail_install)
    code = installer.transactional_install(
        root=root,
        requirements=req,
        verify_module="pip",
        seed_command=[sys.executable],
        log_path=log,
    )

    assert code == 1
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "preserve me"
    assert not (root / ".deareis_venv.__backup_v292").exists()
    assert "restored" in log.read_text(encoding="utf-8").lower()


def test_transactional_installer_discards_corrupted_tilde_pip_residue(tmp_path):
    root = tmp_path / "studio"
    residue = root / ".deareis_venv" / "Lib" / "site-packages" / "~ip"
    residue.mkdir(parents=True)
    (residue / "broken_marker.txt").write_text("WinError 145 residue", encoding="utf-8")
    req = root / "requirements_test.txt"
    req.write_text("# empty test requirements\n", encoding="utf-8")
    log = root / "install.log"

    code = installer.transactional_install(
        root=root,
        requirements=req,
        verify_module="pip",
        seed_command=[sys.executable],
        log_path=log,
    )

    assert code == 0
    assert installer.env_python(root / ".deareis_venv").exists()
    assert not (root / ".deareis_venv" / "Lib" / "site-packages" / "~ip").exists()
    assert not (root / ".deareis_venv.__backup_v292").exists()
