"""The relay: every chat, carried to the execution node serving it over
that node's link (docs/system/chat-session.md,
docs/system/execution-nodes.md#the-node-link).

Nodes connect to the Data Core (api/services/nodes/link.py); the Data
Core dials nobody. A chat that needs a node is **attached** to one of
the nodes connected now: a fresh delegation is minted for the chat,
bound to that node — the Data Core accepts it only on a request that
node presents its own credential with — and sent over the link with
the chat's id. From then on the door's frames for the chat go out
over that link (``FRAMES``, each naming its chat), and every frame the
node emits for it comes back the same way and reaches the person's
socket as an ``AI:Chat:Event``. A node that speaks for a chat not
attached to it is not heard.

A delegation is a key, never authority: what the chat may do is what
``AI:Chat:Contract`` answers when the node asks.

Which node: one connected, admitted and unrevoked, that may serve the
chat's organization — a shared node, or one of the organization's own
— the least loaded. A node an organization owns never serves another
organization's chat.

A person who stopped everything of theirs (halt.py) is attached
nowhere: no key is minted and no frame goes out until they resume. The
one exception is the stop itself.

A delegation lives an hour; a chat may be served for days. So the
relay also renews: every attached chat whose credential is old enough
is handed a fresh one over the link, as a ``credential`` frame, well
before the old one lapses.

The manager is what app_state holds, and the only thing the routes and
controllers reach for.
"""

import asyncio
import time
from typing import Any, Dict, Optional

from api.services.chat_session.identity import Delegation
from database.stores import UserStore
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
    """One chat, as the relay holds it: whose it is, and the node
    attached to it, if any."""

    def __init__(self, chat_id: str, user: Dict[str, Any]):
        self.chat_id = chat_id
        self.user = dict(user)
        self.link = None
        #: when the node last received a credential for this chat
        #: (monotonic seconds) — what renewal is due against
        self.credentialed_at: Optional[float] = None
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
    #: How long a killed chat is given to write down what it had done
    #: before its key is withdrawn.
    KILL_GRACE_SECONDS = 10.0

    def __init__(self, links):
        self.links = links
        self.chats: Dict[str, ChatBinding] = {}
        self.lock = asyncio.Lock()
        #: the renewal loop, once the app's lifespan started it
        self.renewer = None
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)
        links.on_event = self.on_event
        links.on_link.append(self.on_link)
        links.on_lost.append(self.on_lost)

    # ------------------------------------------------------------------
    # Which node
    # ------------------------------------------------------------------

    @staticmethod
    def _may_serve(link, user: Dict[str, Any]) -> bool:
        """Whether this node may serve this person's chat at all: a
        shared node, or one of their organization's own."""
        return link.identity.shared or link.scope == str(user.get("org_id") or "")

    def _choose(self, binding: ChatBinding):
        candidates = [link for link in self.links.connected()
                      if self._may_serve(link, binding.user)]
        if not candidates:
            return None
        return min(candidates, key=lambda link: (link.load, link.connected_at))

    # ------------------------------------------------------------------
    async def _binding(self, chat_id: str, user: Dict[str, Any]) -> ChatBinding:
        async with self.lock:
            binding = self.chats.get(chat_id)
            if binding is None:
                binding = ChatBinding(chat_id, user)
                self.chats[chat_id] = binding
            return binding

    async def _attach(self, binding: ChatBinding, halting: bool = False,
                      again: bool = False) -> bool:
        """The chat on a node: the one already serving it, or one
        chosen now and handed a delegation bound to it. ``again`` sends
        the attachment to the node already serving it too — a person
        arriving, who is greeted with the chat's present tense."""
        async with binding.attaching:
            if not halting and UserStore().stopped(
                    str(binding.user.get("user_id") or "")):
                return False
            if binding.attached and not again:
                return True
            link = binding.link if binding.attached else self._choose(binding)
            if link is None:
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
            binding.link = link
            link.chats.add(binding.chat_id)
            if not await link.send({"type": "attach", "chat_id": binding.chat_id,
                                    "credential": credential}):
                link.chats.discard(binding.chat_id)
                binding.link = previous if previous is not link else None
                return False
            binding.credentialed_at = time.monotonic()
            return True

    async def ensure(self, chat_id, user) -> bool:
        """The person connected to a chat: make sure a node serves it,
        with fresh tokens. False when no node may serve it — the
        person's socket stays up regardless — or the person has stopped
        everything of theirs."""
        if UserStore().stopped(str(user.get("user_id") or "")):
            return False
        binding = await self._binding(str(chat_id), user)
        return await self._attach(binding, again=True)

    async def send(self, chat_id, user, frame, halting: bool = False) -> bool:
        """One frame of the door's vocabulary, to the node serving the
        chat — attached first if none is. Said, not raised, when it does
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
        its person may no longer act."""
        async with self.lock:
            binding = self.chats.pop(str(chat_id), None)
        if binding is not None:
            await self._detach(binding)

    async def _detach(self, binding: ChatBinding) -> None:
        link, binding.link = binding.link, None
        if link is not None:
            link.chats.discard(binding.chat_id)
            await link.send({"type": "detach", "chat_id": binding.chat_id})

    def chats_of(self, user):
        """The chats of this person a node is attached for."""
        user_id = str(user.get("user_id") or "")
        return {chat_id for chat_id, binding in self.chats.items()
                if str(binding.user.get("user_id") or "") == user_id
                and binding.attached}

    async def kill(self, chat_id, user) -> bool:
        """End everything one chat is doing, as part of the person
        stopping all of theirs: the kill is said — attaching for it if no
        node serves the chat, since a chat may be working with nobody
        watching — the node's word that it is over is waited for a short
        while, and the chat is detached. Returns whether it was said."""
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
        """The person's last socket to a chat went away. The chat leaves
        its node with them — unless it holds schedule rows: the node's
        clock fires those only while the chat is attached there, and
        opening and leaving a scheduled chat must not stop its clock.
        Returns whether the chat was detached."""
        from database.stores import ScheduleStore

        if ScheduleStore().rows(str(chat_id)):
            return False
        await self.close(chat_id, user)
        return True

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

    async def on_lost(self, link) -> None:
        """A node's link ended: its chats are attached nowhere until a
        node is there for them, and the people watching are told, so a
        spinner never waits on a node that has gone."""
        for binding in list(self.chats.values()):
            if binding.link is not link:
                continue
            binding.link = None
            try:
                await get_ws_manager().send_frontend(
                    binding.chat_id, binding.user, {
                        "endpoint": "AI:Chat:Event",
                        "data": {"event": "runtime_disconnected",
                                 "chat_id": binding.chat_id}})
            except Exception:
                pass

    async def on_link(self, link) -> None:
        """A node connected: the chats waiting for one are attached —
        those a person has open, and those that hold a clock."""
        for binding in list(self.chats.values()):
            if not binding.attached and self._may_serve(link, binding.user):
                await self._attach(binding)
        await self.redial_scheduled()

    async def redial_scheduled(self) -> int:
        """Every chat holding schedule rows is attached to a node, so
        its clock can adopt them. A chat whose owner may no longer act
        is skipped and logged — never attached as somebody who cannot."""
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
            if await self.ensure(chat_id, user):
                attached += 1
        return attached

    # ------------------------------------------------------------------
    # Renewal
    # ------------------------------------------------------------------

    def start_renewing(self):
        """The renewal loop, for the life of the process. The app's
        lifespan starts it; tests call renew_due directly."""
        if self.renewer is None or self.renewer.done():
            self.renewer = asyncio.get_running_loop().create_task(
                self._renew_loop())

    async def _renew_loop(self):
        while True:
            await asyncio.sleep(self.RENEW_TICK_SECONDS)
            try:
                await self.renew_due()
                await self.redial_scheduled()
            except Exception as exc:  # one bad tick never ends the loop
                self.logger.warning(f"Credential renewal tick failed: {exc}")

    def due(self, binding: ChatBinding, after_seconds: float) -> bool:
        return (binding.attached and binding.credentialed_at is not None
                and time.monotonic() - binding.credentialed_at >= after_seconds)

    async def renew(self, binding: ChatBinding) -> bool:
        """A fresh delegation for the chat, bound to the node serving
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
        """Shutdown: the renewal loop ends. The chats are left where
        they are; the links close with the process."""
        renewer, self.renewer = self.renewer, None
        if renewer is not None:
            renewer.cancel()
        async with self.lock:
            self.chats.clear()
