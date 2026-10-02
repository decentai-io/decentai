"""An install or update is prepared on the runtime at once (the
`agents_changed` frame), through the person's most recent chat, so
their next chat does not wait on a package pull and a pip build."""

import time

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import app_call
from test_ai_messages import make_chat


class FakeRuntimeClients:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, user, frame):
        self.sent.append((chat_id, frame))
        return True


class TestPreparedOnInstall:
    def test_an_install_tells_the_runtime_through_the_persons_latest_chat(
            self, admin, seed, control, manifest_doc, monkeypatch):
        import server.setup.app_state as app_state

        fake = FakeRuntimeClients()
        monkeypatch.setattr(app_state, "get_runtime_clients", lambda: fake)
        chat_id = make_chat(admin)
        control["manifest"] = manifest_doc

        installed = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"})
        assert installed.status_code == 200, installed.text
        agent = installed.json()["data"]["agent"]
        # The send is a task on the app's loop, behind the response.
        for _ in range(100):
            if fake.sent:
                break
            time.sleep(0.02)
        assert fake.sent, "the runtime was told"
        sent_chat, frame = fake.sent[-1]
        assert sent_chat == chat_id
        assert frame["event"] == "agents_changed"
        [entry] = frame["agents"]
        assert entry["agent_id"] == agent["agent_id"]
        assert entry["package_digest"] == agent["package_digest"]
        assert entry["package_digest"].startswith("sha256:")
        assert len(entry["manifest_hash"]) == 64

    def test_nobody_without_a_chat_is_told_about(
            self, admin, seed, control, manifest_doc, monkeypatch):
        import server.setup.app_state as app_state

        fake = FakeRuntimeClients()
        monkeypatch.setattr(app_state, "get_runtime_clients", lambda: fake)
        control["manifest"] = manifest_doc
        installed = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"})
        assert installed.status_code == 200, installed.text
        assert fake.sent == []
