"""The runtime-reads-secrets flow, end to end through /app.

This is the foundation of the LLM-key design: a delegated RUNTIME
principal (Bearer token, no cookie) may pull a secret's decrypted values
through Secrets:Secret:Use; a browser principal may not — values never
flow back to people.
"""

import dataclasses

import pytest

from conftest import app_call, define_secret, service_keys, signing_key  # noqa: F401


def make_llm_connection(admin, name="org-default"):
    """An LLM connection — the settings module's record, which the chat
    llm block names by id and the runtime resolves at this same door."""
    response = app_call(admin, "Settings:Llm:Create", {
        "endpoint": "https://api.example.test/v1",
        "name": name, "provider": "anthropic",
        "model": "claude-sonnet-5", "api_key": "sk-ant-secret",
    })
    assert response.status_code == 200, response.text
    return response.json()["connection"]["resource_ref"]


def make_probe_secret(admin, name="org-default", key="sk-ant-secret"):
    """An ordinary secret for exercising the RESOLUTION machinery —
    bindings, ambiguity, revocation. The slug is nothing special, which
    is the point: these rules hold for every credential."""
    try:
        define_secret("conn_probe", "Probe", [
            {"name": "provider", "label": "Provider", "type": "string",
             "storage": "keys", "required": True},
            {"name": "model", "label": "Model", "type": "string",
             "storage": "keys", "required": False},
            {"name": "api_key", "label": "Key", "type": "secret",
             "required": True},
        ])
    except ValueError:
        pass  # already there

    response = app_call(admin, "Secrets:Secret:Create", {
        "definition_id": "conn_probe", "name": name,
        "fields": {"provider": "anthropic", "model": "claude-sonnet-5",
                   "api_key": key},
    })
    assert response.status_code == 200, response.text
    return response.json()["resource"]["resource_ref"]


def runtime_call(anon, seed, chat_id, endpoint, data):
    """One /app call as the chat's delegated runtime."""
    return anon.post("/app", json={
        "endpoint": endpoint, "data": {"chat_id": chat_id, **data},
    }, headers=runtime_headers(seed, chat_id))


def runtime_headers(seed, chat_id="chat_llm"):
    from api.services.chat_session.identity import Delegation

    token = Delegation().for_chat(
        {
            "user_id": seed.admin["_id"],
            "org_id": seed.org["_id"],
            "email": seed.admin["email"],
            "session_id": "",
        },
        chat_id,
    )
    return {"Authorization": f"Bearer {token}"}


class TestRuntimeSecretUse:
    def test_runtime_principal_reads_decrypted_values(
        self, anon, admin, seed, signing_key
    ):
        ref = make_probe_secret(admin)

        response = anon.post("/app", json={
            "endpoint": "Secrets:Secret:Use",
            "data": {"resource_ref": ref},
        }, headers=runtime_headers(seed))
        assert response.status_code == 200, response.text
        values = response.json()["values"]
        assert values["api_key"] == "sk-ant-secret"
        # keys-stored fields (provider, model) are metadata, not values —
        # they travel BESIDE the values, so an agent receives the whole
        # credential while encryption stays exactly where declared.
        assert "provider" not in values and "model" not in values
        keys = response.json()["keys"]
        assert keys["provider"] == "anthropic"
        assert keys["model"] == "claude-sonnet-5"
        assert "api_key" not in keys

    def test_browser_principal_is_refused_values(self, admin, seed):
        ref = make_probe_secret(admin)

        response = app_call(admin, "Secrets:Secret:Use", {"resource_ref": ref})
        assert response.status_code == 403
        assert "Only the AI runtime" in response.json()["error"]

    def test_use_by_category_resolves_the_sole_instance(
        self, anon, admin, seed, signing_key
    ):
        make_probe_secret(admin)

        response = anon.post("/app", json={
            "endpoint": "Secrets:Secret:Use",
            "data": {"resource_id": "conn_probe"},
        }, headers=runtime_headers(seed))
        assert response.status_code == 200, response.text
        assert response.json()["values"]["api_key"] == "sk-ant-secret"

    def test_use_by_category_with_no_instance_is_404(
        self, anon, admin, seed, signing_key
    ):
        response = anon.post("/app", json={
            "endpoint": "Secrets:Secret:Use",
            "data": {"resource_id": "notebook__connection"},
        }, headers=runtime_headers(seed))
        assert response.status_code == 404
        assert "No secret instance" in response.json()["error"]

    def test_ambiguity_requires_an_explicit_chat_binding(
        self, anon, admin, seed, signing_key
    ):
        first_ref = make_probe_secret(admin)
        second = app_call(admin, "Secrets:Secret:Create", {
            "definition_id": "conn_probe", "name": "personal",
            "fields": {"provider": "openai", "api_key": "sk-oa-other"},
        })
        assert second.status_code == 200

        created = app_call(admin, "AI:Chat:Create", {"request_id": "c1"})
        chat_id = created.json()["data"]["chat"]["chat_id"]
        headers = runtime_headers(seed, chat_id)
        probe = {
            "endpoint": "Secrets:Secret:Use",
            "data": {"resource_id": "conn_probe"},
        }

        # Two instances, no binding: refuse rather than guess.
        ambiguous = anon.post("/app", json=probe, headers=headers)
        assert ambiguous.status_code == 409
        assert "config.bindings" in ambiguous.json()["error"]

        # The chat's explicit binding settles it.
        app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id,
            "config": {"bindings": {"conn_probe": first_ref}},
        })
        bound = anon.post("/app", json=probe, headers=headers)
        assert bound.status_code == 200
        assert bound.json()["values"]["api_key"] == "sk-ant-secret"

    def test_a_slot_lists_its_instances_and_uses_one_by_ref(
        self, anon, admin, seed, signing_key
    ):
        """Two credentials for one declaration: the runtime lists them
        (names and keys, never values), the default is marked, and a
        ref among them is usable — while a ref from another family the
        person can also see is refused for this slot."""
        first_ref = make_probe_secret(admin)
        second = app_call(admin, "Secrets:Secret:Create", {
            "definition_id": "conn_probe", "name": "personal",
            "fields": {"provider": "openai", "api_key": "sk-oa-other"},
        })
        second_ref = second.json()["resource"]["resource_ref"]
        assert app_call(admin, "Secrets:Secret:set_default", {
            "resource_id": "conn_probe", "resource_ref": first_ref,
        }).status_code == 200
        headers = runtime_headers(seed)

        listed = anon.post("/app", json={
            "endpoint": "Secrets:Secret:Instances",
            "data": {"resource_id": "conn_probe"},
        }, headers=headers)
        assert listed.status_code == 200, listed.text
        instances = {i["resource_ref"]: i for i in listed.json()["instances"]}
        assert set(instances) == {first_ref, second_ref}
        assert instances[first_ref]["is_default"] is True
        assert instances[second_ref]["keys"]["provider"] == "openai"
        assert all("values" not in i and "api_key" not in str(i)
                   for i in instances.values())

        used = anon.post("/app", json={
            "endpoint": "Secrets:Secret:Use",
            "data": {"resource_id": "conn_probe", "resource_ref": second_ref},
        }, headers=headers)
        assert used.status_code == 200, used.text
        assert used.json()["values"]["api_key"] == "sk-oa-other"

        # Visible, but another family's: not this slot's to name.
        other = make_llm_connection(admin)
        refused = anon.post("/app", json={
            "endpoint": "Secrets:Secret:Use",
            "data": {"resource_id": "conn_probe", "resource_ref": other},
        }, headers=headers)
        assert refused.status_code == 403, refused.text

        # A browser principal may not list a slot's instances either.
        assert app_call(admin, "Secrets:Secret:Instances",
                        {"resource_id": "conn_probe"}).status_code == 403

    def test_a_broken_binding_is_an_error_not_a_fallback(
        self, anon, admin, seed, signing_key
    ):
        make_probe_secret(admin)
        created = app_call(admin, "AI:Chat:Create", {"request_id": "c1"})
        chat_id = created.json()["data"]["chat"]["chat_id"]
        app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id,
            "config": {"bindings": {"conn_probe": "secret_gone"}},
        })

        response = anon.post("/app", json={
            "endpoint": "Secrets:Secret:Use",
            "data": {"resource_id": "conn_probe"},
        }, headers=runtime_headers(seed, chat_id))
        assert response.status_code == 404
        assert "not usable" in response.json()["error"]

    def test_revoking_the_delegation_kills_the_access(
        self, anon, admin, seed, signing_key
    ):
        from database.stores import RuntimeSessionStore

        ref = make_probe_secret(admin)
        headers = runtime_headers(seed)

        first = anon.post("/app", json={
            "endpoint": "Secrets:Secret:Use",
            "data": {"resource_ref": ref},
        }, headers=headers)
        assert first.status_code == 200

        RuntimeSessionStore().delete_for_user(seed.admin["_id"])

        second = anon.post("/app", json={
            "endpoint": "Secrets:Secret:Use",
            "data": {"resource_ref": ref},
        }, headers=headers)
        assert second.status_code == 401
