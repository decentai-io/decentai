"""Who may take something over.

Stewardship of a stored thing — a credential, a record, a file, a
skill, a model connection, an agent source — is transferable, and the
one rule every transfer shares is here: the successor is an active
member of the same organization, and never the person being replaced.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from database.stores import UserStore


def successor_or_error(org_id: str, user_id: Any,
                       excluding: str = "") -> Tuple[Optional[Dict[str, Any]], str]:
    """(user document, "") when ``user_id`` names an active member of
    ``org_id`` other than ``excluding``; (None, why) otherwise."""
    user_id = str(user_id or "").strip()
    if not user_id:
        return None, "Name the person who takes it over (user_id)."
    doc = UserStore().get_in(str(org_id or ""), user_id)
    if doc is None:
        return None, "The successor is not a member of this organization."
    if doc.get("status", UserStore.STATUS_ACTIVE) != UserStore.STATUS_ACTIVE:
        return None, "The successor is disabled; choose an active member."
    if excluding and user_id == str(excluding):
        return None, "That is already the owner."
    return doc, ""
