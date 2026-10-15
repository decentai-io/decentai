"""Moving a deployment's bytes to the store FILE_STORAGE_PROVIDER names.

Two kinds of bytes move: uploaded files, each under the record that
says where its bytes are (``values.storage_provider``), and approved
agent packages. Each is copied, checked, and only then recorded as
moved; nothing is deleted where it was, so a move can be checked and
run again, and the old store is emptied by hand once the deployment
has been seen working on the new one.

Run by bootstrap/move_storage.py.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import List, Optional

from database.crypto import SecretCipher, SecretCipherError


@dataclass
class Moved:
    files: int = 0
    files_already: int = 0
    packages: int = 0
    packages_already: int = 0
    problems: List[str] = field(default_factory=list)


def _target_writer(target: str, settings):
    """(write(folder, file_id, bytes), has(folder, file_id)) for the
    store files are moving to."""
    from database.file_connectors import CONNECTORS

    connector = CONNECTORS[target](settings)
    if target == "local":
        import os

        def path(folder, file_id):
            directory = connector._resolve_folder(folder)
            if directory is None or os.path.basename(file_id) != file_id:
                raise ValueError(f"{folder}/{file_id} is not a path in the store")
            return os.path.join(directory, file_id)

        def write(folder, file_id, content):
            with open(path(folder, file_id), "wb") as out:
                out.write(content)

        def has(folder, file_id):
            return os.path.isfile(path(folder, file_id))
        return write, has

    def key(folder, file_id):
        found = connector._key(folder, file_id)
        if found is None:
            raise ValueError(f"{folder}/{file_id} is not a key in the store")
        return found

    def write(folder, file_id, content):
        connector.store.put(key(folder, file_id), io.BytesIO(content),
                            connector._content_type(file_id))

    def has(folder, file_id):
        return connector.store.has(key(folder, file_id))
    return write, has


def _read(connector, values, file_id) -> bytes:
    body, status = connector.download({
        "file_id": file_id, "folder": values.get("folder"),
        "filename": values.get("filename"), "file_size": values.get("file_size"),
        "file_type": values.get("file_type")})
    if status != 200:
        raise ValueError(body.get("error") or "not readable")
    stream = body["file_data"]
    try:
        return stream.read()
    finally:
        stream.close()


def move_files(db, settings, target: str, check: bool, moved: Moved) -> None:
    from database.file_connectors import CONNECTORS

    write, has = _target_writer(target, settings)
    sources = {}
    collection = db.collection("files")
    for document in collection.find({}):
        doc_id = document["_id"]
        try:
            values = SecretCipher.decrypt(document.get("values"), doc_id)
        except SecretCipherError as exc:
            moved.problems.append(f"file {doc_id}: {exc}")
            continue
        provider = str(values.get("storage_provider") or "local")
        if provider == target:
            moved.files_already += 1
            continue
        file_id = str(document.get("resource_id") or "")
        folder = values.get("folder")
        try:
            if provider not in sources:
                sources[provider] = CONNECTORS[provider](settings)
            if check:
                moved.files += 1
                continue
            content = _read(sources[provider], values, file_id)
            write(folder, file_id, content)
            if not has(folder, file_id):
                raise ValueError("not there after it was written")
        except Exception as exc:
            moved.problems.append(f"file {doc_id} ({provider}): {exc}")
            continue
        values["storage_provider"] = target
        collection.update_one(
            {"_id": doc_id},
            {"$set": {"values": SecretCipher.encrypt(values, doc_id)}})
        moved.files += 1


def move_packages(settings, source: str, target: str, check: bool,
                  moved: Moved) -> None:
    from database.agent_packages import PackageStore

    if source == target:
        return
    origin = PackageStore(provider=source).backend
    destination = PackageStore(provider=target).backend
    for org in origin.orgs():
        for name in origin.list(org):
            if destination.has(org, name):
                moved.packages_already += 1
                continue
            if check:
                moved.packages += 1
                continue
            archive = origin.get(org, name)
            try:
                destination.put(org, name, archive)
                if destination.get(org, name) != archive:
                    raise ValueError("read back differently")
            except Exception as exc:
                moved.problems.append(f"package {org}/{name}: {exc}")
                continue
            moved.packages += 1


def move(db, settings, check: bool = False,
         packages_from: Optional[str] = None) -> Moved:
    target = str(settings.file_storage_provider or "local").lower()
    moved = Moved()
    move_files(db, settings, target, check, moved)
    move_packages(settings, str(packages_from or "local").lower(), target,
                  check, moved)
    return moved
