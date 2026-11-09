"""What the tests stand on: ``runtime/src`` as the package ``runtime``, and ``async def`` tests run with no
plugin."""

import asyncio
import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC.parents[1]))  # communication/ and shared/, which the link uses
spec = importlib.util.spec_from_file_location("runtime", SRC / "__init__.py", submodule_search_locations=[str(SRC)])
module = importlib.util.module_from_spec(spec)
sys.modules["runtime"] = module
spec.loader.exec_module(module)
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    asyncio.run(pyfuncitem.obj(**{name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}))
    return True
