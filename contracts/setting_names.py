"""Settings renamed with the engine's parts, read by today's name and,
where that is not set, by the names an older install still uses.

The Data Core was once called the backend, and a deploy.env written
then says BACKEND_SERVICE_PRIVATE_KEY where today's says
DATA_CORE_SERVICE_PRIVATE_KEY. Every place that reads one of these
settings asks here, so an older name is honoured in one place and only
where the newer one is not set.

It renames nothing in a file: bootstrap/setup.py does that, once, for
an install on one computer. The settings only Compose reads (the
Execution Sandbox's SANDBOX_MEMORY, SANDBOX_CPUS, SANDBOX_LABELS) fall
back to their older names in docker-compose.yml itself.
"""

from __future__ import annotations

import os
from typing import Dict, Tuple

#: Each renamed setting, and the older names read where it is not set,
#: the more recent first.
OLDER_NAMES: Dict[str, Tuple[str, ...]] = {
    "DATA_CORE_SERVICE_PRIVATE_KEY": ("DATA_CORE_PRIVATE_KEY",
                                      "BACKEND_SERVICE_PRIVATE_KEY"),
    "DATA_CORE_SERVICE_PUBLIC_KEY": ("DATA_CORE_PUBLIC_KEY",
                                     "BACKEND_SERVICE_PUBLIC_KEY"),
    "DATA_CORE_INTERNAL_URL": ("DATA_CORE_URL", "BACKEND_INTERNAL_URL"),
    "DATA_CORE_HOST": ("BACKEND_HOST",),
    "DATA_CORE_PORT": ("BACKEND_PORT",),
}


def setting(name: str, default: str = "") -> str:
    """The value of ``name`` in the environment, or else of the first of
    its older names that is set; ``default`` where none is. A value that
    is only blanks is not set."""
    for each in (name, *OLDER_NAMES.get(name, ())):
        value = (os.environ.get(each) or "").strip()
        if value:
            return value
    return default
