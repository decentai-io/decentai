"""Shared plumbing for AI endpoint controllers.

Responses use the shared contracts envelope (EndpointResponse), so the
frontend, backend, and runtime all read one shape:
{version, request_id, status, data, error}.

The guards below are the other half. Almost every door here asks the
same two or three questions before it does anything — is this the
runtime, does this chat exist and belong to the caller, is this
delegation bound to THIS chat — and eight controllers had each written
their own copy of the answer. A guard that exists in eight places is a
guard that will one day be right in seven.

Every name here starts with an underscore, and that is load-bearing
rather than stylistic. The router dispatches Domain:Controller:action by
getattr and refuses names beginning with one, so a public method on a
registered controller IS an endpoint whether or not anyone meant it to
be: with a policy granting `*`, `AI:Chat:respond` used to dispatch, take
the request envelope where a user belongs, and answer 200 with nonsense.
The rule for this package is that the dispatchable surface of a
controller is exactly the endpoints it means to serve.
"""

import re

from contracts import EndpointResponse

from database.stores import ChatStore


class AIController:
    @staticmethod
    def _payload(data):
        value = (data or {}).get("data")
        return value if isinstance(value, dict) else {}

    @classmethod
    def _request_id(cls, data):
        return str(cls._payload(data).get("request_id") or "")

    @staticmethod
    def _public(document):
        return {key: value for key, value in document.items() if key != "_id"}

    def _respond(self, data, payload, status_code=200):
        response = EndpointResponse.success(self._request_id(data), payload)
        return response.model_dump(mode="json"), status_code

    def _fail(self, data, code, message, status_code=400):
        response = EndpointResponse.failure(self._request_id(data), code, message)
        return response.model_dump(mode="json"), status_code

    @staticmethod
    def _org(user):
        """The caller's organization, from their own record — never from
        the request, which is something a caller can lie about."""
        return str((user or {}).get("org_id") or "")

    # ------------------------------------------------------------------
    def _identity(self, user, chat_id):
        return {
            "chat_id": str(chat_id or ""),
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
        }

    def _chat(self, user, chat_id):
        return ChatStore().for_identity(self._identity(user, chat_id))

    def _next_sequence(self, chat_id):
        """The chat-wide message ordering counter, atomically."""
        return ChatStore().next_sequence(chat_id)

    # ------------------------------------------------------------------
    # The guards
    # ------------------------------------------------------------------
    def _runtime_only(self, data, user, doing):
        """None when the AI runtime is calling, else the refusal.

        ``doing`` finishes the sentence "Only the AI runtime may …", so
        the person reading a 403 is told which door refused them rather
        than that some door did."""
        if user.get("principal_type") != "runtime":
            return self._fail(
                data, "forbidden",
                f"Only the AI runtime may {doing}.", 403,
            )
        return None

    def _chat_or_refusal(self, data, user, chat_id=None):
        """(chat, None) or (None, failure).

        The chat is looked up by the CALLER'S identity (see `chat`), so a
        chat that exists but belongs to somebody else is indistinguishable
        from one that does not — which is the correct answer to give."""
        if chat_id is None:
            chat_id = self._payload(data).get("chat_id")
        chat = self._chat(user, chat_id)
        if chat is None:
            return None, self._fail(
                data, "chat_not_found", "Chat not found.", 404,
            )
        return chat, None

    def _runtime_chat(self, data, user, doing):
        """(chat, None) or (None, failure) — the full chain a record the
        runtime writes has to pass.

        The last link is the one worth naming: a delegation is minted for
        ONE chat, so a token that reaches a different chat is refused even
        when the same person owns both."""
        refusal = self._runtime_only(data, user, doing)
        if refusal is not None:
            return None, refusal

        chat, failure = self._chat_or_refusal(data, user)
        if failure is not None:
            return None, failure

        if str(user.get("chat_id") or "") != chat["chat_id"]:
            return None, self._fail(
                data, "forbidden",
                "This delegation is bound to a different chat.", 403,
            )
        return chat, None

    @staticmethod
    def _owner(user):
        """The caller's own records, as a query.

        For the reads and writes that are NOT chat-scoped — an execution
        by id, an approval being resolved. Same principle as `identity`:
        the scope comes from the caller's record, never from the request.
        """
        return {
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
        }

    THREAD_PATTERN = re.compile(r"^sub_[0-9a-f]{8}$")

    def _thread(self, data):
        """(thread, None) or (None, failure) — the sub-assistant thread a
        record belongs to (docs/system/chat-session.md, child threads). Absent
        means the chat's own conversation. The runtime mints the ids and
        they are addressed under the parent's credential, so the shape
        is the only thing worth checking."""
        thread = self._payload(data).get("thread")
        if thread in (None, ""):
            return "", None
        if not isinstance(thread, str) or not self.THREAD_PATTERN.match(thread):
            return None, self._fail(
                data, "invalid_request", "thread must look like sub_xxxxxxxx.")
        return thread, None

    def _limit(self, data, default, ceiling):
        """A caller's page size, bounded.

        Anything outside (1, ceiling] — absent, a string, a boolean,
        zero, or a number large enough to be a mistake — gets the
        default. Refusing the request instead would turn a clumsy client
        into an error nobody can act on, and honouring it would let one
        call ask for a chat's entire history."""
        value = self._payload(data).get("limit")
        if isinstance(value, bool) or not isinstance(value, int):
            return default
        return value if 0 < value <= ceiling else default
