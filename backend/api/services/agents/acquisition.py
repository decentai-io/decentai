"""Getting an agent's code, and keeping it.

This is the seam the runtime used to sit behind. An install now goes:

    open the source  ->  package the one agent's folder
                     ->  store it under (organization, digest)

and every later need for that code — a container recreated without its
volume, a second runtime process, a reinstall — is answered from the
store rather than from the upstream repository. A repository that was
deleted, made private, or force-pushed stops being able to take a
running agent away, which is the whole reason acquisition moved.

Nothing here clones anything itself. A source (sources.py) opens itself
into a folder and says which commit it was; from there a catalog is a
catalog and an agent's folder is an agent's folder.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from database.agent_packages import PackageStore
from api.services.agents.repository import Repository, RepositoryError
from api.services.agents.sources import GitSource
from contracts.agent_manifest import ManifestValidator
from contracts.agent_package import AgentPackage, PackagingError
from server.custom_logging import CustomLoggerFactory


class AcquisitionError(RuntimeError):
    """The code could not be got. Written for an administrator to read."""


class AgentAcquisition:
    """What is done with a source once it is open."""

    def __init__(self, store: Optional[PackageStore] = None):
        self.store = store or PackageStore()
        self.repository = Repository()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def inspect(self, source: GitSource) -> Dict[str, Any]:
        """What a repository offers, without running any of it.

        Open it, read the catalog, throw the folder away. Nothing is
        imported, nothing is stored, nothing is approved — this is the
        document an administrator reads before deciding."""
        try:
            with source.open() as (folder, sha):
                catalog = self.repository.discover(folder)
        except RepositoryError as exc:
            raise AcquisitionError(str(exc)) from exc
        except Exception as exc:  # yaml, OS errors — same audience
            raise AcquisitionError(source.unreadable(exc)) from exc

        return self._offering(sha, catalog)

    def acquire(self, org_id: str, source: GitSource, local_agent_id: str,
                catalog_path: str = ".") -> Tuple[bytes, str, Dict[str, Any]]:
        """Take one agent out of a repository and keep it.

        Returns (archive, digest, manifest). The archive is that agent's
        OWN folder, not the repository it arrived in: what gets approved,
        stored and imported is one agent, so a monorepo of ten agents
        becomes ten packages that can be updated and removed
        independently.
        """
        try:
            with source.open() as (folder, _):
                catalog = self.repository.discover(folder)
                selected = self._select(
                    catalog, local_agent_id, catalog_path,
                    source.missing_agent(local_agent_id, catalog_path))
                archive, digest = AgentPackage.build(
                    (folder / selected["path"]).resolve())
        except (RepositoryError, PackagingError) as exc:
            raise AcquisitionError(str(exc)) from exc

        # Deliberately after the source has closed: the bytes are in
        # hand, and storing them must not depend on a checkout that is
        # already being deleted.
        self.store.put(org_id, digest, archive)
        return archive, digest, selected["manifest"]

    def approved_package(self, org_id: str, digest: str) -> bytes:
        """The exact bytes an approval names.

        The only way code reaches the runtime after the first install.
        A missing package is a plain error rather than a silent re-clone:
        re-fetching would quietly reintroduce whatever the repository
        holds NOW under an approval made for what it held then."""
        try:
            return self.store.get(org_id, digest)
        except PackagingError as exc:
            raise AcquisitionError(str(exc)) from exc

    def reclaim(self, org_id: str, pinned) -> int:
        """Delete this organization's stored packages that no approval
        points at any more. `pinned` is the reachable set — every
        `package_digest` on its manifests — and comes from the caller so
        this service never reads the database. Uninstalls and upgrades
        are what strand packages, so those two paths call this."""
        keep = {str(digest) for digest in pinned if digest}
        removed = 0
        for digest in self.store.list_digests(org_id):
            if digest not in keep and self.store.delete(org_id, digest):
                removed += 1
        return removed

    # ------------------------------------------------------------------
    @staticmethod
    def _offering(sha: str, catalog: Dict[str, Any]) -> Dict[str, Any]:
        """What an administrator reads before deciding.

        The manifests are validated but NOT refused here: a catalog whose
        third agent is malformed still offers the other two, and the
        errors travel beside each entry for the page to show."""
        for entry in catalog["agents"]:
            entry["errors"] = ManifestValidator().validate(
                entry["manifest"])[:10]
        return {"sha": sha, "catalog": catalog}

    @staticmethod
    def _select(catalog: Dict[str, Any], local_agent_id: str,
                catalog_path: str, missing: str) -> Dict[str, Any]:
        """The one catalog entry being installed — matched on id AND path,
        so a repository that lists the same id twice cannot have one
        entry's approval fetch the other one's folder."""
        selected = next(
            (item for item in catalog["agents"]
             if item["id"] == local_agent_id
             and item["path"] == (catalog_path or ".")),
            None,
        )
        if selected is None:
            raise AcquisitionError(missing)
        return selected
