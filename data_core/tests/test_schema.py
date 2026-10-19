"""database/schema.json, applied: every collection with its validator,
and every index exactly as declared — partial and unique ones keeping
both."""

from database.mongo_db import MongoDB, load_database_schema


class RecordingDb:
    """Stands in for a pymongo Database, remembering what was asked of
    it."""

    def __init__(self, existing=()):
        self.existing = set(existing)
        self.created = []
        self.commands = []
        self.collections = {}

    def list_collection_names(self):
        return list(self.existing)

    def create_collection(self, name, **options):
        self.created.append((name, options))
        self.existing.add(name)

    def command(self, *args, **kwargs):
        self.commands.append((args, kwargs))

    def __getitem__(self, name):
        return self.collections.setdefault(name, RecordingCollection())


class RecordingCollection:
    def __init__(self):
        self.indexes = []

    def index_information(self):
        return {"_id_": {"key": [["_id", 1]]}}

    def create_index(self, keys, name=None, **options):
        self.indexes.append((name, keys, options))

    def drop_index(self, name):  # pragma: no cover - nothing to drop here
        pass


def applied():
    """Run apply_schema against a recording database, without connecting
    to anything."""
    db = MongoDB.__new__(MongoDB)
    db.db = RecordingDb()
    db.apply_schema()
    return db.db


def partial_indexes():
    """Every partial index the schema declares, read from the schema."""
    return {
        index["name"]
        for definition in load_database_schema()["collections"].values()
        for index in definition.get("indexes", [])
        if "partialFilterExpression" in index
    }


class TestApplyingTheSchema:
    def test_every_collection_is_created_with_its_validator(self):
        recorded = applied()
        declared = load_database_schema()["collections"]
        assert len(recorded.created) == len(declared)
        for name, options in recorded.created:
            assert options["validator"] == declared[name]["validator"]
            assert options["validationAction"] == "error"

    def test_partial_indexes_keep_their_filter_and_their_uniqueness(self):
        recorded = applied()
        names = partial_indexes()
        assert names, "the schema declares at least one partial index"

        found = {
            name: options
            for collection in recorded.collections.values()
            for name, _, options in collection.indexes
            if name in names
        }
        assert set(found) == names
        for name, options in found.items():
            assert options["unique"] is True, name
            assert "partialFilterExpression" in options, name
