"""The platform executor: the one chokepoint for function invocations.

Driven by the real Notebook agent plus purpose-built broken/slow stub
agents. Everything comes back as (result, status) — never an exception.
"""

import asyncio
import json
import textwrap
from pathlib import Path

import pytest

from ai_runtime.execution.executor import FunctionExecutor
from ai_runtime.sinks import ChatSinks
from ai_runtime.tests.fixture_agents import load_agents
from ai_runtime.execution.resources import ResourceAccess, ResourceDenied
from sim.resources import InMemoryResourceProvider

AGENTS_DIR = Path(__file__).resolve().parent / "fixtures" / "agents"

from ai_runtime.tests.fixture_agents import MINIMAL_MANIFEST, write_agent


@pytest.fixture()
def notebook():
    agents, errors = load_agents(AGENTS_DIR)
    assert errors == {}
    return agents["notebook"]


def run(coro):
    return asyncio.run(coro)


def make_executor(**kwargs):
    """An executor with a stocked in-memory provider (canonical ids).
    A door of the chat's goes in by its name (ai_runtime/sinks.py)."""
    kwargs.setdefault("provider", InMemoryResourceProvider(
        secrets={"notebook__connection": {"api_token": "t"}}
    ))
    doors = {k: kwargs.pop(k) for k in list(kwargs) if k in ChatSinks.names()}
    return FunctionExecutor(sinks=ChatSinks(**doors), **kwargs)


class TestHappyPath:
    def test_valid_invocation_succeeds(self, notebook):
        executor = make_executor()
        result, status = run(executor.invoke(
            notebook, "notebook.note.save",
            {"notebook": "personal", "title": "Milk"},
        ))
        assert status == "success"
        assert result["created"] is True

    def test_defaults_from_the_schema_are_applied(self, notebook):
        # note.find declares limit default 25; the function must receive it.
        executor = make_executor()
        result, status = run(executor.invoke(
            notebook, "notebook.note.find", {},
        ))
        assert status == "success"
        assert result == {"notes": [], "total": 0}

    def test_progress_reaches_the_sink(self, notebook):
        seen = []

        async def sink(text, source):
            seen.append((text, source))

        executor = make_executor(progress=sink)
        _, status = run(executor.invoke(
            notebook, "notebook.note.save",
            {"notebook": "personal", "title": "Milk"},
        ))
        assert status == "success"
        # The agent's own line, told as the agent's, on its call.
        [(text, source)] = seen
        assert text == "Saving note in 'personal'"
        assert source["kind"] == "agent"
        assert source["function"] == "notebook.note.save"
        assert source["call_id"].startswith("c_")


class TestDisplays:
    """What a call offers to show: kept beside its result in a chat,
    quietly not kept where nobody could see it."""

    def test_a_call_offers_what_it_found_as_a_table(self, notebook):
        stored = {}

        async def storage(source, result):
            ref = f"stg_{len(stored) + 1}"
            stored[ref] = result
            return ref

        executor = make_executor(store=storage)

        async def scenario():
            await executor.invoke(notebook, "notebook.note.save",
                                  {"notebook": "work", "title": "Ship"})
            return await executor.invoke(notebook, "notebook.note.find",
                                         {"notebook": "work"})

        result, status = run(scenario())
        assert status == "success"
        [display] = result["displays"]
        assert (display["kind"], display["title"]) == ("table", "Notes")
        kept = stored[display["display_id"]]
        assert kept["columns"] == ["title", "notebook", "priority"]
        assert kept["rows"][0]["title"] == "Ship"

    def test_without_a_chat_the_offer_is_not_kept(self, notebook):
        executor = make_executor()

        async def scenario():
            await executor.invoke(notebook, "notebook.note.save",
                                  {"notebook": "work", "title": "Ship"})
            return await executor.invoke(notebook, "notebook.note.find",
                                         {"notebook": "work"})

        result, status = run(scenario())
        assert status == "success"
        assert result["total"] == 1
        assert "displays" not in result


class TestValidation:
    def test_unknown_function(self, notebook):
        result, status = run(FunctionExecutor().invoke(
            notebook, "notebook.note.nope", {},
        ))
        assert status == "error"
        assert "Unknown function" in result["error"]

    def test_missing_required_input(self, notebook):
        result, status = run(FunctionExecutor().invoke(
            notebook, "notebook.note.save", {"notebook": "personal"},
        ))
        assert status == "error"
        assert "Invalid inputs" in result["error"]
        assert "title" in result["error"]

    def test_wrong_input_type(self, notebook):
        result, status = run(FunctionExecutor().invoke(
            notebook, "notebook.note.find", {"limit": "many"},
        ))
        assert status == "error"
        assert "limit" in result["error"]

    def test_undeclared_input_rejected(self, notebook):
        result, status = run(FunctionExecutor().invoke(
            notebook, "notebook.note.save",
            {"notebook": "p", "title": "t", "sneaky": True},
        ))
        assert status == "error"
        assert "Invalid inputs" in result["error"]


class TestPermissionLevels:
    def test_higher_level_without_approver_is_denied(self, notebook):
        # sync.push is level 3; chat level defaults to 1.
        result, status = run(FunctionExecutor().invoke(
            notebook, "notebook.sync.push", {"notebook": "personal"},
        ))
        assert status == "error"
        assert result.get("denied") is True

    def test_level_at_or_below_chat_level_runs_directly(self, notebook):
        asked = []

        async def approver(request):
            asked.append(request)
            return True

        executor = make_executor(approve=approver)
        _, status = run(executor.invoke(
            notebook, "notebook.sync.status", {}, chat_level=0,
        ))
        assert status == "success"
        assert asked == []  # level 0 at chat level 0: no approval involved

    def test_approver_approval_lets_the_function_run(self, notebook):
        asked = []

        async def approver(request):
            asked.append(request)
            return True

        executor = make_executor(approve=approver)
        result, status = run(executor.invoke(
            notebook, "notebook.sync.push", {"notebook": "personal"},
            chat_level=1,
        ))
        assert status == "success"
        assert result["pushed"] == 0
        assert asked[0]["function"] == "notebook.sync.push"
        assert asked[0]["permission_level"] == 3
        assert asked[0]["chat_level"] == 1

    def test_approver_denial_is_an_observation(self, notebook):
        async def approver(request):
            return False

        result, status = run(make_executor(approve=approver).invoke(
            notebook, "notebook.sync.push", {"notebook": "personal"},
        ))
        assert status == "error"
        assert result["denied"] is True
        assert "not approved" in result["error"]


class TestResultStorage:
    def test_successful_results_are_stored_and_referenced(self, notebook):
        stored = []

        async def storage(source, result):
            stored.append((source, result))
            return "stg_1"

        executor = make_executor(store=storage)
        result, status = run(executor.invoke(
            notebook, "notebook.note.save",
            {"notebook": "personal", "title": "Milk"},
        ))
        assert status == "success"
        assert result["storage_ref"] == "stg_1"
        assert stored[0][0] == "notebook.note.save"
        assert "storage_ref" not in stored[0][1]  # stored before stamping

    def test_storage_failure_never_kills_the_result(self, notebook):
        async def storage(source, result):
            raise RuntimeError("storage down")

        executor = make_executor(store=storage)
        result, status = run(executor.invoke(
            notebook, "notebook.note.save",
            {"notebook": "personal", "title": "Milk"},
        ))
        assert status == "success"
        assert "storage_ref" not in result

    def test_error_results_are_not_stored(self, notebook):
        stored = []

        async def storage(source, result):
            stored.append(source)
            return "stg_1"

        executor = make_executor(store=storage)
        _, status = run(executor.invoke(
            notebook, "notebook.note.save", {"notebook": "personal"},
        ))
        assert status == "error"
        assert stored == []


class TestReferenceInputs:
    def test_references_resolve_before_the_function_runs(self, notebook):
        async def resolver(storage_ref, path):
            assert (storage_ref, path) == ("stg_1", "rows.0.title")
            return "Milk from storage"

        executor = make_executor(read_result=resolver)
        result, status = run(executor.invoke(
            notebook, "notebook.note.save",
            {"notebook": "personal",
             "title": {"storage_ref": "stg_1", "path": "rows.0.title"}},
        ))
        assert status == "success"

        # The resolved value went through schema validation and into the
        # function: find it back by title.
        found, _ = run(executor.invoke(
            notebook, "notebook.note.find", {"query": "milk from storage"},
        ))
        assert found["total"] == 1

    def test_unresolvable_references_are_an_error(self, notebook):
        async def resolver(storage_ref, path):
            raise RuntimeError("not found")

        executor = make_executor(read_result=resolver)
        result, status = run(executor.invoke(
            notebook, "notebook.note.save",
            {"notebook": "p", "title": {"storage_ref": "stg_nope"}},
        ))
        assert status == "error"
        assert "Reference input could not be resolved" in result["error"]

    def test_ordinary_dict_inputs_pass_untouched(self, notebook):
        async def resolver(storage_ref, path):
            raise AssertionError("must not be called")

        executor = make_executor(read_result=resolver)
        _, status = run(executor.invoke(
            notebook, "notebook.note.save",
            {"notebook": "p", "title": "t", "content": {"body": "plain"}},
        ))
        assert status == "success"


class TestFailureContainment:
    def build(self, tmp_path, agent_code, manifest=None):
        write_agent(
            tmp_path, "demo", manifest=manifest, files={"agent.py": agent_code}
        )
        agents, errors = load_agents(tmp_path)
        assert errors == {}
        return agents["demo"]

    def test_function_exception_becomes_an_error(self, tmp_path):
        agent = self.build(tmp_path, textwrap.dedent("""\
            from decentai_sdk.base import AgentBase, ToolBase

            class MainTool(ToolBase):
                id = "main"

                async def run(self, call):
                    raise RuntimeError("boom")

            class DemoAgent(AgentBase):
                def tools(self):
                    return [MainTool(self)]
        """))
        result, status = run(FunctionExecutor().invoke(agent, "demo.main.run", {}))
        assert status == "error"
        assert "failed" in result["error"]
        # The reason travels. "failed." with the cause amputated turns a
        # one-line fix into a debugging session over server logs — and a
        # function's success result already reaches the model unfiltered,
        # so its failure reason is the same trust domain.
        assert "boom" in result["error"]

    def test_timeout_is_enforced(self, tmp_path):
        manifest = MINIMAL_MANIFEST.format(
            agent_id="demo", entrypoint="agent:DemoAgent"
        ).replace(
            "permission_level: 0",
            "permission_level: 0\n        timeout_seconds: 1",
        )
        agent = self.build(tmp_path, textwrap.dedent("""\
            import time
            from decentai_sdk.base import AgentBase, ToolBase

            class MainTool(ToolBase):
                id = "main"

                def run(self, call):
                    time.sleep(3)
                    return {}, "success"

            class DemoAgent(AgentBase):
                def tools(self):
                    return [MainTool(self)]
        """), manifest=manifest)
        result, status = run(FunctionExecutor().invoke(agent, "demo.main.run", {}))
        assert status == "error"
        assert "timed out after 1s" in result["error"]

    def test_invalid_return_shape_is_an_error(self, tmp_path):
        agent = self.build(tmp_path, textwrap.dedent("""\
            from decentai_sdk.base import AgentBase, ToolBase

            class MainTool(ToolBase):
                id = "main"

                async def run(self, call):
                    return {"just": "a dict"}

            class DemoAgent(AgentBase):
                def tools(self):
                    return [MainTool(self)]
        """))
        result, status = run(FunctionExecutor().invoke(agent, "demo.main.run", {}))
        assert status == "error"
        assert "invalid result" in result["error"]

    def test_output_not_matching_manifest_is_an_error(self, tmp_path):
        manifest = MINIMAL_MANIFEST.format(
            agent_id="demo", entrypoint="agent:DemoAgent"
        ).replace(
            "outputs: {type: object}",
            "outputs: {type: object, required: [answer], "
            "properties: {answer: {type: string}}}",
        )
        agent = self.build(tmp_path, textwrap.dedent("""\
            from decentai_sdk.base import AgentBase, ToolBase

            class MainTool(ToolBase):
                id = "main"

                async def run(self, call):
                    return {"wrong_key": 1}, "success"

            class DemoAgent(AgentBase):
                def tools(self):
                    return [MainTool(self)]
        """), manifest=manifest)
        result, status = run(FunctionExecutor().invoke(agent, "demo.main.run", {}))
        assert status == "error"
        assert "not matching its manifest" in result["error"]

    def test_error_results_skip_output_validation(self, tmp_path):
        # An error payload is dynamic by contract — never schema-checked.
        manifest = MINIMAL_MANIFEST.format(
            agent_id="demo", entrypoint="agent:DemoAgent"
        ).replace(
            "outputs: {type: object}",
            "outputs: {type: object, required: [answer]}",
        )
        agent = self.build(tmp_path, textwrap.dedent("""\
            from decentai_sdk.base import AgentBase, ToolBase

            class MainTool(ToolBase):
                id = "main"

                async def run(self, call):
                    return {"error": "domain problem"}, "error"

            class DemoAgent(AgentBase):
                def tools(self):
                    return [MainTool(self)]
        """), manifest=manifest)
        result, status = run(FunctionExecutor().invoke(agent, "demo.main.run", {}))
        assert status == "error"
        assert result["error"] == "domain problem"


FILE_MANIFEST = textwrap.dedent("""\
    schema_version: "1.0"
    agent:
      id: demo
      name: Demo
      version: "1.0.0"
      description: A test agent.
    network:
      hosts: []
    implementation:
      entrypoint: agent:DemoAgent
    resources:
      files:
        - id: doc
          label: Doc
          binding: {cardinality: many, required: false}
          constraints:
            mime_types: [application/octet-stream, text/plain]
            max_size_mb: 1
    tools:
      - id: main
        name: Main
        description: The one tool.
        resources:
          files: [doc]
        functions:
          - id: run
            name: Run
            description: Write bytes, read them back.
            permission_level: 2
            resources:
              files: {doc: [create, read]}
            inputs: {type: object}
            outputs: {type: object}
""")


class TestMediatedResources:
    def test_a_file_of_bytes_survives_the_wire(self, tmp_path):
        # The wire carries JSON strings; a workbook or a PDF written as
        # text arrived as replacement characters. Bytes travel as base64
        # both ways, and text is still text.
        agent = TestFailureContainment().build(tmp_path, textwrap.dedent("""\
            import base64
            from decentai_sdk.base import AgentBase, ToolBase

            class MainTool(ToolBase):
                id = "main"

                async def run(self, call):
                    raw = bytes(range(256))
                    made = await call.resources.create_file(
                        "doc", "bytes.bin",
                        content_base64=base64.b64encode(raw).decode("ascii"))
                    back = await call.resources.read_file("doc", made["resource_ref"])
                    text = await call.resources.create_file(
                        "doc", "note.txt", content="héllo")
                    text_back = await call.resources.read_file(
                        "doc", text["resource_ref"])
                    return {
                        "same": base64.b64decode(back["content_base64"]) == raw,
                        "no_text": "content" not in back,
                        "text": text_back["content"],
                        "text_b64": text_back["content_base64"],
                    }, "success"

            class DemoAgent(AgentBase):
                def tools(self):
                    return [MainTool(self)]
        """), manifest=FILE_MANIFEST)
        result, status = run(FunctionExecutor(
            provider=InMemoryResourceProvider()).invoke(
                agent, "demo.main.run", {}, chat_level=2))
        assert status == "success", result
        assert result["same"] is True and result["no_text"] is True
        assert result["text"] == "héllo"
        assert result["text_b64"] == "aMOpbGxv"

    def test_functions_reach_only_declared_resources(self, tmp_path):
        agent = TestFailureContainment().build(tmp_path, textwrap.dedent("""\
            from decentai_sdk.base import AgentBase, ToolBase

            class MainTool(ToolBase):
                id = "main"

                async def run(self, call):
                    # "notes" is NOT declared by this function.
                    await call.resources.create_data("notes", {"x": 1})
                    return {}, "success"

            class DemoAgent(AgentBase):
                def tools(self):
                    return [MainTool(self)]
        """))
        provider = InMemoryResourceProvider()
        result, status = run(
            FunctionExecutor(provider=provider).invoke(agent, "demo.main.run", {})
        )
        assert status == "error"          # ResourceDenied surfaced as failure
        assert provider.data == {}        # nothing was written

    def test_a_ref_outside_the_slot_is_read_only_when_handed(self):
        """A file the person attached, or another agent stored, is read
        when the call was given its ref — in its inputs, or as the
        answer to its question — and never by a ref it came up with.
        Nothing handed is deleted: that stays inside the slot."""
        provider = InMemoryResourceProvider()

        async def scenario():
            theirs = (await provider.create_file(
                "uploads", "report.csv", b"a,b"))["resource_ref"]
            grants = {"files": {"document": {"read", "delete"}}}

            guessing = ResourceAccess(grants, provider, namespace="demo")
            with pytest.raises(KeyError):
                await guessing.read_file("document", theirs)

            given = ResourceAccess(
                grants, provider, namespace="demo",
                handed=FunctionExecutor._refs_in({"files": [{"ref": theirs}]}))
            assert (await given.read_file("document", theirs))["filename"] == "report.csv"
            assert await given.delete_file("document", theirs) is False
            assert theirs in provider.files["uploads"]

            asked = ResourceAccess(grants, provider, namespace="demo")
            asked.hand(theirs)
            assert (await asked.read_file("document", theirs))["filename"] == "report.csv"

        run(scenario())

    def test_resource_access_enforces_declared_operations(self):
        provider = InMemoryResourceProvider(
            secrets={"demo__connection": {"base_url": "x", "api_token": "t"}}
        )
        access = ResourceAccess(
            {"secrets": {"connection": {"use"}},
             "data": {"note": {"create", "read"}}},
            provider,
            definitions={"data": {"note": {"title": "keys",
                                           "content": "values"}}},
            namespace="demo",
        )

        async def scenario():
            secret = await access.use_secret("connection")
            assert secret["api_token"] == "t"

            record = await access.create_data(
                "note", {"title": "Milk", "content": {"body": "secret text"}}
            )
            ref = record["resource_ref"]
            # Canonical category and the manifest-driven storage split.
            assert record["resource_id"] == "demo__note"
            assert record["keys"] == {"title": "Milk"}
            stored = provider.data["demo__note"][ref]
            assert stored["values"] == {"content": {"body": "secret text"}}

            fetched = await access.read_data("note", ref)
            assert fetched["keys"]["title"] == "Milk"
            # Read back as the backend answers it: the encrypted half
            # decrypted, since the agent that wrote the body reads it.
            assert fetched["values"] == {"content": {"body": "secret text"}}

            with pytest.raises(ResourceDenied):
                await access.delete_data("note", ref)
            with pytest.raises(ResourceDenied):
                await access.use_secret("other_secret")
            with pytest.raises(ResourceDenied):
                await access.create_file("document", "a.csv", "x")

        run(scenario())


class TestLoginsAskedForAsItWorks:
    """call.credential reaches the chat only through a function that
    declared credentials: true, and only with a well-formed ask."""

    @staticmethod
    def build(tmp_path, name, manifest, code):
        folder = tmp_path / name
        write_agent(folder, "demo", manifest=manifest, files={"agent.py": code})
        agents, errors = load_agents(folder)
        assert errors == {}, errors
        return agents["demo"]

    def test_undeclared_functions_cannot_ask_and_declared_ones_are_checked(self, tmp_path):
        asked = []

        async def credentialer(host, fields, account, site, refresh, source):
            asked.append((host, fields, account, site, refresh, source["agent"]))
            return {"email": "a", "password": "b"}

        plain = MINIMAL_MANIFEST.format(agent_id="demo", entrypoint="agent:DemoAgent")
        agent = self.build(tmp_path, "plain", plain, textwrap.dedent("""
            from decentai_sdk.base import AgentBase, ToolBase

            class MainTool(ToolBase):
                id = "main"
                async def run(self, call):
                    login = await call.credential("id.atlassian.com", [
                        {"name": "email", "type": "text"},
                        {"name": "password", "type": "secret"},
                    ], site="acme.atlassian.net")
                    return {"login": login}, "success"

            class DemoAgent(AgentBase):
                def tools(self):
                    return [MainTool(self)]
        """))
        executor = FunctionExecutor(provider=InMemoryResourceProvider(),
                                    sinks=ChatSinks(credential=credentialer))
        # Not declared: the worker's ask is refused as a resource would be.
        result, status = run(executor.invoke(agent, "demo.main.run", {}))
        assert status == "error" and "credentials: true" in str(result), result
        assert asked == []

        # Declared: the ask reaches the chat, shaped and in the agent's name.
        declared = self.build(tmp_path, "declared", plain.replace(
            "permission_level: 0", "permission_level: 0\n        credentials: true"),
            textwrap.dedent("""
            from decentai_sdk.base import AgentBase, ToolBase

            class MainTool(ToolBase):
                id = "main"
                async def run(self, call):
                    login = await call.credential("id.atlassian.com", [
                        {"name": "email", "type": "text"},
                        {"name": "password", "label": "Password", "type": "secret",
                         "remember": False},
                    ], site="acme.atlassian.net", account="a@x.example")
                    return {"login": login}, "success"

            class DemoAgent(AgentBase):
                def tools(self):
                    return [MainTool(self)]
        """))
        result, status = run(executor.invoke(declared, "demo.main.run", {}))
        assert status == "success", result
        assert result["login"] == {"email": "a", "password": "b"}
        [(host, fields, account, site, refresh, agent_ref)] = asked
        assert (host, account, site, refresh, agent_ref) == (
            "id.atlassian.com", "a@x.example", "acme.atlassian.net", False, "demo")
        assert fields == [
            {"name": "email", "label": "email", "type": "text", "required": True,
             "remember": True},
            {"name": "password", "label": "Password", "type": "secret",
             "required": True, "remember": False}]


class TestSeveralCredentials:
    """A person with two credentials for one declaration: the agent
    lists them, names one, and the platform still checks the name."""

    ACCOUNTS = [
        {"resource_ref": "sec_work", "name": "Google — work",
         "keys": {"account": "a@work.example", "status": "connected"},
         "values": {"access_token": "tok-work"}, "default": True},
        {"resource_ref": "sec_home", "name": "Google — home",
         "keys": {"account": "a@home.example", "status": "connected"},
         "values": {"access_token": "tok-home"}},
    ]

    def access(self, provider):
        return ResourceAccess(
            {"secrets": {"google": {"use"}}}, provider, definitions={},
            namespace="demo")

    def test_the_agent_lists_them_and_uses_one_by_ref(self):
        provider = InMemoryResourceProvider(secrets={"demo__google": self.ACCOUNTS})
        access = self.access(provider)

        async def scenario():
            listed = await access.list_secrets("google")
            assert [a["keys"]["account"] for a in listed] == [
                "a@work.example", "a@home.example"]
            # Every key the backend's listing has (docs/agents/sdk.md):
            # code written to the page runs under the stand-in.
            assert all({"resource_ref", "name", "keys", "is_default",
                        "is_bound"} <= set(a) for a in listed)
            assert all("values" not in a and "access_token" not in a
                       for a in listed), "a listing carries no value"
            assert [a["is_default"] for a in listed] == [True, False]
            home = await access.use_secret("google", ref="sec_home")
            assert home["access_token"] == "tok-home"
            assert home["account"] == "a@home.example"
            # No ref: the default answers, as before.
            assert (await access.use_secret("google"))["access_token"] == "tok-work"
            with pytest.raises(ResourceDenied):
                await access.use_secret("google", ref="sec_nobody")
            with pytest.raises(ResourceDenied):
                await access.list_secrets("other")

        run(scenario())

    def test_several_with_no_default_is_a_refusal_not_a_guess(self):
        two = [dict(a, default=False) for a in self.ACCOUNTS]
        access = self.access(InMemoryResourceProvider(secrets={"demo__google": two}))

        async def scenario():
            with pytest.raises(ResourceDenied, match="Multiple"):
                await access.use_secret("google")
            assert len(await access.list_secrets("google")) == 2

        run(scenario())


class TestFunctionGrants:
    def test_a_constrained_deny_refuses_only_its_values(self):
        from ai_runtime.execution.grants import FunctionGrants

        grants = FunctionGrants([
            {"effect": "allow", "functions": ["agt.*.*"]},
            {"effect": "deny", "functions": ["agt.note.save"],
             "constraints": {"notebook": ["team_a"]}},
        ])
        # The decision: denied for the named value, allowed for others.
        assert not grants.allows("agt.note.save", {"notebook": "team_a"})
        assert grants.allows("agt.note.save", {"notebook": "team_b"})
        # The early gate, with no values yet: SOME value could be
        # allowed, so the function is reachable. Once, the constrained
        # deny refused it here for every value.
        assert grants.may_reach("agt.note.save")

    def test_an_unconstrained_deny_refuses_at_the_gate(self):
        from ai_runtime.execution.grants import FunctionGrants

        grants = FunctionGrants([
            {"effect": "allow", "functions": ["agt.*.*"]},
            {"effect": "deny", "functions": ["agt.note.save"]},
        ])
        assert not grants.may_reach("agt.note.save")
        assert grants.may_reach("agt.note.find")

    def test_a_value_nobody_named_is_read_against_the_caller(self):
        """A function may leave its scope's input optional. What the
        agent then acts on cannot be shown, so a deny for some values
        holds and an allow for some values does not."""
        from ai_runtime.execution.grants import FunctionGrants

        denied = FunctionGrants([
            {"effect": "allow", "functions": ["agt.*.*"]},
            {"effect": "deny", "functions": ["agt.note.*"],
             "constraints": {"notebook": ["team_a"]}},
        ])
        assert not denied.allows("agt.note.find", {"notebook": None})
        # A function with no such scope at all is not what the deny is about.
        assert denied.allows("agt.note.ping", {})

        allowed = FunctionGrants([
            {"effect": "allow", "functions": ["agt.note.*"],
             "constraints": {"notebook": ["team_a"]}},
        ])
        assert not allowed.allows("agt.note.find", {"notebook": None})
        assert allowed.allows("agt.note.find", {"notebook": "team_a"})

    def test_an_optional_scope_left_out_does_not_slip_past_a_deny(self, notebook):
        from ai_runtime.execution.grants import FunctionGrants

        executor = make_executor(grants=FunctionGrants([
            {"effect": "allow", "functions": ["notebook.*.*"]},
            {"effect": "deny", "functions": ["notebook.note.find"],
             "constraints": {"notebook": ["work"]}},
        ]))
        # Every notebook, the denied one among them: refused.
        result, status = run(executor.invoke(notebook, "notebook.note.find", {}))
        assert status == "error" and result.get("not_permitted") is True
        # One that is not denied, named: served.
        result, status = run(executor.invoke(
            notebook, "notebook.note.find", {"notebook": "personal"}))
        assert status == "success"


class TestResumeInvoke:
    """A parked invocation completed after a reconnect: every gate
    re-runs against current state before anything executes."""

    def _parked(self):
        from ai_runtime.execution.executor import (
            ParkedInvocation, action_hash,
        )

        inputs = {"notebook": "personal", "title": "Milk"}
        parked = ParkedInvocation(
            agent_id="notebook", function="notebook.note.save",
            inputs=inputs, permission_level=1, chat_level=0,
        )
        return parked, action_hash("notebook", "notebook.note.save", inputs)

    def test_approved_decision_executes_once(self, notebook):
        parked, digest = self._parked()
        executor = make_executor()
        result, status = run(executor.resume_invoke(
            notebook, parked, digest, "approve",
        ))
        assert status == "success"
        assert result["created"] is True

    def test_hash_mismatch_blocks_execution(self, notebook):
        parked, _ = self._parked()
        executor = make_executor()
        result, status = run(executor.resume_invoke(
            notebook, parked, "0" * 64, "approve",
        ))
        assert status == "error"
        assert "does not match its approval" in result["error"]

    def test_tightened_grants_deny_re_dispatch(self, notebook):
        from ai_runtime.execution.grants import FunctionGrants

        parked, digest = self._parked()
        executor = make_executor(grants=FunctionGrants([
            {"effect": "allow", "functions": ["notebook.sync.*"]},
        ]))
        result, status = run(executor.resume_invoke(
            notebook, parked, digest, "approve",
        ))
        assert status == "error"
        assert result.get("not_permitted") is True

    def test_deny_and_expired_produce_denial_without_executing(self, notebook):
        parked, digest = self._parked()
        for decision in ("deny", "expired"):
            executor = make_executor()
            result, status = run(executor.resume_invoke(
                notebook, parked, digest, decision,
            ))
            assert status == "error"
            assert result.get("denied") is True

    def test_pending_decision_reparks_and_follows_the_answer(self, notebook):
        parked, digest = self._parked()
        executor = make_executor()

        async def approve():
            return True

        result, status = run(executor.resume_invoke(
            notebook, parked, digest, "pending", repark=approve,
        ))
        assert status == "success"

        async def deny():
            return False

        result, status = run(executor.resume_invoke(
            notebook, parked, digest, "pending", repark=deny,
        ))
        assert status == "error"
        assert result.get("denied") is True

    def test_pending_without_a_reparker_denies(self, notebook):
        parked, digest = self._parked()
        result, status = run(make_executor().resume_invoke(
            notebook, parked, digest, "pending",
        ))
        assert status == "error"
        assert result.get("denied") is True


class TestTheTrail:
    """Every invocation lands on the platform's trail, whatever the
    outcome — the agent, the function, its level, the inputs in
    outline, the outcome, the duration — and a trail that will not
    write never costs the result."""

    def test_a_run_a_refusal_and_a_denial_are_all_recorded(self, notebook):
        trail = []

        async def audit(event):
            trail.append(event)

        async def storage(source, result):
            return "stg_kept"

        executor = make_executor(audit=audit, store=storage)
        run(executor.invoke(notebook, "notebook.note.save",
                            {"notebook": "personal", "title": "Milk"}))
        run(executor.invoke(notebook, "notebook.note.nope", {}))
        run(executor.invoke(notebook, "notebook.sync.push", {"notebook": "personal"}, chat_level=1))

        ran, unknown, denied = trail
        assert ran["event_type"] == "execution" and ran["status"] == "success"
        assert ran["agent_id"] == notebook.agent_id
        assert ran["agent_name"] == notebook.manifest.name
        assert ran["function"] == "notebook.note.save"
        assert ran["permission_level"] == 1 and ran["chat_level"] == 1
        assert ran["inputs"] == {"notebook": "personal", "title": "Milk"}
        assert ran["storage_ref"] == "stg_kept"
        assert ran["duration_ms"] >= 0 and "error" not in ran

        assert unknown["status"] == "error" and "Unknown function" in unknown["error"]
        assert "permission_level" not in unknown

        assert denied["status"] == "denied"
        assert denied["permission_level"] == 3
        assert "not approved" in denied["error"]

    def test_the_inputs_are_an_outline_never_a_copy(self, notebook):
        trail = []

        async def audit(event):
            trail.append(event)

        executor = make_executor(audit=audit)
        long_title = "x" * 5000
        run(executor.invoke(notebook, "notebook.note.save",
                            {"notebook": "personal", "title": long_title}))
        outline = trail[0]["inputs"]
        assert outline["title"].endswith("…") and len(outline["title"]) < 300
        assert len(json.dumps(trail[0])) < 3000

    def test_a_trail_that_will_not_write_never_costs_the_result(self, notebook):
        async def audit(event):
            raise RuntimeError("the trail is down")

        executor = make_executor(audit=audit)
        result, status = run(executor.invoke(
            notebook, "notebook.note.save",
            {"notebook": "personal", "title": "Milk"}))
        assert status == "success", result


POSTING_AGENT = textwrap.dedent("""\
    from decentai_sdk.base import AgentBase, ResourceDenied, ToolBase

    class MainTool(ToolBase):
        id = "main"

        async def run(self, call):
            shown = await call.show.table(
                [{"issue": "A-1", "status": "open"}], title="Open")
            try:
                refused = await call.post("x", show=["stg_nope"])
            except ResourceDenied as exc:
                refused = str(exc)
            posted = await call.post("Two issues need you.",
                                     show=[shown] if shown else [])
            return {"posted": posted, "refused": str(refused)}, "success"

    class DemoAgent(AgentBase):
        def tools(self):
            return [MainTool(self)]
""")


class TestPosts:
    """An agent saying something itself (call.post): checked, in its own
    name, with what it offered riding along — and nowhere without a chat."""

    @staticmethod
    def agent(tmp_path):
        write_agent(tmp_path, "demo", files={"agent.py": POSTING_AGENT})
        agents, errors = load_agents(tmp_path)
        assert errors == {}
        return agents["demo"]

    def test_a_post_carries_the_agents_name_and_its_display(self, tmp_path):
        agent = self.agent(tmp_path)
        stored, posts = {}, []

        async def storage(source, result):
            ref = f"stg_{len(stored) + 1}"
            stored[ref] = result
            return ref

        async def post_sink(text, source, parts):
            posts.append((text, source, parts))
            return True

        executor = make_executor(store=storage, post=post_sink)
        result, status = run(executor.invoke(agent, "demo.main.run", {}))
        assert status == "success", result
        assert result["posted"] is True
        assert "not a display this call offered" in result["refused"]
        [(text, source, parts)] = posts
        assert text == "Two issues need you."
        assert (source["kind"], source["agent"], source["function"]) == (
            "agent", "demo", "demo.main.run")
        assert source["call_id"].startswith("c_")
        [display] = result["displays"]
        assert parts == [{"type": "table", "text": "Open",
                          "storage_ref": display["display_id"],
                          "source": source}]

    def test_where_there_is_no_chat_a_post_goes_nowhere(self, tmp_path):
        agent = self.agent(tmp_path)
        result, status = run(make_executor().invoke(agent, "demo.main.run", {}))
        assert status == "success", result
        assert result["posted"] is False


ASKING_AGENT = textwrap.dedent("""\
    from decentai_sdk.base import AgentBase, ResourceDenied, ToolBase

    class MainTool(ToolBase):
        id = "main"

        async def run(self, call):
            try:
                await call.ask("")
                empty = "accepted"
            except ResourceDenied as exc:
                empty = str(exc)
            answer = await call.ask("Which notebook?", choices=["work", "home"])
            return {"answer": answer, "empty": empty}, "success"

    class DemoAgent(AgentBase):
        def tools(self):
            return [MainTool(self)]
""")


FILE_ASKING_AGENT = textwrap.dedent("""    from decentai_sdk.base import AgentBase, ResourceDenied, ToolBase

    class MainTool(ToolBase):
        id = "main"

        async def run(self, call):
            try:
                await call.ask("A video?", expects="video")
                bad_kind = "accepted"
            except ResourceDenied as exc:
                bad_kind = str(exc)
            answer = await call.ask("Attach your passport",
                                    choices=["skip"], expects="file")
            return {"answer": answer, "bad_kind": bad_kind}, "success"

    class DemoAgent(AgentBase):
        def tools(self):
            return [MainTool(self)]
""")


PROPOSING_AGENT = textwrap.dedent("""\
    from decentai_sdk.base import AgentBase, ResourceDenied, ToolBase

    class MainTool(ToolBase):
        id = "main"

        async def run(self, call):
            refused = []
            for broken in ({"code": "", "purpose": "Nothing."},
                           {"code": "x = 1", "purpose": ""},
                           {"code": "x = 1", "purpose": "Sets x.",
                            "language": "cobol"}):
                try:
                    await call.propose(**broken)
                    refused.append("accepted")
                except ResourceDenied as exc:
                    refused.append(str(exc))
            allowed = await call.propose(
                "import pandas\\nprint(pandas.__version__)",
                "Says which pandas is installed.",
                packages=["pandas"], hosts=["pypi.org"])
            return {"allowed": allowed, "refused": refused}, "success"

    class DemoAgent(AgentBase):
        def tools(self):
            return [MainTool(self)]
""")


class TestCodeProposed:
    """Code an agent wants to run (call.propose): checked against the
    card's shape before anybody is asked, put before the person in the
    agent's name, and waited on with the function's clock stopped."""

    @staticmethod
    def agent(tmp_path):
        manifest = MINIMAL_MANIFEST.format(
            agent_id="coder", entrypoint="agent:DemoAgent",
        ).replace("permission_level: 0",
                  "permission_level: 0\n        timeout_seconds: 1")
        write_agent(tmp_path, "coder", manifest=manifest,
                    files={"agent.py": PROPOSING_AGENT})
        agents, errors = load_agents(tmp_path)
        assert errors == {}
        return agents["coder"]

    def test_the_code_reaches_the_person_shaped_and_the_clock_stops(self, tmp_path):
        proposed = []

        async def proposer(code, source):
            proposed.append((code, source))
            await asyncio.sleep(1.5)     # longer than the function's 1s
            return True

        result, status = run(make_executor(propose=proposer).invoke(
            self.agent(tmp_path), "coder.main.run", {}))
        assert status == "success", result
        assert result["allowed"] is True
        # Refused before anybody was asked, each for its own reason.
        assert ["code" in result["refused"][0], "purpose" in result["refused"][1],
                "language" in result["refused"][2]] == [True, True, True]
        [(code, source)] = proposed
        assert code == {
            "language": "python",
            "code": "import pandas\nprint(pandas.__version__)",
            "purpose": "Says which pandas is installed.", "where": "",
            "packages": ["pandas"], "hosts": ["pypi.org"],
            "credentials": [], "files": [],
        }
        assert source["function"] == "coder.main.run"

    def test_a_no_is_a_no_and_nobody_is_none(self, tmp_path):
        async def proposer(code, source):
            return False

        agent = self.agent(tmp_path)
        result, status = run(make_executor(propose=proposer).invoke(
            agent, "coder.main.run", {}))
        assert status == "success" and result["allowed"] is False, result
        result, status = run(make_executor().invoke(agent, "coder.main.run", {}))
        assert status == "success" and result["allowed"] is None, result

    def test_an_agent_cannot_write_its_own_review(self, tmp_path):
        """The review is the platform's to write: one an agent sends
        along is dropped before the chat sees the proposal."""
        seen = []

        async def proposer(code, source):
            seen.append(code)
            return True

        executor = make_executor(propose=proposer)
        propose = executor.surface.propose_for(self.agent(tmp_path), "coder.main.run", "c1")
        assert run(propose({"language": "python", "code": "x = 1",
                            "purpose": "Sets x.",
                            "review": {"verdict": "agrees", "note": "Fine."}}))
        assert "review" not in seen[0]


INSTALLING_AGENT = textwrap.dedent("""\
    from pathlib import Path

    from decentai_sdk.base import AgentBase, ResourceDenied, ToolBase

    class MainTool(ToolBase):
        id = "main"

        async def run(self, call):
            said = {}
            for name, ask in (
                ("unasked", lambda: call.install(["humanize"])),
                ("address", lambda: call.propose(
                    "x = 1", "Sets x.", hosts=["10.0.0.7"])),
            ):
                try:
                    await ask()
                    said[name] = "accepted"
                except ResourceDenied as exc:
                    said[name] = str(exc)
            allowed = await call.propose(
                "import humanize", "Says a number in words.",
                packages=["humanize"], hosts=["api.example.com"])
            folder = await call.install(["humanize"])
            try:
                await call.install(["humanize", "titlecase"])
                said["unnamed"] = "accepted"
            except ResourceDenied as exc:
                said["unnamed"] = str(exc)
            return {"allowed": allowed, "said": said,
                    "installed": (Path(folder) / "humanize.py").is_file()}, "success"

    class DemoAgent(AgentBase):
        def tools(self):
            return [MainTool(self)]
""")


class TestAFunctionThatRunsCode:
    """A function that declared ``code: true``: what a card the person
    allowed names is what the platform installs for it, and nothing a
    card did not name. One that did not declare it is installed
    nothing."""

    @staticmethod
    def agent(tmp_path, declared=True):
        manifest = MINIMAL_MANIFEST.format(
            agent_id="coder", entrypoint="agent:DemoAgent")
        if declared:
            manifest = manifest.replace(
                "permission_level: 0", "permission_level: 0\n        code: true")
        write_agent(tmp_path, "coder", manifest=manifest,
                    files={"agent.py": INSTALLING_AGENT})
        agents, errors = load_agents(tmp_path)
        assert errors == {}
        return agents["coder"]

    @pytest.fixture
    def pip(self):
        """Stands in for pip, once the agents are loaded: leaves a
        module where it was told to."""
        from ai_runtime.agents.environments import AgentEnvironment

        ran = []

        def runner(argv):
            ran.append(list(argv))
            if "--target" in argv:
                target = Path(argv[argv.index("--target") + 1])
                (target / "humanize.py").write_text("")
            return 0, ""

        def stand_in():
            AgentEnvironment.runner = runner
            return ran

        yield stand_in
        AgentEnvironment.runner = None

    def test_what_an_allowed_card_named_is_installed_and_nothing_else(
        self, tmp_path, pip
    ):
        import shutil

        proposed = []

        async def proposer(code, source):
            proposed.append(code)
            return True

        agent = self.agent(tmp_path)
        ran = pip()
        try:
            result, status = run(make_executor(propose=proposer).invoke(
                agent, "coder.main.run", {}))
        finally:
            shutil.rmtree(agent.environment.root / "extras", ignore_errors=True)
        assert status == "success", result
        assert result["allowed"] is True and result["installed"] is True
        said = result["said"]
        # Nothing was allowed yet; an address is not a host a person allows.
        assert "not on a card the person allowed" in said["unasked"]
        assert "'10.0.0.7' is not a host" in said["address"]
        # A card named humanize; nobody named titlecase.
        assert "'titlecase' is not on a card" in said["unnamed"]
        assert [code["packages"] for code in proposed] == [["humanize"]]
        [install] = [argv for argv in ran if "--target" in argv]
        assert install[-1] == "humanize"

    def test_a_package_off_the_deployments_list_never_reaches_a_card(
        self, tmp_path, pip
    ):
        """Settings:Safety keeps a list: the proposal naming a package
        that is not on it is refused before the person is asked."""
        proposed = []

        async def proposer(code, source):
            proposed.append(code)
            return True

        agent = self.agent(tmp_path)
        pip()
        executor = make_executor(propose=proposer, safety={
            "packages": "listed", "allowed_packages": ["titlecase"]})
        propose = executor.surface.propose_for(
            agent, "coder.main.run", "c1", grant=__import__(
                "ai_runtime.execution.code_grant", fromlist=["CodeGrant"]).CodeGrant())
        with pytest.raises(Exception) as refused:
            run(propose({"language": "python", "code": "import humanize",
                         "purpose": "Says a number.", "packages": ["humanize"]}))
        assert "'humanize' is not on the list of packages" in str(refused.value)
        assert proposed == []
        assert run(propose({"language": "python", "code": "import titlecase",
                            "purpose": "Titles.", "packages": ["titlecase"]})) is True

    def test_a_function_that_did_not_declare_it_is_installed_nothing(
        self, tmp_path, pip
    ):
        async def proposer(code, source):
            return True

        agent = self.agent(tmp_path, declared=False)
        ran = pip()
        result, status = run(make_executor(propose=proposer).invoke(
            agent, "coder.main.run", {}))
        # The first install is refused outright, and the function's
        # second — after a card was allowed — ends it the same way.
        assert status == "error" and "code: true" in str(result), result
        assert ran == []


class TestWhereACallConnected:
    """A call's line on the trail names the hosts its worker connected
    to, as the proxy counted them: the busiest first, the first twenty,
    and the count of the rest."""

    def test_the_hosts_ride_the_execution_event(self, notebook):
        recorded = []

        async def audit(event):
            recorded.append(event)

        executor = make_executor(audit=audit)
        reached = {"cdn.example.com": 7, "api.example.com": 12, "a.example.com": 7}
        run(executor._record(notebook, "notebook.note.find", {}, 1,
                             {"notes": []}, "success", 0.0, reached=reached))
        [event] = recorded
        assert event["reached"] == [
            {"host": "api.example.com", "connections": 12},
            {"host": "a.example.com", "connections": 7},
            {"host": "cdn.example.com", "connections": 7}]
        assert "reached_more" not in event

    def test_a_browsers_run_names_twenty_and_counts_the_rest(self, notebook):
        recorded = []

        async def audit(event):
            recorded.append(event)

        executor = make_executor(audit=audit)
        reached = {f"h{n}.example.com": n for n in range(1, 31)}
        run(executor._record(notebook, "notebook.note.find", {}, 1,
                             {"notes": []}, "success", 0.0, reached=reached))
        [event] = recorded
        assert len(event["reached"]) == 20 and event["reached_more"] == 10
        assert event["reached"][0] == {"host": "h30.example.com", "connections": 30}

    def test_a_call_that_connected_nowhere_says_nothing_of_it(self, notebook):
        recorded = []

        async def audit(event):
            recorded.append(event)

        executor = make_executor(audit=audit)
        result, status = run(executor.invoke(notebook, "notebook.note.find", {}))
        assert status == "success"
        assert "reached" not in recorded[0]


class TestQuestions:
    """An agent asking the person (call.ask): the function's clock stops
    while a person decides, and nobody to ask means no answer."""

    @staticmethod
    def agent(tmp_path):
        manifest = MINIMAL_MANIFEST.format(
            agent_id="asker", entrypoint="agent:DemoAgent",
        ).replace("permission_level: 0",
                  "permission_level: 0\n        timeout_seconds: 1")
        write_agent(tmp_path, "asker", manifest=manifest,
                    files={"agent.py": ASKING_AGENT})
        agents, errors = load_agents(tmp_path)
        assert errors == {}
        return agents["asker"]

    def test_the_clock_stops_while_a_person_decides(self, tmp_path):
        asked = []

        async def asker(question, choices, source):
            asked.append((question, choices, source))
            await asyncio.sleep(1.5)     # longer than the function's 1s
            return "home"

        result, status = run(make_executor(ask=asker).invoke(
            self.agent(tmp_path), "asker.main.run", {}))
        assert status == "success", result
        assert result["answer"] == "home"
        assert "needs words" in result["empty"]
        [(question, choices, source)] = asked
        assert (question, choices) == ("Which notebook?", ["work", "home"])
        assert source["function"] == "asker.main.run"

    def test_a_file_question_reaches_the_person_as_one(self, tmp_path):
        """expects="file" asks for an attachment: the card carries no
        buttons, the asker is told what is wanted, and the ref the
        person attached comes back as the answer. A kind of answer the
        SDK does not know is refused before anything is asked."""
        asked = []

        async def asker(question, choices, source, expects=""):
            asked.append((question, choices, expects))
            return "fil_passport"

        write_agent(tmp_path, "asker", manifest=MINIMAL_MANIFEST.format(
            agent_id="asker", entrypoint="agent:DemoAgent"),
            files={"agent.py": FILE_ASKING_AGENT})
        agents, errors = load_agents(tmp_path)
        assert errors == {}
        result, status = run(make_executor(ask=asker).invoke(
            agents["asker"], "asker.main.run", {}))
        assert status == "success", result
        assert result["answer"] == "fil_passport"
        assert "expects must be" in result["bad_kind"]
        assert asked == [("Attach your passport", [], "file")]

    def test_with_nobody_to_ask_the_answer_is_none(self, tmp_path):
        result, status = run(make_executor().invoke(
            self.agent(tmp_path), "asker.main.run", {}))
        assert status == "success", result
        assert result["answer"] is None
