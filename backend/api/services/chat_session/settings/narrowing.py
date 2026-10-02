"""What a chat serves out of everything the person could reach.

Two lists, the same shape, and deliberately not the same rules — because
an agent and a skill are not the same kind of thing.

An agent runs code the platform had to approve and install. So a person's
default may only name agents actually installed in their organization,
and a single chat may not reach past that default: narrowing your agents
on the preferences page is meant to mean something, and a chat that could
opt back in would make it decoration.

A skill is text this person can already read. Choosing one for a single
conversation grants nothing they did not have, so a chat is NOT bounded
by the default — only the default itself is checked, and only so that it
does not quietly name somebody else's private skill and do nothing.

Absent and empty are never the same. No key means no narrowing at all;
an empty list is a choice, and it means none.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from api.services.chat_session.settings.setting import Setting, chosen_by


class Narrowing(Setting):
    """A list of refs. Subclasses say which refs are allowed where."""

    #: What to call the entries when refusing, written for a person.
    noun = ""

    def default(
        self, user: Dict[str, Any], chosen: Dict[str, Any],
    ) -> Optional[List[str]]:
        stored = chosen.get(self.preference)
        return list(stored) if isinstance(stored, list) else None

    def clamp(self, value: Any) -> List[str]:
        cleaned, _ = self._shape(value)
        return cleaned if cleaned is not None else []

    # ------------------------------------------------------------------
    def _shape(self, raw: Any) -> Tuple[Optional[List[str]], str]:
        """Strings, trimmed, deduplicated, order kept."""
        if not isinstance(raw, list) or any(
            not isinstance(entry, str) or not entry.strip() for entry in raw
        ):
            return None, f"{self.name} must be a list of {self.noun}."

        cleaned: List[str] = []
        for entry in raw:
            entry = entry.strip()
            if entry not in cleaned:
                cleaned.append(entry)
        return cleaned, ""


class EnabledAgents(Narrowing):
    name = "enabled_agents"
    preference = "enabled_agents"
    noun = "agent ids"

    def default(
        self, user: Dict[str, Any], chosen: Dict[str, Any],
    ) -> Optional[List[str]]:
        return self.alive(user, super().default(user, chosen))

    def alive(self, user: Dict[str, Any], refs: Any) -> Any:
        """The refs that still name an installed agent.

        A list is written about the agents of its day. One that names
        agents since uninstalled narrows to the ones that survive; one
        that names only such agents narrows nothing — it was about a
        world that is gone, not a choice of none. Without this a person
        whose one chosen agent was uninstalled opened every new chat
        with no agents at all, and the page told them none was
        installed. An empty list stays the choice it is."""
        if not isinstance(refs, list) or not refs:
            return refs

        from database.stores import AgentManifestStore

        installed: Set[str] = set(AgentManifestStore().installed_versions(
            str(user.get("org_id") or "")))
        live = [ref for ref in refs if ref in installed]
        return live or None

    def check(
        self, user: Dict[str, Any], value: Any,
    ) -> Tuple[Optional[List[str]], str]:
        """A chat may narrow the default, never reach past it.

        Checked on the way in whichever door it came through. Updating a
        chat used to be checked and creating one did not, so a chat could
        be born holding agents its owner had turned off and simply not be
        allowed to change afterwards.
        """
        cleaned, problem = self._shape(value)
        if problem:
            return None, problem

        allowed = chosen_by(user).get(self.preference)
        if isinstance(allowed, list) and set(cleaned) - set(allowed):
            return None, ("A chat can only use agents enabled in your "
                          "defaults.")
        return cleaned, ""

    def check_preference(
        self, user: Dict[str, Any], value: Any,
    ) -> Tuple[Optional[List[str]], str]:
        """A default may only name agents this organization has installed."""
        cleaned, problem = self._shape(value)
        if problem:
            return None, problem

        from database.stores import AgentManifestStore

        installed: Set[str] = set(AgentManifestStore().installed_versions(
            str(user.get("org_id") or "")))
        if set(cleaned) - installed:
            return None, "One or more selected agents are not installed."
        return cleaned, ""


class EnabledSkills(Narrowing):
    name = "enabled_skills"
    preference = "enabled_skills"
    noun = "skill references"

    def check_preference(
        self, user: Dict[str, Any], value: Any,
    ) -> Tuple[Optional[List[str]], str]:
        """Only skills this person can actually see: a default naming
        someone else's private skill would be a preference that silently
        does nothing."""
        cleaned, problem = self._shape(value)
        if problem:
            return None, problem

        from database.stores.data.skills import SkillStore

        visible = {
            row["resource_ref"]
            for row in SkillStore().list_visible(user, with_values=False)
        }
        if set(cleaned) - visible:
            return None, "One or more selected skills are not visible to you."
        return cleaned, ""

    def check(
        self, user: Dict[str, Any], value: Any,
    ) -> Tuple[Optional[List[str]], str]:
        cleaned, problem = self._shape(value)
        return (None, problem) if problem else (cleaned, "")
