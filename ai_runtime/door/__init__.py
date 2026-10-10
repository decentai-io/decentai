"""The door: how the backend reaches the runtime, and what stands
behind it (docs/reference/session-door.md).

    app.py        the FastAPI application: the routes, and what is built
                  and stopped with it (the host, the clock)
    auth.py       what "the backend" means: the service token, verified
    socket.py     /chats/{chat_id} — the chat socket, a pipe to the host
    monitor.py    /internal/monitor/* and /internal/health/live — what
                  agents did and use, read by the backend; the one
                  unauthenticated probe
    host.py       SessionHost — one session per chat, built from what the
                  backend answers; frames in, emissions out
    roster.py     AgentRoster — the agents a chat is served, pulled and
                  installed from what its contract names
"""

from ai_runtime.door.app import create_app

__all__ = ["create_app"]
