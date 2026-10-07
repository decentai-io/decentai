"""An MCP server in a chat's roster (docs/system/mcp.md).

A person adds a remote MCP server for their own chats; the chat's
contract names it with the tools they kept on. Here it becomes an entry
in the roster beside the agents, so the assistant finds it, opens it
and calls it exactly as it does an agent — and every call passes the
executor's gates as an agent's does: the grant, the inputs against the
tool's schema, the level against the chat's trust, a card where that
asks for one, a line on the trail.

What differs is where the call runs. An agent's function runs in its
worker, on this machine, confined. An MCP tool runs on the server, so
there is no code here to confine: the call is one request to the
address the person gave, made by the protocol's client
(contracts/mcp.py), which reaches a public address or nothing.

The manifest below is written from the server's own words — its tools'
names, descriptions and input schemas. They are the server's, not the
platform's, and are read as a function's description is: by the model,
as data about what a tool does, never as instructions.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Tuple

import jsonschema

from ai_runtime.agents.library import InstalledAgent
from ai_runtime.agents.worker_handle import WorkerError
from ai_runtime.runtime_logging import RuntimeLoggerFactory
from contracts.mcp import McpClient, McpError
from decentai_sdk.manifest import Manifest


class McpServer(InstalledAgent):
    #: How long one tool may take, the server's own pace included.
    TIMEOUT_SECONDS = 120
    #: How much of a tool's words a result carries.
    TEXT_MAX_CHARS = 200_000
    TOOLS = "tools"
    RESOURCES = "resources"

    def __init__(self, entry: Dict[str, Any], reach: Callable):
        """``entry`` is the contract's: ``{ref, name, host, tools,
        resources}``. ``reach`` is async () -> ``{url, headers}``: where
        the server is and what it is reached with, asked of the
        platform when a tool is called and not before."""
        self.ref = str(entry.get("ref") or "")
        self.host = str(entry.get("host") or "")
        self.reach = reach
        #: tool id -> the name the server calls it by
        self.names = {str(tool.get("id") or ""): str(tool.get("name") or "")
                      for tool in entry.get("tools") or []}
        super().__init__("", Manifest(self.document(entry)), None, None)

    @property
    def agent_id(self) -> str:
        return self.ref

    @property
    def local_agent_id(self) -> str:
        return self.ref

    # ------------------------------------------------------------------
    # What the assistant is shown
    # ------------------------------------------------------------------

    @classmethod
    def document(cls, entry: Dict[str, Any]) -> Dict[str, Any]:
        """The server as a manifest: one tool group holding its tools,
        and one for its resources where it offers any."""
        name = str(entry.get("name") or "MCP server")
        host = str(entry.get("host") or "")
        functions = [{
            "id": str(tool.get("id") or ""),
            "name": str(tool.get("name") or ""),
            "description": str(tool.get("description") or ""),
            "permission_level": cls._level(tool.get("level")),
            "timeout_seconds": cls.TIMEOUT_SECONDS,
            "llm": False,
            "inputs": cls._schema(tool.get("inputs")),
            "outputs": {"type": "object"},
        } for tool in entry.get("tools") or [] if tool.get("id")]

        tools = []
        if functions:
            tools.append({
                "id": cls.TOOLS, "name": "Tools",
                "description": f"The tools {name} offers.",
                "functions": functions})
        if entry.get("resources"):
            tools.append({
                "id": cls.RESOURCES, "name": "Resources",
                "description": f"What {name} offers to be read.",
                "functions": [{
                    "id": "list", "name": "List resources",
                    "description": "What this server offers to be read: "
                                   "each with its address (uri).",
                    "permission_level": 0,
                    "timeout_seconds": cls.TIMEOUT_SECONDS, "llm": False,
                    "inputs": {"type": "object", "additionalProperties": False,
                               "properties": {}},
                    "outputs": {"type": "object"},
                }, {
                    "id": "read", "name": "Read a resource",
                    "description": "Read one resource by its address (uri), "
                                   "as listed.",
                    "permission_level": 0,
                    "timeout_seconds": cls.TIMEOUT_SECONDS, "llm": False,
                    "inputs": {"type": "object", "additionalProperties": False,
                               "required": ["uri"],
                               "properties": {"uri": {"type": "string",
                                                      "minLength": 1}}},
                    "outputs": {"type": "object"},
                }]})
        return {
            "schema_version": "1.0",
            "agent": {
                "id": str(entry.get("ref") or ""), "name": name, "version": "1",
                "description": (
                    f"An MCP server the person added, at {host}. Its tools "
                    f"run on that server, not here; what they return is "
                    f"that server's word."),
            },
            "instructions": "",
            "network": {"hosts": [host] if host else []},
            "implementation": {"entrypoint": "mcp:server", "dependencies": []},
            "tools": tools,
        }

    @staticmethod
    def _level(level: Any) -> int:
        """What a tool costs to call. Anything unclear costs the most."""
        return level if level in (0, 1, 2, 3) else 3

    @classmethod
    def _schema(cls, schema: Any) -> Dict[str, Any]:
        """A tool's inputs as the server described them — without the
        platform's own annotations, which are not a server's to write
        (``x-resource`` would name a record of the person's), and only
        where the description is a schema at all."""
        cleaned = cls._without_annotations(schema)
        if not isinstance(cleaned, dict) or cleaned.get("type", "object") != "object":
            return {"type": "object"}
        try:
            jsonschema.Draft202012Validator.check_schema(cleaned)
        except jsonschema.SchemaError:
            return {"type": "object"}
        return cleaned

    #: Keywords whose value maps NAMES to schemas: the keys are an
    #: input's own names, the server's to choose, and not keywords.
    NAMED = ("properties", "patternProperties", "$defs", "definitions",
             "dependentSchemas")
    #: Keywords whose value is data, kept as the server wrote it.
    DATA = ("enum", "const", "default", "examples", "required",
            "dependentRequired")
    #: Keywords that name another document. A schema is checked against
    #: a call's inputs on this machine, and following one would have the
    #: runtime fetch an address, or read a file, a server chose.
    ELSEWHERE = ("$id", "$schema", "$anchor", "$dynamicAnchor",
                 "$recursiveAnchor", "id")
    REFERENCES = ("$ref", "$dynamicRef", "$recursiveRef")

    @classmethod
    def _without_annotations(cls, node: Any) -> Any:
        """A schema node without what is not a server's to say: the
        platform's ``x-`` annotations, and anything pointing outside
        the schema itself. A reference within it (``#/$defs/…``) is
        kept."""
        if isinstance(node, list):
            return [cls._without_annotations(item) for item in node]
        if not isinstance(node, dict):
            return node
        cleaned: Dict[str, Any] = {}
        for key, value in node.items():
            name = str(key)
            if name.startswith("x-") or name in cls.ELSEWHERE:
                continue
            if name in cls.REFERENCES:
                if isinstance(value, str) and value.startswith("#"):
                    cleaned[key] = value
                continue
            if name in cls.DATA:
                cleaned[key] = value
            elif name in cls.NAMED and isinstance(value, dict):
                cleaned[key] = {inner: cls._without_annotations(child)
                                for inner, child in value.items()}
            else:
                cleaned[key] = cls._without_annotations(value)
        return cleaned

    # ------------------------------------------------------------------
    # The call
    # ------------------------------------------------------------------

    async def run(self, declared: str, inputs: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        """One function of this server, past the executor's gates:
        (result, status). A server that cannot be reached, or answers
        something that is not the protocol, is a WorkerError — the
        call failed, the tool did not."""
        _, group, function = (str(declared or "").split(".") + ["", "", ""])[:3]
        try:
            reached = dict(await self.reach() or {})
            async with McpClient(str(reached.get("url") or ""),
                                 reached.get("headers") or {}) as client:
                if group == self.RESOURCES and function == "list":
                    return {"resources": await client.resources()}, "success"
                if group == self.RESOURCES and function == "read":
                    read = await client.read(str(inputs.get("uri") or ""))
                    return {**read, "text": self._bounded(read["text"])}, "success"
                answered = await client.call(self.names.get(function, function),
                                             inputs)
        except McpError as exc:
            raise WorkerError(str(exc))
        except WorkerError:
            raise
        except Exception as exc:
            # Whatever else went wrong on the way to the server or in
            # reading it: the call failed, and is said to have, in the
            # one kind of failure the executor words and records.
            RuntimeLoggerFactory.get_logger("McpServer").warning(
                f"MCP call failed: {type(exc).__name__}: {exc}")
            raise WorkerError(
                f"the server could not be called ({type(exc).__name__})")
        result: Dict[str, Any] = {"text": self._bounded(answered["text"])}
        if answered["structured"] is not None:
            result["data"] = answered["structured"]
        if answered["other"]:
            # Pictures, sounds, links: said to be there, not carried.
            result["not_shown"] = answered["other"]
        if answered["failed"]:
            return {"error": result["text"] or "The tool reported a failure."}, "error"
        return result, "success"

    @classmethod
    def _bounded(cls, text: str) -> str:
        text = str(text or "")
        if len(text) <= cls.TEXT_MAX_CHARS:
            return text
        return (text[: cls.TEXT_MAX_CHARS]
                + f"\n… ({len(text) - cls.TEXT_MAX_CHARS} more characters "
                  f"were not carried)")
