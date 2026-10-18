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
connected that may serve the chat's organization, its own first, then
the last holder, then the least loaded.

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
from database.stores import LeaseStore, UserStore
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

    WAITING_FOR_A_NODE = "No execution node is connected that may serve this chat."
    MOVING = ("The execution node serving this chat stopped answering; the "
              "chat moves to another within a minute.")

    def __init__(self, links):
        self.links = links
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

    # ------------------------------------------------------------------
    async def _binding(self, chat_id: str, user: Dict[str, Any]) -> ChatBinding:
        async with self.lock:
            binding = self.chats.get(chat_id)
            if binding is None:
                binding = ChatBinding(chat_id, user)
                self.chats[chat_id] = binding
            return binding

    @staticmethod
    def _standing(chat_id: str) -> bool:
        """A chat the node keeps while it holds schedule rows: the
        node's clock fires them only while it holds the chat."""
        from database.stores import ScheduleStore

        return bool(ScheduleStore().rows(chat_id))

    async def _attach(self, binding: ChatBinding, halting: bool = False,
                      again: bool = False) -> bool:
        """The chat on a node, under a lease: the node already holding
        it, or one placed now. ``again`` sends the attachment to the
        node already holding it too — a person arriving, who is greeted
        with the chat's present tense."""
        async with binding.attaching:
            if not halting and UserStore().stopped(
                    str(binding.user.get("user_id") or "")):
                return False
            if binding.attached and not again:
                return True
            leases = LeaseStore()
            current = leases.current(binding.chat_id)
            org_id = str(binding.user.get("org_id") or "")
            if leases.live(current):
                link = self.links.get(current["node_id"])
                if link is None:
                    # Its node is gone and its lease not yet: the chat
                    # moves when the lease lapses, never sooner — the
                    # node may only be slow, and two must never act.
                    binding.waiting = self.MOVING
                    return False
            else:
                link = self.placement.choose(
                    self.links.connected(), org_id,
                    str((current or {}).get("node_id") or ""))
                if link is None:
                    binding.waiting = self.WAITING_FOR_A_NODE
                    return False
            lease = leases.grant(binding.chat_id, link.key, binding.user)
            if lease is None:
                binding.waiting = self.MOVING
                return False
            try:
                credential = Delegation().for_chat(
                    binding.user, binding.chat_id, node=link.key)
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
                    "watched": binding.watched,
                    "standing": self._standing(binding.chat_id)}):
                link.chats.discard(binding.chat_id)
                binding.link = None
                return False
            if previous is not None and previous is not link:
                previous.chats.discard(binding.chat_id)
                await previous.send({"type": "detach", "chat_id": binding.chat_id})
            binding.waiting = ""
            binding.credentialed_at = time.monotonic()
            return True

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
        binding = await self._binding(str(chat_id), user)
        binding.watched = True
        return await self._attach(binding, again=True)

    async def send(self, chat_id, user, frame, halting: bool = False) -> bool:
        """One frame of the door's vocabulary, to the node holding the
        chat — placed first if none does. Said, not raised, when it does
        not go: a decision already recorded must not turn into a
        failure."""
        if str(frame.get("event") or "") not in FRAMES:
            return False
        binding = await self._binding(str(chat_id), user)
        if not await self._attach(binding, halting):
            return False
        link = binding.link
        if link is None or not await link.send(
                {"type": "chat", "chat_id": binding.chat_id, "frame": frame}):
            self.logger.warning(f"Frame for chat {chat_id} not sent")
            return False
        return True

    async def close(self, chat_id, user) -> None:
        """Nothing may keep this chat on a node: it is being erased, or
        its person may no longer act. Its node forgets it, and its lease
        is let go."""
        async with self.lock:
            binding = self.chats.pop(str(chat_id), None)
        link = binding.link if binding is not None else None
        if link is None:
            current = LeaseStore().current(str(chat_id))
            if LeaseStore.live(current):
                link = self.links.get(current["node_id"])
        if link is not None:
            link.chats.discard(str(chat_id))
            await link.send({"type": "detach", "chat_id": str(chat_id)})
        LeaseStore().release(str(chat_id))

    def chats_of(self, user):
        """The chats of this person a node holds now."""
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
        binding = await self._binding(str(chat_id), user)
        binding.killed.clear()
        said = await self.send(chat_id, user, {"event": "stop", "force": True},
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
        told nobody watches, and lets the chat go once it is idle.
        Returns whether there was a node to tell."""
        binding = self.chats.get(str(chat_id))
        if binding is None:
            return False
        binding.watched = False
        if not binding.attached:
            return False
        return await binding.link.send(
            {"type": "watch", "chat_id": binding.chat_id, "watched": False})

    # ------------------------------------------------------------------
    # What the links say
    # ------------------------------------------------------------------

    async def on_event(self, link, chat_id: str, frame: Dict[str, Any]) -> None:
        """A frame a node emitted for one chat: to the person, verbatim
        — if this chat is attached to that node, and only then."""
        binding = self.chats.get(chat_id)
        if binding is None or binding.link is not link:
            return
        if frame.get("event") == "stopped":
            binding.killed.set()
        await get_ws_manager().send_frontend(chat_id, binding.user, {
            "endpoint": "AI:Chat:Event",
            "data": {**frame, "chat_id": chat_id},
        })

    async def on_beat(self, link, frame: Dict[str, Any]) -> None:
        """A node's heartbeat renews the leases it holds."""
        said = frame.get("chats")
        chats = list(said) if isinstance(said, (dict, list)) else []
        LeaseStore().renew(link.key, chats)

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
                await get_ws_manager().send_frontend(
                    binding.chat_id, binding.user, {
                        "endpoint": "AI:Chat:Event",
                        "data": {"event": "runtime_disconnected",
                                 "chat_id": binding.chat_id}})
            except Exception:
                pass

    async def on_link(self, link) -> None:
        """A node connected: the chats waiting for a node are attached —
        those still leased to this node, back to it under the same
        number, and the rest where they are placed."""
        for binding in list(self.chats.values()):
            if not binding.attached and self.placement.may_serve(
                    link, str(binding.user.get("org_id") or "")):
                await self._attach(binding)
        await self.redial_scheduled()

    async def redial_scheduled(self) -> int:
        """Every chat holding schedule rows is held by a node, so its
        clock can adopt them. A chat whose owner may no longer act is
        skipped and logged — never attached as somebody who cannot."""
        from api.services.chat_session.identity import StandingOwner
        from database.stores import ScheduleStore

        attached = 0
        for holder in ScheduleStore().holders():
            chat_id = str(holder.get("chat_id") or "")
            current = self.chats.get(chat_id)
            if current is not None and current.attached:
                continue
            user = StandingOwner().resolve(holder)
            if user is None:
                self.logger.warning(
                    f"Schedules of chat {chat_id} not attached: their owner "
                    f"may no longer act")
                continue
            binding = await self._binding(chat_id, user)
            if await self._attach(binding):
                attached += 1
        return attached

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
                binding = await self._binding(chat_id, user)
            old = binding.link
            binding.link = None
            if await self._attach(binding):
                moved += 1
                self.logger.info(
                    f"Chat {chat_id} moved from {lease.get('node_id')} to "
                    f"{binding.link.key} under lease {binding.lease}")
            elif old is not None:
                binding.link = old
        return moved

    # ------------------------------------------------------------------
    # Renewal
    # ------------------------------------------------------------------

    def start_renewing(self):
        """The renewal and lease loops, for the life of the process.
        The app's lifespan starts them; tests call renew_due and
        fail_over directly."""
        loop = asyncio.get_running_loop()
        if self.renewer is None or self.renewer.done():
            self.renewer = loop.create_task(self._renew_loop())
        if self.leaser is None or self.leaser.done():
            self.leaser = loop.create_task(self._lease_loop())

    async def _renew_loop(self):
        while True:
            await asyncio.sleep(self.RENEW_TICK_SECONDS)
            try:
                await self.renew_due()
                await self.redial_scheduled()
            except Exception as exc:  # one bad tick never ends the loop
                self.logger.warning(f"Credential renewal tick failed: {exc}")

    async def _lease_loop(self):
        while True:
            await asyncio.sleep(self.LEASE_TICK_SECONDS)
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
        async with self.lock:
            self.chats.clear()
