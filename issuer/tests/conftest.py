"""The issuer's tests: a real MongoDB (MONGO_URI), a database of their own per test, removed after.

    python -m pytest issuer/tests -q
"""

import os
import secrets
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pymongo import MongoClient  # noqa: E402

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def database():
    """A database name of this test's own, dropped at the end."""
    name = f"issuer_test_{secrets.token_hex(4)}"
    yield name
    client = MongoClient(MONGO_URI)
    client.drop_database(name)
    client.close()
