"""bootstrap/init_db.py waits for the database before it seeds it: a new
database's container says it is healthy while it is still setting itself
up, and then restarts once."""

import importlib.util
from pathlib import Path

import pytest
from pymongo.errors import PyMongoError

from conftest import MONGO_URI

ROOT = Path(__file__).resolve().parent.parent.parent

_spec = importlib.util.spec_from_file_location(
    "decentai_init_db", ROOT / "bootstrap" / "init_db.py")
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


def test_a_database_that_answers_is_not_waited_for():
    module.wait_for_database(MONGO_URI, seconds=5)


def test_a_database_that_never_answers_is_given_up_on_when_the_time_is_out():
    with pytest.raises(PyMongoError):
        module.wait_for_database("mongodb://127.0.0.1:1", seconds=0.5)
