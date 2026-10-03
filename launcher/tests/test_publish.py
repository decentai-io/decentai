"""What is published is what a launcher will believe."""

import base64

import pytest

from launcher.publish import Publisher, main
from launcher.release import Keys, Release, ReleaseError

LAUNCHER = "ghcr.io/decentai-io/decentai-launcher@sha256:" + "d" * 64
IMAGES = {
    "backend": "ghcr.io/decentai-io/decentai-backend@sha256:" + "a" * 64,
    "runtime": "ghcr.io/decentai-io/decentai-ai-runtime@sha256:" + "b" * 64,
    "frontend": "ghcr.io/decentai-io/decentai-caddy@sha256:" + "c" * 64,
}


class TestAKey:
    def test_the_public_half_is_one_a_launcher_reads(self, tmp_path):
        private = Publisher.make_key("release", tmp_path)
        [public] = Keys(tmp_path).all()
        assert len(public) == 32
        assert len(base64.b64decode(private)) == 32

    def test_a_key_a_launcher_already_carries_is_not_replaced(self, tmp_path):
        Publisher.make_key("release", tmp_path)
        before = (tmp_path / "release.pub").read_text()
        with pytest.raises(ReleaseError, match="already there"):
            Publisher.make_key("release", tmp_path)
        assert (tmp_path / "release.pub").read_text() == before


class TestARelease:
    def test_written_signed_and_believed_by_a_launcher(self, tmp_path):
        private = Publisher.make_key("release", tmp_path / "keys")
        document = Publisher.document("1.4.0", IMAGES, notes="https://x/notes",
                                      launcher=LAUNCHER)
        path = Publisher.write(tmp_path / "release.json", document, private)

        release = Publisher.check(path, tmp_path / "keys")
        assert release.signed is True
        assert (release.version, release.images) == ("1.4.0", IMAGES)
        assert release.launcher == LAUNCHER
        # A launcher with other keys does not believe it.
        Publisher.make_key("other", tmp_path / "elsewhere")
        with pytest.raises(ReleaseError, match="not signed by a key"):
            Publisher.check(path, tmp_path / "elsewhere")

    def test_an_image_named_by_a_tag_is_not_published(self):
        with pytest.raises(ReleaseError, match="by its digest"):
            Publisher.document("1.4.0", {**IMAGES, "runtime": "runtime:latest"},
                               launcher=LAUNCHER)
        with pytest.raises(ReleaseError, match="the launcher"):
            Publisher.document("1.4.0", IMAGES, launcher="launcher:latest")

    def test_a_version_that_is_not_one_is_not_published(self):
        with pytest.raises(ReleaseError, match="version"):
            Publisher.document("latest", IMAGES, launcher=LAUNCHER)

    def test_something_that_is_not_a_key_signs_nothing(self, tmp_path):
        document = Publisher.document("1.4.0", IMAGES, launcher=LAUNCHER)
        with pytest.raises(ReleaseError, match="not a signing key"):
            Publisher.write(tmp_path / "release.json", document, "nonsense")
        assert not (tmp_path / "release.json.sig").exists()


class TestTheCommandLine:
    def arguments(self, tmp_path):
        return ["release", str(tmp_path / "release.json"), "--version", "1.4.0",
                "--keys", str(tmp_path / "keys"), "--launcher", LAUNCHER,
                *[item for part, image in IMAGES.items()
                  for item in (f"--{part}", image)]]

    def test_a_release_signed_with_the_repositorys_key(
            self, tmp_path, monkeypatch, capsys):
        private = Publisher.make_key("release", tmp_path / "keys")
        monkeypatch.setenv(Publisher.KEY_VARIABLE, private)
        assert main(self.arguments(tmp_path)) == 0
        assert "signed" in capsys.readouterr().out
        assert Release.read(str(tmp_path / "release.json"),
                            Keys(tmp_path / "keys").all()).signed

    def test_a_key_no_launcher_accepts_fails_the_release(
            self, tmp_path, monkeypatch, capsys):
        Publisher.make_key("release", tmp_path / "keys")
        monkeypatch.setenv(Publisher.KEY_VARIABLE,
                           Publisher.make_key("stray", tmp_path / "stray"))
        assert main(self.arguments(tmp_path)) == 1
        assert "not signed by a key" in capsys.readouterr().err

    def test_making_a_key_prints_the_private_half_once(self, tmp_path, capsys):
        assert main(["keys", "release", str(tmp_path)]) == 0
        said = capsys.readouterr().out
        assert "commit it" in said and (tmp_path / "release.pub").exists()
