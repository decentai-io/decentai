import json
import socket
from datetime import timedelta
from pathlib import Path

from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from server.custom_logging import CustomLoggerFactory


SCHEMA_PATH = Path(__file__).with_name("schema.json")


def load_database_schema() -> dict:
    """Load and minimally validate the declarative MongoDB schema."""
    with SCHEMA_PATH.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    if not isinstance(schema.get("collections"), dict):
        raise ValueError("database/schema.json must define a collections object")
    return schema


class MongoDB:
    """MongoDB connection, and the declarative schema laid down in two
    halves (docs/system/cluster.md#the-schema).

    **At every start** (``apply_schema``): what the schema declares and
    the database lacks — a collection with its validator, an index —
    is made, and nothing that is there is changed or dropped. Several
    replicas start at once, and during an upgrade replicas of two
    versions run side by side: a start that dropped what its version
    does not declare would take an index from the other version's
    queries.

    **Once per version** (``migrate``): the database brought to exactly
    what this version declares — validators replaced, indexes whose
    keys or options changed made again, indexes no longer declared
    dropped — and the version written down. The leader runs it when it
    takes the lead, and the seeder as it starts; a claim on the version
    row lets one of them do it, and a version older than the one
    written does nothing.
    """

    #: The row in ``cluster_state`` that says which version the
    #: database was last brought to.
    VERSION_ROW = "schema"
    #: How long one migration may hold its claim before another may
    #: take it over: a process that died mid-step.
    CLAIM_SECONDS = 600

    def __init__(self, settings):
        self.logger = CustomLoggerFactory.get_logger(__class__.__name__)
        try:
            self.client = MongoClient(
                settings.mongo_uri,
                serverSelectionTimeoutMS=5000,
                tz_aware=True,
            )
            self.client.admin.command("ping")
            self.db = self._database(settings.mongo_database_name)
            self.apply_schema()
            self.logger.info(
                f"MongoDB connected: db={settings.mongo_database_name}")
        except Exception as exc:
            self.logger.error(f"Error initializing MongoDB connection: {exc}")
            raise

    def _database(self, name: str):
        """The database, its writes waiting for what the server is: on a
        replica set, the majority and its journal — so a write the Data
        Core was told is done survives the primary's loss (a lease's
        number, a row's claim), and asked for at once, where the
        server's default waits for its next periodic journal flush; on
        a single server, the server's own default, unchanged."""
        from pymongo import WriteConcern

        if self.client.admin.command("hello").get("setName"):
            return self.client.get_database(
                name, write_concern=WriteConcern(w="majority", j=True))
        return self.client[name]

    def collection(self, name: str):
        return self.db[name]

    def replica_set(self) -> str:
        """The replica set's name, or "" for a server that is none: the
        replicas' bus reads a change stream, which only a replica set
        has (api/services/cluster/bus.py)."""
        try:
            return str(self.client.admin.command("hello").get("setName") or "")
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Every start: what is missing, and nothing else
    # ------------------------------------------------------------------

    def apply_schema(self) -> None:
        """Create every declared collection and index that is missing."""
        definitions = load_database_schema()["collections"]
        existing = set(self.db.list_collection_names())

        for name, definition in definitions.items():
            if name not in existing:
                options = {}
                if definition.get("capped"):
                    options = {"capped": True,
                               "size": int(definition["capped"]["size"])}
                self.db.create_collection(
                    name,
                    validator=definition.get("validator", {}),
                    validationLevel="moderate",
                    validationAction="error",
                    **options,
                )
            collection = self.db[name]
            information = collection.index_information()
            for index in definition.get("indexes", []):
                wanted = [list(pair) for pair in index["keys"]]
                if index["name"] in information or any(
                        [list(pair) for pair in spec.get("key", [])] == wanted
                        for spec in information.values()):
                    # There already, perhaps as another version has it:
                    # changing it is the migration's.
                    continue
                options = {key: value for key, value in index.items()
                           if key not in {"keys", "name"}}
                try:
                    collection.create_index(index["keys"], name=index["name"],
                                            **options)
                except Exception as exc:
                    self.logger.warning(
                        f"Index {name}.{index['name']} not made at start "
                        f"({exc}); the schema's migration makes it.")

    # ------------------------------------------------------------------
    # Once per version: exactly what this version declares
    # ------------------------------------------------------------------

    def migrate(self, by: str = "") -> bool:
        """Bring the database to this version's schema, once. Returns
        whether this call did; False when the database is there
        already, at a later version, or another is doing it now."""
        from util import utc_now

        version = int(load_database_schema().get("schema_version") or 0)
        state = self.db["cluster_state"]
        by = by or socket.gethostname()
        now = utc_now()
        claim = {"applying_by": by,
                 "applying_until": now + timedelta(seconds=self.CLAIM_SECONDS)}
        claimed = state.find_one_and_update(
            {"_id": self.VERSION_ROW, "version": {"$lt": version},
             "$or": [{"applying_until": {"$exists": False}},
                     {"applying_until": {"$lte": now}}]},
            {"$set": claim}, return_document=ReturnDocument.AFTER)
        if claimed is None:
            if state.find_one({"_id": self.VERSION_ROW}, {"_id": 1}) is not None:
                return False
            try:
                state.insert_one({"_id": self.VERSION_ROW, "version": 0, **claim})
            except DuplicateKeyError:
                return False
        self.reconcile()
        state.update_one(
            {"_id": self.VERSION_ROW},
            {"$set": {"version": version, "applied_at": utc_now(),
                      "applied_by": by},
             "$unset": {"applying_by": "", "applying_until": ""}})
        self.logger.info(f"Schema brought to version {version}")
        return True

    def reconcile(self) -> None:
        """Every declared collection's validator, and its indexes exactly
        as declared: nothing undeclared left behind."""
        definitions = load_database_schema()["collections"]
        self.apply_schema()
        for name, definition in definitions.items():
            self.db.command(
                "collMod",
                name,
                validator=definition.get("validator", {}),
                validationLevel="moderate",
                validationAction="error",
            )
            collection = self.db[name]
            declared_indexes = {
                index["name"] for index in definition.get("indexes", [])
            }
            for existing_name in collection.index_information():
                if existing_name != "_id_" and existing_name not in declared_indexes:
                    collection.drop_index(existing_name)
            for index in definition.get("indexes", []):
                options = {
                    key: value for key, value in index.items()
                    if key not in {"keys", "name"}
                }
                self._replace_index(
                    collection,
                    index["name"],
                    index["keys"],
                    **options,
                )

    def _replace_index(self, collection, name: str, keys, **options) -> None:
        """Ensure a named index has exactly the declared keys and options."""
        wanted = [list(pair) for pair in keys]
        information = collection.index_information()

        for existing_name, spec in information.items():
            if existing_name == "_id_":
                continue
            same_keys = [list(pair) for pair in spec.get("key", [])] == wanted
            if same_keys and existing_name != name:
                collection.drop_index(existing_name)

        try:
            collection.create_index(keys, name=name, **options)
        except Exception:
            if name in collection.index_information():
                collection.drop_index(name)
            collection.create_index(keys, name=name, **options)
