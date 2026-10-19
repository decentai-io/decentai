"""Which credential answers for an agent's slot, in this chat.

A person who keeps two credentials for one slot — two accounts of the
same service — says which one a chat uses here, by the slot's category
(``<agent ref>__<slot>``) and the credential's ref. It outranks the
default marked on the agent's page; with neither, two credentials are
a question the chat refuses to guess at (Secrets:Secret:Use).

Per chat, so there is no preference behind it: a default for every
chat is the one on the agent's page.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from api.services.chat_session.settings.setting import Setting


class CredentialBindings(Setting):
    name = "bindings"
    preference = ""

    #: More slots than any chat's agents declare between them.
    LARGEST = 100
    CATEGORY = re.compile(r"^[A-Za-z0-9_]{1,128}$")
    REF = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

    def check(self, user: Dict[str, Any], value: Any
              ) -> Tuple[Optional[Dict[str, str]], str]:
        if not isinstance(value, dict):
            return None, "bindings must map a slot to a credential."
        if len(value) > self.LARGEST:
            return None, f"A chat binds at most {self.LARGEST} slots."
        for category, ref in value.items():
            if not (isinstance(category, str) and self.CATEGORY.match(category)
                    and isinstance(ref, str) and self.REF.match(ref)):
                return None, ("bindings must map a slot's category to a "
                              "credential's ref.")
        return dict(value), ""

    def clamp(self, value: Any) -> Dict[str, str]:
        checked, problem = self.check({}, value)
        return checked if not problem else {}
