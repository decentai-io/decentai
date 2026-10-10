"""The runtime's own tests — no backend, no database, no network.

Real agents in real workers, the sim standing in for the platform and
a fake standing in for the agents' container (fake_container.py).
Nothing here needs MongoDB, so this suite runs alongside anything.

    cd ai_runtime && python -m pytest tests -q
"""

import pytest

from ai_runtime.tests import fake_container


@pytest.fixture(autouse=True)
def _agents_container(tmp_path):
    """Every test has an agents' container of its own, stood in for
    here: whatever it started dies with the test."""
    fake_container.begin(tmp_path / "agents")
    yield
    fake_container.end()
