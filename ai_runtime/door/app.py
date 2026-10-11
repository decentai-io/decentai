"""The application: the parts main.py built, started on the loop in
the order each needs the one before, and stopped in the reverse order
(docs/system/architecture.md).

    app = create_app(settings, services, library, container, confinement)

At start: the agents' container answers; confinement is proved there
and the proxy, the agents' one way out, serves; the library registers
what is on disk and rebuilds, in the background, what waits for an
environment; the runtime starts keeping its stock of every approved
package; the clock starts. At shutdown:
the warm-up and the stocking are cancelled, the clock stops, the
workers are asked to leave and then ended, the proxy stops, and the
backend's client closes. Everything runs on the
one loop the server gives the application.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from ai_runtime.agents.pool import WorkerPool
from ai_runtime.chat.clock import Scheduler
from ai_runtime.door import monitor, socket
from ai_runtime.door.host import SessionHost
from ai_runtime.settings import RuntimeSettings

#: The runtime's doors: the chat socket, and the monitoring and health
#: endpoints the backend reads.
router = APIRouter()
router.include_router(socket.router)
router.include_router(monitor.router)


#: How long shutdown waits for the workers to leave before ending them.
WORKERS_STOP_SECONDS = 20.0


class AgentsOnly:
    """What a test that only needs certain agents present hands in
    place of a library: the mapping serves, and nothing is installed,
    registered or built."""

    def __init__(self, agents):
        self._agents = agents if agents is not None else {}

    def loaded(self):
        return self._agents

    def load_all(self) -> None:
        pass

    async def warm_up(self) -> int:
        return 0


def create_app(settings: RuntimeSettings, services, library, container,
               confinement, agents=None) -> FastAPI:
    """The application, from the parts main.py built: ``services`` is
    the backend's client, the platform surface the sessions live
    against (docs/reference/session-door.md); ``library`` the live
    AgentLibrary, what code this host has and the only thing that may
    change it; ``container`` the agents' container, where every worker
    runs; ``confinement`` what holds a worker there. A test that only
    needs certain agents present may pass a plain ``agents`` mapping
    in place of a library."""
    if library is None:
        library = AgentsOnly(agents)

    host = scheduler = None
    if services is not None:
        host = SessionHost(services, library, WorkerPool(confinement))
        # The platform's own voice, beside the host: the clock. Its
        # fires are deterministic — gated by the manifest's word
        # (schedulable), never by a model. A schedule fires as its
        # chat, with an executor the host builds from that chat's
        # contract (host.fire_context). What wakes a mind goes through
        # the inside door a socket cannot reach.
        # No executor of the clock's own: the host builds one for each
        # fire, held to that chat's grants, on the process's one pool.
        scheduler = Scheduler(services, host)
        host.clock = scheduler

    @asynccontextmanager
    async def lifespan(app):
        if not await container.wait():
            raise RuntimeError(
                f"The agents' container at {container.host}:{container.port} "
                f"does not answer. The runtime starts nothing of an agent's "
                f"itself.")
        # Proved before anything is started: the warm-up below verifies
        # packages, and verification runs their code. Only what this
        # application started is stopped at the end: a test hands in a
        # confinement it started itself, on a loop of its own.
        started_the_confinement = confinement.proxy is None
        await confinement.start(
            settings.egress_port, allow_loopback=settings.egress_allows_loopback)
        # The library owns the install directory: the code under it
        # and the environments beside it. The environments a deploy
        # left behind are rebuilt now, in the background, not by the
        # first person to name each agent.
        library.load_all()
        warming = asyncio.ensure_future(library.warm_up())
        # The runtime keeps its stock of every approved package from
        # here on, as itself (door/roster.py, keep_stocked).
        stocking = None
        if host is not None:
            stocking = asyncio.ensure_future(host.agents.keep_stocked())
        if scheduler is not None:
            await scheduler.start()
        yield
        for task in (warming, stocking):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
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
                await host.workers.terminate()
        if started_the_confinement:
            await confinement.stop()
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
    app.state.container = container
    app.state.confinement = confinement

    app.include_router(router)

    return app
