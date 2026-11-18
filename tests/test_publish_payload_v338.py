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


def test_keeps_only_current_release_files_from_verified_old_manifests(tmp_path, existing_remote):
    remote, source, first_commit = existing_remote
    git(source, "pull", str(remote), "main")
    stale = "Install_FaradaIQ_eStudio_v331.bat"
    edited = "maintainer-edited.txt"
    for number, names in ((331, (stale, edited)), (336, ("QA_v336.md",))):
        for name in names:
            (source / name).write_bytes(b"Old release content")
        digest = sha256(b"Old release content").hexdigest()
        (source / f"PACKAGE_MANIFEST_SHA256_v{number}.txt").write_text(
            "".join(f"{digest}  {name}\n" for name in names)
        )
    (source / edited).write_bytes(b"Maintainer revised this independently")
    git(source, "add", ".")
    git(source, "commit", "-m", "Previous releases overlaid on main")
    git(source, "push", str(remote), "main")
    previous = git(remote, "rev-parse", "main")
    git(remote, "tag", "v336", previous)

    commit = publish(remote, make_archive(tmp_path, number=337))
    assert git(remote, "rev-parse", f"{commit}^") == previous
    assert git(remote, "rev-parse", "v336") == previous
    assert git(remote, "show", f"v336:{stale}") == "Old release content"
    assert git(remote, "show", f"{first_commit}:README.md") == "Old GitHub page"
    tracked = git(remote, "ls-tree", "--name-only", "main").splitlines()
    assert stale not in tracked
    assert "QA_v336.md" not in tracked
    assert [p for p in tracked if p.startswith("PACKAGE_MANIFEST_SHA256_")] == [
        "PACKAGE_MANIFEST_SHA256_v337.txt"
    ]
    assert git(remote, "show", f"main:{edited}") == "Maintainer revised this independently"
    assert git(remote, "show", "main:preserve.txt") == "Independent remote change"


def test_retry_protects_concurrent_edits_to_stale_release_files(tmp_path, existing_remote, monkeypatch):
    remote, source, _ = existing_remote
    name = "Install_FaradaIQ_eStudio_v336.bat"
    old_content = b"Old release content"
    (source / name).write_bytes(old_content)
    (source / "PACKAGE_MANIFEST_SHA256_v336.txt").write_text(
        f"{sha256(old_content).hexdigest()}  {name}\n"
    )
    git(source, "add", ".")
    git(source, "commit", "-m", "Prior release")
    git(source, "push", str(remote), "main")
    original_command = uploader._Repository.command
    raced = []

    def race(repository, *args, **kwargs):
        if args[0] == "push" and not raced:
            (source / name).write_text("Edited during upload")
            git(source, "add", "--", name)
            git(source, "commit", "-m", "Concurrent change to old release file")
            git(source, "push", str(remote), "main")
            raced.append(git(source, "rev-parse", "HEAD"))
        return original_command(repository, *args, **kwargs)

    monkeypatch.setattr(uploader._Repository, "command", race)
    with pytest.raises(RuntimeError, match="changed release files"):
        publish(remote, make_archive(tmp_path, number=337))
    assert git(remote, "rev-parse", "main") == raced[0]
    assert git(remote, "show", f"main:{name}") == "Edited during upload"


def test_upload_rebuild_in_an_overlay_folder_uses_current_package_inventory(tmp_path):
    import shutil
    import sys

    current = make_archive(tmp_path, number=337)
    root = tmp_path / "overlaid"
    with zipfile.ZipFile(current) as archive:
        archive.extractall(root)
    (root / "Install_FaradaIQ_eStudio_v336.bat").write_text("Retired launcher")
    (root / "unlisted_notes.md").write_text("Local notes")
    scripts = root / "scripts"
    scripts.mkdir()
    build_script = scripts / "build_release.py"
    shutil.copy2(Path(__file__).resolve().parents[1] / "scripts" / build_script.name, build_script)
    output_zip = tmp_path / "out" / current.name
    result = subprocess.run(
        [sys.executable, str(build_script), "--from-manifest", "--output", str(output_zip)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(output_zip) as archive:
        assert set(archive.namelist()) == {
            "README.md", "release.json", "Install.command", "PACKAGE_MANIFEST_SHA256_v337.txt",
        }


def test_public_homepage_sets_up_only_missing_pages_and_website():
    from scripts import publish_homepage

    calls = []
    def fake_gh(*args):
        calls.append(args)
        if args == ("repos/venky-pnnl/FaradaiQ_eStudio",):
            return {"private": False, "has_pages": False, "homepage": ""}
        return {"html_url": "https://venky-pnnl.github.io/FaradaiQ_eStudio/"}

    url = publish_homepage.configure_homepage("venky-pnnl/FaradaiQ_eStudio", fake_gh)
    assert url == "https://venky-pnnl.github.io/FaradaiQ_eStudio/"
    assert calls[1] == ("--method", "POST", "repos/venky-pnnl/FaradaiQ_eStudio/pages",
                        "-f", "source[branch]=main", "-f", "source[path]=/docs")
    assert calls[2] == ("--method", "PATCH", "repos/venky-pnnl/FaradaiQ_eStudio",
                        "-f", "homepage=" + url)


def test_public_homepage_preserves_existing_pages_and_custom_website():
    from scripts import publish_homepage

    calls = []
    def fake_gh(*args):
        calls.append(args)
        if args == ("repos/venky-pnnl/FaradaiQ_eStudio",):
            return {"private": False, "has_pages": True, "homepage": "https://project.example/"}
        return {"html_url": "https://custom.example/", "source": {"branch": "custom", "path": "/"}}

    assert publish_homepage.configure_homepage("venky-pnnl/FaradaiQ_eStudio", fake_gh) == "https://custom.example/"
    assert len(calls) == 2
    assert publish_homepage.configure_homepage("another-owner/repo", fake_gh) is None
    assert len(calls) == 2
    assert publish_homepage.configure_homepage("venky-pnnl/FaradaiQ_eStudio", lambda *args: {"private": True}) is None
