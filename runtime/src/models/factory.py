"""Which connector speaks a connection's protocol. Which providers exist is the catalog's to say."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

from shared.llm.providers import LlmProviders
from ..way_out.proxy import WayOut

from .anthropic import Anthropic
from .base import Connector, TextTools
from .bedrock import Bedrock
from .gemini import Gemini
from .openai import OpenAI
from .responses import Responses
from .scripted import Scripted
from .vertex import VertexAnthropic, VertexGemini


class Factory:
    PROTOCOLS = {"openai": OpenAI, "openai-responses": Responses, "anthropic": Anthropic, "gemini": Gemini,
                 "bedrock": Bedrock, "vertex": VertexGemini, "vertex-anthropic": VertexAnthropic}
    #: In no catalog, so no page offers it. Reachable from a chat config on purpose (scripted.py).
    SCRIPTED = {"fake": Scripted}
    #: The providers whose client knows their address when none is given.
    OWN_ADDRESS = ("openai", "anthropic")

    @classmethod
    def create(cls, config: dict[str, Any], session: str = "") -> Connector:
        """A connector for one contract's model block. ``session`` is the chat its requests are grouped by."""
        provider = str(config.get("provider") or "").strip().lower()
        if provider in cls.SCRIPTED:
            return cls.SCRIPTED[provider](config)
        route = LlmProviders.route(provider, config.get("model"), config.get("endpoint"))
        if route is None:
            raise ValueError(f"Unsupported LLM provider '{provider}': it is not in the platform's catalog of "
                             f"providers. A service that speaks OpenAI's protocol is 'openai_compatible'.")
        # Never fall back to OpenAI's public endpoint for another provider.
        if provider not in cls.OWN_ADDRESS and not str(config.get("endpoint") or "").strip():
            raise ValueError(f"{provider} requires endpoint")
        if LlmProviders.unfilled(config.get("endpoint")):
            raise ValueError(f"{provider}'s endpoint still has a blank to fill in ({config.get('endpoint')})")
        connector = cls.PROTOCOLS[route["protocol"]]({**config, "endpoint": route["endpoint"]})
        connector.cache_for(session)
        return TextTools(connector) if config.get("tool_mode") == "text" else connector

    @classmethod
    async def connected(cls, config: dict[str, Any], inside: list[str], session: str = "") -> Connector:
        """A connector whose endpoint is a public address, or one the operator named in AI_RUNTIME_INSIDE_HOSTS."""
        connector = cls.create(config, session)
        parts = urlsplit(getattr(connector, "inner", connector).endpoint)
        host = (parts.hostname or "").lower()
        found = await WayOut.addresses(host, parts.port or 443) if host else []
        if host and not (cls._named(host, found, inside) or found and all(WayOut.public(a) for a in found)):
            await connector.close()
            raise ValueError(f"{host} is not a public address; the operator lets a model inside this network be "
                             f"reached by naming it in AI_RUNTIME_INSIDE_HOSTS.")
        return connector

    @staticmethod
    def _named(host: str, found: list[str], inside: list[str]) -> bool:
        """Whether the operator named the host, or a network holding every address it answers with."""
        networks = [ipaddress.ip_network(n, strict=False) for n in inside if "/" in n]
        return host in inside or bool(found) and all(any(ipaddress.ip_address(a) in n for n in networks) for a in found)
