"""The identities the sandbox's tests run with: one root, and from it a sandbox and three runtimes, each in a
folder of its own. Run once, before the sandbox starts:

    python sandbox/tests/identities.py /identities
"""

import os
import sys
import time

from communication.identity import Identity


class Identities:
    #: The sandbox runs as the platform's user, and only it may read its files.
    SANDBOX_USER = 999
    LIFETIME = 86400
    NODES = (("sandbox", "sandbox", "sandbox", SANDBOX_USER), ("runtime", "test-runtime", "runtime", 0),
             ("runtime-2", "test-runtime-2", "runtime", 0), ("runtime-3", "test-runtime-3", "runtime", 0),
             ("api", "test-api", "api", 0))

    @classmethod
    def make(cls, folder: str) -> None:
        root = Identity.root("sandbox-tests-root")
        for name, node_id, kind, owner in cls.NODES:
            node = Identity()
            node.certificate = root.certify(id=node_id, kind=kind, key=node.public, issued=int(time.time()),
                                            lifetime=cls.LIFETIME, metadata={})
            path = os.path.join(folder, name)
            node.save(path, [root.certificate])
            for owned in (path, os.path.join(path, Identity.NODE_FILE), os.path.join(path, Identity.ROOTS_FILE)):
                os.chown(owned, owner, owner)
            os.chmod(path, 0o700)


if __name__ == "__main__":
    Identities.make(sys.argv[1])
