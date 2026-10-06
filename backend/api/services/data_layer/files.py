"""Files — the controller. The store (and the byte connectors) live
under database/, with everything else that persists."""

from __future__ import annotations

from typing import Any, Dict, Optional

from api.services.data_layer.base import Refusal, ResourceController
from contracts.file_types import FileTypes
from database.crypto import SecretCipherError
from database.stores.data.files import FileStore
from server.setup.app_state import get_settings
from database.file_connectors import CONNECTORS
from database.file_connectors import FileConnector


class FileController(ResourceController):
    """The resource shape plus bytes. Upload IS the create; download is
    owner-filtered like any read. The backend owns resource_id, owner, and
    encrypted file metadata; callers own only keys."""

    STORE = FileStore
    ESCAPE_ACTION = "files:file:set_owner_any"

    DEFAULT_SUBFOLDER = "uploads"

    def __init__(self):
        super().__init__()
        self.settings = get_settings()
        self._connectors: Dict[str, FileConnector] = {}

        # Built now so misconfiguration fails at boot, not on first upload.
        # Writes use this; reads follow each doc's storage_provider stamp.
        self.write_connector = self._connector(
            self.settings.file_storage_provider
        )

    @classmethod
    def _clean_subfolder(cls, raw: Any) -> str:
        """A relative subfolder and nothing more: separators normalized,
        empty and traversal segments dropped."""
        segments = str(raw or "").replace("\\", "/").split("/")
        cleaned = [
            segment.strip() for segment in segments
            if segment.strip() and segment.strip() not in (".", "..")
        ]
        return "/".join(cleaned) or cls.DEFAULT_SUBFOLDER

    def _connector(self, provider: Any) -> FileConnector:
        provider = str(provider or "").strip().lower()
        if provider not in self._connectors:
            connector_cls = CONNECTORS.get(provider)
            if connector_cls is None:
                raise ValueError(f"Unknown file storage provider '{provider}'.")
            self._connectors[provider] = connector_cls(self.settings)
        return self._connectors[provider]

    def _doc_connector(self, doc: Dict[str, Any]) -> tuple[Optional[FileConnector], Optional[Refusal]]:
        """The connector a stored document's bytes live on, or the refusal
        when its provider is not configured in this deployment."""
        provider = (doc.get("values") or {}).get("storage_provider")
        try:
            return self._connector(provider), None
        except ValueError as exc:
            self.logger.error(f"File {doc.get('_id')}: {exc}")
            return None, ({"error": str(exc)}, 500)

    # ------------------------------------------------------------------
    # Upload — the create
    # ------------------------------------------------------------------

    def list(self, data: dict, user: dict):
        """Every visible file, each one saying which agent made it, by
        name. An agent's upload carries only its category key —
        ``<ref>__<resource id>`` — and a page that printed that showed
        people a minted id where a name belongs. The names come from
        this organization's approvals, read once per listing; a ref no
        approval answers for any more is passed through as it is."""
        answer, status = super().list(data, user)
        if status != 200:
            return answer, status
        names = None
        for resource in answer.get("resources") or []:
            ref = self._agent_ref_of(resource)
            if not ref:
                continue
            if names is None:
                names = self._agent_names(user)
            resource["agent"] = {"agent_id": ref, "name": names.get(ref, "")}
        return answer, status

    @staticmethod
    def _agent_ref_of(resource: Dict[str, Any]) -> str:
        """The approval ref an agent's file was stored under, from its
        category key; empty for an upload or a chat attachment."""
        category = str((resource.get("keys") or {}).get("category") or "")
        ref, sep, _ = category.partition("__")
        return ref if sep and ref.startswith("agt_") else ""

    @staticmethod
    def _agent_names(user: dict) -> Dict[str, str]:
        from database.stores import AgentManifestStore

        names: Dict[str, str] = {}
        for doc in AgentManifestStore().list(str(user.get("org_id") or "")):
            manifest = doc.get("manifest") or {}
            names[str(doc["_id"])] = str((manifest.get("agent") or {}).get("name") or "")
        return names

    def upload(self, data: dict, user: dict, *, agent_samples: bool = False):
        """The create. ``agent_samples`` is the samples door's, in
        process only: an agent's sample sheet stores into its own slots."""
        payload = self._payload(data)
        # The /upload route nests fields under additional_data; in-process
        # callers pass them at the top level. Merge, top level winning.
        extra = payload.get("additional_data")
        if isinstance(extra, dict):
            payload = {**extra, **payload}

        refusal = self._protected(
            payload, "org_id", "resource_id", "owner", "values")
        if refusal:
            return refusal

        filename = payload.get("filename")
        file_bytes = payload.get("file_bytes")
        if file_bytes is None and payload.get("content_base64") is not None:
            # JSON-gateway callers (the runtime) cannot send a stream;
            # they send base64 content instead.
            import base64
            import io
            try:
                file_bytes = io.BytesIO(
                    base64.b64decode(str(payload["content_base64"]))
                )
            except (ValueError, TypeError):
                return {"error": "content_base64 is not valid base64."}, 400
        if not filename or file_bytes is None:
            return {"error": "A file and its filename are required."}, 400

        # The same ceiling the multipart route enforces. Both doors, or
        # the cap is a suggestion: the base64 path is how the runtime
        # uploads, and it is no less able to exhaust memory.
        limit_mb = getattr(get_settings(), "max_upload_mb", 25)
        file_bytes.seek(0, 2)
        size = file_bytes.tell()
        file_bytes.seek(0)
        if size > limit_mb * 1024 * 1024:
            return {"error": f"Files must be {limit_mb} MB or smaller."}, 400
        if size == 0:
            # An empty file is never what anyone meant. Stored, it shows
            # as a file card that opens to nothing; refused, the agent
            # that produced nothing hears so at once.
            return {"error": "The file is empty — nothing was stored."}, 400

        # Folder layout is data-layer-owned: <user_id>/<subfolder>. Callers
        # supply only the subfolder — nobody can address another user's tree.
        folder = (
            f"{user.get('user_id') or 'anonymous'}/"
            f"{self._clean_subfolder(payload.get('folder'))}"
        )

        owner, refusal = self._owner_or_refusal(user, self._default_owner(user))
        if refusal:
            return refusal

        try:
            meta = self.STORE.clean_keys(payload.get("meta"))
        except ValueError as exc:
            return {"error": str(exc)}, 400
        refusal = self._slot_refusal(user, meta.get("category"), agent_samples)
        if refusal:
            return refusal
        # What the slot's manifest said it takes, held for whoever
        # stores there: the agent, its sample sheet, or a person.
        unfit = FileTypes.refusal(
            self._slot_constraints(user, meta.get("category")), filename, size)
        if unfit:
            return {"error": unfit}, 400

        connector = self.write_connector
        stored, status = connector.upload({
            "filename": filename,
            "file_bytes": file_bytes,
            "folder": folder,
            "meta": meta,
        })
        if status != 200:
            return {"error": stored.get("error", "Failed to store the file.")}, status

        # The backend-generated storage id is also the unique file resource
        # id, so an existing visible record is returned as-is.
        existing = self.store.list_visible(
            user,
            resource_id=stored["file_id"],
        )
        if existing:
            return {"resource": existing[0]}, 200

        try:
            resource = self.store.create(
                user,
                resource_id=stored["file_id"],
                owner=owner,
                keys=meta,
                values={
                    "filename": stored["filename"],
                    "file_type": stored.get("file_type"),
                    "file_size": stored.get("file_size"),
                    "folder": folder,
                    "storage_provider": connector.storage_provider_type,
                },
            )
        except ValueError as exc:
            # The id is a content hash and its index is deployment-wide,
            # so this also fires when a record for these exact bytes
            # exists but is not VISIBLE to the uploader — their own
            # earlier copy, re-shared to a group they have since left.
            # They can neither see it nor replace it, and "already
            # exists" describes something they have no way to find.
            # The bytes stay: another document still references them.
            if self.store.exists_anywhere(stored["file_id"]):
                return {
                    "error": "This file is already stored, under a record "
                             "you can no longer see. Ask whoever it was "
                             "shared with to share it back, or an "
                             "administrator to remove it.",
                }, 409

            # Document failed after the bytes landed — clean them up.
            self._drop_bytes_if_last(
                connector, stored["file_id"], folder, excluding=None
            )
            return {"error": str(exc)}, 400

        self.logger.info(
            f"{user.get('email')} uploaded {resource['values']['filename']} "
            f"as file {resource['resource_ref']}"
        )
        return {"resource": resource}, 200

    @staticmethod
    def _slot_of(doc: Dict[str, Any]) -> str:
        """A file's slot is its category key; its resource_id names the
        stored bytes."""
        return str((doc.get("keys") or {}).get("category") or "")

    @staticmethod
    def _slot_refusal(user: dict, category: Any, agent_samples: bool):
        """A person storing a file in an installed agent's slot
        (``agt_<ref>__<slot>``) needs the manifest's ``user_access`` to
        say ``create``; the agent itself (the runtime, its sample
        sheet) does not."""
        category = str(category or "")
        if (agent_samples or user.get("principal_type") == "runtime"
                or not (category.startswith("agt_") and "__" in category)):
            return None
        from database.stores import AgentManifestStore

        agent_ref, _, slot = category.partition("__")
        agent = AgentManifestStore().installed_in(
            str(user.get("org_id") or ""), agent_ref)
        declared = next((
            resource for resource in
            ((agent or {}).get("manifest") or {}).get("resources", {}).get("files") or []
            if str(resource.get("id") or "") == slot), None)
        if declared is None:
            return {"error": "No installed agent declares that file slot."}, 404
        if "create" not in (declared.get("user_access") or []):
            return {"error": "The agent keeps these files itself — they can "
                             "be read and deleted here, not added."}, 403
        return None

    @staticmethod
    def _slot_constraints(user: dict, category: Any):
        """The ``constraints`` an installed agent's manifest declared
        for the slot a file is being stored in (``agt_<ref>__<slot>``);
        None for a file that is in no agent's slot, and for a slot that
        declared none."""
        category = str(category or "")
        if not (category.startswith("agt_") and "__" in category):
            return None
        from database.stores import AgentManifestStore

        agent_ref, _, slot = category.partition("__")
        agent = AgentManifestStore().installed_in(
            str(user.get("org_id") or ""), agent_ref)
        return FileTypes.constraints_of((agent or {}).get("manifest") or {}, slot)

    def create(self, data: dict, user: dict):
        """Not part of this domain — a file document exists because bytes
        were stored. Upload is the create.

        DO NOT DELETE THIS METHOD. The gateway dispatches only catalog
        actions, and `files:file:create` is not one — this is the second
        guard behind that. ResourceController offers a generic `create`,
        and without this override a file record could be minted whose
        `folder` and `filename` never pass through `_clean_subfolder` —
        and download reads those two fields back verbatim, so `..` in a
        forged folder walks out of the upload directory.
        """
        return {"error": "Files are created by upload."}, 400

    def update(self, data: dict, user: dict):
        """Keys and sharing. Everything else about a file — where its
        bytes live, how big they are, what type they are — was written
        when they landed and is not a caller's to revise.

        Sharing IS the caller's: it is the only decision about a file
        that belongs to the person rather than to the platform, and it
        goes through the same gate as every other resource, so nobody
        can share into a group they are not in without the escape grant.
        """
        payload = self._payload(data)
        refusal = self._protected(payload, "org_id", "resource_id", "values")
        if refusal:
            return refusal

        ref = str(payload.get("resource_ref") or "")
        doc = self.store.visible_doc(user, ref)
        if doc is None:
            return self._not_found()
        refusal = self._edit_refusal(user, doc)
        if refusal:
            return refusal

        owner = None
        if payload.get("owner") is not None:
            owner, refusal = self._owner_or_refusal(user, payload.get("owner"))
            if refusal:
                return refusal

        if "keys" not in payload and owner is None:
            return {"error": "Keys or owner are required."}, 400
        keys = payload.get("keys")
        if isinstance(keys, dict) and "category" in keys and str(
                keys["category"] or "") != str(
                (doc.get("keys") or {}).get("category") or ""):
            return {"error": "Where a file belongs is set when it lands."}, 400

        try:
            resource = self.store.update(
                doc, owner=owner, keys=payload.get("keys"),
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400
        self.logger.info(f"{user.get('email')} updated file {ref}")
        return {"resource": resource}, 200

    # ------------------------------------------------------------------
    # Download — visibility-checked bytes
    # ------------------------------------------------------------------

    def download(self, data: dict, user: dict):
        ref = str(self._payload(data).get("resource_ref") or "")

        # The loud read: a download of a file whose cipher key was
        # rotated away must say so, never hand back empty bytes.
        try:
            doc = self.store.visible_record(user, ref)
        except SecretCipherError as exc:
            return {"error": str(exc)}, 409
        if doc is None or self._outside_slot(user, data, doc):
            return self._not_found()

        connector, refusal = self._doc_connector(doc)
        if refusal:
            return refusal

        values = doc.get("values") or {}
        body, status = connector.download({
            "file_id": doc.get("resource_id"),
            "folder": values.get("folder"),
            "filename": values.get("filename"),
            "file_size": values.get("file_size"),
            "file_type": values.get("file_type"),
        })
        if status != 200:
            return {"error": body.get("error", "File not found.")}, 404

        # JSON-gateway callers (the runtime) cannot consume a stream; with
        # encoding=base64 the body is read and returned inline.
        if str(self._payload(data).get("encoding") or "").lower() == "base64":
            import base64
            stream = body.get("file_data")
            # Any body a connector returns: a file, a stream with read(),
            # or one that only iterates. Never an empty fallback — that
            # handed agents zero bytes for a file that was there.
            try:
                if isinstance(stream, (bytes, bytearray)):
                    content = bytes(stream)
                elif hasattr(stream, "read"):
                    content = stream.read()
                elif stream is not None:
                    content = b"".join(stream)
                else:
                    return {"error": "The file could not be read."}, 500
            finally:
                if hasattr(stream, "close"):
                    stream.close()
            return {
                "filename": body.get("filename"),
                "file_type": body.get("file_type"),
                "file_size": body.get("file_size"),
                "content_base64": base64.b64encode(content).decode("ascii"),
            }, 200
        return body, 200

    # ------------------------------------------------------------------
    # Delete — the bytes go with the last reference
    # ------------------------------------------------------------------

    def _before_delete(self, doc: Dict[str, Any]) -> Optional[Refusal]:
        connector, _ = self._doc_connector(doc)
        if connector is None:
            # Unconfigured provider must not make the document undeletable.
            return None
        values = doc.get("values") or {}
        self._drop_bytes_if_last(
            connector, doc.get("resource_id"), values.get("folder"),
            excluding=doc["_id"],
        )
        return None

    def _drop_bytes_if_last(self, connector: FileConnector, storage_id: Any, folder: Any, excluding: Optional[str]) -> None:
        if not storage_id:
            return
        if self.store.others_claiming(str(storage_id), str(excluding or "")):
            return
        body, status = connector.delete(
            {"file_id": storage_id, "folder": folder}
        )
        if status != 200:
            # Orphaned bytes never block a deletion — log and move on.
            self.logger.warning(
                f"Stored bytes for {storage_id} not removed: "
                f"{body.get('error', 'unknown error')}"
            )
