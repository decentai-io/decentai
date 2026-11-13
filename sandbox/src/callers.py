"""The sandbox's callers: each told the sandbox is there, called back, and the orders it gives
(documentation/sandbox.md#6-the-caller-protocol)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable

from communication.router import Answer, Router
from .files import FileRefused

if TYPE_CHECKING:
    from .sandbox import Sandbox

log = logging.getLogger("sandbox")


class Refused(Exception):
    """An order not carried out; ``reason`` is "full", "stopping" or "program" when that is why."""

    def __init__(self, why: str, reason: str = ""):
        super().__init__(why)
        self.reason = reason


class CallerGone(RuntimeError):
    """The caller stopped answering; what was for it is let go."""


class Caller:
    """One caller, by the address the sandbox's settings give: served while it answers."""

    EVERY, TELL_EVERY, RETRY_SECONDS = 0.5, 5.0, 0.5
    #: What a state moves at every reading: told every TELL_EVERY seconds, never taken as a change.
    MOVING = ("memory_used", "memory", "tasks", "cpu")

    def __init__(self, sandbox: "Sandbox", address: str):
        self.sandbox, self.address = sandbox, address
        #: From its answer to the state: its id, named in every call after, and its way out's port.
        self.id: str | None = None
        self.way_out: int | None = None
        self.served = False
        self._heard_at = float("-inf")

    @property
    def name(self) -> str:
        return self.id or self.address

    async def forever(self) -> None:
        """Told the state when it changes and every few seconds all the same; gone once it stops answering, and
        served again once it answers, under whatever id it has then."""
        told, told_at = None, 0.0
        while True:
            state, now = self.sandbox.report(self), time.monotonic()
            if self._steady(state) != self._steady(told) or now - told_at >= self.TELL_EVERY:
                told, told_at = state, now
                await self._tell(state)
            if self.served and time.monotonic() - self._heard_at > self.sandbox.settings.caller_gone_seconds:
                self.served, self.id, self.way_out = False, None, None
                log.warning(f"{self.address} stopped answering: its processes end.")
                await self.sandbox.lost(self)
            await asyncio.sleep(self.EVERY)

    async def call(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        """A call to it, checking it is the caller it was; RuntimeError unless answered 200."""
        answer, status = await self.sandbox.node.call(self.address, endpoint, data, id=self.id)
        if status != 200:
            raise RuntimeError(f"{endpoint} was answered {status}: {answer}")
        return answer

    async def message(self, process: str, seq: int, sender: str, message: dict[str, Any]) -> None:
        await self._until_taken("Caller:Process:message",
                                {"process": process, "seq": seq, "from": sender, "message": message})

    async def ended(self, process: str, code: int | None, reason: str) -> None:
        try:
            await self._until_taken("Caller:Process:ended", {"process": process, "code": code, "reason": reason})
        except CallerGone:
            pass

    async def pieces(self, file: Any) -> AsyncIterator[bytes]:
        """A program's or a file's bytes, pulled from it piece by piece."""
        offset, size = 0, None
        while size is None or offset < size:
            answer = await self.call("Caller:File:piece", {"file": file, "offset": offset})
            piece, size = bytes(answer["data"]), int(answer["size"])
            if not piece and offset < size:
                raise RuntimeError(f"{self.name} sent no bytes at {offset} of {size}")
            offset += len(piece)
            yield piece

    async def tunnel(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """A TLS connection to its way out, which must answer with its own certificate."""
        if not self.served or self.way_out is None:
            raise ConnectionError(f"{self.address} names no way out")
        return await self.sandbox.node.connect(f"{self.address.rpartition(':')[0]}:{self.way_out}", id=self.id)

    async def _until_taken(self, endpoint: str, data: dict[str, Any]) -> None:
        """Sent until taken, while it is served; the number in it lets it drop one it took already."""
        while True:
            try:
                await self.call(endpoint, data)
                return
            except Exception as error:
                if not self.served:
                    raise CallerGone(f"{self.address} is gone") from error
                await asyncio.sleep(self.RETRY_SECONDS)

    async def _tell(self, state: dict[str, Any]) -> None:
        try:
            # a caller is a runtime: until its id is known, TLS checks the kind, and then that id
            reached = {"id": self.id} if self.id else {"kind": "runtime"}
            answer = await self.sandbox.node.call(self.address, "Caller:Sandbox:state", state, **reached)
        except Exception as error:
            log.debug(f"{self.address} did not take the state: {error}")
            return
        answered, status = answer
        if status != 200 or not isinstance(answered, dict) or not isinstance(answered.get("id"), str) \
                or not isinstance(answered.get("way_out"), int):
            log.debug(f"{self.address} answered the state {status} without its id and way out")
            return
        self.id, self.way_out, self._heard_at = answered["id"], answered["way_out"], time.monotonic()
        if not self.served:
            self.served = True
            log.info(f"{self.id} is served, at {self.address}.")

    @classmethod
    def _steady(cls, state: dict[str, Any] | None) -> Any:
        """A state without what moves at every reading."""
        if state is None:
            return None
        return {**state, "room": {key: value for key, value in state["room"].items() if key not in cls.MOVING},
                "processes": [{key: value for key, value in found.items() if key not in cls.MOVING}
                              for found in state["processes"]]}


class Orders:
    """What callers order; each order is the calling caller's, found by its certificate."""

    def __init__(self, sandbox: "Sandbox"):
        self.sandbox = sandbox

    async def _carry_out(self, peer: dict[str, Any], work: Callable[[Caller], Awaitable[dict]]) -> Answer:
        caller = next((caller for caller in self.sandbox.callers if caller.served and caller.id == peer["id"]), None)
        if caller is None:
            return Router.failed("Sandbox Error", f"{peer['id']} is not served here.", 403)
        try:
            return await work(caller), 200
        except Refused as refused:
            answer, status = Router.failed("Sandbox Error", str(refused), 409 if refused.reason else 400)
            return ({**answer, "reason": refused.reason} if refused.reason else answer), status
        except (ValueError, FileRefused, OSError) as refused:
            return Router.failed("Sandbox Error", str(refused) or type(refused).__name__)
        except Exception as error:
            log.exception(f"an order of {peer['id']} failed")
            return Router.failed("Sandbox Error", f"the sandbox failed: {error}", 500)


class ProcessOrders(Orders):
    async def start(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        async def work(caller: Caller) -> dict:
            await self.sandbox.start(caller, data)
            return {"started": True}
        return await self._carry_out(peer, work)

    async def send(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        async def work(caller: Caller) -> dict:
            await self.sandbox.named(caller, data.get("process")).take(data.get("seq"), data.get("message"))
            return {"taken": True}
        return await self._carry_out(peer, work)

    async def end(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        async def work(caller: Caller) -> dict:
            self.sandbox.run(self.sandbox.named(caller, data.get("process")).end())
            return {"ending": True}
        return await self._carry_out(peer, work)


class FileOrders(Orders):
    async def piece(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        async def work(caller: Caller) -> dict:
            return self.sandbox.outgoing.piece(caller, data.get("file"), data.get("offset"))
        return await self._carry_out(peer, work)
