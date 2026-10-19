"""Settings:Memory — what the assistant has been told to remember about
a person. Under Settings because it is the person's own configuration
of the assistant, beside the models it thinks with — not a place they
work in.

Explicit by design, and explicit about its origin. A memory is either
learned — a model emitted a `remember` action during a turn, which the
chat shows as it happens — or written by the person themselves on the
memory page. Every record says which it was, and says so again if the
person later edited it. Nothing is extracted silently in the background:
an invisible profile is one nobody can correct.

Writing is the only operation open to both callers. A delegated runtime
may add what it was told to remember and read the record back; editing
and forgetting are the person's own acts, because a model that could
rewrite its own memories could quietly change what it believes about
someone.

Personal, never shared — memories carry a user_id, not an owner map.
"""

from api.endpoints.app.ai.base import AIController
from database.stores import MemoryStore
from server.custom_logging import CustomLoggerFactory


class MemoryController(AIController):
    Name = "Memory"

    def __init__(self):
        self.store = MemoryStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    def list(self, data, user):
        """The record, and the size of the shelf it sits on. The cap is
        not decoration: at the limit the oldest memory is dropped to make
        room, so a person is owed a view of how close they are."""
        return self._respond(data, {
            "memories": self.store.list_for(user),
            "limit": MemoryStore.MAX_PER_USER,
        })

    def create(self, data, user):
        """Both write paths: the runtime's `remember` action during a
        turn, and a person writing one themselves.

        Which it was is recorded rather than inferred — a runtime that
        omitted its chat id would otherwise be indistinguishable from a
        person, and the page would credit the wrong author."""
        payload = self._payload(data)
        authored = str(user.get("token_type") or "").upper() == "WEB"
        try:
            memory = self.store.create(
                user,
                text=payload.get("text"),
                source_chat_id="" if authored else str(payload.get("chat_id") or ""),
                authored=authored,
            )
        except ValueError as exc:
            return self._fail(data, "invalid_memory", str(exc))

        self.logger.info(
            f"{user.get('email')} "
            f"{'added' if authored else 'remembered'} {memory['memory_id']}"
        )
        return self._respond(data, {"memory": memory})

    def delete(self, data, user):
        memory_id = str(self._payload(data).get("memory_id") or "")
        if not self.store.delete(user, memory_id):
            return self._fail(data, "not_found", "Memory not found.", 404)
        self.logger.info(f"{user.get('email')} forgot {memory_id}")
        return self._respond(data, {"deleted": True})

    def update(self, data, user):
        """Let a person correct their own memory; delegated runtimes are
        intentionally not allowed to call this operation."""
        payload = self._payload(data)
        try:
            memory = self.store.update(
                user,
                memory_id=str(payload.get("memory_id") or ""),
                text=payload.get("text"),
            )
        except ValueError as exc:
            return self._fail(data, "invalid_memory", str(exc))
        if not memory:
            return self._fail(data, "not_found", "Memory not found.", 404)
        self.logger.info(f"{user.get('email')} updated memory {memory['memory_id']}")
        return self._respond(data, {"memory": memory})
