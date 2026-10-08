"""Typed access over a VALIDATED manifest document.

The document arrives as plain JSON-shaped data — in the runtime host it
was read from yaml and validated by contracts/agent_manifest.py; in a
worker it arrives over the handshake, already validated. This class
never validates and never parses: it answers questions about a document
somebody else has already proven.

It lives in the SDK, not in contracts, because agent code (AgentBase)
needs it inside a worker where only the SDK is importable — and one
implementation serves both sides, so they cannot drift.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Tuple


class Manifest:
    """Typed access over a VALIDATED manifest document."""

    def __init__(self, document: Dict[str, Any]):
        self.document = document

    # -- agent -----------------------------------------------------------
    @property
    def agent_id(self) -> str:
        return self.document["agent"]["id"]

    @property
    def name(self) -> str:
        """What the author called it, for a person to read. Falls back to
        the id, which is at least something the author chose."""
        return str(self.document["agent"].get("name") or self.agent_id)

    @property
    def version(self) -> str:
        return self.document["agent"]["version"]

    @property
    def instructions(self) -> str:
        return str(self.document["agent"].get("instructions") or "")

    def examples(self) -> List[dict]:
        """The prompts to try first, as declared: [{title, prompt}]."""
        return [
            {"title": str(e.get("title") or ""), "prompt": str(e.get("prompt") or "")}
            for e in (self.document.get("agent") or {}).get("examples") or []
            if isinstance(e, dict)
        ]

    # -- implementation --------------------------------------------------
    @property
    def entrypoint(self) -> Tuple[str, str]:
        module, _, class_name = self.document["implementation"]["entrypoint"].partition(":")
        return module, class_name

    @property
    def dependencies(self) -> List[str]:
        return list(self.document["implementation"].get("dependencies") or [])

    # -- network -----------------------------------------------------------
    @property
    def network(self) -> Dict[str, Any]:
        """The hosts the agent connects to, as it declared them:

            declared      whether the manifest says anything at all
            any           every host, where the manifest said so
            hosts         the names, a leading ``*.`` for every host under
                          one, and ``:<port>`` where a port was declared
            from_secrets  ``<secret>.<field>``: hosts a granted credential
                          names, with ``:<port>`` where one was declared

        A valid manifest always declares; a document with no block —
        one that never passed validation — reaches nothing."""
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
        """Whether a function of this agent runs code it puts before
        the person (``code: true``) — and so reaches, for one call, the
        hosts a card they allowed named."""
        return any(function.get("code") is True
                   for tool in self.document.get("tools") or []
                   if isinstance(tool, dict)
                   for function in tool.get("functions") or []
                   if isinstance(function, dict))

    # -- authorization / resources ---------------------------------------
    @property
    def scopes(self) -> Dict[str, dict]:
        return dict((self.document.get("authorization") or {}).get("scopes") or {})

    def resources(self, kind: str) -> List[dict]:
        return list((self.document.get("resources") or {}).get(kind) or [])

    # -- tools / functions -----------------------------------------------
    def tools(self) -> List[dict]:
        return list(self.document.get("tools") or [])

    def functions(self) -> Iterator[Tuple[str, dict, dict]]:
        """Yield (canonical_name, tool, function) for every function."""
        for tool in self.tools():
            for function in tool.get("functions") or []:
                yield (
                    f"{self.agent_id}.{tool['id']}.{function['id']}",
                    tool,
                    function,
                )

    def function(self, canonical_name: str) -> Optional[Tuple[dict, dict]]:
        for name, tool, function in self.functions():
            if name == canonical_name:
                return tool, function
        return None
