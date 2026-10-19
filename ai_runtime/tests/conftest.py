"""The runtime's own tests — no Data Core, no database, no network.

Real agents in real workers, the sim standing in for the platform and
a fake standing in for the Execution Sandbox (fake_container.py).
Nothing here needs MongoDB, so this suite runs alongside anything.

    cd ai_runtime && python -m pytest tests -q
"""

import pytest

from ai_runtime.tests import fake_container


@pytest.fixture(autouse=True)
def _sandbox(tmp_path):
    """Every test has an Execution Sandbox of its own, stood in for
    here: whatever it started dies with the test."""
    fake_container.begin(tmp_path / "agents")
    yield
    fake_container.end()
