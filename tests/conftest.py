"""Tests that span the Data Core and the runtime — an approval on one
side becoming code that serves on the other, a policy becoming a grant.

The harness is the Data Core's (data_core/tests/conftest.py): real app,
real MongoDB, disposable database. It is loaded here by path and its
fixtures re-exported, so a spanning test reads exactly like a Data Core
one — and helpers shared between Data Core tests stay importable, since
data_core/tests is on the path.

    python -m pytest tests -q          (from the project root)
"""

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_CORE_TESTS = PROJECT_ROOT / "data_core" / "tests"
for path in (PROJECT_ROOT, PROJECT_ROOT / "data_core", DATA_CORE_TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

_spec = importlib.util.spec_from_file_location(
    "data_core_harness", DATA_CORE_TESTS / "conftest.py")
_harness = importlib.util.module_from_spec(_spec)
sys.modules["data_core_harness"] = _harness
_spec.loader.exec_module(_harness)

ADMIN_EMAIL = _harness.ADMIN_EMAIL
ADMIN_PASSWORD = _harness.ADMIN_PASSWORD
app_call = _harness.app_call
define_secret = _harness.define_secret
version_secret = _harness.version_secret
service_keys = _harness.service_keys
runtime_identity = _harness.runtime_identity
runtime_service_token = _harness.runtime_service_token
register_authority = _harness.register_authority
delegated_headers = _harness.delegated_headers
node_for_tests = _harness.node_for_tests
signing_key_pair = _harness.signing_key_pair
signing_key = _harness.signing_key
pytest_collection_modifyitems = _harness.pytest_collection_modifyitems
app = _harness.app
seed = _harness.seed
anon = _harness.anon
admin = _harness.admin


@pytest.fixture(autouse=True)
def _sandbox(tmp_path):
    """Every test has an Execution Sandbox of its own, stood in for
    by the runtime's test double (ai_runtime/tests/fake_container.py):
    whatever it started dies with the test."""
    from ai_runtime.tests import fake_container

    fake_container.begin(tmp_path / "agents")
    yield
    fake_container.end()
