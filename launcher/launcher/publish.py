"""Publishing a release: the maintainer's side of release.py.

    python -m launcher.publish keys release ./keys
        makes a signing key: writes keys/release.pub, the half the
        launcher carries, and prints the private half once

    python -m launcher.publish release release.json --version 1.4.0 \\
        --backend …@sha256:… --runtime …@sha256:… --frontend …@sha256:… \\
        --notes https://…
        writes the release file and, beside it, its signature, made
        with the private key in DECENTAI_RELEASE_KEY

The release workflow (.github/workflows/release.yml) runs the second
after it has built the images, and then reads the file back the way a
launcher will, with the keys this repository carries: a release signed
with a key no launcher accepts is not published.

Not in the launcher's own command line: a person running DecentAI never
signs anything.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from launcher import VERSION
from launcher.release import PARTS, SCHEMA_VERSION, Keys, Release, ReleaseError


class Publisher:
    #: Where the workflow hands over the private key.
    KEY_VARIABLE = "DECENTAI_RELEASE_KEY"

    # ------------------------------------------------------------------
    # A key
    # ------------------------------------------------------------------

    @staticmethod
    def make_key(name: str, folder: str | Path) -> str:
        """A new Ed25519 pair. The public half is written as
        ``<folder>/<name>.pub``; the private half is returned, base64,
        and kept nowhere by this."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)

        path = Path(folder) / f"{name}.pub"
        if path.exists():
            raise ReleaseError(
                f"{path} is already there. A launcher that is out carries "
                f"it; make a key under another name instead.")
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(base64.b64encode(public).decode("ascii") + "\n",
                        encoding="utf-8")
        secret = private.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption())
        return base64.b64encode(secret).decode("ascii")

    # ------------------------------------------------------------------
    # A release
    # ------------------------------------------------------------------

    @staticmethod
    def document(version: str, images: Dict[str, str], notes: str = "",
                 released_at: str = "") -> dict:
        document = {
            "schema_version": SCHEMA_VERSION,
            "version": version,
            "released_at": released_at or datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "images": {part: images.get(part, "") for part in PARTS},
            # The launcher that is published with a release is the
            # oldest one known to install it.
            "minimum_launcher": VERSION,
            "notes": notes,
        }
        problems = Release.problems(document)
        if problems:
            raise ReleaseError("That is not a release: " + "; ".join(problems))
        loose = [part for part in PARTS
                 if "@sha256:" not in document["images"][part]]
        if loose:
            raise ReleaseError(
                "A published release names each image by its digest "
                "(…@sha256:…), so that what is signed is what is installed: "
                + ", ".join(loose) + " is named by a tag.")
        return document

    @classmethod
    def write(cls, path: str | Path, document: dict, private_key: str) -> Path:
        """The release file and its signature beside it. The signature
        is over the file's bytes exactly as written."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)

        try:
            secret = base64.b64decode(str(private_key or "").strip(),
                                      validate=True)
            key = Ed25519PrivateKey.from_private_bytes(secret)
        except ValueError as exc:
            raise ReleaseError(
                f"{cls.KEY_VARIABLE} is not a signing key: the base64 of an "
                f"Ed25519 private key, as `publish keys` printed it.") from exc
        path = Path(path)
        raw = (json.dumps(document, indent=2) + "\n").encode("utf-8")
        path.write_bytes(raw)
        Path(f"{path}.sig").write_bytes(
            base64.b64encode(key.sign(raw)) + b"\n")
        return path

    @staticmethod
    def check(path: str | Path, keys_folder: str | Path) -> Release:
        """The file read back as a launcher reads it. Raises when no
        key in ``keys_folder`` signed it."""
        return Release.read(str(path), Keys(keys_folder).all())


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog="launcher.publish", description="Make a key, or sign a release.")
    commands = top.add_subparsers(dest="command", required=True)

    keys = commands.add_parser("keys", help="make a signing key")
    keys.add_argument("name", help="what to call it: release, backup")
    keys.add_argument("folder", help="where the public half goes: launcher/keys")

    release = commands.add_parser("release", help="write and sign a release file")
    release.add_argument("path")
    release.add_argument("--version", required=True)
    for part in PARTS:
        release.add_argument(f"--{part}", required=True,
                             help="the image, by digest")
    release.add_argument("--notes", default="")
    release.add_argument("--keys", default=str(
        Path(__file__).resolve().parent.parent / "keys"),
        help="the public keys a launcher accepts, to check the result against")
    return top


def main(argv=None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "keys":
            private = Publisher.make_key(arguments.name, arguments.folder)
            print(f"Wrote {Path(arguments.folder) / (arguments.name + '.pub')}: "
                  f"commit it.\n\nThe private key, shown once. Keep it where "
                  f"releases are signed, and nowhere else:\n\n{private}")
            return 0
        document = Publisher.document(
            arguments.version,
            {part: getattr(arguments, part) for part in PARTS},
            notes=arguments.notes)
        path = Publisher.write(arguments.path, document,
                               os.environ.get(Publisher.KEY_VARIABLE) or "")
        release = Publisher.check(path, arguments.keys)
        print(f"Release {release.version} written to {path} and signed.")
        return 0
    except ReleaseError as failed:
        print(f"\n{failed}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
