"""Manifest v1 validation, driven by the Notebook reference agent.

The real manifest must validate cleanly; every deliberately broken variant
must be rejected with an error that names the right place.
"""

import copy
from pathlib import Path

import pytest
import yaml

from contracts.agent_manifest import (
    Manifest,
    ManifestValidator,
    load_manifest,
)

from ai_runtime.tests.fixture_agents import AGENTS_DIR

MANIFEST_PATH = AGENTS_DIR / "notebook" / "manifest.yaml"


@pytest.fixture()
def document():
    with open(MANIFEST_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def errors_of(document):
    return ManifestValidator().validate(document)


def broken(document, mutate):
    """Deep-copy the golden document and apply one mutation."""
    clone = copy.deepcopy(document)
    mutate(clone)
    return clone


class TestAFunctionThatRunsCode:
    """``code: true`` on a function: part of what an administrator
    approves, and how the platform knows to open what a card names."""

    @staticmethod
    def with_code(document, value):
        def mutate(d):
            d["tools"][0]["functions"][0]["code"] = value
        return broken(document, mutate)

    def test_it_is_said_with_true_or_false(self, document):
        assert errors_of(self.with_code(document, True)) == []
        assert errors_of(self.with_code(document, False)) == []
        [error] = errors_of(self.with_code(document, "yes"))
        assert "code must be true or false" in error

    def test_an_agent_runs_code_when_one_of_its_functions_says_so(self, document):
        assert Manifest(document).runs_code is False
        assert Manifest(self.with_code(document, False)).runs_code is False
        assert Manifest(self.with_code(document, True)).runs_code is True


class TestGoldenManifest:
    def test_notebook_manifest_is_valid(self, document):
        assert errors_of(document) == []

    def test_load_manifest_returns_typed_access(self):
        manifest, errors = load_manifest(MANIFEST_PATH)
        assert errors == []
        assert manifest.agent_id == "notebook"
        assert manifest.entrypoint == ("agent", "NotebookAgent")
        assert manifest.dependencies == []
        assert set(manifest.scopes) == {"notebook"}

        names = [name for name, _, _ in manifest.functions()]
        assert names == [
            "notebook.note.save", "notebook.note.find",
            "notebook.archive.export", "notebook.archive.import",
            "notebook.sync.status", "notebook.sync.push",
        ]

        tool, function = manifest.function("notebook.sync.push")
        assert tool["id"] == "sync"
        assert function["permission_level"] == 3
        assert manifest.function("notebook.nope.nope") is None

    def test_unreadable_file_reports_not_raises(self, tmp_path):
        manifest, errors = load_manifest(tmp_path / "missing.yaml")
        assert manifest is None
        assert errors and "cannot read manifest" in errors[0]


class TestStructure:
    def test_non_mapping_rejected(self):
        assert errors_of([]) == ["manifest must be a mapping"]

    def test_wrong_schema_version(self, document):
        doc = broken(document, lambda d: d.update(schema_version="2.0"))
        assert any("schema_version" in e for e in errors_of(doc))

    def test_missing_agent_block(self, document):
        doc = broken(document, lambda d: d.pop("agent"))
        assert any("agent block is required" in e for e in errors_of(doc))

    def test_bad_agent_id(self, document):
        doc = broken(document, lambda d: d["agent"].update(id="Data::SQL"))
        assert any("agent.id" in e for e in errors_of(doc))

    def test_bad_version(self, document):
        doc = broken(document, lambda d: d["agent"].update(version="v1"))
        assert any("agent.version" in e for e in errors_of(doc))

    def test_missing_entrypoint(self, document):
        doc = broken(document, lambda d: d["implementation"].pop("entrypoint"))
        assert any("implementation.entrypoint" in e for e in errors_of(doc))

    def test_malformed_entrypoint(self, document):
        doc = broken(
            document,
            lambda d: d["implementation"].update(entrypoint="agent.NotebookAgent"),
        )
        assert any("implementation.entrypoint" in e for e in errors_of(doc))

    def test_too_many_tools(self, document):
        def mutate(d):
            template = copy.deepcopy(d["tools"][0])
            for i in range(17):
                clone = copy.deepcopy(template)
                clone["id"] = f"extra_{i}"
                d["tools"].append(clone)
        assert any("too many tools" in e for e in errors_of(broken(document, mutate)))

    def test_duplicate_tool_id(self, document):
        doc = broken(document, lambda d: d["tools"][1].update(id="note"))
        assert any("duplicate tool id" in e for e in errors_of(doc))


class TestResources:
    def test_duplicate_resource_id_across_kinds(self, document):
        # A file resource named "note" collides with the data resource.
        doc = broken(
            document, lambda d: d["resources"]["files"][0].update(id="note")
        )
        assert any("duplicate resource id 'note'" in e for e in errors_of(doc))

    def test_select_without_options(self, document):
        doc = broken(
            document,
            lambda d: d["resources"]["data"][1]["fields"][0].pop("options"),
        )
        assert any("options" in e for e in errors_of(doc))

    def test_bad_binding_cardinality(self, document):
        doc = broken(
            document,
            lambda d: d["resources"]["secrets"][0]["binding"].update(
                cardinality="single"
            ),
        )
        assert any("cardinality" in e for e in errors_of(doc))

    def test_family_is_refused_rather_than_ignored(self, document):
        """`family` used to let a manifest name a SHARED definition, so
        claiming a slug was enough to reach a credential nobody granted.

        Refused rather than dropped: resource keys are not whitelisted,
        so ignoring it would let an agent that relied on joining install
        cleanly, derive its own empty definition, and fail at use time
        far from the cause."""
        doc = broken(
            document,
            lambda d: d["resources"]["secrets"][0].update(
                family="llm_api_key"
            ),
        )
        errors = errors_of(doc)
        assert any("family is no longer supported" in e for e in errors)
        assert any("granted" in e for e in errors)

    def test_unknown_field_type(self, document):
        doc = broken(
            document,
            lambda d: d["resources"]["data"][0]["fields"][0].update(type="text"),
        )
        assert any("type must be one of" in e for e in errors_of(doc))

    def test_tool_referencing_undeclared_resource(self, document):
        doc = broken(
            document, lambda d: d["tools"][0]["resources"]["data"].append("ghost")
        )
        assert any(
            "'ghost' is not a declared agent resource" in e for e in errors_of(doc)
        )


class TestNetwork:
    """The hosts an agent connects to. It has to be said, and what is
    said is the whole of what the agent reaches."""

    @staticmethod
    def with_hosts(document, hosts):
        return broken(document, lambda d: d.update(network={"hosts": hosts}))

    def test_a_manifest_that_says_nothing_is_refused(self, document):
        """Where an agent connects is approved, so it has to be said."""
        silent = broken(document, lambda d: d.pop("network"))
        assert any("network block is required" in e for e in errors_of(silent))
        # And a document that never passed validation reaches nothing.
        assert Manifest(silent).network == {
            "declared": False, "any": False, "hosts": [], "from_secrets": []}

    def test_names_wildcards_and_a_credentials_field(self, document):
        doc = self.with_hosts(document, [
            "api.atlassian.com", "*.atlassian.net",
            {"from_secret": "connection.base_url"},
        ])
        assert errors_of(doc) == []
        assert Manifest(doc).network == {
            "declared": True, "any": False,
            "hosts": ["api.atlassian.com", "*.atlassian.net"],
            "from_secrets": ["connection.base_url"],
        }

    def test_any_is_said_in_place_of_the_list(self, document):
        doc = self.with_hosts(document, "any")
        assert errors_of(doc) == []
        assert Manifest(doc).network == {
            "declared": True, "any": True, "hosts": [], "from_secrets": []}

    def test_an_empty_list_is_an_agent_that_connects_to_nothing(self, document):
        doc = self.with_hosts(document, [])
        assert errors_of(doc) == []
        assert Manifest(doc).network == {
            "declared": True, "any": False, "hosts": [], "from_secrets": []}

    @pytest.mark.parametrize("host", [
        "10.0.0.5", "127.0.0.1", "[::1]", "169.254.169.254",
        "https://api.example.com", "https://api.example.com:443",
        "api.example.com/v1", "localhost", "backend", "API.Example.com",
        "*", "any", "*.com.", "api.*.example.com", "-bad.example.com", "",
        "10.0.0.5:993", "localhost:993", "*:993", "any:993",
    ])
    def test_what_is_not_a_name_is_refused(self, document, host):
        errors = errors_of(self.with_hosts(document, [host]))
        assert any("network.hosts[0]" in e for e in errors), errors

    def test_a_name_may_say_the_port_it_is_reached_on(self, document):
        """For an agent that speaks a protocol that is not the web's:
        mail, a database. The port is part of what was declared."""
        doc = self.with_hosts(document, [
            "imap.example.com:993", "smtp.example.com:465",
            "smtp.example.com:587", "*.mail.example.com:993",
            {"from_secret": "connection.base_url", "port": 993},
            {"from_secret": "connection.base_url"},
        ])
        assert errors_of(doc) == []
        assert Manifest(doc).network == {
            "declared": True, "any": False,
            "hosts": ["imap.example.com:993", "smtp.example.com:465",
                      "smtp.example.com:587", "*.mail.example.com:993"],
            "from_secrets": ["connection.base_url:993", "connection.base_url"],
        }

    @pytest.mark.parametrize("host", [
        "imap.example.com:0", "imap.example.com:65536", "imap.example.com:",
        "imap.example.com:imap", "imap.example.com:0993", "imap.example.com:-1",
        "imap.example.com:993:465",
    ])
    def test_a_port_that_is_not_one_is_refused(self, document, host):
        errors = errors_of(self.with_hosts(document, [host]))
        assert any("network.hosts[0]" in e for e in errors), errors

    @pytest.mark.parametrize("port", [0, 65536, "993", True, 9.5, None])
    def test_a_credentials_port_that_is_not_one_is_refused(self, document, port):
        errors = errors_of(self.with_hosts(
            document, [{"from_secret": "connection.base_url", "port": port}]))
        assert any("network.hosts[0]" in e for e in errors), errors

    def test_a_host_said_twice_on_one_port(self, document):
        errors = errors_of(self.with_hosts(
            document, ["imap.example.com:993", "imap.example.com:993"]))
        assert any("declared twice" in e for e in errors)

    def test_a_host_that_is_neither_is_refused(self, document):
        errors = errors_of(self.with_hosts(document, [42]))
        assert any("network.hosts[0]" in e for e in errors)

    def test_a_secret_the_agent_does_not_declare(self, document):
        errors = errors_of(self.with_hosts(
            document, [{"from_secret": "ghost.base_url"}]))
        assert any("'ghost' is not a secret this agent declares" in e
                   for e in errors)

    def test_a_field_the_secret_does_not_have(self, document):
        errors = errors_of(self.with_hosts(
            document, [{"from_secret": "connection.host"}]))
        assert any("'host' is not a field of 'connection'" in e for e in errors)

    def test_an_address_is_never_read_from_a_secret_field(self, document):
        """Whoever approves is shown where the agent connects. A field
        kept encrypted cannot be shown, so it cannot be an address."""
        errors = errors_of(self.with_hosts(
            document, [{"from_secret": "connection.api_token"}]))
        assert any("must be a string stored in keys" in e for e in errors)

    def test_a_host_said_twice(self, document):
        errors = errors_of(self.with_hosts(
            document, ["api.example.com", "api.example.com"]))
        assert any("declared twice" in e for e in errors)

    def test_too_many_hosts(self, document):
        errors = errors_of(self.with_hosts(
            document, [f"h{n}.example.com" for n in range(51)]))
        assert any("too many hosts" in e for e in errors)

    def test_the_block_holds_hosts_and_nothing_else(self, document):
        doc = broken(document, lambda d: d.update(
            network={"hosts": [], "ports": [443]}))
        assert any("unknown keys: ['ports']" in e for e in errors_of(doc))
        assert errors_of(broken(document, lambda d: d.update(network=[]))) == [
            "network must be a mapping"]
        assert any("must be a list of hosts" in e for e in errors_of(
            broken(document, lambda d: d.update(network={"hosts": "all"}))))


class TestOauthBlock:
    """A credential obtained by signing in. The common shape is Google's
    and Microsoft's; a provider that departs from it says how, in a
    closed vocabulary, and anything else is refused by name."""

    COMMON = {
        "provider": "google",
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": ["openid"],
        "identity": {"url": "https://openidconnect.googleapis.com/v1/userinfo",
                     "field": "email"},
    }

    @staticmethod
    def with_oauth(document, oauth):
        return broken(document, lambda d: d["resources"]["secrets"][0].update(oauth=oauth))

    def errors(self, document, **changes):
        return errors_of(self.with_oauth(document, {**self.COMMON, **changes}))

    def test_the_common_shape_is_valid(self, document):
        assert self.errors(document) == []

    def test_slack_notion_and_dropbox_shapes_are_valid(self, document):
        assert self.errors(document, scope_param="user_scope", scope_separator=",",
                           token_path="authed_user") == []
        assert self.errors(document, token_auth="basic", token_format="json",
                           identity={"source": "token",
                                     "field": "owner.user.person.email"}) == []
        assert self.errors(document, identity={
            "url": "https://api.dropboxapi.com/2/users/get_current_account",
            "method": "POST", "headers": {"X-Probe": "1"}, "field": "email"}) == []

    @pytest.mark.parametrize("changes, named", [
        ({"scope_separator": ";"}, "scope_separator"),
        ({"token_auth": "digest"}, "token_auth"),
        ({"token_format": "xml"}, "token_format"),
        ({"scope_param": "User Scope"}, "scope_param"),
        ({"token_path": "authed_user..token"}, "token_path"),
        ({"identity": {"source": "token", "url": "https://x.example/me"}}, "identity.url"),
        ({"identity": {"url": "https://x.example/me", "method": "PUT"}}, "identity.method"),
        ({"identity": {"url": "https://x.example/me", "verb": "GET"}}, "unknown keys"),
        ({"refresh_url": "https://x.example/refresh"}, "unknown keys"),
    ])
    def test_a_departure_outside_the_vocabulary_is_refused_by_name(
            self, document, changes, named):
        assert any(named in e for e in self.errors(document, **changes)), named


class TestFunctions:
    def test_missing_permission_level(self, document):
        doc = broken(
            document,
            lambda d: d["tools"][0]["functions"][0].pop("permission_level"),
        )
        assert any("permission_level" in e for e in errors_of(doc))

    def test_out_of_range_permission_level(self, document):
        doc = broken(
            document,
            lambda d: d["tools"][0]["functions"][0].update(permission_level=4),
        )
        assert any("permission_level" in e for e in errors_of(doc))

    def test_function_using_resource_not_in_parent_tool(self, document):
        # note.save trying to use the sync tool's connection secret.
        doc = broken(
            document,
            lambda d: d["tools"][0]["functions"][0]["resources"].update(
                secrets={"connection": "use"}
            ),
        )
        assert any(
            "not declared by the parent tool" in e for e in errors_of(doc)
        )

    def test_unknown_operation(self, document):
        doc = broken(
            document,
            lambda d: d["tools"][0]["functions"][0]["resources"]["data"].update(
                note=["create", "annihilate"]
            ),
        )
        assert any("unknown operations" in e for e in errors_of(doc))

    def test_duplicate_operations(self, document):
        doc = broken(
            document,
            lambda d: d["tools"][0]["functions"][0]["resources"]["data"].update(
                note=["create", "create"]
            ),
        )
        assert any("duplicate operations" in e for e in errors_of(doc))

    def test_undeclared_scope(self, document):
        doc = broken(
            document,
            lambda d: d["tools"][0]["functions"][0]["authorization"][
                "scopes"
            ].update(workspace={"from_input": "notebook"}),
        )
        assert any("scope is not declared" in e for e in errors_of(doc))

    def test_from_input_must_name_an_input_property(self, document):
        doc = broken(
            document,
            lambda d: d["tools"][0]["functions"][0]["authorization"]["scopes"][
                "notebook"
            ].update(from_input="nope"),
        )
        assert any("not a property of inputs" in e for e in errors_of(doc))


class TestSchemas:
    def test_forbidden_keyword(self, document):
        doc = broken(
            document,
            lambda d: d["tools"][0]["functions"][0]["inputs"].update(
                anyOf=[{"type": "object"}]
            ),
        )
        assert any("forbidden schema keyword 'anyOf'" in e for e in errors_of(doc))

    def test_snake_case_keyword_rejected(self, document):
        def mutate(d):
            inputs = d["tools"][0]["functions"][0]["inputs"]
            inputs["additional_properties"] = inputs.pop("additionalProperties")
        assert any(
            "unknown schema keyword 'additional_properties'" in e
            for e in errors_of(broken(document, mutate))
        )

    def test_root_must_be_object(self, document):
        doc = broken(
            document,
            lambda d: d["tools"][0]["functions"][0]["inputs"].update(type="array"),
        )
        assert any("root schema must have type object" in e for e in errors_of(doc))

    def test_depth_cap(self, document):
        def mutate(d):
            node = {"type": "object"}
            for _ in range(6):
                node = {"type": "object", "properties": {"deep": node}}
            d["tools"][0]["functions"][0]["inputs"] = node
        assert any("nesting exceeds depth" in e for e in errors_of(broken(document, mutate)))

    def test_x_resource_unknown_id(self, document):
        doc = broken(
            document,
            lambda d: d["tools"][1]["functions"][1]["inputs"]["properties"][
                "file_ref"
            ]["x-resource"].update(id="ghost"),
        )
        assert any("x-resource id 'ghost'" in e for e in errors_of(doc))

    def test_x_resource_requires_string_node(self, document):
        doc = broken(
            document,
            lambda d: d["tools"][1]["functions"][1]["inputs"]["properties"][
                "file_ref"
            ].update(type="integer"),
        )
        assert any("string-typed node" in e for e in errors_of(doc))


class TestFieldStorageContradictions:
    """Declarations that could only ever fail at runtime are refused at
    review time instead.

    The demo agent shipped exactly the first one — `content: object` in
    keys — and every save failed with a backend 400 the user had to
    debug. Keys are the scalar, queryable half of a record; the manifest
    is where that contract is enforceable before anything runs."""

    def test_an_object_cannot_live_in_keys(self, document):
        bad = broken(document, lambda d: d["resources"]["data"][0][
            "fields"].append({
                "name": "payload", "label": "Payload",
                "type": "object", "storage": "keys",
            }))
        assert any("object cannot be stored in keys" in error
                   for error in errors_of(bad))

    def test_an_object_in_values_is_fine(self, document):
        good = broken(document, lambda d: d["resources"]["data"][0][
            "fields"].append({
                "name": "payload", "label": "Payload",
                "type": "object", "storage": "values",
            }))
        assert errors_of(good) == []

    def test_a_requirement_that_is_an_option_is_refused(self, document):
        """What begins with a dash would be read as an order by the
        program that installs the packages. It was approved, and the
        agent then failed to load."""
        for option in ("--index-url https://packages.example.test/simple",
                       "-r requirements.txt", "  -e ."):
            bad = broken(document, lambda d, option=option: d[
                "implementation"].update(dependencies=["humanize>=4.9,<5",
                                                       option]))
            assert any("is an option, not a requirement" in error
                       for error in errors_of(bad)), option
        good = broken(document, lambda d: d["implementation"].update(
            dependencies=["humanize>=4.9,<5", "python-docx>=1.1,<2"]))
        assert errors_of(good) == []

    def test_a_secret_has_no_object_field(self, document):
        """A credential's fields are what a person types into a form.
        The place credentials are kept has no object, so a manifest
        that declared one was approved and then failed to install."""
        bad = broken(document, lambda d: d["resources"]["secrets"][0][
            "fields"].append({
                "name": "blob", "label": "Blob",
                "type": "object", "storage": "values",
            }))
        assert any("type must be one of" in error for error in errors_of(bad))

    def test_a_secrets_names_are_held_to_what_keeps_credentials(self, document):
        def with_field(name):
            return broken(document, lambda d: d["resources"]["secrets"][0][
                "fields"].append({"name": name, "label": "X",
                                  "type": "string", "storage": "keys"}))

        assert any("2 to 60 characters" in e for e in errors_of(with_field("a")))
        assert any("own label" in e for e in errors_of(with_field("name")))
        assert errors_of(with_field("ab")) == []

        def with_id(resource_id):
            def rename(d):
                secret = d["resources"]["secrets"][0]
                old, secret["id"] = secret["id"], resource_id
                text = yaml.safe_dump(d).replace(f"{old}.", f"{resource_id}.")
                d.clear()
                d.update(yaml.safe_load(text))
                d["resources"]["secrets"][0]["id"] = resource_id
            return broken(document, rename)

        assert any("at most 34 characters" in e
                   for e in errors_of(with_id("s" * 35)))

    def test_a_secret_has_at_most_fifty_fields(self, document):
        bad = broken(document, lambda d: d["resources"]["secrets"][0][
            "fields"].extend(
                {"name": f"field_{n}", "label": "X", "type": "string",
                 "storage": "keys"} for n in range(50)))
        assert any("at most 50 fields" in error for error in errors_of(bad))

    def test_what_the_validator_holds_a_secret_to_is_what_keeps_it(self):
        """The numbers are said twice, once where a manifest is read
        and once where a credential's shape is kept. This is what
        holds them to each other."""
        import sys

        from contracts import agent_manifest as said

        backend = Path(__file__).resolve().parent.parent / "backend"
        if str(backend) not in sys.path:
            sys.path.insert(0, str(backend))
        from database.stores.data.definitions import DefinitionStore as kept

        assert said.SECRET_FIELD_NAME_PATTERN.pattern == kept.SLUG_PATTERN.pattern
        assert said.SECRET_FIELDS_MAX == kept.MAX_FIELDS
        assert tuple(said.SECRET_RESERVED_FIELD_NAMES) == tuple(
            kept.RESERVED_FIELD_NAMES)
        assert said.SECRET_FIELD_TYPES <= set(kept.FIELD_TYPES)
        # "<agt_ + 20>__<id>" is held to the same pattern: 60 in all.
        assert kept.SLUG_PATTERN.match("agt_" + "a" * 20 + "__" + "s" * 34)
        assert not kept.SLUG_PATTERN.match("agt_" + "a" * 20 + "__" + "s" * 35)
        assert said.SECRET_ID_MAX_CHARS == 34

    def test_a_secret_cannot_live_in_keys(self, document):
        """Keys are plaintext. A secret there is a credential stored in
        the open, whatever the author intended."""
        bad = broken(document, lambda d: d["resources"]["secrets"][0][
            "fields"].append({
                "name": "extra_token", "label": "Extra",
                "type": "secret", "storage": "keys",
            }))
        assert any("secret cannot be stored in keys" in error
                   for error in errors_of(bad))


class TestUserAccess:
    """`user_access` names what a person may do to a resource directly.

    read and delete are deliberately not in the vocabulary: the owner
    always sees their own data and can always remove it — declaring
    either would claim authority the platform refuses to make
    conditional."""

    def test_create_update_on_data_is_valid(self, document):
        good = broken(document, lambda d: d["resources"]["data"][0].update(
            user_access=["create", "update"]))
        assert errors_of(good) == []

    def test_create_on_files_is_valid(self, document):
        good = broken(document, lambda d: d["resources"]["files"][0].update(
            user_access=["create"]))
        assert errors_of(good) == []

    def test_delete_is_not_declarable(self, document):
        bad = broken(document, lambda d: d["resources"]["data"][0].update(
            user_access=["delete"]))
        assert any("'delete' is not declarable" in e for e in errors_of(bad))

    def test_read_is_not_declarable(self, document):
        bad = broken(document, lambda d: d["resources"]["data"][0].update(
            user_access=["read"]))
        assert any("'read' is not declarable" in e for e in errors_of(bad))

    def test_update_on_files_is_refused(self, document):
        """A file's content is immutable; replacing one is delete+create."""
        bad = broken(document, lambda d: d["resources"]["files"][0].update(
            user_access=["update"]))
        assert any("immutable" in e for e in errors_of(bad))

    def test_secrets_have_no_user_access(self, document):
        bad = broken(document, lambda d: d["resources"]["secrets"][0].update(
            user_access=["create"]))
        assert any("secrets have no user_access" in e for e in errors_of(bad))

    def test_unknown_operation(self, document):
        bad = broken(document, lambda d: d["resources"]["data"][0].update(
            user_access=["annihilate"]))
        assert any("unknown operation 'annihilate'" in e for e in errors_of(bad))

    def test_empty_list_refused(self, document):
        bad = broken(document, lambda d: d["resources"]["data"][0].update(
            user_access=[]))
        assert any("non-empty list" in e for e in errors_of(bad))

    def test_duplicate_operations(self, document):
        bad = broken(document, lambda d: d["resources"]["data"][0].update(
            user_access=["create", "create"]))
        assert any("duplicate operations" in e for e in errors_of(bad))


class TestWatch:
    """`watch: true` names the function the platform calls when a
    person opens the agent's screen: nothing was asked that could be
    approved, and no model is behind it."""

    def test_a_level_zero_function_may_be_the_watch(self, document):
        # note.find is a level-0 read.
        good = broken(document, lambda d: d["tools"][0]["functions"][1].update(
            watch=True))
        assert errors_of(good) == []

    def test_a_function_that_would_ask_may_not(self, document):
        # sync.push is level 3.
        bad = broken(document, lambda d: d["tools"][2]["functions"][1].update(
            watch=True))
        assert any("watch function must be permission_level 0" in e
                   for e in errors_of(bad))

    def test_nor_one_that_asks_the_model(self, document):
        bad = broken(document, lambda d: d["tools"][0]["functions"][1].update(
            watch=True, llm=True))
        assert any("watch function cannot declare llm" in e
                   for e in errors_of(bad))


class TestSchedulable:
    """`schedulable: true` marks a function as designed to run on a
    clock, with the ceilings a run nobody watches carries. Cadence and
    inputs stay the schedule's, so there is nothing else for the
    manifest to say."""

    def test_a_level_zero_function_may_be_schedulable(self, document):
        # note.find is a level-0 read.
        good = broken(document, lambda d: d["tools"][0]["functions"][1].update(
            schedulable=True))
        assert errors_of(good) == []

    def test_schedulable_false_is_valid_and_inert(self, document):
        good = broken(document, lambda d: d["tools"][2]["functions"][1].update(
            schedulable=False))
        assert errors_of(good) == []

    def test_permission_ceiling(self, document):
        # sync.push is level 3 — nobody is there to approve it.
        bad = broken(document, lambda d: d["tools"][2]["functions"][1].update(
            schedulable=True))
        assert any(
            "schedulable function must be permission_level 0 or 1" in e
            for e in errors_of(bad)
        )

    def test_llm_is_refused(self, document):
        def mutate(d):
            d["tools"][0]["functions"][1].update(schedulable=True, llm=True)
        bad = broken(document, mutate)
        assert any(
            "schedulable function cannot declare llm" in e
            for e in errors_of(bad)
        )

    def test_non_boolean_refused(self, document):
        bad = broken(document, lambda d: d["tools"][0]["functions"][1].update(
            schedulable="yes"))
        assert any("must be true or false" in e for e in errors_of(bad))
