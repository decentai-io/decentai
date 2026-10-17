"""The two halves meet (docs/system/chat-session.md).

A person's socket into the backend; the backend's dial to a REAL
runtime process serving the same code main.py serves; the runtime's
services calls back into the backend over /app as the chat's
delegation; the answer relayed home. The live smoke test, automated —
nothing here is stubbed but the model.
"""

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
    """One uvicorn server in a thread, lifespan off: the backend's app
    state is the test session's, and must not be reset by a shutdown."""
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="critical",
        lifespan="off",
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture()
def full_stack(app, tmp_path, monkeypatch):
    """The backend served over HTTP, a real runtime served beside it and
    pointed at the backend, and the backend pointed at the runtime.

    The runtime starts EMPTY, with a real library in a fresh directory:
    the code it serves is whatever a chat's contract makes it pull
    (docs/system/agent-code.md). Its environments are the fixtures' shared
    venv — the fixture agents declare no dependencies."""
    from ai_runtime.agents.library import AgentLibrary
    from ai_runtime.door.app import create_app as create_runtime_app
    from ai_runtime.door.settings import RuntimeSettings
    from ai_runtime.door.platform import Backend, Gateway, RuntimeIdentity
    from ai_runtime.tests.fake_container import confinement, container
    from ai_runtime.tests.fixture_agents import worker_environment
    from server.setup.app_state import get_state

    private_pem, public_pem = service_keys()
    # And the runtime's own identity toward the backend, for the code it
    # pulls as itself (the package door).
    runtime_private, runtime_public = service_keys()

    backend_port = free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    backend_server, backend_thread = serve(app, backend_port)

    shared = worker_environment()
    monkeypatch.setattr(AgentLibrary, "environment",
                        lambda self, digest: shared)
    runtime_port = free_port()
    runtime_app = create_runtime_app(
        RuntimeSettings(backend_service_public_key=public_pem,
                        backend_url=backend_url),
        services=Backend(Gateway(backend_url), RuntimeIdentity(runtime_private)),
        library=AgentLibrary(tmp_path / "runtime", confinement()),
        container=container(), confinement=confinement(),
    )
    runtime_server, runtime_thread = serve(runtime_app, runtime_port)

    deadline = time.monotonic() + 10
    while not (backend_server.started and runtime_server.started):
        assert time.monotonic() < deadline, "servers never started"
        time.sleep(0.02)

    state = get_state()
    original = state.settings
    state.settings = dataclasses.replace(
        original,
        ai_runtime_url=f"http://127.0.0.1:{runtime_port}",
        backend_service_private_key=private_pem,
        ai_runtime_service_public_key=runtime_public,
    )
    try:
        yield SimpleNamespace(backend_url=backend_url, runtime_app=runtime_app)
    finally:
        state.settings = original
        runtime_app.state.host.shutdown()
        for server, thread in ((runtime_server, runtime_thread),
                               (backend_server, backend_thread)):
            server.should_exit = True
            thread.join(timeout=5)


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

        with admin.websocket_connect(
            f"/chats/{chat_id}", headers=cookie_header(admin)
        ) as frontend:
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

        # A new audience: the same mind, hydrated from the backend. The
        # first turn's tail (it goes idle after answering) may still be
        # on its way when the new socket opens; nothing else may come
        # before the greeting.
        with admin.websocket_connect(
            f"/chats/{chat_id}", headers=cookie_header(admin)
        ) as frontend:
            frame = receive(frontend)
            while frame["event"] != "hello":
                assert frame["event"] in ("idle", "working"), frame
                frame = receive(frontend)
            frontend.send_json({"endpoint": "AI:Chat:Input",
                                "data": {"text": "again?", "parts": []}})
            seen = until_ai_message(frontend)
            assert seen[-1]["message"]["parts"][0]["content"] == "Still here."

        # The records are the backend's, and whole.
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

        with admin.websocket_connect(
            f"/chats/{chat_id}", headers=cookie_header(admin)
        ) as frontend:
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


class TestThePull:
    def test_a_chat_pulls_the_code_its_organization_approved(
        self, admin, full_stack, control, manifest_doc
    ):
        """Nothing is pushed to the runtime at install. The chat's
        contract names the approval; the runtime pulls the exact bytes
        through the backend's door, verifies them, and runs the agent
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

        with admin.websocket_connect(
            f"/chats/{chat_id}", headers=cookie_header(admin)
        ) as frontend:
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
