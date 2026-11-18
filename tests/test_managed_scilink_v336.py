import json
from pathlib import Path
import zipfile

import pytest

from faradaiq import managed_scilink as manager


FIRST = "a" * 40
SECOND = "b" * 40


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("FARADAIQ_SCILINK_ROOT", str(tmp_path))
    built = []
    prepared = []

    def prepare(root):
        prepared.append(root)

    def install(root, commit, progress):
        built.append(commit)
        python, cli = manager._runtime_paths(root, commit)
        python.parent.mkdir(parents=True)
        python.touch()
        cli.touch()
        return "0.0.84"

    monkeypatch.setattr(manager, "_prepare_bootstrap", prepare)
    monkeypatch.setattr(manager, "_latest_commit", lambda: FIRST)
    monkeypatch.setattr(manager, "_install_candidate", install)
    return tmp_path, built, prepared


def test_git_commit_comparison_uses_same_semver_and_installs_only_when_needed(workspace, monkeypatch):
    root, built, _ = workspace
    assert manager.current() is None
    assert manager.update()["changed"]
    assert not manager.update()["changed"]
    assert built == [FIRST]
    assert manager.runtime_python().is_file()
    assert manager.runtime_cli().is_file()
    monkeypatch.setattr(manager, "_latest_commit", lambda: SECOND)
    updated = manager.update()
    assert updated["commit"] == SECOND and updated["version"] == "0.0.84"
    assert built == [FIRST, SECOND]
    assert (root/"versions"/FIRST).is_dir()
    assert json.loads((root/"active.json").read_text())["commit"] == SECOND


def test_failed_replacement_leaves_active_install(workspace, monkeypatch):
    root, _, _ = workspace
    manager.update()
    old = (root/"active.json").read_bytes()
    monkeypatch.setattr(manager, "_latest_commit", lambda: SECOND)

    def fail(root, commit, progress):
        (root/"versions"/commit).mkdir()
        raise RuntimeError("Candidate did not start.")

    monkeypatch.setattr(manager, "_install_candidate", fail)
    with pytest.raises(RuntimeError, match="Candidate did not start"):
        manager.update()
    assert (root/"active.json").read_bytes() == old
    assert manager.current()["commit"] == FIRST
    assert not (root/"versions"/SECOND).exists()


def test_archive_refuses_outside_paths(tmp_path):
    archive = tmp_path/"source.zip"
    with zipfile.ZipFile(archive, "w") as file:
        file.writestr(f"SciLink-{FIRST}/../../escape", "unexpected")
    with pytest.raises(ValueError, match="unsafe"):
        manager._unpack_source(archive, tmp_path/"stage", FIRST)
    assert not (tmp_path/"escape").exists()


def test_invalid_local_receipt_does_not_traverse(workspace):
    root, _, _ = workspace
    (root/"active.json").write_text(json.dumps({"commit": "../other", "version": "1"}))
    assert manager.current() is None
    assert manager.runtime_cli() is None
