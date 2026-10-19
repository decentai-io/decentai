import json
from pathlib import Path

from pymongo import MongoClient

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
    """MongoDB connection that applies the declarative schema on startup:
    every collection with its validator, every index as declared, and
    nothing undeclared left behind."""

    def __init__(self, settings):
        self.logger = CustomLoggerFactory.get_logger(__class__.__name__)
        try:
            self.client = MongoClient(
                settings.mongo_uri,
                serverSelectionTimeoutMS=5000,
                tz_aware=True,
            )
            self.db = self.client[settings.mongo_database_name]
            self.client.admin.command("ping")
            self.apply_schema()
            self.logger.info(
                f"MongoDB connected: db={settings.mongo_database_name}")
        except Exception as exc:
            self.logger.error(f"Error initializing MongoDB connection: {exc}")
            raise

    def collection(self, name: str):
        return self.db[name]

    def apply_schema(self) -> None:
        """Create/update every declared collection, validator, and index."""
        definitions = load_database_schema()["collections"]
        existing = set(self.db.list_collection_names())

        for name, definition in definitions.items():
            validator = definition.get("validator", {})
            if name not in existing:
                self.db.create_collection(
                    name,
                    validator=validator,
                    validationLevel="moderate",
                    validationAction="error",
                )
            else:
                self.db.command(
                    "collMod",
                    name,
                    validator=validator,
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
