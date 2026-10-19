"""Sharing — one implementation, configured per record type.

Everything a person can share answers the same three questions the same
way: WHO may see it (an ``owner`` map of groups and users, filtered into
every read), WHOM may it be shared to (the writer's own reach), and WHO
may change it (its creator). A ``Sharing`` engine is those answers
written once; what differs between record types is POLICY, carried by
the ``SharingProfile`` it is constructed with:

* A PERSONAL record (a secret, a file, a record, a skill) may not be
  shared org-wide or beyond the creator's own groups without the
  domain's escape grant.
* An INFRASTRUCTURE record (an LLM connection, an agent source) exists
  to be used: org-wide is open to whoever creates one, and the creator
  is always retained among the users.

EVERYTHING defaults to private. Nothing a person makes reaches anyone
else until they say so — the consumer supplies the creator-only owner
when a create names none.

Both may be shared to individual people who sit in one of the sharer's
explicit groups — the same boundary the peers listing draws, so the
picker and the refusal agree.

A new shareable type is one constructor call — ``Sharing(PERSONAL,
"draft")`` — or, when its policy genuinely differs, one new profile.
Presentation stays at the edges: each consumer words its own refusals
(``reach_refusal`` carries the one wording two pages already share), so
tests that pin today's messages keep pinning them while the RULES cannot
drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from database.stores.iam import GroupStore


@dataclass(frozen=True)
class SharingProfile:
    #: Whether the creator is always kept among the users — an
    #: infrastructure record is never shared out of its author's sight.
    retain_creator: bool = False
    #: Whether naming the Everyone group is open to anybody, or part of
    #: what the escape grant lifts.
    everyone_open: bool = False
    #: Whom the writer may name as users: only themselves ("self"), or
    #: people sharing one of their explicit groups ("peers").
    people_reach: str = "peers"


#: Personal records: secrets, files, agent records, skills.
PERSONAL = SharingProfile(retain_creator=False,
                          everyone_open=False, people_reach="peers")

#: Org infrastructure: LLM connections, agent sources.
INFRASTRUCTURE = SharingProfile(retain_creator=True,
                                everyone_open=True, people_reach="peers")


class Sharing:
    """The sharing rules for one kind of record.

    Constructed with the profile that carries its policy and the label
    its refusals name — ``Sharing(PERSONAL, "secret")`` — and held by
    whichever store or controller enforces it. The read-side helpers
    (``visibility_or``, ``visibility_filter``, ``org_wide``,
    ``may_edit``) read no policy and are static, so a caller with no
    profile in hand can still build a filter."""

    def __init__(self, profile: SharingProfile, label: str):
        self.profile = profile
        self.label = str(label or "record")

    # ── Writing an owner ────────────────────────────────────────────────

    def clean(self, owner: Any, *, creator: str = "",
              max_entries: Optional[int] = None) -> Dict[str, Any]:
        """The normalized owner map, or a ValueError saying what is wrong.

        An owner naming nobody is refused — the document would be
        invisible to everyone, its creator included. An absent owner is
        the CREATOR-ONLY map when a creator is known: everything
        defaults to private, and reaching anyone else is a choice. The
        organization never belongs INSIDE an owner: reads pin it beside
        the owner, and writing it here would be the org-wide share by a
        name the filter does not read."""
        profile = self.profile
        if owner is None:
            if not creator:
                raise ValueError(
                    "Owner must be an object with 'groups' and 'users'.")
            owner = {"users": [creator]}
        if not isinstance(owner, dict):
            raise ValueError("Owner must be an object with 'groups' and 'users'.")
        if "org" in owner or "org_id" in owner:
            raise ValueError("Organization does not belong inside owner.")

        cleaned = {
            "groups": self._clean_id_list(owner.get("groups"), "groups",
                                          max_entries),
            "users": self._clean_id_list(owner.get("users"), "users",
                                         max_entries),
        }
        if profile.retain_creator and creator \
                and creator not in cleaned["users"]:
            cleaned["users"].append(creator)
        if not any(cleaned.values()):
            raise ValueError(
                f"A {self.label} needs at least one owner group or user.")
        return cleaned

    def missing_in_org(self, org_id: str,
                       owner: Dict[str, Any]) -> List[str]:
        """The named groups and users that do NOT exist in this
        organization — another organization's ids fail exactly like
        made-up ones, because the lookup carries the org beside the id.
        Even smuggled in, a foreign id would be inert (reads pin the
        reader's own org before the owner map is consulted), but a share
        that silently reaches nobody is a typo, and typos get refused."""
        from database.stores.iam import UserStore

        groups = GroupStore()
        missing = [
            f"group {group_id}" for group_id in owner.get("groups") or []
            if group_id != GroupStore.EVERYONE_ID
            and groups.get_in(org_id, groups.resolve(org_id, group_id)) is None
        ]
        users = UserStore()
        missing += [
            f"user {user_id}" for user_id in owner.get("users") or []
            if users.get_in(org_id, user_id) is None
        ]
        return missing

    def check_exists(self, org_id: str, owner: Dict[str, Any]) -> None:
        """`missing_in_org`, raised the way the infrastructure
        consumers word it."""
        missing = self.missing_in_org(org_id, owner)
        if missing:
            raise ValueError(
                "Not in this organization: " + ", ".join(missing) + ".")

    def beyond_reach(self, user: Dict[str, Any],
                     owner: Dict[str, Any]) -> List[str]:
        """What this writer may not share to, as a list of labels —
        empty when the owner is within their reach. Groups: only their
        own explicit ones, with Everyone open or escape-gated by
        profile. People: themselves always; peers when the profile says
        so."""
        profile = self.profile
        mine = set(user.get("assigned_groups") or [])
        mine.discard(GroupStore.EVERYONE_ID)
        user_id = str(user.get("user_id") or "")

        beyond: List[str] = []
        for group in owner.get("groups") or []:
            if group == GroupStore.EVERYONE_ID:
                if not profile.everyone_open:
                    beyond.append("everyone")
            elif group not in mine:
                beyond.append(f"group {group}")

        named = [str(u) for u in owner.get("users") or []
                 if str(u) and str(u) != user_id]
        if named and profile.people_reach == "peers":
            from database.stores.iam import UserStore

            reachable = set()
            if mine:
                reachable = {
                    str(doc["_id"]) for doc in UserStore().col.find({
                        "org_id": str(user.get("org_id") or ""),
                        "_id": {"$in": named},
                        "assigned_groups": {"$in": list(mine)},
                    }, {"_id": 1})
                }
            beyond += [f"user {u}" for u in named if u not in reachable]
        elif named:  # people_reach == "self"
            beyond += [f"user {u}" for u in named]
        return beyond

    def reach_refusal(
        self, user: Dict[str, Any], owner: Any,
    ) -> Optional[Tuple[Dict[str, Any], int]]:
        """The reach rule as (body, status) or None — one wording,
        shared by the pages that take it as-is. Consumers that word
        their own refusals use `beyond_reach` instead."""
        if not isinstance(owner, dict):
            return None
        beyond = self.beyond_reach(user, owner)
        groups = [b[len("group "):] for b in beyond if b.startswith("group ")]
        if groups:
            return {
                "error": "You can only share to groups you belong to: "
                         + ", ".join(groups) + " is not one of yours.",
            }, 403
        users = [b[len("user "):] for b in beyond if b.startswith("user ")]
        if users:
            return {
                "error": "You can only share to people who share a "
                         "group with you: " + ", ".join(users) + " does not.",
            }, 403
        return None

    # ── Reading and changing — policy-free, so static ───────────────────

    @staticmethod
    def org_wide(owner: Optional[Dict[str, Any]]) -> bool:
        return GroupStore.EVERYONE_ID in ((owner or {}).get("groups") or [])

    @staticmethod
    def visibility_or(user: Dict[str, Any]) -> Dict[str, Any]:
        """The owner half of a read filter — combine with an org pin."""
        groups = list(user.get("assigned_groups") or [])
        if GroupStore.EVERYONE_ID not in groups:
            groups.append(GroupStore.EVERYONE_ID)
        matches: list = [{"owner.groups": {"$in": groups}}]
        user_id = str(user.get("user_id") or "")
        if user_id:
            matches.append({"owner.users": user_id})
        return {"$or": matches}

    @classmethod
    def visibility_filter(cls, user: Dict[str, Any]) -> Dict[str, Any]:
        """The whole enforced read filter: the caller's org, then the
        owner."""
        return {
            "org_id": str(user.get("org_id") or ""),
            **cls.visibility_or(user),
        }

    @staticmethod
    def may_edit(user: Dict[str, Any], doc: Dict[str, Any],
                 creator_field: str = "created_by",
                 escape: bool = False) -> bool:
        """Creator-only: seeing a shared document is not authority over
        it."""
        if escape:
            return True
        creator = str(doc.get(creator_field) or "")
        return bool(creator) and creator == str(user.get("user_id") or "")

    # ── Plumbing ────────────────────────────────────────────────────────

    def _clean_id_list(self, raw: Any, kind: str,
                       max_entries: Optional[int]) -> List[str]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise ValueError(f"Owner {kind} must be a list of ids.")
        if max_entries is not None and len(raw) > max_entries:
            raise ValueError(
                f"At most {max_entries} {kind} per {self.label}.")

        cleaned: List[str] = []
        for item in raw:
            item = str(item or "").strip()
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned
