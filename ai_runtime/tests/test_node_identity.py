"""This execution node's identity, and the Data Core's
(ai_runtime/door/node.py, docs/system/execution-nodes.md).

A node reads its credential from where its operator puts it and reads
it again before it lapses; it never holds an authority's key. And it
believes nothing the Data Core's key did not sign: a reply unsigned, or
signed for another node, another request or other bytes, is refused as
if nothing had answered.
"""

import json
import os
import time

import httpx
import jwt
import pytest

from ai_runtime.door.node import (
    CredentialError, DataCoreKey, NodeCredential, SignatureRefused)
from ai_runtime.door.platform import Gateway, GatewayError
from ai_runtime.tests import fake_container
from contracts.node import (
    DATA_CORE_ISSUER, HEADER_NODE, HEADER_REQUEST, HEADER_SIGNATURE,
    REPLY_TYPE, body_digest, mint)


def run(awaitable):
    return fake_container.run(awaitable)


def keys():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (
        private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode(),
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
    )


AUTHORITY = keys()
DATA_CORE = keys()


def signed_reply(node, request_id, body, private=DATA_CORE[0], **changes):
    now = int(time.time())
    claims = {"iss": DATA_CORE_ISSUER, "typ": REPLY_TYPE, "aud": node,
              "req": request_id, "body": body_digest(body),
              "iat": now, "exp": now + 60, **changes}
    return jwt.encode(claims, private, algorithm="RS256")


class TestTheCredential:
    def test_it_is_read_from_the_file_the_operator_mounts(self, tmp_path):
        path = tmp_path / "credential"
        path.write_text(mint(AUTHORITY[0], "node-a") + "\n")
        node = NodeCredential(file=str(path))
        token = run(node.token())
        assert jwt.decode(token, options={"verify_signature": False})["sub"] == "node-a"
        assert node.name == "node-a"

    def test_it_is_read_again_as_it_nears_its_end(self, tmp_path):
        path = tmp_path / "credential"
        path.write_text(mint(AUTHORITY[0], "node-a", seconds=60))
        node = NodeCredential(file=str(path))
        first = run(node.token())
        # A fresh one is written beside the old, with the same mtime
        # moment or not: the old one is inside its last two minutes.
        path.write_text(mint(AUTHORITY[0], "node-a", seconds=600))
        os.utime(path, (time.time() + 5, time.time() + 5))
        second = run(node.token())
        assert second != first

    def test_a_fresh_one_is_kept_until_it_must_be_read_again(self, tmp_path):
        path = tmp_path / "credential"
        path.write_text(mint(AUTHORITY[0], "node-a", seconds=600))
        node = NodeCredential(file=str(path))
        first = run(node.token())
        stat = path.stat()
        path.write_text(mint(AUTHORITY[0], "node-b", seconds=600))
        # The same moment on the file: nothing says it changed.
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert run(node.token()) == first

    def test_no_credential_is_said_and_never_made_up(self, tmp_path):
        node = NodeCredential(file=str(tmp_path / "nothing-here"))
        with pytest.raises(CredentialError, match="no credential"):
            run(node.token())

    def test_an_issuer_is_asked_when_one_is_named(self, monkeypatch):
        token = mint(AUTHORITY[0], "node-i")
        real = httpx.AsyncClient

        def client(*args, **kwargs):
            return real(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"credential": token})))

        monkeypatch.setattr(httpx, "AsyncClient", client)
        node = NodeCredential(issuer_url="http://issuer.local/credential")
        assert run(node.token()) == token


class TestTheDataCoresSignature:
    def test_a_reply_signed_for_this_node_and_this_request_is_believed(self):
        key = DataCoreKey(DATA_CORE[1])
        body = b'{"ok":true}'
        key.check_reply(signed_reply("auth_1/node-a", "r1", body),
                        "node-a", "r1", body)

    @pytest.mark.parametrize("why, signature, node, request_id, body", [
        ("not signed", "", "node-a", "r1", b"{}"),
        ("by another key", signed_reply("auth_1/node-a", "r1", b"{}",
                                        private=AUTHORITY[0]),
         "node-a", "r1", b"{}"),
        ("for another node", signed_reply("auth_1/node-b", "r1", b"{}"),
         "node-a", "r1", b"{}"),
        ("for another request", signed_reply("auth_1/node-a", "r0", b"{}"),
         "node-a", "r1", b"{}"),
        ("over other bytes", signed_reply("auth_1/node-a", "r1", b"{}"),
         "node-a", "r1", b'{"contract":"widened"}'),
    ])
    def test_anything_else_is_refused(self, why, signature, node, request_id, body):
        with pytest.raises(SignatureRefused):
            DataCoreKey(DATA_CORE[1]).check_reply(signature, node, request_id, body)

    def test_a_gateway_refuses_an_answer_the_data_core_did_not_sign(self):
        node = NodeCredential.fixed(mint(AUTHORITY[0], "node-a"))

        def answer(request):
            body = json.dumps({"status": "success", "request_id": "x",
                               "data": {"llm": "a model of the attacker's"}}).encode()
            return httpx.Response(200, content=body)

        gateway = Gateway("http://data-core.test", node=node,
                          key=DataCoreKey(DATA_CORE[1]))
        gateway._client = httpx.AsyncClient(
            base_url="http://data-core.test", transport=httpx.MockTransport(answer))
        with pytest.raises(GatewayError, match="not the Data Core's"):
            run(gateway.call("AI:Chat:Contract", {}, "a-delegation"))

    def test_every_request_carries_the_nodes_credential_and_its_own_id(self):
        token = mint(AUTHORITY[0], "node-a")
        node = NodeCredential.fixed(token)
        seen = {}

        def answer(request):
            seen.update(request.headers)
            body = b'{"answered":true}'
            return httpx.Response(200, content=body, headers={
                HEADER_SIGNATURE: signed_reply(
                    "auth_1/node-a", request.headers[HEADER_REQUEST], body)})

        gateway = Gateway("http://data-core.test", node=node,
                          key=DataCoreKey(DATA_CORE[1]))
        gateway._client = httpx.AsyncClient(
            base_url="http://data-core.test", transport=httpx.MockTransport(answer))
        assert run(gateway.call("AI:Chat:Contract", {}, "a-delegation")) == {
            "answered": True}
        assert seen[HEADER_NODE.lower()] == token
        assert seen["authorization"] == "Bearer a-delegation"


class TestTheSettings:
    NAMES = ("DATA_CORE_SERVICE_PUBLIC_KEY", "AI_RUNTIME_NODE_CREDENTIAL_FILE",
             "AI_RUNTIME_NODE_CREDENTIAL_URL", "DATA_CORE_INTERNAL_URL")

    def settings(self, monkeypatch, **said):
        from ai_runtime.door.settings import RuntimeSettings

        for name in self.NAMES:
            monkeypatch.delenv(name, raising=False)
        # The secret its Execution Sandboxes prove when they register:
        # required of every runtime since sandboxes register (Phase 6).
        monkeypatch.setenv("AI_RUNTIME_SANDBOX_SECRET", "the node's secret")
        for name, value in said.items():
            monkeypatch.setenv(name, value)
        return RuntimeSettings.from_env()

    def test_a_node_without_a_credential_does_not_start(self, monkeypatch):
        with pytest.raises(ValueError, match="AI_RUNTIME_NODE_CREDENTIAL_FILE"):
            self.settings(monkeypatch, DATA_CORE_SERVICE_PUBLIC_KEY="k",
                          DATA_CORE_INTERNAL_URL="http://dc:8000")

    def test_nor_one_that_cannot_tell_the_data_core(self, monkeypatch):
        with pytest.raises(ValueError, match="DATA_CORE_SERVICE_PUBLIC_KEY"):
            self.settings(monkeypatch, DATA_CORE_INTERNAL_URL="http://dc:8000",
                          AI_RUNTIME_NODE_CREDENTIAL_FILE="/run/c")

    def test_nor_one_that_cannot_find_it(self, monkeypatch):
        with pytest.raises(ValueError, match="DATA_CORE_INTERNAL_URL"):
            self.settings(monkeypatch, DATA_CORE_SERVICE_PUBLIC_KEY="k",
                          AI_RUNTIME_NODE_CREDENTIAL_FILE="/run/c")

    def test_the_data_core_and_the_credential_are_read(self, monkeypatch):
        read = self.settings(monkeypatch, DATA_CORE_SERVICE_PUBLIC_KEY="key",
                             DATA_CORE_INTERNAL_URL="http://data-core:8000",
                             AI_RUNTIME_NODE_CREDENTIAL_URL="http://issuer/")
        assert (read.data_core_service_public_key, read.data_core_url,
                read.node_credential_url) == (
            "key", "http://data-core:8000", "http://issuer/")
