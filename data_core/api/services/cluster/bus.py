"""The bus between the Data Core's replicas (docs/system/cluster.md#the-bus).

    bus.on("relay", handler)                       what this replica answers
    bus.post(replica_id, "frontend", body, lane)   one way
    answer = await bus.call(replica_id, "relay", body)

A message is a document in a capped collection (``cluster_bus``), and
each replica reads those meant for it through a change stream: the
replica set a cluster needs anyway carries it, and no broker is added
(decided, docs/decentralization-plan.md). Capped, the collection never
grows; the stream reads the oplog, not the collection.

**Nothing is written for this replica itself.** A message for here is
handled here, at once: the relay's fast path, when a person's socket
and the chat's node link share a replica, never touches the bus.

**Order.** Messages one replica posts in one lane — a chat's id — are
handled one at a time, in the order posted, by the replica they are
for: a chat's frames keep their order across the bus as they do on a
node's link. Messages in no lane are handled as they come.

**At most once.** A replica that was not reading when a message was
written — starting, or its stream broken past resuming — never sees it,
and an answer that does not come is a timeout the caller hears
(``BusError``). What must happen is never the bus's alone: a lease, a
claim, a row in the database decides, and the bus only carries what one
replica asks another to do now.

A body is carried as JSON text, so a frame arrives exactly as it was
posted, whatever its keys.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import timedelta
from typing import Any, Awaitable, Callable, Dict, Optional

from server.custom_logging import CustomLoggerFactory
from util import utc_now

Handler = Callable[[Dict[str, Any], str], Awaitable[Optional[Dict[str, Any]]]]


class BusError(RuntimeError):
    """A message that could not go, or an answer that did not come."""


class Bus:
    #: Every replica but the one posting.
    EVERYONE = "*"
    #: How long a call waits for its answer unless it says otherwise.
    CALL_SECONDS = 10.0
    #: A message older than this when it arrives is not acted on: whoever
    #: posted it has stopped waiting, and the world it spoke of moved.
    STALE_SECONDS = 30.0
    #: How long one read of the stream waits before looking whether the
    #: bus is stopping.
    READ_MILLISECONDS = 250

    def __init__(self, replica_id: str):
        self.replica_id = replica_id
        self.handlers: Dict[str, Handler] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        #: set once the stream is open: a replica reads what is posted
        #: from then on
        self.ready = threading.Event()
        self._answers: Dict[str, asyncio.Future] = {}
        self._lanes: Dict[str, asyncio.Queue] = {}
        self._tasks: set = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def on(self, kind: str, handler: Handler) -> None:
        self.handlers[kind] = handler

    # ------------------------------------------------------------------
    def start(self) -> bool:
        """Read the stream from now, on a thread of its own, handing each
        message to this loop. False — and the bus stays off — where the
        database is no replica set: that Data Core runs alone."""
        from server.setup.app_state import get_db

        if self.running:
            return True
        if not get_db().replica_set():
            self.logger.info(
                "MongoDB is not a replica set: the replicas' bus is off, and "
                "this Data Core runs alone.")
            return False
        self.loop = asyncio.get_running_loop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._read, name="cluster-bus",
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)
        self.ready.clear()
        for future in list(self._answers.values()):
            if not future.done():
                future.set_exception(BusError("the bus stopped"))

    def _read(self) -> None:
        from pymongo.errors import OperationFailure, PyMongoError

        from database.stores.cluster import BusStore

        pipeline = [{"$match": {
            "operationType": "insert",
            "fullDocument.to": {"$in": [self.replica_id, self.EVERYONE]},
            "fullDocument.from": {"$ne": self.replica_id},
        }}]
        token = None
        while not self._stop.is_set():
            try:
                with BusStore().col.watch(
                        pipeline, resume_after=token,
                        max_await_time_ms=self.READ_MILLISECONDS) as stream:
                    self.ready.set()
                    while not self._stop.is_set() and stream.alive:
                        change = stream.try_next()
                        if change is None:
                            continue
                        token = stream.resume_token
                        message = change.get("fullDocument")
                        loop = self.loop
                        if message and loop is not None and not loop.is_closed():
                            loop.call_soon_threadsafe(self._arrived, message)
            except OperationFailure as exc:
                # Resuming where the oplog no longer reaches: read from
                # now, and what was missed is missed.
                self.logger.warning(f"The bus's stream was lost ({exc}); "
                                    f"reading from now")
                token = None
                self._stop.wait(1.0)
            except PyMongoError as exc:
                if self._stop.is_set():
                    return
                self.logger.warning(f"The bus's stream broke ({exc}); resuming")
                self._stop.wait(1.0)
            except Exception as exc:  # the stream outlives any one message
                self.logger.error(f"The bus's reader failed: {exc!r}")
                self._stop.wait(1.0)

    # ------------------------------------------------------------------
    def post(self, to: str, kind: str, body: Dict[str, Any],
             lane: str = "") -> bool:
        """One message, one way. True once it is on its way — handled
        here at once when it is for this replica. False when the bus is
        off and the message is for another."""
        if to == self.replica_id:
            self._arrived({"to": to, "from": self.replica_id, "kind": kind,
                           "lane": lane, "body": body, "at": utc_now()},
                          here=True)
            return True
        if not self.running:
            return False
        self._publish({"to": to, "kind": kind, "lane": lane, "body": body})
        return True

    async def call(self, to: str, kind: str, body: Dict[str, Any],
                   timeout: Optional[float] = None, lane: str = "") -> Dict[str, Any]:
        """A message, and the answer the replica it is for gives. Raises
        BusError when none comes in time, or the bus is off."""
        timeout = self.CALL_SECONDS if timeout is None else timeout
        if to == self.replica_id:
            return await self._run(kind, body, self.replica_id)
        if not self.running:
            raise BusError("the bus is off: this Data Core runs alone")
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._answers[request_id] = future
        try:
            self._publish({"to": to, "kind": kind, "lane": lane, "body": body,
                           "request_id": request_id})
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise BusError(f"replica {to} did not answer {kind!r} in {timeout}s")
        finally:
            self._answers.pop(request_id, None)

    def _publish(self, message: Dict[str, Any]) -> None:
        from database.stores.cluster import BusStore

        BusStore().publish({
            **{key: value for key, value in message.items() if key != "body"},
            "from": self.replica_id,
            "body": json.dumps(message.get("body") or {}, default=str),
            "at": utc_now(),
        })

    # ------------------------------------------------------------------
    def _arrived(self, message: Dict[str, Any], here: bool = False) -> None:
        """On the loop: an answer to one of this replica's calls, or a
        message to handle — in its lane's order, or at once."""
        body = message.get("body")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                body = {}
        message = {**message, "body": body if isinstance(body, dict) else {}}
        if message.get("kind") == "answer":
            future = self._answers.get(str(message.get("request_id") or ""))
            if future is not None and not future.done():
                if "error" in message["body"]:
                    future.set_exception(BusError(str(message["body"]["error"])))
                else:
                    future.set_result(message["body"].get("result") or {})
            return
        at = message.get("at")
        if not here and at is not None and utc_now() - at > timedelta(
                seconds=self.STALE_SECONDS):
            self.logger.warning(f"A {message.get('kind')!r} message from "
                                f"{message.get('from')} came too late; dropped")
            return
        lane = str(message.get("lane") or "")
        if not lane:
            self._spawn(self._handle(message))
            return
        queue = self._lanes.get(lane)
        if queue is None:
            queue = self._lanes[lane] = asyncio.Queue()
            self._spawn(self._drain(lane, queue))
        queue.put_nowait(message)

    def _spawn(self, work) -> None:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _drain(self, lane: str, queue: asyncio.Queue) -> None:
        try:
            while not queue.empty():
                await self._handle(queue.get_nowait())
        finally:
            if self._lanes.get(lane) is queue:
                del self._lanes[lane]

    async def _handle(self, message: Dict[str, Any]) -> None:
        sender = str(message.get("from") or "")
        request_id = str(message.get("request_id") or "")
        try:
            result = await self._run(str(message.get("kind") or ""),
                                     message["body"], sender)
            answer = {"result": result or {}}
        except Exception as exc:
            if not request_id:
                self.logger.warning(
                    f"A {message.get('kind')!r} message from {sender} failed: {exc!r}")
            answer = {"error": str(exc) or exc.__class__.__name__}
        if request_id and sender != self.replica_id:
            try:
                self._publish({"to": sender, "kind": "answer",
                               "request_id": request_id, "body": answer})
            except Exception as exc:
                self.logger.warning(f"An answer to {sender} was not sent: {exc!r}")

    async def _run(self, kind: str, body: Dict[str, Any],
                   sender: str) -> Optional[Dict[str, Any]]:
        handler = self.handlers.get(kind)
        if handler is None:
            raise BusError(f"this replica answers no {kind!r}")
        return await handler(body, sender)
