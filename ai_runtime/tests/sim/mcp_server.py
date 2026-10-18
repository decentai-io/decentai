"""A stand-in MCP server, for tests: the protocol's HTTP transport on a
port of this machine, with a few tools and a resource, answering as
JSON or as an event stream.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List


class FakeMcpServer:
    SESSION = "session-1"

    def __init__(self, token: str = "", stream: bool = False):
        #: the bearer token it wants, or '' for none
        self.token = token
        #: answer as an event stream rather than JSON
        self.stream = stream
        self.tools: List[Dict[str, Any]] = [
            {"name": "echo", "description": "Say back what was said.",
             "inputSchema": {"type": "object", "required": ["text"],
                             "additionalProperties": False,
                             "properties": {"text": {"type": "string"}}}},
            {"name": "Get-Weather", "description": "The weather somewhere.",
             "inputSchema": {"type": "object",
                             "properties": {"city": {"type": "string"}}}},
            {"name": "fail", "description": "Always fails.",
             "inputSchema": {"type": "object"}},
        ]
        self.resources = [{"uri": "note://one", "name": "One",
                           "mimeType": "text/plain"}]
        #: every JSON-RPC message received, in order
        self.received: List[Dict[str, Any]] = []
        #: the headers of the last request
        self.headers: Dict[str, str] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                message = json.loads(self.rfile.read(length) or b"{}")
                outer.headers = {k.lower(): v for k, v in self.headers.items()}
                outer.received.append(message)
                if outer.token and self.headers.get(
                        "Authorization") != f"Bearer {outer.token}":
                    self.send_response(401)
                    self.end_headers()
                    return
                if "id" not in message:
                    self.send_response(202)
                    self.end_headers()
                    return
                answer = {"jsonrpc": "2.0", "id": message["id"],
                          **outer.answer(message)}
                if outer.stream:
                    body = (f"event: message\ndata: {json.dumps(answer)}\n\n"
                            ).encode("utf-8")
                    kind = "text/event-stream"
                else:
                    body = json.dumps(answer).encode("utf-8")
                    kind = "application/json"
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Mcp-Session-Id", outer.SESSION)
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def answer(self, message: Dict[str, Any]) -> Dict[str, Any]:
        method = message.get("method")
        params = message.get("params") or {}
        if method == "initialize":
            return {"result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "Stand-in", "version": "1"}}}
        if method == "tools/list":
            return {"result": {"tools": self.tools}}
        if method == "resources/list":
            return {"result": {"resources": self.resources}}
        if method == "resources/read":
            return {"result": {"contents": [
                {"uri": params.get("uri"), "mimeType": "text/plain",
                 "text": "the first note"}]}}
        if method == "tools/call":
            name, arguments = params.get("name"), params.get("arguments") or {}
            if name == "echo":
                return {"result": {
                    "content": [{"type": "text", "text": arguments.get("text", "")}],
                    "structuredContent": {"said": arguments.get("text", "")}}}
            if name == "Get-Weather":
                return {"result": {"content": [
                    {"type": "text", "text": f"Sunny in {arguments.get('city')}"},
                    {"type": "image", "data": "AAAA", "mimeType": "image/png"}]}}
            if name == "fail":
                return {"result": {"isError": True, "content": [
                    {"type": "text", "text": "it did not work"}]}}
            return {"error": {"code": -32602, "message": f"Unknown tool {name}"}}
        return {"error": {"code": -32601, "message": "Method not found"}}
