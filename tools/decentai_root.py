"""decentai-root: the one program the root's keys meet. It runs on the offline machine only, and keeps
no state of its own: the keys and certificates are the files it is given
(documentation/communication.md).

    python decentai_root.py init acme
        the two roots, their keys encrypted with a passphrase, and roots.pem
    python decentai_root.py sign authority.csr --name decentai-acme-issuer-1-ca --kinds issuer,api,runtime,sandbox
        an issuer's intermediate, after showing the request's fingerprint and asking
    python decentai_root.py show decentai-acme-issuer-1-ca.pem --roots roots.pem
        what a certificate says, and whether it leads to the roots

The passphrase is asked for, never stored. ``--passphrase-file`` reads it from a file instead.
"""

import argparse
import getpass
import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography import x509  # noqa: E402
from cryptography.exceptions import InvalidSignature  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

from communication.enrollment import Enrollment  # noqa: E402
from communication.identity import Identity, TrustError  # noqa: E402

PEM = serialization.Encoding.PEM


class RootTool:
    def __init__(self, folder: str = ".", passphrase_file: str | None = None,
                 ask: Callable[[str], str] = input, say: Callable[[str], None] = print):
        self.folder, self.passphrase_file, self.ask, self.say = folder, passphrase_file, ask, say

    def init(self, deployment: str) -> list[str]:
        """The deployment's two roots, a primary and a backup, and roots.pem. Nothing is overwritten."""
        names = [f"decentai-{deployment}-root-{number}" for number in (1, 2)]
        files = [f"{name}{ending}" for name in names for ending in (".key", ".pem")] + [Identity.ROOTS_FILE]
        for name in files:
            if os.path.exists(self._path(name)):
                raise FileExistsError(f"{self._path(name)} is already there: nothing was written")
        passphrase = self._passphrase(twice=True)
        roots = []
        for name in names:
            root = Identity.root(name)
            roots.append(root.certificate)
            self._write(f"{name}.key", root.key.private_bytes(PEM, serialization.PrivateFormat.PKCS8,
                                                              serialization.BestAvailableEncryption(passphrase)))
            self._write(f"{name}.pem", root.certificate.public_bytes(PEM))
        self._write(Identity.ROOTS_FILE, b"".join(root.public_bytes(PEM) for root in roots))
        self.say(f"wrote {files[0]}     the primary's key, encrypted: keep it here")
        self.say(f"wrote {files[2]}     the backup's key, encrypted: move it to its own place")
        self.say(f"wrote {files[1]}     the primary's certificate")
        self.say(f"wrote {files[3]}     the backup's certificate")
        self.say(f"wrote {Identity.ROOTS_FILE}                    both certificates: what every node is given")
        return files

    def sign(self, request: str, name: str, kinds: list[str], root: str | None = None, days: int = 120,
             yes: bool = False) -> str | None:
        """An issuer's intermediate, for the key in its request, signed by the primary root: the name
        and kinds come from the operator, never from the request. Returns the file written, or None
        if the operator said no."""
        with open(request, encoding="utf-8") as file:
            pem = file.read()
        key = Enrollment.requested_key(pem)
        out = f"{name}.pem"
        if os.path.exists(self._path(out)):
            raise FileExistsError(f"{self._path(out)} is already there")
        self.say(f"request fingerprint {Enrollment.fingerprint(pem)}")
        if not yes and self.ask(f"sign it as {name}, for {days} days, kinds {', '.join(kinds)}? [y/N] ").strip().lower() != "y":
            self.say("not signed")
            return None
        primary = self._primary(root)
        certificate = primary.certify_authority(name, key=key, kinds=kinds, issued=int(time.time()),
                                                lifetime=days * 86400)
        self._write(out, certificate.public_bytes(PEM))
        self.say(f"wrote {out}")
        return out

    def show(self, certificate: str, roots: str | None = None, chain: list[str] = ()) -> list[str]:
        """What a certificate says, and, given the roots, whether it leads to them."""
        with open(certificate, "rb") as file:
            shown = x509.load_pem_x509_certificate(file.read())
        basic = shown.extensions.get_extension_for_class(x509.BasicConstraints).value
        lines = [f"subject   {shown.subject.rfc4514_string()}", f"issuer    {shown.issuer.rfc4514_string()}",
                 f"valid     {shown.not_valid_before_utc:%Y-%m-%d %H:%M} to {shown.not_valid_after_utc:%Y-%m-%d %H:%M} UTC"]
        if basic.ca:
            lines.append(f"signs     {'intermediates' if basic.path_length else 'nodes'} (an authority)")
            try:
                trees = shown.extensions.get_extension_for_class(x509.NameConstraints).value.permitted_subtrees
                suffix = Identity.name(kind="")
                lines.append("kinds     " + ", ".join(tree.value[:-len(suffix)] for tree in trees
                                                      if tree.value.endswith(suffix)))
            except x509.ExtensionNotFound:
                lines.append("kinds     any")
        else:
            fields = Identity.fields(shown)
            lines += [f"node      {fields['id']}, kind {fields['kind']}",
                      f"metadata  {json.dumps(fields['metadata'])}"]
        if roots:
            lines.append(f"roots     {self._leads(shown, basic.ca, roots, chain)}")
        for line in lines:
            self.say(line)
        return lines

    def _leads(self, certificate: x509.Certificate, authority: bool, roots: str, chain: list[str]) -> str:
        with open(roots, "rb") as file:
            trusted = x509.load_pem_x509_certificates(file.read())
        if authority:
            for root in trusted:
                try:
                    certificate.verify_directly_issued_by(root)
                    return f"leads to {root.subject.rfc4514_string()}"
                except (ValueError, TypeError, InvalidSignature):
                    continue
            return "leads to none of them"
        between = []
        for path in chain:
            with open(path, "rb") as file:
                between += x509.load_pem_x509_certificates(file.read())
        try:
            Identity.check(certificate, trusted, int(time.time()), chain=between)
            return "leads to them"
        except TrustError as error:
            return f"does not: {error}"

    def _primary(self, root: str | None) -> Identity:
        """The primary root, its key opened with the passphrase."""
        if root is None:
            found = glob.glob(self._path("*-root-1.key"))
            if len(found) != 1:
                raise FileNotFoundError(f"name the primary root's key with --root: found {len(found)} here")
            root = found[0]
        with open(root, "rb") as file:
            key = serialization.load_pem_private_key(file.read(), password=self._passphrase(twice=False))
        with open(root[:-len(".key")] + ".pem", "rb") as file:
            return Identity(key, x509.load_pem_x509_certificate(file.read()))

    def _passphrase(self, twice: bool) -> bytes:
        if self.passphrase_file:
            with open(self.passphrase_file, encoding="utf-8") as file:
                passphrase = file.read().strip()
        else:
            passphrase = getpass.getpass("passphrase for the root keys: ")
            if twice and getpass.getpass("again: ") != passphrase:
                raise ValueError("the two passphrases differ")
        if len(passphrase) < 12:
            raise ValueError("a passphrase of at least 12 characters")
        return passphrase.encode()

    def _write(self, name: str, data: bytes) -> None:
        mode = 0o600 if name.endswith(".key") else 0o644
        with open(os.open(self._path(name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "wb") as file:
            file.write(data)

    def _path(self, name: str) -> str:
        return os.path.join(self.folder, name)

    @classmethod
    def main(cls, argv: list[str] | None = None) -> int:
        parser = argparse.ArgumentParser(prog="decentai-root", description=__doc__.split("\n\n")[0])
        parser.add_argument("--passphrase-file", help="read the passphrase from this file, instead of asking")
        commands = parser.add_subparsers(dest="command", required=True)
        init = commands.add_parser("init", help="make the two roots, and roots.pem")
        init.add_argument("deployment", help="the deployment's name, as in decentai-<deployment>-root-1")
        sign = commands.add_parser("sign", help="sign an issuer's request as its intermediate")
        sign.add_argument("request", help="the issuer's authority.csr")
        sign.add_argument("--name", required=True, help="the intermediate's name: decentai-<deployment>-issuer-<n>-ca")
        sign.add_argument("--kinds", required=True, help="the kinds it may certify, comma-separated")
        sign.add_argument("--root", help="the primary root's key (default: the one *-root-1.key here)")
        sign.add_argument("--days", type=int, default=120, help="how long it lasts (default: 120)")
        sign.add_argument("--yes", action="store_true", help="sign without asking")
        show = commands.add_parser("show", help="what a certificate says, and whether it leads to the roots")
        show.add_argument("certificate")
        show.add_argument("--roots", help="roots.pem, to check it against")
        show.add_argument("--chain", action="append", default=[], help="an intermediate on the way (a node's)")
        args = parser.parse_args(argv)
        tool = cls(passphrase_file=args.passphrase_file)
        try:
            if args.command == "init":
                tool.init(args.deployment)
            elif args.command == "sign":
                tool.sign(args.request, args.name, [kind.strip() for kind in args.kinds.split(",")], args.root,
                          args.days, args.yes)
            else:
                tool.show(args.certificate, args.roots, args.chain)
        except (OSError, ValueError) as error:
            print(f"decentai-root: {error}", file=sys.stderr)
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(RootTool.main())
