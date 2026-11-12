"""An HTTP transport that reaches only the addresses it checked."""

from __future__ import annotations

import ipaddress
import urllib.request

import httpx

from ..utils import lookup, public


class Guard(httpx.AsyncBaseTransport):
    """For each request its host is looked up once, and every address it answers must be public or inside a host or
    network the operator named (``inside``: names, or networks such as ``10.0.0.0/8``). The request then goes to a
    checked address, its name kept for TLS and the Host header: a name can't answer one address to the check and
    another to the connection. Where the operator names a proxy for this machine (HTTPS_PROXY, HTTP_PROXY, and
    NO_PROXY for what it doesn't take), the request goes through it once checked, and the proxy connects."""

    def __init__(self, inside: list[str] = ()):
        self.hosts = {name.lower() for name in inside if "/" not in name}
        self.networks = [ipaddress.ip_network(name, strict=False) for name in inside if "/" in name]
        self.transport = httpx.AsyncHTTPTransport()
        self.proxied = {scheme: httpx.AsyncHTTPTransport(proxy=address)
                        for scheme, address in urllib.request.getproxies().items() if scheme in ("http", "https")}

    def allowed(self, host: str, addresses: list[str]) -> bool:
        return host in self.hosts or all(
            public(address) or any(ipaddress.ip_address(address) in network for network in self.networks)
            for address in addresses)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.raw_host.decode("ascii").lower()
        addresses = await lookup(host, request.url.port or (443 if request.url.scheme == "https" else 80))
        if not addresses:
            raise httpx.ConnectError(f"{host} could not be looked up.", request=request)
        if not self.allowed(host, addresses):
            raise httpx.ConnectError(f"{host} is not a public address; the operator lets a model inside this network "
                                     "be reached by naming it in AI_RUNTIME_INSIDE_HOSTS.", request=request)
        proxied = self.proxied.get(request.url.scheme)
        if proxied is not None and not urllib.request.proxy_bypass(host):
            return await proxied.handle_async_request(request)
        request.extensions["sni_hostname"] = host
        for address in addresses:
            request.url = request.url.copy_with(host=address)
            try:
                return await self.transport.handle_async_request(request)
            except httpx.ConnectError:
                if address == addresses[-1]:
                    raise

    async def aclose(self) -> None:
        for transport in (self.transport, *self.proxied.values()):
            await transport.aclose()
