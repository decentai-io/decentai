"""The two halves meet (docs/system/chat-session.md).

A person's socket into the Data Core; the Data Core's dial to a REAL
runtime process serving the same code main.py serves; the runtime's
services calls back into the Data Core over /app as the chat's
delegation; the answer relayed home. The live smoke test, automated —
nothing here is stubbed but the model.
"""

import asyncio
import dataclasses
import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import app_call, service_keys
from ai_runtime.tests.fake_container import confinement


def action(**kwargs):
    return json.dumps(kwargs)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def cookie_header(client):
    return {"Cookie": f"access_token={client.cookies['access_token']}"}


def serve(app, port):
    """One uvicorn server in a thread, lifespan off: the Data Core's app
    state is the test session's, and must not be reset by a shutdown.
    Its loop is kept on the server (``server.loop``), so a test can run
    what the lifespan would have on it — the relay's lease tick."""
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="critical",
        lifespan="off",
    ))
    server.loop = asyncio.new_event_loop()

    def run():
        asyncio.set_event_loop(server.loop)
        server.loop.run_until_complete(server.serve())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return server, thread


class ServedNode:
    """One execution node, built as main.py builds one, linked to the
    served Data Core from a loop of its own — as a node on another
    machine would be: it dials, and the Data Core reaches it only over
    that link (docs/system/execution-nodes.md)."""

    def __init__(self, data_core_url, data_core_public, authority_private,
                 name, folder):
        from ai_runtime.agents.library import AgentLibrary
        from ai_runtime.door.app import create_app as create_runtime_app
        from ai_runtime.door.node import DataCoreKey, NodeCredential
        from ai_runtime.door.platform import DataCoreClient, Gateway
        from ai_runtime.door.settings import RuntimeSettings
        from ai_runtime.tests.fake_container import confinement, container
        from conftest import runtime_service_token

        self.name = name
        # A credential a registered authority signed, as the install's
        # issuer signs the bundled node's. The node holds the
        # credential and never the authority's key.
        node = NodeCredential.fixed(runtime_service_token(authority_private, name))
        key = DataCoreKey(data_core_public)
        self.app = create_runtime_app(
            RuntimeSettings(data_core_service_public_key=data_core_public,
                            data_core_url=data_core_url,
                            agents_install_dir=str(folder)),
            services=DataCoreClient(Gateway(data_core_url, node=node, key=key), node),
            library=AgentLibrary(folder, confinement()),
            container=container(), confinement=confinement(),
            node=node, data_core=key,
        )
        self.link = self.app.state.link
        self.link.BACKOFF_SECONDS = (0.2,)
        # Beats often enough for a test's short leases.
        self.link.HEARTBEAT_SECONDS = 0.5
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def _on_loop(self, make, timeout=15):
        async def call():
            return await make()
        return asyncio.run_coroutine_threadsafe(call(), self.loop).result(timeout)

    def start(self, wait=True):
        async def begin():
            self.link.start()
        self._on_loop(begin)
        if wait:
            self.wait_linked()
        return self

    def wait_linked(self, timeout=15):
        deadline = time.monotonic() + timeout
        while not self.link.connected.is_set():
            assert time.monotonic() < deadline, f"node {self.name} never linked"
            time.sleep(0.02)

    def stop(self):
        """The node goes, as a process that ends: its link closes and
        its sessions are abandoned where they stand."""
        try:
            self._on_loop(self.link.stop)
        except Exception:
            pass
        self.app.state.host.shutdown()

    def close(self):
        self.stop()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


@pytest.fixture()
def full_stack(app, seed, tmp_path, monkeypatch):
    """The Data Core served over HTTP, and a real execution node linked
    to it — the node dials; the Data Core holds no address of it.

    The node starts EMPTY, with a real library in a fresh directory:
    the code it serves is whatever a chat's contract makes it pull
    (docs/system/agent-code.md). Its environments are the fixtures' shared
    venv — the fixture agents declare no dependencies."""
    from ai_runtime.agents.library import AgentLibrary
    from ai_runtime.tests.fixture_agents import worker_environment
    from conftest import register_authority
    from server.setup.app_state import get_state

    private_pem, public_pem = service_keys()
    authority_private, authority_public = service_keys()
    register_authority("shared", authority_public)

    state = get_state()
    original = state.settings
    # The Data Core's own key, which signs everything it tells a node.
    state.settings = dataclasses.replace(
        original, data_core_service_private_key=private_pem)

    data_core_port = free_port()
    data_core_url = f"http://127.0.0.1:{data_core_port}"
    data_core_server, data_core_thread = serve(app, data_core_port)

    shared = worker_environment()
    monkeypatch.setattr(AgentLibrary, "environment",
                        lambda self, digest: shared)

    deadline = time.monotonic() + 10
    while not data_core_server.started:
        assert time.monotonic() < deadline, "the Data Core never started"
        time.sleep(0.02)

    nodes = []

    def node(name="stack", start=True, authority=None):
        """A node the shared authority vouches for — or ``authority``,
        an organization's private key."""
        made = ServedNode(data_core_url, public_pem, authority or authority_private,
                          name, tmp_path / ("runtime" if not nodes else name))
        nodes.append(made)
        return made.start() if start else made

    first = node()
    try:
        def on_data_core(make, timeout=30):
            """Run one of the Data Core's own coroutines on its loop."""
            async def call():
                return await make()
            return asyncio.run_coroutine_threadsafe(
                call(), data_core_server.loop).result(timeout)

        yield SimpleNamespace(data_core_url=data_core_url, runtime_app=first.app,
                              node=first, add_node=node,
                              authority_private=authority_private,
                              on_data_core=on_data_core)
    finally:
        for made in nodes:
            made.close()
        state.settings = original
        data_core_server.should_exit = True
        data_core_thread.join(timeout=5)


def receive(frontend, timeout=30.0):
    """One frame's data, or a failure — the test client's receive blocks
    forever, and a runtime that never answers must fail, not hang."""
    box = {}

    def take():
        box["frame"] = frontend.receive_json()

    worker = threading.Thread(target=take, daemon=True)
    worker.start()
    worker.join(timeout)
    assert "frame" in box, f"no frame within {timeout}s"
    return box["frame"]["data"]


class ServedSocket:
    """The person's socket into the SERVED Data Core, as a browser's is.

    The test client runs every request on an event loop of its own, and
    the Data Core's dial to the runtime (relay.py) — its socket, its
    locks, its reader — lives on the loop of the frontend socket that
    opened it. A decision posted through the test client while that
    socket is open writes to the dial from another loop, which works or
    hangs as the scheduling falls. Here the socket and the decision both
    reach the served Data Core, so the dial lives and is written on one
    loop, as it is in a deployment."""

    def __init__(self, data_core_url, client, path):
        from websockets.sync.client import connect

        self.data_core_url = data_core_url
        self.headers = cookie_header(client)
        self.socket = connect(
            data_core_url.replace("http://", "ws://") + path,
            additional_headers=self.headers, open_timeout=10)

    def receive_json(self):
        return json.loads(self.socket.recv())

    def send_json(self, frame):
        self.socket.send(json.dumps(frame))

    def call(self, endpoint, data):
        """One /app call as the person, to the served Data Core."""
        import httpx

        return httpx.post(f"{self.data_core_url}/app", headers=self.headers,
                          json={"endpoint": endpoint, "data": data},
                          timeout=30)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.socket.close()


def until_ai_message(frontend, timeout=30.0):
    """Frames off the person's socket until the assistant's reply."""
    seen = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = receive(frontend, deadline - time.monotonic())
        seen.append(frame)
        if (frame.get("event") == "message_created"
                and frame["message"]["actor"] == "ai"):
            return seen
        assert frame.get("event") not in ("error", "runtime_disconnected",
                                          "runtime_unavailable"), frame
    raise AssertionError(f"no reply; saw {[f.get('event') for f in seen]}")


class TestTheTwoHalves:
    def test_a_conversation_round_trips_and_the_mind_survives_a_reconnect(
        self, admin, full_stack
    ):
        from database.stores import ChatStore

        opened = app_call(admin, "AI:Chat:Open", {"title": "Smoke"})
        assert opened.status_code == 200, opened.text
        chat_id = opened.json()["data"]["chat"]["chat_id"]

        # The scripted model, chosen through the chat's own settings —
        # the block reaches the runtime through AI:Chat:Contract.
        configured = app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id, "config": {"llm": {
                "provider": "fake", "responses": [
                    action(action="say", text="Hello from the runtime."),
                    action(action="finish"),
                    action(action="say", text="Still here."),
                    action(action="finish"),
                ]}}})
        assert configured.status_code == 200, configured.text

        with ServedSocket(full_stack.data_core_url, admin,
                          f"/chats/{chat_id}") as frontend:
            hello = receive(frontend)
            assert hello["event"] == "hello", hello
            assert hello["chat_id"] == chat_id

            frontend.send_json({"endpoint": "AI:Chat:Input",
                                "data": {"text": "hi", "parts": []}})
            seen = until_ai_message(frontend)
            actors = [f["message"]["actor"] for f in seen
                      if f.get("event") == "message_created"]
            assert actors == ["user", "ai"]
            assert seen[-1]["message"]["parts"][0]["content"] == \
                "Hello from the runtime."

        # A new audience: the same mind, hydrated from the Data Core. The
        # first turn's tail (it goes idle after answering) may still be
        # on its way when the new socket opens; nothing else may come
        # before the greeting.
        with ServedSocket(full_stack.data_core_url, admin,
                          f"/chats/{chat_id}") as frontend:
            frame = receive(frontend)
            while frame["event"] != "hello":
                assert frame["event"] in ("idle", "working"), frame
                frame = receive(frontend)
            frontend.send_json({"endpoint": "AI:Chat:Input",
                                "data": {"text": "again?", "parts": []}})
            seen = until_ai_message(frontend)
            assert seen[-1]["message"]["parts"][0]["content"] == "Still here."

        # The records are the Data Core's, and whole.
        listed = app_call(admin, "AI:Message:List", {"chat_id": chat_id})
        texts = [m["parts"][0]["content"]
                 for m in listed.json()["data"]["messages"]]
        assert texts == ["hi", "Hello from the runtime.", "again?",
                         "Still here."]

        document = ChatStore().by_chat_id(chat_id)
        state = document["state"]
        assert state["version"] == 1
        roles = [m["role"] for m in state["messages"]]
        assert roles.count("system") == 1          # one continuous mind
        assert state["cursor"] == 2                 # both messages absorbed

        narration = app_call(admin, "AI:Event:List",
                             {"chat_id": chat_id}).json()["data"]["events"]
        assert [e["event"]["event"] for e in narration].count(
            "message_created") == 4


class TestTheOrganizationsRules:
    def test_the_rules_written_on_the_page_are_in_the_frame_and_an_edit_in_the_next(
        self, admin, full_stack
    ):
        from database.stores import ChatStore

        written = app_call(admin, "Settings:Guardrail:Update",
                           {"text": "Answer in French."})
        assert written.status_code == 200, written.text
        chat_id = app_call(admin, "AI:Chat:Open", {"title": "Rules"}).json()["data"]["chat"]["chat_id"]
        assert app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id, "config": {"llm": {
                "provider": "fake", "responses": [
                    action(action="say", text="Bonjour."),
                    action(action="finish"),
                    action(action="say", text="Hello."),
                    action(action="finish"),
                ]}}}).status_code == 200

        def frame(saying):
            # The mind is saved as its beat ends, a moment after the
            # reply is sent.
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                state = ChatStore().by_chat_id(chat_id).get("state") or {}
                content = (state.get("messages") or [{}])[0].get("content", "")
                if saying in content:
                    return content
                time.sleep(0.1)
            raise AssertionError(f"the saved frame never said {saying!r}")

        with ServedSocket(full_stack.data_core_url, admin,
                          f"/chats/{chat_id}") as frontend:
            assert receive(frontend)["event"] == "hello"
            frontend.send_json({"endpoint": "AI:Chat:Input",
                                "data": {"text": "hi", "parts": []}})
            until_ai_message(frontend)
            first = frame("Answer in French.")
            assert "ORGANIZATION'S OWN RULES" in first
            assert "Answer in French." in first
            # In the frame's stable front: before the first block that
            # changes; the plan is no part of the saved frame at all.
            assert first.index("Answer in French.") < first.index("\nSKILLS\n")
            assert "\nPLAN\n" not in first

            # Edited while the chat is open: its next turn reads the new.
            app_call(admin, "Settings:Guardrail:Update", {"text": "Answer in English."})
            frontend.send_json({"endpoint": "AI:Chat:Input",
                                "data": {"text": "again", "parts": []}})
            until_ai_message(frontend)
        second = frame("Answer in English.")
        assert "Answer in English." in second and "French" not in second


class TestASkillFromTheChat:
    def test_the_person_saves_what_the_card_shows_and_the_next_frame_lists_it(
        self, admin, full_stack
    ):
        """save_skill end to end: the runtime opens the card under the
        chat's delegation, the person's own Decide creates the skill as
        them, the door carries its row back, and the mind lists it."""
        from database.stores import AuditStore, ChatStore
        from database.stores.data.skills import SkillStore

        skill = {"title": "Jira bulk resolve",
                 "summary": "When many Jira tickets are to be resolved at once.",
                 "body": "Run in the board's page:\n\n```javascript\n"
                         "await fetch('/rest/api/2/issue/X-1/transitions', "
                         "{method: 'POST'});\n```"}
        chat_id = app_call(admin, "AI:Chat:Open", {"title": "Skill"}).json()["data"]["chat"]["chat_id"]
        assert app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id, "config": {"llm": {
                "provider": "fake", "responses": [
                    action(action="save_skill", **skill),
                    action(action="say", text="Saved it.", final=True),
                ]}}}).status_code == 200

        # The decision is made while the socket is open, as a person
        # makes it: both against the served Data Core (ServedSocket).
        with ServedSocket(full_stack.data_core_url, admin,
                          f"/chats/{chat_id}") as frontend:
            assert receive(frontend)["event"] == "hello"
            frontend.send_json({"endpoint": "AI:Chat:Input", "data": {
                "text": "save that script as a skill", "parts": []}})
            frame = receive(frontend)
            while frame.get("event") != "question_asked":
                frame = receive(frontend)
            assert frame["expects"] == "skill" and frame["skill"] == skill
            assert SkillStore().col.count_documents({"type": "skill"}) == 0
            decided = frontend.call("AI:Approval:Decide", {
                "approval_id": frame["approval_id"], "answer": "save"})
            assert decided.status_code == 200, decided.text
            assert decided.json()["data"]["delivered"] is True
            seen = until_ai_message(frontend)
            assert seen[-1]["message"]["parts"][0]["content"] == "Saved it."

        ref = decided.json()["data"]["skill"]["ref"]
        stored = SkillStore().col.find_one({"_id": ref})
        assert stored["owner"]["users"] == [stored["created_by"]]
        [line] = list(AuditStore().col.find({"event_type": "skill.created"}))
        assert line["chat_id"] == chat_id and line["details"]["source"] == "chat"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = ChatStore().by_chat_id(chat_id).get("state") or {}
            framed = (state.get("messages") or [{}])[0].get("content", "")
            if f"{ref}: Jira bulk resolve" in framed:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("the saved frame never listed the skill")


class TestThePull:
    def test_a_chat_pulls_the_code_its_organization_approved(
        self, admin, full_stack, control, manifest_doc
    ):
        """Nothing is pushed to the runtime at install. The chat's
        contract names the approval; the runtime pulls the exact bytes
        through the Data Core's door, verifies them, and runs the agent
        under the ref — so the record lands in the collection the
        install created (docs/system/agent-code.md)."""
        from ai_runtime.tests.fixture_agents import AGENTS_DIR

        notebook = AGENTS_DIR / "notebook"
        control["manifest"] = manifest_doc
        control["files"] = {
            path.relative_to(notebook).as_posix(): path.read_text(encoding="utf-8")
            for path in notebook.rglob("*.py")
        }
        installed = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"})
        assert installed.status_code == 200, installed.text
        agent = installed.json()["data"]["agent"]
        ref, digest = agent["agent_id"], agent["package_digest"]
        library = full_stack.runtime_app.state.library
        assert not library.has(digest)          # nothing was pushed

        opened = app_call(admin, "AI:Chat:Open", {"title": "Pull"})
        chat_id = opened.json()["data"]["chat"]["chat_id"]
        configured = app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id, "config": {"llm": {
                "provider": "fake", "responses": [
                    action(action="open_agent", agent=ref),
                    action(action="invoke", function=f"{ref}.note.save",
                           inputs={"notebook": "work", "title": "Ship it"}),
                    action(action="say", text="Saved."),
                    action(action="finish"),
                ]}}})
        assert configured.status_code == 200, configured.text

        with ServedSocket(full_stack.data_core_url, admin,
                          f"/chats/{chat_id}") as frontend:
            # A first open narrates the pull before it greets
            # (agent_status: pulling, installing, ready — then hello).
            # Installing builds the agent's environment and downloads its
            # packages, which takes as long as the network does.
            heard = []
            while True:
                frame = receive(frontend, timeout=120.0)
                heard.append((frame["event"], frame.get("phase")))
                if frame["event"] == "hello":
                    break
            assert ("agent_status", "installing") in heard
            assert ("agent_status", "ready") in heard
            # Materialized at build, before the first word.
            assert library.has(digest)
            assert library.agent(digest).local_agent_id == "notebook"

            frontend.send_json({"endpoint": "AI:Chat:Input",
                                "data": {"text": "save a note", "parts": []}})
            seen = until_ai_message(frontend)
            parts = seen[-1]["message"]["parts"]
            reply = parts[0]["content"]
            assert reply.startswith("Saved."), reply
            # The write is marked under the words, from the call's own
            # stored result — not in a sentence the model could write.
            assert any(p.get("type") == "success"
                       and p.get("text", "").startswith("Verified:")
                       for p in parts), parts

        listed = app_call(admin, "Data:Record:List",
                          {"resource_id": f"{ref}__note"})
        assert listed.status_code == 200, listed.text
        titles = [r["keys"]["title"] for r in listed.json()["resources"]]
        assert titles == ["Ship it"]


def open_scripted(admin, title, *responses):
    """A chat thinking with a scripted model, opened as the person."""
    chat_id = app_call(admin, "AI:Chat:Open", {"title": title}).json()["data"]["chat"]["chat_id"]
    assert app_call(admin, "AI:Chat:Update", {
        "chat_id": chat_id, "config": {"llm": {
            "provider": "fake", "responses": list(responses)}}}).status_code == 200
    return chat_id


def until_hello(frontend, timeout=30.0):
    seen = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = receive(frontend, deadline - time.monotonic())
        seen.append(frame.get("event"))
        if frame.get("event") == "hello":
            return seen
    raise AssertionError(f"no hello; saw {seen}")


class TestTheNodeLink:
    """Execution nodes connect to the Data Core; chats ride their links
    (docs/system/execution-nodes.md#the-node-link)."""

    def test_the_data_core_holds_no_address_of_any_node(self, full_stack):
        from server.setup.app_state import get_node_links, get_settings

        assert not hasattr(get_settings(), "ai_runtime_url")
        [link] = get_node_links().connected()
        assert link.key.endswith("/stack")

    def test_a_node_restart_loses_no_chat(self, admin, full_stack):
        # One line said each turn: a session rebuilt on the node that
        # came back thinks with a scripted model started afresh, so the
        # script must not depend on which turn it is.
        chat_id = open_scripted(
            admin, "Restart",
            action(action="say", text="Here."), action(action="finish"),
            action(action="say", text="Here."), action(action="finish"))
        with ServedSocket(full_stack.data_core_url, admin,
                          f"/chats/{chat_id}") as frontend:
            until_hello(frontend)
            frontend.send_json({"endpoint": "AI:Chat:Input",
                                "data": {"text": "one", "parts": []}})
            until_ai_message(frontend)

            # The node goes, as a process that ends, and comes back.
            full_stack.node.stop()
            seen = []
            deadline = time.monotonic() + 15
            while "runtime_disconnected" not in seen:
                assert time.monotonic() < deadline, seen
                seen.append(receive(frontend)["event"])
            full_stack.node.start()
            # The chat the person has open is attached to it again, and
            # the mind is the one the record holds.
            until_hello(frontend)
            frontend.send_json({"endpoint": "AI:Chat:Input",
                                "data": {"text": "two", "parts": []}})
            reply = until_ai_message(frontend)[-1]
        assert reply["message"]["parts"][0]["content"] == "Here."
        listed = app_call(admin, "AI:Message:List", {"chat_id": chat_id})
        assert [m["parts"][0]["content"]
                for m in listed.json()["data"]["messages"]] == [
            "one", "Here.", "two", "Here."]
        # The mind that answered the second time was built from the
        # record: it holds the first turn, and absorbed both messages.
        from database.stores import ChatStore

        deadline = time.monotonic() + 15
        while True:
            state = ChatStore().by_chat_id(chat_id).get("state") or {}
            if state.get("cursor") == 2:
                break
            assert time.monotonic() < deadline, state.get("cursor")
            time.sleep(0.2)
        assert any("one" in str(m.get("content")) for m in state["messages"])

    def test_a_node_that_starts_before_the_data_core_answers_waits_for_it(
            self, admin, full_stack, tmp_path):
        """The node dials and dials again: the order the two start in
        does not matter."""
        from server.setup.app_state import get_node_links

        full_stack.node.stop()
        early = full_stack.add_node("early", start=False)
        # Pointed at nothing yet: a port nobody answers on.
        early.link.url = "ws://127.0.0.1:1/nodes/link"
        early.start(wait=False)
        time.sleep(0.5)
        assert not early.link.connected.is_set()
        # The Data Core "appears" where the node dials.
        early.link.url = full_stack.data_core_url.replace(
            "http://", "ws://") + "/nodes/link"
        early.wait_linked(timeout=20)
        assert any(link.key.endswith("/early")
                   for link in get_node_links().connected())

    def test_a_delegation_is_accepted_only_from_the_node_it_was_handed_to(
            self, admin, full_stack):
        import httpx

        from conftest import runtime_service_token
        from contracts.node import HEADER_NODE
        from server.setup.app_state import get_runtime_clients

        chat_id = open_scripted(admin, "Bound", action(action="finish"))
        with ServedSocket(full_stack.data_core_url, admin,
                          f"/chats/{chat_id}") as frontend:
            until_hello(frontend)
        # The key the node was handed, presented by another node the
        # same authority admits: refused.
        from database.stores import RuntimeSessionStore
        rows = list(RuntimeSessionStore().col.find({"chat_id": chat_id}))
        assert rows and rows[0]["node_id"].endswith("/stack")
        binding = get_runtime_clients().chats.get(chat_id)
        assert binding is None or binding.link is None or \
            binding.link.key.endswith("/stack")

        from api.services.chat_session.identity import Delegation
        from database.stores import UserStore

        person = UserStore().get_by_email("admin@test.org")
        stolen = Delegation().for_chat(
            {"user_id": person["_id"], "org_id": person["org_id"],
             "email": person["email"], "session_id": ""},
            chat_id, node=rows[0]["node_id"])
        other = runtime_service_token(full_stack.authority_private, "thief")
        refused = httpx.post(f"{full_stack.data_core_url}/app", json={
            "endpoint": "AI:State:Get", "data": {"chat_id": chat_id}},
            headers={"Authorization": f"Bearer {stolen}", HEADER_NODE: other})
        assert refused.status_code == 401
        bare = httpx.post(f"{full_stack.data_core_url}/app", json={
            "endpoint": "AI:State:Get", "data": {"chat_id": chat_id}},
            headers={"Authorization": f"Bearer {stolen}"})
        assert bare.status_code == 401


class TestLeases:
    """Chats are placed on nodes under numbered leases
    (docs/system/execution-nodes.md#leases)."""

    def test_with_two_nodes_killing_one_moves_its_chats_to_the_other(
            self, admin, full_stack, monkeypatch):
        from database.stores import LeaseStore
        from server.setup.app_state import get_runtime_clients

        monkeypatch.setattr(LeaseStore, "SECONDS", 3)
        second = full_stack.add_node("second")
        chat_id = open_scripted(
            admin, "Moving",
            action(action="say", text="On the first."), action(action="finish"),
            action(action="say", text="On the second."), action(action="finish"))
        with ServedSocket(full_stack.data_core_url, admin,
                          f"/chats/{chat_id}") as frontend:
            until_hello(frontend)
            first_holder = LeaseStore().current(chat_id)
            frontend.send_json({"endpoint": "AI:Chat:Input",
                                "data": {"text": "one", "parts": []}})
            until_ai_message(frontend)
            dying = (full_stack.node if first_holder["node_id"].endswith("/stack")
                     else second)
            surviving = second if dying is full_stack.node else full_stack.node
            # The holder dies: no word, no release — its lease lapses.
            dying.stop()
            deadline = time.monotonic() + 20
            moved = 0
            while not moved:
                assert time.monotonic() < deadline, "the chat never moved"
                time.sleep(0.5)
                moved = full_stack.on_data_core(get_runtime_clients().fail_over)
            until_hello(frontend)
            now_held = LeaseStore().current(chat_id)
            assert now_held["node_id"].endswith("/" + surviving.name)
            assert now_held["number"] == first_holder["number"] + 1
            frontend.send_json({"endpoint": "AI:Chat:Input",
                                "data": {"text": "two", "parts": []}})
            reply = until_ai_message(frontend)[-1]
        assert reply["message"]["parts"][0]["content"] == "On the second."


def ai_messages(admin, chat_id):
    listed = app_call(admin, "AI:Message:List", {"chat_id": chat_id})
    return [m["parts"][0]["content"] for m in listed.json()["data"]["messages"]
            if m["actor"] == "ai"]


def until_said(admin, chat_id, count=1, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        said = ai_messages(admin, chat_id)
        if len(said) >= count:
            return said
        time.sleep(0.2)
    raise AssertionError(f"the chat said {ai_messages(admin, chat_id)}")


def schedule(admin, chat_id, **when):
    made = app_call(admin, "AI:Schedule:Create", {
        "chat_id": chat_id, "note": "standup", **when})
    assert made.status_code == 200, made.text
    return made.json()["data"]["schedule"]


class TestTheClock:
    """The Data Core decides when a schedule is due and hands the fire
    to the node holding its chat (docs/system/execution-nodes.md#the-clock)."""

    def tick(self, full_stack):
        from server.setup.app_state import get_runtime_clients

        return full_stack.on_data_core(get_runtime_clients().clock.tick)

    def test_a_schedule_fires_through_a_node_and_the_assistant_answers(
            self, admin, full_stack):
        chat_id = open_scripted(
            admin, "Clock",
            action(action="say", text="Time for the standup."),
            action(action="finish"))
        row = schedule(admin, chat_id, delay_seconds=1)
        time.sleep(1.2)
        # Nobody has the chat open, and no node holds it: it is placed
        # as the row comes due, then fired there.
        assert self.tick(full_stack) == 1
        assert until_said(admin, chat_id) == ["Time for the standup."]
        # Fired once: the next look finds nothing due.
        assert self.tick(full_stack) == 0
        from database.stores import ScheduleStore

        deadline = time.monotonic() + 15
        while True:
            kept = next(r for r in ScheduleStore().rows(chat_id)
                        if r["schedule_id"] == row["schedule_id"])
            if kept.get("runs"):
                break
            assert time.monotonic() < deadline, kept
            time.sleep(0.2)
        assert kept["runs"][-1]["status"] == "woke"
        assert kept["enabled"] is False          # a one-off, and done

    def test_with_two_nodes_each_schedule_fires_once(self, admin, full_stack):
        full_stack.add_node("second")
        chats = []
        for title in ("One", "Two", "Three"):
            chat_id = open_scripted(
                admin, title, action(action="say", text=f"{title} woke."),
                action(action="finish"))
            schedule(admin, chat_id, delay_seconds=1)
            chats.append(chat_id)
        time.sleep(1.2)
        fired = self.tick(full_stack) + self.tick(full_stack)
        assert fired == 3
        for chat_id, title in zip(chats, ("One", "Two", "Three")):
            assert until_said(admin, chat_id) == [f"{title} woke."]
        time.sleep(1.0)
        for chat_id in chats:
            assert len(ai_messages(admin, chat_id)) == 1


class TestOrganizationsOwningNodes:
    """An organization that keeps to its own execution nodes
    (docs/system/execution-nodes.md#organizations-owning-nodes)."""

    def test_own_only_a_chat_waits_for_its_own_node_and_never_reaches_the_shared_one(
            self, admin, seed, full_stack):
        from conftest import register_authority
        from database.stores import LeaseStore

        assert app_call(admin, "Settings:Nodes:Update",
                        {"placement": "own_only"}).status_code == 200
        chat_id = open_scripted(
            admin, "Ours", action(action="say", text="On our own node."),
            action(action="finish"))
        shared = full_stack.node
        with ServedSocket(full_stack.data_core_url, admin,
                          f"/chats/{chat_id}") as frontend:
            waiting = receive(frontend)
            assert waiting["event"] == "runtime_unavailable"
            assert "own execution nodes only" in waiting["detail"]
            assert LeaseStore().current(chat_id) is None

            # The organization's own node connects: the chat the person
            # is waiting on is attached to it, by itself.
            private_pem, public_pem = service_keys()
            register_authority(seed.org["_id"], public_pem, name="Our rack")
            ours = full_stack.add_node("ours", authority=private_pem)
            until_hello(frontend)
            frontend.send_json({"endpoint": "AI:Chat:Input",
                                "data": {"text": "hello", "parts": []}})
            reply = until_ai_message(frontend)[-1]
        assert reply["message"]["parts"][0]["content"] == "On our own node."
        assert LeaseStore().current(chat_id)["node_id"].endswith("/ours")
        # The shared node never held it, and was never handed its key.
        assert chat_id not in shared.app.state.host.sessions
        assert chat_id not in shared.app.state.host.services.credentials
        assert ours.app.state.host.services.credentials.get(chat_id)
