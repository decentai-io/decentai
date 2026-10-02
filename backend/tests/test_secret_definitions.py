"""Secret definitions — versioning, enforced validation, name uniqueness,
and the values merge on partial updates.

A definition is derived — from an installed agent's manifest, or a
login an agent asked for — and never written through the gateway, so
these tests make theirs the way an install does (conftest.define_secret).
"""

import pytest
from fastapi.testclient import TestClient

from conftest import app_call, define_secret, version_secret

SECRET_ACTIONS = [
    "secrets:secret:list", "secrets:secret:get", "secrets:secret:create",
    "secrets:secret:update", "secrets:secret:delete",
]

DEF_FIELDS_V1 = [
    {"name": "host", "label": "Host", "type": "string", "required": True,
     "storage": "keys"},
    {"name": "username", "label": "Username", "type": "string",
     "required": True, "storage": "values"},
    {"name": "password", "label": "Password", "type": "secret",
     "required": True},
]


def _make_def(admin, slug="conn", fields=None, label="Conn"):
    return define_secret(slug, label, fields or DEF_FIELDS_V1)


def _publish(fields, slug="conn", label="Conn"):
    """The family's next version, as an agent update would publish it."""
    return version_secret(slug, label, fields)


def _make_secret(client, slug="conn", name="mine", fields=None, owner=None):
    from database.stores.data.definitions import DefinitionStore
    from database.stores import OrganizationStore

    definition = DefinitionStore().latest(
        OrganizationStore().get()["_id"], slug)
    payload = {"definition_ref": definition["_id"], "name": name, "fields": fields or {
        "host": "db.internal", "username": "readonly-analyst",
        "password": "s3cret"}}
    if owner is not None:
        payload["owner"] = owner
    return app_call(client, "Secrets:Secret:Create", payload)


def _user(app, seed, email, groups=None):
    from server.authentication.credentials import PasswordHasher
    from database.stores import UserStore

    doc = UserStore().create(seed.org["_id"], email, "U",
                             PasswordHasher.hash("MemberPass12"), groups or [])
    client = TestClient(app)
    assert client.post("/auth/login", json={
        "email": email, "password": "MemberPass12"}).status_code == 200
    return client, doc


@pytest.fixture(autouse=True)
def _isolate_cipher():
    from database.crypto import SecretCipher
    SecretCipher.reset()
    yield
    SecretCipher.reset()


class TestVersioning:
    def test_identical_content_is_rejected(self, admin, seed):
        _make_def(admin)
        with pytest.raises(ValueError, match="Nothing changed"):
            _publish(DEF_FIELDS_V1)

    def test_a_change_creates_an_immutable_new_version(self, admin, seed):
        v1 = _make_def(admin)
        assert v1["version"] == 1
        assert v1["definition_ref"].endswith("conn/v1")

        v2_fields = DEF_FIELDS_V1 + [
            {"name": "port", "label": "Port", "type": "number",
             "required": False, "storage": "keys"}]
        v2 = _publish(v2_fields)
        assert v2["version"] == 2
        assert v2["definition_ref"].endswith("conn/v2")
        assert v2["definition_ref"] != v1["definition_ref"]

        # v1 still exists, untouched.
        old = app_call(admin, "Secrets:Definition:Get", {
            "definition_ref": v1["definition_ref"]}).json()["definition"]
        assert old["version"] == 1
        assert len(old["fields"]) == len(DEF_FIELDS_V1)

        # New instances pin the latest.
        secret = _make_secret(admin).json()["resource"]
        assert secret["definition_version"] == 2
        assert secret["definition_ref"] == v2["definition_ref"]

    def test_updates_validate_against_the_recorded_version(self, admin, seed):
        """A later version adding a required field must not break updates
        to instances created before it."""
        _make_def(admin)
        secret = _make_secret(admin).json()["resource"]

        _publish(DEF_FIELDS_V1 + [
            {"name": "region", "label": "Region", "type": "string",
             "required": True, "storage": "keys"}])

        # Rotating the password on the v1 instance still works.
        response = app_call(admin, "Secrets:Secret:Update", {
            "resource_ref": secret["resource_ref"],
            "fields": {"password": "rotated"}})
        assert response.status_code == 200
        # …and a v1 instance rejects the v2-only field.
        response = app_call(admin, "Secrets:Secret:Update", {
            "resource_ref": secret["resource_ref"],
            "fields": {"region": "eu"}})
        assert response.status_code == 400


class TestWhoOwnsTheShape:
    def test_a_definition_belongs_to_an_organization(self, admin, seed):
        """There is no deployment-wide shelf: one with no organization
        cannot be made."""
        from database.stores.data.definitions import DefinitionStore

        with pytest.raises(ValueError, match="belongs to an organization"):
            DefinitionStore().create_family(
                {"user_id": ""}, "orphan_shape", "Orphan", "", DEF_FIELDS_V1,
            )


class TestPruning:
    """Versions nothing stands on are deleted the moment they can be —
    they accumulate one per agent update otherwise, forever. A secret
    protects its version; an installed agent's pin protects its."""

    def test_deleting_the_last_secret_takes_the_family_with_it(
            self, admin, seed):
        _make_def(admin)
        secret = _make_secret(admin).json()["resource"]

        assert app_call(admin, "Secrets:Secret:Delete", {
            "resource_ref": secret["resource_ref"]}).status_code == 200

        listed = app_call(admin, "Secrets:Definition:List") \
            .json()["definitions"]
        assert "conn" not in [d["definition_id"] for d in listed]

    def test_an_occupied_version_survives_and_the_empty_one_goes(
            self, admin, seed):
        from database.stores.data.definitions import DefinitionStore

        _make_def(admin)
        _make_secret(admin, name="on-v1")
        _publish(DEF_FIELDS_V1 + [
                {"name": "port", "label": "Port", "type": "number",
                 "required": False, "storage": "keys"}])
        second = _make_secret(admin, name="on-v2", fields={
            "host": "db", "username": "u", "password": "p", "port": 1,
        }).json()["resource"]

        # Deleting the v2 secret leaves v2 empty and unpinned — it goes;
        # v1 keeps its secret and stays, becoming current again.
        assert app_call(admin, "Secrets:Secret:Delete", {
            "resource_ref": second["resource_ref"]}).status_code == 200

        versions = [d["version"] for d in DefinitionStore().versions(
            seed.org["_id"], "conn")]
        assert versions == [1]

    def test_a_pinned_version_is_not_pruned(self, admin, seed):
        from database.stores.data.definitions import DefinitionStore
        from database.stores import AgentManifestStore

        definition = _make_def(admin)
        AgentManifestStore().upsert(
            seed.org["_id"], "mail_agent", "1.0.0",
            {"agent": {"id": "mail_agent"}},
            {"secrets": {"connection": definition["definition_ref"]}},
            "admin@test.org",
        )

        assert DefinitionStore().prune_unused(seed.org["_id"], "conn") == 0
        assert DefinitionStore().latest(seed.org["_id"], "conn") is not None

        # The pin gone, the empty family goes with the next prune —
        # which is exactly what an uninstall triggers.
        AgentManifestStore().delete("mail_agent")
        assert DefinitionStore().prune_unused(seed.org["_id"], "conn") == 1
        assert DefinitionStore().latest(seed.org["_id"], "conn") is None


class TestMigration:
    """A secret keeps the version it was created under — until its
    creator deliberately moves it onto the family's current shape.
    Stored fields carry over where the new shape knows them, dropped
    fields go, and a newly required field must arrive with the request."""

    V2_FIELDS = [
        {"name": "host", "label": "Host", "type": "string",
         "required": True, "storage": "keys"},
        # username is gone in v2; region is new and required.
        {"name": "region", "label": "Region", "type": "string",
         "required": True, "storage": "keys"},
        {"name": "password", "label": "Password", "type": "secret",
         "required": True},
    ]

    def _publish_v2(self, admin):
        _publish(self.V2_FIELDS)

    def test_migrate_carries_kept_fields_and_demands_new_ones(
            self, admin, seed):
        from database.stores.data.secrets import SecretStore
        from database.stores import UserStore

        _make_def(admin)
        secret = _make_secret(admin).json()["resource"]
        self._publish_v2(admin)

        # The new shape requires region; migrating without it is refused
        # before anything moves.
        refused = app_call(admin, "Secrets:Secret:Update", {
            "resource_ref": secret["resource_ref"], "migrate": True,
            "fields": {},
        })
        assert refused.status_code == 400
        assert "Region" in refused.json()["error"]

        moved = app_call(admin, "Secrets:Secret:Update", {
            "resource_ref": secret["resource_ref"], "migrate": True,
            "fields": {"region": "eu-west"},
        })
        assert moved.status_code == 200, moved.text
        resource = moved.json()["resource"]
        assert resource["definition_version"] == 2
        # Kept field carried, dropped field gone, new field landed.
        assert resource["keys"]["host"] == "db.internal"
        assert "username" not in resource["keys"]
        assert resource["keys"]["region"] == "eu-west"

        # The encrypted half crossed without being retyped.
        values = SecretStore().use(
            UserStore.to_public(seed.admin), resource["resource_ref"])
        assert values["password"] == "s3cret"

    def test_migrate_on_a_current_secret_is_an_ordinary_update(
            self, admin, seed):
        _make_def(admin)
        secret = _make_secret(admin).json()["resource"]

        response = app_call(admin, "Secrets:Secret:Update", {
            "resource_ref": secret["resource_ref"], "migrate": True,
            "fields": {"host": "db.other"},
        })
        assert response.status_code == 200, response.text
        assert response.json()["resource"]["definition_version"] == 1
        assert response.json()["resource"]["keys"]["host"] == "db.other"


class TestFollowingAnUpdate:
    """An agent's update may change the shape it reads. A credential
    saved before it follows by itself where the new shape asks nothing
    new of its owner, and is not used half-filled where it does."""

    #: v2 for a shape that only loses a field and gains an optional one.
    EASIER = [
        {"name": "host", "label": "Host", "type": "string",
         "required": True, "storage": "keys"},
        {"name": "note", "label": "Note", "type": "string",
         "required": False, "storage": "keys"},
        {"name": "password", "label": "Password", "type": "secret",
         "required": True},
    ]

    def test_a_credential_follows_when_nothing_new_is_asked(self, admin, seed):
        from database.stores import UserStore
        from database.stores.data.definitions import DefinitionStore
        from database.stores.data.secrets import SecretStore

        org = seed.org["_id"]
        _make_def(admin)
        ref = _make_secret(admin).json()["resource"]["resource_ref"]
        _publish(self.EASIER)

        target = DefinitionStore().latest(org, "conn")
        assert SecretStore().move_forward(org, target) == (1, 0)
        moved = app_call(admin, "Secrets:Secret:Get",
                         {"resource_ref": ref}).json()["resource"]
        assert moved["definition_version"] == 2
        assert moved["keys"]["host"] == "db.internal"
        values = SecretStore().use(UserStore.to_public(seed.admin), ref)
        assert values == {"password": "s3cret"}      # username went with v1
        # Nothing stands on the old version any more, so it goes.
        assert DefinitionStore().prune_unused(org, "conn") == 1
        # Asked again, there is nothing left to move.
        assert SecretStore().move_forward(org, target) == (0, 0)

    def test_a_credential_the_new_shape_asks_more_of_stays(self, admin, seed):
        from database.stores.data.definitions import DefinitionStore
        from database.stores.data.secrets import SecretStore

        org = seed.org["_id"]
        _make_def(admin)
        ref = _make_secret(admin).json()["resource"]["resource_ref"]
        _publish(TestMigration.V2_FIELDS)             # region is required

        target = DefinitionStore().latest(org, "conn")
        assert SecretStore().move_forward(org, target) == (0, 1)
        kept = app_call(admin, "Secrets:Secret:Get",
                        {"resource_ref": ref}).json()["resource"]
        assert kept["definition_version"] == 1
        # Its version is still stood on, and survives a pruning.
        DefinitionStore().prune_unused(org, "conn")
        assert 1 in [doc["version"]
                     for doc in DefinitionStore().versions(org, "conn")]

    def test_a_half_filled_credential_is_not_handed_to_the_agent(
            self, anon, admin, seed, signing_key):
        """Used before its owner completed it: the agent is told why,
        in a sentence the person can act on — not handed a credential
        missing what it now needs."""
        from test_ai_messages import make_chat
        from test_runtime_secret_use import runtime_call

        _make_def(admin)
        ref = _make_secret(admin).json()["resource"]["resource_ref"]
        chat = make_chat(admin, "uses conn")
        used = runtime_call(anon, seed, chat, "Secrets:Secret:Use",
                            {"resource_id": "conn"})
        assert used.status_code == 200, used.text

        _publish(TestMigration.V2_FIELDS)
        behind = runtime_call(anon, seed, chat, "Secrets:Secret:Use",
                              {"resource_id": "conn"})
        assert behind.status_code == 409
        assert "older version" in behind.json()["error"]
        assert "Region" in behind.json()["error"]

        # Completed under Secrets, it is used again.
        assert app_call(admin, "Secrets:Secret:Update", {
            "resource_ref": ref, "migrate": True,
            "fields": {"region": "eu-west"}}).status_code == 200
        again = runtime_call(anon, seed, chat, "Secrets:Secret:Use",
                             {"resource_id": "conn"})
        assert again.status_code == 200, again.text
        assert again.json()["keys"]["region"] == "eu-west"

    def test_an_older_credential_that_lacks_nothing_is_still_used(
            self, anon, admin, seed, signing_key):
        from test_ai_messages import make_chat
        from test_runtime_secret_use import runtime_call

        _make_def(admin)
        _make_secret(admin)
        _publish(self.EASIER)                         # not moved: no update ran
        chat = make_chat(admin, "uses conn")
        used = runtime_call(anon, seed, chat, "Secrets:Secret:Use",
                            {"resource_id": "conn"})
        assert used.status_code == 200, used.text
        assert used.json()["values"]["password"] == "s3cret"


class TestChoosingTheDefinition:
    """A secret names an exact version, or the current one by slug."""

    def test_by_slug_resolves_the_current_version(self, admin, seed):
        _make_def(admin)
        response = app_call(admin, "Secrets:Secret:Create", {
            "definition_id": "conn", "name": "by-slug",
            "fields": {"host": "h", "username": "u", "password": "p"},
        })
        assert response.status_code == 200, response.text
        assert response.json()["resource"]["definition_version"] == 1

    def test_naming_both_is_refused(self, admin, seed):
        definition = _make_def(admin)
        response = app_call(admin, "Secrets:Secret:Create", {
            "definition_ref": definition["definition_ref"],
            "definition_id": "conn", "name": "both",
            "fields": {"host": "h", "username": "u", "password": "p"},
        })
        assert response.status_code == 400
        assert "not both" in response.json()["error"]


class TestEnforcedValidation:
    @pytest.mark.parametrize("field,value", [
        ("org_id", "caller-org"),
        ("type", "data"),
        ("resource_id", "caller-resource"),
        ("definition_version", 99),
    ])
    def test_create_rejects_backend_controlled_identity(
            self, admin, seed, field, value):
        definition = _make_def(admin)
        payload = {
            "definition_ref": definition["definition_ref"],
            "name": f"protected-{field}",
            "fields": {"host": "h", "username": "u", "password": "p"},
            field: value,
        }
        response = app_call(admin, "Secrets:Secret:Create", payload)
        assert response.status_code == 400
        assert field in response.json()["error"]

    @pytest.mark.parametrize("field,value", [
        ("org_id", "caller-org"),
        ("type", "data"),
        ("resource_id", "caller-resource"),
        ("definition_ref", "caller-definition"),
        ("definition_version", 99),
        ("definition_id", "another-definition"),
    ])
    def test_update_rejects_immutable_identity(
            self, admin, seed, field, value):
        _make_def(admin)
        secret = _make_secret(admin).json()["resource"]
        response = app_call(admin, "Secrets:Secret:Update", {
            "resource_ref": secret["resource_ref"],
            "fields": {"host": "new-host"},
            field: value,
        })
        assert response.status_code == 400
        assert field in response.json()["error"]

    def test_a_secret_requires_a_known_definition(self, admin, seed):
        response = app_call(admin, "Secrets:Secret:Create", {
            "definition_ref": "nope", "name": "x",
            "fields": {"host": "h"}})
        assert response.status_code == 404

    def test_the_validation_matrix(self, admin, seed):
        _make_def(admin)
        base = {"host": "h", "username": "u", "password": "p"}

        cases = [
            ({**base, "extra": "x"}, "Unknown fields"),
            ({"username": "u", "password": "p"}, "Required"),      # host missing
            ({**base, "password": ""}, "Required"),                # empty secret
            ({**base, "host": 42}, "must be text"),
        ]
        for fields, message in cases:
            response = _make_secret(admin, name=f"n-{message}", fields=fields)
            assert response.status_code == 400, fields
            assert message in response.json()["error"]

    def test_select_and_number_types(self, admin, seed):
        _make_def(admin, slug="typed", fields=[
            {"name": "engine", "label": "Engine", "type": "select",
             "options": ["postgres", "mysql"], "required": True,
             "storage": "keys"},
            {"name": "port", "label": "Port", "type": "number",
             "required": False, "storage": "keys"},
            {"name": "token", "label": "Token", "type": "secret",
             "required": True},
        ])

        assert _make_secret(admin, slug="typed", name="ok", fields={
            "engine": "postgres", "port": 5432, "token": "t"},
        ).status_code == 200
        assert _make_secret(admin, slug="typed", name="bad-opt", fields={
            "engine": "sqlite", "token": "t"}).status_code == 400
        assert _make_secret(admin, slug="typed", name="bad-num", fields={
            "engine": "mysql", "port": "high", "token": "t"}).status_code == 400

    def test_the_definition_decides_what_is_encrypted(self, admin, seed):
        """host → keys (visible); username/password → values (sealed)."""
        _make_def(admin)
        secret = _make_secret(admin).json()["resource"]

        assert secret["keys"] == {"host": "db.internal"}
        assert "username" not in str(secret)

        # The sentinels have to be long enough to mean something. This
        # searched stored CIPHERTEXT for "ro", which is two characters of
        # base64 and turns up by coincidence every few dozen runs — a
        # test that failed for reasons unrelated to what it was testing.
        from server.setup.app_state import get_db
        doc = get_db().collection("secrets").find_one(
            {"_id": secret["resource_ref"]})
        assert "s3cret" not in str(doc)
        assert "readonly-analyst" not in str(doc.get("values"))


class TestNameUniqueness:
    def test_unique_per_definition_and_creator(self, app, admin, seed):
        _make_def(admin)
        _make_def(admin, slug="conn2", label="Conn2")

        assert _make_secret(admin, name="prod").status_code == 200
        # Same creator, same definition, same name → rejected.
        response = _make_secret(admin, name="prod")
        assert response.status_code == 400
        assert "prod" in response.json()["error"]

        # Different definition, or different creator: fine.
        assert _make_secret(admin, slug="conn2", name="prod").status_code == 200

        creators = _group_with_actions(admin)
        member, _ = _user(app, seed, "member@test.org", [creators])
        assert _make_secret(member, name="prod").status_code == 200

    def test_rename_collision_is_rejected(self, admin, seed):
        _make_def(admin)
        _make_secret(admin, name="a")
        second = _make_secret(admin, name="b").json()["resource"]

        response = app_call(admin, "Secrets:Secret:Update", {
            "resource_ref": second["resource_ref"], "name": "a"})
        assert response.status_code == 400


class TestValuesMerge:
    def test_rotating_one_value_keeps_the_others(self, admin, seed):
        from database.stores.data.secrets import SecretStore
        from database.stores import UserStore

        _make_def(admin)
        secret = _make_secret(admin).json()["resource"]

        assert app_call(admin, "Secrets:Secret:Update", {
            "resource_ref": secret["resource_ref"],
            "fields": {"password": "rotated"}}).status_code == 200

        admin_doc = UserStore().get_by_email("admin@test.org")
        plaintext = SecretStore().use(
            UserStore.to_public(admin_doc), secret["resource_ref"])
        assert plaintext == {
            "username": "readonly-analyst", "password": "rotated"}

    def test_an_empty_encrypted_field_means_keep(self, admin, seed):
        """The UI sends '' for untouched password inputs."""
        from database.stores.data.secrets import SecretStore
        from database.stores import UserStore

        _make_def(admin)
        secret = _make_secret(admin).json()["resource"]

        assert app_call(admin, "Secrets:Secret:Update", {
            "resource_ref": secret["resource_ref"],
            "fields": {"host": "moved.internal", "password": ""},
        }).status_code == 200

        admin_doc = UserStore().get_by_email("admin@test.org")
        plaintext = SecretStore().use(
            UserStore.to_public(admin_doc), secret["resource_ref"])
        assert plaintext["password"] == "s3cret"  # untouched


class TestPermissions:
    def test_definitions_are_read_not_written(self, app, admin, seed):
        """Anyone who may create secrets reads the shapes; nobody writes
        one through the gateway — not even an administrator."""
        _make_def(admin)
        creators = _group_with_actions(admin)
        member, _ = _user(app, seed, "member@test.org", [creators])

        assert _make_secret(member).status_code == 200
        assert app_call(member, "Secrets:Definition:List").status_code == 200

        for endpoint in ("Secrets:Definition:Create", "Secrets:Definition:Update",
                         "Secrets:Definition:Delete",
                         "Secrets:Definition:Deleteversion"):
            refused = app_call(admin, endpoint, {
                "definition_id": "new", "label": "N", "fields": DEF_FIELDS_V1})
            assert refused.status_code == 400, endpoint
            assert "Unknown endpoint" in refused.text

    def test_list_carries_instance_counts(self, admin, seed):
        _make_def(admin)
        _make_secret(admin, name="one")
        _make_secret(admin, name="two")

        listed = app_call(admin, "Secrets:Definition:List").json()["definitions"]
        by_slug = {d["definition_id"]: d for d in listed}
        assert by_slug["conn"]["instance_count"] == 2

    def test_list_says_which_version_the_instances_are_on(self, admin, seed):
        """A count beside a version number reads as a count OF that version.
        It is not: secrets stay on the version they were created under."""
        _make_def(admin)
        _make_secret(admin, name="on-v1")

        _publish(DEF_FIELDS_V1 + [
                {"name": "port", "label": "Port", "type": "number",
                 "required": False, "storage": "keys"}])
        _make_secret(admin, name="on-v2", fields={
            "host": "db.internal", "username": "ro", "password": "s3cret",
            "port": 5432})

        listed = app_call(admin, "Secrets:Definition:List").json()["definitions"]
        conn = {d["definition_id"]: d for d in listed}["conn"]

        assert conn["version"] == 2
        assert conn["instance_count"] == 2
        assert [(v["version"], v["secret_count"]) for v in conn["versions"]] \
            == [(1, 1), (2, 1)]


def _group_with_actions(admin):
    """A group holding the secret actions plus definition read access."""
    policy = app_call(admin, "IAM:Policy:Create", {
        "name": "SecretUsers", "permissions": {"statements": [
            {"effect": "Allow", "actions": SECRET_ACTIONS + [
                "secrets:definition:list", "secrets:definition:get"]}]},
    }).json()["policy"]
    role = app_call(admin, "IAM:Role:Create", {
        "role_name": "SecretUsers", "assigned_policies": [policy["policy_id"]],
    }).json()["role"]
    return app_call(admin, "IAM:Group:Create", {
        "group_name": "SecretUsers", "assigned_roles": [role["role_id"]],
    }).json()["group"]["group_id"]
