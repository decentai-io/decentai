"""Chat: the assistant's embodiment.

    session.py            the Session — hydrate the mind, pump events,
                          persist every beat (docs/system/assistant.md)
    scheduler.py          the clock as an event source: deterministic
                          scheduled functions, wake-the-assistant
                          moments, and the rows that survive restarts
    summarizer.py         the rolling summary — session maintenance
                          that folds a long transcript into the frame
                          once the cycle is idle
    files.py              finding the file the person meant: the
                          ranking behind the assistant's find_files
    code_review.py        the chat's model reading an agent's code
                          before the person is shown it
    current.py            which chat the running code acts for

The reasoning itself is ai_runtime/reasoning; the socket door is
ai_runtime/server. This package is what stands between them.
"""

from ai_runtime.chat.session import Session

__all__ = ["Session"]
