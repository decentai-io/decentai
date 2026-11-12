"""A proxy for connections out, knowing nothing of who uses it: an HTTP proxy and SOCKS5 on one connection, told
apart by its first byte, to reach any host over TCP, to send plain http requests on, and to relay UDP. Every request is
decided by the caller, and only public addresses are ever reached (runtime.md §7)."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import ipaddress
import logging
import socket
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from ..utils import lookup, public

log = logging.getLogger("runtime")


class Proxy:
    """Connections served for sources the caller names. For each request, first
    ``await decide(source, protocol, host, port)`` is asked, ``protocol`` being "tcp" or "udp": a reason refuses,
    None allows, and an asyncio future allows until it is done, when what it allowed is closed. A name refused is never
    looked up. One allowed is looked up once, every address it answers with must be public, and only those addresses
    are reached: never this machine, its network, or a cloud's metadata address.

    What each source reached is counted by host and port, for the caller to take (``taken``): never a byte's content."""

    #: The longest HTTP request head; how long one step may take; a connection quiet both ways IDLE seconds is closed.
    HEAD_MOST, SECONDS, IDLE = 16 * 1024, 20, 300
    #: The hosts and ports counted for one source until it is taken; past them, a new one is not counted.
    COUNTED_MOST = 500
    #: Headers of a plain http request that are the proxy's, not the host's.
    HOPS = (b"host", b"connection", b"keep-alive", b"proxy-connection", b"proxy-authorization")
    #: The runtime's one rule for what is a public address, and its lookup (utils.py); a test may stand in for them.
    public, lookup = staticmethod(public), staticmethod(lookup)

    def __init__(self, decide: Callable[[Any, str, str, int], Awaitable[Any]], most: int = 128):
        """``most``: the connections one source may have open at once, UDP sessions among them."""
        self.decide, self.most = decide, most
        #: Connections open now, by source.
        self.open: collections.Counter = collections.Counter()
        #: What each source reached since it was last taken, by (host, port).
        self.counted: dict[Any, dict[tuple[str, int], dict[str, Any]]] = {}

    def taken(self, source: Any) -> list[dict[str, Any]]:
        """What a source reached since this was last asked, then forgotten here: for each host and port, the
        connections allowed and refused, the bytes out and in, and when first and last, in Unix seconds."""
        return [{"host": host, "port": port, **row} for (host, port), row in self.counted.pop(source, {}).items()]

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, source: Any) -> None:
        """One connection of a source: SOCKS5 when its first byte is 5, else an HTTP proxy's request. Served until
        either side ends, then closed."""
        self.open[source] += 1
        try:
            first = await asyncio.wait_for(reader.readexactly(1), self.SECONDS)
            if first == b"\x05":
                await self._socks(reader, writer, source)
            else:
                await self._http(first, reader, writer, source)
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
            log.debug(f"{source}: a connection ended: {error!r}")
        finally:
            self.open[source] -= 1
            if not self.open[source]:
                del self.open[source]
            writer.close()

    # -- an HTTP proxy's requests ---------------------------------------------------------------------

    async def _http(self, first: bytes, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                    source: Any) -> None:
        """CONNECT host:port, a tunnel; or a plain http request, sent on."""
        try:
            head = first + await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.SECONDS)
        except asyncio.LimitOverrunError:
            head = b""
        if not head or len(head) > self.HEAD_MOST:
            return await self._http_answer(writer, 400, "The request's head is too large.")
        method, target, version = (head.split(b"\r\n", 1)[0].decode("latin-1").split(" ") + ["", "", ""])[:3]
        host, port, path = self.aimed(method, target)
        if not host:
            return await self._http_answer(writer, 400, "Ask for a tunnel (CONNECT host:port), or a plain http:// "
                                                        "address.")
        try:
            up_reader, up_writer, until = await self._connect(source, host, port)
        except PermissionError as refused:
            return await self._http_answer(writer, 403, str(refused))
        except ConnectionError as failed:
            return await self._http_answer(writer, 502, str(failed))
        if method == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        else:
            up_writer.write(self.forwarded(head, method, path, version, host, port))
        await self._carry(reader, writer, up_reader, up_writer, until, (source, host, port))

    @staticmethod
    def aimed(method: str, target: str) -> tuple[str, int, str]:
        """The host and port a request is for, and the path a plain one asks for; no host when it names none."""
        if method == "CONNECT":
            host, _, port = target.rpartition(":")
            path = ""
        else:
            parts, port = urlsplit(target), ""
            with contextlib.suppress(ValueError):
                port = str(parts.port or 80)
            host = (parts.hostname or "") if parts.scheme == "http" else ""
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        if not port.isdigit() or not 0 < int(port) < 65536:
            return "", 0, ""
        return host.strip("[]").lower().rstrip("."), int(port), path

    @classmethod
    def forwarded(cls, head: bytes, method: str, path: str, version: str, host: str, port: int) -> bytes:
        """A plain http request as its host takes it: its path alone, the Host decided, and Connection: close, so a
        next request comes on a connection of its own and is decided too."""
        named = (f"[{host}]" if ":" in host else host) + ("" if port == 80 else f":{port}")
        kept = [line for line in head.split(b"\r\n")[1:-2] if line.split(b":", 1)[0].strip().lower() not in cls.HOPS]
        first = f"{method} {path} {version}".encode("latin-1")
        return b"\r\n".join([first, *kept, f"Host: {named}".encode("latin-1"), b"Connection: close"]) + b"\r\n\r\n"

    @staticmethod
    async def _http_answer(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
        """A request not carried: the reason as the status line's words, so the program can say why."""
        line = " ".join(reason.split())
        writer.write(f"HTTP/1.1 {status} {line}\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n"
                     f"{reason}\n".encode())
        await writer.drain()

    # -- SOCKS5's requests (RFC 1928) -----------------------------------------------------------------

    async def _socks(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, source: Any) -> None:
        """With no authentication, since the source is known: CONNECT, a TCP connection; UDP ASSOCIATE, datagrams."""
        methods = await reader.readexactly((await reader.readexactly(1))[0])
        writer.write(b"\x05\x00" if 0 in methods else b"\x05\xff")
        if 0 not in methods:
            return
        head = await reader.readexactly(5)
        rest = {1: 5, 4: 17, 3: head[4] + 2}.get(head[3])
        if head[0] != 5 or rest is None:
            return self._socks_answer(writer, 8)
        host, port, _ = self.parsed(head[3:] + await reader.readexactly(rest))
        if head[1] == 3:
            return await self._udp(reader, writer, source)
        if head[1] != 1:
            return self._socks_answer(writer, 7)
        try:
            up_reader, up_writer, until = await self._connect(source, host, port)
        except (PermissionError, ConnectionError) as error:
            return self._socks_answer(writer, 2 if isinstance(error, PermissionError) else 4)
        self._socks_answer(writer, 0)
        await self._carry(reader, writer, up_reader, up_writer, until, (source, host, port))

    async def _udp(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, source: Any) -> None:
        """Datagrams between the client and any host, relayed while this connection stays open: each new host decided
        once, and answers taken only from where datagrams went."""
        loop = asyncio.get_running_loop()
        here = (writer.get_extra_info("sockname") or ("",))[0]
        client = (writer.get_extra_info("peername") or ("",))[0]
        relay = self._datagrams(here)
        #: Where the client sends from; each host decided, as (host, port): its address and until; each address sent
        #: to, back to its host; a socket out for each address family; when a datagram last went either way.
        sender, decided, back, out, seen = [None], {}, {}, {}, [time.monotonic()]

        def allowed(key: Any) -> bool:
            return key in decided and (decided[key][1] is None or not decided[key][1].done())

        async def outward() -> None:
            while True:
                data, came = await loop.sock_recvfrom(relay, 65535)
                if came[0] != client or len(data) < 10 or data[2]:  # the client's own, and whole: never in pieces
                    continue
                sender[0], seen[0] = came, time.monotonic()
                with contextlib.suppress(OSError, ValueError, IndexError):
                    host, port, end = self.parsed(data[3:])
                    if not allowed((host, port)):
                        found, until = await self._decided(source, "udp", host, port)
                        decided[(host, port)], back[(found[0], port)] = (found[0], until), (host, port)
                    address = decided[(host, port)][0]
                    family = socket.AF_INET6 if ":" in address else socket.AF_INET
                    if family not in out:
                        out[family] = self._datagrams("::" if family == socket.AF_INET6 else "0.0.0.0")
                        tasks.add(asyncio.ensure_future(inward(out[family])))
                    await loop.sock_sendto(out[family], data[3 + end:], (address, port))
                    self._count(source, host, port, bytes_out=len(data) - 3 - end)

        async def inward(sock: socket.socket) -> None:
            while True:
                data, came = await loop.sock_recvfrom(sock, 65535)
                if sender[0] is not None and allowed(back.get(came[:2])):
                    seen[0] = time.monotonic()
                    await loop.sock_sendto(relay, b"\x00\x00\x00" + self.packed(*came[:2]) + data, sender[0])
                    self._count(source, *back[came[:2]], bytes_in=len(data))

        tasks = {asyncio.ensure_future(outward())}
        self._socks_answer(writer, 0, relay.getsockname()[:2])
        try:
            while True:
                try:
                    if not await asyncio.wait_for(reader.read(1024), self.IDLE):
                        break  # the client closed the connection: the session ends with it
                except TimeoutError:
                    if time.monotonic() - seen[0] >= self.IDLE:
                        break
        finally:
            for task in tasks:
                task.cancel()
            for sock in [relay, *out.values()]:
                sock.close()

    @classmethod
    def _socks_answer(cls, writer: asyncio.StreamWriter, code: int, bound: tuple[str, int] = ("0.0.0.0", 0)) -> None:
        """0 done, 2 not allowed, 4 host unreachable, 7 command not supported, 8 address type not supported."""
        writer.write(bytes([5, code, 0]) + cls.packed(*bound))

    @staticmethod
    def parsed(data: bytes) -> tuple[str, int, int]:
        """An address as SOCKS writes it, at the start of data: the host, the port, and where it ends."""
        if data[0] == 1:
            host, end = socket.inet_ntop(socket.AF_INET, data[1:5]), 5
        elif data[0] == 4:
            host, end = socket.inet_ntop(socket.AF_INET6, data[1:17]), 17
        elif data[0] == 3:
            host, end = data[2:2 + data[1]].decode("ascii", "replace"), 2 + data[1]
        else:
            raise ValueError("an address type SOCKS doesn't have")
        return host.lower().rstrip("."), int.from_bytes(data[end:end + 2], "big"), end + 2

    @staticmethod
    def packed(host: str, port: int) -> bytes:
        """An address as SOCKS writes it: its type, its bytes, its port."""
        ip = ipaddress.ip_address(host)
        return bytes([1 if ip.version == 4 else 4]) + ip.packed + port.to_bytes(2, "big")

    @staticmethod
    def _datagrams(host: str) -> socket.socket:
        """A UDP socket on host, at a port the system chooses."""
        sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.bind((host, 0))
        return sock

    # -- what both share --------------------------------------------------------------------------------

    def _count(self, source: Any, host: str, port: int, **counts: int) -> None:
        """Counts added to what a source reached at host:port, now its last time."""
        rows, now = self.counted.setdefault(source, {}), time.time()
        row = rows.get((host, port))
        if row is None:
            if len(rows) >= self.COUNTED_MOST:
                return
            row = rows[(host, port)] = {"connections": 0, "refused": 0, "bytes_out": 0, "bytes_in": 0,
                                        "first_at": now}
        for name, value in counts.items():
            row[name] += value
        row["last_at"] = now

    async def _decided(self, source: Any, protocol: str, host: str, port: int) -> tuple[list[str], Any]:
        """The addresses host:port may be reached at, and the future it may be reached until (or None): decided by
        name first, then looked up, every address public. PermissionError when it may not be reached; ConnectionError
        when the name has no address."""
        def refused(reason: str) -> PermissionError:
            log.info(f"{source}: {protocol} {host}:{port} refused: {reason}")
            self._count(source, host, port, refused=1)
            return PermissionError(reason)

        if self.open[source] > self.most:
            raise refused("This source has as many connections open as it may.")
        answer = await self.decide(source, protocol, host, port)
        if isinstance(answer, str):
            raise refused(answer)
        found = await self.lookup(host, port)
        if not found:
            log.info(f"{source}: {protocol} {host}:{port} has no address")
            raise ConnectionError(f"{host} could not be looked up.")
        if not all(map(self.public, found)):
            raise refused(f"{host} is not a public address.")
        log.info(f"{source}: {protocol} {host}:{port} allowed")
        self._count(source, host, port, connections=1)
        return found, answer

    async def _connect(self, source: Any, host: str,
                       port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, Any]:
        """A TCP connection to host:port once decided, at each of its addresses in turn; and until when."""
        found, until = await self._decided(source, "tcp", host, port)
        for address in found:
            with contextlib.suppress(OSError, TimeoutError):
                up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(address, port), self.SECONDS)
                return up_reader, up_writer, until
        log.info(f"{source}: tcp {host}:{port} did not answer")
        raise ConnectionError(f"{host}:{port} did not answer.")

    async def _carry(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                     up_reader: asyncio.StreamReader, up_writer: asyncio.StreamWriter, until: Any,
                     aimed: tuple[Any, str, int]) -> None:
        """Bytes both ways until either side ends, both are quiet IDLE seconds, or until is done; counted for
        ``aimed``, the source, host and port."""
        seen = [time.monotonic()]
        pipes = asyncio.gather(self._pipe(reader, up_writer, seen, lambda size: self._count(*aimed, bytes_out=size)),
                               self._pipe(up_reader, writer, seen, lambda size: self._count(*aimed, bytes_in=size)),
                               return_exceptions=True)
        if until is not None:
            await asyncio.wait({pipes, until}, return_when=asyncio.FIRST_COMPLETED)
            writer.close()
            up_writer.close()
        await pipes

    @classmethod
    async def _pipe(cls, source: asyncio.StreamReader, target: asyncio.StreamWriter, seen: list[float],
                    count: Callable[[int], None]) -> None:
        """One way of a connection, the size of each piece given to ``count``; ``seen``: when bytes last went either
        way."""
        try:
            while True:
                try:
                    data = await asyncio.wait_for(source.read(64 * 1024), cls.IDLE)
                except TimeoutError:
                    if time.monotonic() - seen[0] < cls.IDLE:
                        continue
                    break
                if not data:
                    break
                seen[0] = time.monotonic()
                target.write(data)
                count(len(data))
                await target.drain()
        finally:
            target.close()
