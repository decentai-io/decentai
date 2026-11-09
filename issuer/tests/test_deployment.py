"""A whole deployment's trust, end to end, in the compose rig (compose.yml): the real issuer container,
its intermediate signed with the root tool, and nodes here enrolling through it over the network.
Skipped anywhere else."""

import asyncio
import os
import shutil
import time

import pytest

from communication.enrollment import Enrollment
from communication.identity import Identity
from communication.node import Node

ISSUER = os.environ.get("ISSUER_ADDRESS")
GIVEN = "/nodes"  # what the operator gives: roots.pem, and the first API server's token

pytestmark = [pytest.mark.anyio, pytest.mark.skipif(not ISSUER, reason="runs in the compose rig: compose.yml")]


class Echo:
    def say(self, data, peer):
        return {"from": peer["id"], "kind": peer["kind"]}, 200


class Deployment:
    """Nodes started here, each from an identity folder as an operator gives it."""

    def __init__(self, folder):
        self.folder, self.nodes = folder, []

    async def node(self, token, name):
        folder = os.path.join(self.folder, name)
        os.makedirs(folder)
        shutil.copy(os.path.join(GIVEN, Identity.ROOTS_FILE), folder)
        with open(os.path.join(folder, Enrollment.ENROLL_FILE), "w") as file:
            file.write(f"{ISSUER} issuer\n")
        with open(os.path.join(folder, Enrollment.TOKEN_FILE), "w") as file:
            file.write(token)
        node = Node(folder, {"Test": {"Echo": Echo()}}, port=0, host="127.0.0.1")
        self.nodes.append(node)
        await asyncio.wait_for(node.start(), 120)  # the first one waits for the issuer's intermediate
        return node

    async def issuer(self, node, endpoint, data=None):
        return await node.call(ISSUER, endpoint, data, kind="issuer")

    @staticmethod
    def certificate(node):
        return Identity.fields(Identity.load(node.identity_folder)[0].certificate)

    async def stop(self):
        for node in self.nodes:
            await node.stop()


@pytest.fixture
async def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(Enrollment, "RETRY_EVERY", 1)
    running = Deployment(str(tmp_path))
    yield running
    await running.stop()


async def test_a_deployment_from_the_root_to_two_nodes(deployment):
    with open(os.path.join(GIVEN, "first-token")) as file:
        first_token = file.read().strip()

    # An API server enrolls with the first token, through the issuer, whose kind TLS checks.
    api = await deployment.node(first_token, "api")
    assert deployment.certificate(api)["kind"] == "api"

    # It makes a token for a runtime, with a short lifetime, and the runtime enrolls with it.
    answer, status = await deployment.issuer(api, "Certificates:Token:create",
                                             {"kind": "runtime", "metadata": {"scope": "e2e"}, "lifetime": 8})
    assert status == 200
    runtime = await deployment.node(answer["token"], "runtime")
    first = deployment.certificate(runtime)
    assert first["kind"] == "runtime" and first["metadata"] == {"scope": "e2e"}

    # The two call each other, each checking whom it reached.
    assert await api.call(f"127.0.0.1:{runtime.port}", "Test:Echo:say", id=first["id"]) == \
        ({"from": deployment.certificate(api)["id"], "kind": "api"}, 200)
    assert (await runtime.call(f"127.0.0.1:{api.port}", "Test:Echo:say", kind="api"))[0]["kind"] == "runtime"

    # The runtime renews itself from half its lifetime on: same id, a new key.
    deadline = time.monotonic() + 20
    while deployment.certificate(runtime)["key"] == first["key"]:
        assert time.monotonic() < deadline, "the runtime never renewed"
        await asyncio.sleep(0.5)
    assert deployment.certificate(runtime)["id"] == first["id"]
    assert (await api.call(f"127.0.0.1:{runtime.port}", "Test:Echo:say", id=first["id"]))[1] == 200

    # The API server sees both nodes; the runtime may not look.
    listed, _ = await deployment.issuer(api, "Certificates:Node:list")
    assert {deployment.certificate(api)["id"], first["id"]} <= {node["_id"] for node in listed["nodes"]}
    assert (await deployment.issuer(runtime, "Certificates:Node:list"))[1] == 403

    # The token withdrawn, the runtime's renewals are refused.
    await deployment.issuer(api, "Certificates:Token:withdraw", {"id": answer["id"]})
    current, _ = Identity.load(runtime.identity_folder)
    refused, status = await deployment.issuer(runtime, Enrollment.RENEW, Enrollment.renewal(current, Identity()))
    assert status == 403 and "not renewed" in refused["messages"][0]["description"]
