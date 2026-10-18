"""An agent's private environment, as the runtime's tests and an
agent's own tests still name it (decentai-agents' tests/conftest.py).

The environment is built by the Execution Sandbox a worker runs in,
and its code is the sandbox's: ``ai_runtime/agents_container/environments.py``.
"""

from ai_runtime.agents_container.environments import (  # noqa: F401
    READY_MARKER, REQUIREMENT_RE, WHEEL_RE, AgentEnvironment,
)
