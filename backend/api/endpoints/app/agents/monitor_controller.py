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
                            jobs, logs, connections, processes —
                            narrowed, searched, and added up
    Agents:Monitor:Files    what one of this organization's agents keeps
    Agents:Monitor:Summary  what this organization's agents did in a
                            window, added up from the audit trail

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

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from api.endpoints.app.ai.base import AIController
from api.services.agents.monitoring import MonitorError, RuntimeMonitor
from contracts.container import ContainerUsage
from database.stores import AgentManifestStore, AuditStore, OrganizationStore
from server.setup.app_state import get_db
from util import iso, utc_now


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
    # What they did, added up
    # ------------------------------------------------------------------

    #: The windows a summary is asked for, each with the step its
    #: series moves in.
    WINDOWS = {"24h": (timedelta(hours=24), "hour"),
               "7d": (timedelta(days=7), "day"),
               "30d": (timedelta(days=30), "day")}

    def summary(self, data, user):
        """What this organization's agents did in a window, added up
        from the audit trail: calls over time by how they ended, the
        agents called most and how they fared, the hosts reached,
        approvals, model calls and who was active, and the latest calls
        that did not succeed. Counts, never a call's inputs."""
        window = str(self._payload(data).get("window") or "7d")
        if window not in self.WINDOWS:
            return self._fail(data, "invalid_request",
                              f"window is one of {', '.join(self.WINDOWS)}.")
        span, unit = self.WINDOWS[window]
        now = utc_now()
        since = self._step_down(now - span, unit)
        found = AuditStore().activity(self._org(user), since, unit)

        # Every step of the window, the quiet ones too: a chart of
        # only the busy hours draws a line through the silence.
        counted = {row["at"]: row for row in found["series"]}
        step = timedelta(hours=1) if unit == "hour" else timedelta(days=1)
        series, at = [], since
        while at <= now:
            key = iso(at)
            series.append(counted.get(key) or {"at": key, "ok": 0, "failed": 0, "refused": 0})
            at += step
        return self._respond(data, {**found, "series": series, "window": window,
                                    "unit": unit, "since": iso(since), "until": iso(now)})

    @staticmethod
    def _step_down(moment: datetime, unit: str) -> datetime:
        """The start of the hour, or of the day, a moment falls in, as
        the database truncates it: in UTC."""
        moment = moment.astimezone(timezone.utc)
        if unit == "hour":
            return moment.replace(minute=0, second=0, microsecond=0)
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)

    # ------------------------------------------------------------------
    # What happened
    # ------------------------------------------------------------------

    #: How a thing went, as a person narrows to it: "trouble" is any of
    #: the three that did not go well.
    OUTCOMES = ("ok", "trouble", "refused", "unreached", "failed")
    #: The longest search, in characters.
    LONGEST_TEXT = 200
    #: How many times the runtime is asked when the first page is added
    #: up too: the latest this many thousand events are counted.
    TALLY_ROUNDS = 10

    async def events(self, data, user):
        """What happened, the latest first, a page at a time. `kinds`
        is what the runtime reads; `kind`, `agent`, `outcome`, `host`
        and `text` narrow what is shown; `since` and `until`, in
        seconds, keep it to the days asked for. With `tally`, the first
        page comes with what the filters leave, added up."""
        payload = self._payload(data)
        mine = self._mine(user)
        asked = self._filters(payload, mine)
        if isinstance(asked, tuple):
            return self._fail(data, *asked)
        before, since, until = asked["before"], asked["since"], asked["until"]
        if until is not None and (before is None or before > until):
            before = until
        limit = self._limit(data, self.DEFAULT_LIMIT, self.MAX_LIMIT)
        alone = self._alone()
        tally = _Tally() if payload.get("tally") is True else None
        # Adding up reads every kind and agent the counts choose among;
        # a page alone has the runtime read only what it shows.
        kinds = asked["kinds"] if tally or not asked["kind"] else [asked["kind"]]
        agent = "" if tally else asked["agent"]

        found: List[Dict[str, Any]] = []
        more = True
        try:
            for _ in range(self.TALLY_ROUNDS if tally else self.ROUNDS):
                read = await self.runtime.events(self.ASKED, kinds, agent, before)
                more = len(read) >= self.ASKED
                if since is not None:
                    recent = [event for event in read
                              if not isinstance(event, dict)
                              or (event.get("at") or 0) >= since]
                    # An event from before `since`: all after it are older.
                    more = more and len(recent) == len(read)
                    read = recent
                for event in read:
                    if not (isinstance(event, dict) and self._shown(event, mine, alone)):
                        continue
                    event = self._named(event, mine)
                    if tally:
                        tally.add(event, asked, mine)
                    if len(found) <= limit and _matches(event, asked):
                        found.append(event)
                if read:
                    # How far back was read: where the next asking of
                    # the runtime, or the next page, goes on from.
                    before = read[-1].get("at")
                if not more or (not tally and len(found) >= limit):
                    break
        except MonitorError as exc:
            return self._fail(data, "runtime_unavailable", str(exc), 503)
        page = found[:limit]
        said: Dict[str, Any] = {
            "events": page,
            # Where the next page begins: after the last one shown, or
            # — nothing more of what was asked in what was read — after
            # the last one read.
            "next_before": (page[-1].get("at") if len(found) > limit
                            else before if more else None),
        }
        if tally:
            said["tally"] = tally.said(mine, complete=not more, reached=before)
        return self._respond(data, said)

    def _filters(self, payload: Dict[str, Any], mine: Dict[str, str]):
        """What a person asked to be shown, checked; or why it cannot
        be, as (code, message, status)."""
        kinds = payload.get("kinds")
        if kinds is not None and not (
                isinstance(kinds, list)
                and all(isinstance(kind, str) for kind in kinds)):
            return "invalid_request", "kinds must be a list of names.", 400
        words = {name: payload.get(name) or "" for name in
                 ("kind", "agent", "outcome", "host", "text")}
        if not all(isinstance(value, str) for value in words.values()):
            return ("invalid_request",
                    "kind, agent, outcome, host and text are words.", 400)
        if words["kind"] and words["kind"] not in self.KINDS:
            return ("invalid_request",
                    f"kind is one of {', '.join(self.KINDS)}.", 400)
        if words["agent"] and words["agent"] not in mine:
            return "not_found", "That agent is not installed here.", 404
        if words["outcome"] and words["outcome"] not in self.OUTCOMES:
            return ("invalid_request",
                    f"outcome is one of {', '.join(self.OUTCOMES)}.", 400)
        if not self._is_time(payload.get("before")):
            return ("invalid_request",
                    "before is the time a previous page ended at.", 400)
        if not (self._is_time(payload.get("since"))
                and self._is_time(payload.get("until"))):
            return "invalid_request", "since and until are times, in seconds.", 400
        return {
            "kinds": [kind for kind in kinds or [] if kind in self.KINDS],
            "kind": words["kind"], "agent": words["agent"],
            "outcome": words["outcome"],
            "host": words["host"].strip().lower(),
            "text": words["text"].strip().lower()[: self.LONGEST_TEXT],
            "before": payload.get("before"),
            "since": payload.get("since"), "until": payload.get("until"),
        }

    @staticmethod
    def _is_time(value: Any) -> bool:
        """Whether a time was left out, or given as seconds."""
        return value is None or (not isinstance(value, bool)
                                 and isinstance(value, (int, float)))

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


# ----------------------------------------------------------------------
# What happened, narrowed and added up
# ----------------------------------------------------------------------

#: What a search reads of an event: its words, never its numbers.
SEARCHED = ("kind", "agent", "name", "host", "why", "line", "command",
            "program", "job", "where", "refused")


def _outcome(event: Dict[str, Any]) -> str:
    """How it went: ok, or refused, unreached or failed."""
    kind = event.get("kind")
    if kind == "connection":
        if event.get("allowed") is False:
            return "refused"
        return "unreached" if event.get("reached") is False else "ok"
    if kind in ("helper", "program"):
        return "ok" if event.get("code") == 0 else "failed"
    return "failed" if kind in ("worker.failed", "memory") else "ok"


def _matches(event: Dict[str, Any], asked: Dict[str, Any], but: str = "") -> bool:
    """Whether the filters leave an event. `but` names the one not
    applied, to count what choosing another of it would show."""
    if asked["kinds"] and event.get("kind") not in asked["kinds"]:
        return False
    if but != "kind" and asked["kind"] and event.get("kind") != asked["kind"]:
        return False
    if but != "agent" and asked["agent"] and str(event.get("agent") or "") != asked["agent"]:
        return False
    if but != "outcome" and asked["outcome"]:
        outcome = _outcome(event)
        wanted = asked["outcome"]
        if outcome != wanted and not (wanted == "trouble" and outcome != "ok"):
            return False
    if but != "host" and asked["host"] and str(event.get("host") or "").lower() != asked["host"]:
        return False
    if asked["text"]:
        words = " ".join(str(event.get(field) or "") for field in SEARCHED)
        return asked["text"] in words.lower()
    return True


class _Tally:
    """What the filters leave, added up as the log is read: how many,
    and of each kind, outcome, agent and host — each counted as if its
    own filter were not chosen, so the other choices stay in sight —
    and what the connections among them carried."""

    FACETS = ("kind", "outcome", "agent", "host")
    #: The agents and hosts named, the most first.
    TOP = 12

    def __init__(self):
        self.matching = 0
        self.counts: Dict[str, Counter] = {facet: Counter() for facet in self.FACETS}
        self.sent = self.received = 0

    def add(self, event: Dict[str, Any], asked: Dict[str, Any],
            mine: Dict[str, str]) -> None:
        agent = str(event.get("agent") or "")
        facets = {
            "kind": event.get("kind"),
            "outcome": _outcome(event),
            # Only an agent a person can choose: never the platform's.
            "agent": agent if agent in mine else "",
            "host": (str(event.get("host") or "").lower()
                     if event.get("kind") == "connection" else ""),
        }
        for facet, value in facets.items():
            if value and _matches(event, asked, but=facet):
                self.counts[facet][value] += 1
        if _matches(event, asked):
            self.matching += 1
            if event.get("kind") == "connection":
                self.sent += _number(event.get("sent"))
                self.received += _number(event.get("received"))

    def said(self, mine: Dict[str, str], complete: bool,
             reached: Optional[float]) -> Dict[str, Any]:
        return {
            "matching": self.matching,
            # Whether all that was asked was read, or only back to
            # `reached`: the counts are then of the latest.
            "complete": complete,
            "reached": None if complete else reached,
            "kinds": dict(self.counts["kind"]),
            "outcomes": dict(self.counts["outcome"]),
            "agents": [{"agent": agent, "name": mine.get(agent, agent), "count": count}
                       for agent, count in self.counts["agent"].most_common(self.TOP)],
            "hosts": [{"host": host, "count": count}
                      for host, count in self.counts["host"].most_common(self.TOP)],
            "sent": self.sent, "received": self.received,
        }


def _number(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
