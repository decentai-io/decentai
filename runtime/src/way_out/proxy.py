"""The way out: a sandbox carries a process's connection here, the process named on the first line; the host is
decided, looked up once, and reached only at a public address, its bytes passed through unread
(runtime.md §7; agents.md §8; sandbox.md §7)."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from typing import Any
from urllib.parse import urlsplit

from cryptography import x509

from communication.identity import Identity
from communication.node import Node

log = logging.getLogger("runtime")
WEB_PORTS = (80, 443)
NAT64 = ipaddress.ip_network("64:ff9b::/96")


class Hosts:
    """Whether a process may reach a host: declared, opened for a call in flight, or read from a secret; never one the
    organization blocks."""

    @staticmethod
    def matches(pattern: str, host: str, port: int) -> bool:
        name, _, wanted = pattern.partition(":")
        if wanted:
            return wanted.isdigit() and int(wanted) == port and Hosts._named(name, host)
        return port in WEB_PORTS and Hosts._named(name, host)

    @staticmethod
    def _named(pattern: str, host: str) -> bool:
        return host == pattern or (pattern.startswith("*.") and host.endswith(pattern[1:]))

    @classmethod
    def refusal(cls, process: Any, host: str, port: int) -> str:
        blocked = (process.chat.contract.get("organization") or {}).get("blocked_hosts") or []
        if any(cls._named(pattern, host) for pattern in blocked):
            return f"{host} is blocked by the organization's policy."
        declared = [h for h in (process.manifest.get("network") or {}).get("hosts") or [] if isinstance(h, str)]
        if "any" in declared:
            return ""
        opened = {h for call in process.calls.values() for h in call.opened}
        if any(cls.matches(pattern, host, port) for pattern in [*declared, *opened, *process.secret_hosts]):
            return ""
        return f"{host}:{port} is not a host this agent declared, or was opened for this call."


class WayOut:
    #: A connection with no bytes either way for IDLE seconds is closed.
    HEAD_MOST, SECONDS, IDLE = 16 * 1024, 20, 300

    def __init__(self, identity: str, port: int, sandboxes: Any, connections_max: int):
        self.identity, self.port, self.sandboxes, self.connections_max = identity, port, sandboxes, connections_max
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._carried, "0.0.0.0", self.port,
                                                 ssl=Node.tls(self.identity, server=True))

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()

    async def _carried(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """A sandbox's connection: its certificate, the process's name, then the request."""
        process = None
        try:
            shown = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
            peer = Identity.fields(x509.load_der_x509_certificate(shown)) if shown else None
            name = (await asyncio.wait_for(reader.readline(), self.SECONDS)).decode(errors="replace").strip()
            process = self.sandboxes.processes.get(name)
            if peer is None or peer.get("kind") != "sandbox" or process is None or process.sandbox != peer["id"]:
                return
            if process.connections >= self.connections_max:
                return await self._refuse(writer, "This process has as many connections open as it may.")
            process.connections += 1
            await self._serve(process, reader, writer)
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
            log.debug(f"A way-out connection ended: {error}")
        finally:
            if process is not None and process.connections:
                process.connections -= 1
            writer.close()

    async def _serve(self, process: Any, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.SECONDS)
        if len(head) > self.HEAD_MOST:
            return await self._refuse(writer, "The request's head is too large.")
        method, target, version = (head.split(b"\r\n", 1)[0].decode("latin-1").split(" ") + ["", "", ""])[:3]
        tunnel = method == "CONNECT"
        if tunnel:
            host, _, port = target.rpartition(":")
            port = int(port) if port.isdigit() else 0
        else:
            parts = urlsplit(target)
            if parts.scheme != "http" or not parts.hostname:
                return await self._refuse(writer, "Ask for a tunnel (CONNECT), or a plain http:// address.")
            host, port = parts.hostname, parts.port or 80
        host = host.strip("[]").lower()
        refusal = Hosts.refusal(process, host, port)
        found = [] if refusal else await self.addresses(host, port)
        refusal = refusal or ("" if found and all(map(self.public, found)) else f"{host} is not a public address.")
        log.info(f"{process.name}: {host}:{port} {'refused: ' + refusal if refusal else 'reached'}")
        if refusal:
            return await self._refuse(writer, refusal)
        upstream_reader, upstream_writer = await asyncio.wait_for(asyncio.open_connection(found[0], port), self.SECONDS)
        if tunnel:
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        else:
            path = urlsplit(target)._replace(scheme="", netloc="").geturl() or "/"
            upstream_writer.write(head.replace(target.encode("latin-1"), path.encode("latin-1"), 1))
        seen = [time.monotonic()]
        await asyncio.gather(self._pipe(reader, upstream_writer, seen), self._pipe(upstream_reader, writer, seen))

    @staticmethod
    async def addresses(host: str, port: int) -> list[str]:
        """What the name answers with, looked up once; none when it can't be."""
        try:
            found = await asyncio.get_running_loop().getaddrinfo(host, port)
        except (OSError, UnicodeError):
            return []
        return list(dict.fromkeys(info[4][0] for info in found))

    @staticmethod
    def public(address: str) -> bool:
        """The public internet only, an IPv4 address inside an IPv6 one (mapped, 6to4, Teredo, NAT64) judged by what
        is inside."""
        ip = ipaddress.ip_address(address)
        if ip.version == 6:
            ip = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None) or (
                ipaddress.ip_address(int(ip) & 0xFFFFFFFF) if ip in NAT64 else ip)
        return ip.is_global and not ip.is_multicast

    @staticmethod
    async def _refuse(writer: asyncio.StreamWriter, reason: str) -> None:
        line = " ".join(reason.split())
        writer.write(f"HTTP/1.1 403 {line}\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n{reason}\n".encode())
        await writer.drain()

    @classmethod
    async def _pipe(cls, source: asyncio.StreamReader, target: asyncio.StreamWriter, seen: list[float]) -> None:
        """One way of a connection; ``seen`` is when bytes last went either way."""
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
                await target.drain()
        finally:
            target.close()
