"""Tests of install_agents: against a stand-in API server, what it saves, what it installs, and what it leaves.

    python -m pytest tools
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from install_agents import Api, install_all  # noqa: E402

KNOWN = "https://agents.test/catalog.json"
NEW = "https://more.test/catalog.json"


def offer(agent: str, status: str, errors: list[str] | None = None) -> dict:
    return {"source_id": "s1", "digest": "sha256:c", "status": status, "errors": errors or [],
            "manifest": {"agent": {"id": agent, "version": "1.0.0"}}}


class Stand:
    """An API server that answers as the real one does, keeps every call, refuses one install and is busy once."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.busy = True
        stand = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                call = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stand.calls.append((call["endpoint"], call["data"]))
                status, body = stand.answer(call["endpoint"], call["data"])
                self.send_response(status)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *_) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def answer(self, endpoint: str, data: dict) -> tuple[int, dict]:
        if endpoint == "Agents:Agent:sources":
            return 200, {"sources": [{"source_id": "s1", "url": KNOWN}]}
        if endpoint in ("Agents:Agent:sourcerefresh", "Agents:Agent:sourcecreate"):
            return 200, {"source": {"name": "Agents", "agents": 4}}
        if endpoint == "Agents:Agent:available":
            return 200, {"agents": [offer("notes", "not_installed"), offer("mail", "update_available"),
                                    offer("tasks", "installed"), offer("broken", "not_installed", ["no program"]),
                                    offer("refused", "not_installed")]}
        if endpoint == "Agents:Agent:install" and self.busy:
            self.busy = False
            return 429, {"error": {"code": "too_many", "message": "Too many calls.", "retry_after": 0}}
        if endpoint == "Agents:Agent:install" and data["agent_id"] == "refused":
            return 409, {"error": {"code": "conflict", "message": "The source changed since it was reviewed."}}
        return 200, {"agent": {"agent_id": data.get("agent_id"), "state": "checking"}}


@pytest.fixture
def stand():
    running = Stand()
    yield running
    running.server.shutdown()


def test_each_source_is_saved_or_read_again_and_every_new_agent_and_update_is_installed(stand):
    said: list[str] = []
    assert install_all(Api(stand.base, "key"), [KNOWN, NEW], said.append) is False
    endpoints = [endpoint for endpoint, _ in stand.calls]
    assert endpoints[:3] == ["Agents:Agent:sources", "Agents:Agent:sourcerefresh", "Agents:Agent:sourcecreate"]
    assert stand.calls[1][1] == {"source_id": "s1"} and stand.calls[2][1] == {"url": NEW}
    installed = [data["agent_id"] for endpoint, data in stand.calls if endpoint == "Agents:Agent:install"]
    # The first install met a busy server and was asked again; what is installed, or marked in error, is left.
    assert installed == ["notes", "notes", "mail", "refused"]
    assert "  not installed refused 1.0.0: Agents:Agent:install: The source changed since it was reviewed." in said
    assert "  left out broken: no program" in said
    assert said[-1] == "2 installed, 1 failed, 1 in error in their catalog."
