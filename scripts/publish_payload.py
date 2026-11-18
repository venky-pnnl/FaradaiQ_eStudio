"""Publish a checked release ZIP as a fast-forward-only GitHub source commit."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from zipfile import BadZipFile, ZipFile

MAIN = "refs/heads/main"
MANIFEST = re.compile(r"PACKAGE_MANIFEST_SHA256_v([0-9]+)\.txt")


def _parse_manifest(raw):
    entries = {}
    for line in raw.decode("utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        path = PurePosixPath(relative)
        if (not relative or path.is_absolute() or path.as_posix() != relative or ".." in path.parts
                or any(part.casefold() == ".git" for part in path.parts)
                or any(ord(char) < 32 or char in "\\:" for char in relative)
                or relative.casefold() in entries or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError(f"Invalid release manifest entry: {relative!r}")
        entries[relative.casefold()] = (relative, digest)
    return entries


def _release_files(archive):
    with ZipFile(archive) as package:
        metadata = json.loads(package.read("release.json"))
        version = metadata["version"]
        if (not re.fullmatch(r"v[0-9]+", version) or metadata["version_number"] != int(version[1:])
                or metadata["package_asset"] != f"FaradaIQ_eStudio_{version}_package.zip"
                or metadata["package_asset"] != archive.name):
            raise ValueError("Release metadata and ZIP name disagree.")
        manifest_name = f"PACKAGE_MANIFEST_SHA256_{version}.txt"
        manifest = package.read(manifest_name)
        expected = _parse_manifest(manifest)
        entries = package.infolist()
        if (len(entries) != len(expected) + 1 or {entry.filename for entry in entries}
                != {relative for relative, _ in expected.values()} | {manifest_name}):
            raise ValueError("Release ZIP files do not match its manifest.")
        files = {}
        for relative, digest in expected.values():
            content = package.read(relative)
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError(f"Release payload checksum failed: {relative}")
            mode = "100755" if relative.endswith((".sh", ".command")) else "100644"
            files[relative] = (mode, content)
        files[manifest_name] = ("100644", manifest)
    return metadata, files


class _Repository:
    def __init__(self, directory, executable, author_name, author_email):
        self.directory = directory
        self.executable = executable
        self.env = {key: value for key, value in os.environ.items() if key not in {
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
        }}
        self.env.update(GIT_AUTHOR_NAME=author_name, GIT_AUTHOR_EMAIL=author_email,
                        GIT_COMMITTER_NAME=author_name, GIT_COMMITTER_EMAIL=author_email)

    def command(self, *arguments, data=None, check=True):
        command = [self.executable, "-C", str(self.directory), *arguments]
        result = subprocess.run(command, input=data, capture_output=True, env=self.env)
        if check and result.returncode:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Git {arguments[0]} failed. {error}")
        return result

    def value(self, *arguments, data=None):
        return self.command(*arguments, data=data).stdout.decode("utf-8").strip()

    def tip(self, version):
        refs = dict(line.strip().split("\t", 1)[::-1] for line in
                    self.value("ls-remote", "--refs", "origin", MAIN, f"refs/tags/{version}").splitlines())
        if f"refs/tags/{version}" in refs:
            raise RuntimeError(f"Git tag {version} already exists. Existing release tags are preserved.")
        return refs.get(MAIN)

    def fetch_main(self, advertised):
        if advertised is None:
            return None
        self.command("fetch", "--no-tags", "origin", MAIN)
        return self.value("rev-parse", "FETCH_HEAD")

    def stale_files(self, parent, number, files):
        if parent is None:
            return set()
        records = self.command("ls-tree", "-r", "-z", parent).stdout.split(b"\0")
        blobs = {}
        for record in filter(None, records):
            metadata, path = record.split(b"\t", 1)
            mode, _, blob = metadata.decode("ascii").split()
            if mode in {"100644", "100755"}:
                blobs[path.decode("utf-8", errors="surrogateescape")] = blob

        manifests = []
        for path in blobs:
            match = MANIFEST.fullmatch(path)
            if match and int(match[1]) < number:
                manifests.append((int(match[1]), path))

        stale, prior_digests = set(), {}
        for _, path in sorted(manifests, reverse=True):
            try:
                entries = _parse_manifest(self.command("cat-file", "blob", blobs[path]).stdout)
            except ValueError:
                continue  # Keep manifests whose ownership cannot be established.
            if not entries:
                continue
            stale.add(path)
            for name, digest in entries.values():
                if name in blobs and name not in files and not MANIFEST.fullmatch(name):
                    prior_digests.setdefault(name, digest)
        for path, digest in prior_digests.items():
            content = self.command("cat-file", "blob", blobs[path]).stdout
            if hashlib.sha256(content).hexdigest() == digest:
                stale.add(path)
        return stale

    def stage(self, parent, index_info, files, stale):
        self.command("read-tree", parent if parent else "--empty")
        original = self.value("write-tree")
        existing = set(self.command("ls-files", "-z").stdout.decode("utf-8", errors="surrogateescape").split("\0")) - stale - {""}
        existing_lower = {path.casefold(): path for path in existing}
        for relative in files:
            parents = PurePosixPath(relative).parents
            if (any(str(parent_path) in existing for parent_path in parents if str(parent_path) != ".")
                    or any(path.startswith(relative + "/") for path in existing)):
                raise RuntimeError(f"Remote has a file/folder conflict at {relative}.")
            if existing_lower.get(relative.casefold(), relative) != relative:
                raise RuntimeError(f"Remote has a filename case conflict at {relative}.")
        deletions = "".join(f"0 {'0' * len(original)}\t{path}\0" for path in sorted(stale))
        self.command("update-index", "-z", "--index-info", data=deletions.encode("utf-8") + index_info)
        return original, self.value("write-tree")


def _index_payload(repo, directory, files):
    incoming = []
    for relative, (_, content) in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        incoming.append(path.as_posix())
    blob_ids = repo.value("hash-object", "-w", "--no-filters", "--stdin-paths",
                         data=("\n".join(incoming) + "\n").encode("utf-8")).splitlines()
    if len(blob_ids) != len(files):
        raise RuntimeError("Git did not record all of the verified payload files.")
    return "".join(f"{mode} {blob}\t{relative}\0" for (relative, (mode, _)), blob
                   in zip(files.items(), blob_ids)).encode("utf-8")


def _remote_version(repo, parent):
    if not parent:
        return None
    remote = repo.command("show", f"{parent}:release.json", check=False)
    if remote.returncode:
        return None
    try:
        number = json.loads(remote.stdout).get("version_number")
    except (AttributeError, ValueError):
        return None
    return number if type(number) is int else None


def publish(archive: Path, remote: str, git: str, author_name: str, author_email: str) -> str:
    metadata, files = _release_files(archive)
    version = metadata["version"]
    number = metadata["version_number"]
    with tempfile.TemporaryDirectory(prefix="FaradaIQ-publish-") as temp:
        directory = Path(temp)
        work = directory / "git"
        work.mkdir()
        repo = _Repository(work, git, author_name, author_email)
        repo.command("init", "--template=", "-b", "main")
        repo.command("remote", "add", "origin", remote)
        parent = repo.fetch_main(repo.tip(version))
        index_info = _index_payload(repo, directory / "payload", files)
        stale = repo.stale_files(parent, number, files)
        if stale:
            print(f"Replacing {len(stale)} retired release files on main.", file=sys.stderr)
        for attempt in range(2):
            remote_number = _remote_version(repo, parent)
            if remote_number is not None and remote_number > number:
                raise RuntimeError(f"GitHub main already contains newer eStudio v{remote_number}.")
            original, tree = repo.stage(parent, index_info, files, stale)
            if tree == original and parent:
                return parent
            if remote_number == number:
                raise RuntimeError(f"GitHub main already contains different source for {version}. Use a new version.")
            parents = ("-p", parent) if parent else ()
            commit = repo.value("commit-tree", tree, *parents, "-m", f"Release FaradaIQ eStudio {version}")
            pushed = repo.command("push", "origin", f"{commit}:{MAIN}", check=False)
            if pushed.returncode == 0:
                return commit
            new_tip = repo.tip(version)
            if new_tip == commit:
                return commit  # The server accepted the push before the connection failed.
            if attempt or not new_tip or new_tip == parent:
                details = pushed.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"GitHub did not accept the fast-forward push. {details}")
            newer = repo.fetch_main(new_tip)
            if parent and repo.command("merge-base", "--is-ancestor", parent, newer, check=False).returncode:
                raise RuntimeError("GitHub main history changed during upload. Review it before retrying.")
            changed = set(repo.command("diff", "--name-only", "-z", original, newer).stdout.decode("utf-8", errors="surrogateescape").split("\0"))
            overlap = sorted(changed & (files.keys() | stale))
            if overlap:
                raise RuntimeError("GitHub main changed release files during upload. Review: " + ", ".join(overlap))
            parent = newer
    raise RuntimeError("The source push did not complete.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--remote", required=True)
    parser.add_argument("--git", required=True)
    parser.add_argument("--author-name", required=True)
    parser.add_argument("--author-email", required=True)
    args = parser.parse_args()
    try:
        print(publish(args.zip, args.remote, args.git, args.author_name, args.author_email))
    except (BadZipFile, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"Release upload: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
