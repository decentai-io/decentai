"""People's chat sockets, on this replica and as the cluster knows them
(docs/system/cluster.md#presence).

A person has one socket per chat. It is held here, in memory, and
said in the presence table (``cluster_sockets``) with this replica's
id, so that whichever replica holds the chat's node link can reach it:
``deliver`` sends a frame to the socket here when it is here, and over
the bus to the replica that holds it otherwise. A newer socket replaces
the older wherever the older is — here, or on another replica, which is
told to close it (4001).
"""

import asyncio
import time
import uuid


class WSManager:
    def __init__(self):
        self.frontends = {}
        #: each socket's id here, as presence names it
        self.socket_ids = {}
        self.lock = asyncio.Lock()
        #: where another replica's socket for a key was last seen:
        #: key -> (replica id or "", when looked)
        self._elsewhere = {}
        self._handling = False
        self._listen(self._cluster())

    @staticmethod
    def key(chat_id, user):
        return f"{user.get('org_id')}:{user.get('user_id')}:{chat_id}"

    @staticmethod
    def _cluster():
        from server.setup.app_state import get_state

        return get_state().cluster

    def _listen(self, cluster) -> None:
        """What the other replicas ask of this one's sockets, answered
        once the cluster is there to ask."""
        if self._handling or cluster is None:
            return
        self._handling = True
        cluster.bus.on("frontend", self._on_frontend)
        cluster.bus.on("replace_socket", self._on_replace)

    async def connect(self, websocket, chat_id, user):
        key = self.key(chat_id, user)
        socket_id = uuid.uuid4().hex
        async with self.lock:
            previous = self.frontends.get(key)
            self.frontends[key] = websocket
            self.socket_ids[key] = socket_id
        if previous is not None and previous is not websocket:
            await previous.close(code=4001, reason="Replaced by a newer connection")
        cluster = self._cluster()
        if cluster is None:
            return
        self._listen(cluster)
        self._elsewhere.pop(key, None)
        try:
            from database.stores.cluster import SocketPresenceStore

            before = SocketPresenceStore().hold(
                key, chat_id, cluster.replica_id, socket_id, cluster.EXPIRES_SECONDS)
        except Exception:
            return  # where the socket is, is a view: the socket still serves
        if before and before.get("replica_id") not in (None, cluster.replica_id):
            cluster.bus.post(str(before["replica_id"]), "replace_socket",
                             {"key": key, "socket_id": before.get("socket_id")})

    async def disconnect(self, websocket, chat_id, user):
        """Remove this socket only if it is still the registered one, and
        say so — a socket that was replaced (4001) no longer owns the
        chat's connections and must not tear down its successor's."""
        key = self.key(chat_id, user)
        async with self.lock:
            if self.frontends.get(key) is websocket:
                self.frontends.pop(key, None)
                socket_id = self.socket_ids.pop(key, "")
            else:
                return False
        if self._cluster() is not None:
            try:
                from database.stores.cluster import SocketPresenceStore

                SocketPresenceStore().release(key, socket_id)
            except Exception:
                pass
        return True

    async def send_frontend(self, chat_id, user, message):
        websocket = self.frontends.get(self.key(chat_id, user))
        if websocket is None:
            return False
        try:
            await websocket.send_json(message)
            return True
        except Exception:
            await self.disconnect(websocket, chat_id, user)
            return False

    # ------------------------------------------------------------------
    # The cluster's sockets
    # ------------------------------------------------------------------

    async def deliver(self, chat_id, user, message):
        """A frame for the person's socket wherever it is: here, or on
        the replica that holds it, in the chat's order. False when they
        have none open."""
        if await self.send_frontend(chat_id, user, message):
            return True
        replica = self.elsewhere(chat_id, user)
        if not replica:
            return False
        cluster = self._cluster()
        return cluster.bus.post(replica, "frontend", {
            "chat_id": str(chat_id),
            "user": {"org_id": user.get("org_id"), "user_id": user.get("user_id")},
            "message": message}, lane=str(chat_id))

    def watching(self, chat_id, user) -> bool:
        """Whether the person has the chat open, here or anywhere."""
        if self.frontends.get(self.key(chat_id, user)) is not None:
            return True
        return bool(self.elsewhere(chat_id, user))

    def elsewhere(self, chat_id, user) -> str:
        """The other replica holding the person's socket, or ""; looked
        up at most once a second for a key."""
        cluster = self._cluster()
        if cluster is None or not (cluster.others or cluster.bus.running):
            return ""
        key = self.key(chat_id, user)
        seen = self._elsewhere.get(key)
        now = time.monotonic()
        if seen is not None and now - seen[1] < cluster.SOCKET_CACHE_SECONDS:
            return seen[0]
        try:
            from database.stores.cluster import SocketPresenceStore

            row = SocketPresenceStore().holder(key)
        except Exception:
            row = None
        replica = str((row or {}).get("replica_id") or "")
        if replica == cluster.replica_id:
            replica = ""
        self._elsewhere[key] = (replica, now)
        return replica

    def seen_on(self, chat_id, user, replica: str) -> None:
        """Another replica says it holds the person's socket now — it
        asked for the chat on their behalf — so frames go there without
        waiting for the next look."""
        cluster = self._cluster()
        if cluster is not None and replica and replica != cluster.replica_id:
            self._elsewhere[self.key(chat_id, user)] = (replica, time.monotonic())

    async def _on_frontend(self, body, sender):
        user = body.get("user") if isinstance(body.get("user"), dict) else {}
        await self.send_frontend(str(body.get("chat_id") or ""), user,
                                 body.get("message") or {})
        return {}

    async def _on_replace(self, body, sender):
        """A newer socket for the key opened on another replica."""
        key = str(body.get("key") or "")
        websocket = self.frontends.get(key)
        if websocket is not None and self.socket_ids.get(key) == body.get("socket_id"):
            await websocket.close(code=4001, reason="Replaced by a newer connection")
        return {}
