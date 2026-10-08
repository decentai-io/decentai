import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI

from ai_runtime.chat.scheduler import ScheduleRunner, Scheduler
from ai_runtime.server.host import ServingRoster, SessionHost
from ai_runtime.server.routes import router
from ai_runtime.server.settings import RuntimeSettings


#: How long shutdown waits for the workers to leave before ending them.
WORKERS_STOP_SECONDS = 20.0


def create_app(settings: RuntimeSettings | None = None, services=None,
               library=None, agents=None) -> FastAPI:
    """``services`` is the platform surface the sessions live against
    (docs/reference/session-door.md) — the backend's client, or the sim.
    Without one the chat door refuses politely and every other door
    serves as always.

    ``library`` is the live AgentLibrary — what code this host has, and
    the only thing that may change it. A test that only needs certain
    agents present may pass a plain ``agents`` mapping instead; the
    library keys by package digest, so a stub's keys are its addresses."""
    settings = settings or RuntimeSettings.from_env()
    if library is None:
        # A mapping is enough for the doors that only read what is
        # serving; without a library nothing can be installed, which is
        # what a test that passes a plain dict is asking for.
        library = SimpleNamespace(
            loaded=lambda: agents if agents is not None else {},
        )

    host = scheduler = None
    if services is not None:
        host = SessionHost(services, library)
        # The platform's own voice, beside the host: the clock. Its
        # fires are deterministic — gated by the manifest's word
        # (schedulable), never by a model. A schedule fires as its
        # chat, with an executor the host builds from that chat's
        # contract (host.fire_context). What wakes a mind goes through
        # the inside door a socket cannot reach.
        # No executor of the clock's own: the host builds one for each
        # fire, held to that chat's grants, on the process's one pool.
        scheduler = Scheduler(
            services.schedules,
            ScheduleRunner(ServingRoster(host), None, host.deliver_event),
        )
        host.clock = scheduler

    @asynccontextmanager
    async def lifespan(app):
        if scheduler is not None:
            await scheduler.start()
        yield
        if scheduler is not None:
            await scheduler.stop()
        # Abandonment, by design: every mind is durable to its last
        # beat, so shutdown cancels the pumps and walks away — the next
        # process hydrates exactly what a crash would have left.
        if host is not None:
            host.shutdown()
            # The workers are not abandoned: each is asked to leave and
            # then ended, and what it was given to run with — its spool
            # folder, its place — is taken back. Bounded, and ended
            # outright where asking takes too long.
            try:
                await asyncio.wait_for(
                    host.workers.stop(), WORKERS_STOP_SECONDS)
            except Exception:
                host.workers.terminate()
        close = getattr(getattr(services, "gateway", None), "close", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass

    app = FastAPI(
        title="DecentAI Private Runtime", version="1.0.0",
        docs_url=None, redoc_url=None, openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.host = host
    app.state.scheduler = scheduler
    app.state.library = library

    app.include_router(router)

    return app
