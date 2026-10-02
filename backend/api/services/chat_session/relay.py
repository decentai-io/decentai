"""The backend's connection to the AI runtime — one per (user, chat).

The dial (docs/system/chat-session.md): the service token proves this is the
backend, and beside it travels a fresh delegation for the chat — the
runtime access token, the `runtime_sessions` row it names — which the
runtime hands to its services as the key to call back with. A key,
never authority: what the chat may do is what `AI:Chat:Contract`
answers when the runtime asks.

Over the socket the relay speaks the door's vocabulary and nothing
else: the frames in FRAMES go out; every frame the runtime emits —
`hello` first — comes back and reaches the person's frontend socket as
an `AI:Chat:Event`.

The manager at the bottom is what app_state holds, and the only thing
the routes and controllers reach for. At boot it re-dials every chat
that holds schedules, so the runtime's clock can load their rows —
reminders fire only while the backend keeps a delegation open, and
that is the backend's to keep.

A person who stopped everything of theirs (halt.py) is not dialed for:
no key is minted and no frame goes out until they resume. The one
exception is the stop itself, which dials to say it.

A delegation lives an hour; a dial may live for days. So the manager
also renews: every live dial whose credential is old enough is handed
a fresh one over the open socket, as a `credential` frame, well before
the old one lapses. Without that, a scheduled chat nobody has open
would fire for an hour and then fail quietly until the next boot.
"""

import asyncio
import json
import time
from urllib.parse import urlparse, urlunparse

import websockets

from api.services.chat_session.identity import Delegation, ServiceToken
from database.stores import UserStore
from server.custom_logging import CustomLoggerFactory
from server.setup.app_state import get_settings, get_ws_manager

#: what a frontend may say through the relay, and the frame it becomes
FRAMES = {"user_message", "approval_decided", "question_answered", "stop",
          "schedules_changed", "screen_input", "screen_open", "agents_changed"}


class RuntimeClient:
    def __init__(self, chat_id, user):
        self.chat_id = chat_id
        self.user = dict(user)
        self.socket = None
        self.reader = None
        #: one frame on the wire at a time
        self.lock = asyncio.Lock()
        #: one dial at a time: two callers dialing at once would mint
        #: two delegations, and the second would revoke the first's
        self.dialing = asyncio.Lock()
        #: when the runtime last received a credential for this chat
        #: (monotonic seconds) — what renewal is due against.
        self.credentialed_at = None
        #: set when the runtime says a kill of this chat is over
        self.killed = asyncio.Event()

        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    def url(self):
        parsed = urlparse(get_settings().ai_runtime_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        path = f"{parsed.path.rstrip('/')}/chats/{self.chat_id}"
        return urlunparse((scheme, parsed.netloc, path, "", "", ""))

    async def connect(self, halting=False):
        """``halting`` is the stop's own dial: the only one made for a
        person who is stopped."""
        async with self.dialing:
            if not halting and UserStore().stopped(
                    str(self.user.get("user_id") or "")):
                return False
            if self.socket is not None:
                return True

            # Any failure — missing signing key, runtime down, refused
            # handshake — means the same thing to the caller: no runtime
            # connection. The frontend socket must survive it.
            try:
                service_token = ServiceToken().issue()
                credential = Delegation().for_chat(self.user, self.chat_id)
                socket = await websockets.connect(
                    self.url(),
                    additional_headers={
                        "Authorization": f"Bearer {service_token}",
                        "X-DecentAI-Runtime-Access": credential,
                    },
                    open_timeout=5,
                )
            except Exception as e:
                self.logger.warning(
                    f"Runtime connect failed for chat {self.chat_id}: {e}"
                )
                return False

            self.socket = socket
            self.credentialed_at = time.monotonic()
            self.reader = asyncio.create_task(self._relay(socket))
            return True

    def due(self, after_seconds):
        """Whether this dial is live and its credential old enough to
        renew."""
        return (self.socket is not None
                and self.credentialed_at is not None
                and time.monotonic() - self.credentialed_at >= after_seconds)

    async def renew(self):
        """A fresh delegation for the chat, handed over the open socket.

        The credential the handshake carried lives an hour, and the
        socket may outlive it by days — a scheduled chat's dial is kept
        open on purpose. Minting the new row retires the old one at
        once, so this is the one moment a call the runtime has in
        flight can be refused; the frame is the very next thing on the
        wire. A socket that is gone is left alone: the next input or
        boot re-dials with a fresh credential of its own."""
        socket = self.socket
        if socket is None or UserStore().stopped(
                str(self.user.get("user_id") or "")):
            return False
        try:
            credential = Delegation().for_chat(self.user, self.chat_id)
            if not credential:
                raise RuntimeError("no delegation could be minted")
            async with self.lock:
                await socket.send(json.dumps(
                    {"event": "credential", "credential": credential}))
        except Exception as exc:
            self.logger.warning(
                f"Credential for chat {self.chat_id} not renewed: {exc}")
            return False
        self.credentialed_at = time.monotonic()
        return True

    async def _relay(self, socket):
        """Every frame the runtime sends — the hello, the narration,
        the cards — is the person's to see: through to their frontend
        socket as an event, verbatim. Reads the socket it was started
        for: when that one ends after a newer dial replaced it, the
        newer one is left alone and nobody is told of a disconnect."""
        try:
            async for raw in socket:
                try:
                    frame = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(frame, dict):
                    if frame.get("event") == "stopped":
                        self.killed.set()
                    await get_ws_manager().send_frontend(
                        self.chat_id, self.user, {
                            "endpoint": "AI:Chat:Event",
                            "data": {**frame, "chat_id": self.chat_id},
                        })
        except Exception as e:
            self.logger.warning(f"Runtime relay ended for chat {self.chat_id}: {e}")
        finally:
            if self.socket is socket:
                self.socket = None
                # The person is told, so a spinner never waits on a runtime
                # that has gone; the next input re-dials.
                try:
                    await get_ws_manager().send_frontend(
                        self.chat_id, self.user, {
                            "endpoint": "AI:Chat:Event",
                            "data": {"event": "runtime_disconnected",
                                     "chat_id": self.chat_id},
                        })
                except Exception:
                    pass

    async def send(self, frame, halting=False):
        """One frame of the door's vocabulary, verbatim."""
        if str(frame.get("event") or "") not in FRAMES:
            return False
        if not await self.connect(halting):
            return False
        socket = self.socket
        if socket is None:
            return False
        try:
            async with self.lock:
                await socket.send(json.dumps(frame, default=str))
        except Exception as exc:
            # Gone between the check and the send. Said, not raised: a
            # decision already recorded must not turn into a failure,
            # and the next frame re-dials.
            self.logger.warning(
                f"Frame for chat {self.chat_id} not sent: {exc!r}")
            if self.socket is socket:
                self.socket = None
            return False
        return True

    #: How long a close handshake may keep a caller waiting. The frame
    #: goes out at once; what can drag is the transport going away
    #: afterwards, and a route's teardown or a shutdown must not stall
    #: on it.
    CLOSE_TIMEOUT_SECONDS = 2.0

    async def close(self):
        socket, self.socket = self.socket, None
        if socket is not None:
            # asyncio.timeout, not wait_for: the close runs inline, so
            # its close frame is written before the first yield — a
            # wrapper task would send it a loop turn later, or never,
            # when the caller is a teardown the loop does not return to.
            try:
                async with asyncio.timeout(self.CLOSE_TIMEOUT_SECONDS):
                    await socket.close()
            except (TimeoutError, Exception) as exc:
                self.logger.warning(
                    f"Runtime connection for chat {self.chat_id} did not "
                    f"close cleanly: {exc!r}")
        reader, self.reader = self.reader, None
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()


class RuntimeClientManager:
    """Mirror of WSManager, for the runtime side of the relay."""

    #: When a live dial's credential is renewed: well inside the
    #: delegation's hour (Delegation.DEFAULT_TTL_SECONDS), with room for
    #: a slow tick and the access cache's minute.
    RENEW_AFTER_SECONDS = 45 * 60
    RENEW_TICK_SECONDS = 60

    def __init__(self):
        self.clients = {}
        self.lock = asyncio.Lock()
        #: the renewal loop, once the app's lifespan started it
        self.renewer = None
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def key(chat_id, user):
        return f"{user.get('org_id')}:{user.get('user_id')}:{chat_id}"

    async def ensure(self, chat_id, user):
        """The user connected to a chat: make sure its runtime connection
        exists, with fresh tokens. Returns False if the runtime is
        unreachable — the frontend socket stays up regardless — or
        the person has stopped everything of theirs."""
        if UserStore().stopped(str(user.get("user_id") or "")):
            return False
        key = self.key(chat_id, user)
        async with self.lock:
            client = self.clients.get(key)
            if client is None:
                client = RuntimeClient(chat_id, user)
                self.clients[key] = client
        return await client.connect()

    async def send(self, chat_id, user, frame):
        key = self.key(chat_id, user)
        client = self.clients.get(key)
        if client is None:
            # A frame can arrive while no frontend socket is up — a
            # gateway decision after a disconnect. Dial the runtime: the
            # session hydrates and hears it.
            if not await self.ensure(chat_id, user):
                return False
            client = self.clients.get(key)
            if client is None:
                return False
        return await client.send(frame)

    async def close(self, chat_id, user):
        async with self.lock:
            client = self.clients.pop(self.key(chat_id, user), None)
        if client is not None:
            await client.close()

    def chats_of(self, user):
        """The chats of this person a socket is held for."""
        prefix = self.key("", user)
        return {key[len(prefix):] for key in self.clients
                if key.startswith(prefix)}

    #: How long a killed chat is given to write down what it had done
    #: before its key is withdrawn.
    KILL_GRACE_SECONDS = 10.0

    async def kill(self, chat_id, user):
        """End everything one chat is doing, as part of the person
        stopping all of theirs: the kill is said — dialing for it if no
        socket is up, since a chat may be working with nobody watching
        — the runtime's word that it is over is waited for a short
        while, and the socket is closed. Returns whether it was said."""
        key = self.key(chat_id, user)
        async with self.lock:
            client = self.clients.get(key)
            if client is None:
                client = RuntimeClient(chat_id, user)
                self.clients[key] = client
        client.killed.clear()
        said = await client.send({"event": "stop", "force": True}, halting=True)
        if said:
            try:
                async with asyncio.timeout(self.KILL_GRACE_SECONDS):
                    await client.killed.wait()
            except TimeoutError:
                self.logger.warning(
                    f"Chat {chat_id} did not say its kill was over")
        await self.close(chat_id, user)
        return said

    async def release(self, chat_id, user):
        """The person's last socket to a chat went away. The dial goes
        with it — unless the chat holds schedule rows. Reminders fire
        only while the backend keeps a delegation open, and the boot
        redial above says whose job that is: the backend's, not the
        browser tab's. Opening and leaving a scheduled chat must not
        stop its clock. Returns whether the dial was closed."""
        from database.stores import ScheduleStore

        if ScheduleStore().rows(chat_id):
            return False
        await self.close(chat_id, user)
        return True

    async def redial_scheduled(self):
        """At boot: every chat holding schedule rows gets its delegation
        back, so the runtime's clock can adopt them. A chat whose owner
        may no longer act is skipped and logged — never dialed as
        somebody who cannot."""
        from api.services.chat_session.identity import StandingOwner
        from database.stores import ScheduleStore

        dialed = 0
        for holder in ScheduleStore().holders():
            # A person who stopped everything is not dialed (connect
            # refuses); their clocks wait for them to resume.
            user = StandingOwner().resolve(holder)
            if user is None:
                self.logger.warning(
                    f"Schedules of chat {holder.get('chat_id')} not "
                    f"redialed: their owner may no longer act")
                continue
            if await self.ensure(str(holder["chat_id"]), user):
                dialed += 1
        return dialed

    def start_renewing(self):
        """The renewal loop, for the life of the process: each tick,
        every live dial older than RENEW_AFTER_SECONDS is handed a fresh
        credential. The app's lifespan starts it; tests call renew_due
        directly and never wait on a tick."""
        if self.renewer is None or self.renewer.done():
            self.renewer = asyncio.get_running_loop().create_task(
                self._renew_loop())

    async def _renew_loop(self):
        while True:
            await asyncio.sleep(self.RENEW_TICK_SECONDS)
            try:
                await self.renew_due()
            except Exception as exc:  # one bad tick never ends the loop
                self.logger.warning(f"Credential renewal tick failed: {exc}")

    async def renew_due(self):
        """Renew every live dial whose credential is old enough.
        Returns how many were renewed."""
        renewed = 0
        for client in list(self.clients.values()):
            if client.due(self.RENEW_AFTER_SECONDS) and await client.renew():
                renewed += 1
        return renewed

    async def close_all(self):
        """Every connection this manager holds — for shutdown.

        Each client owns a socket and a reader task, so dropping the
        references without closing them would leave both behind. One
        client that refuses to close is reported and does not stop the
        rest: shutdown continues regardless.
        """
        renewer, self.renewer = self.renewer, None
        if renewer is not None:
            renewer.cancel()
        async with self.lock:
            clients = list(self.clients.values())
            self.clients.clear()

        for client in clients:
            try:
                await client.close()
            except Exception as error:
                self.logger.warning(
                    f"Runtime connection for chat {client.chat_id} did not "
                    f"close cleanly: {error}"
                )
