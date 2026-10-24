"""Nodes:Node — the execution nodes that were admitted, as a view
(docs/system/execution-nodes.md).

    Nodes:Node:List     the organization's own nodes — and, for the
                        operator, the shared ones: who admitted each
                        (its authority) and for which organization, when
                        it was first and last seen, whether it is
                        connected now, the chats it holds, its Execution
                        Sandboxes as it reports them, and why, by whom
                        and when it was refused, where it was
    Nodes:Node:Update   the name a person gives it, shown in place of
                        the one its credential says, and a note
    Nodes:Node:Refuse   one node refused by name, now, with the reason
                        given: its credential no longer admits it, and
                        its link is closed
    Nodes:Node:Allow    a node refused by name, admitted again
    Nodes:Node:Forget   a node that is not connected, and holds no
                        chat, taken off the list. A refusal stays
    Nodes:Node:Drain    one of a connected node's Execution Sandboxes
                        drained — no new agent placed there, those
                        running finish — or, with ``drain: false``,
                        let take work again; with no ``sandbox_id``,
                        every sandbox the node reports. Asked over the
                        node's link

Nobody fills this list in: it is kept from what admitted nodes
presented. What a node reports — its version, its load, the locks it
says its Sandboxes hold — is shown as it said it, and never admits or
refuses it: a node can say anything about itself. The name and note a
person gives a node are theirs, and never read by admission either.
"""

from __future__ import annotations

from typing import Any, Dict, List

from database.stores import (
    AgentManifestStore, AuditStore, AuthorityStore, LeaseStore, NodeStore,
    RefusedNodeStore, UserStore)
from server.custom_logging import CustomLoggerFactory
from util import iso

from api.endpoints.app.nodes.authority_controller import Operator, Organizations


class NodeController:
    Name = "Node"

    def __init__(self):
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data: dict) -> dict:
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    @staticmethod
    def _org(user: dict) -> str:
        return str(user.get("org_id") or "")

    @classmethod
    def _scopes(cls, user: dict) -> list:
        scopes = [cls._org(user)]
        if Operator.is_operator(user):
            scopes.append(AuthorityStore.SHARED)
        return scopes

    @staticmethod
    def _live() -> Dict[str, Any]:
        from server.setup.app_state import get_node_links

        return {link.key: link for link in get_node_links().everywhere()}

    # ------------------------------------------------------------------
    # The view
    # ------------------------------------------------------------------

    def list(self, data: dict, user: dict):
        org_id = self._org(user)
        refusals = RefusedNodeStore().all()
        authorities = {doc["_id"]: doc for doc in AuthorityStore().list()}
        live = self._live()
        docs = NodeStore().list(self._scopes(user))
        names = Organizations.names({doc.get("scope", "") for doc in docs})
        held = self._held(docs, org_id)
        agents = self._agent_names(docs, org_id)
        nodes = []
        for doc in docs:
            public = NodeStore.to_public(doc)
            authority = authorities.get(public["authority_id"]) or {}
            # Who admitted it: the authority that vouched, and who
            # registered that authority — never a step for this node.
            public["authority"] = authority.get("name", "")
            public["authority_kind"] = authority.get("kind", "")
            public["admitted_by"] = str(
                (authority.get("registered_by") or {}).get("email") or "")
            public["authority_revoked"] = authority.get("revoked_at") is not None
            public["organization"] = Organizations.of(public["scope"], names)
            refusal = refusals.get(public["node_id"])
            public["refused"] = refusal is not None
            public["refusal"] = (RefusedNodeStore.to_public(refusal)
                                 if refusal is not None else None)
            link = live.get(public["node_id"])
            public["connected"] = link is not None
            public["connected_since"] = (
                iso_seconds(link.connected_at) if link is not None else None)
            public["chats"] = link.chat_count if link is not None else 0
            ours, elsewhere = held.get(public["node_id"], ([], 0))
            public["held_chats"] = ours
            public["held_elsewhere"] = elsewhere
            public["sandboxes"] = self._sandboxes(public["reports"], agents)
            nodes.append(public)
        return {"nodes": nodes}, 200

    @staticmethod
    def _held(docs: List[dict], org_id: str) -> Dict[str, tuple]:
        """The chats each node holds a live lease on: the caller's own
        organization's by chat and person, and how many of others' —
        a shared node holds every organization's, and the operator
        reads none of theirs here."""
        found: Dict[str, tuple] = {}
        users: set = set()
        leases = LeaseStore()
        for doc in docs:
            ours, elsewhere = [], 0
            for lease in leases.of_node(doc["_id"]):
                if str(lease.get("org_id") or "") == org_id:
                    ours.append(lease)
                    users.add(str(lease.get("user_id") or ""))
                else:
                    elsewhere += 1
            found[doc["_id"]] = (ours, elsewhere)
        emails = {row["_id"]: str(row.get("email") or "")
                  for row in UserStore().list_by_ids(sorted(users - {""}))}
        return {key: ([{"chat_id": str(lease["_id"]),
                        "user_email": emails.get(str(lease.get("user_id") or ""), ""),
                        "since": iso(lease.get("granted_at"))}
                       for lease in ours], elsewhere)
                for key, (ours, elsewhere) in found.items()}

    @staticmethod
    def _agent_names(docs: List[dict], org_id: str) -> Dict[str, str]:
        """The caller's organization's agents among those the nodes'
        sandboxes say they run: by id, the name a person knows."""
        ids = set()
        for doc in docs:
            for sandbox in (doc.get("reports") or {}).get("sandboxes") or []:
                if isinstance(sandbox, dict):
                    ids.update(str(agent) for agent in sandbox.get("running") or [])
        if not ids:
            return {}
        names = {}
        for row in AgentManifestStore().list_by_ids(sorted(ids)):
            if str(row.get("org_id") or "") != org_id:
                continue
            manifest = row.get("manifest") or {}
            names[row["_id"]] = str(manifest.get("name") or row.get("qualified_id")
                                    or row["_id"])
        return names

    @staticmethod
    def _sandboxes(reports: Dict[str, Any], agents: Dict[str, str]) -> List[dict]:
        """The node's Execution Sandboxes as it reports them, in one
        shape: each one's name, labels, whether it drains and takes
        work, its memory, processors and workers where it said them,
        and the agents it runs — the caller's by name, others counted."""
        shown = []
        for said in reports.get("sandboxes") or []:
            if not isinstance(said, dict) or not said.get("sandbox"):
                continue
            capacity = said.get("capacity") if isinstance(said.get("capacity"), dict) else {}
            memory = capacity.get("memory") if isinstance(capacity.get("memory"), dict) else {}
            cpus = capacity.get("cpus") if isinstance(capacity.get("cpus"), dict) else {}
            running = [str(agent) for agent in said.get("running") or []]
            shown.append({
                "sandbox_id": str(said["sandbox"]),
                "labels": [str(label) for label in said.get("labels") or []],
                "draining": bool(said.get("draining")),
                "eligible": bool(said.get("eligible")),
                "load": {
                    "memory_used": _number(memory.get("used")),
                    "memory_limit": _number(memory.get("limit")),
                    "cpus_used": _number(cpus.get("used")),
                    "cpus_limit": _number(cpus.get("limit")),
                    "workers": _number(capacity.get("workers")),
                } if capacity else None,
                "agents_running": len(running),
                "agents": [{"agent_id": agent, "name": agents[agent]}
                           for agent in running if agent in agents],
            })
        return shown

    # ------------------------------------------------------------------
    # Acting on one node
    # ------------------------------------------------------------------

    def _mine(self, user: dict, key: str):
        doc = NodeStore().get(key)
        if doc is None or doc.get("scope") not in self._scopes(user):
            return None
        return doc

    def update(self, data: dict, user: dict):
        payload = self._payload(data)
        key = str(payload.get("node_id") or "")
        if self._mine(user, key) is None:
            return {"error": "No such node."}, 404
        changes = {field: payload[field] for field in ("display_name", "description")
                   if field in payload and payload[field] is not None}
        if not changes:
            return {"error": "Say what to change: display_name or description."}, 400
        try:
            doc = NodeStore().describe(key, changes)
        except ValueError as exc:
            return {"error": str(exc)}, 400
        AuditStore().append("node.described", user, resource_refs=[key],
                            details={"changed": sorted(changes)})
        return {"node": NodeStore.to_public(doc)}, 200

    async def refuse(self, data: dict, user: dict):
        payload = self._payload(data)
        key = str(payload.get("node_id") or "")
        if self._mine(user, key) is None:
            return {"error": "No such node."}, 404
        try:
            reason = RefusedNodeStore.clean_reason(payload.get("reason"))
        except ValueError as exc:
            return {"error": str(exc)}, 400
        refusals = RefusedNodeStore()
        refusals.refuse(key, user, reason)
        from api.services.nodes.admission import Admission

        Admission.changed()
        from api.endpoints.app.nodes.authority_controller import AuthorityController

        closed = await AuthorityController._close_links(node_key=key)
        AuditStore().append("node.refused", user, resource_refs=[key],
                            details={"links_closed": closed, "reason": reason})
        return {"refused": True, "links_closed": closed,
                "refusal": RefusedNodeStore.to_public(refusals.get(key))}, 200

    def allow(self, data: dict, user: dict):
        key = str(self._payload(data).get("node_id") or "")
        if self._mine(user, key) is None:
            return {"error": "No such node."}, 404
        allowed = RefusedNodeStore().allow(key)
        from api.services.nodes.admission import Admission

        Admission.changed()
        if allowed:
            AuditStore().append("node.allowed", user, resource_refs=[key])
        return {"allowed": allowed}, 200

    def forget(self, data: dict, user: dict):
        """A node that is gone, taken off the list: one that is not
        connected and holds no chat — a node that stopped answering
        holds its chats until their leases lapse, a minute at most.
        A refusal by name stays: a node refused is refused until it is
        allowed, forgotten or not. A node its credential still admits
        is kept again the next time it connects."""
        key = str(self._payload(data).get("node_id") or "")
        if self._mine(user, key) is None:
            return {"error": "No such node."}, 404
        if key in self._live():
            return {"error": "That node is connected now. Refuse it, or "
                             "revoke its authority, and forget it once it "
                             "is gone."}, 409
        held = LeaseStore().of_node(key)
        if held:
            return {"error": f"That node still holds {len(held)} "
                             f"chat{'s' if len(held) != 1 else ''} until "
                             "its leases lapse, within a minute. Try "
                             "again then."}, 409
        NodeStore().forget(key)
        refused = RefusedNodeStore().get(key) is not None
        AuditStore().append("node.forgotten", user, resource_refs=[key],
                            details={"refused": refused})
        return {"forgotten": True, "refused": refused}, 200

    async def drain(self, data: dict, user: dict):
        """One Execution Sandbox of a connected node drained, or let take
        work again with ``drain: false`` — or, with no ``sandbox_id``,
        every sandbox the node reports. Who may is decided here, as for
        refusing the node: its own organization's administrators, and
        the operator for a shared node. The node only does it."""
        payload = self._payload(data)
        key = str(payload.get("node_id") or "")
        sandbox = str(payload.get("sandbox_id") or "").strip()
        if self._mine(user, key) is None:
            return {"error": "No such node."}, 404
        on = payload.get("drain", True) is not False
        link = self._live().get(key)
        if link is None:
            return {"error": "That node is not connected now."}, 409
        if not sandbox:
            return await self._drain_all(link, key, on, user)
        try:
            said = await link.ask("drain", {"sandbox": sandbox, "on": on})
        except TimeoutError:
            return {"error": "The node did not answer."}, 504
        except RuntimeError as exc:
            return {"error": f"The node refused: {exc}"}, 409
        self._drained(user, key, sandbox, on, bool(said.get("draining")))
        return {"node_id": key, "sandbox_id": sandbox,
                "draining": bool(said.get("draining"))}, 200

    async def _drain_all(self, link, key: str, on: bool, user: dict):
        """Every sandbox the node reports, asked in turn: each one's
        answer, or what went wrong with it."""
        named = [str(said.get("sandbox")) for said in
                 (link.reports.get("sandboxes") or [])
                 if isinstance(said, dict) and said.get("sandbox")]
        if not named:
            return {"error": "That node reports no Execution Sandbox to drain."}, 409
        answers = []
        for sandbox in named:
            try:
                said = await link.ask("drain", {"sandbox": sandbox, "on": on})
            except TimeoutError:
                answers.append({"sandbox_id": sandbox, "error": "The node did not answer."})
                continue
            except RuntimeError as exc:
                answers.append({"sandbox_id": sandbox, "error": f"The node refused: {exc}"})
                continue
            draining = bool(said.get("draining"))
            self._drained(user, key, sandbox, on, draining, whole=True)
            answers.append({"sandbox_id": sandbox, "draining": draining})
        done = [answer for answer in answers if "error" not in answer]
        if not done:
            return {"error": answers[0]["error"], "sandboxes": answers}, 409
        return {"node_id": key, "draining": on, "sandboxes": answers}, 200

    @staticmethod
    def _drained(user: dict, key: str, sandbox: str, on: bool, draining: bool,
                 whole: bool = False) -> None:
        details: Dict[str, Any] = {"draining": draining}
        if whole:
            details["whole_node"] = True
        AuditStore().append("sandbox.drained" if on else "sandbox.resumed", user,
                            resource_refs=[key, f"{key}/{sandbox}"],
                            details=details)


def _number(value: Any):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def iso_seconds(seconds: float):
    from datetime import datetime, timezone

    return iso(datetime.fromtimestamp(float(seconds), tz=timezone.utc))
