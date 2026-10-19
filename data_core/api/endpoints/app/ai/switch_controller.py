"""AI:Switch — the person's own stop for everything of theirs.

``Stop`` ends every chat they have running and holds it stopped;
``Resume`` lets it run again; ``Status`` says which it is. All three
are the person's, for their own chats only
(api/services/chat_session/halt.py)."""

from api.services.chat_session.halt import Halt

from .base import AIController


class SwitchController(AIController):
    def status(self, data, user):
        refusal = self._person_only(data, user)
        if refusal is not None:
            return refusal
        return self._respond(data, {"stopped": Halt().stopped(user)})

    async def stop(self, data, user):
        refusal = self._person_only(data, user)
        if refusal is not None:
            return refusal
        chats = await Halt().stop(user)
        return self._respond(data, {"stopped": True, "chats": chats})

    async def resume(self, data, user):
        refusal = self._person_only(data, user)
        if refusal is not None:
            return refusal
        clocked = await Halt().resume(user)
        # The chats that keep a clock fire again as their rows come due;
        # none is attached to a node by the resume itself.
        return self._respond(data, {"stopped": False, "clocked": clocked})

    def _person_only(self, data, user):
        """Never the runtime's delegation: what is being stopped must
        not be able to start itself again."""
        if user.get("principal_type") == "runtime":
            return self._fail(
                data, "forbidden", "Only the person may use this switch.", 403)
        return None
