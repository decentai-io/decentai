"""Where an agent's code comes from: a repository, opened at one commit.

A source knows how to open itself into a folder and say what it was, and
how to name itself in a sentence an administrator reads. Everything
downstream — reading a catalog, packaging an agent, storing a package —
works on the folder and never asks where it came from.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

from api.services.agents.repository import Repository
from database.stores import AgentSourceStore
from util import remove_tree

#: What opening a source hands over: a folder to read, and the commit
#: that was read. Not an identity (that is the per-agent package digest),
#: but a receipt an administrator can point at.
Opened = Iterator[Tuple[Path, str]]


class GitSource:
    """A repository, opened at one commit.

    The commit rather than the ref wherever one is recorded: a ref names
    a moving target, and an install is meant to be repeatable."""

    #: The value written on the source document, and on the `source`
    #: block of an approval.
    kind = AgentSourceStore.KIND_GIT

    def __init__(self, url: str, ref: str = "",
                 credential: Optional[Dict[str, str]] = None):
        self.url = str(url or "")
        self.ref = str(ref or "")
        self.credential = credential

    @classmethod
    def recorded(cls, record: Dict[str, Any],
                 credential: Optional[Dict[str, str]] = None) -> "GitSource":
        """The repository a stored record names — a source document or an
        approval's source block."""
        return cls(
            record.get("url"),
            str(record.get("sha") or record.get("ref") or ""),
            credential,
        )

    @contextmanager
    def open(self) -> Opened:
        """The checkout is thrown away on the way out whatever happened —
        an exception included, which is why this is a context manager
        rather than three lines each caller remembers to write."""
        checkout = None
        try:
            checkout, sha = Repository().fetch(
                self.url, self.ref, credential=self.credential)
            yield Path(checkout), sha
        finally:
            if checkout is not None:
                remove_tree(checkout)

    def unreadable(self, exc: Exception) -> str:
        return f"That repository could not be read: {exc}"

    def missing_agent(self, local_agent_id: str, catalog_path: str) -> str:
        #: It was there when the catalog was read; if it is gone now, the
        #: repository changed underneath the approval.
        return (f"'{local_agent_id}' is no longer in that repository's "
                f"catalog at '{catalog_path}'.")
