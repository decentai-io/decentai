"""The observed actions: what the assistant does when a beat chose one
of them, and what the model reads of it at the next beat.

The cycle (assistant.py, ``_act``) names the method for each action in
``OBSERVED_ACTIONS`` and observes what it returns. None of these ends
the turn; the four that can — say, finish, invoke, sleep — are read in
``_act`` itself, and ``sleep`` only comes here for the clock. Each
method is a function of the action and the mind's state, and reaches
the chat through the doors it was handed (``self.sinks``, ``self.clock``).

The Assistant holds one as ``mind.acts``, and it holds the
assistant as ``self.mind``: a name read here that is not defined here is
the assistant's.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any, Dict, Optional

from ai_runtime.mind import observations
from ai_runtime.mind.documents import DocumentPage, DocumentText, Unreadable
from ai_runtime.mind.state import DONE


class Acts:
    def __init__(self, mind):
        #: The assistant this is a part of; what is not here is its.
        self.mind = mind

    async def open_agent(self, action: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = str(action.get("agent") or "")
        agent = self.mind.agents.get(agent_id)
        if agent is None:
            return {"error": f"No agent '{agent_id}' is available. "
                             f"{self.mind.available()}"}
        answer: Dict[str, Any] = {"agent": agent_id}
        if agent_id in self.mind.state.opened:
            self.mind.state.touch_agent(agent_id)
        else:
            if len(self.mind.state.opened) >= self.mind.open_max():
                evicted = self.mind.state.opened.pop(0)
                answer["closed"] = evicted
                answer["note"] = (f"'{evicted}' was closed to make room — the "
                                  f"least recently used of {self.mind.open_max()} "
                                  f"open agents. Open it again if needed.")
            self.mind.state.open_agent(agent_id)
        answer["instructions"] = agent.manifest.instructions or "(none)"
        answer["catalog"] = self.mind.render_catalog(agent)
        return answer

    def close_agent(self, action: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = str(action.get("agent") or "")
        if not self.mind.state.close_agent(agent_id):
            return {"error": f"Agent '{agent_id}' is not open."}
        return {"closed": agent_id, "open": list(self.mind.state.opened)}

    async def find_agents(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """The installed agents closest in meaning to a few words, in
        any language, best first — the router's search. Without an
        embedding model there is nothing to search by, and every agent
        is under AGENTS already."""
        query = str(action.get("query") or "")
        embedding = self.mind.routing.get("embedding")
        if self.mind.router is None or not isinstance(embedding, dict):
            return {"installed": len(self.mind.agents),
                    "agents": [{"id": agent_id, "name": agent.manifest.name}
                               for agent_id, agent
                               in sorted(self.mind.agents.items())],
                    "note": "No embedding model is configured for agent routing, "
                            "so every agent is listed under AGENTS."}
        try:
            found = await self.mind.router.find(self.mind.agents, query, embedding,
                                                meter=self.mind.meter)
        except Exception as exc:
            found = None
            self.mind.logger.warning(f"find_agents failed: {exc}")
        if found is None:
            return {"error": "The embedding model did not answer; every agent "
                             "is listed under AGENTS for now."}
        return {"installed": len(self.mind.agents), "matched": len(found),
                "agents": found,
                "note": "open_agent takes any id, listed here or not."}

    # -- read ------------------------------------------------------------
    async def read(self, action: Dict[str, Any]) -> Dict[str, Any]:
        ref = str(action.get("storage_ref") or "").strip()
        path = str(action.get("path") or "").strip()
        if not ref:
            return {"status": "error",
                    "result": {"error": "read needs a storage_ref."}}
        # The same door the executor resolves a reference input by.
        read = self.mind.sinks.read_result
        if read is None:
            return {"status": "error", "result": {
                "error": "Stored results cannot be read back here."}}
        try:
            value = await read(ref, path)
        except Exception as exc:
            return {"status": "error", "result": {
                "error": f"The stored result could not be read: {exc}"}}

        # A long list is read in pages: `from` skips what an earlier read
        # showed, so the model never has to walk it one index at a time.
        start = action.get("from")
        start = int(start) if isinstance(start, int) and start > 0 else 0
        if start and isinstance(value, list):
            value = value[start:]

        payload = {"storage_ref": ref, "path": path, "value": value}
        if start:
            payload["from"] = start
        observation = {"status": "success", "result": payload}
        most = self.mind.OBSERVATION_MAX_CHARS
        if len(json.dumps(observation, default=str)) > most:
            payload["value"] = observations.preview(value)
            payload["truncated"] = True
            if isinstance(value, list):
                shown = int(payload["value"].get("items_shown") or 0)
                payload["note"] = (
                    f"Showing {shown} of {len(value)} items"
                    f"{' from ' + str(start) if start else ''}. Read the same "
                    f"path again with \"from\": {start + shown} for the rest.")
            else:
                payload["note"] = ("Still too large to show whole — narrow "
                                   "the path further.")
        return observation

    # -- skills / memory / plan ------------------------------------------
    SKILL_BODY_MAX_CHARS = 6000

    async def find_files(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """The file the user means, when nothing is attached: the
        session looks among what they can see and puts the best few on
        a card; the user decides. What they chose is attached to the
        chat and enters the transcript exactly as an attachment does,
        so a picture among them is shown at the next call."""
        query = str(action.get("query") or "").strip()
        if not query:
            return {"error": "find_files needs a query: the file as the "
                             "user described it."}
        if self.mind.sinks.find_files is None:
            return {"error": "Files cannot be looked up in this chat."}
        try:
            outcome = await self.mind.sinks.find_files(
                query, action.get("names"),
                str(action.get("kind") or "")) or {}
        except Exception as exc:
            return {"error": f"Files could not be looked up: {exc}"}
        status = str(outcome.get("status") or "")
        files = [f for f in (outcome.get("files") or []) if isinstance(f, dict)]
        if status == "chosen" and files:
            lines, images = self.mind.pictures.attached(files)
            said: Dict[str, Any] = {
                "role": "user",
                "content": "The user chose these files:\n" + "\n".join(lines),
            }
            if images:
                said["images"] = images
            self.mind.state.messages.append(said)
            return {
                "files": [{"filename": f.get("filename") or "",
                           "file_type": f.get("file_type") or "",
                           "file_ref": f["resource_ref"]} for f in files],
                "note": "Attached to this chat. Pass a file_ref to a "
                        "function whose input takes a file; a picture "
                        "among them is shown to you.",
            }
        if status in ("chosen", "declined"):
            return {"files": [], "note": "The user chose no files. Ask "
                                         "what they meant, or carry on "
                                         "without."}
        if status == "expired":
            return {"files": [], "note": "Nobody answered the files card."}
        return {"error": str(outcome.get("error")
                             or "Files could not be looked up.")}

    #: A document larger than this is not read here: the bytes have to
    #: cross the gateway whole, base64, and a report is not that big.
    FILE_READ_MAX_BYTES = 8 * 1024 * 1024

    async def read_file(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """A document the user attached or chose, read as text a page at
        a time (mind/documents.py). The download runs under the
        person's own delegation, so the backend's visibility rules —
        not this method — decide what a ref may reach; a ref the model
        made up finds nothing."""
        ref = str(action.get("file_ref") or "").strip()
        if not ref:
            return {"error": "read_file needs a file_ref, from an "
                             "[attached: …] line."}
        reader = self.mind.sinks.read_file or self.mind.sinks.read_image
        if reader is None:
            return {"error": "Files cannot be read in this chat."}
        try:
            record = await reader(ref) or {}
        except Exception as exc:
            return {"error": f"The file could not be read: {exc}"}
        encoded = str(record.get("content_base64") or "")
        if not encoded:
            return {"error": f"No file '{ref}' is visible to this user. "
                             f"Use a file_ref from an [attached: …] line, "
                             f"or find_files."}
        size = int(record.get("file_size") or 0) or (len(encoded) * 3) // 4
        filename = str(record.get("filename") or "")
        file_type = str(record.get("file_type") or "")
        if size > self.FILE_READ_MAX_BYTES:
            return {"file_ref": ref, "filename": filename,
                    "error": f"This file is {size} bytes — too large to "
                             f"read here. Hand its file_ref to an agent "
                             f"that reads it."}
        try:
            # Off the loop: a long PDF takes seconds to read, and this
            # loop serves every chat on the host.
            text = await asyncio.to_thread(
                DocumentText.extract,
                base64.b64decode(encoded), file_type, filename)
        except Unreadable as exc:
            return {"file_ref": ref, "filename": filename,
                    "file_type": file_type, "error": str(exc)}
        except Exception as exc:
            return {"file_ref": ref, "filename": filename,
                    "error": f"The file could not be read: {exc}"}
        start = action.get("from")
        start = int(start) if isinstance(start, int) and start > 0 else 0
        return {"file_ref": ref, "filename": filename,
                "file_type": file_type, **DocumentPage.of(text, start)}

    #: entries one recall answers with, newest first
    RECALL_MAX_ENTRIES = 20

    def recall(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """What fell out of the summary, searched by words: every entry
        carrying all of them, newest first; with no words, the newest.
        The archive is the mind's own state, so this reads nothing
        from anywhere and costs no call."""
        archive = list(self.mind.state.archive)
        if not archive:
            return {"archived": 0, "entries": [],
                    "note": "Nothing has fallen out of this conversation's "
                            "summary yet; the summary above is complete."}
        words = [w for w in str(action.get("query") or "").lower().split() if w]
        found = [entry for entry in archive
                 if all(w in str(entry.get("line") or "").lower()
                        or w in str(entry.get("section") or "").lower()
                        for w in words)]
        newest = list(reversed(found))[: self.RECALL_MAX_ENTRIES]
        return {"archived": len(archive), "matched": len(found),
                "entries": newest,
                "note": ("Each entry says when it was last in the summary "
                         "and which section held it; it may since have been "
                         "superseded by what the summary says now.")}

    async def use_skill(self, action: Dict[str, Any]) -> Dict[str, Any]:
        ref = str(action.get("skill") or "").strip()
        if not ref:
            return {"error": "use_skill needs a skill ref."}
        if self.mind.sinks.read_skill is None:
            return {"error": "No skills are available in this chat."}
        try:
            skill = await self.mind.sinks.read_skill(ref)
        except Exception as exc:
            return {"error": f"The skill could not be read: {exc}"}
        if not skill:
            return {"error": f"No skill '{ref}' is visible to this user."}

        body = str(skill.get("body") or "")
        if len(body) > self.SKILL_BODY_MAX_CHARS:
            body = (body[: self.SKILL_BODY_MAX_CHARS]
                    + "\n…(truncated — the skill is longer than fits here)")
        return {"skill": ref, "title": str(skill.get("title") or ""),
                "text": body}

    #: What a helper is told when it asks for the clock. A schedule is
    #: the chat's: a row of a helper's own would be nobody's, and the
    #: backend refuses it — said here, in words the helper can act on,
    #: and not as the refusal of a write.
    NOT_A_HELPERS = ("A schedule is the chat's, and not a helper's to "
                     "set or remove. Say in your report what should be "
                     "scheduled; the assistant you work for can.")

    def _no_clock(self) -> Optional[Dict[str, Any]]:
        """Why the clock is not this mind's to use, or None: a helper
        has none to set, and a chat may be served by none."""
        if self.mind.sinks.finish is not None:
            return {"error": self.NOT_A_HELPERS}
        if self.mind.clock is None:
            return {"error": "No clock serves this chat."}
        return None

    async def schedule(self, action: Dict[str, Any]) -> Dict[str, Any]:
        refused = self._no_clock()
        if refused:
            return refused
        spec = {k: v for k, v in action.items() if k != "action"}
        try:
            return await self.mind.clock.schedule(spec)
        except Exception as exc:
            return {"error": f"The schedule could not be set: {exc}"}

    async def sleep(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Pause until a moment from now, and be woken then with the
        reason. Not a helper's to do: a helper works to its end and
        reports."""
        refused = self._no_clock()
        if refused:
            return refused
        return await self.mind.clock.sleep(
            action.get("seconds"), str(action.get("why") or ""))

    async def unschedule(self, action: Dict[str, Any]) -> Dict[str, Any]:
        schedule_id = str(action.get("schedule_id") or "").strip()
        if not schedule_id:
            return {"error": "unschedule needs a schedule_id."}
        refused = self._no_clock()
        if refused:
            return refused
        try:
            return await self.mind.clock.unschedule(schedule_id)
        except Exception as exc:
            return {"error": f"The schedule could not be removed: {exc}"}

    async def remember(self, action: Dict[str, Any]) -> Dict[str, Any]:
        text = str(action.get("text") or "").strip()
        if not text:
            return {"error": "remember needs text."}
        if self.mind.sinks.remember is None:
            return {"error": "Memory is not available in this chat."}
        if any(text == existing for existing in self.mind.memories):
            return {"text": "That is already remembered — carry on."}
        try:
            await self.mind.sinks.remember(text)
        except Exception as exc:
            return {"error": f"The memory could not be saved: {exc}"}
        self.mind.memories.append(text)
        return {"text": f"Remembered, and the user can see it: {text}"}

    async def plan(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Set the work items, or update one of them."""
        if "steps" in action:
            steps = action.get("steps")
            if not isinstance(steps, list):
                return {"error": "plan needs a steps list."}
            texts = [str(s).strip() for s in steps if str(s).strip()]
            if not texts:
                return {"error": "A plan needs at least one item."}
            most = self.mind.state.plan.MAX_STEPS
            if len(texts) > most:
                return {"error": f"A plan holds at most {most} items "
                                 f"— group the work into fewer."}
            self.mind.state.plan.replace(texts)
        else:
            ref = action.get("item") or action.get("step")
            ok, why = self.mind.state.plan.update(
                ref, str(action.get("status") or ""),
                evidence=action.get("evidence"),
                blocker=str(action.get("blocker") or ""),
                depends_on=action.get("depends_on"),
                proven=self.proven(),
            )
            if not ok:
                return {"error": why}

        why = await self.show_plan()
        if why:
            return {"error": why}
        return {"text": "Plan recorded — the user sees it.",
                "plan": self.mind.state.plan.to_steps()}

    async def show_plan(self) -> Optional[str]:
        """The plan as it now stands, to the audience. The model reads
        it on its next call without anything rewritten: the plan is
        put after the conversation for every request
        (Assistant._request), never kept in the frame."""
        if self.mind.sinks.plan is not None:
            try:
                await self.mind.sinks.plan(self.mind.state.plan.to_steps())
            except Exception as exc:
                return f"The plan could not be shown: {exc}"
        return None

    def proven(self) -> set:
        """What the trace can vouch for: storage refs of successful
        invocations, ids of finished jobs. The only evidence a plan
        item may name."""
        proven = {
            entry["result"]["storage_ref"] for entry in self.mind.state.trace
            if entry.get("status") == "success"
            and isinstance(entry.get("result"), dict)
            and isinstance(entry["result"].get("storage_ref"), str)
        }
        proven.update(job_id for job_id, job in self.mind.state.jobs.items()
                      if job.status == DONE)
        return proven
