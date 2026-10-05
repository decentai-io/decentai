"""Agents:Monitor — what agents use and did, for a person to be shown
(docs/system/monitoring.md).

The runtime writes down what it sees of agents' processes and answers
what they use now. It does not know the platform's people or their
organizations: one runtime serves them all and writes one log. This is
where that is decided.

    Agents:Monitor:Usage    what the agents are given, and what each of
                            this organization's running agents uses now;
                            where the deployment is one organization's,
                            what each part of the platform holds too
    Agents:Monitor:Events   what happened: workers' lives, the helper's
                            jobs, logs, connections, processes
    Agents:Monitor:Files    what one of this organization's agents keeps

**An organization is shown its own agents and no other's.** Everything
an agent did carries its approval's ref, which is this organization's
or is not. What carries none is the platform's own — the builder, a
verification, the helper's check — and one runtime does that for every
organization, so it is shown only where the deployment has one.

Each action is a grant of its own, held by administrators and by
whoever they give it to: what an agent did is what every person of the
organization did through it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from api.endpoints.app.ai.base import AIController
from api.services.agents.monitoring import MonitorError, RuntimeMonitor
from contracts.container import ContainerUsage
from database.stores import AgentManifestStore, OrganizationStore
from server.setup.app_state import get_db


class MonitorController(AIController):
    DEFAULT_LIMIT = 100
    MAX_LIMIT = 500
    #: What the runtime is asked for to fill one page: the log is every
    #: organization's, and a page is filled from this one's part of it.
    ASKED = 1000
    #: How many times the runtime is asked again before a page is
    #: handed over short.
    ROUNDS = 5

    #: The kinds a person may ask for.
    KINDS = ("worker.started", "worker.ended", "worker.failed", "helper",
             "program", "log", "connection", "process", "memory")

    def __init__(self):
        self.agents = AgentManifestStore()
        self.organizations = OrganizationStore()
        self.runtime = RuntimeMonitor()
        #: The backend's own container, read by the backend: each part
        #: of the platform can read its own and no other's.
        self.own = ContainerUsage()

    # ------------------------------------------------------------------
    # Whose is whose
    # ------------------------------------------------------------------

    def _mine(self, user: dict) -> Dict[str, str]:
        """This organization's agents: approval ref -> the name a
        person knows it by."""
        return {
            str(doc["_id"]): str(
                ((doc.get("manifest") or {}).get("agent") or {}).get("name")
                or doc["_id"])
            for doc in self.agents.list(self._org(user))
        }

    def _alone(self) -> bool:
        """Whether this deployment has one organization: then what the
        platform did for no agent in particular is that organization's
        to see."""
        return self.organizations.col.count_documents({}, limit=2) == 1

    # ------------------------------------------------------------------
    # What they use now
    # ------------------------------------------------------------------

    async def usage(self, data, user):
        try:
            said = await self.runtime.usage()
        except MonitorError as exc:
            return self._fail(data, "runtime_unavailable", str(exc), 503)
        mine = self._mine(user)
        usage = said.get("usage")
        # The platform's own parts are the deployment's, not an
        # organization's: shown where the two are the same.
        services = self._services(said) if self._alone() else []
        if not isinstance(usage, dict):
            # Agents with no container of their own: nobody adds it up.
            return self._respond(data, {
                "counted": False, "agents": [], "limits": {},
                "memory": None, "services": services,
                "confined": said.get("confined") or {}})
        agents = [
            {**agent, "name": mine[str(agent.get("agent") or "")]}
            for agent in usage.get("agents") or []
            if isinstance(agent, dict) and str(agent.get("agent") or "") in mine
        ]
        return self._respond(data, {
            "counted": True, "agents": agents,
            "limits": usage.get("limits") or {},
            # The container's own, which every organization's agents
            # share: how full it is, never whose.
            "memory": usage.get("memory"),
            "at": usage.get("at"),
            "services": services,
            "confined": said.get("confined") or {},
        })

    def _services(self, said: Dict[str, Any]) -> List[Dict[str, Any]]:
        """What each part of the platform holds now, each as it read
        of itself: the backend here, the runtime and the agents'
        container in the runtime's answer, the database as it says."""
        found = [{"id": "backend", "name": "Backend", **self.own.now()}]
        runtime = said.get("runtime")
        if isinstance(runtime, dict):
            found.append({"id": "runtime", "name": "Runtime", **{
                key: runtime.get(key)
                for key in ("memory", "memory_limit", "cpu", "cpus")}})
        usage = said.get("usage")
        if isinstance(usage, dict):
            limits = usage.get("limits") or {}
            found.append({
                "id": "agents", "name": "Agents",
                "memory": usage.get("memory"),
                "memory_limit": limits.get("memory"),
                "cpu": usage.get("cpu"), "cpus": limits.get("cpus")})
        database = self._database()
        if database:
            found.append({"id": "database", "name": "Database", **database})
        return found

    @staticmethod
    def _database() -> Dict[str, Any]:
        """What the database says of itself: the memory its server
        holds, and how much it keeps on disk for this platform. Empty
        where the platform's account may not ask."""
        said: Dict[str, Any] = {}
        try:
            mongo = get_db()
            kept = mongo.db.command("dbStats")
            said["disk"] = int(kept.get("storageSize") or 0) + int(
                kept.get("indexSize") or 0)
            server = mongo.client.admin.command("serverStatus")
            resident = (server.get("mem") or {}).get("resident")
            if isinstance(resident, (int, float)):
                said["memory"] = int(resident) * 1024 * 1024
            said["connections"] = int(
                (server.get("connections") or {}).get("current") or 0)
        except Exception:
            # An account that may read and write its own database, and
            # ask nothing of the server: what it could say, it said.
            pass
        return said

    # ------------------------------------------------------------------
    # What happened
    # ------------------------------------------------------------------

    async def events(self, data, user):
        payload = self._payload(data)
        kinds = payload.get("kinds")
        if kinds is not None and not (
                isinstance(kinds, list)
                and all(isinstance(kind, str) for kind in kinds)):
            return self._fail(data, "invalid_request",
                              "kinds must be a list of names.")
        kinds = [kind for kind in kinds or [] if kind in self.KINDS]
        agent = str(payload.get("agent") or "")
        mine = self._mine(user)
        if agent and agent not in mine:
            return self._fail(data, "not_found",
                              "That agent is not installed here.", 404)
        before = payload.get("before")
        if before is not None and (isinstance(before, bool)
                                   or not isinstance(before, (int, float))):
            return self._fail(data, "invalid_request",
                              "before is the time a previous page ended at.")
        limit = self._limit(data, self.DEFAULT_LIMIT, self.MAX_LIMIT)
        alone = self._alone()

        found: List[Dict[str, Any]] = []
        more = True
        try:
            for _ in range(self.ROUNDS):
                asked = await self.runtime.events(
                    self.ASKED, kinds, agent, before)
                for event in asked:
                    if isinstance(event, dict) and self._shown(event, mine, alone):
                        found.append(self._named(event, mine))
                more = len(asked) >= self.ASKED
                if asked:
                    # How far back was read: where the next asking of
                    # the runtime, or the next page, goes on from.
                    before = asked[-1].get("at")
                if not more or len(found) >= limit:
                    break
        except MonitorError as exc:
            return self._fail(data, "runtime_unavailable", str(exc), 503)
        page = found[:limit]
        return self._respond(data, {
            "events": page,
            # Where the next page begins: after the last one shown, or
            # — nothing of this organization's in what was read — after
            # the last one read.
            "next_before": (page[-1].get("at") if len(found) > limit
                            else before if more else None),
        })

    @staticmethod
    def _shown(event: Dict[str, Any], mine: Dict[str, str], alone: bool) -> bool:
        agent = str(event.get("agent") or "")
        if agent in mine:
            return True
        # The platform's own — the builder, a verification, a check of
        # the helper — or an agent of another organization.
        return alone and not agent.startswith("agt_")

    @staticmethod
    def _named(event: Dict[str, Any], mine: Dict[str, str]) -> Dict[str, Any]:
        """The event, under the name this organization's approval has
        now: the one written down was the manifest's on the day."""
        agent = str(event.get("agent") or "")
        return {**event, "name": mine[agent]} if agent in mine else event

    # ------------------------------------------------------------------
    # What one agent keeps
    # ------------------------------------------------------------------

    async def files(self, data, user):
        agent = str(self._payload(data).get("agent") or "")
        mine = self._mine(user)
        if agent not in mine:
            return self._fail(data, "not_found",
                              "That agent is not installed here.", 404)
        try:
            said = await self.runtime.files(agent)
        except MonitorError as exc:
            return self._fail(data, "runtime_unavailable", str(exc), 503)
        if said.get("error"):
            return self._respond(data, {
                "agent": agent, "name": mine[agent], "files": [],
                "count": 0, "bytes": 0, "note": str(said["error"])})
        return self._respond(data, {
            "agent": agent, "name": mine[agent],
            "files": [self._file(entry) for entry in said.get("files") or []
                      if isinstance(entry, dict)],
            "count": int(said.get("count") or 0),
            "bytes": int(said.get("bytes") or 0),
        })

    @staticmethod
    def _file(entry: Dict[str, Any]) -> Dict[str, Any]:
        """A file as a person is shown it: where it is inside the
        agent's own folders, never where those are on the machine."""
        path = str(entry.get("path") or "")
        for folder in ("home", "spool"):
            marker = f"/{folder}/"
            if marker in path:
                path = folder + "/" + path.split(marker, 1)[1]
                break
        return {"path": path, "bytes": int(entry.get("bytes") or 0),
                "modified": entry.get("modified")}
