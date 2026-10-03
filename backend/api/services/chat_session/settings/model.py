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

A connection is a provider and its key; the MODEL is the chat's to
choose, from the ones that provider serves. So the block a chat carries
names both — the connection by its ref and the model by the provider's
own id for it — and, for a model that thinks before answering, how hard.

The values themselves never come through here — a secret_ref travels, and
the runtime resolves it through the one documented consumer path.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from api.services.chat_session.settings.setting import Setting

#: How hard a reasoning model is asked to think: the provider's own
#: word for it (low, high, xhigh, …), which the catalog lists per model.
#: Checked for shape only — which words a model takes is the provider's
#: to say, and it says so in a refusal the chat shows.
EFFORT = re.compile(r"[a-z]{1,16}")
#: The longest model id accepted. Bedrock's inference-profile ARNs are
#: the long ones.
MODEL_MAX = 300


def checked_model(value: Any) -> Tuple[Optional[str], str]:
    model = str(value or "").strip()
    if len(model) > MODEL_MAX:
        return None, "That model name is too long."
    return model, ""


def checked_effort(value: Any) -> Tuple[Optional[str], str]:
    effort = str(value or "").strip().lower()
    if effort and not EFFORT.fullmatch(effort):
        return None, "Reasoning effort is one word, such as low or high."
    return effort, ""


class ModelChoice(Setting):
    #: A chat carries the resolved block; a preference names the secret.
    #: This is the one setting whose two doors hold different things,
    #: which is why ``check`` and ``check_preference`` differ here.
    name = "llm"
    preference = "llm_secret_ref"

    RESOURCE_ID = "llm_api_key"

    def usable(self, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The connections this person can see, default first.

        A connection stores its provider and the model it starts with
        as required fields, so every row here is servable by
        construction — and the public shape (``resource_ref`` +
        ``keys``) is the same one this module's consumers were always
        handed."""
        from database.stores import LlmConnectionStore

        return LlmConnectionStore().list(user)

    def block(self, secret: Optional[Dict[str, Any]], model: str = "",
              effort: str = "") -> Optional[Dict[str, Any]]:
        """The ``llm`` block a chat config carries, from one connection.

        The provider and the address are the connection's. The model is
        the one named, or the one the connection starts with when none
        is. ``endpoint`` rides along only when the connection names one:
        an absent key means the provider's own address, and writing an
        empty string there would send a turn to nowhere.
        """
        if not secret:
            return None

        keys = secret["keys"]
        chosen = {
            "provider": keys["provider"],
            "model": model or keys["model"],
            "secret_ref": secret["resource_ref"],
        }
        if keys.get("endpoint"):
            chosen["endpoint"] = keys["endpoint"]
        if effort:
            chosen["reasoning_effort"] = effort
        return chosen

    # ------------------------------------------------------------------
    def default(
        self, user: Dict[str, Any], chosen: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """The model a new chat starts with: the person's own choice —
        the connection, the model and the effort they last picked —
        else the ORGANIZATION'S default connection and the model it
        starts with. Narrower authority first — the org default exists
        exactly for the person who never picked."""
        connections = self.usable(user)
        preferred = str(chosen.get(self.preference) or "")
        selected = next(
            (connection for connection in connections
             if connection.get("resource_ref") == preferred),
            None,
        )
        if selected is not None:
            # The model is remembered with the connection it is of: on
            # another connection it would name another provider's model.
            return self.block(
                selected,
                str(chosen.get(PreferredModel.preference) or ""),
                str(chosen.get(PreferredEffort.preference) or ""))
        return self.block(next(
            (connection for connection in connections
             if connection.get("is_default")),
            None,
        ))

    def check(
        self, user: Dict[str, Any], value: Any,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """A chat's own model: the connection verified, the model and
        the effort the chat's own.

        A ``secret_ref`` must name a connection this person can actually
        see. Then the provider and the address are written from that
        connection — they are whose key it is and where it is sent, and
        the runtime takes them from the connection whatever a block says
        — while ``model`` and ``reasoning_effort`` stay as chosen: any
        model the provider serves, by its own id, listed in the catalog
        or not.

        A block with no ``secret_ref`` is carried through as it is. The
        scripted provider needs no key at all and rides with keys of its
        own (``responses``), and a provider that does need a key and has
        none fails in the runtime, which is the only place that can say
        what actually went wrong.
        """
        if not isinstance(value, dict):
            return None, "llm must be an object."

        block = dict(value)
        model, problem = checked_model(block.get("model"))
        if problem:
            return None, problem
        effort, problem = checked_effort(block.get("reasoning_effort"))
        if problem:
            return None, problem

        ref = str(block.get("secret_ref") or "").strip()
        if not ref:
            return block, ""

        from database.stores import LlmConnectionStore

        store = LlmConnectionStore()
        connection = store.to_public(store.visible(user, ref))
        if connection is None:
            return None, "That model connection is not available to you."
        block.pop("endpoint", None)
        block.pop("reasoning_effort", None)
        block.update(self.block(connection, model, effort))
        return block, ""

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


class PreferredModel(Setting):
    """Which of the preferred connection's models a new chat starts
    with: the one the person last picked. A preference only — a chat
    carries its model inside its ``llm`` block."""

    preference = "llm_model"

    def check_preference(
        self, user: Dict[str, Any], value: Any,
    ) -> Tuple[Optional[str], str]:
        if value is not None and not isinstance(value, str):
            return None, "llm_model must be a string or null."
        return checked_model(value)


class PreferredEffort(Setting):
    """How hard that model is asked to think, remembered with it."""

    preference = "llm_reasoning_effort"

    def check_preference(
        self, user: Dict[str, Any], value: Any,
    ) -> Tuple[Optional[str], str]:
        if value is not None and not isinstance(value, str):
            return None, "llm_reasoning_effort must be a string or null."
        return checked_effort(value)
