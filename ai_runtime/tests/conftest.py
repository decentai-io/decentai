"""The runtime's own tests — no backend, no database, no network.

Real agents in real workers, the sim standing in for the platform.
Nothing here needs MongoDB, so this suite runs alongside anything.

    cd ai_runtime && python -m pytest tests -q
"""

import pytest


@pytest.fixture(autouse=True)
def _reap_worker_pools():
    """Workers spawned during a test die with it. Each test's executors
    own private WorkerPools whose processes would otherwise outlive the
    test's event loop; production holds one long-lived pool and never
    goes through this."""
    yield
    from ai_runtime.agents.worker_pool import WorkerPool

    WorkerPool.terminate_all()
