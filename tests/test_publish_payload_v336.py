"""Exercise the release uploader against real local Git remotes."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from scripts import publish_payload as uploader


def git(root, *argv):
    return subprocess.check_output(["git", "-C", str(root), *argv], text=True).strip()


def make_archive(root, *, body=b"Version 336\n", number=336, corrupt=False):
    version = f"v{number}"
    archive = root / f"FaradaIQ_eStudio_{version}_package.zip"
    payload = {
        "README.md": body,
        "release.json": json.dumps({"version": version, "version_number": number, "package_asset": archive.name}).encode(),
        "Install.command": b"#!/bin/bash\n",
    }
    manifest = "".join(f"{sha256(data).hexdigest()}  {name}\n" for name, data in payload.items())
    with zipfile.ZipFile(archive, "w") as output:
        for name, data in payload.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o100755 if name.endswith(".command") else 0o100644) << 16
            output.writestr(info, data if not corrupt or name != "README.md" else b"wrong")
        output.writestr(f"PACKAGE_MANIFEST_SHA256_{version}.txt", manifest)
    return archive


@pytest.fixture
def existing_remote(tmp_path):
    remote, source = tmp_path/"remote.git", tmp_path/"existing source"
    remote.mkdir()
    source.mkdir()
    git(remote, "init", "--bare", "-b", "main")
    git(source, "init", "-b", "main")
    git(source, "config", "user.name", "Upstream")
    git(source, "config", "user.email", "upstream@example.com")
    (source/"README.md").write_text("Old GitHub page\n")
    (source/"preserve.txt").write_text("Independent remote change\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "Existing remote commit")
    git(source, "push", str(remote), "main")
    return remote, source, git(remote,"rev-parse","main")


def publish(remote, archive):
    return uploader.publish(archive, str(remote), "git", "Release tester", "release@example.com")


def test_release_zip_extends_existing_history_without_using_extracted_folder_git(tmp_path, existing_remote):
    remote, source, before = existing_remote
    archive = make_archive(tmp_path)
    commit = publish(remote, archive)
    assert git(remote,"rev-parse","main^")==before
    assert git(remote,"rev-parse","main")==commit
    assert git(remote,"show","main:preserve.txt")=="Independent remote change"
    assert git(remote,"show","main:README.md")=="Version 336"
    assert git(remote,"ls-tree","main","Install.command").startswith("100755 ")
    assert git(remote,"show","main:PACKAGE_MANIFEST_SHA256_v336.txt")
    assert git(source,"rev-parse","HEAD")==before
    assert publish(remote, archive)==commit


def test_empty_remote_and_existing_tags(tmp_path):
    remote = tmp_path/"remote.git"
    remote.mkdir()
    git(remote,"init","--bare","-b","main")
    archive = make_archive(tmp_path)
    commit = publish(remote, archive)
    assert git(remote,"rev-list","--count","main")=="1"
    git(remote,"tag","v336",commit)
    with pytest.raises(RuntimeError,match="already exists"):
        publish(remote,archive)


def test_verified_zip_required_and_older_release_cannot_replace_newer_main(tmp_path, existing_remote):
    remote, _, before = existing_remote
    with pytest.raises(ValueError, match="checksum"):
        publish(remote, make_archive(tmp_path, corrupt=True))
    assert git(remote,"rev-parse","main")==before
    newer = publish(remote,make_archive(tmp_path,number=337))
    with pytest.raises(RuntimeError,match="newer"):
        publish(remote,make_archive(tmp_path,number=336))
    assert git(remote,"rev-parse","main")==newer


@pytest.mark.parametrize("overlapping", [False, True])
def test_retry_keeps_concurrent_remote_edits(tmp_path, existing_remote, monkeypatch, overlapping):
    remote, source, _ = existing_remote
    original_command = uploader._Repository.command
    raced = []

    def race(repository, *args, **kwargs):
        if args[0] == "push" and not raced:
            path = source/("README.md" if overlapping else "new remote note.txt")
            path.write_text("Saved by another maintainer\n")
            git(source, "add", "--", path.name)
            git(source, "commit", "-m", "Concurrent remote change")
            git(source, "push", str(remote), "main")
            raced.append(git(remote, "rev-parse", "main"))
        return original_command(repository, *args, **kwargs)

    monkeypatch.setattr(uploader._Repository, "command", race)
    archive = make_archive(tmp_path)
    if overlapping:
        with pytest.raises(RuntimeError, match="changed release files"):
            publish(remote, archive)
        assert git(remote, "rev-parse", "main") == raced[0]
        assert git(remote, "show", "main:README.md") == "Saved by another maintainer"
    else:
        assert publish(remote, archive) == git(remote, "rev-parse", "main")
        assert git(remote, "rev-parse", "main^") == raced[0]
        assert git(remote, "show", "main:new remote note.txt") == "Saved by another maintainer"
