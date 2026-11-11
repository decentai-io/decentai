"""The people's door: every call on ``POST /app``, and the few routes a call can't take the shape of: a file's bytes,
speech, a webhook, a provider's redirect, the live socket, the probes and the metrics (api-server.md §2, §3, §9)."""

from __future__ import annotations

import asyncio
import hmac
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..chats.socket import Live
from ..people.permissions import Rules
from ..records.files import Files
from ..records.oauth import Connections
from ..runtimes.store import Runtimes
from ..settings.speech import Speech
from ..settings.webhooks import Hooks
from ..storage.database import Database
from ..trail.trail import Trail
from ..utils import MIB, Outside, Settings
from .calls import Caller, Refused, Router
from .callers import Callers


class Guard:
    """No body larger than its route takes, read or declared; and how every answer may be used."""

    HEADERS = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin"),
               (b"x-frame-options", b"DENY")]

    def __init__(self, app: ASGIApp):
        self.app, settings = app, Settings.read()
        self.limits = {"/upload": settings.upload_max + MIB, "/speech": 10 * MIB + MIB, "/hooks/": 64 * 1024}
        self.default = settings.call_max

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        limit = next((v for k, v in self.limits.items() if path.startswith(k)), self.default)
        declared = dict(scope.get("headers") or ()).get(b"content-length", b"0")
        if not declared.isdigit() or int(declared) > limit:
            await self._too_large(send)
            return
        seen = 0

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > limit:
                raise Refused("too_large", "The call is larger than this route takes.")
            return message

        async def headed(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *self.HEADERS]
            await send(message)

        try:
            await self.app(scope, counted, headed)
        except Refused:
            await self._too_large(send)

    @staticmethod
    async def _too_large(send: Send) -> None:
        body = b'{"error": {"code": "too_large", "message": "The call is larger than this route takes."}}'
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})


class Answered(JSONResponse):
    """An answer in JSON, its dates as ISO text."""

    def render(self, content: Any) -> bytes:
        return super().render(jsonable_encoder(content))


class PeopleDoor:
    def __init__(self, router: Router, callers: Callers):
        self.router, self.callers = router, callers

    def app(self, lifespan: Any) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
        settings = Settings.read()
        app.add_middleware(Guard)
        app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_methods=["GET", "POST"],
                           allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])
        app.add_api_route("/app", self.call, methods=["POST"])
        app.add_api_route("/upload", self.upload, methods=["POST"])
        app.add_api_route("/download/{record_id}", self.download, methods=["GET"])
        app.add_api_route("/speech", self.speech, methods=["POST"])
        app.add_api_route("/hooks/{hook_id}", self.hook, methods=["POST"])
        app.add_api_route("/oauth/callback", self.oauth, methods=["GET"])
        app.add_api_route("/healthz", lambda: {"status": "ok"}, methods=["GET"])
        app.add_api_route("/readyz", self.ready, methods=["GET"])
        if settings.metrics_token:
            app.add_api_route("/metrics", self.metrics, methods=["GET"])
        app.add_api_websocket_route("/chats/{chat_id}", self.socket)
        return app

    async def caller(self, request: Request | WebSocket) -> Caller:
        token = request.headers.get("authorization", "")
        token = token[7:].strip() if token.startswith("Bearer ") else ""
        if not token and isinstance(request, WebSocket):
            offered = [p.strip() for p in request.headers.get("sec-websocket-protocol", "").split(",")]
            token = offered[offered.index(Live.PROTOCOL) + 1] if Live.PROTOCOL in offered[:-1] else ""
        return await asyncio.to_thread(self.callers.person, token, request.client.host if request.client else "")

    async def call(self, request: Request) -> Answered:
        try:
            body = await request.json()
        except ValueError:
            return Answered(*Refused("invalid", "The body is not JSON.").answer())
        answer, status = await self.router.route(body, await self.caller(request))
        return Answered(answer, status, headers={"Cache-Control": "no-store"})

    async def _person(self, request: Request, action: str) -> Caller:
        caller = await self.caller(request)
        if caller.kind != "person":
            raise Refused("signed_out", "Sign in first.")
        if await asyncio.to_thread(Rules().allows, caller.person, action) != "allow":
            raise Refused("refused", "You are not allowed to do this.")
        await asyncio.to_thread(self.callers.within_rate, caller)
        return caller

    async def upload(self, request: Request) -> Answered:
        """The file's bytes as the body; its name, and the agent and resource it is of, in the address."""
        caller, query = Caller("anyone"), request.query_params
        try:
            caller = await self._person(request, "files:file:upload")
            of = {"agent": query["agent"], "resource": query["resource"]} if query.get("agent") else None
            answer = (await Files().upload(caller, query.get("name", ""), of, request.stream()), 200)
        except Refused as refused:
            answer = refused.answer()
        except ValueError as invalid:
            answer = Refused("invalid", str(invalid)).answer()
        await asyncio.to_thread(Trail().add, caller, "files:file:upload", dict(query), answer)
        return Answered(*answer)

    async def download(self, request: Request, record_id: str) -> Any:
        try:
            caller = await self._person(request, "files:file:download")
            file, pieces = await asyncio.to_thread(Files().download, caller, record_id)
        except Refused as refused:
            return Answered(*refused.answer())
        return StreamingResponse(pieces, media_type=file["type"], headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file['name'])}",
            "Content-Security-Policy": "sandbox", "Cache-Control": "private, no-store"})

    async def speech(self, request: Request) -> Answered:
        try:
            caller = await self._person(request, "settings:speech:transcribe")
            text = await Outside.run(Speech().transcribe, caller, await request.body(),
                                     request.headers.get("content-type", ""), request.query_params.get("language", ""))
            return Answered({"text": text})
        except Refused as refused:
            return Answered(*refused.answer())

    async def hook(self, request: Request, hook_id: str) -> Answered:
        answer, status = await Hooks().receive(hook_id, request.headers, await request.body())
        return Answered(answer, status)

    async def oauth(self, request: Request) -> RedirectResponse:
        """The provider's redirect: its code kept for the person who started it, who completes it in the app."""
        state, code = request.query_params.get("state", ""), request.query_params.get("code", "")
        parked = bool(state and code) and await asyncio.to_thread(Connections.parked, state, code)
        place = f"/oauth/done?state={quote(state)}" if parked else "/oauth/done?failed=1"
        return RedirectResponse(Settings.read().app_url + place, 303)

    async def ready(self) -> Answered:
        ready = await asyncio.to_thread(Database.ready)
        return Answered({"status": "ready" if ready else "not ready"}, 200 if ready else 503)

    async def metrics(self, request: Request) -> PlainTextResponse:
        """What the runtimes run, in Prometheus' text format, for the operator's token alone."""
        given = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(given, f"Bearer {Settings.read().metrics_token}".encode()):
            return PlainTextResponse("", 401)
        return PlainTextResponse(await asyncio.to_thread(self._metrics), media_type="text/plain; version=0.0.4")

    @staticmethod
    def _metrics() -> str:
        def label(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

        live = Runtimes().live()
        lines = ["# TYPE decentai_runtimes gauge", f"decentai_runtimes {len(live)}",
                 "# TYPE decentai_sandboxes gauge", f"decentai_sandboxes {sum(len(r.get('sandboxes') or []) for r in live)}"]
        agents = sorted(Runtimes.agents().items())
        for field, name, about in (("processes", "processes", "Processes running for an agent."),
                                   ("memory", "memory_bytes", "The memory they hold."),
                                   ("cpu", "cpu_cores", "The processor's cores they used over the last second.")):
            lines += [f"# HELP decentai_agent_{name} {about}", f"# TYPE decentai_agent_{name} gauge"]
            lines += [f'decentai_agent_{name}{{org="{label(org)}",agent="{label(agent)}"}} {counts[field]}'
                      for (org, agent), counts in agents]
        return "\n".join(lines) + "\n"

    async def socket(self, socket: WebSocket, chat_id: str) -> None:
        await Live().serve(socket, chat_id, lambda: self.caller(socket), self.router)
