"""Execution nodes are admitted by authorities, never one at a time
(docs/system/execution-nodes.md, api/services/nodes/admission.py).

A node presents a short-lived credential an authority signed. It is
admitted when the authority is registered and not revoked, the
credential is unexpired and lives minutes, and its scope fits the
authority's â€” and not when it is refused by name. Thousands of nodes
need no step each; one revoke refuses them all at their next request.
"""

import time

import jwt
import pytest

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import (
    ADMIN_EMAIL, app_call, register_authority, runtime_service_token,
    service_keys)
from contracts.node import (
    HEADER_NODE, HEADER_REQUEST, HEADER_SIGNATURE, body_digest, mint)


def as_node(anon, token, endpoint="Agents:Package:List", data=None,
            request_id="req-1"):
    headers = {HEADER_REQUEST: request_id}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return anon.post("/app", json={"endpoint": endpoint, "data": data or {}},
                     headers=headers)


def install(admin, control, manifest_doc, url="https://example.test/notebook.git"):
    control["manifest"] = manifest_doc
    answer = app_call(admin, "Agents:Agent:Install", {"url": url})
    assert answer.status_code == 200, answer.text
    return answer.json()["data"]["agent"]


@pytest.fixture()
def authority(seed):
    private_pem, public_pem = service_keys()
    doc = register_authority("shared", public_pem)
    return private_pem, doc


class TestAdmission:
    def test_a_node_an_authority_vouches_for_is_admitted(self, anon, authority):
        private_pem, _ = authority
        answer = as_node(anon, runtime_service_token(private_pem, "node-1"))
        assert answer.status_code == 200, answer.text

    def test_a_node_with_no_credential_is_refused(self, anon, authority):
        assert as_node(anon, "").status_code == 401

    def test_an_expired_credential_is_refused(self, anon, authority):
        private_pem, _ = authority
        token = mint(private_pem, "node-1", seconds=60,
                     now=time.time() - 3600)
        assert as_node(anon, token).status_code == 401

    def test_a_credential_signed_by_an_unregistered_authority_is_refused(
            self, anon, authority):
        stranger, _ = service_keys()
        assert as_node(anon, runtime_service_token(stranger)).status_code == 401

    def test_a_credential_that_lives_hours_is_refused(self, anon, authority):
        private_pem, _ = authority
        token = mint(private_pem, "node-1", seconds=6 * 3600)
        assert as_node(anon, token).status_code == 401

    def test_a_token_that_is_not_for_the_data_core_is_refused(self, anon, authority):
        private_pem, _ = authority
        now = int(time.time())
        token = jwt.encode({"sub": "node-1", "aud": "somebody-else",
                            "iat": now, "exp": now + 300}, private_pem,
                           algorithm="RS256")
        assert as_node(anon, token).status_code == 401

    def test_revoking_the_authority_refuses_its_nodes_at_their_next_request(
            self, anon, admin, authority):
        private_pem, doc = authority
        token = runtime_service_token(private_pem, "node-1")
        assert as_node(anon, token).status_code == 200
        revoked = app_call(admin, "Nodes:Authority:Revoke",
                           {"authority_id": doc["_id"]})
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["revoked"] is True
        # The same credential, still unexpired: refused now.
        assert as_node(anon, token).status_code == 401

    def test_a_node_refused_by_name_is_refused_whatever_its_credential(
            self, anon, admin, authority):
        private_pem, doc = authority
        token = runtime_service_token(private_pem, "node-1")
        assert as_node(anon, token).status_code == 200
        key = f"{doc['_id']}/node-1"
        refused = app_call(admin, "Nodes:Node:Refuse", {"node_id": key})
        assert refused.status_code == 200, refused.text
        assert as_node(anon, token).status_code == 401
        # Another node of the same authority is not touched.
        assert as_node(anon, runtime_service_token(private_pem, "node-2")
                       ).status_code == 200
        allowed = app_call(admin, "Nodes:Node:Allow", {"node_id": key})
        assert allowed.json()["allowed"] is True
        assert as_node(anon, token).status_code == 200


class TestScope:
    def test_an_organizations_authority_cannot_admit_a_shared_node(
            self, anon, seed):
        private_pem, public_pem = service_keys()
        register_authority(seed.org["_id"], public_pem)
        shared = mint(private_pem, "node-1", organization="shared")
        assert as_node(anon, shared).status_code == 401

    def test_nor_a_node_of_another_organization(self, anon, seed):
        private_pem, public_pem = service_keys()
        register_authority(seed.org["_id"], public_pem)
        other = mint(private_pem, "node-1", organization="org_somebody_else")
        assert as_node(anon, other).status_code == 401

    def test_a_shared_authority_admits_shared_nodes_only(self, anon, authority):
        private_pem, _ = authority
        named = mint(private_pem, "node-1", organization="org_x")
        assert as_node(anon, named).status_code == 401

    def test_an_organizations_node_is_told_of_its_own_code_and_no_others(
            self, app, anon, admin, seed, control, manifest_doc):
        from test_multi_tenancy import Tenant

        install(admin, control, manifest_doc)
        private_pem, public_pem = service_keys()
        other = Tenant(app, "Other Org", "other@other.test", ["*"])
        register_authority(other.org["_id"], public_pem)
        token = mint(private_pem, "their-node")
        answer = as_node(anon, token)
        assert answer.status_code == 200, answer.text
        # The seeded organization approved a package; this node is the
        # other organization's, and is told of none.
        assert answer.json()["data"]["packages"] == []

        shared_private, shared_public = service_keys()
        register_authority("shared", shared_public)
        shared = as_node(anon, mint(shared_private, "shared-node"))
        assert len(shared.json()["data"]["packages"]) == 1


class TestTheReplyIsSigned:
    def test_a_nodes_answer_carries_the_data_cores_signature_over_its_bytes(
            self, anon, authority, signing_key_pair):
        private_pem, doc = authority
        _, data_core_public = signing_key_pair
        answer = as_node(anon, runtime_service_token(private_pem, "node-1"),
                         request_id="req-77")
        assert answer.status_code == 200
        claims = jwt.decode(answer.headers[HEADER_SIGNATURE], data_core_public,
                            algorithms=["RS256"], audience=f"{doc['_id']}/node-1",
                            issuer="decentai-data-core")
        assert claims["req"] == "req-77"
        assert claims["body"] == body_digest(answer.content)

    def test_a_refusal_to_a_node_is_signed_too(self, anon, authority,
                                                signing_key_pair):
        private_pem, _ = authority
        answer = as_node(anon, runtime_service_token(private_pem, "node-1"),
                         endpoint="IAM:User:List")
        assert answer.status_code == 403
        assert answer.headers.get(HEADER_SIGNATURE)

    def test_a_persons_answer_is_not(self, admin):
        answer = app_call(admin, "Account:Profile:Get", {})
        assert answer.status_code == 200
        assert HEADER_SIGNATURE not in answer.headers

    def test_a_delegation_presented_by_a_node_the_data_core_refuses_is_refused(
            self, anon, seed, signing_key_pair):
        from test_runtime_secret_use import runtime_headers

        stranger, _ = service_keys()
        headers = {**runtime_headers(seed, "chat_x"),
                   HEADER_NODE: runtime_service_token(stranger)}
        answer = anon.post("/app", json={"endpoint": "AI:State:Get",
                                         "data": {"chat_id": "chat_x"}},
                           headers=headers)
        assert answer.status_code == 401


class TestTheAuthorityDoors:
    def test_an_administrator_registers_an_authority_for_their_organization(
            self, admin, seed):
        _, public_pem = service_keys()
        made = app_call(admin, "Nodes:Authority:Create", {
            "name": "Our cluster", "public_key": public_pem})
        assert made.status_code == 200, made.text
        authority = made.json()["authority"]
        assert authority["scope"] == seed.org["_id"]
        assert authority["registered_by"]["email"] == ADMIN_EMAIL
        listed = app_call(admin, "Nodes:Authority:List", {}).json()["authorities"]
        assert [a["name"] for a in listed] == ["Our cluster"]

    def test_the_same_key_is_not_registered_twice(self, admin, seed):
        _, public_pem = service_keys()
        app_call(admin, "Nodes:Authority:Create",
                 {"name": "Ours", "public_key": public_pem})
        again = app_call(admin, "Nodes:Authority:Create",
                         {"name": "Ours again", "public_key": public_pem})
        assert again.status_code == 400

    def test_the_operator_registers_a_shared_one(self, admin, seed):
        _, public_pem = service_keys()
        made = app_call(admin, "Nodes:Authority:Create", {
            "name": "Shared pool", "public_key": public_pem, "scope": "shared"})
        assert made.status_code == 200, made.text
        assert made.json()["authority"]["scope"] == "shared"

    def test_an_administrator_of_another_organization_is_not_the_operator(
            self, app, seed):
        """They hold * in their own organization, and still may not
        vouch for nodes every organization's chats would run on."""
        from test_multi_tenancy import Tenant

        other = Tenant(app, "Other Org", "other@other.test", ["*"])
        _, public_pem = service_keys()
        refused = app_call(other.client, "Nodes:Authority:Create", {
            "name": "Ours for all", "public_key": public_pem, "scope": "shared"})
        assert refused.status_code == 403, refused.text
        mine = app_call(other.client, "Nodes:Authority:Create", {
            "name": "Ours", "public_key": public_pem})
        assert mine.status_code == 200, mine.text
        assert mine.json()["authority"]["scope"] == other.org["_id"]

    def test_nobody_revokes_another_organizations_authority(self, app, admin, seed):
        from test_multi_tenancy import Tenant

        other = Tenant(app, "Other Org", "other@other.test", ["*"])
        _, public_pem = service_keys()
        theirs = register_authority(other.org["_id"], public_pem)
        refused = app_call(admin, "Nodes:Authority:Revoke",
                           {"authority_id": theirs["_id"]})
        assert refused.status_code == 404

    def test_an_issuers_key_set_is_an_authority_too(self, anon, admin, seed,
                                                     monkeypatch):
        """A cluster's workload identity or a secret manager publishes
        its keys at an address; the Data Core reads them from there."""
        from cryptography.hazmat.primitives.asymmetric import rsa

        from api.services.nodes import admission

        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
        jwk.update(kid="k1", use="sig", alg="RS256")
        monkeypatch.setattr(admission.IssuerKeys, "_fetch", staticmethod(
            lambda url: jwt.PyJWKSet.from_dict({"keys": [jwk]})
            if url == "https://issuer.test/keys" else None))
        made = app_call(admin, "Nodes:Authority:Create", {
            "name": "Cluster identity", "jwks_url": "https://issuer.test/keys",
            "issuer": "https://issuer.test"})
        assert made.status_code == 200, made.text

        from cryptography.hazmat.primitives import serialization

        pem = private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        token = mint(pem, "pod-7", key_id="k1", issuer="https://issuer.test")
        assert as_node(anon, token).status_code == 200


class TestTheView:
    def test_the_nodes_admitted_are_kept_without_anybody_listing_them(
            self, anon, admin, seed):
        private_pem, public_pem = service_keys()
        doc = register_authority(seed.org["_id"], public_pem, name="Our racks")
        for name in ("rack-1", "rack-2"):
            assert as_node(anon, mint(private_pem, name, labels=["gpu"])
                           ).status_code == 200
        listed = app_call(admin, "Nodes:Node:List", {}).json()["nodes"]
        assert sorted(n["name"] for n in listed) == ["rack-1", "rack-2"]
        first = listed[0]
        assert first["authority_id"] == doc["_id"]
        assert first["authority"] == "Our racks"
        assert first["labels"] == ["gpu"]
        assert first["first_seen"] and first["last_seen"]

    def test_another_organizations_nodes_are_not_shown(self, app, anon, admin, seed):
        from test_multi_tenancy import Tenant

        other = Tenant(app, "Other Org", "other@other.test", ["*"])
        private_pem, public_pem = service_keys()
        register_authority(other.org["_id"], public_pem)
        assert as_node(anon, mint(private_pem, "theirs")).status_code == 200
        assert app_call(admin, "Nodes:Node:List", {}).json()["nodes"] == []


class TestTheInstall:
    def test_the_seeder_registers_the_installs_authority_once(
            self, seed, monkeypatch):
        from database.stores import AuthorityStore
        from init_db import register_install_authority

        _, public_pem = service_keys()
        monkeypatch.setenv("NODE_AUTHORITY_PUBLIC_KEY",
                           public_pem.replace("\n", "\\n"))
        register_install_authority()
        register_install_authority()
        shared = AuthorityStore().list("shared")
        assert len(shared) == 1 and shared[0]["name"] == "This install"

    def test_a_revoked_install_authority_stays_revoked_across_a_start(
            self, seed, monkeypatch):
        from database.stores import AuthorityStore
        from init_db import register_install_authority

        _, public_pem = service_keys()
        monkeypatch.setenv("NODE_AUTHORITY_PUBLIC_KEY", public_pem)
        register_install_authority()
        doc = AuthorityStore().list("shared")[0]
        AuthorityStore().revoke(doc["_id"], {"email": "operator"})
        register_install_authority()
        assert [a["revoked_at"] is not None
                for a in AuthorityStore().list("shared")] == [True]

    def test_the_installs_issuer_signs_what_the_data_core_admits(
            self, anon, seed, tmp_path, monkeypatch):
        """The whole of the one-command install's chain: the seeder
        registers the public half, the issuer signs with the private
        half and writes the file, the node presents what it reads."""
        from init_db import register_install_authority
        from node_issuer import Issuer

        private_pem, public_pem = service_keys()
        monkeypatch.setenv("NODE_AUTHORITY_PUBLIC_KEY", public_pem)
        register_install_authority()
        path = tmp_path / "node" / "credential"
        Issuer(private_pem.replace("\n", "\\n"), "bundled", path).write()
        token = path.read_text().strip()
        assert jwt.get_unverified_header(token)["kid"].startswith("sha256:")
        assert as_node(anon, token).status_code == 200
