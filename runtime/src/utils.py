"""What every part uses and none owns: the runtime's settings, and what is a public address
(documentation/runtime.md §9)."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket

NAT64 = ipaddress.ip_network("64:ff9b::/96")


def public(address: str) -> bool:
    """Of the public internet only, an IPv4 address inside an IPv6 one (mapped, 6to4, Teredo, NAT64) judged by what is
    inside."""
    ip = ipaddress.ip_address(address)
    if ip.version == 6:
        ip = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None) or (
            ipaddress.ip_address(int(ip) & 0xFFFFFFFF) if ip in NAT64 else ip)
    return ip.is_global and not ip.is_multicast


async def lookup(host: str, port: int) -> list[str]:
    """The addresses a name answers with, looked up once, in the order to try them; none when it can't be."""
    try:
        found = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    return list(dict.fromkeys(info[4][0] for info in found))


class SettingsRefused(ValueError):
    """A setting that can't be used: what to set."""


class Settings:
    """Every AI_RUNTIME_* variable."""

    ADDRESS = re.compile(r"(?:[A-Za-z0-9.\-]+|\[[0-9A-Fa-f:.]+\]):([0-9]{1,5})")

    def __init__(self, environment: dict[str, str]):
        self.environment = environment
        self.address = self._address(self._text("AI_RUNTIME_ADDRESS"), "AI_RUNTIME_ADDRESS")
        self.api_servers = [self._address(a, "AI_RUNTIME_API_SERVERS")
                            for a in dict.fromkeys(self._text("AI_RUNTIME_API_SERVERS").replace(",", " ").split())]
        if not self.api_servers:
            raise SettingsRefused("AI_RUNTIME_API_SERVERS: the API servers it serves, each as host:port.")
        self.identity = self._text("AI_RUNTIME_IDENTITY", "/run/decentai/runtime")
        self.proxy_port = self._number("AI_RUNTIME_PROXY_PORT", 8005, 1, 65535)
        self.idle_seconds = self._number("AI_RUNTIME_AGENT_PROCESS_IDLE_MINUTES", 12, 1, 1440) * 60
        self.ready_seconds = self._number("AI_RUNTIME_AGENT_READY_SECONDS", 60, 1, 3600)
        self.cancel_seconds = self._number("AI_RUNTIME_AGENT_CANCEL_SECONDS", 5, 1, 3600)
        self.ask_hours = self._number("AI_RUNTIME_ASK_HOURS", 24, 1, 720)
        self.connections_max = self._number("AI_RUNTIME_AGENT_CONNECTIONS_MAX", 128, 1, 100000)
        self.chats_max = self._number("AI_RUNTIME_CHATS_MAX", 500, 1, 100000)
        #: Model hosts inside this network that may be reached anyway: names, or networks such as 10.0.0.0/8.
        self.inside_hosts = [host.lower() for host in self._text("AI_RUNTIME_INSIDE_HOSTS").replace(",", " ").split()]
        for network in [host for host in self.inside_hosts if "/" in host]:
            try:
                ipaddress.ip_network(network, strict=False)
            except ValueError:
                raise SettingsRefused(f"AI_RUNTIME_INSIDE_HOSTS: '{network}' is not a network.") from None
        self.log_level = self._text("AI_RUNTIME_LOG_LEVEL", "INFO").upper()
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise SettingsRefused("AI_RUNTIME_LOG_LEVEL is DEBUG, INFO, WARNING or ERROR.")

    def _text(self, name: str, default: str = "") -> str:
        return self.environment.get(name, "").strip() or default

    def _address(self, value: str, name: str) -> str:
        found = self.ADDRESS.fullmatch(value)
        if found is None or not 1 <= int(found.group(1)) <= 65535:
            raise SettingsRefused(f"{name}: host:port.")
        return value

    def _number(self, name: str, default: int, low: int, high: int) -> int:
        text = self._text(name, str(default))
        if not text.isdigit() or not low <= int(text) <= high:
            raise SettingsRefused(f"{name}: '{text}' is not a whole number from {low} to {high}.")
        return int(text)

