"""Who a call comes from: a person by session token or API key, a runtime by its certificate and the chat it holds
(api-server.md §2)."""

from __future__ import annotations

from typing import Any

from ..chats.store import Chats
from ..people.store import ApiKeys, Organizations, Sessions, Users, hashed
from ..runtimes.calls import RuntimeCalls
from ..runtimes.store import Refusals
from ..utils import Settings
from .calls import Caller, Refused
from .limits import Limits


class Callers:
    """Each call reads the session, the person and the organization again: someone disabled, removed or signed out
    is refused on their next call."""

    def person(self, token: str, address: str) -> Caller:
        if not token:
            return Caller("anyone", address=address)
        if token.startswith(ApiKeys.PREFIX):
            key = ApiKeys().find(token)
            person = self._standing(key["user_id"]) if key else None
            if person is None:
                return Caller("anyone", address=address)
            ApiKeys().touch(key)
            return Caller("person", person=person, key=key["_id"], address=address)
        session = Sessions().find(token)
        person = self._standing(session["user_id"]) if session else None
        if person is None:
            return Caller("anyone", address=address)
        return Caller("person", person=person, session=hashed(token), address=address)

    def runtime(self, peer: dict[str, Any] | None) -> Caller:
        """A runtime's certificate, unless refused by its id."""
        if not peer or peer.get("kind") != "runtime" or Refusals().refused(peer["id"]):
            return Caller("anyone")
        return Caller("runtime", runtime=peer)

    def for_chat(self, caller: Caller, data: dict[str, Any], recording: bool) -> Caller:
        """A runtime acts for a chat it holds, as its person, in an organization it may serve. While the person has
        stopped everything, it may only write down what the chat did."""
        chat = Chats().find(str(data.get("chat_id") or ""))
        if chat is None or chat.get("holder") != caller.runtime["id"]:
            raise Refused("conflict", "This runtime doesn't hold that chat.", reason="stale")
        person = self._standing(chat["user_id"])
        if person is None or not RuntimeCalls.may_serve(caller.runtime, chat["org_id"]):
            raise Refused("refused", "This runtime may not act for that chat.")
        if person.get("stopped") and not recording:
            raise Refused("refused", "Its person has stopped everything.")
        return Caller("runtime", person=person, runtime=caller.runtime, chat=chat)

    def within_rate(self, caller: Caller) -> None:
        Limits().check(f"calls:{caller.user_id}", Settings.read().calls_per_minute, 60, "calls")

    @staticmethod
    def _standing(user_id: str) -> dict[str, Any] | None:
        person = Users().col.find_one({"_id": user_id, "status": "active"})
        return person if person and Organizations().active(person["org_id"]) else None
