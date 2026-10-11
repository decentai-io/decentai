"""An install marks the agent preparing and sends nothing anywhere: a
runtime stocks itself with every approved package on its own clock,
as itself (docs/system/agent-code.md, the package door)."""

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import app_call


class TestPreparingOnInstall:
    def test_an_install_is_preparing_from_the_start(
            self, admin, seed, control, manifest_doc):
        control["manifest"] = manifest_doc
        installed = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"})
        assert installed.status_code == 200, installed.text
        agent = installed.json()["data"]["agent"]
        assert agent["prepared"] == {"state": "preparing", "error": ""}
        assert agent["package_digest"].startswith("sha256:")

    def test_nobody_needs_a_chat_for_it(
            self, admin, seed, control, manifest_doc, monkeypatch):
        """The old way reached the runtime through the installer's most
        recent chat; a person with none was left to meet the build on
        first open. Nothing is dialed now, chat or no chat."""
        import server.setup.app_state as app_state

        def nobody_dials():
            raise AssertionError("the install dialed a runtime")

        monkeypatch.setattr(app_state, "get_runtime_clients", nobody_dials)
        control["manifest"] = manifest_doc
        installed = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"})
        assert installed.status_code == 200, installed.text
        assert installed.json()["data"]["agent"]["prepared"]["state"] == "preparing"
