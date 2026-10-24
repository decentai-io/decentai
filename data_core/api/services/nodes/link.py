"""The node link, from the Data Core's side
(docs/system/execution-nodes.md#the-node-link).

Execution nodes connect to the Data Core, never the other way round:
the Data Core holds no node's address, and a node can run behind a
firewall that lets nothing in. Each node keeps one connection,

    WS /nodes/link     Authorization: Bearer <the node's credential>
                       X-DecentAI-Node-Protocol: <the version it speaks>
                       X-DecentAI-Node-Nonce: <its own, for this connection>

and every chat it serves is carried over it, each frame naming its
chat. The handshake admits the node (admission.py) and refuses a
protocol other than this Data Core's, with the reason in the close.
Every frame the Data Core sends is a token signed with its own key
(signing.py), for this node, this connection and this place in the
sequence. A heartbeat checks the node's credential again: one that
lapsed, an authority revoked, a node refused, and the link is closed.

    NodeLink    one connected node: send, ask, its chats and load
    NodeLinks   every node connected to this replica, in memory, and
                the route's handler

**In a cluster** (docs/system/cluster.md) a node's link is on one
replica, and only that replica can sign its frames — the sequence is
the connection's. So each link is said in the presence table
(``cluster_links``) with this replica's id, refreshed by the replica's
heartbeat, and the node's load with each of the node's beats; another
replica places chats on it from there, and asks it questions through
this one (``ask``). A node linking again on another replica takes the
row, and this replica is told to close the old link (4408). Revoking a
node closes its link wherever it is (``close_everywhere``).

What a node says of itself (its load, its locks) is kept for the nodes
view and to place chats by load; it never admits or refuses a node.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect

from api.services.nodes.admission import Admission, NodeIdentity, Refused
from api.services.nodes.signing import DataCoreSignature
from contracts.node import (
    CLOSE_PROTOCOL, CLOSE_REFUSED, CLOSE_REPLACED, HEADER_NONCE,
    HEADER_PROTOCOL, NODE_PROTOCOL_VERSION)
from database.stores import NodeStore
from server.custom_logging import CustomLoggerFactory


def _close_reason(text: str) -> str:
    """A close frame's reason holds 123 bytes."""
    raw = str(text or "").encode("utf-8")[:120]
    return raw.decode("utf-8", "ignore")


class NodeLink:
    def __init__(self, websocket: WebSocket, identity: NodeIdentity,
                 nonce: str, token: str, reports: Dict[str, Any]):
        self.socket = websocket
        self.identity = identity
        self.key = identity.key
        self.nonce = nonce
        #: the credential the node last presented
        self.token = token
        self.reports = dict(reports or {})
        #: the chats this Data Core has attached here
        self.chats: set = set()
        self.connected_at = time.time()
        self.last_beat = time.time()
        self.open = True
        self._seq = 0
        self._sending = asyncio.Lock()
        self._asked: Dict[str, asyncio.Future] = {}

    @property
    def scope(self) -> str:
        return self.identity.scope

    @property
    def chat_count(self) -> int:
        return len(self.chats)

    @property
    def load(self) -> int:
        """How busy the node is, by what it says and what it was given:
        the larger of the two."""
        said = (self.reports.get("load") or {}) if isinstance(
            self.reports.get("load"), dict) else {}
        try:
            sessions = int(said.get("sessions") or 0)
        except (TypeError, ValueError):
            sessions = 0
        return max(sessions, len(self.chats))

    async def send(self, frame: Dict[str, Any]) -> bool:
        """One frame, signed, in sequence. False when the node is gone."""
        if not self.open:
            return False
        clean = json.loads(json.dumps(frame, default=str))
        try:
            async with self._sending:
                # Signing fails on this side — a Data Core without its
                # service key — and is said as that, not taken for the
                # node going away.
                token = DataCoreSignature().frame(
                    self.key, self.nonce, self._seq, clean)
                self._seq += 1
                await self.socket.send_text(token)
        except ValueError as exc:
            self.open = False
            CustomLoggerFactory.get_logger(type(self).__name__).error(
                f"A frame to node {self.key} could not be signed: {exc}")
            return False
        except Exception:
            # The socket is gone: the node left, or the network did.
            self.open = False
            return False
        return True

    async def ask(self, what: str, asked: Dict[str, Any],
                  timeout: float = 20.0) -> Dict[str, Any]:
        """A question for the node (monitoring), and its answer. Raises
        RuntimeError with the node's own words where it refused, and
        TimeoutError where it said nothing."""
        request_id = f"ask_{uuid.uuid4().hex[:16]}"
        future = asyncio.get_running_loop().create_future()
        self._asked[request_id] = future
        try:
            if not await self.send({"type": "monitor", "request_id": request_id,
                                    "what": what, "asked": asked}):
                raise RuntimeError("the node is not connected")
            return await asyncio.wait_for(future, timeout)
        finally:
            self._asked.pop(request_id, None)

    def answered(self, frame: Dict[str, Any]) -> None:
        future = self._asked.get(str(frame.get("request_id") or ""))
        if future is None or future.done():
            return
        if frame.get("error"):
            future.set_exception(RuntimeError(str(frame["error"])[:300]))
        else:
            result = frame.get("result")
            future.set_result(result if isinstance(result, dict) else {})

    async def close(self, code: int, reason: str) -> None:
        self.open = False
        try:
            await self.socket.close(code=code, reason=_close_reason(reason))
        except Exception:
            pass

    def fail_questions(self) -> None:
        for future in self._asked.values():
            if not future.done():
                future.set_exception(RuntimeError("the node's link ended"))


class NodeLinks:
    #: How long a node may take to say hello.
    HELLO_SECONDS = 10.0

    def __init__(self):
        from server.setup.app_state import get_state

        self.links: Dict[str, NodeLink] = {}
        #: this process as a replica; None where there is no cluster
        #: (a test's links of their own)
        self.cluster = get_state().cluster
        if self.cluster is not None:
            self.cluster.bus.on("ask", self._on_ask)
            self.cluster.bus.on("close_links", self._on_close_links)
        #: called with a link once it is welcomed, and once it is gone:
        #: the relay attaches and detaches chats (chat_session/relay.py)
        self.on_link: List[Callable[[NodeLink], Awaitable[None]]] = []
        self.on_lost: List[Callable[[NodeLink], Awaitable[None]]] = []
        #: what a node says for one of its chats: (link, chat_id, frame)
        self.on_event: Optional[Callable[..., Awaitable[None]]] = None
        #: what a node says about itself: (link, frame), for leases
        self.on_beat: List[Callable[..., Awaitable[None]]] = []
        self.on_frame: Dict[str, Callable[..., Awaitable[None]]] = {}
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    def connected(self) -> List[NodeLink]:
        return [link for link in self.links.values() if link.open]

    def everywhere(self) -> list:
        """Every node connected now, to this replica or to another
        (``RemoteLink``, api/services/cluster): what placement chooses
        among, the nodes view shows and monitoring asks."""
        found = self.connected()
        if self.cluster is not None:
            here = {link.key for link in found}
            found += [link for link in self.cluster.links() if link.key not in here]
        return found

    def get(self, key: str) -> Optional[NodeLink]:
        link = self.links.get(str(key or ""))
        return link if link is not None and link.open else None

    async def close_where(self, which: Callable[[NodeIdentity], bool],
                          reason: str) -> int:
        closing = [link for link in list(self.links.values())
                   if which(link.identity)]
        for link in closing:
            await link.close(CLOSE_REFUSED, reason)
        return len(closing)

    async def close_all(self) -> None:
        for link in list(self.links.values()):
            await link.close(1001, "the Data Core is stopping")

    async def close_everywhere(self, reason: str, authority_id: str = "",
                               node_key: str = "") -> int:
        """The links of the nodes an authority vouches for, or of one
        node, closed now on every replica that holds one. Returns how
        many were closed."""
        def which(identity) -> bool:
            if node_key:
                return identity.key == node_key
            return bool(authority_id) and identity.authority_id == authority_id

        closed = await self.close_where(which, reason)
        if self.cluster is None:
            return closed
        held = {link.replica_id for link in self.cluster.links()
                if which(link.identity)}
        for replica in held:
            try:
                answer = await self.cluster.bus.call(
                    replica, "close_links", {"reason": reason,
                                             "authority_id": authority_id,
                                             "node_key": node_key})
                closed += int(answer.get("closed") or 0)
            except Exception as exc:
                self.logger.warning(
                    f"Replica {replica} did not close its links: {exc}")
        return closed

    # ------------------------------------------------------------------
    # What the other replicas ask
    # ------------------------------------------------------------------

    async def _on_close_links(self, body: Dict[str, Any], sender: str):
        node_key = str(body.get("node_key") or "")
        authority_id = str(body.get("authority_id") or "")
        link_id = str(body.get("link_id") or "")
        if link_id:
            # The one link a node left for another replica: closed as a
            # replaced link is, and only while it is still that one.
            link = self.links.get(node_key)
            if link is None or link.nonce != link_id:
                return {"closed": 0}
            await link.close(CLOSE_REPLACED, "the same node connected again")
            return {"closed": 1}

        def which(identity) -> bool:
            if node_key:
                return identity.key == node_key
            return bool(authority_id) and identity.authority_id == authority_id

        return {"closed": await self.close_where(
            which, str(body.get("reason") or "refused"))}

    async def _on_ask(self, body: Dict[str, Any], sender: str):
        link = self.get(str(body.get("node") or ""))
        if link is None:
            return {"error": "the node is not connected"}
        try:
            result = await link.ask(str(body.get("what") or ""),
                                    body.get("asked") or {},
                                    float(body.get("timeout") or 20.0))
        except TimeoutError:
            return {"error": "the node did not answer"}
        except RuntimeError as exc:
            return {"error": str(exc)}
        return {"result": result}

    def _present(self, link: NodeLink) -> None:
        """This link, in the presence table; a replica the node left is
        told to close the link it still has there."""
        if self.cluster is None:
            return
        from database.stores.cluster import LinkPresenceStore

        identity = link.identity
        try:
            before = LinkPresenceStore().hold(
                link.key, self.cluster.replica_id, link.nonce, {
                    "name": identity.name, "authority_id": identity.authority_id,
                    "scope": identity.scope, "shared": bool(identity.shared),
                    "labels": list(identity.labels),
                    "connected_at": float(link.connected_at),
                    **self._load(link)}, self.cluster.EXPIRES_SECONDS)
        except Exception as exc:  # where a link is, is a view
            self.logger.warning(f"Node {link.key} not said present: {exc}")
            return
        if before and before.get("replica_id") not in (None, self.cluster.replica_id):
            self.cluster.bus.post(str(before["replica_id"]), "close_links", {
                "node_key": link.key, "link_id": str(before.get("link_id") or "")})

    @staticmethod
    def _load(link: NodeLink) -> Dict[str, Any]:
        """What the node said of its load, and how many chats this
        replica gave it: what another replica places by."""
        said = link.reports.get("load") if isinstance(
            link.reports.get("load"), dict) else {}
        loads = []
        for name in ("sessions", "chats"):
            try:
                loads.append(int(said.get(name) or 0))
            except (TypeError, ValueError):
                pass
        return {"load": max(loads or [0]), "chats": len(link.chats)}

    # ------------------------------------------------------------------
    async def serve(self, websocket: WebSocket) -> None:
        """One node's connection, from the handshake to its end."""
        await websocket.accept()
        version = str(websocket.headers.get(HEADER_PROTOCOL) or "").strip()
        if version != str(NODE_PROTOCOL_VERSION):
            await websocket.close(code=CLOSE_PROTOCOL, reason=_close_reason(
                f"this Data Core speaks node protocol {NODE_PROTOCOL_VERSION}; "
                f"the node said {version or 'none'}"))
            return
        scheme, _, token = str(
            websocket.headers.get("authorization") or "").partition(" ")
        token = token.strip() if scheme.lower() == "bearer" else ""
        try:
            identity = Admission().admit(token)
        except Refused as refused:
            self.logger.info(f"A node's link was refused: {refused}")
            await websocket.close(code=CLOSE_REFUSED,
                                  reason=_close_reason(str(refused)))
            return
        nonce = str(websocket.headers.get(HEADER_NONCE) or "").strip()
        if len(nonce) < 16:
            await websocket.close(code=CLOSE_REFUSED, reason="a nonce is required")
            return
        try:
            hello = await asyncio.wait_for(websocket.receive_json(),
                                           self.HELLO_SECONDS)
        except Exception:
            await websocket.close(code=CLOSE_REFUSED, reason="no hello")
            return
        if not isinstance(hello, dict) or hello.get("type") != "hello" \
                or hello.get("protocol_version") != NODE_PROTOCOL_VERSION:
            await websocket.close(code=CLOSE_PROTOCOL, reason=_close_reason(
                f"the hello is not node protocol {NODE_PROTOCOL_VERSION}'s"))
            return

        reports = hello.get("reports") if isinstance(hello.get("reports"), dict) else {}
        link = NodeLink(websocket, identity, nonce, token, reports)
        previous = self.links.get(link.key)
        self.links[link.key] = link
        if previous is not None:
            await previous.close(CLOSE_REPLACED, "the same node connected again")
        self._seen(link, connected=True)
        self._present(link)
        await link.send({"type": "welcome",
                         "protocol_version": NODE_PROTOCOL_VERSION,
                         "node": link.key, "scope": identity.scope})
        self.logger.info(f"Execution node {link.key} linked ({identity.scope})")
        for callback in list(self.on_link):
            await self._quietly(callback, link)
        try:
            while True:
                frame = await websocket.receive_json()
                if not isinstance(frame, dict):
                    continue
                if not await self._handle(link, frame):
                    return
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            link.open = False
            link.fail_questions()
            if self.links.get(link.key) is link:
                del self.links[link.key]
                try:
                    NodeStore().disconnected(link.key)
                except Exception:
                    pass
            if self.cluster is not None:
                try:
                    from database.stores.cluster import LinkPresenceStore

                    LinkPresenceStore().release(link.key, link.nonce)
                except Exception:
                    pass
            for callback in list(self.on_lost):
                await self._quietly(callback, link)
            self.logger.info(f"Execution node {link.key} unlinked")

    async def _quietly(self, callback, *args) -> None:
        try:
            await callback(*args)
        except Exception as exc:
            self.logger.warning(f"A node link's callback failed: {exc!r}")

    def _seen(self, link: NodeLink, connected: Optional[bool] = None) -> None:
        try:
            store = NodeStore()
            store.seen(link.identity, connected=connected)
            store.reported(link.key, link.reports)
        except Exception as exc:  # the view, never the gate
            self.logger.warning(f"Node {link.key} not recorded: {exc}")

    async def _handle(self, link: NodeLink, frame: Dict[str, Any]) -> bool:
        """One frame from a node. False ends the link."""
        kind = str(frame.get("type") or "")
        if kind == "event":
            if self.on_event is not None and isinstance(frame.get("frame"), dict):
                await self._quietly(self.on_event, link,
                                    str(frame.get("chat_id") or ""), frame["frame"])
        elif kind == "heartbeat":
            link.last_beat = time.time()
            if isinstance(frame.get("reports"), dict):
                link.reports = frame["reports"]
            # The credential is checked again: a lapse, a revoke or a
            # refusal by name ends the link here at the latest.
            try:
                Admission().admit(link.token, record=False)
            except Refused as refused:
                await link.close(CLOSE_REFUSED, str(refused))
                return False
            self._seen(link)
            if self.cluster is not None:
                try:
                    from database.stores.cluster import LinkPresenceStore

                    LinkPresenceStore().reported(link.key, link.nonce,
                                                 self._load(link))
                except Exception:
                    pass
            for callback in list(self.on_beat):
                await self._quietly(callback, link, frame)
        elif kind == "credential":
            token = str(frame.get("token") or "")
            try:
                fresh = Admission().admit(token)
            except Refused as refused:
                await link.close(CLOSE_REFUSED, str(refused))
                return False
            if fresh.key != link.key:
                await link.close(CLOSE_REFUSED,
                                 "a credential for another node")
                return False
            link.token = token
            link.identity = fresh
        elif kind == "monitor_result":
            link.answered(frame)
        elif kind in self.on_frame:
            await self._quietly(self.on_frame[kind], link, frame)
        return True
