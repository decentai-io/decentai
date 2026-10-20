from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fastapi.middleware.cors import CORSMiddleware

from server.setup.app_settings import Settings
from server.setup.app_bootstrap import init_state, shutdown_state


class SecurityHeadersMiddleware:
    """What every answer says about how it may be used: never guess a
    content type, send no address to another site, never be shown in
    another page's frame. The engine is a headless API with nothing of
    its own in front of it, so these are its to send, on every HTTP
    response; a WebSocket has no such headers. The page's own policy
    (what it may load) is the web client's to send with its pages."""

    HEADERS = (
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "same-origin"),
        ("X-Frame-Options", "DENY"),
    )

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.HEADERS:
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodyLimitMiddleware:
    """No request body larger than its route can use. The engine is
    headless and assumes nothing in front of it caps a body, while a
    body is read whole before anything looks at it — so an anonymous
    sign-in carrying gigabytes would be held in memory. A body that
    says its length is refused before it is read (413); one that does
    not say is counted, and the connection is ended where it passes the
    limit."""

    #: Sign-in and an outside system's webhook: small JSON, from anybody.
    SMALL = 64 * 1024
    SMALL_PREFIXES = ("/auth/", "/hooks/")

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.upload = (int(getattr(settings, "max_upload_mb", 25)) + 1) * 1024 * 1024
        self.other = int(getattr(settings, "max_request_mb", 32)) * 1024 * 1024

    def limit(self, path: str) -> int:
        if path.startswith(self.SMALL_PREFIXES):
            return self.SMALL
        if path == "/upload" or path.startswith("/upload/"):
            # The file, and its multipart framing.
            return self.upload
        return self.other

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit(scope.get("path", ""))
        for name, value in scope.get("headers", ()):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = limit + 1
                if declared > limit:
                    await self._too_large(send, limit)
                    return
                break

        seen = 0

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    # Past the limit with no length said: the request
                    # is over, as if the caller had gone.
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, counted, send)

    @staticmethod
    async def _too_large(send: Send, limit: int) -> None:
        body = (b'{"error": "The request is too large: %d KB at most."}'
                % (limit // 1024))
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def _add_middlewares(app: FastAPI, settings: Settings) -> None:
    app.add_middleware(BodyLimitMiddleware, settings=settings)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
        expose_headers=["Content-Type", "Authorization"],
    )


def _include_routers(app: FastAPI) -> None:
    from server.routes.auth import router as authentication_router
    from server.routes.ws import router as ws_router
    from server.routes.app import router as app_router
    from server.routes.upload import router as upload_router
    from server.routes.download import router as download_router
    from server.routes.oauth import router as oauth_router
    from server.routes.health import router as health_router
    from server.routes.status import router as status_router
    from server.routes.hooks import router as hooks_router
    from server.routes.nodes import router as nodes_router

    app.include_router(health_router)
    # Where execution nodes connect, each over one link
    # (docs/system/execution-nodes.md).
    app.include_router(nodes_router)
    app.include_router(status_router)
    # Outside systems' events for a person's webhooks: no session,
    # the webhook's own secret (docs/system/webhooks.md).
    app.include_router(hooks_router)

    # Sign-in, invitations, password resets and the session itself:
    # outside the gateway, since they are how a session is obtained.
    app.include_router(authentication_router)

    app.include_router(ws_router)
    app.include_router(upload_router)
    app.include_router(download_router)
    app.include_router(oauth_router)
    app.include_router(app_router)


def create_app() -> FastAPI:
    settings = Settings.from_env()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Execution nodes connect to the Data Core, and the relay
        # carries chats over their links: a node that connects is given
        # the chats that wait for one (chat_session/relay.py). A
        # delegation lives an hour and a chat may be served for days:
        # the relay renews each credential over the link before it
        # lapses, for as long as the process runs.
        from server.setup.app_state import get_runtime_clients

        get_runtime_clients().start_renewing()
        # Notifications to other systems are posted from their queue,
        # retried as their receivers allow (server/outbound.py).
        from server.outbound import Sender

        sender = Sender()
        sender.start()
        try:
            yield
        finally:
            sender.stop()
            await shutdown_state()

    init_state(settings)

    # Surface key problems at boot, not on the first secret operation —
    # including the dev-key fallback's do-not-deploy-this-way warning.
    from database.crypto import SecretCipher

    SecretCipher.verify()

    app = FastAPI(
        title="DecentAI",
        version="1.0.0",
        lifespan=lifespan,
        # No generated API pages: the engine's API is one gateway
        # (docs/reference/api.md), and these would describe only its
        # doors, to anybody who asked.
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    app.state.settings = settings

    _add_middlewares(app, settings)
    _include_routers(app)

    return app
