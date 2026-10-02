"""The one way out for a confined worker (docs/system/sandbox.md).

A manifest says which hosts its agent connects to, and an administrator
approves it. This is what holds the agent to that: a proxy the runtime
runs, which every worker is pointed at and — where the container has a
firewall rule — cannot go around.

    proxy = EgressProxy(port)
    proxy.start()
    token = proxy.admit(agent_id, manifest.network)   one worker's pass
    proxy.address(token)                              what the worker is given
    proxy.learn(token, host)                          a host its credential named
    proxy.lend(token, hosts)                          hosts a person allowed, for a call
    proxy.block(token, names)                         sites no agent may open here
    proxy.reached(token)                              the hosts it connected to, counted
    proxy.take_back(token, hosts)                     when the call ends
    proxy.dismiss(token)

For every connection a worker asks for, the proxy:

1. knows the worker by its token, and refuses one it does not know;
2. refuses a host the agent's manifest does not name;
3. resolves the name itself, and refuses an address that is not a
   public one — this machine, a private network, the address a cloud's
   metadata service answers on — whatever the manifest says;
4. connects to the address it checked, and passes bytes both ways.

It sees a host and a port. Encrypted traffic passes through it closed:
it opens nothing and installs no certificate.

It runs on a thread and a loop of its own. What a browser downloads
passes through here, and the runtime's own loop has chats to serve.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import secrets
import socket
import threading
from typing import Callable, ClassVar, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from ai_runtime.runtime_logging import RuntimeLoggerFactory


class Admission:
    """What one worker may reach: what its agent's manifest declared."""

    def __init__(self, agent_id: str, network: dict):
        self.agent_id = agent_id
        network = network or {}
        #: Every host — said, or implied by a manifest that says nothing.
        self.any = bool(network.get("any"))
        #: What was declared, each as (name, port): a name that begins
        #: with a dot is every host under it, and a port of None is
        #: where the web answers.
        self.opened: List[Tuple[str, Optional[int]]] = []
        for host in network.get("hosts") or []:
            self.learn(*self.named(host))
        #: ``<secret>.<field>``: hosts a granted credential will name.
        self.from_secrets = [str(f) for f in network.get("from_secrets") or []]
        #: What a person allowed for a call and no longer: (name, port)
        #: and how many calls hold it, so that one call ending does not
        #: close what another still runs on.
        self.lent: Dict[Tuple[str, Optional[int]], int] = {}
        #: Sites no agent may open in this deployment (Settings:Safety):
        #: a name is itself and every host under it, and it is refused
        #: whatever the manifest declared, ``any`` included.
        self.blocked: List[str] = []
        #: The hosts this worker connected to, and how many times: what
        #: a call's line on the audit trail is written from.
        self.reached: Dict[str, int] = {}

    def blocks(self, host: str) -> bool:
        host = str(host or "").strip().lower().rstrip(".")
        return any(host == name or host.endswith("." + name)
                   for name in self.blocked)

    @staticmethod
    def named(declared: str) -> Tuple[str, Optional[int]]:
        """(name, port) of a host as a manifest writes it."""
        declared = str(declared or "").strip().lower()
        name, colon, port = declared.rpartition(":")
        if colon and port.isdigit():
            return name, int(port)
        return declared, None

    def learn(self, host: str, port: Optional[int] = None) -> None:
        host = str(host or "").strip().lower().rstrip(".")
        if host.startswith("*."):
            host = host[1:]                       # ".example.com"
        if host and (host, port) not in self.opened:
            self.opened.append((host, port))

    def lend(self, host: str, port: Optional[int] = None) -> Tuple[str, Optional[int]]:
        """A host opened for one call. Returns what was lent, for
        taking back."""
        lent = (str(host or "").strip().lower().rstrip("."), port)
        if lent[0]:
            self.lent[lent] = self.lent.get(lent, 0) + 1
        return lent

    def take_back(self, host: str, port: Optional[int] = None) -> None:
        lent = (str(host or "").strip().lower().rstrip("."), port)
        held = self.lent.get(lent, 0)
        if held <= 1:
            self.lent.pop(lent, None)
        else:
            self.lent[lent] = held - 1

    def _ports(self, host: str) -> List[Optional[int]]:
        """The ports declared for this host, by every entry that names
        it, and the ones lent for a call; empty when none does."""
        host = str(host or "").strip().lower().rstrip(".")
        return [port for name, port in [*self.opened, *self.lent]
                if host == name or (name.startswith(".") and host.endswith(name)
                                    and len(host) > len(name))]

    def names(self, host: str) -> bool:
        return self.any or bool(self._ports(host))

    def opens(self, host: str, port: int, web: Tuple[int, ...]) -> bool:
        """Whether this port of the host was declared: the port
        written beside the name, or where the web answers (``web``)
        for a name written alone."""
        if self.any:
            return True
        return any(port in web if declared is None else port == declared
                   for declared in self._ports(host))

    def reached_on(self, host: str, web: Tuple[int, ...]) -> str:
        """The ports the host was declared on, in words: the ones
        written beside its name, then the web's where it was written
        alone."""
        declared = self._ports(host)
        ports = sorted({port for port in declared if port is not None})
        if None in declared:
            ports += [port for port in web if port not in ports]
        return " or ".join(str(port) for port in ports)


class Refused(Exception):
    """A connection the proxy will not make, and what the worker is told."""

    def __init__(self, status: int, why: str):
        super().__init__(why)
        self.status = status
        self.why = why


class EgressProxy:
    HOST = "127.0.0.1"
    #: A host an agent named is reached where a service answers.
    PORTS_OF_A_NAMED_HOST: ClassVar[Tuple[int, ...]] = (443, 80)
    HEAD_LIMIT = 64 * 1024
    HEAD_SECONDS = 30
    CONNECT_SECONDS = 20
    CHUNK = 64 * 1024
    #: How long a connection may carry nothing, either way, before it
    #: is closed. Longer than the longest wait a protocol keeps on
    #: purpose (a mailbox watched for new mail renews under half an
    #: hour); a connection quiet for longer is one somebody forgot.
    IDLE_SECONDS = 30 * 60
    #: Connections open at once: for one worker — a browser loading a
    #: page opens dozens — and for the proxy as a whole. One past the
    #: number is refused, and says so, rather than left to starve the
    #: others of the runtime's own sockets.
    MAX_PER_WORKER = 128
    MAX_CONNECTIONS = 1024

    #: Test seam — a callable (host, port) -> [addresses]. Set it and
    #: no name is looked up.
    resolver: ClassVar[Optional[Callable[[str, int], List[str]]]] = None

    def __init__(self, port: int, allow_loopback: bool = False):
        self.port = int(port)
        #: The tests' escape, as the web agents have it: a loopback
        #: address and nothing else. Never in a deployment.
        self.allow_loopback = bool(allow_loopback)
        self._admissions: Dict[str, Admission] = {}
        self._guard = threading.Lock()
        #: The connections being served. Held here because nothing else
        #: holds them: a loop keeps a task only weakly, and a connection
        #: whose worker hung up while the host was still answering would
        #: be collected mid-sentence, its other end left open.
        self._serving: set = set()
        #: token -> connections that worker has open now.
        self._open: Dict[str, int] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._problem = ""
        self.refusals = 0
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # Its life
    # ------------------------------------------------------------------

    def start(self) -> List[str]:
        """Listen. Returns errors, empty when the proxy is serving."""
        ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(ready,), name="egress-proxy", daemon=True)
        self._thread.start()
        if not ready.wait(10):
            return ["the proxy did not start in time"]
        return [self._problem] if self._problem else []

    def _run(self, ready: threading.Event) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            server = loop.run_until_complete(asyncio.start_server(
                self._serve, self.HOST, self.port, limit=self.HEAD_LIMIT))
        except OSError as exc:
            self._problem = f"the proxy could not listen on {self.port}: {exc}"
            ready.set()
            loop.close()
            return
        self.port = server.sockets[0].getsockname()[1]
        self._loop = loop
        ready.set()
        try:
            loop.run_forever()
        finally:
            # Whatever is still passing ends here, on the loop it began
            # on: a connection closed after its loop is one nobody can
            # close.
            server.close()
            passing = asyncio.all_tasks(loop)
            for task in passing:
                task.cancel()
            loop.run_until_complete(
                asyncio.gather(*passing, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def stop(self) -> None:
        loop, self._loop = self._loop, None
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(5)

    @property
    def serving(self) -> bool:
        return self._loop is not None

    # ------------------------------------------------------------------
    # Who may pass
    # ------------------------------------------------------------------

    def admit(self, agent_id: str, network: dict) -> str:
        """A pass for one worker. What it opens is what ``network`` —
        the manifest's own answer — declared."""
        token = secrets.token_urlsafe(24)
        with self._guard:
            self._admissions[token] = Admission(agent_id, network)
        return token

    def dismiss(self, token: str) -> None:
        with self._guard:
            self._admissions.pop(str(token or ""), None)

    def learn(self, token: str, host: str, port: Optional[int] = None) -> None:
        """A host the worker's credential named (``from_secret``), and
        the port it is reached on where that is not the web's."""
        with self._guard:
            admission = self._admissions.get(str(token or ""))
            if admission is not None:
                admission.learn(host, port)

    def lend(self, token: str, hosts: List[str]) -> List[Tuple[str, Optional[int]]]:
        """Hosts a person allowed on a code card, opened until the call
        that asked ends. Returns what was lent."""
        with self._guard:
            admission = self._admissions.get(str(token or ""))
            if admission is None:
                return []
            return [admission.lend(*Admission.named(host)) for host in hosts or []]

    def take_back(self, token: str,
                  lent: List[Tuple[str, Optional[int]]]) -> None:
        with self._guard:
            admission = self._admissions.get(str(token or ""))
            if admission is not None:
                for host, port in lent or []:
                    admission.take_back(host, port)

    def block(self, token: str, names: List[str]) -> None:
        """The sites no agent may open, as the deployment's setting has
        them now: said again with every call, so a change takes effect
        at the next one."""
        cleaned = [str(name or "").strip().lower().strip(".")
                   for name in names or []]
        with self._guard:
            admission = self._admissions.get(str(token or ""))
            if admission is not None:
                admission.blocked = [name for name in cleaned if name]

    def reached(self, token: str) -> Dict[str, int]:
        """host -> how many connections this worker has made to it."""
        with self._guard:
            admission = self._admissions.get(str(token or ""))
            return dict(admission.reached) if admission is not None else {}

    def address(self, token: str) -> str:
        """What a worker is given as its proxy."""
        return f"http://worker:{token}@{self.HOST}:{self.port}"

    def _admission(self, token: str) -> Optional[Admission]:
        with self._guard:
            return self._admissions.get(token)

    # ------------------------------------------------------------------
    # One connection
    # ------------------------------------------------------------------

    async def _serve(self, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        serving = asyncio.current_task()
        self._serving.add(serving)
        try:
            await self._connection(reader, writer)
        finally:
            self._serving.discard(serving)

    def _hold(self, token: str) -> str:
        """One more connection for this worker, counted — or Refused,
        when it or the proxy already has as many as it may. Returns
        what ``_release`` is given when the connection ends."""
        with self._guard:
            mine = self._open.get(token, 0)
            if mine >= self.MAX_PER_WORKER:
                raise Refused(503, f"this agent already has "
                                   f"{self.MAX_PER_WORKER} connections open")
            if sum(self._open.values()) >= self.MAX_CONNECTIONS:
                raise Refused(503, "too many connections are open through "
                                   "the platform's proxy; try again shortly")
            self._open[token] = mine + 1
        return token

    def _release(self, token: str) -> None:
        with self._guard:
            left = self._open.get(token, 0) - 1
            if left > 0:
                self._open[token] = left
            else:
                self._open.pop(token, None)

    async def _connection(self, reader: asyncio.StreamReader,
                          writer: asyncio.StreamWriter) -> None:
        upstream: Optional[asyncio.StreamWriter] = None
        held = ""
        try:
            try:
                head = await asyncio.wait_for(
                    reader.readuntil(b"\r\n\r\n"), self.HEAD_SECONDS)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                    asyncio.TimeoutError, ConnectionError):
                return
            try:
                method, target, headers = self._parse(head)
                token = self._token(headers)
                admission = self._admission(token)
                if admission is None:
                    raise Refused(407, "this proxy serves the platform's "
                                       "own workers, and does not know you")
                held = self._hold(token)
                tunnel = method == "CONNECT"
                host, port, path = self._where(method, target)
                self._check_name(admission, host, port)
                address = await self._public_address(host, port)
            except Refused as refusal:
                await self._refuse(writer, refusal, head)
                return

            try:
                upstream_reader, upstream = await asyncio.wait_for(
                    asyncio.open_connection(address, port), self.CONNECT_SECONDS)
            except (OSError, asyncio.TimeoutError) as exc:
                await self._answer(writer, 502, f"{host} could not be reached: "
                                                f"{exc or 'no answer in time'}")
                return

            with self._guard:
                admission.reached[host] = admission.reached.get(host, 0) + 1
            if tunnel:
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
            else:
                upstream.write(self._forwarded(method, path, host, port, headers))
                await upstream.drain()
            await self._pass(reader, writer, upstream_reader, upstream)
        except (ConnectionError, OSError):
            pass
        finally:
            if held:
                self._release(held)
            for stream in (writer, upstream):
                if stream is not None:
                    try:
                        stream.close()
                    except RuntimeError:
                        pass        # its loop closed under it

    # -- reading what was asked ------------------------------------------

    @staticmethod
    def _parse(head: bytes) -> Tuple[str, str, List[Tuple[str, str]]]:
        try:
            lines = head.decode("latin-1").split("\r\n")
            method, target, _version = lines[0].split(" ", 2)
        except ValueError:
            raise Refused(400, "that is not a request a proxy can read")
        headers = []
        for line in lines[1:]:
            name, colon, value = line.partition(":")
            if colon:
                headers.append((name.strip(), value.strip()))
        return method.upper(), target, headers

    @staticmethod
    def _token(headers: List[Tuple[str, str]]) -> str:
        for name, value in headers:
            if name.lower() != "proxy-authorization":
                continue
            scheme, _, encoded = value.partition(" ")
            if scheme.lower() != "basic":
                return ""
            try:
                decoded = base64.b64decode(encoded.strip()).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return ""
            return decoded.partition(":")[2]
        return ""

    @staticmethod
    def _where(method: str, target: str) -> Tuple[str, int, str]:
        """(host, port, path) of what was asked for."""
        if method == "CONNECT":
            parts = urlsplit("//" + target)
            path = ""
            default = 443
        else:
            parts = urlsplit(target)
            if parts.scheme.lower() != "http":
                raise Refused(400, "a proxy is asked for a whole address: "
                                   "http://host/path, or CONNECT host:port")
            path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
            default = 80
        try:
            host, port = parts.hostname or "", parts.port or default
        except ValueError:
            raise Refused(400, "that address has no readable port")
        if not host:
            raise Refused(400, "that address names no host")
        return host.lower().rstrip("."), port, path

    # -- deciding ---------------------------------------------------------

    def _check_name(self, admission: Admission, host: str, port: int) -> None:
        if admission.blocks(host):
            raise Refused(403, f"{host} is on the list of sites no agent "
                               f"may open here")
        if admission.blocked and self._is_address(host):
            # The list is of names. An address reaches a site without
            # saying which, so while a list is in force nothing is
            # reached by address — whatever else the agent may reach.
            raise Refused(403, f"{host} is an address, not a name, and "
                               f"sites are blocked here by name")
        if admission.any:
            return
        if self._is_address(host):
            raise Refused(403, f"{admission.agent_id} connects to the hosts "
                               f"it declared, and {host} is an address, "
                               f"not a name")
        if not admission.names(host):
            raise Refused(403, f"{admission.agent_id} did not declare {host} "
                               f"among the hosts it connects to")
        if not admission.opens(host, port, self.PORTS_OF_A_NAMED_HOST):
            reached_on = admission.reached_on(host, self.PORTS_OF_A_NAMED_HOST)
            raise Refused(403, f"{host} is reached on port {reached_on}, "
                               f"not {port}")

    @staticmethod
    def _is_address(host: str) -> bool:
        try:
            ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            return False
        return True

    async def _public_address(self, host: str, port: int) -> str:
        """The address to connect to: one this proxy looked up and
        checked itself. One forbidden answer refuses the host — which
        one a connection would use is not ours to choose."""
        addresses = await self._resolve(host, port)
        if not addresses:
            raise Refused(502, f"{host} does not resolve")
        for address in addresses:
            why = self._not_public(address)
            if why:
                raise Refused(403, f"{host} is {why}; only public internet "
                                   f"addresses are reached")
        return addresses[0]

    async def _resolve(self, host: str, port: int) -> List[str]:
        if EgressProxy.resolver is not None:
            return list(EgressProxy.resolver(host, port))
        try:
            answers = await asyncio.get_running_loop().getaddrinfo(
                host.strip("[]"), port, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError):
            return []
        found: List[str] = []
        for answer in answers:
            address = str(answer[4][0]).split("%", 1)[0]
            if address not in found:
                found.append(address)
        return found

    def _not_public(self, address: str) -> str:
        """Why this address is not one to connect to, or ''."""
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return "not an address"
        ip = self._carried(ip)
        if ip.is_loopback:
            return "" if self.allow_loopback else "this machine's own address"
        if ip.is_link_local:
            return "a link-local address"
        if ip.is_private:
            return "a private network address"
        if not ip.is_global:
            return "not a public internet address"
        return ""

    #: IPv6 ranges that carry an IPv4 address in their last 32 bits: a
    #: NAT64 gateway's, and the old IPv4-compatible form.
    CARRYING = (ipaddress.ip_network("64:ff9b::/96"),
                ipaddress.ip_network("::/96"))

    @classmethod
    def _carried(cls, ip):
        """The IPv4 address an IPv6 one stands for, where it stands for
        one — mapped, 6to4, Teredo, NAT64. It is that address a
        connection reaches, so that is the one judged: a private or
        loopback address does not become public by being wrapped."""
        if ip.version != 6:
            return ip
        for inner in (ip.ipv4_mapped, ip.sixtofour,
                      ip.teredo[1] if ip.teredo else None):
            if inner is not None:
                return inner
        if int(ip) > 1 and any(ip in network for network in cls.CARRYING):
            return ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
        return ip

    # -- answering ---------------------------------------------------------

    @staticmethod
    def _forwarded(method: str, path: str, host: str, port: int,
                   headers: List[Tuple[str, str]]) -> bytes:
        """The request as the host itself is asked: its path, without
        what was said to the proxy, and one request to a connection.
        Host is the one that was checked, never the worker's own: on an
        address that serves many sites, a different Host would reach a
        different one."""
        kept = [(name, value) for name, value in headers
                if name.lower() not in (
                    "proxy-authorization", "proxy-connection", "connection",
                    "host")]
        kept.insert(0, ("Host", host if port == 80 else f"{host}:{port}"))
        kept.append(("Connection", "close"))
        lines = [f"{method} {path} HTTP/1.1",
                 *[f"{name}: {value}" for name, value in kept]]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")

    async def _refuse(self, writer: asyncio.StreamWriter, refusal: Refused,
                      head: bytes) -> None:
        # A program that was given a pass asks once without it, is told
        # to show it, and asks again: that first answer is how a proxy
        # is spoken to, and no refusal of anybody.
        challenge = (refusal.status == 407
                     and b"proxy-authorization:" not in head.lower())
        if not challenge:
            self.refusals += 1
            asked = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")[:200]
            self.logger.warning(
                f"Refused ({refusal.status}): {refusal.why} [{asked}]")
        await self._answer(writer, refusal.status, refusal.why)

    #: What marks an answer as the proxy's own and not the host's.
    REFUSED_HEADER = "X-DecentAI-Refused"

    @classmethod
    async def _answer(cls, writer: asyncio.StreamWriter, status: int,
                      why: str) -> None:
        # The reason is the status line's own: for a tunnel that was
        # refused, the line is all a program is shown of the answer.
        reason = "Proxy Authentication Required" if status == 407 else (
            why.encode("ascii", "replace").decode("ascii")
            .replace("\r", " ").replace("\n", " ")[:300])
        body = f"DecentAI: {why}.\n".encode("utf-8")
        lines = [f"HTTP/1.1 {status} {reason}",
                 "Content-Type: text/plain; charset=utf-8",
                 f"Content-Length: {len(body)}", "Connection: close",
                 f"{cls.REFUSED_HEADER}: {status}"]
        if status == 407:
            lines.append('Proxy-Authenticate: Basic realm="DecentAI"')
        try:
            writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
            await writer.drain()
        except (ConnectionError, OSError):
            pass

    async def _pass(self, reader: asyncio.StreamReader,
                    writer: asyncio.StreamWriter,
                    upstream_reader: asyncio.StreamReader,
                    upstream: asyncio.StreamWriter) -> None:
        """Bytes both ways, until both sides have said all they had —
        or neither has said anything for ``IDLE_SECONDS``."""
        loop = asyncio.get_running_loop()
        heard = [loop.time()]       # when a byte last moved, either way

        async def one_way(source: asyncio.StreamReader,
                          sink: asyncio.StreamWriter) -> None:
            try:
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            source.read(self.CHUNK), self.IDLE_SECONDS)
                    except asyncio.TimeoutError:
                        # Quiet this way. A download is quiet one way
                        # for as long as it lasts: only both is idle.
                        if loop.time() - heard[0] < self.IDLE_SECONDS:
                            continue
                        break
                    if not chunk:
                        break
                    heard[0] = loop.time()
                    sink.write(chunk)
                    await sink.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                try:
                    if sink.can_write_eof():
                        sink.write_eof()
                except (ConnectionError, OSError, RuntimeError):
                    pass

        await asyncio.gather(one_way(reader, upstream),
                             one_way(upstream_reader, writer))
