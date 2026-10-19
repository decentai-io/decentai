# util.py — small shared helpers with no dependencies on the app's modules.
import uuid
from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    """Timezone-aware UTC timestamp — the single source of 'now' for stored
    documents, so every collection times out and sorts consistently."""
    return datetime.now(timezone.utc)


def new_id() -> str:
    """Opaque unique id for documents (uuid4 hex, no dashes)."""
    return uuid.uuid4().hex


def iso(value: Optional[datetime]) -> Optional[str]:
    """ISO-8601 rendering for API responses; ``None`` passes through."""
    return value.isoformat() if value else None

def remove_tree(path) -> None:
    """Delete a fetched checkout.

    git leaves its object files read-only, which stops a plain rmtree on
    Windows — clear the bit and retry rather than leaving half a
    repository behind. The runtime carries its own copy of this for its
    own folders; the two processes do not share code.
    """
    import shutil
    import stat
    from pathlib import Path

    def force(action, name, _exception):
        try:
            Path(name).chmod(stat.S_IWRITE)
            action(name)
        except OSError:
            pass

    if Path(path).exists():
        shutil.rmtree(path, onerror=force)
