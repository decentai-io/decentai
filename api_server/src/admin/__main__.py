"""python -m api_server.admin <command>: the operator's tool, run in the container (api-server.md §11)."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
from typing import Any

from pymongo import MongoClient
from pymongo.uri_parser import parse_uri

from communication.node import Node

from ..people.endpoints import People
from ..people.store import Organizations, Passwords, Sessions, Users
from ..records.store import Records
from ..runtimes.calls import RuntimeCalls
from ..storage.cipher import Cipher
from ..storage.database import Database
from ..storage.files import Folder, S3, Storage
from ..trail.trail import Trail
from ..utils import Settings


class Admin:
    """Each command a method; each says what it did."""

    def database(self, args: argparse.Namespace) -> str:
        """The API server's database user, the one its URI names: everything but the trail, where it may only add
        and read. Made again, it is brought up to date."""
        settings = Settings.read()
        named = parse_uri(settings.database_uri)
        db = MongoClient(args.admin_uri)[settings.database_name]
        importlib.import_module(f"{__package__.rpartition('.')[0]}.doors.catalog")  # every endpoint, and its store
        kept = sorted({store.COLLECTION for store in Database.stores()} | {Database.DEPLOYMENT})
        adding = ["find", "insert", "createCollection", "createIndex"]
        privileges = [{"resource": {"db": settings.database_name, "collection": collection},
                       "actions": adding if collection == Trail.COLLECTION else [*adding, "update", "remove",
                                                                                 "changeStream"]}
                      for collection in kept]
        made = {role["role"] for role in db.command("rolesInfo", 1)["roles"]}
        db.command("updateRole" if "decentai_api" in made else "createRole", "decentai_api", privileges=privileges,
                   roles=[])
        users = {user["user"] for user in db.command("usersInfo", 1)["users"]}
        db.command("updateUser" if named["username"] in users else "createUser", named["username"],
                   pwd=named["password"], roles=["decentai_api"])
        return f"{named['username']} may read and write {settings.database_name}, and only add to its trail."

    def organizations(self, args: argparse.Namespace) -> str:
        if args.action == "list":
            return "\n".join(f"{o['_id']}  {o['status']:8}  {o['name']}" for o in Organizations().col.find())
        if args.action == "create":
            password = Passwords.temporary()
            person = People.found(args.name, args.admin_name, args.admin_email, password)
            Users().set(person["_id"], must_change_password=True)
            return f"Made {args.name}; {args.admin_email} signs in with {password} and chooses their own."
        status = "active" if args.action == "enable" else "disabled"
        Organizations().col.update_one({"_id": args.org_id}, {"$set": {"status": status}})
        if status == "disabled":
            Sessions().col.delete_many({"org_id": args.org_id})
        return f"{args.org_id} is {status}."

    def password(self, args: argparse.Namespace) -> str:
        person = Users().by_email(args.email)
        if person is None:
            return "No such person."
        password = Passwords.temporary()
        Users().set(person["_id"], password_hash=Passwords.hash(password), must_change_password=True)
        Sessions().end_all(person["_id"])
        return f"{args.email} signs in with {password} and chooses their own."

    def reencrypt(self, args: argparse.Namespace) -> str:
        """Every value sealed under an older key, sealed again under the active one."""
        count = 0
        for store, field, bound in self._sealed():
            for doc in store.find({field: {"$regex": r"^[^.]+\."}}):
                if Cipher.stale(doc[field]):
                    store.update_one({"_id": doc["_id"]}, {"$set": {field: Cipher.seal(Cipher.open(doc[field], bound(doc)),
                                                                                      bound(doc))}})
                    count += 1
        return f"{count} values sealed again under {Cipher.active}."

    def storage(self, args: argparse.Namespace) -> str:
        """Every file and program copied from one storage to the other; the settings name both."""
        settings = Settings.read()
        source, target = (Folder(settings.storage_folder), S3(settings.s3)) if args.to == "s3" else \
            (S3(settings.s3), Folder(settings.storage_folder))
        keys = [r["file"]["key"] for r in Records().col.find({"file": {"$ne": None}}, {"file.key": 1})]
        keys += [f["key"] for a in Database.db["agents"].find() for f in a["programs"].values()]
        for key in keys:
            with target.writing(key, 2 * 1024 ** 3) as writing:
                for piece in source.pieces(key):
                    writing.write(piece)
        return f"{len(keys)} files copied to {args.to}: set AI_API_STORAGE={args.to}."

    def intermediate(self, args: argparse.Namespace) -> str:
        RuntimeCalls.node = Node(Settings.read().identity, port=None)

        async def asked() -> Any:
            await RuntimeCalls.node.start()
            try:
                if args.action == "request":
                    return await RuntimeCalls().issuer("Certificates:Authority:request", {})
                return await RuntimeCalls().issuer("Certificates:Authority:install",
                                                   {"certificate": open(args.file, encoding="utf-8").read()})
            finally:
                await RuntimeCalls.node.stop()
        return str(asyncio.run(asked()))

    @staticmethod
    def _sealed() -> list[tuple[Any, str, Any]]:
        db = Database.db
        return [(db["records"], "values", Records.bound),
                (db["sources"], "credential", lambda d: f"{d['org_id']}:source:{d['_id']}"),
                (db["models"], "key", lambda d: f"{d['org_id']}:model:{d['_id']}"),
                (db["oauth_apps"], "secret", lambda d: f"{d['org_id']}:oauth_app:{d['_id']}"),
                (db["mcp_servers"], "headers", lambda d: f"{d['org_id']}:mcp:{d['_id']}"),
                (db["webhooks"], "secret", lambda d: f"{d['org_id']}:webhook:{d['_id']}")]


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m api_server.admin")
    commands = parser.add_subparsers(dest="command", required=True)
    database = commands.add_parser("database", help="make the database user AI_API_DATABASE_URI names")
    database.add_argument("--admin-uri", required=True)
    organizations = commands.add_parser("organizations", help="list, create, disable or enable organizations")
    organizations.add_argument("action", choices=("list", "create", "disable", "enable"))
    organizations.add_argument("--org-id", default="")
    organizations.add_argument("--name", default="")
    organizations.add_argument("--admin-name", default="Administrator")
    organizations.add_argument("--admin-email", default="")
    commands.add_parser("password", help="reset a person's password").add_argument("email")
    commands.add_parser("reencrypt", help="seal every value under the active key")
    commands.add_parser("storage", help="copy files between local and S3 storage").add_argument("to", choices=("local", "s3"))
    intermediate = commands.add_parser("intermediate", help="the issuer's intermediate: request it, or install it")
    intermediate.add_argument("action", choices=("request", "install"))
    intermediate.add_argument("--file", default="")
    args = parser.parse_args()
    settings = Settings.read()
    Cipher.load(settings.encryption_keys, settings.encryption_active)
    if args.command != "database":
        Database.connect(settings.database_uri, settings.database_name)
        Storage.open(settings)
    print(getattr(Admin(), args.command)(args))
    return 0


sys.exit(main())
