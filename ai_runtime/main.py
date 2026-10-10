"""The runtime's start: its settings, the parts built in order, and
the server (docs/system/architecture.md).

    python ai_runtime/main.py

What is built, in the order each needs the one before: where agents
did things are written down; the agents' container, waited for;
confinement, proved there; the proxy, the agents' one way out; the
library of installed code; the backend's client; and the application
that serves chats with all of them.
"""

import logging
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(Path(__file__).resolve().parent / "config.env", override=False)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s")

from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.container import AgentsContainer
from ai_runtime.agents.library import AgentLibrary
from ai_runtime.door.app import create_app
from ai_runtime.events import Events
from ai_runtime.services.backend import BackendServices, Gateway
from ai_runtime.settings import RuntimeSettings


settings = RuntimeSettings.from_env()

# What agents do is written down from here on, where a person can be
# shown it (docs/system/monitoring.md).
Events.configure(settings.agents_install_dir, "runtime")

# Where every process of an agent's is started: the agents' container,
# which starts beside this one and is waited for.
container = AgentsContainer(settings.agents_container, settings.agents_install_dir)
if not container.wait():
    raise SystemExit(
        f"The agents' container at {settings.agents_container} does not "
        f"answer. The runtime starts nothing of an agent's itself.")

# Whether a worker there runs as a user of its own, proved before
# anything is started: the warm-up below verifies packages, and
# verification runs their code. Then the proxy, the one way out.
confinement = Confinement(
    settings.agents_install_dir, container, package_hosts=settings.package_hosts)
confinement.check()
confinement.open_the_way_out(
    settings.egress_port, allow_loopback=settings.egress_allows_loopback)

# The library owns the install directory: the code under it, the
# environments beside it, and the import path they need. The
# environments a deploy left behind are rebuilt now, in the
# background, not by the first person to name each agent.
library = AgentLibrary(settings.agents_install_dir, confinement)
library.load_all()
library.warm_up_in_background()

# The platform the sessions live against: the backend, over its /app
# gateway (docs/system/chat-session.md).
services = BackendServices(Gateway(settings.backend_url))

app = create_app(settings, services=services, library=library,
                 container=container, confinement=confinement)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
