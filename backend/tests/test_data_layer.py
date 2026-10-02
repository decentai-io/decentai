"""The data layer — one document format, one set of rules, three domains.

The shared behaviors (the owner filter, the write boundary, creator-only
editing and organization-scoped visibility) are written once and run
against both create-based domains — secrets and agent data. Files share
the same base but create by upload; their surface is covered in
test_files.py.

Secret values never appear in an API response. Authorized agent-data reads
receive decrypted values, while both domains keep only ciphertext at rest.
"""

import itertools

import pytest
from fastapi.testclient import TestClient

from conftest import app_call


class Domain:
    """Everything a shared test needs to know about one domain — including
    how to phrase a valid create/update, since secrets are definition-driven
    (definition_ref + name + flat fields) while records stay free-form
    (resource_id + keys + values)."""

    def __init__(self, gateway, prefix, store_name, labels):
        self.gateway = gateway            # "Secrets:Secret"
        self.prefix = prefix              # "secrets:secret"
        self.store_name = store_name      # a store exported by data_layer
        self.labels = labels              # two usable resource labels
        self.actions = [
            f"{prefix}:{action}"
            for action in ("list", "get", "create", "update", "delete")
        ]
        self.escape = f"{prefix}:set_owner_any"
        self._names = itertools.count(1)

    @property
    def is_secrets(self):
        return self.prefix == "secrets:secret"

    @property
    def store(self):
        import database.stores as stores
        return getattr(stores, self.store_name)()

    def call(self, client, action, payload=None):
        return app_call(client, f"{self.gateway}:{action}", payload)

    def create_payload(self, owner=None, label=None, keys=None, values=None):
        if self.is_secrets:
            from database.stores.data.definitions import DefinitionStore
            from database.stores import OrganizationStore
            definition = DefinitionStore().latest(
                OrganizationStore().get()["_id"],
                label if label is not None else self.labels[0],
            )
            payload = {
                "definition_ref": (definition or {}).get("_id", ""),
                "name": f"s-{next(self._names)}",
                "fields": {**(keys or {}), **(values or {})},
            }
        else:
            payload = {
                "resource_id": label if label is not None else self.labels[0],
                "keys": keys,
                "values": values,
            }
        if owner is not None:
            payload["owner"] = owner
        return payload

    def update_payload(self, ref, owner=None, keys=None, values=None):
        if self.is_secrets:
            payload = {
                "resource_ref": ref,
                "fields": {**(keys or {}), **(values or {})} or None,
            }
        else:
            payload = {"resource_ref": ref, "keys": keys, "values": values}
        if owner is not None:
            payload["owner"] = owner
        return payload


DOMAINS = [
    Domain("Secrets:Secret", "secrets:secret", "SecretStore",
           ("test_secret", "test_secret_b")),
    Domain("Data:Record", "data:record", "AgentDataStore",
           ("connection", "other_label")),
]


@pytest.fixture(params=DOMAINS, ids=lambda d: d.prefix)
def domain(request):
    return request.param


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _group(admin, name, actions):
    """A group whose members hold exactly ``actions``."""
    policy = app_call(admin, "IAM:Policy:Create", {
        "name": name, "permissions": {"statements": [
            {"effect": "Allow", "actions": actions}]},
    }).json()["policy"]
    role = app_call(admin, "IAM:Role:Create", {
        "role_name": name, "assigned_policies": [policy["policy_id"]],
    }).json()["role"]
    return app_call(admin, "IAM:Group:Create", {
        "group_name": name, "assigned_roles": [role["role_id"]],
    }).json()["group"]["group_id"]


def _user(app, seed, email, groups=None):
    """A signed-in client plus the stored user document."""
    from server.authentication.credentials import PasswordHasher
    from database.stores import UserStore

    doc = UserStore().create(seed.org["_id"], email, "U",
                             PasswordHasher.hash("MemberPass12"), groups or [])
    client = TestClient(app)
    assert client.post("/auth/login", json={
        "email": email, "password": "MemberPass12"}).status_code == 200
    return client, doc


def _principal(doc):
    """The user dict a controller or an in-process consumer is handed."""
    from database.stores import UserStore
    return UserStore().to_public(doc)


def _refs(response):
    return {r["resource_ref"] for r in response.json()["resources"]}


@pytest.fixture(autouse=True)
def _isolate_cipher():
    """Key material is process state — no test may leak it into the next."""
    from database.crypto import SecretCipher
    SecretCipher.reset()
    yield
    SecretCipher.reset()


# ----------------------------------------------------------------------
# Visibility — owner is an OR, enforced in the store
# ----------------------------------------------------------------------

class TestVisibility:
    def test_everyone_group_reaches_the_organization(self, app, admin, seed, domain):
        resource = domain.call(admin, "Create", domain.create_payload(
            owner={"groups": ["everyone"]},
            keys={"note": "for-all"},
            values={"payload": "org-wide"},
        )).json()["resource"]

        group = _group(admin, "Readers", domain.actions)
        member, _ = _user(app, seed, "member@test.org", [group])

        assert _refs(domain.call(member, "List")) == {resource["resource_ref"]}

    def test_a_group_document_reaches_only_that_group(self, app, admin, seed, domain):
        readers = _group(admin, "Readers", domain.actions)
        inner = _group(admin, "Inner", domain.actions)

        resource = domain.call(admin, "Create", domain.create_payload(
            owner={"groups": [inner]},
            values={"payload": "team"},
        )).json()["resource"]

        insider, _ = _user(app, seed, "insider@test.org", [readers, inner])
        outsider, _ = _user(app, seed, "outsider@test.org", [readers])

        assert _refs(domain.call(insider, "List")) == {resource["resource_ref"]}
        assert _refs(domain.call(outsider, "List")) == set()
        assert domain.call(outsider, "Get", {
            "resource_ref": resource["resource_ref"]}).status_code == 404

    def test_a_named_user_gets_in_without_the_group(self, app, admin, seed, domain):
        """Owner is an OR: naming a user is enough on its own. That is the
        administrator's decision, not an inconsistency."""
        readers = _group(admin, "Readers", domain.actions)
        inner = _group(admin, "Inner", domain.actions)
        named, named_doc = _user(app, seed, "named@test.org", [readers])

        resource = domain.call(admin, "Create", domain.create_payload(
            owner={"groups": [inner], "users": [named_doc["_id"]]},
            values={"payload": "shared"},
        )).json()["resource"]

        assert _refs(domain.call(named, "List")) == {resource["resource_ref"]}

    def test_an_administrator_does_not_see_what_was_not_shared(
            self, app, admin, seed, domain):
        """Owner is enforced for everyone — a wildcard policy reaches the
        API, not other people's documents."""
        readers = _group(admin, "Readers", domain.actions)
        member, member_doc = _user(app, seed, "member@test.org", [readers])

        resource = domain.call(member, "Create", domain.create_payload(
            owner={"users": [member_doc["_id"]]},
            values={"payload": "mine"},
        )).json()["resource"]

        assert _refs(domain.call(admin, "List")) == set()
        assert domain.call(admin, "Get", {
            "resource_ref": resource["resource_ref"]}).status_code == 404

    def test_the_label_filters_but_does_not_rank(self, admin, seed, domain):
        """Several documents under one label all come back — choosing among
        them is the consuming endpoint's job. Keys narrow exactly."""
        for name in ("sales", "ops"):
            domain.call(admin, "Create", domain.create_payload(
                owner={"groups": ["everyone"]},
                keys={"database": name},
                values={"payload": name},
            ))
        domain.call(admin, "Create", domain.create_payload(
            owner={"groups": ["everyone"]},
            label=domain.labels[1],
            values={"payload": "x"},
        ))

        listed = domain.call(admin, "List",
                             {"resource_id": domain.labels[0]}).json()["resources"]
        assert sorted(r["keys"]["database"] for r in listed) == ["ops", "sales"]

        narrowed = domain.call(admin, "List", {
            "resource_id": domain.labels[0], "keys": {"database": "ops"},
        }).json()["resources"]
        assert [r["keys"]["database"] for r in narrowed] == ["ops"]

    def test_an_unowned_create_is_private_to_the_caller(self, app, admin, seed, domain):
        """No owner in the payload: the document belongs to its creator."""
        readers = _group(admin, "Readers", domain.actions)
        member, member_doc = _user(app, seed, "member@test.org", [readers])
        other, _ = _user(app, seed, "other@test.org", [readers])

        resource = domain.call(member, "Create", domain.create_payload(
            values={"payload": "mine"},
        )).json()["resource"]
        assert resource["org_id"] == seed.org["_id"]
        assert set(resource["owner"]) == {"groups", "users"}
        assert resource["owner"]["users"] == [member_doc["_id"]]

        assert _refs(domain.call(member, "List")) == {resource["resource_ref"]}
        assert _refs(domain.call(other, "List")) == set()


# ----------------------------------------------------------------------
# Values follow each domain's read contract
# ----------------------------------------------------------------------

class TestValueResponses:
    def test_values_are_hidden_for_secrets_and_returned_for_data(
            self, admin, seed, domain):
        created = domain.call(admin, "Create", domain.create_payload(
            owner={"groups": ["everyone"]},
            keys={"note": "visible"},
            values={"payload": "hunter2-never-leaks"},
        ))
        ref = created.json()["resource"]["resource_ref"]

        fetched = domain.call(admin, "Get", {"resource_ref": ref})
        listed = domain.call(admin, "List")
        updated = domain.call(admin, "Update", domain.update_payload(
            ref, values={"payload": "rotated-never-leaks"}))

        responses = [created, fetched, listed, updated]
        assert all(response.status_code == 200 for response in responses)
        if domain.is_secrets:
            for response in responses:
                assert "values" not in response.text
                assert "hunter2" not in response.text
                assert "rotated" not in response.text
        else:
            assert created.json()["resource"]["values"] == {
                "payload": "hunter2-never-leaks"
            }
            assert fetched.json()["resource"]["values"] == {
                "payload": "hunter2-never-leaks"
            }
            assert listed.json()["resources"][0]["values"] == {
                "payload": "hunter2-never-leaks"
            }
            assert updated.json()["resource"]["values"] == {
                "payload": "rotated-never-leaks"
            }

    def test_record_values_may_be_structured_and_round_trip(self, admin, seed):
        """Records accept JSON objects/arrays as values — the cipher
        serializes the whole map, so structure survives encryption. Secrets
        stay text-only through their definition field types."""
        domain = DOMAINS[1]  # data:record
        content = {"text": "milk, eggs, bread", "tags": ["errand", "home"]}
        created = domain.call(admin, "Create", domain.create_payload(
            owner={"groups": ["everyone"]},
            keys={"title": "structured"},
            values={"content": content, "steps": [1, 2, 3], "plain": "text"},
        ))
        assert created.status_code == 200, created.text
        ref = created.json()["resource"]["resource_ref"]

        fetched = domain.call(admin, "Get", {"resource_ref": ref})
        assert fetched.json()["resource"]["values"] == {
            "content": content, "steps": [1, 2, 3], "plain": "text",
        }

    def test_scalar_values_keep_their_json_type(self, admin, seed):
        """A number stays a number and a boolean stays a boolean — storing
        them as text handed consumers "5" and "False"."""
        domain = DOMAINS[1]  # data:record
        values = {
            "count": 5,
            "ratio": 0.5,
            "done": False,
            "also_done": True,
            "text": "5",
        }
        created = domain.call(admin, "Create", domain.create_payload(
            owner={"groups": ["everyone"]},
            keys={"title": "typed"},
            values=values,
        ))
        assert created.status_code == 200, created.text

        stored = domain.call(
            admin, "Get",
            {"resource_ref": created.json()["resource"]["resource_ref"]},
        ).json()["resource"]["values"]
        assert stored == values
        assert stored["done"] is False and stored["also_done"] is True
        assert isinstance(stored["count"], int)
        assert isinstance(stored["text"], str)

    def test_a_secrets_typed_fields_survive_as_typed(self, admin, seed):
        """A definition validates a number as a number and a boolean as a
        boolean; the store must not flatten them into the ciphertext, or the
        runtime receives "5432" and "False"."""
        from database.stores.data.definitions import DefinitionStore
        from database.stores.data.secrets import SecretStore

        definition = DefinitionStore().create_family(
            {"user_id": "", "org_id": seed.org["_id"]},
            "typed_connection", "Typed connection", "",
            [
                {"name": "host", "type": "string", "storage": "keys"},
                {"name": "port", "type": "number", "storage": "values"},
                {"name": "verify_tls", "type": "boolean",
                 "storage": "values"},
            ],
        )

        created = app_call(admin, "Secrets:Secret:create", {
            "definition_ref": definition["_id"],
            "name": "Typed instance",
            "fields": {"host": "db.local", "port": 5432, "verify_tls": False},
        })
        assert created.status_code == 200, created.text

        # Secrets never return values over the API — read them the way an
        # in-process consumer does.
        values = SecretStore().use(
            _principal(seed.admin),
            created.json()["resource"]["resource_ref"],
        )
        assert values == {"port": 5432, "verify_tls": False}
        assert values["verify_tls"] is False

    def test_an_oversized_structured_value_is_refused(self, admin, seed):
        from database.stores.data.resources import ResourceStore

        domain = DOMAINS[1]
        response = domain.call(admin, "Create", domain.create_payload(
            owner={"groups": ["everyone"]},
            values={"content": {"blob": "x" * (ResourceStore.MAX_VALUE_LENGTH + 1)}},
        ))
        assert response.status_code == 400
        assert "characters or fewer" in response.json()["error"]

    def test_a_large_structured_value_is_kept(self, admin, seed):
        """A browser's saved sign-in for a large site is a JSON object
        well past the old 8 KB: a value holds it."""
        domain = DOMAINS[1]
        response = domain.call(admin, "Create", domain.create_payload(
            owner={"groups": ["everyone"]},
            values={"content": {"cookies": [{"name": f"c{i}", "value": "v" * 200}
                                            for i in range(600)]}},
        ))
        assert response.status_code == 200, response.text

    def test_the_stored_document_holds_only_ciphertext(self, admin, seed, domain):
        ref = domain.call(admin, "Create", domain.create_payload(
            owner={"groups": ["everyone"]},
            values={"payload": "hunter2-never-leaks"},
        )).json()["resource"]["resource_ref"]

        from server.setup.app_state import get_db
        doc = get_db().collection(domain.store.COLLECTION).find_one({"_id": ref})

        assert "hunter2" not in str(doc)
        assert doc["values"]["alg"] == "aes-256-gcm"
        assert doc["values"]["key_version"]


# ----------------------------------------------------------------------
# use() — the only path to plaintext, and no route reaches it
# ----------------------------------------------------------------------

class TestUse:
    def test_it_decrypts_for_a_consumer_and_respects_visibility(
            self, app, admin, seed, domain):
        readers = _group(admin, "Readers", domain.actions)
        _, member_doc = _user(app, seed, "member@test.org", [readers])
        _, stranger_doc = _user(app, seed, "stranger@test.org", [readers])

        ref = domain.call(admin, "Create", domain.create_payload(
            owner={"users": [member_doc["_id"]]},
            values={"left": "a", "right": "b"},
        )).json()["resource"]["resource_ref"]

        store = domain.store
        assert store.use(_principal(member_doc), ref) == {"left": "a", "right": "b"}

        with pytest.raises(ValueError):
            store.use(_principal(stranger_doc), ref)

    def test_it_is_not_dispatchable(self, admin, seed, domain):
        """The gateway must not be able to reach it even by name."""
        response = domain.call(admin, "Use", {"resource_ref": "x"})
        assert response.status_code != 200


# ----------------------------------------------------------------------
# The write boundary
# ----------------------------------------------------------------------

class TestOwnerBoundary:
    def test_org_id_is_backend_controlled(self, admin, seed, domain):
        create = domain.create_payload()
        create["org_id"] = "caller-org"
        assert domain.call(admin, "Create", create).status_code == 400

        resource = domain.call(admin, "Create", domain.create_payload()).json()["resource"]
        update = domain.update_payload(resource["resource_ref"], keys={})
        update["org_id"] = "caller-org"
        assert domain.call(admin, "Update", update).status_code == 400

    def test_data_type_and_resource_id_are_immutable(self, admin, seed, domain):
        if domain.is_secrets:
            pytest.skip("Secret identity is definition-driven.")

        create = domain.create_payload()
        create["type"] = "secret"
        assert domain.call(admin, "Create", create).status_code == 400

        resource = domain.call(admin, "Create", domain.create_payload()).json()["resource"]
        for field, value in (("type", "secret"), ("resource_id", "replacement")):
            update = domain.update_payload(resource["resource_ref"], keys={})
            update[field] = value
            assert domain.call(admin, "Update", update).status_code == 400

    def test_a_member_may_share_only_within_their_own_reach(
            self, app, admin, seed, domain):
        """Themselves, their groups, and their PEERS — people sharing an
        explicit group with them. Nothing wider without the escape."""
        readers = _group(admin, "Readers", domain.actions)
        other = _group(admin, "Other", domain.actions)
        member, member_doc = _user(app, seed, "member@test.org", [readers])
        peer, peer_doc = _user(app, seed, "peer@test.org", [readers])

        assert domain.call(member, "Create", domain.create_payload(
            owner={"users": [member_doc["_id"]]})).status_code == 200
        assert domain.call(member, "Create", domain.create_payload(
            owner={"groups": [readers]})).status_code == 200
        # A peer — same explicit group — is within reach by name.
        assert domain.call(member, "Create", domain.create_payload(
            owner={"users": [peer_doc["_id"]]})).status_code == 200

        # …and nothing wider: a foreign group, the whole org, or a
        # person who shares no group with the writer.
        for owner in ({"groups": [other]},
                      {"groups": ["everyone"]},
                      {"users": [seed.admin["_id"]]}):
            response = domain.call(member, "Create",
                                   domain.create_payload(owner=owner))
            assert response.status_code == 403, owner
            assert response.json()["ungrantable"]

    def test_the_escape_action_lifts_it(self, app, admin, seed, domain):
        wide = _group(admin, "Escapees", domain.actions + [domain.escape])
        member, _ = _user(app, seed, "member@test.org", [wide])

        assert domain.call(member, "Create", domain.create_payload(
            owner={"groups": ["everyone"]})).status_code == 200

    def test_it_applies_to_updates_too(self, app, admin, seed, domain):
        readers = _group(admin, "Readers", domain.actions)
        member, member_doc = _user(app, seed, "member@test.org", [readers])

        ref = domain.call(member, "Create", domain.create_payload(
            owner={"users": [member_doc["_id"]]},
        )).json()["resource"]["resource_ref"]

        assert domain.call(member, "Update", domain.update_payload(
            ref, owner={"groups": ["everyone"]})).status_code == 403

    def test_an_owner_naming_nobody_is_rejected(self, admin, seed, domain):
        response = domain.call(admin, "Create", domain.create_payload(
            owner={"groups": [], "users": []}))
        assert response.status_code == 400

    def test_a_document_needs_a_label(self, admin, seed, domain):
        """Records need a resource_id; secrets need a known definition."""
        response = domain.call(admin, "Create", domain.create_payload(
            label="", owner={"groups": ["everyone"]}))
        assert response.status_code in (400, 404)


# ----------------------------------------------------------------------
# Creator-only editing
# ----------------------------------------------------------------------

class TestCreatorOnlyEditing:
    def test_seeing_a_document_is_not_authority_over_it(self, app, admin, seed, domain):
        readers = _group(admin, "Readers", domain.actions)
        owner_client, _ = _user(app, seed, "owner@test.org", [readers])
        other, _ = _user(app, seed, "other@test.org", [readers])

        ref = domain.call(owner_client, "Create", domain.create_payload(
            owner={"groups": [readers]},
        )).json()["resource"]["resource_ref"]

        # The other member can see it…
        assert _refs(domain.call(other, "List")) == {ref}
        # …and can do nothing to it.
        assert domain.call(other, "Update", domain.update_payload(
            ref, keys={"note": "hijack"})).status_code == 403
        assert domain.call(other, "Delete",
                           {"resource_ref": ref}).status_code == 403

        # The creator can.
        assert domain.call(owner_client, "Update", domain.update_payload(
            ref, keys={"note": "fine"})).status_code == 200
        assert domain.call(owner_client, "Delete",
                           {"resource_ref": ref}).status_code == 200

    def test_the_escape_action_lifts_it(self, app, admin, seed, domain):
        readers = _group(admin, "Readers", domain.actions)
        wide = _group(admin, "Escapees", domain.actions + [domain.escape])
        owner_client, _ = _user(app, seed, "owner@test.org", [readers])
        maintainer, _ = _user(app, seed, "maintainer@test.org", [readers, wide])

        ref = domain.call(owner_client, "Create", domain.create_payload(
            owner={"groups": [readers]},
        )).json()["resource"]["resource_ref"]

        assert domain.call(maintainer, "Update", domain.update_payload(
            ref, keys={"note": "maintained"})).status_code == 200


# ----------------------------------------------------------------------
# Domains are separate grants
# ----------------------------------------------------------------------

class TestDomainSeparation:
    def test_holding_one_domain_grants_nothing_in_another(self, app, admin, seed):
        secrets_only = _group(admin, "SecretsOnly", DOMAINS[0].actions)
        member, _ = _user(app, seed, "member@test.org", [secrets_only])

        assert DOMAINS[0].call(member, "List").status_code == 200
        assert DOMAINS[1].call(member, "List").status_code == 403


# ----------------------------------------------------------------------
# The cipher
# ----------------------------------------------------------------------

class TestCipher:
    def test_round_trip(self):
        from database.crypto import SecretCipher

        blob = SecretCipher.encrypt({"password": "s3cret"}, "doc-a")
        assert SecretCipher.decrypt(blob, "doc-a") == {"password": "s3cret"}

    def test_a_blob_cannot_be_moved_to_another_document(self):
        """Lift the ciphertext out of a document you administer, paste it
        into one you own, and it is inert."""
        from database.crypto import SecretCipher, SecretCipherError

        blob = SecretCipher.encrypt({"password": "s3cret"}, "doc-a")
        with pytest.raises(SecretCipherError):
            SecretCipher.decrypt(blob, "doc-b")

    def test_a_tampered_value_does_not_decrypt(self):
        from database.crypto import SecretCipher, SecretCipherError

        blob = SecretCipher.encrypt({"password": "s3cret"}, "doc-a")
        # Changed to something the first character is NOT: overwriting it
        # with a fixed "A" left the ciphertext untouched whenever it
        # already began with one, and the test then failed for having
        # tampered with nothing — about one run in sixty-four.
        first = blob["ciphertext"][0]
        blob["ciphertext"] = ("B" if first == "A" else "A") + blob["ciphertext"][1:]
        with pytest.raises(SecretCipherError):
            SecretCipher.decrypt(blob, "doc-a")

    def test_keys_rotate(self, monkeypatch):
        from database.crypto import SecretCipher, SecretCipherError

        first, second = SecretCipher.new_key(), SecretCipher.new_key()

        monkeypatch.setenv("SECRET_ENCRYPTION_KEYS", f"1:{first}")
        monkeypatch.setenv("SECRET_ENCRYPTION_ACTIVE", "1")
        SecretCipher.reset()
        old = SecretCipher.encrypt({"password": "s3cret"}, "doc-a")
        assert old["key_version"] == "1"

        # Both configured: new writes use the new key, old blobs still read.
        monkeypatch.setenv("SECRET_ENCRYPTION_KEYS", f"1:{first},2:{second}")
        monkeypatch.setenv("SECRET_ENCRYPTION_ACTIVE", "2")
        SecretCipher.reset()
        assert SecretCipher.decrypt(old, "doc-a") == {"password": "s3cret"}
        assert SecretCipher.encrypt({"a": "b"}, "doc-a")["key_version"] == "2"

        # Retire the old key and the old blob fails loudly, not silently.
        monkeypatch.setenv("SECRET_ENCRYPTION_KEYS", f"2:{second}")
        SecretCipher.reset()
        with pytest.raises(SecretCipherError):
            SecretCipher.decrypt(old, "doc-a")

    def test_there_is_no_plaintext_fallback(self):
        from database.crypto import SecretCipher, SecretCipherError

        for blob in ({"password": "plain"}, {}, "enc:v1:legacy", None):
            with pytest.raises(SecretCipherError):
                SecretCipher.decrypt(blob, "doc-a")

    def test_a_misconfigured_key_is_refused(self, monkeypatch):
        from database.crypto import SecretCipher, SecretCipherError

        monkeypatch.setenv("SECRET_ENCRYPTION_KEYS", "1:tooshort")
        SecretCipher.reset()
        with pytest.raises(SecretCipherError):
            SecretCipher.encrypt({"a": "b"}, "doc-a")

        monkeypatch.setenv("SECRET_ENCRYPTION_KEYS", f"1:{SecretCipher.new_key()}")
        monkeypatch.setenv("SECRET_ENCRYPTION_ACTIVE", "9")
        SecretCipher.reset()
        with pytest.raises(SecretCipherError):
            SecretCipher.encrypt({"a": "b"}, "doc-a")


# ----------------------------------------------------------------------
# An agent's update is partial at the field level
# ----------------------------------------------------------------------

def _runtime_headers(seed, chat_id="chat_records"):
    from api.services.chat_session.identity import Delegation

    token = Delegation().for_chat(
        {"user_id": seed.admin["_id"], "org_id": seed.org["_id"],
         "email": seed.admin["email"], "session_id": ""},
        chat_id,
    )
    return {"Authorization": f"Bearer {token}"}


class TestRuntimeUpdatesMerge:
    """The simulator agents are tested against merges an update field by
    field, and every agent writes that way: mark a proposal confirmed,
    move a request to comparing. Production must keep the fields the
    agent did not send — keys and values both."""

    def test_keys_the_agent_did_not_send_survive(self, anon, admin, seed):
        domain = DOMAINS[1]  # data:record
        created = domain.call(admin, "Create", domain.create_payload(
            keys={"title": "Send the quotation", "kind": "commitment",
                  "quote": "Dana will send it by Thursday", "status": "proposed"},
            values={"notes": "from the ops meeting", "attempts": 1},
        ))
        assert created.status_code == 200, created.text
        ref = created.json()["resource"]["resource_ref"]

        updated = anon.post("/app", json={
            "endpoint": "Data:Record:Update",
            "data": {"resource_ref": ref,
                     "keys": {"status": "confirmed", "task_ref": "t1"}},
        }, headers=_runtime_headers(seed))
        assert updated.status_code == 200, updated.text

        stored = domain.call(admin, "Get", {"resource_ref": ref}).json()["resource"]
        assert stored["keys"] == {
            "title": "Send the quotation", "kind": "commitment",
            "quote": "Dana will send it by Thursday",
            "status": "confirmed", "task_ref": "t1",
        }
        assert stored["values"] == {"notes": "from the ops meeting", "attempts": 1}

    def test_values_merge_over_the_decrypted_blob(self, anon, admin, seed):
        domain = DOMAINS[1]
        created = domain.call(admin, "Create", domain.create_payload(
            keys={"title": "note"},
            values={"content": {"text": "first"}, "pinned": True},
        ))
        ref = created.json()["resource"]["resource_ref"]

        updated = anon.post("/app", json={
            "endpoint": "Data:Record:Update",
            "data": {"resource_ref": ref, "values": {"content": {"text": "second"}}},
        }, headers=_runtime_headers(seed))
        assert updated.status_code == 200, updated.text

        stored = domain.call(admin, "Get", {"resource_ref": ref}).json()["resource"]
        assert stored["keys"] == {"title": "note"}
        assert stored["values"] == {"content": {"text": "second"}, "pinned": True}

    def test_an_unknown_record_is_not_found(self, anon, seed):
        response = anon.post("/app", json={
            "endpoint": "Data:Record:Update",
            "data": {"resource_ref": "nope", "keys": {"status": "x"}},
        }, headers=_runtime_headers(seed))
        assert response.status_code == 404


class TestAnAgentStaysInItsSlot:
    """An agent's call by reference names its slot, and a record kept
    under any other category answers as one that does not exist — the
    person's own notes, another agent's records. The assistant's own
    reads name no slot and see what the person sees."""

    def test_a_record_of_another_category_is_not_there(self, anon, admin, seed):
        domain = DOMAINS[1]  # data:record
        created = domain.call(admin, "Create", domain.create_payload(
            keys={"title": "the person's own"}))
        assert created.status_code == 200, created.text
        record = created.json()["resource"]
        ref, category = record["resource_ref"], record["resource_id"]

        def call(endpoint, slot, **extra):
            return anon.post("/app", json={
                "endpoint": endpoint,
                "data": {"resource_ref": ref, "slot": slot, **extra},
            }, headers=_runtime_headers(seed))

        elsewhere = "agt_somebody__notes"
        assert call("Data:Record:Get", elsewhere).status_code == 404
        assert call("Data:Record:Update", elsewhere,
                    keys={"title": "rewritten"}).status_code == 404
        assert call("Data:Record:Delete", elsewhere).status_code == 404
        assert call("Data:Record:Get", category).status_code == 200

        # Without a slot, the assistant reads it as the person would.
        unnamed = anon.post("/app", json={
            "endpoint": "Data:Record:Get", "data": {"resource_ref": ref},
        }, headers=_runtime_headers(seed))
        assert unnamed.status_code == 200
