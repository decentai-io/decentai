"""Agents:Monitor — what agents use and did, read from the runtime and
shown to an organization of its own agents only
(docs/system/monitoring.md).

The runtime is stood in for at the wire: what it would answer is what a
test says, and what it was asked is kept.
"""

import httpx
import pytest

from conftest import app_call
from test_data_layer import _user
from test_multi_tenancy import _two
from test_runtime_secret_use import signing_key  # noqa: F401

from api.services.agents.monitoring import RuntimeMonitor


class Runtime:
    """The runtime's three doors, answering what a test put there."""

    def __init__(self):
        self.asked = []
        self.events = []
        self.usage = None
        self.files = {"files": [], "count": 0, "bytes": 0}
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("nobody is listening")
        self.asked.append((request.url.path, dict(request.url.params),
                           request.headers.get("Authorization", "")))
        if request.url.path == "/internal/monitor/events":
            asked = request.url.params
            found = sorted(self.events, key=lambda e: e["at"], reverse=True)
            if asked.get("agent"):
                found = [e for e in found if e.get("agent") == asked["agent"]]
            if asked.get("kinds"):
                found = [e for e in found
                         if e["kind"] in asked["kinds"].split(",")]
            if asked.get("before"):
                found = [e for e in found if e["at"] < float(asked["before"])]
            return httpx.Response(200, json={
                "events": found[: int(asked.get("limit") or 200)]})
        if request.url.path == "/internal/monitor/usage":
            return httpx.Response(200, json={
                "usage": self.usage,
                "runtime": {"memory": 300, "memory_limit": None,
                            "cpu": 0.1, "cpus": None},
                "confined": {"user": True, "files": True, "network": True}})
        if request.url.path == "/internal/monitor/files":
            return httpx.Response(200, json=self.files)
        return httpx.Response(404)


@pytest.fixture()
def runtime(signing_key):
    stand_in = Runtime()
    RuntimeMonitor.transport = httpx.MockTransport(stand_in)
    yield stand_in
    RuntimeMonitor.transport = None


def install(org_id, agent_ref, name):
    from database.stores import AgentManifestStore

    return AgentManifestStore().upsert(
        org_id, agent_ref, "1.0.0", {"agent": {"id": "x", "name": name}},
        {}, "admin@test.org", qualified_id=agent_ref)


def org_of(client):
    from database.stores import UserStore

    email = client.get("/auth/me").json().get("email")
    return UserStore().get_by_email(email)["org_id"] if email else None


def seeded_org():
    from conftest import ADMIN_EMAIL
    from database.stores import UserStore

    return UserStore().get_by_email(ADMIN_EMAIL)["org_id"]


class TestWhoMayLook:
    def test_it_is_a_grant_of_its_own_and_a_member_does_not_hold_it(
            self, app, seed, admin, runtime):
        member, _ = _user(app, seed, "member@test.org")
        for action in ("Agents:Monitor:Usage", "Agents:Monitor:Events",
                       "Agents:Monitor:Files"):
            assert app_call(member, action, {"agent": "agt_a"}).status_code == 403
        assert runtime.asked == []
        assert app_call(admin, "Agents:Monitor:Usage").status_code == 200

    def test_the_runtime_is_asked_as_the_backend(self, admin, runtime):
        app_call(admin, "Agents:Monitor:Usage")
        path, _, authorization = runtime.asked[0]
        assert path == "/internal/monitor/usage"
        assert authorization.startswith("Bearer ")

    def test_a_runtime_that_is_down_is_said_so(self, admin, runtime):
        runtime.down = True
        for action in ("Agents:Monitor:Usage", "Agents:Monitor:Events"):
            refused = app_call(admin, action)
            assert refused.status_code == 503
            assert "did not answer" in refused.text


class TestWhatTheyUse:
    def test_an_organization_is_shown_its_own_agents_and_how_full_the_container_is(
            self, app, seed, runtime):
        left, right = _two(app)
        install(left.org_id, "agt_left", "Notes")
        install(right.org_id, "agt_right", "Browser")
        runtime.usage = {
            "at": 10.0, "memory": 900,
            "limits": {"memory": 2000, "cpus": 1.5},
            "agents": [
                {"agent": "agt_left", "name": "as the manifest said", "user": 20001,
                 "memory": 100, "cpu": 0.2, "processes": 2},
                {"agent": "agt_right", "name": "Browser", "user": 20002,
                 "memory": 700, "cpu": 0.9, "processes": 40},
                {"agent": "", "name": "", "user": 20003, "memory": 5,
                 "cpu": 0.0, "processes": 1},
            ],
        }
        said = app_call(left.client, "Agents:Monitor:Usage").json()["data"]
        assert said["counted"] is True
        assert [agent["agent"] for agent in said["agents"]] == ["agt_left"]
        # Under the name this organization's approval has.
        assert said["agents"][0]["name"] == "Notes"
        assert said["agents"][0]["memory"] == 100
        assert said["limits"] == {"memory": 2000, "cpus": 1.5}
        assert said["memory"] == 900
        assert said["confined"]["network"] is True
        theirs = app_call(right.client, "Agents:Monitor:Usage").json()["data"]
        assert [agent["agent"] for agent in theirs["agents"]] == ["agt_right"]

    def test_where_nobody_adds_it_up_it_says_so(self, admin, runtime):
        runtime.usage = None
        said = app_call(admin, "Agents:Monitor:Usage").json()["data"]
        assert said["counted"] is False and said["agents"] == []
        # The platform's own parts are still said: each read its own.
        assert [part["id"] for part in said["services"]] == [
            "backend", "runtime", "database"]

    def test_one_organizations_deployment_is_shown_what_each_part_holds(
            self, admin, runtime):
        runtime.usage = {"at": 1.0, "memory": 900, "cpu": 0.7,
                         "limits": {"memory": 2000, "cpus": 1.5}, "agents": []}
        said = app_call(admin, "Agents:Monitor:Usage").json()["data"]
        parts = {part["id"]: part for part in said["services"]}
        assert list(parts) == ["backend", "runtime", "agents", "database"]
        assert parts["runtime"] == {
            "id": "runtime", "name": "Runtime", "memory": 300,
            "memory_limit": None, "cpu": 0.1, "cpus": None}
        assert parts["agents"] == {
            "id": "agents", "name": "Agents", "memory": 900,
            "memory_limit": 2000, "cpu": 0.7, "cpus": 1.5}
        assert set(parts["backend"]) == {
            "id", "name", "memory", "memory_limit", "cpu", "cpus"}
        # The database says it of itself: what it keeps for this
        # platform, and what its server holds.
        assert parts["database"]["disk"] > 0
        assert parts["database"]["name"] == "Database"

    def test_where_there_are_several_organizations_it_is_not(
            self, app, seed, runtime):
        left, _ = _two(app)
        runtime.usage = {"at": 1.0, "memory": 900, "limits": {}, "agents": []}
        said = app_call(left.client, "Agents:Monitor:Usage").json()["data"]
        assert said["services"] == []


class TestWhatHappened:
    def written(self):
        return [
            {"at": 1.0, "kind": "helper", "job": "check", "code": 0},
            {"at": 2.0, "kind": "program", "agent": "builder",
             "name": "the builder", "program": "pip wheel pandas", "code": 0},
            {"at": 3.0, "kind": "worker.started", "agent": "agt_left",
             "name": "old name", "user": 20001},
            {"at": 4.0, "kind": "connection", "agent": "agt_left",
             "host": "api.example.com", "allowed": True},
            {"at": 5.0, "kind": "connection", "agent": "agt_right",
             "host": "mail.example.com", "allowed": False, "why": "refused"},
            {"at": 6.0, "kind": "log", "agent": "agt_right", "line": "theirs"},
            {"at": 7.0, "kind": "connection", "agent": "agt_gone",
             "host": "x.example", "allowed": True},
        ]

    def test_an_organization_reads_its_own_agents_and_no_others(
            self, app, seed, runtime):
        left, right = _two(app)
        install(left.org_id, "agt_left", "Notes")
        install(right.org_id, "agt_right", "Browser")
        runtime.events = self.written()
        mine = app_call(left.client, "Agents:Monitor:Events").json()["data"]
        assert [event["at"] for event in mine["events"]] == [4.0, 3.0]
        assert all(event["agent"] == "agt_left" for event in mine["events"])
        assert mine["events"][1]["name"] == "Notes"
        assert mine["next_before"] is None
        theirs = app_call(right.client, "Agents:Monitor:Events").json()["data"]
        assert [event["at"] for event in theirs["events"]] == [6.0, 5.0]
        assert "mail.example.com" not in str(mine)

    def test_what_the_platform_did_for_no_agent_is_shown_where_there_is_one_organization(
            self, admin, seed, runtime):
        install(seeded_org(), "agt_left", "Notes")
        runtime.events = self.written()
        said = app_call(admin, "Agents:Monitor:Events").json()["data"]
        # Its own agent's, the builder's and the helper's check — never
        # an agent's that is not installed here.
        assert [event["at"] for event in said["events"]] == [4.0, 3.0, 2.0, 1.0]

    def test_where_there_are_several_it_is_not(self, app, seed, runtime):
        left, _ = _two(app)
        install(left.org_id, "agt_left", "Notes")
        runtime.events = self.written()
        said = app_call(left.client, "Agents:Monitor:Events").json()["data"]
        assert {event["kind"] for event in said["events"]} == {
            "worker.started", "connection"}

    def test_only_the_kinds_and_the_agent_asked_for(self, admin, seed, runtime):
        install(seeded_org(), "agt_left", "Notes")
        runtime.events = self.written()
        said = app_call(admin, "Agents:Monitor:Events", {
            "kinds": ["connection", "no-such-kind"], "agent": "agt_left",
        }).json()["data"]
        assert [event["at"] for event in said["events"]] == [4.0]
        _, asked, _ = runtime.asked[-1]
        assert asked["kinds"] == "connection" and asked["agent"] == "agt_left"

    def test_another_organizations_agent_cannot_be_asked_about(
            self, app, seed, runtime):
        left, right = _two(app)
        install(right.org_id, "agt_right", "Browser")
        refused = app_call(left.client, "Agents:Monitor:Events",
                           {"agent": "agt_right"})
        assert refused.status_code == 404
        assert runtime.asked == []

    def test_a_page_ends_where_the_next_begins(self, admin, seed, runtime):
        install(seeded_org(), "agt_left", "Notes")
        runtime.events = [
            {"at": float(number), "kind": "log", "agent": "agt_left",
             "line": str(number)} for number in range(1, 8)]
        first = app_call(admin, "Agents:Monitor:Events", {"limit": 3}).json()["data"]
        assert [event["line"] for event in first["events"]] == ["7", "6", "5"]
        assert first["next_before"] == 5.0
        second = app_call(admin, "Agents:Monitor:Events", {
            "limit": 3, "before": first["next_before"]}).json()["data"]
        assert [event["line"] for event in second["events"]] == ["4", "3", "2"]
        last = app_call(admin, "Agents:Monitor:Events", {
            "limit": 3, "before": second["next_before"]}).json()["data"]
        assert [event["line"] for event in last["events"]] == ["1"]
        assert last["next_before"] is None

    def test_a_page_is_filled_past_other_organizations_events(
            self, app, seed, runtime, monkeypatch):
        """The log is every organization's: a page of this one's is
        read from further back than one answer of the runtime."""
        from api.endpoints.app.agents.monitor_controller import MonitorController

        monkeypatch.setattr(MonitorController, "ASKED", 4)
        left, right = _two(app)
        install(left.org_id, "agt_left", "Notes")
        install(right.org_id, "agt_right", "Browser")
        runtime.events = (
            [{"at": 100.0 + n, "kind": "log", "agent": "agt_right", "line": "t"}
             for n in range(6)]
            + [{"at": 10.0 + n, "kind": "log", "agent": "agt_left", "line": str(n)}
               for n in range(3)])
        said = app_call(left.client, "Agents:Monitor:Events",
                        {"limit": 2}).json()["data"]
        assert [event["line"] for event in said["events"]] == ["2", "1"]
        assert said["next_before"] == 11.0

    def test_only_what_happened_in_the_days_asked_for(self, admin, seed, runtime):
        install(seeded_org(), "agt_left", "Notes")
        runtime.events = [
            {"at": float(number), "kind": "log", "agent": "agt_left",
             "line": str(number)} for number in range(1, 8)]
        said = app_call(admin, "Agents:Monitor:Events", {
            "since": 3.0, "until": 6.0}).json()["data"]
        assert [event["line"] for event in said["events"]] == ["5", "4", "3"]
        assert said["next_before"] is None

    def test_the_days_are_read_a_page_at_a_time(
            self, admin, seed, runtime, monkeypatch):
        from api.endpoints.app.agents.monitor_controller import MonitorController

        monkeypatch.setattr(MonitorController, "ASKED", 2)
        install(seeded_org(), "agt_left", "Notes")
        runtime.events = [
            {"at": float(number), "kind": "log", "agent": "agt_left",
             "line": str(number)} for number in range(1, 8)]
        first = app_call(admin, "Agents:Monitor:Events", {
            "limit": 2, "since": 3.0, "until": 7.0}).json()["data"]
        assert [event["line"] for event in first["events"]] == ["6", "5"]
        second = app_call(admin, "Agents:Monitor:Events", {
            "limit": 2, "since": 3.0, "until": 7.0,
            "before": first["next_before"]}).json()["data"]
        assert [event["line"] for event in second["events"]] == ["4", "3"]
        last = app_call(admin, "Agents:Monitor:Events", {
            "limit": 2, "since": 3.0, "until": 7.0,
            "before": second["next_before"]}).json()["data"]
        assert last["events"] == [] and last["next_before"] is None

    def test_what_is_not_a_list_or_a_time_is_refused(self, admin, runtime):
        assert app_call(admin, "Agents:Monitor:Events",
                        {"kinds": "connection"}).status_code == 400
        assert app_call(admin, "Agents:Monitor:Events",
                        {"before": "yesterday"}).status_code == 400
        assert app_call(admin, "Agents:Monitor:Events",
                        {"since": "2026-10-01"}).status_code == 400
        assert app_call(admin, "Agents:Monitor:Events",
                        {"kind": "no-such-kind"}).status_code == 400
        assert app_call(admin, "Agents:Monitor:Events",
                        {"outcome": "meh"}).status_code == 400
        assert app_call(admin, "Agents:Monitor:Events",
                        {"text": ["a"]}).status_code == 400


class TestWhatHappenedNarrowed:
    def written(self):
        return [
            {"at": 1.0, "kind": "helper", "job": "check", "code": 0},
            {"at": 2.0, "kind": "worker.started", "agent": "agt_left"},
            {"at": 3.0, "kind": "connection", "agent": "agt_left",
             "host": "API.example.com", "allowed": True, "reached": True,
             "sent": 100, "received": 1000},
            {"at": 4.0, "kind": "connection", "agent": "agt_left",
             "host": "evil.example", "allowed": False, "why": "not declared"},
            {"at": 5.0, "kind": "connection", "agent": "agt_mail",
             "host": "api.example.com", "allowed": True, "reached": False,
             "why": "timed out"},
            {"at": 6.0, "kind": "log", "agent": "agt_mail",
             "line": "Fetched 3 messages from the INBOX"},
            {"at": 7.0, "kind": "program", "agent": "agt_left",
             "program": "python run.py", "code": 2},
            {"at": 8.0, "kind": "connection", "agent": "agt_other",
             "host": "api.example.com", "allowed": True},
        ]

    @pytest.fixture()
    def installed(self, seed, runtime):
        install(seeded_org(), "agt_left", "Notes")
        install(seeded_org(), "agt_mail", "Mail")
        runtime.events = self.written()

    def shown(self, admin, **asked):
        said = app_call(admin, "Agents:Monitor:Events", asked).json()["data"]
        return [event["at"] for event in said["events"]]

    def test_only_what_went_as_asked(self, admin, installed):
        assert self.shown(admin, outcome="trouble") == [7.0, 5.0, 4.0]
        assert self.shown(admin, outcome="refused") == [4.0]
        assert self.shown(admin, outcome="unreached") == [5.0]
        assert self.shown(admin, outcome="failed") == [7.0]
        assert self.shown(admin, outcome="ok") == [6.0, 3.0, 2.0, 1.0]

    def test_one_host_whatever_its_case(self, admin, installed):
        assert self.shown(admin, host="api.example.com") == [5.0, 3.0]

    def test_a_search_reads_the_words_whatever_their_case(self, admin, installed):
        assert self.shown(admin, text="inbox") == [6.0]
        assert self.shown(admin, text="NOT DECLARED") == [4.0]
        assert self.shown(admin, text="mail") == [6.0, 5.0]
        # A search is words, never a pattern.
        assert self.shown(admin, text=".*") == []

    def test_one_kind_is_read_alone_from_the_runtime(self, admin, installed, runtime):
        assert self.shown(admin, kind="connection", agent="agt_left") == [4.0, 3.0]
        _, asked, _ = runtime.asked[-1]
        assert asked["kinds"] == "connection" and asked["agent"] == "agt_left"

    def test_the_first_page_comes_added_up(self, admin, installed, runtime):
        said = app_call(admin, "Agents:Monitor:Events", {
            "kinds": ["connection"], "tally": True,
        }).json()["data"]
        tally = said["tally"]
        assert [event["at"] for event in said["events"]] == [5.0, 4.0, 3.0]
        assert tally["matching"] == 3 and tally["complete"] is True
        assert tally["outcomes"] == {"ok": 1, "refused": 1, "unreached": 1}
        assert tally["hosts"][0] == {"host": "api.example.com", "count": 2}
        assert {a["name"]: a["count"] for a in tally["agents"]} == {"Notes": 2, "Mail": 1}
        assert tally["sent"] == 100 and tally["received"] == 1000
        # Another organization's agent is neither shown nor counted.
        assert "agt_other" not in str(said)

    def test_each_count_is_of_what_choosing_it_would_show(self, admin, installed):
        tally = app_call(admin, "Agents:Monitor:Events", {
            "kind": "connection", "agent": "agt_left", "tally": True,
        }).json()["data"]["tally"]
        assert tally["matching"] == 2
        # Every kind of this agent's, every agent's connections.
        assert tally["kinds"] == {"worker.started": 1, "connection": 2, "program": 1}
        assert {a["name"]: a["count"] for a in tally["agents"]} == {"Notes": 2, "Mail": 1}
        assert tally["outcomes"] == {"ok": 1, "refused": 1}

    def test_a_count_says_how_far_back_it_read(
            self, admin, installed, monkeypatch):
        from api.endpoints.app.agents.monitor_controller import MonitorController

        monkeypatch.setattr(MonitorController, "ASKED", 2)
        monkeypatch.setattr(MonitorController, "TALLY_ROUNDS", 2)
        said = app_call(admin, "Agents:Monitor:Events", {
            "limit": 1, "tally": True}).json()["data"]
        assert [event["at"] for event in said["events"]] == [7.0]
        assert said["next_before"] == 7.0
        assert said["tally"]["complete"] is False
        assert said["tally"]["reached"] == 5.0
        # 8, 7, 6 and 5 were read; 8 is another organization's.
        assert said["tally"]["matching"] == 3


class TestWhatAnAgentKeeps:
    def test_its_files_are_shown_inside_its_own_folders(self, admin, seed, runtime):
        install(seeded_org(), "agt_left", "Notes")
        runtime.files = {"count": 2, "bytes": 2052, "files": [
            {"path": "/data/agents/workers/agt_left/home/notes/a.txt",
             "bytes": 4, "modified": 100},
            {"path": "/data/agents/workers/agt_left/spool/big.bin",
             "bytes": 2048, "modified": 200},
        ]}
        said = app_call(admin, "Agents:Monitor:Files",
                        {"agent": "agt_left"}).json()["data"]
        assert said["name"] == "Notes" and said["count"] == 2
        assert [entry["path"] for entry in said["files"]] == [
            "home/notes/a.txt", "spool/big.bin"]
        assert "/data/agents" not in str(said)
        _, asked, _ = runtime.asked[-1]
        assert asked == {"agent": "agt_left"}

    def test_an_agent_that_has_not_run_has_nothing_and_says_why(
            self, admin, seed, runtime):
        install(seeded_org(), "agt_left", "Notes")
        runtime.files = {"error": "That agent has no place here."}
        said = app_call(admin, "Agents:Monitor:Files",
                        {"agent": "agt_left"}).json()["data"]
        assert said["files"] == [] and "no place" in said["note"]

    def test_another_organizations_agent_is_not_listed(self, app, seed, runtime):
        left, right = _two(app)
        install(right.org_id, "agt_right", "Browser")
        assert app_call(left.client, "Agents:Monitor:Files",
                        {"agent": "agt_right"}).status_code == 404
        assert runtime.asked == []


# ======================================================================
# What they did, added up (Agents:Monitor:Summary)
# ======================================================================

def _ran(org_id, agent_id, name, status, ms, hours_ago=1, reached=None,
         user_id="u1", chat_id="chat_1"):
    """An execution the runtime recorded, as the audit trail keeps it."""
    from datetime import timedelta

    from database.stores import AuditStore
    from util import new_id, utc_now

    details = {"agent_id": agent_id, "agent_name": name, "status": status,
               "duration_ms": ms}
    if reached:
        details["reached"] = reached
    AuditStore().col.insert_one({
        "event_id": f"aud_{new_id()}", "event_type": "execution",
        "org_id": org_id, "user_id": user_id, "actor": "", "chat_id": chat_id,
        "execution_id": None, "function": f"{name.lower()}.go", "resource_refs": [],
        "details": details, "timestamp": utc_now() - timedelta(hours=hours_ago),
    })


class TestWhatTheyDidAddedUp:
    def test_it_is_a_grant_of_its_own(self, app, seed, admin):
        member, _ = _user(app, seed, "member@test.org")
        assert app_call(member, "Agents:Monitor:Summary").status_code == 403
        assert app_call(admin, "Agents:Monitor:Summary").status_code == 200

    def test_calls_are_counted_by_how_they_ended_agent_and_host(self, admin):
        org = seeded_org()
        _ran(org, "agt_n", "Notebook", "success", 200, reached=[{"host": "a.test", "connections": 3}])
        _ran(org, "agt_n", "Notebook", "error", 400, hours_ago=3)
        _ran(org, "agt_b", "Browser", "denied", 0, hours_ago=2,
             reached=[{"host": "a.test", "connections": 2}])
        _ran(org, "agt_n", "Notebook", "success", 600, hours_ago=24 * 10)  # outside 7 days

        said = app_call(admin, "Agents:Monitor:Summary", {"window": "7d"}).json()["data"]

        assert said["calls"] == {"ok": 1, "failed": 1, "refused": 1, "total": 3, "avg_ms": 200}
        assert said["agents"][0] == {"agent": "agt_n", "name": "Notebook", "calls": 2,
                                     "failed": 1, "avg_ms": 300}
        assert said["hosts"] == [{"host": "a.test", "connections": 5, "calls": 2}]
        assert [p["status"] for p in said["problems"]] == ["denied", "error"]  # latest first
        assert said["people"] == 1

    def test_every_step_of_the_window_is_there_the_quiet_ones_too(self, admin):
        _ran(seeded_org(), "agt_n", "Notebook", "success", 10, hours_ago=2)
        said = app_call(admin, "Agents:Monitor:Summary", {"window": "24h"}).json()["data"]
        assert said["unit"] == "hour"
        assert len(said["series"]) in (24, 25)
        assert sum(step["ok"] for step in said["series"]) == 1

    def test_an_organization_adds_up_its_own_and_no_other(self, app, seed):
        left, right = _two(app)
        _ran(left.org_id, "agt_l", "Left", "success", 10)
        theirs = app_call(right.client, "Agents:Monitor:Summary").json()["data"]
        assert theirs["calls"]["total"] == 0
        mine = app_call(left.client, "Agents:Monitor:Summary").json()["data"]
        assert mine["calls"]["total"] == 1

    def test_approvals_asked_decided_and_waiting(self, admin):
        from database.stores import AuditStore
        from database.stores import UserStore
        from conftest import ADMIN_EMAIL

        user = UserStore().get_by_email(ADMIN_EMAIL)
        user = {"org_id": user["org_id"], "user_id": user["_id"], "email": ADMIN_EMAIL}
        store = AuditStore()
        for ref in ("ap_1", "ap_2", "ap_3"):
            store.append("approval.requested", user, resource_refs=[ref])
        store.append("approval.resolved", user, resource_refs=["ap_1"], details={"decision": "allow"})
        store.append("approval.resolved", user, resource_refs=["ap_2"], details={"decision": "deny"})

        said = app_call(admin, "Agents:Monitor:Summary").json()["data"]
        assert said["approvals"] == {"asked": 3, "allowed": 1, "denied": 1, "waiting": 1}

    def test_a_window_it_does_not_know_is_refused(self, admin):
        assert app_call(admin, "Agents:Monitor:Summary", {"window": "1y"}).status_code == 400


class TestTheTrailCountedByKind:
    def test_the_first_page_says_how_many_of_each_kind(self, admin):
        org = seeded_org()
        _ran(org, "agt_n", "Notebook", "success", 10)
        _ran(org, "agt_n", "Notebook", "error", 10)
        page = app_call(admin, "AI:Audit:List_all", {"counts": True}).json()["data"]
        assert page["counts"]["execution"] == 2
        # A later page is the same window: it is not counted again.
        later = app_call(admin, "AI:Audit:List_all", {
            "counts": True, "before": {"timestamp": "2100-01-01T00:00:00+00:00", "event_id": "z"}}).json()["data"]
        assert "counts" not in later
