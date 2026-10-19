"""The sample data an installed agent ships, loaded for the person who
asks and taken back on request.

Loading reads the sheet from the exact package that was approved —
never from the repository as it is now — and writes each row through
the same door a person's own record uses, so every sample is validated
against the installed manifest. What was minted is remembered per
person and per agent; removal deletes that and nothing else.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from api.services.agents.acquisition import AcquisitionError
from api.services.data_layer.files import FileController
from api.services.data_layer.records import DataController
from contracts.agent_package import AgentPackage
from contracts.agent_samples import SampleSheet, resolve_pointer
from database.stores.agents.samples import AgentSampleStore


class AgentSamplesMixin:
    """Mixed into AgentController: uses its store, acquisition and helpers."""

    def _sample_sheet(self, approved: Dict[str, Any]) -> Optional[SampleSheet]:
        """The sheet inside the approved package, or None when the agent
        ships none."""
        digest = str(approved.get("package_digest") or "")
        if not digest:
            return None
        archive = self.acquisition.approved_package(approved["org_id"], digest)
        with tempfile.TemporaryDirectory(prefix="decentai-samples-") as scratch:
            folder = Path(scratch)
            AgentPackage.extract(archive, folder)
            sheet = SampleSheet.read(folder, approved.get("manifest") or {})
            if sheet is None:
                return None
            # The bytes are read now, while the folder exists; the sheet
            # outlives it.
            for item in sheet.files:
                item["bytes"] = (folder / item["path"]).read_bytes()
            return sheet

    def _installed_agent(self, data, user):
        payload = self._payload(data)
        agent_ref = str(payload.get("agent_id") or "")
        approved = self.store.installed_in(self._org(user), agent_ref)
        if approved is None:
            return None, self._fail(data, "not_found", "Agent is not installed.", 404)
        return approved, None

    # ── endpoints ─────────────────────────────────────────────────────
    def samples(self, data, user):
        """What loading would do for this agent, and whether this person
        already did."""
        approved, refusal = self._installed_agent(data, user)
        if refusal:
            return refusal
        try:
            sheet = self._sample_sheet(approved)
        except AcquisitionError as exc:
            return self._fail(data, "unavailable", str(exc), 503)
        loaded = AgentSampleStore().get(approved["org_id"], approved["_id"],
                                        str(user.get("user_id") or ""))
        return self._respond(data, {
            "agent_id": approved["_id"],
            "samples": sheet.summary() if sheet else None,
            "loaded": AgentSampleStore.to_public(loaded),
        })

    def loadsamples(self, data, user):
        approved, refusal = self._installed_agent(data, user)
        if refusal:
            return refusal
        registry = AgentSampleStore()
        user_id = str(user.get("user_id") or "")
        if registry.get(approved["org_id"], approved["_id"], user_id):
            return self._fail(data, "already_loaded",
                              "You already loaded this agent's sample data. Remove it first "
                              "to load it again.", 409)
        try:
            sheet = self._sample_sheet(approved)
        except AcquisitionError as exc:
            return self._fail(data, "unavailable", str(exc), 503)
        if sheet is None:
            return self._fail(data, "no_samples", "This agent ships no sample data.", 404)
        if not sheet.ok:
            return self._fail(data, "invalid",
                              "This agent's sample sheet is not valid: " + "; ".join(sheet.errors), 400)

        ids: Dict[str, str] = {}
        file_refs: List[str] = []
        record_refs: List[str] = []
        files, records = FileController(), DataController()
        failures: List[str] = []
        try:
            for item in sheet.files:
                body, status = files.upload({"data": {
                    "filename": item["filename"],
                    "file_bytes": io.BytesIO(item["bytes"]),
                    "folder": "samples",
                    "meta": {"category": f"{approved['_id']}__{item['slot']}"},
                }}, user, agent_samples=True)
                if status != 200:
                    failures.append(f"{item['filename']}: {body.get('error', 'not stored')}")
                    break
                ref = str(body["resource"]["resource_ref"])
                ids[item["ref"]] = ref
                file_refs.append(ref)
            if not failures:
                for index, row in enumerate(sheet.records):
                    fields = {name: resolve_pointer(value, ids) for name, value in row["fields"].items()}
                    body, status = records.create({"data": {
                        "resource_id": f"{approved['_id']}__{row['slot']}",
                        "fields": fields,
                    }}, user, agent_samples=True)
                    if status != 200:
                        failures.append(f"{row['slot']} #{index + 1}: {body.get('error', 'refused')}")
                        break
                    ref = str(body["resource"]["resource_ref"])
                    if row.get("ref"):
                        ids[row["ref"]] = ref
                    record_refs.append(ref)
        except Exception as exc:  # a half-load is worse than none
            failures.append(str(exc))

        if failures:
            self._take_back(user, record_refs, file_refs)
            return self._fail(data, "refused",
                              "The sample data could not be loaded, and what had been "
                              "written was removed again: " + "; ".join(failures), 400)

        registry.remember(approved["org_id"], approved["_id"], user_id,
                          record_refs=record_refs, file_refs=file_refs)
        self._audit(user, "agent.samples_loaded", approved, len(record_refs), len(file_refs))
        return self._respond(data, {
            "agent_id": approved["_id"],
            "loaded": {"records": len(record_refs), "files": len(file_refs)},
        })

    def removesamples(self, data, user):
        approved, refusal = self._installed_agent(data, user)
        if refusal:
            return refusal
        registry = AgentSampleStore()
        user_id = str(user.get("user_id") or "")
        loaded = registry.get(approved["org_id"], approved["_id"], user_id)
        if loaded is None:
            return self._fail(data, "not_loaded", "You have not loaded this agent's sample data.", 404)
        removed = self._take_back(user, list(loaded.get("record_refs") or []),
                                  list(loaded.get("file_refs") or []))
        registry.forget(approved["org_id"], approved["_id"], user_id)
        self._audit(user, "agent.samples_removed", approved, removed["records"], removed["files"])
        return self._respond(data, {"agent_id": approved["_id"], "removed": removed})

    # ── helpers ───────────────────────────────────────────────────────
    @staticmethod
    def _take_back(user, record_refs: List[str], file_refs: List[str]) -> Dict[str, int]:
        """Delete what a load minted, through the same doors, so bytes go
        with their last reference. A row the person already deleted is
        simply not there any more."""
        files, records = FileController(), DataController()
        counts = {"records": 0, "files": 0}
        for ref in reversed(record_refs):
            _, status = records.delete({"data": {"resource_ref": ref}}, user)
            counts["records"] += int(status == 200)
        for ref in reversed(file_refs):
            _, status = files.delete({"data": {"resource_ref": ref}}, user)
            counts["files"] += int(status == 200)
        return counts

    def _audit(self, user, event: str, approved: Dict[str, Any], records: int, files: int) -> None:
        from database.stores import AuditStore
        AuditStore().append(event, user, resource_refs=[approved["_id"]],
                            details={"agent": (approved.get("manifest") or {}).get("agent", {}).get("name", ""),
                                     "records": records, "files": files})
