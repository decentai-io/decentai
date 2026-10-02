import asyncio
import logging
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Configuration comes from the process environment — the deployment sets it
# (docker compose env_file, ECS task definition, K8s manifest). For local
# development, seed os.environ from ./config.env if present; values already
# in the real environment always win. Loaded before any app import so every
# module sees the same environment.
load_dotenv("config.env", override=False)

from server.setup.app_factory import create_app


if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


app = create_app()


def disable_uvicorn_logs():
    for logger_name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi",
    ):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = False
        logger.disabled = True


if __name__ == "__main__":
    disable_uvicorn_logs()

    # The settings the app was built with — read once, in one place,
    # rather than a second look at the environment here.
    settings = app.state.settings

    uvicorn.run(
        app,
        host=settings.backend_host,
        port=settings.backend_port,
        loop="asyncio",
        ws="websockets",
        log_level="critical",
        access_log=False,
        # Behind a reverse proxy, X-Forwarded-* from trusted hops is
        # the real client — session IPs and the login lockout depend on it.
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
    )
