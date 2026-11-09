"""What the node and enrollment tests share: a root, identity folders it certifies, and nodes on
this machine."""

import os
import time

import pytest

from communication.identity import Identity
from communication.node import Node


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Network:
    """A root, and identity folders certified by it."""

    def __init__(self, folder):
        self.folder = folder
        self.root = Identity.root("root-1")
        self._made = 0

    def path(self, name):
        """A new, empty folder."""
        self._made += 1
        return os.path.join(self.folder, f"{name}-{self._made}")

    def identity(self, node_id, kind, issued=None, lifetime=3600, root=None, authority=None):
        """The folder of a new node's identity, written now: certified by the root, or by an
        intermediate the root certified for ``authority`` (a list of kinds)."""
        path = self.path(node_id)
        node, signer = Identity(), root
        if authority is not None:
            signer = self.authority(authority)
            node.chain = [signer.certificate]
        self._write(path, node, node_id, kind, issued, lifetime, signer)
        return path

    def authority(self, kinds):
        """An intermediate the root certified, for those kinds."""
        signer = Identity()
        signer.certificate = self.root.certify_authority("ca-1", key=signer.public, kinds=kinds,
                                                         issued=int(time.time()))
        return signer

    def renew(self, folder, node_id, kind, lifetime=7200):
        """The same node, its certificate renewed in its folder."""
        self._write(folder, Identity(Identity.load(folder)[0].key), node_id, kind, None, lifetime, None)

    def _write(self, folder, node, node_id, kind, issued, lifetime, root):
        node.certificate = (root or self.root).certify(id=node_id, kind=kind, key=node.public,
                                                        issued=int(time.time()) if issued is None else issued,
                                                        lifetime=lifetime)
        node.save(folder, [self.root.certificate])


@pytest.fixture
def net(tmp_path):
    return Network(str(tmp_path))


@pytest.fixture
async def nodes(net):
    """Start nodes, all stopped at the end."""
    started = []

    async def start(node_id, kind, endpoints=None, folder=None, **options):
        node = Node(folder or net.identity(node_id, kind), endpoints, port=options.pop("port", 0),
                    host="127.0.0.1", **options)
        started.append(node)
        await node.start()
        return node

    yield start
    for node in started:
        await node.stop()


def at(node):
    return f"127.0.0.1:{node.port}"
