"""Keys rotated: a value sealed under an older key opens still, and the admin tool seals each again under the active
one, after which the older key may go (api-server.md §11)."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import uuid

from api_server.records.store import Records
from api_server.settings.models import ModelEndpoints, Models
from api_server.storage.cipher import Cipher
from api_server.storage.database import Database

NEW = "02:" + base64.b64encode(b"n" * 32).decode()


def test_a_value_sealed_under_an_older_key_opens_until_the_admin_tool_seals_it_under_the_new_one(org, monkeypatch):
    person, apart = org.member.person, f"keys_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(Database, "db", Database.client[apart])  # the tool seals every value it finds: these alone
    monkeypatch.setattr(Cipher, "keys", Cipher.keys)
    monkeypatch.setattr(Cipher, "active", Cipher.active)
    record = Records().create(person, "secret", {}, {"password": "hunter22"})
    model = {"_id": Models.new_id(), "org_id": org.org_id}
    Models().col.insert_one({**model, "key": ModelEndpoints._sealed(model, "sk-1")})
    both = f"{os.environ['AI_API_ENCRYPTION_KEYS']},{NEW}"
    Cipher.load(both, "02")
    assert Records().opened(record) == {"password": "hunter22"} and Cipher.stale(record["values"])
    assert not Cipher.stale(Records().create(person, "secret", {}, {"password": "new"})["values"])
    done = subprocess.run([sys.executable, "-m", "api_server.admin", "reencrypt"], capture_output=True, text=True,
                          env={**os.environ, "AI_API_ENCRYPTION_KEYS": both, "AI_API_ENCRYPTION_ACTIVE": "02",
                               "AI_API_DATABASE_NAME": apart}, timeout=60)
    assert done.stdout.strip() == "2 values sealed again under 02.", done.stderr
    Cipher.load(NEW, "02")
    assert Records().opened(Records().col.find_one({"_id": record["_id"]})) == {"password": "hunter22"}
    assert Models().key(Models().col.find_one({"_id": model["_id"]})) == {"key": "sk-1"}
    Database.client.drop_database(apart)
