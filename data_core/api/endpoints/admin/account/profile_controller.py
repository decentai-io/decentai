"""Your own profile: read it, rename yourself, remember UI preferences.

The caller is the subject — the user id comes from the verified session,
never from the payload — so these actions cannot be pointed at anyone else.
Changing a PASSWORD is deliberately not here: credential rotation lives on
the public /auth/* router so it can never be revoked by policy.
"""

from __future__ import annotations

from typing import Any, Dict

from database.stores import GroupStore, UserStore
from server.custom_logging import CustomLoggerFactory


class ProfileController:
    Name = "Profile"

    def __init__(self):
        self.users = UserStore()
        self.groups = GroupStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data: dict) -> Dict[str, Any]:
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    def get(self, data: dict, user: dict):
        """The caller's own record, with their groups resolved for display."""
        doc = self.users.get(str(user.get("user_id") or ""))
        if doc is None:
            return {"error": "unauthorized"}, 401

        body = self._public(doc)
        body["groups"] = [
            {"group_id": g["_id"], "group_name": g.get("group_name", "")}
            for g in self.groups.list_by_ids(list(doc.get("assigned_groups") or []))
        ]
        return {"profile": body}, 200

    def peers(self, data: dict, user: dict):
        """The people who share an EXPLICIT group with the caller — the
        reach of "share with a person", listed so a picker can offer it.

        Names and emails of colleagues you already sit in a group with,
        nothing more; the implicit Everyone group deliberately does not
        count, or this would be the whole organization for everybody."""
        doc = self.users.get(str(user.get("user_id") or ""))
        if doc is None:
            return {"error": "unauthorized"}, 401

        peers = self.users.peers_of(
            str(user.get("org_id") or ""),
            list(doc.get("assigned_groups") or []), doc["_id"])
        return {"peers": [
            {"user_id": p["_id"],
             "user_name": p.get("user_name", ""),
             "email": p.get("email", "")}
            for p in peers
        ]}, 200

    @staticmethod
    def _public(doc: dict) -> Dict[str, Any]:
        body = UserStore.to_public(doc) or {}
        preferences = doc.get("preferences")
        body["preferences"] = (
            dict(preferences) if isinstance(preferences, dict) else {}
        )
        return body

    def update(self, data: dict, user: dict):
        """Update the caller's display name and safe personal preferences.

        Email, groups, and every authority-bearing field remain an
        administrator's decision.  An LLM preference is accepted only when
        it points at a usable key the caller can currently see.
        """
        user_id = str(user.get("user_id") or "")
        if not user_id:
            return {"error": "unauthorized"}, 401

        payload = self._payload(data)

        try:
            if "user_name" in payload:
                updated = self.users.update_profile(
                    user_id, payload.get("user_name")
                )
            else:
                updated = self.users.get(user_id)

            if "preferences" in payload:
                preferences = payload.get("preferences")
                if not isinstance(preferences, dict):
                    raise ValueError("preferences must be an object.")

                # The settings package owns what a chat preference is and
                # what a valid one looks like. This door used to hold its
                # own field list and its own copy of the model check, and
                # they had already drifted from the chat page's.
                from api.services.chat_session.settings import ChatSettings

                saved, problem = ChatSettings().set_defaults(
                    user, preferences.get("chat")
                )
                if problem:
                    raise ValueError(problem)
                updated = saved

        except ValueError as exc:
            return {"error": str(exc)}, 400

        self.logger.info(f"{user.get('email')} updated their profile")
        return {"profile": self._public(updated)}, 200
