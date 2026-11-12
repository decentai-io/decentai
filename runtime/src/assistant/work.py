"""The assistant's work log: every call, task, question and offer it makes, begun, run, waiting on the person, ended,
and kept with its output, the files it returned and the records its program touched (assistant.md §3)."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from ..controllers.api_servers import ApiServers
from ..controllers.process import Process
from .history import SHOWN, cut, text
from .thread import DONE, FAILED, RUNNING, STOPPED, UNKNOWN, WAITING, Thread

log = logging.getLogger("runtime")


def strings(value: Any) -> set[str]:
    """Every text in a value, however deep."""
    if isinstance(value, str):
        return {value}
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    return set().union(*(strings(item) for item in items))


class Work:
    """The log, by id: each entry its ``thread`` (none for work nothing waits for), ``origin``, ``kind``, ``title``,
    ``status`` (running, waiting for the person, done, failed, stopped or unknown), ``card``, ``inputs``, ``output``
    (its ref, size and first line), ``files``, ``records``, and when it ``started`` and ``ended``. Entries are never
    removed: past the state's size, the oldest keep only their refs. Outputs are kept whole, apart from the state."""

    #: Tasks running at once; inputs kept in the log as they are up to this many characters, past it by their ref;
    #: outputs kept whole up to what an API server takes.
    TASKS_MOST, INPUTS_MOST, OUTPUT_MOST = 4, 16_000, 2 * 1024 * 1024

    def __init__(self, assistant: Any, entries: list[dict[str, Any]]):
        self.assistant, self.chat = assistant, assistant.chat
        self.entries: dict[str, dict[str, Any]] = {entry["id"]: entry for entry in entries}
        self.jobs: dict[str, Any] = {}

    # -- beginning ------------------------------------------------------------------------------------

    def begin(self, thread: Thread | None, call: dict[str, Any] | None, kind: str, title: str, inputs: Any = None,
              origin: str = "") -> dict[str, Any]:
        """A piece of work begun for one of a thought's calls, and waited for by its thread; or, with no thread, work
        nothing waits for."""
        assistant = self.assistant
        id = assistant.next("t" if kind == "task" else "c")
        origin = origin or thread.origin
        # a run's own: a scheduled function nothing waits for is a run of its own
        run = thread.in_run if thread else {"thread": id, "origin": origin, "title": title[:200], "message": 0}
        entry = {"id": id, "thread": thread.id if thread else "", "origin": origin, "run": run, "kind": kind,
                 "title": title[:200], "tool": call["id"] if call else "", "status": RUNNING, "card": "",
                 "before": False, "started": assistant.now(), "ended": "", "inputs": None, "output": None,
                 "files": [], "records": []}
        if inputs is not None and len(text(inputs)) > self.INPUTS_MOST:
            assistant.later(self._inputs(entry, inputs))
        elif inputs is not None:
            entry["inputs"] = inputs
        self.entries[id] = entry
        if thread is not None:
            thread.waiting.append(id)
        self.told(entry)
        return entry

    def told(self, entry: dict[str, Any]) -> None:
        """An entry as the chat's page is told it, as it begins, waits, ends and has its output kept: everything but
        its inputs whole."""
        shown = {key: entry.get(key) for key in ("id", "title", "status", "kind", "origin", "thread", "run", "means",
                                                 "why", "card", "started", "ended", "output", "files", "records")}
        shown["inputs"] = cut(entry["inputs"], 600) if entry.get("inputs") is not None else None
        self.assistant.event("work", shown)

    def calling(self, thread: Thread, call: dict[str, Any], means: tuple, inputs: dict[str, Any],
                why: str) -> tuple[dict[str, Any], Callable]:
        """An agent's function or an MCP tool, as work: ``why`` is shown to the person with what they approve."""
        entry = self.begin(thread, call, "call" if means[0] == "agent" else "mcp", means[3], inputs)
        entry["means"] = {"agent": means[1], "function": means[2]} if means[0] == "agent" else \
            {"server": means[1]["id"], "tool": means[2]}
        entry["why"] = why[:1000]
        return entry, self.calls(entry, inputs)

    def calls(self, entry: dict[str, Any], inputs: dict[str, Any] | None = None) -> Callable:
        """The job of an agent's function or an MCP tool, made from its entry, so it can run anew after a restart, on
        the card it waited on; at its own trust if it has one, else its thread's."""
        async def job() -> Any:
            given = inputs if inputs is not None else await self._inputs_back(entry)
            means, thread = entry["means"], self.assistant.threads.get(entry["thread"]) if entry["tool"] else None
            asked = {"trust": entry["trust"] if "trust" in entry else thread.trust if thread else None,
                     "work": entry["id"], "why": entry.get("why", ""), "origin": entry["origin"],
                     "card": entry["card"] if entry.get("before") else ""}
            if "agent" in means:
                named = strings(given)
                return await self.chat.call(means["agent"], means["function"], given,
                                            [f for f in self.assistant.known if f in named], **asked)
            server = next((s for s in self.chat.contract["assistant"]["mcp_servers"] if s["id"] == means["server"]),
                          None)
            if server is None:
                raise Process.Failed("not_found", "That MCP server is no longer the person's.")
            answer = await self.chat.mcp(server, means["tool"], given, **asked)
            if answer["failed"]:
                raise Process.Failed("failed", answer["text"] or "The tool failed.")
            return answer["structured"] or answer["text"]
        return job

    def asking(self, entry: dict[str, Any]) -> Callable:
        """The job of the assistant's own question: on its card, however long the person takes."""
        async def job() -> str:
            request = entry["inputs"]
            answer = await self.chat.ask(request, work=entry["id"], card=entry.get("card", ""))
            found = answer.get(request["expects"]) if isinstance(answer, dict) else None
            return str(found) if found is not None else "The person dismissed the question."
        return job

    def offering(self, kind: str, request: dict[str, Any]) -> Callable:
        async def job() -> str:
            await self.chat.offer(kind, request)
            return "Offered: the person decides."
        return job

    def task(self, thread: Thread, call: dict[str, Any], brief: str, title: str) -> Any:
        """A task of the thread's: a thread of its own, at the same origin and trust."""
        threads = self.assistant.threads
        running = sum(1 for t in threads.values() if t.kind == "task" and t.status == RUNNING)
        if running >= self.TASKS_MOST:
            return f"Not now: {running} tasks are running; wait for one to end."
        entry = self.begin(thread, call, "task", f"Task · {title}")
        task = threads[entry["id"]] = Thread(self.assistant, entry["id"], "task", thread.id, thread.depth + 1,
                                             thread.origin, thread.trust)
        task.heard(brief)
        return entry, None

    def scheduled(self, event: dict[str, Any]) -> None:
        """A function due on a schedule: work nothing waits for, at the schedule's trust. How it ended is told to the
        schedule; a run whose output has its ``wake`` field set starts a run thread."""
        what, schedule = event["what"], str(event.get("schedule_id") or "")
        agent_id, _, function_id = str(what["function"]).partition(".")
        entry = self.begin(None, None, "call", f"Scheduled · {what['function']}", what.get("inputs") or {},
                           origin=f"schedule:{schedule}")
        entry.update(means={"agent": agent_id, "function": function_id}, trust=event.get("trust"))
        job = self.calls(entry, what.get("inputs") or {})

        async def woke() -> Any:
            output, files, records = await job()
            if what.get("wake") and isinstance(output, dict) and output.get(what["wake"]):
                self.assistant.run(f"schedule:{schedule}", event.get("trust"), f"A scheduled run of "
                                   f"{what['function']} came back with {what['wake']} set. Its output: "
                                   f"{cut(output, SHOWN)}", schedule, title=str(what["function"]))
            return output, files, records

        async def run() -> None:
            await self._done(entry, woke)
            await self.chat.ran(schedule, entry["status"] != DONE, (entry["output"] or {}).get("line", ""))

        self.jobs[entry["id"]] = self.assistant.later(run())

    # -- running and ending ---------------------------------------------------------------------------

    def go(self, entry: dict[str, Any], job: Callable) -> None:
        self.jobs[entry["id"]] = self.assistant.later(self._done(entry, job))

    async def _done(self, entry: dict[str, Any], job: Callable) -> None:
        try:
            result = await job()
        except Process.Failed as failed:
            return self.ended(entry, FAILED, f"{failed.code}: {failed.message}")
        except ApiServers.Refused as refused:
            return self.ended(entry, FAILED, f"{refused.code}: {refused.message}")
        except Exception as error:
            log.warning(f"{self.chat.id}: {entry['id']} failed: {error!r}")
            return self.ended(entry, FAILED, f"failed: {str(error)[:1000]}")
        output, files, records = result if isinstance(result, tuple) else (result, [], [])
        self.ended(entry, DONE, output, files, records)

    def ended(self, entry: dict[str, Any], status: str, output: Any, files: list[str] = (),
              records: list[dict[str, Any]] = (), tell: bool = True) -> None:
        """Work over: kept in the log with its output (kept whole, apart), the files it returned and the records its
        program touched; its thread told."""
        if entry["status"] not in (RUNNING, WAITING):
            return
        assistant, said = self.assistant, text(output)
        self.jobs.pop(entry["id"], None)
        entry.update(status=status, ended=assistant.now(), files=list(files), records=list(records),
                     output={"size": len(said), "line": said[:200]})
        assistant.known = (assistant.known + list(files))[-assistant.FILES_KNOWN:]
        assistant.files += list(files)
        assistant.later(self._output(entry, said))
        self.told(entry)
        thread = assistant.threads.get(entry["thread"]) if entry["tool"] else None
        if thread is not None and tell:
            thread.answered(entry, cut(said, SHOWN, entry["id"]))
        assistant.keep()
        assistant.check()

    def stop(self, id: str, status: str = STOPPED, why: str = "Stopped.", tell: bool = True) -> None:
        """A piece of work stopped, with everything under it; a card it waited on expires."""
        entry = self.entries.get(id)
        if entry is None or entry["status"] not in (RUNNING, WAITING):
            return
        task = self.assistant.threads.get(id)
        if task is not None and task.kind == "task":
            task.halt()
        job = self.jobs.pop(id, None)
        if job is not None:
            job.cancel()
        if entry.get("card") and entry["status"] == WAITING:
            self.assistant.later(self.chat.expire(entry["card"]))
        self.ended(entry, status, why, tell=tell)

    def stop_all(self) -> None:
        for id, entry in list(self.entries.items()):
            if entry["status"] in (RUNNING, WAITING):
                self.stop(id)

    # -- the person, and restarts ---------------------------------------------------------------------

    def waiting(self, id: str, card: str, before: bool) -> None:
        """A piece of work waits on a card (``before`` it is sent, for an approval; or within its call, for what its
        program asks), or, with no card, goes on."""
        entry = self.entries.get(id)
        if entry is None or entry["status"] not in (RUNNING, WAITING):
            return
        entry.update(status=WAITING if card else RUNNING, before=before and bool(card))
        if card:
            entry["card"] = card
        self.told(entry)
        self.assistant.keep()
        self.assistant.check()

    def late(self, event: dict[str, Any]) -> None:
        """A card answered after the work that asked it stopped waiting: its thread is told, to run it again."""
        entry = next((e for e in self.entries.values() if e.get("card") and e["card"] == event.get("card")), None)
        # work still waiting took the answer itself, as after a restart
        if entry is not None and entry["status"] not in (RUNNING, WAITING):
            threads = self.assistant.threads
            thread = threads.get(entry["thread"]) or threads[""]
            thread.heard(f"The person answered {entry['id']}'s card ({entry['title']}) after it had stopped "
                         f"waiting: {cut(event.get('answer'), 2000)}. Run it again if it is still wanted.")

    def restarted(self, cards: list[dict[str, Any]]) -> None:
        """Read back: work waiting for the person goes on, on the card it waited on; a call that was running is
        unknown; a sleep is over. Other cards still open belong to calls that are gone, and expire."""
        kept = set()
        for entry in list(self.entries.values()):
            if entry["status"] not in (RUNNING, WAITING) or entry["kind"] == "task":
                continue
            if entry["kind"] in ("call", "mcp") and entry["status"] == WAITING and entry.get("before"):
                kept.add(entry["card"])
                self.go(entry, self.calls(entry))
            elif entry["kind"] == "question":
                kept.add(entry.get("card", ""))
                self.go(entry, self.asking(entry))
            elif entry["kind"] == "sleep":
                self.ended(entry, DONE, "Woken: the runtime restarted.")
            else:
                self.ended(entry, UNKNOWN, "unknown: the runtime restarted while it ran, so it may have been done; "
                                           "check before doing it again.")
        for card in cards:
            if card["status"] == "open" and card["card_id"] not in kept:
                self.assistant.later(self.chat.expire(card["card_id"]))

    def trimmed(self) -> int:
        """The oldest ended work keeps only its refs: the characters that freed, 0 when none is left."""
        for entry in self.entries.values():
            if entry["status"] not in (RUNNING, WAITING) and (entry.get("inputs") or entry.get("why")):
                freed = len(str(entry.get("inputs"))) + len(str(entry.get("why", "")))
                entry.update(inputs=None, why="")
                return freed
        return 0

    # -- kept apart -----------------------------------------------------------------------------------

    async def _output(self, entry: dict[str, Any], said: str) -> None:
        """An output kept whole, apart from the state: the log names it by its ref."""
        try:
            kept = await self.chat.store(said[:self.OUTPUT_MOST])
        except ApiServers.Refused as refused:
            log.warning(f"{self.chat.id}: {entry['id']}'s output was not kept: {refused.message}")
            return
        entry["output"]["ref"] = kept["ref"]
        self.told(entry)
        self.assistant.keep()

    async def _inputs(self, entry: dict[str, Any], inputs: Any) -> None:
        """Inputs too long for the log, kept apart: the log names them by their ref."""
        entry["inputs"] = {"ref": (await self.chat.store(text(inputs)))["ref"]}
        self.assistant.keep()

    async def _inputs_back(self, entry: dict[str, Any]) -> Any:
        if isinstance(entry.get("inputs"), dict) and set(entry["inputs"]) == {"ref"}:
            return json.loads((await self.chat.output(entry["inputs"]["ref"], 0, self.OUTPUT_MOST))["text"])
        return entry.get("inputs") or {}
