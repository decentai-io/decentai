"""The identity folders of the nodes Compose starts, filled before any of them starts: the roots, where to enroll,
and a token (documentation/communication.md). The `identities` service runs this at every start of
the stack, in the issuer's image: a token made stays, and the rest is written again only where it differs.

    python identities.py                the folders, as above
    python identities.py token <kind>   a first token for a node Compose doesn't start, printed once
                                        (documentation/communication.md)
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from communication.enrollment import Enrollment
from communication.identity import Identity
from issuer.store import Store


class Identities:
    #: The roots as they were given, and one folder per node, named by its kind.
    ROOTS = Path("/given/roots.pem")
    NODES = Path("/nodes")
    #: The platform's user, in every image.
    USER = 999
    ENROLL_AT = "issuer:8004 issuer\n"
    #: Each certificate's lifetime. A machine off for less than this finds its nodes still certified.
    LIFETIME = 7 * 86400
    #: That of a node Compose doesn't start, which has its issuer to reach: as a token left to its default gives.
    ELSEWHERE = 12 * 3600

    def __init__(self, store: Store):
        self.store = store

    def token(self, kind: str, lifetime: int = ELSEWHERE) -> str:
        """A token of no scope, any number of uses and no end, since a node whose certificate ended enrolls again."""
        return self.store.create_token(kind, {}, lifetime, None, None, "install")[0]

    def fill(self) -> None:
        roots = self.ROOTS.read_text(encoding="utf-8")
        for folder in sorted(self.NODES.iterdir()):
            self._own(folder)
            self._put(folder / Identity.ROOTS_FILE, roots)
            if folder.name == "issuer":  # it certifies itself
                self._own(folder / "authority")
            else:
                self._enrollable(folder)

    def _enrollable(self, folder: Path) -> None:
        self._put(folder / Enrollment.ENROLL_FILE, self.ENROLL_AT)
        token = folder / Enrollment.TOKEN_FILE
        if not token.exists():
            self._put(token, self.token(folder.name.split("-")[0], self.LIFETIME))

    def _own(self, folder: Path) -> None:
        os.chown(folder, self.USER, self.USER)
        folder.chmod(0o700)

    def _put(self, path: Path, text: str) -> None:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
        os.chown(path, self.USER, self.USER)


if __name__ == "__main__":
    identities = Identities(Store(os.environ["MONGO_URI"], os.environ.get("ISSUER_DATABASE", "decentai_issuer")))
    match sys.argv[1:]:
        case []:
            identities.fill()
        case ["token", kind]:
            print(identities.token(kind))
        case _:
            sys.exit("usage: identities.py [token <kind>]")
