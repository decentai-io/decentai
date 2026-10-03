"""What a person can choose about how a chat thinks.

Three layers, and the last one with an answer wins:

    what the platform ships     TrustLevel.STANDARD, TurnBudget.STANDARD
    what this person chose      preferences.chat, on their profile page
    what this chat says         the chat's own config

None of it comes from the environment any more. A deployment variable
answered these questions in the wrong place — the person who redeploys is
not the person waiting on a turn, and a limit nobody in the application
could see or change was a refusal with no explanation attached.

The resolver here is what the two doors call. Before it, the merging was
written out by hand in the chat controller (``if not config.get("llm")``,
a loop over the fields that were missing) and the checking was written a
second time in the profile controller, with its own field whitelist. Two
hand-written copies of the same three layers is how a setting comes to be
validated on one door and not the other.

Adding a setting is one class in this package and one line below.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from api.services.chat_session.settings.bindings import CredentialBindings
from api.services.chat_session.settings.model import (
    ModelChoice, PreferredEffort, PreferredModel,
)
from api.services.chat_session.settings.narrowing import EnabledAgents, EnabledSkills
from api.services.chat_session.settings.setting import Setting, chosen_by
from api.services.chat_session.settings.skills_cap import SkillsCap
from api.services.chat_session.settings.timezone import Timezone
from api.services.chat_session.settings.trust import TrustLevel
from api.services.chat_session.settings.turns import TurnBudget

__all__ = [
    "ChatSettings", "CredentialBindings", "EnabledAgents", "EnabledSkills",
    "ModelChoice", "Setting", "SkillsCap", "Timezone", "TrustLevel",
    "TurnBudget", "chosen_by",
]


class ChatSettings:
    """Every setting a chat carries, asked as one."""

    def __init__(self):
        self.settings = (
            TrustLevel(), TurnBudget(), ModelChoice(),
            PreferredModel(), PreferredEffort(),
            EnabledAgents(), EnabledSkills(), Timezone(), SkillsCap(),
            CredentialBindings(),
        )

    # ------------------------------------------------------------------
    def for_new_chat(
        self, user: Dict[str, Any], asked: Any,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """The config a chat is created with: what its creator asked for,
        and this person's defaults for everything they left out."""
        config, problem = self._checked(user, asked)
        if problem:
            return None, problem

        chosen = chosen_by(user)
        for setting in self.settings:
            if setting.name in config:
                continue
            value = setting.default(user, chosen)
            if value is not None:
                config[setting.name] = value
        return config, ""

    def for_update(
        self, user: Dict[str, Any], asked: Any,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """The config a chat is changed to.

        Nothing is filled in from the defaults here. The page sends back
        the whole config it was given, so a setting that is missing is one
        this chat is meant to stop carrying — not one to answer again.
        """
        return self._checked(user, asked)

    def as_served(self, config: Any) -> Dict[str, Any]:
        """The config on its way out to a runtime.

        Every stored value is clamped, because this is the last place
        before something acts on it and there is nobody here to refuse to.
        A level hand-edited in the database, or written by a version of
        this code that checked less, reaches the executor as a level
        rather than as whatever it says.
        """
        served = dict(config or {})
        for setting in self.settings:
            if setting.name in served:
                served[setting.name] = setting.clamp(served[setting.name])
        return served

    def budgets_of(self, config: Any) -> Dict[str, Any]:
        """What the runtime should enforce, returned BESIDE the chat and
        never inside its config.

        The chat page writes the whole config back whenever one setting
        changes, so a value the backend computed into it would come back
        on the next save as though a person had chosen it.

        The turn budget and the skills cap travel. Nothing is said about
        agent steps: the runtime falls back to each agent's declared
        limit when no number arrives, and that limit is the reviewed one.
        """
        turns = TurnBudget()
        skills = SkillsCap()
        return {
            "max_turns": turns.clamp(
                (config or {}).get(turns.name, turns.STANDARD)
            ),
            "max_skills": skills.clamp(
                (config or {}).get(skills.name, skills.STANDARD)
            ),
        }

    # ------------------------------------------------------------------
    def set_defaults(
        self, user: Dict[str, Any], asked: Any,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """Save this person's defaults. Returns the updated user document.

        The keys accepted are exactly the settings' own, so the door
        cannot fall behind the vocabulary the way a hand-written list did.
        """
        from database.stores import UserStore

        if not isinstance(asked, dict):
            return None, "preferences.chat must be an object."

        by_preference = {
            setting.preference: setting for setting in self.settings
            if setting.preference
        }
        unknown = set(asked) - set(by_preference)
        if unknown:
            return None, (
                "These are not chat preferences: "
                + ", ".join(sorted(unknown)) + "."
            )

        values: Dict[str, Any] = {}
        for key, raw in asked.items():
            value, problem = by_preference[key].check_preference(user, raw)
            if problem:
                return None, problem
            values[key] = value

        updated = UserStore().set_chat_preferences(
            str(user.get("user_id") or ""), values
        )
        return updated, ""

    # ------------------------------------------------------------------
    def _checked(
        self, user: Dict[str, Any], asked: Any,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """Each setting present is checked by the one that owns it. A key
        no setting owns is not stored: the runtime reads the chat's
        contract, never its config, so nothing else could ever read it.
        """
        asked = asked if isinstance(asked, dict) else {}
        config: Dict[str, Any] = {}
        for setting in self.settings:
            # A setting with no name is a preference only: no chat
            # config carries it.
            if not setting.name or setting.name not in asked:
                continue
            value, problem = setting.check(user, asked[setting.name])
            if problem:
                return None, problem
            config[setting.name] = value
        return config, ""
