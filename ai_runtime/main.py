"""The runtime's start: its settings, the parts, and the server
(docs/system/architecture.md).

    python ai_runtime/main.py

The parts are built here and started by the application, on its loop,
in the order each needs the one before (door/app.py): where what agents
do is written down; the Execution Sandboxes, which register with this
AI Runtime; confinement, and the proxy that is the agents' one way out;
the library of approved code; the Data Core's client; and the
application that serves chats with all of them.
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
from ai_runtime.agents.sandbox import ExecutionSandbox
from ai_runtime.agents.library import AgentLibrary
from ai_runtime.agents.sandboxes import Sandboxes
from sandbox.events import Events
from ai_runtime.door.app import create_app
from ai_runtime.door.node import DataCoreKey, NodeCredential
from ai_runtime.door.platform import DataCoreClient, Gateway
from ai_runtime.door.settings import RuntimeSettings


settings = RuntimeSettings.from_env()

# What agents do is written down from here on, where a person can be
# shown it (docs/system/monitoring.md).
Events.configure(settings.agents_install_dir, "runtime")

# Where every process of an agent's is started: the Execution
# Sandboxes, which register with this runtime, proving the node's
# secret. The runtime starts nothing of an agent's itself. One named by
# its address (AI_RUNTIME_SANDBOX_ADDRESS) is known besides them.
container = Sandboxes(settings.sandbox_secret, port=settings.sandbox_port)
if settings.sandbox_address:
    container.add(ExecutionSandbox(settings.sandbox_address,
                                   settings.sandbox_secret))

# What holds a worker there, as this side sees it: the proxy as its
# one way out, and the container's own proof at start.
confinement = Confinement(container, package_hosts=settings.package_hosts)

# The library owns the install directory: the code under it, the
# environments beside it, and the import path they need.
library = AgentLibrary(settings.agents_install_dir, confinement)

# Who this execution node is: a credential an authority signed, read
# from where its operator puts it, and read again before it lapses —
# and who the Data Core is: its public key, which every reply must be
# signed with (door/node.py, docs/system/execution-nodes.md).
node = NodeCredential(file=settings.node_credential_file,
                      issuer_url=settings.node_credential_url)
data_core = DataCoreKey(settings.data_core_service_public_key)
# Who this runtime is to its sandboxes, which a pool's serve several
# runtimes: its node, and where its proxy answers their workers.
container.node = node
container.proxy_port = settings.egress_port

# The platform the sessions live against: the Data Core, over its /app
# gateway (docs/system/chat-session.md) — and what this node asks as
# itself: the approved packages, to stock itself with
# (docs/system/agent-code.md).
services = DataCoreClient(Gateway(settings.data_core_urls or settings.data_core_url,
                                  node=node, key=data_core),
                          node)

app = create_app(settings, services=services, library=library,
                 container=container, confinement=confinement,
                 node=node, data_core=data_core)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
