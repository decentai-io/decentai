"""A connection to a host, for a protocol that is not the web's.

Where the platform confines agents, a worker's one way out is the
platform's proxy (docs/system/sandbox.md). A library that speaks HTTP
takes the proxy from the environment and needs nothing. One that
speaks something else — mail, a database — opens a socket of its own,
and a socket opened straight from a confined worker goes nowhere.

    from decentai_sdk.net import Tunnel

    link = Tunnel.open("imap.example.com", 993)     # a socket, connected

The host and the port are the ones the agent's manifest declared
(``imap.example.com:993``): the proxy opens those and refuses the rest,
and ``TunnelRefused`` carries its reason in its own words. What travels
through is the agent's own — the proxy passes bytes and reads none, so
a connection that is to be encrypted is wrapped by the agent, over the
socket this returns. Where nothing confines, the socket is an ordinary
one, connected straight.

Stdlib only, as everything in the SDK is.
"""

from __future__ import annotations

import base64
import os
import re
import socket
from typing import Optional
from urllib.parse import urlsplit

#: Where the platform points a confined worker.
PROXY_VARIABLE = "DECENTAI_PROXY"


class TunnelRefused(ConnectionError):
    """The platform's proxy would not open the connection. ``status``
    is its answer's number and ``reason`` its own words: 403 for a host
    or a port the agent did not declare, 502 for a host that was
    allowed and did not answer."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason or f"the platform's proxy answered {status}")
        self.status = status
        self.reason = reason


class Tunnel:
    HEAD_LIMIT = 16 * 1024
    ANSWER_RE = re.compile(r"^HTTP/\d\.\d (\d{3}) ?(.*)$")

    @classmethod
    def proxy(cls) -> Optional[str]:
        """The platform's proxy, or None where nothing confines."""
        return (os.environ.get(PROXY_VARIABLE) or "").strip() or None

    @classmethod
    def open(cls, host: str, port: int, timeout: Optional[float] = 30) -> socket.socket:
        """A socket connected to ``host`` on ``port``: through the
        platform's proxy where there is one, straight where there is
        not. Raises TunnelRefused for what the proxy would not open and
        OSError for a connection that could not be made."""
        proxy = cls.proxy()
        if proxy is None:
            return socket.create_connection((host, int(port)), timeout)
        return cls.through(proxy, host, int(port), timeout)

    @classmethod
    def through(cls, proxy: str, host: str, port: int,
                timeout: Optional[float] = 30) -> socket.socket:
        where = urlsplit(proxy)
        target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        credentials = base64.b64encode(
            f"{where.username or ''}:{where.password or ''}".encode("utf-8")
        ).decode("ascii")
        try:
            request = (f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
                       f"Proxy-Authorization: Basic {credentials}\r\n\r\n"
                       ).encode("latin-1")
        except UnicodeEncodeError:
            # Said before anything is opened, and as the error a
            # connection that could not be made is said in.
            raise OSError(f"'{host}' is not a host name that can be "
                          f"connected to") from None
        link = socket.create_connection((where.hostname, where.port), timeout)
        try:
            link.sendall(request)
            head = cls._head(link)
        except BaseException:
            link.close()
            raise
        line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        answer = cls.ANSWER_RE.match(line)
        if answer is None or answer.group(1) != "200":
            link.close()
            raise TunnelRefused(int(answer.group(1)) if answer else 502,
                                answer.group(2).strip() if answer else
                                "the platform's proxy gave no answer that can be read")
        return link

    @classmethod
    def _head(cls, link: socket.socket) -> bytes:
        """The proxy's answer, to the blank line that ends it and no
        further: what follows is the host's, and the agent's to read."""
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            byte = link.recv(1)
            if not byte:
                break
            head += byte
            if len(head) > cls.HEAD_LIMIT:
                raise TunnelRefused(502, "the platform's proxy answered without end")
        return head
