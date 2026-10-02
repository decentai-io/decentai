"""Which chat the running code acts for.

The executor's provider and the clock's fires reach the platform with
no chat in their signatures — they never needed one while the platform
was in-process. A platform reached over a network needs the chat to
pick the credential, so the session and the clock set it here for the
duration of their work, and anything they call inherits it (the same
mechanism as the executor's CURRENT_JOB_ID).
"""

from contextvars import ContextVar

CURRENT_CHAT: ContextVar[str] = ContextVar("current_chat", default="")
