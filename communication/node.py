"""A node: it serves its endpoints on port 8004 over HTTPS with mutual TLS, and calls other nodes'
endpoints (documentation/communication.md)."""

import asyncio
import json
import logging
import os
import ssl
from typing import Any, Callable

import aiohttp
import msgpack
from aiohttp import web
from cryptography import x509

from communication.enrollment import Enrollment
from communication.identity import Identity
from communication.router import Answer, Router

log = logging.getLogger("communication")


class Node:
    PORT = 8004
    #: The largest body, either way.
    LIMIT = 1024 * 1024
    #: How long a call may take, in seconds.
    TIMEOUT = 30
    #: How often the identity folder is read again, in seconds.
    RELOAD_EVERY = 60
    MSGPACK, JSON = "application/msgpack", "application/json"

    def __init__(self, identity_folder: str, endpoints: dict[str, dict[str, Any]] | None = None,
                 allowed: Callable[[dict[str, Any] | None, str], bool] | None = None,
                 port: int | None = PORT, host: str = "0.0.0.0", limit: int = LIMIT, certified_only: bool = False):
        self.identity_folder = identity_folder
        self.limit = limit
        #: A caller with no certificate is refused before its body is read.
        self.certified_only = certified_only
        self.router = Router(endpoints or {}, allowed)
        self._host, self._port = host, port
        self._runner: web.AppRunner | None = None
        self._session: aiohttp.ClientSession | None = None
        self._client_tls: ssl.SSLContext | None = None
        self._named_tls: ssl.SSLContext | None = None
        self._server_tls: ssl.SSLContext | None = None
        self._tasks: list[asyncio.Task] = []
        #: Set once this running node enrolled again and took a new id: what it knows as its own is old.
        self.renamed = asyncio.Event()

    @classmethod
    async def enroll(cls, identity_folder: str) -> None:
        """A certificate, when the folder says where to ask: for a node that must know its own id before
        anything else can be built."""
        if Enrollment.wanted(identity_folder):
            node = cls(identity_folder, port=None)
            try:
                await node.start()
            finally:
                await node.stop()

    @property
    def port(self) -> int | None:
        """The port it serves on (the one it was given, or the one chosen for port 0), or None."""
        return self._runner.addresses[0][1] if self._runner and self._runner.addresses else None

    async def start(self, router: Router | None = None) -> None:
        """Enroll first, if the folder says where and holds no certificate that hasn't ended; then serve,
        if given a port. ``router`` is for a node whose controllers call out through the node itself, so
        it can't be built with them."""
        self.router = router or self.router
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.TIMEOUT))
        self.reload()
        if Enrollment.wanted(self.identity_folder):
            enrollment = Enrollment(self)
            await enrollment.ready()
            self._tasks.append(asyncio.create_task(enrollment.keep()))
        if self._port is not None:
            self._server_tls = self.tls(self.identity_folder, server=True)
            app = web.Application(client_max_size=self.limit)
            app.router.add_get("/health", self._health)
            app.router.add_post("/app", self._app)
            self._runner = web.AppRunner(app, access_log=None)
            await self._runner.setup()
            await web.TCPSite(self._runner, self._host, self._port, ssl_context=self._server_tls).start()
        self._tasks.append(asyncio.create_task(self._reload_forever()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if self._session is not None:
            await self._session.close()

    async def call(self, address: str, endpoint: str, data: dict[str, Any] | None = None,
                   kind: str | None = None, id: str | None = None, seconds: float = TIMEOUT) -> Answer:
        """Call an endpoint on the node at ``host:port``: its (answer, status). Given ``kind`` or ``id``
        (one of them), TLS also checks that the node reached has that kind or that id, before anything
        is sent. A node that is not there, whose certificate no trusted root signed, or that is not
        the one expected, raises aiohttp.ClientError; one that takes longer than ``seconds``, TimeoutError;
        an answer that can't be read, ValueError."""
        body = msgpack.packb({"endpoint": endpoint, "data": data or {}}, use_bin_type=True, default=str)
        expected = None if id is None and kind is None else Identity.name(id=id, kind=kind)
        async with self._session.post(f"https://{address}/app", data=body, server_hostname=expected,
                                      ssl=self._client_tls if expected is None else self._named_tls,
                                      timeout=aiohttp.ClientTimeout(total=seconds),
                                      headers={"Content-Type": self.MSGPACK}) as response:
            raw, form = await self._bounded(response), response.content_type
            # Something in between (a proxy, say) may answer in its own words: those come as text.
            answer = self._decode(raw, form) if form in (self.MSGPACK, self.JSON) else raw.decode(errors="replace")
            return answer, response.status

    async def _bounded(self, response: aiohttp.ClientResponse) -> bytes:
        """An answer, held to the limit a body has: the node that wrote it is not trusted to."""
        body = bytearray()
        async for piece in response.content.iter_chunked(64 * 1024):
            body += piece
            if len(body) > self.limit:
                raise aiohttp.ClientPayloadError(f"An answer is at most {self.limit} bytes.")
        return bytes(body)

    async def connect(self, address: str, kind: str | None = None,
                      id: str | None = None) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """A TLS connection of its own to another port of another node (``host:port``), both sides
        proved as on a call, and the node reached checked as ``call`` checks it: for bytes both ways,
        not calls. The other node serves that port with ``Node.tls(folder, server=True)``."""
        host, _, port = address.rpartition(":")
        expected = None if id is None and kind is None else Identity.name(id=id, kind=kind)
        return await asyncio.open_connection(host.strip("[]"), int(port), server_hostname=expected,
                                             ssl=self._client_tls if expected is None else self._named_tls)

    def reload(self) -> None:
        """Read the identity folder again: the certificate the server shows, and the ones calls are
        made with. Enrollment calls it after writing a new certificate."""
        if self._server_tls is not None:
            self._server_tls.load_cert_chain(os.path.join(self.identity_folder, Identity.NODE_FILE))
        named = self.tls(self.identity_folder, server=False)
        named.check_hostname = True
        self._client_tls, self._named_tls = self.tls(self.identity_folder, server=False), named

    @classmethod
    def tls(cls, identity_folder: str, server: bool) -> ssl.SSLContext:
        """TLS 1.3 with this node's certificate, checking the other side's against the trusted roots.
        Nodes are known by their certificates, not by host names: a caller that wants to know whom it
        reached checks one of the names the certificate carries (Identity.name). A server only asks
        for the caller's certificate, so /health answers anyone. A caller with no certificate, or only
        one that ended, calls with none: the one it ended with would be refused in TLS, and it could
        never enroll again. A server gives no ticket to resume a session with: OpenSSL would take the
        certificate as proved, even one that has ended since."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        if server or Identity.current(identity_folder) is not None:
            context.load_cert_chain(os.path.join(identity_folder, Identity.NODE_FILE))
        context.load_verify_locations(os.path.join(identity_folder, Identity.ROOTS_FILE))
        context.check_hostname = False
        context.verify_mode = ssl.CERT_OPTIONAL if server else ssl.CERT_REQUIRED
        if server:
            context.num_tickets = 0
        return context

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _app(self, request: web.Request) -> web.Response:
        shown = request.transport.get_extra_info("ssl_object").getpeercert(binary_form=True)
        peer = Identity.fields(x509.load_der_x509_certificate(shown)) if shown else None
        kind = self.JSON if request.content_type == self.JSON else self.MSGPACK
        if peer is None and self.certified_only:
            answer, status = Router.failed("Permission Error", "A certificate is required.", 401)
            return web.Response(body=msgpack.packb(answer) if kind == self.MSGPACK else json.dumps(answer).encode(),
                                status=status, content_type=kind)
        try:
            call = self._decode(await request.read(), kind)
        except web.HTTPRequestEntityTooLarge:
            answer, status = Router.failed("API Error", f"A body is at most {self.limit} bytes.", 413)
        except ValueError as error:
            answer, status = Router.failed("API Error", f"Invalid body: {error}")
        else:
            answer, status = await self.router.route(call, peer)
        body = json.dumps(answer, default=str).encode() if kind == self.JSON else \
            msgpack.packb(answer, use_bin_type=True, default=str)
        return web.Response(body=body, status=status, content_type=kind)

    @classmethod
    def _decode(cls, body: bytes, kind: str) -> Any:
        try:
            return json.loads(body) if kind == cls.JSON else msgpack.unpackb(body, raw=False)
        except Exception as error:  # msgpack raises several types for bad input
            raise ValueError(f"not {kind}: {error}") from error

    async def _reload_forever(self) -> None:
        """Every minute, the identity folder read again: a certificate renewed by anyone is picked up."""
        while True:
            await asyncio.sleep(self.RELOAD_EVERY)
            try:
                self.reload()
            except (OSError, ValueError, ssl.SSLError) as error:
                log.warning("could not read the identity folder again: %s", error)
