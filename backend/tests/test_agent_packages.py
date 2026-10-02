"""Packages: the bytes an approval names, and the store that keeps them.

Everything downstream rests on one property — the same code packages to
the same digest, on any machine, at any time. If that is not true then
verification fails for code that never changed, the store grows a copy
per install, and "the code we approved" goes back to being a claim about
a repository rather than a fact about bytes.

So most of what is tested here is what must NOT change the digest, and
what must not be allowed into a package at all.
"""

import os
import stat
import tarfile
from pathlib import Path

import pytest

from database.agent_packages import PackageStore
from contracts.agent_package import AgentPackage, PackagingError


def agent_folder(root: Path, name: str = "agent") -> Path:
    folder = root / name
    (folder / "tools").mkdir(parents=True)
    (folder / "manifest.yaml").write_text("agent:\n  id: notebook\n")
    (folder / "agent.py").write_text("class Agent:\n    pass\n")
    (folder / "tools" / "notes.py").write_text("def save():\n    pass\n")
    return folder


class TestTheDigestNamesTheCode:
    def test_the_same_folder_packages_identically_twice(self, tmp_path):
        folder = agent_folder(tmp_path)
        assert AgentPackage.build(folder)[1] == AgentPackage.build(folder)[1]

    def test_when_the_files_were_touched_does_not_change_it(self, tmp_path):
        """Two clones of one commit have different mtimes. If those
        reached the archive, the same commit would package differently on
        every machine that fetched it."""
        folder = agent_folder(tmp_path)
        _, before = AgentPackage.build(folder)

        for path in folder.rglob("*"):
            if path.is_file():
                os.utime(path, (1_000_000, 1_000_000))

        assert AgentPackage.build(folder)[1] == before

    def test_a_copy_in_another_place_packages_the_same(self, tmp_path):
        """Only the paths INSIDE the agent count; where the checkout
        happened to land does not."""
        import shutil

        first = agent_folder(tmp_path, "here")
        second = tmp_path / "somewhere-else" / "different-name"
        second.parent.mkdir()
        shutil.copytree(first, second)

        assert AgentPackage.build(first)[1] == AgentPackage.build(second)[1]

    def test_build_artefacts_are_left_out(self, tmp_path):
        """`__pycache__` appears the moment an agent is imported. Inside
        the digest, it would mean the code changed by being run."""
        folder = agent_folder(tmp_path)
        _, clean = AgentPackage.build(folder)

        (folder / "__pycache__").mkdir()
        (folder / "__pycache__" / "agent.cpython-311.pyc").write_bytes(b"junk")
        (folder / "agent.pyc").write_bytes(b"junk")
        (folder / ".git").mkdir()
        (folder / ".git" / "HEAD").write_text("ref: refs/heads/main")

        assert AgentPackage.build(folder)[1] == clean

    def test_changing_a_file_does_change_it(self, tmp_path):
        folder = agent_folder(tmp_path)
        _, before = AgentPackage.build(folder)
        (folder / "agent.py").write_text("class Agent:\n    pass  # edited\n")
        assert AgentPackage.build(folder)[1] != before

    def test_the_digest_is_over_the_bytes_that_travel(self, tmp_path):
        archive, digest = AgentPackage.build(agent_folder(tmp_path))
        assert AgentPackage.digest(archive) == digest
        assert digest.startswith("sha256:") and len(digest) == 71


class TestWhatAPackageMayNotContain:
    def test_a_symbolic_link_is_refused(self, tmp_path):
        """A link is a way to reach outside the folder, and its target
        cannot be part of the digest."""
        folder = agent_folder(tmp_path)
        try:
            (folder / "escape").symlink_to(tmp_path)
        except (OSError, NotImplementedError):
            pytest.skip("this platform will not create symbolic links here")

        with pytest.raises(PackagingError, match="symbolic link"):
            AgentPackage.build(folder)

    def test_an_empty_folder_is_refused(self, tmp_path):
        empty = tmp_path / "nothing"
        empty.mkdir()
        with pytest.raises(PackagingError, match="nothing to package"):
            AgentPackage.build(empty)

    def test_a_path_that_escapes_is_refused_on_the_way_in(self, tmp_path):
        """The digest says the bytes are the ones that were approved, not
        that they are safe — so unpacking checks for itself."""
        import gzip
        import io

        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as tar:
            info = tarfile.TarInfo("../escaped.py")
            info.size = 4
            tar.addfile(info, io.BytesIO(b"evil"))
        archive = gzip.compress(raw.getvalue())

        with pytest.raises(PackagingError, match="escapes"):
            AgentPackage.extract(archive, tmp_path / "out")

    def test_something_that_is_not_a_file_is_refused_on_the_way_in(
            self, tmp_path):
        import gzip
        import io

        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as tar:
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        archive = gzip.compress(raw.getvalue())

        with pytest.raises(PackagingError, match="not a plain file"):
            AgentPackage.extract(archive, tmp_path / "out")

    def test_nonsense_is_reported_rather_than_raised_raw(self, tmp_path):
        with pytest.raises(PackagingError, match="not readable"):
            AgentPackage.extract(b"this is not an archive", tmp_path / "out")


class TestRoundTrip:
    def test_what_goes_in_comes_out(self, tmp_path):
        folder = agent_folder(tmp_path)
        archive, _ = AgentPackage.build(folder)
        out = tmp_path / "unpacked"
        AgentPackage.extract(archive, out)

        assert sorted(p.relative_to(out).as_posix()
                      for p in out.rglob("*") if p.is_file()) == [
            "agent.py", "manifest.yaml", "tools/notes.py",
        ]
        assert (out / "tools" / "notes.py").read_text() == "def save():\n    pass\n"

    def test_an_executable_stays_executable(self, tmp_path):
        if os.name == "nt":
            pytest.skip("Windows does not carry an executable bit")
        folder = agent_folder(tmp_path)
        script = folder / "run.sh"
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)

        archive, _ = AgentPackage.build(folder)
        out = tmp_path / "unpacked"
        AgentPackage.extract(archive, out)
        assert (out / "run.sh").stat().st_mode & stat.S_IXUSR


class TestTheStore:
    def test_a_package_is_kept_under_its_organization_and_digest(
            self, tmp_path):
        store = PackageStore(tmp_path)
        archive, digest = AgentPackage.build(agent_folder(tmp_path))

        store.put("org_a", digest, archive)
        assert store.get("org_a", digest) == archive
        assert (tmp_path / "org_a" / f"{digest.split(':')[1]}.tar.gz").is_file()

    def test_two_organizations_keep_their_own_copy(self, tmp_path):
        """The same public repository imported twice is stored twice. The
        digest is identical — it is the same code — and the boundary is
        the folder, which needs no argument about who may read what."""
        store = PackageStore(tmp_path)
        archive, digest = AgentPackage.build(agent_folder(tmp_path))

        store.put("org_a", digest, archive)
        store.put("org_b", digest, archive)

        assert store.list_digests("org_a") == store.list_digests("org_b") \
            == [digest]
        assert store.has("org_a", digest) and store.has("org_b", digest)

        store.delete("org_a", digest)
        assert not store.has("org_a", digest)
        assert store.has("org_b", digest), "one org's deletion reached another"

    def test_storing_the_same_package_again_is_a_no_op(self, tmp_path):
        store = PackageStore(tmp_path)
        archive, digest = AgentPackage.build(agent_folder(tmp_path))

        store.put("org_a", digest, archive)
        written = (tmp_path / "org_a" / f"{digest.split(':')[1]}.tar.gz")
        stamp = written.stat().st_mtime_ns

        store.put("org_a", digest, archive)
        assert written.stat().st_mtime_ns == stamp

    def test_a_missing_package_says_what_to_do(self, tmp_path):
        store = PackageStore(tmp_path)
        with pytest.raises(PackagingError, match="Install the agent again"):
            store.get("org_a", "sha256:" + "a" * 64)

    def test_an_organization_id_cannot_name_another_folder(self, tmp_path):
        """It arrives from a document. A separator in one would put a
        package somewhere it was never meant to go."""
        store = PackageStore(tmp_path)
        for hostile in ("../elsewhere", "a/b", "", "."):
            with pytest.raises(PackagingError, match="cannot name a folder"):
                store.put(hostile, "sha256:" + "a" * 64, b"x")

    def test_only_a_real_digest_names_a_package(self, tmp_path):
        store = PackageStore(tmp_path)
        for hostile in ("sha256:zz", "../../etc/passwd", "sha1:" + "a" * 40):
            with pytest.raises(PackagingError, match="not a package digest"):
                store.get("org_a", hostile)
