"""Reading a source's catalog, and installing one of its agents: files checked and kept by digest, the manifest
checked, a runtime's ready check before use (api-server.md §5)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any
from urllib.parse import urljoin, urlsplit

from ..runtimes.calls import RuntimeCalls
from ..storage.files import Storage
from ..utils import MIB, Outside, OutsideRefused
from .manifest import Standard
from .store import Agents, Releases, Sources


class Installer:
    CATALOG_MOST, PROGRAM_MOST, SAMPLES_MOST = 8 * MIB, 1024 * MIB, 64 * MIB

    def read(self, source: dict[str, Any]) -> dict[str, Any]:
        """The catalog as it is now, kept as read, each entry's manifest checked. Nothing in it runs."""
        try:
            answer = Outside(60, self.CATALOG_MOST, Sources().credential(source)).fetch("GET", source["url"])
            if answer.status_code != 200:
                raise OutsideRefused(f"The catalog's address answered {answer.status_code}.")
            catalog = json.loads(answer.content)
        except ValueError as failure:
            return {"error": str(failure) if isinstance(failure, OutsideRefused) else "The catalog is not JSON."}
        errors = Standard.catalog_errors(catalog)
        if errors:
            return {"error": "The catalog is not schema 3: " + "; ".join(errors[:3])}
        for entry in catalog["agents"]:
            entry["errors"] = Standard.manifest_errors(entry["manifest"])
        return {"catalog": catalog, "digest": "sha256:" + hashlib.sha256(answer.content).hexdigest(),
                "read_at": Sources.now(), "error": ""}

    async def install(self, source: dict[str, Any], agent_id: str, version: str, reviewed: str) -> dict[str, Any]:
        """The version reviewed, pinned to the catalog as it was reviewed."""
        if reviewed != source.get("digest"):
            raise ValueError("The source changed since it was reviewed: review it again.")
        entry = next((e for e in (source.get("catalog") or {}).get("agents") or []
                      if e["manifest"].get("agent", {}).get("id") == agent_id
                      and e["manifest"]["agent"].get("version") == version), None)
        if entry is None or entry.get("errors"):
            raise ValueError("; ".join((entry or {}).get("errors") or ["The catalog offers no such agent."])[:600])
        org_id = source["org_id"]
        held = await asyncio.to_thread(Agents().installed, org_id, agent_id)
        if held and held["source_id"] != source["_id"]:
            raise ValueError(f"{agent_id} is installed from another source: one agent id comes from one source.")
        release = Standard.release(entry)
        await asyncio.to_thread(Releases().keep, org_id, agent_id, version, release)
        files = {machine: await self._kept(source, f, self.PROGRAM_MOST, "programs")
                 for machine, f in entry["programs"].items()}
        samples = await self._kept(source, entry["samples"], self.SAMPLES_MOST, "samples") if entry.get("samples") else None
        agent = {"org_id": org_id, "agent_id": agent_id, "version": version, "source_id": source["_id"],
                 "manifest": entry["manifest"], "base": entry.get("base", ""), "readme": entry.get("readme", ""),
                 "programs": files, "programs_keys": [f["key"] for f in files.values()], "samples": samples,
                 "release": release, "state": "checking", "error": "", "installed_at": Agents.now()}
        await asyncio.to_thread(Agents().col.update_one, {"org_id": org_id, "agent_id": agent_id},
                                {"$set": agent, "$setOnInsert": {"_id": Agents.new_id()}}, upsert=True)
        return await self.check(org_id, agent_id)

    async def check(self, org_id: str, agent_id: str) -> dict[str, Any]:
        """A runtime starts it once with no network and waits for a ready that matches; until then it isn't used."""
        agent = await asyncio.to_thread(Agents().installed, org_id, agent_id)
        try:
            said = await RuntimeCalls().ask(org_id, "Runtime:Agent:check", {"agent": {
                "agent_id": agent_id, "version": agent["version"], "manifest": agent["manifest"],
                "base": agent["base"], "programs": agent["programs"]}}, RuntimeCalls.CHECK_SECONDS)
            state, error = ("ready", "") if said.get("ok") is True else ("failed", str(said.get("error") or "")[:500])
        except ValueError as failure:
            state, error = "checking", str(failure)
        await asyncio.to_thread(Agents().col.update_one, {"_id": agent["_id"]}, {"$set": {"state": state, "error": error}})
        if state == "ready":
            await RuntimeCalls().changed({"org_id": org_id})
        return {**agent, "state": state, "error": error}

    async def _kept(self, source: dict[str, Any], file: dict[str, Any], most: int, kind: str) -> dict[str, Any]:
        """A file downloaded once into storage by its digest, checked as it arrives. The source's credential goes to
        the source's own host alone."""
        key = f"{kind}/{file['digest']}"
        if not await asyncio.to_thread(Storage.current.exists, key):
            url = urljoin(source["url"], file["url"])
            own = urlsplit(url)[:2] == urlsplit(source["url"])[:2]
            outside = Outside(1800, min(file["size"], most), Sources().credential(source) if own else None)
            await Outside.run(self._download, outside, url, key, file)
        return {"key": key, "digest": file["digest"], "size": file["size"]}

    @staticmethod
    def _download(outside: Outside, url: str, key: str, file: dict[str, Any]) -> None:
        with Storage.current.writing(key, file["size"]) as writing:
            outside.download(url, writing.write)
            if writing.digest != file["digest"] or writing.size != file["size"]:
                raise ValueError(f"{url} is not the file the catalog names: its digest or size differs.")
