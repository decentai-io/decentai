"""Pull by digest (docs/system/agent-code.md): a session materializes the
code its contract names, and everything outside the package speaks the
approval's ref while the package keeps its own name.

Real library in an empty directory, real worker over the real wire; the
sim plays the platform, with a governing contract and a pull door.
"""

import asyncio
import base64
import json

import pytest

from ai_runtime.agents import AgentLibrary, ApprovedAgent
from ai_runtime.chat.scheduler import Schedule, ScheduleRunner
from ai_runtime.execution.executor import FunctionExecutor
from ai_runtime.execution.grants import FunctionGrants
from ai_runtime.server.host import ServingRoster, SessionHost
from ai_runtime.tests.fixture_agents import (
    AGENTS_DIR, load_agents, worker_environment,
)
from contracts.agent_manifest import load_manifest, manifest_hash
from contracts.agent_package import AgentPackage
from sim.resources import InMemoryResourceProvider
from sim.session_services import SimSessionServices

NOTEBOOK = AGENTS_DIR / "notebook"
REF = "agt_0ba09c35d8324040b6bd"


def run(awaitable):
    return asyncio.run(awaitable)


def action(**kwargs):
    return json.dumps(kwargs)


def approved_notebook():
    agents, errors = load_agents(AGENTS_DIR)
    assert errors == {}
    return ApprovedAgent(agents["notebook"], REF)


class PullingServices(SimSessionServices):
    """The sim with a governing contract — the notebook named by its
    approval — and a pull door serving one archive."""

    def __init__(self, archive, digest, expected_hash, grants=None):
        super().__init__()
        self.archive = archive
        self.digest = digest
        self.expected_hash = expected_hash
        self.grants = self.GRANTS if grants is None else grants
        self.pulls = []

    async def contract(self, chat_id):
        base = await super().contract(chat_id)
        return {**base, "grants": self.grants, "agents": [{
            "agent_id": REF, "name": "Notebook", "local_agent_id": "notebook",
            "package_digest": self.digest, "manifest_hash": self.expected_hash,
        }]}

    async def fetch_package(self, chat_id, agent_id):
        self.pulls.append((chat_id, agent_id))
        return {
            "agent_id": agent_id, "package_digest": self.digest,
            "manifest_hash": self.expected_hash, "local_agent_id": "notebook",
            "package": base64.b64encode(self.archive).decode("ascii"),
        }


@pytest.fixture()
def library(tmp_path, monkeypatch):
    """A real library in an empty directory. Its environments are the
    fixtures' shared venv: the notebook declares no dependencies, so a
    venv per digest would only cost the time to build it."""
    shared = worker_environment()
    monkeypatch.setattr(AgentLibrary, "environment",
                        lambda self, digest: shared)
    monkeypatch.setattr(AgentLibrary, "environment_for",
                        lambda self, dependencies: shared)
    return AgentLibrary(tmp_path / "runtime")


@pytest.fixture()
def package():
    """(archive, digest, manifest hash) — the notebook as the backend
    would package and approve it."""
    archive, digest = AgentPackage.build(NOTEBOOK)
    manifest, errors = load_manifest(NOTEBOOK / "manifest.yaml")
    assert errors == []
    return archive, digest, manifest_hash(manifest.document)


class TestTwoNames:
    def test_the_approval_answers_to_the_ref_the_package_to_its_own_name(self):
        approved = approved_notebook()
        assert approved.agent_id == REF
        assert approved.local_agent_id == "notebook"
        assert approved.declared(f"{REF}.note.save") == "notebook.note.save"
        assert approved.granted("notebook.note.save") == f"{REF}.note.save"
        # A name that is not this agent's is not rewritten into one.
        assert approved.declared("other.note.save") == "other.note.save"

    def test_grants_and_resources_speak_the_ref_the_worker_the_package(self):
        provider = InMemoryResourceProvider()
        executor = FunctionExecutor(provider=provider, grants=FunctionGrants([
            {"effect": "allow", "functions": [f"{REF}.*.*"]}]))
        result, status = run(executor.invoke(
            approved_notebook(), f"{REF}.note.save",
            {"notebook": "work", "title": "Ship it"}))
        assert status == "success", result
        # The record lives where the platform keyed it: under the ref.
        assert list(provider.data) == [f"{REF}__note"]

    def test_a_grant_in_the_packages_own_name_reaches_nothing(self):
        """Grants are written in the platform's vocabulary. The other
        direction denies everything — and must, loudly."""
        executor = FunctionExecutor(
            provider=InMemoryResourceProvider(),
            grants=FunctionGrants([
                {"effect": "allow", "functions": ["notebook.*.*"]}]))
        result, status = run(executor.invoke(
            approved_notebook(), f"{REF}.note.save",
            {"notebook": "work", "title": "Ship it"}))
        assert status == "error"
        assert result.get("not_permitted") is True


class TestThePull:
    def test_a_build_pulls_what_the_contract_names_once(self, library, package):
        archive, digest, expected = package
        services = PullingServices(archive, digest, expected)
        host = SessionHost(services, library)

        async def scenario():
            first = await host.session("chat_1")
            second = await host.session("chat_2")
            return first, second

        first, second = run(scenario())
        assert list(first.roster) == [REF]
        assert isinstance(first.roster[REF], ApprovedAgent)
        assert second.roster[REF].digest == digest
        assert library.has(digest)
        assert library.agent(digest).local_agent_id == "notebook"
        # Two chats, one pull: the digest is the whole of "must I fetch?".
        assert services.pulls == [("chat_1", REF)]

    def test_the_pulled_code_runs_under_the_ref(self, library, package):
        archive, digest, expected = package
        services = PullingServices(archive, digest, expected, grants=[
            {"effect": "allow", "functions": [f"{REF}.*.*"]}])
        services.contracts["chat_1"] = {"llm": {"provider": "fake", "responses": [
            action(action="open_agent", agent=REF),
            action(action="invoke", function=f"{REF}.note.save",
                   inputs={"notebook": "work", "title": "Ship it"}),
            action(action="say", text="Saved."),
            action(action="finish"),
        ]}}
        host = SessionHost(services, library)

        async def scenario():
            await host.handle("chat_1", {"event": "user_message",
                                         "text": "save a note"})
            await host.sessions["chat_1"].wait_idle()

        run(scenario())
        assert list(services.provider.data) == [f"{REF}__note"]
        entry = host.sessions["chat_1"].assistant.state.trace[-1]
        assert (entry["agent"], entry["function"], entry["status"]) == (
            REF, f"{REF}.note.save", "success")
        # Evidence found the function under the ref: the write earned
        # its verified line beside the reply.
        reply = services.messages["chat_1"][-1]
        assert reply["parts"][0]["content"] == "Saved."
        # The write is recorded on the message, under the approval's ref.
        assert [p["text"] for p in reply["parts"]
                if p.get("type") == "success"] == ["Verified: Save Note"]

    def test_tampered_bytes_are_refused_and_the_chat_still_opens(
            self, library, package):
        archive, digest, expected = package
        tampered = archive[:-1] + bytes([archive[-1] ^ 0xFF])
        services = PullingServices(tampered, digest, expected)
        host = SessionHost(services, library)

        async def scenario():
            return await host.session("chat_1"), await host.session("chat_2")

        first, second = run(scenario())
        assert first.roster == {} and second.roster == {}
        assert not library.has(digest)
        assert "digest" in host._refused[digest]
        # The same bytes fail the same way: refused once, not per chat.
        assert len(services.pulls) == 1

    def test_the_clock_resolves_a_row_through_the_chats_roster(
            self, library, package):
        """A schedule row names the function by the ref; the runner asks
        the host for that chat's roster — materialized, no mind built."""
        archive, digest, expected = package
        services = PullingServices(archive, digest, expected)
        host = SessionHost(services, library)
        runner = ScheduleRunner(
            ServingRoster(host),
            FunctionExecutor(provider=services.provider), host.deliver_event)
        due = Schedule("chat_1", "invoke", function=f"{REF}.note.find",
                       inputs={})

        outcome = run(runner.fire(due))
        assert outcome["status"] == "success", outcome
        assert outcome["result"]["notes"] == [] and outcome["result"]["total"] == 0
        assert host.sessions == {}
        assert services.pulls == [("chat_1", REF)]
        # The fire left its line on the chat's trail.
        assert [(e["chat_id"], e["function"]) for e in services.audit] == [
            ("chat_1", f"{REF}.note.find")]

    def test_a_fire_is_held_to_the_chats_grants(self, library, package):
        """A schedule runs as its chat: a function the person was not
        given — or was given and lost — does not run on the clock."""
        archive, digest, expected = package
        services = PullingServices(archive, digest, expected, grants=[
            {"effect": "allow", "functions": [f"{REF}.note.save"]}])
        host = SessionHost(services, library)
        runner = ScheduleRunner(
            ServingRoster(host),
            FunctionExecutor(provider=services.provider), host.deliver_event)

        outcome = run(runner.fire(Schedule(
            "chat_1", "invoke", function=f"{REF}.note.find", inputs={})))
        assert outcome["status"] == "error", outcome
        assert outcome["result"]["not_permitted"] is True


class TestTheAudienceHearsTheWait:
    """A first open waits on a fetch and a build; the door narrates it
    on the socket, before the hello, and only when there is a wait."""

    def test_a_first_open_is_narrated_then_greeted(self, library, package):
        from ai_runtime.tests.test_session_door import FakeSocket

        archive, digest, expected = package
        services = PullingServices(archive, digest, expected)
        host = SessionHost(services, library)
        first, second = FakeSocket(), FakeSocket()

        async def scenario():
            await host.attach(first, "chat_1")
            await host.attach(second, "chat_2")

        run(scenario())
        events = [f["event"] for f in first.sent]
        assert events == ["agent_status"] * 3 + ["hello"]
        assert [f["phase"] for f in first.heard("agent_status")] == [
            "pulling", "installing", "ready"]
        assert all(f["name"] == "Notebook" and f["agent"] == REF
                   for f in first.heard("agent_status"))
        assert "Preparing Notebook" in first.sent[0]["text"]
        # Held already: the second audience is greeted at once.
        assert [f["event"] for f in second.sent] == ["hello"]
        # A courtesy, not a record.
        assert not [e for e in services.events
                    if e.get("event") == "agent_status"]

    def test_a_refusal_is_told_not_hidden(self, library, package):
        from ai_runtime.tests.test_session_door import FakeSocket

        archive, digest, expected = package
        tampered = archive[:-1] + bytes([archive[-1] ^ 0xFF])
        services = PullingServices(tampered, digest, expected)
        host = SessionHost(services, library)
        socket = FakeSocket()

        run(host.attach(socket, "chat_1"))
        phases = [f["phase"] for f in socket.heard("agent_status")]
        assert phases == ["pulling", "installing", "failed"]
        assert "digest" in socket.heard("agent_status")[-1]["text"]
        assert socket.heard("hello")


class TestReclaimingTheDisk:
    """A package installs once and serves everyone who approved those
    bytes, so no uninstall may delete it. The host sweeps instead: what
    the platform still approves anywhere, plus what this process is
    using, stays; the rest goes with its environment."""

    @staticmethod
    def _stranded(library, digest):
        """A second package on the disk that no approval names — what a
        sweep exists to remove."""
        other = library.store_dir / ("b" * 64)
        other.mkdir(parents=True)
        (other / "manifest.yaml").write_text("agent: {}", encoding="utf-8")
        return f"sha256:{'b' * 64}"

    def test_an_install_is_prepared_before_any_chat_asks(self, library, package):
        """The platform says an agent was installed; the code is pulled
        and its environment built at once, with the chat's delegation,
        so a session opened later finds it serving and pulls nothing."""
        archive, digest, expected = package
        services = PullingServices(archive, digest, expected)
        host = SessionHost(services, library)

        async def scenario():
            await host.handle("chat_1", {"event": "agents_changed", "agents": [{
                "agent_id": REF, "name": "Notebook", "package_digest": digest,
                "manifest_hash": expected}]})
            deadline = asyncio.get_running_loop().time() + 30
            while library.agent(digest) is None or REF not in services.prepared:
                assert asyncio.get_running_loop().time() < deadline, "not prepared"
                await asyncio.sleep(0.05)
            session = await host.session("chat_2")
            await asyncio.sleep(0.05)
            return session

        session = run(scenario())
        assert session.roster[REF].digest == digest
        assert services.pulls == [("chat_1", REF)]
        # The platform was told once, by the chat that built it — the
        # second chat found it serving and said nothing new.
        # With it, what this process holds the agent to: nothing, where
        # nothing confines — a test, a developer's machine.
        assert services.prepared[REF] == {
            "digest": digest, "state": "ready", "error": "",
            "confined": {"user": False, "files": False, "network": False}}
        assert services.prepared_reports == [("chat_1", REF, digest, "ready")]

    def test_code_nobody_approves_is_forgotten(self, library, package):
        archive, digest, expected = package
        services = PullingServices(archive, digest, expected)
        host = SessionHost(services, library)

        async def scenario():
            await host.session("chat_1")            # installs the notebook
            stranded = self._stranded(library, digest)
            # The platform approves the notebook and nothing else, and
            # the session that holds it has been forgotten.
            services.pinned = {digest}
            host.sessions.clear()
            removed = await host.reclaim("chat_1")
            return stranded, removed

        stranded, removed = run(scenario())
        assert removed == 1
        assert not library.has(stranded)
        assert library.has(digest)

    def test_what_a_live_session_holds_survives(self, library, package):
        """A session keeps its roster for as long as it lives, so its
        code must outlive the sweep whether or not a call is in flight."""
        archive, digest, expected = package
        services = PullingServices(archive, digest, expected)
        host = SessionHost(services, library)

        async def scenario():
            await host.session("chat_1")
            # The platform has forgotten it — uninstalled everywhere —
            # but this process is still serving it.
            services.pinned = {"sha256:" + "c" * 64}
            return await host.reclaim("chat_1")

        assert run(scenario()) == 0
        assert library.has(digest)

    def test_a_platform_that_will_not_answer_deletes_nothing(
            self, library, package):
        archive, digest, expected = package
        services = PullingServices(archive, digest, expected)
        host = SessionHost(services, library)

        async def refuse(chat_id):
            raise RuntimeError("the platform is unreachable")

        async def scenario():
            await host.session("chat_1")
            stranded = self._stranded(library, digest)
            host.sessions.clear()
            services.pinned_digests = refuse
            return stranded, await host.reclaim("chat_1")

        stranded, removed = run(scenario())
        assert removed == 0
        assert library.has(stranded)

    def test_an_empty_answer_is_distrusted_not_obeyed(self, library, package):
        """A platform that has forgotten every approval is a platform to
        distrust: an empty set must not empty the disk."""
        archive, digest, expected = package
        services = PullingServices(archive, digest, expected)
        host = SessionHost(services, library)

        async def scenario():
            await host.session("chat_1")
            stranded = self._stranded(library, digest)
            host.sessions.clear()
            services.pinned = set()
            return stranded, await host.reclaim("chat_1")

        stranded, removed = run(scenario())
        assert removed == 0
        assert library.has(stranded) and library.has(digest)

    def test_a_package_that_never_arrived_is_asked_for_again(
            self, library, package):
        """A pull the platform would not answer says nothing about the
        bytes. Remembering it hid an installed agent from every chat
        until the process restarted; the next chat asks again."""
        archive, digest, expected = package
        services = PullingServices(archive, digest, expected)
        refusals = []

        async def refuse(chat_id, agent_id):
            refusals.append(chat_id)
            raise RuntimeError(
                "Agents:Agent:Fetch_package failed (401): unauthorized")

        services.fetch_package = refuse
        host = SessionHost(services, library)

        async def scenario():
            first = await host.session("chat_1")
            assert first.roster == {} and digest not in host._refused
            # The moment passes: the door answers, and the agent is there
            # without anyone restarting anything.
            services.fetch_package = PullingServices.fetch_package.__get__(
                services, PullingServices)
            return await host.session("chat_2")

        second = run(scenario())
        assert refusals == ["chat_1"]
        assert list(second.roster) == [REF]
        assert library.has(digest)

    def test_a_refused_package_is_retried_after_it_is_reclaimed(
            self, library, package):
        """The negative cache remembers bytes, and the bytes are gone —
        so a later install of the same digest starts clean."""
        archive, digest, expected = package
        tampered = archive[:-1] + bytes([archive[-1] ^ 0xFF])
        services = PullingServices(tampered, digest, expected)
        host = SessionHost(services, library)

        async def scenario():
            await host.session("chat_1")            # refuses the tampered bytes
            assert digest in host._refused
            services.pinned = {"sha256:" + "d" * 64}
            host.sessions.clear()
            await host.reclaim("chat_1")

        run(scenario())
        assert digest not in host._refused
