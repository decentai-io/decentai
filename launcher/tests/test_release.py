"""A release file is believed for its signature, and for nothing else."""

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from launcher.release import Keys, Release, ReleaseError

RELEASE = {
    "schema_version": "1.0",
    "version": "1.4.0",
    "released_at": "2026-10-10T08:00:00Z",
    "images": {
        "backend": "ghcr.io/decentai/backend@sha256:" + "a" * 64,
        "runtime": "ghcr.io/decentai/runtime@sha256:" + "b" * 64,
        "frontend": "ghcr.io/decentai/frontend@sha256:" + "c" * 64,
    },
    "minimum_launcher": "0.1.0",
    "notes": "https://example.com/notes/1.4.0",
}


class Signer:
    def __init__(self):
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def write(self, folder, document=RELEASE, name="release.json"):
        raw = json.dumps(document, indent=1).encode("utf-8")
        path = folder / name
        path.write_bytes(raw)
        (folder / f"{name}.sig").write_bytes(
            base64.b64encode(self.private.sign(raw)))
        return path


@pytest.fixture
def signer():
    return Signer()


class TestASignedRelease:
    def test_it_is_read_and_says_what_it_installs(self, signer, tmp_path):
        release = Release.read(str(signer.write(tmp_path)), [signer.public])
        assert release.signed is True
        assert release.version == "1.4.0"
        assert release.images == RELEASE["images"]
        assert release.notes == RELEASE["notes"]

    def test_either_of_the_launchers_keys_will_do(self, signer, tmp_path):
        backup = Signer()
        path = backup.write(tmp_path)
        assert Release.read(str(path), [signer.public, backup.public]).signed

    def test_one_byte_changed_and_it_is_not_installed(self, signer, tmp_path):
        path = signer.write(tmp_path)
        path.write_bytes(path.read_bytes().replace(b"1.4.0", b"1.4.1"))
        with pytest.raises(ReleaseError, match="not signed by a key this launcher accepts"):
            Release.read(str(path), [signer.public])

    def test_somebody_elses_signature_is_not_a_signature(self, signer, tmp_path):
        path = Signer().write(tmp_path)
        with pytest.raises(ReleaseError, match="not signed by a key"):
            Release.read(str(path), [signer.public])

    def test_a_launcher_with_no_keys_believes_nobody(self, signer, tmp_path):
        with pytest.raises(ReleaseError):
            Release.read(str(signer.write(tmp_path)), [])

    def test_a_signature_that_is_not_one(self, signer, tmp_path):
        path = signer.write(tmp_path)
        (tmp_path / "release.json.sig").write_text("not base64 !!", encoding="utf-8")
        with pytest.raises(ReleaseError):
            Release.read(str(path), [signer.public])


class TestWhereTheReleaseIsRead:
    def command_line(self, tmp_path, monkeypatch, named=None):
        import argparse
        from launcher.__main__ import CommandLine
        monkeypatch.setenv("DECENTAI_STATE", str(tmp_path))
        return CommandLine(argparse.Namespace(release=named, unsigned=False))

    def test_a_build_of_ones_own_reads_the_file_it_was_built_with(
            self, tmp_path, monkeypatch):
        monkeypatch.delenv("DECENTAI_RELEASE_URL", raising=False)
        line = self.command_line(tmp_path, monkeypatch)
        assert line.source() == str(line.RELEASE)

    def test_a_published_launcher_reads_where_releases_are_published(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("DECENTAI_RELEASE_URL", "https://example.com/release.json")
        line = self.command_line(tmp_path, monkeypatch)
        assert line.source() == "https://example.com/release.json"
        named = self.command_line(tmp_path, monkeypatch, "/tmp/mine.json")
        assert named.source() == "/tmp/mine.json"


class TestAReleaseNobodySigned:
    def file(self, tmp_path, document=RELEASE):
        path = tmp_path / "release.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return str(path)

    def test_it_is_not_installed(self, signer, tmp_path):
        with pytest.raises(ReleaseError, match="carries no signature"):
            Release.read(self.file(tmp_path), [signer.public])

    def test_it_is_installed_when_the_person_says_so_and_says_what_it_is(
            self, signer, tmp_path):
        release = Release.read(self.file(tmp_path), [signer.public], unsigned=True)
        assert release.signed is False and release.version == "1.4.0"

    def test_saying_so_does_not_make_a_signed_one_unsigned(self, signer, tmp_path):
        assert Release.read(str(signer.write(tmp_path)), [signer.public],
                            unsigned=True).signed is True


class TestWhatAReleaseIs:
    @pytest.mark.parametrize("change, word", [
        ({"schema_version": "2.0"}, "schema_version"),
        ({"version": "latest"}, "version"),
        ({"images": {"backend": "a", "runtime": "b"}}, "images.frontend"),
        ({"images": "all of them"}, "images must name"),
        ({"images": {"backend": "x y", "runtime": "b:1", "frontend": "c:1"}},
         "images.backend"),
    ])
    def test_a_file_that_is_not_a_release(self, signer, tmp_path, change, word):
        path = signer.write(tmp_path, {**RELEASE, **change})
        with pytest.raises(ReleaseError, match=word):
            Release.read(str(path), [signer.public])

    def test_it_is_read_from_a_file_or_an_https_address(self, signer):
        with pytest.raises(ReleaseError, match="https address"):
            Release.read("http://example.com/release.json", [signer.public])

    def test_a_file_that_is_not_there(self, signer, tmp_path):
        with pytest.raises(ReleaseError, match="could not be read"):
            Release.read(str(tmp_path / "missing.json"), [signer.public])

    @pytest.mark.parametrize("newer, older", [
        ("1.4.0", "1.3.9"), ("1.10.0", "1.9.0"), ("2.0.0", "1.99.99"),
        ("0.1.1", "0.1.0-local"),
    ])
    def test_a_version_is_three_numbers(self, newer, older):
        assert Release.number(newer) > Release.number(older)
        assert Release({**RELEASE, "version": newer}, True).newer_than(older)
        assert not Release({**RELEASE, "version": older}, True).newer_than(newer)
        assert not Release({**RELEASE, "version": newer}, True).newer_than(newer)


class TestTheKeysALauncherCarries:
    def test_every_public_key_in_its_folder(self, signer, tmp_path):
        backup = Signer()
        (tmp_path / "release-1.pub").write_bytes(base64.b64encode(signer.public))
        (tmp_path / "release-2.pub").write_bytes(base64.b64encode(backup.public))
        (tmp_path / "README.md").write_text("not a key", encoding="utf-8")
        (tmp_path / "broken.pub").write_text("neither is this", encoding="utf-8")
        assert Keys(tmp_path).all() == [signer.public, backup.public]

    def test_a_folder_that_is_not_there_holds_none(self, tmp_path):
        assert Keys(tmp_path / "nowhere").all() == []
