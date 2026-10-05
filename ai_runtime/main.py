import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(Path(__file__).resolve().parent / "config.env", override=False)

from ai_runtime.agents import AgentLibrary, Confinement, Events, Spawner
from ai_runtime.server import create_app
from ai_runtime.server.settings import RuntimeSettings


settings = RuntimeSettings.from_env()

# What agents do is written down from here on, where a person can be
# shown it (docs/system/monitoring.md).
Events.configure(settings.agents_install_dir, "runtime")

# Where agents' processes are started: here, or in a container of
# their own, which starts beside this one and is waited for — what
# follows asks it whether workers can be confined.
spawner = Spawner.configure(settings.agents_spawner, settings.agents_install_dir)
if spawner.remote:
    spawner.wait()

# Whether an agent's worker runs as a user of its own here, found out
# and said before anything is started: the warm-up below verifies
# packages, and verification runs their code.
Confinement.configure(
    settings.agents_install_dir, settings.egress_port,
    allow_loopback=settings.egress_allows_loopback,
    package_hosts=settings.package_hosts)

# The library owns this directory: the code under it, the dependencies
# beside it, and the import path they need.
library = AgentLibrary(settings.agents_install_dir)
library.load_all()
# The environments a deploy left behind are rebuilt now, in the
# background, not by the first person to name each agent.
library.warm_up_in_background()

# The platform the sessions live against: the backend over its /app
# gateway when one is configured (docs/system/chat-session.md), else the sim —
# in-memory, so a standalone runtime converses but remembers nothing
# across restarts.
if settings.backend_url:
    from ai_runtime.services import BackendServices, Gateway

    services = BackendServices(Gateway(settings.backend_url))
else:
    try:
        from sim.session_services import SimSessionServices
    except ImportError:
        # The image carries the runtime and not the stand-in: there, a
        # runtime with no platform to reach is a setting left empty.
        raise SystemExit(
            "BACKEND_INTERNAL_URL is not set. A runtime run alone needs "
            "the repository's sim/ beside it; a deployed one needs the "
            "address of its backend.")

    services = SimSessionServices()

app = create_app(settings, services=services, library=library)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
