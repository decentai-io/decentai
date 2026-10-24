"""The relay: every chat, carried to the execution node that holds it,
over that node's link (docs/system/chat-session.md,
docs/system/execution-nodes.md).

Nodes connect to the Data Core (api/services/nodes/link.py); the Data
Core dials nobody. A chat that needs a node is **attached** to one: it
is given a **lease** there (database/stores/nodes.py, ``LeaseStore``)
— the node, a number, an expiry — and a fresh delegation bound to that
node, both sent over the link with the chat's id. From then on the
door's frames for the chat go out over that link (``FRAMES``, each
naming its chat), and every frame the node emits for it comes back the
same way and reaches the person's socket as an ``AI:Chat:Event``. A
node that speaks for a chat not attached to it is not heard.

A delegation is a key, never authority: what the chat may do is what
``AI:Chat:Contract`` answers when the node asks.

**Leases.** A lease lasts a minute and the node's heartbeat renews it
every twenty seconds; every call the node makes for the chat carries
its number, and the gateway refuses any other (server/routes/app.py),
so two nodes never both act for one chat. A chat goes idle with nobody
watching and the node lets the lease go (``released``). A node that
stops answering keeps the lease until it lapses; then the chat is placed
again (``fail_over``), with a number past the last, and the new node
rebuilds it from the record as a restart always has — a call that was
running is said to have ended unknown, and is never run again by itself.

**Which node** (api/services/nodes/placement.py): the one already
holding the chat while its lease is live; otherwise among the nodes
connected — to this replica or any other — that may serve the chat's
organization, its own first, then the last holder, then the least
loaded. An organization that keeps to its own nodes (Settings:Nodes,
``own_only``) is served by no shared node: with none of its own
connected, the chat waits and its person is told why; a shared node
holding one of its chats when it changed lets the chat go at once
(``keep_to_own``).

**Several replicas** (docs/system/cluster.md). A node's link is on one
Data Core replica, and only that replica can sign the node's frames —
so the chat is bound where its node's link is: that replica mints the
chat's delegations, sends its frames, hears its events and renews its
key. Every operation here — a person arriving, a frame, a fire, a stop
— is done on this replica when the chat's node is linked here, and
otherwise asked of the replica that holds the link, over the bus
(``Elsewhere``, ``_anywhere``). When the person's socket and the node's
link share a replica, nothing crosses the bus. A node's event reaches
the person's socket wherever it is (``WSManager.deliver``). A node
that links to a replica — after the one it was on stopped — is given
back every chat it holds a live lease on, under the same numbers.

**The clock** is the Data Core's (chat_session/clock.py): it decides
when a schedule is due, puts the chat on a node (``hold``) and sends
the node a ``fire`` (``fire``); a node gives back one it never ran
(``unfired``). No chat is kept on a node for its clock. The clock and
lease fail-over run on the replica that leads, alone.

A person who stopped everything of theirs (halt.py) is attached
nowhere: no key is minted and no frame goes out until they resume. The
one exception is the stop itself, sent to the lease holder.

A delegation lives an hour; a chat may be held for days. So the relay
also renews: every attached chat whose credential is old enough is
handed a fresh one over the link, as a ``credential`` frame.

The manager is what app_state holds, and the only thing the routes and
controllers reach for.
"""

import asyncio
import time
from typing import Any, Dict, Optional

from api.services.chat_session.identity import Delegation
from api.services.nodes.placement import Placement
from database.stores import LeaseStore, OrganizationStore, UserStore
from server.custom_logging import CustomLoggerFactory
from server.setup.app_state import get_ws_manager

#: what a client may say through the relay, and the frame it becomes
FRAMES = {"user_message", "approval_decided", "question_answered", "stop",
          "schedules_changed", "screen_input", "screen_open",
          # The Data Core's own, never a browser's: an outside system's
          # event for a webhook's chat (server/routes/hooks.py). The
          # browser's socket builds only the frames of the actions it
          # knows, and this is none of them.
          "webhook_event"}


class Elsewhere(Exception):
    """The chat's node is linked to another replica: what was asked is
    that replica's to do."""

    def __init__(self, replica_id: str):
        super().__init__(replica_id)
        self.replica_id = replica_id


class ChatBinding:
    """One chat, as the relay holds it: whose it is, the node holding
    it and under which lease, and whether a person is watching."""

    def __init__(self, chat_id: str, user: Dict[str, Any]):
        self.chat_id = chat_id
        self.user = dict(user)
        self.link = None
        self.lease: Optional[int] = None
        #: whether a person has the chat open
        self.watched = False
        #: when the node last received a credential for this chat
        #: (monotonic seconds) — what renewal is due against
        self.credentialed_at: Optional[float] = None
        #: why no node holds the chat, in words for the person
        self.waiting = ""
        #: set when the node says a kill of this chat is over
        self.killed = asyncio.Event()
        #: one attachment at a time: two would mint two delegations,
        #: and the second would revoke the first's
        self.attaching = asyncio.Lock()

    @property
    def attached(self) -> bool:
        return self.link is not None and self.link.open


class RuntimeClientManager:
    #: When an attached chat's credential is renewed: well inside the
    #: delegation's hour (Delegation.DEFAULT_TTL_SECONDS), with room for
    #: a slow tick and the access cache's minute.
    RENEW_AFTER_SECONDS = 45 * 60
    RENEW_TICK_SECONDS = 60
    #: How often lapsed leases are looked for.
    LEASE_TICK_SECONDS = 5
    #: How long a killed chat is given to write down what it had done
    #: before its key is withdrawn.
    KILL_GRACE_SECONDS = 10.0
    #: How many times an operation follows a chat that moved between
    #: replicas while it was being asked for.
    ROUTE_ATTEMPTS = 3

    WAITING_FOR_A_NODE = "No execution node is connected that may serve this chat."
    WAITING_FOR_OWN = ("This organization runs its chats on its own execution "
                       "nodes only, and none of them is connected. The chat "
                       "waits for one rather than run elsewhere.")
    MOVING = ("The execution node serving this chat stopped answering; the "
              "chat moves to another within a minute.")

    def __init__(self, links):
        self.links = links
        #: this process as one replica (api/services/cluster); None for
        #: a test's links of their own
        self.cluster = getattr(links, "cluster", None)
        self.placement = Placement()
        self.chats: Dict[str, ChatBinding] = {}
        self.lock = asyncio.Lock()
        #: the renewal and lease loops, once the app's lifespan started them
        self.renewer = None
        self.leaser = None
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)
        links.on_event = self.on_event
        links.on_link.append(self.on_link)
        links.on_lost.append(self.on_lost)
        links.on_beat.append(self.on_beat)
        links.on_frame["released"] = self.on_released
        from api.services.chat_session.clock import ScheduleClock

        #: the Data Core's clock (chat_session/clock.py)
        self.clock = ScheduleClock(self)
        # A fire a node was handed and gives back unrun.
        links.on_frame["unfired"] = self.clock.on_unfired
        if self.cluster is not None:
            self.cluster.bus.on("relay", self._on_relay)
            self.cluster.bus.on("link_up", self._on_link_up)
            self.cluster.on_gone.append(self.on_replicas_gone)

    # ------------------------------------------------------------------
    async def _binding(self, chat_id: str, user: Dict[str, Any]) -> ChatBinding:
        async with self.lock:
            binding = self.chats.get(chat_id)
            if binding is None:
                binding = ChatBinding(chat_id, user)
                self.chats[chat_id] = binding
            return binding

    def _remote(self, node_key: str):
        """The node, where another replica holds its link; else None."""
        if self.cluster is None or self.links.get(node_key) is not None:
            return None
        return self.cluster.link(node_key)

    async def _attach(self, binding: ChatBinding, halting: bool = False,
                      again: bool = False) -> bool:
        """The chat on a node, under a lease: the node already holding
        it, or one placed now. ``again`` sends the attachment to the
        node already holding it too — a person arriving, who is greeted
        with the chat's present tense. Raises Elsewhere when that node's
        link is on another replica."""
        # What an attachment reads and writes — the person, the lease,
        # the organization's setting, the delegation — waits on the
        # database, on a thread: the loop carries every chat and every
        # node's link meanwhile. The lock keeps one attachment at a time.
        async with binding.attaching:
            if not halting and await asyncio.to_thread(
                    UserStore().stopped, str(binding.user.get("user_id") or "")):
                return False
            if binding.attached and not again:
                return True
            leases = LeaseStore()
            current = await asyncio.to_thread(leases.current, binding.chat_id)
            org_id = str(binding.user.get("org_id") or "")
            own_only = await asyncio.to_thread(
                OrganizationStore().own_nodes_only, org_id)
            link = None
            if leases.live(current):
                link = self.links.get(current["node_id"])
                if link is None:
                    remote = self._remote(current["node_id"])
                    if remote is not None:
                        raise Elsewhere(remote.replica_id)
                if link is not None and not self.placement.may_serve(
                        link, org_id, own_only):
                    # The organization keeps to its own nodes now: the
                    # shared node holding the chat lets it go, at once.
                    await self._let_go(binding, link)
                    current = await asyncio.to_thread(leases.current, binding.chat_id)
                    link = None
                elif link is None:
                    # Its node is gone and its lease not yet: the chat
                    # moves when the lease lapses, never sooner — the
                    # node may only be slow, and two must never act.
                    binding.waiting = self.MOVING
                    return False
            # Already this node's: attached again, it is handed the key
            # it holds rather than a fresh one, which would retire that
            # key under a call it has in flight.
            again_here = link is not None
            if link is None:
                link = self.placement.choose(
                    await asyncio.to_thread(self.links.everywhere), org_id,
                    str((current or {}).get("node_id") or ""), own_only)
                if link is None:
                    binding.waiting = (self.WAITING_FOR_OWN if own_only
                                       else self.WAITING_FOR_A_NODE)
                    return False
                if getattr(link, "replica_id", None):
                    raise Elsewhere(link.replica_id)
            lease = await asyncio.to_thread(
                leases.grant, binding.chat_id, link.key, binding.user)
            if lease is None:
                binding.waiting = self.MOVING
                return False
            age = 0.0
            try:
                resumed = await asyncio.to_thread(
                    Delegation().resumed, binding.user, binding.chat_id,
                    link.key) if again_here else None
                if resumed is not None:
                    credential, age = resumed
                else:
                    credential = await asyncio.to_thread(
                        Delegation().for_chat, binding.user, binding.chat_id,
                        link.key)
            except Exception as exc:
                self.logger.warning(
                    f"No delegation for chat {binding.chat_id}: {exc}")
                return False
            # Bound before the frame goes: the node's greeting may come
            # back while the frame is still being written, and is heard
            # only from the node the chat is attached to.
            previous = binding.link
            binding.link, binding.lease = link, int(lease["number"])
            link.chats.add(binding.chat_id)
            if not await link.send({
                    "type": "attach", "chat_id": binding.chat_id,
                    "lease": binding.lease, "credential": credential,
                    "watched": binding.watched}):
                link.chats.discard(binding.chat_id)
                binding.link = None
                return False
            if previous is not None and previous is not link:
                previous.chats.discard(binding.chat_id)
                await previous.send({"type": "detach", "chat_id": binding.chat_id})
            binding.waiting = ""
            # Renewal is due by the key's age, whichever replica made it.
            binding.credentialed_at = time.monotonic() - age
            return True

    # ------------------------------------------------------------------
    # Here, or where the chat's node is
    # ------------------------------------------------------------------

    async def _anywhere(self, op: str, chat_id: str, user: Dict[str, Any],
                        **args) -> Dict[str, Any]:
        """One of the relay's operations, on this replica when the
        chat's node is linked here, and asked of the replica that holds
        the link otherwise — following the chat if it moved meanwhile."""
        chat_id = str(chat_id)
        for _ in range(self.ROUTE_ATTEMPTS):
            try:
                return await self._here(op, chat_id, user, args)
            except Elsewhere as there:
                self._stray(chat_id)
                if args.get("watched"):
                    args["watcher"] = self.cluster.replica_id
                try:
                    answer = await self.cluster.bus.call(
                        there.replica_id, "relay",
                        {"op": op, "chat_id": chat_id, "user": user, "args": args},
                        timeout=self.KILL_GRACE_SECONDS + 10 if op == "kill" else None,
                        lane=chat_id)
                except Exception as exc:
                    self.logger.warning(
                        f"Replica {there.replica_id} did not do {op!r} for chat "
                        f"{chat_id}: {exc}")
                    answer = {"ok": False, "waiting": self.MOVING}
                if answer.get("elsewhere"):
                    continue
                if op == "attach" and args.get("watched") and not answer.get("ok"):
                    # Waiting here, where the person is: told why, and
                    # attached when a node that may serve it links.
                    binding = await self._binding(chat_id, user)
                    binding.watched = True
                    binding.waiting = str(answer.get("waiting") or "")
                return answer
        return {"ok": False, "waiting": self.MOVING}

    def _stray(self, chat_id: str) -> None:
        """A binding left here for a chat held elsewhere: forgotten,
        unless the person's socket is here, where it says they wait."""
        binding = self.chats.get(chat_id)
        if binding is None or binding.attached:
            return
        try:
            local = get_ws_manager().frontends.get(
                get_ws_manager().key(chat_id, binding.user)) is not None
        except Exception:
            local = False
        if not local:
            self.chats.pop(chat_id, None)

    async def _here(self, op: str, chat_id: str, user: Dict[str, Any],
                    args: Dict[str, Any]) -> Dict[str, Any]:
        """``op`` on this replica. Raises Elsewhere where the chat's
        node is another replica's."""
        if op == "attach":
            binding = await self._binding(chat_id, user)
            if args.get("watched"):
                binding.watched = True
            ok = await self._attach(binding, halting=bool(args.get("halting")),
                                    again=bool(args.get("again")))
            return {"ok": ok, "waiting": binding.waiting,
                    "node": binding.link.key if ok and binding.link else ""}
        if op == "send":
            return {"ok": await self._send_here(chat_id, user, args.get("frame") or {},
                                                bool(args.get("halting")))}
        if op == "fire":
            return await self._fire_here(chat_id, args.get("row") or {})
        if op == "tell":
            return {"ok": await self._tell_here(chat_id, args.get("frame") or {})}
        if op == "kill":
            return {"ok": await self._kill_here(chat_id, user)}
        if op == "release":
            return {"ok": await self._release_here(chat_id, user)}
        if op == "close":
            await self._close_here(chat_id)
            return {"ok": True}
        if op == "let_go":
            return {"ok": await self._let_go_here(chat_id, user)}
        raise ValueError(f"no relay operation {op!r}")

    async def _on_relay(self, body: Dict[str, Any], sender: str) -> Dict[str, Any]:
        """Another replica asks for a chat whose node is linked here."""
        op = str(body.get("op") or "")
        chat_id = str(body.get("chat_id") or "")
        user = body.get("user") if isinstance(body.get("user"), dict) else {}
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        if op == "unwatch":
            binding = self.chats.get(chat_id)
            if binding is not None and not self._watching(chat_id, binding.user):
                binding.watched = False
            return {}
        if args.get("watcher"):
            get_ws_manager().seen_on(chat_id, user, str(args["watcher"]))
        try:
            answer = await self._here(op, chat_id, user, args)
        except Elsewhere as there:
            self._stray(chat_id)
            return {"elsewhere": there.replica_id}
        if not answer.get("ok"):
            self._stray(chat_id)
        return answer

    def _watching(self, chat_id: str, user: Dict[str, Any]) -> bool:
        """Whether the person has the chat open, on any replica."""
        try:
            return get_ws_manager().watching(chat_id, user)
        except Exception:
            return False

    def _held(self, chat_id: str) -> bool:
        """Whether a node connected anywhere holds the chat's live lease."""
        current = LeaseStore().current(chat_id)
        if not LeaseStore.live(current):
            return False
        node = current["node_id"]
        return self.links.get(node) is not None or self._remote(node) is not None

    # ------------------------------------------------------------------
    # What the routes and controllers ask
    # ------------------------------------------------------------------

    def why_waiting(self, chat_id: str) -> str:
        binding = self.chats.get(str(chat_id))
        return binding.waiting if binding is not None else ""

    async def ensure(self, chat_id, user) -> bool:
        """The person connected to a chat: make sure a node holds it,
        with fresh tokens, and knows they are watching. False when no
        node may serve it now — the person's socket stays up regardless
        — or the person has stopped everything of theirs."""
        if UserStore().stopped(str(user.get("user_id") or "")):
            return False
        answer = await self._anywhere("attach", chat_id, user,
                                      watched=True, again=True)
        return bool(answer.get("ok"))

    async def send(self, chat_id, user, frame, halting: bool = False) -> bool:
        """One frame of the door's vocabulary, to the node holding the
        chat — placed first if none does. Said, not raised, when it does
        not go: a decision already recorded must not turn into a
        failure."""
        if str(frame.get("event") or "") not in FRAMES:
            return False
        answer = await self._anywhere("send", chat_id, user, frame=dict(frame),
                                      halting=halting)
        return bool(answer.get("ok"))

    async def _send_here(self, chat_id, user, frame, halting: bool) -> bool:
        binding = await self._binding(str(chat_id), user)
        if not await self._attach(binding, halting):
            return False
        link = binding.link
        if link is None or not await link.send(
                {"type": "chat", "chat_id": binding.chat_id, "frame": frame}):
            self.logger.warning(f"Frame for chat {chat_id} not sent")
            return False
        return True

    async def hold(self, chat_id, user) -> bool:
        """The chat on a node, nobody watching: what the clock asks
        before a fire. False when no node may take it now."""
        if UserStore().stopped(str(user.get("user_id") or "")):
            return False
        return bool((await self._anywhere("attach", chat_id, user)).get("ok"))

    def node_of(self, chat_id) -> Optional[str]:
        """The node holding a chat now, linked to any replica, or None."""
        binding = self.chats.get(str(chat_id))
        if binding is not None and binding.attached:
            return binding.link.key
        current = LeaseStore().current(str(chat_id))
        if LeaseStore.live(current) and self._held(str(chat_id)):
            return str(current["node_id"])
        return None

    async def fire(self, chat_id, user, row: Dict[str, Any]) -> Optional[str]:
        """A schedule's fire, to the node holding the chat: the Data
        Core's own voice over the link, never a frame a person could
        send. The node runs it and writes back what it did. Returns the
        node it went to, or None when it went nowhere."""
        answer = await self._anywhere("fire", chat_id, user, row=dict(row))
        if not answer.get("ok"):
            return None
        return str(answer.get("node") or "") or None

    async def _fire_here(self, chat_id: str, row: Dict[str, Any]) -> Dict[str, Any]:
        binding = self.chats.get(chat_id)
        if binding is None or not binding.attached:
            current = LeaseStore().current(chat_id)
            if LeaseStore.live(current):
                remote = self._remote(current["node_id"])
                if remote is not None:
                    raise Elsewhere(remote.replica_id)
            return {"ok": False}
        link = binding.link
        if not await link.send(
                {"type": "fire", "chat_id": binding.chat_id, "schedule": dict(row)}):
            return {"ok": False}
        return {"ok": True, "node": link.key}

    async def tell(self, chat_id, user, frame) -> bool:
        """A frame for a chat a node holds now, and nothing if none
        does: what changed on the record that a node holding the chat
        should hear (``schedules_changed``) and one that does not will
        read when it is attached. Never places a chat."""
        if str(frame.get("event") or "") not in FRAMES:
            return False
        return bool((await self._anywhere("tell", chat_id, user,
                                          frame=dict(frame))).get("ok"))

    async def _tell_here(self, chat_id: str, frame: Dict[str, Any]) -> bool:
        binding = self.chats.get(chat_id)
        if binding is None or not binding.attached:
            current = LeaseStore().current(chat_id)
            if not LeaseStore.live(current):
                return False
            link = self.links.get(current["node_id"])
            if link is None:
                remote = self._remote(current["node_id"])
                if remote is not None:
                    raise Elsewhere(remote.replica_id)
                return False
            return await link.send(
                {"type": "chat", "chat_id": chat_id, "frame": frame})
        return await binding.link.send(
            {"type": "chat", "chat_id": binding.chat_id, "frame": frame})

    async def _let_go(self, binding: ChatBinding, link) -> None:
        """The node holding a chat is told to forget it, and its lease
        is ended: what a node no longer allowed to serve the chat holds."""
        link.chats.discard(binding.chat_id)
        await link.send({"type": "detach", "chat_id": binding.chat_id})
        LeaseStore().release(binding.chat_id, link.key)
        if binding.link is link:
            binding.link, binding.lease = None, None

    async def keep_to_own(self, org_id: str) -> int:
        """An organization now keeps to its own nodes: every chat of its
        held by a shared node is let go there at once, and the ones a
        person has open placed again on one of its own — or left
        waiting, and the person told why. Returns how many moved off."""
        moved = 0
        for doc in LeaseStore().col.find({"org_id": str(org_id),
                                          "released": False}):
            if not LeaseStore.live(doc):
                continue
            link = self.links.get(doc["node_id"]) or self._remote(doc["node_id"])
            if link is None or self.placement.may_serve(link, org_id, True):
                continue
            chat_id = str(doc["_id"])
            binding = self.chats.get(chat_id)
            user = binding.user if binding is not None else {
                "org_id": org_id, "user_id": doc.get("user_id")}
            if (await self._anywhere("let_go", chat_id, user)).get("ok"):
                moved += 1
        return moved

    async def _let_go_here(self, chat_id: str, user: Dict[str, Any]) -> bool:
        """One chat of an organization that keeps to its own nodes, off
        the shared node linked here — and placed again, where it is
        open, on one of the organization's own anywhere."""
        current = LeaseStore().current(chat_id)
        if not LeaseStore.live(current):
            return False
        link = self.links.get(current["node_id"])
        if link is None:
            remote = self._remote(current["node_id"])
            if remote is not None:
                raise Elsewhere(remote.replica_id)
            return False
        binding = self.chats.get(chat_id) or ChatBinding(chat_id, user)
        await self._let_go(binding, link)
        if not (binding.watched or self._watching(chat_id, binding.user)):
            return True
        answer = await self._anywhere("attach", chat_id, binding.user,
                                      watched=True, again=True)
        if not answer.get("ok"):
            try:
                await get_ws_manager().deliver(chat_id, binding.user, {
                    "endpoint": "AI:Chat:Event",
                    "data": {"event": "runtime_unavailable", "chat_id": chat_id,
                             "detail": str(answer.get("waiting") or "")}})
            except Exception:
                pass
        return True

    async def close(self, chat_id, user) -> None:
        """Nothing may keep this chat on a node: it is being erased, or
        its person may no longer act. Its node forgets it, and its lease
        is let go."""
        await self._anywhere("close", chat_id, user)
        LeaseStore().release(str(chat_id))

    async def _close_here(self, chat_id: str) -> None:
        async with self.lock:
            binding = self.chats.pop(chat_id, None)
        link = binding.link if binding is not None else None
        if link is None:
            current = LeaseStore().current(chat_id)
            if LeaseStore.live(current):
                link = self.links.get(current["node_id"])
                if link is None:
                    remote = self._remote(current["node_id"])
                    if remote is not None:
                        raise Elsewhere(remote.replica_id)
        if link is not None:
            link.chats.discard(chat_id)
            await link.send({"type": "detach", "chat_id": chat_id})
        LeaseStore().release(chat_id)

    def chats_of(self, user):
        """The chats of this person a node linked here holds now."""
        user_id = str(user.get("user_id") or "")
        return {chat_id for chat_id, binding in self.chats.items()
                if str(binding.user.get("user_id") or "") == user_id
                and binding.attached}

    async def kill(self, chat_id, user) -> bool:
        """End everything one chat is doing, as part of the person
        stopping all of theirs: the kill is said to the node holding the
        chat — placing it if none does, since a chat may be working with
        nobody watching — the node's word that it is over is waited for
        a short while, and the chat is let go. Returns whether it was
        said."""
        return bool((await self._anywhere("kill", chat_id, user)).get("ok"))

    async def _kill_here(self, chat_id: str, user: Dict[str, Any]) -> bool:
        binding = await self._binding(chat_id, user)
        binding.killed.clear()
        said = await self._send_here(chat_id, user, {"event": "stop", "force": True},
                                     halting=True)
        if said:
            try:
                async with asyncio.timeout(self.KILL_GRACE_SECONDS):
                    await binding.killed.wait()
            except TimeoutError:
                self.logger.warning(
                    f"Chat {chat_id} did not say its kill was over")
        await self.close(chat_id, user)
        return said

    async def release(self, chat_id, user) -> bool:
        """The person's last socket to a chat went away: the node is
        told nobody watches, and lets the chat go once it is idle —
        scheduled or not: the Data Core's clock places it again when a
        row comes due. Returns whether there was a node to tell."""
        return bool((await self._anywhere("release", chat_id, user)).get("ok"))

    async def _release_here(self, chat_id: str, user: Dict[str, Any]) -> bool:
        binding = self.chats.get(chat_id)
        if binding is not None and self._watching(chat_id, binding.user):
            # The person opened it again, on another replica, before
            # this one heard they left: still watched.
            return False
        if binding is not None:
            binding.watched = False
            if binding.attached:
                return await binding.link.send(
                    {"type": "watch", "chat_id": binding.chat_id, "watched": False})
        current = LeaseStore().current(chat_id)
        if LeaseStore.live(current):
            remote = self._remote(current["node_id"])
            if remote is not None:
                raise Elsewhere(remote.replica_id)
        if binding is None and self.cluster is not None and self.cluster.others:
            # Held nowhere now: a replica that bound the chat for this
            # person before its node went hears they left.
            self.cluster.bus.post(self.cluster.bus.EVERYONE, "relay", {
                "op": "unwatch", "chat_id": chat_id, "user": user})
        return False

    # ------------------------------------------------------------------
    # What the links say
    # ------------------------------------------------------------------

    async def on_event(self, link, chat_id: str, frame: Dict[str, Any]) -> None:
        """A frame a node emitted for one chat: to the person, verbatim,
        on whichever replica their socket is — if this chat is attached
        to that node, and only then."""
        binding = self.chats.get(chat_id)
        if binding is None or binding.link is not link:
            return
        if frame.get("event") == "stopped":
            binding.killed.set()
        await get_ws_manager().deliver(chat_id, binding.user, {
            "endpoint": "AI:Chat:Event",
            "data": {**frame, "chat_id": chat_id},
        })

    async def on_beat(self, link, frame: Dict[str, Any]) -> None:
        """A node's heartbeat renews the leases it holds."""
        said = frame.get("chats")
        chats = list(said) if isinstance(said, (dict, list)) else []
        await asyncio.to_thread(LeaseStore().renew, link.key, chats)

    async def on_released(self, link, frame: Dict[str, Any]) -> None:
        """A node let a chat go: idle, with nobody watching."""
        chat_id = str(frame.get("chat_id") or "")
        lease = frame.get("lease")
        try:
            number = int(lease) if lease is not None else None
        except (TypeError, ValueError):
            number = None
        LeaseStore().release(chat_id, link.key, number)
        link.chats.discard(chat_id)
        binding = self.chats.get(chat_id)
        if binding is not None and binding.link is link:
            binding.link, binding.lease = None, None
            if not binding.watched:
                self.chats.pop(chat_id, None)

    async def on_lost(self, link) -> None:
        """A node's link ended: its chats are attached nowhere until it
        comes back or their leases lapse, and the people watching are
        told, so a spinner never waits on a node that has gone."""
        for binding in list(self.chats.values()):
            if binding.link is not link:
                continue
            binding.link = None
            if not binding.watched:
                continue
            try:
                await get_ws_manager().deliver(
                    binding.chat_id, binding.user, {
                        "endpoint": "AI:Chat:Event",
                        "data": {"event": "runtime_disconnected",
                                 "chat_id": binding.chat_id}})
            except Exception:
                pass

    async def on_link(self, link) -> None:
        """A node connected: the chats it holds a live lease on are
        attached to it again under the same numbers — wherever it was
        linked before, this replica or one that stopped — then the chats
        a person is waiting on, where they are placed; and the other
        replicas are told, for the chats waiting there. Nothing is
        attached for a clock: the Data Core's fires a chat when it is
        due (chat_session/clock.py)."""
        from api.services.chat_session.identity import StandingOwner
        from database.stores import ChatStore

        for lease in LeaseStore().of_node(link.key):
            chat_id = str(lease["_id"])
            binding = self.chats.get(chat_id)
            if binding is not None and binding.link is link and binding.attached:
                continue
            if binding is None:
                chat = ChatStore().by_chat_id(chat_id)
                user = StandingOwner().resolve(chat) if chat else None
                if user is None:
                    continue
                binding = await self._binding(chat_id, user)
                binding.watched = self._watching(chat_id, user)
            try:
                await self._attach(binding)
            except Elsewhere:
                self._stray(chat_id)
        for binding in list(self.chats.values()):
            if not binding.attached and binding.watched \
                    and self.placement.may_serve(
                        link, str(binding.user.get("org_id") or "")):
                await self._quietly_attach(binding)
        if self.cluster is not None and self.cluster.others:
            self.cluster.bus.post(self.cluster.bus.EVERYONE, "link_up",
                                  {"node": link.key})

    async def _quietly_attach(self, binding: ChatBinding) -> None:
        """A chat a person waits on here, attached where it is placed."""
        try:
            await self._attach(binding)
        except Elsewhere:
            if not self._held(binding.chat_id):
                await self._anywhere("attach", binding.chat_id, binding.user,
                                     watched=True)

    async def _on_link_up(self, body: Dict[str, Any], sender: str) -> Dict[str, Any]:
        """A node linked to another replica: the chats a person waits on
        here may be placed on it now."""
        for binding in list(self.chats.values()):
            if binding.attached or not binding.watched \
                    or self._held(binding.chat_id):
                continue
            await self._quietly_attach(binding)
        return {}

    async def on_replicas_gone(self, gone: set) -> None:
        """Replicas stopped beating, and the node links they held went
        with them: the people watching here a chat whose node is now
        linked nowhere are told, as when a link ends here. The node
        linking again gives its chats back (``on_link``)."""
        manager = get_ws_manager()
        for key in list(manager.frontends):
            org_id, _, rest = key.partition(":")
            user_id, _, chat_id = rest.partition(":")
            current = LeaseStore().current(chat_id)
            if not LeaseStore.live(current) or self._held(chat_id):
                continue
            user = {"org_id": org_id, "user_id": user_id}
            binding = self.chats.get(chat_id)
            if binding is None:
                binding = await self._binding(chat_id, user)
            binding.watched = True
            binding.waiting = self.MOVING
            await manager.send_frontend(chat_id, user, {
                "endpoint": "AI:Chat:Event",
                "data": {"event": "runtime_disconnected", "chat_id": chat_id}})

    async def fail_over(self) -> int:
        """Every lease whose node stopped renewing it and never let it
        go: the chat placed again, under a number past the last, so the
        node that had it can write nothing more for it. Returns how many
        moved."""
        from api.services.chat_session.identity import StandingOwner
        from database.stores import ChatStore

        moved = 0
        for lease in LeaseStore().lapsed():
            chat_id = str(lease["_id"])
            binding = self.chats.get(chat_id)
            if binding is None:
                chat = ChatStore().by_chat_id(chat_id)
                user = StandingOwner().resolve(chat) if chat else None
                if user is None:
                    # Nothing to move: the chat is gone, or its person
                    # may no longer act.
                    LeaseStore().release(chat_id)
                    continue
                watched = self._watching(chat_id, user)
                old = None
            else:
                user, watched = binding.user, binding.watched
                old, binding.link = binding.link, None
            answer = await self._anywhere("attach", chat_id, user, watched=watched)
            if answer.get("ok"):
                moved += 1
                self.logger.info(
                    f"Chat {chat_id} moved from {lease.get('node_id')} to "
                    f"{answer.get('node')}")
            elif old is not None and chat_id in self.chats:
                self.chats[chat_id].link = old
        return moved

    # ------------------------------------------------------------------
    # Renewal
    # ------------------------------------------------------------------

    def start_renewing(self):
        """The renewal and lease loops and the clock, for the life of
        the process. The app's lifespan starts them; tests call
        renew_due, fail_over and the clock's tick directly."""
        loop = asyncio.get_running_loop()
        if self.renewer is None or self.renewer.done():
            self.renewer = loop.create_task(self._renew_loop())
        if self.leaser is None or self.leaser.done():
            self.leaser = loop.create_task(self._lease_loop())
        self.clock.start()

    @property
    def leading(self) -> bool:
        """Whether this replica runs the loops one replica must."""
        return self.cluster is None or self.cluster.leading

    async def _renew_loop(self):
        while True:
            await asyncio.sleep(self.RENEW_TICK_SECONDS)
            try:
                await self.renew_due()
            except Exception as exc:  # one bad tick never ends the loop
                self.logger.warning(f"Credential renewal tick failed: {exc}")

    async def _lease_loop(self):
        while True:
            await asyncio.sleep(self.LEASE_TICK_SECONDS)
            if not self.leading:
                continue
            try:
                await self.fail_over()
            except Exception as exc:  # one bad tick never ends the loop
                self.logger.warning(f"Lease tick failed: {exc}")

    def due(self, binding: ChatBinding, after_seconds: float) -> bool:
        return (binding.attached and binding.credentialed_at is not None
                and time.monotonic() - binding.credentialed_at >= after_seconds)

    async def renew(self, binding: ChatBinding) -> bool:
        """A fresh delegation for the chat, bound to the node holding
        it, handed over the link. Minting the new row retires the old
        one at once; the frame is the very next thing on the wire."""
        link = binding.link
        if link is None or UserStore().stopped(
                str(binding.user.get("user_id") or "")):
            return False
        try:
            credential = Delegation().for_chat(
                binding.user, binding.chat_id, node=link.key)
            if not credential:
                raise RuntimeError("no delegation could be minted")
        except Exception as exc:
            self.logger.warning(
                f"Credential for chat {binding.chat_id} not renewed: {exc}")
            return False
        if not await link.send({"type": "chat", "chat_id": binding.chat_id,
                                "frame": {"event": "credential",
                                          "credential": credential}}):
            return False
        binding.credentialed_at = time.monotonic()
        return True

    async def renew_due(self) -> int:
        """Renew every attached chat whose credential is old enough.
        Returns how many were renewed."""
        renewed = 0
        for binding in list(self.chats.values()):
            if self.due(binding, self.RENEW_AFTER_SECONDS) \
                    and await self.renew(binding):
                renewed += 1
        return renewed

    async def close_all(self):
        """Shutdown: the loops end. The chats are left where they are;
        their leases lapse, or their nodes let them go."""
        for name in ("renewer", "leaser"):
            task = getattr(self, name)
            setattr(self, name, None)
            if task is not None:
                task.cancel()
        self.clock.stop()
        async with self.lock:
            self.chats.clear()
