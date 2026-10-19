"""One thing a person can choose about how a chat thinks.

Eight of these exist: the trust level, the turn budget, the model, the
two narrowing lists, the time zone, the skills cap and the credential
bindings. They are not
alike in what they hold — a whole number, a block naming a connection, a
list of refs — but they are alike in what is asked of them, and that is
what this base is.

Every setting answers four questions:

    default()           what a new chat gets when nobody said
    check()             a value being chosen NOW, on a chat
    check_preference()  ...and the same, on the preferences page
    clamp()             a stored value, being served

The split between ``check`` and ``clamp`` is the important one, and it is
about who is standing there. ``check`` runs while a person is present, so
it refuses: quietly correcting somebody's choice would leave them
believing something untrue about their own chat. ``clamp`` runs on the way
out to the runtime, where the value was decided some time ago by somebody
who has gone — so it corrects, because there is nobody to tell and a
refusal would only strand the work.

The split between ``check`` and ``check_preference`` is smaller. For most
settings it is the same question twice, and the base says so. The model is
the exception: a chat carries a resolved block, while a preference names a
secret, so the two doors really are asking different things.

Two doors ask these — the chat page and the preferences page — and that
is the whole reason the class exists. Before this, each door had written
its own version, and they had already drifted: a chat's agents were
checked against the person's defaults when updating and not when
creating, and a chat's model was not checked at all.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple


class Setting:
    #: The key this setting uses inside a chat's ``config``.
    name = ""

    #: The key it uses inside ``preferences.chat``. Empty means a setting
    #: a person holds no default for (the credential bindings).
    preference = ""

    def default(self, user: Dict[str, Any], chosen: Dict[str, Any]) -> Any:
        """What a new chat carries when its creator said nothing.

        ``chosen`` is this person's ``preferences.chat`` block, read once
        by the resolver and handed to every setting, so five settings do
        not become five reads of the same document.

        None means the config carries nothing for this setting — which is
        not the same as carrying zero, and is how "no narrowing at all"
        stays distinct from "narrowed to nothing".
        """
        return None

    def check(self, user: Dict[str, Any], value: Any) -> Tuple[Any, str]:
        """(value, "") or (None, reason), for a chat's own value."""
        return value, ""

    def check_preference(
        self, user: Dict[str, Any], value: Any,
    ) -> Tuple[Any, str]:
        """(value, "") or (None, reason), for the default behind it."""
        return self.check(user, value)

    def clamp(self, value: Any) -> Any:
        """A stored value, made safe to act on. Never raises."""
        return value


def chosen_by(user: Dict[str, Any]) -> Dict[str, Any]:
    """This person's saved chat preferences — the model they picked, the
    level and patience they like, the agents and skills they keep on.

    A person with none simply has an empty one, which is why every caller
    can treat the answer as a dict and none of them has to ask whether
    the document has been written to yet.
    """
    from database.stores import UserStore

    record = UserStore().get(str(user.get("user_id") or "")) or {}
    return (record.get("preferences") or {}).get("chat") or {}
