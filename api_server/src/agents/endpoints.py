"""Sources, installs, the agents a person may use, their samples, and the programs runtimes fetch
(api-server.md §5)."""

from __future__ import annotations

import asyncio
from typing import Any

from ..doors.calls import RUNTIME, Caller, Controller, Refused, endpoint
from ..people.permissions import Rules
from ..runtimes.calls import RuntimeCalls
from ..storage.files import PIECE, Storage
from ..utils import Outside, Settings
from .install import Installer
from .samples import Samples
from .store import Agents, Loaded, Sources


class AgentEndpoints(Controller):
    NAME = "Agents:Agent"
    ADMIN = "agents:agent:install"

    def _source(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        source = Sources().get_in(caller.org_id, data.get("source_id"))
        if source is None:
            raise Refused("not_found", "No such source.")
        return source

    def _agent(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        agent = Agents().installed(caller.org_id, str(data.get("agent_id") or ""))
        if agent is None or not (Rules().uses(caller.person, agent) or Rules().allows(caller.person, self.ADMIN) == "allow"):
            raise Refused("not_found", "No such agent you may use.")
        return agent

    @endpoint("See the agents you may use", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        admin, rules = Rules().allows(caller.person, self.ADMIN) == "allow", Rules()
        return {"agents": self._shown([a for a in Agents().col.find({"org_id": caller.org_id})
                                       if admin or (a["state"] == "ready" and rules.uses(caller.person, a))], caller)}

    @endpoint("See one agent and its manifest", changes=False)
    def get(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"agent": self._shown([self._agent(data, caller)], caller)[0]}

    @staticmethod
    def _shown(agents: list[dict[str, Any]], caller: Caller) -> list[dict[str, Any]]:
        """Each with its source's address, and whether the person loaded its samples."""
        urls = {s["_id"]: s["url"] for s in Sources().col.find({"org_id": caller.org_id}, {"url": 1})}
        loaded = {row["agent_id"] for row in Loaded().col.find({"user_id": caller.user_id}, {"agent_id": 1})}
        return [{**Agents.public(a), "source_url": urls.get(a["source_id"], ""), "samples_loaded": a["agent_id"] in loaded}
                for a in agents]

    @endpoint("See the saved sources", changes=False)
    def sources(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"sources": [Sources.public(s) for s in Sources().col.find({"org_id": caller.org_id})],
                "reference": Settings.read().reference_catalog}

    @endpoint("See what the sources offer, against what is installed", changes=False)
    def available(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        installed = {a["agent_id"]: a for a in Agents().col.find({"org_id": caller.org_id})}
        offered = []
        for source in Sources().col.find({"org_id": caller.org_id}):
            for entry in (source.get("catalog") or {}).get("agents") or []:
                info, held = entry["manifest"].get("agent") or {}, installed.get((entry["manifest"].get("agent") or {}).get("id"))
                status = "not_installed" if held is None else "installed" if held["version"] == info.get("version") \
                    else "update_available" if held["source_id"] == source["_id"] else "other_source"
                offered.append({"source_id": source["_id"], "digest": source.get("digest"), "status": status,
                                "manifest": entry["manifest"], "machines": sorted(entry.get("programs") or {}),
                                "base": entry.get("base", ""), "readme": entry.get("readme", ""),
                                "errors": entry.get("errors") or []})
        return {"agents": offered}

    @endpoint("Save a catalog's address as a source, with a credential if it is private")
    async def sourcecreate(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        url = str(data.get("url") or "").strip()
        await self._reachable(url)
        source = {"_id": Sources.new_id(), "org_id": caller.org_id, "url": url, "created_by": caller.user_id}
        source["credential"] = Sources().seal(source["_id"], caller.org_id, self._headers(data.get("credential")))
        await asyncio.to_thread(Sources().col.insert_one, source)
        return {"source": await self._read(source)}

    @endpoint("Change a source; a new address asks for its credential again")
    async def sourceupdate(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        source = await asyncio.to_thread(self._source, data, caller)
        changes: dict[str, Any] = {}
        if "url" in data and str(data["url"]).strip() != source["url"]:
            if source.get("credential") and "credential" not in data:
                raise ValueError("Give the credential again to send it to a new address.")
            changes["url"] = str(data["url"]).strip()
            await self._reachable(changes["url"])
        if "credential" in data:
            changes["credential"] = Sources().seal(source["_id"], caller.org_id, self._headers(data["credential"]))
        await asyncio.to_thread(Sources().col.update_one, {"_id": source["_id"]}, {"$set": changes})
        return {"source": await self._read({**source, **changes})}

    @endpoint("Read a source again")
    async def sourcerefresh(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"source": await self._read(await asyncio.to_thread(self._source, data, caller))}

    @endpoint("Delete a source, uninstalling its agents")
    async def sourcedelete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        source = await asyncio.to_thread(self._source, data, caller)
        await asyncio.to_thread(Agents().col.delete_many, {"source_id": source["_id"]})
        await asyncio.to_thread(Sources().col.delete_one, {"_id": source["_id"]})
        await RuntimeCalls().changed({"org_id": caller.org_id})
        return {}

    @endpoint("Install, or update to, the version of an agent you reviewed")
    async def install(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        source = await asyncio.to_thread(self._source, data, caller)
        agent = await Installer().install(source, str(data.get("agent_id") or ""), str(data.get("version") or ""),
                                          str(data.get("digest") or ""))
        return {"agent": Agents.public(agent)}

    @endpoint("Ask a runtime to check an installed agent again")
    async def check(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        agent = await asyncio.to_thread(self._agent, data, caller)
        return {"agent": Agents.public(await Installer().check(caller.org_id, agent["agent_id"]))}

    @endpoint("Uninstall an agent; its records stay their people's")
    async def delete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        agent = await asyncio.to_thread(self._agent, data, caller)
        await asyncio.to_thread(Agents().col.delete_one, {"_id": agent["_id"]})
        await RuntimeCalls().changed({"org_id": caller.org_id})
        return {}

    @endpoint("See what an agent's samples hold", changes=False)
    def samples(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        agent = self._agent(data, caller)
        return {"samples": Samples().summary(agent) if agent.get("samples") else None}

    @endpoint("Load an agent's samples as records of your own")
    def loadsamples(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        agent = self._agent(data, caller)
        if not agent.get("samples"):
            raise Refused("not_found", "This agent has no samples.")
        return {"loaded": Samples().load(caller.person, agent)}

    @endpoint("Remove the samples you loaded")
    def removesamples(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"removed": Samples().remove(caller.person, str(data.get("agent_id") or ""))}

    @staticmethod
    async def _reachable(url: str) -> None:
        refusal = await Outside.run(Outside.refusal, url)
        if refusal:
            raise ValueError(refusal)

    async def _read(self, source: dict[str, Any]) -> dict[str, Any]:
        read = await Outside.run(Installer().read, source)
        await asyncio.to_thread(Sources().col.update_one, {"_id": source["_id"]}, {"$set": read})
        return Sources.public({**source, **read})

    @staticmethod
    def _headers(raw: Any) -> dict[str, str]:
        """None, a token, or a header and its value."""
        if not raw:
            return {}
        raw = raw if isinstance(raw, dict) else {}
        header, value = str(raw.get("header") or "").strip(), str(raw.get("value") or raw.get("token") or "").strip()
        header, value = (header, value) if header else ("Authorization", f"Bearer {value}")
        if not value or len(value) > 4000 or "\n" in value or "\r" in value or \
                not header.replace("-", "").replace("_", "").isalnum() or header.lower() in ("host", "content-length"):
            raise ValueError("The credential is a token, or a header and its value.")
        return {header: value}


class PackageEndpoints(Controller):
    NAME = "Agents:Package"

    @endpoint("A runtime fetches a program it runs, a piece at a time", by=RUNTIME, changes=False)
    def fetch(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        """Only a program installed in an organization the runtime may serve."""
        key = f"programs/{data.get('digest')}"
        held = [a["org_id"] for a in Agents().col.find({"programs_keys": key}, {"org_id": 1})]
        if not any(RuntimeCalls.may_serve(caller.runtime, org) for org in held):
            raise Refused("not_found", "No such program for this runtime.")
        size = next(a["programs"] for a in Agents().col.find({"programs_keys": key}).limit(1))
        size = next(f["size"] for f in size.values() if f["key"] == key)
        return {"data": Storage.current.read(key, int(data.get("offset") or 0), PIECE), "size": size}
