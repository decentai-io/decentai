"""Governance — the platform-wide rules that are nobody's domain alone.

    sharing.py   who may see a record, whom it may be shared to, and
                 who may change it — one engine, configured per record
                 type through a SharingProfile

It sits beside ``server/authentication`` on purpose: authorization
answers "may this caller perform this action", governance answers "how
far does what they make reach". Both layers stand on it — controllers
clean and refuse with it, stores filter reads through it — which is why
it lives here rather than under ``api/``: the database layer may never
import from the API layer, and these rules are as much the stores' as
the controllers'.
"""

from server.governance.sharing import (
    INFRASTRUCTURE,
    PERSONAL,
    Sharing,
    SharingProfile,
)

__all__ = ["INFRASTRUCTURE", "PERSONAL", "Sharing", "SharingProfile"]
