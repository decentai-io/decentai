from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from server.custom_logging import CustomLoggerFactory
from fastapi.middleware.cors import CORSMiddleware

from server.setup.app_settings import Settings
from server.setup.app_bootstrap import init_state, shutdown_state


def _add_middlewares(app: FastAPI, settings: Settings) -> None:
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

    app.include_router(health_router)
    app.include_router(status_router)

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
        # Reminders fire only while the backend keeps a delegation open
        # for their chat (docs/system/chat-session.md): every chat holding
        # schedules is re-dialed now, so the runtime's clock can adopt
        # its rows. A runtime that is down is simply retried by the next
        # boot — the rows are durable here.
        from server.setup.app_state import get_runtime_clients

        try:
            await get_runtime_clients().redial_scheduled()
        except Exception as exc:
            CustomLoggerFactory.get_logger("app_factory").warning(
                f"Scheduled chats not re-dialed: {exc}")
        # A delegation lives an hour and a kept-open dial may live for
        # days: the manager renews each live dial's credential over the
        # socket before it lapses, for as long as the process runs.
        get_runtime_clients().start_renewing()
        try:
            yield
        finally:
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
    )

    app.state.settings = settings

    _add_middlewares(app, settings)
    _include_routers(app)

    return app
