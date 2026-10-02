"""Which model a chat or a run thinks with.

The doors that resolve this must agree about what "usable" means: a
new chat picking the person's default, and the preferences page saving
the choice. They had separate copies of the same filter, so a secret naming
a provider but no model was usable in one place and not another depending
on which copy was edited last.

Where they legitimately differ is what to do about the answer, and that
stays with them. A chat may fall back to a saved preference and quietly
adopt a sole key as one; a run has nobody to ask, so more than one
visible key is a refusal rather than a guess.

The values themselves never come through here — a secret_ref travels, and
the runtime resolves it through the one documented consumer path.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from api.services.chat_session.settings.setting import Setting


class ModelChoice(Setting):
    #: A chat carries the resolved block; a preference names the secret.
    #: This is the one setting whose two doors hold different things,
    #: which is why ``check`` and ``check_preference`` differ here.
    name = "llm"
    preference = "llm_secret_ref"

    RESOURCE_ID = "llm_api_key"

    def usable(self, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The organization's LLM connections, default first.

        These used to be secrets on a seeded definition, filtered for
        the ones that had both halves of the choice filled in. A
        connection stores provider and model as required fields, so
        every row here is servable by construction — and the public
        shape (``resource_ref`` + ``keys``) is the same one this
        module's consumers were always handed."""
        from database.stores import LlmConnectionStore

        # An embedding or transcription connection is for routing or
        # speech, not thinking: never offered to a chat, never a default.
        return [row for row in LlmConnectionStore().list(user)
                if (row.get("keys") or {}).get("purpose", "chat") == "chat"]

    def block(self, secret: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """The ``llm`` block a chat config carries, from one usable secret.

        ``endpoint`` rides along only when the secret names one: an absent
        key means the provider's own address, and writing an empty string
        there would send a turn to nowhere.
        """
        if not secret:
            return None

        keys = secret["keys"]
        chosen = {
            "provider": keys["provider"],
            "model": keys["model"],
            "secret_ref": secret["resource_ref"],
        }
        if keys.get("endpoint"):
            chosen["endpoint"] = keys["endpoint"]
        return chosen

    # ------------------------------------------------------------------
    def default(
        self, user: Dict[str, Any], chosen: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """The model a new chat starts with: the person's own choice from
        their preferences page, else the ORGANIZATION'S default
        connection. Narrower authority first — the org default exists
        exactly for the person who never picked."""
        connections = self.usable(user)
        preferred = str(chosen.get(self.preference) or "")
        selected = next(
            (connection for connection in connections
             if connection.get("resource_ref") == preferred),
            None,
        )
        if selected is None:
            selected = next(
                (connection for connection in connections
                 if connection.get("is_default")),
                None,
            )
        return self.block(selected)

    def check(
        self, user: Dict[str, Any], value: Any,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """A chat's own model: carried through, with the key verified.

        One thing is checked, and it is the one thing that was missing —
        a ``secret_ref`` must name an LLM key this person can actually
        see. The block used to reach storage completely unvalidated, so a
        request could name somebody else's secret and the refusal, if any
        came, came from inside the runtime.

        NOT rebuilt from that secret. The block is the runtime's input,
        not a copy of the record: its ``provider`` is what decides which
        connector gets built, and provider-specific keys ride along with
        it — ``endpoint`` for Azure, ``responses`` for the scripted
        connector that integration tests and deployment smoke checks run
        against. Rebuilding threw every one of those away.

        A block with no ``secret_ref`` is allowed. The scripted provider
        needs no key at all, and a provider that does need one and has
        none fails in the runtime, which is the only place that can say
        what actually went wrong.
        """
        if not isinstance(value, dict):
            return None, "llm must be an object."

        ref = str(value.get("secret_ref") or "").strip()
        if ref:
            from database.stores import LlmConnectionStore

            if LlmConnectionStore().visible(user, ref) is None:
                return None, "That model connection is not available to you."

        return dict(value), ""

    def check_preference(
        self, user: Dict[str, Any], value: Any,
    ) -> Tuple[Optional[str], str]:
        """The preference names a connection, and clearing it is a choice
        — it hands the person back to the organization's default."""
        if value is not None and not isinstance(value, str):
            return None, "llm_secret_ref must be a string or null."

        ref = str(value or "").strip()
        if not ref:
            return "", ""

        if not any(candidate.get("resource_ref") == ref
                   for candidate in self.usable(user)):
            return None, "Choose one of the organization's LLM connections."
        return ref, ""

    def clamp(self, value: Any) -> Optional[Dict[str, Any]]:
        """Served as stored. A model is not a number to be brought into
        range: either the secret still resolves, in which case the block
        is right, or it does not, and the runtime says so with the only
        context that could explain it."""
        return value
