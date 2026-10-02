"""Whether code an agent proposed needs a card (Settings:Safety).

An agent puts code before the person (call.propose) and the runtime
opens a card for it. Where the organization chose to be asked less
often, the card is settled here, as it is opened, and nobody is asked:

- only code the assistant read and found to do what it says — one that
  does more, or that could not be read, is always put to the person;
- a script in a page, once a person allowed a script of the same agent
  on the same site in this chat;
- a program that is a correction — the same call already had a program
  a person allowed, and this one needs no package, host, credential or
  file beyond what they allowed then;
- a program that reaches no site and uses no credential, where the
  organization chose to be asked only for those.

What "a person allowed" means is read from the cards themselves, and a
card this rule settled is not one: a setting never vouches for itself.
"""

from __future__ import annotations

from typing import Any, Dict, List

from database.stores import ApprovalStore

NEEDS = ("packages", "hosts", "credentials", "files")


class CodeRules:
    #: Who decided a card nobody was shown.
    SETTING = "setting"

    def __init__(self, safety: Dict[str, Any], approvals: ApprovalStore):
        self.safety = dict(safety or {})
        self.approvals = approvals

    def settles(self, chat_id: str, request: Dict[str, Any]) -> bool:
        code = request.get("code") or {}
        if (code.get("review") or {}).get("verdict") != "agrees":
            return False
        if code.get("language") == "javascript":
            return self._script(chat_id, request, code)
        return self._program(chat_id, request, code)

    # ------------------------------------------------------------------
    def _allowed_by_a_person(self, chat_id: str,
                             request: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The code of this agent a person allowed in this chat."""
        agent = str(request.get("agent_id") or "")
        return [card.get("request") or {}
                for card in self.approvals.code_allowed_in_chat(chat_id, self.SETTING)
                if str((card.get("request") or {}).get("agent_id") or "") == agent]

    def _script(self, chat_id: str, request: Dict[str, Any],
                code: Dict[str, Any]) -> bool:
        where = str(code.get("where") or "")
        if self.safety.get("scripts") != "once_per_site" or not where:
            return False
        return any(
            (earlier.get("code") or {}).get("language") == "javascript"
            and str((earlier.get("code") or {}).get("where") or "") == where
            for earlier in self._allowed_by_a_person(chat_id, request))

    def _program(self, chat_id: str, request: Dict[str, Any],
                 code: Dict[str, Any]) -> bool:
        mode = self.safety.get("programs")
        if mode not in ("corrections", "quiet"):
            return False
        if mode == "quiet" and not code.get("hosts") and not code.get("credentials"):
            return True
        call_id = str(request.get("call_id") or "")
        if not call_id:
            return False
        earlier = [card.get("code") or {}
                   for card in self._allowed_by_a_person(chat_id, request)
                   if str(card.get("call_id") or "") == call_id]
        if not earlier:
            return False
        return all(
            set(code.get(need) or []) <= {name for allowed in earlier
                                          for name in allowed.get(need) or []}
            for need in NEEDS)
