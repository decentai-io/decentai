"""Agent approvals — one document per agent an organization installed.

The manifest is the contract an administrator approved; the package
digest names the exact bytes that answer for it. Qualified ids are the
readable names policies and pages use."""

from __future__ import annotations

import re

from typing import Any, Dict, Optional

from database.stores.iam import OrgScopedStore
from util import new_id, utc_now


class AgentManifestStore(OrgScopedStore):
    """Approved agent manifests — the authoritative record of which agents
    an ORGANIZATION runs, keyed by agent ref.

    The code is shared: one runtime process loads each installed package
    once. The approval is not — the same repository installed by two
    organizations is two rows here, and what a chat may call is its own
    organization's approved set."""

    COLLECTION = "agent_manifests"

    STATUS_INSTALLED = "installed"
    STATUS_DISABLED = "disabled"

    def upsert(
        self,
        org_id: str,
        agent_id: str,
        version: str,
        manifest: Dict[str, Any],
        resources: Dict[str, Any],
        installed_by: str,
        source: Optional[Dict[str, Any]] = None,
        manifest_hash: str = "",
        qualified_id: str = "",
        source_id: str = "",
        local_agent_id: str = "",
        samples: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = utc_now()
        doc = {
            "_id": agent_id,
            # What the agent's sample sheet would load, as the catalog
            # listed it at approval; None when it ships none.
            "samples": samples,
            "org_id": str(org_id or ""),
            # The identity key: which saved source this agent came from,
            # and what that source's catalog calls it. Unique within the
            # organization, and what `ref_for` looks the ref up by so a
            # re-install keeps the identity it already had.
            "source_id": str(source_id or ""),
            "local_agent_id": str(local_agent_id or ""),
            # The readable name policies may use for this agent.
            "qualified_id": qualified_id or agent_id,
            "version": version,
            "status": self.STATUS_INSTALLED,
            "manifest": manifest,
            # Derived canonical mappings (e.g. secrets -> definition_ref).
            "resources": resources,
            # Where the code came from — the repository, ref and commit —
            # and a fingerprint of exactly what was approved.
            "source": source or {},
            "manifest_hash": manifest_hash,
            "installed_by": installed_by,
            "updated_at": now,
        }
        self.col.update_one(
            {"_id": agent_id},
            {"$set": doc, "$setOnInsert": {"installed_at": now}},
            upsert=True,
        )
        return self.col.find_one({"_id": agent_id})

    def list(self, org_id: str) -> list:
        return list(self.col.find({"org_id": str(org_id or "")}).sort("_id", 1))

    def installed_versions(self, org_id: str) -> Dict[str, str]:
        """agent_id -> approved version, for the runtime's scope message.

        One organization's approved set: the runtime holds every installed
        package, and this is the part of it a given chat may reach."""
        return {
            doc["_id"]: doc["version"]
            for doc in self.col.find({
                "org_id": str(org_id or ""), "status": self.STATUS_INSTALLED,
            })
        }

    def installed_in(self, org_id: str, agent_id: str):
        """One approval, only if it stands: the pull door reads this, and
        an uninstalled agent's bytes are not servable."""
        return self.col.find_one({
            "_id": str(agent_id or ""), "org_id": str(org_id or ""),
            "status": self.STATUS_INSTALLED,
        })

    def set_package_digest(self, agent_id: str, digest: str) -> None:
        """Record which bytes answer for this approval — written the
        moment they are stored, before anything is allowed to serve
        them."""
        self.col.update_one(
            {"_id": str(agent_id or "")},
            {"$set": {"package_digest": str(digest or "")}},
        )

    def set_prepared(self, agent_id: str, digest: str, state: str,
                     error: str = "",
                     confined: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
        """What a runtime has said about this approval's code being
        ready to run on it: ``preparing`` when the install sent a
        runtime to build it, ``ready`` or ``failed`` when one answered.
        Always against a digest, so a word about the last version never
        describes the next one. ``confined`` is what that runtime holds
        the agent to, when it said."""
        record = {
            "digest": str(digest or ""), "state": str(state or ""),
            "error": str(error or "")[:400], "at": utc_now(),
        }
        if confined is not None:
            record["confined"] = dict(confined)
        self.col.update_one(
            {"_id": str(agent_id or "")}, {"$set": {"prepared": record}})
        return record

    def put_back(self, doc: Dict[str, Any]) -> None:
        """Overwrite an approval with exactly what it was — the install
        path's rollback when fetching the code fails after the upsert.
        Not the base `restore`, which re-INSERTS a deleted row."""
        self.col.replace_one({"_id": doc["_id"]}, doc)

    def pinned_digests(self, org_id: str) -> set:
        """Every package digest some approval of this organization still
        points at — the reachable set for reclaiming stored packages.
        Status is deliberately ignored: any document naming a digest
        keeps it, whatever state the install is in."""
        return {
            str(doc["package_digest"])
            for doc in self.col.find(
                {"org_id": str(org_id or ""),
                 "package_digest": {"$exists": True, "$ne": ""}},
                {"package_digest": 1},
            )
        }

    def pinned_digests_everywhere(self) -> set:
        """Every package digest ANY organization still points at.

        The one query in this store that deliberately crosses the tenant
        line, because the thing it protects is shared: a runtime holds
        one folder and one virtual environment per digest, serving every
        organization that approved those bytes. Asked per organization
        the answer could never authorise a deletion — one tenant's "I no
        longer pin this" says nothing about the others — so the sweep
        needs the union or it needs to keep everything.

        Digests and nothing else: no organization, no agent, no name.
        A runtime that has this learns only what it could already learn
        by listing its own store directory."""
        return {
            str(doc["package_digest"])
            for doc in self.col.find(
                {"package_digest": {"$exists": True, "$ne": ""}},
                {"package_digest": 1},
            )
        }

    @staticmethod
    def new_ref() -> str:
        """A fresh platform ref.

        `agt_` and the first twenty hex characters of a uuid4: 74 random
        bits, the version and variant marks being fixed. Twenty keeps
        the derived `<agent_ref>__<resource>` slug within the definition
        service's 60-character contract even when the manifest uses its
        maximum 32-character resource id."""
        return f"agt_{new_id()[:20]}"

    def existing_ref(self, org_id: str, source_id: str,
                     local_agent_id: str) -> str:
        """The ref this organization already uses for that source's agent,
        or "" if it has never installed it."""
        if not (source_id and local_agent_id):
            return ""
        doc = self.col.find_one({
            "org_id": str(org_id or ""),
            "source_id": str(source_id),
            "local_agent_id": str(local_agent_id),
        }, {"_id": 1})
        return str(doc["_id"]) if doc else ""

    def ref_for(self, org_id: str, source_id: str,
                local_agent_id: str) -> str:
        """The ref to install this agent under — the one already in use,
        else a new one.

        Minted here rather than on the source document, because a ref
        names an APPROVAL: the same repository saved by two
        organizations gives each its own identity for the same upstream
        agent.

        Two administrators installing the same agent at the same instant
        would mint two refs; the unique index refuses the second, which
        is a clean failure rather than a split identity."""
        return (self.existing_ref(org_id, source_id, local_agent_id)
                or self.new_ref())

    def assign_qualified_id(
        self, org_id: str, agent_ref: str, local_agent_id: str,
        source_name: str = "",
    ) -> str:
        """A name a person can read.

        The platform ref (`agt_7967167a45dc4ae185d5`) is what everything
        is keyed by, and nobody will type it correctly. This is the same
        agent under a name its author chose — bare when it is free, and
        qualified by its source when two repositories offer the same one.

        Assigned once and kept: an id that changed when somebody else
        installed a similarly-named agent would relabel this one under
        everybody's nose. Nothing is KEYED by it — access is granted
        against the ref — so a rename is now cosmetic rather than the
        silent revocation it once would have been."""
        existing = self.col.find_one({"_id": agent_ref}) or {}
        if existing.get("qualified_id"):
            return str(existing["qualified_id"])

        # Unique within the organization that will write policies in it.
        # Another organization installing the same agent gets the same
        # readable name, in its own namespace.
        taken = {
            str(doc.get("qualified_id") or "")
            for doc in self.col.find(
                {"org_id": str(org_id or "")}, {"qualified_id": 1})
            if doc.get("_id") != agent_ref
        }
        candidate = self._slug(local_agent_id) or "agent"
        if candidate not in taken:
            return candidate

        qualified = f"{self._slug(source_name) or 'source'}__{candidate}"
        if qualified not in taken:
            return qualified
        # Two sources with the same name offering the same agent: rare
        # enough to end in the ref's own tail rather than a counter.
        return f"{qualified}_{str(agent_ref)[-4:]}"

    @staticmethod
    def _slug(value: str) -> str:
        cleaned = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
        cleaned = re.sub(r"_+", "_", cleaned)[:40]
        return cleaned if re.fullmatch(r"[a-z][a-z0-9_]*", cleaned or "") else ""

    def qualified_ids(self, org_id: str) -> dict:
        """{readable name: platform ref} for every installed agent.

        Names are unique WITHIN an organization, which is what lets two
        of them both call their copy `notebook`. Nothing resolves through
        this on a hot path any more — a grant is written against the ref
        — so it is the readable-name index, not an identity map."""
        return {
            str(doc["qualified_id"]): str(doc["_id"])
            for doc in self.col.find(
                {"org_id": str(org_id or ""), "status": self.STATUS_INSTALLED},
                {"qualified_id": 1},
            )
            if doc.get("qualified_id")
        }

    def installed_from_source(self, org_id: str, source_id: str) -> list:
        """Installed agents whose code came from one saved source.

        A source is how an installed agent is recognised again: its ref
        is looked up by (organization, source, local id), so the same
        repository re-added under a fresh source document mints fresh
        refs that can never be matched to what is already approved —
        along with the secret definitions derived under them. Hence the
        deletion guard that calls this."""
        source_id = str(source_id or "").strip()
        if not source_id:
            return []
        return [
            {"agent_ref": str(doc.get("_id") or ""),
             "name": str(((doc.get("manifest") or {}).get("agent") or {})
                         .get("name") or doc.get("_id") or ""),
             "version": str(doc.get("version") or "")}
            for doc in self.col.find({
                "org_id": str(org_id or ""),
                "status": self.STATUS_INSTALLED,
                "source_id": source_id,
            })
        ]

    def agents_pinning_secret_definition(
        self, org_id: str, definition_id: str,
    ) -> dict:
        """Installed agents that approved this family, and the version each
        one pinned. An agent resolves the exact ref it approved and never a
        "latest", so this is precisely what publishing a new version will
        NOT reach."""
        family = str(definition_id or "").strip().lower()
        pins: dict = {}
        for doc in self.col.find({
            "org_id": str(org_id or ""), "status": self.STATUS_INSTALLED,
        }):
            agent_id = str(doc.get("_id") or "")
            if not agent_id:
                continue
            refs = ((doc.get("resources") or {}).get("secrets") or {}).values()
            for ref in refs:
                slug, _, version = str(ref).partition("/v")
                # A ref names the organization that owns the definition
                # (`<org_id>:<slug>/vN`); the family is the slug alone. An
                # install stores exactly that form, so comparing the whole
                # left half matched nothing real — the definitions page
                # reported no agent uses, and offered to delete a shape an
                # installed agent was standing on.
                if slug.rpartition(":")[2].strip().lower() == family:
                    pins[agent_id] = int(version) if version.isdigit() else 1
        return pins
