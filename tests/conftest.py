"""Tests that span the backend and the runtime — an approval on one
side becoming code that serves on the other, a policy becoming a grant.

The harness is the backend's (backend/tests/conftest.py): real app,
real MongoDB, disposable database. It is loaded here by path and its
fixtures re-exported, so a spanning test reads exactly like a backend
one — and helpers shared between backend tests stay importable, since
backend/tests is on the path.

    python -m pytest tests -q          (from the project root)
"""

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_TESTS = PROJECT_ROOT / "backend" / "tests"
for path in (PROJECT_ROOT, PROJECT_ROOT / "backend", BACKEND_TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

_spec = importlib.util.spec_from_file_location(
    "backend_harness", BACKEND_TESTS / "conftest.py")
_harness = importlib.util.module_from_spec(_spec)
sys.modules["backend_harness"] = _harness
_spec.loader.exec_module(_harness)

ADMIN_EMAIL = _harness.ADMIN_EMAIL
ADMIN_PASSWORD = _harness.ADMIN_PASSWORD
app_call = _harness.app_call
define_secret = _harness.define_secret
version_secret = _harness.version_secret
service_keys = _harness.service_keys
signing_key = _harness.signing_key
pytest_collection_modifyitems = _harness.pytest_collection_modifyitems
app = _harness.app
seed = _harness.seed
anon = _harness.anon
admin = _harness.admin


@pytest.fixture(autouse=True)
def _reap_worker_pools():
    """Workers spawned during a test die with it. A test's runtime owns
    private WorkerPools whose processes would otherwise outlive the
    test's event loop; production holds one long-lived pool and never
    goes through this."""
    yield
    from ai_runtime.agents.worker_pool import WorkerPool

    WorkerPool.terminate_all()
