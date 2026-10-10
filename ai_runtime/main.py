"""The runtime's start: its settings, the parts, and the server
(docs/system/architecture.md).

    python ai_runtime/main.py

The parts are built here and started by the application, on its loop,
in the order each needs the one before (door/app.py): where what agents
do is written down; the agents' container; confinement, and the proxy
that is the agents' one way out; the library of installed code; the
backend's client; and the application that serves chats with all of
them.
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
# which starts beside this one. The runtime starts nothing of an
# agent's itself.
container = AgentsContainer(settings.agents_container, settings.agents_install_dir)

# What holds a worker there: a user of its own, and the proxy as its
# one way out.
confinement = Confinement(
    settings.agents_install_dir, container, package_hosts=settings.package_hosts)

# The library owns the install directory: the code under it, the
# environments beside it, and the import path they need.
library = AgentLibrary(settings.agents_install_dir, confinement)

# The platform the sessions live against: the backend, over its /app
# gateway (docs/system/chat-session.md).
services = BackendServices(Gateway(settings.backend_url))

app = create_app(settings, services=services, library=library,
                 container=container, confinement=confinement)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
