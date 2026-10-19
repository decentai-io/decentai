"""The node link: this execution node's one connection to the Data Core
(docs/system/execution-nodes.md#the-node-link).

The node dials the Data Core, never the other way round, so a node can
run anywhere — behind a customer's firewall included — and the Data
Core holds no node's address. One long-lived connection per node,
reconnecting with backoff, carries every chat this node serves:

    Data Core → node     (each a token the Data Core signed, for this
                          node and this connection, in sequence)
      welcome            the handshake's answer
      attach             serve this chat, under this lease, with this
                         credential
      watch              whether a person is watching the chat
      chat               one of the door's frames for one chat
                         (user_message, approval_decided, stop, …)
      fire               a schedule of a chat this node holds is due:
                         run it (the Data Core's clock decided)
      detach             the chat is no longer this node's: forget it
      monitor            a question about agents (door/monitor.py), or
                         ``what: drain``: stop placing agents on one of
                         this node's Execution Sandboxes, or let it take
                         work again (agents/sandboxes.py)

    node → Data Core
      hello              who is speaking, the protocol it speaks, and
                         what it says of itself (shown, never a gate)
      event              what one chat emits, for its audience
      heartbeat          still here: the chats it holds, by lease —
                         which renews them — and its load
      released           a chat went idle with nobody watching: its
                         lease is let go
      unfired            a fire this node was handed and never ran,
                         given back: the chat left while it waited
      credential         a fresh credential, before the last lapses
      monitor_result     the answer to one question

A frame the Data Core did not sign, for another node or connection, or
out of sequence, ends the connection: nothing it said is believed.

Each chat has a ``ChatChannel``: the host's audience for it (it is what
a socket was), and an inbox read in order, so one chat's frames keep
their order and no chat waits on another's.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import urlparse, urlunparse

from ai_runtime.door.node import SignatureRefused
from contracts.node import (
    CLOSE_PROTOCOL, HEADER_NONCE, HEADER_PROTOCOL, NODE_PROTOCOL_VERSION)


class ChatChannel:
    """One chat's audience on the link: the Data Core, which passes
    what it hears on to the person where they are there."""

    def __init__(self, link: "NodeLink", chat_id: str):
        self.link = link
        self.chat_id = chat_id
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.pump: Optional[asyncio.Task] = None
        #: the lease this node holds the chat under
        self.lease: Optional[int] = None
        #: whether a person is watching: an unwatched chat is let go
        #: once it is idle
        self.watched = True

    async def send_json(self, frame: Dict[str, Any]) -> None:
        await self.link.send({"type": "event", "chat_id": self.chat_id,
                              "frame": frame})

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """A channel is not closed by the host: the Data Core says when
        a chat is no longer this node's (``detach``)."""


class NodeLink:
    #: How long between two attempts to connect, growing to the last.
    BACKOFF_SECONDS = (1, 2, 4, 8, 15, 30)
    #: How often the node says it is still here.
    HEARTBEAT_SECONDS = 20.0
    #: How long a welcome may take to arrive.
    WELCOME_SECONDS = 10.0

    def __init__(self, url: str, host, node, key, monitor=None,
                 report: Optional[Callable[[], Dict[str, Any]]] = None):
        self.url = self.link_url(url)
        self.host = host
        #: this node's credential (door/node.py, NodeCredential)
        self.node = node
        #: the Data Core's public key (door/node.py, DataCoreKey)
        self.key = key
        self.monitor = monitor
        #: ``drain(sandbox_id, on)``: this node's Execution Sandboxes,
        #: drained or let take work again when an administrator asks
        #: through the Data Core (Nodes:Node:Drain).
        self.drainer: Optional[Callable[[str, bool], Awaitable[bool]]] = None
        self.report = report or (lambda: {})
        self.channels: Dict[str, ChatChannel] = {}
        #: who the Data Core says this node is, once welcomed
        self.node_key = ""
        self.connected = asyncio.Event()
        self._socket = None
        self._sending = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._sent_credential = ""
        #: ``unfired`` frames that found the link down, said once it is
        #: up again
        self._given_back: list = []
        self.logger = logging.getLogger(self.__class__.__name__)
        if host is not None:
            host.forgotten = self._forgotten
            host.unfired = self.unfired
            host.serves = self.serves
            services = getattr(host, "services", None)
            if services is not None and hasattr(services, "on_stale"):
                services.on_stale = self._stale

    @staticmethod
    def link_url(base: str) -> str:
        parsed = urlparse(str(base or ""))
        scheme = "wss" if parsed.scheme == "https" else "ws"
        path = f"{parsed.path.rstrip('/')}/nodes/link"
        return urlunparse((scheme, parsed.netloc, path, "", "", ""))

    # ------------------------------------------------------------------
    def start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.get_running_loop().create_task(self.run())
        return self._task

    async def stop(self) -> None:
        # A fire still waiting behind its row's last never began: given
        # back while the link can say so, for the Data Core to fire
        # where the chat goes next.
        give_back = getattr(getattr(self.host, "clock", None),
                            "give_back_waiting", None)
        if give_back is not None:
            try:
                await give_back()
            except Exception as exc:
                self.logger.warning(f"Waiting fires not given back: {exc}")
        if self._given_back:
            self.logger.warning(
                f"{len(self._given_back)} fire(s) given back while the link "
                f"was down are lost with it")
        self._stopping = True
        socket, self._socket = self._socket, None
        if socket is not None:
            try:
                await socket.close()
            except Exception:
                pass
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._lost()

    async def run(self) -> None:
        """Connect, serve until the connection ends, and connect again —
        for as long as the node runs."""
        attempt = 0
        while not self._stopping:
            try:
                welcomed = await self._connect()
                if welcomed:
                    attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.warning(f"The link to the Data Core failed: {exc}")
            finally:
                await self._lost()
            if self._stopping:
                return
            wait = self.BACKOFF_SECONDS[min(attempt, len(self.BACKOFF_SECONDS) - 1)]
            attempt += 1
            await asyncio.sleep(wait)

    async def _connect(self) -> bool:
        import websockets

        credential = await self.node.token()
        nonce = secrets.token_urlsafe(24)
        async with websockets.connect(
                self.url, open_timeout=10, max_size=None,
                additional_headers={
                    "Authorization": f"Bearer {credential}",
                    HEADER_PROTOCOL: str(NODE_PROTOCOL_VERSION),
                    HEADER_NONCE: nonce,
                }) as socket:
            self._socket = socket
            self._sent_credential = credential
            await self.send({"type": "hello",
                             "protocol_version": NODE_PROTOCOL_VERSION,
                             "reports": self._reports()})
            reader = _FrameReader(socket, self.key, self.node.name, nonce)
            try:
                welcome = await asyncio.wait_for(reader.next(),
                                                 self.WELCOME_SECONDS)
            except websockets.ConnectionClosed as closed:
                self._said_why(closed)
                return False
            if welcome.get("type") != "welcome":
                raise SignatureRefused("the first frame is not a welcome")
            self.node_key = str(welcome.get("node") or "")
            self.connected.set()
            self.logger.info(
                f"Linked to the Data Core as {self.node_key or self.node.name}")
            # Fires given back while the link was down, said now.
            given, self._given_back = self._given_back, []
            for frame in given:
                if not await self.send(frame):
                    self._given_back.append(frame)
            beat = asyncio.get_running_loop().create_task(self._heartbeat())
            try:
                while True:
                    try:
                        frame = await reader.next()
                    except websockets.ConnectionClosed as closed:
                        self._said_why(closed)
                        return True
                    await self._dispatch(frame)
            finally:
                beat.cancel()
                await asyncio.gather(beat, return_exceptions=True)

    def _said_why(self, closed) -> None:
        received = getattr(closed, "rcvd", None)
        code = getattr(received, "code", None)
        reason = getattr(received, "reason", "") or ""
        if code == CLOSE_PROTOCOL:
            self.logger.error(
                f"The Data Core speaks another node protocol: {reason}. This "
                f"node speaks {NODE_PROTOCOL_VERSION}; one of the two must be "
                f"upgraded.")
        elif code is not None:
            self.logger.warning(f"The Data Core closed the link ({code}): {reason}")

    async def _lost(self) -> None:
        """The connection is gone: every chat's audience goes with it.
        The sessions stay until they are idle; the Data Core attaches
        again what it still wants served here."""
        self.connected.clear()
        self._socket = None
        channels, self.channels = self.channels, {}
        for chat_id, channel in channels.items():
            if channel.pump is not None:
                channel.pump.cancel()
            try:
                await self.host.detach(channel, chat_id)
            except Exception:
                pass

    def _reports(self) -> Dict[str, Any]:
        try:
            said = dict(self.report() or {})
        except Exception:
            said = {}
        sessions = getattr(self.host, "sessions", {}) if self.host else {}
        return {**said, "protocol_version": NODE_PROTOCOL_VERSION,
                "load": {"sessions": len(sessions), "chats": len(self.channels)}}

    # ------------------------------------------------------------------
    async def send(self, frame: Dict[str, Any]) -> bool:
        socket = self._socket
        if socket is None:
            return False
        try:
            async with self._sending:
                await socket.send(json.dumps(frame, default=str))
        except Exception as exc:
            self.logger.warning(f"A frame to the Data Core was not sent: {exc!r}")
            return False
        return True

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.HEARTBEAT_SECONDS)
            await self.beat()

    async def beat(self) -> None:
        """Still here: the chats served and the load — and a fresh
        credential, where the last one sent is about to lapse."""
        try:
            credential = await self.node.token()
        except Exception as exc:
            self.logger.warning(f"No credential to renew the link with: {exc}")
            credential = ""
        if credential and credential != self._sent_credential:
            if await self.send({"type": "credential", "token": credential}):
                self._sent_credential = credential
        await self.send({"type": "heartbeat",
                         "chats": {chat_id: channel.lease
                                   for chat_id, channel in self.channels.items()},
                         "reports": self._reports(), "at": time.time()})

    # ------------------------------------------------------------------
    async def _dispatch(self, frame: Dict[str, Any]) -> None:
        kind = str(frame.get("type") or "")
        if kind == "monitor":
            asyncio.get_running_loop().create_task(self._answer(frame))
            return
        chat_id = str(frame.get("chat_id") or "")
        if kind == "watch" and chat_id in self.channels:
            channel = self.channels[chat_id]
            channel.watched = bool(frame.get("watched"))
            if not channel.watched:
                self.host.unwatched(chat_id)
            return
        if kind not in ("attach", "chat", "fire", "detach") or not chat_id:
            self.logger.warning(f"A frame of no known kind: {kind!r}")
            return
        channel = self.channels.get(chat_id)
        if channel is None:
            if kind == "detach":
                return
            if kind in ("chat", "fire"):
                # Only an attachment brings a chat here: a frame for one
                # never attached builds nothing, with no key to build on.
                await self.send({"type": "event", "chat_id": chat_id, "frame": {
                    "event": "error",
                    "detail": "This chat is not attached to this node."}})
                return
            channel = ChatChannel(self, chat_id)
            self.channels[chat_id] = channel
            channel.pump = asyncio.get_running_loop().create_task(
                self._pump(channel))
        channel.inbox.put_nowait((kind, frame))

    async def _pump(self, channel: ChatChannel) -> None:
        """One chat's frames, in the order they came."""
        chat_id = channel.chat_id
        while True:
            kind, frame = await channel.inbox.get()
            try:
                if kind == "attach":
                    channel.lease = frame.get("lease")
                    channel.watched = bool(frame.get("watched", True))
                    hold = getattr(self.host.services, "hold", None)
                    if hold is not None:
                        hold(chat_id, channel.lease)
                    await self.host.attach(
                        channel, chat_id, str(frame.get("credential") or ""))
                    if not channel.watched:
                        self.host.unwatched(chat_id)
                elif kind == "chat":
                    inner = frame.get("frame")
                    if isinstance(inner, dict):
                        await self.host.handle(chat_id, inner)
                elif kind == "fire":
                    # The Data Core's own voice, never a person's: the
                    # clock runs the row as it was found due.
                    await self.host.fire(chat_id, frame.get("schedule"))
                elif kind == "detach":
                    # Not this node's any more: forgotten where it
                    # stands, its key and its lease let go.
                    await self._drop(channel)
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One frame that could not be handled is that frame's
                # failure: said to the chat's audience, and the chat
                # goes on hearing the next one.
                self.logger.error(f"Frame for {chat_id} failed: {exc}",
                                  exc_info=True)
                await channel.send_json({
                    "event": "error",
                    "detail": (f"Session not opened: {exc}" if kind == "attach"
                               else f"That could not be handled: {exc}")})

    async def _drop(self, channel: ChatChannel) -> None:
        chat_id = channel.chat_id
        if self.channels.get(chat_id) is channel:
            del self.channels[chat_id]
        self.host.forget(chat_id)
        await self.host.detach(channel, chat_id)
        self._let_go(chat_id)

    def _let_go(self, chat_id: str) -> None:
        """The chat's key and lease, gone from this node's services."""
        services = getattr(self.host, "services", None)
        revoke = getattr(services, "revoke", None)
        if revoke is not None:
            revoke(chat_id)
        hold = getattr(services, "hold", None)
        if hold is not None:
            hold(chat_id, None)

    def _forgotten(self, chat_id: str) -> None:
        """The host forgot an idle chat nobody watches: its lease is let
        go, so the Data Core may place it anywhere when it is next
        needed."""
        channel = self.channels.get(chat_id)
        if channel is None or channel.watched:
            return
        del self.channels[chat_id]
        if channel.pump is not None:
            channel.pump.cancel()
        self._let_go(chat_id)
        asyncio.get_running_loop().create_task(self.send(
            {"type": "released", "chat_id": chat_id, "lease": channel.lease}))
        asyncio.get_running_loop().create_task(
            self.host.detach(channel, chat_id))

    def serves(self, chat_id: str) -> bool:
        """Whether this node may act for the chat now: linked, and the
        chat attached here. A fire waiting for one that is not is not
        run here (chat/clock.py, ``Scheduler._run_waiting``)."""
        return self.connected.is_set() and chat_id in self.channels

    #: How many given-back fires are kept for a link that is down.
    GIVEN_BACK_KEPT = 1000

    async def unfired(self, chat_id: str, row: Dict[str, Any]) -> bool:
        """A fire the Data Core handed over and this node never ran,
        given back (``unfired``): the Data Core fires it again where
        the chat is. Said when the link is up again if it is down now.
        False only when it can be said neither now nor later."""
        frame = {"type": "unfired", "chat_id": chat_id,
                 "schedule_id": str(row.get("schedule_id") or ""),
                 "fired_at": row.get("fired_at")}
        if await self.send(frame):
            return True
        if self._stopping or len(self._given_back) >= self.GIVEN_BACK_KEPT:
            return False
        self._given_back.append(frame)
        return True

    def _stale(self, chat_id: str) -> None:
        """The Data Core refused a call as this chat's lease being old:
        another node holds it. Let it go at once."""
        channel = self.channels.get(chat_id)
        self.logger.warning(f"Chat {chat_id} is held by another node now; "
                            f"let go here")
        if channel is not None:
            if channel.pump is not None and channel.pump is not asyncio.current_task():
                channel.pump.cancel()
            asyncio.get_running_loop().create_task(self._drop(channel))
        else:
            self.host.forget(chat_id)

    async def _answer(self, frame: Dict[str, Any]) -> None:
        request_id = str(frame.get("request_id") or "")
        what = str(frame.get("what") or "")
        asked = frame.get("asked") if isinstance(frame.get("asked"), dict) else {}
        try:
            if what == "drain":
                result = await self._drain(asked)
            else:
                if self.monitor is None:
                    raise ValueError("This node answers no monitoring.")
                result = await self.monitor.answer(what, asked)
            await self.send({"type": "monitor_result",
                             "request_id": request_id, "result": result})
        except Exception as exc:
            await self.send({"type": "monitor_result",
                             "request_id": request_id, "error": str(exc)})

    async def _drain(self, asked: Dict[str, Any]) -> Dict[str, Any]:
        """One of this node's Execution Sandboxes drained (no new worker
        placed there; those running finish), or let take work again
        with ``on: false``. The Data Core decided who may ask; this only
        does it, for a sandbox that is registered here."""
        if self.drainer is None:
            raise ValueError("This node drains no Execution Sandbox.")
        sandbox = str(asked.get("sandbox") or "").strip()
        if not sandbox:
            raise ValueError("Which Execution Sandbox? None was named.")
        on = asked.get("on", True) is not False
        return {"sandbox": sandbox, "draining": bool(await self.drainer(sandbox, on))}


class _FrameReader:
    """The Data Core's frames on one connection, each checked: its key,
    this node, this connection, the next in sequence."""

    def __init__(self, socket, key, node: str, nonce: str):
        self.socket = socket
        self.key = key
        self.node = node
        self.nonce = nonce
        self.seq = 0

    async def next(self) -> Dict[str, Any]:
        raw = await self.socket.recv()
        claims = self.key.open_frame(
            raw if isinstance(raw, str) else raw.decode("utf-8"),
            self.node, self.nonce)
        if int(claims.get("seq", -1)) != self.seq:
            raise SignatureRefused(
                f"a frame out of sequence ({claims.get('seq')}, not {self.seq})")
        self.seq += 1
        return dict(claims["frame"])
