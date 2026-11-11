"""Who a call comes from: a person by session token or API key, a runtime by its certificate and the chat it holds
(api-server.md §2)."""

from __future__ import annotations

import time
from typing import Any

from ..chats.store import Chats
from ..people.store import ApiKeys, Organizations, Sessions, hashed
from ..runtimes.calls import RuntimeCalls
from ..runtimes.store import Refusals
from ..utils import Settings
from .calls import Caller, Refused
from .limits import Limits


class Callers:
    """A person's call reads the session or key, the person and the organization again, in one round trip: someone
    disabled, removed or signed out is refused on their next call. What a runtime's call reads, its refusal and its
    standing in a chat, each server keeps for KEPT_SECONDS."""

    KEPT_SECONDS = 2.0
    #: By ("refused", runtime) and by (runtime, chat): until when, and what was read.
    _kept: dict[tuple[str, str], tuple[float, Any]] = {}

    def person(self, token: str, address: str) -> Caller:
        if not token:
            return Caller("anyone", address=address)
        keyed = token.startswith(ApiKeys.PREFIX)
        found = Organizations.standing(ApiKeys() if keyed else Sessions(),
                                       {"hash" if keyed else "_id": hashed(token), "expires_at": {"$gt": Sessions.now()}})
        if found is None:
            return Caller("anyone", address=address)
        held, person = found
        if keyed:
            ApiKeys().touch(held)
            return Caller("person", person=person, key=held["_id"], address=address)
        return Caller("person", person=person, session=held["_id"], address=address)

    def runtime(self, peer: dict[str, Any] | None) -> Caller:
        """A runtime's certificate, unless refused by its id."""
        if not peer or peer.get("kind") != "runtime" or self._kept_or(
                ("refused", peer["id"]), lambda: Refusals().refused(peer["id"]), lambda refused: True):
            return Caller("anyone")
        return Caller("runtime", runtime=peer)

    def for_chat(self, caller: Caller, data: dict[str, Any], recording: bool) -> Caller:
        """A runtime acts for a chat it holds, as its person, in an organization it may serve. While the person has
        stopped everything, it may only write down what the chat did."""
        runtime, chat_id = caller.runtime, str(data.get("chat_id") or "")
        found = self._kept_or((runtime["id"], chat_id), lambda: Organizations.standing(Chats(), {"_id": chat_id}),
                              lambda found: found is not None and found[0].get("holder") == runtime["id"])
        if found is None or found[0].get("holder") != runtime["id"]:
            chat = found[0] if found else Chats().find(chat_id)
            if chat is None or chat.get("holder") != runtime["id"]:
                raise Refused("conflict", "This runtime doesn't hold that chat.", reason="stale")
            raise Refused("refused", "This runtime may not act for that chat.")
        chat, person = found
        if not RuntimeCalls.may_serve(runtime, chat["org_id"], person["organization"].get("settings")):
            raise Refused("refused", "This runtime may not act for that chat.")
        if person.get("stopped") and not recording:
            raise Refused("refused", "Its person has stopped everything.")
        return Caller("runtime", person=person, runtime=runtime, chat=dict(chat))

    def within_rate(self, caller: Caller) -> None:
        Limits().counted(f"calls:{caller.user_id}", Settings.read().calls_per_minute, 60, "calls")

    @classmethod
    def _kept_or(cls, key: tuple[str, str], read: Any, keep: Any) -> Any:
        """What was read for ``key`` within KEPT_SECONDS, or read now, and kept when ``keep`` says it may be."""
        now = time.monotonic()
        held = cls._kept.get(key)
        if held is not None and held[0] > now:
            return held[1]
        found = read()
        if keep(found):
            if len(cls._kept) > 10_000:
                cls._kept.clear()
            cls._kept[key] = (now + cls.KEPT_SECONDS, found)
        return found
