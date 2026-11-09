"""Reading a manifest the API server has already validated (documentation/agents.md)."""

from __future__ import annotations

import re
from typing import Any, Iterator

#: A host an agent may name: a name, ``*.`` in front for every host under it.
HOST_PATTERN = re.compile(
    r"^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")
#: What an address looks like, which a host may not be.
ADDRESS_PATTERN = re.compile(r"^[0-9.]+$|^\[")


class Manifest:
    """Typed access over a valid manifest document: it never validates and never parses."""

    def __init__(self, document: dict[str, Any]):
        self.document = document

    @property
    def agent_id(self) -> str:
        return self.document["agent"]["id"]

    @property
    def name(self) -> str:
        """What the author called it, or its id."""
        return str(self.document["agent"].get("name") or self.agent_id)

    @property
    def version(self) -> str:
        return self.document["agent"]["version"]

    @property
    def runs(self) -> int:
        """How many runs of it may serve one chat at once: more than one only when it keeps nothing of a chat in
        its process between calls."""
        runs = self.document["agent"].get("runs")
        return runs if isinstance(runs, int) and not isinstance(runs, bool) and 1 <= runs <= 8 else 1

    @property
    def instructions(self) -> str:
        return str(self.document["agent"].get("instructions") or "")

    def examples(self) -> list[dict]:
        """The prompts to try first: [{title, prompt}]."""
        return [
            {"title": str(e.get("title") or ""), "prompt": str(e.get("prompt") or "")}
            for e in (self.document.get("agent") or {}).get("examples") or []
            if isinstance(e, dict)
        ]

    @property
    def network(self) -> dict[str, Any]:
        """``{declared, any, hosts, from_secrets}``: hosts with ``:<port>`` where one was declared, and
        ``<secret>.<field>`` for a host a granted credential names. No block reaches nothing."""
        block = self.document.get("network")
        if not isinstance(block, dict):
            return {"declared": False, "any": False, "hosts": [], "from_secrets": []}
        listed = block.get("hosts")
        if not isinstance(listed, list):
            return {"declared": True, "any": listed == "any",
                    "hosts": [], "from_secrets": []}
        return {
            "declared": True,
            "any": False,
            "hosts": [host for host in listed if isinstance(host, str)],
            "from_secrets": [
                str(host.get("from_secret") or "")
                + (f":{host['port']}" if host.get("port") else "")
                for host in listed if isinstance(host, dict)
            ],
        }

    @property
    def runs_code(self) -> bool:
        """Whether a function says ``code: true``: it may reach, for one call, the hosts an allowed card named."""
        return any(function.get("code") is True
                   for tool in self.document.get("tools") or []
                   if isinstance(tool, dict)
                   for function in tool.get("functions") or []
                   if isinstance(function, dict))

    @property
    def scopes(self) -> dict[str, dict]:
        return dict((self.document.get("authorization") or {}).get("scopes") or {})

    def resources(self, kind: str) -> list[dict]:
        return list((self.document.get("resources") or {}).get(kind) or [])

    def tools(self) -> list[dict]:
        return list(self.document.get("tools") or [])

    def functions(self) -> Iterator[tuple[str, dict, dict]]:
        """(full name, tool, function) for every function."""
        for tool in self.tools():
            for function in tool.get("functions") or []:
                yield f"{self.agent_id}.{tool['id']}.{function['id']}", tool, function

    def function(self, name: str) -> tuple[dict, dict] | None:
        for full, tool, function in self.functions():
            if full == name:
                return tool, function
        return None
